"""
BasicPitch TFLite model loader for StemForge.

This version removes all TensorFlow and SavedModel dependencies and uses
`ai-edge-litert` (Google's NumPy-2.x-compatible TFLite runtime) to load
the vendored BasicPitch `.tflite` model.

The public API is compatible with the pipeline callers in
`pipelines/basicpitch_pipeline.py` and `models/midi_loader.py`:
  - `.load()` initialises the interpreter and returns `self`
  - `.predict(audio_path, ...)` runs windowed TFLite inference and returns
    a list of raw note events ``(start_s, end_s, pitch_midi, amplitude, ...)``.
"""

import pathlib
import logging
from typing import List, Optional, Union

import numpy as np
import ai_edge_litert.interpreter as tflite

from utils.errors import ModelLoadError, PipelineExecutionError
from models.basicpitch.constants import (
    AUDIO_SAMPLE_RATE,
    AUDIO_N_SAMPLES,
    FFT_HOP,
    N_FREQ_BINS_CONTOURS,
    ANNOTATIONS_FPS,
    AUDIO_WINDOW_LENGTH,
)
from models.basicpitch.inference import get_audio_input, unwrap_output, DEFAULT_OVERLAPPING_FRAMES
from models.basicpitch.note_creation import model_output_to_notes

log = logging.getLogger("stemforge.models.basicpitch_loader")

# Number of overlap frames used when windowing audio
_OVERLAP_LEN: int = DEFAULT_OVERLAPPING_FRAMES * FFT_HOP
_HOP_SIZE: int = AUDIO_N_SAMPLES - _OVERLAP_LEN


def _suffix_key(detail: dict) -> int:
    """Return the integer suffix from a tensor name like 'StatefulPartitionedCall:2'."""
    name = detail["name"]
    try:
        return int(name.rsplit(":", 1)[-1])
    except (ValueError, IndexError):
        return 999


