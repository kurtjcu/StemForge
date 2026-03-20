"""LarsNet drum sub-separation backend.

Provides :class:`LarsNetBackend` which wraps the vendored LarsNet model
with absolute config path resolution, lazy loading, and safe GPU eviction.

Usage::

    backend = LarsNetBackend()
    backend.load(device="cpu")
    stems = backend.separate(Path("drums.wav"))
    backend.evict()
"""
from __future__ import annotations

import logging
import pathlib
import sys

import yaml

from models.registry import LARSNET_STEM_KEYS
from utils.cache import get_model_cache_dir
from utils.errors import ModelLoadError

logger = logging.getLogger(__name__)

_VENDOR_DIR = pathlib.Path(__file__).resolve().parent.parent / "vendor"
_LARSNET_VENDOR = _VENDOR_DIR / "larsnet"
_CONFIG_TEMPLATE = _LARSNET_VENDOR / "config.yaml"
_CACHE_SUBDIR = "larsnet"

# Expected checkpoint filename pattern per stem
_CHECKPOINT_NAMES: dict[str, str] = {
    stem: f"pretrained_{stem}_unet.pth" for stem in LARSNET_STEM_KEYS
}


def _build_absolute_config(cache_dir: pathlib.Path) -> dict:
    """Load config.yaml template and rewrite checkpoint paths to absolute.

    Returns the config dict with inference_models values pointing to
    ``cache_dir/{stem}/pretrained_{stem}_unet.pth``.
    """
    with open(_CONFIG_TEMPLATE, "r") as f:
        cfg = yaml.safe_load(f)
    for stem in LARSNET_STEM_KEYS:
        cfg["inference_models"][stem] = str(
            cache_dir / stem / _CHECKPOINT_NAMES[stem]
        )
    return cfg


def _write_absolute_config(cache_dir: pathlib.Path) -> pathlib.Path:
    """Write resolved config with absolute checkpoint paths to cache dir.

    The generated config is written to ``cache_dir/_larsnet_config.yaml``
    and regenerated on every :meth:`LarsNetBackend.load` call (idempotent).

    Returns the absolute path to the written config file.
    """
    cfg = _build_absolute_config(cache_dir)
    config_path = cache_dir / "_larsnet_config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        yaml.safe_dump(cfg, f)
    return config_path


class LarsNetBackend:
    """LarsNet drum sub-separation backend.

    Mirrors :class:`AdtofBackend` lifecycle: lazy ``load()`` → use → ``evict()``.
    Resolves config.yaml checkpoint paths to absolute at load time.
    """

    def __init__(self) -> None:
        self._model = None  # LarsNet instance when loaded
        self._device: str = "cpu"

    def load(self, device: str = "cpu") -> None:
        """Load all 5 LarsNet U-Net checkpoints.

        Generates an absolute-path config, checks all checkpoints exist,
        then instantiates the LarsNet model.

        Raises
        ------
        ModelLoadError
            If any checkpoint file is missing, with download instructions.
        """
        import torch  # noqa: F401 — needed for cuda.empty_cache later

        cache_dir = get_model_cache_dir(_CACHE_SUBDIR)

        # Check all 5 checkpoints exist before attempting load
        checkpoint_paths = [
            cache_dir / stem / _CHECKPOINT_NAMES[stem]
            for stem in LARSNET_STEM_KEYS
        ]
        missing = [p for p in checkpoint_paths if not p.exists()]
        if missing:
            raise ModelLoadError(
                f"LarsNet weights not found ({len(missing)}/5 missing). "
                f"Run: bash scripts/download_larsnet_weights.sh\n"
                f"Missing: {[str(p) for p in missing[:2]]}{'...' if len(missing) > 2 else ''}",
                model_name="larsnet-drums",
            )

        # Write resolved config with absolute paths
        config_path = _write_absolute_config(cache_dir)

        # Ensure vendor dir is on sys.path for LarsNet imports
        vendor_str = str(_VENDOR_DIR)
        if vendor_str not in sys.path:
            sys.path.insert(0, vendor_str)

        try:
            from larsnet import LarsNet

            model = LarsNet(
                wiener_filter=False,
                wiener_exponent=1.0,
                config=str(config_path),
                return_stft=False,
                device=device,
            )
            self._model = model
            self._device = device
            logger.info(
                "LarsNet model loaded on %s (%d U-Nets)",
                device,
                len(model.models),
            )
        except ModelLoadError:
            raise
        except Exception as exc:
            raise ModelLoadError(
                str(exc), model_name="larsnet-drums"
            ) from exc

    def evict(self) -> None:
        """Release all 5 U-Net models from memory.

        Moves each sub-model to CPU, clears the models dict, sets _model
        to None, and calls torch.cuda.empty_cache() if on CUDA.
        """
        import torch

        if self._model is not None:
            for stem_model in self._model.models.values():
                stem_model.cpu()
            self._model.models.clear()
            self._model = None
            if self._device.startswith("cuda"):
                torch.cuda.empty_cache()
        logger.info("LarsNet model evicted")

    @property
    def is_loaded(self) -> bool:
        """Whether the model is currently loaded."""
        return self._model is not None
