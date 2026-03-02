# SFX Stem Builder — Specification & Current Implementation State

> **Purpose of this document:** Describe what has been built, what was originally specced, and where the current implementation does not match the user's intent. Used to clarify the correct design before further implementation.

---

## 1. What Was Originally Specced

The SFX Stem Builder was designed to let users:

1. Create a blank audio **canvas** with a fixed duration (matching a reference stem or manually set).
2. Place multiple Synth-generated sound effect clips onto the canvas at specific timestamps with per-clip volume and fade controls.
3. Non-destructively edit placements (move, adjust, remove) — the canvas is re-rendered from a JSON manifest on every change.
4. Render the composite to a single WAV stem.
5. Send that stem to the Mix tab.

The original spec anticipated a **reference stem waveform** displayed for visual alignment, with the canvas waveform below it.

---

## 2. What Has Been Built (Backend)

### 2.1 Data Model

SFX manifests are JSON files stored at `~/.local/share/stemforge/output/sfx/{sfx_id}/manifest.json`.

```json
{
  "id": "sfx_a1b2c3",
  "name": "Rain & Thunder",
  "duration_ms": 30000,
  "sample_rate": 44100,
  "channels": 2,
  "apply_limiter": false,
  "placements": [
    {
      "id": "p1",
      "clip_path": "/path/to/clip.wav",
      "start_ms": 0,
      "volume": 1.0,
      "fade_in_ms": 0,
      "fade_out_ms": 0,
      "fade_curve": "linear"
    }
  ]
}
```

The rendered WAV is at `{sfx_id}/rendered.wav`.

### 2.2 Renderer (`backend/services/sfx_renderer.py`)

- Creates a stereo 44100 Hz canvas of silence
- For each placement: load clip → resample → convert channels → apply fade in/out → apply volume → sum into canvas at `start_ms` offset
- Optional soft limiter (tanh)
- Clips extending past the canvas end are truncated

### 2.3 API Endpoints (`backend/api/sfx.py`, prefix `/api/sfx`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/create` | Create new canvas (manual duration or reference-stem mode) |
| GET | `` | List all canvases in session |
| GET | `/{id}` | Full manifest + waveform peaks + rendered path |
| POST | `/{id}/placements` | Add a clip placement, re-render |
| PUT | `/{id}/placements/{pid}` | Update a placement (any field), re-render |
| DELETE | `/{id}/placements/{pid}` | Remove a placement, re-render |
| PATCH | `/{id}` | Update name, limiter toggle, **or canvas duration_ms** (resize + re-render) |
| POST | `/{id}/send-to-mix` | Add rendered WAV as audio track in Mix tab |
| DELETE | `/{id}` | Delete canvas, files, and associated mix track |
| GET | `/{id}/stream` | Stream rendered WAV |
| GET | `/{id}/reference-waveform` | Waveform peaks for the reference stem (if set) |
| GET | `/available-clips` | List WAV files in Synth output + Stems dirs as clip sources |

The PATCH endpoint now accepts `duration_ms` to resize the canvas and re-render all existing placements at the new length.

---

## 3. What Has Been Built (Frontend)

### 3.1 Layout — Synth Tab, Two-Column

