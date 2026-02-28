"""Compose endpoints — AceStep music generation, adapted from Wrangler's backend/main.py."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from backend.api.acestep_wrapper import (
    _LANG_LABELS,
    create_sample,
    format_input,
    get_audio_bytes,
    health_check,
    query_result,
    release_task,
)
from backend.services import acestep_state
from backend.services.session_store import session
from utils.paths import COMPOSE_DIR

router = APIRouter(prefix="/api/compose", tags=["compose"])

# ---------------------------------------------------------------------------
# In-process job store
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_pending: dict[str, dict] = {}
_uploads: dict[str, dict] = {}
_upload_dir = Path(tempfile.mkdtemp(prefix="stemforge-compose-"))

# ---------------------------------------------------------------------------
# Parameter mapping tables
# ---------------------------------------------------------------------------

_LYRIC_ADHERENCE = [3.0, 7.0, 12.0]
_QUALITY_STEPS = [15, 60, 120]

_GEN_MODEL = {
    "turbo": "acestep-v15-turbo",
    "sft": "acestep-v15-sft",
    "base": "acestep-v15-base",
}

_SCHEDULER = {
    "euler": "ode",
    "dpm": "dpm",
    "ddim": "ddim",
}

# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    style: str = ""
    lyrics: str = ""
    duration: float = 30.0
    lyric_adherence: int = 1
    creativity: float = 50.0
    quality: int = 1

    seed: Optional[int] = None
    gen_model: str = "turbo"
    batch_size: int = 1
    scheduler: str = "euler"
    audio_format: str = "mp3"

    key: str = ""
    bpm: Optional[int] = None
    time_signature: str = "4/4"

    guidance_scale_raw: Optional[float] = None
    audio_guidance_scale: Optional[float] = None
    inference_steps_raw: Optional[int] = None

    sample_query: Optional[str] = None
    vocal_language: str = "en"

    task_type: str = "text2music"
    src_audio_path: Optional[str] = None
    audio_cover_strength: Optional[float] = None
    repainting_start: Optional[float] = None
    repainting_end: Optional[float] = None


class GenerateLyricsRequest(BaseModel):
    description: str
    vocal_language: str = "en"


class EstimateDurationRequest(BaseModel):
    lyrics: str = ""
    bpm: Optional[int] = None
    time_signature: str = "4/4"
    lm_model: str = "1.7b"


class EstimateSectionsRequest(BaseModel):
    lyrics: str = ""
    duration: float = 30.0
    bpm: Optional[int] = None
    time_signature: str = "4/4"


class SendToSessionRequest(BaseModel):
    audio_path: str
    title: str = ""


# ---------------------------------------------------------------------------
# Guard
# ---------------------------------------------------------------------------


def _require_acestep() -> None:
    """Raise 503 if AceStep is not running. Distinguishes disabled/ready/starting/crashed."""
    state = acestep_state.get_status()
    status = state["status"]
    if status == "running":
        return
    if status == "disabled":
        raise HTTPException(503, "AceStep is disabled (start without --no-acestep)")
    if status == "ready":
        raise HTTPException(503, "AceStep is not started yet. Call POST /api/compose/start first.")
    if status == "starting":
        raise HTTPException(503, "AceStep is starting up — downloading models, please stand by")
    if status == "crashed":
        raise HTTPException(503, f"AceStep crashed: {state.get('error', 'unknown error')}")
    raise HTTPException(503, f"AceStep is {status}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_payload(req: GenerateRequest) -> dict:
    lyric_adherence = max(0, min(2, req.lyric_adherence))
    quality = max(0, min(2, req.quality))
    creativity = max(0.0, min(100.0, req.creativity))

    shift = round(5.0 - (creativity / 100.0) * 4.0, 2)

    song_parts = []
    if req.key:
        song_parts.append(req.key)
    if req.bpm:
        song_parts.append(f"{req.bpm} BPM")
    if song_parts:
        song_parts.append(f"{req.time_signature} time")
        suffix = ", ".join(song_parts)
        prompt = f"{req.style}, {suffix}" if req.style else suffix
    else:
        prompt = req.style

    guidance_scale = (
        req.guidance_scale_raw
        if req.guidance_scale_raw is not None
        else _LYRIC_ADHERENCE[lyric_adherence]
    )
    inference_steps = (
        req.inference_steps_raw
        if req.inference_steps_raw is not None
        else _QUALITY_STEPS[quality]
    )

    payload = {
        "prompt": prompt,
        "lyrics": req.lyrics,
        "audio_duration": req.duration,
        "guidance_scale": guidance_scale,
        "shift": shift,
        "inference_steps": inference_steps,
        "batch_size": max(1, req.batch_size),
        "use_random_seed": req.seed is None,
        "seed": req.seed if req.seed is not None else -1,
        "infer_method": _SCHEDULER.get(req.scheduler, "ode"),
        "audio_format": req.audio_format,
    }

    if req.audio_guidance_scale is not None:
        payload["audio_guidance_scale"] = req.audio_guidance_scale

    if req.sample_query:
        label = _LANG_LABELS.get(req.vocal_language, "")
        enriched = f"{req.sample_query}. {label} vocals." if label else req.sample_query
        payload["sample_query"] = enriched
        payload["vocal_language"] = req.vocal_language

    model_name = _GEN_MODEL.get(req.gen_model)
    if model_name:
        payload["model"] = model_name

    if req.task_type in ("cover", "repaint"):
        payload["task_type"] = req.task_type
        if req.src_audio_path:
            payload["src_audio_path"] = req.src_audio_path
        if req.task_type == "cover" and req.audio_cover_strength is not None:
            payload["audio_cover_strength"] = req.audio_cover_strength
        if req.task_type == "repaint":
            if req.repainting_start is not None:
                payload["repainting_start"] = req.repainting_start
            if req.repainting_end is not None:
                payload["repainting_end"] = req.repainting_end

    return payload


def _resolve_audio_path(path: str) -> str:
    if "?" in path:
        qs = parse_qs(urlparse(path).query)
        if "path" in qs:
            return qs["path"][0]
    return path


def _ensure_in_tmp(path: str) -> str:
    """Copy a file to the system temp dir if it isn't already there."""
    import os

    path = _resolve_audio_path(path)
    system_temp = os.path.realpath(tempfile.gettempdir())
    real = os.path.realpath(path)
    try:
        in_temp = os.path.commonpath([system_temp, real]) == system_temp
    except ValueError:
        in_temp = False
    if in_temp:
        return path
    suffix = Path(path).suffix or ".mp3"
    fd, tmp_path = tempfile.mkstemp(prefix="stemforge_src_", suffix=suffix)
    os.close(fd)
    shutil.copy2(real, tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Duration estimation — heuristic fallback
# ---------------------------------------------------------------------------

_SECTION_BARS: dict[str, int] = {
    "intro": 8, "verse": 16, "pre-chorus": 8, "prechorus": 8, "pre chorus": 8,
    "chorus": 8, "hook": 8, "bridge": 8, "outro": 8, "instrumental": 8,
    "break": 8, "interlude": 8, "refrain": 8, "drop": 8, "build": 8, "solo": 8,
}

_SECTION_RE = re.compile(r"^\[([^\]]+)\]", re.MULTILINE | re.IGNORECASE)


def _heuristic_seconds(lyrics: str, bpm: int, time_signature: str) -> float:
    headers = _SECTION_RE.findall(lyrics)
    try:
        num = int(time_signature.split("/")[0])
    except (ValueError, IndexError):
        num = 4

    def _lookup_bars(header: str) -> int:
        h = header.strip().lower()
        if h in _SECTION_BARS:
            return _SECTION_BARS[h]
        for key, bars in _SECTION_BARS.items():
            if h.startswith(key) or key in h:
                return bars
        return 8

    if not headers:
        total_bars = 16 * 2 + 8 * 2
    else:
        total_bars = sum(_lookup_bars(h) for h in headers)

    seconds = total_bars * num / bpm * 60
    seconds = round(seconds / 5) * 5
    return max(10.0, min(600.0, seconds))


def _estimate_sections(
    lyrics: str, duration: float, bpm: int, time_signature: str,
) -> list[dict]:
    headers = _SECTION_RE.findall(lyrics)
    if not headers:
        return []
    try:
        num = int(time_signature.split("/")[0])
    except (ValueError, IndexError):
        num = 4

    def _lookup_bars(header: str) -> int:
        h = header.strip().lower()
        if h in _SECTION_BARS:
            return _SECTION_BARS[h]
        for key, bars in _SECTION_BARS.items():
            if h.startswith(key) or key in h:
                return bars
        return 8

    sections = []
    for header in headers:
        bars = _lookup_bars(header)
        raw_secs = bars * num / bpm * 60
        sections.append({"name": header.strip(), "bars": bars, "raw_secs": raw_secs})

    total_raw = sum(s["raw_secs"] for s in sections)
    if total_raw <= 0:
        return []

    scale = duration / total_raw
    cursor = 0.0
    result = []
    for s in sections:
        scaled = s["raw_secs"] * scale
        result.append({
            "name": s["name"],
            "start": round(cursor, 2),
            "end": round(cursor + scaled, 2),
            "bars": s["bars"],
        })
        cursor += scaled
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health")
async def compose_health():
    state = acestep_state.get_status()
    result = {"acestep_status": state["status"], "port": state["port"]}
    if state["status"] == "running":
        try:
            upstream = await health_check()
            result["acestep_health"] = upstream
        except Exception as exc:
            result["acestep_health_error"] = str(exc)
    return result


@router.post("/start")
async def start_acestep():
    """Launch the AceStep subprocess on demand (first use). Idempotent."""
    state = acestep_state.get_status()
    if state["status"] == "disabled":
        raise HTTPException(400, "AceStep is disabled (started with --no-acestep)")
    if state["status"] in ("starting", "running"):
        return {"acestep_status": state["status"], "message": "Already started"}

    launched = acestep_state.launch()
    if not launched:
        return {"acestep_status": acestep_state.get_status()["status"], "message": "Launch skipped"}

    return {"acestep_status": "starting", "message": "AceStep is starting — downloading models if needed"}


@router.post("/generate")
async def generate(req: GenerateRequest):
    _require_acestep()
    if req.src_audio_path:
        safe_path = _ensure_in_tmp(req.src_audio_path)
        if safe_path != req.src_audio_path:
            req = req.model_copy(update={"src_audio_path": safe_path})
    payload = _build_payload(req)
    try:
        task_id = await release_task(payload)
    except Exception as exc:
        raise HTTPException(502, f"AceStep error: {exc}")

    _pending[task_id] = {"params": req.model_dump(), "format": req.audio_format}
    return {"task_id": task_id}


@router.get("/status/{task_id}")
async def status(task_id: str):
    _require_acestep()
    try:
        data = await query_result(task_id)
    except Exception as exc:
        raise HTTPException(502, f"AceStep error: {exc}")

    if data["status"] == "done" and task_id not in _jobs:
        pending = _pending.pop(task_id, {})
        _jobs[task_id] = {
            "results": data["results"],
            "params": pending.get("params", {}),
            "format": pending.get("format", "mp3"),
        }
    return data


@router.get("/audio")
async def audio_proxy(path: str):
    _require_acestep()
    resolved = _resolve_audio_path(path)
    fp = Path(resolved)
    if fp.is_file():
        ct = mimetypes.guess_type(str(fp))[0] or "audio/mpeg"
        return FileResponse(str(fp), media_type=ct)
    try:
        data, content_type = await get_audio_bytes(path)
    except Exception as exc:
        raise HTTPException(502, f"Audio fetch error: {exc}")
    return Response(content=data, media_type=content_type)


@router.get("/download/{job_id}/{index}/audio")
async def download_audio(job_id: str, index: int):
    job = _jobs.get(job_id)
    if not job or index >= len(job["results"]):
        raise HTTPException(404, "Result not found")
    audio_url = job["results"][index]["audio_url"]
    try:
        data, content_type = await get_audio_bytes(audio_url)
    except Exception as exc:
        raise HTTPException(502, f"Audio fetch error: {exc}")
    fmt = job["format"]
    filename = f"acestep-{job_id[:8]}-{index + 1}.{fmt}"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/download/{job_id}/{index}/json")
async def download_json(job_id: str, index: int):
    job = _jobs.get(job_id)
    if not job or index >= len(job["results"]):
        raise HTTPException(404, "Result not found")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": job["params"],
        "meta": job["results"][index].get("meta"),
    }
    filename = f"acestep-{job_id[:8]}-{index + 1}.json"
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/generate-lyrics")
async def generate_lyrics(req: GenerateLyricsRequest):
    _require_acestep()
    if not req.description.strip():
        raise HTTPException(422, "Description cannot be empty")
    try:
        task_id = await create_sample(req.description, req.vocal_language)
    except Exception as exc:
        raise HTTPException(502, f"AceStep error: {exc}")

    for _ in range(300):
        await asyncio.sleep(2)
        try:
            data = await query_result(task_id)
        except Exception as exc:
            raise HTTPException(502, f"AceStep poll error: {exc}")

        if data["status"] == "done":
            results = data.get("results") or []
            if not results:
                raise HTTPException(502, "No results returned")
            result = results[0]
            meta = result.get("meta") or {}
            raw_audio_url = result.get("audio_url", "")
            audio_path = ""
            if raw_audio_url:
                parsed = parse_qs(urlparse(raw_audio_url).query)
                audio_path = parsed.get("path", [""])[0]
            return {
                "caption": result.get("prompt", ""),
                "lyrics": result.get("lyrics", ""),
                "bpm": meta.get("bpm"),
                "key_scale": meta.get("keyscale", ""),
                "time_signature": meta.get("timesignature", "4/4"),
                "duration": meta.get("duration"),
                "audio_url": raw_audio_url,
                "audio_path": audio_path,
            }
        elif data["status"] == "error":
            raise HTTPException(502, "Lyrics generation failed")

    raise HTTPException(504, "Lyrics generation timed out")


