"""SFX Stem Builder renderer.

Takes an SFX manifest (canvas + placements) and renders a composite WAV
by reading each clip, applying per-clip volume/fade, and summing into a
canvas of silence.
"""

from __future__ import annotations

import logging
import pathlib

import numpy as np

from utils.audio_io import read_audio, write_audio, convert_channels
from utils.paths import SFX_DIR

log = logging.getLogger(__name__)

CANVAS_SAMPLE_RATE = 44100
CANVAS_CHANNELS = 2


def make_fade(length_samples: int, curve: str = "linear") -> np.ndarray:
    """Return a fade-in ramp of *length_samples* values from 0 → 1.

    For fade-out, reverse the result: ``make_fade(n)[::-1]``.

    Parameters
    ----------
    length_samples:
        Number of samples in the ramp.  If ≤ 0, returns empty array.
    curve:
        ``"linear"`` or ``"cosine"``.
    """
    if length_samples <= 0:
        return np.array([], dtype=np.float32)
    t = np.linspace(0.0, 1.0, length_samples, dtype=np.float32)
    if curve == "cosine":
        return (1.0 - np.cos(t * np.pi)) / 2.0
    return t  # linear


def render_sfx(manifest: dict, output_base: pathlib.Path | None = None) -> pathlib.Path:
    """Render an SFX manifest to a WAV file and return the output path.

    The manifest dict must contain:
    - ``id`` (str)
    - ``sample_rate`` (int)
    - ``channels`` (int)
    - ``total_samples`` (int)
    - ``apply_limiter`` (bool)
    - ``placements`` (list of dicts with keys: clip_path, start_ms,
      volume, fade_in_ms, fade_out_ms, fade_curve)
    """
    sr = manifest["sample_rate"]
    channels = manifest["channels"]
    total_samples = manifest["total_samples"]
    canvas = np.zeros((channels, total_samples), dtype=np.float32)

    for p in manifest.get("placements", []):
        clip_path = pathlib.Path(p["clip_path"])
        if not clip_path.exists():
            log.warning("SFX render: clip not found, skipping: %s", clip_path)
            continue

        clip, clip_sr = read_audio(clip_path, target_rate=sr)
        clip = convert_channels(clip, channels)

        offset = int(p["start_ms"] * sr / 1000)
        volume = float(p.get("volume", 1.0))
        fade_in_samples = int(p.get("fade_in_ms", 0) * sr / 1000)
        fade_out_samples = int(p.get("fade_out_ms", 0) * sr / 1000)
        fade_curve = p.get("fade_curve", "linear")

        clip_len = clip.shape[1]

        # Apply fade-in
        if fade_in_samples > 0:
            fi = min(fade_in_samples, clip_len)
            ramp = make_fade(fi, fade_curve)
            clip[:, :fi] *= ramp

        # Apply fade-out
        if fade_out_samples > 0:
            fo = min(fade_out_samples, clip_len)
            ramp = make_fade(fo, fade_curve)[::-1]
            clip[:, -fo:] *= ramp

        # Apply volume
        clip *= volume

        # Sum into canvas at offset
        end = min(offset + clip_len, total_samples)
        usable = end - offset
        if usable > 0 and offset >= 0:
            canvas[:, offset:end] += clip[:, :usable]

    # Optional soft limiter
    if manifest.get("apply_limiter", False):
        canvas = np.tanh(canvas).astype(np.float32)

    # Write output
    base = output_base or SFX_DIR
    out_dir = base / manifest["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "rendered.wav"
    bit_depth = manifest.get("bit_depth", 24)
    write_audio(canvas, sr, out_path, bit_depth=bit_depth)

    log.info("SFX rendered: %s  samples=%d  placements=%d",
             manifest["id"], total_samples, len(manifest.get("placements", [])))
    return out_path


def generate_waveform_peaks(path: pathlib.Path, points: int = 2000) -> list[float]:
    """Return downsampled absolute-peak data for waveform visualisation."""
    waveform, sr = read_audio(path, mono=True)
    samples = waveform[0]
    n = len(samples)
    if n <= points:
        return [float(abs(s)) for s in samples]
    chunk_size = n // points
    peaks = []
    for i in range(points):
        chunk = samples[i * chunk_size : (i + 1) * chunk_size]
        peaks.append(float(np.max(np.abs(chunk))))
    return peaks
