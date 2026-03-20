"""
Tests for DrumMidiSpec registry entry and ADTOF drum model registration.

Phase 01 Plan 02 — RED phase.
"""
from __future__ import annotations


def test_drum_midi_spec_importable() -> None:
    """DrumMidiSpec is importable from models.registry and is a ModelSpec subclass."""
    from models.registry import DrumMidiSpec, ModelSpec

    assert isinstance(DrumMidiSpec, type)
    assert issubclass(DrumMidiSpec, ModelSpec)


def test_adtof_registry_entry() -> None:
    """list_specs(DrumMidiSpec) returns an entry for 'adtof-drums' with correct fields."""
    from models.registry import DrumMidiSpec, list_specs

    specs = list_specs(DrumMidiSpec)
    assert len(specs) >= 1

    match = next((s for s in specs if s.model_id == "adtof-drums"), None)
    assert match is not None, "No entry with model_id='adtof-drums' found"

    spec = match  # type: ignore[assignment]
    assert spec.class_count == 5
    assert spec.class_labels == ("kick", "snare", "tom", "hi_hat", "cymbal")
    assert spec.sample_rate == 44_100


def test_adtof_capabilities() -> None:
    """ADTOF registry entry has the correct capability tags."""
    from models.registry import get_spec

    spec = get_spec("adtof-drums")
    assert spec.capabilities == frozenset({"transcribe", "drum_transcription", "gpu_acceleration"})


def test_adtof_cache_subdir() -> None:
    """ADTOF registry entry uses cache_subdir='adtof'."""
    from models.registry import get_spec

    spec = get_spec("adtof-drums")
    assert spec.cache_subdir == "adtof"


def test_adtof_checkpoint_url_empty() -> None:
    """ADTOF checkpoint_url is empty (weights bundled in pip package)."""
    from models.registry import get_spec

    spec = get_spec("adtof-drums")
    assert spec.checkpoint_url == ""


def test_get_loader_kwargs_adtof() -> None:
    """get_loader_kwargs('adtof-drums') returns a dict containing 'cache_dir' without raising."""
    from models.registry import get_loader_kwargs

    kw = get_loader_kwargs("adtof-drums")
    assert isinstance(kw, dict)
    assert "cache_dir" in kw


def test_get_pipeline_defaults_adtof() -> None:
    """get_pipeline_defaults('adtof-drums') returns a dict containing 'class_count' without raising."""
    from models.registry import get_pipeline_defaults

    defaults = get_pipeline_defaults("adtof-drums")
    assert isinstance(defaults, dict)
    assert "class_count" in defaults


def test_get_gui_metadata_adtof() -> None:
    """get_gui_metadata('adtof-drums') returns a dict containing 'tooltip' without raising."""
    from models.registry import get_gui_metadata

    meta = get_gui_metadata("adtof-drums")
    assert isinstance(meta, dict)
    assert "tooltip" in meta
