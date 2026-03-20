---
phase: 04-validation-and-ux-polish
plan: "01"
subsystem: midi-api
tags: [tdd, integration-tests, drum-routing, api-extension]
dependency_graph:
  requires: []
  provides: [drum-channel-routing-verified, adt-models-api-endpoint]
  affects: [frontend-adt-selector, midi-pipeline]
tech_stack:
  added: []
  patterns: [tdd-red-green, fastapi-test-client, pretty_midi-round-trip]
key_files:
  created:
    - tests/test_drum_midi_integration.py
  modified:
    - backend/api/midi.py
decisions:
  - "adt_model field on ExtractRequest uses default adtof-drums for backward compatibility — field is accepted but not wired to pipeline logic in this phase"
  - "list_specs(DrumMidiSpec) is the single source of truth for adt_models list — avoids hardcoding model IDs in API layer"
metrics:
  duration: 208s
  completed: "2026-03-21"
  tasks_completed: 2
  files_created: 1
  files_modified: 1
---

# Phase 4 Plan 1: Drum MIDI Integration Tests and API Extension Summary

**One-liner:** Integration tests verify GM channel-10 drum routing with MIDI round-trip serialization; gm-programs endpoint extended with ADT model metadata from registry.

## Tasks Completed

| # | Task | Type | Commit | Files |
|---|------|------|--------|-------|
| 1 | Write integration tests — RED phase | TDD RED | 99601b6 | tests/test_drum_midi_integration.py |
| 2 | Extend backend — adt_models in gm-programs + adt_model on ExtractRequest | TDD GREEN | 33e509c | backend/api/midi.py |

## What Was Built

### Task 1: Integration Tests (RED Phase)

Created `tests/test_drum_midi_integration.py` with three test functions:

- **test_drum_stem_writes_channel_10**: Runs MidiPipeline with a mocked loader on a "drums" stem, asserts `instruments[0].is_drum is True` both in-memory and after writing/reading back a `.mid` file via `pretty_midi.write()` / `pretty_midi.PrettyMIDI()`.

- **test_non_drum_stem_writes_channel_1**: Same pattern for "bass" stem, asserts `is_drum is False` survives the round-trip.

- **test_gm_programs_includes_adt_models**: Uses `fastapi.testclient.TestClient` on the real app to GET `/api/midi/gm-programs`, asserts the JSON contains `adt_models` key with an entry where `model_id == "adtof-drums"`.

RED phase result: 2 passed (channel routing already working from Phase 3), 1 failed (adt_models missing from response).

### Task 2: Backend Extension (GREEN Phase)

Extended `backend/api/midi.py`:

1. **Import**: Added `DrumMidiSpec, list_specs` from `models.registry`.

2. **`_build_adt_model_list()` helper**: Queries `list_specs(DrumMidiSpec)` and returns `[{"model_id", "display_name", "tooltip"}]` for each registered ADT spec. Single source of truth via registry.

3. **`get_gm_programs()` extended**: Added `"adt_models": _build_adt_model_list()` to the response dict.

4. **`ExtractRequest.adt_model` field**: Added `adt_model: str = "adtof-drums"` — accepted by the API, stored in request, default preserves backward compatibility. Not wired to pipeline logic this phase (deferred per plan).

GREEN phase result: All 3 integration tests pass.

## Verification Results

```
uv run pytest tests/test_drum_midi_integration.py -v
  test_drum_stem_writes_channel_10         PASSED
  test_non_drum_stem_writes_channel_1      PASSED
  test_gm_programs_includes_adt_models     PASSED
  3 passed, 2 warnings in 1.54s

uv run pytest tests/ (excluding import-broken test_av.py, test_basicpitch.py, test_sao.py)
  77 passed, 8 warnings in 6.18s
```

The three excluded tests (`test_av.py`, `test_basicpitch.py`, `test_sao.py`) have pre-existing import errors unrelated to this plan — `pyav` dtype mismatch, `basic_pitch` not installed, `k_diffusion` not installed. All are out of scope.

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- tests/test_drum_midi_integration.py: FOUND
- backend/api/midi.py: FOUND
- Commit 99601b6: FOUND
- Commit 33e509c: FOUND
