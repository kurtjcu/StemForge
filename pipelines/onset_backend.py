"""CPU-only energy-based onset detector for isolated drum sub-stems.

Uses librosa onset_strength + onset_detect with per-class delta and wait
parameters tuned for LarsNet sub-stem bleed characteristics.
"""
from __future__ import annotations

import logging
import pathlib

import numpy as np
import soundfile as sf
import librosa

from utils.midi_io import NoteEvent
from utils.errors import InvalidInputError, PipelineExecutionError

logger = logging.getLogger(__name__)

_HOP_LENGTH: int = 128        # 2.9 ms at 44100 Hz — required for +/-5ms accuracy
_NOTE_DURATION: float = 0.06  # 60ms — matches AdtofBackend convention
_DEFAULT_VELOCITY: int = 100

# Per-class peak-picking parameters: (delta, wait_ms).
#
# delta: minimum height above local mean for a peak to be accepted.
#   - 0.07 for kick/snare/toms: these classes have strong, clean transients;
#     the default delta reliably separates true onsets from envelope ripple.
#   - 0.10 for hi-hat: hi-hat sub-stems frequently contain low-level kick bleed
#     (kick is loud at 50-100 Hz; hi-hat mic bleeds via room coupling). A higher
#     delta forces the onset to be clearly above the bleed-induced envelope baseline.
#   - 0.15 for cymbals: cymbal recordings have the highest bleed from all other
#     drums; the longest sustain also creates envelope shoulders that trigger
#     false double-detections at lower deltas.
#
# wait_ms: minimum gap between consecutive onsets in milliseconds.
#   - 200ms for kick/snare: even at 200 BPM the minimum beat grid is 150ms;
#     200ms is safe and prevents pitch-decay re-trigger on the sustained kick body.
#   - 100ms for toms: NOT 200ms — rapid tom fills can have 100ms inter-onset
#     intervals; 200ms would merge them into one detection.
#   - 80ms for hi-hat: 16th notes at 180 BPM = 83ms; 80ms allows typical dense
#     patterns without merging consecutive hits.
#   - 100ms for cymbals: cymbals rarely play 16th notes; 100ms balances bleed
#     suppression with realistic dense playing patterns.
_PER_CLASS: dict[int, tuple[float, int]] = {
    35: (0.07, 200),   # Acoustic Bass Drum (kick): strong transient, 200ms prevents pitch-decay re-trigger
    38: (0.07, 200),   # Acoustic Snare: strong transient, 200ms wait safe for up to 300 BPM
    47: (0.07, 100),   # Mid Tom: strong transient, 100ms wait (NOT 200ms) to allow rapid tom fills
    42: (0.10, 80),    # Closed Hi-Hat: delta raised to 0.10 to suppress kick bleed via room coupling; 80ms allows 16th notes at 180 BPM
    49: (0.15, 100),   # Crash Cymbal 1: delta raised to 0.15 to suppress bleed from all other drums; longest sustain creates envelope shoulders; 100ms safe since cymbals rarely play 16th notes
}


class OnsetBackend:
    """CPU-only energy-based onset detector for isolated drum sub-stems.

    No model weights are loaded. Call detect() directly without a load/evict
    lifecycle. Thread-safe (no mutable state between calls).
    """

    def detect(
        self,
        audio_path: pathlib.Path,
        gm_note: int,
        *,
        velocity: int = _DEFAULT_VELOCITY,
    ) -> list[NoteEvent]:
        """Detect onsets in a sub-stem file and return a NoteEvent list.

        Parameters
        ----------
        audio_path:
            Path to a mono or stereo WAV file (any sample rate; loaded natively).
        gm_note:
            GM percussion note number (35, 38, 42, 47, or 49).
            Selects per-class delta and wait parameters.
        velocity:
            Fixed MIDI velocity for all returned events (1-127).

        Returns
        -------
        list[NoteEvent]
            Sorted ascending by onset time.

        Raises
        ------
        InvalidInputError
            If gm_note is not one of the supported values.
        PipelineExecutionError
            If audio loading or onset detection fails.
        """
        if gm_note not in _PER_CLASS:
            raise InvalidInputError(
                f"OnsetBackend: unsupported gm_note {gm_note}. "
                f"Expected one of {sorted(_PER_CLASS)}.",
                field="gm_note",
            )

        delta, wait_ms = _PER_CLASS[gm_note]

        try:
            data, sr = sf.read(str(audio_path), dtype="float32", always_2d=False)
            if data.ndim > 1:
                data = data.mean(axis=1)  # stereo -> mono

            onset_env = librosa.onset.onset_strength(
                y=data, sr=sr, hop_length=_HOP_LENGTH
            )
            wait_frames = max(1, int(wait_ms / 1000 * sr / _HOP_LENGTH))

            onsets_sec = librosa.onset.onset_detect(
                onset_envelope=onset_env,
                sr=sr,
                hop_length=_HOP_LENGTH,
                units="time",
                delta=delta,
                wait=wait_frames,
            )
        except InvalidInputError:
            raise
        except Exception as exc:
            raise PipelineExecutionError(
                f"OnsetBackend failed on {audio_path}: {exc}",
                pipeline_name="onset_backend",
            ) from exc

        events: list[NoteEvent] = [
            (float(t), float(t) + _NOTE_DURATION, gm_note, velocity)
            for t in onsets_sec
        ]
        events.sort(key=lambda e: e[0])

        logger.info(
            "OnsetBackend: %d onsets detected (gm_note=%d) in %s",
            len(events),
            gm_note,
            audio_path.name,
        )
        return events
