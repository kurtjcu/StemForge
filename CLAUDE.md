# StemForge — Claude Context

## Workflow
- Break all multi-step tasks into a numbered plan before starting
- After each major step, commit and push, then pause and report: what was done, what changed, what's next
- Wait for explicit "continue" or "proceed" confirmation before moving to the next step

## What this project is

AI-powered audio processing web application with six core pipelines:
- **Demucs** — source separation (vocals, drums, bass, other) — 4 models
- **BS-Roformer** — high-quality separation with 2-stem, 4-stem, and 6-stem (guitar + piano) models
- **BasicPitch** — polyphonic MIDI extraction from separated stems (instruments)
- **Vocal MIDI** — vocal pitch-to-MIDI via faster-whisper + PYIN pitch tracking
- **Stable Audio Open** — text-conditioned audio generation with optional audio and MIDI conditioning (Synth tab)
- **AceStep** — full song generation from style descriptions + lyrics (Compose tab, runs as subprocess)

Additional systems:
- **Model registry** (`models/registry.py`) — frozen `ModelSpec` descriptors for all models; single source of truth for device rules, sample rates, capabilities, metadata, and pipeline defaults
- **Audio profiler** (`utils/audio_profile.py`) — spectral analysis that recommends the best engine/model for a given audio file
- **Mix engine** — multi-track mixer combining audio stems and MIDI-rendered tracks with per-track instrument, volume, and FLAC render

**Architecture**: FastAPI backend (`backend/`) + vanilla HTML/CSS/JS frontend (`frontend/`) + AceStep subprocess.
Run with `python run.py` → open `http://localhost:8765` in browser.
AceStep runs on port 8001 by default. Disable with `--no-acestep`.

---

## Current state

All pipelines and the full web UI are implemented:

- Demucs separation — 4 models (htdemucs, htdemucs_ft, mdx_extra, mdx_extra_q), CUDA fallback for MDX-Net
- BS-Roformer separation — 6 models including ViperX vocals (SDR 12.97), KJ vocals, ZFTurbo 4-stem, jarredou 6-stem
- Automatic engine/model recommendation from spectral audio analysis
- MIDI extraction — BasicPitch for instruments, faster-whisper + PYIN pitch for vocals
- MIDI preview — server-side FluidSynth render, streamed to browser via wavesurfer.js
- Mix tab — per-track volume controls, audio/MIDI source types, FLAC render
- Stable Audio Open generation (Synth tab) — text + audio + MIDI conditioning, up to 600 s (chunked at 47 s), Vocal Preservation Mode
- AceStep generation (Compose tab) — full song creation/rework, AI lyrics, 3-column UI, cross-tab integration
- Export panel — all pipeline outputs, 4 audio formats (wav/flac/mp3/ogg), zip download
- Waveform visualization via wavesurfer.js with global transport bar
- Deterministic uv environment, Python 3.11, CUDA 13.0 wheels
- macOS support via MPS acceleration (separate `pyproject.toml.MAC`)

---

## Project structure

```
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
├── backend/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app, router registration, static mount
│   ├── api/
│   │   ├── __init__.py
│   │   ├── system.py               # /api/health, /api/device, /api/models, /api/session
│   │   ├── audio.py                # /api/upload, /api/audio/stream|download|waveform|info, /api/audio/profile
│   │   ├── separate.py             # /api/separate, /api/separate/recommend, /api/jobs/{id}
│   │   ├── midi.py                 # /api/midi/extract|render|save|stems
│   │   ├── generate.py             # /api/generate (Synth tab)
│   │   ├── compose.py              # /api/compose/* (Compose tab — AceStep proxy)
│   │   ├── acestep_wrapper.py      # HTTP client for AceStep API
│   │   ├── mix.py                  # /api/mix/tracks|render|add-audio|add-midi
│   │   └── export.py               # /api/export, /api/export/download-zip
│   └── services/
│       ├── __init__.py
│       ├── job_manager.py          # Background thread runner + in-memory job store
│       ├── session_store.py        # Thread-safe session state (replaces old AppState)
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
│       ├── mix.js                  # Mix tab
│       ├── generate.js             # Synth tab (Stable Audio Open)
│       ├── compose.js              # Compose tab (AceStep — 3-col layout)
│       ├── export.js               # Export tab
│       ├── midi-viz.js             # Canvas piano roll
│       └── audio-player.js         # Global transport bar
│
├── pipelines/                      # UNCHANGED — all pipeline logic
│   ├── demucs_pipeline.py
│   ├── roformer_pipeline.py
│   ├── midi_pipeline.py
│   ├── basicpitch_pipeline.py
│   ├── vocal_midi_pipeline.py
│   ├── musicgen_pipeline.py
│   └── resample.py
│
├── models/                         # UNCHANGED — model registry + loaders
│   ├── registry.py
│   ├── demucs_loader.py
│   ├── roformer_loader.py
│   ├── midi_loader.py
│   ├── basicpitch_loader.py
│   ├── basicpitch/
│   └── musicgen_loader.py
│
├── utils/
│   ├── cache.py                    # Model cache dir resolution (MODEL_LOCATION)
│   ├── paths.py                    # Output directory constants (shared across layers)
│   ├── audio_io.py                 # read_audio / write_audio
│   ├── audio_profile.py            # Spectral analysis + engine recommendation
│   ├── midi_io.py                  # MIDI read / write / helpers
│   ├── device.py                   # get_device / is_mps — platform-aware torch device
│   ├── platform.py                 # get_data_dir — OS-idiomatic data paths
│   ├── logging_utils.py            # configure_logging
│   └── errors.py                   # Custom exception hierarchy
```

