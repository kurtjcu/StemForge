# StemForge

StemForge is a desktop application that chains three AI audio pipelines into
a single, cohesive workflow:

1. **Demucs** – source separation (vocals, drums, bass, other)
2. **BasicPitch** – polyphonic MIDI extraction from separated stems
3. **MusicGen** – text-conditioned (and melody-conditioned) audio generation

---

## Project structure

```
StemForge/
├── gui/                        # Graphical user interface
│   ├── app.py                  # Application entry point and lifecycle
│   └── components/             # Individual UI panels
│       ├── loader.py           # Audio file selection panel
│       ├── demucs_panel.py     # Source-separation controls
│       ├── basicpitch_panel.py # MIDI extraction controls
│       ├── musicgen_panel.py   # Audio generation controls
│       └── export_panel.py     # Output aggregation and export
│
├── pipelines/                  # AI pipeline orchestration
│   ├── demucs_pipeline.py      # Demucs separation job lifecycle
│   ├── basicpitch_pipeline.py  # BasicPitch transcription job lifecycle
│   ├── musicgen_pipeline.py    # MusicGen generation job lifecycle
│   └── resample.py             # Sample-rate conversion utilities
│
├── models/                     # Model weight loading and caching
│   ├── demucs_loader.py        # Demucs checkpoint loader
│   ├── basicpitch_loader.py    # BasicPitch weight loader (SavedModel / ONNX)
│   └── musicgen_loader.py      # MusicGen transformer + EnCodec loader
│
├── utils/                      # Shared utilities
│   ├── audio_io.py             # Audio file reading and writing
│   ├── midi_io.py              # Standard MIDI File reading and writing
│   └── logging.py              # Rotating file + console logger setup
│
└── README.md
```

---

## Pipelines at a glance

### Demucs (source separation)

| Config key     | Type        | Default    | Description                        |
|----------------|-------------|------------|------------------------------------|
| `model_name`   | `str`       | `htdemucs` | Model variant identifier           |
| `stems`        | `list[str]` | all four   | Subset of stems to extract         |
| `output_dir`   | `Path`      | required   | Directory for separated audio      |
| `sample_rate`  | `int`       | `44100`    | Output sample rate (Hz)            |

### BasicPitch (MIDI extraction)

| Config key              | Type    | Default | Description                             |
|-------------------------|---------|---------|-----------------------------------------|
| `onset_threshold`       | `float` | `0.5`   | Minimum onset confidence (0 – 1)        |
| `frame_threshold`       | `float` | `0.3`   | Minimum frame confidence (0 – 1)        |
| `minimum_note_length`   | `float` | `58.0`  | Shortest note allowed (ms)              |
| `minimum_frequency`     | `float` | `None`  | Lowest pitch to transcribe (Hz)         |
| `maximum_frequency`     | `float` | `None`  | Highest pitch to transcribe (Hz)        |

### MusicGen (audio generation)

| Config key          | Type    | Default                      | Description                        |
|---------------------|---------|------------------------------|------------------------------------|
| `model_name`        | `str`   | `facebook/musicgen-small`    | HuggingFace model ID               |
| `prompt`            | `str`   | required                     | Text description of desired music  |
| `duration_seconds`  | `float` | `10.0`                       | Length of generated audio          |
| `melody_path`       | `Path`  | `None`                       | Optional melody conditioning file  |
| `top_k`             | `int`   | `250`                        | Top-k sampling parameter           |
| `temperature`       | `float` | `1.0`                        | Sampling temperature               |

---

## Model cache

Downloaded model weights are stored under `~/.cache/stemforge/`:

```
~/.cache/stemforge/
├── demucs/
├── basicpitch/
└── musicgen/
```

Logs are written to `~/.local/share/stemforge/logs/stemforge.log` with
automatic rotation (10 MiB per file, five backups).

---

## Status

> **This repository contains scaffolding stubs only.**
> No real inference logic has been implemented yet.
> All classes and functions contain `pass` statements as placeholders.
