"""Audio enhancement endpoints — denoise / dereverb / auto-tune."""

from __future__ import annotations

import io
import pathlib
import uuid
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import SessionStore, TrackState, get_user_session
from backend.services import pipeline_manager
from pipelines.enhance_pipeline import PRESETS, EnhanceConfig
from pipelines.autotune_pipeline import (
    NOTE_NAMES, SCALES, SCALE_LABELS, AutotuneConfig,
    AUTOTUNE_METHODS, AUTOTUNE_METHOD_LABELS,
)
from pipelines.effects_pipeline import EffectsConfig, EffectSlot
from utils.paths import ENHANCE_DIR, STEMS_DIR, user_dir

router = APIRouter(prefix="/api/enhance", tags=["enhance"])


class EnhanceRequest(BaseModel):
    preset: str               # "denoise" | "denoise_aggr" | "dereverb"
    stem_path: str            # absolute path to the audio file to process


class BatchEnhanceRequest(BaseModel):
    preset: str
    files: list[dict]         # [{filename, path}, ...]


class BatchSaveAllRequest(BaseModel):
    paths: list[dict]         # [{filename, path}, ...]


class AutotuneRequest(BaseModel):
    stem_path: str
    key: str = "Auto"                # "Auto" for auto-detection
    scale: str = "auto"              # "auto" for auto-detection
    correction_strength: float = 0.8  # 0.0–1.0
    humanize: float = 0.15            # 0.0–1.0
    method: str = "world_fast"       # "world_fast", "world", or "stft"


class EffectsRequest(BaseModel):
    stem_path: str
    chain: list[dict]  # [{type, method, bypass, params}, ...]


@router.get("/presets")
def get_presets() -> dict:
    """Return available enhancement presets."""
    return {
        "presets": [
            {"key": k, "label": v["label"], "description": v["description"]}
            for k, v in PRESETS.items()
        ]
    }


@router.get("/stems")
def get_available_stems(session: SessionStore = Depends(get_user_session)) -> dict:
    """Return stems available for enhancement (from separation + uploads)."""
    stems = []

    # Separated stems
    for label, path in session.stem_paths.items():
        stems.append({
            "label": label,
            "path": str(path),
            "source": "separation",
        })

    # Already-enhanced stems (can be re-processed with different preset)
    for label, path in session.enhance_paths.items():
        stems.append({
            "label": label,
            "path": str(path),
            "source": "enhanced",
        })

    # Uploaded audio (original file)
    if session.audio_path:
        stems.append({
            "label": "Original Upload",
            "path": str(session.audio_path),
            "source": "upload",
        })

    return {"stems": stems}


