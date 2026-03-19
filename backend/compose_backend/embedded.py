"""EmbeddedComposeBackend — ComposeBackend implementation for an in-process AceStep subprocess.

This wraps the existing ``acestep_state`` (subprocess lifecycle) and
``acestep_wrapper`` (HTTP client) behind the ``ComposeBackend`` protocol.
Every method is a thin delegation — no business logic lives here.

GPU busy/idle signalling
------------------------
``_gpu_active`` is a ``threading.Event`` set when a generation or training
job is submitted and cleared when that job reaches a terminal state
(done / error / stopped).  ``sync_gpu_claim()`` reads it synchronously so
the ``GpuScheduler`` can check it from threading context without an asyncio
bridge.  ``gpu_claim()`` is the async wrapper required by the protocol
(used by ``RemoteComposeBackend`` and any future async callers).

Snapshot storage
----------------
Snapshot methods operate on the local filesystem paths under ``_TRAIN_DIR``.
This is correct for the embedded backend because StemForge and the AceStep
subprocess share the same filesystem.  ``RemoteComposeBackend`` must
implement these differently (HTTP to the remote machine).

Payload passthrough for generate()
-----------------------------------
``generate()`` receives the pre-built AceStep wire payload via
``params.extra``.  The payload is built by ``_build_payload()`` in
``compose.py`` — a StemForge-specific function that maps frontend parameters
to AceStep's ``/release_task`` format.  Keeping that logic in ``compose.py``
avoids duplicating it here and ensures the embedded backend sends the same
payload that was previously sent directly.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.compose_backend.protocol import (
    AdapterStatus,
    BackendMode,
    ComposeStatus,
    GenerateParams,
    GenerateResult,
    TaskStatus,
    TrainParams,
)

# ---------------------------------------------------------------------------
# Training directory constants
# Resolved from the same env var as compose.py so both see the same tree.
# ---------------------------------------------------------------------------

_TRAIN_DIR = Path(os.environ.get(
    "TRAIN_DIR",
    str(Path(__file__).parent.parent.parent / "Ace-Step-Wrangler" / "training"),
))
_TRAIN_TENSOR_DIR = _TRAIN_DIR / "tensors"
_TRAIN_OUTPUT_DIR = _TRAIN_DIR / "output"
_TRAIN_SNAPSHOTS_DIR = _TRAIN_DIR / "snapshots"
_TRAIN_DATASET_FILE = _TRAIN_DIR / "dataset.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Sanitise a snapshot name to a safe filesystem component."""
    return re.sub(r'[^a-zA-Z0-9._-]', '_', name)[:128]


