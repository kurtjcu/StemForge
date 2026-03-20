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

import soundfile as sf
import torch

from utils.errors import InvalidInputError, ModelLoadError, PipelineExecutionError
from utils.midi_io import NoteEvent

logger = logging.getLogger(__name__)

NOTE_DURATION: float = 0.06       # 60ms — matches notes_to_midi() drum cap
DEFAULT_VELOCITY: int = 100       # Fixed velocity; amplitude-based is v2 (ECLASS-02)
_VALID_GM_NOTES: frozenset[int] = frozenset({35, 38, 42, 47, 49})


def _peaks_to_note_events(
    peaks: dict[int, list[float]],
    duration: float = NOTE_DURATION,
    velocity: int = DEFAULT_VELOCITY,
) -> list[NoteEvent]:
    """Convert PeakPicker output to sorted NoteEvent list.

    Parameters
    ----------
    peaks:
        ``{gm_note: [onset_time_sec, ...]}`` from ``PeakPicker.pick()``.
    duration:
        Fixed note duration in seconds.
    velocity:
        Fixed MIDI velocity (1-127).

    Returns
    -------
    list[NoteEvent]
        Sorted by onset time ascending.
    """
    events: list[NoteEvent] = []
    for gm_note, times in peaks.items():
        gm_note_int = int(gm_note)
        assert gm_note_int in _VALID_GM_NOTES, (
            f"Unexpected GM note {gm_note_int} from PeakPicker; "
            f"expected one of {sorted(_VALID_GM_NOTES)}"
        )
        for onset in times:
            events.append((onset, onset + duration, gm_note_int, velocity))
    events.sort(key=lambda e: e[0])
    return events


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
        """Transcribe drum audio and return NoteEvent tuples.

        Raises
        ------
        InvalidInputError
            If the audio sample rate is not 44100 Hz.
        PipelineExecutionError
            If the model forward pass or onset detection fails.
        RuntimeError
            If called before ``load()``.
        """
        # --- Sample rate guard (ADT-02) ---
        info = sf.info(str(audio_path))
        if info.samplerate != 44100:
            raise InvalidInputError(
                f"ADTOF requires 44100 Hz audio; got {info.samplerate} Hz: {audio_path}",
                field="audio_path",
            )

        if self._model is None:
            raise RuntimeError("Model not loaded — call load() first")

        try:
            from adtof_pytorch import (
                load_audio_for_model,
                PeakPicker,
                FRAME_RNN_THRESHOLDS,
                LABELS_5,
            )

            # --- Forward pass (ADT-01: zero disk writes) ---
            x = load_audio_for_model(str(audio_path))
            x = x.to(self._device)
            with torch.no_grad():
                pred = self._model(x).cpu().numpy()

            # --- Onset detection (ADT-04) ---
            picker = PeakPicker(thresholds=FRAME_RNN_THRESHOLDS, fps=100)
            picked = picker.pick(pred, labels=LABELS_5, label_offset=0)[0]

            # --- Convert to NoteEvent ---
            events = _peaks_to_note_events(picked)
            logger.info("ADTOF transcription: %d note events", len(events))
            return events

        except InvalidInputError:
            raise
        except Exception as exc:
            raise PipelineExecutionError(
                f"ADTOF prediction failed: {exc}",
                pipeline_name="adtof_backend",
            ) from exc

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