```
┌─────────────────────────────┬──────────────────────────────────────┐
│ LEFT COLUMN                 │ RIGHT COLUMN                         │
│                             │                                      │
│ [Generation Controls]       │ [Generation Progress / Result card]  │
│  - Prompt                   │                                      │
│  - Duration (0–120s)        │ [sfx-section — hidden until canvas   │
│  - Steps                    │  is created or loaded]               │
│  - CFG Scale                │                                      │
│  - Conditioning source      │   ┌─ ALIGN TO STEM ────────────────┐ │
│  - Vocal Preservation       │   │ [dropdown: audio stems + MIDI] │ │
│  - [Generate button]        │   │ [reference waveform — hidden   │ │
│                             │   │  until stem selected]          │ │
│ [SFX STEM BUILDER card]     │   └────────────────────────────────┘ │
│  - Canvas name input        │                                      │
│  - Duration slider (0–120s) │   ┌─ canvas title / Play/Stop/Rew ┐ │
│  - [New Canvas] [dropdown   │   │ [SFX canvas waveform — white] │ │
│    of existing canvases]    │   └────────────────────────────────┘ │
│                             │                                      │
│                             │   ┌─ SETTINGS ─────────────────────┐ │
│                             │   │ [Soft limiter toggle]          │ │
│                             │   │ [Delete Canvas]                │ │
│                             │   └────────────────────────────────┘ │
│                             │                                      │
│                             │   ┌─ ADD CLIP MANUALLY ────────────┐ │
│                             │   │ [clip source dropdown]         │ │
│                             │   │ [Start ms] [Volume]            │ │
│                             │   │ [Fade in] [Fade out] [Curve]   │ │
│                             │   │ [Add Clip button]              │ │
│                             │   └────────────────────────────────┘ │
│                             │                                      │
│                             │   ┌─ PLACEMENTS ───────────────────┐ │
│                             │   │ [list of clips with Edit/      │ │
│                             │   │  Remove per clip]              │ │
│                             │   └────────────────────────────────┘ │
└─────────────────────────────┴──────────────────────────────────────┘
```

### 3.2 "Align to Stem" Dropdown

- Populated from two sources:
  - **Audio stems** (from Separate tab, `stemsReady` event): shown with green waveform
  - **MIDI stems** (from MIDI tab, `midiReady` event): labeled `"vocals [MIDI]"` etc.; rendered to audio on-demand via `/api/midi/render` before waveform loads; shown with purple waveform
- When a stem is selected:
  1. Fetches stem audio info (`/api/audio/info`) to get duration
  2. Sets the canvas duration slider value to match
  3. Shows a **read-only wavesurfer waveform** of the reference stem (non-interactive, `interact: false`) in a hidden div that becomes visible
  4. If a canvas is currently loaded: PATCHes the canvas with the new `duration_ms` and reloads it

### 3.3 SFX Canvas Waveform

- Color: **white** (`waveColor: '#ffffff'`) — distinct from green (audio stems) and purple (MIDI)
- Interactive (wavesurfer default) — clicking seeks, play/pause/stop/rewind buttons in the card header
- Buttons: ▶ Play / ⏸ Pause (toggle), ⏹ Stop, ⏮ Rewind, time display
- Playback is **local to the wavesurfer instance** (same pattern as Separate tab stem cards — not global transport)

### 3.4 Placement List

Each clip in the canvas shows:
- Clip filename
- `@ {start_ms}ms | vol {x}% | fi {ms} | fo {ms}` summary
- **Edit** button: populates the "Add Clip Manually" form with current values; swaps Add button for "Update Clip" + Cancel
- **Remove** button

### 3.5 Waveform Colors (system-wide)

| Source | Color |
|--------|-------|
| Audio stems (Separate tab) | Green (`#22c55e`) |
| MIDI renders | Purple (`#a855f7`) |
| SFX canvas | White (`#ffffff`) |
| Mix master waveform | Green (default) |

### 3.6 Mix Tab

- SFX tracks (label starts with `"SFX: "`) have their label rendered in **white bold** to distinguish from regular stems

---

## 4. Timeline Implementation (DAW-style, added 2026-03-02)

The stacked waveform approach was replaced with a multi-track DAW-style timeline:

- Single "timeline card" merges the old "ALIGN TO STEM" card and "SFX canvas" card
- `#sfx-timeline` contains: ruler (tick marks), lanes area, amber playhead line
- Reference stem → green lane spanning full width; MIDI reference → purple variant
- Clip placements → white semi-transparent blocks sized by `clip_duration_ms`, packed into non-overlapping rows via `packPlacements()` greedy algorithm
- Click on empty timeline space → sets `#sfx-clip-start` value
- Click on clip block → opens edit mode in the Placements list below
- Playhead moves in sync with hidden wavesurfer timeupdate events
- "Send to Mix" replaced by "Show in Mix" (navigates to Mix tab)
- Canvas auto-added to Mix on creation (`mix_track_id` stored in manifest)
- All mutations emit `sfxReady` → Mix tab auto-refreshes

