---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: Archive
status: completed
stopped_at: Completed 08-midipipeline-mode-dispatcher 08-02-PLAN.md
last_updated: "2026-03-21T00:26:42.160Z"
last_activity: 2026-03-21 — Phase 06 Plan 01 complete
progress:
  total_phases: 7
  completed_phases: 5
  total_plans: 7
  completed_plans: 7
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-21)

**Core value:** Drum stem MIDI extraction produces accurate GM channel-10 output — playable through FluidSynth preview without manual correction
**Current focus:** Milestone v2.0 — LarsNet Drum Sub-Separation (Phase 6 complete, Phase 7 next)

## Current Position

Phase: 06-onset-detection-backend
Plan: 01 (complete)
Status: Phase 06 complete — OnsetBackend implemented and tested
Last activity: 2026-03-21 — Phase 06 Plan 01 complete

Progress: [████░░░░░░░░░░░░░░░░] 3/7+ plans completed

## Performance Metrics

**Velocity (from v1.0):**
- Total plans completed: 8
- Average duration: ~3 min/plan
- Total execution time: ~25 min

**By Phase (v1.0):**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-foundation | 2 | ~7 min | ~3.5 min |
| 02-adtof-backend | 2 | ~5 min | ~2.5 min |
| 03-loader-and-pipeline-wiring | 2 | ~4 min | ~2 min |
| 04-validation-and-ux-polish | 2 | ~7.5 min | ~3.75 min |
| Phase 05-larsnet-registry-and-loader-stub P01 | 8 | 1 tasks | 6 files |
| Phase 06-onset-detection-backend P01 | 4 min | 2 tasks | 2 files |
| Phase 05-larsnet-registry-and-loader-stub P02 | 8 | 2 tasks | 5 files |
| Phase 07-midi-model-loader-extensions P01 | 3 min | 2 tasks | 2 files |
| Phase 08-midipipeline-mode-dispatcher P01 | 3.5 min | 2 tasks | 2 files |
| Phase 09 P01 | 5 | 2 tasks | 4 files |
| Phase 08-midipipeline-mode-dispatcher P02 | 2 | 2 tasks | 3 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [v1.0]: ADTOF-pytorch as sole v1 ADT backend — PyTorch-only, bundled weights
- [v1.0]: Multi-backend abstraction from day one — Protocol-based duck-typed interface
- [v1.0]: typing.Protocol over ABC for ADT backends — structural subtyping
- [v2.0]: LarsNet for drum sub-separation — 5 classes match ADTOF 1:1; vendored like python-audio-separator
- [v2.0]: Three runtime modes — ADTOF-only, LarsNet+ADTOF, LarsNet+onset-detection
- [v2.0]: Sub-stems exposed as playable audio — not just internal to MIDI pipeline
- [v2.0]: CC BY-NC 4.0 acceptable for LarsNet weights
- [v2.0]: LarsNet default device is CPU — 5 U-Net checkpoints (~700 MB VRAM) make GPU use risky alongside Demucs/Roformer
- [v2.0]: Sub-stems stored under STEMS_DIR/drum_sub/{job_id}/ — never in stem_paths session field
- [v2.0]: LarsNet evicted before ADTOF loads in LarsNet+ADTOF mode — VRAM safety contract enforced in loader, not pipeline
- [Phase 05-larsnet-registry-and-loader-stub]: vendor/larsnet __init__.py adds package dir to sys.path so upstream flat imports work without patching upstream files
- [Phase 05-larsnet-registry-and-loader-stub]: LARSNET_STEM_KEYS uses config.yaml names: 'toms'/'hihat'/'cymbals' (differs from ADTOF: 'tom'/'hi_hat'/'cymbal')
- [Phase 06-onset-detection-backend]: pre_roll=0.2s in test WAV helpers — t=0 onset is degenerate for spectral flux (no pre-stimulus baseline)
- [Phase 06-onset-detection-backend]: wait_ms=100 for toms (GM 47) — rapid tom fills need 100ms gap, not 200ms
- [Phase 06-onset-detection-backend]: hop_length=128 unconditionally — default 512 gives 11.6ms resolution, fails ±5ms criterion
- [Phase 05-larsnet-registry-and-loader-stub]: LarsNet config written to cache_dir/_larsnet_config.yaml at load time — idempotent, inspectable, no tempfile needed
- [Phase 05-larsnet-registry-and-loader-stub]: sys.path insertion for vendor/larsnet inside load() — avoids side effects at startup, consistent with adtof_backend pattern
- [Phase 07-midi-model-loader-extensions]: INFRA-03 eviction sequencing enforced in loader layer: evict_larsnet() called before _ensure_adtof() inside convert_drum_to_midi_with_larsnet()
- [Phase 08-01]: OnsetBackend imported at module level (not lazily) so unittest.mock.patch can target pipelines.midi_pipeline.OnsetBackend
- [Phase 09]: GUARD-01 checked before stems filter to ensure 400 fires for larsnet modes even when no drum stem selected
- [Phase 08-02]: drum_mode persisted to session before job dispatch — session reflects last-used mode even if job fails
- [Phase 08-02]: drum_mode passed in config_kwargs dict rather than as separate argument — consistent with all other MidiConfig params

### Critical Implementation Notes

- LARSNET_STEM_KEYS = ("kick", "snare", "toms", "hihat", "cymbals") — sourced from config.yaml, NOT from ADTOF labels
- config.yaml uses relative paths — must resolve to absolute at load time using __file__
- Checkpoint paths inside config.yaml also relative — must be rewritten to absolute at load time
- Phase 5 and Phase 6 can be developed in parallel once Phase 5 vendoring setup is complete
- ADTOF-only baseline must be regression-tested in Phase 8 before any LarsNet modes are declared working

### Pending Todos

None yet.

### Blockers/Concerns

- numpy >=2.0 vs LarsNet's pinned 1.26.2: LarsNet's array usage appears safe but must be verified at first import in Phase 5
- LarsNet Google Drive gdown file IDs must be extracted from LarsNet README at implementation time (not in research files)
- Per-class onset delta values require empirical tuning on real drum audio during Phase 6 (no published values exist)
- Tensor input path to larsnet(x) is non-documented but recommended — validate in Phase 5 against file-path input on reference drum stem

## Session Continuity

Last session: 2026-03-21T00:26:42.157Z
Stopped at: Completed 08-midipipeline-mode-dispatcher 08-02-PLAN.md
Resume file: None
Next step: Phase 7 (LarsNet separation wiring)
