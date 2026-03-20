# Phase 3: Loader and Pipeline Wiring - Context

**Gathered:** 2026-03-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Wire the ADTOF backend (Phase 2) into StemForge's MIDI pipeline so that drum stems are automatically routed to drum transcription instead of BasicPitch. Delivers: extended `MidiModelLoader` with ADTOF support, `_DRUM_STEM_LABELS` routing branch in `MidiPipeline.run()`, lazy-loaded drum model with `evict()` support, progress callbacks during drum transcription, and sample rate validation for non-44100 Hz inputs.

</domain>

<decisions>
## Implementation Decisions

### Drum Stem Routing Labels
- `_DRUM_STEM_LABELS: frozenset[str] = frozenset({"drums", "Drums & percussion"})` — mirrors `STEM_IS_DRUM` in `backend/api/midi.py`
- Follows exact same pattern as `_VOCAL_STEM_LABELS` in `midi_pipeline.py` (frozenset membership check)
- Routing priority: vocal labels checked first (existing), then drum labels, then BasicPitch fallback
- Labels are case-sensitive — `"drums"` (Demucs) and `"Drums & percussion"` (BS-Roformer) are the exact output names

### Loader Architecture
- Extend existing `MidiModelLoader` in `models/midi_loader.py` with ADTOF support — keeps the one existing `midi` pipeline slot and eviction chain intact
- Add `_adtof_backend` field, `_ensure_adtof()` lazy-load method, `convert_drum_to_midi()`, and `evict_drum_model()`
- `convert_drum_to_midi(path)` handles: `AdtofBackend.predict()` -> return `list[NoteEvent]` (sample rate must be 44100 Hz; Phase 2 backend asserts this)
- Lazy-loaded inside `MidiModelLoader` when a drum stem is first encountered — NOT loaded at pipeline `load_model()` time
- Pattern mirrors `_ensure_whisper()` in `MidiModelLoader` — load on first use, cache for subsequent calls
- `MidiModelLoader.evict()` chain includes ADTOF backend eviction

### VRAM Eviction
- `AdtofBackend.evict()` already calls `torch.cuda.empty_cache()` when the model was on a CUDA device (verified in `pipelines/adtof_backend.py:186-198`)
- The in-pipeline eviction chain is: `MidiPipeline.run()` calls `self._loader.evict_drum_model()` after drum stems loop -> `MidiModelLoader.evict_drum_model()` calls `self._adtof_backend.evict()` -> `AdtofBackend.evict()` moves model to CPU and calls `torch.cuda.empty_cache()`
- No modification to `backend/api/midi.py` is needed — the eviction happens inside the pipeline run, not after the job completes
- This is functionally equivalent to `pipeline_manager.evict()` for VRAM release, but more targeted (only evicts ADTOF, not BasicPitch)

### Progress Callback Wiring
- Drum transcription uses the same `_report(pct)` callback pattern as the BasicPitch path
- Three stages: before loading (~base_pct + 2.0), after loading (~base_pct + 10.0), after inference (~base_pct + per-stem fraction)
- Progress percentages allocated within the per-stem loop in `MidiPipeline.run()` — same as existing vocal/pitched paths
- No new callback mechanism needed — reuse `self._progress_callback`

### notes_to_midi() Integration
- Drum stem MIDI built via `notes_to_midi(notes, is_drum=True, ...)` — uses Phase 1's `is_drum` parameter
- Per-stem MIDI data stored in `result.stem_midi_data` like all other stems
- `_build_stem_midi()` in MidiPipeline passes `is_drum=True` for drum stems

### Claude's Discretion
- Exact progress percentage breakpoints within the drum transcription stage
- Log message wording for drum routing decisions
- Whether to add `drum_loader` to `pipeline_manager.py` cache or keep it pipeline-internal

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Pipeline routing pattern
- `pipelines/midi_pipeline.py` §L52 — `_VOCAL_STEM_LABELS` frozenset and routing check at §L312 — the exact pattern to replicate for drum stems
- `pipelines/midi_pipeline.py` §L245 — `MidiPipeline.run()` — the method where the drum branch must be inserted
- `pipelines/midi_pipeline.py` §L415 — `_build_stem_midi()` — needs `is_drum` parameter threading

