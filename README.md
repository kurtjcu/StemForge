# StemForge

StemForge is a desktop application that unifies three AI audio pipelines into a
single, cohesive workflow with a graphical interface:

1. **Demucs** – high‑quality source separation (vocals, drums, bass, other)
2. **BasicPitch** – polyphonic MIDI extraction from separated stems
3. **MusicGen** – text‑conditioned (and melody‑conditioned) audio generation  
   *(MusicGen backend coming soon; GUI panel already implemented)*

All pipelines run locally, with GPU acceleration when available.

---

## 🔧 Environment Setup

StemForge uses **uv** for dependency management, virtual environments, and fully
reproducible installs. The repository includes a committed `uv.lock`, so every
machine resolves identical versions of all dependencies.

### Prerequisites

#### **Python**
- Python **3.11** is required.
- If your system Python is older/newer, install 3.11 via uv:

      uv python install 3.11

#### **FFmpeg 5.1+ (with development headers)**  
Required for **PyAV**, which compiles against your system FFmpeg and needs the
`ch_layout` API introduced in FFmpeg 5.1.

Installation varies by distribution:

##### **Ubuntu / Debian**
Ubuntu 22.04 ships FFmpeg 4.x, which is too old. Options:

**Option A — Install FFmpeg 7 from PPA:**

```
sudo add-apt-repository -y ppa:ubuntuhandbook1/ffmpeg7
sudo apt update
sudo apt install ffmpeg libavcodec-dev libavformat-dev libavdevice-dev \
    libavfilter-dev libavutil-dev libswscale-dev libswresample-dev
```

**Option B — Use distro packages (Ubuntu 24.04+)**  
Ubuntu 24.04 includes FFmpeg 6.x+ which is compatible.

##### **Fedora**
Fedora provides modern FFmpeg builds:

```
sudo dnf install ffmpeg-free ffmpeg-free-devel
```

##### **Arch / Manjaro**
FFmpeg is already recent enough:

```
sudo pacman -S ffmpeg
```

##### **Other distros**
Ensure:

- `ffmpeg` is version **5.1 or newer**
- development headers (`libavcodec-dev`, `ffmpeg-devel`, etc.) are installed

---

## ⚡ GPU Acceleration (Optional)

StemForge uses PyTorch **2.10.0+cu128**, pinned in the lockfile.

To enable GPU acceleration:

- Install an NVIDIA driver supporting **CUDA 12.8**
- Ensure the driver is active before running `uv sync`

If no compatible GPU is present, StemForge runs entirely on CPU.

---

## 1. Install uv

Follow instructions at:

https://github.com/astral-sh/uv

Or install via script:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 2. Sync the environment

From the project root:

```
uv sync
```

This will:

- create `.venv/`
- install all dependencies exactly as pinned in `uv.lock`
- install StemForge in editable mode

---

## 3. Run StemForge

```
uv run stemforge
```

Or:

```
uv run python -m stemforge
```

This launches the full DearPyGUI desktop application.

---

## 4. Development Tools

Run linting, type checks, and tests:

```
make check
```

Tests only:

```
make test
```

---

## Project Structure

```
StemForge/
├── gui/                        # Graphical user interface
│   ├── app.py                  # Main window, theme, dialogs, tab layout
│   ├── constants.py            # Shared GUI paths (stems/, midi/, exports/, etc.)
│   └── components/             # Individual UI panels
│       ├── loader.py           # Audio file selection
│       ├── demucs_panel.py     # Source separation controls
│       ├── basicpitch_panel.py # MIDI extraction controls
│       ├── musicgen_panel.py   # Prompt, melody, sliders (backend pending)
│       └── export_panel.py     # Export/transcode separated stems + MIDI
│
├── pipelines/                  # AI pipeline orchestration
│   ├── demucs_pipeline.py      # Fully implemented + GPU‑aware
│   ├── basicpitch_pipeline.py  # Fully implemented + MIDI output
│   ├── vocal_midi_pipeline.py  # End‑to‑end vocal MIDI extraction
│   ├── musicgen_pipeline.py    # Stub (GUI implemented; backend coming soon)
│   └── resample.py             # soxr‑based sample‑rate conversion
│
├── models/                     # Model loading and caching
│   ├── demucs_loader.py        # Checkpoint loader with registry + checksum
│   ├── basicpitch_loader.py    # TF SavedModel loader with GPU disabled
│   └── musicgen_loader.py      # Placeholder for transformer + EnCodec loader
│
├── utils/                      # Shared utilities
│   ├── audio_io.py             # Robust audio I/O, MP3 writing, channel ops
│   ├── midi_io.py              # PrettyMIDI-based note → MIDI assembly
│   ├── logging_utils.py        # Rotating file + console logger
│   └── errors.py               # Unified exception types
│
└── README.md
```

---

## Model Cache

Models are stored under:

```
~/.cache/stemforge/
```

With subdirectories:

```
demucs/
basicpitch/
musicgen/
```

Logs are written to:

```
~/.local/share/stemforge/logs/stemforge.log
```

with rotation (10 MiB × 5 backups).

---

## Status

StemForge is now a **fully functional desktop application**:

- ✔ Demucs pipeline implemented and GPU‑accelerated  
- ✔ BasicPitch pipeline implemented with MIDI quantization  
- ✔ Vocal MIDI pipeline implemented and tested  
- ✔ GUI implemented with all panels wired  
- ✔ Export panel supports copying and transcoding  
- ✔ Deterministic uv‑managed environment  
- ⏳ MusicGen backend coming soon (GUI panel already live)

StemForge is evolving into a complete local AI audio workstation.

