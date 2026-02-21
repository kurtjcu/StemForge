"""
BasicPitch TFLite model loader for StemForge.

This version removes all TensorFlow and SavedModel dependencies and uses
`tflite-runtime` to load the official BasicPitch `.tflite` model shipped
inside the `basic_pitch` GitHub package.

The public API remains compatible with the previous TensorFlow-based
loader: `.load()` initialises the model, and `.predict(audio)` performs
frame-level inference and returns note events.
"""

import os
import pathlib
import logging
from typing import Any, List

import numpy as np
import tflite_runtime.interpreter as tflite

from utils.errors import ModelLoadError

# BasicPitch feature extraction + postprocessing
from basic_pitch.features import compute_features
from basic_pitch.postprocessing import model_output_to_notes
from basic_pitch.constants import AUDIO_SAMPLE_RATE

log = logging.getLogger("stemforge.models.basicpitch_loader")


class BasicPitchModelLoader:
    """
    Loads the BasicPitch TFLite model and exposes a simple inference API.

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
        """
        Locate the BasicPitch TFLite model inside the installed package.

        The GitHub repo ships the model at:
            basic_pitch/model.tflite
        """
        try:
            import basic_pitch
        except ImportError as exc:
            raise ModelLoadError(
                f"basic-pitch package is not installed: {exc}",
                model_name="basicpitch",
            ) from exc

        pkg_dir = pathlib.Path(basic_pitch.__file__).parent
        model_path = pkg_dir / "model.tflite"

        if not model_path.exists():
            raise ModelLoadError(
                f"BasicPitch TFLite model not found at: {model_path}",
                model_name="basicpitch",
            )

        return model_path

    def load(self) -> "BasicPitchModelLoader":
        """
        Load the TFLite interpreter and prepare it for inference.

        Returns
        -------
        BasicPitchModelLoader
            The loader instance with an initialised interpreter.
        """
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

    def predict(self, audio: np.ndarray, sample_rate: int = AUDIO_SAMPLE_RATE) -> List[Any]:
        """
        Run BasicPitch inference on a raw audio array.

        Parameters
        ----------
        audio : np.ndarray
            Raw mono audio samples.
        sample_rate : int
            Audio sample rate. Must match BasicPitch's expected rate.

        Returns
        -------
        list
            A list of note events (pitch, onset, offset, velocity).
        """
        if self._interpreter is None:
            raise ModelLoadError(
                "BasicPitch model not loaded. Call load() first.",
                model_name="basicpitch",
            )

        # Extract features using the official BasicPitch pipeline
        features = compute_features(audio, sample_rate)

        # TFLite expects float32 input
        input_tensor = features.astype(np.float32)

        # Set input tensor
        self._interpreter.set_tensor(self._input_details[0]["index"], input_tensor)

        # Run inference
        self._interpreter.invoke()

        # Collect output tensors
        outputs = [
            self._interpreter.get_tensor(detail["index"])
            for detail in self._output_details
        ]

        # Convert model outputs to note events
        notes = model_output_to_notes(outputs)
        return notes

    def evict(self) -> None:
        """Release interpreter resources."""
        self._interpreter = None
        self._input_details = None
        self._output_details = None
        log.debug("BasicPitch TFLite interpreter evicted from memory")
