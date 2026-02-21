#!/usr/bin/env python
# encoding: utf-8
#
# Copyright 2022 Spotify AB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Feature extraction pipeline for BasicPitch TFLite inference.

This module is a minimal rewrite of the upstream basic_pitch inference module.
All TensorFlow, ONNX, CoreML, and SavedModel dependencies have been removed.
The only functionality retained is audio pre-processing (windowing and
resampling) needed to prepare input for the vendored TFLite model.

The CQT spectrogram computation happens *inside* the TFLite model binary, so
no feature-extraction layers need to run in Python.

Public API
----------
compute_features(audio, sample_rate)
    Main entry point for ``BasicPitchModelLoader.predict()``.
    Returns all audio windows as a single batch ready for ``set_tensor``.

window_audio_file(audio_original, hop_size)
    Yields fixed-length, zero-padded audio windows.

get_audio_input(audio_path, overlap_len, hop_size)
    Loads an audio file from disk, pads for overlap, and yields windows.

unwrap_output(output, audio_original_length, n_overlapping_frames, hop_size)
    Stitches per-window model outputs into a single time × frequency matrix.
"""

import pathlib
import logging
from typing import Dict, Iterable, Optional, Tuple, Union

import librosa
import numpy as np
import numpy.typing as npt

from models.basicpitch.constants import (
    AUDIO_SAMPLE_RATE,
    AUDIO_N_SAMPLES,
    ANNOTATIONS_FPS,
    FFT_HOP,
    AUDIO_WINDOW_LENGTH,
)


log = logging.getLogger("stemforge.models.basicpitch.inference")

DEFAULT_OVERLAPPING_FRAMES: int = 30


# ---------------------------------------------------------------------------
# Audio windowing
# ---------------------------------------------------------------------------

def window_audio_file(
    audio_original: npt.NDArray[np.float32],
    hop_size: int,
) -> Iterable[Tuple[npt.NDArray[np.float32], Dict[str, float]]]:
    """Yield fixed-length, zero-padded audio windows.

    Parameters
    ----------
    audio_original:
        Mono audio array, shape ``(n_samples,)``.
    hop_size:
        Number of samples to advance between successive windows.

    Yields
    ------
    window : np.ndarray
        Shape ``(AUDIO_N_SAMPLES, 1)``.
    window_time : dict
        ``{'start': float, 'end': float}`` times in seconds.
    """
    for i in range(0, audio_original.shape[0], hop_size):
        window = audio_original[i : i + AUDIO_N_SAMPLES]
        if len(window) < AUDIO_N_SAMPLES:
            window = np.pad(window, [[0, AUDIO_N_SAMPLES - len(window)]])
        t_start = float(i) / AUDIO_SAMPLE_RATE
        window_time = {
            "start": t_start,
            "end": t_start + (AUDIO_N_SAMPLES / AUDIO_SAMPLE_RATE),
        }
        yield np.expand_dims(window, axis=-1), window_time


def get_audio_input(
    audio_path: Union[pathlib.Path, str],
    overlap_len: int,
    hop_size: int,
) -> Iterable[Tuple[npt.NDArray[np.float32], Dict[str, float], int]]:
    """Load a mono audio file and yield windowed chunks.

    Parameters
    ----------
    audio_path:
        Path to an audio file in any format supported by librosa.
    overlap_len:
        Number of overlap samples prepended as silence before windowing.
        Must be even.
    hop_size:
        Number of samples to advance between successive windows.

    Yields
    ------
    window : np.ndarray
        Shape ``(1, AUDIO_N_SAMPLES, 1)``.
    window_time : dict
        ``{'start': float, 'end': float}`` times in seconds.
    audio_original_length : int
        Number of samples in the original audio *before* padding.
    """
    assert overlap_len % 2 == 0, f"overlap_len must be even, got {overlap_len}"

    audio_original, _ = librosa.load(str(audio_path), sr=AUDIO_SAMPLE_RATE, mono=True)

    original_length = audio_original.shape[0]
    audio_original = np.concatenate(
        [np.zeros((overlap_len // 2,), dtype=np.float32), audio_original]
    )
    for window, window_time in window_audio_file(audio_original, hop_size):
        yield np.expand_dims(window, axis=0), window_time, original_length


# ---------------------------------------------------------------------------
# Output un-wrapping
# ---------------------------------------------------------------------------

def unwrap_output(
    output: npt.NDArray[np.float32],
    audio_original_length: int,
    n_overlapping_frames: int,
    hop_size: int,
) -> Optional[npt.NDArray[np.float32]]:
    """Stitch per-window model outputs into a single time × frequency matrix.

    Parameters
    ----------
    output:
        Stacked per-window predictions, shape ``(n_batches, n_frames, n_freqs)``.
    audio_original_length:
        Length of the original audio signal in samples (before padding).
    n_overlapping_frames:
        Number of overlapping frames trimmed from each window boundary.
    hop_size:
        Hop size used when windowing the audio.

    Returns
    -------
    np.ndarray or None
        Unwrapped predictions, shape ``(n_frames_total, n_freqs)``.
        Returns ``None`` if *output* is not rank-3.
    """
    if len(output.shape) != 3:
        return None

    n_olap = int(0.5 * n_overlapping_frames)
    if n_olap > 0:
        output = output[:, n_olap:-n_olap, :]

    output_shape = output.shape
    unwrapped = output.reshape(output_shape[0] * output_shape[1], output_shape[2])

    n_expected_windows = audio_original_length / hop_size
    n_frames_per_window = (AUDIO_WINDOW_LENGTH * ANNOTATIONS_FPS) - n_overlapping_frames
    return unwrapped[: int(n_expected_windows * n_frames_per_window), :]


# ---------------------------------------------------------------------------
# Main entry point for TFLite inference
# ---------------------------------------------------------------------------

def compute_features(
    audio: npt.NDArray[np.float32],
    sample_rate: int = AUDIO_SAMPLE_RATE,
) -> npt.NDArray[np.float32]:
    """Prepare a raw audio array for BasicPitch TFLite inference.

    Performs three pre-processing steps in sequence:

    1. **Mono downmix** — averages across channels if the input is multi-channel.
    2. **Resampling** — resamples to ``AUDIO_SAMPLE_RATE`` (22 050 Hz) using
       ``librosa.resample`` if the provided *sample_rate* differs.
    3. **Windowing** — pads for overlap alignment and slices into
       ``AUDIO_N_SAMPLES``-length chunks with ``DEFAULT_OVERLAPPING_FRAMES``
       overlap on each boundary.

    The CQT spectrogram and all subsequent feature computations happen
    *inside* the TFLite model binary; no TensorFlow code is required here.

    Parameters
    ----------
    audio:
        Raw PCM audio.  Accepted shapes:
        ``(n_samples,)`` for mono, or ``(n_channels, n_samples)`` /
        ``(n_samples, n_channels)`` for multi-channel (the shorter dimension
        is treated as the channel axis).
    sample_rate:
        Sample rate of *audio* in Hz.  Pass ``AUDIO_SAMPLE_RATE`` (22 050)
        to skip resampling.

    Returns
    -------
    np.ndarray
        All audio windows batched together, shape
        ``(n_windows, AUDIO_N_SAMPLES, 1)``, dtype ``float32``.
        Pass this array directly to ``tflite.Interpreter.set_tensor()``.
    """
    # ---- Mono downmix -------------------------------------------------------
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 2:
        # Treat the shorter axis as channels (handles both (C, T) and (T, C))
        if audio.shape[0] < audio.shape[1]:
            audio = audio.mean(axis=0)
        else:
            audio = audio.mean(axis=1)

    # ---- Resample -----------------------------------------------------------
    if sample_rate != AUDIO_SAMPLE_RATE:
        log.debug(
            "compute_features: resampling %d Hz → %d Hz",
            sample_rate, AUDIO_SAMPLE_RATE,
        )
        audio = librosa.resample(
            audio, orig_sr=sample_rate, target_sr=AUDIO_SAMPLE_RATE
        )

    # ---- Windowing (mirrors get_audio_input / run_inference logic) ----------
    overlap_len = DEFAULT_OVERLAPPING_FRAMES * FFT_HOP
    hop_size = AUDIO_N_SAMPLES - overlap_len

    # Prepend silence so the first frame is centred at sample 0
    audio = np.concatenate(
        [np.zeros(overlap_len // 2, dtype=np.float32), audio]
    )

    windows = [
        window  # shape (AUDIO_N_SAMPLES, 1)
        for window, _ in window_audio_file(audio, hop_size)
    ]

    return np.stack(windows, axis=0).astype(np.float32)
    # shape: (n_windows, AUDIO_N_SAMPLES, 1)
