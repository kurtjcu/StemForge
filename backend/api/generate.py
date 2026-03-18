"""Audio generation endpoint (Stable Audio Open)."""

from __future__ import annotations

import pathlib
import re
import unicodedata
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import SessionStore, get_user_session
from backend.services import pipeline_manager
from utils.paths import MUSICGEN_DIR, MIDI_DIR, user_dir

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


def _clip_name_from_prompt(prompt: str, max_len: int = 30) -> str:
    """Derive a short filename-safe clip name from a generation prompt."""
    text = unicodedata.normalize("NFKD", prompt).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9 ]", "", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rsplit(" ", 1)[0]
    return text.replace(" ", "_").lower() or "clip"


def _run_generation(req: GenerateRequest, job_id: str, session: SessionStore) -> dict:
    """Execute generation pipeline (runs in background thread)."""
    from pipelines.musicgen_pipeline import MusicGenConfig

    # Resolve conditioning (before acquiring GPU lock)
    init_audio_path = None
    midi_path = None

    if req.conditioning_source == "audio" and req.conditioning_path:
        init_audio_path = pathlib.Path(req.conditioning_path)
    elif req.conditioning_source == "midi":
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

    gen_out = user_dir(MUSICGEN_DIR, session.user)

    config = MusicGenConfig(
        prompt=req.prompt,
        duration_seconds=req.duration,
        steps=req.steps,
        cfg_scale=req.cfg_scale,
        init_audio_path=init_audio_path,
        midi_path=midi_path,
        output_dir=gen_out,
        negative_prompt=req.negative_prompt if req.vocal_preservation else "",
    )

    def _gen_cb(pct, stage=""):
        job_manager.update_progress(job_id, pct / 100.0, stage)

    with pipeline_manager.gpu_session(pipeline_hint="musicgen") as ctx:
        pipeline = pipeline_manager.get_musicgen(ctx.gpu_index)
        pipeline.configure(config)
        pipeline.set_progress_callback(_gen_cb)
        job_manager.update_progress(job_id, 0.05, "Loading model...")
        pipeline.load_model(device=ctx.device)
        try:
            result = pipeline.run(req.prompt)
        finally:
            pipeline_manager.evict("musicgen", ctx.gpu_index)

    # Rename from timestamp to prompt-based name for identifiability
    clip_name = _clip_name_from_prompt(req.prompt)
    short_id = uuid.uuid4().hex[:6]
    new_filename = f"{clip_name}_{short_id}.wav"
    new_path = result.audio_path.parent / new_filename
    result.audio_path.rename(new_path)

    session.musicgen_path = new_path

    return {
        "audio_path": str(new_path),
        "duration": result.duration_seconds,
        "name": clip_name.replace("_", " ").title(),
    }


@router.post("/generate")
def start_generation(req: GenerateRequest, session: SessionStore = Depends(get_user_session)) -> dict:
    if not req.prompt.strip():
        raise HTTPException(422, "Prompt is required")

    job_id = job_manager.create_job("generate", user=session.user)
    job_manager.run_job(job_id, _run_generation, req, job_id, session)
    return {"job_id": job_id}
