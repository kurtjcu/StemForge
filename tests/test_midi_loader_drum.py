"""Tests for MidiModelLoader ADTOF drum transcription extension.

RED phase: Tests for _ensure_adtof(), convert_drum_to_midi(), evict_drum_model().
All tests expected to fail until models/midi_loader.py is extended.
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch, sentinel

import pytest

from models.midi_loader import MidiModelLoader
from utils.errors import PipelineExecutionError


# ---------------------------------------------------------------------------
# Lazy loading
# ---------------------------------------------------------------------------


def test_adtof_lazy_not_loaded_at_init() -> None:
    """_adtof_backend is None immediately after construction — ADTOF not loaded at init."""
    loader = MidiModelLoader()
    assert loader._adtof_backend is None


def test_ensure_adtof_returns_backend() -> None:
    """_ensure_adtof() returns an AdtofBackend instance after first call."""
    loader = MidiModelLoader()

    mock_backend_instance = MagicMock()
    mock_backend_cls = MagicMock(return_value=mock_backend_instance)

    with patch("pipelines.adtof_backend.AdtofBackend", mock_backend_cls):
        # Patch the import inside _ensure_adtof via the module path it imports from
        with patch.dict("sys.modules", {}):
            import pipelines.adtof_backend as _adtof_mod
            orig_cls = getattr(_adtof_mod, "AdtofBackend", None)
            _adtof_mod.AdtofBackend = mock_backend_cls  # type: ignore[attr-defined]
            try:
                # Patch the deferred import inside the method
                with patch("models.midi_loader.MidiModelLoader._ensure_adtof", wraps=None) as _mock:
                    pass
            finally:
                if orig_cls is not None:
                    _adtof_mod.AdtofBackend = orig_cls

    # Simpler approach: directly test by patching the deferred import
    loader2 = MidiModelLoader()
    mock_instance = MagicMock()
    mock_cls = MagicMock(return_value=mock_instance)

    import sys
    # Create a mock for pipelines.adtof_backend that has AdtofBackend
    mock_adtof_module = MagicMock()
    mock_adtof_module.AdtofBackend = mock_cls
    with patch.dict(sys.modules, {"pipelines.adtof_backend": mock_adtof_module}):
        result = loader2._ensure_adtof()

    assert result is mock_instance
    mock_instance.load.assert_called_once()


def test_ensure_adtof_caches_instance() -> None:
    """Second call to _ensure_adtof() returns the same object (cached)."""
    loader = MidiModelLoader()
    mock_instance = MagicMock()
    mock_cls = MagicMock(return_value=mock_instance)

    import sys
    mock_adtof_module = MagicMock()
    mock_adtof_module.AdtofBackend = mock_cls
    with patch.dict(sys.modules, {"pipelines.adtof_backend": mock_adtof_module}):
        first = loader._ensure_adtof()
        second = loader._ensure_adtof()

    assert first is second
    # AdtofBackend() constructor called only once
    assert mock_cls.call_count == 1


# ---------------------------------------------------------------------------
# convert_drum_to_midi
# ---------------------------------------------------------------------------


def test_convert_drum_to_midi_calls_predict() -> None:
    """convert_drum_to_midi(path) calls backend.predict(path) and returns its result."""
    loader = MidiModelLoader()
    expected = [(0.1, 0.16, 35, 100)]

    mock_backend = MagicMock()
    mock_backend.predict.return_value = expected
    loader._adtof_backend = mock_backend

    result = loader.convert_drum_to_midi(pathlib.Path("dummy.wav"))

    mock_backend.predict.assert_called_once_with(pathlib.Path("dummy.wav"))
    assert result == expected


def test_convert_drum_to_midi_wraps_exception() -> None:
    """Non-PipelineExecutionError from predict() is wrapped in PipelineExecutionError."""
    loader = MidiModelLoader()

    mock_backend = MagicMock()
    mock_backend.predict.side_effect = ValueError("boom")
    loader._adtof_backend = mock_backend

    with pytest.raises(PipelineExecutionError) as exc_info:
        loader.convert_drum_to_midi(pathlib.Path("dummy.wav"))

    assert "midi" in exc_info.value.pipeline_name  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# evict
# ---------------------------------------------------------------------------


def test_evict_clears_adtof_backend() -> None:
    """evict() sets _adtof_backend to None and calls backend.evict()."""
    loader = MidiModelLoader()
    mock_backend = MagicMock()
    loader._adtof_backend = mock_backend

    loader.evict()

    assert loader._adtof_backend is None
    mock_backend.evict.assert_called_once()


def test_evict_no_error_when_none() -> None:
    """evict() with _adtof_backend=None does not raise any exception."""
    loader = MidiModelLoader()
    assert loader._adtof_backend is None

    # Must not raise
    loader.evict()


# ---------------------------------------------------------------------------
# evict_drum_model
# ---------------------------------------------------------------------------


def test_evict_drum_model_leaves_basicpitch() -> None:
    """evict_drum_model() clears _adtof_backend only; _model (BasicPitch) is untouched."""
    loader = MidiModelLoader()
    mock_backend = MagicMock()
    loader._adtof_backend = mock_backend
    # Simulate a loaded BasicPitch model
    sentinel_bp = sentinel.basicpitch_model
    loader._model = sentinel_bp  # type: ignore[assignment]

    loader.evict_drum_model()

    assert loader._adtof_backend is None
    mock_backend.evict.assert_called_once()
    assert loader._model is sentinel_bp
