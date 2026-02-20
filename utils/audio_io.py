"""
Audio I/O utilities for StemForge.

Provides functions for reading audio files into NumPy arrays and writing
NumPy arrays back to audio files.  All waveforms use the
``(channels, samples)`` axis convention (C-major, torch-compatible) and
``float32`` dtype.

Backend
-------
Reading and lossless writing delegate to :mod:`soundfile` (libsndfile).
MP3 writing delegates to :mod:`pydub` (FFmpeg/libmp3lame).

Waveform convention
-------------------
Every function that returns or accepts a waveform uses:

* **shape** — ``(channels, samples)``; mono audio has shape ``(1, samples)``
* **dtype** — ``numpy.float32``
* **range** — ``[-1.0, 1.0]`` (floating-point full-scale)

Callers that need a torch tensor can convert with
``torch.from_numpy(waveform)`` without copying.
"""

import pathlib
import logging
from typing import Any

import numpy as np
import soundfile as sf
import soxr

from utils.errors import AudioProcessingError, InvalidInputError


log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# Canonical set of extensions supported for *reading*.
# Import this wherever file-extension validation is needed instead of
# duplicating the set.
SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {".wav", ".flac", ".mp3", ".ogg", ".aiff", ".aif"}
)

# Formats supported for *writing* without an extra backend.
# MP3 is read-only via soundfile; write via pydub (see write_audio).
_LOSSLESS_WRITE_FORMATS: frozenset[str] = frozenset({"wav", "flac", "ogg"})

# Canonical type alias for a waveform array.
# Concrete type: numpy.ndarray[float32], shape (channels, samples).
Waveform = Any


# ---------------------------------------------------------------------------
# Probe dataclass
# ---------------------------------------------------------------------------

class AudioInfo:
    """Metadata returned by :func:`probe` without decoding sample data.

    Parameters
    ----------
    path:
        Absolute path of the probed file.
    sample_rate:
        Sample rate in Hz.
    channels:
        Number of audio channels.
    num_frames:
        Total number of sample frames (length in samples per channel).
    duration:
        Duration in seconds (``num_frames / sample_rate``).
    bit_depth:
        Bit depth for PCM formats (e.g. 16, 24, 32), or ``None`` for
        lossy formats (MP3, OGG Vorbis) where it is not meaningful.
    format:
        Upper-case format string reported by libsndfile (e.g.
        ``'WAV'``, ``'FLAC'``).  Empty string for formats libsndfile
        does not recognise directly (e.g. MP3 on some platforms).
    """

    path: pathlib.Path
    sample_rate: int
    channels: int
    num_frames: int
    duration: float
    bit_depth: int | None
    format: str

    def __init__(
        self,
        path: pathlib.Path,
        sample_rate: int,
        channels: int,
        num_frames: int,
        duration: float,
        bit_depth: int | None,
        format: str,
    ) -> None:
        self.path = path
        self.sample_rate = sample_rate
        self.channels = channels
        self.num_frames = num_frames
        self.duration = duration
        self.bit_depth = bit_depth
        self.format = format

    def __repr__(self) -> str:
        return (
            f"AudioInfo(path={self.path.name!r}, sr={self.sample_rate}, "
            f"ch={self.channels}, dur={self.duration:.2f}s, fmt={self.format!r})"
        )


# ---------------------------------------------------------------------------
# Core I/O
# ---------------------------------------------------------------------------

def probe(path: pathlib.Path) -> AudioInfo:
    """Return metadata for *path* without decoding any sample data.

    Opens the file once and reads only the header/container metadata.
    Prefer this over calling :func:`get_sample_rate` and
    :func:`get_duration` separately.

    Parameters
    ----------
    path:
        Path to the audio file.

    Returns
    -------
    AudioInfo
        Populated metadata object.

    Raises
    ------
    :class:`~utils.errors.InvalidInputError`
        If *path* does not exist or has an unsupported extension.
    :class:`~utils.errors.AudioProcessingError`
        If the file header cannot be read.
    """
    _validate_path(path)
    try:
        info = sf.info(str(path))
    except Exception as exc:
        raise AudioProcessingError(
            f"Cannot read audio metadata from {path}: {exc}",
            path=str(path),
        ) from exc

    # Map soundfile subtype to bit depth where applicable
    subtype = info.subtype or ""
    bit_depth: int | None = None
    if "16" in subtype:
        bit_depth = 16
    elif "24" in subtype:
        bit_depth = 24
    elif "32" in subtype:
        bit_depth = 32

    return AudioInfo(
        path=path.resolve(),
        sample_rate=info.samplerate,
        channels=info.channels,
        num_frames=info.frames,
        duration=info.duration,
        bit_depth=bit_depth,
        format=info.format or "",
    )


