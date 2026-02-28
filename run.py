"""StemForge launcher — starts the FastAPI server and optionally the AceStep subprocess."""

import argparse
import os
import signal
import subprocess

import uvicorn

# AceStep passthrough vars listed here only for the banner display.
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


def _print_banner(port: int, acestep_port: int, acestep_enabled: bool, gpu: str | None) -> None:
    gpu_display = gpu if gpu else "auto"
    acestep_display = f"ready (port {acestep_port}, starts on first use)" if acestep_enabled else "disabled"
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

    from backend.services import acestep_state

    if args.no_acestep:
        acestep_state.set_status("disabled", port=args.acestep_port)
    else:
        # Configure but don't spawn — AceStep starts on first use
        acestep_state.configure(args.acestep_port, args.gpu)

    _print_banner(args.port, args.acestep_port, not args.no_acestep, args.gpu)

    # Graceful shutdown: terminate AceStep on SIGINT/SIGTERM
    def _shutdown(signum: int, frame: object) -> None:
        proc = acestep_state.get_process()
        if proc and proc.poll() is None:
            print("\n[stemforge] Stopping AceStep...")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
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
