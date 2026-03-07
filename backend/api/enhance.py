"""Audio enhancement endpoints — denoise / dereverb."""

from __future__ import annotations

import pathlib
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import session, TrackState
from backend.services import pipeline_manager
from pipelines.enhance_pipeline import PRESETS, EnhanceConfig
from utils.paths import ENHANCE_DIR, STEMS_DIR

router = APIRouter(prefix="/api/enhance", tags=["enhance"])


class EnhanceRequest(BaseModel):
    preset: str               # "denoise" | "denoise_aggr" | "dereverb"
    stem_path: str            # absolute path to the audio file to process


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

    return {
        "output_path": str(result.output_path),
        "preset": result.preset,
        "label": label,
        "stem_path": stem_path,
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