def read_audio(
    path: pathlib.Path,
    mono: bool = False,
    target_rate: int | None = None,
) -> tuple[Waveform, int]:
    """Read an audio file and return ``(waveform, sample_rate)``.

    The returned waveform is always ``float32`` with shape
    ``(channels, samples)``.  If *mono* is ``True`` the result has shape
    ``(1, samples)``.

    Parameters
    ----------
    path:
        Path to the audio file.  Supported extensions:
        ``.wav``, ``.flac``, ``.mp3``, ``.ogg``, ``.aiff``, ``.aif``.
    mono:
        When ``True``, downmix all channels to a single channel before
        returning.  Equivalent to calling :func:`mix_down_to_mono` on
        the result, but more efficient because no intermediate array is
        created.
    target_rate:
        When provided, resample the waveform to this sample rate using
        a high-quality sinc filter (:mod:`soxr`).  The returned integer
        sample rate reflects the resampled rate, not the file's native
        rate.

    Returns
    -------
    tuple[Waveform, int]
        ``(waveform, sample_rate)`` where *waveform* has shape
        ``(channels, samples)`` (or ``(1, samples)`` if *mono* is
        ``True``) and *sample_rate* is in Hz.

    Raises
    ------
    :class:`~utils.errors.InvalidInputError`
        If *path* does not exist or has an unsupported extension, or if
        *target_rate* is not a positive integer.
    :class:`~utils.errors.AudioProcessingError`
        If the file cannot be decoded.
    """
    _validate_path(path)
    if target_rate is not None and target_rate <= 0:
        raise InvalidInputError(
            f"target_rate must be a positive integer, got {target_rate}.",
            field="target_rate",
        )

    try:
        # soundfile returns (samples, channels) float64 by default
        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    except Exception as exc:
        raise AudioProcessingError(
            f"Cannot decode audio file {path}: {exc}",
            path=str(path),
        ) from exc

    # Transpose to (channels, samples)
    waveform: Waveform = data.T

    if mono:
        waveform = mix_down_to_mono(waveform)

    if target_rate is not None and target_rate != sr:
        waveform = _resample_numpy(waveform, sr, target_rate)
        sr = target_rate

    log.debug(
        "read_audio: %s  sr=%d  shape=%s",
        path.name, sr, waveform.shape,
    )
    return waveform, sr


