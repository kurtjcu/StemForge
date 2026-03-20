"""Integration tests for drum MIDI channel routing and API endpoint.

Tests:
  - test_drum_stem_writes_channel_10: drum stem -> is_drum=True, survives round-trip
  - test_non_drum_stem_writes_channel_1: non-drum stem -> is_drum=False, survives round-trip
  - test_gm_programs_includes_adt_models: /api/midi/gm-programs returns adt_models list

RED phase notes:
  - test_drum_stem_writes_channel_10 and test_non_drum_stem_writes_channel_1 should
    already pass (channel routing is in place from Phase 3).
  - test_gm_programs_includes_adt_models MUST fail until backend/api/midi.py is
    extended to include the adt_models key.
"""
from __future__ import annotations

import pathlib
import tempfile

import pretty_midi
import pytest
from unittest.mock import MagicMock

from pipelines.midi_pipeline import MidiPipeline, MidiConfig


# ---------------------------------------------------------------------------
# Test infrastructure — reused from test_midi_pipeline_routing.py pattern
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
# Channel routing integration tests (with round-trip serialization)
# ---------------------------------------------------------------------------


def test_drum_stem_writes_channel_10(tmp_path: pathlib.Path) -> None:
    """Drum stem produces is_drum=True in-memory and after MIDI file round-trip."""
    pipeline, _ = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "drums.wav"
    tmp_file.write_bytes(b"\x00")

    result = pipeline.run({"drums": tmp_file})

    midi_obj = result.stem_midi_data["drums"]
    assert len(midi_obj.instruments) > 0, "MIDI object should have at least one instrument"

    # In-memory assertion
    assert midi_obj.instruments[0].is_drum is True, (
        "Drum stem MIDI should have is_drum=True on instrument[0] in memory"
    )

    # Round-trip: write to .mid file, read back, verify is_drum survives
    mid_path = tmp_path / "drums_roundtrip.mid"
    midi_obj.write(str(mid_path))
    reloaded = pretty_midi.PrettyMIDI(str(mid_path))
    assert len(reloaded.instruments) > 0, "Round-tripped MIDI should have at least one instrument"
    assert reloaded.instruments[0].is_drum is True, (
        "Drum stem MIDI should have is_drum=True after MIDI file round-trip"
    )


def test_non_drum_stem_writes_channel_1(tmp_path: pathlib.Path) -> None:
    """Non-drum stem produces is_drum=False in-memory and after MIDI file round-trip."""
    pipeline, _ = _make_pipeline_with_mock_loader()
    tmp_file = tmp_path / "bass.wav"
    tmp_file.write_bytes(b"\x00")

    result = pipeline.run({"bass": tmp_file})

    midi_obj = result.stem_midi_data["bass"]
    assert len(midi_obj.instruments) > 0, "MIDI object should have at least one instrument"

    # In-memory assertion
    assert midi_obj.instruments[0].is_drum is False, (
        "Non-drum stem MIDI should have is_drum=False on instrument[0] in memory"
    )

    # Round-trip: write to .mid file, read back, verify is_drum survives
    mid_path = tmp_path / "bass_roundtrip.mid"
    midi_obj.write(str(mid_path))
    reloaded = pretty_midi.PrettyMIDI(str(mid_path))
    assert len(reloaded.instruments) > 0, "Round-tripped MIDI should have at least one instrument"
    assert reloaded.instruments[0].is_drum is False, (
        "Non-drum stem MIDI should have is_drum=False after MIDI file round-trip"
    )


# ---------------------------------------------------------------------------
# API endpoint test: /api/midi/gm-programs must include adt_models
# ---------------------------------------------------------------------------


def test_gm_programs_includes_adt_models() -> None:
    """GET /api/midi/gm-programs returns 200 with adt_models key containing adtof-drums."""
    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    response = client.get("/api/midi/gm-programs")

    assert response.status_code == 200, (
        f"Expected 200 from /api/midi/gm-programs, got {response.status_code}"
    )

    data = response.json()
    assert "adt_models" in data, (
        f"Response missing 'adt_models' key. Keys present: {list(data.keys())}"
    )

    adt_models = data["adt_models"]
    assert isinstance(adt_models, list), (
        f"Expected adt_models to be a list, got {type(adt_models)}"
    )

    model_ids = [entry.get("model_id") for entry in adt_models]
    assert "adtof-drums" in model_ids, (
        f"Expected 'adtof-drums' in adt_models list. Got: {model_ids}"
    )
