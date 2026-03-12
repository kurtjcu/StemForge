# Third-Party Notices

StemForge integrates third-party source code, AI model weights, and library
dependencies. **StemForge's own license (PolyForm Noncommercial 1.0.0) and any
StemForge commercial license do not grant rights to these components.** Users must
independently review and comply with each component's upstream license.

---

## Vendored Source Code

Source code included directly in the StemForge repository.

| Path | Project | License | Copyright |
|------|---------|---------|-----------|
| `vendor/rvc/` | [Applio](https://github.com/IAHispano/Applio) (RVC inference) | MIT | Copyright 2023 IAHispano |
| `vendor/python-audio-separator/` | [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) | MIT | Copyright 2023 karaokenerds / Andrew Beveridge |

### Git Submodules

| Path | Project | License | Copyright |
|------|---------|---------|-----------|
| `Ace-Step-Wrangler/` | [Ace-Step-Wrangler](https://github.com/tsondo/Ace-Step-Wrangler) | MIT | Copyright 2025 Todd Green |
| `Ace-Step-Wrangler/vendor/ACE-Step-1.5/` | [ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5) | MIT | Copyright 2024 ACE Studio & StepFun |

---

## AI Models Downloaded at Runtime

These model weights are not included in the repository. They are downloaded
automatically on first use and cached locally.

| Model | Creator | License | Commercial Use | Source |
|-------|---------|---------|---------------|--------|
| Demucs (htdemucs, htdemucs_ft, mdx_extra, mdx_extra_q) | Meta / Facebook Research | MIT | Yes | [github.com/facebookresearch/demucs](https://github.com/facebookresearch/demucs) |
| BS-Roformer ViperX vocals | ViperX / TRvlvr | MIT | Yes | [github.com/TRvlvr/model_repo](https://github.com/TRvlvr/model_repo) |
| BS-Roformer ZFTurbo 4-stem | ZFTurbo | MIT | Yes | [github.com/ZFTurbo/Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training) |
| BS-Roformer KimberleyJensen vocals | KimberleyJensen | GPL-3.0 | Copyleft | [huggingface.co/KimberleyJSN/melbandroformer](https://huggingface.co/KimberleyJSN/melbandroformer) |
| BS-Roformer jarredou 6-stem | jarredou | Unknown | Unknown | [huggingface.co/jarredou/BS-ROFO-SW-Fixed](https://huggingface.co/jarredou/BS-ROFO-SW-Fixed) |
| Stable Audio Open 1.0 | Stability AI | Stability AI Community License | < $1 M revenue: Yes; otherwise: requires Enterprise license | [huggingface.co/stabilityai/stable-audio-open-1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0) |
| ACE-Step 1.5 | ACE Studio & StepFun | MIT | Yes | [github.com/ace-step/ACE-Step-1.5](https://github.com/ace-step/ACE-Step-1.5) |
| Whisper (tiny, base, small, medium) | OpenAI | MIT | Yes | [github.com/openai/whisper](https://github.com/openai/whisper) |
| UVR separation models (Roformer, MDXC, VR) | UVR / Anjok07 | MIT | Yes | [github.com/Anjok07/ultimatevocalremovergui](https://github.com/Anjok07/ultimatevocalremovergui) |
| RVC voice models (built-in + HuggingFace) | Various | Various | Check per model | Various HuggingFace repos |
| RMVPE pitch model | RVC-Project | MIT | Yes | [github.com/RVC-Project/Retrieval-based-Voice-Conversion](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion) |

---

## Key Python Dependencies

Major libraries used by StemForge. This is not exhaustive; run `uv pip list` for the
full dependency tree.

| Package | License | Notes |
|---------|---------|-------|
| PyTorch (torch, torchaudio) | BSD-3-Clause | Meta / Facebook |
| audiocraft | MIT (code) / CC-BY-NC 4.0 (MusicGen weights) | StemForge uses only the library code (MIT), not MusicGen/AudioGen weights |
| basic-pitch | Apache 2.0 | Spotify |
| faster-whisper | MIT | SYSTRAN |
| torchcrepe | MIT | Max Morrison |
| parselmouth (Praat) | GPL-3.0 | Praat is GPL; parselmouth Python bindings are GPL-3.0 |
| FluidSynth (pyfluidsynth) | LGPL-2.1 | Dynamically linked |
| wavesurfer.js | BSD-3-Clause | Frontend audio visualization |
| FastAPI | MIT | Backend framework |
| uvicorn | BSD-3-Clause | ASGI server |
| pretty_midi | MIT | MIDI handling |
| librosa | ISC | Audio analysis |
| numpy | BSD-3-Clause | |
| scipy | BSD-3-Clause | |

---

## Notes

- **Stable Audio Open 1.0** requires HuggingFace authentication and acceptance of
  the Stability AI Community License before download. Commercial use is free for
  organizations with annual revenue under $1 M USD; higher revenue requires a
  separate Stability AI Enterprise license.

- **audiocraft** is imported as a library dependency (MIT-licensed code). StemForge
  does **not** load MusicGen or AudioGen model weights (which are CC-BY-NC 4.0).

- **parselmouth** wraps Praat under GPL-3.0. It is a pip-installed dependency, not
  vendored or statically linked.

- This document is provided for informational purposes and may not be exhaustive.
  License terms may change upstream. Users are responsible for verifying current
  license terms of all components they use.
