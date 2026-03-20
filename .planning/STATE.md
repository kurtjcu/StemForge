---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: milestone
status: planning
stopped_at: Completed 04-02-PLAN.md
last_updated: "2026-03-20T21:43:08.647Z"
last_activity: 2026-03-20 — Phase 2 complete, transitioning to Phase 3
progress:
  total_phases: 4
  completed_phases: 4
  total_plans: 8
  completed_plans: 8
  percent: 100
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-20)

**Core value:** Drum stem MIDI extraction produces accurate GM channel-10 output — playable through FluidSynth preview without manual correction
**Current focus:** Phase 3 — Loader and Pipeline Wiring

## Current Position

Phase: 3 of 4 (Loader and Pipeline Wiring)
Plan: Not started
Status: Ready to plan
Last activity: 2026-03-20 — Phase 2 complete, transitioning to Phase 3

Progress: [████████████████████] 4/4 plans (100% of planned so far)

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

**Recent Trend:**
- Last 5 plans: none yet
- Trend: -

*Updated after each plan completion*
| Phase 01-foundation P02 | 3 | 2 tasks | 3 files |
| Phase 01-foundation P01 | 4 | 2 tasks | 4 files |
| Phase 02-adtof-backend P01 | 2 | 2 tasks | 2 files |
| Phase 02-adtof-backend P02 | 3 | 2 tasks | 2 files |
| Phase 03-loader-and-pipeline-wiring P01 | 2 | 2 tasks | 2 files |
| Phase 03-loader-and-pipeline-wiring P02 | 2 | 2 tasks | 2 files |
| Phase 04-validation-and-ux-polish P01 | 208s | 2 tasks | 2 files |
| Phase 04-validation-and-ux-polish P02 | 244 | 2 tasks | 1 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Pre-phase]: ADTOF-pytorch as sole v1 ADT backend — PyTorch-only, bundled weights, no new transitive deps
- [Pre-phase]: Multi-backend abstraction from day one — `load/predict/evict` duck-typed interface so ADT_STR can be added later
- [Pre-phase]: ADT_STR explicitly excluded — torch==2.8.0 pin and CLAP/numpy conflicts unresolved as of 2026-03-20
- [Phase 01-foundation]: Used xavriley/ADTOF-pytorch (PyTorch-only) over MZehren/ADTOF (TensorFlow — numpy>=2.0 conflict)
- [Phase 01-foundation]: adtof-pytorch pinned at commit 85c192e; checkpoint_url='' as weights are bundled; MAC deferred to Phase 2
- [Phase 01-foundation]: is_drum appended as last keyword param to notes_to_midi() to preserve all existing callers
- [Phase 01-foundation]: 60ms cap applied after degenerate-note guard; ADTOF_5CLASS_GM_NOTE preserves non-sequential model ordering
- [Phase 02-adtof-backend]: adtof_pytorch imported inside load() not at module level — preserves lazy loading
- [Phase 02-adtof-backend]: typing.Protocol structural subtyping over ABC — future ADT_STR backend can implement without inheriting
- [Phase 02-adtof-backend]: Only InvalidInputError re-raised in predict() except clause — RuntimeError from model forward must reach PipelineExecutionError wrapper
- [Phase 03-loader-and-pipeline-wiring]: Deferred import from pipelines.adtof_backend inside _ensure_adtof() body mirrors _ensure_whisper() pattern
- [Phase 03-loader-and-pipeline-wiring]: evict_drum_model() public method enables selective ADTOF eviction without disturbing BasicPitch TF model
- [Phase 03-loader-and-pipeline-wiring]: _DRUM_STEM_LABELS frozenset mirrors STEM_IS_DRUM in backend/api/midi.py for consistent label routing
- [Phase 03-loader-and-pipeline-wiring]: 3-stage progress callbacks in drum branch prevent progress bar freeze during ADTOF model load
- [Phase 03-loader-and-pipeline-wiring]: Post-loop evict_drum_model() inside MidiPipeline.run() frees VRAM; AdtofBackend.evict() handles torch.cuda.empty_cache() internally
- [Phase 04-01]: adt_model field on ExtractRequest uses default adtof-drums for backward compatibility — field accepted but not wired to pipeline logic in this phase
- [Phase 04-01]: list_specs(DrumMidiSpec) is the single source of truth for adt_models list — avoids hardcoding model IDs in API layer
- [Phase 04-02]: Event delegation on #midi-stems container avoids re-render pitfall; syncAdtGroupVisibility() called at end of populateStemCheckboxes() for immediate visibility on stem load
- [Phase 04-02]: adt_model fallback is adtof-drums in startExtraction() — backend accepts but ignores for v1, kept for future wiring

### Pending Todos

None yet.

### Blockers/Concerns

None active.

## Session Continuity

Last session: 2026-03-20T21:43:08.645Z
Stopped at: Completed 04-02-PLAN.md
Resume file: None
