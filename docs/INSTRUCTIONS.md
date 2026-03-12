# StemForge — User Guide

**Tab bar:** Separate · Enhance · MIDI · Synth · Compose · Mix · Export

---

## 1. Getting Started

Drag and drop any audio file (WAV, FLAC, MP3, OGG, AIFF) or video file (MP4, MKV, WEBM, AVI, MOV) onto the upload zone, or click it to browse. Video files have their audio extracted automatically via FFmpeg.

The waveform appears in the transport bar at the bottom of the page. Use the play/pause button there to audition the file at any time. Once a file is loaded, the Separate tab becomes active.

---

## 2. Separate — Stem Separation

Split a track into individual stems using one of three AI engines:

- **Demucs** — 4 models (htdemucs, htdemucs_ft, mdx_extra, mdx_extra_q). Good general-purpose choice. `htdemucs_ft` is fine-tuned for higher vocal quality.
- **BS-Roformer** — 6 models including ViperX vocals (best vocal SDR), KJ vocals, ZFTurbo 4-stem, and jarredou 6-stem (adds guitar and piano stems).
- **ACE-Step** — AI-generative extraction powered by the AceStep base model. Extracts one stem at a time from a wider set of track types: vocals, backing vocals, drums, bass, guitar, keyboard, strings, brass, woodwinds, synth, percussion, and FX. Requires the AceStep backend to be running (it will start automatically if needed).

**Demucs / BS-Roformer workflow:**
1. Choose an engine and model.
2. Optionally click **Help me choose** — StemForge analyses your audio and recommends the best engine and model automatically.
3. Check the stems you want to extract (all selected by default).
4. Click **Separate**. A progress bar tracks the job. When done, each stem appears as a playable waveform and is passed automatically to the MIDI and Mix tabs.

**ACE-Step workflow:**
1. Select **ACE-Step** as the engine. The model selector is replaced by an info banner.
2. Use the radio buttons to select the single stem type to extract.
3. Click **Separate**. StemForge starts the AceStep backend if it isn't already running, uploads your audio, and runs generative extraction. The extracted stem appears as a playable waveform when complete.

### Batch Mode

Extract a single stem type from multiple files at once.

1. Toggle **Batch mode** at the top of the Separate tab.
2. Drop multiple audio files onto the upload zone (or click to browse and multi-select).
3. The stem selector switches to radio buttons — pick the **one** stem type to extract from all files.
4. Click **Separate All**. The model loads once and processes each file sequentially with per-file progress updates.
5. Results appear as individual waveform cards named `<stem>-stem-<filename>`. Each can be saved individually, or click **Save All** to download all results as a ZIP archive.

---

## 3. Enhance — Audio Enhancement

Three processing modes, selected via the mode bar at the top of the tab: **Clean Up** · **Tune** · **Effects**

**Source audio:** Select from separated stems, enhanced outputs, uploads, or composed songs. Sources refresh automatically as other tabs produce results.

### Clean Up

Remove noise and reverb from vocals and instruments using UVR (Ultimate Vocal Remover) models.

**Presets:** 8 curated presets across three model architectures:

- **Denoise** — remove background noise and hiss (Roformer, MDXC, and VR-architecture models at varying aggressiveness levels)
- **Dereverb** — strip room reverb and ambience (Roformer and VR-architecture models)

Models auto-download on first use (~50–200 MB each, cached at `~/.cache/stemforge/uvr/`).

**Workflow:**
1. Select a source audio file from the dropdown.
2. Choose a denoise or dereverb preset.
3. Click **Process**. Progress is shown while the model runs.
4. The result appears as a waveform below the original, with a **change intensity** diff visualization showing where the enhancement had the most effect.
5. Enhanced audio is automatically available in Mix, Export, and as a source for further enhancement (chain denoise → dereverb).

#### Batch Mode

Apply the same enhancement preset to multiple files at once.

1. Toggle **Batch mode** at the top of the Enhance tab.
2. Drop multiple audio files onto the upload zone (or click to browse and multi-select).
3. Choose a denoise or dereverb preset.
4. Click **Process All**. The model loads once and processes each file sequentially with per-file progress updates.
5. Results appear as individual waveform cards. Each can be saved individually, or click **Save All** to download all results as a ZIP archive.

### Tune (Auto-Tune)

Pitch-correct vocals using CREPE neural pitch detection and Praat TD-PSOLA resynthesis. Preserves vocal formants — no metallic artifacts.

