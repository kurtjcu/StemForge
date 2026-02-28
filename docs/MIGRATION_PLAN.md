# StemForge → FastAPI + HTML Migration Plan

## Goal

Convert StemForge from a DearPyGUI desktop app to a FastAPI backend + vanilla HTML/CSS/JS frontend, matching the architecture of Ace-Step-Wrangler. Prepare the structure so Ace-Step-Wrangler can later be folded in as a panel/submodule.

---

## Architecture Overview

### Current (DearPyGUI)
```
GUI thread → directly calls pipeline objects → renders results in DPG widgets
```

### Target (FastAPI + HTML)
```
Browser (HTML/CSS/JS) → fetch()/WebSocket → FastAPI backend → pipeline objects
                                                            → serves static frontend
```

### Key Difference from Wrangler

Wrangler's backend is a **thin relay** — it proxies requests to a separate AceStep API process. StemForge is different: the pipelines (Demucs, BS-Roformer, BasicPitch, etc.) run **in-process** because they're Python libraries, not external API servers. The FastAPI backend will directly import and run pipeline code.

This means StemForge's backend is "thick" compared to Wrangler's, but the frontend pattern (vanilla HTML/CSS/JS, same dark DAW aesthetic, same `fetch()` + polling pattern) is identical.

---

## Target Structure

```
StemForge/
├── run.py                          # Unified launcher (single server)
├── pyproject.toml                  # Updated deps (drop dearpygui, add fastapi/uvicorn)
├── config.py                       # Unchanged
│
├── backend/
│   ├── main.py                     # FastAPI app — routes, static mount
│   ├── api/
│   │   ├── separate.py             # /api/separate — Demucs + BS-Roformer endpoints
│   │   ├── midi.py                 # /api/midi — BasicPitch + VocalMIDI endpoints
│   │   ├── generate.py             # /api/generate — Stable Audio Open endpoints
│   │   ├── mix.py                  # /api/mix — Mix engine endpoints
│   │   ├── export.py               # /api/export — Format conversion endpoints
│   │   └── system.py               # /api/health, /api/device, /api/models
│   └── services/
│       ├── job_manager.py          # Background task runner + job store
│       └── audio_server.py         # Serve audio files, waveform data
│
├── frontend/
│   ├── index.html                  # Single-page app shell with tab navigation
│   ├── style.css                   # Dark DAW theme (port Wrangler's tokens)
│   ├── app.js                      # Main app logic, routing, shared state
│   └── components/
│       ├── loader.js               # File upload/browser
│       ├── separate.js             # Separation tab UI
│       ├── midi.js                 # MIDI tab UI
│       ├── generate.js             # Generate tab UI
│       ├── mix.js                  # Mix tab UI
│       ├── export.js               # Export tab UI
│       ├── waveform.js             # Waveform visualiser (canvas-based)
│       ├── midi-viz.js             # MIDI piano roll visualiser
│       └── audio-player.js         # DAW-style transport (rewind/play/stop/scrub)
│
├── pipelines/                      # Unchanged — existing pipeline code
│   ├── demucs_pipeline.py
│   ├── bsroformer_pipeline.py
│   ├── midi_pipeline.py
│   ├── vocal_midi_pipeline.py
│   └── musicgen_pipeline.py
│
├── models/                         # Unchanged
├── utils/                          # Unchanged
│
├── vendor/                         # Future: ACE-Step-1.5 submodule
│   └── ACE-Step-1.5/              # (added when folding in Wrangler)
│
├── gui/                            # DEPRECATED — remove after migration
│   └── ...
│
└── docs/
    ├── GENERATE.md
    ├── FUTURE_PLANS.md
    └── MIGRATION.md                # This plan
```

---

## Migration Stages

### Stage 1: Backend API Layer

**Create `backend/main.py` and route modules.** Each pipeline gets its own router.

**Pattern for long-running tasks** (separation, MIDI extraction, generation):
```python
# POST /api/separate → returns { "job_id": "..." }
# GET  /api/jobs/{job_id} → returns { "status": "running"|"done"|"error", "progress": 0.42, "stage": "Loading model...", "result": {...} }
```

