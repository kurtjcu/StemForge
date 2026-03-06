# StemForge — User Guide

**Tab bar:** Separate · MIDI · Synth · Compose · Mix · Export

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

---

## 3. MIDI — MIDI Extraction

Extract a MIDI representation from any separated stem.

- **Instrument stems** (bass, other, guitar, piano) — uses BasicPitch for polyphonic MIDI extraction.
- **Vocal stem** — uses faster-whisper for timing + PYIN pitch tracking for melody.

Select the stems to extract from, then click **Extract MIDI**. Each extracted stem shows a piano-roll preview and a **Preview** button that renders the MIDI to audio via FluidSynth and plays it in the browser. MIDI files can be saved to disk or sent to the Mix tab as a rendered track.

---

## 4. Synth — Audio Generation & SFX Stem Builder

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

## 5. Compose — Full Song Generation (AceStep)

AceStep runs as a background process. On first visit the button reads **Initialize** — click it to start the backend and download model weights (~20 GB, cached for future sessions). Once ready the button becomes **Generate**.

Three modes are available via tabs:

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

---

## 6. Mix — Multi-Track Mixer

Combines stems, MIDI-rendered tracks, synth outputs, and composed songs into a single stereo mix.

Tracks populate automatically as pipelines complete. Each track has:
- **Enable/disable toggle** — mute a track without removing it
- **Volume slider** — per-track level (0–200%)
- **Instrument selector** — for MIDI tracks, choose the GM instrument used for FluidSynth rendering
- **Source label** — shows where the track came from (stem, MIDI, synth, compose, SFX)

Tracks can also be added manually via **Add Audio** or **Add MIDI**.

Click **Render Mix** to bounce all enabled tracks to a stereo FLAC file. The result is added to the Export tab.

---

## 7. Export

Select any combination of pipeline outputs and download them in your preferred format.

- **Format** — WAV, FLAC, MP3, or OGG
- **Select all / deselect all** — quick toggle for bulk export
- Individual files can be downloaded separately or all selected files can be bundled as a **ZIP archive**

Available outputs include all separated stems, extracted MIDI files, Synth-generated audio, composed songs, voice-converted audio, SFX renders, and the final mix.
