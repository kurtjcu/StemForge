"""Tests for OnsetBackend — energy-based onset detection on isolated drum sub-stems."""
from __future__ import annotations

import pathlib
import sys
import time

import numpy as np
import pytest
import soundfile as sf

from pipelines.onset_backend import OnsetBackend
from utils.errors import InvalidInputError


# ---------------------------------------------------------------------------
# Synthetic WAV helpers
# ---------------------------------------------------------------------------


def make_kick_wav(
    path: pathlib.Path,
    bpm: float = 120.0,
    duration: float = 4.0,
    sr: int = 44100,
) -> list[float]:
    """Write a synthetic kick WAV and return ground-truth onset times.

    Each kick is a 150ms sine sweep decaying from 100 Hz to ~20 Hz with an
    exponential amplitude envelope — mimicking the low-frequency body of an
    acoustic bass drum.
    """
    beat_interval = 60.0 / bpm
    onset_times = [i * beat_interval for i in range(int(duration / beat_interval))]
    y = np.zeros(int(sr * duration), dtype=np.float32)
    for t in onset_times:
        s = int(t * sr)
        body_len = int(0.15 * sr)
        if s + body_len >= len(y):
            continue
        t_body = np.linspace(0, 0.15, body_len)
        freq = 100 * np.exp(-5 * t_body)
        phase = np.cumsum(2 * np.pi * freq / sr)
        body = np.sin(phase) * np.exp(-20 * t_body)
        y[s : s + body_len] += body
    y /= np.max(np.abs(y)) + 1e-8
    sf.write(str(path), y, sr)
    return onset_times


def make_hihat_wav_with_bleed(
    path: pathlib.Path,
    bpm: float = 120.0,
    duration: float = 4.0,
    sr: int = 44100,
) -> int:
    """Write a synthetic hi-hat WAV with kick bleed and return ground-truth hi-hat count.

    Hi-hat events are white noise bursts at 8th-note intervals with exponential
    decay.  Quarter-note slots also receive low-level kick bleed (0.15x amplitude)
    to simulate room coupling from the kick drum.
    """
    beat_interval = 60.0 / bpm
    eighth_interval = beat_interval / 2

    hihat_onset_times = [
        i * eighth_interval for i in range(int(duration / eighth_interval))
    ]

    y = np.zeros(int(sr * duration), dtype=np.float32)
    rng = np.random.default_rng(seed=42)

    # Hi-hat bursts (8th notes)
    for t in hihat_onset_times:
        s = int(t * sr)
        burst_len = int(0.03 * sr)  # 30 ms
        if s + burst_len >= len(y):
            continue
        t_burst = np.linspace(0, 0.03, burst_len)
        noise = rng.standard_normal(burst_len).astype(np.float32)
        envelope = np.exp(-60 * t_burst).astype(np.float32)
        y[s : s + burst_len] += 0.8 * noise * envelope

    # Kick bleed (quarter notes = every 2nd 8th note)
    quarter_times = [i * beat_interval for i in range(int(duration / beat_interval))]
    for t in quarter_times:
        s = int(t * sr)
        body_len = int(0.15 * sr)
        if s + body_len >= len(y):
            continue
        t_body = np.linspace(0, 0.15, body_len)
        freq = 100 * np.exp(-5 * t_body)
        phase = np.cumsum(2 * np.pi * freq / sr)
        body = np.sin(phase) * np.exp(-20 * t_body)
        y[s : s + body_len] += 0.15 * body.astype(np.float32)

    y /= np.max(np.abs(y)) + 1e-8
    sf.write(str(path), y, sr)
    return len(hihat_onset_times)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_kick_timing_within_5ms(tmp_path: pathlib.Path) -> None:
    """Onset times are within ±5 ms for all beats in a synthetic 4/4 kick WAV."""
    kick_path = tmp_path / "kick.wav"
    gt_times = make_kick_wav(kick_path, bpm=120.0, duration=4.0)

    backend = OnsetBackend()
    events = backend.detect(kick_path, gm_note=35)
    detected_times = [e[0] for e in events]

    assert len(events) == len(gt_times), (
        f"Expected {len(gt_times)} onsets, detected {len(events)}"
    )
    for gt_t in gt_times:
        nearest = min(detected_times, key=lambda x: abs(x - gt_t))
        assert abs(nearest - gt_t) <= 0.005, (
            f"Ground-truth onset at {gt_t:.4f}s: nearest detected = {nearest:.4f}s "
            f"(error {abs(nearest - gt_t) * 1000:.1f} ms > 5 ms)"
        )


def test_hihat_bleed_suppressed(tmp_path: pathlib.Path) -> None:
    """Hi-hat onset count on a bleed-contaminated stem is within ±2 of ground truth."""
    hihat_path = tmp_path / "hihat.wav"
    gt_count = make_hihat_wav_with_bleed(hihat_path, bpm=120.0, duration=4.0)

    backend = OnsetBackend()
    events = backend.detect(hihat_path, gm_note=42)

    assert abs(len(events) - gt_count) <= 2, (
        f"Expected {gt_count} hi-hat onsets (±2), detected {len(events)} "
        f"(error = {abs(len(events) - gt_count)})"
    )


def test_detect_under_2_seconds(tmp_path: pathlib.Path) -> None:
    """detect() on a 60-second sub-stem completes in under 2 seconds."""
    kick_path = tmp_path / "kick60.wav"
    make_kick_wav(kick_path, bpm=120.0, duration=60.0)

    backend = OnsetBackend()
    start = time.perf_counter()
    backend.detect(kick_path, gm_note=35)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, (
        f"detect() took {elapsed:.2f}s on a 60-second stem (limit: 2.0s)"
    )


def test_no_model_weights_imported() -> None:
    """OnsetBackend imports no torch, tensorflow, or adtof_pytorch at any point."""
    before = set(sys.modules.keys())
    # Import and instantiate — neither must trigger model weight loading
    from pipelines.onset_backend import OnsetBackend as _OB  # noqa: F401

    _OB()

    new_modules = set(sys.modules.keys()) - before
    for forbidden in ("torch", "tensorflow", "adtof_pytorch"):
        assert forbidden not in new_modules, (
            f"OnsetBackend caused {forbidden!r} to be imported"
        )


def test_invalid_gm_note_raises(tmp_path: pathlib.Path) -> None:
    """detect() raises InvalidInputError for an unsupported GM note number."""
    # Write a minimal 1-second silence WAV so file loading succeeds
    silence = np.zeros(44100, dtype=np.float32)
    audio_path = tmp_path / "silence.wav"
    sf.write(str(audio_path), silence, 44100)

    backend = OnsetBackend()
    with pytest.raises(InvalidInputError):
        backend.detect(audio_path, gm_note=99)
