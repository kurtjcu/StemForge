# StemForge — User Guide

## Getting Started

Launch the app with `python run.py` and open **http://localhost:8765** in your browser.

The interface has six tabs across the top: **Separate, MIDI, Synth, Compose, Mix, Export**. A global transport bar at the bottom provides shared audio playback. The device badge in the top-right corner shows whether you're running on GPU or CPU.

Use the **New Session** button (top-right) to clear all loaded tracks, stems, and results when you want to start fresh.

### Supported Input Formats

- **Audio:** WAV, FLAC, MP3, OGG, AIFF
- **Video:** MP4, MKV, WEBM, AVI, MOV (audio is extracted automatically via FFmpeg)

---

## 1. Separate — Source Separation

Split a song into individual stems (vocals, drums, bass, other, guitar, piano).

### How to use

1. **Load a file** — drag and drop onto the drop zone, or click to browse. Video files work too.
2. **Choose an engine:**
   - **Demucs** — 4 models, all produce vocals/drums/bass/other.
   - **BS-Roformer** — 6 models. ViperX and KJ produce vocals + other. ZFTurbo produces 4 stems. jarredou produces 6 stems (adds guitar + piano).
3. **Use "Help me choose"** (optional) — analyzes your audio and recommends the best engine and model.
4. **Check the stems** you want to extract (all selected by default).
5. Click **Separate** and wait for the progress bar to complete.

### Results

Each stem appears as a card with its own waveform player. Use Play/Pause to audition stems, and the save button to download individual stems as WAV files.

Separated stems are automatically sent to the MIDI, Synth, Mix, and Export tabs.

---

## 2. MIDI — Polyphonic MIDI Extraction

Extract MIDI note data from separated audio stems.

### Prerequisites

Run separation first — the MIDI tab needs audio stems to work with.

### How to use

1. **Select stems** to process (all checked by default).
2. Set **Key** (or leave on auto-detect), **BPM**, and **Time Signature** for accurate quantization.
3. Adjust **Onset threshold** (higher = fewer notes detected) and **Frame threshold** as needed.
4. Click **Extract MIDI**.

### Results

Each stem shows its note count. Use **Preview** to hear a FluidSynth render of the MIDI, or **Save** to write the MIDI file to `~/Music/StemForge/`. A **Save merged MIDI** button combines all stems into one file.

MIDI stems are sent to the Synth tab (as align references) and the Mix tab (as MIDI tracks).

---

## 3. Synth — Audio Generation + SFX Stem Builder

Two tools in one tab: generate new audio clips from text prompts, then arrange them on a DAW-style timeline.

### Generating Audio

1. Write a **Prompt** describing the audio you want (e.g., "warm analog pad with slow filter sweep").
2. Set **Duration** (up to 120 s), **Steps** (more = higher quality), and **CFG Scale** (higher = more prompt-adherent).
3. Optionally choose a **Conditioning source** — an audio stem, MIDI, or mix render to guide the generation.
4. Toggle **Vocal Preservation Mode** if conditioning on a vocal stem.
5. Click **Generate**.

Generated clips appear as playable cards. Click the clip name to rename it. Use **+ SFX Canvas** to send it directly to the canvas below.

### SFX Stem Builder

Build composite sound design stems by arranging clips on a timeline.

1. **Create a canvas** — give it a name and duration, then click New Canvas. Or select an existing canvas from the dropdown.
2. **Align to a reference** — pick a separated stem or upload a reference file. The canvas resizes to match the reference duration, and its waveform appears as a visual guide on the timeline.
3. **Add clips** — use the clip source dropdown (groups: This Session / Saved SFX / Imported). Set the start time (click the timeline to position), volume, and fade in/out. Click **Add Clip**.
4. **Edit placements** — click a clip block on the timeline or use the Edit button in the placements list to adjust timing, volume, and fades.
5. **Render Canvas** — renders the arrangement and sends it to the Mix tab as a new track.

The timeline ruler, reference waveform lane, and color-coded clip blocks give you a visual overview of your arrangement. Enable **Soft limiter** to prevent clipping in the render.

---

## 4. Compose — Full Song Generation (AceStep)

Generate complete songs with vocals, instruments, and structure.

### First-time setup

