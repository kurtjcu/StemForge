"""BS-Roformer / MelBand-Roformer separation pipeline for StemForge.

Implements the same configure / load_model / run / clear lifecycle as
DemucsPipeline, using chunked overlap-add inference so arbitrarily long
files can be processed with bounded GPU memory.

Algorithm
---------
1. Read stereo audio, normalize by mean absolute value.
2. Reflection-pad to a multiple of ``chunk_size``.
3. Slide a window of size ``chunk_size`` with step
   ``chunk_size // num_overlap``, applying a linear (triangular) fade to
   each chunk before accumulation.
4. Divide accumulated output by accumulated weights; trim padding; denormalize.
5. If ``other_fix`` is True (all current models), compute
   ``other = clip(mix - vocals, -1, 1)`` instead of running a second pass.
"""

from __future__ import annotations

import logging
import pathlib
import threading
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import torch

from models.registry import get_spec, RoformerSpec
from models.roformer_loader import RoformerModelLoader
from utils.audio_io import read_audio, write_audio
from utils.errors import ModelLoadError, PipelineExecutionError, InvalidInputError
from utils.paths import STEMS_DIR as _STEMS_DIR


log = logging.getLogger("stemforge.pipelines.roformer")


# ---------------------------------------------------------------------------
# Config / Result
# ---------------------------------------------------------------------------

@dataclass
class RoformerConfig:
    """Per-run configuration for :class:`RoformerPipeline`."""

    model_id: str
    stems: list[str] = field(default_factory=lambda: ["vocals", "other"])
    output_dir: pathlib.Path = _STEMS_DIR
    sample_rate: int = 44_100
    bit_depth: int = 24
    chunk_size: int = 352_800
    num_overlap: int = 2


