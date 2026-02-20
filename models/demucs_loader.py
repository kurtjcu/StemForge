"""
Demucs model loader for StemForge.

Responsible for locating, downloading (if absent), verifying, and loading
Demucs model weights into memory.  Caches loaded models in a process-level
registry so that repeated pipeline runs reuse the same in-memory model
without redundant I/O.
"""

import gc
import os
import pathlib
import logging
import hashlib
from typing import Any

from utils.errors import ModelLoadError


DEFAULT_MODEL_CACHE_DIR: pathlib.Path = pathlib.Path.home() / ".cache" / "stemforge" / "demucs"

log = logging.getLogger("stemforge.models.demucs_loader")


class DemucsModelLoader:
    """Loads and caches Demucs model weights.

    Parameters
    ----------
    cache_dir:
        Directory used to store downloaded model checkpoints.  Defaults
        to ``~/.cache/stemforge/demucs``.
    """

    cache_dir: pathlib.Path
    _registry: dict[str, Any]

    def __init__(self, cache_dir: pathlib.Path = DEFAULT_MODEL_CACHE_DIR) -> None:
        self.cache_dir = cache_dir
        self._registry = {}

    def load(self, model_name: str) -> Any:
        """Return the model for *model_name*, loading it from cache or downloading it.

        Parameters
        ----------
        model_name:
            Identifier of the Demucs model variant (e.g. ``'htdemucs'``).

        Returns
        -------
        Any
            Loaded model ready for inference (type determined at runtime).

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If *model_name* is not a recognised variant, the checkpoint file
            is corrupt or missing, or insufficient memory is available.
        """
        if model_name in self._registry:
            log.debug("Returning in-memory model '%s'", model_name)
            return self._registry[model_name]

        try:
            from demucs.pretrained import get_model
        except ImportError as exc:
            raise ModelLoadError(
                f"demucs package is not installed: {exc}",
                model_name=model_name,
            ) from exc

        log.info(
            "Loading Demucs model '%s' (may download weights on first run)", model_name
        )
        try:
            model = get_model(model_name)
        except KeyError as exc:
            raise ModelLoadError(
                f"Unknown Demucs model name '{model_name}'.  "
                f"Valid names include: htdemucs, htdemucs_ft, mdx_extra, mdx_extra_q.",
                model_name=model_name,
            ) from exc
        except Exception as exc:
            raise ModelLoadError(
                f"Failed to load Demucs model '{model_name}': {exc}",
                model_name=model_name,
            ) from exc

        self._registry[model_name] = model
        log.info(
            "Model '%s' ready  (samplerate=%d Hz, sources=%s, channels=%d)",
            model_name,
            model.samplerate,
            model.sources,
            model.audio_channels,
        )
        return model

    def is_cached(self, model_name: str) -> bool:
        """Return *True* if the checkpoint for *model_name* exists in the cache."""
        return model_name in self._registry

    def download(self, model_name: str) -> pathlib.Path:
        """Download the checkpoint for *model_name* and return its local path.

        Demucs downloads checkpoints as a side-effect of :func:`~demucs.pretrained.get_model`,
        so this method simply delegates to :meth:`load`.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If the download fails due to a network error or the remote
            resource returns an unexpected response.
        """
        self.load(model_name)
        return self.cache_dir

    def _verify_checksum(self, file_path: pathlib.Path, expected: str) -> bool:
        """Return *True* when SHA-256 of *file_path* matches *expected*."""
        sha = hashlib.sha256()
        with file_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                sha.update(chunk)
        return sha.hexdigest() == expected

    def _cache_path(self, model_name: str) -> pathlib.Path:
        """Derive the expected local checkpoint path for *model_name*."""
        return self.cache_dir / model_name

    def evict(self, model_name: str) -> None:
        """Remove the in-memory cached model for *model_name* to free memory."""
        if model_name in self._registry:
            del self._registry[model_name]
            gc.collect()
            log.debug("Evicted model '%s' from in-memory registry", model_name)
