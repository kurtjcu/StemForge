"""Effects chain pipeline — per-stem channel strip processing.

Applies a user-defined chain of effects (EQ, compressor, noise gate,
stereo width) with both DSP and ML method variants. Each effect slot
can be independently bypassed.
"""

from __future__ import annotations

import logging
import pathlib
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import soundfile as sf

from utils.paths import ENHANCE_DIR

log = logging.getLogger("stemforge.pipelines.effects")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class EffectSlot:
    """Single effect in the chain."""
    effect_type: str    # "eq" | "compressor" | "gate" | "stereo_width"
    method: str         # "dsp" | "la2a" | "spectral" | "deepfilter"
    bypass: bool = False
    params: dict = field(default_factory=dict)


@dataclass
class EffectsConfig:
    """Per-run configuration for :class:`EffectsPipeline`."""
    chain: list[EffectSlot] = field(default_factory=list)
    output_dir: pathlib.Path = ENHANCE_DIR


@dataclass
class EffectsResult:
    """Return value from :meth:`EffectsPipeline.run`."""
    output_path: pathlib.Path
    chain_summary: str  # e.g. "EQ + LA-2A Compressor + Stereo Width"


# ---------------------------------------------------------------------------
# ML model caches (module-level, released by clear())
# ---------------------------------------------------------------------------

_la2a_model = None
_df_model = None
_df_state = None

_EFFECT_LABELS = {
    "eq": "EQ",
    "compressor": "Compressor",
    "gate": "Gate",
    "stereo_width": "Stereo Width",
}

_METHOD_LABELS = {
    "dsp": "",
    "la2a": "LA-2A",
    "spectral": "Spectral",
    "deepfilter": "DeepFilterNet",
}


# ---------------------------------------------------------------------------
# EQ — 3-band parametric (low shelf, mid peak, high shelf)
# ---------------------------------------------------------------------------

def _biquad_lowshelf(freq: float, gain_db: float, sr: int, q: float = 0.707) -> np.ndarray:
    """Compute SOS coefficients for a low-shelf biquad filter."""
    from scipy.signal import tf2sos
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * np.pi * freq / sr
    alpha = np.sin(w0) / (2 * q)
    cos_w0 = np.cos(w0)
    sq_A = np.sqrt(A)

    b0 = A * ((A + 1) - (A - 1) * cos_w0 + 2 * sq_A * alpha)
    b1 = 2 * A * ((A - 1) - (A + 1) * cos_w0)
    b2 = A * ((A + 1) - (A - 1) * cos_w0 - 2 * sq_A * alpha)
    a0 = (A + 1) + (A - 1) * cos_w0 + 2 * sq_A * alpha
    a1 = -2 * ((A - 1) + (A + 1) * cos_w0)
    a2 = (A + 1) + (A - 1) * cos_w0 - 2 * sq_A * alpha

    return tf2sos([b0, b1, b2], [a0, a1, a2])


def _biquad_highshelf(freq: float, gain_db: float, sr: int, q: float = 0.707) -> np.ndarray:
    """Compute SOS coefficients for a high-shelf biquad filter."""
    from scipy.signal import tf2sos
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * np.pi * freq / sr
    alpha = np.sin(w0) / (2 * q)
    cos_w0 = np.cos(w0)
    sq_A = np.sqrt(A)

    b0 = A * ((A + 1) + (A - 1) * cos_w0 + 2 * sq_A * alpha)
    b1 = -2 * A * ((A - 1) + (A + 1) * cos_w0)
    b2 = A * ((A + 1) + (A - 1) * cos_w0 - 2 * sq_A * alpha)
    a0 = (A + 1) - (A - 1) * cos_w0 + 2 * sq_A * alpha
    a1 = 2 * ((A - 1) - (A + 1) * cos_w0)
    a2 = (A + 1) - (A - 1) * cos_w0 - 2 * sq_A * alpha

    return tf2sos([b0, b1, b2], [a0, a1, a2])


