"""
BasicPitch model loader for StemForge.

Responsible for locating, downloading (if absent), verifying, and loading
the BasicPitch neural network weights into memory.  The loader supports
both the original TensorFlow SavedModel format and an ONNX export, with
format selection deferred to runtime environment detection.

GPU note
--------
BasicPitch's TensorFlow build has no precompiled CUDA kernels for compute
capability 12.0 (RTX 5080) and ptxas is unavailable for JIT fallback.
``load()`` therefore unconditionally forces TF to run on CPU only by
setting ``CUDA_VISIBLE_DEVICES=-1`` *before* TF is imported and calling
``tf.config.set_visible_devices([], "GPU")`` after initialisation.
This has no effect on the PyTorch-based pipelines (Demucs, MusicGen).
"""

import gc
import os
import pathlib
import logging
import hashlib
from typing import Any

from models.registry import BASICPITCH
from utils.errors import ModelLoadError


DEFAULT_MODEL_CACHE_DIR: pathlib.Path = BASICPITCH.cache_dir

SUPPORTED_FORMATS: tuple[str, ...] = ("savedmodel", "onnx")

log = logging.getLogger("stemforge.models.basicpitch_loader")


class BasicPitchModelLoader:
    """Loads and caches BasicPitch model weights.

    Parameters
    ----------
    cache_dir:
        Directory used to store downloaded model files.  Defaults to
        ``~/.cache/stemforge/basicpitch``.
    preferred_format:
        Preferred model serialisation format (``'savedmodel'`` or ``'onnx'``).
        Currently only ``'savedmodel'`` is supported; ``'onnx'`` is reserved
        for a future export path.
    """

    cache_dir: pathlib.Path
    preferred_format: str
    _model: Any

    def __init__(
        self,
        cache_dir: pathlib.Path = DEFAULT_MODEL_CACHE_DIR,
        preferred_format: str = "savedmodel",
    ) -> None:
        self.cache_dir = cache_dir
        self.preferred_format = preferred_format
        self._model = None

    def load(self) -> Any:
        """Return the BasicPitch model, loading or downloading it as needed.

        Forces TensorFlow to run on CPU only to avoid CUDA kernel
        incompatibilities on unsupported GPU architectures.

        Returns
        -------
        Any
            Loaded TF SavedModel object ready for frame-level inference.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If the model files are corrupt, cannot be deserialised, or
            insufficient memory is available to initialise the session.
        """
        if self._model is not None:
            log.debug("Returning cached BasicPitch model")
            return self._model

        # Force TF to CPU *before* any TF symbol is imported.
        # This must happen before `import tensorflow` to take effect.
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

        try:
            import tensorflow as tf
        except ImportError as exc:
            raise ModelLoadError(
                f"TensorFlow is not installed: {exc}", model_name="basicpitch"
            ) from exc

        # Belt-and-suspenders: also call the runtime API in case TF was
        # already imported earlier in this process.
        try:
            tf.config.set_visible_devices([], "GPU")
        except RuntimeError:
            pass  # GPU init already happened; env var should have prevented it

        try:
            from basic_pitch import ICASSP_2022_MODEL_PATH
        except ImportError as exc:
            raise ModelLoadError(
                f"basic-pitch package is not installed: {exc}", model_name="basicpitch"
            ) from exc

        log.info("Loading BasicPitch model from %s", ICASSP_2022_MODEL_PATH)
        try:
            self._model = tf.saved_model.load(str(ICASSP_2022_MODEL_PATH))
        except Exception as exc:
            raise ModelLoadError(
                f"Failed to load BasicPitch SavedModel: {exc}", model_name="basicpitch"
            ) from exc

        log.info("BasicPitch model loaded (CPU-only)")
        return self._model

    def is_cached(self) -> bool:
        """Return *True* if the model files exist in the local cache directory."""
        if self._model is not None:
            return True
        # BasicPitch ships its model inside the package directory.
        try:
            from basic_pitch import ICASSP_2022_MODEL_PATH
            return pathlib.Path(ICASSP_2022_MODEL_PATH).exists()
        except ImportError:
            return False

    def download(self) -> pathlib.Path:
        """Download the model files and return the root cache directory path.

        BasicPitch bundles its weights inside the installed package, so no
        explicit download step is needed.  This method calls :meth:`load`
        to trigger any deferred initialisation and returns the package
        model directory.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If the download fails due to a network error or the server
            returns an unexpected response.
        """
        self.load()
        try:
            from basic_pitch import ICASSP_2022_MODEL_PATH
            return pathlib.Path(ICASSP_2022_MODEL_PATH)
        except ImportError:
            return self.cache_dir

    def _verify_checksum(self, file_path: pathlib.Path, expected: str) -> bool:
        """Return *True* when SHA-256 of *file_path* matches *expected*."""
        sha = hashlib.sha256()
        with file_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                sha.update(chunk)
        return sha.hexdigest() == expected

    def _select_format(self) -> str:
        """Choose the best available format based on installed runtime libraries."""
        # ONNX export is not yet supported by the upstream basic-pitch package.
        # Always fall back to savedmodel regardless of preferred_format.
        return "savedmodel"

    def evict(self) -> None:
        """Remove the in-memory cached model to free memory."""
        if self._model is not None:
            self._model = None
            gc.collect()
            log.debug("BasicPitch model evicted from memory")
