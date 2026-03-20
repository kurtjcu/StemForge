"""Tests for MidiPipeline drum stem routing branch.

RED phase: Tests for _DRUM_STEM_LABELS, drum routing in run(), is_drum parameter
on _build_stem_midi(), 3-stage progress callbacks, and post-loop eviction.
All tests expected to fail until pipelines/midi_pipeline.py is extended.
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, call

import pytest

from pipelines.midi_pipeline import MidiPipeline, MidiConfig


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------


def _make_pipeline_with_mock_loader():
    """Create a MidiPipeline with a mock loader, bypassing load_model()."""
    pipeline = MidiPipeline()
    pipeline._config = MidiConfig()
    mock_loader = MagicMock()
    # convert_drum_to_midi returns one kick note event
    mock_loader.convert_drum_to_midi.return_value = [(0.1, 0.16, 35, 100)]
    # convert_audio_to_midi returns one pitched note
    mock_loader.convert_audio_to_midi.return_value = [(0.1, 0.4, 60, 80)]
    # convert_vocal_to_midi returns (notes, lyrics)
    mock_loader.convert_vocal_to_midi.return_value = ([(0.1, 0.5, 60, 80)], [])
    pipeline._loader = mock_loader
    pipeline.is_loaded = True
    return pipeline, mock_loader


# ---------------------------------------------------------------------------
# Routing tests
# ---------------------------------------------------------------------------


def test_drum_label_routes_to_adt(tmp_path: pathlib.Path) -> None:
    """A stem labeled 'drums' calls convert_drum_to_midi(), not convert_audio_to_midi()."""
    pipeline, mock_loader = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "drums.wav"
    tmp_file.write_bytes(b"\x00")

    pipeline.run({"drums": tmp_file})

    assert mock_loader.convert_drum_to_midi.called, (
        "convert_drum_to_midi() should be called for 'drums' label"
    )
    assert not mock_loader.convert_audio_to_midi.called, (
        "convert_audio_to_midi() should NOT be called for 'drums' label"
    )


def test_roformer_drum_label_routes_to_adt(tmp_path: pathlib.Path) -> None:
    """A stem labeled 'Drums & percussion' calls convert_drum_to_midi(), not convert_audio_to_midi()."""
    pipeline, mock_loader = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "drums_perc.wav"
    tmp_file.write_bytes(b"\x00")

    pipeline.run({"Drums & percussion": tmp_file})

    assert mock_loader.convert_drum_to_midi.called, (
        "convert_drum_to_midi() should be called for 'Drums & percussion' label"
    )
    assert not mock_loader.convert_audio_to_midi.called, (
        "convert_audio_to_midi() should NOT be called for 'Drums & percussion' label"
    )


def test_vocal_label_not_routed_to_drum(tmp_path: pathlib.Path) -> None:
    """A stem labeled 'vocals' calls convert_vocal_to_midi(), not convert_drum_to_midi()."""
    pipeline, mock_loader = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "vocals.wav"
    tmp_file.write_bytes(b"\x00")

    pipeline.run({"vocals": tmp_file})

    assert mock_loader.convert_vocal_to_midi.called, (
        "convert_vocal_to_midi() should be called for 'vocals' label"
    )
    assert not mock_loader.convert_drum_to_midi.called, (
        "convert_drum_to_midi() should NOT be called for 'vocals' label"
    )


def test_bass_label_not_routed_to_drum(tmp_path: pathlib.Path) -> None:
    """A stem labeled 'bass' calls convert_audio_to_midi(), not convert_drum_to_midi()."""
    pipeline, mock_loader = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "bass.wav"
    tmp_file.write_bytes(b"\x00")

    pipeline.run({"bass": tmp_file})

    assert mock_loader.convert_audio_to_midi.called, (
        "convert_audio_to_midi() should be called for 'bass' label"
    )
    assert not mock_loader.convert_drum_to_midi.called, (
        "convert_drum_to_midi() should NOT be called for 'bass' label"
    )


# ---------------------------------------------------------------------------
# is_drum parameter tests
# ---------------------------------------------------------------------------


def test_drum_stem_midi_is_drum_true(tmp_path: pathlib.Path) -> None:
    """Drum stem result has instruments[0].is_drum == True."""
    pipeline, mock_loader = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "drums.wav"
    tmp_file.write_bytes(b"\x00")

    result = pipeline.run({"drums": tmp_file})

    midi_obj = result.stem_midi_data["drums"]
    assert len(midi_obj.instruments) > 0, "MIDI object should have at least one instrument"
    assert midi_obj.instruments[0].is_drum is True, (
        "Drum stem MIDI should have is_drum=True on instrument[0]"
    )


def test_non_drum_stem_midi_is_drum_false(tmp_path: pathlib.Path) -> None:
    """Non-drum stem result has instruments[0].is_drum == False."""
    pipeline, mock_loader = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "bass.wav"
    tmp_file.write_bytes(b"\x00")

    result = pipeline.run({"bass": tmp_file})

    midi_obj = result.stem_midi_data["bass"]
    assert len(midi_obj.instruments) > 0, "MIDI object should have at least one instrument"
    assert midi_obj.instruments[0].is_drum is False, (
        "Non-drum stem MIDI should have is_drum=False on instrument[0]"
    )


# ---------------------------------------------------------------------------
# 3-stage progress callback test
# ---------------------------------------------------------------------------


def test_drum_path_reports_progress_3_stages(tmp_path: pathlib.Path) -> None:
    """_report() is called at least 3 times specifically during drum stem processing.

    Per RESEARCH.md Pitfall 6, the drum branch must report at 3 stages:
    (1) before _ensure_adtof() / convert_drum_to_midi() — before loading
    (2) after convert_drum_to_midi() returns — after loading/predict
    (3) after predict (done) — final per-stem marker
    """
    pipeline, mock_loader = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "drums.wav"
    tmp_file.write_bytes(b"\x00")

    progress_callback = MagicMock()
    pipeline._progress_callback = progress_callback

    pipeline.run({"drums": tmp_file})

    # Overall pipeline reports: initial 2.0, base_pct at start of stem, plus
    # at least 3 drum-specific stages, plus 80.0 and 100.0 at the end.
    # Total must be at least 5 (2 framing + 3 drum-specific).
    assert progress_callback.call_count >= 5, (
        f"Expected at least 5 _report() calls during drum stem processing, "
        f"got {progress_callback.call_count}"
    )

    # Extract all percentage values passed to the callback
    reported_pcts = [c.args[0] for c in progress_callback.call_args_list]

    # The drum branch must fire 3 calls in quick succession inside the stem loop.
    # All drum-branch calls occur after base_pct (5.0 for 1 stem) and before 80.0.
    drum_branch_calls = [p for p in reported_pcts if 5.0 < p < 80.0]
    assert len(drum_branch_calls) >= 3, (
        f"Expected at least 3 progress callbacks in the drum branch range (5.0 < pct < 80.0), "
        f"got {len(drum_branch_calls)}: {drum_branch_calls}"
    )


# ---------------------------------------------------------------------------
# Eviction tests
# ---------------------------------------------------------------------------


def test_drum_evict_called_after_loop(tmp_path: pathlib.Path) -> None:
    """evict_drum_model() is called after the stems loop when drum stems were present."""
    pipeline, mock_loader = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "drums.wav"
    tmp_file.write_bytes(b"\x00")

    pipeline.run({"drums": tmp_file})

    assert mock_loader.evict_drum_model.called, (
        "evict_drum_model() should be called after processing drum stems"
    )


def test_no_drum_evict_when_no_drums(tmp_path: pathlib.Path) -> None:
    """evict_drum_model() is NOT called when no drum stems in request."""
    pipeline, mock_loader = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "bass.wav"
    tmp_file.write_bytes(b"\x00")

    pipeline.run({"bass": tmp_file})

    assert not mock_loader.evict_drum_model.called, (
        "evict_drum_model() should NOT be called when no drum stems are present"
    )
