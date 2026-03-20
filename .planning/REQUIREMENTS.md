# Requirements: Drum Stem to MIDI

**Defined:** 2026-03-20
**Core Value:** When a user separates a drum stem and extracts MIDI, the result must have both accurate onset timing and correct instrument classification — playable through FluidSynth preview without manual correction.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### MIDI Utilities

- [x] **MIDI-01**: GM drum note constants defined as single source of truth in `utils/drum_map.py`
- [x] **MIDI-02**: `notes_to_midi()` accepts `is_drum` parameter and creates `pretty_midi.Instrument(is_drum=True)` for channel 10 output
- [x] **MIDI-03**: Drum notes use fixed ~60ms duration (onset-only, no sustain)

### ADT Backend

- [x] **ADT-01**: ADTOF-pytorch integrated with in-memory prediction (not `predictFolder` disk writes)
- [x] **ADT-02**: Audio loaded at exactly 44100 Hz with assertion guard
- [x] **ADT-03**: Multi-backend ADT abstraction with common load/predict/evict interface for future backends
- [x] **ADT-04**: Onset matrix converted to NoteEvent list with correct GM note mapping (non-sequential LABELS_5)

### Model Registry

- [x] **REG-01**: `DrumMidiSpec` frozen dataclass added to model registry
- [x] **REG-02**: ADTOF model registered with capabilities, cache subdir, class count, class labels
- [x] **REG-03**: User-facing ADT model selector in MIDI panel (like separation engine picker)

### Pipeline Integration

- [x] **PIPE-01**: `_DRUM_STEM_LABELS` routing branch in `MidiPipeline.run()` (third branch alongside vocal and pitched)
- [x] **PIPE-02**: Labels cover both Demucs (`"drums"`) and BS-Roformer (`"Drums & percussion"`) output names
- [x] **PIPE-03**: Drum model lazy-loaded on first use (not at startup) with `evict()` for GPU memory release
- [x] **PIPE-04**: Progress callbacks wired during drum transcription job

### Dependencies

- [x] **DEP-01**: ADTOF-pytorch added as git dependency in `pyproject.toml`

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Enhanced Classification

- **ECLASS-01**: 7-class output (open hi-hat + crash/ride split) for richer drum MIDI
- **ECLASS-02**: Velocity from onset amplitude (scale ADTOF amplitude to 1-127 range)

### Additional Backends

- **BACK-01**: ADT_STR backend when public weights released and torch/CLAP conflicts resolved

### User Controls

- **CTRL-01**: Onset threshold slider in MIDI panel
- **CTRL-02**: GM note remap UI for custom percussion mapping

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| MusicXML / sheet music export | Not a producer-workflow need |
| Few-shot adaptation to custom drum kits | Research problem, no viable library today |
| Electronic music-specific mitigations | ADTOF generalises reasonably; optimise later based on user feedback |
| Frontend changes beyond model selector | Existing MIDI panel already handles drum tracks correctly |
| Quantized MIDI output toggle | Requires tempo detection; defer until users request grid-snapped timing |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| MIDI-01 | Phase 1 | Complete |
| MIDI-02 | Phase 1 | Complete |
| MIDI-03 | Phase 1 | Complete |
| ADT-01 | Phase 2 | Complete |
| ADT-02 | Phase 2 | Complete |
| ADT-03 | Phase 2 | Complete |
| ADT-04 | Phase 2 | Complete |
| REG-01 | Phase 1 | Complete |
| REG-02 | Phase 1 | Complete |
| REG-03 | Phase 4 | Complete |
| PIPE-01 | Phase 3 | Complete |
| PIPE-02 | Phase 3 | Complete |
| PIPE-03 | Phase 3 | Complete |
| PIPE-04 | Phase 3 | Complete |
| DEP-01 | Phase 1 | Complete |

**Coverage:**
- v1 requirements: 15 total
- Mapped to phases: 15
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-20*
*Last updated: 2026-03-20 after roadmap creation*
