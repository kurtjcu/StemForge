"""Thin async wrapper around the AceStep local REST API.

AceStep runs as a separate process. We never import AceStep directly — all
communication is via HTTP. The base URL is resolved dynamically from
acestep_state so the --acestep-port flag propagates automatically.

Key quirk: /query_result returns `result` as a JSON *string*, not a nested
object. parse_result() handles that.
"""

import json
import mimetypes
from pathlib import Path

import httpx

from backend.services.acestep_state import get_port

_TIMEOUT_SUBMIT = httpx.Timeout(30.0)
_TIMEOUT_POLL = httpx.Timeout(10.0)
_TIMEOUT_AUDIO = httpx.Timeout(60.0)


def _base_url() -> str:
    return f"http://localhost:{get_port()}"


async def health_check() -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.get(f"{_base_url()}/health")
        r.raise_for_status()
        return r.json()


async def release_task(payload: dict) -> str:
    """Submit a generation task. Returns the task_id string."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_SUBMIT) as client:
        r = await client.post(f"{_base_url()}/release_task", json=payload)
        r.raise_for_status()
        body = r.json()
        return body["data"]["task_id"]


async def query_result(task_id: str) -> dict:
    """Poll a task. Returns a normalised dict.

    NOTE: AceStep returns `result` as a JSON *string* — we parse it here.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.post(
            f"{_base_url()}/query_result",
            json={"task_id_list": [task_id]},
        )
        r.raise_for_status()
        body = r.json()
        entry = body["data"][0]
        code = entry["status"]  # 0=running, 1=succeeded, 2=failed

    if code == 0:
        return {"status": "processing", "results": None}
    if code == 2:
        return {"status": "error", "results": None}

    items = json.loads(entry["result"])
    return {
        "status": "done",
        "results": [
            {
                "audio_url": item.get("file", ""),
                "meta": item.get("metas"),
                "prompt": item.get("prompt", ""),
                "lyrics": item.get("lyrics", ""),
            }
            for item in items
        ],
    }


_LANG_LABELS: dict[str, str] = {
    "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
    "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
    "it": "Italian", "ru": "Russian", "ar": "Arabic", "hi": "Hindi",
}


async def create_sample(query: str, language: str = "en") -> str:
    """Submit a lyrics-generation task via /release_task with sample_query."""
    label = _LANG_LABELS.get(language)
    enriched_query = f"{query}. {label} vocals." if label else query
    async with httpx.AsyncClient(timeout=_TIMEOUT_SUBMIT) as client:
        r = await client.post(
            f"{_base_url()}/release_task",
            json={"sample_query": enriched_query, "vocal_language": language},
        )
        r.raise_for_status()
        body = r.json()
        return body["data"]["task_id"]


async def format_input(lyrics: str) -> dict:
    """Call AceStep's /format_input LM endpoint for structured lyrics analysis."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_SUBMIT) as client:
        r = await client.post(
            f"{_base_url()}/format_input",
            json={"lyrics": lyrics},
        )
        r.raise_for_status()
        return r.json()


async def get_audio_bytes(path: str) -> tuple[bytes, str]:
    """Download audio and return (bytes, content_type).

    Paths without query strings are treated as local filesystem paths
    (both processes share the same filesystem). Query-string paths are
    forwarded to the AceStep HTTP server.
    """
    if "?" not in path:
        fp = Path(path)
        if not fp.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")
        ct = mimetypes.guess_type(str(fp))[0] or "audio/mpeg"
        return fp.read_bytes(), ct

    async with httpx.AsyncClient(timeout=_TIMEOUT_AUDIO) as client:
        r = await client.get(f"{_base_url()}{path}")
        r.raise_for_status()
        ct = r.headers.get("content-type", "audio/mpeg")
        return r.content, ct