---

## Import layer order (no circular imports)

```
utils/  →  models/  →  pipelines/  →  backend/services/  →  backend/api/  →  backend/main.py
```

`utils/paths.py` holds output directory constants — imported by pipelines, backend, and any layer.
`frontend/` is purely static (served by FastAPI's `StaticFiles`).

---

## Backend architecture

### Services (`backend/services/`)

| Service | Purpose |
|---|---|
| `job_manager.py` | `JobManager` — background thread runner, UUID-based job store, progress callback bridge |
| `session_store.py` | `SessionStore` — thread-safe singleton replacing old `AppState`; holds audio/stem/MIDI/mix/compose state |
| `pipeline_manager.py` | Lazy-loaded pipeline singletons with GPU memory lock; `get_demucs()`, `get_roformer()`, etc. |
| `acestep_state.py` | Thread-safe AceStep subprocess status: disabled / starting / running / crashed |

### API Endpoints (`backend/api/`)

| Method | Path | Type | Purpose |
|--------|------|------|---------|
| GET | /api/health | sync | Health check |
| GET | /api/device | sync | GPU/device info |
| GET | /api/models | sync | All registered models |
| GET | /api/session | sync | Current session state |
| DELETE | /api/session | sync | Clear session |
| POST | /api/upload | sync | Upload audio file |
| GET | /api/audio/stream | sync | Stream audio (inline) |
| GET | /api/audio/download | sync | Download audio (attachment) |
| GET | /api/audio/waveform | sync | Downsampled peaks JSON |
| GET | /api/audio/info | sync | Audio metadata |
| POST | /api/audio/profile | sync | Audio profiler + recommendation |
| POST | /api/separate | job | Start separation |
| GET | /api/separate/recommend | sync | Quick engine recommendation |
| GET | /api/jobs/{id} | sync | Poll any job's status |
| POST | /api/midi/extract | job | Start MIDI extraction |
| POST | /api/midi/render | sync | FluidSynth render to WAV |
| POST | /api/midi/save | sync | Save MIDI to disk |
| GET | /api/midi/stems | sync | Available MIDI stem labels |
| POST | /api/generate | job | Start audio generation (Synth) |
| GET | /api/compose/health | sync | AceStep subprocess status |
| POST | /api/compose/generate | async | Start AceStep generation (Compose) |
| GET | /api/compose/status/{id} | sync | Poll AceStep task status |
| GET | /api/compose/audio | sync | Audio proxy from AceStep |
| GET | /api/compose/download/{id}/{n}/audio | sync | Download compose result audio |
| GET | /api/compose/download/{id}/{n}/json | sync | Download compose result metadata |
| POST | /api/compose/generate-lyrics | sync | AI lyrics generation via AceStep LM |
| POST | /api/compose/estimate-duration | sync | Auto-duration estimation |
| POST | /api/compose/estimate-sections | sync | Section structure estimation |
| POST | /api/compose/upload-audio | sync | Upload audio for Rework mode |
| POST | /api/compose/send-to-session | sync | Save compose audio to session |
| GET | /api/mix/tracks | sync | Current track list |
| POST | /api/mix/tracks | sync | Update track state |
| POST | /api/mix/render | job | Render mix to FLAC |
| POST | /api/mix/add-audio | sync | Add manual audio track |
| POST | /api/mix/add-midi | sync | Add manual MIDI track |
| DELETE | /api/mix/tracks/{id} | sync | Remove track |
| POST | /api/export | job | Start export |
| POST | /api/export/download-zip | sync | Zip download |

---

## Frontend architecture

### Event bus pattern (replaces DPG callback wiring)

```
Separate done  → appState.emit("stemsReady", stemPaths)
MIDI done      → appState.emit("midiReady", {labels, noteCounts})
Generate done  → appState.emit("generateReady", audioPath)
Compose done   → appState.emit("composeReady", {path, title, metadata})
Mix done       → appState.emit("mixReady", mixPath)
File loaded    → appState.emit("fileLoaded", audioInfo)
```

Downstream components subscribe in their `init*()` functions:
- MIDI listens to `stemsReady` → populate stem checkboxes
- Mix listens to `stemsReady` + `generateReady` + `composeReady` → add/refresh tracks
- Generate listens to `stemsReady` + `midiReady` + `mixReady` → populate conditioning sources
- Export listens to all (including `composeReady`) → enable artifact checkboxes
- Separate listens to `fileLoaded` → enable separation button

### Job polling

Long-running pipeline jobs use `pollJob(jobId, {onProgress, onDone, onError, interval})` with 2s default interval.

---

## Model registry (`models/registry.py`)

Frozen `ModelSpec` subclasses describe every model variant. Spec types:

| Spec class | Models | Pipeline |
|---|---|---|
| `DemucsSpec` | htdemucs, htdemucs_ft, mdx_extra, mdx_extra_q | `DemucsPipeline` |
| `RoformerSpec` | roformer-viperx-vocals, roformer-kj-vocals, roformer-zfturbo-4stem, roformer-jarredou-6stem, + 2 more | `RoformerPipeline` |
| `BasicPitchSpec` | basicpitch | `BasicPitchPipeline` |
| `WhisperSpec` | whisper-tiny, whisper-base, whisper-small, whisper-medium | `VocalMidiPipeline` |
| `StableAudioSpec` | stable-audio-open-1.0 | `MusicGenPipeline` |

Public API: `get_spec()`, `list_specs()`, `get_loader_kwargs()`, `get_pipeline_defaults()`, `get_gui_metadata()`.

---

## Pipeline interface (all pipelines follow this contract)

```python
pipeline.configure(config)   # supply Config dataclass
pipeline.load_model()        # load weights — raises ModelLoadError
result = pipeline.run(input) # run inference — raises PipelineExecutionError / InvalidInputError
pipeline.clear()             # release GPU memory
```

---

## Exception hierarchy (`utils/errors.py`)

```
StemForgeError
├── ModelLoadError(model_name=)            — weight loading / download failures
├── AudioProcessingError(path=)            — read / write / resample failures
├── PipelineExecutionError(pipeline_name=) — runtime inference failures
└── InvalidInputError(field=)             — pre-processing validation failures
```

---

## Output directories (`utils/paths.py`)

| Constant | Path |
|---|---|
| `STEMS_DIR` | `~/.local/share/stemforge/output/stems/` |
| `MIDI_DIR` | `~/Music/StemForge/` |
| `MUSICGEN_DIR` | `~/.local/share/stemforge/output/musicgen/` |
| `COMPOSE_DIR` | `~/.local/share/stemforge/output/compose/` |
| `MIX_DIR` | `~/.local/share/stemforge/output/mix/` |
| `EXPORT_DIR` | `~/.local/share/stemforge/output/exports/` |

---

## AceStep subprocess

AceStep runs as a separate process managed by `run.py`:

- **Port:** 8001 (configurable via `--acestep-port` or `ACESTEP_PORT`)
- **Disable:** `--no-acestep` flag — Compose tab shows disabled state
- **GPU:** `--gpu N` sets `CUDA_VISIBLE_DEVICES` on the AceStep subprocess only
- **State tracking:** `backend/services/acestep_state.py` — thread-safe status: disabled/starting/running/crashed
- **Graceful degradation:** StemForge stays alive if AceStep crashes. All other tabs work normally.
- **Compose router:** `backend/api/compose.py` proxies requests to AceStep's API via `backend/api/acestep_wrapper.py`

**Tab bar:** Separate · MIDI · Synth · Compose · Mix · Export

---

## Platform notes

- **Linux (primary)**: CUDA 13.0 wheels, uv sync, Python 3.11
- **macOS (Apple Silicon)**: MPS acceleration via `pyproject.toml.MAC`; use `from utils.device import get_device`, never hardcode `"cuda"`
- **FluidSynth**: Required for MIDI preview and Mix tab; GM soundfont auto-discovered

---

## Caches and logs

- Model weights: `~/.cache/stemforge/` (subdirs per model type) — override with `MODEL_LOCATION` env var or `--model-dir` flag
- Cache resolution: `utils/cache.py` → `get_model_cache_base()` / `get_model_cache_dir(subdir)`
- AceStep checkpoints: also reads `MODEL_LOCATION` (forwarded via `_PASSTHROUGH_VARS` in `acestep_state.py`)
- Logs: `~/.local/share/stemforge/logs/stemforge.log`
