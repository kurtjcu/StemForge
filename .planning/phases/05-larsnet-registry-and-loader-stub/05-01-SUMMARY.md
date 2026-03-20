---
phase: 05-larsnet-registry-and-loader-stub
plan: 01
subsystem: infra
tags: [larsnet, model-registry, vendor, drum-separation, pytorch]

# Dependency graph
requires: []
provides:
  - LarsNetSpec frozen dataclass in models/registry.py
  - LARSNET_DRUMS registry entry with model_id='larsnet-drums'
  - LARSNET_STEM_KEYS constant = ('kick', 'snare', 'toms', 'hihat', 'cymbals')
  - vendor/larsnet/ package with LarsNet class importable via sys.path
  - get_loader_kwargs / get_pipeline_defaults / get_gui_metadata dispatch for LarsNetSpec
affects:
  - 05-02 (loader stub depends on LarsNetSpec and vendor/larsnet/)
  - 06 (pipeline wiring needs LarsNetSpec registry entry)
  - backend API endpoints that serve model metadata

# Tech tracking
tech-stack:
  added:
    - polimi-ispl/larsnet (vendored, MIT code + CC BY-NC 4.0 weights)
  patterns:
    - Vendor package with sys.path insertion to support flat upstream imports
    - LarsNetSpec as frozen dataclass extending ModelSpec with stem_keys + checkpoint_count
    - LARSNET_STEM_KEYS canonical constant sourced from config.yaml, NOT from ADTOF labels

key-files:
  created:
    - vendor/larsnet/__init__.py
    - vendor/larsnet/larsnet.py
    - vendor/larsnet/unet.py
    - vendor/larsnet/config.yaml
    - tests/test_larsnet_spec.py
  modified:
    - models/registry.py
    - vendor/__init__.py

key-decisions:
  - "Add vendor/larsnet/ directory to sys.path inside __init__.py so upstream flat imports (from unet import ...) work without patching upstream files"
  - "LarsNetSpec placed before RoformerSpec in registry.py to keep separation-related specs grouped after DrumMidiSpec"
  - "LARSNET_STEM_KEYS uses 'toms'/'hihat'/'cymbals' — differs from ADTOF labels ('tom'/'hi_hat'/'cymbal')"

patterns-established:
  - "Vendor flat-import fix: add _pkg_dir to sys.path in vendor package __init__.py before importing the module"
  - "TDD red-commit before any implementation files exist"

requirements-completed: [INFRA-01, SEP-01]

# Metrics
duration: 8min
completed: 2026-03-20
---

# Phase 05 Plan 01: LarsNet Registry and Vendor Package Summary

**LarsNetSpec frozen dataclass in model registry with 5-stem drum sub-separation entry, vendored larsnet.py/unet.py/config.yaml from polimi-ispl/larsnet@main**

## Performance

- **Duration:** ~8 min
- **Started:** 2026-03-20T23:41:00Z
- **Completed:** 2026-03-20T23:49:32Z
- **Tasks:** 1 (TDD: RED + GREEN)
- **Files modified:** 6

## Accomplishments

- Vendored larsnet.py (134 lines), unet.py (257 lines), config.yaml from polimi-ispl/larsnet@main without modification
- Added LarsNetSpec frozen dataclass with stem_keys and checkpoint_count fields to models/registry.py
- Registered LARSNET_DRUMS with device='cpu', CC BY-NC 4.0 license warning, 5 U-Net checkpoints
- Defined LARSNET_STEM_KEYS = ('kick', 'snare', 'toms', 'hihat', 'cymbals') sourced from config.yaml
- Added get_loader_kwargs / get_pipeline_defaults / get_gui_metadata dispatch branches for LarsNetSpec
- 14 passing tests covering all spec fields and vendor import

## Task Commits

1. **Task 1 RED: test(05-01) — failing tests for LarsNetSpec** - `8e53a3e` (test)
2. **Task 1 GREEN: feat(05-01) — vendor LarsNet + register LarsNetSpec** - `f54d82b` (feat)

## Files Created/Modified

- `vendor/larsnet/__init__.py` — Package entry point; adds vendor/larsnet/ to sys.path for flat upstream imports; re-exports LarsNet
- `vendor/larsnet/larsnet.py` — Upstream LarsNet class (unmodified, 134 lines)
- `vendor/larsnet/unet.py` — Upstream U-Net architecture (unmodified, 257 lines)
- `vendor/larsnet/config.yaml` — Model config with inference_models and relative checkpoint paths
- `vendor/__init__.py` — Added larsnet attribution comment block
- `models/registry.py` — LarsNetSpec class, LARSNET_STEM_KEYS constant, LARSNET_DRUMS registration, helper function dispatch
- `tests/test_larsnet_spec.py` — 14 tests for registry spec fields and vendor import

## Decisions Made

- **sys.path insertion over patching upstream**: The upstream larsnet.py uses `from unet import ...` (flat import). Rather than patching the upstream file, we add the package directory itself to sys.path inside `__init__.py`. This preserves upstream source unchanged and is the same pattern used by other vendored packages in the project.
- **Stem key naming follows config.yaml, not ADTOF**: LARSNET_STEM_KEYS = ('kick', 'snare', 'toms', 'hihat', 'cymbals') — note 'toms' not 'tom', 'hihat' not 'hi_hat', 'cymbals' not 'cymbal'. This is critical for Phase 6 pipeline alignment.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added sys.path insertion in vendor/larsnet/__init__.py**
- **Found during:** Task 1 GREEN (vendor package import test)
- **Issue:** Upstream larsnet.py uses `from unet import UNetUtils, UNet, UNetWaveform` (flat absolute import). When imported as a Python package from `vendor/`, Python looks for `unet` as a top-level module and fails.
- **Fix:** Added `sys.path.insert(0, _pkg_dir)` in `__init__.py` before `from .larsnet import LarsNet`, where `_pkg_dir` is the `vendor/larsnet/` directory absolute path. Upstream source files remain fully unmodified.
- **Files modified:** vendor/larsnet/__init__.py
- **Verification:** All 14 tests pass including `test_vendor_larsnet_importable`
- **Committed in:** f54d82b (Task 1 GREEN commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Auto-fix required for correctness — upstream flat import pattern incompatible with Python packaging. Fix preserves upstream source integrity.

## Issues Encountered

None beyond the auto-fixed import issue above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- LarsNetSpec and LARSNET_STEM_KEYS are the foundation for Phase 05 Plan 02 (loader stub)
- vendor/larsnet/ package is importable; torch/torchaudio deps already in venv
- config.yaml checkpoint paths are relative — Phase 06 pipeline must resolve to absolute paths using `__file__`
- LarsNet weights are not downloaded here; download/gdown integration is Plan 02's concern

## Self-Check: PASSED

All created files verified present on disk. Task commits 8e53a3e and f54d82b verified in git log.

---
*Phase: 05-larsnet-registry-and-loader-stub*
*Completed: 2026-03-20*
