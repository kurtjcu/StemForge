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
_TIMEOUT_LORA = httpx.Timeout(300.0)


def _base_url() -> str:
    return f"http://localhost:{get_port()}"


def _unwrap(body: dict) -> dict:
    """Strip AceStep's standard {data: ..., code: ..., timestamp: ...} envelope.

    AceStep wraps all responses in this format.  The generation helpers
    (release_task, query_result) already unwrap manually; this helper
    provides a consistent unwrapper for LoRA and training endpoints.
    """
    if isinstance(body.get("data"), (dict, list)):
        return body["data"]
    return body


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
                "seed_value": item.get("seed_value", ""),
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


# ─── LoRA adapter management ─────────────────────────────────────────────


async def lora_load(lora_path: str, adapter_name: str | None = None) -> dict:
    """Load a LoRA/LoKR adapter into the active model."""
    payload: dict = {"lora_path": lora_path}
    if adapter_name:
        payload["adapter_name"] = adapter_name
    async with httpx.AsyncClient(timeout=_TIMEOUT_LORA) as client:
        r = await client.post(f"{_base_url()}/v1/lora/load", json=payload)
        r.raise_for_status()
        return _unwrap(r.json())


async def lora_unload() -> dict:
    """Unload all LoRA adapters and restore the base model."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_LORA) as client:
        r = await client.post(f"{_base_url()}/v1/lora/unload")
        r.raise_for_status()
        return _unwrap(r.json())


async def lora_toggle(use_lora: bool) -> dict:
    """Enable or disable the loaded LoRA adapter."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.post(f"{_base_url()}/v1/lora/toggle", json={"use_lora": use_lora})
        r.raise_for_status()
        return _unwrap(r.json())


async def lora_scale(scale: float, adapter_name: str | None = None) -> dict:
    """Set the LoRA influence scale (0.0–1.0)."""
    payload: dict = {"scale": scale}
    if adapter_name:
        payload["adapter_name"] = adapter_name
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.post(f"{_base_url()}/v1/lora/scale", json=payload)
        r.raise_for_status()
        return _unwrap(r.json())


async def lora_status() -> dict:
    """Get current LoRA adapter state."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.get(f"{_base_url()}/v1/lora/status")
        r.raise_for_status()
        return _unwrap(r.json())


# ─── Training pipeline ────────────────────────────────────────────────────

_TIMEOUT_TRAIN = httpx.Timeout(300.0)


async def dataset_scan(payload: dict) -> dict:
    """Load audio files into AceStep dataset."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_TRAIN) as client:
        r = await client.post(f"{_base_url()}/v1/dataset/scan", json=payload)
        r.raise_for_status()
        return _unwrap(r.json())


async def dataset_preprocess_async(output_dir: str) -> dict:
    """Start async preprocessing of dataset into tensors."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_TRAIN) as client:
        r = await client.post(f"{_base_url()}/v1/dataset/preprocess_async", json={"output_dir": output_dir})
        r.raise_for_status()
        return _unwrap(r.json())


async def dataset_preprocess_status(task_id: str | None = None) -> dict:
    """Poll preprocessing progress."""
    url = f"{_base_url()}/v1/dataset/preprocess_status"
    if task_id:
        url += f"/{task_id}"
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.get(url)
        r.raise_for_status()
        return _unwrap(r.json())


async def dataset_auto_label_async(payload: dict) -> dict:
    """Start async auto-labeling of dataset samples."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_TRAIN) as client:
        r = await client.post(f"{_base_url()}/v1/dataset/auto_label_async", json=payload)
        r.raise_for_status()
        return _unwrap(r.json())


async def dataset_auto_label_status() -> dict:
    """Poll auto-label progress."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.get(f"{_base_url()}/v1/dataset/auto_label_status")
        r.raise_for_status()
        return _unwrap(r.json())


async def dataset_sample_update(sample_idx: int, payload: dict) -> dict:
    """Update a single sample's metadata."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.put(f"{_base_url()}/v1/dataset/sample/{sample_idx}", json=payload)
        r.raise_for_status()
        return _unwrap(r.json())


async def dataset_samples() -> dict:
    """List all loaded dataset samples."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.get(f"{_base_url()}/v1/dataset/samples")
        r.raise_for_status()
        return _unwrap(r.json())


async def dataset_save(file_path: str) -> dict:
    """Save dataset to disk."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.post(f"{_base_url()}/v1/dataset/save", json={"file_path": file_path})
        r.raise_for_status()
        return _unwrap(r.json())


async def dataset_load(file_path: str) -> dict:
    """Load dataset from disk."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.get(f"{_base_url()}/v1/dataset/load", params={"file_path": file_path})
        r.raise_for_status()
        return _unwrap(r.json())


async def training_start(payload: dict) -> dict:
    """Start LoRA training."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_TRAIN) as client:
        r = await client.post(f"{_base_url()}/v1/training/start", json=payload)
        r.raise_for_status()
        return _unwrap(r.json())


async def training_start_lokr(payload: dict) -> dict:
    """Start LoKR training."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_TRAIN) as client:
        r = await client.post(f"{_base_url()}/v1/training/start_lokr", json=payload)
        r.raise_for_status()
        return _unwrap(r.json())


async def training_status() -> dict:
    """Get current training status."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.get(f"{_base_url()}/v1/training/status")
        r.raise_for_status()
        return _unwrap(r.json())


async def training_stop() -> dict:
    """Stop the current training run."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_POLL) as client:
        r = await client.post(f"{_base_url()}/v1/training/stop")
        r.raise_for_status()
        return _unwrap(r.json())


async def training_export(export_path: str, lora_output_dir: str) -> dict:
    """Export trained adapter to loras/ directory."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_TRAIN) as client:
        r = await client.post(f"{_base_url()}/v1/training/export", json={
            "export_path": export_path, "lora_output_dir": lora_output_dir,
        })
        r.raise_for_status()
        return _unwrap(r.json())


async def reinitialize_service() -> dict:
    """Reload the generation model after training."""
    async with httpx.AsyncClient(timeout=_TIMEOUT_TRAIN) as client:
        r = await client.post(f"{_base_url()}/v1/reinitialize")
        r.raise_for_status()
        return _unwrap(r.json())