def _biquad_peaking(freq: float, gain_db: float, sr: int, q: float = 1.0) -> np.ndarray:
    """Compute SOS coefficients for a peaking EQ biquad filter."""
    from scipy.signal import tf2sos
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * np.pi * freq / sr
    alpha = np.sin(w0) / (2 * q)
    cos_w0 = np.cos(w0)

    b0 = 1 + alpha * A
    b1 = -2 * cos_w0
    b2 = 1 - alpha * A
    a0 = 1 + alpha / A
    a1 = -2 * cos_w0
    a2 = 1 - alpha / A

    return tf2sos([b0, b1, b2], [a0, a1, a2])


def _apply_eq(audio: np.ndarray, sr: int, params: dict) -> np.ndarray:
    """Apply 3-band parametric EQ (low shelf, mid peak, high shelf)."""
    from scipy.signal import sosfilt

    low_gain = params.get("low_gain", 0.0)
    low_freq = params.get("low_freq", 100.0)
    mid_gain = params.get("mid_gain", 0.0)
    mid_freq = params.get("mid_freq", 1000.0)
    mid_q = params.get("mid_q", 1.0)
    high_gain = params.get("high_gain", 0.0)
    high_freq = params.get("high_freq", 8000.0)

    # Skip if all gains are zero
    if abs(low_gain) < 0.01 and abs(mid_gain) < 0.01 and abs(high_gain) < 0.01:
        return audio

    # Build cascaded SOS sections
    sections = []
    if abs(low_gain) >= 0.01:
        sections.append(_biquad_lowshelf(low_freq, low_gain, sr))
    if abs(mid_gain) >= 0.01:
        sections.append(_biquad_peaking(mid_freq, mid_gain, sr, q=mid_q))
    if abs(high_gain) >= 0.01:
        sections.append(_biquad_highshelf(high_freq, high_gain, sr))

    if not sections:
        return audio

    sos = np.vstack(sections)

    # Process each channel independently
    if audio.ndim == 1:
        return sosfilt(sos, audio).astype(np.float32)

    result = np.empty_like(audio)
    for ch in range(audio.shape[0]):
        result[ch] = sosfilt(sos, audio[ch]).astype(np.float32)
    return result


# ---------------------------------------------------------------------------
# Compressor — DSP (RMS envelope follower)
# ---------------------------------------------------------------------------

def _apply_compressor_dsp(audio: np.ndarray, sr: int, params: dict) -> np.ndarray:
    """DSP compressor with RMS envelope follower and attack/release."""
    threshold_db = params.get("threshold_db", -20.0)
    ratio = params.get("ratio", 4.0)
    attack_ms = params.get("attack_ms", 10.0)
    release_ms = params.get("release_ms", 100.0)
    makeup_db = params.get("makeup_db", 0.0)

    threshold = 10 ** (threshold_db / 20.0)
    attack_coeff = np.exp(-1.0 / (sr * attack_ms / 1000.0))
    release_coeff = np.exp(-1.0 / (sr * release_ms / 1000.0))
    makeup_gain = 10 ** (makeup_db / 20.0)

    def _compress_channel(signal: np.ndarray) -> np.ndarray:
        envelope = np.zeros_like(signal)
        env = 0.0
        for i in range(len(signal)):
            level = abs(signal[i])
            if level > env:
                env = attack_coeff * env + (1 - attack_coeff) * level
            else:
                env = release_coeff * env + (1 - release_coeff) * level
            envelope[i] = env

        # Compute gain reduction
        gain = np.ones_like(signal)
        mask = envelope > threshold
        if ratio >= 100:
            # Limiter mode
            gain[mask] = threshold / np.maximum(envelope[mask], 1e-10)
        else:
            # Standard compression
            db_over = 20 * np.log10(np.maximum(envelope[mask], 1e-10) / threshold)
            db_reduction = db_over * (1 - 1 / ratio)
            gain[mask] = 10 ** (-db_reduction / 20.0)

        return (signal * gain * makeup_gain).astype(np.float32)

    if audio.ndim == 1:
        return _compress_channel(audio)

    result = np.empty_like(audio)
    for ch in range(audio.shape[0]):
        result[ch] = _compress_channel(audio[ch])
    return result


