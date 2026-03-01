# Acknowledgments

StemForge is built on the shoulders of many outstanding open-source projects.
We are grateful to every team listed below for making their work freely available.

---

## Demucs — Meta (Facebook AI Research)

Hybrid Transformer source separation powering the Separate tab (htdemucs, htdemucs_ft, mdx_extra, mdx_extra_q).

- **Repository:** https://github.com/facebookresearch/demucs
- **Paper:** Rouard, Massa & Défossez — *Hybrid Transformers for Music Source Separation* (ICASSP 2023)
- **License:** MIT

---

## BS-Roformer / MelBand-Roformer — Community

High-quality separation models used alongside Demucs in the Separate tab.

### `bs-roformer` Python package — Lucidrains

- **Repository:** https://github.com/lucidrains/BS-RoFormer

### ViperX vocal model (SDR 12.97) — TRvlvr / UVR community

- **Model repository:** https://github.com/TRvlvr/model_repo

### KimberleyJensen MelBand-Roformer vocal model

- **Model repository:** https://huggingface.co/KimberleyJSN/melbandroformer

### ZFTurbo 4-stem BS-Roformer & Music-Source-Separation-Training — Roman Solovyev (ZFTurbo)

- **Repository:** https://github.com/ZFTurbo/Music-Source-Separation-Training
- **Paper:** Solovyev et al. — *Benchmarks and leaderboards for sound demixing tasks* (2023)

### jarredou 6-stem BS-Roformer (guitar + piano)

- **Model repository:** https://huggingface.co/jarredou/BS-ROFO-SW-Fixed

---

## Basic Pitch — Spotify

Polyphonic audio-to-MIDI transcription for instrument stems in the MIDI tab.

- **Repository:** https://github.com/spotify/basic-pitch
- **Paper:** Bittner et al. — *A Lightweight Instrument-Agnostic Model for Polyphonic Note Transcription and Multipitch Estimation* (ICASSP 2022)
- **License:** Apache 2.0

---

## Whisper — OpenAI

Speech recognition model used (via faster-whisper) for vocal pitch-to-MIDI extraction.

- **Repository:** https://github.com/openai/whisper
- **Paper:** Radford et al. — *Robust Speech Recognition via Large-Scale Weak Supervision* (2022)
- **License:** MIT

---

## faster-whisper — SYSTRAN

CTranslate2-accelerated Whisper inference powering the Vocal MIDI pipeline.

- **Repository:** https://github.com/SYSTRAN/faster-whisper
- **License:** MIT

---

## Stable Audio Open — Stability AI

Text-conditioned audio generation model powering the Synth tab.

- **Repository:** https://huggingface.co/stabilityai/stable-audio-open-1.0
- **Paper:** Evans et al. — *Stable Audio Open* (2024)
- **License:** Stability AI Community License

---

## ACE-Step — ACE Studio / Timedomain

Full song generation from lyrics and style descriptions, powering the Compose tab.

- **Repository:** https://github.com/AceStudioAI/ACE-Step
- **Paper:** *ACE-Step: A Step Towards Music Generation Foundation Model* (2025)
- **License:** Apache 2.0

---

## PyTorch — Meta (Facebook AI Research)

Deep learning framework underlying all inference pipelines.

- **Repository:** https://github.com/pytorch/pytorch
- **License:** BSD-3-Clause

---

## Hugging Face Diffusers

Diffusion pipeline framework used to load and run Stable Audio Open.

- **Repository:** https://github.com/huggingface/diffusers
- **License:** Apache 2.0

---

## Hugging Face Transformers

Tokenizer and model infrastructure used by the generation pipelines.

- **Repository:** https://github.com/huggingface/transformers
- **License:** Apache 2.0

---

## librosa

Audio analysis and feature extraction used in the audio profiler and resampling utilities.

- **Repository:** https://github.com/librosa/librosa
- **Paper:** McFee et al. — *librosa: Audio and Music Signal Analysis in Python* (SciPy 2015)
- **License:** ISC

---

## FluidSynth

Software synthesizer used for MIDI preview rendering and Mix tab audio.

- **Repository:** https://github.com/FluidSynth/fluidsynth
- **License:** LGPL-2.1

---

## wavesurfer.js — katspaugh

Waveform visualization in the browser, used for all audio players and the global transport bar.

- **Repository:** https://github.com/katspaugh/wavesurfer.js
- **License:** BSD-3-Clause

---

## FastAPI — Sebastián Ramírez (tiangolo)

Web framework powering the StemForge backend API.

- **Repository:** https://github.com/fastapi/fastapi
- **License:** MIT

---

## Uvicorn — Encode

ASGI server running the FastAPI application.

- **Repository:** https://github.com/encode/uvicorn
- **License:** BSD-3-Clause

---

## uv — Astral

Blazing-fast Python package manager and resolver used for deterministic environments.

- **Repository:** https://github.com/astral-sh/uv
- **License:** MIT / Apache 2.0

---

## Additional dependencies

StemForge also relies on many other excellent open-source libraries including
NumPy, SciPy, soundfile, mido, pretty_midi, einops, safetensors, accelerate,
pydub, soxr, and ai-edge-litert (TFLite runtime). Thank you to all their
maintainers and contributors.

---

If you believe your project should be listed here and is not, please
[open an issue](https://github.com/tsondo/StemForge/issues) and we will add it.
