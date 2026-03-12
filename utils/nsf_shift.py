"""Neural vocoder pitch shifting via NSF-HiFiGAN.

Uses an F0-conditioned neural source-filter vocoder to resynthesize audio
with modified pitch. Produces higher quality than WORLD or STFT phase vocoder,
especially on compressed or noisy audio, at the cost of requiring a GPU.

Model: NSF-HiFiGAN from openvpi/vocoders (CC BY-NC-SA 4.0 pretrained weights).
Code: vendored from DDSP-SVC (MIT license).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

log = logging.getLogger("stemforge.utils.nsf_shift")

# Model download URL and cache location
_MODEL_URL = "https://github.com/openvpi/vocoders/releases/download/nsf-hifigan-44.1k-hop512-128bin-2024.02/nsf_hifigan_44.1k_hop512_128bin_2024.02.zip"
_MODEL_DIR_NAME = "nsf_hifigan"

# Singleton — loaded once, reused across calls
_generator = None
_stft = None
_config = None
_device = None


def _get_cache_dir() -> Path:
    """Return the cache directory for the NSF-HiFiGAN model."""
    from utils.cache import get_model_cache_dir
    return get_model_cache_dir(_MODEL_DIR_NAME)


def _ensure_model() -> Path:
    """Download the model if not already cached. Return path to checkpoint."""
    cache_dir = _get_cache_dir()
    # The zip extracts to a directory; look for model_path inside
    ckpt = cache_dir / "model"
    config = cache_dir / "config.json"

    if ckpt.exists() and config.exists():
        return ckpt

    log.info("Downloading NSF-HiFiGAN model (~55 MB)...")
    cache_dir.mkdir(parents=True, exist_ok=True)

    import io
    import zipfile
    import urllib.request

    # Download zip
    with urllib.request.urlopen(_MODEL_URL) as resp:
        zip_data = resp.read()

    # Extract
    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        zf.extractall(cache_dir)

    # The zip may contain files in a subdirectory — find the checkpoint
    # Look for any .pt or .ckpt file and the config.json
    for root, _dirs, files in os.walk(cache_dir):
        root_path = Path(root)
        for f in files:
            if f == "config.json" and not config.exists():
                if root_path / f != config:
                    (root_path / f).rename(config)
            if f.endswith((".pt", ".ckpt")) and not ckpt.exists():
                if root_path / f != ckpt:
                    (root_path / f).rename(ckpt)

    if not ckpt.exists() or not config.exists():
        raise FileNotFoundError(
            f"NSF-HiFiGAN model files not found after extraction in {cache_dir}"
        )

    log.info("NSF-HiFiGAN model ready at %s", cache_dir)
    return ckpt


def _load_model(device: str):
    """Load the NSF-HiFiGAN generator and STFT processor (singleton)."""
    global _generator, _stft, _config, _device

    if _generator is not None and _device == device:
        return

    import sys
    # Ensure vendor/ is importable
    vendor_dir = str(Path(__file__).resolve().parent.parent / "vendor")
    if vendor_dir not in sys.path:
        sys.path.insert(0, vendor_dir)

    from nsf_hifigan import load_model, STFT

    ckpt_path = _ensure_model()
    log.info("Loading NSF-HiFiGAN on %s...", device)
    generator, h = load_model(str(ckpt_path), device=device)
    _generator = generator
    _config = h
    _device = device

    _stft = STFT(
        sr=h.sampling_rate,
        n_mels=h.num_mels,
        n_fft=h.n_fft,
        win_size=h.win_size,
        hop_length=h.hop_size,
        fmin=h.fmin,
        fmax=h.fmax,
    )
    log.info("NSF-HiFiGAN loaded (sr=%d, hop=%d, mels=%d)",
             h.sampling_rate, h.hop_size, h.num_mels)


def nsf_pitch_shift(
    audio: np.ndarray,
    sr: int,
    source_f0: np.ndarray,
    target_f0: np.ndarray,
    hop_size: int = 128,
) -> np.ndarray:
    """Pitch-shift *audio* using NSF-HiFiGAN neural vocoder.

    Extracts mel spectrogram from the original audio, builds a corrected
    F0 contour, and resynthesizes via the neural vocoder. Requires GPU.

    Parameters
    ----------
    audio : ndarray, shape (n_samples,)
        Mono float audio.
    sr : int
        Sample rate of the input audio.
    source_f0 : ndarray, shape (n_frames,)
        Original F0 per CREPE frame (Hz). 0 = unvoiced.
    target_f0 : ndarray, shape (n_frames,)
        Corrected F0 per CREPE frame. 0 = unvoiced.
    hop_size : int
        CREPE hop size in samples (default 128).

    Returns
    -------
    ndarray, shape (n_samples,)
        Pitch-shifted mono audio, same length as input.
    """
    import torch
    import soxr

    from utils.device import get_device

    device = str(get_device())
    if device == "cpu":
        raise RuntimeError(
            "Neural vocoder requires GPU. Select WORLD or STFT method instead."
        )

    _load_model(device)
    assert _generator is not None and _stft is not None and _config is not None

    n_orig = len(audio)
    model_sr = _config.sampling_rate  # 44100
    model_hop = _config.hop_size    # 512

    # --- Resample to model SR if needed ---
    if sr != model_sr:
        audio_resampled = soxr.resample(audio, sr, model_sr, quality="HQ")
    else:
        audio_resampled = audio

    n_model = len(audio_resampled)
    audio32 = audio_resampled.astype(np.float32)

    # --- Compute mel spectrogram ---
    audio_tensor = torch.FloatTensor(audio32).unsqueeze(0).to(device)
    with torch.no_grad():
        mel = _stft.get_mel(audio_tensor)  # (1, n_mels, frames)

    n_mel_frames = mel.shape[2]

    # --- Build F0 at model's frame rate ---
    # CREPE F0 is at hop_size intervals at the original SR
    # Model expects F0 at model_hop intervals at model_sr
    crepe_times = (np.arange(len(target_f0)) + 0.5) * hop_size / sr
    model_times = (np.arange(n_mel_frames) + 0.5) * model_hop / model_sr

    # Interpolate target F0 onto model frame grid
    target_f0_model = np.interp(model_times, crepe_times, target_f0)

    # Preserve voicing decisions (don't interpolate across unvoiced gaps)
    voiced_crepe = source_f0 > 0
    voiced_interp = np.interp(model_times, crepe_times, voiced_crepe.astype(float))
    target_f0_model[voiced_interp < 0.5] = 0.0

    f0_tensor = torch.FloatTensor(target_f0_model).unsqueeze(0).to(device)

    # Align lengths
    min_frames = min(mel.shape[2], f0_tensor.shape[1])
    mel = mel[:, :, :min_frames]
    f0_tensor = f0_tensor[:, :min_frames]

    # --- Neural vocoder synthesis ---
    with torch.no_grad():
        result_tensor = _generator(mel, f0_tensor)  # (1, 1, samples)

    result = result_tensor.squeeze().cpu().numpy()

    # --- Resample back to original SR if needed ---
    if sr != model_sr:
        result = soxr.resample(result, model_sr, sr, quality="HQ")

    # Match original length
    if len(result) > n_orig:
        result = result[:n_orig]
    elif len(result) < n_orig:
        result = np.pad(result, (0, n_orig - len(result)))

    return result.astype(audio.dtype)


def unload_model():
    """Release GPU memory held by the NSF-HiFiGAN model."""
    global _generator, _stft, _config, _device
    if _generator is not None:
        del _generator
        _generator = None
        _stft = None
        _config = None
        _device = None
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        log.info("NSF-HiFiGAN model unloaded")
