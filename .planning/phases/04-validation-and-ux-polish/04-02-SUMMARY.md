---
phase: 04-validation-and-ux-polish
plan: 02
subsystem: ui
tags: [midi, adt, drums, frontend, javascript]

# Dependency graph
requires:
  - phase: 04-01
    provides: adt_models field in /api/midi/gm-programs response, DrumMidiSpec registry

provides:
  - ADT model selector group in MIDI panel with visibility toggle and caveat text
  - adt_model field sent in MIDI extraction request body

affects: [midi-tab, midi-extraction, drum-midi-flow]

# Tech tracking
tech-stack:
  added: []
  patterns: [visibility-toggle-via-event-delegation, populate-select-from-backend-data]

key-files:
  created: []
  modified:
    - frontend/components/midi.js

key-decisions:
  - "Event delegation on #midi-stems container for visibility toggle avoids the re-render pitfall when populateStemCheckboxes() replaces all checkboxes"
  - "syncAdtGroupVisibility() called at end of populateStemCheckboxes() ensures group shows immediately when stems load, not just on subsequent changes"
  - "adt_model fallback is adtof-drums in startExtraction() for future-proofing — backend accepts but ignores for v1"

patterns-established:
  - "Select populated from backend registry data, not hardcoded — mirrors sep-engine picker pattern"
  - "Hidden group toggled with classList.toggle('hidden', !condition) — single boolean expression"

requirements-completed: [REG-03]

# Metrics
duration: 3min
completed: 2026-03-20
---

# Phase 4 Plan 02: ADT Model Selector in MIDI Panel Summary

**ADT model selector in MIDI panel with event-delegated visibility toggle, registry-populated options, and caveat text about electronic drum accuracy limits**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-03-20T21:38:55Z
- **Completed:** 2026-03-20T21:41:51Z
- **Tasks:** 2 (1 auto + 1 checkpoint auto-approved)
- **Files modified:** 1

## Accomplishments
- Added hidden `#midi-adt-group` div to MIDI panel between stem checkboxes and key selector
- Wired `syncAdtGroupVisibility()` via event delegation on `#midi-stems` change events and end of `populateStemCheckboxes()` call
- ADT select populated from `data.adt_models` returned by `/api/midi/gm-programs` (registry-driven, not hardcoded)
- Caveat text "Best results with acoustic drums. Electronic/programmed drums may have lower accuracy." shown with subdued styling
- `adt_model` field included in MIDI extraction request body with fallback to `adtof-drums`
- All 38 drum/MIDI-related tests pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Add ADT model selector group to MIDI panel** - `dc788ed` (feat)
2. **Task 2: Verify ADT selector and drum MIDI flow in browser** - auto-approved (checkpoint, auto-chain active)

**Plan metadata:** (docs commit follows)

## Files Created/Modified
- `frontend/components/midi.js` - Added adtModels variable, adtGroup DOM element, syncAdtGroupVisibility() function, adt_models population in loadGmPrograms(), adt_model in startExtraction() request body

## Decisions Made
- Used event delegation on `#midi-stems` container rather than attaching listeners inside `populateStemCheckboxes()` — avoids the re-render pitfall where replacing all checkboxes would orphan event listeners
- Called `syncAdtGroupVisibility()` at end of `populateStemCheckboxes()` so the group toggles immediately on stem load, not only on subsequent checkbox changes
- `adt_model` fallback is `adtof-drums` string in case the select is not yet populated — backend accepts field but ignores it in v1

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- `tests/test_av.py` and `tests/test_basicpitch.py` have pre-existing failures (PyAV dtype mismatch, missing basic_pitch module) unrelated to this plan. Confirmed pre-existing by stash/test/pop. Deferred per scope boundary rule.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 4 (Validation and UX Polish) complete — all 2 plans finished
- REG-03 fulfilled: user sees ADT model selector when drum stems are checked, understands accuracy limits via caveat text
- Selector populated from registry via backend, not hardcoded
- adt_model round-trips through extraction request for future wiring to pipeline logic

---
*Phase: 04-validation-and-ux-polish*
*Completed: 2026-03-20*
