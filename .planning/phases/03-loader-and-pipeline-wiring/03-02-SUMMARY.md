---
phase: 03-loader-and-pipeline-wiring
plan: "02"
subsystem: pipelines
tags: [tdd, midi, drums, routing, adtof, is_drum, eviction]
dependency_graph:
  requires: ["03-01"]
  provides: ["_DRUM_STEM_LABELS", "drum routing branch", "is_drum parameter", "evict_drum_model call"]
  affects: ["pipelines/midi_pipeline.py", "utils/midi_io.py (via is_drum)"]
tech_stack:
  added: []
  patterns: ["TDD red-green", "frozenset label routing", "3-stage progress callbacks", "post-loop eviction"]
key_files:
  created: ["tests/test_midi_pipeline_routing.py"]
  modified: ["pipelines/midi_pipeline.py"]
decisions:
  - "_DRUM_STEM_LABELS frozenset mirrors STEM_IS_DRUM in backend/api/midi.py — single source of truth for routing labels"
  - "3-stage progress callbacks (before loading, after predict, done) prevent progress bar freeze during ADTOF inference"
  - "Post-loop evict_drum_model() inside pipeline run() — AdtofBackend.evict() already handles torch.cuda.empty_cache()"
  - "is_drum=False default on _build_stem_midi() preserves all existing callers without modification"
metrics:
  duration_minutes: 2
  completed_date: "2026-03-20"
  tasks_completed: 2
  files_modified: 2
---

# Phase 03 Plan 02: MidiPipeline Drum Routing Branch Summary

**One-liner:** Drum stems route to ADTOF via `_DRUM_STEM_LABELS` frozenset with `is_drum=True` channel-10 MIDI output and post-loop GPU eviction.

## What Was Built

Added drum stem routing into `MidiPipeline.run()` so stems labeled `"drums"` or `"Drums & percussion"` are transcribed via ADTOF instead of BasicPitch. The pipeline now produces correct GM channel-10 percussion MIDI for drum stems.

### Changes Made

**`pipelines/midi_pipeline.py`** (31 insertions, 2 deletions):
1. `_DRUM_STEM_LABELS` frozenset at module level (after `_VOCAL_STEM_LABELS`) — matches `STEM_IS_DRUM` keys in `backend/api/midi.py`
2. Third routing branch `elif label in _DRUM_STEM_LABELS:` in `run()` — calls `self._loader.convert_drum_to_midi(path, duration=...)`
3. 3-stage `self._report()` calls in drum branch: before loading (`base_pct + 2.0`), after predict (`base_pct + 10.0`), done (`base_pct + (1/total) * 70.0`)
4. `is_drum` parameter on `_build_stem_midi()` — forwarded to `notes_to_midi(..., is_drum=is_drum)`
5. `_build_stem_midi` call site passes `is_drum=(label in _DRUM_STEM_LABELS)`
6. Post-loop `evict_drum_model()` call when any drum stems were present

**`tests/test_midi_pipeline_routing.py`** (212 lines, 9 tests):
- `test_drum_label_routes_to_adt` — "drums" → `convert_drum_to_midi()`
- `test_roformer_drum_label_routes_to_adt` — "Drums & percussion" → `convert_drum_to_midi()`
- `test_vocal_label_not_routed_to_drum` — "vocals" → `convert_vocal_to_midi()`, no regression
- `test_bass_label_not_routed_to_drum` — "bass" → `convert_audio_to_midi()`, no regression
- `test_drum_stem_midi_is_drum_true` — drum stem MIDI has `instruments[0].is_drum == True`
- `test_non_drum_stem_midi_is_drum_false` — non-drum stem MIDI has `instruments[0].is_drum == False`
- `test_drum_path_reports_progress_3_stages` — at least 3 callbacks in drum branch range
- `test_drum_evict_called_after_loop` — `evict_drum_model()` called when drum stems present
- `test_no_drum_evict_when_no_drums` — `evict_drum_model()` NOT called without drum stems

## Test Results

```
32 passed in 1.65s
  - tests/test_midi_pipeline_routing.py: 9 passed (new)
  - tests/test_midi_loader_drum.py:      8 passed (no regression)
  - tests/test_adtof_backend.py:        15 passed (no regression)
```

## Deviations from Plan

None — plan executed exactly as written.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| `_DRUM_STEM_LABELS` mirrors `STEM_IS_DRUM` in `backend/api/midi.py` | Keeps label definitions consistent; both sets must be updated together when new drum stem labels are added |
| 3-stage progress callbacks (before load, after predict, done) | Prevents progress bar from appearing frozen during slow ADTOF model load on first call |
| Post-loop eviction inside `MidiPipeline.run()` | ADTOF model is large; freeing VRAM before next job keeps memory pressure low; `AdtofBackend.evict()` already handles `torch.cuda.empty_cache()` |
| `is_drum=False` default on `_build_stem_midi()` | Zero-impact change — all existing callers (vocal, BasicPitch) continue working without modification |

## Self-Check: PASSED

- `/home/kurt/StemForge/pipelines/midi_pipeline.py` — EXISTS, contains `_DRUM_STEM_LABELS`, `elif label in _DRUM_STEM_LABELS`, `evict_drum_model()`
- `/home/kurt/StemForge/tests/test_midi_pipeline_routing.py` — EXISTS, 9 test functions
- Commit `b744e1c` — RED phase tests
- Commit `a227796` — GREEN phase implementation