# ---------------------------------------------------------------------------
# Compressor — LA-2A (neural, micro-tcn)
# ---------------------------------------------------------------------------

def _get_la2a_model(device_str: str | None = None):
    """Load or return cached LA-2A TCN model."""
    global _la2a_model
    if _la2a_model is not None:
        return _la2a_model

    import sys
    import os
    import torch
    from utils.cache import get_model_cache_dir

    # Add vendor to path for micro_tcn import
    vendor_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor")
    if vendor_dir not in sys.path:
        sys.path.insert(0, vendor_dir)

    from micro_tcn.tcn import TCNModel

    cache_dir = get_model_cache_dir("micro-tcn")
    ckpt_path = cache_dir / "la2a.ckpt"

    if not ckpt_path.exists():
        log.info("Downloading micro-tcn LA-2A checkpoint...")
        from huggingface_hub import hf_hub_download
        downloaded = hf_hub_download(
            repo_id="csteinmetz1/micro-tcn",
            filename="la2a/lightning_logs/version_0/checkpoints/epoch=79-step=51039.ckpt",
            cache_dir=str(cache_dir),
            local_dir=str(cache_dir),
        )
        # Move to standard name
        pathlib.Path(downloaded).rename(ckpt_path)
        log.info("LA-2A checkpoint saved to %s", ckpt_path)

    # Load checkpoint — it's a pytorch_lightning checkpoint with state_dict under "state_dict"
    device = device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(str(ckpt_path), map_location=device, weights_only=False)

    # Extract hyperparams from checkpoint
    hparams = checkpoint.get("hyper_parameters", {})

    model = TCNModel(
        nparams=hparams.get("nparams", 2),
        ninputs=hparams.get("ninputs", 1),
        noutputs=hparams.get("noutputs", 1),
        nblocks=hparams.get("nblocks", 4),
        kernel_size=hparams.get("kernel_size", 5),
        dilation_growth=hparams.get("dilation_growth", 10),
        channel_growth=hparams.get("channel_growth", 1),
        channel_width=hparams.get("channel_width", 32),
        stack_size=hparams.get("stack_size", 10),
        grouped=hparams.get("grouped", False),
        causal=hparams.get("causal", True),
    )

    # Load state dict — strip "model." prefix if present (pytorch_lightning convention)
    state = checkpoint.get("state_dict", checkpoint)
    cleaned = {}
    for k, v in state.items():
        # Remove common PL prefixes
        clean_key = k
        for prefix in ("model.", "net."):
            if clean_key.startswith(prefix):
                clean_key = clean_key[len(prefix):]
        cleaned[clean_key] = v

    model.load_state_dict(cleaned, strict=False)
    model.eval()
    model.to(device)
    _la2a_model = model
    log.info("LA-2A TCN model loaded on %s", device)
    return model


def _apply_compressor_la2a(audio: np.ndarray, sr: int, params: dict,
                           device_str: str | None = None) -> np.ndarray:
    """Apply LA-2A style compression via micro-tcn neural model."""
    import torch
    import soxr

    model = _get_la2a_model(device_str)
    device = next(model.parameters()).device

    peak_reduction = params.get("peak_reduction", 50.0) / 100.0  # normalize to 0-1
    gain = params.get("gain", 50.0) / 100.0  # normalize to 0-1

    # Model expects 44100 Hz mono
    target_sr = 44100
    is_stereo = audio.ndim == 2 and audio.shape[0] == 2

    def _process_mono(mono: np.ndarray) -> np.ndarray:
        # Resample to 44100 if needed
        if sr != target_sr:
            mono = soxr.resample(mono, sr, target_sr, quality="HQ")

        # Run inference in chunks (model receptive field is finite)
        x = torch.from_numpy(mono).float().unsqueeze(0).unsqueeze(0).to(device)
        p = torch.tensor([[peak_reduction, gain]]).float().to(device)

        # Expand params to match time dimension for FiLM conditioning
        p = p.unsqueeze(-1).expand(-1, -1, x.shape[-1])

        with torch.no_grad():
            y = model(x, p)

        result = y.squeeze().cpu().numpy()

        # Resample back if needed
        if sr != target_sr:
            result = soxr.resample(result, target_sr, sr, quality="HQ")

        return result.astype(np.float32)

    if is_stereo:
        left = _process_mono(audio[0])
        right = _process_mono(audio[1])
        min_len = min(len(left), len(right))
        return np.stack([left[:min_len], right[:min_len]])
    elif audio.ndim == 2:
        return _process_mono(audio[0]).reshape(1, -1)
    else:
        return _process_mono(audio)