Click **Initialize** to start the AceStep subprocess. Model downloads happen automatically on first run — this may take a while.

### Create Mode

1. **Write lyrics** in the My Lyrics tab using `[Verse]`, `[Chorus]`, `[Bridge]` section markers. Or use **AI Lyrics** to generate them from a description. Choose **Instrumental** for no vocals.
2. **Build a style** — click genre tags (Electronic, Jazz, Rock, etc.) and mood tags (Uplifting, Melancholic, Energetic, etc.). Add a custom description for more detail.
3. Set **Duration** (up to 10 minutes) or enable **Auto** to estimate from your lyrics and BPM.
4. Adjust **Strictly follow lyrics**, **Creativity**, and **Quality** sliders.
5. Click **Generate**.

### Rework Mode

1. **Upload audio** — drag and drop or browse for a file.
2. Choose an approach:
   - **Reimagine** — reinterpret the full track with a new style. Adjust the strength slider.
   - **Fix & Blend** — repair or replace a specific time region. Set start and end times.
3. Describe the desired **Style direction**.
4. Click **Reimagine** or **Fix & Blend**.

### Results

Each result has an inline player, Download button, and a JSON metadata link. Use **-> Separate** to load the generated song back into the Separate tab for stem extraction — a powerful loop for iterating on AI-generated music.

### Advanced Options

Expand the **Advanced** panel for control over generation model (Turbo/HQ/Base), planning intelligence, VRAM tier, batch size, audio format, seed, scheduler, inference steps, and guidance scales.

---

## 5. Mix — Multi-Track Mixer

Combine all your audio and MIDI into a final mix.

### How it works

Tracks are added automatically when you separate stems, extract MIDI, generate audio, compose songs, or render SFX canvases. You can also add tracks manually:

- **+ Audio** — import any audio file as a track.
- **+ MIDI** — import a MIDI file as a track.

### Per-track controls

- **Enable/Disable toggle** — include or exclude from the mix.
- **Volume slider** — 0.0 to 1.0.
- **Delete (x)** — remove the track.
- Audio tracks have inline waveform players for auditioning.

### Preview and Render

- **Preview** — plays all enabled audio tracks simultaneously from the start. Click again to pause, or use Stop. Preview ends automatically when the longest track finishes.
- **Render Mix** — produces a FLAC file combining all enabled tracks at their set volumes. A Master Mix player appears at the top when rendering is complete.

---

## 6. Export — Download Your Work

Package any combination of outputs for download.

### How to use

1. Check the **artifacts** you want to export — stems, generated audio, composed songs, SFX stems, and the final mix are all listed.
2. Choose an **Output format**: WAV, FLAC, MP3, or OGG.
3. Click **Export** to convert files to the chosen format.
4. Click **Download All as ZIP** to bundle everything into `stemforge_export.zip`.

Individual files can also be downloaded one at a time from the results list.

---

## Typical Workflow

```
Upload audio -> Separate stems -> Extract MIDI -> Generate SFX -> Compose new parts
                                                        |               |
                                                        v               v
                                                   Mix everything together
                                                        |
                                                        v
                                                   Export final files
```

1. **Separate** a song into stems.
2. **Extract MIDI** from the instrumental stems.
3. **Generate** new audio in the Synth tab, conditioned on your stems or MIDI.
4. **Build SFX** on the canvas, aligned to a reference stem.
5. **Compose** entirely new songs, or rework existing audio.
6. **Mix** all tracks together with volume balancing.
7. **Export** in your preferred format.

Each tab feeds into the next automatically — separated stems appear in MIDI, Synth, and Mix. Generated clips appear in Mix. Composed songs can loop back to Separate. The whole pipeline is interconnected.

---

## Tips

- **"Help me choose"** on the Separate tab saves time — it profiles your audio and picks the best model.
- **BS-Roformer ViperX** is generally the best vocal separator for recorded music.
- **Click the timeline** in the SFX builder to position clips visually instead of typing milliseconds.
- **Conditioning** in the Synth tab produces more coherent results than pure text generation.
- **Auto duration** in Compose estimates timing from your lyrics — useful for getting natural pacing.
- Use **-> Separate** on Compose results to extract stems from AI-generated songs for further remixing.
- The **Preview** button in Mix lets you hear the balance before committing to a render.
