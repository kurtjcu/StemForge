---
phase: 07-midi-model-loader-extensions
plan: 01
subsystem: models
tags: [tdd, midi-loader, larsnet, drum-separation, vram-management]
dependency_graph:
  requires: [05-larsnet-registry-and-loader-stub, 06-onset-detection-backend]
  provides: [MidiModelLoader._ensure_larsnet, MidiModelLoader.evict_larsnet, MidiModelLoader.separate_drums, MidiModelLoader.convert_drum_to_midi_with_larsnet]
  affects: [pipelines/midi_pipeline.py, backend/api/midi.py]
tech_stack:
  added: []
  patterns: [lazy-import, tdd-red-green, mock-based-isolation]
key_files:
  created: [tests/test_midi_loader_larsnet.py]
  modified: [models/midi_loader.py]
decisions:
  - "evict_larsnet() placed in evict() master method for complete cleanup"
  - "separate_drums() normalizes mono input to stereo internally"
  - "convert_drum_to_midi_with_larsnet() uses tempfile bridge for ADTOF path interface"
metrics:
  duration: "3 min"
  completed: "2026-03-21"
  tasks_completed: 2
  files_changed: 2
---

# Phase 07 Plan 01: MidiModelLoader LarsNet Extension Summary

**One-liner:** LarsNet lazy-load facade with INFRA-03 eviction sequencing — separate_drums returns 5-stem dict, convert_drum_to_midi_with_larsnet enforces evict-before-ADTOF.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | RED — Write failing tests | 121782e | tests/test_midi_loader_larsnet.py (created) |
| 2 | GREEN — Implement LarsNet loader methods | 3ecda9c | models/midi_loader.py (modified) |

## Verification

```
tests/test_midi_loader_larsnet.py  13 passed
tests/test_midi_loader_drum.py      8 passed (no regression)
Full suite (excl. pre-existing broken)  132 passed
```

Lazy import verified: no top-level `from pipelines.larsnet_backend import LarsNetBackend` in midi_loader.py.

## Deviations from Plan

None — plan executed exactly as written.

## Key Decisions

- `evict_larsnet()` added to master `evict()` method so complete teardown always clears LarsNet
- `separate_drums()` normalizes 1D (mono) tensors to stereo via `unsqueeze(0).expand(2, -1)` before passing to backend
- `convert_drum_to_midi_with_larsnet()` uses `tempfile.NamedTemporaryFile` + `soundfile.write` to bridge tensor input to ADTOF's path-based `predict()` interface (ADTOF cannot accept in-memory tensors)
- INFRA-03 contract enforced at loader layer: `evict_larsnet()` is called explicitly before `_ensure_adtof()` inside `convert_drum_to_midi_with_larsnet()`

## Self-Check: PASSED

- [x] tests/test_midi_loader_larsnet.py exists
- [x] models/midi_loader.py contains all 4 new methods
- [x] Commit 121782e exists (RED phase)
- [x] Commit 3ecda9c exists (GREEN phase)
- [x] 13 LarsNet tests pass
- [x] 8 drum loader tests pass (no regression)
- [x] No top-level larsnet import in midi_loader.py
