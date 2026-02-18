# Contributing to StemForge

Thank you for your interest in contributing to StemForge!  
This project uses **uv** for environment management and dependency resolution.  
uv provides fast, reproducible installs and a clean workflow for contributors.

---

## 🧱 Prerequisites

Before contributing, please install:

- Python 3.10+
- uv (https://github.com/astral-sh/uv)

To install uv, run:

    curl -LsSf https://astral.sh/uv/install.sh | sh

---

## 🚀 Getting Started

Clone the repository:

    git clone https://github.com/<your-org>/StemForge.git
    cd StemForge

Sync the development environment:

    uv sync

This will:

- create `.venv/` automatically  
- install StemForge in editable mode  
- install all runtime and dev dependencies  
- generate or update `uv.lock`  

---

## 🧪 Running StemForge

Use uv to run the application:

    uv run stemforge

Or run any Python module:

    uv run python -m stemforge

---

## 🧹 Code Quality

StemForge uses:

- ruff for linting/formatting  
- mypy for type checking  
- pytest for tests  

Run all checks:

    make check

Run tests only:

    make test

---

## 📦 Adding Dependencies

All dependencies must be added to `pyproject.toml`.

After editing dependencies, resync the environment:

    uv sync

This updates `.venv/` and `uv.lock`.

---

## 🧭 Project Structure

StemForge is organized into:

    gui/         – Gradio UI
    pipelines/   – Demucs, BasicPitch, MusicGen pipelines
    models/      – Model loader stubs
    utils/       – Audio/MIDI helpers, logging, errors
    config.py    – Configuration layer

---

## 🤝 Pull Requests

1. Create a feature branch  
2. Make your changes  
3. Run `make check`  
4. Submit a PR  

Thank you for helping build StemForge!
