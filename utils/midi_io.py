"""
MIDI I/O utilities for StemForge.

Provides lightweight functions for reading and writing Standard MIDI Files
(SMF) using only the Python standard library.  Supports single-track and
multi-track MIDI files (format 0 and format 1).
"""

import os
import pathlib
import logging
import struct


def read_midi(path: pathlib.Path) -> object:
    """Parse a Standard MIDI File and return a structured representation.

    Parameters
    ----------
    path:
        Path to the ``.mid`` or ``.midi`` file to read.

    Returns
    -------
    object
        An object with ``tracks``, ``ticks_per_beat``, and ``format``
        attributes (concrete type determined at runtime).
    """
    pass


def write_midi(midi_data: object, path: pathlib.Path) -> pathlib.Path:
    """Serialise *midi_data* to a Standard MIDI File at *path*.

    Parameters
    ----------
    midi_data:
        Structured MIDI data as returned by :func:`read_midi` or constructed
        from note events via :func:`notes_to_midi`.
    path:
        Destination file path (will be created or overwritten).

    Returns
    -------
    pathlib.Path
        Resolved path of the written file.
    """
    pass


def notes_to_midi(
    note_events: list[tuple[float, float, int, int]],
    ticks_per_beat: int = 480,
    tempo_bpm: float = 120.0,
) -> object:
    """Convert a list of note events to a MIDI data object.

    Parameters
    ----------
    note_events:
        List of ``(start_sec, end_sec, pitch, velocity)`` tuples.
    ticks_per_beat:
        MIDI ticks per quarter-note (resolution).
    tempo_bpm:
        Tempo in beats per minute for the generated MIDI file.

    Returns
    -------
    object
        MIDI data object suitable for passing to :func:`write_midi`.
    """
    pass


def midi_to_notes(midi_data: object) -> list[tuple[float, float, int, int]]:
    """Extract note events from *midi_data* as ``(start, end, pitch, velocity)`` tuples.

    Parameters
    ----------
    midi_data:
        MIDI data object as returned by :func:`read_midi`.
    """
    pass


def get_tempo(midi_data: object) -> float:
    """Return the first tempo marking in *midi_data* as beats per minute."""
    pass


def quantise_notes(
    note_events: list[tuple[float, float, int, int]],
    grid_seconds: float,
) -> list[tuple[float, float, int, int]]:
    """Snap note start and end times in *note_events* to the nearest *grid_seconds* boundary."""
    pass
