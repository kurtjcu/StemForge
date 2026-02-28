# StemForge → FastAPI + HTML Migration Plan

> **Status: COMPLETE** — All migration stages finished as of commit `6b905cf` (2026-02-28).
> The `gui/` directory and DearPyGUI dependency have been fully removed.

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

## Target Structure (Achieved)

> Deviations from original plan noted with ✎.

```
StemForge/
├── run.py                          # Unified launcher (single server)
├── pyproject.toml                  # Updated deps (drop dearpygui, add fastapi/uvicorn)
├── config.py                       # Unchanged
│
├── backend/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app — routes, static mount
│   ├── api/
│   │   ├── __init__.py
│   │   ├── audio.py                # ✎ Audio serving endpoints (replaces planned audio_server.py service)
│   │   ├── separate.py             # /api/separate — Demucs + BS-Roformer endpoints
│   │   ├── midi.py                 # /api/midi — BasicPitch + VocalMIDI endpoints
│   │   ├── generate.py             # /api/generate — Stable Audio Open endpoints
│   │   ├── mix.py                  # /api/mix — Mix engine endpoints
│   │   ├── export.py               # /api/export — Format conversion endpoints
│   │   └── system.py               # /api/health, /api/device, /api/models, /api/session
│   └── services/
│       ├── __init__.py
│       ├── job_manager.py          # Background task runner + job store
│       ├── session_store.py        # ✎ Thread-safe session state (replaces gui/state.py AppState)
│       └── pipeline_manager.py     # ✎ Lazy-loaded pipeline singletons with GPU lock
│
├── frontend/
│   ├── index.html                  # Single-page app shell with tab navigation
│   ├── style.css                   # Dark DAW theme (Wrangler's design tokens)
│   ├── app.js                      # State management, event bus, tab switching, poll helper
│   └── components/
│       ├── loader.js               # Drag-and-drop upload + file info
│       ├── separate.js             # Separation tab UI
│       ├── midi.js                 # MIDI tab UI
│       ├── generate.js             # Generate tab UI
│       ├── mix.js                  # Mix tab UI
│       ├── export.js               # Export tab UI
│       ├── waveform.js             # wavesurfer.js wrapper (✎ uses wavesurfer.js CDN, not raw canvas)
│       ├── midi-viz.js             # Canvas piano roll visualiser
│       └── audio-player.js         # Global transport bar
│
├── pipelines/                      # Unchanged — all pipeline code
│   ├── demucs_pipeline.py
│   ├── roformer_pipeline.py        # ✎ Named roformer_ not bsroformer_ (matches original codebase)
│   ├── midi_pipeline.py
│   ├── basicpitch_pipeline.py
│   ├── vocal_midi_pipeline.py
│   ├── musicgen_pipeline.py
│   └── resample.py
│
├── models/                         # Unchanged — model registry + loaders
├── utils/                          # Unchanged (paths.py added for output dir constants)
│
├── vendor/                         # ✎ Empty — flashy/torchdiffeq stubs removed (Audiocraft dropped)
│
└── docs/
    ├── GENERATE.md
    ├── FUTURE_PLANS.md
    └── MIGRATION_PLAN.md           # This plan
```

> **Removed:** `gui/` directory — fully deleted after migration.

---

## Migration Stages

### Stage 1: Backend API Layer — DONE

**Created `backend/main.py` and route modules.** Each pipeline has its own router in `backend/api/`.

**What was built:**
- `job_manager.py` — UUID-based job store, daemon threads, progress callbacks
- `session_store.py` — thread-safe `SessionStore` singleton (replaced `gui/state.py` AppState)
- `pipeline_manager.py` — lazy-loaded pipeline singletons with GPU memory lock
- `backend/api/audio.py` — audio streaming, download, waveform, info, and profiler endpoints
- All long-running tasks (separate, midi extract, generate, mix render, export) use background jobs with polling via `GET /api/jobs/{id}`

**Deviation:** WebSocket progress (`WS /ws/jobs/{job_id}`) was not implemented. Polling at 2s intervals proved sufficient and simpler. The planned `audio_server.py` service became `backend/api/audio.py` (API router instead of service — cleaner separation).

### Stage 2: Static Frontend Shell — DONE

