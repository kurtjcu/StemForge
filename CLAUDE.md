# StemForge — Claude Context

## What this project is

AI-powered audio processing desktop application with three pipelines:
- **Demucs** — source separation (vocals, drums, bass, other)
- **BasicPitch** — MIDI extraction from separated stems
- **MusicGen** — text-prompted audio generation with optional melody conditioning

GUI is Gradio (`gui/app.py`). Run with `stemforge` console script or `python -m gui.app`.

---

## Current state

**All files are stubs.** Every method body is `pass`. No business logic has been implemented yet. The scaffold includes: correct imports, type hints, class-level attribute annotations, and full docstrings (including `Raises` sections referencing custom exceptions). Logic implementation is the next phase.

---

## Project structure

```
StemForge/
├── config.py                          # Centralised config (StemForgeConfig class)
├── pyproject.toml
├── .gitignore
├── utils/
│   ├── errors.py                      # Custom exception hierarchy
│   ├── audio_io.py                    # read_audio / write_audio / helpers
│   ├── midi_io.py                     # read_midi / write_midi / notes_to_midi / helpers
│   └── logging.py                     # configure_logging / get_logger
├── models/
│   ├── demucs_loader.py               # DemucsModelLoader
│   ├── basicpitch_loader.py           # BasicPitchModelLoader
│   └── musicgen_loader.py             # MusicGenModelLoader
├── pipelines/
│   ├── resample.py                    # ResamplePipeline + Resampler + free functions
│   ├── demucs_pipeline.py             # DemucsPipeline, DemucsConfig, DemucsResult
│   ├── basicpitch_pipeline.py         # BasicPitchPipeline, BasicPitchConfig, BasicPitchResult
│   └── musicgen_pipeline.py           # MusicGenPipeline, MusicGenConfig, MusicGenResult
└── gui/
    ├── app.py                         # build_ui() + main() — Gradio Blocks entrypoint
    └── components/
        ├── loader.py                  # LoaderPanel
        ├── demucs_panel.py            # DemucsPanel
        ├── basicpitch_panel.py        # BasicPitchPanel
        ├── musicgen_panel.py          # MusicGenPanel
        └── export_panel.py            # ExportPanel
```

---

## Import layer order (no circular imports)

```
utils/  →  models/  →  pipelines/  →  gui/components/  →  gui/app.py
```

`config.py` is imported by any layer that needs settings. It only imports from `utils.errors`.

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
├── ModelLoadError(model_name=)         — weight loading / download failures
├── AudioProcessingError(path=)         — read / write / resample failures
├── PipelineExecutionError(pipeline_name=) — runtime inference failures
└── InvalidInputError(field=)           — pre-processing validation failures
```

All pipeline `run()` and loader `load()` docstrings already reference these.

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
- `_env_path / _env_int / _env_str / _env_log_level` — private env-var helper stubs

Default sample rates in `config.py`:
- `DEFAULT_DEMUCS_SAMPLE_RATE = 44_100`
- `DEFAULT_BASICPITCH_SAMPLE_RATE = 22_050`
- `DEFAULT_MUSICGEN_SAMPLE_RATE = 32_000`

---

## Model loaders (`models/`)

Each has: `load(model_name)` → `Any`, `is_cached()`, `download()`, `evict()`, `_verify_checksum()`.
- `DemucsModelLoader` — caches at `~/.cache/stemforge/demucs/`
- `BasicPitchModelLoader` — supports `'onnx'` and `'savedmodel'` formats
- `MusicGenModelLoader` — loads both transformer (`lm`) and EnCodec (`codec`) weights

---

## Gradio UI (`gui/app.py`)

Five tabs wired to panel singletons:

| Tab | Callback | Panel singleton |
|---|---|---|
| Load Audio | `on_audio_upload`, `on_clear_audio` | `_loader` |
| Demucs | `on_run_demucs` | `_demucs` |
| BasicPitch | `on_run_basicpitch` | `_basicpitch` |
| MusicGen | `on_run_musicgen` | `_musicgen` |
| Export | `on_run_export` | `_export` |

All callbacks currently return `"[stub] ..."` placeholder strings.

---

## Constants defined in component modules

| Constant | Value | Module |
|---|---|---|
| `SUPPORTED_EXTENSIONS` | `('wav','flac','mp3','ogg','aiff','aif')` | `gui/components/loader.py` |
| `DEMUCS_MODELS` | `('htdemucs','htdemucs_ft','mdx_extra','mdx_extra_q')` | `gui/components/demucs_panel.py` |
| `STEM_TARGETS` | `('vocals','drums','bass','other')` | `gui/components/demucs_panel.py` |
| `MUSICGEN_MODELS` | `('facebook/musicgen-small', …-medium, …-large, …-melody)` | `gui/components/musicgen_panel.py` |
| `EXPORT_FORMATS` | `('wav','flac','mp3','ogg')` | `gui/components/export_panel.py` |

---

## Runtime dependencies (pyproject.toml)

`demucs>=4.0.0`, `basic-pitch>=0.3.0`, `audiocraft>=1.3.0`, `torch>=2.1.0`,
`torchaudio>=2.1.0`, `numpy>=1.24.0`, `scipy>=1.11.0`, `librosa>=0.10.0`,
`soundfile>=0.12.0`, `mido>=1.3.2`, `gradio>=4.0.0`,
`tomli>=2.0.0; python_version < '3.11'`

Optional: `[cuda]` for GPU wheels, `[dev]` for pytest/ruff/mypy/pre-commit.

---

## Git / GitHub

- Remote: `git@github.com:tsondo/StemForge.git`
- Branch: `main`
- Last commit: `432a3be` — pyproject.toml updated for config.py
- All files committed and pushed; repo is clean.
