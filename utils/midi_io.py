"""
MIDI I/O utilities for StemForge.

Provides lightweight functions for reading and writing Standard MIDI Files
(SMF) using only the Python standard library.  Supports single-track and
multi-track MIDI files (format 0 and format 1).

Backend
-------
All operations delegate to :mod:`pretty_midi`, which is already a
StemForge runtime dependency and is used by :mod:`pipelines.vocal_midi_pipeline`.
The :data:`MidiData` type alias is a :class:`pretty_midi.PrettyMIDI` object.
"""

import os
import pathlib
import logging
import struct
from typing import Any, TypeAlias

import pretty_midi


# A note event is a (start_sec, end_sec, pitch_midi, velocity) tuple.
NoteEvent: TypeAlias = tuple[float, float, int, int]

# A MIDI data object wraps tracks, tempo, and resolution.  The concrete
# type is determined at runtime by whichever MIDI library is available.
MidiData: TypeAlias = Any

log = logging.getLogger("stemforge.utils.midi_io")


def read_midi(path: pathlib.Path) -> MidiData:
    """Parse a Standard MIDI File and return a structured representation.

    Parameters
    ----------
    path:
        Path to the ``.mid`` or ``.midi`` file to read.

    Returns
    -------
    MidiData
        An object with ``tracks``, ``ticks_per_beat``, and ``format``
        attributes (concrete type determined at runtime).
    """
    path = pathlib.Path(path)
    if not path.exists():
        raise FileNotFoundError(f"MIDI file not found: {path}")
    pm = pretty_midi.PrettyMIDI(str(path))
    log.debug("read_midi: %s  instruments=%d", path.name, len(pm.instruments))
    return pm


def write_midi(midi_data: MidiData, path: pathlib.Path) -> pathlib.Path:
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
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    midi_data.write(str(path))
    log.debug("write_midi: %s", path)
    return path.resolve()


def notes_to_midi(
    note_events: list[NoteEvent],
    ticks_per_beat: int = 480,
    tempo_bpm: float = 120.0,
) -> MidiData:
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
    MidiData
        MIDI data object suitable for passing to :func:`write_midi`.
    """
    pm = pretty_midi.PrettyMIDI(
        resolution=int(ticks_per_beat),
        initial_tempo=float(tempo_bpm),
    )
    instrument = pretty_midi.Instrument(
        program=pretty_midi.instrument_name_to_program("Acoustic Grand Piano"),
        name="StemForge",
    )
    for start, end, pitch, velocity in note_events:
        # Guard against degenerate notes (zero or negative duration).
        if end <= start:
            continue
        note = pretty_midi.Note(
            velocity=max(1, min(127, int(velocity))),
            pitch=max(0, min(127, int(pitch))),
            start=float(start),
            end=float(end),
        )
        instrument.notes.append(note)
    instrument.notes.sort(key=lambda n: n.start)
    pm.instruments.append(instrument)
    return pm


def midi_to_notes(midi_data: MidiData) -> list[NoteEvent]:
    """Extract note events from *midi_data* as ``(start, end, pitch, velocity)`` tuples.

    Parameters
    ----------
    midi_data:
        MIDI data object as returned by :func:`read_midi`.
    """
    events: list[NoteEvent] = []
    for instrument in midi_data.instruments:
        for note in instrument.notes:
            events.append((note.start, note.end, note.pitch, note.velocity))
    return sorted(events, key=lambda e: e[0])


def get_tempo(midi_data: MidiData) -> float:
    """Return the first tempo marking in *midi_data* as beats per minute."""
    _, tempos = midi_data.get_tempo_changes()
    if len(tempos) == 0:
        return 120.0
    return float(tempos[0])


def quantise_notes(
    note_events: list[NoteEvent],
    grid_seconds: float,
) -> list[NoteEvent]:
    """Snap note start and end times in *note_events* to the nearest *grid_seconds* boundary."""
    if grid_seconds <= 0:
        return list(note_events)

    def snap(t: float) -> float:
        return round(t / grid_seconds) * grid_seconds

    result: list[NoteEvent] = []
    for start, end, pitch, velocity in note_events:
        q_start = snap(start)
        q_end = snap(end)
        # Guarantee at least one grid cell of duration after snapping.
        if q_end <= q_start:
            q_end = q_start + grid_seconds
        result.append((q_start, q_end, pitch, velocity))
    return result
