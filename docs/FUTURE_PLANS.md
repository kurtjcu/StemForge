# Future Plans

Planned features and research directions for StemForge. Items are roughly
ordered by priority within each section. Nothing here is committed — this
is a living document for tracking ideas.

---

## Voice transformation (Generate tab)

The Generate tab currently uses Stable Audio Open for text-conditioned audio
generation. A major gap is the ability to take an isolated vocal stem and
transform it — change the voice, adjust emotional delivery, or apply a
style/LoRA to it — while preserving lyrics, timing, and musical phrasing.

### Goal

Given a separated vocal stem, produce a new vocal stem where the voice
identity, timbre, or emotional character has been changed, with lyrics and
timing intact. The output should be mixable back into the original song
via the Mix tab.

### Candidate approaches

**1. RVC (Retrieval-based Voice Conversion)**
- Best fit for StemForge's "take a stem, transform it" workflow.
- Speech-to-speech: takes audio in, produces audio out. Preserves
  intonation, timing, and pitch contour of the source.
- Supports custom voice models (.pth) trained from short samples.
- Real-time capable, low latency, battle-tested in music production.
- Active community: Applio (MIT, actively maintained) is the leading fork.
- Integration path: run inference on a separated vocal stem, write the
  result as a new stem, make it available in the Mix tab.
- Limitation: voice model must be trained or sourced separately. No
  text-based emotion control.

**2. Chatterbox / Chatterbox Turbo (Resemble AI)**
- MIT-licensed, Python 3.11, zero-shot voice cloning from ~5 s of audio.
- Unique emotion exaggeration control (monotone → dramatic) via a single
  parameter — directly addresses the "change emotional character" goal.
- Includes voice conversion scripts (not just TTS).
- Built-in PerTh watermarking for responsible use.
- 23-language multilingual support.
- Limitation: primarily a TTS engine. Voice conversion mode exists but
  is secondary to text-to-speech. May not preserve singing phrasing as
  well as a dedicated SVC model. Needs evaluation on sung vocals
  specifically.

**3. GPT-SoVITS**
- Few-shot voice cloning + TTS with ~1 minute of reference audio.
- Two-stage architecture: GPT for semantic/prosody, SoVITS for acoustic
  synthesis. Produces emotionally rich output.
- Cross-lingual synthesis (EN, ZH, JA, KO, and more).
- Limitation: heavier dependency footprint, complex training pipeline,
  primarily TTS-oriented. Singing voice conversion is possible but
  requires more setup than RVC.

**4. so-vits-svc / so-vits-svc-fork**
- Singing voice conversion specifically. Historically the go-to for
  music-focused voice transformation.
- Limitation: original project archived. Forks exist but are less
  actively maintained than RVC/Applio. Higher latency than RVC.

### Recommended evaluation order

1. **RVC via Applio** — closest to StemForge's existing workflow. Test
   with a Demucs-separated vocal stem, evaluate output quality and
   whether timing/lyrics are preserved.
2. **Chatterbox voice conversion mode** — evaluate on sung vocals
   specifically. If it handles singing well, the emotion control is a
   unique differentiator no other option offers.
3. **GPT-SoVITS** — evaluate if the above two don't meet quality bar
   for emotional re-delivery or cross-lingual use cases.

### Integration design (sketch)

- New section or mode in the Generate tab: "Voice Transform"
- Input: a vocal stem (from Separate tab or manually loaded)
- Controls: voice model selector, optional emotion/style parameters
- Output: a new WAV stem at 44.1 kHz, written to the musicgen output
  directory, and automatically available in the Mix tab
- The existing Stable Audio Open generation and Voice Transform would
  be sibling modes within the Generate tab, not separate tabs

### Open questions

- Should voice models be managed through the model registry, or kept as
  a separate user-managed collection (like soundfonts)?
- What's the minimum viable UX for selecting/loading a voice model?
- How to handle the ethical dimension — watermarking, consent notices,
  or usage warnings when converting to a cloned voice?
- Can LoRA-style fine-tuning be supported for any of these models to
  allow lightweight voice customization without full retraining?

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
