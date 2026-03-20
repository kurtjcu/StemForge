---
phase: 3
slug: loader-and-pipeline-wiring
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-20
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest >= 9.0.2 |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` |
| **Quick run command** | `python -m pytest tests/test_midi_loader_drum.py tests/test_midi_pipeline_routing.py -x -q` |
| **Full suite command** | `python -m pytest tests/ -x -q` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_midi_loader_drum.py tests/test_midi_pipeline_routing.py -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -x -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 03-01-01 | 01 | 1 | PIPE-01 | unit (mock loader) | `pytest tests/test_midi_pipeline_routing.py::test_drum_label_routes_to_adt -x` | No W0 | pending |
| 03-01-02 | 01 | 1 | PIPE-01 | unit (mock loader) | `pytest tests/test_midi_pipeline_routing.py::test_drum_stem_midi_is_drum_true -x` | No W0 | pending |
| 03-01-03 | 01 | 1 | PIPE-02 | unit (mock loader) | `pytest tests/test_midi_pipeline_routing.py::test_demucs_drum_label_routed -x` | No W0 | pending |
| 03-01-04 | 01 | 1 | PIPE-02 | unit (mock loader) | `pytest tests/test_midi_pipeline_routing.py::test_roformer_drum_label_routed -x` | No W0 | pending |
| 03-01-05 | 01 | 1 | PIPE-02 | unit (mock loader) | `pytest tests/test_midi_pipeline_routing.py::test_vocal_label_not_routed_to_drum -x` | No W0 | pending |
| 03-01-06 | 01 | 1 | PIPE-02 | unit (mock loader) | `pytest tests/test_midi_pipeline_routing.py::test_bass_label_not_routed_to_drum -x` | No W0 | pending |
| 03-01-07 | 01 | 1 | PIPE-03 | unit | `pytest tests/test_midi_loader_drum.py::test_adtof_lazy_not_loaded_at_init -x` | No W0 | pending |
| 03-01-08 | 01 | 1 | PIPE-03 | unit (mock backend) | `pytest tests/test_midi_loader_drum.py::test_evict_clears_adtof_backend -x` | No W0 | pending |
| 03-01-09 | 01 | 1 | PIPE-04 | unit (spy) | `pytest tests/test_midi_pipeline_routing.py::test_drum_path_reports_progress -x` | No W0 | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_midi_loader_drum.py` — 8 test functions covering PIPE-03 (lazy loading, eviction, conversion); uses mock AdtofBackend, no real audio or GPU needed
- [ ] `tests/test_midi_pipeline_routing.py` — 9 test functions covering PIPE-01, PIPE-02, PIPE-04 (routing, is_drum, progress, eviction); uses mock loader, no real audio or GPU needed
- [ ] `tests/conftest.py` — shared fixtures (if not already present)

*All tests use mocks — no actual audio files or GPU required.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| FluidSynth plays drum MIDI as audible percussion (kick, snare, hi-hat distinguishable) | PIPE-01, PIPE-02 | Audio quality judgment requires human ear | Upload drum stem -> Extract MIDI -> Play FluidSynth preview -> Verify percussion sounds correct |
| VRAM released after drum extraction (next Demucs job starts) | PIPE-03 | Requires GPU hardware + memory monitoring | Extract drum MIDI -> Immediately run Demucs separation -> Verify no CUDA OOM error |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
