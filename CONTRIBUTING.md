# Contributing to StemForge

Thank you for your interest in contributing to StemForge!
This project uses **uv** for deterministic environments, fast installs, and clean dependency management.

---

## Prerequisites

### Python
StemForge requires **Python 3.11**.

If your system Python differs:
    uv python install 3.11

### uv
Install uv:
    curl -LsSf https://astral.sh/uv/install.sh | sh

### FFmpeg (required for PyAV)
You must have **FFmpeg >= 5.1** with development headers.

Ubuntu 22.04:
    sudo add-apt-repository -y ppa:ubuntuhandbook1/ffmpeg7
    sudo apt update
    sudo apt install ffmpeg libavcodec-dev libavformat-dev libavdevice-dev \
        libavfilter-dev libavutil-dev libswscale-dev libswresample-dev

Ubuntu 24.04+:
    sudo apt install ffmpeg libavcodec-dev libavformat-dev

Fedora:
    sudo dnf install ffmpeg-free ffmpeg-free-devel

Arch:
    sudo pacman -S ffmpeg

### FluidSynth (required for MIDI preview and Mix tab)

Fedora:
    sudo dnf install fluidsynth fluidsynth-devel fluid-soundfont-gm

Ubuntu / Debian:
    sudo apt install libfluidsynth3 libfluidsynth-dev fluid-soundfont-gm

Arch:
    sudo pacman -S fluidsynth soundfont-fluid

### GPU (optional)
For GPU acceleration:
- NVIDIA driver supporting **CUDA 13.0**
- PyTorch **2.10.0+cu130** (pinned in uv.lock)

CPU-only works everywhere.

---

## Getting Started

Clone (use `--recursive` for the AceStep submodule):
    git clone --recursive git@github.com:tsondo/StemForge.git
    cd StemForge

Sync environment:
    uv sync

This creates `.venv/`, installs all dependencies, and respects the pinned `uv.lock`.

Run the app:
    uv run python run.py

Then open http://localhost:8765 in your browser.

---

## Code Quality

StemForge uses:
- **ruff** for linting and formatting
- **mypy** for type checking
- **pytest** for tests

Run everything:
    make check

Run tests:
    make test

Before submitting a PR:
- no ruff errors
- no mypy errors
- tests pass

---

## Adding Dependencies

All dependencies must be added to `pyproject.toml`.

After editing:
    uv sync

This updates `.venv/` and regenerates `uv.lock`.

Avoid unnecessary dependency churn.

---

## Project Structure

    backend/           – FastAPI backend (API routers + services)
    backend/api/       – API endpoint routers (separate, midi, generate, compose, mix, export, etc.)
    backend/services/  – Job manager, session store, pipeline manager, AceStep state
    frontend/          – Vanilla HTML/CSS/JS SPA (served by FastAPI StaticFiles)
    frontend/components/ – Per-tab JS modules (separate, midi, generate, compose, mix, export)
    pipelines/         – Demucs, BS-Roformer, BasicPitch, Vocal MIDI, Stable Audio Open
    models/            – Model loaders, registry, vendored BasicPitch
    utils/             – Audio I/O, MIDI I/O, paths, device detection, logging, errors
    Ace-Step-Wrangler/ – Git submodule for AceStep (Compose tab)

Notes:
- Backend API endpoints must not block the event loop — long operations run in background threads via `JobManager`
- All output paths come from `utils/paths.py`
- Import layer order: `utils/ → models/ → pipelines/ → backend/services/ → backend/api/ → backend/main.py` (no circular imports)
- Frontend uses an event bus (`appState.on()`/`appState.emit()`) for cross-tab communication
- AceStep runs as a subprocess managed by `run.py`, proxied via `backend/api/compose.py`

---

## Model Weights

Models are cached under:
    ~/.cache/stemforge/

Do not commit model weights.

Loaders must:
- be deterministic
- validate checksums when applicable
- avoid re-downloading unnecessarily

---

## Contributing from macOS

**Setup**

Copy the macOS pyproject file before syncing:

    cp pyproject.toml.MAC pyproject.toml
    uv sync

Install FluidSynth and set the library path:

    brew install fluid-synth
    export DYLD_LIBRARY_PATH="$(brew --prefix fluid-synth)/lib:$DYLD_LIBRARY_PATH"

Add the `export` to your `~/.zshrc`.

**Rules for macOS-compatible code**

- When using PyTorch device selection, always use `from utils.device import get_device` — never hardcode `"cuda"`.
- When using app data paths, always use `from utils.platform import get_data_dir` — never hardcode `~/.local/share/`.
- Avoid `torch.float16` unconditionally — use `is_mps()` from `utils.device` and fall back to `float32` on MPS.
- Avoid operations that break on MPS; test with `PYTORCH_ENABLE_MPS_FALLBACK=1` (set automatically by the app).
- Do NOT commit a `pyproject.toml` derived from `pyproject.toml.MAC` — the canonical `pyproject.toml` is the Linux/CUDA version.

---

## Pull Requests

1. Create a feature branch
2. Make focused, minimal changes
3. Run `make check`
4. Ensure no regressions
5. Submit a PR with a clear description

Small, incremental PRs are preferred.

Thank you for helping build StemForge!
