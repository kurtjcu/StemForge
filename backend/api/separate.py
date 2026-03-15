"""Separation endpoints + job polling."""

from __future__ import annotations

import io
import pathlib
import shutil
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import SessionStore, TrackState, get_user_session
from backend.services import pipeline_manager
from utils.paths import STEMS_DIR

router = APIRouter(prefix="/api", tags=["separate"])

_BATCH_DIR = STEMS_DIR / "batch"


class SeparateRequest(BaseModel):
    engine: str = "demucs"            # "demucs" or "roformer"
    model_id: str = "htdemucs"
    stems: list[str] | None = None    # None = all available


class BatchSeparateRequest(BaseModel):
    engine: str = "demucs"
    model_id: str = "htdemucs"
    stem: str                         # single stem to extract
    files: list[dict]                 # [{filename, path}, ...]


class BatchSaveAllRequest(BaseModel):
    paths: list[dict]                 # [{filename, path}, ...]


def _make_pipeline_cb(job_id: str):
    """Bridge pipeline progress (0–100, str) to job_manager (0–1, str)."""
    def _cb(pct, stage=""):
        job_manager.update_progress(job_id, pct / 100.0, stage)
    return _cb


def _run_separation(
    engine: str,
    model_id: str,
    stems: list[str] | None,
    audio_path: pathlib.Path,
    job_id: str,
    session: SessionStore,
) -> dict:
    """Execute separation pipeline (runs in background thread)."""
    pipeline_cb = _make_pipeline_cb(job_id)

    # Inherit source audio quality from session
    audio_info = session.audio_info or {}
    source_sr = audio_info.get("sample_rate", 44100)
    source_bd = audio_info.get("bit_depth") or 24

    pipeline_name = "roformer" if engine == "roformer" else "demucs"
    try:
        if engine == "roformer":
            from pipelines.roformer_pipeline import RoformerConfig
            from models.registry import get_spec, RoformerSpec

            pipeline = pipeline_manager.get_roformer()
            spec = get_spec(model_id)
            if not isinstance(spec, RoformerSpec):
                raise ValueError(f"{model_id} is not a Roformer model")

            config = RoformerConfig(
                model_id=model_id,
                stems=stems or list(spec.available_stems),
                output_dir=STEMS_DIR,
                sample_rate=source_sr,
                bit_depth=source_bd,
                chunk_size=spec.default_chunk_size,
                num_overlap=spec.default_num_overlap,
            )
            pipeline.configure(config)
            pipeline.set_progress_callback(pipeline_cb)
            pipeline_cb(5, "Loading model...")
            pipeline.load_model()
            result = pipeline.run(audio_path)
        else:
            from pipelines.demucs_pipeline import DemucsConfig

            pipeline = pipeline_manager.get_demucs()
            config = DemucsConfig(
                model_name=model_id,
                stems=stems or ["vocals", "drums", "bass", "other"],
                output_dir=STEMS_DIR,
                sample_rate=source_sr,
                bit_depth=source_bd,
            )
            pipeline.configure(config)
            pipeline.set_progress_callback(pipeline_cb)
            pipeline_cb(5, "Loading model...")
            pipeline.load_model()
            result = pipeline.run(audio_path)
    finally:
        # Free GPU memory so other pipelines (AceStep, Synth) can use it
        pipeline_manager.evict(pipeline_name)

    # Store stem paths in session and auto-add mix tracks
    stem_paths = dict(result.stem_paths)
    session.stem_paths = stem_paths

    for label, path in stem_paths.items():
        track_id = f"stem-{label}"
        if not session.get_track(track_id):
            session.add_track(TrackState(
                track_id=track_id,
                label=label.replace("_", " ").title(),
                source="audio",
                path=path,
            ))

    return {
        "stem_paths": {k: str(v) for k, v in stem_paths.items()},
    }


@router.post("/separate")
def start_separation(req: SeparateRequest, session: SessionStore = Depends(get_user_session)) -> dict:
    audio_path = session.audio_path
    if not audio_path:
        raise HTTPException(400, "No audio file loaded — upload first")

    job_id = job_manager.create_job("separate")
    job_manager.run_job(
        job_id,
        _run_separation,
        req.engine,
        req.model_id,
        req.stems,
        audio_path,
        job_id,
        session,
    )
    return {"job_id": job_id}


@router.get("/separate/recommend")
def recommend_separator(session: SessionStore = Depends(get_user_session)) -> dict:
    audio_path = session.audio_path
    if not audio_path:
        raise HTTPException(400, "No audio file loaded")

    from utils.audio_profile import profile_audio, recommend_separator as _recommend

    profile = profile_audio(audio_path)
    rec = _recommend(profile)

    result = {
        "engine": rec.engine,
        "model_id": rec.model_id,
        "reason": rec.reason,
        "confidence": rec.confidence,
    }
    if rec.license_warning:
        result["license_warning"] = rec.license_warning
    return result


