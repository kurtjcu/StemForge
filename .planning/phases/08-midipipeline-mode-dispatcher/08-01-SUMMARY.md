---
phase: 08-midipipeline-mode-dispatcher
plan: "01"
subsystem: pipelines/midi_pipeline
tags: [tdd, midi, drum-mode, larsnet, dispatch]
dependency_graph:
  requires: [07-01]
  provides: [drum-mode-dispatch, MidiConfig.drum_mode, MidiResult.drum_sub_stems]
  affects: [pipelines/midi_pipeline.py, backend/api/midi.py]
tech_stack:
  added: []
  patterns: [TDD red-green, mode dispatch, module-level import for patchability]
key_files:
  created:
    - tests/test_midi_pipeline_mode_dispatch.py
  modified:
    - pipelines/midi_pipeline.py
decisions:
  - "OnsetBackend imported at module level (not lazily) so unittest.mock.patch can target pipelines.midi_pipeline.OnsetBackend"
  - "drum_sub_stems defaults to empty dict in MidiResult for backward compatibility"
  - "MidiConfig drum_mode validation raises InvalidInputError at construction time, not at run() time"
metrics:
  duration: "~3.5 min"
  completed_date: "2026-03-21"
  tasks_completed: 2
  files_modified: 2
---

# Phase 08 Plan 01: MidiPipeline Three-Mode Drum Dispatch Summary

Three-mode drum MIDI dispatch in MidiPipeline with TDD: `adtof_only` (regression-safe), `larsnet_adtof` (LarsNet separation + ADTOF transcription), and `larsnet_onset` (LarsNet separation + energy-based onset detection per sub-stem), all gated by `MidiConfig.drum_mode` validation.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | RED — Write failing tests for drum mode dispatch | b734d94 | tests/test_midi_pipeline_mode_dispatch.py |
| 2 | GREEN — Implement drum mode dispatch in MidiPipeline | 59b8d09 | pipelines/midi_pipeline.py |

## What Was Built

### `tests/test_midi_pipeline_mode_dispatch.py` (new, 213 lines)
Six test functions covering all mode dispatch behaviors:
- `test_adtof_only_mode_regression` — only `convert_drum_to_midi()` called; `drum_sub_stems == {}`
- `test_larsnet_adtof_calls_loader` — `convert_drum_to_midi_with_larsnet()` called; 5-key `drum_sub_stems`
- `test_larsnet_onset_routes_to_onset_backend` — `separate_drums()` + `OnsetBackend.detect()` x5 + `evict_larsnet()`; 5-key `drum_sub_stems`
- `test_drum_sub_stems_populated_for_larsnet_modes` — all three modes tested in one function
- `test_invalid_drum_mode_raises` — `MidiConfig(drum_mode="bogus")` raises `InvalidInputError`
- `test_default_drum_mode_is_adtof_only` — `MidiConfig().drum_mode == "adtof_only"`

### `pipelines/midi_pipeline.py` (modified)
- `_VALID_DRUM_MODES: frozenset[str]` — `{"adtof_only", "larsnet_adtof", "larsnet_onset"}`
- `_LARSNET_STEM_TO_GM_NOTE: dict[str, int]` — GM note mapping for 5 LarsNet sub-stem classes
- `_load_drum_tensor(path) -> torch.Tensor` — load WAV as stereo float32 at 44100 Hz
- `MidiConfig.drum_mode: str` — validated at construction; default `"adtof_only"`
- `MidiResult.drum_sub_stems: dict[str, Path]` — populated in LarsNet modes; empty dict otherwise
- `MidiPipeline.run(stems, *, job_id=None)` — extended signature with UUID fallback
- Three-mode drum branch: `adtof_only` / `larsnet_adtof` / `larsnet_onset` dispatch

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Module-level import required for OnsetBackend patchability**
- **Found during:** Task 2 GREEN run
- **Issue:** Plan specified lazy import (`from pipelines.onset_backend import OnsetBackend` inside the larsnet_onset branch). `unittest.mock.patch("pipelines.midi_pipeline.OnsetBackend")` requires the attribute to exist in the module namespace, so the lazy import caused `AttributeError` on patch entry.
- **Fix:** Moved `from pipelines.onset_backend import OnsetBackend` to the module-level imports block; removed the redundant local import from the larsnet_onset branch.
- **Files modified:** `pipelines/midi_pipeline.py`
- **Commit:** 59b8d09

## Verification

```
tests/test_midi_pipeline_mode_dispatch.py ......   [ 6 passed]
tests/test_midi_pipeline_routing.py .........      [ 9 passed]
15 passed in 1.14s
```

All 6 new mode dispatch tests green. All 9 existing routing regression tests still pass.

## Self-Check: PASSED

- `tests/test_midi_pipeline_mode_dispatch.py` exists with 6 test functions
- `pipelines/midi_pipeline.py` contains `_VALID_DRUM_MODES`, `_LARSNET_STEM_TO_GM_NOTE`, `_load_drum_tensor`, `drum_mode` in MidiConfig, `drum_sub_stems` in MidiResult, three-mode dispatch
- Commits b734d94 and 59b8d09 verified in git log
