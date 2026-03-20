"""music21 bridge — MIDI cleanup, analysis, and notation export.

Round-trip: PrettyMIDI -> temp .mid -> music21 Score -> manipulate -> output.

All public functions accept PrettyMIDI objects directly (the type already
stored in SessionStore.stem_midi_data) and return either a new PrettyMIDI,
a string (MusicXML), or a file path (PDF).
"""

from __future__ import annotations

import logging
import pathlib
import subprocess
import tempfile
from typing import Any

import music21

log = logging.getLogger("stemforge.utils.music21_bridge")

MidiData = Any  # pretty_midi.PrettyMIDI


# ──────────────────────────────────────────────────────────────────────
# Internal: PrettyMIDI <-> music21 Score conversion
# ──────────────────────────────────────────────────────────────────────

def _to_score(
    midi_data: MidiData,
    *,
    quantize: bool = True,
    quarter_length_divisors: tuple[int, ...] = (4, 3),
) -> music21.stream.Score:
    """Write PrettyMIDI to temp file, parse into music21 Score."""
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
        midi_data.write(tmp.name)
        tmp_path = tmp.name

    try:
        score = music21.converter.parse(
            tmp_path,
            format="midi",
            quantizePost=quantize,
            quarterLengthDivisors=quarter_length_divisors,
        )
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)

    if not isinstance(score, music21.stream.Score):
        # converter.parse sometimes returns a Part — wrap it
        wrapped = music21.stream.Score()
        if isinstance(score, music21.stream.Part):
            wrapped.insert(0, score)
        else:
            part = music21.stream.Part()
            part.insert(0, score)
            wrapped.insert(0, part)
        score = wrapped

    return score


def _to_pretty_midi(score: music21.stream.Score) -> MidiData:
    """Convert music21 Score back to PrettyMIDI via temp MIDI file."""
    import pretty_midi

    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        mf = music21.midi.translate.streamToMidiFile(score)
        mf.open(tmp_path, "wb")
        mf.write()
        mf.close()
        return pretty_midi.PrettyMIDI(tmp_path)
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────────────
# Tier 1: Clean Up
# ──────────────────────────────────────────────────────────────────────

def clean_midi(
    midi_data: MidiData,
    *,
    quarter_length_divisors: tuple[int, ...] = (4, 3),
    min_note_quarterLength: float = 0.125,
    key: str | None = None,
    time_signature: str | None = None,
) -> MidiData:
    """Quantize, filter, and clean a PrettyMIDI object -> new PrettyMIDI.

    Steps:
    1. Parse with quantization (snaps onsets/durations to grid)
    2. Remove micro-notes shorter than min_note_quarterLength
    3. Apply key signature if provided
    4. Apply time signature if provided
    5. makeNotation() for proper beaming/rest placement
    6. Round-trip back to PrettyMIDI
    """
    score = _to_score(
        midi_data,
        quantize=True,
        quarter_length_divisors=quarter_length_divisors,
    )

    # Filter micro-notes
    for part in score.parts:
        for note_obj in part.recurse().notes:
            if note_obj.quarterLength < min_note_quarterLength:
                part.remove(note_obj, recurse=True)

    # Apply key signature
    if key:
        try:
            ks = music21.key.Key(key)
            for part in score.parts:
                # Remove existing key signatures
                for existing_ks in part.recurse().getElementsByClass("KeySignature"):
                    part.remove(existing_ks, recurse=True)
                part.insert(0, ks)
        except Exception:
            log.warning("Could not parse key '%s', skipping", key)

    # Apply time signature
    if time_signature:
        try:
            ts = music21.meter.TimeSignature(time_signature)
            for part in score.parts:
                for existing_ts in part.recurse().getElementsByClass("TimeSignature"):
                    part.remove(existing_ts, recurse=True)
                part.insert(0, ts)
        except Exception:
            log.warning("Could not parse time signature '%s', skipping", time_signature)

    # Fix beaming, stem direction, rest placement
    score.makeNotation(inPlace=True)

    return _to_pretty_midi(score)


# ──────────────────────────────────────────────────────────────────────
# Tier 2: Analysis & Transformation
# ──────────────────────────────────────────────────────────────────────