def _run_enhance(job_id: str, stem_path: str, preset: str,
                 session: SessionStore) -> dict:
    """Background job: run enhancement pipeline."""
    progress_cb = job_manager.make_progress_callback(job_id)

    # Validate input path
    path = pathlib.Path(stem_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {stem_path}")

    enhance_out = user_dir(ENHANCE_DIR, session.user)
    with pipeline_manager.gpu_session(pipeline_hint="enhance") as ctx:
        pipeline = pipeline_manager.get_enhance(ctx.gpu_index)
        pipeline.configure(EnhanceConfig(preset=preset, output_dir=enhance_out))
        try:
            result = pipeline.run(path, preset, progress_cb=progress_cb)
        finally:
            pipeline_manager.evict("enhance", ctx.gpu_index)

    # Store in session
    label = f"{path.stem} ({result.label})"
    session.add_enhance_path(label, result.output_path)

    # Add as mix track
    track = TrackState(
        track_id=uuid.uuid4().hex[:8],
        label=f"Enhanced: {label}",
        source="audio",
        path=result.output_path,
    )
    session.add_track(track)

    # Include all separator outputs so the user can A/B compare
    all_outputs = []
    if result.all_outputs:
        all_outputs = [
            {"path": str(o.path), "stem_label": o.stem_label}
            for o in result.all_outputs
        ]

    return {
        "output_path": str(result.output_path),
        "preset": result.preset,
        "label": label,
        "stem_path": stem_path,
        "all_outputs": all_outputs,
    }


@router.post("")
def start_enhance(req: EnhanceRequest,
                  session: SessionStore = Depends(get_user_session)) -> dict:
    """Start an enhancement job."""
    if req.preset not in PRESETS:
        raise HTTPException(400, f"Unknown preset: {req.preset}")

    path = pathlib.Path(req.stem_path)
    if not path.exists():
        raise HTTPException(404, f"Audio file not found: {req.stem_path}")

    # Validate path is within allowed directories
    resolved = path.resolve()
    allowed_roots = [STEMS_DIR.resolve(), ENHANCE_DIR.resolve()]
    # Also allow the original upload path
    if session.audio_path:
        allowed_roots.append(session.audio_path.resolve().parent)
    if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
        raise HTTPException(403, "Path not within allowed directories")

    job_id = job_manager.create_job("enhance", user=session.user)
    job_manager.run_job(job_id, _run_enhance, job_id, req.stem_path, req.preset,
                        session)
    return {"job_id": job_id}


# ─── Batch enhancement ──────────────────────────────────────────────────


def _run_batch_enhance(preset: str, files: list[dict], job_id: str, user: str = "local") -> dict:
    """Enhance multiple files with the same preset (runs in background thread)."""
    total = len(files)
    results: list[dict] = []

    batch_dir = user_dir(ENHANCE_DIR, user) / "batch"
    batch_dir.mkdir(parents=True, exist_ok=True)

    with pipeline_manager.gpu_session(pipeline_hint="enhance") as ctx:
        pipeline = pipeline_manager.get_enhance(ctx.gpu_index)
        pipeline.configure(EnhanceConfig(preset=preset, output_dir=batch_dir))

        job_manager.update_progress(job_id, 0.02, "Loading model...")
        pipeline.load_model(preset)

        try:
            for i, finfo in enumerate(files):
                audio_path = pathlib.Path(finfo["path"])
                display_name = finfo["filename"]

                def _batch_cb(pct, stage="", _i=i):
                    file_progress = pct / 100.0 if pct > 1 else pct
                    overall = ((_i + file_progress) / total) * 0.9 + 0.05
                    job_manager.update_progress(job_id, overall, f"[{_i + 1}/{total}] {stage}")

                try:
                    result = pipeline.run(audio_path, preset, progress_cb=_batch_cb)
                    dest_name = f"enhanced-{preset}-{audio_path.stem}{result.output_path.suffix}"
                    dest = batch_dir / dest_name
                    if result.output_path != dest:
                        result.output_path.rename(dest)
                    results.append({
                        "filename": display_name,
                        "output_name": dest_name,
                        "path": str(dest),
                        "preset": preset,
                    })
                except Exception as exc:
                    results.append({
                        "filename": display_name,
                        "error": str(exc),
                    })
        finally:
            pipeline_manager.evict("enhance", ctx.gpu_index)

    job_manager.update_progress(job_id, 1.0, "Done")
    return {"results": results, "preset": preset}


@router.post("/batch")
def start_batch_enhance(req: BatchEnhanceRequest, request: Request) -> dict:
    """Start a batch enhancement job."""
    if req.preset not in PRESETS:
        raise HTTPException(400, f"Unknown preset: {req.preset}")
    if not req.files:
        raise HTTPException(400, "No files provided")

    user = getattr(request.state, "user", "local")
    job_id = job_manager.create_job("enhance-batch", user=user)
    job_manager.run_job(job_id, _run_batch_enhance, req.preset, req.files, job_id, user)
    return {"job_id": job_id}


@router.post("/batch/save-all")
def batch_save_all(req: BatchSaveAllRequest):
    """Zip all batch enhancement results for download."""
    batch_root = ENHANCE_DIR.resolve()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in req.paths:
            p = pathlib.Path(item["path"]).resolve()
            if not str(p).startswith(str(batch_root)) or not p.exists():
                continue
            zf.write(p, item["filename"])
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="batch-enhanced.zip"'},
    )


