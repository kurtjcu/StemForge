"""Pitch shifting via STFT phase vocoder with cepstral formant preservation.

Uses stftpitchshift (MIT) to apply pitch correction in the frequency domain.
Processes each voiced phrase with its median pitch ratio and cepstral formant
preservation for natural-sounding correction.

Works better than WORLD on compressed audio (MP3) because it operates in the
spectral domain and doesn't rely on time-domain periodicity analysis.
"""

from __future__ import annotations

import numpy as np


def stft_pitch_shift(
    audio: np.ndarray,
    sr: int,
    source_f0: np.ndarray,
    target_f0: np.ndarray,
    hop_size: int = 128,
) -> np.ndarray:
    """Pitch-shift *audio* using STFT phase vocoder with formant preservation.

    Identifies voiced phrases from the F0 contour, computes the median pitch
    ratio per phrase, and applies stftpitchshift with cepstral formant
    preservation to each phrase independently.

    Parameters
    ----------
    audio : ndarray, shape (n_samples,)
        Mono float audio.
    sr : int
        Sample rate.
    source_f0 : ndarray, shape (n_frames,)
        Original F0 per CREPE frame (Hz). 0 = unvoiced.
    target_f0 : ndarray, shape (n_frames,)
        Corrected F0 per frame. 0 = unvoiced.
    hop_size : int
        CREPE hop size in samples.

    Returns
    -------
    ndarray, shape (n_samples,)
        Pitch-shifted mono audio, same length as input.
    """
    from stftpitchshift import StftPitchShift

    n = len(audio)
    audio32 = audio.astype(np.float32)

    # Per-frame pitch ratio
    n_frames = len(source_f0)
    ratio = np.ones(n_frames, dtype=np.float64)
    voiced = source_f0 > 0
    ratio[voiced] = target_f0[voiced] / source_f0[voiced]

    if not np.any(voiced) or np.allclose(ratio[voiced], 1.0, atol=1e-4):
        return audio.copy()

    # STFT parameters
    frame_size = 2048
    stft_hop = 256
    quefrency = 1.0 / 80.0  # formant lifter: ~80 Hz floor

    pitchshifter = StftPitchShift(frame_size, stft_hop, sr)

    # Find voiced phrases (contiguous runs, merging small gaps)
    margin_frames = max(1, int(0.05 * sr / hop_size))  # 50ms margin
    phrases = _find_phrases(voiced, n_frames, margin_frames)

    result = audio32.copy()

    for phrase_start, phrase_end in phrases:
        seg_voiced = voiced[phrase_start:phrase_end]
        seg_ratio = ratio[phrase_start:phrase_end]
        if not np.any(seg_voiced):
            continue
        avg_ratio = float(np.median(seg_ratio[seg_voiced]))
        if abs(avg_ratio - 1.0) < 1e-4:
            continue

        # Convert frame range to sample range with margin for STFT context
        samp_start = max(0, phrase_start * hop_size - frame_size)
        samp_end = min(n, phrase_end * hop_size + frame_size)
        seg_audio = audio32[samp_start:samp_end]

        # Ensure minimum length for STFT
        min_len = frame_size * 4
        pad_len = max(0, min_len - len(seg_audio))
        if pad_len > 0:
            seg_audio = np.pad(seg_audio, (0, pad_len))

        shifted = pitchshifter.shiftpitch(
            seg_audio,
            factors=avg_ratio,
            quefrency=quefrency,
            normalization=True,
        )

        # Crossfade into result
        actual_len = samp_end - samp_start
        shifted = shifted[:actual_len]

        fade_len = min(int(0.01 * sr), actual_len // 4)  # 10ms fade
        if fade_len > 1:
            fade_in = np.linspace(0, 1, fade_len, dtype=np.float32)
            fade_out = np.linspace(1, 0, fade_len, dtype=np.float32)

            shifted[:fade_len] = (
                result[samp_start:samp_start + fade_len] * (1 - fade_in)
                + shifted[:fade_len] * fade_in
            )
            shifted[-fade_len:] = (
                shifted[-fade_len:] * fade_out
                + result[samp_end - fade_len:samp_end] * (1 - fade_out)
            )

        result[samp_start:samp_end] = shifted

    if len(result) > n:
        result = result[:n]

    return result


def _find_phrases(
    voiced: np.ndarray, n_frames: int, margin: int,
) -> list[tuple[int, int]]:
    """Find contiguous voiced runs, merging those separated by small gaps."""
    phrases: list[tuple[int, int]] = []
    i = 0
    while i < n_frames:
        if voiced[i]:
            start = i
            while i < n_frames and voiced[i]:
                i += 1
            end = i
            if phrases and (start - phrases[-1][1]) <= margin * 2:
                phrases[-1] = (phrases[-1][0], end)
            else:
                phrases.append((start, end))
        else:
            i += 1
    return phrases
