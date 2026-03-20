"""Unit tests for utils.drum_map — GM drum constants and gm_note().

These tests are written FIRST (TDD RED phase) against the not-yet-existing
utils/drum_map module. All tests should FAIL with ImportError until
utils/drum_map.py is created (Task 2).
"""
from __future__ import annotations


def test_adtof_drum_class_enum() -> None:
    """AdtofDrumClass enum has exactly 5 members with correct integer values."""
    from utils.drum_map import AdtofDrumClass

    assert AdtofDrumClass.KICK == 0
    assert AdtofDrumClass.SNARE == 1
    assert AdtofDrumClass.TOM == 2
    assert AdtofDrumClass.HI_HAT == 3
    assert AdtofDrumClass.CYMBAL == 4
    assert len(AdtofDrumClass) == 5


def test_drum_map_adtof_5class() -> None:
    """ADTOF_5CLASS_GM_NOTE maps class indices to exact GM note numbers."""
    from utils.drum_map import ADTOF_5CLASS_GM_NOTE

    assert ADTOF_5CLASS_GM_NOTE == {0: 35, 1: 38, 2: 47, 3: 42, 4: 49}


def test_gm_note_non_sequential() -> None:
    """gm_note() preserves the non-sequential ordering: tom(47) > hi-hat(42)."""
    from utils.drum_map import AdtofDrumClass, gm_note

    # Tom (index 2) maps to GM note 47.
    assert gm_note(AdtofDrumClass.TOM) == 47
    # Hi-hat (index 3) maps to GM note 42 — numerically lower than tom despite higher index.
    assert gm_note(AdtofDrumClass.HI_HAT) == 42


def test_gm_note_all_classes() -> None:
    """gm_note() returns the correct GM note number for every AdtofDrumClass member."""
    from utils.drum_map import AdtofDrumClass, gm_note

    assert gm_note(AdtofDrumClass.KICK) == 35
    assert gm_note(AdtofDrumClass.SNARE) == 38
    assert gm_note(AdtofDrumClass.TOM) == 47
    assert gm_note(AdtofDrumClass.HI_HAT) == 42
    assert gm_note(AdtofDrumClass.CYMBAL) == 49


def test_gm_drum_names() -> None:
    """GM_DRUM_NAMES maps GM note numbers to human-readable percussion names."""
    from utils.drum_map import GM_DRUM_NAMES

    assert GM_DRUM_NAMES[35] == "Acoustic Bass Drum"
    assert GM_DRUM_NAMES[38] == "Acoustic Snare"
    assert GM_DRUM_NAMES[47] == "Mid Tom"
    assert GM_DRUM_NAMES[42] == "Closed Hi-Hat"
    assert GM_DRUM_NAMES[49] == "Crash Cymbal 1"
