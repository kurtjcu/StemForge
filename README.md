# StemForge

![Build](https://img.shields.io/badge/build-passing-brightgreen)
![CUDA](https://img.shields.io/badge/CUDA-12.9%2B-blue)
![PyTorch](https://img.shields.io/badge/PyTorch-2.10.0%2Bcu130-informational)
![Demucs](https://img.shields.io/badge/Demucs-enabled-success)
![BS--Roformer](https://img.shields.io/badge/BS--Roformer-enabled-success)
![MusicGen](https://img.shields.io/badge/MusicGen-enabled-success)
![License](https://img.shields.io/github/license/tsondo/StemForge)

StemForge is a local, GPU‑accelerated desktop application for AI‑powered audio work:

- **Demucs** — stem separation (vocals, drums, bass, other) — 4 models including fine-tuned and MDX variants
- **BS-Roformer** — high-quality separation with 2-stem vocal, 4-stem, and 6-stem (guitar + piano) models
- **MIDI extraction** — polyphonic BasicPitch for instruments, faster-whisper + pitch tracking for vocals
- **Stable Audio Open** — text-conditioned audio generation up to 600 s, with optional audio and MIDI conditioning
- **Export** — transcode any pipeline output (stems, MIDI, generated audio) to wav / flac / mp3 / ogg

Everything runs locally with deterministic environments via uv.

---

## Requirements

### Python
- Python 3.11

If missing:

    uv python install 3.11

### FFmpeg ≥ 5.1 (with development headers)
Required for audio decoding.

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
- NVIDIA driver supporting CUDA 12.9+
- PyTorch 2.10.0+cu130 (pinned) will use the GPU automatically
- CPU‑only works everywhere, just slower

---

## HuggingFace Authentication (required for the Generate tab)

The Generate tab uses [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0),
a gated model. You must accept its license and authenticate before StemForge can download it.

**Step 1 — Accept the license**

Visit https://huggingface.co/stabilityai/stable-audio-open-1.0, sign in with a free
HuggingFace account, and click **Agree and access repository**.

**Step 2 — Create a token**

Go to https://huggingface.co/settings/tokens and create a token with **Read** access.

**Step 3 — Log in locally**

    huggingface-cli login

Paste your token when prompted. It is saved to `~/.cache/huggingface/token` and
picked up automatically by StemForge on every subsequent run — you only need to do
this once.

The model weights (~2 GB) are downloaded on the first Generate run and cached under
`~/.cache/stemforge/musicgen/`.

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
    ├── config.py                       # Aggregate config, env/file loading
    │
    ├── gui/
    │   ├── app.py                      # Main window + theme + render loop
    │   ├── state.py                    # AppState singleton (thread-safe shared state)
    │   ├── constants.py                # Output directory paths
    │   ├── icons.py                    # DearPyGUI icon textures
    │   └── components/
    │       ├── loader.py               # File browser bar (top of window)
    │       ├── file_browser.py         # Reusable custom file/dir browser
    │       ├── waveform_widget.py      # Waveform preview + playback widget
    │       ├── demucs_panel.py         # Separate tab (Demucs + BS-Roformer)
    │       ├── midi_panel.py           # MIDI tab (BasicPitch + vocal MIDI)
    │       ├── musicgen_panel.py       # Generate tab (Stable Audio Open)
    │       └── export_panel.py         # Export tab (copy + transcode)
    │
    ├── pipelines/
    │   ├── demucs_pipeline.py          # Demucs separation pipeline
    │   ├── roformer_pipeline.py        # BS-Roformer separation pipeline
    │   ├── midi_pipeline.py            # Unified MIDI extraction pipeline
    │   ├── basicpitch_pipeline.py      # BasicPitch inference pipeline
    │   ├── vocal_midi_pipeline.py      # Vocal pitch-to-MIDI pipeline
    │   ├── musicgen_pipeline.py        # Stable Audio Open generation pipeline
    │   └── resample.py                 # Audio resampling pipeline
    │
    ├── models/
    │   ├── registry.py                 # Model registry (specs + metadata)
    │   ├── demucs_loader.py            # Demucs model loader
    │   ├── roformer_loader.py          # BS-Roformer model loader
    │   ├── midi_loader.py              # BasicPitch + Whisper loader
    │   ├── basicpitch_loader.py        # Vendored BasicPitch TFLite loader
    │   ├── basicpitch/                 # Vendored BasicPitch (ai-edge-litert)
    │   └── musicgen_loader.py          # Stable Audio Open loader (diffusers)
    │
    └── utils/
        ├── audio_io.py                 # read_audio / write_audio
        ├── midi_io.py                  # MIDI read / write / helpers
        ├── logging_utils.py            # configure_logging
        └── errors.py                   # Custom exception hierarchy

---

## Tabs

### Separate
Choose between **Demucs** (4 models) and **BS-Roformer** (6 models including 6-stem guitar + piano).
An automatic spectral analysis runs when a file is loaded and suggests the best engine and model.
Separated stems are previewed with waveform widgets and can be saved individually.

### MIDI
Extracts MIDI from any separated stem or a manually loaded audio file.
Instrument stems use BasicPitch (polyphonic); vocal stems use faster-whisper + pitch tracking.
Supports Ace-Step JSON metadata auto-detection for BPM, key, and lyrics prefill.

### Generate
Text-conditioned audio generation via Stable Audio Open 1.0 (44,100 Hz stereo).
Optional audio conditioning from a separated stem or loaded file.
Optional MIDI conditioning — BPM, key, and instrument families are appended to the prompt.
Duration up to 600 s (chunked generation, 47 s per chunk).

### Export
Select any combination of stems, MIDI, and generated audio.
Choose output format (wav / flac / mp3 / ogg) and destination folder.
Transcoding is performed automatically when the source and target formats differ.
The checklist auto-refreshes after each pipeline run.

---

## Model Cache & Logs

Models:

    ~/.cache/stemforge/

Logs:

    ~/.local/share/stemforge/logs/stemforge.log

---

## Current Status

All pipelines and the full GUI are implemented and working:

- Demucs separation — 4 models, CUDA fallback for MDX-Net
- BS-Roformer separation — 6 models including 4-stem and 6-stem (guitar + piano)
- Automatic engine/model recommendation from spectral audio analysis
- MIDI extraction — BasicPitch for instruments, faster-whisper + pitch for vocals
- Stable Audio Open generation — text + audio + MIDI conditioning, up to 600 s
- Export panel — all pipeline outputs, 4 audio formats, auto-refresh on pipeline completion
- Waveform preview and playback for all stems and generated audio
- Deterministic uv environment, Python 3.11, CUDA 13.0 wheels

StemForge is evolving into... not sure what, but its musical!
