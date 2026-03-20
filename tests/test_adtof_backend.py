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
