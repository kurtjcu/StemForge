"""Fast spectral profiling and separator recommendation for StemForge.

Reads at 22050 Hz mono, truncates to 60s, computes one STFT and derives
7 features from it.  Typical runtime: <250ms on a modern CPU.

Public API
----------
profile_audio(path)          -> AudioProfile
recommend_separator(profile) -> Recommendation
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import numpy as np
import scipy.signal
from scipy.ndimage import uniform_filter1d

from utils.audio_io import read_audio, probe
from utils.errors import AudioProcessingError


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ANALYSIS_SR = 22_050           # Hz — fast enough, covers full audio spectrum
_MAX_DURATION = 60.0            # seconds — cap analysis window
_MIN_DURATION = 0.5             # seconds — raise below this
_SHORT_DURATION = 2.0           # seconds — downgrade to "low" confidence

_N_FFT = 2048
_HOP = 512

_VOCAL_LOW_HZ  = 200
_VOCAL_HIGH_HZ = 4_000
_SPLIT_HZ      = 1_000          # harmonic_decay split frequency

_EPS = 1e-9


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AudioProfile:
    """Feature vector extracted from a short spectral analysis window."""
    spectral_flatness:    float   # 0 = tonal, 1 = noise-like  (Wiener entropy)
    transient_sharpness:  float   # 0 = smooth, 1 = sharp attacks
    dynamic_range:        float   # 0 = compressed, 1 = wide
    noise_floor:          float   # 0 = clean, 1 = very noisy
    stereo_correlation:   float   # 0 = wide/decorrelated, 1 = mono-like
    harmonic_decay:       float   # 0 = fast spectral decay, 1 = sustained high-freq
    vocal_naturalness:    float   # 0 = synthetic, 1 = smooth natural formants
    duration_seconds:     float
    is_mono:              bool
    analysis_note:        str     # edge-case notes for display


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Separator recommendation produced by recommend_separator()."""
    engine:     str   # "Demucs" or "BS-Roformer"
    model_id:   str   # e.g. "htdemucs_ft", "roformer-viperx-vocals"
    reason:     str   # human-readable explanation
    confidence: str   # "high", "moderate", or "low"


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def profile_audio(path: pathlib.Path) -> AudioProfile:
    """Analyse *path* and return an AudioProfile.

    Reads at 22050 Hz, truncates to 60 s, computes one STFT, then derives
    all 7 features from the shared magnitude/power arrays.

    Raises
    ------
    AudioProcessingError
        If the file is shorter than 0.5 seconds.
    """
    info = probe(path)
    if info.duration < _MIN_DURATION:
        raise AudioProcessingError(
            f"Audio too short for analysis ({info.duration:.2f}s < {_MIN_DURATION}s).",
            path=str(path),
        )

    is_mono = info.channels == 1
    notes: list[str] = []

    # --- Load mono for spectral features ---
    waveform_mono, _ = read_audio(path, mono=True, target_rate=_ANALYSIS_SR)
    mono = waveform_mono[0]  # shape (samples,)

    # Truncate to analysis window
    max_samples = int(_MAX_DURATION * _ANALYSIS_SR)
    if len(mono) > max_samples:
        mono = mono[:max_samples]

    duration_seconds = len(mono) / _ANALYSIS_SR

    if duration_seconds < _SHORT_DURATION:
        notes.append(f"Short file ({duration_seconds:.1f}s) — confidence limited.")

    # --- Shared STFT (reused by all spectral features) ---
    freqs, _times, stft = scipy.signal.stft(
        mono, fs=_ANALYSIS_SR, nperseg=_N_FFT, noverlap=_N_FFT - _HOP,
    )
    magnitude = np.abs(stft)           # (freq_bins, time_frames)
    power     = magnitude ** 2

    # --- 7 features ---
    sf_val  = _spectral_flatness(power)
    ts_val  = _transient_sharpness(magnitude)
    dr_val  = _dynamic_range(mono)
    nf_val  = _noise_floor(power)
    sc_val  = _stereo_correlation(path, is_mono)
    hd_val  = _harmonic_decay(power, freqs)
    vn_val  = _vocal_naturalness(magnitude, freqs)

    if is_mono:
        notes.append("Mono audio — stereo correlation not applicable.")

    return AudioProfile(
        spectral_flatness   = float(sf_val),
        transient_sharpness = float(ts_val),
        dynamic_range       = float(dr_val),
        noise_floor         = float(nf_val),
        stereo_correlation  = float(sc_val),
        harmonic_decay      = float(hd_val),
        vocal_naturalness   = float(vn_val),
        duration_seconds    = float(duration_seconds),
        is_mono             = is_mono,
        analysis_note       = "  ".join(notes),
    )