class BasicPitchModelLoader:
    """
    Loads the BasicPitch TFLite model and exposes a file-level inference API.

    The TFLite interpreter is CPU-only, lightweight, and fully compatible
    with NumPy 2.x and the rest of the PyTorch-based StemForge stack.
    """

    def __init__(self, preferred_format: str = "tflite") -> None:
        self.preferred_format = preferred_format
        self._interpreter: tflite.Interpreter | None = None
        self._input_details = None
        self._output_details = None
        self._model_path = None

    def _find_tflite_model(self) -> pathlib.Path:
        model_path = pathlib.Path(__file__).parent / "basicpitch" / "model.tflite"
        if not model_path.exists():
            raise ModelLoadError(
                f"BasicPitch TFLite model not found at: {model_path}",
                model_name="basicpitch",
            )
        return model_path

    def load(self) -> "BasicPitchModelLoader":
        """Load the TFLite interpreter. Returns self (idempotent)."""
        if self._interpreter is not None:
            log.debug("Returning cached BasicPitch TFLite interpreter")
            return self

        self._model_path = self._find_tflite_model()
        log.info("Loading BasicPitch TFLite model from %s", self._model_path)

        try:
            self._interpreter = tflite.Interpreter(model_path=str(self._model_path))
            self._interpreter.allocate_tensors()
            self._input_details = self._interpreter.get_input_details()
            self._output_details = self._interpreter.get_output_details()
        except Exception as exc:
            raise ModelLoadError(
                f"Failed to load BasicPitch TFLite model: {exc}",
                model_name="basicpitch",
            ) from exc

        log.info("BasicPitch TFLite model loaded (CPU-only)")
        return self

    def predict(
        self,
        audio_path: Union[pathlib.Path, str],
        *,
        onset_threshold: float = 0.5,
        frame_threshold: float = 0.3,
        minimum_note_length: float = 58.0,
        minimum_frequency: Optional[float] = None,
        maximum_frequency: Optional[float] = None,
        include_pitch_bends: bool = True,
        multiple_pitch_bends: bool = False,
        melodia_trick: bool = True,
        midi_tempo: float = 120.0,
    ) -> list:
        """Transcribe *audio_path* to a list of raw note events.

        Runs windowed TFLite inference and post-processes the model output
        into note events.

        Returns
        -------
        list of (start_s, end_s, pitch_midi, amplitude, pitch_bends) tuples.
        """
        if self._interpreter is None:
            raise ModelLoadError(
                "BasicPitch model not loaded. Call load() first.",
                model_name="basicpitch",
            )

        # Convert ms → frames for minimum note length
        min_note_len = max(1, int(round(minimum_note_length / 1000.0 * ANNOTATIONS_FPS)))

        # Collect per-window outputs:
        # per_window[j] = list of shape-(1, ANNOT_N_FRAMES, bins) arrays, one per window
        n_outputs = len(self._output_details)
        per_window: List[List[np.ndarray]] = [[] for _ in range(n_outputs)]
        audio_original_length: int = 0

        try:
            for window, _window_time, orig_len in get_audio_input(
                audio_path, _OVERLAP_LEN, _HOP_SIZE
            ):
                audio_original_length = orig_len  # same value every iteration

                self._interpreter.set_tensor(
                    self._input_details[0]["index"],
                    window.astype(np.float32),
                )
                self._interpreter.invoke()

                for j, detail in enumerate(self._output_details):
                    per_window[j].append(
                        self._interpreter.get_tensor(detail["index"]).copy()
                    )
        except Exception as exc:
            raise PipelineExecutionError(
                f"BasicPitch TFLite inference failed: {exc}",
                pipeline_name="basicpitch",
            ) from exc

        if not per_window[0]:
            return []

        # Stack windows: (n_windows, ANNOT_N_FRAMES, bins) → unwrap to (n_frames_total, bins)
        stacked = [
            np.concatenate(per_window[j], axis=0)  # (n_windows, frames, bins)
            for j in range(n_outputs)
        ]

        unwrapped: List[Optional[np.ndarray]] = [
            unwrap_output(stacked[j], audio_original_length, DEFAULT_OVERLAPPING_FRAMES, _HOP_SIZE)
            for j in range(n_outputs)
        ]

        # Map outputs to named keys using the StatefulPartitionedCall:N suffix.
        # Suffix :0 → contour (264 bins), :1 → note (88 bins), :2 → onset (88 bins)
        sorted_by_suffix = sorted(
            enumerate(self._output_details), key=lambda t: _suffix_key(t[1])
        )
        output_dict: dict = {}
        note_onset_queue: List[np.ndarray] = []
        for j, detail in sorted_by_suffix:
            arr = unwrapped[j]
            if arr is None:
                continue
            n_bins = arr.shape[-1]
            if n_bins == N_FREQ_BINS_CONTOURS:
                output_dict["contour"] = arr
            else:
                note_onset_queue.append(arr)

        if len(note_onset_queue) >= 1:
            output_dict["note"] = note_onset_queue[0]
        if len(note_onset_queue) >= 2:
            output_dict["onset"] = note_onset_queue[1]

        if not output_dict.get("note") is not None or not output_dict.get("onset") is not None:
            log.warning("BasicPitch: could not map all output tensors — got keys: %s", list(output_dict))

        _midi, note_events = model_output_to_notes(
            output_dict,
            onset_thresh=onset_threshold,
            frame_thresh=frame_threshold,
            infer_onsets=True,
            min_note_len=min_note_len,
            min_freq=minimum_frequency,
            max_freq=maximum_frequency,
            include_pitch_bends=include_pitch_bends,
            multiple_pitch_bends=multiple_pitch_bends,
            melodia_trick=melodia_trick,
            midi_tempo=midi_tempo,
        )

        return note_events

    def evict(self) -> None:
        """Release interpreter resources."""
        self._interpreter = None
        self._input_details = None
        self._output_details = None
        log.debug("BasicPitch TFLite interpreter evicted from memory")