### ADTOF backend (Phase 2 deliverable)
- `pipelines/adtof_backend.py` — `AdtofBackend` with `load(device)`, `predict(audio_path)`, `evict()` — the backend to wrap

### Existing loader pattern
- `models/midi_loader.py` — `MidiModelLoader` with `_ensure_whisper()` lazy-load pattern at §L106 — reference for ADTOF lazy loading
- `models/midi_loader.py` §L132 — `convert_audio_to_midi()` — reference for `convert_drum_to_midi()` signature

### Backend API integration
- `backend/api/midi.py` §L83 — `STEM_IS_DRUM` dict — label-to-drum mapping, must stay in sync with `_DRUM_STEM_LABELS`
- `backend/api/midi.py` §L133 — `_run_midi_extraction()` — job runner that calls pipeline, auto-adds mix tracks with `is_drum` flag

### Pipeline manager
- `backend/services/pipeline_manager.py` §L272 — `_get_or_create("midi")` — MidiPipeline singleton; drum loader lives inside it, not as separate pipeline

### Phase 1 utilities
- `utils/midi_io.py` — `notes_to_midi()` with `is_drum` parameter, `NoteEvent` type alias
- `utils/drum_map.py` — `ADTOF_5CLASS_GM_NOTE` mapping (reference, not directly used in Phase 3)

### Requirements
- `.planning/REQUIREMENTS.md` — PIPE-01, PIPE-02, PIPE-03, PIPE-04

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `_VOCAL_STEM_LABELS` pattern — exact template for `_DRUM_STEM_LABELS`
- `MidiModelLoader._ensure_whisper()` — lazy-load-on-first-use pattern for ADTOF extension
- `AdtofBackend` (Phase 2) — complete, tested, ready to wrap
- `notes_to_midi(is_drum=True)` — Phase 1 deliverable, handles channel 10 routing

### Established Patterns
- Frozenset for stem label sets (vocal, drum)
- Extending existing loader class with new backend (whisper pattern)
- Lazy import of heavy ML libraries inside method bodies
- Pipeline `_report(pct)` for progress callbacks
- `try/except` wrapping with domain-specific error classes

### Integration Points
- `MidiPipeline.run()` — add drum routing branch between vocal and BasicPitch paths
- `MidiPipeline._build_stem_midi()` — pass `is_drum=True` for drum stems
- `models/midi_loader.py` — extend with `_adtof_backend`, `_ensure_adtof()`, `convert_drum_to_midi()`, `evict_drum_model()`
- `MidiModelLoader.evict()` — chain includes ADTOF backend eviction

</code_context>

<specifics>
## Specific Ideas

- Routing order: vocals -> drums -> BasicPitch (pitched instruments) — three-way branch in the stem loop
- ADTOF integration extends MidiModelLoader, not a separate loader class — keeps the single `midi` pipeline slot and eviction chain intact
- `_build_stem_midi()` already calls `notes_to_midi()` — just needs `is_drum` threaded through

</specifics>

<deferred>
## Deferred Ideas

- ADT model selector in MIDI panel (REG-03) — Phase 4
- Onset threshold UI slider (CTRL-01) — v2
- Electronic music accuracy caveats in UI — Phase 4
- Consolidating `_STEM_IS_DRUM` dicts across files — cleanup phase

</deferred>

---

*Phase: 03-loader-and-pipeline-wiring*
*Context gathered: 2026-03-20*
*Updated: 2026-03-20 — reconciled Loader Architecture decision to match RESEARCH.md recommendation (extend MidiModelLoader instead of new DrumMidiLoader class)*
