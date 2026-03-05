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
- **Voice model training** — see dedicated section below.
- **Batch processing** — convert multiple stems through the same
  voice model in one operation.
- **Ethical safeguards** — watermarking, consent notices, or usage
  warnings when converting to a cloned voice.

---

## RVC voice model training

Train custom voice models from audio samples directly within StemForge.
The vendored Applio code already includes the model architectures
(Synthesizer, MultiPeriodDiscriminator) needed for training — only the
training loop, preprocessing, and feature extraction code needs to be
added. Evaluated as ~15–22 hours of implementation effort.

### Dependency impact

Three new packages, no conflicts with the existing venv:

- `tensorboard` — training loss and spectrogram logging
- `matplotlib` — spectrogram rendering for TensorBoard
- `scikit-learn` — MiniBatchKMeans for FAISS index size reduction

All other training deps (torch.distributed, noisereduce, torchcrepe,
faiss-cpu, transformers, librosa, scipy, soxr) are already present.

### Training workflow (4 steps)

1. **Preprocess** — load user's audio files (~2–10 min of voice),
   high-pass filter + optional denoise, slice into segments on silence
   boundaries, write full-SR WAVs + 16 kHz WAVs.
2. **Extract** — run F0 pitch extraction (RMVPE/CREPE/FCPE) on each
   segment, run HuBERT/ContentVec speaker embedding extraction,
   generate training config JSON + filelist.
3. **Train** — GAN training loop: Synthesizer (generator) vs
   MultiPeriodDiscriminator with mel-spectrogram, KL divergence,
   adversarial, and feature-matching losses. Saves periodic checkpoints
   + inference-ready `.pth`. Optional overtraining detection via
   smoothed EMA loss. Supports bf16 on Ampere+ GPUs.
4. **Index** — concatenate all extracted embeddings, optionally
   KMeans-reduce (for large datasets >200 k frames), build FAISS
   IVFFlat index, save as `.index` file.

**Output**: `{name}.pth` + `{name}.index` — the same format our
inference pipeline already consumes, ready to use in Voice mode.

### Code to vendor

~3,500 lines across 13 Python files + 4 JSON configs from Applio's
`rvc/train/` subtree. Key files:

| File | Purpose |
|------|---------|
| `train/train.py` | GAN training loop (~1,160 lines) |
| `train/data_utils.py` | Dataset, collate, bucket sampler |
| `train/losses.py` | Adversarial, feature-matching, KL losses |
| `train/mel_processing.py` | Spectrogram / mel computation |
| `train/preprocess/preprocess.py` | Audio slicing + normalization |
| `train/extract/extract.py` | F0 + embedding extraction |
| `train/process/extract_model.py` | Strip checkpoint to inference `.pth` |
| `train/process/extract_index.py` | Build FAISS `.index` |

All training code imports from `lib/algorithm/` (Synthesizer,
Discriminator) which is already vendored. Main refactoring work is
replacing Applio's `os.getcwd()` + sys.path hacks with StemForge
import conventions.

### GPU / VRAM requirements

| Config | VRAM | Notes |
|--------|------|-------|
| Minimum viable | 4 GB | batch_size=2, very slow |
| Practical | 6–8 GB | batch_size=4–8 |
| RTX 5080 (16 GB) | 16 GB | batch_size=8–16 + bf16 + GPU caching |

Training locks the GPU for minutes-to-hours. Should run as a managed
subprocess (like AceStep) with the pipeline_manager GPU lock ensuring
mutual exclusion with inference pipelines.

### Integration sketch

- **Backend**: ~6 new API endpoints (preprocess, extract, train, stop,
  status, build-index) under `/api/voice/train/*`
- **Frontend**: training panel in Voice mode — dataset upload/selection,
  model name, sample rate, epoch count, batch size, progress bars with
  loss curves
- **Pretrained models**: auto-download base G/D weights from HuggingFace
  (~200–400 MB per sample rate variant)

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
