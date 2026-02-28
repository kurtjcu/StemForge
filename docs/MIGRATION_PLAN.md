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
- `vendor/` directory removed (flashy/torchdiffeq stubs deleted, Audiocraft dropped)
- macOS MPS support via `pyproject.toml.MAC`

**Not yet done:**
- Keyboard shortcuts
- Responsive layout adjustments

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

6. **Tab-based navigation** — StemForge's sequential workflow (load → separate → midi → synth → compose → mix → export) maps naturally to tabs. Wrangler's 3-column layout becomes the content of the "Compose" tab.

7. **Persistent transport bar** — a global audio player at the bottom (like a DAW transport) that any tab can send audio to. Matches Wrangler's player pattern.

---

## Folding Ace-Step-Wrangler In — Integration Plan

### Overview

ACE-Step-Wrangler is added as a **git submodule** (not inlined), preserving it as an independently runnable project. Its full feature set — Create mode, Rework mode (Reimagine + Fix & Blend), AI lyrics generation, three lyrics tabs (My Lyrics / AI Lyrics / Instrumental), advanced panel — becomes the **Compose** tab within StemForge.

The existing Generate tab is renamed to **Synth** (subtitle: Stable Audio Open).

### Tab Bar After Integration

```
Load · Separate · MIDI · Synth · Compose · Mix · Export
```

### Target Structure

```
StemForge/
├── ACE-Step-Wrangler/              # git submodule (independently runnable)
│   ├── vendor/
│   │   └── ACE-Step-1.5/          # nested submodule — upstream AceStep
│   ├── backend/
│   │   ├── main.py                # Wrangler's standalone server (unused when inside StemForge)
│   │   └── acestep_wrapper.py     # AceStep API wrapper
│   ├── frontend/
│   │   ├── index.html
│   │   ├── style.css
│   │   └── app.js
│   └── run.py                     # Wrangler's standalone launcher (unused when inside StemForge)
│
├── run.py                          # Updated: manages AceStep subprocess + StemForge server
├── pyproject.toml                  # Updated: ace-step as path dependency
│
├── backend/
│   ├── main.py                     # Updated: registers compose router
│   └── api/
│       ├── compose.py              # NEW — Wrangler's main.py adapted as FastAPI router
│       ├── acestep_wrapper.py      # COPIED from Wrangler (or imported from submodule)
│       ├── generate.py             # RENAMED internally: "Generate" → "Synth" in UI references
│       └── ...                     # existing routers unchanged
│
├── frontend/
│   ├── index.html                  # Updated: "Synth" tab + "Compose" tab
│   ├── style.css                   # Updated: merged Wrangler CSS under .compose-tab namespace
│   ├── app.js                      # Updated: new tab, new events
│   └── components/
│       ├── compose.js              # NEW — Wrangler's app.js adapted to render inside tab panel
│       ├── generate.js             # RENAMED UI labels: "Generate" → "Synth"
│       └── ...                     # existing components unchanged
│
└── docs/
    ├── SYNTH.md                    # RENAMED from GENERATE.md
    ├── COMPOSE.md                  # NEW — Compose tab reference (from Wrangler USER_GUIDE.md)
    └── ...
```

---

### Stage 10: Submodule + Dependencies

1. `git submodule add https://github.com/tsondo/ACE-Step-Wrangler.git ACE-Step-Wrangler` at repo root
2. The nested `ACE-Step-1.5/` submodule comes with Wrangler (inside `vendor/`)
3. Add `ace-step` as a path dependency in `pyproject.toml` via `[tool.uv.sources]`, pointing at `ACE-Step-Wrangler/vendor/ACE-Step-1.5/`
4. No new Python deps needed — Wrangler's only runtime deps are FastAPI + uvicorn which StemForge already has
5. Update `.gitmodules` and verify `git clone --recursive` pulls everything

**Deliverable:** `uv sync` installs AceStep's ML stack alongside StemForge's existing deps. Both `StemForge/` and `ACE-Step-Wrangler/` remain independently cloneable and runnable.

### Stage 11: Launcher Changes (`run.py`)

