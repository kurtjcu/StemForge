"""StemForge launcher — starts the FastAPI server and optionally the AceStep subprocess."""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time

import uvicorn

# AceStep environment variables forwarded to the subprocess if set by the user.
_ACESTEP_PASSTHROUGH_VARS = [
    "ACESTEP_DEVICE",
    "MAX_CUDA_VRAM",
    "ACESTEP_VAE_ON_CPU",
    "ACESTEP_LM_BACKEND",
    "ACESTEP_INIT_LLM",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch StemForge + optional AceStep server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("STEMFORGE_PORT", "8765")),
        help="StemForge server port (default: 8765, env STEMFORGE_PORT)",
    )
    parser.add_argument(
        "--acestep-port",
        type=int,
        default=int(os.environ.get("ACESTEP_PORT", "8001")),
        help="AceStep API server port (default: 8001, env ACESTEP_PORT)",
    )
    parser.add_argument(
        "--no-acestep",
        action="store_true",
        default=False,
        help="Disable the AceStep subprocess (Compose tab unavailable)",
    )
    parser.add_argument(
        "--gpu",
        type=str,
        default=os.environ.get("ACESTEP_GPU"),
        help="GPU device(s) for AceStep (e.g. 0, 1, 0,1). Sets CUDA_VISIBLE_DEVICES.",
    )
    return parser.parse_args()


def _monitor_acestep(proc: subprocess.Popen) -> None:
    """Daemon thread that watches the AceStep subprocess and updates shared state."""
    from backend.services.acestep_state import set_status

    # Wait a moment for process to start, then mark running
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


def _start_acestep(port: int, gpu: str | None) -> subprocess.Popen:
    """Spawn the AceStep API server as a subprocess."""
    env = os.environ.copy()
    if gpu:
        env["CUDA_VISIBLE_DEVICES"] = gpu
    # Forward user-set AceStep vars
    for var in _ACESTEP_PASSTHROUGH_VARS:
        if var in os.environ:
            env[var] = os.environ[var]

    cmd = [
        sys.executable, "-m", "acestep.api_server",
        "--host", "127.0.0.1",
        "--port", str(port),
    ]
    print(f"[stemforge] Starting AceStep API server on port {port}...")
    return subprocess.Popen(cmd, env=env)


def _print_banner(port: int, acestep_port: int, acestep_enabled: bool, gpu: str | None) -> None:
    gpu_display = gpu if gpu else "auto"
    acestep_display = f"enabled (port {acestep_port})" if acestep_enabled else "disabled"
    active_overrides = {
        k: os.environ[k] for k in _ACESTEP_PASSTHROUGH_VARS if k in os.environ
    }
    print()
    print("=" * 60)
    print("  StemForge")
    print("=" * 60)
    print(f"  Server:     http://localhost:{port}")
    print(f"  AceStep:    {acestep_display}")
    print(f"  GPU:        {gpu_display}")
    if active_overrides:
        print("-" * 60)
        print("  AceStep env overrides:")
        for k, v in active_overrides.items():
            print(f"    {k}={v}")
    print("=" * 60)
    print()


def main() -> None:
    args = _parse_args()
    acestep_proc: subprocess.Popen | None = None

    # Import and configure state before starting anything
    from backend.services.acestep_state import set_status

    set_status("disabled", port=args.acestep_port)

    _print_banner(args.port, args.acestep_port, not args.no_acestep, args.gpu)

    # Start AceStep subprocess if enabled
    if not args.no_acestep:
        set_status("starting", port=args.acestep_port)
        acestep_proc = _start_acestep(args.acestep_port, args.gpu)
        # Daemon monitor thread — will update state on crash
        monitor = threading.Thread(target=_monitor_acestep, args=(acestep_proc,), daemon=True)
        monitor.start()

    # Graceful shutdown: terminate AceStep on SIGINT/SIGTERM
    def _shutdown(signum: int, frame: object) -> None:
        if acestep_proc and acestep_proc.poll() is None:
            print("\n[stemforge] Stopping AceStep...")
            acestep_proc.terminate()
            try:
                acestep_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                acestep_proc.kill()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Start StemForge FastAPI server (blocks)
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
