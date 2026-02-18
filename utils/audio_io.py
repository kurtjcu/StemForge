"""
Audio I/O utilities for StemForge.

Provides functions for reading audio files into numeric arrays and writing
numeric arrays back to audio files in various formats.  All functions work
with raw Python data structures and the standard library; format-specific
decoding is delegated to whichever runtime codec is available.
"""

import os
import pathlib
import logging
import struct
import wave
from typing import Any


# A waveform is a numeric array-like of shape (channels, samples) or (samples,).
# The concrete type depends on the runtime tensor library (numpy, torch, etc.),
# so Any is used here and throughout.
Waveform = Any


def read_audio(
    path: pathlib.Path,
    mono: bool = False,
    target_rate: int | None = None,
) -> tuple[Waveform, int]:
    """Read an audio file and return ``(waveform, sample_rate)``.

    Parameters
    ----------
    path:
        Path to the audio file (WAV, FLAC, MP3, OGG, AIFF supported).
    mono:
        When *True*, downmix multi-channel audio to a single channel.
    target_rate:
        If provided, resample the waveform to this rate after loading.

    Returns
    -------
    tuple[Waveform, int]
        A ``(waveform, sample_rate)`` pair where *waveform* is a numeric
        array of shape ``(channels, samples)`` and *sample_rate* is in Hz.
    """
    pass


def write_audio(
    waveform: Waveform,
    sample_rate: int,
    path: pathlib.Path,
    fmt: str = "wav",
    bit_depth: int = 16,
) -> pathlib.Path:
    """Write *waveform* to *path* in the specified format.

    Parameters
    ----------
    waveform:
        Numeric array of shape ``(channels, samples)`` or ``(samples,)``.
    sample_rate:
        Sample rate of *waveform* in Hz.
    path:
        Destination file path (extension may be overridden by *fmt*).
    fmt:
        Output format string: ``'wav'``, ``'flac'``, ``'mp3'``, or ``'ogg'``.
    bit_depth:
        Bit depth for lossless formats (16 or 24).

    Returns
    -------
    pathlib.Path
        Resolved path of the written file.
    """
    pass


def get_duration(path: pathlib.Path) -> float:
    """Return the duration of an audio file in seconds.

    Parameters
    ----------
    path:
        Path to the audio file.
    """
    pass


def get_sample_rate(path: pathlib.Path) -> int:
    """Return the sample rate of an audio file in Hz.

    Parameters
    ----------
    path:
        Path to the audio file.
    """
    pass


def mix_down_to_mono(waveform: Waveform) -> Waveform:
    """Average all channels of *waveform* into a single-channel array."""
    pass


def normalise_peak(waveform: Waveform, peak: float = 1.0) -> Waveform:
    """Scale *waveform* so that its absolute peak equals *peak*."""
    pass
