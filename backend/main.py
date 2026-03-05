"""FastAPI application — router registration and static file mount."""

from __future__ import annotations

import logging
import pathlib

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from utils.paths import OUTPUT_BASE, STEMS_DIR, MIDI_DIR, MUSICGEN_DIR, MIX_DIR, EXPORT_DIR, COMPOSE_DIR, SFX_DIR, VOICE_DIR
from utils.logging_utils import configure_logging

from backend.api import system, audio, separate, midi, generate, mix, export, compose, sfx, voice

configure_logging()
log = logging.getLogger("stemforge")

app = FastAPI(title="StemForge", version="0.2.0")

# Register API routers
app.include_router(system.router)
app.include_router(audio.router)
app.include_router(separate.router)
app.include_router(midi.router)
app.include_router(generate.router)
app.include_router(mix.router)
app.include_router(export.router)
app.include_router(compose.router)
app.include_router(sfx.router)
app.include_router(voice.router)

# Ensure output directories exist
for d in (OUTPUT_BASE, STEMS_DIR, MIDI_DIR, MUSICGEN_DIR, MIX_DIR, EXPORT_DIR, COMPOSE_DIR, SFX_DIR, VOICE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Mount assets (icons, etc.)
_assets_dir = pathlib.Path(__file__).parent.parent / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

# Mount frontend static files (must be last so API routes take precedence)
_frontend_dir = pathlib.Path(__file__).parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")

log.info("StemForge backend ready")
