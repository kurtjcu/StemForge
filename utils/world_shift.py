"""Pitch shifting via WORLD vocoder — analyze, modify F0, resynthesize.

WORLD decomposes audio into F0 (pitch), spectral envelope, and aperiodicity.
Modifying F0 alone and resynthesizing naturally preserves formants without
any pitch-mark placement — the main source of distortion in TD-PSOLA.

License: pyworld is MIT, the underlying WORLD C++ library is Modified-BSD.
"""

from __future__ import annotations

import numpy as np


def world_pitch_shift(
    audio: np.ndarray,
    sr: int,
    source_f0: np.ndarray,
    target_f0: np.ndarray,
    hop_size: int = 128,
) -> np.ndarray:
    """Pitch-shift *audio* from *source_f0* to *target_f0* using WORLD vocoder.

    Uses CREPE-detected pitch (source_f0/target_f0) for the correction curve,
    but WORLD's own DIO+StoneMask for analysis pitch marks and
    CheapTrick+D4C for spectral envelope and aperiodicity.  Only the F0
    parameter is replaced before resynthesis.

    Parameters
    ----------
    audio : ndarray, shape (n_samples,)
        Mono float audio.
    sr : int
        Sample rate (must be >= 16000).
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

    # --- WORLD analysis ---
    # Use DIO for F0 (fast), refine with StoneMask
    world_f0, world_t = pw.dio(audio64, sr)
    world_f0 = pw.stonemask(audio64, world_f0, world_t, sr)

    # Spectral envelope and aperiodicity
    sp = pw.cheaptrick(audio64, world_f0, world_t, sr)
    ap = pw.d4c(audio64, world_f0, world_t, sr)

    # --- Map CREPE correction ratio onto WORLD's F0 grid ---
    n_world = len(world_f0)
    world_hop_ms = (world_t[1] - world_t[0]) * 1000.0 if n_world > 1 else 5.0

    # Build per-CREPE-frame pitch ratio (target / source)
    ratio_crepe = np.ones(len(source_f0), dtype=np.float64)
    voiced_mask = source_f0 > 0
    ratio_crepe[voiced_mask] = target_f0[voiced_mask] / source_f0[voiced_mask]

    # CREPE frame times in seconds
    crepe_times = (np.arange(len(source_f0)) + 0.5) * hop_size / sr

    # Interpolate ratio onto WORLD's time grid
    ratio_world = np.interp(world_t, crepe_times, ratio_crepe)

    # Apply ratio to WORLD's own F0 (preserving WORLD's voicing decisions)
    new_f0 = world_f0.copy()
    for i in range(n_world):
        if world_f0[i] > 0:
            new_f0[i] = world_f0[i] * ratio_world[i]
            # Clamp to reasonable vocal range
            new_f0[i] = np.clip(new_f0[i], 50.0, 1100.0)

    # --- WORLD synthesis ---
    result = pw.synthesize(new_f0, sp, ap, sr)

    # Match input length
    if len(result) > n:
        result = result[:n]
    elif len(result) < n:
        result = np.pad(result, (0, n - len(result)))

    return result.astype(audio.dtype)
