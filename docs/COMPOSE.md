# Compose Tab — AceStep Music Generation

StemForge's Compose tab generates full songs using [AceStep 1.5](https://github.com/ace-step/ACE-Step),
an AI music generation model that produces complete tracks from style descriptions and lyrics.

AceStep runs as a separate subprocess managed by StemForge's launcher.

---

## Quick start

1. Start StemForge normally (without `--no-acestep`).
2. Open browser → Compose tab.
3. Click **⏻ Initialize** — this starts the AceStep backend and downloads model weights (~20 GB) on the first run. An elapsed timer shows progress.
4. Once ready, the button becomes **▶ Generate**.
5. Pick genre/mood tags, write or generate lyrics, click **Generate**.

---

## Modes

### Create mode

Build a song from scratch using:

- **Genre tags** — 16 categories (Electronic, Hip-Hop, Jazz, Rock, etc.)
- **Mood tags** — 10 categories (Uplifting, Melancholic, Energetic, etc.)
- **Song parameters** — Key, BPM, time signature
- **Custom description** — free-text style prompt
- **Lyrics** — three tabs: My Lyrics (manual), AI Lyrics (generated), Instrumental

All selections combine into a style prompt shown in the preview area.

### Rework mode

Transform an existing audio file:

- **Reimagine** — regenerate the full song with new style while preserving structure. Strength slider controls how much of the original to keep.
- **Fix & Blend** — regenerate only a selected time region. Useful for fixing a bad section while keeping the rest.

Upload audio via drag-and-drop or file browser. Set a style direction to guide the transformation.

---

## Lyrics

### My Lyrics tab

Write lyrics manually or load from a `.txt` / `.lrc` file. Use section headers like `[Verse 1]`, `[Chorus]`, etc. The character count and lyrics-too-long warning update in real time.

### AI Lyrics tab

Describe what the song is about and AceStep's language model generates lyrics. The style, BPM, key, and duration are sent as guidance context.

### Instrumental tab

No lyrics — AceStep generates an instrumental track from your style settings alone.

---

## Controls

| Control | Range | Description |
|---|---|---|
| Duration | 10–600 s | Target song length. **Auto** estimates from lyrics + BPM. |
| Strictly follow lyrics | Loose / Med / Strict | How closely generation follows the lyrics. Maps to lyric guidance scale. |
| Creativity | 0–100% | Balance between style adherence and creative freedom. |
| Quality | Raw / Balanced / Polished | Number of inference steps — more = higher quality, slower. |

---

## Advanced panel

Accessible via the disclosure triangle below the Generate button.

| Setting | Description |
|---|---|
| Generation model | Turbo (fast), High Quality (sft), Base |
| Planning intelligence | None, Small (0.6B), Medium (1.7B, default), Large (4B) |
| VRAM tier | ≤16GB, 24GB, 32GB+ — controls batch size limits |
| Batch size | Generate multiple variations at once (limited by VRAM + model combo) |
| Audio format | MP3 (default), WAV, FLAC |
| Seed | Reproducibility control (leave empty for random) |
| Scheduler | Euler, DPM++, DDIM |
| Inference steps | 10–150 (synced from Quality slider) |
| Guidance scale (lyric) | 1–15 (synced from lyrics adherence slider) |
| Guidance scale (audio) | 1–15 |

---

## Cross-tab integration

### Send to Separate

Each result card has a **→ Separate** button that:
1. Downloads the audio from AceStep
2. Saves it to StemForge's compose output directory
3. Loads it as the session's active audio file
4. Switches to the Separate tab

From there, you can separate stems, extract MIDI, and remix in the Mix tab.

### Mix tab

Compose results appear in the Mix tab when `composeReady` fires. After sending to session, composed tracks are available for multi-track mixing.

### Export tab

Compose outputs appear in the Export tab's artifact checklist.

---

## AceStep subprocess

StemForge manages AceStep as a child process:

- **Lazy startup:** AceStep doesn't start until you click **Initialize** on the Compose tab. This avoids a ~20 GB download on app startup.
- **Default port:** 8001 (configurable via `--acestep-port` or `ACESTEP_PORT`)
- **Disable:** `--no-acestep` flag → Compose tab shows disabled message
- **Crash handling:** StemForge stays running; Compose tab shows error state

### Environment variables forwarded to AceStep

| Variable | Purpose |
|---|---|
| `ACESTEP_DEVICE` | Force device (cuda/cpu) |
| `MAX_CUDA_VRAM` | VRAM limit |
| `ACESTEP_VAE_ON_CPU` | Offload VAE to CPU |
| `ACESTEP_LM_BACKEND` | Language model backend |
| `ACESTEP_INIT_LLM` | Initialize LLM on startup |

### GPU selection

`--gpu N` sets `CUDA_VISIBLE_DEVICES=N` on the AceStep subprocess only.
StemForge's in-process pipelines use their own device detection.
