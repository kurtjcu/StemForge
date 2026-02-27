# StemForge — Claude Context

## Workflow
- Break all multi-step tasks into a numbered plan before starting
- After each major step, pause and report: what was done, what changed, what's next
- Wait for explicit "continue" or "proceed" confirmation before moving to the next step

## What this project is

AI-powered audio processing desktop application with five core pipelines:
- **Demucs** — source separation (vocals, drums, bass, other) — 4 models
- **BS-Roformer** — high-quality separation with 2-stem, 4-stem, and 6-stem (guitar + piano) models
- **BasicPitch** — polyphonic MIDI extraction from separated stems (instruments)
- **Vocal MIDI** — vocal pitch-to-MIDI via faster-whisper + PYIN pitch tracking
- **Stable Audio Open** — text-conditioned audio generation with optional audio and MIDI conditioning

Additional systems:
- **Model registry** (`models/registry.py`) — frozen `ModelSpec` descriptors for all models; single source of truth for device rules, sample rates, capabilities, GUI metadata, and pipeline defaults
- **Audio profiler** (`utils/audio_profile.py`) — spectral analysis that recommends the best engine/model for a given audio file
- **Mix engine** — multi-track mixer combining audio stems and MIDI-rendered tracks with per-track instrument, volume, solo, and FLAC render

GUI is **DearPyGUI** (`gui/app.py`). Run with `stemforge` console script or `python -m gui.app`.

---

## Current state

All pipelines and the full GUI are implemented and working:

- Demucs separation — 4 models (htdemucs, htdemucs_ft, mdx_extra, mdx_extra_q), CUDA fallback for MDX-Net
- BS-Roformer separation — 6 models including ViperX vocals (SDR 12.97), KJ vocals, ZFTurbo 4-stem, jarredou 6-stem
- Automatic engine/model recommendation from spectral audio analysis
- MIDI extraction — BasicPitch for instruments, faster-whisper + PYIN pitch for vocals
- MIDI preview — per-stem FluidSynth playback with default GM instruments, in-memory until saved
- Mix tab — per-track instrument/volume controls, solo preview, click-to-seek master timeline, FLAC render
- Stable Audio Open generation — text + audio + MIDI conditioning, up to 600 s (chunked at 47 s), Vocal Preservation Mode
- Export panel — all pipeline outputs including mix, 4 audio formats (wav/flac/mp3/ogg), auto-refresh on pipeline completion
- Waveform and MIDI visualizers with second-labeled ruler ticks and click-to-seek on all plots
- WSL audio support — auto-detects WSL via `/proc/version`, routes through WSLg PulseAudio socket
- Deterministic uv environment, Python 3.11, CUDA 13.0 wheels
- macOS support via MPS acceleration (separate `pyproject.toml.MAC`)

---

## Project structure

```
StemForge/
├── config.py                       # StemForgeConfig — aggregate config, env/file loading
├── pyproject.toml
├── pyproject.toml.MAC              # macOS variant (MPS, no CUDA index)
├── .gitignore
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
│       ├── midi_player_widget.py   # MIDI preview widget (FluidSynth)
│       ├── demucs_panel.py         # Separate tab (Demucs + BS-Roformer)
│       ├── midi_panel.py           # MIDI tab (BasicPitch + vocal MIDI)
│       ├── mix_panel.py            # Mix tab (multi-track mixer)
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
├── utils/
│   ├── audio_io.py                 # read_audio / write_audio
│   ├── audio_profile.py            # Spectral analysis + engine recommendation
│   ├── midi_io.py                  # MIDI read / write / helpers
│   ├── wsl.py                      # WSL detection + PulseAudio routing
│   ├── device.py                   # get_device / is_mps — platform-aware torch device
│   ├── platform.py                 # get_data_dir — OS-idiomatic data paths
│   ├── logging_utils.py            # configure_logging
│   └── errors.py                   # Custom exception hierarchy
│
└── vendor/
    ├── flashy/                     # Minimal flashy stubs for Audiocraft/Demucs
    └── flashy_src/                 # Original flashy source (MIT, Meta)
```