# ─── Auto-tune ────────────────────────────────────────────────────────


@router.get("/autotune-options")
def get_autotune_options() -> dict:
    """Return available keys, scales, and synthesis methods for auto-tune."""
    import torch
    has_gpu = torch.cuda.is_available()

    methods = []
    for m in AUTOTUNE_METHODS:
        entry = {"key": m, "label": AUTOTUNE_METHOD_LABELS[m]}
        if m == "neural":
            entry["requires_gpu"] = True
            entry["disabled"] = not has_gpu
        methods.append(entry)

    return {
        "keys": NOTE_NAMES,
        "scales": [
            {"key": k, "label": SCALE_LABELS.get(k, k)}
            for k in SCALES
        ],
        "methods": methods,
    }


def _run_autotune(job_id: str, stem_path: str, key: str, scale: str,
                  correction_strength: float, humanize: float,
                  method: str = "world",
                  *, session: SessionStore) -> dict:
    """Background job: run auto-tune pipeline."""
    progress_cb = job_manager.make_progress_callback(job_id)

    path = pathlib.Path(stem_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {stem_path}")

    enhance_out = user_dir(ENHANCE_DIR, session.user)
    with pipeline_manager.gpu_session(pipeline_hint="autotune") as ctx:
        pipeline = pipeline_manager.get_autotune(ctx.gpu_index)
        pipeline.configure(AutotuneConfig(
            key=key,
            scale=scale,
            correction_strength=correction_strength,
            humanize=humanize,
            method=method,
            output_dir=enhance_out,
        ))
        pipeline.load_model(device=ctx.device)
        try:
            result = pipeline.run(path, progress_cb=progress_cb)
        finally:
            pipeline_manager.evict("autotune", ctx.gpu_index)

    # Use the actual key/scale (may have been auto-detected)
    actual_key = result.key
    actual_scale = result.scale
    scale_display = SCALE_LABELS.get(actual_scale, actual_scale)

    # Store in session
    label = f"{path.stem} (Auto-Tune {actual_key} {scale_display})"
    session.add_enhance_path(label, result.output_path)

    # Add as mix track
    track = TrackState(
        track_id=uuid.uuid4().hex[:8],
        label=f"Enhanced: {label}",
        source="audio",
        path=result.output_path,
    )
    session.add_track(track)

    resp = {
        "output_path": str(result.output_path),
        "label": label,
        "key": actual_key,
        "scale": actual_scale,
        "stem_path": stem_path,
    }
    if result.detected_key:
        resp["detected_key"] = result.detected_key
    if result.detected_scale:
        resp["detected_scale"] = result.detected_scale
    return resp


@router.post("/autotune")
def start_autotune(req: AutotuneRequest,
                   session: SessionStore = Depends(get_user_session)) -> dict:
    """Start an auto-tune job."""
    if req.key != "Auto" and req.key not in NOTE_NAMES:
        raise HTTPException(400, f"Unknown key: {req.key}")
    if req.scale != "auto" and req.scale not in SCALES:
        raise HTTPException(400, f"Unknown scale: {req.scale}")
    if req.method not in AUTOTUNE_METHODS:
        raise HTTPException(400, f"Unknown method: {req.method}")

    path = pathlib.Path(req.stem_path)
    if not path.exists():
        raise HTTPException(404, f"Audio file not found: {req.stem_path}")

    # Validate path is within allowed directories
    resolved = path.resolve()
    allowed_roots = [STEMS_DIR.resolve(), ENHANCE_DIR.resolve()]
    if session.audio_path:
        allowed_roots.append(session.audio_path.resolve().parent)
    if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
        raise HTTPException(403, "Path not within allowed directories")

    job_id = job_manager.create_job("autotune", user=session.user)
    job_manager.run_job(
        job_id, _run_autotune, job_id, req.stem_path,
        req.key, req.scale, req.correction_strength, req.humanize,
        req.method, session=session,
    )
    return {"job_id": job_id}


# ─── Effects chain ─────────────────────────────────────────────────────

_VALID_EFFECTS = {"eq", "compressor", "gate", "stereo_width"}
_VALID_METHODS = {
    "eq": {"dsp"},
    "compressor": {"dsp", "la2a"},
    "gate": {"dsp", "spectral", "deepfilter"},
    "stereo_width": {"dsp"},
}


@router.get("/effects-options")
def get_effects_options() -> dict:
    """Return the full schema of effects, methods, and parameter ranges."""
    import torch
    has_gpu = torch.cuda.is_available()

    # Check if deepfilternet is available
    has_deepfilter = False
    try:
        import df  # noqa: F401
        has_deepfilter = True
    except ImportError:
        pass

    return {
        "effects": [
            {
                "type": "eq",
                "label": "Parametric EQ",
                "methods": [{"key": "dsp", "label": "3-Band Parametric"}],
                "params": {
                    "low_gain": {"label": "Low Gain", "unit": "dB", "min": -12, "max": 12, "default": 0, "step": 0.5},
                    "low_freq": {"label": "Low Freq", "unit": "Hz", "min": 20, "max": 500, "default": 100, "step": 10},
                    "mid_gain": {"label": "Mid Gain", "unit": "dB", "min": -12, "max": 12, "default": 0, "step": 0.5},
                    "mid_freq": {"label": "Mid Freq", "unit": "Hz", "min": 200, "max": 8000, "default": 1000, "step": 50},
                    "mid_q": {"label": "Mid Q", "unit": "", "min": 0.1, "max": 10, "default": 1.0, "step": 0.1},
                    "high_gain": {"label": "High Gain", "unit": "dB", "min": -12, "max": 12, "default": 0, "step": 0.5},
                    "high_freq": {"label": "High Freq", "unit": "Hz", "min": 2000, "max": 20000, "default": 8000, "step": 100},
                },
            },
            {
                "type": "compressor",
                "label": "Compressor",
                "methods": [
                    {"key": "dsp", "label": "DSP Compressor"},
                    {"key": "la2a", "label": "LA-2A (Neural)"},
                ],
                "params_by_method": {
                    "dsp": {
                        "threshold_db": {"label": "Threshold", "unit": "dB", "min": -60, "max": 0, "default": -20, "step": 1},
                        "ratio": {"label": "Ratio", "unit": ":1", "min": 1, "max": 20, "default": 4, "step": 0.5},
                        "attack_ms": {"label": "Attack", "unit": "ms", "min": 0.1, "max": 100, "default": 10, "step": 0.5},
                        "release_ms": {"label": "Release", "unit": "ms", "min": 10, "max": 1000, "default": 100, "step": 10},
                        "makeup_db": {"label": "Makeup Gain", "unit": "dB", "min": 0, "max": 24, "default": 0, "step": 0.5},
                    },
                    "la2a": {
                        "peak_reduction": {"label": "Peak Reduction", "unit": "%", "min": 0, "max": 100, "default": 50, "step": 1},
                        "gain": {"label": "Gain", "unit": "%", "min": 0, "max": 100, "default": 50, "step": 1},
                    },
                },
            },
            {
                "type": "gate",
                "label": "Noise Gate",
                "methods": [
                    {"key": "dsp", "label": "Threshold Gate"},
                    {"key": "spectral", "label": "Spectral Gate (GPU)", "requires_gpu": True, "disabled": not has_gpu},
                    {"key": "deepfilter", "label": "DeepFilterNet (Vocals)", "vocals_only": True, "disabled": not has_deepfilter},
                ],
                "params_by_method": {
                    "dsp": {
                        "threshold_db": {"label": "Threshold", "unit": "dB", "min": -80, "max": 0, "default": -40, "step": 1},
                        "attack_ms": {"label": "Attack", "unit": "ms", "min": 0.1, "max": 50, "default": 1, "step": 0.5},
                        "hold_ms": {"label": "Hold", "unit": "ms", "min": 1, "max": 500, "default": 50, "step": 5},
                        "release_ms": {"label": "Release", "unit": "ms", "min": 10, "max": 1000, "default": 100, "step": 10},
                    },
                    "spectral": {
                        "stationary": {"label": "Stationary", "type": "bool", "default": True},
                        "threshold_scale": {"label": "Threshold Scale", "unit": "", "min": 0.5, "max": 5.0, "default": 1.5, "step": 0.1},
                    },
                    "deepfilter": {
                        "atten_lim_db": {"label": "Max Attenuation", "unit": "dB", "min": 0, "max": 100, "default": 100, "step": 5},
                    },
                },
            },
            {
                "type": "stereo_width",
                "label": "Stereo Width",
                "methods": [{"key": "dsp", "label": "Mid/Side"}],
                "params": {
                    "width": {"label": "Width", "unit": "%", "min": 0, "max": 200, "default": 100, "step": 5},
                },
            },
        ],
    }


def _run_effects(job_id: str, stem_path: str, chain_dicts: list[dict],
                 session: SessionStore) -> dict:
    """Background job: run effects chain pipeline."""
    progress_cb = job_manager.make_progress_callback(job_id)

    path = pathlib.Path(stem_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {stem_path}")

    # Convert dicts to EffectSlot objects
    chain = []
    for d in chain_dicts:
        chain.append(EffectSlot(
            effect_type=d["type"],
            method=d.get("method", "dsp"),
            bypass=d.get("bypass", False),
            params=d.get("params", {}),
        ))

    enhance_out = user_dir(ENHANCE_DIR, session.user)
    with pipeline_manager.gpu_session(pipeline_hint="effects") as ctx:
        pipeline = pipeline_manager.get_effects(ctx.gpu_index)
        pipeline.configure(EffectsConfig(chain=chain, output_dir=enhance_out))
        pipeline.load_model(device=ctx.device)
        try:
            result = pipeline.run(path, progress_cb=progress_cb)
        finally:
            pipeline_manager.evict("effects", ctx.gpu_index)

    # Store in session
    label = f"{path.stem} (FX: {result.chain_summary})"
    session.add_enhance_path(label, result.output_path)

    # Add as mix track
    track = TrackState(
        track_id=uuid.uuid4().hex[:8],
        label=f"Enhanced: {label}",
        source="audio",
        path=result.output_path,
    )
    session.add_track(track)

    return {
        "output_path": str(result.output_path),
        "label": label,
        "chain_summary": result.chain_summary,
        "stem_path": stem_path,
    }


@router.post("/effects")
def start_effects(req: EffectsRequest,
                  session: SessionStore = Depends(get_user_session)) -> dict:
    """Start an effects chain job."""
    # Validate chain
    if not req.chain:
        raise HTTPException(400, "Empty effects chain")

    for i, slot in enumerate(req.chain):
        etype = slot.get("type")
        if etype not in _VALID_EFFECTS:
            raise HTTPException(400, f"Slot {i}: unknown effect type '{etype}'")
        method = slot.get("method", "dsp")
        if method not in _VALID_METHODS.get(etype, set()):
            raise HTTPException(400, f"Slot {i}: invalid method '{method}' for effect '{etype}'")

    # Validate path
    path = pathlib.Path(req.stem_path)
    if not path.exists():
        raise HTTPException(404, f"Audio file not found: {req.stem_path}")

    resolved = path.resolve()
    allowed_roots = [STEMS_DIR.resolve(), ENHANCE_DIR.resolve()]
    if session.audio_path:
        allowed_roots.append(session.audio_path.resolve().parent)
    if not any(str(resolved).startswith(str(root)) for root in allowed_roots):
        raise HTTPException(403, "Path not within allowed directories")

    job_id = job_manager.create_job("effects", user=session.user)
    job_manager.run_job(job_id, _run_effects, job_id, req.stem_path, req.chain,
                        session)
    return {"job_id": job_id}
