"""Mix track management and render endpoints."""

from __future__ import annotations

import copy
import pathlib
import uuid

import numpy as np
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import session, TrackState
from utils.paths import MIX_DIR, OUTPUT_BASE

router = APIRouter(prefix="/api/mix", tags=["mix"])

_MIX_UPLOAD_DIR = OUTPUT_BASE / "mix_uploads"


class TrackUpdate(BaseModel):
    track_id: str
    enabled: bool | None = None
    volume: float | None = None
    program: int | None = None
    is_drum: bool | None = None


@router.get("/tracks")
def get_tracks() -> dict:
    return {"tracks": session.to_dict()["mix_tracks"]}


@router.post("/tracks")
def update_track(req: TrackUpdate) -> dict:
    track = session.get_track(req.track_id)
    if not track:
        raise HTTPException(404, f"Track '{req.track_id}' not found")

    if req.enabled is not None:
        track.enabled = req.enabled
    if req.volume is not None:
        track.volume = max(0.0, min(1.0, req.volume))
    if req.program is not None:
        track.program = req.program
    if req.is_drum is not None:
        track.is_drum = req.is_drum

    return {"status": "updated", "track_id": req.track_id}


@router.delete("/tracks/{track_id}")
def remove_track(track_id: str) -> dict:
    if not session.remove_track(track_id):
        raise HTTPException(404, f"Track '{track_id}' not found")
    return {"status": "removed"}


@router.post("/add-audio")
async def add_audio_track(file: UploadFile = File(...)) -> dict:
    import shutil

    _MIX_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = _MIX_UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{file.filename}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    track = TrackState(
        track_id=uuid.uuid4().hex[:8],
        label=file.filename or "Audio Track",
        source="audio",
        path=dest,
    )
    session.add_track(track)
    return {"track_id": track.track_id, "label": track.label}


@router.post("/add-midi")
async def add_midi_track(file: UploadFile = File(...)) -> dict:
    import shutil

    _MIX_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = _MIX_UPLOAD_DIR / f"{uuid.uuid4().hex[:8]}_{file.filename}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    import pretty_midi
    midi_data = pretty_midi.PrettyMIDI(str(dest))

    track = TrackState(
        track_id=uuid.uuid4().hex[:8],
        label=file.filename or "MIDI Track",
        source="midi",
        path=dest,
        midi_data=midi_data,
    )
    session.add_track(track)
    return {"track_id": track.track_id, "label": track.label}


def _run_mix_render(job_id: str) -> dict:
    """Render all enabled tracks to a single audio file."""
    progress_cb = job_manager.make_progress_callback(job_id)
    tracks = session.mix_tracks

    enabled = [t for t in tracks if t.enabled]
    if not enabled:
        raise ValueError("No enabled tracks to render")

    sr = 44100
    max_samples = 0
    rendered: list[tuple[np.ndarray, float]] = []

    progress_cb(0.05, "Rendering tracks...")

    for i, track in enumerate(enabled):
        progress_cb(0.05 + 0.7 * i / len(enabled), f"Rendering {track.label}...")

        if track.source == "audio" and track.path:
            from utils.audio_io import read_audio
            waveform, file_sr = read_audio(track.path, mono=False, target_rate=sr)
            # Convert to mono for mixing simplicity
            if waveform.shape[0] > 1:
                audio = waveform.mean(axis=0)
            else:
                audio = waveform[0]
            rendered.append((audio, track.volume))
            max_samples = max(max_samples, len(audio))

        elif track.source == "midi" and track.midi_data:
            midi_data = copy.deepcopy(track.midi_data)
            for inst in midi_data.instruments:
                inst.program = track.program
                inst.is_drum = track.is_drum
            audio = midi_data.fluidsynth(fs=sr)
            if audio is not None and len(audio) > 0:
                audio = audio.astype(np.float32)
                peak = np.abs(audio).max()
                if peak > 0:
                    audio = audio / peak
                rendered.append((audio, track.volume))
                max_samples = max(max_samples, len(audio))

    if not rendered:
        raise ValueError("No audio rendered from enabled tracks")

    progress_cb(0.80, "Mixing...")

    # Mix all tracks
    mix = np.zeros(max_samples, dtype=np.float32)
    for audio, volume in rendered:
        padded = np.zeros(max_samples, dtype=np.float32)
        padded[:len(audio)] = audio
        mix += padded * volume

    # Normalize
    peak = np.abs(mix).max()
    if peak > 0:
        mix = mix / peak * 0.9

    # Write stereo
    stereo = np.stack([mix, mix])  # (2, samples)

    MIX_DIR.mkdir(parents=True, exist_ok=True)
    out_path = MIX_DIR / f"mix_{uuid.uuid4().hex[:6]}.flac"

    from utils.audio_io import write_audio
    write_audio(stereo, sr, out_path, fmt="flac")

    session.mix_path = out_path
    progress_cb(1.0, "Done")

    return {"mix_path": str(out_path), "duration": max_samples / sr}


@router.post("/render")
def start_mix_render() -> dict:
    tracks = session.mix_tracks
    enabled = [t for t in tracks if t.enabled]
    if not enabled:
        raise HTTPException(400, "No enabled tracks to render")

    job_id = job_manager.create_job("mix")
    job_manager.run_job(job_id, _run_mix_render, job_id)
    return {"job_id": job_id}