1. Add `--no-acestep` flag (default: AceStep **enabled**)
2. Add `--acestep-port` (default 8001) configurable via `ACESTEP_PORT` env var
3. Make StemForge's own port configurable: `--port` (default 8765) / `STEMFORGE_PORT` env var
4. When AceStep enabled: spawn AceStep API subprocess (adapted from Wrangler's `run.py` subprocess management)
5. **Graceful degradation:** if AceStep subprocess crashes or fails to start, log the error verbosely but keep StemForge running. Set an internal flag that the Compose tab reads to show an error state. StemForge must never exit because AceStep is unhealthy.
6. **Lazy model download:** do not block startup on AceStep model availability. The Compose tab frontend handles the "models not downloaded" state (see Stage 13).
7. Forward `ACESTEP_*` env vars to the subprocess (same passthrough list as Wrangler: `ACESTEP_DEVICE`, `MAX_CUDA_VRAM`, `ACESTEP_VAE_ON_CPU`, `ACESTEP_LM_BACKEND`, `ACESTEP_INIT_LLM`)
8. `--gpu` flag for AceStep GPU selection (same behavior as Wrangler: sets `CUDA_VISIBLE_DEVICES` on the AceStep subprocess only)
9. Update startup banner to show StemForge port, AceStep status (enabled/disabled), AceStep port

**Key difference from Wrangler's `run.py`:** Wrangler exits if either process dies. StemForge must stay alive — only the Compose tab becomes unavailable.

### Stage 12: Backend — Compose Router

1. Copy `ACE-Step-Wrangler/backend/acestep_wrapper.py` → `backend/api/acestep_wrapper.py`
2. Adapt `ACE-Step-Wrangler/backend/main.py` → `backend/api/compose.py` as a FastAPI router (not standalone app)
3. Mount all Wrangler endpoints under `/api/compose/` prefix:
   - `POST /api/compose/generate` — Create mode + Rework mode generation
   - `POST /api/compose/generate-lyrics` — AI lyrics via AceStep LM
   - `POST /api/compose/upload-audio` — Rework mode audio upload
   - `POST /api/compose/format-duration` — Auto duration estimation
   - `GET /api/compose/status/{job_id}` — Generation progress polling
   - All other Wrangler endpoints as needed
4. Add `GET /api/compose/health` — returns AceStep subprocess status: `running`, `crashed`, `disabled` (--no-acestep), or `models-not-downloaded`
5. Register the compose router in `backend/main.py`
6. **Error handling:** all compose endpoints return clear error responses when AceStep is unavailable, with enough detail to diagnose (e.g. "AceStep process exited with code 1 — check logs for CUDA errors")

### Stage 13: Frontend — Tab Rename + Compose Tab

1. Rename "Generate" tab → **"Synth"** in `index.html`, `app.js`, and `generate.js`. Add subtitle "Stable Audio Open" in the tab panel header area.
2. Add **"Compose"** tab after Synth with subtitle "AceStep". Add the tab panel container in `index.html`.
3. Create `frontend/components/compose.js` — adapted from Wrangler's `app.js`:
   - Renders Wrangler's full 3-column layout inside the Compose tab panel
   - All Wrangler UI features: Create mode (style panel, 3 lyrics tabs, controls, advanced panel), Rework mode (audio input, region selection, reimagine/fix & blend, waveform editor), output cards with playback/download
   - Wrangler's Now Playing bar maps to StemForge's global transport bar (use `appState.emit` to send audio to the transport)
4. Merge Wrangler's CSS into StemForge's `style.css`:
   - Namespace Wrangler-specific classes under `.compose-tab` to avoid collisions
   - Shared design tokens already match — verify and resolve any drift
   - Wrangler's 3-column grid must use relative widths (not viewport-based) to work within a tab panel
5. **AceStep unavailable states in the Compose tab:**
   - `disabled`: "AceStep is not enabled. Start StemForge without `--no-acestep` to use Compose."
   - `crashed`: "AceStep encountered an error and is not running. Check the terminal for details." (with enough log context to diagnose)
   - `models-not-downloaded`: "AceStep models (~10 GB) need to be downloaded before first use. [Download Now]" — clicking triggers model download with a progress indicator
   - `running`: normal Compose UI

### Stage 14: Cross-Tab Integration — Compose → Separate

1. When AceStep generation completes in Compose, emit: `appState.emit("composeReady", {path, metadata})`
2. Each Compose result card gets a **"Send to Separate →"** button
3. Clicking it:
   - Uploads the generated audio to StemForge's session store via `POST /api/upload`
   - Switches to the Separate tab
   - Triggers the same load flow as if the user had dropped a file on the Load tab
4. The Load tab also shows a "Recently composed" section listing available Compose outputs

### Stage 15: Cross-Tab Integration — Compose → Mix

1. Mix tab listens to `composeReady` event
2. Composed tracks auto-appear in Mix with:
   - **Color: white** — new CSS variable `--stem-compose: #ffffff`
   - **Label:** "Composed: [prompt excerpt or song title]"
   - Source type: audio
   - Default: enabled, volume 1.0
3. Multiple Compose generations accumulate as separate Mix tracks (user can toggle each on/off)
4. Synth outputs keep their own color and labeling:
   - **Color: white** — CSS variable `--stem-synth: #ffffff`
   - **Label:** "Synth: [prompt excerpt]"

**Mix tab track color scheme:**

| Source | Color | CSS Variable | Label prefix |
|--------|-------|-------------|--------------|
| Audio stems (Separate) | Green | `--stem-audio` | stem name (vocals, drums, etc.) |
| MIDI stems | Purple | `--stem-midi` | stem name + "(MIDI)" |
| Synth outputs (SAO) | White | `--stem-synth` | "Synth: [prompt]" |
| Compose outputs (AceStep) | White | `--stem-compose` | "Composed: [title]" |

### Stage 16: Documentation Updates

1. Rename `docs/GENERATE.md` → `docs/SYNTH.md` — update all internal references and content to use "Synth" naming
2. Create `docs/COMPOSE.md` — Compose tab reference adapted from Wrangler's `docs/USER_GUIDE.md`, covering Create mode, Rework mode, AI lyrics, advanced panel, and cross-tab integration features (Send to Separate, Mix integration)
3. Update `CLAUDE.md`:
   - New tab names (Synth, Compose)
   - Compose architecture: submodule, AceStep subprocess, compose router
   - New events: `composeReady`
   - AceStep subprocess lifecycle and error handling
   - Port configuration
4. Update `README.md`:
   - Installation: `git clone --recursive` now pulls Wrangler + AceStep submodules
   - New `--no-acestep` and `--acestep-port` flags
   - Updated tab descriptions
   - Updated project structure tree
5. Update this file (`MIGRATION_PLAN.md`): mark Wrangler integration stages as complete
6. Update `pyproject.toml` and `pyproject.toml.MAC` with AceStep path dependency

### Stage 17: Polish + Testing

1. Export tab gains Compose outputs in its artifact checklist
2. Keyboard shortcuts for Synth and Compose tabs
3. **Test: full creative loop** — Compose a song → Send to Separate → extract MIDI → remix in Mix → Export
4. **Test: `--no-acestep` mode** — everything except Compose works normally, Compose tab shows disabled state
5. **Test: AceStep crash recovery** — kill AceStep process mid-operation, verify StemForge stays running, Compose tab shows error state with useful diagnostics
6. **Test: model download flow** — fresh install with no cached models, verify download prompt appears and works
7. **Test: port configuration** — verify `--port`, `--acestep-port`, `STEMFORGE_PORT`, `ACESTEP_PORT` all work
8. **CSS verification:** Wrangler's 3-column layout renders correctly within the tab panel container at various viewport sizes

---

## Risk Areas

### CSS Fluid Width
Wrangler's 3-column grid assumes full viewport width. Inside a StemForge tab panel, it needs relative sizing. Namespace under `.compose-tab` and convert fixed viewport units to container-relative. May require iteration.

### Port Management
Three ports in play: StemForge (8765), AceStep API (8001). All configurable via CLI flags and env vars. Clear startup logging is essential.

### Dependency Conflicts
AceStep pins specific PyTorch versions. StemForge's existing pipelines also need PyTorch. Both target CUDA 13.0 — verify pinned versions are compatible during Stage 10.

### GPU Contention
AceStep runs as a separate process and is not aware of StemForge's in-process GPU lock (`pipeline_manager.py`). Running a Compose generation while a Separate or Synth job is active could cause OOM. Document this limitation. Users should not run GPU-intensive operations across tabs simultaneously.

**Future consideration: multi-GPU support.** For systems with multiple GPUs, a future iteration could assign AceStep and StemForge's in-process pipelines to different devices, enabling truly simultaneous Compose + Separate/Synth workflows. This would require:
- Device assignment configuration (which GPU for AceStep, which for in-process pipelines)
- UI indication of which device each pipeline is using
- Automatic detection of available GPUs and intelligent default assignment

This is out of scope for the initial integration but worth pursuing once the single-GPU workflow is stable and tested.
