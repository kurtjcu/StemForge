"""RemoteComposeBackend — connects to a standalone Wrangler instance.

All compose calls are forwarded over HTTP to ``self._base_url``.
This backend does not manage any local subprocesses or GPU resources;
the remote host is responsible for those.

Snapshot management is not supported in remote mode because snapshots are
stored on the filesystem of whichever machine runs the training process.
Calling any snapshot method raises ``NotImplementedError`` with a clear
message so the limitation is explicit rather than silently broken.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from backend.compose_backend.protocol import (
    AdapterStatus,
    BackendMode,
    ComposeStatus,
    GenerateParams,
    GenerateResult,
    TaskStatus,
    TrainParams,
)

# Timeout budget mirrors acestep_wrapper.py
_T_POLL = httpx.Timeout(10.0)
_T_SUBMIT = httpx.Timeout(30.0)
_T_AUDIO = httpx.Timeout(60.0)
_T_LORA = httpx.Timeout(300.0)
_T_TRAIN = httpx.Timeout(300.0)


def _unwrap(body: dict) -> dict:
    """Strip AceStep's standard envelope: {data: ..., code: ..., timestamp: ...}."""
    if isinstance(body.get("data"), (dict, list)):
        return body["data"]
    return body


class RemoteComposeBackend:
    """ComposeBackend that forwards all calls to a remote Wrangler instance."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    # -- Lifecycle -------------------------------------------------------

    async def start(self) -> ComposeStatus:
        """Verify the remote is reachable.  start() is a no-op for remote mode —
        the user is responsible for managing the remote process."""
        return await self.get_status()

    async def shutdown(self) -> None:
        """No-op: the remote process is managed externally."""
        pass

    async def get_status(self) -> ComposeStatus:
        try:
            async with httpx.AsyncClient(timeout=_T_POLL) as client:
                r = await client.get(f"{self._base_url}/health")
                r.raise_for_status()
                body = r.json()
            # AceStep wraps responses: {"data": {...}, "code": 200, ...}
            inner = body.get("data") if isinstance(body.get("data"), dict) else body
            models_loaded = bool(inner.get("models_initialized"))
            status = "running" if models_loaded else "starting"
            return ComposeStatus(
                mode=BackendMode.REMOTE,
                status=status,
                models_loaded=models_loaded,
            )
        except Exception as exc:
            return ComposeStatus(
                mode=BackendMode.REMOTE,
                status="crashed",
                error=str(exc),
            )

    # -- Generation ------------------------------------------------------

    async def generate(self, params: GenerateParams) -> str:
        """Submit a generation task.  Payload must be in params.extra."""
        async with httpx.AsyncClient(timeout=_T_SUBMIT) as client:
            r = await client.post(f"{self._base_url}/release_task", json=params.extra)
            r.raise_for_status()
            body = r.json()
            return body["data"]["task_id"]

    async def poll_task(self, task_id: str) -> TaskStatus:
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.post(
                f"{self._base_url}/query_result",
                json={"task_id_list": [task_id]},
            )
            r.raise_for_status()
            body = r.json()
            entry = body["data"][0]
            code = entry["status"]  # 0=running, 1=succeeded, 2=failed

        if code == 0:
            return TaskStatus(status="processing", results=None)
        if code == 2:
            return TaskStatus(status="error", results=None)

        items = json.loads(entry["result"])
        results = [
            GenerateResult(
                audio_url=item.get("file", ""),
                meta=item.get("metas"),
                prompt=item.get("prompt", ""),
                lyrics=item.get("lyrics", ""),
                seed_value=item.get("seed_value", ""),
            )
            for item in items
        ]
        return TaskStatus(status="done", results=results)

    async def get_audio(self, path: str) -> tuple[bytes, str]:
        """Fetch audio bytes from the remote host."""
        async with httpx.AsyncClient(timeout=_T_AUDIO) as client:
            r = await client.get(f"{self._base_url}{path}")
            r.raise_for_status()
            ct = r.headers.get("content-type", "audio/mpeg")
            return r.content, ct

    async def format_lyrics(self, lyrics: str) -> dict:
        async with httpx.AsyncClient(timeout=_T_SUBMIT) as client:
            r = await client.post(
                f"{self._base_url}/format_input",
                json={"lyrics": lyrics},
            )
            r.raise_for_status()
            return r.json()

    async def create_sample(self, query: str, language: str = "en") -> str:
        async with httpx.AsyncClient(timeout=_T_SUBMIT) as client:
            r = await client.post(
                f"{self._base_url}/release_task",
                json={"sample_query": query, "vocal_language": language},
            )
            r.raise_for_status()
            return r.json()["data"]["task_id"]

    # -- Adapter management ---------------------------------------------

    async def load_adapter(self, adapter_path: str, adapter_name: str | None = None) -> dict:
        payload: dict[str, Any] = {"lora_path": adapter_path}
        if adapter_name:
            payload["adapter_name"] = adapter_name
        async with httpx.AsyncClient(timeout=_T_LORA) as client:
            r = await client.post(f"{self._base_url}/v1/lora/load", json=payload)
            r.raise_for_status()
            return _unwrap(r.json())

    async def unload_adapter(self) -> dict:
        async with httpx.AsyncClient(timeout=_T_LORA) as client:
            r = await client.post(f"{self._base_url}/v1/lora/unload")
            r.raise_for_status()
            return _unwrap(r.json())

    async def toggle_adapter(self, use_lora: bool) -> dict:
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.post(
                f"{self._base_url}/v1/lora/toggle",
                json={"use_lora": use_lora},
            )
            r.raise_for_status()
            return _unwrap(r.json())

    async def scale_adapter(self, scale: float, adapter_name: str | None = None) -> dict:
        payload: dict[str, Any] = {"scale": scale}
        if adapter_name:
            payload["adapter_name"] = adapter_name
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.post(f"{self._base_url}/v1/lora/scale", json=payload)
            r.raise_for_status()
            return _unwrap(r.json())

    async def adapter_status(self) -> AdapterStatus:
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.get(f"{self._base_url}/v1/lora/status")
            r.raise_for_status()
            raw = _unwrap(r.json())
        return AdapterStatus(
            loaded=raw.get("loaded", False),
            adapter_name=raw.get("adapter_name"),
            scale=raw.get("scale", 1.0),
            use_lora=raw.get("use_lora", True),
            raw=raw,
        )

    # -- Training -------------------------------------------------------

    async def dataset_scan(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=_T_TRAIN) as client:
            r = await client.post(f"{self._base_url}/v1/dataset/scan", json=payload)
            r.raise_for_status()
            return _unwrap(r.json())

    async def dataset_preprocess(self, output_dir: str) -> dict:
        async with httpx.AsyncClient(timeout=_T_TRAIN) as client:
            r = await client.post(
                f"{self._base_url}/v1/dataset/preprocess_async",
                json={"output_dir": output_dir},
            )
            r.raise_for_status()
            return _unwrap(r.json())

    async def dataset_preprocess_status(self, task_id: str | None = None) -> dict:
        url = f"{self._base_url}/v1/dataset/preprocess_status"
        if task_id:
            url += f"/{task_id}"
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.get(url)
            r.raise_for_status()
            return _unwrap(r.json())

    async def dataset_auto_label(self, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=_T_TRAIN) as client:
            r = await client.post(
                f"{self._base_url}/v1/dataset/auto_label_async",
                json=payload,
            )
            r.raise_for_status()
            return _unwrap(r.json())

    async def dataset_auto_label_status(self) -> dict:
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.get(f"{self._base_url}/v1/dataset/auto_label_status")
            r.raise_for_status()
            return _unwrap(r.json())

    async def dataset_sample_update(self, sample_idx: int, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.put(
                f"{self._base_url}/v1/dataset/sample/{sample_idx}",
                json=payload,
            )
            r.raise_for_status()
            return _unwrap(r.json())

    async def dataset_samples(self) -> dict:
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.get(f"{self._base_url}/v1/dataset/samples")
            r.raise_for_status()
            return _unwrap(r.json())

    async def dataset_save(self, file_path: str) -> dict:
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.post(
                f"{self._base_url}/v1/dataset/save",
                json={"file_path": file_path},
            )
            r.raise_for_status()
            return _unwrap(r.json())

    async def dataset_load(self, file_path: str) -> dict:
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.get(
                f"{self._base_url}/v1/dataset/load",
                params={"file_path": file_path},
            )
            r.raise_for_status()
            return _unwrap(r.json())

    async def train_start(self, params: TrainParams) -> dict:
        payload: dict[str, Any] = {
            "lora_rank": params.lora_rank,
            "lora_alpha": params.lora_alpha,
            "lora_dropout": params.lora_dropout,
            "learning_rate": params.learning_rate,
            "train_epochs": params.train_epochs,
            "train_batch_size": params.train_batch_size,
            "gradient_accumulation": params.gradient_accumulation,
            "save_every_n_epochs": params.save_every_n_epochs,
            "training_seed": params.training_seed,
            "gradient_checkpointing": params.gradient_checkpointing,
        }
        # tensor_dir and output_dir default to the remote host's own paths
        # when not supplied by the caller.
        if params.tensor_dir:
            payload["tensor_dir"] = params.tensor_dir
        if params.adapter_type == "lokr":
            if params.output_dir:
                payload["output_dir"] = params.output_dir
            async with httpx.AsyncClient(timeout=_T_TRAIN) as client:
                r = await client.post(
                    f"{self._base_url}/v1/training/start_lokr",
                    json=payload,
                )
                r.raise_for_status()
                return _unwrap(r.json())
        if params.output_dir:
            payload["lora_output_dir"] = params.output_dir
        async with httpx.AsyncClient(timeout=_T_TRAIN) as client:
            r = await client.post(
                f"{self._base_url}/v1/training/start",
                json=payload,
            )
            r.raise_for_status()
            return _unwrap(r.json())

    async def train_status(self) -> dict:
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.get(f"{self._base_url}/v1/training/status")
            r.raise_for_status()
            return _unwrap(r.json())

    async def train_stop(self) -> dict:
        async with httpx.AsyncClient(timeout=_T_POLL) as client:
            r = await client.post(f"{self._base_url}/v1/training/stop")
            r.raise_for_status()
            return _unwrap(r.json())

    async def train_export(self, export_path: str, lora_output_dir: str) -> dict:
        async with httpx.AsyncClient(timeout=_T_TRAIN) as client:
            r = await client.post(
                f"{self._base_url}/v1/training/export",
                json={"export_path": export_path, "lora_output_dir": lora_output_dir},
            )
            r.raise_for_status()
            return _unwrap(r.json())

    async def reinitialize(self) -> dict:
        async with httpx.AsyncClient(timeout=_T_TRAIN) as client:
            r = await client.post(f"{self._base_url}/v1/reinitialize")
            r.raise_for_status()
            return _unwrap(r.json())

    # -- Snapshots -------------------------------------------------------
    # Snapshots are stored on the filesystem of whichever machine runs
    # training.  For remote mode that machine is not accessible here.

    async def train_snapshots_list(self) -> dict:
        raise NotImplementedError(
            "Snapshot management is not supported in remote mode. "
            "Connect directly to the Wrangler host to manage snapshots."
        )

    async def train_snapshot_save(self, name: str) -> dict:
        raise NotImplementedError(
            "Snapshot management is not supported in remote mode."
        )

    async def train_snapshot_load(self, name: str) -> dict:
        raise NotImplementedError(
            "Snapshot management is not supported in remote mode."
        )

    async def train_snapshot_delete(self, name: str) -> dict:
        raise NotImplementedError(
            "Snapshot management is not supported in remote mode."
        )

    # -- GPU coordination -----------------------------------------------

    async def gpu_claim(self) -> list[int]:
        """Remote backend manages its own GPU — no local indices to claim."""
        return []

    async def gpu_release_hint(self) -> None:
        """No-op: GPU management is the remote host's responsibility."""
        pass
