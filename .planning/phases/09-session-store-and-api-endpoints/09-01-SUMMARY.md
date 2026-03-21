---
phase: 09-session-store-and-api-endpoints
plan: "01"
subsystem: session-store-and-api
tags: [tdd, session-store, midi-api, guards, larsnet]
dependency_graph:
  requires: []
  provides: [INFRA-04, SEP-03, GUARD-01, GUARD-02]
  affects: [backend/services/session_store.py, backend/api/midi.py]
tech_stack:
  added: []
  patterns: [thread-safe property, FastAPI dependency override in tests, frozenset guards]
key_files:
  created:
    - tests/test_session_store_drum_sub_stems.py
    - tests/test_midi_api_guards.py
  modified:
    - backend/services/session_store.py
    - backend/api/midi.py
decisions:
  - "GUARD-01 checked before stems filter to ensure 400 fires for larsnet modes even when no drum stem selected"
metrics:
  duration: "~5 min"
  completed: "2026-03-21"
  tasks: 2
  files: 4
---

# Phase 9 Plan 01: Session Store and API Endpoints Summary

**One-liner:** drum_sub_stem_paths isolated field in SessionStore plus LarsNet API guards and GET /api/midi/sub-stems endpoint via TDD

## What Was Built

- `SessionStore._drum_sub_stem_paths` — thread-safe dict field, separate from `stem_paths`, with property/setter/add/clear/to_dict support
- `GET /api/midi/sub-stems` — new endpoint returning per-instrument sub-stem paths from the last LarsNet extraction
- GUARD-01 — pre-condition check in `start_extraction()`: any LarsNet mode requires a drum stem in session (400 with actionable message)
- GUARD-02 — pre-condition check in `start_extraction()`: LarsNet weights must exist on disk (400 with download instructions)
- `_run_midi_extraction()` stores `result.drum_sub_stems` into `session.drum_sub_stem_paths` after each extraction

## Tests

- `tests/test_session_store_drum_sub_stems.py` — 6 INFRA-04 isolation tests (all pass)
- `tests/test_midi_api_guards.py` — 7 GUARD-01/GUARD-02/SEP-03 tests (all pass)
- 17 regression tests (midi pipeline routing + mode dispatch) — unchanged, all pass

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] GUARD-01 moved before stems filter to avoid premature 422**
- **Found during:** Task 2 GREEN phase — `test_guard01_no_drum_stem` got 422 instead of 400
- **Issue:** The `if not stems: raise HTTPException(422, ...)` fired before GUARD-01 because the guard was placed after the filter
- **Fix:** Moved GUARD-01/GUARD-02 checks before the `stems` dict filter so the guard fires first when `drum_mode` is a LarsNet mode
- **Files modified:** backend/api/midi.py

## Self-Check: PASSED

Files created:
- tests/test_session_store_drum_sub_stems.py ✓
- tests/test_midi_api_guards.py ✓

Implementation in committed files:
- backend/services/session_store.py contains `_drum_sub_stem_paths` ✓
- backend/api/midi.py contains `_LARSNET_MODES`, `get_drum_sub_stems`, guards ✓

All 13 tests pass ✓
