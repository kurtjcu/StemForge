"""
Tests for LarsNetSpec registry entry and vendor package.

Phase 05 Plan 01 — TDD RED phase.
"""
from __future__ import annotations


def test_larsnet_spec_importable() -> None:
    """LarsNetSpec is importable from models.registry and is a ModelSpec subclass."""
    from models.registry import LarsNetSpec, ModelSpec

    assert isinstance(LarsNetSpec, type)
    assert issubclass(LarsNetSpec, ModelSpec)


def test_larsnet_registry_entry_exists() -> None:
    """list_specs(LarsNetSpec) returns an entry with model_id 'larsnet-drums'."""
    from models.registry import LarsNetSpec, list_specs

    specs = list_specs(LarsNetSpec)
    assert len(specs) >= 1

    match = next((s for s in specs if s.model_id == "larsnet-drums"), None)
    assert match is not None, "No entry with model_id='larsnet-drums' found"


def test_larsnet_stem_keys() -> None:
    """Registry entry has stem_keys == ('kick', 'snare', 'toms', 'hihat', 'cymbals')."""
    from models.registry import get_spec

    spec = get_spec("larsnet-drums")
    assert spec.stem_keys == ("kick", "snare", "toms", "hihat", "cymbals")


def test_larsnet_checkpoint_count() -> None:
    """Registry entry has checkpoint_count == 5."""
    from models.registry import get_spec

    spec = get_spec("larsnet-drums")
    assert spec.checkpoint_count == 5


def test_larsnet_capabilities() -> None:
    """Registry entry has capabilities == frozenset({'separate', 'drum_sub_separation'})."""
    from models.registry import get_spec

    spec = get_spec("larsnet-drums")
    assert spec.capabilities == frozenset({"separate", "drum_sub_separation"})


def test_larsnet_license_warning() -> None:
    """Registry entry has license_warning mentioning CC BY-NC 4.0."""
    from models.registry import get_spec

    spec = get_spec("larsnet-drums")
    assert "CC BY-NC 4.0" in spec.license_warning


def test_larsnet_device_and_cache_subdir() -> None:
    """Registry entry has device == 'cpu' and cache_subdir == 'larsnet'."""
    from models.registry import get_spec

    spec = get_spec("larsnet-drums")
    assert spec.device == "cpu"
    assert spec.cache_subdir == "larsnet"


def test_larsnet_sample_rate() -> None:
    """Registry entry has sample_rate == 44100."""
    from models.registry import get_spec

    spec = get_spec("larsnet-drums")
    assert spec.sample_rate == 44_100


def test_larsnet_stem_keys_constant() -> None:
    """LARSNET_STEM_KEYS constant equals ('kick', 'snare', 'toms', 'hihat', 'cymbals')."""
    from models.registry import LARSNET_STEM_KEYS

    assert LARSNET_STEM_KEYS == ("kick", "snare", "toms", "hihat", "cymbals")


def test_get_spec_larsnet() -> None:
    """get_spec('larsnet-drums') returns the LarsNetSpec instance."""
    from models.registry import LarsNetSpec, get_spec

    spec = get_spec("larsnet-drums")
    assert isinstance(spec, LarsNetSpec)


def test_get_loader_kwargs_larsnet() -> None:
    """get_loader_kwargs('larsnet-drums') returns dict with 'cache_dir' key."""
    from models.registry import get_loader_kwargs

    kw = get_loader_kwargs("larsnet-drums")
    assert isinstance(kw, dict)
    assert "cache_dir" in kw


def test_get_pipeline_defaults_larsnet() -> None:
    """get_pipeline_defaults('larsnet-drums') returns dict with 'stem_keys' key."""
    from models.registry import get_pipeline_defaults

    defaults = get_pipeline_defaults("larsnet-drums")
    assert isinstance(defaults, dict)
    assert "stem_keys" in defaults


def test_get_gui_metadata_larsnet() -> None:
    """get_gui_metadata('larsnet-drums') returns dict with 'stem_keys' and 'model_choices' keys."""
    from models.registry import get_gui_metadata

    meta = get_gui_metadata("larsnet-drums")
    assert isinstance(meta, dict)
    assert "stem_keys" in meta
    assert "model_choices" in meta


def test_vendor_larsnet_importable() -> None:
    """from larsnet import LarsNet succeeds (vendor package is importable)."""
    import os
    import sys

    vendor_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor"
    )
    if vendor_dir not in sys.path:
        sys.path.insert(0, vendor_dir)

    from larsnet import LarsNet  # noqa: F401

    assert LarsNet is not None
