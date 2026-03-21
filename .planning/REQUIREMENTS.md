# Requirements: Drum Stem to MIDI

**Defined:** 2026-03-20
**Core Value:** When a user separates a drum stem and extracts MIDI, the result must have both accurate onset timing and correct instrument classification — playable through FluidSynth preview without manual correction.

## v1.0 Requirements (Complete)

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

## v2.0 Requirements

Requirements for LarsNet Drum Sub-Separation milestone. Each maps to roadmap phases.

### Drum Sub-Separation

- [x] **SEP-01**: LarsNet model vendored and loadable from StemForge runtime environment
- [x] **SEP-02**: LarsNet separates drum stem into 5 per-instrument stereo sub-stems (kick, snare, hi-hat, toms, cymbals)
- [x] **SEP-03**: Sub-stems playable in MIDI panel with transport bar integration
- [ ] **SEP-04**: Sub-stems downloadable via Export tab (opt-in checkbox to avoid zip bloat)
- [ ] **SEP-05**: Wiener filter "Reduce crosstalk" toggle (fixed alpha=2) for high-bleed recordings
- [ ] **SEP-06**: Sub-stems forwarded to Mix and Enhance tabs via event bus

### MIDI Modes

- [x] **MODE-01**: Three runtime-selectable drum MIDI modes: ADTOF-only, LarsNet+ADTOF, LarsNet+onset-detection
- [ ] **MODE-02**: Mode selector in MIDI panel alongside existing ADT model selector
- [x] **MODE-03**: Selected mode persisted in session state
- [x] **MODE-04**: Energy-based onset detection backend for LarsNet+onset mode using librosa with per-class thresholds

### Guard Rails

- [x] **GUARD-01**: LarsNet modes disabled in UI when no drum stem is in session
- [x] **GUARD-02**: Graceful error with download instructions when LarsNet weights not found
- [ ] **GUARD-03**: Quality warnings for weak instrument classes (toms ~9 dB, cymbals ~4 dB nSDR) in UI tooltips

### Infrastructure

- [x] **INFRA-01**: LarsNetSpec registered in model registry with capabilities, cache subdir, license info
- [x] **INFRA-02**: LarsNet lazy-loaded on first use with evict() for GPU memory release
- [x] **INFRA-03**: LarsNet evicted before ADTOF loads in LarsNet+ADTOF mode (VRAM safety)
- [x] **INFRA-04**: Sub-stems stored in dedicated path (not mixed with primary stems in session)
- [x] **INFRA-05**: LarsNet weight download helper script using gdown

## Future Requirements

Deferred to future release. Tracked but not in current roadmap.

### Enhanced Output

- **EOUT-01**: Per-hit velocity from sub-stem RMS amplitude (1-127 range)
- **EOUT-02**: Per-hit pan from sub-stem stereo balance
- **EOUT-03**: 7-class output (open hi-hat + crash/ride split via sub-stem loudness heuristics)

### Additional Backends

- **BACK-01**: ADT_STR backend when public weights released and torch/CLAP conflicts resolved

### User Controls

- **CTRL-01**: Onset threshold slider in MIDI panel
- **CTRL-02**: GM note remap UI for custom percussion mapping
- **CTRL-03**: Continuous alpha slider for Wiener filtering (replacing binary toggle)

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| LarsNet on full mix (pre-separation) | Model trained on isolated drum mixes only; degrades unpredictably on full mixes |
| Real-time / streaming sub-separation | LarsNet uses fixed 512-frame windows; streaming requires overlap-add with non-trivial artifacts |
| Sub-stem quality metrics (SDR) in UI | Computing SDR requires ground-truth reference; model benchmarks would mislead |
| LarsNet fine-tuning on real recordings | Research problem; model trained on synthesized audio |
| Per-hit velocity from sub-stem amplitude | Correct but deferred — needs per-performance normalization context |
| Per-hit pan from sub-stem stereo | GM MIDI channel 10 has no per-note pan; representation mismatch |
| MusicXML / sheet music export | Not a producer-workflow need |
| Few-shot adaptation to custom drum kits | Research problem, no viable library today |
| Quantized MIDI output toggle | Requires tempo detection; defer until users request grid-snapped timing |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

### v1.0 (Complete)

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

### v2.0

| Requirement | Phase | Status |
|-------------|-------|--------|
| SEP-01 | Phase 5 | Complete |
| SEP-02 | Phase 7 | Complete |
| SEP-03 | Phase 9 | Complete |
| SEP-04 | Phase 11 | Pending |
| SEP-05 | Phase 11 | Pending |
| SEP-06 | Phase 10 | Pending |
| MODE-01 | Phase 8 | Complete |
| MODE-02 | Phase 10 | Pending |
| MODE-03 | Phase 8 | Complete |
| MODE-04 | Phase 6 | Complete |
| GUARD-01 | Phase 9 | Complete |
| GUARD-02 | Phase 9 | Complete |
| GUARD-03 | Phase 10 | Pending |
| INFRA-01 | Phase 5 | Complete |
| INFRA-02 | Phase 5 | Complete |
| INFRA-03 | Phase 7 | Complete |
| INFRA-04 | Phase 9 | Complete |
| INFRA-05 | Phase 5 | Complete |

**Coverage:**
- v1.0 requirements: 15 total, 15 complete
- v2.0 requirements: 18 total
- Mapped to phases: 18
- Unmapped: 0

---
*Requirements defined: 2026-03-20*
*Last updated: 2026-03-21 after v2.0 roadmap creation*