# ─── Batch separation ────────────────────────────────────────────────────


def _run_batch_separation(
    engine: str,
    model_id: str,
    stem: str,
    files: list[dict],
    job_id: str,
    session: SessionStore,
) -> dict:
    """Separate one stem from each file in the batch (runs in background thread)."""
    total = len(files)
    results = []

    _BATCH_DIR.mkdir(parents=True, exist_ok=True)

    # Inherit source audio quality from session (batch files may vary,
    # but we use the session source as the quality target)
    audio_info = session.audio_info or {}
    source_sr = audio_info.get("sample_rate", 44100)
    source_bd = audio_info.get("bit_depth") or 24

    # Load model once, reuse for all files
    pipeline_name = "roformer" if engine == "roformer" else "demucs"
    if engine == "roformer":
        from pipelines.roformer_pipeline import RoformerConfig
        from models.registry import get_spec, RoformerSpec

        pipeline = pipeline_manager.get_roformer()
        spec = get_spec(model_id)
        if not isinstance(spec, RoformerSpec):
            raise ValueError(f"{model_id} is not a Roformer model")

        def _configure():
            config = RoformerConfig(
                model_id=model_id,
                stems=[stem],
                output_dir=_BATCH_DIR,
                sample_rate=source_sr,
                bit_depth=source_bd,
                chunk_size=spec.default_chunk_size,
                num_overlap=spec.default_num_overlap,
            )
            pipeline.configure(config)
    else:
        from pipelines.demucs_pipeline import DemucsConfig

        pipeline = pipeline_manager.get_demucs()

        def _configure():
            config = DemucsConfig(
                model_name=model_id,
                stems=[stem],
                output_dir=_BATCH_DIR,
                sample_rate=source_sr,
                bit_depth=source_bd,
            )
            pipeline.configure(config)

    job_manager.update_progress(job_id, 0.02, "Loading model...")
    _configure()
    pipeline.load_model()

    try:
        for i, finfo in enumerate(files):
            audio_path = pathlib.Path(finfo["path"])
            display_name = finfo["filename"]
            base_name = pathlib.Path(display_name).stem

            job_manager.update_progress(
                job_id,
                (i / total) * 0.9 + 0.05,
                f"Processing {i + 1}/{total}: {display_name}",
            )

            def _batch_cb(pct, stage="", _i=i):
                file_progress = pct / 100.0
                overall = ((_i + file_progress) / total) * 0.9 + 0.05
                job_manager.update_progress(job_id, overall, f"[{_i + 1}/{total}] {stage}")

            pipeline.set_progress_callback(_batch_cb)

            # Re-configure for each file (output_dir stays the same)
            _configure()

            try:
                result = pipeline.run(audio_path)
                stem_paths = dict(result.stem_paths)

                if stem in stem_paths:
                    src = pathlib.Path(stem_paths[stem])
                    dest_name = f"{stem}-stem-{base_name}{src.suffix}"
                    dest = _BATCH_DIR / dest_name
                    if src != dest:
                        shutil.move(str(src), str(dest))
                    results.append({
                        "filename": display_name,
                        "stem": stem,
                        "output_name": dest_name,
                        "path": str(dest),
                    })
                else:
                    results.append({
                        "filename": display_name,
                        "error": f"Stem '{stem}' not found in output",
                    })
            except Exception as exc:
                results.append({
                    "filename": display_name,
                    "error": str(exc),
                })
    finally:
        # Free GPU memory so other pipelines (AceStep, Synth) can use it
        pipeline_manager.evict(pipeline_name)

    job_manager.update_progress(job_id, 1.0, "Done")
    return {"results": results, "stem": stem}


@router.post("/separate/batch")
def start_batch_separation(req: BatchSeparateRequest, session: SessionStore = Depends(get_user_session)) -> dict:
    if not req.files:
        raise HTTPException(400, "No files provided")

    job_id = job_manager.create_job("separate-batch")
    job_manager.run_job(
        job_id,
        _run_batch_separation,
        req.engine,
        req.model_id,
        req.stem,
        req.files,
        job_id,
        session,
    )
    return {"job_id": job_id}


@router.post("/separate/batch/save-all")
def batch_save_all(req: BatchSaveAllRequest):
    """Zip all batch results for download."""
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
        headers={"Content-Disposition": 'attachment; filename="batch-stems.zip"'},
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    data = job_manager.to_dict(job_id)
    if not data:
        raise HTTPException(404, "Job not found")
    return data
