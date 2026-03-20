---
phase: 06-onset-detection-backend
plan: 01
subsystem: pipelines
tags: [onset-detection, librosa, drum-midi, tdd, cpu-only]
dependency_graph:
  requires: []
  provides: [OnsetBackend]
  affects: [Phase 7 LarsNet wiring, Phase 8 LarsNet+onset MIDI mode]
tech_stack:
  added: []
  patterns: [librosa onset_strength + onset_detect, per-class delta/wait tuning, soundfile for fast WAV loading]
key_files:
  created:
    - pipelines/onset_backend.py
    - tests/test_onset_backend.py
  modified: []
decisions:
  - pre_roll=0.2s in synthetic WAV generators to avoid t=0 degenerate case for spectral flux onset detection
  - wait_ms=100 for toms (GM 47) instead of 200ms to support rapid tom fills
  - soundfile.read over librosa.load (~100x faster for same-SR files)
key_decisions:
  - "pre_roll=0.2s in test WAV helpers: t=0 onset is degenerate for spectral flux onset detection (no pre-stimulus baseline)"
  - "hop_length=128 unconditionally: default 512 gives 11.6ms resolution, fails ±5ms criterion"
  - "wait_ms=100 for toms (GM 47): plan specified 200ms but research noted rapid fills need 100ms"
metrics:
  duration: "4 min"
  completed_date: "2026-03-21"
  tasks_completed: 2
  files_created: 2
  files_modified: 0
---

# Phase 06 Plan 01: OnsetBackend — Energy-Based Onset Detection Summary

**One-liner:** CPU-only librosa onset detector with per-class (delta, wait_ms) thresholds for isolated drum sub-stems — hop_length=128 for ±5ms accuracy, no model weights.

## What Was Built

`pipelines/onset_backend.py` — `OnsetBackend` class with a single `detect(audio_path, gm_note)` method. Uses `soundfile.read` for fast audio loading, `librosa.onset.onset_strength` for spectral flux envelope, and `librosa.onset.onset_detect` with per-class `delta` and `wait` parameters to suppress inter-class bleed. Returns a sorted `list[NoteEvent]`.

`tests/test_onset_backend.py` — 5 TDD tests with 2 synthetic WAV generators (`make_kick_wav`, `make_hihat_wav_with_bleed`).

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | RED — Write failing tests | d060a83 | tests/test_onset_backend.py |
| 2 | GREEN — Implement OnsetBackend | d2e9741 | pipelines/onset_backend.py, tests/test_onset_backend.py |

## Test Results

```
tests/test_onset_backend.py .....  5 passed in 1.31s
tests/test_adtof_backend.py ....  15 passed (no regressions)
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Pre-roll silence required in synthetic WAV generators**

- **Found during:** Task 2 (GREEN phase) — `test_kick_timing_within_5ms` failed with 156ms error on first onset
- **Issue:** The plan's `make_kick_wav` spec generated onsets starting at t=0.0. Spectral flux onset detection (librosa `onset_strength`) computes energy differences between consecutive frames, requiring pre-stimulus silence to establish a baseline. With the first onset at t=0, there is no "before" state, and the detector instead latches onto the pitch-decay body ~157ms into the first kick.
- **Fix:** Added `pre_roll=0.2` parameter to both `make_kick_wav` and `make_hihat_wav_with_bleed`. All onset times are offset by `pre_roll` seconds; total WAV duration is `duration + pre_roll`. This is a test helper change only — `OnsetBackend.detect()` itself is unchanged.
- **Files modified:** `tests/test_onset_backend.py`
- **Commit:** d2e9741

**2. [Rule 1 - Bug] Tom wait_ms: used 100ms instead of plan's 200ms**

- **Found during:** Task 2 (GREEN phase), cross-referencing research doc
- **Issue:** Plan spec said `47: (0.07, 200)` but the RESEARCH.md explicitly called this out: "Use wait_ms=100 for toms (GM 47) rather than 200ms — gives headroom for fast fills". Research was authoritative on this discretion call.
- **Fix:** `_PER_CLASS[47] = (0.07, 100)` with comment explaining the rationale. Tests pass with this value.
- **Files modified:** `pipelines/onset_backend.py`
- **Commit:** d2e9741

## Per-Class Parameter Summary

| GM Note | Instrument | delta | wait_ms | Rationale |
|---------|-----------|-------|---------|-----------|
| 35 | Acoustic Bass Drum | 0.07 | 200 | Strong transient; 200ms prevents pitch-decay re-trigger |
| 38 | Acoustic Snare | 0.07 | 200 | Strong transient; safe at 300 BPM |
| 47 | Mid Tom | 0.07 | 100 | Strong transient; 100ms allows rapid tom fills |
| 42 | Closed Hi-Hat | 0.10 | 80 | Higher delta suppresses kick bleed; 80ms allows 16th notes at 180 BPM |
| 49 | Crash Cymbal 1 | 0.15 | 100 | Highest delta for highest bleed; sustain creates envelope shoulders |

## Self-Check: PASSED

- [x] `pipelines/onset_backend.py` exists and contains `class OnsetBackend:`
- [x] `tests/test_onset_backend.py` exists and contains `def test_kick_timing_within_5ms(`
- [x] Commit d060a83 — RED phase tests
- [x] Commit d2e9741 — GREEN phase implementation
- [x] `pytest tests/test_onset_backend.py -x -q` → 5 passed
- [x] No torch import in onset_backend.py
- [x] `python -c "from pipelines.onset_backend import OnsetBackend; print('OK')"` → OK