def recommend_separator(profile: AudioProfile) -> Recommendation:
    """Return a separator Recommendation based on an AudioProfile.

    Decision tree (see plan for full rationale):

    1. spectral_flatness > 0.45  →  synthetic/processed  →  Demucs
       1a. transient_sharpness > 0.50  →  htdemucs_ft   (pop/rock)
       1b. else                         →  mdx_extra     (electronic/smooth)

    2. spectral_flatness <= 0.45  →  natural/organic  →  BS-Roformer
       2a. harmonic_decay > 0.60         →  6-stem model (guitar/piano content)
       2b. transients > 0.55 AND hd≤0.4  →  4-stem model (drums/bass dominant)
       2c. vocal_naturalness > 0.60      →  vocal model
           2c-i.  flatness < 0.25        →  viperx (smooth/clean)
           2c-ii. else                   →  kj     (sharper isolation)
       2d. fallback: flatness > 0.35     →  htdemucs (borderline)
           else                          →  4-stem roformer (safe)
    """
    sf = profile.spectral_flatness
    ts = profile.transient_sharpness
    hd = profile.harmonic_decay
    vn = profile.vocal_naturalness

    # Determine base confidence from how far we are from each threshold
    def _margin(*distances: float) -> str:
        m = min(distances)
        if m > 0.15:
            return "high"
        if m > 0.05:
            return "moderate"
        return "low"

    if sf > 0.45:
        # Branch 1 — synthetic/processed
        if ts > 0.50:
            engine    = "Demucs"
            model_id  = "htdemucs_ft"
            reason    = (
                "Processed/electronic texture with sharp transients detected. "
                "htdemucs_ft's fine-tuned model handles mixed pop/rock content well."
            )
            conf = _margin(sf - 0.45, ts - 0.50)
        else:
            engine    = "Demucs"
            model_id  = "mdx_extra"
            reason    = (
                "Synthetic or heavily processed audio with smooth dynamics. "
                "mdx_extra's MDX architecture excels at clean electronic separation."
            )
            conf = _margin(sf - 0.45, 0.50 - ts)
    else:
        # Branch 2 — natural/organic
        if hd > 0.60:
            engine    = "BS-Roformer"
            model_id  = "roformer-jarredou-6stem"
            reason    = (
                "Rich harmonic content (guitar/piano/strings) detected. "
                "The 6-stem model isolates guitar and piano in addition to the core 4 stems."
            )
            conf = _margin(0.45 - sf, hd - 0.60)

        elif ts > 0.55 and hd <= 0.40:
            engine    = "BS-Roformer"
            model_id  = "roformer-zfturbo-4stem"
            reason    = (
                "Strong percussive transients with limited harmonic sustain detected. "
                "Drums/bass-dominant mix — 4-stem Roformer (SDR 9.66) is the best fit."
            )
            conf = _margin(0.45 - sf, ts - 0.55, 0.40 - hd)

        elif vn > 0.60:
            if sf < 0.25:
                engine    = "BS-Roformer"
                model_id  = "roformer-viperx-vocals"
                reason    = (
                    "Natural, smooth vocals with clean spectral envelope. "
                    "ViperX BS-Roformer (SDR 12.97) delivers the cleanest vocal isolation."
                )
                conf = _margin(0.45 - sf, vn - 0.60, 0.25 - sf)
            else:
                engine    = "BS-Roformer"
                model_id  = "roformer-kj-vocals"
                reason    = (
                    "Natural vocals with moderate spectral complexity. "
                    "MelBand-Roformer (KimberleyJensen) handles busier vocal textures well."
                )
                conf = _margin(0.45 - sf, vn - 0.60)

        elif sf > 0.35:
            # Borderline — safe Demucs fallback
            engine    = "Demucs"
            model_id  = "htdemucs"
            reason    = (
                "Borderline spectral characteristics — neither clearly synthetic nor fully organic. "
                "htdemucs is the most robust all-rounder for ambiguous material."
            )
            conf = "low"

        else:
            # Natural, no strong sub-feature signal
            engine    = "BS-Roformer"
            model_id  = "roformer-zfturbo-4stem"
            reason    = (
                "Organic, tonal audio without a dominant vocal or percussion signature. "
                "4-stem Roformer offers a reliable baseline for natural recordings."
            )
            conf = _margin(0.45 - sf)

    # Cap confidence for edge cases
    if profile.is_mono and conf == "high":
        conf = "moderate"
    if profile.duration_seconds < _SHORT_DURATION:
        conf = "low"

    return Recommendation(engine=engine, model_id=model_id, reason=reason, confidence=conf)


# ---------------------------------------------------------------------------
# Feature extractors (private)
# ---------------------------------------------------------------------------

def _spectral_flatness(power: np.ndarray) -> float:
    """Wiener entropy per frame, averaged.  Range [0, 1].

    power: (freq_bins, time_frames), values ≥ 0.
    """
    p = power + _EPS
    log_mean = np.mean(np.log(p), axis=0)     # shape (time_frames,)
    arith_mean = np.mean(p, axis=0)
    wiener = np.exp(log_mean) / (arith_mean + _EPS)
    return float(np.clip(np.mean(wiener), 0.0, 1.0))