**Controls:**
- **Key** — root note (C through B). Determines the tonal center for scale snapping.
- **Scale** — chromatic, major, minor, major pentatonic, minor pentatonic, or blues. Notes are snapped to the nearest scale degree.
- **Correction strength** — 0% (no correction) to 100% (hard snap to nearest note). Default: 80%. Lower values preserve more natural pitch variation.
- **Humanize** — 0% (robotic precision) to 100% (loose, natural feel). Adds random micro-detuning (Gaussian ±50 cents) to avoid the "T-Pain effect". Default: 15%.

**Workflow:**
1. Select a vocal stem from the dropdown.
2. Set key, scale, correction strength, and humanize.
3. Click **Process**. CREPE analyses the pitch contour, scale snapping is applied, and Praat resynthesizes the corrected audio.
4. The tuned result appears as a playable waveform and is added to Mix and Export.

### Effects (coming soon)

Placeholder for Phase 2 — custom DSP effects chain (parametric EQ, compression, convolution reverb, delay) built on scipy.signal.

---

## 4. MIDI — MIDI Extraction

Extract a MIDI representation from any separated stem.

- **Instrument stems** (bass, other, guitar, piano) — uses BasicPitch for polyphonic MIDI extraction.
- **Vocal stem** — uses faster-whisper for timing + PYIN pitch tracking for melody.

Select the stems to extract from, then click **Extract MIDI**. Each extracted stem shows a piano-roll preview and a **Preview** button that renders the MIDI to audio via FluidSynth and plays it in the browser. MIDI files can be saved to disk or sent to the Mix tab as a rendered track.

---

## 5. Synth — Audio Generation & SFX Stem Builder

### Audio Generation (Stable Audio Open)