**Built:** `index.html` + `style.css` + `app.js` with:
- Dark DAW design tokens (`--bg: #0d0d11`, `--accent: #f59e0b`, etc.)
- Tab-based SPA: Load → Separate → MIDI → Generate → Mix → Export
- Global transport bar with waveform via wavesurfer.js
- Event bus pattern (`appState.on()`/`appState.emit()`) replacing DPG callback wiring

**Deviation:** Waveform uses wavesurfer.js via CDN importmap rather than raw `<canvas>` — better UX with less code.

### Stage 3: File Loader Component — DONE

- Drag-and-drop zone + file picker (`frontend/components/loader.js`)
- `POST /api/upload` + `POST /api/audio/profile` for spectral analysis
- Waveform preview in transport bar
- Audio profiler shows recommended engine/model

### Stage 4: Separation Tab — DONE

- Engine selector (Demucs / BS-Roformer) with model dropdown
- Auto-recommend badge from audio profiler
- `POST /api/separate` → poll progress → stem cards
- Per-stem waveform preview and playback
- Stem visualization via wavesurfer.js (not raw canvas)

### Stage 5: MIDI Tab — DONE

- Stem checkboxes populated from `stemsReady` event
- Musical parameter inputs (BPM, key, time sig)
- `POST /api/midi/extract` → poll progress → per-stem MIDI results
- Canvas piano roll visualizer (`midi-viz.js`)
- FluidSynth preview via `POST /api/midi/render` streamed to browser

### Stage 6: Generate Tab — DONE

- Text prompt, audio/MIDI conditioning selectors
- Duration slider, Vocal Preservation toggle + sub-controls
- `POST /api/generate` → poll progress → audio result card
- Chunked generation (47s chunks) for durations up to 600s

### Stage 7: Mix Tab — DONE

- Per-track cards with instrument selector, volume slider
- Audio and MIDI source types
- `POST /api/mix/render` → FLAC output
- Tracks auto-populated from `stemsReady` + `generateReady` events

### Stage 8: Export Tab — DONE

- Checklist of all available outputs (stems, MIDI, mix, generated audio)
- Format selector: wav/flac/mp3/ogg
- Zip download via `POST /api/export/download-zip`

### Stage 9: Polish + Wrangler Integration Prep — PARTIAL

**Done:**
- Error states and recovery in all pipeline UIs
- `vendor/` directory exists (contains flashy stubs + torchdiffeq)
- macOS MPS support via `pyproject.toml.MAC`

**Not yet done:**
- Keyboard shortcuts
- Responsive layout adjustments
- "Compose" tab/panel slot for Wrangler integration
- Panel plugin pattern documentation

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

## Dependency Changes — DONE

### Removed
- ~~`dearpygui>=1.11.0`~~ — removed
- ~~`screeninfo>=0.8.0`~~ — removed
- `sounddevice` — removed (browser handles all playback)

### Added
- `fastapi>=0.115.0` — in pyproject.toml
- `uvicorn[standard]>=0.30.0` — in pyproject.toml
- `python-multipart>=0.0.9` — in pyproject.toml

### Kept
- All pipeline dependencies unchanged

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

## Execution Order for Claude Code — ALL DONE

All steps were completed in commit `6b905cf`:

1. ~~Create `backend/main.py` with FastAPI app, health endpoint, static file mount~~ — done
2. ~~Create `frontend/index.html` + `style.css` with the shell layout and design tokens~~ — done
3. ~~Create `backend/services/job_manager.py` for background task management~~ — done (+ `session_store.py`, `pipeline_manager.py`)
4. ~~Create `backend/api/system.py` for device/model info endpoints~~ — done (+ `audio.py` for streaming/waveform)
5. ~~Wire up the file loader (upload endpoint + frontend component)~~ — done
6. ~~Port separation tab (biggest pipeline, proves the pattern works)~~ — done
7. ~~Port remaining tabs one at a time: MIDI → Generate → Mix → Export~~ — done
8. ~~Port waveform/MIDI visualisers to canvas~~ — done (wavesurfer.js for waveforms, canvas for MIDI piano roll)
9. ~~Remove `gui/` directory and DearPyGUI dependency~~ — done
10. ~~Update `pyproject.toml`, `CLAUDE.md`, `README.md`~~ — done
