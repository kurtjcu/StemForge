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
    but WORLD's own Harvest+StoneMask for robust F0 analysis and
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

    # Normalize to ~0.9 peak for consistent WORLD analysis (helps compressed audio)
    peak = np.max(np.abs(audio64))
    if peak > 1e-6:
        gain = 0.9 / peak
        audio64 = audio64 * gain
    else:
        gain = 1.0

    # --- WORLD analysis ---
    # Harvest is slower than DIO but far more robust on compressed/noisy audio
    world_f0, world_t = pw.harvest(
        audio64, sr,
        f0_floor=50.0,
        f0_ceil=1100.0,
        frame_period=5.0,
    )
    world_f0 = pw.stonemask(audio64, world_f0, world_t, sr)

    # Use consistent FFT size for CheapTrick and D4C
    fft_size = pw.get_cheaptrick_fft_size(sr, f0_floor=50.0)
    sp = pw.cheaptrick(audio64, world_f0, world_t, sr, fft_size=fft_size)

    # D4C with threshold=0.0: trust CREPE's voicing decisions, don't let
    # WORLD reclassify voiced frames as unvoiced (fixes spurious dropouts
    # on compressed audio where aperiodicity estimates are noisy)
    ap = pw.d4c(audio64, world_f0, world_t, sr, threshold=0.0, fft_size=fft_size)

    # --- Map CREPE correction ratio onto WORLD's F0 grid ---
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
