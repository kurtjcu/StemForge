"""FastAPI application — router registration and static file mount."""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, JSONResponse

from utils.paths import OUTPUT_BASE, STEMS_DIR, MIDI_DIR, MUSICGEN_DIR, MIX_DIR, EXPORT_DIR, COMPOSE_DIR, SFX_DIR, VOICE_DIR, ENHANCE_DIR
from utils.logging_utils import configure_logging

from backend.api import system, audio, separate, midi, generate, mix, export, compose, sfx, voice, enhance
from backend.services.session_store import registry
from backend.services.job_manager import job_manager

configure_logging()
log = logging.getLogger("stemforge")

app = FastAPI(title="StemForge", version="0.2.0")

# ---------------------------------------------------------------------------
# Multi-user configuration (all overridable via env)
# ---------------------------------------------------------------------------

MAX_USERS = int(os.environ.get("MAX_USERS", "0"))                # 0 = unlimited
MAX_JOBS_PER_USER = int(os.environ.get("MAX_JOBS_PER_USER", "3"))
SESSION_TIMEOUT_MIN = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "60"))
JOB_TTL_MIN = int(os.environ.get("JOB_TTL_MINUTES", "120"))


# ---------------------------------------------------------------------------
# User middleware — inject request.state.user from reverse proxy header
# ---------------------------------------------------------------------------

@app.middleware("http")
async def inject_user(request: Request, call_next):
    """Identify user from x-auth-user header (set by reverse proxy).

    Falls back to "local" for single-user dev mode.  Enforces capacity
    limits when MAX_USERS > 0.
    """
    user = request.headers.get("x-auth-user", "local")
    request.state.user = user

    # Capacity enforcement (skip for "local" — single-user dev mode)
    if user != "local" and MAX_USERS > 0:
        timeout = SESSION_TIMEOUT_MIN * 60
        # Check if this is a new user and we're at capacity
        existing_users = registry.list_users()
        if user not in existing_users:
            active = registry.active_count(timeout)
            if active >= MAX_USERS:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Server is at capacity. Try again later."},
                )

    # Touch session (creates if new, updates last_seen)
    registry.get(user)

    response = await call_next(request)
    return response


class NoCacheStaticMiddleware(BaseHTTPMiddleware):
    """Prevent browsers from caching frontend JS/CSS files during development."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        path = request.url.path
        if path.endswith((".js", ".css", ".html")) or path == "/":
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app.add_middleware(NoCacheStaticMiddleware)

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
app.include_router(enhance.router)

# Ensure output directories exist
for d in (OUTPUT_BASE, STEMS_DIR, MIDI_DIR, MUSICGEN_DIR, MIX_DIR, EXPORT_DIR, COMPOSE_DIR, SFX_DIR, VOICE_DIR, ENHANCE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Mount assets (icons, etc.)
_assets_dir = pathlib.Path(__file__).parent.parent / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

# Mount frontend static files (must be last so API routes take precedence)
_frontend_dir = pathlib.Path(__file__).parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


# ---------------------------------------------------------------------------
# Background cleanup task — expire stale sessions, jobs, locks
# ---------------------------------------------------------------------------

async def _cleanup_loop():
    """Periodically expire stale sessions and jobs."""
    while True:
        await asyncio.sleep(60)
        session_ttl = SESSION_TIMEOUT_MIN * 60
        job_ttl = JOB_TTL_MIN * 60

        expired_sessions = registry.expire(session_ttl)
        expired_jobs = job_manager.expire_jobs(job_ttl)

        if expired_sessions or expired_jobs:
            log.info("Cleanup: %d session(s), %d job(s) expired",
                     len(expired_sessions), expired_jobs)


@app.on_event("startup")
async def _start_cleanup():
    asyncio.create_task(_cleanup_loop())


log.info("StemForge backend ready")