### Key data fields added to placement manifest:

```json
{
  "clip_name": "thunder.wav",
  "clip_duration_ms": 4200
}
```

Old manifests are backfilled on `GET /{sfx_id}` without blocking the response.

---

## 5. Where the Implementation Does Not Match the User's Intent (historical)

The user expressed that the "Align to" feature is **not working as envisioned**. Based on the conversation, the intent appears to be:

### What the user wants (inferred, needs clarification):

1. **Reference waveform as a persistent visual ruler** — the selected stem should be shown as a waveform that acts as a visual timeline guide, with the SFX canvas waveform directly below it, **both at the same time scale simultaneously**. The user should be able to look at both at the same time and visually judge where sounds fall.

2. **Clip placement relative to the reference** — the user generates sound effects and places them on the canvas with `start_ms`, `fade_in_ms`, `fade_out_ms` etc., looking at the reference waveform above to decide where each clip should go.

3. **Visual adjustment** — after placing clips, the user should be able to see the result (SFX canvas waveform) and compare it to the reference stem to check alignment, then adjust `start_ms`, fade windows etc. on individual clips.

4. **The reference is read-only** — it is never modified, only used for visual guidance.

### What the current implementation actually does:

- The reference waveform appears as a small non-interactive waveform in a card above the canvas — **but it is the same visual size and format as the canvas**, with no shared time axis or any indication that positions correspond
- Both waveforms are independent wavesurfer instances with no time-sync mechanism
- The placement controls (`start_ms`, `fade_in`, `fade_out`) are in a separate "ADD CLIP MANUALLY" card below the canvas — there is no visual connection between "I can see the reference waveform showing that the chorus starts at 45s" and "I type 45000 in the start_ms box"
- There is no way to see **where on the reference waveform** a placed clip falls
- The waveforms cannot be visually scrubbed together or compared side by side in a meaningful DAW-like way

### What is likely needed (but not yet designed):

- A **shared time-axis view** where the reference stem and each SFX clip appear as horizontal tracks stacked vertically, all sharing the same horizontal time axis (like a multi-track DAW lane view)
- **Drag-to-position** or at minimum a visual indicator showing where a clip falls on the timeline relative to the reference
- **Clip markers** on the canvas waveform showing where each placement begins and ends
- Possibly: the ability to **click on the reference waveform** to set the `start_ms` of the next clip to add

---

## 5. Files Involved

| File | Role |
|------|------|
| `frontend/components/generate.js` | All Synth tab UI including SFX canvas, Align to, placement list |
| `backend/api/sfx.py` | All SFX REST endpoints |
| `backend/services/sfx_renderer.py` | Canvas rendering engine |
| `frontend/components/waveform.js` | createWaveform() — color scheme |
| `frontend/components/mix.js` | Mix track list — SFX label color |
| `utils/paths.py` | SFX_DIR constant |
| `backend/services/session_store.py` | SFX manifest storage in session |

---

## 6. Key Design Questions for Clarification

1. Should the reference stem and SFX clips share a **visual timeline** (DAW-style track lanes), or is two stacked waveforms with the same pixel width sufficient?

2. Should clip placement be done by **clicking on the timeline** to set `start_ms`, or by **typing ms values** (current), or both?

3. Should clip boundaries (start + end) be visible as **markers or regions** on the timeline?

4. Should the user be able to **drag clips** to reposition them, or is a text-entry workflow acceptable?

5. Should the reference waveform be **playable** (for listening reference) or truly read-only/display-only?

6. Is the **generated clip waveform** needed in the placement UI, or just the filename?

7. Should there be a **combined preview** where the reference stem and placed SFX clips play back together for monitoring?
