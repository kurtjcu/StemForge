"""Fast spectral profiling and separator recommendation for StemForge.

Reads at 22050 Hz, truncates to 60 s, computes one STFT and derives
9 features from shared arrays.  Typical runtime: < 400 ms on a modern CPU.

The central addition over the original 7-feature version is
``drum_intrusion_risk`` — a composite score that detects when Roformer is
likely to bleed drums into the vocal stem, triggering a Demucs recommendation
before any other sub-classification is attempted.

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

_ANALYSIS_SR    = 22_050   # Hz — covers full audible spectrum, fast I/O
_MAX_DURATION   = 60.0     # seconds — cap analysis window
_MIN_DURATION   = 0.5      # seconds — raise below this
_SHORT_DURATION = 2.0      # seconds — downgrade to "low" confidence

_N_FFT = 2048
_HOP   = 512

_EPS = 1e-9


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AudioProfile:
    """Feature vector extracted from a short spectral analysis window.

    All scalar features are normalised to [0.0, 1.0].
    """
    spectral_flatness:   float  # 0 = tonal, 1 = noise-like  (Wiener entropy)
    transient_sharpness: float  # 0 = smooth sustain, 1 = very sharp attacks
    transient_density:   float  # 0 = sparse onsets, 1 = dense (fraction of onset frames)
    dynamic_range:       float  # 0 = heavily compressed, 1 = wide dynamic range
    noise_floor:         float  # 0 = clean recording, 1 = very noisy
    stereo_correlation:  float  # 0 = wide/decorrelated, 1 = mono-like / phase-correlated
    harmonic_decay:      float  # 0 = fast spectral rolloff, 1 = sustained high-frequency content
    vocal_naturalness:   float  # 0 = synthetic / non-vocal, 1 = smooth natural formants
    drum_intrusion_risk: float  # 0 = clean / no drums, 1 = heavy drum bleed risk
    duration_seconds:    float
    is_mono:             bool
    analysis_note:       str    # Edge-case notes surfaced in the UI


@dataclass(frozen=True, slots=True)
class Recommendation:
    """Separator recommendation produced by recommend_separator()."""
    engine:          str       # "Demucs" or "BS-Roformer"
    model_id:        str       # registry ID, e.g. "htdemucs", "roformer-viperx-vocals"
    reason:          str       # Human-readable explanation shown to the user
    confidence:      str       # "high", "moderate", or "low"
    license_warning: str = ""  # Non-empty if recommended model has licensing concerns


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def profile_audio(path: pathlib.Path) -> AudioProfile:
    """Analyse *path* and return an AudioProfile.

    One stereo file read → mono derived from channels → one STFT → 9 features.
    The half-wave-rectified spectral diff (``pos_diff``) is pre-computed once
    and shared by the transient and drum-intrusion extractors.

    Raises
    ------
    AudioProcessingError
        If the file is shorter than 0.5 s.
    """
    info = probe(path)
    if info.duration < _MIN_DURATION:
        raise AudioProcessingError(
            f"Audio too short for analysis ({info.duration:.2f}s < {_MIN_DURATION}s).",
            path=str(path),
        )

    notes: list[str] = []

    # --- Single file read: stereo; derive mono (avoids a second read) ---
    waveform, _ = read_audio(path, mono=False, target_rate=_ANALYSIS_SR)
    is_mono = waveform.shape[0] == 1

    max_samples = int(_MAX_DURATION * _ANALYSIS_SR)
    waveform    = waveform[:, :max_samples]
    mono        = np.mean(waveform, axis=0)   # (samples,)

    duration_seconds = waveform.shape[1] / _ANALYSIS_SR
    if duration_seconds < _SHORT_DURATION:
        notes.append(f"Short file ({duration_seconds:.1f}s) — confidence limited.")

    # --- Shared STFT ---
    freqs, _times, stft = scipy.signal.stft(
        mono, fs=_ANALYSIS_SR, nperseg=_N_FFT, noverlap=_N_FFT - _HOP,
    )
    magnitude = np.abs(stft)   # (n_bins, n_frames)
    power     = magnitude ** 2

    # --- Pre-computed arrays shared by multiple extractors ---
    # pos_diff: half-wave rectified frame-to-frame spectral change
    pos_diff = np.maximum(np.diff(magnitude, axis=1), 0.0)  # (n_bins, n_frames-1)
    pos_flux = pos_diff.sum(axis=0)                         # (n_frames-1,)

    # --- 9 feature extractors ---
    sf_val   = _spectral_flatness(power)
    ts_val   = _transient_sharpness(pos_flux)
    td_val   = _transient_density(pos_flux)
    dr_val   = _dynamic_range(mono)
    nf_val   = _noise_floor(power)
    hd_val   = _harmonic_decay(power, freqs)
    vn_val   = _vocal_naturalness(magnitude, freqs)
    dir_val  = _drum_intrusion_risk(magnitude, freqs, pos_diff, pos_flux)

    # Stereo correlation uses pre-loaded L/R — no second file read
    if is_mono:
        sc_val = 0.5
        notes.append("Mono audio — stereo correlation not applicable.")
    else:
        L = waveform[0]
        R = waveform[1]
        r      = float(np.corrcoef(L, R)[0, 1])
        sc_val = float(np.clip((r + 1.0) / 2.0, 0.0, 1.0))

    # Surfaced diagnostic notes
    if dir_val > 0.45:
        notes.append(f"Drum-intrusion risk: {dir_val:.2f} (high).")
    elif dir_val > 0.30:
        notes.append(f"Drum-intrusion risk: {dir_val:.2f} (moderate).")

    return AudioProfile(
        spectral_flatness   = float(sf_val),
        transient_sharpness = float(ts_val),
        transient_density   = float(td_val),
        dynamic_range       = float(dr_val),
        noise_floor         = float(nf_val),
        stereo_correlation  = float(sc_val),
        harmonic_decay      = float(hd_val),
        vocal_naturalness   = float(vn_val),
        drum_intrusion_risk = float(dir_val),
        duration_seconds    = float(duration_seconds),
        is_mono             = is_mono,
        analysis_note       = "  ".join(notes),
    )


def recommend_separator(profile: AudioProfile) -> Recommendation:
    """Return a Recommendation based on an AudioProfile.

    Priority order — drum-intrusion risk is evaluated first so that Roformer
    is never recommended for content where it is known to leak drums into
    the vocal stem.

    1.  drum_intrusion_risk > 0.55           →  Demucs (high bleed risk)
    2.  drum_intrusion_risk > 0.35
          AND vocal_naturalness < 0.65       →  Demucs (moderate risk, no dominant vocal)
    3.  spectral_flatness > 0.45             →  Demucs (synthetic / heavily processed)
    4.  dynamic_range < 0.08
          AND stereo_correlation > 0.92      →  Demucs (over-mastered / fake stereo)
    5.  vocal_naturalness > 0.65
          AND drum_intrusion_risk < 0.35     →  Roformer vocal (viperx or kj)
    6.  harmonic_decay > 0.60
          AND drum_intrusion_risk < 0.40     →  Roformer 6-stem
    7.  spectral_flatness < 0.35
          AND drum_intrusion_risk < 0.35     →  Roformer 4-stem
    8.  Fallback                             →  htdemucs (safe all-rounder)
    """
    sf       = profile.spectral_flatness
    ts       = profile.transient_sharpness
    td       = profile.transient_density
    dr       = profile.dynamic_range
    sc       = profile.stereo_correlation
    hd       = profile.harmonic_decay
    vn       = profile.vocal_naturalness
    dir_risk = profile.drum_intrusion_risk

    def _cap(conf: str) -> str:
        """Apply edge-case confidence caps."""
        if profile.is_mono and conf == "high":
            return "moderate"
        if profile.duration_seconds < _SHORT_DURATION:
            return "low"
        return conf

    def _margin(*distances: float) -> str:
        """Confidence from the minimum distance to the nearest decision boundary."""
        m = min(abs(d) for d in distances)
        if m > 0.15:
            return "high"
        if m > 0.05:
            return "moderate"
        return "low"

    # ── Veto 1: High drum-intrusion risk ─────────────────────────────────────
    # Roformer is known to bleed drums into the vocal stem when kick, snare and
    # cymbal transients dominate the mix.  Demucs is always safer in this region.
    if dir_risk > 0.55:
        model_id = "htdemucs_ft" if (ts > 0.50 or td > 0.40) else "htdemucs"
        reason = (
            f"High drum-intrusion risk detected (score {dir_risk:.2f}). "
            "Strong kick, snare or cymbal transients are present; Roformer is likely "
            "to bleed drum energy into the vocal stem. "
            f"Demucs ({model_id}) will produce cleaner separation."
        )
        return Recommendation(
            engine="Demucs", model_id=model_id,
            reason=reason, confidence=_cap(_margin(dir_risk - 0.55)),
        )

    # ── Veto 2: Moderate drum risk without a dominant vocal ───────────────────
    # When drum energy is meaningful but vocals are not strongly present,
    # Roformer's vocal extraction can become unreliable.
    if dir_risk > 0.35 and vn < 0.65:
        model_id = "htdemucs_ft" if td > 0.35 else "htdemucs"
        reason = (
            f"Moderate drum-intrusion risk (score {dir_risk:.2f}) detected "
            "without a clearly dominant vocal signal. "
            "Demucs is the safer choice to avoid drum bleed into separated stems."
        )
        return Recommendation(
            engine="Demucs", model_id=model_id,
            reason=reason, confidence=_cap(_margin(dir_risk - 0.35, 0.65 - vn)),
        )

    # ── Veto 3: Synthetic / heavily processed audio ───────────────────────────
    if sf > 0.45:
        if td > 0.40:
            model_id = "htdemucs_ft"
            reason = (
                "Processed or electronic texture with frequent transients detected. "
                "htdemucs_ft handles synthetic pop/electronic content well."
            )
            conf = _margin(sf - 0.45, td - 0.40)
        else:
            model_id = "mdx_extra"
            reason = (
                "Synthetic or heavily processed audio with flat spectral texture. "
                "mdx_extra's MDX architecture excels at clean electronic separation."
            )
            conf = _margin(sf - 0.45)
        return Recommendation(
            engine="Demucs", model_id=model_id,
            reason=reason, confidence=_cap(conf),
        )

    # ── Veto 4: Over-mastered / near-zero dynamic range + fake stereo ─────────
    if dr < 0.08 and sc > 0.92:
        reason = (
            "Extremely compressed dynamic range with near-perfect stereo correlation. "
            "This profile (heavily limited or synthetically stereo-widened mix) "
            "is better handled by Demucs htdemucs_ft."
        )
        return Recommendation(
            engine="Demucs", model_id="htdemucs_ft",
            reason=reason, confidence=_cap("moderate"),
        )

    # ── All vetos passed: natural/organic audio, drum risk ≤ 0.55 ────────────
    # For moderate drum risk (0.35–0.55) that reached here, vocals ARE dominant
    # (vn ≥ 0.65) so we tentatively try Roformer vocal models below.

    # ── Roformer A: Vocal-forward, low drum risk ──────────────────────────────
    # High vocal naturalness + low drum risk = ideal Roformer conditions.
    if vn > 0.65 and dir_risk < 0.35:
        if sf < 0.25:
            model_id = "roformer-viperx-vocals"
            reason = (
                "Natural, smooth vocals with clean spectral envelope detected. "
                "Low drum-intrusion risk — ViperX BS-Roformer (SDR 12.97) will produce "
                "the cleanest vocal isolation. "
                "If MIDI extraction is planned, this stem will be ideal for BasicPitch."
            )
        else:
            model_id = "roformer-kj-vocals"
            reason = (
                "Natural vocals with moderate spectral complexity detected. "
                "Low drum-intrusion risk — MelBand-Roformer (KimberleyJensen) handles "
                "busier vocal textures well."
            )
        conf = _margin(vn - 0.65, 0.35 - dir_risk)
        license_warn = ""
        if model_id == "roformer-kj-vocals":
            license_warn = (
                "This model's weights are licensed under GPL-3.0 (copyleft) — "
                "MIT-licensed alternatives exist (ViperX, ZFTurbo)."
            )
        return Recommendation(
            engine="BS-Roformer", model_id=model_id,
            reason=reason, confidence=_cap(conf),
            license_warning=license_warn,
        )

    # ── Roformer B: Harmonic-rich instruments, low drum risk ─────────────────
    # Sustained high-frequency content (piano, guitar, strings) with low
    # drum risk is the prime use-case for the 6-stem model.
    if hd > 0.60 and dir_risk < 0.40:
        reason = (
            "Rich harmonic content (guitar, piano or strings) detected with low "
            "drum-intrusion risk. "
            "The 6-stem model isolates guitar and piano in addition to the core 4 stems. "
            "Ideal if MIDI extraction of melodic instruments is planned."
        )
        conf = _margin(hd - 0.60, 0.40 - dir_risk)
        return Recommendation(
            engine="BS-Roformer", model_id="roformer-jarredou-6stem",
            reason=reason, confidence=_cap(conf),
            license_warning=(
                "This model's weights have no license specified — "
                "use at your own legal risk."
            ),
        )

    # ── Roformer C: Organic / natural baseline, low drum risk ─────────────────
    if sf < 0.35 and dir_risk < 0.35:
        reason = (
            "Organic, tonal audio with low drum-intrusion risk detected. "
            "4-stem BS-Roformer (ZFTurbo, SDR 9.66) provides clean baseline "
            "separation for this type of recording."
        )
        conf = _margin(0.35 - sf, 0.35 - dir_risk)
        return Recommendation(
            engine="BS-Roformer", model_id="roformer-zfturbo-4stem",
            reason=reason, confidence=_cap(conf),
        )

    # ── Fallback: htdemucs — safe all-rounder ────────────────────────────────
    reason = (
        "Mixed or ambiguous spectral profile — borderline drum-intrusion risk or "
        "neither clearly synthetic nor fully organic. "
        "htdemucs is the most robust all-rounder for ambiguous material."
    )
    return Recommendation(
        engine="Demucs", model_id="htdemucs",
        reason=reason, confidence=_cap("low"),
    )


# ---------------------------------------------------------------------------
# Feature extractors
# ---------------------------------------------------------------------------

def _spectral_flatness(power: np.ndarray) -> float:
    """Wiener entropy per frame, averaged.  Range [0, 1].

    power: (n_bins, n_frames).  Values ≥ 0.
    Near 0 = tonal/harmonic.  Near 1 = noise-like / spectrally flat.
    """
    p        = power + _EPS
    log_mean = np.mean(np.log(p), axis=0)   # (n_frames,)
    arith_m  = np.mean(p, axis=0)
    wiener   = np.exp(log_mean) / (arith_m + _EPS)
    return float(np.clip(np.mean(wiener), 0.0, 1.0))


def _transient_sharpness(pos_flux: np.ndarray) -> float:
    """Peak-to-mean ratio of positive spectral flux.  Range [0, 1].

    Measures how *sharp* individual onsets are (not how many there are).
    pos_flux: (n_frames-1,) half-wave rectified spectral flux.
    """
    if pos_flux.size == 0:
        return 0.0
    p90       = np.percentile(pos_flux, 90)
    mean_flux = np.mean(pos_flux) + _EPS
    above     = pos_flux[pos_flux > p90]
    ratio     = float(np.mean(above) / mean_flux) if above.size > 0 else 1.0
    return float(np.clip((ratio - 1.0) / 4.0, 0.0, 1.0))


def _transient_density(pos_flux: np.ndarray) -> float:
    """Fraction of frames classified as onsets (above mean + 1σ).  Range [0, 1].

    Measures how *often* onsets occur, independently of their sharpness.
    Normalised so that 25% of frames above threshold → 1.0.
    A Gaussian noise signal has ~16% above this threshold; drum-heavy mixes
    typically reach 20–40%.
    """
    if pos_flux.size < 4:
        return 0.0
    threshold = np.mean(pos_flux) + np.std(pos_flux)
    fraction  = float(np.mean(pos_flux > threshold))
    return float(np.clip(fraction / 0.25, 0.0, 1.0))


def _dynamic_range(mono: np.ndarray) -> float:
    """95th–5th percentile of RMS in 50 ms windows, mapped to [0, 1].

    40 dB → 1.0.  Heavily limited (mastered) audio scores near 0.
    """
    window  = int(0.05 * _ANALYSIS_SR)
    if window < 1:
        return 0.0
    n_frames = len(mono) // window
    if n_frames < 2:
        return 0.0
    frames  = mono[: n_frames * window].reshape(n_frames, window)
    rms     = np.sqrt(np.mean(frames ** 2, axis=1) + _EPS)
    p95     = np.percentile(rms, 95)
    p05     = np.percentile(rms, 5)
    dr_db   = 20.0 * np.log10(p95 / (p05 + _EPS))
    return float(np.clip(dr_db / 40.0, 0.0, 1.0))


def _noise_floor(power: np.ndarray) -> float:
    """Fraction of spectral energy below −60 dB relative to peak.  Range [0, 1].

    Near 0 = clean recording.  Near 1 = heavy background noise.
    """
    total      = np.sum(power) + _EPS
    peak_power = np.max(power)
    threshold  = peak_power * (10 ** (-60.0 / 10.0))
    low_energy = np.sum(power[power < threshold])
    return float(np.clip(low_energy / total * 100.0, 0.0, 1.0))


def _harmonic_decay(power: np.ndarray, freqs: np.ndarray) -> float:
    """High-to-low frequency energy ratio (log-scaled).  Range [0, 1].

    Splits at 1 kHz.  Near 1 = sustained high-frequency content (piano,
    strings, guitar).  Near 0 = most energy in bass / low-mids.
    """
    split_bin   = np.searchsorted(freqs, 1_000)
    low_energy  = np.sum(power[:split_bin]) + _EPS
    high_energy = np.sum(power[split_bin:]) + _EPS
    ratio       = high_energy / low_energy
    return float(np.clip((np.log10(ratio) + 2.0) / 3.0, 0.0, 1.0))


def _vocal_naturalness(magnitude: np.ndarray, freqs: np.ndarray) -> float:
    """Smoothness of the spectral envelope in the 200–4000 Hz vocal band.  Range [0, 1].

    Applies a boxcar smoother along frequency bins and measures the residual
    deviation.  Small residual = smooth formant structure ≈ natural voice.
    Near 1 = smooth, formant-rich vocal.  Near 0 = rough / non-vocal.
    """
    lo_bin = int(np.searchsorted(freqs, 200))
    hi_bin = int(np.searchsorted(freqs, 4_000))
    band   = magnitude[lo_bin:hi_bin]
    if band.shape[0] < 16:
        return 0.5   # too few bins — return neutral

    mean_spectrum = np.mean(band, axis=1) + _EPS   # (vocal_bins,)
    smoothed      = uniform_filter1d(mean_spectrum, size=15)
    residual      = float(np.mean(np.abs(mean_spectrum - smoothed) / (smoothed + _EPS)))
    return float(np.clip(1.0 - residual / 0.3, 0.0, 1.0))


def _drum_intrusion_risk(
    magnitude: np.ndarray,   # (n_bins, n_frames)
    freqs:     np.ndarray,   # (n_bins,)
    pos_diff:  np.ndarray,   # (n_bins, n_frames-1)  half-wave rectified spectral diff
    pos_flux:  np.ndarray,   # (n_frames-1,)          total positive spectral flux
) -> float:
    """Composite drum-intrusion risk score.  Range [0, 1].

    Five sub-signals (weighted average):
      kick_impulsive  (0.20) — impulsive transients in the 60–200 Hz kick band
      cymbal_share    (0.25) — cymbal/hi-hat dominance in 2–8 kHz flux
      broadband_hit   (0.20) — broadband impulses across all 4 octave bands
      centroid_var    (0.20) — spectral centroid coefficient of variation
      energy_kurtosis (0.15) — excess kurtosis of per-frame energy

    A drum-free string quartet should score < 0.15.
    A full drum-kit mix should score > 0.55.
    Heavy metal / trap beats can reach 0.80+.
    """
    n_bins, n_frames = magnitude.shape

    def _brange(lo_hz: float, hi_hz: float) -> tuple[int, int]:
        lo = int(np.clip(np.searchsorted(freqs, lo_hz), 0, n_bins))
        hi = int(np.clip(np.searchsorted(freqs, hi_hz), 0, n_bins))
        return lo, hi

    n_flux = pos_flux.size

    # ── 1. Kick-band impulsiveness (60–200 Hz) ────────────────────────────────
    # Kick drums create impulsive (high peak-to-mean ratio) transients in this
    # band; sustained bass guitar creates a low ratio.
    k_lo, k_hi = _brange(60, 200)
    if k_hi > k_lo and n_flux > 0:
        kick_flux  = pos_diff[k_lo:k_hi].sum(axis=0)   # (n_frames-1,)
        kf_mean    = np.mean(kick_flux) + _EPS
        kf_p90     = np.percentile(kick_flux, 90)
        # p90/mean=1.5 for Gaussian → 0, =6.5 for impulsive drums → 1.0
        kick_score = float(np.clip((kf_p90 / kf_mean - 1.5) / 5.0, 0.0, 1.0))
    else:
        kick_score = 0.0

    # ── 2. Cymbal / hi-hat dominance (2–8 kHz flux share in loud frames) ──────
    # Cymbals and hi-hats have a characteristic signature: they produce a large
    # share of the spectral flux in the 2–8 kHz range during high-energy frames.
    c_lo, c_hi = _brange(2_000, 8_000)
    if c_hi > c_lo and n_flux > 0:
        cym_flux  = pos_diff[c_lo:c_hi].sum(axis=0)   # (n_frames-1,)
        p75       = np.percentile(pos_flux, 75)
        hi_mask   = pos_flux > p75
        if hi_mask.sum() > 2:
            share     = cym_flux[hi_mask] / (pos_flux[hi_mask] + _EPS)
            # 35% share in high-flux frames → saturates at 1.0
            cym_score = float(np.clip(np.mean(share) / 0.35, 0.0, 1.0))
        else:
            cym_score = 0.0
    else:
        cym_score = 0.0

    # ── 3. Broadband impulse detection ────────────────────────────────────────
    # Snare / kick generate simultaneous transients across all frequency bands.
    # A guitar pluck or piano note creates a narrow-band transient in 1–2 bands.
    # We normalize each band to its own mean so that bass-heavy music doesn't
    # confuse low-frequency energy for broadband drum transients.
    if n_flux > 8:
        band_defs = [
            _brange(20,    250),
            _brange(250,   1_000),
            _brange(1_000, 4_000),
            _brange(4_000, _ANALYSIS_SR // 2),
        ]
        band_flux = np.stack([
            pos_diff[lo:hi].sum(axis=0) if hi > lo else np.zeros(n_flux)
            for lo, hi in band_defs
        ])                                                   # (4, n_frames-1)
        band_means = band_flux.mean(axis=1, keepdims=True) + _EPS  # (4, 1)
        band_norm  = band_flux / band_means                  # normalized to each band's own mean

        p80     = np.percentile(pos_flux, 80)
        hi_mask = pos_flux > p80
        if hi_mask.sum() > 4:
            # Count how many bands are simultaneously > 1.5× their own mean
            n_active       = (band_norm[:, hi_mask] > 1.5).sum(axis=0)  # (n_hi,)
            # 3+ bands simultaneously elevated → broadband; saturate at 50% of frames
            broadband_score = float(np.clip(np.mean(n_active >= 3) / 0.50, 0.0, 1.0))
        else:
            broadband_score = 0.0
    else:
        broadband_score = 0.0

    # ── 4. Spectral centroid coefficient of variation ─────────────────────────
    # Drums shift the spectral centroid dramatically: kick pulls it low,
    # cymbal splashes pull it high.  High CoV → drum-like variance.
    frame_sum = magnitude.sum(axis=0) + _EPS                              # (n_frames,)
    centroid  = (freqs[:, np.newaxis] * magnitude).sum(axis=0) / frame_sum  # (n_frames,)
    centroid_cv    = float(np.std(centroid) / (np.mean(centroid) + _EPS))
    # CoV of 0.40 → saturates at 1.0; ambient/vocal mixes typically < 0.20
    centroid_score = float(np.clip(centroid_cv / 0.40, 0.0, 1.0))

    # ── 5. Excess kurtosis of per-frame energy ────────────────────────────────
    # Drum hits create a leptokurtic (heavy-tailed) energy distribution:
    # most frames are quiet, occasional frames (drum hits) are very loud.
    # Excess kurtosis = kurtosis − 3; Gaussian → 0, drums → 5–30+.
    energy      = (magnitude ** 2).sum(axis=0)   # (n_frames,)
    mu          = np.mean(energy)
    sigma       = np.std(energy) + _EPS
    excess_kurt = float(np.mean(((energy - mu) / sigma) ** 4) - 3.0)
    # 15 → 1.0; ambient music typically < 5
    kurtosis_score = float(np.clip(excess_kurt / 15.0, 0.0, 1.0))

    composite = (
        0.20 * kick_score     +
        0.25 * cym_score      +
        0.20 * broadband_score +
        0.20 * centroid_score +
        0.15 * kurtosis_score
    )
    return float(np.clip(composite, 0.0, 1.0))
