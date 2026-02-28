<p align="center">
  <img src="StemForgeLogo.png" alt="StemForge" width="260"/>
  <br/>
  <strong>StemForge</strong>
</p>

![Build](https://img.shields.io/badge/build-passing-brightgreen)
![CUDA](https://img.shields.io/badge/CUDA-12.9%2B-blue)
![PyTorch](https://img.shields.io/badge/PyTorch-2.10.0%2Bcu130-informational)
![Demucs](https://img.shields.io/badge/Demucs-enabled-success)
![BS--Roformer](https://img.shields.io/badge/BS--Roformer-enabled-success)
![MusicGen](https://img.shields.io/badge/MusicGen-enabled-success)
![License](https://img.shields.io/badge/license-PolyForm%20NC%201.0-blue)

StemForge is a local, GPU‑accelerated web application for AI‑powered audio work:

- **Demucs** — stem separation (vocals, drums, bass, other) — 4 models including fine-tuned and MDX variants
- **BS-Roformer** — high-quality separation with 2-stem vocal, 4-stem, and 6-stem (guitar + piano) models
- **MIDI extraction** — polyphonic BasicPitch for instruments, faster-whisper + pitch tracking for vocals; per-stem MIDI preview via FluidSynth
- **Mix** — multi-track mixer combining audio stems and MIDI-rendered tracks; per-track instrument, volume, and FLAC render
- **Stable Audio Open** — text-conditioned audio generation up to 600 s, with optional audio and MIDI conditioning
- **Export** — transcode any pipeline output (stems, MIDI, mix, generated audio) to wav / flac / mp3 / ogg

Everything runs locally with deterministic environments via uv.

See [Future Plans](docs/FUTURE_PLANS.md) for the roadmap, including voice transformation and native packaging.

---

## Requirements

### uv
StemForge uses [uv](https://docs.astral.sh/uv/) to manage the Python version and all dependencies.
Install it once and `uv sync` takes care of the rest.

Ubuntu / Debian:

    curl -LsSf https://astral.sh/uv/install.sh | sh

Fedora / RHEL / CentOS:

    curl -LsSf https://astral.sh/uv/install.sh | sh

Arch / Manjaro:

    sudo pacman -S uv

openSUSE:

    curl -LsSf https://astral.sh/uv/install.sh | sh

Any distro (pipx fallback):

    pipx install uv

After installing, open a new terminal (or run `source $HOME/.local/bin/env`) so the `uv`
command is on your PATH.

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

### FluidSynth + GM Soundfont (required for MIDI preview and Mix tab)

Fedora:

    sudo dnf install fluidsynth fluidsynth-devel fluid-soundfont-gm

Ubuntu / Debian:

    sudo apt install libfluidsynth3 libfluidsynth-dev fluid-soundfont-gm

Arch / Manjaro:

    sudo pacman -S fluidsynth soundfont-fluid

The GM soundfont is auto-discovered at startup.
On Fedora it installs to
`/usr/share/soundfonts/FluidR3_GM.sf2`; use the Browse button on the Mix tab
to point StemForge at a different `.sf2` file if needed.

### GPU (optional)
- NVIDIA driver supporting CUDA 12.9+
- PyTorch 2.10.0+cu130 (pinned) will use the GPU automatically
- CPU‑only works everywhere, just slower

### Audio on WSL (Windows Subsystem for Linux)
StemForge detects WSL automatically and routes audio through PulseAudio. You need
these system packages installed before running StemForge:

    sudo apt install libportaudio2 pulseaudio-utils libasound2-plugins libfluidsynth3 fluid-soundfont-gm

- **libportaudio2** — PortAudio shared library (required by `sounddevice`)
- **pulseaudio-utils** — provides `pactl` for verifying the audio server
- **libasound2-plugins** — ALSA-PulseAudio bridge (Ubuntu 22.04's PortAudio is built
  against ALSA only; this plugin routes ALSA output through PulseAudio)
- **libfluidsynth3** — FluidSynth C library (required by `pyfluidsynth` for MIDI preview)
- **fluid-soundfont-gm** — General MIDI soundfont used by the MIDI and Mix tabs

**Windows 11 (WSLg) — recommended**

WSLg ships PulseAudio support out of the box. Verify it is working:

    pactl info

If that returns audio server info, you are done — StemForge will find the socket
automatically.

**Windows 10 (or WSLg not working)**

Install [PulseAudio for Windows](https://github.com/pgaskin/pulseaudio-win32) on the
Windows side and configure it to accept TCP connections, then expose the server address
to WSL:

    export PULSE_SERVER=tcp:$(grep nameserver /etc/resolv.conf | awk '{print $2}'):4713

Add that line to your `~/.bashrc` so it persists across sessions. Refer to the
PulseAudio for Windows documentation for enabling the TCP module in `default.pa`.

**Verify devices are detected**

After installing the packages above, confirm that PortAudio can see PulseAudio:

    python -c "import sounddevice; print(sounddevice.query_devices())"

You should see at least one `pulse` device listed. If the list is empty, restart WSL
from PowerShell (`wsl --shutdown`) and try again.

**Troubleshooting**

If you get no audio or a "device -1" error on startup, confirm PulseAudio is reachable:

    pactl info 2>/dev/null && echo "Audio OK" || echo "PulseAudio not found"

StemForge will not attempt JACK or direct ALSA output under WSL.

---

## macOS Support

macOS on **Apple Silicon** (M1/M2/M3) is supported via MPS acceleration.
Intel Macs will run CPU-only.

### Setup

**Step 1** — Copy the macOS pyproject file before installing:

    cp pyproject.toml.MAC pyproject.toml
    uv sync

**Step 2** — Install FluidSynth:

    brew install fluid-synth

**Step 3** — Set the library path so pyfluidsynth can find it:

    export DYLD_LIBRARY_PATH="$(brew --prefix fluid-synth)/lib:$DYLD_LIBRARY_PATH"

Add the `export` line to your `~/.zshrc` so it persists across sessions.

### macOS limitations

- **`mdx_extra_q` Demucs model** is not available on macOS (requires `diffq`, which does not build on macOS). The model is automatically hidden from the UI.
- **BasicPitch MIDI extraction** may have limited functionality on macOS — `ai-edge-litert` (the TFLite runtime) is a Linux-only package. The MIDI tab will surface a clear error if this is attempted.
- **Vocal MIDI** (faster-whisper) works on macOS.
- **Stable Audio Open** generation works on macOS via MPS.

### Performance

MPS acceleration is used automatically when available (Apple Silicon).
Expect significantly faster inference than CPU-only, but slower than CUDA on a discrete GPU.

---

## HuggingFace Authentication (required for the Generate tab)

The Generate tab uses [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0),
a gated model. You must accept its license and authenticate before StemForge can download it.
See [docs/GENERATE.md](docs/GENERATE.md) for full documentation on conditioning modes, parameters,
and Vocal Preservation Mode.

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

    uv run python run.py

Then open http://localhost:8765 in your browser.

---

## Project Structure

    StemForge/
    ├── run.py                          # uvicorn launcher (port 8765)
    ├── config.py                       # Aggregate config, env/file loading
    │
    ├── docs/
    │   ├── GENERATE.md                 # Generate tab deep-dive (conditioning, params, vocal stems)
    │   ├── FUTURE_PLANS.md             # Roadmap: voice transformation, packaging, DAW integration
    │   └── MIGRATION_PLAN.md           # DearPyGUI → FastAPI migration plan
    │
    ├── backend/
    │   ├── main.py                     # FastAPI app, router registration, static mount
    │   ├── api/
    │   │   ├── system.py               # /api/health, /api/device, /api/models, /api/session
    │   │   ├── audio.py                # /api/upload, /api/audio/stream|download|waveform|info
    │   │   ├── separate.py             # /api/separate, /api/separate/recommend, /api/jobs/{id}
    │   │   ├── midi.py                 # /api/midi/extract|render|save|stems
    │   │   ├── generate.py             # /api/generate
    │   │   ├── mix.py                  # /api/mix/tracks|render|add-audio|add-midi
    │   │   └── export.py               # /api/export, /api/export/download-zip
    │   └── services/
    │       ├── job_manager.py          # Background thread runner + in-memory job store
    │       ├── session_store.py        # Thread-safe session state
    │       └── pipeline_manager.py     # Lazy-loaded pipeline singletons
    │
    ├── frontend/
    │   ├── index.html                  # SPA shell — header, tab bar, tab panels, transport bar
    │   ├── style.css                   # Design tokens + full layout (dark DAW aesthetic)
    │   ├── app.js                      # State management, event bus, tab switching, poll helper
    │   └── components/
    │       ├── loader.js               # Drag-and-drop upload + file info
    │       ├── waveform.js             # wavesurfer.js wrapper
    │       ├── separate.js             # Separation tab
    │       ├── midi.js                 # MIDI tab
    │       ├── mix.js                  # Mix tab
    │       ├── generate.js             # Generate tab
    │       ├── export.js               # Export tab
    │       ├── midi-viz.js             # Canvas piano roll
    │       └── audio-player.js         # Global transport bar
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
        ├── paths.py                    # Output directory constants
        ├── audio_io.py                 # read_audio / write_audio
        ├── audio_profile.py            # Spectral analysis + engine recommendation
        ├── midi_io.py                  # MIDI read / write / helpers
        ├── device.py                   # get_device / is_mps — platform-aware torch device
        ├── platform.py                 # get_data_dir — OS-idiomatic data paths
        ├── wsl.py                      # WSL detection + PulseAudio routing
        ├── logging_utils.py            # configure_logging
        └── errors.py                   # Custom exception hierarchy

---

## Tabs

### Separate
Choose between **Demucs** (4 models) and **BS-Roformer** (6 models including 6-stem guitar + piano).
"Help me choose" runs spectral analysis and suggests the best engine and model.
Separated stems appear as inline players with waveform visualization, play/pause, stop, rewind,
and save-as buttons.

### MIDI
Extracts MIDI from any separated stem or a manually loaded audio file.
Instrument stems use BasicPitch (polyphonic); vocal stems use faster-whisper + pitch tracking.
Each stem gets a preview player rendered server-side via FluidSynth.
Extracted MIDI is kept in memory until explicitly saved.

### Mix
Combines audio stems and MIDI-rendered tracks into a single stereo mix.
Tracks appear automatically after Separate and/or MIDI extraction.
Per-track volume controls, instrument selection (MIDI tracks), and enable/disable toggle.
Renders to FLAC.

### Generate
Text-conditioned audio generation via Stable Audio Open 1.0 (44,100 Hz stereo).
Optional conditioning from audio stems, MIDI, or the current mix.
Duration up to 600 s (chunked generation, 47 s per chunk).
Includes Vocal Preservation Mode.
See [docs/GENERATE.md](docs/GENERATE.md) for full documentation.

### Export
Select any combination of pipeline outputs, choose format (wav/flac/mp3/ogg),
and download individually or as a ZIP archive.

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
- MIDI preview — per-stem FluidSynth playback with default GM instruments, in-memory until saved
- Mix tab — per-track instrument/volume controls, solo preview, click-to-seek master timeline, FLAC render
- Stable Audio Open generation — text + audio + MIDI conditioning, up to 600 s
- Export panel — all pipeline outputs including mix, 4 audio formats, auto-refresh on pipeline completion
- Waveform and MIDI visualizers with second-labeled ruler ticks and click-to-seek on all plots
- WSL audio support — auto-detects WSL via `/proc/version`, routes through WSLg PulseAudio socket
- Deterministic uv environment, Python 3.11, CUDA 13.0 wheels

StemForge is evolving into a musical playground where you can regenerate and remix any part of any song.

---

## License

StemForge is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).
Free for personal, educational, research, and other noncommercial use.

**Commercial use requires a paid commercial license.**
Contact [tsondo@gmail.com](mailto:tsondo@gmail.com) to discuss terms.
