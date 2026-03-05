# Future Plans

Planned features and research directions for StemForge. Items are roughly
ordered by priority within each section. Nothing here is committed — this
is a living document for tracking ideas.

---

## Voice transformation — IMPLEMENTED

Voice conversion is now live in the **Compose tab** as a 5th mode
(**Create | Rework | Lego | Complete | Voice**), powered by RVC
(Retrieval-based Voice Conversion) via vendored Applio inference code.

### What shipped

- **RVC pipeline** (`pipelines/rvc_pipeline.py`) wrapping Applio's
  VoiceConverter — audio-in → audio-out, preserves lyrics, timing,
  and pitch contour of the source.
- **Vendored Applio** (`vendor/rvc/`) — inference-only subtree (no
  Gradio, no training code). MIT-licensed.
- **14 built-in voice models** auto-downloaded from HuggingFace on
  first use: Freddie Mercury, Adele, Frank Sinatra, Kurt Cobain,
  Ariana Grande, Taylor Swift, The Weeknd, Drake, Hatsune Miku,
  Donald Trump, SpongeBob, Peter Griffin, plus generic Male and Female.
- **Voice model browser** — search HuggingFace for RVC models by name,
  download with one click. Also supports uploading local .pth/.index files.
- **Controls**: pitch shift (-24 to +24 semitones), F0 method
  (RMVPE/CREPE/FCPE), voice character (index rate), consonant protection.
- **Source audio**: select from separated stems or load any audio file.
- **Cross-tab integration**: results auto-appear in Mix and Export tabs
  via the `transformReady` event. Transport bar picks up playback.
- **Backend**: `POST /api/voice/convert`, `GET /api/voice/models`,
  `POST /api/voice/models/import`, `POST /api/voice/models/upload`,
  `DELETE /api/voice/models/{name}`, `GET /api/voice/models/search`.
- Voice models cached at `~/.cache/stemforge/voice_models/`.

### Future improvements

- **Chatterbox / emotion control** — evaluate Resemble AI's voice
  conversion mode for emotion exaggeration (monotone → dramatic).
  Would complement RVC's identity-focused conversion.
- **GPT-SoVITS** — evaluate for cross-lingual singing voice conversion
  and cases where richer prosody control is needed.
- **Voice model training** — allow users to train custom RVC models
  from short audio samples directly within StemForge.
- **Batch processing** — convert multiple stems through the same
  voice model in one operation.
- **Ethical safeguards** — watermarking, consent notices, or usage
  warnings when converting to a cloned voice.

---

## Other ideas

_Add future feature ideas below this line._

### Native packaging
- RPM packages for Fedora/RHEL
- MSI installer for Windows
- .dmg for macOS
- Would make StemForge accessible to non-developers

### Batch processing
- Process multiple audio files through the same pipeline sequence
- Useful for albums or sample libraries
- Would benefit from a queue/job system in the GUI

### DAW integration
- Export stems + MIDI in a format that opens directly as a DAW project
  (e.g., Reaper project file, Ableton Live Set via ALS XML)
- Alternatively, a VST plugin wrapper for real-time stem separation

### Improved audio generation
- Evaluate newer open-source generation models as they emerge
  (successors to Stable Audio Open, music-focused diffusion models)
- Explore LoRA fine-tuning of the generation model on specific genres
  or instruments for higher-quality, more controllable output