@dataclass
class RoformerResult:
    """Return value from :meth:`RoformerPipeline.run`."""

    stem_paths: dict[str, pathlib.Path]
    sample_rate: int
    duration_seconds: float


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class RoformerPipeline:
    """Chunked overlap-add inference pipeline for BS-Roformer models.

    Lifecycle
    ---------
    ::

        pipeline.configure(config)
        pipeline.load_model()
        result = pipeline.run(audio_path)
        pipeline.clear()
    """

    def __init__(self) -> None:
        self._config: RoformerConfig | None = None
        self._model: torch.nn.Module | None = None
        self._yaml_config: dict | None = None
        self._loader = RoformerModelLoader()
        self._progress_cb: Callable[[float, str], None] | None = None
        self._cancel: threading.Event = threading.Event()
        self._last_device: str = "?"  # "GPU" or "CPU", updated during _infer
        self._device_hint: torch.device | None = None  # set by load_model(device=)

    @property
    def last_device(self) -> str:
        """Device label used for the most recent (or current) inference chunk."""
        return self._last_device

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def configure(self, config: RoformerConfig) -> None:
        self._config = config

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load_model(self, device: "torch.device | None" = None) -> None:
        """Download weights if needed and instantiate the model.

        Parameters
        ----------
        device:
            Target device hint (e.g. ``cuda:1``).  Stored and passed to
            ``_select_device()`` during inference.  When ``None``,
            auto-detects.
        """
        self._device_hint = device
        if self._config is None:
            raise PipelineExecutionError("roformer", "Call configure() before load_model().")
        try:
            self._model, self._yaml_config = self._loader.load(self._config.model_id)
        except ModelLoadError:
            raise
        except Exception as exc:
            raise ModelLoadError(self._config.model_id, str(exc)) from exc

    def clear(self) -> None:
        """Release the model from memory."""
        self._model = None
        self._yaml_config = None
        torch.cuda.empty_cache()

    def set_progress_callback(self, cb: Callable[[float, str], None]) -> None:
        self._progress_cb = cb

    def set_cancel_event(self, event: threading.Event) -> None:
        """Set a cancellation event checked during inference."""
        self._cancel = event

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, path: pathlib.Path) -> RoformerResult:
        """Separate *path* and write stems to the configured output directory.

        Parameters
        ----------
        path:
            Input audio file (any format supported by :func:`read_audio`).

        Returns
        -------
        RoformerResult
            Paths to the written stem WAV files plus metadata.

        Raises
        ------
        InvalidInputError
            If no audio file is provided.
        PipelineExecutionError
            On inference failure.
        """
        if self._config is None:
            raise PipelineExecutionError("roformer", "Call configure() first.")
        if self._model is None:
            raise PipelineExecutionError("roformer", "Call load_model() first.")
        if path is None:
            raise InvalidInputError("path", "Audio path must not be None.")

        spec = get_spec(self._config.model_id)
        assert isinstance(spec, RoformerSpec)

        # Override chunk_size and num_overlap from the YAML if available,
        # since model authors tune these per checkpoint.
        yaml_audio = (self._yaml_config or {}).get("audio", {})
        yaml_infer = (self._yaml_config or {}).get("inference", {})
        if "chunk_size" in yaml_audio:
            self._config.chunk_size = yaml_audio["chunk_size"]
        if "num_overlap" in yaml_infer:
            self._config.num_overlap = yaml_infer["num_overlap"]

        self._report(10.0, "Reading audio...")
        waveform, sr = read_audio(path, target_rate=self._config.sample_rate, mono=False)
        # waveform: (channels, samples) float32 numpy
        if waveform.ndim == 1:
            waveform = np.stack([waveform, waveform])
        elif waveform.shape[0] > 2:
            waveform = waveform[:2]

        duration = waveform.shape[1] / self._config.sample_rate

        try:
            if spec.target_instrument is None:
                # Multi-stem model: all stems predicted simultaneously
                stem_arrays = self._infer_multi(waveform, spec)
            else:
                # Single-target model: predict one stem, optionally derive other
                target_np = self._infer(waveform, spec)
                stem_arrays: dict[str, np.ndarray] = {spec.target_instrument: target_np}
                if spec.other_fix:
                    stem_arrays["other"] = np.clip(waveform - target_np, -1.0, 1.0)
        except Exception as exc:
            raise PipelineExecutionError("roformer", str(exc)) from exc

        if self._cancel.is_set():
            raise PipelineExecutionError("roformer", "Cancelled.")

        self._report(90.0, "Writing stems...")
        stem_paths: dict[str, pathlib.Path] = {}
        self._config.output_dir.mkdir(parents=True, exist_ok=True)

        base = path.stem
        for stem_name, audio_np in stem_arrays.items():
            if stem_name not in self._config.stems:
                continue
            out_path = self._config.output_dir / f"{base}_{stem_name}.wav"
            write_audio(audio_np, self._config.sample_rate, out_path,
                       bit_depth=self._config.bit_depth)
            stem_paths[stem_name] = out_path
            log.info("Wrote %s -> %s", stem_name, out_path)

        self._report(100.0, "Done.")
        return RoformerResult(
            stem_paths=stem_paths,
            sample_rate=self._config.sample_rate,
            duration_seconds=duration,
        )

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def _infer(self, mix_np: np.ndarray, spec: RoformerSpec) -> np.ndarray:
        """Chunked overlap-add forward pass; returns predicted target array."""
        chunk_size = self._config.chunk_size  # type: ignore[union-attr]
        num_overlap = self._config.num_overlap  # type: ignore[union-attr]
        step = chunk_size // num_overlap

        n_samples = mix_np.shape[1]

        # Reflection-pad to a multiple of chunk_size
        pad_right = (chunk_size - n_samples % chunk_size) % chunk_size
        if pad_right > 0:
            mix_padded = np.pad(mix_np, ((0, 0), (0, pad_right)), mode="reflect")
        else:
            mix_padded = mix_np

        total_padded = mix_padded.shape[1]
        result_padded = np.zeros_like(mix_padded)
        weight_padded = np.zeros(total_padded, dtype=np.float32)

        # Linear (triangular) fade window — peaks at 1.0 at centre
        window = np.linspace(0, 1, chunk_size // 2, endpoint=False)
        window = np.concatenate([window, window[::-1]])
        if window.shape[0] < chunk_size:
            window = np.pad(window, (0, chunk_size - window.shape[0]), constant_values=window[-1])
        window = window[:chunk_size].astype(np.float32)

        device = self._select_device(self._device_hint)
        self._last_device = "GPU" if device.type == "cuda" else "CPU"
        self._model.to(device)  # type: ignore[union-attr]

        # Ensure we always cover the full signal
        positions = list(range(0, total_padded - chunk_size + 1, step))
        if not positions or positions[-1] + chunk_size < total_padded:
            positions.append(total_padded - chunk_size)

        for chunk_idx, pos in enumerate(positions):
            if self._cancel.is_set():
                break

            pct = 10.0 + 78.0 * chunk_idx / max(len(positions), 1)
            self._report(pct, f"chunk {chunk_idx + 1}/{len(positions)}")

            chunk = mix_padded[:, pos: pos + chunk_size].copy()
            chunk_t = torch.from_numpy(chunk).unsqueeze(0).to(device)  # (1, 2, T)

            with torch.no_grad():
                pred = self._model(chunk_t)  # type: ignore[union-attr]

            pred_np = pred.squeeze(0).cpu().numpy()  # (2, T) or (1, 2, T) -> handled below
            if pred_np.ndim == 3:
                pred_np = pred_np[0]
            pred_np = pred_np[:, :chunk_size]

            # Actual output may be shorter than chunk_size when hop_length doesn't
            # divide chunk_size evenly (e.g. hop=512 vs chunk=352800 gives -32 samples).
            actual_len = pred_np.shape[-1]
            w = window[:actual_len]

            # Accumulate weighted result
            result_padded[:, pos: pos + actual_len] += pred_np * w[np.newaxis, :]
            weight_padded[pos: pos + actual_len] += w

        # Avoid division by zero where no chunks covered
        eps = 1e-8
        weight_padded = np.maximum(weight_padded, eps)
        result_padded /= weight_padded[np.newaxis, :]

        # Trim padding and clamp
        result = result_padded[:, :n_samples]
        return np.clip(result, -1.0, 1.0).astype(np.float32)

    def _infer_multi(self, mix_np: np.ndarray, spec: RoformerSpec) -> dict[str, np.ndarray]:
        """Chunked overlap-add for multi-stem models.

        Returns
        -------
        dict mapping each stem name (from ``training.instruments`` in the YAML)
        to a ``(2, T)`` float32 array.
        """
        chunk_size = self._config.chunk_size  # type: ignore[union-attr]
        num_overlap = self._config.num_overlap  # type: ignore[union-attr]
        step = chunk_size // num_overlap

        instruments: list[str] = self._yaml_config["training"]["instruments"]  # type: ignore[index]
        num_stems = len(instruments)

        n_samples = mix_np.shape[1]
        pad_right = (chunk_size - n_samples % chunk_size) % chunk_size
        mix_padded = (
            np.pad(mix_np, ((0, 0), (0, pad_right)), mode="reflect")
            if pad_right > 0
            else mix_np
        )
        total_padded = mix_padded.shape[1]

        result_padded = np.zeros((num_stems, 2, total_padded), dtype=np.float32)
        weight_padded = np.zeros(total_padded, dtype=np.float32)

        window = np.linspace(0, 1, chunk_size // 2, endpoint=False)
        window = np.concatenate([window, window[::-1]])
        if window.shape[0] < chunk_size:
            window = np.pad(window, (0, chunk_size - window.shape[0]), constant_values=window[-1])
        window = window[:chunk_size].astype(np.float32)

        device = self._select_device(self._device_hint)
        self._last_device = "GPU" if device.type == "cuda" else "CPU"
        self._model.to(device)  # type: ignore[union-attr]

        positions = list(range(0, total_padded - chunk_size + 1, step))
        if not positions or positions[-1] + chunk_size < total_padded:
            positions.append(total_padded - chunk_size)

        for chunk_idx, pos in enumerate(positions):
            if self._cancel.is_set():
                break

            pct = 10.0 + 78.0 * chunk_idx / max(len(positions), 1)
            self._report(pct, f"chunk {chunk_idx + 1}/{len(positions)}")

            chunk = mix_padded[:, pos: pos + chunk_size].copy()
            chunk_t = torch.from_numpy(chunk).unsqueeze(0).to(device)  # (1, 2, T)

            with torch.no_grad():
                pred = self._model(chunk_t)  # type: ignore[union-attr]  # (1, num_stems, 2, T)

            pred_np = pred.squeeze(0).cpu().numpy()  # (num_stems, 2, T)
            pred_np = pred_np[:, :, :chunk_size]

            # Clip window to actual output length (may differ from chunk_size when
            # hop_length doesn't divide chunk_size evenly).
            actual_len = pred_np.shape[-1]
            w = window[:actual_len]

            result_padded[:, :, pos: pos + actual_len] += (
                pred_np * w[np.newaxis, np.newaxis, :]
            )
            weight_padded[pos: pos + actual_len] += w

        eps = 1e-8
        weight_padded = np.maximum(weight_padded, eps)
        result_padded /= weight_padded[np.newaxis, np.newaxis, :]

        result = result_padded[:, :, :n_samples]
        result = np.clip(result, -1.0, 1.0).astype(np.float32)
        return {name: result[i] for i, name in enumerate(instruments)}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_device(self, hint: torch.device | None = None) -> torch.device:
        """Return CUDA if a quick probe succeeds; CPU otherwise.

        If *hint* is given and is a CUDA device, the probe runs on that
        specific GPU.  Otherwise falls back to the default GPU.

        torch 2.10+cu128 has a cublasSgemv bug on CUDA 12.9 that breaks the
        3D×1D batch matmul used by hyper-connections inside BSRoformer.
        We probe the exact failing pattern and pin to CPU for the whole run.
        """
        if not torch.cuda.is_available():
            return torch.device("cpu")
        target = hint if (hint is not None and hint.type == "cuda") else torch.device("cuda")
        try:
            a = torch.zeros(1, 8, 16, device=target)
            b = torch.zeros(16, device=target)
            _ = a @ b
            return target
        except RuntimeError:
            log.info("cuBLAS 3D×1D matmul probe failed — running BS-Roformer on CPU")
            return torch.device("cpu")

    def _report(self, pct: float, stage: str) -> None:
        if self._progress_cb is not None:
            try:
                self._progress_cb(pct, stage)
            except Exception:
                pass