def write_audio(
    waveform: Waveform,
    sample_rate: int,
    path: pathlib.Path,
    fmt: str | None = None,
    bit_depth: int = 16,
    mp3_bitrate: int = 192,
) -> pathlib.Path:
    """Write *waveform* to *path*.

    Parameters
    ----------
    waveform:
        Float32 array of shape ``(channels, samples)`` or ``(samples,)``
        for mono.  Values should be in ``[-1.0, 1.0]``; out-of-range
        values are clipped before writing.
    sample_rate:
        Sample rate of *waveform* in Hz.
    path:
        Destination file path.  The parent directory is created if it
        does not exist.
    fmt:
        Output format: ``'wav'``, ``'flac'``, ``'ogg'``, or ``'mp3'``.
        When ``None`` (default), the format is inferred from *path*'s
        extension.  If *path* has no extension and *fmt* is ``None``,
        ``'wav'`` is used as a fallback.
    bit_depth:
        Bit depth for PCM formats (``16`` or ``24``).  Ignored for OGG
        and MP3 (which are always lossy).  Default: ``16``.
    mp3_bitrate:
        Bitrate in kbps for MP3 output.  Ignored for all other formats.
        Default: ``192``.

    Returns
    -------
    pathlib.Path
        Resolved absolute path of the written file.

    Raises
    ------
    :class:`~utils.errors.InvalidInputError`
        If *fmt* specifies an unsupported format, or if *bit_depth* is
        not 16 or 24.
    :class:`~utils.errors.AudioProcessingError`
        If writing the file fails.
    """
    if bit_depth not in (16, 24, 32):
        raise InvalidInputError(
            f"bit_depth must be 16, 24, or 32; got {bit_depth}.",
            field="bit_depth",
        )

    # Resolve format
    resolved_fmt = _resolve_write_format(path, fmt)

    # Normalise waveform shape to (samples, channels) for soundfile / pydub
    arr = np.asarray(waveform, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[:, np.newaxis]      # (samples, 1)
    else:
        arr = arr.T                   # (channels, samples) → (samples, channels)

    # Clip to valid float range
    arr = np.clip(arr, -1.0, 1.0)

    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if resolved_fmt == "mp3":
            _write_mp3(arr, sample_rate, path, mp3_bitrate)
        else:
            subtype = _bit_depth_to_subtype(resolved_fmt, bit_depth)
            sf.write(str(path), arr, sample_rate, subtype=subtype)
    except AudioProcessingError:
        raise
    except Exception as exc:
        raise AudioProcessingError(
            f"Failed to write audio to {path}: {exc}",
            path=str(path),
        ) from exc

    log.debug(
        "write_audio: %s  sr=%d  fmt=%s  shape=%s",
        path.name, sample_rate, resolved_fmt, arr.shape,
    )
    return path.resolve()


# ---------------------------------------------------------------------------
# Probe helpers
# ---------------------------------------------------------------------------

def get_duration(path: pathlib.Path) -> float:
    """Return the duration of *path* in seconds.

    Thin wrapper over :func:`probe`.  When you also need the sample rate
    or channel count, prefer calling :func:`probe` once.

    Raises
    ------
    :class:`~utils.errors.InvalidInputError`
        If *path* does not exist or has an unsupported extension.
    :class:`~utils.errors.AudioProcessingError`
        If the file header cannot be read.
    """
    return probe(path).duration


def get_sample_rate(path: pathlib.Path) -> int:
    """Return the sample rate of *path* in Hz.

    Thin wrapper over :func:`probe`.  When you also need duration or
    channel count, prefer calling :func:`probe` once.

    Raises
    ------
    :class:`~utils.errors.InvalidInputError`
        If *path* does not exist or has an unsupported extension.
    :class:`~utils.errors.AudioProcessingError`
        If the file header cannot be read.
    """
    return probe(path).sample_rate


# ---------------------------------------------------------------------------
# Signal primitives
# ---------------------------------------------------------------------------

def mix_down_to_mono(waveform: Waveform) -> Waveform:
    """Average all channels into a single channel.

    Parameters
    ----------
    waveform:
        Float32 array of shape ``(channels, samples)`` or ``(samples,)``.

    Returns
    -------
    Waveform
        Float32 array of shape ``(1, samples)``.
    """
    arr = np.asarray(waveform, dtype=np.float32)
    if arr.ndim == 1:
        return arr[np.newaxis, :]
    # (channels, samples) → mean over channels → (1, samples)
    return arr.mean(axis=0, keepdims=True)


def normalise_peak(waveform: Waveform, peak: float = 1.0) -> Waveform:
    """Scale *waveform* so its absolute maximum equals *peak*.

    If the waveform is silent (all zeros), it is returned unchanged.

    Parameters
    ----------
    waveform:
        Float32 array of shape ``(channels, samples)`` or ``(samples,)``.
    peak:
        Target absolute peak level.  Default: ``1.0`` (0 dBFS).

    Returns
    -------
    Waveform
        Normalised float32 array with the same shape as *waveform*.
    """
    arr = np.asarray(waveform, dtype=np.float32)
    max_val = np.abs(arr).max()
    if max_val == 0.0:
        return arr
    return arr * (peak / max_val)


def convert_channels(waveform: Waveform, target_channels: int) -> Waveform:
    """Convert *waveform* to *target_channels* channels.

    Rules:

    * **Same channel count** — returned as-is.
    * **Any → mono (1)** — channels are averaged via :func:`mix_down_to_mono`.
    * **Mono (1) → stereo (2)** — the single channel is duplicated.
    * **Mono (1) → N > 2** — the single channel is broadcast to all N channels.
    * **Stereo (2) → N ≠ 1** — raises :class:`~utils.errors.InvalidInputError`;
      downmix to mono first, then convert.

    Parameters
    ----------
    waveform:
        Float32 array of shape ``(channels, samples)`` or ``(samples,)``.
    target_channels:
        Desired number of output channels (must be ≥ 1).

    Returns
    -------
    Waveform
        Float32 array of shape ``(target_channels, samples)``.

    Raises
    ------
    :class:`~utils.errors.InvalidInputError`
        If *target_channels* is less than 1, or if an unsupported
        channel conversion is requested.
    """
    if target_channels < 1:
        raise InvalidInputError(
            f"target_channels must be ≥ 1, got {target_channels}.",
            field="target_channels",
        )

    arr = np.asarray(waveform, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr[np.newaxis, :]  # treat as (1, samples)

    in_channels = arr.shape[0]

    if in_channels == target_channels:
        return arr

    if target_channels == 1:
        return mix_down_to_mono(arr)

    if in_channels == 1:
        # Broadcast single channel to all targets
        return np.repeat(arr, target_channels, axis=0)

    raise InvalidInputError(
        f"Cannot convert {in_channels}-channel audio to {target_channels} channels "
        f"without ambiguity.  Downmix to mono first, then convert.",
        field="target_channels",
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _validate_path(path: pathlib.Path) -> None:
    """Raise InvalidInputError if *path* is missing or unsupported."""
    if not path.exists():
        raise InvalidInputError(
            f"Audio file not found: {path}",
            field="path",
        )
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise InvalidInputError(
            f"Unsupported audio extension '{path.suffix}'. "
            f"Supported: {sorted(SUPPORTED_EXTENSIONS)}",
            field="path",
        )


def _resolve_write_format(path: pathlib.Path, fmt: str | None) -> str:
    """Return the lowercase format string to use when writing *path*."""
    if fmt is not None:
        resolved = fmt.lower()
    else:
        resolved = path.suffix.lstrip(".").lower() or "wav"

    supported = _LOSSLESS_WRITE_FORMATS | {"mp3"}
    if resolved not in supported:
        raise InvalidInputError(
            f"Unsupported write format '{resolved}'. "
            f"Supported: {sorted(supported)}",
            field="fmt",
        )
    return resolved


def _bit_depth_to_subtype(fmt: str, bit_depth: int) -> str:
    """Map a format + bit_depth to the soundfile subtype string."""
    if fmt == "ogg":
        return "VORBIS"
    return f"PCM_{bit_depth}"


def _resample_numpy(
    waveform: Waveform,
    original_rate: int,
    target_rate: int,
) -> Waveform:
    """Resample *waveform* using soxr (high-quality sinc)."""
    arr = np.asarray(waveform, dtype=np.float32)
    # soxr expects (samples, channels)
    resampled = soxr.resample(arr.T, original_rate, target_rate, quality="HQ")
    return resampled.T  # back to (channels, samples)


def _write_mp3(
    data: Any,
    sample_rate: int,
    path: pathlib.Path,
    bitrate: int,
) -> None:
    """Write *data* (samples × channels, float32) as MP3 via pydub."""
    from pydub import AudioSegment

    # pydub needs int16 PCM bytes
    pcm = (data * 32767).astype(np.int16)
    channels = pcm.shape[1] if pcm.ndim > 1 else 1
    seg = AudioSegment(
        pcm.tobytes(),
        frame_rate=sample_rate,
        sample_width=2,
        channels=channels,
    )
    seg.export(str(path), format="mp3", bitrate=f"{bitrate}k")
