# SFX Stem Builder — Backend Specification

## Overview

Add an SFX Stem Builder to the Synth tab. It lets users create a blank audio canvas (matching a reference stem's duration/sample rate or a manually specified length), place multiple Synth-generated sound effect clips onto it at specific timestamps with per-clip volume and fade controls, and render the result as a standard WAV stem available in the Mix tab.

The feature is **non-destructive**: a JSON manifest is the source of truth. The WAV is re-rendered from the manifest on every edit. Users can move, replace, remove, and adjust clips after initial placement.

---

## 1. Data Model

### 1.1 SFX Manifest (JSON)

Persisted alongside the rendered WAV in the SFX output directory.

```json
{
  "id": "sfx_a1b2c3",
  "name": "Rain & Thunder",
  "duration_ms": 240000,
  "sample_rate": 44100,
  "channels": 2,
  "reference_stem": "/path/to/vocals.wav",
  "apply_limiter": false,
  "placements": [
    {
      "id": "p1",
      "clip_path": "/path/to/rain_loop.wav",
      "clip_name": "rain_loop.wav",
      "start_ms": 0,
      "volume": 0.8,
      "fade_in_ms": 500,
      "fade_out_ms": 2000,
      "fade_curve": "cosine"
    },
    {
      "id": "p2",
      "clip_path": "/path/to/thunder.wav",
      "clip_name": "thunder.wav",
      "start_ms": 15000,
      "volume": 1.2,
      "fade_in_ms": 50,
      "fade_out_ms": 800,
      "fade_curve": "linear"
    }
  ]
}
```

**Field rules:**
- `id`: Auto-generated, `sfx_` + 6-char hex.
- `duration_ms`: Set at creation, immutable after (canvas length).
- `sample_rate` / `channels`: Copied from reference stem, or default 44100 / 2 if manual duration.
- `reference_stem`: Path to reference file (nullable if manual duration).
- `apply_limiter`: Boolean, off by default.
- `placements[].id`: Auto-generated, `p` + incrementing int or short hex.
- `placements[].volume`: Float, 0.0–2.0 (1.0 = unity gain).
- `placements[].fade_curve`: `"linear"` or `"cosine"`.
- `placements[].start_ms`: Must be >= 0 and < `duration_ms`.
- Clips may overlap. No enforcement of non-overlap.

### 1.2 Output Directory

Add `SFX_DIR` to `utils/paths.py`:

```python
SFX_DIR = OUTPUT_BASE / "sfx"
```

Each SFX stem gets a subdirectory: `SFX_DIR / manifest_id /` containing:
- `manifest.json` — the source of truth
- `rendered.wav` — the current composite render

### 1.3 Session Store Changes

Add to `SessionStore`:

```python
self._sfx_manifests: dict[str, dict] = {}  # id → manifest dict
```

With property + setter following existing patterns. Add helper methods:

- `add_sfx_manifest(manifest: dict) -> None`
- `get_sfx_manifest(sfx_id: str) -> dict | None`
- `remove_sfx_manifest(sfx_id: str) -> bool`
- `sfx_manifest_ids -> list[str]` (property)

Update `clear()` to reset `_sfx_manifests`.
Update `to_dict()` to include `sfx_manifests` summary (ids + names, not full placements).

---

## 2. Rendering Engine

Create `backend/services/sfx_renderer.py`.

### 2.1 Core Render Function

```
render_sfx(manifest: dict) -> Path
```

**Algorithm:**
1. Create a numpy zeros array: shape `(channels, int(duration_ms / 1000 * sample_rate))`.
2. For each placement in `manifest["placements"]`:
   a. Load clip audio via `utils.audio_io.read_audio`. Resample to manifest's `sample_rate` if mismatched.
   b. Convert to correct channel count (mono→stereo duplicate, or stereo→mono average).
   c. Apply fade-in ramp to first `fade_in_ms` worth of samples.
   d. Apply fade-out ramp to last `fade_out_ms` worth of samples.
   e. Multiply by `volume`.
   f. Calculate sample offset from `start_ms`.
   g. If clip extends beyond canvas duration, truncate the clip.
   h. Add (sum) clip samples into the canvas array at the offset.
3. If `apply_limiter` is true, apply soft clipping:
   - `np.tanh(canvas)` is sufficient as a simple soft clipper.
   - Alternatively: ceiling at 1.0 with `np.clip` after a gentle compression curve.
   - Start with `np.tanh` — it's smooth and predictable.
4. Write canvas to WAV via `utils.audio_io.write_audio`.
5. Return the output path.

### 2.2 Fade Curves

```python
def make_fade(length_samples: int, curve: str = "cosine") -> np.ndarray:
    if curve == "cosine":
        return (1 - np.cos(np.linspace(0, np.pi / 2, length_samples))) 
    else:  # linear
        return np.linspace(0, 1, length_samples)
```

Fade-in: multiply clip `[:fade_samples]` by `make_fade(fade_samples, curve)`.
Fade-out: multiply clip `[-fade_samples:]` by `make_fade(fade_samples, curve)[::-1]`.

### 2.3 Waveform Data

After rendering, also generate waveform peak data (reuse the pattern from `GET /api/audio/waveform`) and cache it so the frontend can display the canvas waveform without a separate call.

---

## 3. API Endpoints

New router: `backend/api/sfx.py`, prefix `/api/sfx`, registered in `backend/main.py`.

### 3.1 Create SFX Stem

```
POST /api/sfx/create
```

**Request body:**
```json
{
  "name": "Rain & Thunder",
  "mode": "reference",
  "reference_stem_path": "/path/to/vocals.wav",
  "duration_ms": null
}
```

- `mode`: `"reference"` or `"manual"`.
- If `"reference"`: read duration/sample_rate/channels from the file at `reference_stem_path`. Store the path in the manifest.
- If `"manual"`: use provided `duration_ms`, default sample_rate=44100, channels=2.

**Response:**
```json
{
  "sfx_id": "sfx_a1b2c3",
  "duration_ms": 240000,
  "sample_rate": 44100,
  "channels": 2,
  "reference_stem": "/path/to/vocals.wav"
}
```

**Logic:**
1. Validate inputs.
2. Build initial manifest (empty placements).
3. Save manifest JSON to `SFX_DIR / sfx_id / manifest.json`.
4. Store in session.
5. Return summary.

### 3.2 Get SFX Manifest

```
GET /api/sfx/{sfx_id}
```

**Response:** Full manifest JSON including all placements.

Also returns `"rendered_path"` and `"waveform"` (peak data) if a render exists.

### 3.3 List SFX Stems

```
GET /api/sfx
```

**Response:**
```json
{
  "sfx_stems": [
    {
      "sfx_id": "sfx_a1b2c3",
      "name": "Rain & Thunder",
      "duration_ms": 240000,
      "placement_count": 2,
      "has_render": true
    }
  ]
}
```

### 3.4 Add Placement

```
POST /api/sfx/{sfx_id}/placements
```

**Request body:**
```json
{
  "clip_path": "/path/to/rain_loop.wav",
  "start_ms": 0,
  "volume": 0.8,
  "fade_in_ms": 500,
  "fade_out_ms": 2000,
  "fade_curve": "cosine"
}
```

**Logic:**
1. Validate `sfx_id` exists, `clip_path` exists, `start_ms` is valid.
2. Auto-populate `clip_name` from filename.
3. Generate placement `id`.
4. Append to manifest placements.
5. Save manifest.
6. Re-render WAV (call `render_sfx`).
7. Return updated manifest + waveform data.

**Response:** Full updated manifest with waveform peak data.

### 3.5 Update Placement

```
PUT /api/sfx/{sfx_id}/placements/{placement_id}
```

**Request body** (all fields optional — only provided fields are updated):
```json
{
  "clip_path": "/path/to/new_clip.wav",
  "start_ms": 5000,
  "volume": 1.0,
  "fade_in_ms": 100,
  "fade_out_ms": 500,
  "fade_curve": "linear"
}
```

**Logic:**
1. Find placement by id.
2. Update provided fields.
3. Save manifest.
4. Re-render.
5. Return updated manifest + waveform data.

### 3.6 Remove Placement

```
DELETE /api/sfx/{sfx_id}/placements/{placement_id}
```

**Logic:**
1. Remove placement from manifest.
2. Save manifest.
3. Re-render (or write silence if no placements remain).
4. Return updated manifest + waveform data.

### 3.7 Update SFX Settings

```
PATCH /api/sfx/{sfx_id}
```

**Request body** (all optional):
```json
{
  "name": "Updated Name",
  "apply_limiter": true
}
```

**Logic:** Update manifest-level settings, save, re-render if `apply_limiter` changed.

### 3.8 Send to Mix

```
POST /api/sfx/{sfx_id}/send-to-mix
```

**Logic:**
1. Verify render exists.
2. Add as a `TrackState` to session mix tracks:
   - `track_id`: `"sfx-{sfx_id}"`
   - `label`: manifest `name`
   - `source`: `"audio"`
   - `path`: rendered WAV path
3. Skip if track_id already exists in mix (idempotent).

**Response:**
```json
{
  "track_id": "sfx-sfx_a1b2c3",
  "label": "Rain & Thunder",
  "status": "added"
}
```

### 3.9 Delete SFX Stem

```
DELETE /api/sfx/{sfx_id}
```

**Logic:**
1. Remove from session.
2. Remove corresponding mix track if present.
3. Delete the `SFX_DIR / sfx_id /` directory.

### 3.10 Preview / Stream

```
GET /api/sfx/{sfx_id}/stream
```

Serves the rendered WAV for playback. Reuse the `FileResponse` pattern from `audio.py`. Add the SFX directory to `_ALLOWED_ROOTS` in `audio.py`, or serve directly from this router.

### 3.11 Reference Waveform

```
GET /api/sfx/{sfx_id}/reference-waveform
```

Returns waveform peak data for the reference stem (if one exists). Uses the same downsampling logic as `GET /api/audio/waveform`.

---

## 4. Available Clips Source

The frontend needs to know which Synth-generated clips are available for placement. These live in `MUSICGEN_DIR` (the Stable Audio output directory).

```
GET /api/sfx/available-clips
```

**Logic:** List all `.wav` files in `MUSICGEN_DIR`, return filename + path + duration for each.

**Response:**
```json
{
  "clips": [
    {
      "path": "/path/to/output/musicgen/rain_20250301_143022.wav",
      "name": "rain_20250301_143022.wav",
      "duration_ms": 30000
    }
  ]
}
```

---

## 5. Implementation Checklist

### Files to Create
1. `backend/api/sfx.py` — New API router (all endpoints above)
2. `backend/services/sfx_renderer.py` — Render engine (render_sfx, make_fade)

### Files to Modify
1. `utils/paths.py` — Add `SFX_DIR`
2. `backend/services/session_store.py` — Add SFX manifest storage, update `clear()` and `to_dict()`
3. `backend/main.py` — Import and register `sfx.router`, ensure `SFX_DIR` is created at startup
4. `backend/api/audio.py` — Add `SFX_DIR` to `_ALLOWED_ROOTS` if streaming through the shared audio endpoint

### Existing Patterns to Follow
- **Router structure**: Match `backend/api/generate.py` and `backend/api/mix.py` patterns.
- **Job manager**: Rendering is fast (array math, no ML inference), so run synchronously — no need for background jobs unless renders take > 1s on large files. If needed, use `job_manager` like separation does.
- **Error handling**: Use `HTTPException` with 400/404/422 codes consistently.
- **Path validation**: Reuse `_validate_path()` pattern from `audio.py` for clip_path inputs.
- **Session access**: Use the lock-protected property pattern from existing session store code.

### Testing Plan
1. **Create from reference**: POST `/api/sfx/create` with mode=reference pointing to an existing stem. Verify manifest JSON is written, duration matches source.
2. **Create manual**: POST with mode=manual, duration_ms=60000. Verify defaults.
3. **Add placement**: Add a clip, verify manifest updated, WAV rendered, waveform data returned.
4. **Overlap**: Add two clips at overlapping timestamps, verify they sum correctly.
5. **Update placement**: Move a clip (change start_ms), verify re-render.
6. **Replace clip**: Change clip_path on existing placement, verify re-render.
7. **Remove placement**: Delete a placement, verify manifest and render updated.
8. **Limiter toggle**: Enable limiter, verify render changes.
9. **Send to mix**: Verify track appears in `/api/mix/tracks`.
10. **Delete SFX stem**: Verify cleanup of files, session, and mix track.
11. **Edge cases**: Clip extends past canvas end (should truncate). Zero-length fade. Volume at 0.0 and 2.0. Empty placements list (should render silence).

---

## 6. Notes for Frontend (Later Phase)

After the backend is tested and working, the frontend will need:
- A new "SFX Stem Builder" section in the Synth tab (below existing generation controls)
- Reference waveform display (read-only, for visual alignment)
- Canvas waveform display (updates after each edit)
- Placement list with controls per clip (start time, volume slider+numeric, fade in/out, fade curve dropdown, remove button)
- Dropdown of available clips from `GET /api/sfx/available-clips`
- Limiter toggle
- "Send to Mix" button
- Preview/playback button

The frontend is a separate implementation phase — do not build it during backend work.