def _transient_sharpness(magnitude: np.ndarray) -> float:
    """Half-wave rectified spectral flux ratio.  Range [0, 1].

    magnitude: (freq_bins, time_frames).
    """
    diff = np.diff(magnitude, axis=1)
    flux = np.sum(np.maximum(diff, 0.0), axis=0)  # shape (time_frames-1,)
    if flux.size == 0:
        return 0.0
    p90 = np.percentile(flux, 90)
    mean_flux = np.mean(flux) + _EPS
    above = flux[flux > p90]
    ratio = float(np.mean(above) / mean_flux) if above.size > 0 else 1.0
    return float(np.clip((ratio - 1.0) / 4.0, 0.0, 1.0))


def _dynamic_range(mono: np.ndarray) -> float:
    """95th–5th percentile of RMS in 50 ms windows.  Range [0, 1].

    Maps 40 dB → 1.0.
    """
    window = int(0.05 * _ANALYSIS_SR)
    if window < 1:
        return 0.0
    n_frames = len(mono) // window
    if n_frames < 2:
        return 0.0
    frames = mono[: n_frames * window].reshape(n_frames, window)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + _EPS)
    p95 = np.percentile(rms, 95)
    p05 = np.percentile(rms, 5)
    dr_db = 20.0 * np.log10(p95 / (p05 + _EPS))
    return float(np.clip(dr_db / 40.0, 0.0, 1.0))


def _noise_floor(power: np.ndarray) -> float:
    """Fraction of spectral energy below -60 dB relative to peak.  Range [0, 1].

    power: (freq_bins, time_frames).
    """
    total = np.sum(power) + _EPS
    peak_power = np.max(power)
    threshold = peak_power * (10 ** (-60.0 / 10.0))
    low_energy = np.sum(power[power < threshold])
    ratio = low_energy / total
    return float(np.clip(ratio * 100.0, 0.0, 1.0))


def _stereo_correlation(path: pathlib.Path, is_mono: bool) -> float:
    """Pearson r between L/R channels, mapped to [0, 1].

    Returns 0.5 for mono (neutral — neither wide nor mono-like).
    """
    if is_mono:
        return 0.5
    try:
        stereo, _ = read_audio(path, mono=False, target_rate=_ANALYSIS_SR)
        if stereo.shape[0] < 2:
            return 0.5
        max_samples = int(_MAX_DURATION * _ANALYSIS_SR)
        L = stereo[0, :max_samples]
        R = stereo[1, :max_samples]
        r = float(np.corrcoef(L, R)[0, 1])
        # Map [-1, 1] → [0, 1]  (r=1 → correlation=1.0, r=-1 → 0.0)
        return float(np.clip((r + 1.0) / 2.0, 0.0, 1.0))
    except Exception:
        return 0.5


def _harmonic_decay(power: np.ndarray, freqs: np.ndarray) -> float:
    """Ratio of high-frequency to low-frequency energy (log-scaled).  Range [0, 1].

    Splits at 1 kHz.  A value near 1 means sustained high-frequency energy
    (strings, piano), near 0 means most energy is in bass/low-mids.

    freqs: 1-D array of frequency bin centres (Hz).
    power: (freq_bins, time_frames).
    """
    split_bin = np.searchsorted(freqs, _SPLIT_HZ)
    low_energy  = np.sum(power[:split_bin]) + _EPS
    high_energy = np.sum(power[split_bin:]) + _EPS
    ratio = high_energy / low_energy
    # log10(ratio): 0 → ratio=1, -2 → all low, +1 → all high
    mapped = float(np.clip((np.log10(ratio) + 2.0) / 3.0, 0.0, 1.0))
    return mapped


def _vocal_naturalness(magnitude: np.ndarray, freqs: np.ndarray) -> float:
    """Smoothness of the spectral envelope in the 200–4000 Hz vocal band.  Range [0, 1].

    Applies a boxcar smoother along frequency bins and measures residual
    deviation.  Small residual = smooth formant structure = natural voice.

    magnitude: (freq_bins, time_frames).
    freqs: 1-D array of frequency bin centres (Hz).
    """
    lo_bin = int(np.searchsorted(freqs, _VOCAL_LOW_HZ))
    hi_bin = int(np.searchsorted(freqs, _VOCAL_HIGH_HZ))
    band = magnitude[lo_bin:hi_bin]
    if band.shape[0] < 16:
        return 0.5   # not enough bins — return neutral

    mean_spectrum = np.mean(band, axis=1) + _EPS        # (vocal_bins,)
    smoothed = uniform_filter1d(mean_spectrum, size=15)
    residual = float(np.mean(np.abs(mean_spectrum - smoothed) / (smoothed + _EPS)))
    return float(np.clip(1.0 - residual / 0.3, 0.0, 1.0))
