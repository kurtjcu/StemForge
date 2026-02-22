"""BS-Roformer / MelBand-Roformer model loader for StemForge.

Downloads the ``.ckpt`` weight file and ``.yaml`` config file on first use,
then instantiates the appropriate model class from the ``bs-roformer`` package.
"""

from __future__ import annotations

import logging
import pathlib
import urllib.request
from typing import Any

import torch
import torch.nn as nn
import yaml

from models.registry import get_spec, RoformerSpec
from utils.errors import ModelLoadError


log = logging.getLogger("stemforge.models.roformer_loader")

DEFAULT_ROFORMER_CACHE_DIR = pathlib.Path.home() / ".cache" / "stemforge" / "roformer"

# ---------------------------------------------------------------------------
# hyper-connections CUDA contiguity patch
# ---------------------------------------------------------------------------
# hyper-connections <=0.4.9 has a bug where width_connection() performs
#   normed @ self.dynamic_beta_fn.float()
# but `normed` can be non-contiguous after norm+float() on CUDA, causing
# CUBLAS_STATUS_INVALID_VALUE (cublasSgemv rejects non-contiguous strides).
# We patch the method at import time so the fix survives pip reinstalls.

def _check_gpu_compatibility() -> None:
    """Log a warning if the torch/CUDA stack has known cuBLAS incompatibilities.

    torch 2.10+cu128 (built for CUDA 12.8) has pervasive CUBLAS_STATUS_INVALID_VALUE
    errors when run against the CUDA 12.9 runtime. All cuBLAS GEMM paths fail,
    so RoformerPipeline will use CPU for inference until torch is upgraded to a
    build that targets CUDA 12.9 or later.
    """
    if not torch.cuda.is_available():
        return
    try:
        a = torch.zeros(2, 4, device='cuda')
        b = torch.zeros(4, 2, device='cuda')
        torch.mm(a, b)
    except RuntimeError as exc:
        if "CUBLAS" in str(exc) or "CUDA error" in str(exc):
            log.warning(
                "cuBLAS is not functional on this torch/CUDA combination "
                "(torch %s, CUDA runtime %s). "
                "BS-Roformer will run on CPU. "
                "Upgrade to a torch build targeting your CUDA runtime to enable GPU.",
                torch.__version__,
                torch.version.cuda,
            )

_check_gpu_compatibility()

# Keys present in some community YAMLs that are NOT valid constructor params for
# either BSRoformer or MelBandRoformer (training/utility flags, not architecture).
_INVALID_ROFORMER_KEYS = frozenset({
    "linear_transformer_depth",  # not a constructor param in any released version
    "use_torch_checkpoint",      # gradient-checkpointing training flag
    "skip_connection",           # not present in all package versions
})


class RoformerModelLoader:
    """Download-on-demand loader for BS-Roformer and MelBand-Roformer models."""

    def __init__(self, cache_dir: pathlib.Path = DEFAULT_ROFORMER_CACHE_DIR) -> None:
        self._cache_dir = pathlib.Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def load(self, model_id: str) -> tuple[nn.Module, dict[str, Any]]:
        """Return ``(model, yaml_config)`` for *model_id*.

        Downloads weight and config files on first call.

        Raises
        ------
        ModelLoadError
            If download, YAML parsing, or model instantiation fails.
        """
        spec = get_spec(model_id)
        if not isinstance(spec, RoformerSpec):
            raise ModelLoadError(
                model_id,
                f"{model_id!r} is not a RoformerSpec (got {type(spec).__name__}).",
            )

        ckpt_path = self._cache_dir / f"{model_id}.ckpt"
        yaml_path = self._cache_dir / f"{model_id}.yaml"

        try:
            if not ckpt_path.exists():
                log.info("Downloading checkpoint: %s", spec.checkpoint_url)
                self._download(spec.checkpoint_url, ckpt_path)
            if not yaml_path.exists():
                log.info("Downloading config: %s", spec.config_url)
                self._download(spec.config_url, yaml_path)
        except Exception as exc:
            raise ModelLoadError(model_id, f"Download failed: {exc}") from exc

        try:
            with yaml_path.open() as fh:
                # full_load required: config files use !!python/tuple tags
                yaml_config: dict[str, Any] = yaml.full_load(fh)
        except Exception as exc:
            raise ModelLoadError(model_id, f"YAML parse error: {exc}") from exc

        try:
            model = self._instantiate(spec, yaml_config)
        except Exception as exc:
            raise ModelLoadError(model_id, f"Model instantiation failed: {exc}") from exc

        try:
            state = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
            # Some checkpoints wrap weights under 'state_dict' or 'model' keys
            if isinstance(state, dict):
                state = state.get("state_dict", state.get("model", state))
            model.load_state_dict(state, strict=False)
        except Exception as exc:
            raise ModelLoadError(model_id, f"Checkpoint load failed: {exc}") from exc

        model.eval()
        log.info("Loaded %s (%s)", model_id, type(model).__name__)
        return model, yaml_config

    def evict(self, model_id: str) -> None:
        """Remove cached files for *model_id* (forces re-download on next load)."""
        for ext in (".ckpt", ".yaml"):
            p = self._cache_dir / f"{model_id}{ext}"
            if p.exists():
                p.unlink()
                log.info("Evicted cache file: %s", p)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _download(self, url: str, dest: pathlib.Path) -> None:
        """Atomically download *url* to *dest* via a temporary file."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            urllib.request.urlretrieve(url, str(tmp))
            tmp.rename(dest)
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise

    def _instantiate(self, spec: RoformerSpec, yaml_config: dict[str, Any]) -> nn.Module:
        """Instantiate the correct model class from *yaml_config* without weights."""
        model_kwargs = dict(yaml_config.get("model", {}))

        # bs-roformer uses beartype, which enforces tuple for sequence params;
        # YAML deserialises sequences as list, so convert recursively.
        for key, value in model_kwargs.items():
            if isinstance(value, list):
                model_kwargs[key] = tuple(value)
            elif isinstance(value, dict):
                model_kwargs[key] = {
                    k: tuple(v) if isinstance(v, list) else v
                    for k, v in value.items()
                }

        for bad_key in _INVALID_ROFORMER_KEYS:
            model_kwargs.pop(bad_key, None)

        if spec.architecture == "bs_roformer" or "freqs_per_bands" in model_kwargs:
            from bs_roformer import BSRoformer  # type: ignore[import]
            return BSRoformer(**model_kwargs)
        else:
            from bs_roformer import MelBandRoformer  # type: ignore[import]
            return MelBandRoformer(**model_kwargs)