Generate audio up to 600 seconds from a text prompt. Requires HuggingFace authentication — see [README: HuggingFace Authentication](../README.md#huggingface-authentication-required-for-the-synth-tab).

**Controls:**
- **Prompt** — describe the sound or music to generate (e.g. "lo-fi hip hop beat, 90 BPM, vinyl noise")
- **Duration** — up to 600 s; long durations are produced in 47 s chunks and concatenated
- **Steps / CFG scale** — diffusion step count and classifier-free guidance strength
- **Seed** — set a fixed seed for reproducible results; leave blank for random

**Conditioning (optional):**
- **Audio conditioning** — upload or select a stem/mix to guide the timbre and structure of the output
- **MIDI conditioning** — use an extracted MIDI file to guide melodic content
- **Vocal Preservation Mode** — isolates and preserves a vocal from the audio conditioning source while regenerating the backing track

Click **Generate**. Progress is shown while the model runs. The result appears as a waveform and is added to the Mix tab automatically.

### SFX Stem Builder

A DAW-style timeline for assembling sound-effect stems from individual clips.

1. Click **New Canvas** and set a duration.
2. Add clips from your session stems, previously saved files, or import external audio.
3. Drag clips onto the timeline, or set start time and duration manually. Each clip supports per-clip fade in/out and volume.
4. Optionally align the canvas to a reference stem (e.g. lock a sound effect to the drums timeline).
5. Click **Render Canvas** to bounce the arrangement to a single audio file, then **Send to Mix** to add it as a Mix track.

---

## 6. Compose — Full Song Generation (AceStep)

AceStep runs as a background process. On first visit the button reads **Initialize** — click it to start the backend and download model weights (~20 GB, cached for future sessions). Once ready the button becomes **Generate**.

Six modes are available via tabs:

### Create mode

Build a song from scratch:
- **Tags** — comma-separated genre/mood/instrument descriptors (e.g. `pop, upbeat, piano, female vocal`)
- **Duration** — target length in seconds; use "Estimate" to let AceStep suggest a duration
- **Lyrics** — enter manually, click **Generate Lyrics** for AI-written lyrics, or leave blank for instrumental
- **Section structure** — optionally define verse/chorus/bridge sections; "Estimate Sections" auto-fills from duration

Click **Generate** to start. Progress updates appear in the status area. The finished song plays in the waveform preview and is added to the Mix and Export tabs.

### Rework mode

Transform an existing audio file:
- **Reimagine** — full regeneration guided by a new prompt while preserving the original's structure
- **Fix & Blend** — regenerate a selected time region and blend it seamlessly with the rest

Upload audio via the Rework upload zone or use a separated stem or composed song from the session.

### Lego / Complete modes

Advanced structural editing powered by AceStep's analysis capabilities:
- **Lego** — decompose a song into sections and regenerate individual parts while keeping the rest intact
- **Complete** — extend or fill in missing sections of a partial composition

Both modes require the base generation model (auto-selected when entering the mode).

### Voice mode

Apply AI voice conversion (RVC) to any audio source:

1. **Source** — select a separated vocal stem or upload any audio file
2. **Voice model** — choose from 14 built-in voices (Freddie Mercury, Adele, Drake, and more), search HuggingFace for community models, or upload a local `.pth` file. Models download automatically on first use (~50–140 MB each).
3. **Controls:**
   - **Pitch shift** — semitones up or down
   - **F0 method** — pitch extraction algorithm (rmvpe recommended for most voices)
   - **Index ratio** — how strongly the voice character is applied
   - **Consonant protection** — reduce artefacts on unvoiced consonants

Click **Convert**. The result appears as a waveform. Use **Send to Separate** or **Send to Mix** to route it into the rest of the workflow.

### Seed controls

Every generation produces a seed value that can be reused for reproducible results:
- **Last** — recall the seed from the most recent generation
- **Random** — clear the seed field to let AceStep pick a random seed
- Set a specific seed number to reproduce an earlier result exactly (same tags, lyrics, duration, and seed → same output)

### LoRA adapter management

Fine-tuned adapters can be loaded to steer AceStep's generation style:

1. **Browse** — select from adapters in the `loras/` directory (supports PEFT LoRA directories and `.safetensors` files)
2. **Load** — activate the selected adapter. A status indicator shows the active adapter name.
3. **Scale** — adjust influence from 0% (base model only) to 100% (full adapter effect) via the slider
4. **Unload** — remove the adapter and restore the base model

If an adapter is silently dropped during generation (can happen after long idle periods), a warning appears so you can reload it.

### Project save / load

Save and restore the complete Compose tab state:
- **Save Project** — downloads a `.json` file capturing all settings: tags, lyrics, duration, mode, approach, seed, LoRA state, advanced parameters (~30 fields)
- **Load Project** — upload a previously saved project file to restore all settings in one click

### Train mode

Train custom LoRA or LoKR adapters to teach AceStep new styles, genres, or artist signatures.

**Pipeline (5 steps):**

1. **Upload** — drag audio files onto the upload zone (WAV, MP3, FLAC, OGG). These are the reference tracks AceStep will learn from.
2. **Scan** — load the uploaded audio into AceStep's dataset system. File durations and basic metadata are extracted.
3. **Auto-label** — AI-powered analysis generates style tags and structural captions for each sample. Progress is shown per-sample. Labels can be manually edited in the sample table after completion.
4. **Preprocess** — convert audio + labels into training tensors. This is the most time-intensive preparation step.
5. **Train** — start LoRA or LoKR fine-tuning with configurable parameters:
   - **Adapter type** — LoRA (general purpose) or LoKR (more compact)
   - **Rank** — adapter capacity (4–128, higher = more expressive but slower)
   - **Epochs** — number of training passes
   - **Learning rate** — training step size
   - **Advanced** — batch size, warmup steps, gradient accumulation, save interval

A live **loss chart** tracks training progress. Training can be stopped early if results are satisfactory.

**After training:**
- **Export** — save the trained adapter to the `loras/` directory, making it immediately available in the LoRA browser
- **Reinitialize** — reload the generation model to pick up the new adapter

**Snapshots:** Save and load named snapshots of your dataset + preprocessed tensors. Useful for iterating on training with different hyperparameters without re-running the full pipeline.

---

## 7. Mix — Multi-Track Mixer

Combines stems, MIDI-rendered tracks, synth outputs, and composed songs into a single stereo mix.

Tracks populate automatically as pipelines complete. Each track has:
- **Enable/disable toggle** — mute a track without removing it
- **Volume slider** — per-track level (0–200%)
- **Instrument selector** — for MIDI tracks, choose the GM instrument used for FluidSynth rendering
- **Source label** — shows where the track came from (stem, MIDI, synth, compose, SFX)

Tracks can also be added manually via **Add Audio** or **Add MIDI**.

Click **Render Mix** to bounce all enabled tracks to a stereo FLAC file. The result is added to the Export tab.

---

## 8. Export

Select any combination of pipeline outputs and download them in your preferred format.

- **Format** — WAV, FLAC, MP3, or OGG
- **Select all / deselect all** — quick toggle for bulk export
- Individual files can be downloaded separately or all selected files can be bundled as a **ZIP archive**

Available outputs include all separated stems, enhanced audio, extracted MIDI files, Synth-generated audio, composed songs, voice-converted audio, SFX renders, and the final mix.