@router.post("/estimate-duration")
async def estimate_duration(req: EstimateDurationRequest):
    bpm = req.bpm if req.bpm else 120
    if req.lm_model != "none" and req.lyrics.strip():
        try:
            _require_acestep()
            result = await format_input(req.lyrics)
            body = result if isinstance(result, dict) else {}
            for key in ("data", "result"):
                if isinstance(body.get(key), dict):
                    body = body[key]
                    break
            if "duration" in body:
                secs = float(body["duration"])
                secs = round(secs / 5) * 5
                secs = max(10.0, min(600.0, secs))
                return {"seconds": secs, "method": "lm"}
        except HTTPException:
            pass  # AceStep unavailable — fall through to heuristic
        except Exception:
            pass

    secs = _heuristic_seconds(req.lyrics, bpm, req.time_signature)
    resp: dict = {"seconds": secs, "method": "heuristic"}
    if not req.bpm:
        resp["assumed_bpm"] = 120
    return resp


@router.post("/estimate-sections")
async def estimate_sections(req: EstimateSectionsRequest):
    bpm = req.bpm if req.bpm else 120
    sections = _estimate_sections(req.lyrics, req.duration, bpm, req.time_signature)
    return {"sections": sections}


@router.post("/upload-audio")
async def upload_audio(file: UploadFile):
    _require_acestep()
    if not file.content_type or not file.content_type.startswith("audio/"):
        raise HTTPException(422, "Only audio files are supported")
    upload_id = uuid.uuid4().hex[:12]
    suffix = Path(file.filename or "audio").suffix or ".wav"
    dest = _upload_dir / f"{upload_id}{suffix}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    _uploads[upload_id] = {"path": str(dest), "filename": file.filename or "audio"}
    return {"upload_id": upload_id, "path": str(dest), "filename": file.filename}