def detect_key(midi_data: MidiData) -> dict:
    """Run Krumhansl-Schmuckler key detection on MIDI data.

    Returns dict with key, confidence, and top alternates.
    """
    score = _to_score(midi_data, quantize=False)
    analysis = score.analyze("key")

    # Get alternates from the analysis
    alternates = []
    try:
        alt_keys = analysis.alternateInterpretations[:3]
        for alt in alt_keys:
            alternates.append({
                "key": f"{alt.tonic.name} {alt.mode}",
                "confidence": round(alt.correlationCoefficient, 3),
            })
    except (AttributeError, TypeError):
        pass

    return {
        "key": f"{analysis.tonic.name} {analysis.mode}",
        "confidence": round(analysis.correlationCoefficient, 3),
        "alternates": alternates,
    }


def transpose_midi(
    midi_data: MidiData,
    *,
    semitones: int = 0,
    interval: str | None = None,
    key: str | None = None,
) -> MidiData:
    """Transpose all notes by the given interval -> new PrettyMIDI.

    Either semitones or interval must be provided.
    If key is given, enharmonic spelling respects the target key.
    """
    score = _to_score(midi_data, quantize=False)

    if interval:
        intv = music21.interval.Interval(interval)
    elif semitones != 0:
        intv = music21.interval.Interval(semitones)
    else:
        return midi_data  # no-op

    score = score.transpose(intv)

    # Re-spell for target key if provided
    if key:
        try:
            target_key = music21.key.Key(key)
            for note_obj in score.recurse().notes:
                if hasattr(note_obj, "pitch"):
                    note_obj.pitch = note_obj.pitch.getEnharmonic()
                    # Prefer spelling in the target key
                    note_obj.pitch.spelledOut = True
        except Exception:
            log.warning("Could not apply key spelling for '%s'", key)

    return _to_pretty_midi(score)


