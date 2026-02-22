# StemForge — Claude Context

## Workflow
- Break all multi-step tasks into a numbered plan before starting
- After each major step, pause and report: what was done, what changed, what's next
- Wait for explicit "continue" or "proceed" confirmation before moving to the next step

## What this project is


AI-powered audio processing desktop application with three pipelines:
- **Demucs** — source separation (vocals, drums, bass, other)
- **BasicPitch** — MIDI extraction from separated stems
- **MusicGen** — text-prompted audio generation with optional melody conditioning

GUI is **DearPyGUI** (`gui/app.py`). Run with `stemforge` console script or `python -m gui.app`.

---

## Current state

- **Demucs pipeline** — fully implemented and working in the GUI.
- **BasicPitch pipeline** — fully implemented. Two GUI UX issues remain (see below).
- **MusicGen pipeline** — stub only; implementation is the next phase.
- **Export panel** — stub only.

### Pending GUI fixes (next session)

1. **Stem handoff UX** — After Demucs runs, the MIDI tab gives no visual indication of which stems are available. The mechanism works (via `app_state.stem_paths`) but the user can't tell without just trying. Fix plan:
   - Implement `DemucsPanel.add_result_listener` to store and call callbacks after a successful run.
   - Add `BasicPitchPanel.notify_stems_ready(stem_paths)` to update the combo items to only the available stems and show a "ready" status line.
   - Wire them in `app.py`: `_demucs.add_result_listener(_basicpitch.notify_stems_ready)`.

2. **Font size too small** — DearPyGUI renders at its default tiny size. Fix: add a `_setup_fonts()` function in `app.py` that loads a system TTF (e.g. `/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf`) at ~16px via `dpg.add_font_registry()` and `dpg.bind_font()`. Need to verify font paths on Fedora 43 first (`fc-list`).

---

## Project structure

```
StemForge/
├── config.py                          # StemForgeConfig — aggregate config, env/file loading
├── pyproject.toml
├── .gitignore
├── utils/
│   ├── errors.py                      # Custom exception hierarchy
│   ├── audio_io.py                    # read_audio / write_audio / helpers  [implemented]
│   ├── midi_io.py                     # read_midi / write_midi / notes_to_midi [implemented]
│   └── logging.py                     # configure_logging / get_logger  [implemented]
├── models/
│   ├── demucs_loader.py               # DemucsModelLoader  [implemented]
│   ├── basicpitch_loader.py           # BasicPitchModelLoader  [implemented]
│   └── musicgen_loader.py             # MusicGenModelLoader  [stub]
├── pipelines/
│   ├── resample.py                    # ResamplePipeline + Resampler  [implemented]
│   ├── demucs_pipeline.py             # DemucsPipeline, DemucsConfig, DemucsResult  [implemented]
│   ├── basicpitch_pipeline.py         # BasicPitchPipeline, BasicPitchConfig, BasicPitchResult  [implemented]
│   └── musicgen_pipeline.py           # MusicGenPipeline, MusicGenConfig, MusicGenResult  [stub]
└── gui/
    ├── app.py                         # DearPyGUI viewport + tab bar + main()
    ├── state.py                       # AppState singleton (app_state) — thread-safe shared state
    ├── constants.py                   # Output directory paths (_STEMS_DIR, _MIDI_DIR, etc.)
    └── components/
        ├── loader.py                  # LoaderPanel — file browse bar at top of window
        ├── demucs_panel.py            # DemucsPanel — "Separate" tab
        ├── basicpitch_panel.py        # BasicPitchPanel — "MIDI" tab
        ├── musicgen_panel.py          # MusicGenPanel — "Generate" tab  [stub]
        └── export_panel.py            # ExportPanel — "Export" tab  [stub]
```

---

## Import layer order (no circular imports)

```
utils/  →  models/  →  pipelines/  →  gui/components/  →  gui/app.py
```

`config.py` is imported by any layer that needs settings. It only imports from `utils.errors`.
`gui/state.py` and `gui/constants.py` import nothing from the project — safe for any layer.

---

## Inter-panel state (`gui/state.py`)

All panels share a single `AppState` singleton (`app_state`). Properties are lock-protected.

| Property | Written by | Read by |
|---|---|---|
| `audio_path` | `LoaderPanel` | `DemucsPanel` |
| `stem_paths` | `DemucsPanel` | `BasicPitchPanel` |
| `midi_path` | `BasicPitchPanel` | `ExportPanel` |
| `musicgen_path` | `MusicGenPanel` | `ExportPanel` |

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

