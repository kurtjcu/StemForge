"""Unit tests for is_drum parameter and 60ms cap in utils.midi_io.notes_to_midi().

Written FIRST (TDD RED phase). Tests for is_drum=True behavior will fail with
TypeError until notes_to_midi() is extended with the is_drum parameter (Task 2).
"""
from __future__ import annotations

import pytest


def test_notes_to_midi_drum_channel() -> None:
    """notes_to_midi(events, is_drum=True) produces an instrument routed to channel 10."""
    from utils.midi_io import notes_to_midi

    result = notes_to_midi([(0.5, 1.5, 35, 100)], is_drum=True)

    assert result.instruments[0].is_drum is True
    assert result.instruments[0].name == "StemForge Drums"


def test_notes_to_midi_regression() -> None:
    """notes_to_midi() without is_drum produces identical output to pre-change behavior."""
    from utils.midi_io import notes_to_midi

    # Default call (no is_drum arg) — must match original Acoustic Grand Piano behavior.
    result_default = notes_to_midi([(0.5, 1.5, 60, 100)])
    assert result_default.instruments[0].is_drum is False
    assert result_default.instruments[0].program == 0
    assert result_default.instruments[0].name == "StemForge"

    # Explicit is_drum=False — must be identical to the default.
    result_explicit = notes_to_midi([(0.5, 1.5, 60, 100)], is_drum=False)
    assert result_explicit.instruments[0].is_drum is False
    assert result_explicit.instruments[0].program == 0
    assert result_explicit.instruments[0].name == "StemForge"


def test_drum_note_60ms_cap() -> None:
    """Drum notes longer than 60ms are clamped to exactly 60ms duration."""
    from utils.midi_io import notes_to_midi

    # Note with start=1.0, end=2.0 (1000ms) should be clamped to end=1.06.
    result = notes_to_midi([(1.0, 2.0, 35, 100)], is_drum=True)

    assert len(result.instruments[0].notes) == 1
    note = result.instruments[0].notes[0]
    assert note.end == pytest.approx(1.06, abs=1e-6)


def test_drum_note_short_passthrough() -> None:
    """Drum notes already shorter than 60ms are not modified."""
    from utils.midi_io import notes_to_midi

    # Note with start=1.0, end=1.03 (30ms) should pass through unchanged.
    result = notes_to_midi([(1.0, 1.03, 35, 100)], is_drum=True)

    assert len(result.instruments[0].notes) == 1
    note = result.instruments[0].notes[0]
    assert note.end == pytest.approx(1.03, abs=1e-6)


def test_drum_degenerate_note_filtered() -> None:
    """Degenerate notes (end <= start) are filtered before the 60ms cap is applied."""
    from utils.midi_io import notes_to_midi

    # Note with start=1.0, end=0.5 — end < start, must be discarded entirely.
    result = notes_to_midi([(1.0, 0.5, 35, 100)], is_drum=True)

    assert len(result.instruments[0].notes) == 0
