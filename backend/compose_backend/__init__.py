"""compose_backend — factory and scheduler coordination interface.

Public API
----------
configure_compose_backend(mode, port, gpu, remote_url)
    Called once by run.py at startup.  Sets the module-level backend singleton.

get_compose_backend() -> ComposeBackend | None
    Returns the active backend, or None when compose is disabled.

claimed_gpu_indices() -> set[int]
    Sync function safe to call from threading context (GpuScheduler).
    Returns the GPU indices the compose backend is actively using right now.
    Returns an empty set when idle, disabled, or in remote mode.
"""

from __future__ import annotations

from backend.compose_backend.protocol import BackendMode, ComposeBackend

_backend: ComposeBackend | None = None


def configure_compose_backend(
    mode: BackendMode,
    port: int = 8001,
    gpu: str | None = None,
    remote_url: str | None = None,
) -> ComposeBackend | None:
    """Instantiate and store the compose backend singleton.

    Called once from ``run.py`` before uvicorn starts.  Returns the backend
    so callers can inspect it, but the canonical accessor is
    ``get_compose_backend()``.
    """
    global _backend

    if mode == BackendMode.EMBEDDED:
        from backend.compose_backend.embedded import EmbeddedComposeBackend
        _backend = EmbeddedComposeBackend(port=port, gpu=gpu)

    elif mode == BackendMode.REMOTE:
        if not remote_url:
            raise ValueError("--compose-url is required for --compose-mode remote")
        from backend.compose_backend.remote import RemoteComposeBackend
        _backend = RemoteComposeBackend(base_url=remote_url)

    else:  # DISABLED
        _backend = None

    return _backend


def get_compose_backend() -> ComposeBackend | None:
    """Return the active backend singleton, or None if compose is disabled."""
    return _backend


def claimed_gpu_indices() -> set[int]:
    """Return GPU indices the compose backend is actively using right now.

    Designed to be called from the GpuScheduler's threading context — no
    asyncio bridge is needed.

    - EmbeddedComposeBackend: reads a threading.Event directly.
    - RemoteComposeBackend: always returns set() (external GPU, not ours).
    - None (disabled): always returns set().
    """
    backend = _backend
    if backend is None:
        return set()

    # Import here to avoid a top-level cycle: embedded imports from protocol,
    # protocol imports nothing from this package, so this is safe.
    from backend.compose_backend.embedded import EmbeddedComposeBackend
    if isinstance(backend, EmbeddedComposeBackend):
        return set(backend.sync_gpu_claim())

    # Remote backend manages its own machine's GPUs.
    return set()
