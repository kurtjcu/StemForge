# Roadmap: Drum Stem to MIDI — ADTOF Integration

## Overview

This milestone adds a purpose-built drum transcription branch to StemForge's MIDI pipeline. Currently drum stems are silently misrouted to BasicPitch (a pitched-instrument model), producing garbled output. Four phases build bottom-up through StemForge's established import layer order: shared utilities first, then the ADTOF inference backend in isolation, then loader/pipeline wiring, and finally validation with UX polish. The result is a user who separates a drum stem and clicks Extract MIDI getting accurate GM channel-10 MIDI with correct kick/snare/hi-hat/tom/cymbal classification that plays back correctly through FluidSynth preview.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Foundation** - GM drum maps, `is_drum` parameter on `notes_to_midi()`, and `DrumMidiSpec` in model registry (completed 2026-03-20)
- [ ] **Phase 2: ADTOF Backend** - Isolated ADTOF-pytorch inference backend with in-memory onset matrix conversion
- [ ] **Phase 3: Loader and Pipeline Wiring** - `DrumMidiLoader`, `MidiModelLoader` extension, and `_DRUM_STEM_LABELS` routing branch
- [ ] **Phase 4: Validation and UX Polish** - Integration tests, progress callbacks, and model selector caveat text

## Phase Details

### Phase 1: Foundation
**Goal**: Shared utilities and model registry support drum transcription without breaking existing paths
**Depends on**: Nothing (first phase)
**Requirements**: MIDI-01, MIDI-02, MIDI-03, REG-01, REG-02, DEP-01
**Success Criteria** (what must be TRUE):
  1. `notes_to_midi()` called with `is_drum=True` produces a MIDI file where the instrument track sits on channel 10 and note numbers match the supplied GM map
  2. `notes_to_midi()` called without `is_drum` (default) produces identical output to pre-change behavior for all existing callers
  3. `utils/drum_map.py` exports `ADTOF_5CLASS_GM_NOTE` mapping `{0: 35, 1: 38, 2: 47, 3: 42, 4: 49}` — tom at index 2, hi-hat at index 3, non-sequential ordering preserved exactly
  4. `DrumMidiSpec` is importable from `models/registry.py` and `list_specs()` returns the ADTOF entry with correct capabilities and cache subdir
  5. ADTOF-pytorch appears as a git dependency in `pyproject.toml` and `uv sync` resolves cleanly
**Plans:** 2/2 plans complete

Plans:
- [ ] 01-01-PLAN.md — GM drum map module and notes_to_midi is_drum parameter (TDD)
- [ ] 01-02-PLAN.md — DrumMidiSpec registry entry and ADTOF-pytorch dependency (TDD)

### Phase 2: ADTOF Backend
**Goal**: ADTOF-pytorch inference runs in-memory and returns verified GM note events without touching disk
**Depends on**: Phase 1
**Requirements**: ADT-01, ADT-02, ADT-03, ADT-04
**Success Criteria** (what must be TRUE):
  1. `AdtofBackend.predict(path)` returns a list of `NoteEvent` tuples with MIDI note numbers drawn exclusively from `{35, 38, 42, 47, 49}` — no other note numbers appear
  2. Passing a 44100 Hz WAV and a 22050 Hz WAV of the same audio to `predict()` produces different onset times, confirming the 44100 Hz assertion guard fires on the 22050 Hz input
  3. `AdtofBackend` never writes any file to disk during prediction — no MIDI, no temp files, no output directories created
  4. `AdtofBackend.evict()` releases the model from memory (subsequent `predict()` call forces a reload)
  5. A second backend implementing the same `load/predict/evict` interface can be registered alongside `AdtofBackend` without modifying `AdtofBackend`
**Plans:** 2 plans

Plans:
- [ ] 02-01-PLAN.md — AdtofBackendProtocol and AdtofBackend load/evict lifecycle (TDD)
- [ ] 02-02-PLAN.md — predict() with 44100 Hz guard and NoteEvent conversion (TDD)

### Phase 3: Loader and Pipeline Wiring
**Goal**: A user can upload a drum stem, click Extract MIDI, and receive a playable GM channel-10 MIDI file via FluidSynth preview
**Depends on**: Phase 2
**Requirements**: PIPE-01, PIPE-02, PIPE-03, PIPE-04
**Success Criteria** (what must be TRUE):
  1. Uploading a drum stem labeled `"drums"` (from Demucs) and running MIDI extraction produces a MIDI file that FluidSynth previews as percussion — kick, snare, and hi-hat are audibly distinguishable
  2. Uploading a drum stem labeled `"Drums & percussion"` (from BS-Roformer) produces the same correct percussion output
  3. Uploading a non-drum stem (e.g., vocals or bass) still routes through the existing vocal or BasicPitch path unchanged — no regression
  4. The MIDI extraction job shows progress updates at recognizable stages (audio load, processing, done) rather than hanging silently at 0%
  5. After drum MIDI extraction completes, a second separation job (Demucs) starts without VRAM errors — the drum loader has been evicted
**Plans**: TBD

### Phase 4: Validation and UX Polish
**Goal**: The drum MIDI path is verified correct by test, and the model selector communicates ADTOF's known accuracy limits
**Depends on**: Phase 3
**Requirements**: REG-03
**Success Criteria** (what must be TRUE):
  1. The MIDI panel shows an ADT model selector populated from the registry — identical interaction pattern to the separation engine picker
  2. The ADTOF model entry in the selector displays a caveat that electronic/programmed drums have lower accuracy than acoustic drums
  3. A drum stem run through the full pipeline produces MIDI where every note is on channel 10 — confirmed by reading the output file header, not just listening
  4. Running MIDI extraction on a non-drum stem with the same session state produces MIDI on channel 1 — the `is_drum=False` default path is confirmed working
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation | 2/2 | Complete    | 2026-03-20 |
| 2. ADTOF Backend | 0/2 | Not started | - |
| 3. Loader and Pipeline Wiring | 0/TBD | Not started | - |
| 4. Validation and UX Polish | 0/TBD | Not started | - |
