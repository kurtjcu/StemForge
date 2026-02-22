"""Stable Audio Open model loader for StemForge.

Wraps ``stable_audio_tools.get_pretrained_model`` to provide the same
load/evict interface as the other StemForge model loaders.

Returns a ``(model, model_config)`` tuple where *model_config* is the dict
from the HuggingFace repo (contains ``"sample_rate"``, ``"sample_size"``, etc.)
and *model* is a ``ConditionedDiffusionModelWrapper`` ready for inference.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any

import torch

from utils.errors import ModelLoadError


log = logging.getLogger("stemforge.models.musicgen_loader")

DEFAULT_MODEL_CACHE_DIR: pathlib.Path = (
    pathlib.Path.home() / ".cache" / "stemforge" / "musicgen"
)


class MusicGenModelLoader:
    """Load and cache the Stable Audio Open generation model.

    Parameters
    ----------
    cache_dir:
        Directory used to store downloaded model checkpoints.
        Passed to Hugging Face Hub via the ``HF_HOME`` environment variable.
        Defaults to ``~/.cache/stemforge/musicgen``.
    """

    def __init__(self, cache_dir: pathlib.Path = DEFAULT_MODEL_CACHE_DIR) -> None:
        self._cache_dir = pathlib.Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._model: Any = None
        self._model_config: dict | None = None
        self._loaded_model_name: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, model_name: str) -> tuple[Any, dict]:
        """Return ``(model, model_config)`` for *model_name*, loading if needed.

        On first call, downloads weights from HuggingFace Hub (~2 GB) and
        caches them locally.  Subsequent calls return the cached instance.
        The model is moved to CUDA if available, otherwise CPU.

        Parameters
        ----------
        model_name:
            HuggingFace repo ID, e.g. ``"stabilityai/stable-audio-open-1.0"``.

        Returns
        -------
        tuple[model, dict]
            Loaded ``ConditionedDiffusionModelWrapper`` and its config dict.

        Raises
        ------
        ModelLoadError
            If ``stable-audio-tools`` is not installed, or if the model
            cannot be downloaded or loaded.
        """
        if self._loaded_model_name == model_name and self._model is not None:
            return self._model, self._model_config  # type: ignore[return-value]

        try:
            from stable_audio_tools import get_pretrained_model  # type: ignore[import]
        except ImportError as exc:
            raise ModelLoadError(
                "stable-audio-tools is not installed.\n"
                "Install with: uv pip install stable-audio-tools",
                model_name=model_name,
            ) from exc

        try:
            log.info("Loading %s …", model_name)
            model, model_config = get_pretrained_model(model_name)
        except Exception as exc:
            raise ModelLoadError(
                f"Failed to download / load {model_name}: {exc}",
                model_name=model_name,
            ) from exc

        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:
            model = model.to(device)
            model.train(mode=False)   # put in inference mode (no grad, frozen BN)
        except Exception as exc:
            raise ModelLoadError(
                f"Failed to move {model_name} to {device}: {exc}",
                model_name=model_name,
            ) from exc

        self._model = model
        self._model_config = model_config
        self._loaded_model_name = model_name
        log.info("Loaded %s on %s", model_name, device)
        return model, model_config

    def is_cached(self, model_name: str) -> bool:
        """Return *True* if *model_name* is already loaded in memory."""
        return self._loaded_model_name == model_name and self._model is not None

    def evict(self, model_name: str | None = None) -> None:
        """Release the in-memory model to free GPU/CPU memory.

        Parameters
        ----------
        model_name:
            If given, only evict if this name matches the currently loaded
            model.  Pass ``None`` to evict unconditionally.
        """
        if model_name is not None and self._loaded_model_name != model_name:
            return
        if self._model is not None:
            try:
                self._model.cpu()
            except Exception:
                pass
            del self._model
        self._model = None
        self._model_config = None
        self._loaded_model_name = None
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        log.info("Evicted generation model from memory")
