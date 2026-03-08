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
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from backend.api.acestep_wrapper import (
    _LANG_LABELS,
    create_sample,
    dataset_auto_label_async,
    dataset_auto_label_status,
    dataset_load,
    dataset_preprocess_async,
    dataset_preprocess_status,
    dataset_sample_update,
    dataset_samples,
    dataset_save,
    dataset_scan,
    format_input,
    get_audio_bytes,
    health_check,
    lora_load as _lora_load,
    lora_scale as _lora_scale,
    lora_status as _lora_status,
    lora_toggle as _lora_toggle,
    lora_unload as _lora_unload,
    query_result,
    reinitialize_service,
    release_task,
    training_export,
    training_start,
    training_start_lokr,
    training_status,
    training_stop,
)
from backend.services import acestep_state
from backend.services.session_store import session
from utils.paths import COMPOSE_DIR

import os

router = APIRouter(prefix="/api/compose", tags=["compose"])

# ---------------------------------------------------------------------------
# LoRA adapter directory
# ---------------------------------------------------------------------------

_LORA_DIR = Path(os.environ.get(
    "LORA_DIR",
    str(Path(__file__).parent.parent.parent / "Ace-Step-Wrangler" / "loras"),
))

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

_LM_MODEL = {
    "none": None,
    "0.6b": "acestep-5Hz-lm-0.6B",
    "1.7b": "acestep-5Hz-lm-1.7B",
    "4b": "acestep-5Hz-lm-4B",
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
    lm_model: str = "1.7b"
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

    # Analyze task types (extract / lego / complete)
    track_name: Optional[str] = None
    track_classes: Optional[List[str]] = None


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

    lm_path = _LM_MODEL.get(req.lm_model)
    if lm_path:
        payload["lm_model_path"] = lm_path

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
    elif req.task_type in ("extract", "lego", "complete"):
        payload["task_type"] = req.task_type
        if req.src_audio_path:
            payload["src_audio_path"] = req.src_audio_path
        if req.track_name:
            payload["track_name"] = req.track_name
        if req.track_classes:
            payload["track_classes"] = req.track_classes

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


# ---------------------------------------------------------------------------
# LoRA adapter management
# ---------------------------------------------------------------------------


class LoRALoadRequest(BaseModel):
    lora_path: str
    adapter_name: Optional[str] = None


class LoRAToggleRequest(BaseModel):
    use_lora: bool


class LoRAScaleRequest(BaseModel):
    scale: float
    adapter_name: Optional[str] = None


@router.post("/lora/load")
async def lora_load_route(req: LoRALoadRequest):
    try:
        return await _lora_load(req.lora_path, req.adapter_name)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/lora/unload")
async def lora_unload_route():
    try:
        return await _lora_unload()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/lora/toggle")
async def lora_toggle_route(req: LoRAToggleRequest):
    try:
        return await _lora_toggle(req.use_lora)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/lora/scale")
async def lora_scale_route(req: LoRAScaleRequest):
    try:
        return await _lora_scale(req.scale, req.adapter_name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.get("/lora/status")
async def lora_status_route():
    try:
        return await _lora_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.get("/lora/browse")
async def lora_browse():
    """List available LoRA/LoKR adapters in the configured loras directory."""
    adapters = []
    if not _LORA_DIR.is_dir():
        return {"adapters": adapters, "lora_dir": str(_LORA_DIR)}

    for entry in sorted(_LORA_DIR.iterdir()):
        if entry.name.startswith("."):
            continue
        # PEFT LoRA: directory with adapter_config.json
        if entry.is_dir() and (entry / "adapter_config.json").exists():
            size_bytes = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            adapters.append({
                "name": entry.name,
                "path": str(entry),
                "type": "lora",
                "size_mb": round(size_bytes / 1_048_576, 1),
            })
        # LoKR: single .safetensors file
        elif entry.is_file() and entry.suffix == ".safetensors":
            adapters.append({
                "name": entry.name,
                "path": str(entry),
                "type": "lokr",
                "size_mb": round(entry.stat().st_size / 1_048_576, 1),
            })

    return {"adapters": adapters, "lora_dir": str(_LORA_DIR)}


# ---------------------------------------------------------------------------
# Training pipeline
# ---------------------------------------------------------------------------

_TRAIN_DIR = Path(os.environ.get(
    "TRAIN_DIR",
    str(Path(__file__).parent.parent.parent / "Ace-Step-Wrangler" / "training"),
))
_TRAIN_AUDIO_DIR = _TRAIN_DIR / "audio"
_TRAIN_TENSOR_DIR = _TRAIN_DIR / "tensors"
_TRAIN_OUTPUT_DIR = _TRAIN_DIR / "output"
_TRAIN_SNAPSHOTS_DIR = _TRAIN_DIR / "snapshots"
_TRAIN_DATASET_FILE = _TRAIN_DIR / "dataset.json"

for _d in (_TRAIN_DIR, _TRAIN_AUDIO_DIR, _TRAIN_TENSOR_DIR, _TRAIN_OUTPUT_DIR, _TRAIN_SNAPSHOTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

_SIDECAR_SUFFIX = ".stemforge.json"
_SIDECAR_KEYS = ("caption", "genre", "lyrics", "bpm", "keyscale", "timesignature",
                 "language", "is_instrumental", "prompt_override")


async def _write_sidecars() -> int:
    """Write .stemforge.json sidecar files for all labeled samples.

    Called after labeling completes or manual edits are saved so that
    captions survive re-scans and interruptions.
    """
    try:
        data = await dataset_samples()
    except Exception:
        return 0
    samples = data.get("samples", [])
    written = 0
    for s in samples:
        if not s.get("labeled") and not s.get("caption"):
            continue
        filename = s.get("filename", "")
        if not filename:
            continue
        sidecar = _TRAIN_AUDIO_DIR / (filename + _SIDECAR_SUFFIX)
        payload = {k: s.get(k) for k in _SIDECAR_KEYS if s.get(k) is not None}
        if payload:
            sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            written += 1
    return written


async def _apply_sidecars() -> int:
    """Read .stemforge.json sidecars and apply them to scanned samples.

    Called after scan so that previously labeled files keep their captions.
    """
    try:
        data = await dataset_samples()
    except Exception:
        return 0
    samples = data.get("samples", [])
    applied = 0
    for i, s in enumerate(samples):
        if s.get("labeled") or s.get("caption"):
            continue
        filename = s.get("filename", "")
        if not filename:
            continue
        sidecar = _TRAIN_AUDIO_DIR / (filename + _SIDECAR_SUFFIX)
        if not sidecar.is_file():
            continue
        try:
            meta = json.loads(sidecar.read_text())
        except Exception:
            continue
        if not meta:
            continue
        try:
            await dataset_sample_update(i, meta)
            applied += 1
        except Exception:
            pass
    return applied


class TrainScanRequest(BaseModel):
    stems_mode: bool = False


class TrainLabelRequest(BaseModel):
    lm_model_path: str = ""
    stems_mode: bool = False


class SampleUpdateRequest(BaseModel):
    caption: str = ""
    genre: str = ""
    mood: str = ""
    lyrics: str = ""
    timesignature: str = ""
    language: str = "unknown"
    is_instrumental: bool = True


class TrainStartRequest(BaseModel):
    adapter_type: str = "lora"
    lora_rank: int = 64
    lora_alpha: int = 128
    lora_dropout: float = 0.1
    learning_rate: float = 0.0001
    train_epochs: int = 10
    train_batch_size: int = 1
    gradient_accumulation: int = 4
    save_every_n_epochs: int = 5
    training_seed: int = 42
    gradient_checkpointing: bool = True
    tensor_dir: str = ""
    output_dir: str = ""


class TrainExportRequest(BaseModel):
    name: str
    output_dir: str = ""


class SnapshotRequest(BaseModel):
    name: str


def _safe_filename(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9._-]', '_', name)[:128]


@router.post("/train/upload")
async def train_upload(files: List[UploadFile]):
    """Upload audio files for training."""
    uploaded, skipped = [], []
    for f in files:
        safe_name = _safe_filename(f.filename or "audio.wav")
        dest = _TRAIN_AUDIO_DIR / safe_name
        if dest.exists():
            skipped.append(safe_name)
            continue
        content = await f.read()
        dest.write_bytes(content)
        uploaded.append(safe_name)

    audio_files = sorted(p.name for p in _TRAIN_AUDIO_DIR.iterdir()
                         if p.is_file() and not p.name.endswith(_SIDECAR_SUFFIX))
    return {"uploaded": uploaded, "skipped": skipped, "files": audio_files, "audio_dir": str(_TRAIN_AUDIO_DIR)}


@router.post("/train/clear")
async def train_clear():
    """Delete all audio, sidecar, and tensor files."""
    audio_removed = sum(1 for f in _TRAIN_AUDIO_DIR.iterdir()
                        if f.is_file() and not f.name.endswith(_SIDECAR_SUFFIX) and f.unlink() is None)
    sidecar_removed = sum(1 for f in _TRAIN_AUDIO_DIR.glob(f"*{_SIDECAR_SUFFIX}") if f.unlink() is None)
    tensor_removed = sum(1 for f in _TRAIN_TENSOR_DIR.rglob("*.pt") if f.unlink() is None)
    if _TRAIN_DATASET_FILE.exists():
        _TRAIN_DATASET_FILE.unlink()
    return {"removed": {"audio": audio_removed, "sidecars": sidecar_removed, "tensors": tensor_removed}}


@router.get("/train/pipeline-state")
async def train_pipeline_state():
    """Report disk state for pipeline recovery."""
    audio_files = sorted(p.name for p in _TRAIN_AUDIO_DIR.iterdir()
                         if p.is_file() and not p.name.endswith(_SIDECAR_SUFFIX)) if _TRAIN_AUDIO_DIR.is_dir() else []
    tensor_count = sum(1 for _ in _TRAIN_TENSOR_DIR.rglob("*.pt")) if _TRAIN_TENSOR_DIR.is_dir() else 0
    return {
        "audio_files": audio_files,
        "audio_count": len(audio_files),
        "has_audio": len(audio_files) > 0,
        "has_tensors": tensor_count > 0,
        "tensor_count": tensor_count,
        "has_saved_dataset": _TRAIN_DATASET_FILE.exists(),
    }


@router.post("/train/scan")
async def train_scan(req: TrainScanRequest):
    """Load uploaded audio into AceStep dataset."""
    try:
        payload = {"audio_dir": str(_TRAIN_AUDIO_DIR)}
        if req.stems_mode:
            payload["stems_mode"] = True
        result = await dataset_scan(payload)
        # Restore captions from sidecar files written during previous labeling
        restored = await _apply_sidecars()
        if restored:
            await dataset_save(str(_TRAIN_DATASET_FILE))
            result["restored_captions"] = restored
        return result
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/train/label")
async def train_label(req: TrainLabelRequest):
    """Start async auto-labeling."""
    try:
        payload: dict = {"only_unlabeled": True}
        if req.lm_model_path:
            payload["lm_model_path"] = req.lm_model_path
        if req.stems_mode:
            payload["stems_mode"] = True
        return await dataset_auto_label_async(payload)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.get("/train/label/status")
async def train_label_status():
    """Poll auto-label progress."""
    try:
        return await dataset_auto_label_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.get("/train/samples")
async def train_samples():
    """List loaded dataset samples."""
    try:
        return await dataset_samples()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.put("/train/sample/{sample_idx}")
async def train_sample_update(sample_idx: int, req: SampleUpdateRequest):
    """Update sample metadata, auto-save, and write caption sidecar."""
    try:
        result = await dataset_sample_update(sample_idx, req.model_dump())
        await dataset_save(str(_TRAIN_DATASET_FILE))
        await _write_sidecars()
        return result
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/train/save")
async def train_save():
    """Save dataset to disk and write caption sidecars."""
    try:
        result = await dataset_save(str(_TRAIN_DATASET_FILE))
        await _write_sidecars()
        return result
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/train/load")
async def train_load():
    """Load saved dataset from disk."""
    try:
        return await dataset_load(str(_TRAIN_DATASET_FILE))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/train/preprocess")
async def train_preprocess():
    """Start async preprocessing into tensors."""
    try:
        return await dataset_preprocess_async(str(_TRAIN_TENSOR_DIR))
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.get("/train/preprocess/status")
async def train_preprocess_status(task_id: Optional[str] = None):
    """Poll preprocessing progress."""
    try:
        return await dataset_preprocess_status(task_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/train/start")
async def train_start(req: TrainStartRequest):
    """Start LoRA/LoKR training."""
    payload = {
        "lora_rank": req.lora_rank,
        "lora_alpha": req.lora_alpha,
        "lora_dropout": req.lora_dropout,
        "learning_rate": req.learning_rate,
        "train_epochs": req.train_epochs,
        "train_batch_size": req.train_batch_size,
        "gradient_accumulation": req.gradient_accumulation,
        "save_every_n_epochs": req.save_every_n_epochs,
        "training_seed": req.training_seed,
        "gradient_checkpointing": req.gradient_checkpointing,
        "tensor_dir": req.tensor_dir or str(_TRAIN_TENSOR_DIR),
        "output_dir": req.output_dir or str(_TRAIN_OUTPUT_DIR),
    }
    try:
        if req.adapter_type == "lokr":
            return await training_start_lokr(payload)
        return await training_start(payload)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.get("/train/status")
async def train_status():
    """Poll training status."""
    try:
        return await training_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/train/stop")
async def train_stop():
    """Stop current training run."""
    try:
        return await training_stop()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/train/export")
async def train_export(req: TrainExportRequest):
    """Export trained adapter to loras/ directory."""
    try:
        export_path = str(_LORA_DIR / _safe_filename(req.name))
        lora_output_dir = req.output_dir or str(_TRAIN_OUTPUT_DIR)
        return await training_export(export_path, lora_output_dir)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.post("/train/reinitialize")
async def train_reinitialize():
    """Reload the generation model after training."""
    try:
        return await reinitialize_service()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AceStep error: {exc}")


@router.get("/train/snapshots")
async def train_snapshots():
    """List saved snapshots."""
    snapshots = []
    if not _TRAIN_SNAPSHOTS_DIR.is_dir():
        return {"snapshots": snapshots}

    for entry in sorted(_TRAIN_SNAPSHOTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        meta = {}
        meta_file = entry / "meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                pass
        size_bytes = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
        snapshots.append({"name": entry.name, "meta": meta, "size_mb": round(size_bytes / 1_048_576, 1)})

    return {"snapshots": snapshots}


@router.post("/train/snapshots/save")
async def train_snapshot_save(req: SnapshotRequest):
    """Save current dataset + tensors as a named snapshot."""
    safe_name = _safe_filename(req.name)
    snap_dir = _TRAIN_SNAPSHOTS_DIR / safe_name
    snap_dir.mkdir(parents=True, exist_ok=True)

    try:
        await dataset_save(str(_TRAIN_DATASET_FILE))
    except Exception:
        pass

    if _TRAIN_DATASET_FILE.exists():
        shutil.copy2(_TRAIN_DATASET_FILE, snap_dir / "dataset.json")

    snap_tensor_dir = snap_dir / "tensors"
    if _TRAIN_TENSOR_DIR.is_dir():
        if snap_tensor_dir.exists():
            shutil.rmtree(snap_tensor_dir)
        shutil.copytree(_TRAIN_TENSOR_DIR, snap_tensor_dir)

    meta = {
        "saved": datetime.now(timezone.utc).isoformat(),
        "has_dataset": _TRAIN_DATASET_FILE.exists(),
        "tensor_count": sum(1 for _ in _TRAIN_TENSOR_DIR.rglob("*.pt")) if _TRAIN_TENSOR_DIR.is_dir() else 0,
    }
    (snap_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return {"name": safe_name, "meta": meta}


@router.post("/train/snapshots/load")
async def train_snapshot_load(req: SnapshotRequest):
    """Load a named snapshot back into the working directory."""
    safe_name = _safe_filename(req.name)
    snap_dir = _TRAIN_SNAPSHOTS_DIR / safe_name
    if not snap_dir.is_dir():
        raise HTTPException(404, f"Snapshot not found: {safe_name}")

    snap_dataset = snap_dir / "dataset.json"
    if snap_dataset.exists():
        shutil.copy2(snap_dataset, _TRAIN_DATASET_FILE)

    snap_tensors = snap_dir / "tensors"
    if snap_tensors.is_dir():
        if _TRAIN_TENSOR_DIR.exists():
            shutil.rmtree(_TRAIN_TENSOR_DIR)
        shutil.copytree(snap_tensors, _TRAIN_TENSOR_DIR)

    try:
        if _TRAIN_DATASET_FILE.exists():
            await dataset_load(str(_TRAIN_DATASET_FILE))
    except Exception:
        pass

    return {"name": safe_name, "restored": True}


@router.delete("/train/snapshots/{name}")
async def train_snapshot_delete(name: str):
    """Delete a named snapshot."""
    safe_name = _safe_filename(name)
    snap_dir = _TRAIN_SNAPSHOTS_DIR / safe_name
    if snap_dir.is_dir():
        shutil.rmtree(snap_dir)
    return {"deleted": safe_name}
