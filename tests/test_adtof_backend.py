"""Tests for AdtofBackendProtocol and AdtofBackend load/evict lifecycle.

RED phase: All tests expected to fail until pipelines/adtof_backend.py is implemented.
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Protocol structural checks
# ---------------------------------------------------------------------------


def test_protocol_structural_check() -> None:
    """A duck-typed class with load/predict/evict passes isinstance check."""
    from pipelines.adtof_backend import AdtofBackendProtocol

    class DummyBackend:
        def load(self, device: str = "cpu") -> None:
            ...

        def predict(self, audio_path: pathlib.Path) -> list:
            return []

        def evict(self) -> None:
            ...

    assert isinstance(DummyBackend(), AdtofBackendProtocol) is True


def test_protocol_rejects_incomplete() -> None:
    """A class missing predict() fails isinstance check against AdtofBackendProtocol."""
    from pipelines.adtof_backend import AdtofBackendProtocol

    class IncompleteBackend:
        def load(self, device: str = "cpu") -> None:
            ...

        def evict(self) -> None:
            ...

    assert isinstance(IncompleteBackend(), AdtofBackendProtocol) is False


# ---------------------------------------------------------------------------
# Load / evict lifecycle
# ---------------------------------------------------------------------------

_MOCK_PATCHES = [
    "adtof_pytorch.calculate_n_bins",
    "adtof_pytorch.create_frame_rnn_model",
    "adtof_pytorch.get_default_weights_path",
    "adtof_pytorch.load_pytorch_weights",
]


def _make_mock_model() -> MagicMock:
    mock_model = MagicMock()
    mock_model.eval.return_value = mock_model
    mock_model.to.return_value = mock_model
    mock_model.cpu.return_value = mock_model
    return mock_model


def test_load_creates_model() -> None:
    """After load(), self._model is not None."""
    from pipelines.adtof_backend import AdtofBackend

    mock_model = _make_mock_model()

    with (
        patch("adtof_pytorch.calculate_n_bins", return_value=168),
        patch("adtof_pytorch.create_frame_rnn_model", return_value=mock_model),
        patch("adtof_pytorch.get_default_weights_path", return_value=pathlib.Path("/fake/weights.pth")),
        patch("adtof_pytorch.load_pytorch_weights", return_value=mock_model),
    ):
        backend = AdtofBackend()
        backend.load(device="cpu")

    assert backend._model is not None


def test_load_sets_eval_mode() -> None:
    """After load(), model.eval() was called."""
    from pipelines.adtof_backend import AdtofBackend

    mock_model = _make_mock_model()

    with (
        patch("adtof_pytorch.calculate_n_bins", return_value=168),
        patch("adtof_pytorch.create_frame_rnn_model", return_value=mock_model),
        patch("adtof_pytorch.get_default_weights_path", return_value=pathlib.Path("/fake/weights.pth")),
        patch("adtof_pytorch.load_pytorch_weights", return_value=mock_model),
    ):
        backend = AdtofBackend()
        backend.load(device="cpu")

    assert mock_model.eval.called is True


def test_evict_clears_model() -> None:
    """After load() then evict(), self._model is None."""
    from pipelines.adtof_backend import AdtofBackend

    mock_model = _make_mock_model()

    with (
        patch("adtof_pytorch.calculate_n_bins", return_value=168),
        patch("adtof_pytorch.create_frame_rnn_model", return_value=mock_model),
        patch("adtof_pytorch.get_default_weights_path", return_value=pathlib.Path("/fake/weights.pth")),
        patch("adtof_pytorch.load_pytorch_weights", return_value=mock_model),
    ):
        backend = AdtofBackend()
        backend.load(device="cpu")
        backend.evict()

    assert backend._model is None


def test_load_after_evict() -> None:
    """After evict(), calling load() again creates a new model.

    create_frame_rnn_model should be called twice (once per load call).
    """
    from pipelines.adtof_backend import AdtofBackend

    mock_model = _make_mock_model()
    mock_create = MagicMock(return_value=mock_model)

    with (
        patch("adtof_pytorch.calculate_n_bins", return_value=168),
        patch("adtof_pytorch.create_frame_rnn_model", mock_create),
        patch("adtof_pytorch.get_default_weights_path", return_value=pathlib.Path("/fake/weights.pth")),
        patch("adtof_pytorch.load_pytorch_weights", return_value=mock_model),
    ):
        backend = AdtofBackend()
        backend.load(device="cpu")
        backend.evict()
        backend.load(device="cpu")

    assert backend._model is not None
    assert mock_create.call_count == 2


def test_load_wraps_errors() -> None:
    """If create_frame_rnn_model raises, load() wraps it in ModelLoadError."""
    from pipelines.adtof_backend import AdtofBackend
    from utils.errors import ModelLoadError

    with (
        patch("adtof_pytorch.calculate_n_bins", return_value=168),
        patch("adtof_pytorch.create_frame_rnn_model", side_effect=RuntimeError("bad weights")),
        patch("adtof_pytorch.get_default_weights_path", return_value=pathlib.Path("/fake/weights.pth")),
    ):
        backend = AdtofBackend()
        with pytest.raises(ModelLoadError):
            backend.load(device="cpu")


# ---------------------------------------------------------------------------
# predict() — sample rate guard, NoteEvent conversion, correctness
# ---------------------------------------------------------------------------

import builtins
import numpy as np
import torch


def _make_predict_backend() -> "AdtofBackend":
    """Return an AdtofBackend with _model and _device pre-set (skip load())."""
    from pipelines.adtof_backend import AdtofBackend

    mock_model = MagicMock()
    # forward pass returns (1, 10, 5) sigmoid activations — all 0.9
    mock_model.return_value = torch.full((1, 10, 5), 0.9)
    backend = AdtofBackend()
    backend._model = mock_model
    backend._device = "cpu"
    return backend


def _make_sf_info(samplerate: int = 44100) -> MagicMock:
    info = MagicMock()
    info.samplerate = samplerate
    return info


def _make_audio_tensor() -> torch.Tensor:
    return torch.zeros(1, 10, 168, 1)


def _make_peak_picker_mock(peaks: dict) -> MagicMock:
    """Return a mock PeakPicker class whose instance .pick() returns [peaks]."""
    mock_picker_instance = MagicMock()
    mock_picker_instance.pick.return_value = [peaks]
    mock_picker_cls = MagicMock(return_value=mock_picker_instance)
    return mock_picker_cls


_KNOWN_PEAKS = {35: [0.5, 1.2], 38: [0.3], 47: [0.8], 42: [1.0, 1.5], 49: []}


def test_predict_rejects_wrong_sample_rate() -> None:
    """predict() raises InvalidInputError when audio is not 44100 Hz."""
    from utils.errors import InvalidInputError

    backend = _make_predict_backend()
    with patch("soundfile.info", return_value=_make_sf_info(22050)):
        with pytest.raises(InvalidInputError) as exc_info:
            backend.predict(pathlib.Path("/fake/drums.wav"))
    assert "44100" in str(exc_info.value)


def test_predict_no_disk_writes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """predict() with mocked model does not create any files."""
    backend = _make_predict_backend()
    monkeypatch.chdir(tmp_path)

    # Track open() calls — raise if any write-mode open occurs
    real_open = builtins.open

    def guarded_open(file, mode="r", *args, **kwargs):
        if "w" in str(mode) or "x" in str(mode):
            raise AssertionError(f"Unexpected write-mode open({file!r}, {mode!r})")
        return real_open(file, mode, *args, **kwargs)

    mock_picker_cls = _make_peak_picker_mock(_KNOWN_PEAKS)

    with (
        patch("soundfile.info", return_value=_make_sf_info(44100)),
        patch("adtof_pytorch.load_audio_for_model", return_value=_make_audio_tensor()),
        patch("adtof_pytorch.PeakPicker", mock_picker_cls),
        patch("adtof_pytorch.FRAME_RNN_THRESHOLDS", {}),
        patch("adtof_pytorch.LABELS_5", [35, 38, 47, 42, 49]),
        patch("builtins.open", side_effect=guarded_open),
    ):
        backend.predict(pathlib.Path("/fake/drums.wav"))

    assert list(tmp_path.iterdir()) == []


def test_note_events_gm_notes_only() -> None:
    """All NoteEvent pitches are exclusively in {35, 38, 42, 47, 49}."""
    backend = _make_predict_backend()
    mock_picker_cls = _make_peak_picker_mock(_KNOWN_PEAKS)

    with (
        patch("soundfile.info", return_value=_make_sf_info()),
        patch("adtof_pytorch.load_audio_for_model", return_value=_make_audio_tensor()),
        patch("adtof_pytorch.PeakPicker", mock_picker_cls),
        patch("adtof_pytorch.FRAME_RNN_THRESHOLDS", {}),
        patch("adtof_pytorch.LABELS_5", [35, 38, 47, 42, 49]),
    ):
        events = backend.predict(pathlib.Path("/fake/drums.wav"))

    assert len(events) > 0
    assert all(e[2] in {35, 38, 42, 47, 49} for e in events)


def test_non_sequential_label_mapping() -> None:
    """Tom (GM 47) and hi-hat (GM 42) are mapped correctly (non-sequential)."""
    backend = _make_predict_backend()
    # Only tom=47 at t=1.0 and hi-hat=42 at t=2.0
    peaks = {47: [1.0], 42: [2.0]}
    mock_picker_cls = _make_peak_picker_mock(peaks)

    with (
        patch("soundfile.info", return_value=_make_sf_info()),
        patch("adtof_pytorch.load_audio_for_model", return_value=_make_audio_tensor()),
        patch("adtof_pytorch.PeakPicker", mock_picker_cls),
        patch("adtof_pytorch.FRAME_RNN_THRESHOLDS", {}),
        patch("adtof_pytorch.LABELS_5", [35, 38, 47, 42, 49]),
    ):
        events = backend.predict(pathlib.Path("/fake/drums.wav"))

    assert len(events) == 2
    tom_event = next(e for e in events if abs(e[0] - 1.0) < 1e-9)
    hihat_event = next(e for e in events if abs(e[0] - 2.0) < 1e-9)
    assert tom_event[2] == 47, f"Expected GM 47 (tom) at t=1.0, got {tom_event[2]}"
    assert hihat_event[2] == 42, f"Expected GM 42 (hi-hat) at t=2.0, got {hihat_event[2]}"


def test_note_event_duration_and_velocity() -> None:
    """Each NoteEvent has 60ms duration and velocity 100."""
    backend = _make_predict_backend()
    mock_picker_cls = _make_peak_picker_mock({35: [0.5], 38: [1.0]})

    with (
        patch("soundfile.info", return_value=_make_sf_info()),
        patch("adtof_pytorch.load_audio_for_model", return_value=_make_audio_tensor()),
        patch("adtof_pytorch.PeakPicker", mock_picker_cls),
        patch("adtof_pytorch.FRAME_RNN_THRESHOLDS", {}),
        patch("adtof_pytorch.LABELS_5", [35, 38, 47, 42, 49]),
    ):
        events = backend.predict(pathlib.Path("/fake/drums.wav"))

    assert len(events) == 2
    for e in events:
        assert e[3] == 100, f"Expected velocity=100, got {e[3]}"
        assert abs((e[1] - e[0]) - 0.06) < 1e-9, f"Expected duration=0.06s, got {e[1]-e[0]}"


def test_note_events_sorted_by_onset() -> None:
    """Output NoteEvents are sorted by start time ascending."""
    backend = _make_predict_backend()
    # Out-of-order across classes: kick at t=2.0, snare at t=0.5, hihat at t=1.0
    mock_picker_cls = _make_peak_picker_mock({35: [2.0], 38: [0.5], 42: [1.0]})

    with (
        patch("soundfile.info", return_value=_make_sf_info()),
        patch("adtof_pytorch.load_audio_for_model", return_value=_make_audio_tensor()),
        patch("adtof_pytorch.PeakPicker", mock_picker_cls),
        patch("adtof_pytorch.FRAME_RNN_THRESHOLDS", {}),
        patch("adtof_pytorch.LABELS_5", [35, 38, 47, 42, 49]),
    ):
        events = backend.predict(pathlib.Path("/fake/drums.wav"))

    onsets = [e[0] for e in events]
    assert onsets == sorted(onsets), f"Events not sorted by onset: {onsets}"


def test_predict_raises_pipeline_error() -> None:
    """If model forward pass raises, predict() wraps it in PipelineExecutionError."""
    from utils.errors import PipelineExecutionError

    backend = _make_predict_backend()
    backend._model = MagicMock(side_effect=RuntimeError("CUDA OOM"))

    with (
        patch("soundfile.info", return_value=_make_sf_info()),
        patch("adtof_pytorch.load_audio_for_model", return_value=_make_audio_tensor()),
        patch("adtof_pytorch.PeakPicker", _make_peak_picker_mock({})),
        patch("adtof_pytorch.FRAME_RNN_THRESHOLDS", {}),
        patch("adtof_pytorch.LABELS_5", [35, 38, 47, 42, 49]),
    ):
        with pytest.raises(PipelineExecutionError):
            backend.predict(pathlib.Path("/fake/drums.wav"))


def test_peaks_to_note_events_empty() -> None:
    """_peaks_to_note_events({}) returns empty list."""
    from pipelines.adtof_backend import _peaks_to_note_events

    result = _peaks_to_note_events({})
    assert result == []