def _parse_gpu_indices(gpu: str | None) -> list[int]:
    """Parse the ``--gpu`` argument (e.g. ``'0'``, ``'1'``, ``'0,1'``) into ints."""
    if not gpu:
        return []
    try:
        return [int(g.strip()) for g in gpu.split(",") if g.strip()]
    except (ValueError, TypeError):
        return []


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class EmbeddedComposeBackend:
    """Wraps the AceStep subprocess behind the ComposeBackend protocol."""

    def __init__(self, port: int, gpu: str | None) -> None:
        self._port = port
        self._gpu = gpu
        self._gpu_indices = _parse_gpu_indices(gpu)
        # Set while a generation or training job is active, cleared on completion.
        self._gpu_active = threading.Event()

        # Configure the subprocess state machine so launch() works when called.
        # This sets _launch_config and transitions status to "ready".
        from backend.services import acestep_state
        acestep_state.configure(port, gpu)

        # Ensure training directories exist (same dirs compose.py creates at import).
        for _d in (_TRAIN_DIR, _TRAIN_TENSOR_DIR, _TRAIN_OUTPUT_DIR, _TRAIN_SNAPSHOTS_DIR):
            _d.mkdir(parents=True, exist_ok=True)

    # -- Lifecycle -------------------------------------------------------

    async def start(self) -> ComposeStatus:
        """Launch the AceStep subprocess.  Idempotent."""
        from backend.services import acestep_state
        acestep_state.launch()
        return await self.get_status()

    async def shutdown(self) -> None:
        """No-op: subprocess lifecycle is managed by run.py."""
        pass

    async def get_status(self) -> ComposeStatus:
        """Return current backend state.

        Also self-heals ``_gpu_active`` if AceStep has crashed while a job
        was in flight (e.g. browser disconnected before poll returned done).
        """
        from backend.services import acestep_state
        state = acestep_state.get_status()
        status = state["status"]

        if status not in ("running", "starting") and self._gpu_active.is_set():
            self._gpu_active.clear()

        free_mb: int | None = None
        if self._gpu_indices:
            try:
                import torch
                free, _ = torch.cuda.mem_get_info(self._gpu_indices[0])
                free_mb = free >> 20
            except Exception:
                pass

        return ComposeStatus(
            mode=BackendMode.EMBEDDED,
            status=status,
            port=state.get("port"),
            gpu_indices=list(self._gpu_indices),
            gpu_busy=self._gpu_active.is_set(),
            gpu_free_vram_mb=free_mb,
            error=state.get("error"),
            tenant=acestep_state.get_tenant(),
            models_loaded=(status == "running"),
        )

    # -- Generation ------------------------------------------------------

    async def generate(self, params: GenerateParams) -> str:
        """Submit a generation task.  Returns the task_id.

        The AceStep wire payload must be pre-built by compose.py's
        ``_build_payload()`` and passed in ``params.extra``.
        """
        from backend.api.acestep_wrapper import release_task
        self._gpu_active.set()
        try:
            return await release_task(params.extra)
        except Exception:
            self._gpu_active.clear()
            raise

    async def poll_task(self, task_id: str) -> TaskStatus:
        from backend.api.acestep_wrapper import query_result
        data = await query_result(task_id)
        if data["status"] in ("done", "error"):
            self._gpu_active.clear()
        results = None
        if data["status"] == "done" and data.get("results"):
            results = [
                GenerateResult(
                    audio_url=r.get("audio_url", ""),
                    meta=r.get("meta"),
                    prompt=r.get("prompt", ""),
                    lyrics=r.get("lyrics", ""),
                    seed_value=r.get("seed_value", ""),
                )
                for r in data["results"]
            ]
        return TaskStatus(status=data["status"], results=results)

    async def get_audio(self, path: str) -> tuple[bytes, str]:
        from backend.api.acestep_wrapper import get_audio_bytes
        return await get_audio_bytes(path)

    async def format_lyrics(self, lyrics: str) -> dict:
        from backend.api.acestep_wrapper import format_input
        return await format_input(lyrics)

    async def create_sample(self, query: str, language: str = "en") -> str:
        from backend.api import acestep_wrapper
        return await acestep_wrapper.create_sample(query, language)

    # -- Adapter management ---------------------------------------------

    async def load_adapter(self, adapter_path: str, adapter_name: str | None = None) -> dict:
        from backend.api.acestep_wrapper import lora_load
        return await lora_load(adapter_path, adapter_name)

    async def unload_adapter(self) -> dict:
        from backend.api.acestep_wrapper import lora_unload
        return await lora_unload()

    async def toggle_adapter(self, use_lora: bool) -> dict:
        from backend.api.acestep_wrapper import lora_toggle
        return await lora_toggle(use_lora)

    async def scale_adapter(self, scale: float, adapter_name: str | None = None) -> dict:
        from backend.api.acestep_wrapper import lora_scale
        return await lora_scale(scale, adapter_name)

    async def adapter_status(self) -> AdapterStatus:
        from backend.api.acestep_wrapper import lora_status
        raw = await lora_status()
        return AdapterStatus(
            loaded=raw.get("loaded", False),
            adapter_name=raw.get("adapter_name"),
            scale=raw.get("scale", 1.0),
            use_lora=raw.get("use_lora", True),
            raw=raw,
        )

    # -- Training -------------------------------------------------------

    async def dataset_scan(self, payload: dict) -> dict:
        from backend.api.acestep_wrapper import dataset_scan
        return await dataset_scan(payload)

    async def dataset_preprocess(self, output_dir: str) -> dict:
        from backend.api.acestep_wrapper import dataset_preprocess_async
        return await dataset_preprocess_async(output_dir)

    async def dataset_preprocess_status(self, task_id: str | None = None) -> dict:
        from backend.api.acestep_wrapper import dataset_preprocess_status
        return await dataset_preprocess_status(task_id)

    async def dataset_auto_label(self, payload: dict) -> dict:
        from backend.api.acestep_wrapper import dataset_auto_label_async
        return await dataset_auto_label_async(payload)

    async def dataset_auto_label_status(self) -> dict:
        from backend.api.acestep_wrapper import dataset_auto_label_status
        return await dataset_auto_label_status()

    async def dataset_sample_update(self, sample_idx: int, payload: dict) -> dict:
        from backend.api.acestep_wrapper import dataset_sample_update
        return await dataset_sample_update(sample_idx, payload)

    async def dataset_samples(self) -> dict:
        from backend.api.acestep_wrapper import dataset_samples
        return await dataset_samples()

    async def dataset_save(self, file_path: str) -> dict:
        from backend.api.acestep_wrapper import dataset_save
        return await dataset_save(file_path)

    async def dataset_load(self, file_path: str) -> dict:
        from backend.api.acestep_wrapper import dataset_load
        return await dataset_load(file_path)

    async def train_start(self, params: TrainParams) -> dict:
        """Start LoRA or LoKR training and mark GPU as busy."""
        from backend.api.acestep_wrapper import training_start, training_start_lokr
        out_dir = params.output_dir or str(_TRAIN_OUTPUT_DIR)
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
            "tensor_dir": params.tensor_dir or str(_TRAIN_TENSOR_DIR),
        }
        self._gpu_active.set()
        try:
            if params.adapter_type == "lokr":
                payload["output_dir"] = out_dir
                return await training_start_lokr(payload)
            payload["lora_output_dir"] = out_dir
            return await training_start(payload)
        except Exception:
            self._gpu_active.clear()
            raise

    async def train_status(self) -> dict:
        """Poll training status and clear GPU claim when training completes."""
        from backend.api.acestep_wrapper import training_status
        result = await training_status()
        # Clear GPU claim for any terminal or idle state.
        # AceStep returns "training" while active; anything else is terminal.
        if result.get("status", "") != "training":
            self._gpu_active.clear()
        return result

    async def train_stop(self) -> dict:
        from backend.api.acestep_wrapper import training_stop
        try:
            return await training_stop()
        finally:
            self._gpu_active.clear()

    async def train_export(self, export_path: str, lora_output_dir: str) -> dict:
        from backend.api.acestep_wrapper import training_export
        return await training_export(export_path, lora_output_dir)

    async def reinitialize(self) -> dict:
        from backend.api.acestep_wrapper import reinitialize_service
        return await reinitialize_service()

    # -- Snapshots -------------------------------------------------------

    async def train_snapshots_list(self) -> dict:
        snapshots = []
        if not _TRAIN_SNAPSHOTS_DIR.is_dir():
            return {"snapshots": snapshots}
        for entry in sorted(_TRAIN_SNAPSHOTS_DIR.iterdir()):
            if not entry.is_dir():
                continue
            meta: dict = {}
            meta_file = entry / "meta.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text())
                except Exception:
                    pass
            size_bytes = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
            snapshots.append({
                "name": entry.name,
                "meta": meta,
                "size_mb": round(size_bytes / 1_048_576, 1),
            })
        return {"snapshots": snapshots}

    async def train_snapshot_save(self, name: str) -> dict:
        """Flush AceStep's in-memory dataset then copy dataset + tensors to snapshot."""
        safe_name = _safe_filename(name)
        snap_dir = _TRAIN_SNAPSHOTS_DIR / safe_name
        snap_dir.mkdir(parents=True, exist_ok=True)

        # Flush AceStep's in-memory dataset to disk before snapshotting.
        try:
            await self.dataset_save(str(_TRAIN_DATASET_FILE))
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
            "tensor_count": (
                sum(1 for _ in _TRAIN_TENSOR_DIR.rglob("*.pt"))
                if _TRAIN_TENSOR_DIR.is_dir() else 0
            ),
        }
        (snap_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return {"name": safe_name, "meta": meta}

    async def train_snapshot_load(self, name: str) -> dict:
        """Restore snapshot files then reload dataset into AceStep's in-memory state."""
        safe_name = _safe_filename(name)
        snap_dir = _TRAIN_SNAPSHOTS_DIR / safe_name
        if not snap_dir.is_dir():
            raise FileNotFoundError(f"Snapshot not found: {safe_name}")

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
                await self.dataset_load(str(_TRAIN_DATASET_FILE))
        except Exception:
            pass

        return {"name": safe_name, "restored": True}

    async def train_snapshot_delete(self, name: str) -> dict:
        safe_name = _safe_filename(name)
        snap_dir = _TRAIN_SNAPSHOTS_DIR / safe_name
        if snap_dir.is_dir():
            shutil.rmtree(snap_dir)
        return {"deleted": safe_name}

    # -- GPU coordination -----------------------------------------------

    async def gpu_claim(self) -> list[int]:
        """Async wrapper around sync_gpu_claim for protocol compliance."""
        return self.sync_gpu_claim()

    def sync_gpu_claim(self) -> list[int]:
        """Return GPU indices currently in use.  Safe to call from threading context.

        Reads ``_gpu_active`` directly — no asyncio bridge required.
        Also self-heals if AceStep has crashed while ``_gpu_active`` is set.
        """
        if not self._gpu_active.is_set():
            return []
        from backend.services import acestep_state
        if acestep_state.get_status()["status"] != "running":
            self._gpu_active.clear()
            return []
        return list(self._gpu_indices)

    async def gpu_release_hint(self) -> None:
        """No-op: AceStep already offloads to CPU between generations via MAX_CUDA_VRAM."""
        pass
