"""Thread-safe AceStep subprocess status + lazy launch.

Shared between run.py (configures launch params) and the compose router
(triggers launch on first use, reads status).
"""

import os
import subprocess
import sys
import threading
import time
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {
    "status": "disabled",  # disabled | ready | starting | running | crashed
    "port": 8001,
    "exit_code": None,
    "error": None,
}

# Launch config set by run.py, consumed by launch()
_launch_config: dict[str, Any] = {}
_proc: subprocess.Popen | None = None

# AceStep environment variables forwarded to the subprocess if set by the user.
_PASSTHROUGH_VARS = [
    "ACESTEP_DEVICE",
    "MAX_CUDA_VRAM",
    "ACESTEP_VAE_ON_CPU",
    "ACESTEP_LM_BACKEND",
    "ACESTEP_INIT_LLM",
    "MODEL_LOCATION",
]


def get_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def set_status(status: str, **kwargs: Any) -> None:
    with _lock:
        _state["status"] = status
        _state.update(kwargs)


def get_port() -> int:
    with _lock:
        return _state["port"]


def get_process() -> subprocess.Popen | None:
    """Return the subprocess handle (for shutdown handler in run.py)."""
    with _lock:
        return _proc


def configure(port: int, gpu: str | None) -> None:
    """Store launch parameters. Called by run.py at startup.

    Sets status to 'ready' — AceStep is configured but not yet spawned.
    The subprocess starts on first use via launch().
    """
    with _lock:
        _launch_config["port"] = port
        _launch_config["gpu"] = gpu
        _state["status"] = "ready"
        _state["port"] = port


def launch() -> bool:
    """Spawn AceStep subprocess if not already running.

    Returns True if launch was initiated, False if already running/starting.
    Safe to call multiple times — only the first call spawns the process.
    """
    global _proc

    with _lock:
        if _state["status"] in ("starting", "running"):
            return False
        if _state["status"] == "disabled":
            return False
        if not _launch_config:
            return False

        port = _launch_config["port"]
        gpu = _launch_config.get("gpu")

        _state["status"] = "starting"

    # Build environment
    env = os.environ.copy()
    if gpu:
        env["CUDA_VISIBLE_DEVICES"] = gpu
    for var in _PASSTHROUGH_VARS:
        if var in os.environ:
            env[var] = os.environ[var]

    # Preamble sets process-wide torch config without touching vendor code:
    # - Tensor Core acceleration (medium precision) for speed on Ampere+ GPUs
    # - Deterministic CUDA ops for reproducible generation with same seed
    cmd = [
        sys.executable, "-c",
        "import os; os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8');"
        "import torch;"
        "torch.set_float32_matmul_precision('medium');"
        "torch.use_deterministic_algorithms(True, warn_only=True);"
        "torch.backends.cudnn.deterministic = True;"
        "torch.backends.cudnn.benchmark = False;"
        "from acestep.api_server import main; main()",
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
    print(f"[stemforge] Starting AceStep API server on port {port}...")

    proc = subprocess.Popen(cmd, env=env)
    with _lock:
        _proc = proc

    # Start monitor thread
    monitor = threading.Thread(target=_monitor, args=(proc,), daemon=True)
    monitor.start()
    return True


def _monitor(proc: subprocess.Popen) -> None:
    """Daemon thread that watches the subprocess and updates shared state.

    Polls AceStep's /health endpoint until ``models_initialized`` is true,
    only then sets status to ``"running"``.  This prevents the frontend from
    enabling the Generate button while models are still downloading/loading.

    There is no fixed timeout — as long as the process is alive it is
    presumably still downloading or loading models, so we keep waiting.
    Only sets ``"crashed"`` when the process actually exits.
    """
    import json as _json
    import urllib.request

    port = _state["port"]
    url = f"http://127.0.0.1:{port}/health"

    # Give the process a moment to start
    time.sleep(5)

    # Poll health endpoint until models are loaded.
    # No timeout — keep going as long as the process is alive.
    polls = 0
    while proc.poll() is None:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = _json.loads(resp.read())
            # AceStep wraps responses: {"data": {...}, "code": 200, ...}
            inner = data.get("data") if isinstance(data.get("data"), dict) else data
            if inner.get("models_initialized"):
                set_status("running")
                print("[stemforge] AceStep is ready (models loaded)")
                break
        except Exception:
            pass  # Server not yet accepting connections

        polls += 1
        if polls % 20 == 0:  # Log every ~60s so the user knows it's still working
            mins = polls * 3 // 60
            print(f"[stemforge] Still waiting for AceStep models ({mins}m elapsed)...")

        time.sleep(3)
    else:
        # Process exited before models were ready
        code = proc.returncode
        set_status("crashed", exit_code=code,
                   error=f"AceStep exited with code {code}")
        print(f"[stemforge] AceStep crashed during startup (exit code {code})")
        return

    # Crash monitoring loop
    while proc.poll() is None:
        time.sleep(1)

    code = proc.returncode
    set_status("crashed", exit_code=code, error=f"AceStep exited with code {code}")
    print(f"[stemforge] AceStep crashed (exit code {code}). Compose tab unavailable.")