# ---------------------------------------------------------------------------
# Noise Gate — DSP (threshold with envelope)
# ---------------------------------------------------------------------------

def _apply_gate_dsp(audio: np.ndarray, sr: int, params: dict) -> np.ndarray:
    """Threshold noise gate with attack/hold/release envelope."""
    threshold_db = params.get("threshold_db", -40.0)
    attack_ms = params.get("attack_ms", 1.0)
    hold_ms = params.get("hold_ms", 50.0)
    release_ms = params.get("release_ms", 100.0)

    threshold = 10 ** (threshold_db / 20.0)
    attack_samples = max(1, int(sr * attack_ms / 1000.0))
    hold_samples = max(1, int(sr * hold_ms / 1000.0))
    release_samples = max(1, int(sr * release_ms / 1000.0))

    def _gate_channel(signal: np.ndarray) -> np.ndarray:
        n = len(signal)
        gain = np.zeros(n, dtype=np.float32)

        # Detect gate open/close based on RMS in small windows
        win_size = max(1, int(sr * 0.005))  # 5ms windows
        state = 0  # 0=closed, 1=attack, 2=open, 3=hold, 4=release
        hold_counter = 0
        gate_val = 0.0

        for i in range(n):
            # Compute local level
            start = max(0, i - win_size // 2)
            end = min(n, i + win_size // 2 + 1)
            level = np.sqrt(np.mean(signal[start:end] ** 2))

            if level > threshold:
                if state in (0, 4):  # closed or releasing -> attack
                    state = 1
                elif state == 3:  # hold -> open
                    state = 2
                hold_counter = hold_samples

                if state == 1:
                    gate_val = min(1.0, gate_val + 1.0 / attack_samples)
                    if gate_val >= 1.0:
                        gate_val = 1.0
                        state = 2
                elif state == 2:
                    gate_val = 1.0
            else:
                if state == 2:  # open -> hold
                    state = 3
                    hold_counter = hold_samples

                if state == 3:
                    hold_counter -= 1
                    if hold_counter <= 0:
                        state = 4
                    gate_val = 1.0
                elif state == 4:
                    gate_val = max(0.0, gate_val - 1.0 / release_samples)
                    if gate_val <= 0.0:
                        gate_val = 0.0
                        state = 0

            gain[i] = gate_val

        return (signal * gain).astype(np.float32)

    if audio.ndim == 1:
        return _gate_channel(audio)

    result = np.empty_like(audio)
    for ch in range(audio.shape[0]):
        result[ch] = _gate_channel(audio[ch])
    return result


# ---------------------------------------------------------------------------
# Noise Gate — Spectral (TorchGating, GPU-accelerated)
# ---------------------------------------------------------------------------

def _apply_gate_spectral(audio: np.ndarray, sr: int, params: dict,
                         device_str: str | None = None) -> np.ndarray:
    """GPU-accelerated spectral noise gate via TorchGating."""
    import torch
    from torchgating import TorchGating

    stationary = params.get("stationary", True)
    threshold_scale = params.get("threshold_scale", 1.5)

    device = device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu")
    tg = TorchGating(sr, nonstationary=not stationary, n_std_thresh_stationary=threshold_scale).to(device)

    def _process_channel(signal: np.ndarray) -> np.ndarray:
        x = torch.from_numpy(signal).float().unsqueeze(0).to(device)
        with torch.no_grad():
            y = tg(x)
        return y.squeeze(0).cpu().numpy().astype(np.float32)

    if audio.ndim == 1:
        return _process_channel(audio)

    result = np.empty_like(audio)
    for ch in range(audio.shape[0]):
        result[ch] = _process_channel(audio[ch])
    return result


# ---------------------------------------------------------------------------
# Noise Gate — DeepFilterNet (neural vocal denoiser)
# ---------------------------------------------------------------------------

def _get_df_model():
    """Load or return cached DeepFilterNet model."""
    global _df_model, _df_state
    if _df_model is not None:
        return _df_model, _df_state

    try:
        from df.enhance import init_df
    except ImportError:
        raise RuntimeError(
            "DeepFilterNet is not installed (requires numpy<2.0). "
            "Use the DSP or Spectral gate method instead."
        )
    model, df_state, _ = init_df()
    _df_model = model
    _df_state = df_state
    log.info("DeepFilterNet model loaded")
    return model, df_state


def _apply_gate_deepfilter(audio: np.ndarray, sr: int, params: dict) -> np.ndarray:
    """Neural vocal noise suppression via DeepFilterNet."""
    try:
        from df.enhance import enhance
    except ImportError:
        raise RuntimeError(
            "DeepFilterNet is not installed (requires numpy<2.0). "
            "Use the DSP or Spectral gate method instead."
        )

    import torch
    import soxr

    model, df_state = _get_df_model()
    atten_lim_db = params.get("atten_lim_db", 100.0)

    # DeepFilterNet requires 48kHz
    target_sr = df_state.sr()

    def _process_mono(mono: np.ndarray) -> np.ndarray:
        if sr != target_sr:
            mono = soxr.resample(mono, sr, target_sr, quality="HQ")

        x = torch.from_numpy(mono).float().unsqueeze(0).unsqueeze(0)
        with torch.no_grad():
            y = enhance(model, df_state, x, atten_lim_db=atten_lim_db)
        result = y.squeeze().numpy()

        if sr != target_sr:
            result = soxr.resample(result, target_sr, sr, quality="HQ")

        return result.astype(np.float32)

    is_stereo = audio.ndim == 2 and audio.shape[0] == 2

    if is_stereo:
        left = _process_mono(audio[0])
        right = _process_mono(audio[1])
        min_len = min(len(left), len(right))
        return np.stack([left[:min_len], right[:min_len]])
    elif audio.ndim == 2:
        return _process_mono(audio[0]).reshape(1, -1)
    else:
        return _process_mono(audio)


# ---------------------------------------------------------------------------
# Stereo Width — Mid/Side encoding
# ---------------------------------------------------------------------------

def _apply_stereo_width(audio: np.ndarray, sr: int, params: dict) -> np.ndarray:
    """Adjust stereo width via mid/side processing."""
    width = params.get("width", 100.0) / 100.0  # 0=mono, 1=unchanged, 2=wide

    if audio.ndim == 1 or (audio.ndim == 2 and audio.shape[0] == 1):
        log.warning("Stereo width skipped: input is mono")
        return audio

    if abs(width - 1.0) < 0.01:
        return audio  # No change needed

    left = audio[0]
    right = audio[1]

    # Encode to mid/side
    mid = (left + right) * 0.5
    side = (left - right) * 0.5

    # Scale sides by width factor
    side = side * width

    # Decode back to left/right
    new_left = mid + side
    new_right = mid - side

    result = np.stack([new_left, new_right]).astype(np.float32)

    # Handle any extra channels (pass through unchanged)
    if audio.shape[0] > 2:
        result = np.vstack([result, audio[2:]])

    return result


# ---------------------------------------------------------------------------
# Effect dispatcher
# ---------------------------------------------------------------------------

_EFFECT_DISPATCH = {
    ("eq", "dsp"): _apply_eq,
    ("compressor", "dsp"): _apply_compressor_dsp,
    ("compressor", "la2a"): _apply_compressor_la2a,
    ("gate", "dsp"): _apply_gate_dsp,
    ("gate", "spectral"): _apply_gate_spectral,
    ("gate", "deepfilter"): _apply_gate_deepfilter,
    ("stereo_width", "dsp"): _apply_stereo_width,
}

# Effects that use GPU and accept an optional device_str kwarg
_GPU_EFFECTS = {("compressor", "la2a"), ("gate", "spectral")}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class EffectsPipeline:
    """Effects chain pipeline — per-stem channel strip processing."""

    def __init__(self) -> None:
        self._config: EffectsConfig | None = None
        self._device_str: str | None = None  # e.g. "cuda:1"

    def configure(self, config: EffectsConfig) -> None:
        self._config = config

    def load_model(self, device: "torch.device | None" = None) -> None:
        """Store device hint for GPU-backed effects (LA-2A, spectral gate)."""
        self._device_str = str(device) if device is not None else None

    def run(
        self,
        audio_path: str | pathlib.Path,
        progress_cb: Callable[[float, str], None] | None = None,
    ) -> EffectsResult:
        """Apply the configured effects chain to an audio file."""
        cfg = self._config or EffectsConfig()
        audio_path = pathlib.Path(audio_path)

        if progress_cb:
            progress_cb(0.02, "Reading audio...")

        # Read audio as (channels, samples) float32
        audio, sr = sf.read(str(audio_path), dtype="float32", always_2d=True)
        audio = audio.T  # (samples, channels) -> (channels, samples)

        # Filter to active (non-bypassed) slots
        active_slots = [s for s in cfg.chain if not s.bypass]
        total = max(len(active_slots), 1)
        summary_parts = []

        for i, slot in enumerate(active_slots):
            effect_label = _EFFECT_LABELS.get(slot.effect_type, slot.effect_type)
            method_label = _METHOD_LABELS.get(slot.method, slot.method)
            display = f"{method_label} {effect_label}".strip() if method_label else effect_label

            if progress_cb:
                progress_cb(0.05 + 0.85 * (i / total), f"Applying {display}...")

            key = (slot.effect_type, slot.method)
            fn = _EFFECT_DISPATCH.get(key)
            if fn is None:
                log.warning("Unknown effect: %s/%s — skipping", slot.effect_type, slot.method)
                continue

            # GPU-backed effects accept an optional device_str kwarg
            if key in _GPU_EFFECTS and self._device_str:
                audio = fn(audio, sr, slot.params, device_str=self._device_str)
            else:
                audio = fn(audio, sr, slot.params)
            summary_parts.append(display)

        if progress_cb:
            progress_cb(0.92, "Writing output...")

        # Clip to prevent clipping distortion
        audio = np.clip(audio, -1.0, 1.0)

        # Write output — transpose back to (samples, channels)
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        chain_tag = "_".join(s.effect_type for s in active_slots) if active_slots else "bypass"
        out_name = f"{audio_path.stem}_fx_{chain_tag}.wav"
        output_path = cfg.output_dir / out_name
        sf.write(str(output_path), audio.T, sr, subtype="FLOAT")

        chain_summary = " + ".join(summary_parts) if summary_parts else "Bypass (no active effects)"
        log.info("Effects chain complete: %s → %s (%s)", audio_path.name, output_path.name, chain_summary)

        if progress_cb:
            progress_cb(1.0, "Done")

        return EffectsResult(output_path=output_path, chain_summary=chain_summary)

    def clear(self) -> None:
        """Release cached ML models and GPU memory."""
        global _la2a_model, _df_model, _df_state
        if _la2a_model is not None:
            _la2a_model = None
            log.info("Released LA-2A model")
        if _df_model is not None:
            _df_model = None
            _df_state = None
            log.info("Released DeepFilterNet model")

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
