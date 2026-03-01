"""MIDI extraction, render, and save endpoints."""

from __future__ import annotations

import copy
import pathlib
import tempfile
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import session, TrackState
from backend.services import pipeline_manager
from utils.paths import MIDI_DIR

router = APIRouter(prefix="/api/midi", tags=["midi"])


class ExtractRequest(BaseModel):
    stems: list[str]
    key: str = "Any"
    bpm: float = 120.0
    time_signature: str = "4/4"
    duration: float = 0.0
    prompt: str = ""
    onset_threshold: float = 0.5
    frame_threshold: float = 0.3
    min_note_ms: float = 58.0


class RenderRequest(BaseModel):
    stem_label: str
    program: int = 0
    is_drum: bool = False


class SaveRequest(BaseModel):
    label: str = "merged"    # "merged" or a stem label


def _run_midi_extraction(
    stems: dict[str, pathlib.Path],
    config_kwargs: dict,
    job_id: str,
) -> dict:
    """Execute MIDI pipeline (runs in background thread)."""
    from pipelines.midi_pipeline import MidiConfig

    # MidiPipeline callback takes 1 arg (pct 0–100), not (pct, stage)
    def _midi_cb(pct):
        job_manager.update_progress(job_id, pct / 100.0, "Extracting MIDI...")

    pipeline = pipeline_manager.get_midi()
    config = MidiConfig(**config_kwargs)
    pipeline.configure(config)

    job_manager.update_progress(job_id, 0.05, "Loading model...")
    pipeline.load_model()

    pipeline.set_progress_callback(_midi_cb)
    result = pipeline.run(stems)

    # Store in session and auto-add mix tracks
    session.merged_midi_data = result.merged_midi_data
    session.stem_midi_data = result.stem_midi_data or {}

    stem_info = {}
    for label, midi_data in (result.stem_midi_data or {}).items():
        note_count = sum(len(inst.notes) for inst in midi_data.instruments)
        stem_info[label] = {"note_count": note_count}

        track_id = f"midi-{label}"
        if not session.get_track(track_id):
            session.add_track(TrackState(
                track_id=track_id,
                label=f"{label.replace('_', ' ').title()} (MIDI)",
                source="midi",
                midi_data=midi_data,
            ))

    return {
        "labels": list((result.stem_midi_data or {}).keys()),
        "stem_info": stem_info,
        "has_merged": result.merged_midi_data is not None,
    }


@router.post("/extract")
def start_extraction(req: ExtractRequest) -> dict:
    stem_paths = session.stem_paths
    if not stem_paths:
        raise HTTPException(400, "No stems available — run separation first")

    # Filter to requested stems
    stems = {k: v for k, v in stem_paths.items() if k in req.stems}
    if not stems:
        raise HTTPException(422, f"None of {req.stems} found in session stems")

    config_kwargs = {
        "key": req.key,
        "bpm": req.bpm,
        "time_signature": req.time_signature,
        "duration_seconds": req.duration,
        "prompt": req.prompt,
        "onset_threshold": req.onset_threshold,
        "frame_threshold": req.frame_threshold,
        "minimum_note_length": req.min_note_ms,
    }

    job_id = job_manager.create_job("midi")
    job_manager.run_job(job_id, _run_midi_extraction, stems, config_kwargs, job_id)
    return {"job_id": job_id}


@router.post("/render")
def render_midi_to_audio(req: RenderRequest) -> dict:
    """Render a stem's MIDI to audio via FluidSynth."""
    stem_midi = session.stem_midi_data
    if req.stem_label not in stem_midi:
        raise HTTPException(404, f"No MIDI for stem '{req.stem_label}'")

    import pretty_midi

    midi_data = copy.deepcopy(stem_midi[req.stem_label])

    # Set program/drum for all instruments
    for inst in midi_data.instruments:
        inst.program = req.program
        inst.is_drum = req.is_drum

    # Render via FluidSynth
    audio = midi_data.fluidsynth(fs=44100)
    if audio is None or len(audio) == 0:
        raise HTTPException(500, "FluidSynth render produced no audio")

    # Write to temp file
    import numpy as np
    from utils.audio_io import write_audio

    MIDI_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MIDI_DIR / f"render_{req.stem_label}_{uuid.uuid4().hex[:6]}.wav"
    waveform = audio.astype(np.float32)
    if waveform.ndim == 1:
        waveform = waveform[np.newaxis, :]
    # Normalize
    peak = np.abs(waveform).max()
    if peak > 0:
        waveform = waveform / peak * 0.9
    write_audio(waveform, 44100, out_path)

    return {
        "audio_path": str(out_path),
        "duration": len(audio) / 44100,
    }


@router.post("/save")
def save_midi(req: SaveRequest) -> dict:
    MIDI_DIR.mkdir(parents=True, exist_ok=True)

    if req.label == "merged":
        midi_data = session.merged_midi_data
        if midi_data is None:
            raise HTTPException(404, "No merged MIDI available")
        out_path = MIDI_DIR / "merged.mid"
    else:
        stem_midi = session.stem_midi_data
        if req.label not in stem_midi:
            raise HTTPException(404, f"No MIDI for stem '{req.label}'")
        midi_data = stem_midi[req.label]
        out_path = MIDI_DIR / f"{req.label}.mid"

    from utils.midi_io import write_midi
    write_midi(midi_data, out_path)
    return {"path": str(out_path)}


@router.get("/stems")
def get_midi_stems() -> dict:
    stem_midi = session.stem_midi_data
    labels = list(stem_midi.keys())
    stem_info = {}
    for label, midi_data in stem_midi.items():
        note_count = sum(len(inst.notes) for inst in midi_data.instruments)
        stem_info[label] = {"note_count": note_count}
    return {"labels": labels, "stem_info": stem_info}
