"""ComposeBackend protocol — the contract between StemForge and any composition engine.

Any backend that implements this protocol can be used to drive the Compose tab,
whether it is an embedded subprocess on the same machine, a standalone Wrangler
instance on the LAN, or a remote cloud deployment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class BackendMode(str, Enum):
    EMBEDDED = "embedded"   # Subprocess managed by StemForge (default)
    REMOTE = "remote"       # Standalone Wrangler at a URL
    DISABLED = "disabled"   # No composition engine


# ---------------------------------------------------------------------------
# Status / result dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ComposeStatus:
    """Snapshot of the compose backend's current state."""

    mode: BackendMode
    status: str                         # disabled | ready | starting | running | crashed
    port: int | None = None
    gpu_indices: list[int] = field(default_factory=list)
    gpu_busy: bool = False              # True while a generation or training job is active
    gpu_free_vram_mb: int | None = None
    error: str | None = None
    tenant: str | None = None
    models_loaded: bool = False


@dataclass
class GenerateParams:
    """All parameters needed for a generation call.

    Maps 1:1 with the GenerateRequest Pydantic model in compose.py.
    The router converts GenerateRequest → GenerateParams before calling the
    backend, keeping Pydantic out of the protocol layer.
    """

    style: str = ""
    lyrics: str = ""
    duration: float = 120.0
    bpm: int | None = None
    key: str | None = None
    time_signature: str = "4/4"
    lyric_adherence: int = 1
    quality: int = 1
    creativity: float = 50.0
    batch_size: int = 1
    seed: int | None = None
    scheduler: str = "euler"
    audio_format: str = "mp3"
    # Rework / Analyze mode fields
    src_audio_path: str | None = None
    reference_audio_path: str | None = None
    guidance_scale_raw: float | None = None
    inference_steps_raw: int | None = None
    # Raw passthrough for fields not yet modelled in this dataclass
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerateResult:
    """One generation result (AceStep may return batch_size > 1 results)."""

    audio_url: str
    meta: dict[str, Any] | None = None
    prompt: str = ""
    lyrics: str = ""
    seed_value: str = ""


@dataclass(frozen=True)
class TaskStatus:
    """Poll response for an async generation task."""

    status: str                         # processing | done | error
    results: list[GenerateResult] | None = None


@dataclass(frozen=True)
class AdapterStatus:
    """Current LoRA/LoKR adapter state from the backend."""

    loaded: bool = False
    adapter_name: str | None = None
    scale: float = 1.0
    use_lora: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrainParams:
    """Training hyperparameters — maps to the TrainStartRequest in compose.py."""

    adapter_type: str = "lora"          # "lora" | "lokr"
    lora_rank: int = 16
    lora_alpha: int = 16
    lora_dropout: float = 0.0
    learning_rate: float = 1e-4
    train_epochs: int = 50
    train_batch_size: int = 4
    gradient_accumulation: int = 1
    save_every_n_epochs: int = 10
    training_seed: int = 42
    gradient_checkpointing: bool = True
    tensor_dir: str | None = None
    output_dir: str | None = None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class ComposeBackend(Protocol):
    """The interface StemForge uses to talk to any composition engine.

    All methods are async.  Implementations handle their own error recovery.
    """

    # -- Lifecycle ---------------------------------------------------------

    async def start(self) -> ComposeStatus:
        """Initialise / launch the backend.  Idempotent."""
        ...

    async def shutdown(self) -> None:
        """Gracefully stop the backend."""
        ...

    async def get_status(self) -> ComposeStatus:
        """Return current backend state including GPU info."""
        ...

    # -- Generation --------------------------------------------------------

    async def generate(self, params: GenerateParams) -> str:
        """Submit a generation task.  Returns task_id."""
        ...

    async def poll_task(self, task_id: str) -> TaskStatus:
        """Poll task progress."""
        ...

    async def get_audio(self, path: str) -> tuple[bytes, str]:
        """Retrieve audio bytes + content_type for a result path."""
        ...

    async def format_lyrics(self, lyrics: str) -> dict:
        """Call the LM for structured lyrics analysis."""
        ...

    async def create_sample(self, query: str, language: str = "en") -> str:
        """Submit AI lyrics generation.  Returns task_id."""
        ...

    # -- Adapter management -----------------------------------------------

    async def load_adapter(self, adapter_path: str, adapter_name: str | None = None) -> dict:
        ...

    async def unload_adapter(self) -> dict:
        ...

    async def toggle_adapter(self, use_lora: bool) -> dict:
        ...

    async def scale_adapter(self, scale: float, adapter_name: str | None = None) -> dict:
        ...

    async def adapter_status(self) -> AdapterStatus:
        ...

    # -- Training ----------------------------------------------------------

    async def dataset_scan(self, payload: dict) -> dict:
        ...

    async def dataset_preprocess(self, output_dir: str) -> dict:
        ...

    async def dataset_preprocess_status(self, task_id: str | None = None) -> dict:
        ...

    async def dataset_auto_label(self, payload: dict) -> dict:
        ...

    async def dataset_auto_label_status(self) -> dict:
        ...

    async def dataset_sample_update(self, sample_idx: int, payload: dict) -> dict:
        ...

    async def dataset_samples(self) -> dict:
        ...

    async def dataset_save(self, file_path: str) -> dict:
        ...

    async def dataset_load(self, file_path: str) -> dict:
        ...

    async def train_start(self, params: TrainParams) -> dict:
        ...

    async def train_status(self) -> dict:
        ...

    async def train_stop(self) -> dict:
        ...

    async def train_export(self, export_path: str, lora_output_dir: str) -> dict:
        ...

    async def reinitialize(self) -> dict:
        ...

    # -- Snapshot management ----------------------------------------------
    # Snapshots capture the full training state (dataset + tensors) so a
    # training run can be paused and resumed.  These belong in the protocol
    # because their storage location depends on which backend is active:
    # embedded backends use the local filesystem; remote backends must
    # delegate to the remote machine.

    async def train_snapshots_list(self) -> dict:
        """Return a list of available snapshots."""
        ...

    async def train_snapshot_save(self, name: str) -> dict:
        """Save current dataset + tensors as a named snapshot."""
        ...

    async def train_snapshot_load(self, name: str) -> dict:
        """Restore a named snapshot into the working training directory."""
        ...

    async def train_snapshot_delete(self, name: str) -> dict:
        """Delete a named snapshot."""
        ...

    # -- GPU coordination (for scheduler integration) ---------------------

    async def gpu_claim(self) -> list[int]:
        """Return GPU indices this backend is actively using RIGHT NOW.

        Empty list = not using any GPU (idle, CPU-offloaded, or not running).
        Used by RemoteComposeBackend; the local scheduler uses the sync
        ``claimed_gpu_indices()`` factory function instead.
        """
        ...

    async def gpu_release_hint(self) -> None:
        """Hint that the backend should release GPU memory if idle.

        Called by the scheduler when other pipelines need the GPU.
        For embedded mode: can trigger CPU offload or VRAM flush.
        For remote mode: HTTP call to Wrangler's memory management endpoint.
        """
        ...
