"""Auto-tune pipeline — pitch correction via torchcrepe + parselmouth PSOLA.

Detects pitch with CREPE (neural F0 estimation), snaps to the nearest note
in a user-chosen musical scale, then resynthesises with Praat's PSOLA to
preserve formants and natural vocal quality.
"""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from utils.paths import ENHANCE_DIR

log = logging.getLogger("stemforge.pipelines.autotune")

# ---------------------------------------------------------------------------
# Musical scale definitions
# ---------------------------------------------------------------------------

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Interval patterns as semitone offsets from root (0 = root)
SCALES: dict[str, list[int]] = {
    "chromatic":         list(range(12)),
    "major":             [0, 2, 4, 5, 7, 9, 11],
    "minor":             [0, 2, 3, 5, 7, 8, 10],
    "major_pentatonic":  [0, 2, 4, 7, 9],
    "minor_pentatonic":  [0, 3, 5, 7, 10],
    "blues":             [0, 3, 5, 6, 7, 10],
}

SCALE_LABELS: dict[str, str] = {
    "chromatic":         "Chromatic",
    "major":             "Major",
    "minor":             "Minor",
    "major_pentatonic":  "Major Pentatonic",
    "minor_pentatonic":  "Minor Pentatonic",
    "blues":             "Blues",
}


# Krumhansl-Kessler key profiles — empirical probe-tone ratings for
# how "stable" each pitch class sounds in major and minor keys.
_KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def detect_key_and_scale(f0: np.ndarray) -> tuple[str, str]:
    """Detect the musical key and scale (major/minor) from an F0 contour.

    Uses Krumhansl-Kessler key-profile correlation: builds a chroma
    histogram from voiced frames and correlates against all 24 major/minor
    profiles. Returns (key_name, scale_name) e.g. ("A", "minor").
    """
    # Build chroma histogram from voiced frames
    voiced = f0[f0 > 0]
    if len(voiced) < 10:
        return ("C", "major")  # fallback — not enough data

    midi_notes = 12.0 * np.log2(voiced / 440.0) + 69.0
    pitch_classes = np.round(midi_notes).astype(int) % 12
    chroma = np.bincount(pitch_classes, minlength=12).astype(float)

    # Normalize to zero-mean for Pearson correlation
    chroma -= chroma.mean()
    chroma_norm = np.linalg.norm(chroma)
    if chroma_norm < 1e-9:
        return ("C", "major")

    best_corr = -2.0
    best_root = 0
    best_mode = "major"

    for root in range(12):
        # Rotate profile so index 0 aligns with this root
        for mode, profile in [("major", _KK_MAJOR), ("minor", _KK_MINOR)]:
            rotated = np.array([profile[(i - root) % 12] for i in range(12)], dtype=float)
            rotated -= rotated.mean()
            r_norm = np.linalg.norm(rotated)
            if r_norm < 1e-9:
                continue
            corr = np.dot(chroma, rotated) / (chroma_norm * r_norm)
            if corr > best_corr:
                best_corr = corr
                best_root = root
                best_mode = mode

    return (NOTE_NAMES[best_root], best_mode)


def _build_scale_notes(root: int, scale_key: str) -> set[int]:
    """Return the set of MIDI note classes (0–11) for a root + scale."""
    pattern = SCALES.get(scale_key, SCALES["chromatic"])
    return {(root + s) % 12 for s in pattern}


def _snap_to_scale(midi_note: float, scale_notes: set[int]) -> float:
    """Snap a fractional MIDI note to the nearest note in the scale."""
    note_class = round(midi_note) % 12
    if note_class in scale_notes:
        return round(midi_note)

    # Search outward for the nearest scale note
    for offset in range(1, 7):
        if (note_class + offset) % 12 in scale_notes:
            return round(midi_note) + offset
        if (note_class - offset) % 12 in scale_notes:
            return round(midi_note) - offset

    return round(midi_note)


