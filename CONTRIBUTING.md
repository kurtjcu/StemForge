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
You must have **FFmpeg ≥ 5.1** with development headers.

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

### GPU (optional)
For GPU acceleration:
- NVIDIA driver supporting **CUDA 12.8**
- PyTorch **2.10.0+cu128** (pinned in uv.lock)

CPU-only works everywhere.

---

## Getting Started

Clone:
    git clone git@github.com:tsondo/StemForge.git
    cd StemForge

Sync environment:
    uv sync

This creates `.venv/`, installs all dependencies, and respects the pinned `uv.lock`.

Run the app:
    uv run stemforge

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

    gui/         – DearPyGUI interface (panels, theme, dialogs)
    pipelines/   – Demucs, BasicPitch, Vocal MIDI, MusicGen (stub)
    models/      – Model loaders + caching
    utils/       – Audio I/O, MIDI I/O, logging, errors
    gui/constants.py – Centralized output directories

Notes:
- GUI code must not block the main thread
- Long operations must run in background threads
- All output paths come from gui/constants.py
- No circular imports

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

## Pull Requests

1. Create a feature branch  
2. Make focused, minimal changes  
3. Run `make check`  
4. Ensure no regressions  
5. Submit a PR with a clear description  

Small, incremental PRs are preferred.

Thank you for helping build StemForge!
