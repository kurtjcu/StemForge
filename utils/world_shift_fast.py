"""Fast WORLD vocoder pitch shifting — skips Harvest, uses CREPE F0 directly.

Same WORLD CheapTrick + D4C + synthesis pipeline as world_shift.py, but instead
of running WORLD's own Harvest F0 estimator (which is the main bottleneck), this
variant constructs WORLD's F0 grid directly from the CREPE-detected F0 contour
that the autotune pipeline has already computed.

~5–10x faster than the full WORLD path at the cost of slightly less robust
spectral envelope estimation on edge cases (Harvest refines voicing boundaries
that CREPE may miss).

License: pyworld is MIT, the underlying WORLD C++ library is Modified-BSD.
"""

from __future__ import annotations

import numpy as np


def world_pitch_shift_fast(
    audio: np.ndarray,
    sr: int,
    source_f0: np.ndarray,
    target_f0: np.ndarray,
    hop_size: int = 128,
) -> np.ndarray:
    """Pitch-shift *audio* using WORLD vocoder, skipping Harvest F0 estimation.

    Resamples CREPE's F0 onto WORLD's 5ms time grid and feeds it directly to
    StoneMask → CheapTrick → D4C → synthesize.  Much faster than the full
    Harvest path because Harvest is O(n²) in audio length.

    Parameters
    ----------
    audio : ndarray, shape (n_samples,)
        Mono float audio.
    sr : int
        Sample rate.
    source_f0 : ndarray, shape (n_frames_crepe,)
        Original F0 per CREPE frame (Hz). 0 = unvoiced.
    target_f0 : ndarray, shape (n_frames_crepe,)
        Corrected F0 per CREPE frame. 0 = unvoiced.
    hop_size : int
        CREPE hop size in samples (default 128).

    Returns
    -------
    ndarray, shape (n_samples,)
        Pitch-shifted mono audio, same length as input.
    """
    import pyworld as pw

    audio64 = audio.astype(np.float64)
    n = len(audio64)

    # Normalize to ~0.9 peak for consistent WORLD analysis
    peak = np.max(np.abs(audio64))
    if peak > 1e-6:
        gain = 0.9 / peak
        audio64 = audio64 * gain
    else:
        gain = 1.0

    # --- Build WORLD time grid (5ms frame period, matching Harvest default) ---
    frame_period = 5.0  # ms
    duration = n / sr
    world_t = np.arange(0, duration, frame_period / 1000.0)

    # Resample CREPE F0 onto WORLD's time grid
    crepe_times = (np.arange(len(source_f0)) + 0.5) * hop_size / sr
    world_f0 = np.interp(world_t, crepe_times, source_f0)

    # Threshold: frames where CREPE said unvoiced → 0
    # Use nearest-neighbor for voicing decisions (interp can smear zeros)
    voiced_crepe = source_f0 > 0
    crepe_voiced_interp = np.interp(world_t, crepe_times, voiced_crepe.astype(float))
    world_f0[crepe_voiced_interp < 0.5] = 0.0

    # StoneMask refines F0 estimates using the actual audio signal
    world_f0 = pw.stonemask(audio64, world_f0, world_t, sr)

    # Re-zero unvoiced frames (StoneMask can introduce small F0 in unvoiced regions)
    world_f0[crepe_voiced_interp < 0.5] = 0.0

    # --- Spectral analysis ---
    fft_size = pw.get_cheaptrick_fft_size(sr, f0_floor=50.0)
    sp = pw.cheaptrick(audio64, world_f0, world_t, sr, fft_size=fft_size)
    ap = pw.d4c(audio64, world_f0, world_t, sr, threshold=0.0, fft_size=fft_size)

    # --- Map CREPE correction ratio onto WORLD's F0 grid ---
    ratio_crepe = np.ones(len(source_f0), dtype=np.float64)
    voiced_mask = source_f0 > 0
    ratio_crepe[voiced_mask] = target_f0[voiced_mask] / source_f0[voiced_mask]

    ratio_world = np.interp(world_t, crepe_times, ratio_crepe)

    # Apply ratio to WORLD F0
    new_f0 = world_f0.copy()
    voiced_world = world_f0 > 0
    new_f0[voiced_world] = np.clip(
        world_f0[voiced_world] * ratio_world[voiced_world],
        50.0, 1100.0,
    )

    # --- WORLD synthesis ---
    result = pw.synthesize(new_f0, sp, ap, sr)

    # Undo normalization gain
    result = result / gain

    # Match input length
    if len(result) > n:
        result = result[:n]
    elif len(result) < n:
        result = np.pad(result, (0, n - len(result)))

    return result.astype(audio.dtype)