# ---------------------------------------------------------------------------
# Config / Result
# ---------------------------------------------------------------------------

@dataclass
class AutotuneConfig:
    """Per-run configuration for :class:`AutotunePipeline`."""

    key: str = "Auto"                  # root note name, or "Auto" for detection
    scale: str = "auto"               # key into SCALES, or "auto" for detection
    correction_strength: float = 0.8   # 0.0 = no correction, 1.0 = full snap
    humanize: float = 0.15             # random detuning amount (0.0–1.0)
    output_dir: pathlib.Path = ENHANCE_DIR


@dataclass
class AutotuneResult:
    """Return value from :meth:`AutotunePipeline.run`."""

    output_path: pathlib.Path
    key: str
    scale: str
    correction_strength: float
    detected_key: str | None = None    # populated when key was "Auto"
    detected_scale: str | None = None  # populated when scale was "auto"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class AutotunePipeline:
    """Pitch correction via CREPE detection + Praat PSOLA resynthesis."""

    def __init__(self) -> None:
        self._config: AutotuneConfig | None = None

    def configure(self, config: AutotuneConfig) -> None:
        self._config = config

    def load_model(self) -> None:
        """No-op — torchcrepe loads lazily, parselmouth is header-only."""
        pass

    def run(
        self,
        audio_path: str | pathlib.Path,
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> AutotuneResult:
        """Run pitch correction on an audio file."""
        import soundfile as sf
        import torch
        import torchcrepe
        import parselmouth
        from parselmouth.praat import call as praat_call

        cfg = self._config or AutotuneConfig()
        audio_path = pathlib.Path(audio_path)

        if progress_cb:
            progress_cb(0.05, "Reading audio...")

        # Read audio — convert to mono for pitch detection
        audio, sr = sf.read(str(audio_path), dtype="float32")
        if audio.ndim == 2:
            mono = audio.mean(axis=1)
        else:
            mono = audio

        if progress_cb:
            progress_cb(0.10, "Detecting pitch with CREPE...")

        # --- Pitch detection via torchcrepe ---
        device = "cuda" if torch.cuda.is_available() else "cpu"
        hop_size = 128  # ~2.9 ms at 44100 Hz — fine resolution for pitch correction
        audio_tensor = torch.FloatTensor(mono).unsqueeze(0).to(device)

        f0, periodicity = torchcrepe.predict(
            audio_tensor,
            sr,
            hop_size,
            fmin=50.0,
            fmax=1100.0,
            model="full",
            batch_size=512,
            device=device,
            return_periodicity=True,
        )

        # Median-filter periodicity and mean-filter f0 for stability
        periodicity = torchcrepe.filter.median(periodicity, 3)
        f0 = torchcrepe.filter.mean(f0, 3)

        # Zero out unvoiced frames
        f0[periodicity < 0.1] = 0
        f0 = f0[0].cpu().numpy()

        if progress_cb:
            progress_cb(0.40, "Computing pitch correction...")

        # --- Auto-detect key and/or scale if requested ---
        detected_key = None
        detected_scale = None
        use_key = cfg.key
        use_scale = cfg.scale

        if cfg.key == "Auto" or cfg.scale == "auto":
            det_k, det_s = detect_key_and_scale(f0)
            if cfg.key == "Auto":
                use_key = det_k
                detected_key = det_k
                log.info("Auto-detected key: %s", det_k)
            if cfg.scale == "auto":
                use_scale = det_s
                detected_scale = det_s
                log.info("Auto-detected scale: %s", det_s)
            if progress_cb:
                progress_cb(0.42, f"Detected: {use_key} {SCALE_LABELS.get(use_scale, use_scale)}")

        # --- Scale snapping ---
        root = NOTE_NAMES.index(use_key) if use_key in NOTE_NAMES else 0
        scale_notes = _build_scale_notes(root, use_scale)
        rng = np.random.default_rng()

        corrected_f0 = np.copy(f0)
        for i in range(len(f0)):
            if f0[i] <= 0:
                continue

            # Convert Hz to MIDI note
            midi_note = 12.0 * np.log2(f0[i] / 440.0) + 69.0
            target_midi = _snap_to_scale(midi_note, scale_notes)

            # Humanize: add slight random detuning (in cents, mapped to semitones)
            if cfg.humanize > 0:
                cents = rng.normal(0, cfg.humanize * 25)
                cents = np.clip(cents, -50, 50)
                target_midi += cents / 100.0

            # Blend original and corrected pitch based on strength
            blended_midi = midi_note * (1 - cfg.correction_strength) + target_midi * cfg.correction_strength
            corrected_f0[i] = 440.0 * 2 ** ((blended_midi - 69.0) / 12.0)

        if progress_cb:
            progress_cb(0.55, "Applying PSOLA pitch correction...")

        # --- Resynthesis via parselmouth PSOLA ---
        sound = parselmouth.Sound(mono, sampling_frequency=sr)
        duration = sound.get_total_duration()
        n_frames = len(corrected_f0)
        time_step = duration / n_frames if n_frames > 0 else hop_size / sr

        # Create a PitchTier with corrected pitch values
        pitch_tier = praat_call("Create PitchTier", "corrected", 0.0, duration)

        for i in range(n_frames):
            t = (i + 0.5) * time_step
            if t > duration:
                break
            if corrected_f0[i] > 0:
                praat_call(pitch_tier, "Add point", t, float(corrected_f0[i]))

        if progress_cb:
            progress_cb(0.70, "Resynthesising audio...")

        # Extract the manipulation object for PSOLA
        manipulation = praat_call(sound, "To Manipulation", time_step, 50.0, 1100.0)

        # Replace the pitch tier
        praat_call([manipulation, pitch_tier], "Replace pitch tier")

        # Resynthesize using PSOLA (overlap-add)
        result_sound = praat_call(manipulation, "Get resynthesis (overlap-add)")

        if progress_cb:
            progress_cb(0.90, "Writing output...")

        # Extract result as numpy array
        result_audio = result_sound.values[0]

        # If original was stereo, apply the same pitch shift to both channels
        # by computing the ratio and applying it
        if audio.ndim == 2:
            # Process right channel separately
            sound_r = parselmouth.Sound(audio[:, 1], sampling_frequency=sr)
            manip_r = praat_call(sound_r, "To Manipulation", time_step, 50.0, 1100.0)
            praat_call([manip_r, pitch_tier], "Replace pitch tier")
            result_r = praat_call(manip_r, "Get resynthesis (overlap-add)")
            result_audio_r = result_r.values[0]

            # Combine channels — ensure same length
            min_len = min(len(result_audio), len(result_audio_r))
            result_stereo = np.column_stack([
                result_audio[:min_len],
                result_audio_r[:min_len],
            ])
            output_audio = result_stereo
        else:
            output_audio = result_audio

        # Write output
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        scale_label = use_scale.replace("_", "-")
        out_name = f"{audio_path.stem}_autotune_{use_key}_{scale_label}.wav"
        output_path = cfg.output_dir / out_name
        sf.write(str(output_path), output_audio, sr, subtype="FLOAT")

        log.info("Auto-tune complete: %s → %s (key=%s, scale=%s, strength=%.0f%%)",
                 audio_path.name, output_path.name, use_key, use_scale,
                 cfg.correction_strength * 100)

        if progress_cb:
            progress_cb(1.0, "Done")

        return AutotuneResult(
            output_path=output_path,
            key=use_key,
            scale=use_scale,
            correction_strength=cfg.correction_strength,
            detected_key=detected_key,
            detected_scale=detected_scale,
        )

    def clear(self) -> None:
        """No persistent GPU resources to release."""
        pass
