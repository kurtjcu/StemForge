---
phase: 03-loader-and-pipeline-wiring
plan: "01"
subsystem: models/midi_loader
tags: [tdd, midi, adtof, drums, lazy-loading]
dependency_graph:
  requires:
    - pipelines/adtof_backend.py (AdtofBackend.load/predict/evict)
    - models/basicpitch_loader.py (unchanged)
  provides:
    - MidiModelLoader._ensure_adtof()
    - MidiModelLoader.convert_drum_to_midi()
    - MidiModelLoader.evict_drum_model()
  affects:
    - Any caller of MidiModelLoader.evict() — now also evicts ADTOF
tech_stack:
  added: []
  patterns:
    - Deferred import inside _ensure_adtof() body to preserve lazy loading
    - Structural subtyping via existing AdtofBackendProtocol
key_files:
  created:
    - tests/test_midi_loader_drum.py
  modified:
    - models/midi_loader.py
decisions:
  - "Deferred import `from pipelines.adtof_backend import AdtofBackend` inside method body — consistent with _ensure_whisper() pattern already in file"
  - "evict_drum_model() as a public method allows selective ADTOF eviction without clearing BasicPitch"
  - "evict() chain extended to call evict_drum_model() after _whisper_model = None — full teardown is automatic"
metrics:
  duration_minutes: 2
  completed_date: "2026-03-20"
  tasks_completed: 2
  files_modified: 2
---

# Phase 3 Plan 01: MidiModelLoader ADTOF Extension Summary

**One-liner:** Lazy-loaded AdtofBackend wired into MidiModelLoader via `_ensure_adtof()` with selective `evict_drum_model()` — same pattern as existing Whisper lazy loader.

## What Was Built

Extended `MidiModelLoader` in `models/midi_loader.py` with three new methods that bridge the Phase 2 ADTOF backend into the loader layer:

- `_ensure_adtof()` — loads `AdtofBackend` on first call, caches instance, defers import to call time
- `convert_drum_to_midi(path, *, duration)` — delegates to `backend.predict()`, wraps non-`PipelineExecutionError` exceptions, optionally clips events by duration
- `evict_drum_model()` — evicts ADTOF only, leaving BasicPitch TF model in memory
- Updated `evict()` to call `evict_drum_model()` for complete teardown

## Test Coverage

8 unit tests in `tests/test_midi_loader_drum.py`:

| Test | Verified |
|------|---------|
| `test_adtof_lazy_not_loaded_at_init` | `_adtof_backend is None` at construction |
| `test_ensure_adtof_returns_backend` | `_ensure_adtof()` returns `AdtofBackend` instance; `load()` called |
| `test_ensure_adtof_caches_instance` | Second call returns same object; constructor called once |
| `test_convert_drum_to_midi_calls_predict` | `predict(path)` called; return value passed through |
| `test_convert_drum_to_midi_wraps_exception` | `ValueError` from `predict()` becomes `PipelineExecutionError(pipeline_name="midi")` |
| `test_evict_clears_adtof_backend` | `evict()` calls `_adtof_backend.evict()` and sets to `None` |
| `test_evict_no_error_when_none` | `evict()` with `_adtof_backend=None` does not raise |
| `test_evict_drum_model_leaves_basicpitch` | `_model` (BasicPitch sentinel) untouched after `evict_drum_model()` |

## Deviations from Plan

None — plan executed exactly as written.

## Commits

- `92f3f5d` — `test(03-01): add failing tests for ADTOF lazy loading and drum conversion` (RED)
- `76a56e8` — `feat(03-01): add ADTOF drum transcription support to MidiModelLoader` (GREEN)

## Self-Check: PASSED

- FOUND: models/midi_loader.py
- FOUND: tests/test_midi_loader_drum.py
- FOUND commit: 92f3f5d (RED — failing tests)
- FOUND commit: 76a56e8 (GREEN — implementation)
- All 8 tests pass; 15 adtof_backend tests still pass (no regressions)