@router.post("/send-to-session")
async def send_to_session(body: SendToSessionRequest):
    """Download composed audio from AceStep, save to COMPOSE_DIR, set as session audio."""
    resolved = _resolve_audio_path(body.audio_path)
    fp = Path(resolved)

    if fp.is_file():
        data = fp.read_bytes()
        ext = fp.suffix or ".mp3"
    else:
        try:
            data, content_type = await get_audio_bytes(body.audio_path)
        except Exception as exc:
            raise HTTPException(502, f"Audio fetch error: {exc}")
        ext_map = {"audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/flac": ".flac"}
        ext = ext_map.get(content_type, ".mp3")

    COMPOSE_DIR.mkdir(parents=True, exist_ok=True)
    dest = COMPOSE_DIR / f"composed_{uuid.uuid4().hex[:8]}{ext}"
    dest.write_bytes(data)

    from utils.audio_io import probe

    session.audio_path = dest
    info = probe(dest)
    session.audio_info = {
        "filename": dest.name,
        "path": str(dest),
        "duration": info.duration,
        "sample_rate": info.sample_rate,
        "channels": info.channels,
        "format": info.format,
    }

    return {
        "path": str(dest),
        "filename": dest.name,
        "duration": info.duration,
        "sample_rate": info.sample_rate,
        "channels": info.channels,
    }
