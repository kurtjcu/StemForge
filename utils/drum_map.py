"""GM drum mapping constants for automatic drum transcription.

Provides the canonical mapping between ADTOF model output class indices
and General MIDI percussion note numbers (channel 10).
"""
from __future__ import annotations

from enum import IntEnum


class AdtofDrumClass(IntEnum):
    """ADTOF 5-class drum output indices.

    Values match the model's output channel ordering from
    ``xavriley/ADTOF-pytorch`` ``LABELS_5 = [35, 38, 47, 42, 49]``.
    """
    KICK   = 0
    SNARE  = 1
    TOM    = 2
    HI_HAT = 3
    CYMBAL = 4


# Internal mapping: AdtofDrumClass -> GM note number.
# NON-SEQUENTIAL: index 2 (tom) maps to 47, index 3 (hi-hat) maps to 42.
# This ordering is preserved from ADTOF model training — do not sort numerically.
_ADTOF_GM: dict[AdtofDrumClass, int] = {
    AdtofDrumClass.KICK:   35,   # Acoustic Bass Drum
    AdtofDrumClass.SNARE:  38,   # Acoustic Snare
    AdtofDrumClass.TOM:    47,   # Mid Tom
    AdtofDrumClass.HI_HAT: 42,   # Closed Hi-Hat
    AdtofDrumClass.CYMBAL: 49,   # Crash Cymbal 1
}

# Flat {int_index: gm_note} dict for downstream consumers.
ADTOF_5CLASS_GM_NOTE: dict[int, int] = {int(k): v for k, v in _ADTOF_GM.items()}

# Human-readable GM drum names for logging and Phase 4 UI.
GM_DRUM_NAMES: dict[int, str] = {
    35: "Acoustic Bass Drum",
    38: "Acoustic Snare",
    42: "Closed Hi-Hat",
    47: "Mid Tom",
    49: "Crash Cymbal 1",
}

# --- 7-class expansion reference (ECLASS-01, v2) ---
# AdtofDrumClass would add: OPEN_HI_HAT = 5, RIDE = 6
# GM notes: open hi-hat = 46, ride cymbal = 51
# Crash/ride split requires retraining or post-hoc amplitude heuristics.


def gm_note(drum_class: AdtofDrumClass) -> int:
    """Return the General MIDI note number for *drum_class*.

    Parameters
    ----------
    drum_class:
        An :class:`AdtofDrumClass` member.

    Returns
    -------
    int
        GM percussion note number (channel 10).

    Raises
    ------
    KeyError
        If *drum_class* is not a valid ADTOF class.
    """
    return _ADTOF_GM[drum_class]