## DearPyGUI UI (`gui/app.py`)

Viewport: 1280 × 820, min 900 × 600. Four tabs plus a persistent top bar.

| Tab label | Panel | Status |
|---|---|---|
| Separate | `DemucsPanel` | working |
| MIDI | `BasicPitchPanel` | working (UX issues pending) |
| Generate | `MusicGenPanel` | stub |
| Export | `ExportPanel` | stub |

Top bar: `LoaderPanel` (file browse + path display + clear) + "■ Stop audio" button.

File dialogs are registered at top level (outside all windows) before the render loop.

---

## Key types and aliases

| Alias | Definition | Defined in |
|---|---|---|
| `Waveform` | `Any` | `utils/audio_io.py`, `pipelines/resample.py` |
| `NoteEvent` | `tuple[float, float, int, int]` = `(start_sec, end_sec, pitch_midi, velocity)` | `utils/midi_io.py`, `pipelines/basicpitch_pipeline.py` |
| `MidiData` | `Any` | `utils/midi_io.py` |

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

## Pipeline interface (all three follow this contract)

```python
pipeline.configure(config)   # supply Config dataclass
pipeline.load_model()        # load weights — raises ModelLoadError
result = pipeline.run(input) # run inference — raises PipelineExecutionError / InvalidInputError
pipeline.clear()             # release weights from memory
```

| Pipeline | `run()` input type | `run()` return type | Internal sample rate |
|---|---|---|---|
| `DemucsPipeline` | `pathlib.Path` (audio file) | `DemucsResult` | 44 100 Hz |
| `BasicPitchPipeline` | `pathlib.Path` (audio stem) | `BasicPitchResult` | 22 050 Hz |
| `MusicGenPipeline` | `str` (text prompt) | `MusicGenResult` | 32 000 Hz |
| `ResamplePipeline` | `pathlib.Path` (audio file) | `ResampleResult` | configurable |

---

## Config system (`config.py`)

- `StemForgeConfig` — single aggregate config object, all fields optional in `__init__`
- Resolution order: explicit kwarg → `STEMFORGE_*` env var → module-level default constant
- `from_env()` — classmethod, env vars only
- `from_file(path)` — classmethod, loads `stemforge.toml` (needs `tomllib`/`tomli`)
- `validate()` — raises `InvalidInputError` on bad values

Default sample rates:
- `DEFAULT_DEMUCS_SAMPLE_RATE = 44_100`
- `DEFAULT_BASICPITCH_SAMPLE_RATE = 22_050`
- `DEFAULT_MUSICGEN_SAMPLE_RATE = 32_000`

---

## Constants defined in component modules

| Constant | Value | Module |
|---|---|---|
| `SUPPORTED_EXTENSIONS` | `('wav','flac','mp3','ogg','aiff','aif')` | `gui/components/loader.py` |
| `DEMUCS_MODELS` | `('htdemucs','htdemucs_ft','mdx_extra','mdx_extra_q')` | `gui/components/demucs_panel.py` |
| `STEM_TARGETS` | `('vocals','drums','bass','other')` | `gui/components/demucs_panel.py` |
| `MUSICGEN_MODELS` | `('facebook/musicgen-small', …-medium, …-large, …-melody)` | `gui/components/musicgen_panel.py` |
| `EXPORT_FORMATS` | `('wav','flac','ogg')` | `gui/components/export_panel.py` |

---

## Runtime dependencies (pyproject.toml)

`demucs>=4.0.0`, `basic-pitch>=0.3.0`, `audiocraft>=1.3.0`, `torch>=2.1.0`,
`torchaudio>=2.1.0`, `numpy>=1.24.0`, `scipy>=1.11.0`, `librosa>=0.10.0`,
`soundfile>=0.12.0`, `mido>=1.3.2`, `dearpygui>=1.11.0`, `sounddevice>=0.4.0`,
`tomli>=2.0.0; python_version < '3.11'`

Optional: `[cuda]` for GPU wheels, `[dev]` for pytest/ruff/mypy/pre-commit.

---

## Git / GitHub

- Remote: `git@github.com:tsondo/StemForge.git`
- Branch: `main`
- Repo is clean as of last session.
