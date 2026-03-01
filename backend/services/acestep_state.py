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

    cmd = [
        sys.executable, "-m", "acestep.api_server",
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
    """Daemon thread that watches the subprocess and updates shared state."""
    # Wait for process to start, then mark running
    time.sleep(3)
    if proc.poll() is None:
        set_status("running")
        print("[stemforge] AceStep is ready")

    # Poll until process exits
    while proc.poll() is None:
        time.sleep(1)

    code = proc.returncode
    set_status("crashed", exit_code=code, error=f"AceStep exited with code {code}")
    print(f"[stemforge] AceStep crashed (exit code {code}). Compose tab unavailable.")