Use `asyncio` + `threading` (same as current DPG pattern — pipelines run in background threads, progress callbacks update a shared job store).

**Pattern for quick operations** (export, audio info):
```python
# POST /api/export → returns file directly
# GET  /api/audio/info?path=... → returns { duration, sample_rate, channels }
```

**Audio serving:**
```python
# GET /api/audio/stream?path=... → streams audio (no download header)
# GET /api/audio/download?path=... → Content-Disposition: attachment
# GET /api/audio/waveform?path=... → returns downsampled waveform JSON for canvas rendering
```

**WebSocket for real-time progress** (upgrade from Wrangler's polling):
```python
# WS /ws/jobs/{job_id} → pushes { "progress": 0.55, "stage": "Separating..." }
```
Fall back to polling via `GET /api/jobs/{job_id}` for simplicity — implement WebSocket as a stretch goal.

**Key implementation notes:**
- `job_manager.py` holds a dict of `{job_id: JobState}` with thread-safe progress updates
- Pipeline `.run()` methods already accept progress callbacks — wire them to update `JobState`
- The existing `AppState` singleton in `gui/state.py` gets replaced by the job manager
- Model loading is lazy and cached, same as current behaviour

### Stage 2: Static Frontend Shell

Port Wrangler's design tokens and CSS structure to StemForge's needs:

```css
:root {
  /* Inherit Wrangler's proven dark DAW palette */
  --bg:             #0d0d11;
  --surface:        #15151c;
  --surface-raised: #1c1c26;
  --accent:         #f59e0b;    /* warm amber */
  /* ... same token system ... */
}
```

**Layout:** Tab-based single-page app (not Wrangler's 3-column layout, which is specific to music generation). StemForge's workflow is sequential: Load → Separate → MIDI → Generate → Mix → Export.

```
┌─────────────────────────────────────────────────────┐
│ [Logo] StemForge          [device: CUDA] [UI Scale] │  ← header
├─────────────────────────────────────────────────────┤
│ [Load] [Separate] [MIDI] [Generate] [Mix] [Export]  │  ← tab bar
├─────────────────────────────────────────────────────┤
│                                                     │
│              Active tab content                     │  ← main area
│                                                     │
├─────────────────────────────────────────────────────┤
│ ◀ ▶ ■  ───────●──────── 1:23 / 3:45   [waveform]  │  ← transport bar
└─────────────────────────────────────────────────────┘
```

### Stage 3: File Loader Component

- Drag-and-drop zone + file picker button
- `POST /api/upload` → stores file, returns `{ file_id, filename, duration, sample_rate }`
- Waveform preview in the transport bar
- Audio profiler results shown (recommended engine/model)

### Stage 4: Separation Tab

- Engine selector (Demucs / BS-Roformer) with model dropdown
- Auto-recommend badge from audio profiler
- "Separate" button → `POST /api/separate` → poll progress → show stem cards
- Each stem card: waveform preview, play button, per-stem volume/solo
- Port the existing stem visualisation from DPG canvas to HTML `<canvas>`

### Stage 5: MIDI Tab

- Stem checkboxes (populated after separation)
- Musical parameter inputs (BPM, key, time sig) — auto-filled from Ace-Step JSON sidecar
- "Extract MIDI" button → `POST /api/midi` → progress → per-stem MIDI results
- Piano roll visualiser via `<canvas>` (port from DPG plot)
- Per-stem FluidSynth preview playback via backend audio endpoint

### Stage 6: Generate Tab

- Text prompt input, audio/MIDI conditioning file selectors
- Duration slider, Vocal Preservation toggle + sub-controls
- "Generate" button → `POST /api/generate` → progress → audio result card
- HuggingFace auth status indicator

### Stage 7: Mix Tab

- Per-track cards (stems + MIDI renders) with instrument selector, volume slider, solo button
- Master timeline with click-to-seek
- "Render Mix" button → `POST /api/mix` → FLAC output

### Stage 8: Export Tab

- Checklist of available outputs (stems, MIDI files, mix, generated audio)
- Format selector (wav/flac/mp3/ogg)
- Bulk download as zip or individual downloads

### Stage 9: Polish + Wrangler Integration Prep

- Keyboard shortcuts
- Responsive layout adjustments
- Error states and recovery
- **Add `vendor/` directory structure** for future ACE-Step-1.5 submodule
- **Add tab/panel slot** in the UI for "Compose" (Wrangler's future home)
- Document the panel plugin pattern so Wrangler can register its own tab

---

## Folding Ace-Step-Wrangler In (Future)

Once the migration is complete, Wrangler becomes a panel within StemForge:

```
StemForge/
├── vendor/
│   └── ACE-Step-1.5/              # git submodule (moved from Wrangler)
├── frontend/
│   └── components/
│       └── compose.js             # Wrangler's app.js adapted as a tab
├── backend/
│   └── api/
│       └── compose.py             # Wrangler's main.py adapted as a router
│       └── acestep_wrapper.py     # Moved from Wrangler unchanged
```

**What changes:**
- Wrangler's `run.py` launcher logic merges into StemForge's `run.py` (add AceStep subprocess management)
- Wrangler's `backend/main.py` becomes `backend/api/compose.py` (a FastAPI router, not a standalone app)
- Wrangler's `frontend/app.js` becomes `frontend/components/compose.js` (renders into a tab panel, not `#app`)
- Wrangler's `style.css` merges into StemForge's `style.css` (shared design tokens already match)
- `pyproject.toml` gains `ace-step` as a path dependency in `[tool.uv.sources]`

**What stays the same:**
- `acestep_wrapper.py` — unchanged, still talks to AceStep REST API
- `vendor/ACE-Step-1.5/` — submodule moves to StemForge root
- All AceStep communication stays via REST API (separate process)

---

## Dependency Changes

### Remove
- `dearpygui>=1.11.0`
- `screeninfo>=0.8.0` (no longer needed for display detection)

### Add
- `fastapi>=0.115.0`
- `uvicorn>=0.30.0`
- `python-multipart>=0.0.9` (for file uploads)

### Keep
- All pipeline dependencies unchanged
- `sounddevice` (still used for backend audio preview if needed, or remove if browser handles all playback)

---

## Key Design Decisions

1. **Vanilla HTML/CSS/JS, no framework** — matches Wrangler, keeps it simple, no build step.

2. **Polling over WebSocket initially** — Wrangler uses polling and it works fine. WebSocket can be added later for smoother progress bars.

3. **Pipelines run in-process** — unlike Wrangler (which proxies to AceStep), StemForge's pipelines are Python libraries that run directly in the backend process. This is a "thick backend" vs Wrangler's "thin relay".

4. **Single server process** — StemForge only needs one `uvicorn` process (backend + static files). When Wrangler is folded in, `run.py` will manage the additional AceStep subprocess.

5. **Shared design tokens** — use Wrangler's CSS custom properties verbatim so the Compose tab looks native when integrated.

6. **Tab-based navigation** — StemForge's sequential workflow (load → separate → midi → generate → mix → export) maps naturally to tabs. Wrangler's 3-column layout becomes the content of the "Compose" tab.

7. **Persistent transport bar** — a global audio player at the bottom (like a DAW transport) that any tab can send audio to. Matches Wrangler's player pattern.

---

## Execution Order for Claude Code

When implementing with Claude Code CLI, execute in this order:

1. Create `backend/main.py` with FastAPI app, health endpoint, static file mount
2. Create `frontend/index.html` + `style.css` with the shell layout and design tokens
3. Create `backend/services/job_manager.py` for background task management
4. Create `backend/api/system.py` for device/model info endpoints
5. Wire up the file loader (upload endpoint + frontend component)
6. Port separation tab (biggest pipeline, proves the pattern works)
7. Port remaining tabs one at a time: MIDI → Generate → Mix → Export
8. Port waveform/MIDI visualisers to canvas
9. Remove `gui/` directory and DearPyGUI dependency
10. Update `pyproject.toml`, `CLAUDE.md`, `README.md`
