"""Audio enhancement endpoints — denoise / dereverb / auto-tune."""

from __future__ import annotations

import io
import pathlib
import uuid
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import session, TrackState
from backend.services import pipeline_manager
from pipelines.enhance_pipeline import PRESETS, EnhanceConfig
from pipelines.autotune_pipeline import (
    NOTE_NAMES, SCALES, SCALE_LABELS, AutotuneConfig,
)
from utils.paths import ENHANCE_DIR, STEMS_DIR

router = APIRouter(prefix="/api/enhance", tags=["enhance"])

_BATCH_DIR = ENHANCE_DIR / "batch"


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
    key: str = "C"
    scale: str = "chromatic"
    correction_strength: float = 0.8  # 0.0–1.0
    humanize: float = 0.15            # 0.0–1.0


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
def get_available_stems() -> dict:
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


def _run_enhance(job_id: str, stem_path: str, preset: str) -> dict:
    """Background job: run enhancement pipeline."""
    progress_cb = job_manager.make_progress_callback(job_id)

    # Validate input path
    path = pathlib.Path(stem_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {stem_path}")

    pipeline = pipeline_manager.get_enhance()
    pipeline.configure(EnhanceConfig(preset=preset, output_dir=ENHANCE_DIR))

    result = pipeline.run(path, preset, progress_cb=progress_cb)

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
def start_enhance(req: EnhanceRequest) -> dict:
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

    job_id = job_manager.create_job("enhance")
    job_manager.run_job(job_id, _run_enhance, job_id, req.stem_path, req.preset)
    return {"job_id": job_id}


# ─── Batch enhancement ──────────────────────────────────────────────────


def _run_batch_enhance(preset: str, files: list[dict], job_id: str) -> dict:
    """Enhance multiple files with the same preset (runs in background thread)."""
    total = len(files)
    results: list[dict] = []

    _BATCH_DIR.mkdir(parents=True, exist_ok=True)

    pipeline = pipeline_manager.get_enhance()
    pipeline.configure(EnhanceConfig(preset=preset, output_dir=_BATCH_DIR))

    job_manager.update_progress(job_id, 0.02, "Loading model...")
    pipeline.load_model(preset)

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
            dest = _BATCH_DIR / dest_name
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

    job_manager.update_progress(job_id, 1.0, "Done")
    return {"results": results, "preset": preset}


@router.post("/batch")
def start_batch_enhance(req: BatchEnhanceRequest) -> dict:
    """Start a batch enhancement job."""
    if req.preset not in PRESETS:
        raise HTTPException(400, f"Unknown preset: {req.preset}")
    if not req.files:
        raise HTTPException(400, "No files provided")

    job_id = job_manager.create_job("enhance-batch")
    job_manager.run_job(job_id, _run_batch_enhance, req.preset, req.files, job_id)
    return {"job_id": job_id}


@router.post("/batch/save-all")
def batch_save_all(req: BatchSaveAllRequest):
    """Zip all batch enhancement results for download."""
    batch_root = _BATCH_DIR.resolve()
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
    """Return available keys and scales for auto-tune."""
    return {
        "keys": NOTE_NAMES,
        "scales": [
            {"key": k, "label": SCALE_LABELS.get(k, k)}
            for k in SCALES
        ],
    }


def _run_autotune(job_id: str, stem_path: str, key: str, scale: str,
                  correction_strength: float, humanize: float) -> dict:
    """Background job: run auto-tune pipeline."""
    progress_cb = job_manager.make_progress_callback(job_id)

    path = pathlib.Path(stem_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {stem_path}")

    pipeline = pipeline_manager.get_autotune()
    pipeline.configure(AutotuneConfig(
        key=key,
        scale=scale,
        correction_strength=correction_strength,
        humanize=humanize,
        output_dir=ENHANCE_DIR,
    ))

    result = pipeline.run(path, progress_cb=progress_cb)

    # Store in session
    label = f"{path.stem} (Auto-Tune {key} {SCALE_LABELS.get(scale, scale)})"
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
        "key": key,
        "scale": scale,
        "stem_path": stem_path,
    }


@router.post("/autotune")
def start_autotune(req: AutotuneRequest) -> dict:
    """Start an auto-tune job."""
    if req.key not in NOTE_NAMES:
        raise HTTPException(400, f"Unknown key: {req.key}")
    if req.scale not in SCALES:
        raise HTTPException(400, f"Unknown scale: {req.scale}")

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

    job_id = job_manager.create_job("autotune")
    job_manager.run_job(
        job_id, _run_autotune, job_id, req.stem_path,
        req.key, req.scale, req.correction_strength, req.humanize,
    )
    return {"job_id": job_id}
