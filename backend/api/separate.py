"""Separation endpoints + job polling."""

from __future__ import annotations

import pathlib

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import session, TrackState
from backend.services import pipeline_manager
from utils.paths import STEMS_DIR

router = APIRouter(prefix="/api", tags=["separate"])


class SeparateRequest(BaseModel):
    engine: str = "demucs"            # "demucs" or "roformer"
    model_id: str = "htdemucs"
    stems: list[str] | None = None    # None = all available


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
) -> dict:
    """Execute separation pipeline (runs in background thread)."""
    pipeline_cb = _make_pipeline_cb(job_id)

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
        )
        pipeline.configure(config)
        pipeline.set_progress_callback(pipeline_cb)
        pipeline_cb(5, "Loading model...")
        pipeline.load_model()
        result = pipeline.run(audio_path)

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
def start_separation(req: SeparateRequest) -> dict:
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
    )
    return {"job_id": job_id}


@router.get("/separate/recommend")
def recommend_separator() -> dict:
    audio_path = session.audio_path
    if not audio_path:
        raise HTTPException(400, "No audio file loaded")

    from utils.audio_profile import profile_audio, recommend_separator as _recommend

    profile = profile_audio(audio_path)
    rec = _recommend(profile)

    return {
        "engine": rec.engine,
        "model_id": rec.model_id,
        "reason": rec.reason,
        "confidence": rec.confidence,
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    data = job_manager.to_dict(job_id)
    if not data:
        raise HTTPException(404, "Job not found")
    return data