---

## Import layer order (no circular imports)

```
utils/  →  models/  →  pipelines/  →  gui/components/  →  gui/app.py
```

`config.py` is imported by any layer that needs settings. It only imports from `utils.errors`.
`gui/state.py` imports nothing from the project — safe for any layer.
`gui/constants.py` imports from `utils.platform` (no circular dependency risk: utils is the base layer).

---

## DearPyGUI UI (`gui/app.py`)

Viewport: screen-aware via `_get_viewport_size()` (~90% of primary monitor, fallback 1280 × 820), min 900 × 600. Five tabs plus a persistent top bar.

| Tab label | Panel class | File |
|---|---|---|
| Separate | `DemucsPanel` | `demucs_panel.py` |
| MIDI | `MidiPanel` | `midi_panel.py` |
| Mix | `MixPanel` | `mix_panel.py` |
| Generate | `MusicGenPanel` | `musicgen_panel.py` |
| Export | `ExportPanel` | `export_panel.py` |

Top bar: `LoaderPanel` (file browse + path display + clear) + "■ Stop audio" button.

Font: DejaVuSans 20 px, `dpg.set_global_font_scale(1.3)` applied at startup. A proper per-user UI scale control is planned for a future release — a simple float slider is insufficient because DPG scales text but not button/widget sizes, requiring a full layout reflow to implement correctly.

File dialogs are registered at top level (outside all windows) before the render loop.

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

## Key types and aliases

| Alias | Definition | Defined in |
|---|---|---|
| `Waveform` | `Any` | `utils/audio_io.py`, `pipelines/resample.py` |
| `NoteEvent` | `tuple[float, float, int, int]` = `(start_sec, end_sec, pitch_midi, velocity)` | `utils/midi_io.py`, `pipelines/basicpitch_pipeline.py` |
| `MidiData` | `Any` | `utils/midi_io.py` |

---

## Output directories (`gui/constants.py`)

All under `~/.local/share/stemforge/output/`:

| Constant | Path |
|---|---|
| `_STEMS_DIR` | `.../stems/` |
| `_MIDI_DIR` | `.../midi/` |
| `_MUSICGEN_DIR` | `.../musicgen/` |
| `_EXPORT_DIR` | `.../exports/` |

---

## Audio profiler (`utils/audio_profile.py`)

Analyzes spectral flatness, transient sharpness/density, harmonic density, vocal naturalness, drum-intrusion risk, and stereo correlation. Returns a `Recommendation(engine, model_id, reason, confidence)` directing the user to the best separation engine and model for their audio.

Veto logic ensures synthetic/electronic content routes to Demucs; natural/organic content with low drum risk routes to Roformer models.

---

## Stable Audio Open generation (`pipelines/musicgen_pipeline.py`)

- Text prompt always required
- Optional audio conditioning via `init_audio_path` (resampled to 44.1 kHz, VAE-encoded)
- Optional MIDI conditioning via `midi_path` (BPM, key, GM instrument families appended to prompt)
- Durations > 47 s are chunked and concatenated
- **Vocal Preservation Mode**: conditioning strength scaling, timing-lock windowed generation (50 ms crossfade), negative prompt support

---

## Platform notes

- **Linux (primary)**: CUDA 13.0 wheels, uv sync, Python 3.11
- **macOS (Apple Silicon)**: MPS acceleration via `pyproject.toml.MAC`; use `from utils.device import get_device`, never hardcode `"cuda"`
- **WSL**: Auto-detected via `/proc/version`; audio routed through PulseAudio; requires libportaudio2, pulseaudio-utils, libasound2-plugins, libfluidsynth3, fluid-soundfont-gm
- **FluidSynth**: Required for MIDI preview and Mix tab; GM soundfont auto-discovered at startup

---

## Caches and logs

- Model weights: `~/.cache/stemforge/` (subdirs per model type)
- Logs: `~/.local/share/stemforge/logs/stemforge.log`
