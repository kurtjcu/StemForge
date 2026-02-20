# StemForge

![Build](https://img.shields.io/badge/build-passing-brightgreen)
![CUDA](https://img.shields.io/badge/CUDA-12.8-blue)
![PyTorch](https://img.shields.io/badge/PyTorch-2.10.0%2Bcu128-informational)
![Demucs](https://img.shields.io/badge/Demucs-enabled-success)
![BasicPitch](https://img.shields.io/badge/BasicPitch-enabled-success)
![MusicGen](https://img.shields.io/badge/MusicGen-coming_soon-yellow)
![License](https://img.shields.io/github/license/tsondo/StemForge)

StemForge is a local, GPU‑accelerated desktop application for AI‑powered audio work:

- **Demucs** — stem separation (vocals, drums, bass, other)
- **BasicPitch** — polyphonic MIDI extraction
- **MusicGen** — text/melody‑conditioned audio generation (backend coming soon; GUI ready)

Everything runs locally with deterministic environments via uv.

---

## Requirements

### Python
- Python 3.11

If missing:
    uv python install 3.11

### FFmpeg ≥ 5.1 (with development headers)
Required for PyAV.

Ubuntu 22.04:
    sudo add-apt-repository -y ppa:ubuntuhandbook1/ffmpeg7
    sudo apt update
    sudo apt install ffmpeg libavcodec-dev libavformat-dev libavdevice-dev \
        libavfilter-dev libavutil-dev libswscale-dev libswresample-dev

Ubuntu 24.04+:
    sudo apt install ffmpeg libavcodec-dev libavformat-dev

Fedora:
    sudo dnf install ffmpeg-free ffmpeg-free-devel

Arch / Manjaro:
    sudo pacman -S ffmpeg

Other distros:
- Ensure ffmpeg ≥ 5.1
- Ensure development headers are installed

### GPU (optional)
- NVIDIA driver supporting CUDA 12.8
- PyTorch 2.10.0+cu128 (pinned) will use the GPU automatically
- CPU‑only works everywhere, just slower

---

## Install & Run

Clone:
    git clone git@github.com:tsondo/StemForge.git
    cd StemForge

Sync environment:
    uv sync

Run:
    uv run stemforge

---

## Project Structure

    StemForge/
    ├── gui/
    │   ├── app.py                  # Main window + theme
    │   ├── constants.py            # Shared output dirs
    │   └── components/
    │       ├── loader.py
    │       ├── demucs_panel.py
    │       ├── basicpitch_panel.py
    │       ├── musicgen_panel.py   # Fully interactive; backend pending
    │       └── export_panel.py
    │
    ├── pipelines/
    │   ├── demucs_pipeline.py
    │   ├── basicpitch_pipeline.py
    │   ├── vocal_midi_pipeline.py
    │   ├── musicgen_pipeline.py    # Stub
    │   └── resample.py
    │
    ├── models/
    │   ├── demucs_loader.py
    │   ├── basicpitch_loader.py
    │   └── musicgen_loader.py      # Stub
    │
    └── utils/
        ├── audio_io.py
        ├── midi_io.py
        ├── logging_utils.py
        └── errors.py

---

## Model Cache & Logs

Models:
    ~/.cache/stemforge/

Logs:
    ~/.local/share/stemforge/logs/stemforge.log

---

## Current Status

- Demucs pipeline fully implemented
- BasicPitch pipeline fully implemented
- Vocal MIDI pipeline fully implemented
- GUI complete and fully wired
- Export panel supports copy + transcoding
- Deterministic uv environment
- MusicGen backend coming soon (GUI already functional)

StemForge is evolving into... not sure what, but its musical!
