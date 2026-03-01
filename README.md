<p align="center">
  <img src="StemForgeLogo.png" alt="StemForge" width="260"/>
  <br/>
  <strong>StemForge</strong>
</p>

![Build](https://img.shields.io/badge/build-passing-brightgreen)
![CUDA](https://img.shields.io/badge/CUDA-13.0-blue)
![PyTorch](https://img.shields.io/badge/PyTorch-2.10.0%2Bcu130-informational)
![Demucs](https://img.shields.io/badge/Demucs-enabled-success)
![BS--Roformer](https://img.shields.io/badge/BS--Roformer-enabled-success)
![Stable Audio Open](https://img.shields.io/badge/Stable%20Audio%20Open-enabled-success)
![AceStep](https://img.shields.io/badge/AceStep-enabled-success)
![License](https://img.shields.io/badge/license-PolyForm%20NC%201.0-blue)

StemForge is a local, GPU-accelerated web application for AI-powered audio work:

- **Demucs** — stem separation (vocals, drums, bass, other) — 4 models including fine-tuned and MDX variants
- **BS-Roformer** — high-quality separation with 2-stem vocal, 4-stem, and 6-stem (guitar + piano) models
- **MIDI extraction** — polyphonic BasicPitch for instruments, faster-whisper + pitch tracking for vocals; per-stem MIDI preview via FluidSynth
- **Stable Audio Open** — text-conditioned audio generation up to 600 s, with optional audio and MIDI conditioning (Synth tab)
- **AceStep** — full song generation from style descriptions + lyrics, with Create and Rework modes (Compose tab)
- **Mix** — multi-track mixer combining audio stems, MIDI-rendered tracks, synth outputs, and composed songs; per-track instrument, volume, and FLAC render
- **Export** — transcode any pipeline output (stems, MIDI, mix, generated audio, composed songs) to wav / flac / mp3 / ogg

Everything runs locally with deterministic environments via uv.

**Tab bar:** Separate · MIDI · Synth · Compose · Mix · Export

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

### FFmpeg >= 5.1 (with development headers)
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
- Ensure ffmpeg >= 5.1
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
- NVIDIA driver supporting CUDA 13.0+
- PyTorch 2.10.0+cu130 (pinned) will use the GPU automatically
- CPU-only works everywhere, just slower

### WSL (Windows Subsystem for Linux)

StemForge is a web application — audio playback happens in the browser, so no
PulseAudio or sounddevice setup is needed. Install FluidSynth for MIDI preview:

    sudo apt install libfluidsynth3 libfluidsynth-dev fluid-soundfont-gm

Then follow the standard Install & Run steps below.

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
- **AceStep** (Compose tab) works on macOS — the subprocess handles MPS detection independently.

### Performance

MPS acceleration is used automatically when available (Apple Silicon).
Expect significantly faster inference than CPU-only, but slower than CUDA on a discrete GPU.

---

## HuggingFace Authentication (required for the Synth tab)

The Synth tab uses [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0),
a gated model. You must accept its license and authenticate before StemForge can download it.
See [docs/SYNTH.md](docs/SYNTH.md) for full documentation on conditioning modes, parameters,
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

The model weights (~2 GB) are downloaded on the first Synth run and cached under
`~/.cache/stemforge/musicgen/`.

---

## Install & Run

Clone (use `--recursive` to pull the AceStep submodule):

    git clone --recursive git@github.com:tsondo/StemForge.git
    cd StemForge

Sync environment:

    uv sync

Run:

    uv run python run.py

Then open http://localhost:8765 in your browser.

### Updating the AceStep submodule

When Ace-Step-Wrangler has new commits (bug fixes, model support, etc.),
pull them into StemForge:

    cd Ace-Step-Wrangler
    git pull origin main
    cd ..
    git add Ace-Step-Wrangler
    git commit -m "Update Ace-Step-Wrangler submodule"
    git push

If Wrangler's nested submodule (`vendor/ACE-Step-1.5`) also changed, pull
that first:

    cd Ace-Step-Wrangler
    git submodule update --remote vendor/ACE-Step-1.5
    git add vendor/ACE-Step-1.5
    git commit -m "Update ACE-Step vendor"
    git push origin main
    cd ..
    git add Ace-Step-Wrangler
    git commit -m "Update Ace-Step-Wrangler submodule"
    git push

After updating, run `uv sync` to pick up any dependency changes.

### Launcher flags

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | 8765 | StemForge server port (also `STEMFORGE_PORT` env var) |
| `--no-acestep` | off | Disable AceStep subprocess — all tabs except Compose work normally |
| `--acestep-port` | 8001 | AceStep API port (also `ACESTEP_PORT` env var) |
| `--gpu N` | auto | Set `CUDA_VISIBLE_DEVICES=N` on the AceStep subprocess only |
| `--model-dir` | `~/.cache/stemforge/` | Shared model cache directory (also `MODEL_LOCATION` env var) |

---

## Project Structure

    StemForge/
    ├── run.py                          # Launcher: uvicorn + AceStep subprocess management
    ├── pyproject.toml
    ├── pyproject.toml.MAC              # macOS variant (MPS, no CUDA index)
    │
    ├── Ace-Step-Wrangler/              # Git submodule (independently runnable)
    │   ├── vendor/ACE-Step-1.5/        # Nested submodule — upstream AceStep
    │   ├── backend/                    # Wrangler's standalone backend (reference)
    │   ├── frontend/                   # Wrangler's standalone frontend (reference)
    │   └── run.py                      # Wrangler's standalone launcher (unused in StemForge)
    │
    ├── docs/
    │   ├── SYNTH.md                    # Synth tab deep-dive (conditioning, params, vocal stems)
    │   ├── COMPOSE.md                  # Compose tab reference (modes, lyrics, controls, cross-tab)
    │   ├── FUTURE_PLANS.md             # Roadmap: voice transformation, packaging, DAW integration
    │   └── MIGRATION_PLAN.md           # DearPyGUI → FastAPI migration plan (complete)
    │
    ├── backend/
    │   ├── main.py                     # FastAPI app, router registration, static mount
    │   ├── api/
    │   │   ├── system.py               # /api/health, /api/device, /api/models, /api/session
    │   │   ├── audio.py                # /api/upload, /api/audio/stream|download|waveform|info|profile
    │   │   ├── separate.py             # /api/separate, /api/separate/recommend, /api/jobs/{id}
    │   │   ├── midi.py                 # /api/midi/extract|render|save|stems
    │   │   ├── generate.py             # /api/generate (Synth tab)
    │   │   ├── compose.py              # /api/compose/* (Compose tab — AceStep proxy)
    │   │   ├── acestep_wrapper.py      # HTTP client for AceStep API
    │   │   ├── mix.py                  # /api/mix/tracks|render|add-audio|add-midi
    │   │   └── export.py               # /api/export, /api/export/download-zip
    │   └── services/
    │       ├── job_manager.py          # Background thread runner + in-memory job store
    │       ├── session_store.py        # Thread-safe session state
    │       ├── pipeline_manager.py     # Lazy-loaded pipeline singletons
    │       └── acestep_state.py        # AceStep subprocess status (disabled/starting/running/crashed)
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
    │       ├── generate.js             # Synth tab (Stable Audio Open)
    │       ├── compose.js              # Compose tab (AceStep — 3-column layout)
    │       ├── mix.js                  # Mix tab
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
    │   └── musicgen_loader.py          # Stable Audio Open loader
    │
    └── utils/
        ├── cache.py                    # Model cache dir resolution (MODEL_LOCATION)
        ├── paths.py                    # Output directory constants
        ├── audio_io.py                 # read_audio / write_audio
        ├── audio_profile.py            # Spectral analysis + engine recommendation
        ├── midi_io.py                  # MIDI read / write / helpers
        ├── device.py                   # get_device / is_mps — platform-aware torch device
        ├── platform.py                 # get_data_dir — OS-idiomatic data paths
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

### Synth
Text-conditioned audio generation via Stable Audio Open 1.0 (44,100 Hz stereo).
Optional conditioning from audio stems, MIDI, or the current mix.
Duration up to 600 s (chunked generation, 47 s per chunk).
Includes Vocal Preservation Mode.
See [docs/SYNTH.md](docs/SYNTH.md) for full documentation.

### Compose
Full song generation via AceStep 1.5, running as a managed subprocess.
See [docs/COMPOSE.md](docs/COMPOSE.md) for full documentation.

- **Create mode** — build a song from genre/mood tags, song parameters, and lyrics (manual, AI-generated, or instrumental)
- **Rework mode** — transform an existing audio file via Reimagine (full regeneration) or Fix & Blend (region-targeted)
- **Cross-tab integration** — send composed audio to Separate for stem extraction, or to Mix for multi-track blending

AceStep model weights (~20 GB) are downloaded on first use to `MODEL_LOCATION` (or `checkpoints/` in the submodule if unset).

### Mix
Combines audio stems, MIDI-rendered tracks, synth outputs, and composed songs into a single stereo mix.
Tracks appear automatically after Separate, MIDI extraction, Synth generation, or Compose.
Per-track volume controls, instrument selection (MIDI tracks), and enable/disable toggle.
Renders to FLAC.

### Export
Select any combination of pipeline outputs, choose format (wav/flac/mp3/ogg),
and download individually or as a ZIP archive.

---

## Model Cache & Logs

Models:

    ~/.cache/stemforge/

Logs:

    ~/.local/share/stemforge/logs/stemforge.log

### Shared model cache

Two users on the same workstation can avoid duplicate downloads by pointing at a single directory:

    # Via environment variable
    MODEL_LOCATION=/data/models uv run python run.py

    # Via CLI flag
    uv run python run.py --model-dir /data/models

All model loaders (Demucs, BS-Roformer, Stable Audio Open, Whisper, AceStep) will read from and write to that path. Demucs downloads (via `torch.hub`) are redirected by setting `TORCH_HOME` automatically. AceStep (Ace-Step-Wrangler) reads the same `MODEL_LOCATION` variable for its checkpoint directory.

---

## Current Status

All pipelines and the full web UI are implemented and working:

- Demucs separation — 4 models, CUDA fallback for MDX-Net
- BS-Roformer separation — 6 models including 4-stem and 6-stem (guitar + piano)
- Automatic engine/model recommendation from spectral audio analysis
- MIDI extraction — BasicPitch for instruments, faster-whisper + pitch for vocals
- MIDI preview — per-stem FluidSynth render, streamed to browser via wavesurfer.js
- Stable Audio Open generation (Synth tab) — text + audio + MIDI conditioning, up to 600 s (chunked at 47 s), Vocal Preservation Mode
- AceStep generation (Compose tab) — full song creation/rework, AI lyrics, 3-column UI, cross-tab integration
- Mix tab — per-track instrument/volume controls, audio/MIDI/synth/compose source types, FLAC render
- Export panel — all pipeline outputs, 4 audio formats (wav/flac/mp3/ogg), zip download
- Waveform visualization via wavesurfer.js with global transport bar
- Deterministic uv environment, Python 3.11, CUDA 13.0 wheels
- macOS support via MPS acceleration (separate `pyproject.toml.MAC`)

StemForge is evolving into a musical playground where you can regenerate and remix any part of any song.

---

## License

StemForge is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).
Free for personal, educational, research, and other noncommercial use.

**Commercial use requires a paid commercial license.**
Contact [tsondo@gmail.com](mailto:tsondo@gmail.com) to discuss terms.