def detect_tempo(midi_data: MidiData) -> dict:
    """Estimate tempo from note onset distribution.

    Returns dict with bpm and confidence level.
    """
    score = _to_score(midi_data, quantize=False)

    try:
        tempos = score.metronomeMarkBoundaries()
        if tempos:
            # Use the first tempo marking
            mm = tempos[0][2]
            return {"bpm": round(mm.number, 1), "confidence": "high"}
    except Exception:
        pass

    # Fallback: analyze onset intervals
    try:
        onsets = []
        for note_obj in score.recurse().notes:
            onsets.append(note_obj.offset)

        if len(onsets) < 4:
            return {"bpm": 120.0, "confidence": "low"}

        onsets.sort()
        intervals = [onsets[i + 1] - onsets[i] for i in range(len(onsets) - 1)]
        intervals = [i for i in intervals if i > 0]

        if not intervals:
            return {"bpm": 120.0, "confidence": "low"}

        # Median inter-onset interval in quarter notes
        intervals.sort()
        median_ioi = intervals[len(intervals) // 2]

        if median_ioi > 0:
            bpm = 60.0 / (median_ioi * 0.5)  # Rough: assume each IOI is ~half a beat
            # Normalize to reasonable range
            while bpm < 60:
                bpm *= 2
            while bpm > 200:
                bpm /= 2
            return {"bpm": round(bpm, 1), "confidence": "medium"}
    except Exception:
        pass

    return {"bpm": 120.0, "confidence": "low"}


# ──────────────────────────────────────────────────────────────────────
# Tier 3: Sheet Music / Notation Export
# ──────────────────────────────────────────────────────────────────────

def _prepare_notation_score(
    midi_data: MidiData,
    *,
    quarter_length_divisors: tuple[int, ...] = (4, 3),
    key: str | None = None,
    time_signature: str | None = None,
    title: str | None = None,
) -> music21.stream.Score:
    """Prepare a clean, notation-ready score (shared by all Tier 3 functions)."""
    score = _to_score(
        midi_data,
        quantize=True,
        quarter_length_divisors=quarter_length_divisors,
    )

    if key:
        try:
            ks = music21.key.Key(key)
            for part in score.parts:
                for existing_ks in part.recurse().getElementsByClass("KeySignature"):
                    part.remove(existing_ks, recurse=True)
                part.insert(0, ks)
        except Exception:
            log.warning("Could not parse key '%s' for notation", key)

    if time_signature:
        try:
            ts = music21.meter.TimeSignature(time_signature)
            for part in score.parts:
                for existing_ts in part.recurse().getElementsByClass("TimeSignature"):
                    part.remove(existing_ts, recurse=True)
                part.insert(0, ts)
        except Exception:
            log.warning("Could not parse time signature '%s' for notation", time_signature)

    if title:
        score.metadata = music21.metadata.Metadata()
        score.metadata.title = title

    score.makeNotation(inPlace=True)
    return score


def to_musicxml(
    midi_data: MidiData,
    *,
    quarter_length_divisors: tuple[int, ...] = (4, 3),
    key: str | None = None,
    time_signature: str | None = None,
    title: str | None = None,
) -> str:
    """Convert PrettyMIDI -> MusicXML string for in-browser rendering."""
    score = _prepare_notation_score(
        midi_data,
        quarter_length_divisors=quarter_length_divisors,
        key=key,
        time_signature=time_signature,
        title=title,
    )

    # Write to temp file and read back as string
    with tempfile.NamedTemporaryFile(suffix=".musicxml", delete=False, mode="w") as tmp:
        tmp_path = tmp.name

    try:
        score.write("musicxml", fp=tmp_path)
        return pathlib.Path(tmp_path).read_text(encoding="utf-8")
    finally:
        pathlib.Path(tmp_path).unlink(missing_ok=True)


def to_pdf(
    midi_data: MidiData,
    output_path: pathlib.Path,
    *,
    quarter_length_divisors: tuple[int, ...] = (4, 3),
    key: str | None = None,
    time_signature: str | None = None,
    title: str | None = None,
) -> pathlib.Path:
    """Convert PrettyMIDI -> PDF via music21 -> LilyPond.

    Raises FileNotFoundError if LilyPond is not installed.
    """
    lp = check_lilypond()
    if not lp["available"]:
        raise FileNotFoundError(
            "LilyPond is not installed. Install it with: sudo dnf install lilypond"
        )

    score = _prepare_notation_score(
        midi_data,
        quarter_length_divisors=quarter_length_divisors,
        key=key,
        time_signature=time_signature,
        title=title,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # music21 can write directly to PDF via LilyPond if configured
    try:
        # Write LilyPond file, then compile to PDF
        with tempfile.TemporaryDirectory() as tmpdir:
            ly_path = pathlib.Path(tmpdir) / "score.ly"
            score.write("lily", fp=str(ly_path))

            result = subprocess.run(
                ["lilypond", "--pdf", "-o", str(pathlib.Path(tmpdir) / "score"), str(ly_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                log.error("LilyPond failed: %s", result.stderr)
                raise RuntimeError(f"LilyPond rendering failed: {result.stderr[:200]}")

            pdf_result = pathlib.Path(tmpdir) / "score.pdf"
            if not pdf_result.is_file():
                raise RuntimeError("LilyPond did not produce a PDF file")

            import shutil
            shutil.move(str(pdf_result), str(output_path))

    except subprocess.TimeoutExpired:
        raise RuntimeError("LilyPond rendering timed out (60s limit)")

    return output_path


def to_musicxml_file(
    midi_data: MidiData,
    output_path: pathlib.Path,
    **kwargs: Any,
) -> pathlib.Path:
    """Write MusicXML to disk (for import into Finale/Sibelius/MuseScore)."""
    xml_str = to_musicxml(midi_data, **kwargs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(xml_str, encoding="utf-8")
    return output_path


# ──────────────────────────────────────────────────────────────────────
# System
# ──────────────────────────────────────────────────────────────────────

def check_lilypond() -> dict:
    """Check LilyPond availability.

    Returns {"available": bool, "version": str | None}
    """
    try:
        result = subprocess.run(
            ["lilypond", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version = result.stdout.split("\n")[0] if result.returncode == 0 else None
        return {"available": result.returncode == 0, "version": version}
    except FileNotFoundError:
        return {"available": False, "version": None}
    except subprocess.TimeoutExpired:
        return {"available": False, "version": None}
