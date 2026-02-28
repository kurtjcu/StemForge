"""Audio generation endpoint (Stable Audio Open)."""

from __future__ import annotations

import pathlib
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import session
from backend.services import pipeline_manager
from utils.paths import MUSICGEN_DIR, MIDI_DIR

router = APIRouter(prefix="/api", tags=["generate"])


class GenerateRequest(BaseModel):
    prompt: str
    duration: float = 30.0
    steps: int = 100
    cfg_scale: float = 7.0
    conditioning_source: str = "none"  # "none", "audio", "midi", "mix"
    conditioning_path: str | None = None
    vocal_preservation: bool = False
    negative_prompt: str = ""


def _run_generation(req: GenerateRequest, job_id: str) -> dict:
    """Execute generation pipeline (runs in background thread)."""
    from pipelines.musicgen_pipeline import MusicGenConfig

    pipeline = pipeline_manager.get_musicgen()

    # Resolve conditioning
    init_audio_path = None
    midi_path = None

    if req.conditioning_source == "audio" and req.conditioning_path:
        init_audio_path = pathlib.Path(req.conditioning_path)
    elif req.conditioning_source == "midi":
        # Write session MIDI to temp file
        merged = session.merged_midi_data
        if merged:
            MIDI_DIR.mkdir(parents=True, exist_ok=True)
            tmp = MIDI_DIR / f"merged_tmp_{uuid.uuid4().hex[:6]}.mid"
            from utils.midi_io import write_midi
            write_midi(merged, tmp)
            midi_path = tmp
    elif req.conditioning_source == "mix":
        mix_path = session.mix_path
        if mix_path:
            init_audio_path = mix_path

    MUSICGEN_DIR.mkdir(parents=True, exist_ok=True)

    config = MusicGenConfig(
        prompt=req.prompt,
        duration_seconds=req.duration,
        steps=req.steps,
        cfg_scale=req.cfg_scale,
        init_audio_path=init_audio_path,
        midi_path=midi_path,
        output_dir=MUSICGEN_DIR,
        negative_prompt=req.negative_prompt if req.vocal_preservation else "",
    )

    # MusicGenPipeline callback: (pct 0–100, stage_str)
    def _gen_cb(pct, stage=""):
        job_manager.update_progress(job_id, pct / 100.0, stage)

    pipeline.configure(config)
    pipeline.set_progress_callback(_gen_cb)
    job_manager.update_progress(job_id, 0.05, "Loading model...")
    pipeline.load_model()
    result = pipeline.run(req.prompt)

    session.musicgen_path = result.audio_path

    return {
        "audio_path": str(result.audio_path),
        "duration": result.duration_seconds,
    }


@router.post("/generate")
def start_generation(req: GenerateRequest) -> dict:
    if not req.prompt.strip():
        raise HTTPException(422, "Prompt is required")

    job_id = job_manager.create_job("generate")
    job_manager.run_job(job_id, _run_generation, req, job_id)
    return {"job_id": job_id}
