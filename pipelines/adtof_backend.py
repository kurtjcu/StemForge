"""ADTOF-pytorch automatic drum transcription backend.

Provides :class:`AdtofBackendProtocol` (structural interface for ADT backends)
and :class:`AdtofBackend` (ADTOF Frame-RNN implementation).

Usage::

    backend = AdtofBackend()
    backend.load(device="cuda")
    events = backend.predict(Path("drums.wav"))
    backend.evict()
"""
from __future__ import annotations

import logging
import pathlib
from typing import Protocol, runtime_checkable

import torch

from utils.errors import ModelLoadError
from utils.midi_io import NoteEvent

logger = logging.getLogger(__name__)


@runtime_checkable
class AdtofBackendProtocol(Protocol):
    """Structural interface for automatic drum transcription backends.

    Implementations are not required to inherit from this class.  Any class
    with matching ``load``, ``predict``, and ``evict`` method signatures will
    satisfy ``isinstance(obj, AdtofBackendProtocol)`` checks at runtime.
    """

    def load(self, device: str = "cpu") -> None:
        """Load model weights into memory on the given device."""
        ...

    def predict(self, audio_path: pathlib.Path) -> list[NoteEvent]:
        """Transcribe drum audio; return NoteEvent tuples, GM notes only."""
        ...

    def evict(self) -> None:
        """Release model weights from memory."""
        ...


class AdtofBackend:
    """ADTOF Frame-RNN drum transcription backend.

    Implements :class:`AdtofBackendProtocol` via structural subtyping
    (no inheritance required).

    Example::

        backend = AdtofBackend()
        backend.load(device="cuda")
        events = backend.predict(Path("drums_stem.wav"))
        backend.evict()
    """

    def __init__(self) -> None:
        self._model: torch.nn.Module | None = None
        self._device: str = "cpu"

    def load(self, device: str = "cpu") -> None:
        """Load ADTOF Frame-RNN weights into memory.

        Imports adtof_pytorch functions at call time to preserve lazy loading.
        Wraps any exception from weight loading in :class:`ModelLoadError`.

        Parameters
        ----------
        device:
            Torch device string, e.g. ``"cpu"``, ``"cuda"``, ``"cuda:0"``.
        """
        try:
            import adtof_pytorch

            n_bins = adtof_pytorch.calculate_n_bins()
            model = adtof_pytorch.create_frame_rnn_model(n_bins)
            model.eval()
            weights_path = adtof_pytorch.get_default_weights_path()
            model = adtof_pytorch.load_pytorch_weights(model, str(weights_path), strict=False)
            model.to(device)
            self._model = model
            self._device = device
            logger.info("ADTOF model loaded on %s", device)
        except Exception as exc:
            raise ModelLoadError(str(exc), model_name="adtof-drums") from exc

    def predict(self, audio_path: pathlib.Path) -> list[NoteEvent]:
        """Transcribe drum audio to NoteEvents.

        .. note::
            This method is implemented in Plan 02-02.  The stub is present
            here so that the Protocol structural check passes (the method must
            exist on the class).

        Raises
        ------
        NotImplementedError
            Always — implementation deferred to Plan 02-02.
        """
        raise NotImplementedError("predict() implemented in Plan 02-02")

    def evict(self) -> None:
        """Release model weights from GPU/CPU memory.

        Moves the model to CPU before clearing the reference so PyTorch can
        free GPU memory.  Calls ``torch.cuda.empty_cache()`` when the model
        was loaded on a CUDA device.
        """
        if self._model is not None:
            self._model.cpu()
            self._model = None
            if self._device.startswith("cuda"):
                torch.cuda.empty_cache()
        logger.info("ADTOF model evicted")
