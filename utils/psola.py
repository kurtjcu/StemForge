"""TD-PSOLA pitch shifting — numpy/scipy implementation.

Time-Domain Pitch-Synchronous Overlap-Add: extract Hanning-windowed grains
at pitch-synchronous marks, reposition them at target-pitch spacing, and
overlap-add to produce pitch-shifted audio with preserved formants.
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
    src_f0 = _f0_to_sample_level(source_f0, hop_size, n)
    tgt_f0 = _f0_to_sample_level(target_f0, hop_size, n)

    voiced = src_f0 > 0
    if not np.any(voiced):
        return audio.copy()

    marks = _place_pitch_marks(src_f0, sr)
    if len(marks) < 2:
        return audio.copy()

    marks = _refine_marks(audio, marks, src_f0, sr)
    psola_out = _psola_resynthesize(audio, marks, src_f0, tgt_f0, sr)
    result = _blend_voiced_unvoiced(audio, psola_out, voiced, sr)

    # Match input length
    if len(result) > n:
        result = result[:n]
    elif len(result) < n:
        result = np.pad(result, (0, n - len(result)))
    return result


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

    # Frame centre positions in samples
    centres = (np.arange(n_frames) + 0.5) * hop_size

    # Find contiguous voiced runs
    voiced = f0_frames > 0
    runs: list[tuple[int, int]] = []
    i = 0
    while i < n_frames:
        if voiced[i]:
            j = i
            while j < n_frames and voiced[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1

    for start, end in runs:
        s_start = max(0, int(centres[start] - hop_size / 2))
        s_end = min(n_samples, int(centres[end - 1] + hop_size / 2))
        if end - start == 1:
            out[s_start:s_end] = f0_frames[start]
        else:
            xp = centres[start:end]
            fp = f0_frames[start:end].astype(np.float64)
            xs = np.arange(s_start, s_end)
            out[s_start:s_end] = np.interp(xs, xp, fp)

    return out


def _place_pitch_marks(f0_samples: np.ndarray, sr: int) -> np.ndarray:
    """Walk forward placing analysis marks at T0 = sr/F0 intervals."""
    n = len(f0_samples)
    marks: list[int] = []

    # Find first voiced sample
    pos = 0
    while pos < n and f0_samples[pos] <= 0:
        pos += 1
    if pos >= n:
        return np.array(marks, dtype=np.int64)

    marks.append(pos)

    while pos < n:
        f0 = f0_samples[pos]
        if f0 <= 0:
            # Skip unvoiced region
            pos += 1
            while pos < n and f0_samples[pos] <= 0:
                pos += 1
            if pos < n:
                marks.append(pos)
            continue
        period = sr / f0
        next_pos = pos + period
        int_next = int(round(next_pos))
        if int_next >= n:
            break
        marks.append(int_next)
        pos = int_next

    return np.array(marks, dtype=np.int64)


def _refine_marks(
    audio: np.ndarray,
    marks: np.ndarray,
    f0_samples: np.ndarray,
    sr: int,
) -> np.ndarray:
    """Snap each mark to the nearest amplitude peak within ±T0/4."""
    refined = marks.copy()
    n = len(audio)
    abs_audio = np.abs(audio)

    for i, m in enumerate(marks):
        if m >= len(f0_samples) or f0_samples[m] <= 0:
            continue
        radius = max(1, int(sr / f0_samples[m] / 4))
        lo = max(0, m - radius)
        hi = min(n, m + radius + 1)
        refined[i] = lo + np.argmax(abs_audio[lo:hi])

    return refined


def _psola_resynthesize(
    audio: np.ndarray,
    marks: np.ndarray,
    src_f0: np.ndarray,
    tgt_f0: np.ndarray,
    sr: int,
) -> np.ndarray:
    """Core overlap-add: reposition grains at target-pitch spacing."""
    n = len(audio)
    output = np.zeros(n, dtype=np.float64)
    win_sum = np.zeros(n, dtype=np.float64)

    # Build synthesis positions from target F0
    syn_marks = _synthesis_marks(marks, tgt_f0, sr)

    for syn_pos in syn_marks:
        # Find nearest analysis mark
        idx = np.argmin(np.abs(marks - syn_pos))
        ana_pos = marks[idx]

        # Source period for grain width
        if ana_pos < len(src_f0) and src_f0[ana_pos] > 0:
            period = int(round(sr / src_f0[ana_pos]))
        else:
            period = int(round(sr / 150.0))  # fallback ~150 Hz
        period = max(period, 4)

        grain_len = 2 * period
        half = period

        # Extract grain with zero-padding at edges
        g_start = ana_pos - half
        g_end = ana_pos + half
        if g_start < 0:
            pad_left = -g_start
            raw = np.concatenate([np.zeros(pad_left), audio[:g_end]])
        elif g_end > n:
            pad_right = g_end - n
            raw = np.concatenate([audio[g_start:], np.zeros(pad_right)])
        else:
            raw = audio[g_start:g_end]

        if len(raw) != grain_len:
            raw = np.resize(raw, grain_len)

        # Hanning window
        win = np.hanning(grain_len)
        grain = raw * win

        # Place grain at synthesis position
        out_start = syn_pos - half
        out_end = syn_pos + half

        # Clip to output bounds
        src_lo = max(0, -out_start)
        dst_lo = max(0, out_start)
        dst_hi = min(n, out_end)
        src_hi = src_lo + (dst_hi - dst_lo)

        if dst_hi <= dst_lo or src_hi <= src_lo:
            continue

        output[dst_lo:dst_hi] += grain[src_lo:src_hi]
        win_sum[dst_lo:dst_hi] += win[src_lo:src_hi]

    # Normalize by window sum
    win_sum = np.maximum(win_sum, 1e-8)
    output /= win_sum

    return output


def _synthesis_marks(
    ana_marks: np.ndarray,
    tgt_f0: np.ndarray,
    sr: int,
) -> list[int]:
    """Generate synthesis mark positions from target F0."""
    if len(ana_marks) == 0:
        return []

    n = len(tgt_f0)
    syn: list[int] = []
    pos = float(ana_marks[0])
    syn.append(int(round(pos)))

    while True:
        ipos = int(round(pos))
        if ipos >= n:
            break
        f0 = tgt_f0[ipos] if ipos < n else 0
        if f0 <= 0:
            # Skip forward through unvoiced
            pos += 1
            while int(round(pos)) < n and tgt_f0[int(round(pos))] <= 0:
                pos += 1
            if int(round(pos)) >= n:
                break
            syn.append(int(round(pos)))
            continue
        period = sr / f0
        pos += period
        ipos = int(round(pos))
        if ipos >= n:
            break
        syn.append(ipos)

    return syn


def _blend_voiced_unvoiced(
    original: np.ndarray,
    psola_out: np.ndarray,
    voiced_mask: np.ndarray,
    sr: int,
) -> np.ndarray:
    """Copy unvoiced regions from original; crossfade 3 ms at boundaries."""
    n = min(len(original), len(psola_out))
    result = np.copy(psola_out[:n])
    fade_len = max(1, int(0.003 * sr))  # 3 ms

    # Find voiced/unvoiced transition points
    v = voiced_mask[:n].astype(np.float64)
    # Copy unvoiced regions directly from original
    unvoiced = v == 0
    result[unvoiced] = original[:n][unvoiced]

    # Crossfade at transitions
    diff = np.diff(v.astype(np.int8))
    transitions = np.where(diff != 0)[0]

    for t in transitions:
        lo = max(0, t - fade_len // 2)
        hi = min(n, t + fade_len // 2 + 1)
        length = hi - lo
        if length < 2:
            continue
        fade = np.linspace(0, 1, length)
        if diff[t] > 0:
            # unvoiced → voiced: fade from original to psola
            result[lo:hi] = original[lo:hi] * (1 - fade) + psola_out[lo:hi] * fade
        else:
            # voiced → unvoiced: fade from psola to original
            result[lo:hi] = psola_out[lo:hi] * (1 - fade) + original[lo:hi] * fade

    return result
