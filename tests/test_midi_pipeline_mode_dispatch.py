"""Tests for MidiPipeline three-mode drum dispatch (Phase 8).

Tests for:
- adtof_only mode regression (existing convert_drum_to_midi path unchanged)
- larsnet_adtof mode (calls convert_drum_to_midi_with_larsnet, populates drum_sub_stems)
- larsnet_onset mode (calls separate_drums + OnsetBackend.detect x5 + evict_larsnet)
- drum_sub_stems population across all three modes
- Invalid drum_mode raises InvalidInputError
- Default drum_mode is 'adtof_only'

All tests expected to fail (RED) until pipelines/midi_pipeline.py is extended.
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch, call

import pytest
import torch

from pipelines.midi_pipeline import MidiPipeline, MidiConfig


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

_LARSNET_STEMS = ("kick", "snare", "toms", "hihat", "cymbals")


def _make_pipeline_with_mock_loader(tmp_path: pathlib.Path):
    """Create a MidiPipeline with a mock loader, bypassing load_model().

    The mock loader includes all Phase-7 methods needed for LarsNet modes.
    """
    pipeline = MidiPipeline()
    pipeline._config = MidiConfig()
    mock_loader = MagicMock()

    # Existing method (adtof_only path)
    mock_loader.convert_drum_to_midi.return_value = [(0.1, 0.16, 35, 100)]
    # Existing basic methods
    mock_loader.convert_audio_to_midi.return_value = [(0.1, 0.4, 60, 80)]
    mock_loader.convert_vocal_to_midi.return_value = ([(0.1, 0.5, 60, 80)], [])

    # Phase-7 methods — sub-stem paths keyed by LarsNet stem name
    sub_stem_paths = {k: tmp_path / f"{k}.wav" for k in _LARSNET_STEMS}
    # Create dummy files so the pipeline can "load" them
    for p in sub_stem_paths.values():
        p.write_bytes(b"\x00" * 44)

    mock_loader.convert_drum_to_midi_with_larsnet.return_value = (
        sub_stem_paths,
        [(0.1, 0.16, 35, 100)],
    )
    mock_loader.separate_drums.return_value = sub_stem_paths
    mock_loader.evict_larsnet.return_value = None

    pipeline._loader = mock_loader
    pipeline.is_loaded = True
    return pipeline, mock_loader, sub_stem_paths


# ---------------------------------------------------------------------------
# Test 1 (SC-1): adtof_only regression
# ---------------------------------------------------------------------------


def test_adtof_only_mode_regression(tmp_path: pathlib.Path) -> None:
    """adtof_only calls ONLY convert_drum_to_midi; LarsNet methods not called; drum_sub_stems == {}."""
    pipeline, mock_loader, _ = _make_pipeline_with_mock_loader(tmp_path)
    pipeline._config = MidiConfig(drum_mode="adtof_only")

    drums_file = tmp_path / "drums.wav"
    drums_file.write_bytes(b"\x00")

    result = pipeline.run({"drums": drums_file})

    # Only the original ADTOF method is called
    assert mock_loader.convert_drum_to_midi.called, (
        "convert_drum_to_midi() must be called in adtof_only mode"
    )
    assert not mock_loader.convert_drum_to_midi_with_larsnet.called, (
        "convert_drum_to_midi_with_larsnet() must NOT be called in adtof_only mode"
    )
    assert not mock_loader.separate_drums.called, (
        "separate_drums() must NOT be called in adtof_only mode"
    )

    # drum_sub_stems is empty
    assert result.drum_sub_stems == {}, (
        f"drum_sub_stems must be empty dict for adtof_only, got {result.drum_sub_stems!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 (SC-2): larsnet_adtof calls convert_drum_to_midi_with_larsnet
# ---------------------------------------------------------------------------


def test_larsnet_adtof_calls_loader(tmp_path: pathlib.Path) -> None:
    """larsnet_adtof calls convert_drum_to_midi_with_larsnet; drum_sub_stems has 5 keys."""
    pipeline, mock_loader, _ = _make_pipeline_with_mock_loader(tmp_path)
    pipeline._config = MidiConfig(drum_mode="larsnet_adtof")

    drums_file = tmp_path / "drums.wav"
    drums_file.write_bytes(b"\x00" * 44)

    dummy_tensor = torch.zeros(2, 44100)

    with patch("pipelines.midi_pipeline._load_drum_tensor", return_value=dummy_tensor):
        result = pipeline.run({"drums": drums_file}, job_id="test-job")

    # Must call the LarsNet+ADTOF method
    assert mock_loader.convert_drum_to_midi_with_larsnet.called, (
        "convert_drum_to_midi_with_larsnet() must be called in larsnet_adtof mode"
    )
    # Must NOT call the separate_drums path
    assert not mock_loader.separate_drums.called, (
        "separate_drums() must NOT be called in larsnet_adtof mode"
    )
    # Must NOT call plain adtof
    assert not mock_loader.convert_drum_to_midi.called, (
        "convert_drum_to_midi() must NOT be called in larsnet_adtof mode"
    )

    # drum_sub_stems has 5 entries matching LARSNET_STEM_KEYS
    assert len(result.drum_sub_stems) == 5, (
        f"drum_sub_stems must have 5 keys for larsnet_adtof, got {len(result.drum_sub_stems)}"
    )
    assert set(result.drum_sub_stems.keys()) == set(_LARSNET_STEMS), (
        f"drum_sub_stems keys must match LARSNET_STEM_KEYS, got {set(result.drum_sub_stems.keys())!r}"
    )


# ---------------------------------------------------------------------------
# Test 3 (SC-3): larsnet_onset calls separate_drums + OnsetBackend.detect x5 + evict_larsnet
# ---------------------------------------------------------------------------


def test_larsnet_onset_routes_to_onset_backend(tmp_path: pathlib.Path) -> None:
    """larsnet_onset calls separate_drums, OnsetBackend.detect 5 times, evict_larsnet; drum_sub_stems has 5 keys."""
    pipeline, mock_loader, sub_stem_paths = _make_pipeline_with_mock_loader(tmp_path)
    pipeline._config = MidiConfig(drum_mode="larsnet_onset")

    drums_file = tmp_path / "drums.wav"
    drums_file.write_bytes(b"\x00" * 44)

    dummy_tensor = torch.zeros(2, 44100)
    mock_onset_instance = MagicMock()
    mock_onset_instance.detect.return_value = [(0.5, 0.56, 35, 100)]

    with patch("pipelines.midi_pipeline._load_drum_tensor", return_value=dummy_tensor), \
         patch("pipelines.midi_pipeline.OnsetBackend", return_value=mock_onset_instance) as MockOnsetBackend:
        result = pipeline.run({"drums": drums_file}, job_id="test-job")

    # separate_drums must be called
    assert mock_loader.separate_drums.called, (
        "separate_drums() must be called in larsnet_onset mode"
    )
    # evict_larsnet must be called after separation
    assert mock_loader.evict_larsnet.called, (
        "evict_larsnet() must be called in larsnet_onset mode after separate_drums"
    )
    # OnsetBackend.detect must be called once per sub-stem (5 times)
    assert mock_onset_instance.detect.call_count == 5, (
        f"OnsetBackend.detect() must be called 5 times (once per sub-stem), "
        f"got {mock_onset_instance.detect.call_count}"
    )
    # Must NOT call LarsNet+ADTOF path
    assert not mock_loader.convert_drum_to_midi_with_larsnet.called, (
        "convert_drum_to_midi_with_larsnet() must NOT be called in larsnet_onset mode"
    )
    # Must NOT call plain ADTOF
    assert not mock_loader.convert_drum_to_midi.called, (
        "convert_drum_to_midi() must NOT be called in larsnet_onset mode"
    )

    # drum_sub_stems has 5 entries
    assert len(result.drum_sub_stems) == 5, (
        f"drum_sub_stems must have 5 keys for larsnet_onset, got {len(result.drum_sub_stems)}"
    )
    assert set(result.drum_sub_stems.keys()) == set(_LARSNET_STEMS), (
        f"drum_sub_stems keys must match LARSNET_STEM_KEYS, got {set(result.drum_sub_stems.keys())!r}"
    )


# ---------------------------------------------------------------------------
# Test 4 (SC-4): drum_sub_stems population across all three modes
# ---------------------------------------------------------------------------


def test_drum_sub_stems_populated_for_larsnet_modes(tmp_path: pathlib.Path) -> None:
    """adtof_only -> drum_sub_stems == {}; larsnet_adtof -> 5 keys; larsnet_onset -> 5 keys."""
    dummy_tensor = torch.zeros(2, 44100)
    mock_onset_instance = MagicMock()
    mock_onset_instance.detect.return_value = [(0.5, 0.56, 35, 100)]

    drums_file = tmp_path / "drums.wav"
    drums_file.write_bytes(b"\x00" * 44)

    # --- adtof_only ---
    pipeline, mock_loader, _ = _make_pipeline_with_mock_loader(tmp_path)
    pipeline._config = MidiConfig(drum_mode="adtof_only")
    result_adtof_only = pipeline.run({"drums": drums_file})
    assert result_adtof_only.drum_sub_stems == {}, (
        f"adtof_only must yield empty drum_sub_stems, got {result_adtof_only.drum_sub_stems!r}"
    )

    # --- larsnet_adtof ---
    pipeline, mock_loader, _ = _make_pipeline_with_mock_loader(tmp_path)
    pipeline._config = MidiConfig(drum_mode="larsnet_adtof")
    with patch("pipelines.midi_pipeline._load_drum_tensor", return_value=dummy_tensor):
        result_larsnet_adtof = pipeline.run({"drums": drums_file}, job_id="test-job")
    assert len(result_larsnet_adtof.drum_sub_stems) == 5, (
        f"larsnet_adtof must yield 5 drum_sub_stems, got {len(result_larsnet_adtof.drum_sub_stems)}"
    )

    # --- larsnet_onset ---
    pipeline, mock_loader, _ = _make_pipeline_with_mock_loader(tmp_path)
    pipeline._config = MidiConfig(drum_mode="larsnet_onset")
    with patch("pipelines.midi_pipeline._load_drum_tensor", return_value=dummy_tensor), \
         patch("pipelines.midi_pipeline.OnsetBackend", return_value=mock_onset_instance):
        result_larsnet_onset = pipeline.run({"drums": drums_file}, job_id="test-job")
    assert len(result_larsnet_onset.drum_sub_stems) == 5, (
        f"larsnet_onset must yield 5 drum_sub_stems, got {len(result_larsnet_onset.drum_sub_stems)}"
    )


# ---------------------------------------------------------------------------
# Test 5: Invalid drum_mode raises InvalidInputError
# ---------------------------------------------------------------------------


def test_invalid_drum_mode_raises() -> None:
    """MidiConfig(drum_mode='bogus') raises InvalidInputError with field='drum_mode'."""
    from utils.errors import InvalidInputError

    with pytest.raises(InvalidInputError, match="drum_mode"):
        MidiConfig(drum_mode="bogus")


# ---------------------------------------------------------------------------
# Test 6: Default drum_mode is 'adtof_only'
# ---------------------------------------------------------------------------


def test_default_drum_mode_is_adtof_only() -> None:
    """MidiConfig() without drum_mode argument has drum_mode == 'adtof_only'."""
    cfg = MidiConfig()
    assert cfg.drum_mode == "adtof_only", (
        f"Default drum_mode must be 'adtof_only', got {cfg.drum_mode!r}"
    )


# ---------------------------------------------------------------------------
# Test 7: drum_mode persisted in SessionStore
# ---------------------------------------------------------------------------


def test_drum_mode_persisted_in_session():
    """drum_mode is persisted to SessionStore and survives clear/re-set cycle."""
    from backend.services.session_store import SessionStore
    session = SessionStore()

    # Default
    assert session.drum_mode == "adtof_only"

    # Set and read back
    session.drum_mode = "larsnet_adtof"
    assert session.drum_mode == "larsnet_adtof"

    # Verify in to_dict
    d = session.to_dict()
    assert d["drum_mode"] == "larsnet_adtof"

    # Clear resets to default
    session.clear()
    assert session.drum_mode == "adtof_only"

    # Set to onset mode
    session.drum_mode = "larsnet_onset"
    assert session.drum_mode == "larsnet_onset"


# ---------------------------------------------------------------------------
# Test 8: ExtractRequest accepts drum_mode field
# ---------------------------------------------------------------------------


def test_extract_request_accepts_drum_mode():
    """ExtractRequest model includes drum_mode field."""
    from backend.api.midi import ExtractRequest
    req = ExtractRequest(stems=["drums"], drum_mode="larsnet_adtof")
    assert req.drum_mode == "larsnet_adtof"

    # Default
    req_default = ExtractRequest(stems=["drums"])
    assert req_default.drum_mode == "adtof_only"
