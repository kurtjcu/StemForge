# Roadmap: Drum Stem to MIDI — v2.0 LarsNet Drum Sub-Separation

## Overview

This milestone integrates LarsNet drum sub-separation into StemForge, adding three runtime-selectable drum MIDI modes and per-instrument sub-stem audio. Seven phases build strictly bottom-up through StemForge's import layer order: registry and LarsNet vendoring first (highest risk), onset detection backend in parallel, then loader extensions, pipeline routing, API and session changes, frontend mode selector, and finally export integration. The result is a user who separates a drum stem, selects a MIDI mode (ADTOF-only, LarsNet+ADTOF, or LarsNet+onset), and receives both per-instrument sub-stem audio cards and accurate GM channel-10 MIDI — all without breaking the existing ADTOF-only path.

**v1.0 milestone (Phases 1–4) is complete.** See archived reference at end of this file.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 5: LarsNet Registry and Loader Stub** — Vendor LarsNet, resolve config/checkpoint paths, register LarsNetSpec, validate loader loads 5 U-Nets from outside the LarsNet directory (completed 2026-03-20)
- [x] **Phase 6: Onset Detection Backend** — CPU-only energy onset detection with per-class delta thresholds, fully validated on synthetic audio before any LarsNet integration (completed 2026-03-20)
- [ ] **Phase 7: MidiModelLoader Extensions** — Loader facade methods for all three modes with correct LarsNet-evicts-before-ADTOF sequencing
- [ ] **Phase 8: MidiPipeline Mode Dispatcher** — drum_mode field on MidiConfig, _run_drum_mode() dispatcher, regression test confirming ADTOF-only path unchanged
- [ ] **Phase 9: Session Store and API Endpoints** — drum_sub_stem_paths session field, drum_mode param in ExtractRequest, sub-stems endpoint, mode pre-condition guards
- [ ] **Phase 10: Frontend Mode Selector and Sub-Stem Cards** — Three-way mode selector in MIDI panel, 5 sub-stem audio cards wired to transport bar, guard-rail disabled states, quality warnings
- [ ] **Phase 11: Export Sub-Stem Download** — Opt-in sub-stem export checkbox, sub-stems forwarded to Mix and Enhance via event bus

## Phase Details

### Phase 5: LarsNet Registry and Loader Stub
**Goal**: LarsNet is vendored, loadable, and correctly evictable from StemForge's runtime environment — all path and key-mismatch pitfalls resolved before any downstream code is written
**Depends on**: Phase 4 (v1.0 complete)
**Requirements**: INFRA-01, INFRA-02, INFRA-05, SEP-01
**Success Criteria** (what must be TRUE):
  1. `from larsnet import LarsNet` succeeds when called from any working directory that is not the LarsNet source directory — config.yaml and all 5 checkpoint paths resolve to absolute paths
  2. `LarsNetBackend.load()` loads all 5 U-Net checkpoints and `LarsNetBackend.evict()` releases them — a GPU memory measurement before and after evict() shows no residual allocation
  3. `LarsNetSpec` is importable from `models/registry.py` and `list_specs()` returns the LarsNet entry with correct capabilities, cache subdir, and CC BY-NC 4.0 license metadata
  4. `scripts/download_larsnet_weights.sh` (or equivalent `gdown`-based helper) downloads all 5 checkpoint files to `~/.cache/stemforge/larsnet/` and a missing-weights import raises a clear error with download instructions rather than a silent hang
  5. `LARSNET_STEM_KEYS = ("kick", "snare", "toms", "hihat", "cymbals")` is defined as a single constant sourced from config.yaml — not derived from the ADTOF registry class_labels
**Plans:** 2/2 plans complete
Plans:
- [ ] 05-01-PLAN.md — Vendor LarsNet source files, add LarsNetSpec to registry
- [ ] 05-02-PLAN.md — LarsNetBackend load/evict, download script, gdown dependency

