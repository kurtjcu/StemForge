"""TD-PSOLA pitch shifting — numpy/scipy implementation.

Time-Domain Pitch-Synchronous Overlap-Add: extract Hanning-windowed grains
at pitch-synchronous marks, reposition them at target-pitch spacing, and
overlap-add to produce pitch-shifted audio with preserved formants.

Each contiguous voiced segment is processed independently so that grains
are never pulled across unvoiced gaps.
"""

from __future__ import annotations

import numpy as np


def psola_pitch_shift(
    audio: np.ndarray,
    sr: int,
    source_f0: np.ndarray,
    target_f0: np.ndarray,
    hop_size: int = 128,
) -> np.ndarray:
    """Pitch-shift *audio* from *source_f0* to *target_f0* using TD-PSOLA.

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
    n = len(audio)
    audio64 = audio.astype(np.float64)

    src_f0 = _f0_to_sample_level(source_f0, hop_size, n)
    tgt_f0 = _f0_to_sample_level(target_f0, hop_size, n)

    # Start from original audio — only modify voiced regions
    result = audio64.copy()
    output = np.zeros(n, dtype=np.float64)
    win_sum = np.zeros(n, dtype=np.float64)

    segments = _find_voiced_segments(src_f0)
    if not segments:
        return audio.copy()

    for seg_start, seg_end in segments:
        ana_marks = _place_segment_marks(audio64, src_f0, sr, seg_start, seg_end)
        if len(ana_marks) < 1:
            continue

        syn_marks = _synthesis_marks_for_segment(
            ana_marks, tgt_f0, sr, seg_start, seg_end,
        )

        for syn_pos in syn_marks:
            # Find nearest analysis mark *within this segment only*
            idx = np.argmin(np.abs(ana_marks - syn_pos))
            ana_pos = int(ana_marks[idx])

            # Grain width = 2 × source period at the analysis mark
            f0_at_mark = src_f0[min(ana_pos, n - 1)]
            if f0_at_mark > 0:
                period = int(round(sr / f0_at_mark))
            else:
                period = int(round(sr / 200.0))
            period = max(period, 4)
            half = period

            # Extract grain centered on analysis mark (zero-pad at edges)
            raw = _extract_padded(audio64, ana_pos - half, ana_pos + half, n)
            grain_len = len(raw)

            # Hanning window
            win = np.hanning(grain_len).astype(np.float64)
            grain = raw * win

            # Accumulate at synthesis position
            out_start = syn_pos - half
            out_end = syn_pos + half

            src_lo = max(0, -out_start)
            dst_lo = max(0, out_start)
            dst_hi = min(n, out_end)
            src_hi = src_lo + (dst_hi - dst_lo)

            if dst_hi <= dst_lo or src_hi <= src_lo:
                continue

            output[dst_lo:dst_hi] += grain[src_lo:src_hi]
            win_sum[dst_lo:dst_hi] += win[src_lo:src_hi]

    # Normalize PSOLA output where window sum is sufficient
    for seg_start, seg_end in segments:
        seg_slice = slice(seg_start, min(seg_end, n))
        ws = win_sum[seg_slice]
        mask = ws > 1e-8
        if np.any(mask):
            result[seg_start:min(seg_end, n)][mask] = (
                output[seg_start:min(seg_end, n)][mask] / ws[mask]
            )

    # Crossfade at voiced/unvoiced boundaries
    result = _crossfade_boundaries(audio64, result, src_f0, sr)

    # Match original dtype
    if len(result) > n:
        result = result[:n]
    elif len(result) < n:
        result = np.pad(result, (0, n - len(result)))

    return result.astype(audio.dtype)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _f0_to_sample_level(
    f0_frames: np.ndarray, hop_size: int, n_samples: int,
) -> np.ndarray:
    """Interpolate frame-level F0 to sample-level within voiced runs."""
    out = np.zeros(n_samples, dtype=np.float64)
    n_frames = len(f0_frames)
    if n_frames == 0:
        return out

    centres = (np.arange(n_frames) + 0.5) * hop_size
    voiced = f0_frames > 0

    i = 0
    while i < n_frames:
        if voiced[i]:
            j = i
            while j < n_frames and voiced[j]:
                j += 1
            s_start = max(0, int(centres[i] - hop_size / 2))
            s_end = min(n_samples, int(centres[j - 1] + hop_size / 2))
            if j - i == 1:
                out[s_start:s_end] = f0_frames[i]
            else:
                xp = centres[i:j]
                fp = f0_frames[i:j].astype(np.float64)
                xs = np.arange(s_start, s_end)
                if len(xs) > 0:
                    out[s_start:s_end] = np.interp(xs, xp, fp)
            i = j
        else:
            i += 1

    return out


def _find_voiced_segments(f0_samples: np.ndarray) -> list[tuple[int, int]]:
    """Return (start, end) pairs for contiguous voiced (F0 > 0) regions."""
    segments: list[tuple[int, int]] = []
    n = len(f0_samples)
    i = 0
    while i < n:
        if f0_samples[i] > 0:
            j = i
            while j < n and f0_samples[j] > 0:
                j += 1
            segments.append((i, j))
            i = j
        else:
            i += 1
    return segments


def _place_segment_marks(
    audio: np.ndarray,
    f0_samples: np.ndarray,
    sr: int,
    seg_start: int,
    seg_end: int,
) -> np.ndarray:
    """Place pitch marks within a single voiced segment, refined to peaks."""
    marks: list[int] = []
    n = len(audio)
    abs_audio = np.abs(audio)

    pos = float(seg_start)

    while pos < seg_end:
        ipos = int(round(pos))
        if ipos >= seg_end:
            break

        # Refine to nearest amplitude peak within ±T0/4
        f0 = f0_samples[min(ipos, len(f0_samples) - 1)]
        if f0 <= 0:
            break
        period = sr / f0
        radius = max(1, int(period / 4))
        lo = max(seg_start, ipos - radius)
        hi = min(min(seg_end, n), ipos + radius + 1)
        refined = lo + int(np.argmax(abs_audio[lo:hi]))
        marks.append(refined)

        # Advance by one period
        pos = refined + period

    return np.array(marks, dtype=np.int64) if marks else np.array([], dtype=np.int64)


def _synthesis_marks_for_segment(
    ana_marks: np.ndarray,
    tgt_f0: np.ndarray,
    sr: int,
    seg_start: int,
    seg_end: int,
) -> list[int]:
    """Generate synthesis marks at target-pitch spacing within one segment.

    Starts at the first analysis mark and walks forward by target period.
    """
    if len(ana_marks) == 0:
        return []

    n_f0 = len(tgt_f0)
    syn: list[int] = [int(ana_marks[0])]
    pos = float(ana_marks[0])

    while True:
        ipos = int(round(pos))
        if ipos < 0 or ipos >= n_f0:
            break

        f0 = tgt_f0[ipos]
        if f0 <= 0:
            # Target is unvoiced here — use source spacing as fallback
            f0 = 200.0
        period = sr / f0
        pos += period
        ipos = int(round(pos))

        # Stay within the segment boundaries (with a small margin for
        # the last grain to still overlap the segment end)
        if ipos >= seg_end + sr // 200:  # ~5ms margin
            break
        if ipos < seg_start:
            continue

        syn.append(ipos)

    return syn


def _extract_padded(
    audio: np.ndarray, start: int, end: int, n: int,
) -> np.ndarray:
    """Extract audio[start:end], zero-padding where out of bounds."""
    length = end - start
    if length <= 0:
        return np.zeros(1, dtype=np.float64)

    if start >= 0 and end <= n:
        return audio[start:end].copy()

    out = np.zeros(length, dtype=np.float64)
    src_lo = max(0, start)
    src_hi = min(n, end)
    dst_lo = src_lo - start
    dst_hi = dst_lo + (src_hi - src_lo)
    out[dst_lo:dst_hi] = audio[src_lo:src_hi]
    return out


def _crossfade_boundaries(
    original: np.ndarray,
    result: np.ndarray,
    f0_samples: np.ndarray,
    sr: int,
) -> np.ndarray:
    """Smooth transitions at voiced/unvoiced boundaries with 5ms crossfade."""
    n = min(len(original), len(result), len(f0_samples))
    fade_len = max(2, int(0.005 * sr))  # 5 ms

    voiced = (f0_samples[:n] > 0).astype(np.int8)
    diff = np.diff(voiced)
    transitions = np.where(diff != 0)[0]

    for t in transitions:
        lo = max(0, t - fade_len // 2)
        hi = min(n, lo + fade_len)
        length = hi - lo
        if length < 2:
            continue

        fade = np.linspace(0.0, 1.0, length)

        if diff[t] > 0:
            # unvoiced → voiced: blend from original to PSOLA result
            result[lo:hi] = original[lo:hi] * (1 - fade) + result[lo:hi] * fade
        else:
            # voiced → unvoiced: blend from PSOLA result to original
            result[lo:hi] = result[lo:hi] * (1 - fade) + original[lo:hi] * fade

    return result