### Phase 6: Onset Detection Backend
**Goal**: Energy-based onset detection on isolated drum sub-stems is validated and threshold-tuned before LarsNet integration makes it harder to isolate onset behaviour
**Depends on**: Phase 5 (LarsNet vendoring complete so stem key names are confirmed)
**Requirements**: MODE-04
**Success Criteria** (what must be TRUE):
  1. `OnsetBackend.detect(path, gm_note)` called on a synthetic 4/4 kick WAV (sine bursts at known intervals) returns onsets within ±5 ms of ground truth for all beats
  2. `OnsetBackend.detect()` on a cymbal sub-stem with known hi-hat bleed produces a hi-hat onset count within the expected ±2 count of ground truth — bleed events are suppressed by per-class delta threshold
  3. `OnsetBackend` is CPU-only, imports no model weights, and completes on a 60-second sub-stem in under 2 seconds
  4. All per-class delta threshold values (`delta=0.07` for kick/snare, `delta=0.10–0.15` for hi-hat/cymbals) are documented with rationale in code comments
**Plans:** 1/1 plans complete
Plans:
- [ ] 06-01-PLAN.md — TDD: OnsetBackend with per-class delta thresholds (tests + implementation)

### Phase 7: MidiModelLoader Extensions
**Goal**: The loader facade exposes all three drum MIDI modes through a stable API — the pipeline never imports backends directly
**Depends on**: Phase 5 and Phase 6
**Requirements**: INFRA-03, SEP-02
**Success Criteria** (what must be TRUE):
  1. `MidiModelLoader.separate_drums(audio_tensor)` returns a `dict[str, Path]` with exactly 5 keys matching `LARSNET_STEM_KEYS` — each path points to a written WAV file under `STEMS_DIR/drum_sub/{job_id}/`
  2. In LarsNet+ADTOF mode, GPU memory measured after `separate_drums()` completes shows LarsNet weights are absent before `_ensure_adtof()` is called — eviction sequencing is enforced by the loader, not the pipeline
  3. `_ensure_larsnet()` follows the same lazy-load pattern as `_ensure_adtof()` — weights are not loaded at startup, only on first call, and load time is reported in logs
  4. All four new loader methods (`_ensure_larsnet`, `evict_larsnet`, `separate_drums`, `convert_drum_to_midi_with_larsnet`) are testable in isolation at the loader layer without invoking the pipeline
**Plans:** 1 plan
Plans:
- [ ] 07-01-PLAN.md — TDD: LarsNet loader methods (_ensure_larsnet, evict_larsnet, separate_drums, convert_drum_to_midi_with_larsnet)

### Phase 8: MidiPipeline Mode Dispatcher
**Goal**: All three drum MIDI modes work end-to-end at the pipeline layer and the existing ADTOF-only path is confirmed regression-free
**Depends on**: Phase 7
**Requirements**: MODE-01, MODE-03
**Success Criteria** (what must be TRUE):
  1. `MidiConfig(drum_mode="adtof_only")` produces identical MIDI output to the pre-v2.0 pipeline on the same drum WAV — onset times and note assignments are byte-for-byte or within floating-point rounding of the v1.0 baseline
  2. `MidiConfig(drum_mode="larsnet_adtof")` produces MIDI where every note is on channel 10 and note numbers are drawn exclusively from `{35, 38, 42, 47, 49}`
  3. `MidiConfig(drum_mode="larsnet_onset")` produces MIDI with kick (MIDI 35) events that match the sub-stem's onset times within ±10 ms — demonstrating onset detection is driving the MIDI output, not ADTOF
  4. `MidiResult.drum_sub_stems` is populated with 5 sub-stem paths when either LarsNet mode is selected, and is an empty dict when ADTOF-only mode is selected
  5. Selected `drum_mode` is preserved in session state and re-read correctly on the next extraction request without requiring re-selection
**Plans**: TBD

### Phase 9: Session Store and API Endpoints
**Goal**: Sub-stem paths are isolated from primary stems in session state, and the API enforces mode pre-conditions before enqueueing any job
**Depends on**: Phase 8
**Requirements**: GUARD-01, GUARD-02, INFRA-04, SEP-03
**Success Criteria** (what must be TRUE):
  1. `GET /api/midi/sub-stems` returns a JSON object with 5 sub-stem paths after a LarsNet mode extraction — the paths are playable via the existing `/api/audio/stream` endpoint without any new streaming code
  2. Requesting a LarsNet+* mode extraction when no drum stem is in session returns HTTP 400 with a message identifying the missing prerequisite — no job is enqueued and no processing begins
  3. Sub-stem paths appear in `session.drum_sub_stem_paths` and are absent from `session.stem_paths` — the Separate tab stem cards are unaffected by sub-stem presence
  4. After a LarsNet extraction completes, playing a sub-stem via the MIDI panel uses the standard transport bar and the audio streams correctly from disk
**Plans**: TBD

### Phase 10: Frontend Mode Selector and Sub-Stem Cards
**Goal**: The user can select a drum MIDI mode from the MIDI panel, audition all 5 sub-stems, and see quality warnings — all without leaving the MIDI tab
**Depends on**: Phase 9
**Requirements**: MODE-02, GUARD-01, GUARD-03, SEP-06
**Success Criteria** (what must be TRUE):
  1. The MIDI panel shows a three-way mode selector (ADTOF-only / LarsNet+ADTOF / LarsNet+onset) — LarsNet options are visually disabled and show a tooltip explanation when no drum stem is in session
  2. After a LarsNet mode extraction completes, 5 sub-stem audio cards appear in the MIDI panel — each card has a play button that loads the sub-stem into the transport bar with label "MIDI (LarsNet — kick)" or equivalent
  3. Toms and cymbals sub-stem cards display a quality warning tooltip citing the nSDR benchmarks (toms ~9 dB, cymbals ~4 dB) — the warning is visible before the user plays the sub-stem
  4. Sub-stem cards do not appear in the Separate tab or any other tab — they are exclusive to the MIDI panel
  5. Sub-stems are forwarded via the event bus so the Mix and Enhance tabs can receive them as available audio sources
**Plans**: TBD

### Phase 11: Export Sub-Stem Download
**Goal**: Users can optionally include drum sub-stems in export zips without polluting the default export for users who only ran LarsNet for MIDI quality improvement
**Depends on**: Phase 9 (sub-stem session paths) and Phase 10 (sub-stem cards confirm UX is stable)
**Requirements**: SEP-04, SEP-05
**Success Criteria** (what must be TRUE):
  1. The Export panel shows an "Include drum sub-stems" checkbox — it is absent when no sub-stems are in session and appears automatically when sub-stems are present
  2. An export zip created without the checkbox checked contains zero sub-stem files — the 5 sub-stem WAVs do not appear in the zip even if sub-stems are in session
  3. An export zip created with the checkbox checked contains all 5 sub-stem WAVs in a `drum_sub/` subdirectory — each file is playable and matches the file served by `/api/audio/stream`
  4. The "Reduce crosstalk" Wiener filter toggle (fixed alpha=2) is available in the MIDI panel when a LarsNet mode is selected — enabling it and re-running extraction produces sub-stems with measurably lower bleed on a high-bleed test recording
**Plans**: TBD

## Progress

**Execution Order:**
Phases 5 and 6 can be developed in parallel once Phase 5's vendoring setup is complete. Phases 7–11 are strictly sequential.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 5. LarsNet Registry and Loader Stub | 2/2 | Complete   | 2026-03-20 |
| 6. Onset Detection Backend | 1/1 | Complete   | 2026-03-20 |
| 7. MidiModelLoader Extensions | 0/1 | Planned | - |
| 8. MidiPipeline Mode Dispatcher | 0/? | Not started | - |
| 9. Session Store and API Endpoints | 0/? | Not started | - |
| 10. Frontend Mode Selector and Sub-Stem Cards | 0/? | Not started | - |
| 11. Export Sub-Stem Download | 0/? | Not started | - |

---

## v1.0 Archive (Phases 1–4, Complete)

Milestone: ADTOF Integration
Completed: 2026-03-20

| Phase | Goal | Status |
|-------|------|--------|
| 1. Foundation | GM drum maps, is_drum parameter, DrumMidiSpec registry | Complete |
| 2. ADTOF Backend | In-memory ADTOF-pytorch inference, NoteEvent conversion | Complete |
| 3. Loader and Pipeline Wiring | MidiModelLoader, _DRUM_STEM_LABELS routing branch | Complete |
| 4. Validation and UX Polish | Integration tests, progress callbacks, model selector | Complete |
