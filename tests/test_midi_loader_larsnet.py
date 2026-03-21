"""Tests for MidiModelLoader LarsNet drum sub-separation extension.

RED phase: Tests for _ensure_larsnet(), evict_larsnet(), separate_drums(),
convert_drum_to_midi_with_larsnet(). All mock-based — no real weights needed.
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock, patch, call

import pytest
import torch

from models.midi_loader import MidiModelLoader
from models.registry import LARSNET_STEM_KEYS
from utils.errors import ModelLoadError, PipelineExecutionError


# ---------------------------------------------------------------------------
# Lazy loading / init
# ---------------------------------------------------------------------------


def test_larsnet_not_loaded_at_init() -> None:
    """_larsnet_backend is None immediately after construction (lazy-load, not eager)."""
    loader = MidiModelLoader()
    assert loader._larsnet_backend is None


# ---------------------------------------------------------------------------
# _ensure_larsnet
# ---------------------------------------------------------------------------


def test_ensure_larsnet_loads_backend() -> None:
    """_ensure_larsnet() calls backend.load() and returns the backend."""
    loader = MidiModelLoader()
    mock_backend = MagicMock()
    mock_cls = MagicMock(return_value=mock_backend)

    mock_larsnet_module = MagicMock()
    mock_larsnet_module.LarsNetBackend = mock_cls

    with patch.dict(sys.modules, {"pipelines.larsnet_backend": mock_larsnet_module}):
        result = loader._ensure_larsnet()

    assert result is mock_backend
    mock_backend.load.assert_called_once()


def test_ensure_larsnet_idempotent() -> None:
    """_ensure_larsnet() called twice returns the same object; load() called only once."""
    loader = MidiModelLoader()
    mock_backend = MagicMock()
    mock_cls = MagicMock(return_value=mock_backend)

    mock_larsnet_module = MagicMock()
    mock_larsnet_module.LarsNetBackend = mock_cls

    with patch.dict(sys.modules, {"pipelines.larsnet_backend": mock_larsnet_module}):
        first = loader._ensure_larsnet()
        second = loader._ensure_larsnet()

    assert first is second
    assert mock_cls.call_count == 1
    mock_backend.load.assert_called_once()


def test_ensure_larsnet_raises_model_load_error_on_import_failure() -> None:
    """_ensure_larsnet() raises ModelLoadError when the import fails."""
    loader = MidiModelLoader()

    with patch.dict(sys.modules, {"pipelines.larsnet_backend": None}):  # type: ignore[dict-item]
        with pytest.raises(ModelLoadError) as exc_info:
            loader._ensure_larsnet()

    assert "larsnet" in exc_info.value.model_name.lower()  # type: ignore[union-attr]


def test_ensure_larsnet_logs_load_time(caplog: pytest.LogCaptureFixture) -> None:
    """_ensure_larsnet() logs load time containing 'ready in'."""
    import logging

    loader = MidiModelLoader()
    mock_backend = MagicMock()
    mock_cls = MagicMock(return_value=mock_backend)

    mock_larsnet_module = MagicMock()
    mock_larsnet_module.LarsNetBackend = mock_cls

    with caplog.at_level(logging.INFO, logger="stemforge.models.midi_loader"):
        with patch.dict(sys.modules, {"pipelines.larsnet_backend": mock_larsnet_module}):
            loader._ensure_larsnet()

    assert any("ready in" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# evict_larsnet
# ---------------------------------------------------------------------------


def test_evict_larsnet_clears_backend() -> None:
    """evict_larsnet() sets _larsnet_backend to None and calls backend.evict()."""
    loader = MidiModelLoader()
    mock_backend = MagicMock()
    loader._larsnet_backend = mock_backend

    loader.evict_larsnet()

    assert loader._larsnet_backend is None
    mock_backend.evict.assert_called_once()


def test_evict_larsnet_noop_when_none() -> None:
    """evict_larsnet() with _larsnet_backend=None does not raise."""
    loader = MidiModelLoader()
    assert loader._larsnet_backend is None
    # Must not raise
    loader.evict_larsnet()


def test_master_evict_calls_evict_larsnet() -> None:
    """evict() also evicts the LarsNet backend (clears _larsnet_backend)."""
    loader = MidiModelLoader()
    mock_larsnet = MagicMock()
    loader._larsnet_backend = mock_larsnet

    loader.evict()

    assert loader._larsnet_backend is None
    mock_larsnet.evict.assert_called_once()


# ---------------------------------------------------------------------------
# separate_drums
# ---------------------------------------------------------------------------


def test_separate_drums_returns_5_stem_paths(tmp_path: pathlib.Path) -> None:
    """separate_drums() returns dict with exactly 5 keys matching LARSNET_STEM_KEYS."""
    loader = MidiModelLoader()

    mock_backend = MagicMock()
    mock_stems = {k: torch.zeros(2, 100) for k in LARSNET_STEM_KEYS}
    mock_backend._model.return_value = mock_stems
    loader._larsnet_backend = mock_backend

    audio = torch.zeros(2, 44100)

    with patch("soundfile.write"):
        result = loader.separate_drums(audio, job_id="test-job")

    assert set(result.keys()) == set(LARSNET_STEM_KEYS)
    assert len(result) == 5


def test_separate_drums_writes_to_correct_directory(tmp_path: pathlib.Path) -> None:
    """separate_drums() writes files under STEMS_DIR / 'drum_sub' / job_id."""
    from utils.paths import STEMS_DIR

    loader = MidiModelLoader()

    mock_backend = MagicMock()
    mock_stems = {k: torch.zeros(2, 100) for k in LARSNET_STEM_KEYS}
    mock_backend._model.return_value = mock_stems
    loader._larsnet_backend = mock_backend

    audio = torch.zeros(2, 44100)
    job_id = "dir-check-job"

    with patch("soundfile.write"):
        result = loader.separate_drums(audio, job_id=job_id)

    expected_dir = STEMS_DIR / "drum_sub" / job_id
    for stem_name, wav_path in result.items():
        assert wav_path.parent == expected_dir
        assert wav_path.name == f"{stem_name}.wav"


def test_separate_drums_handles_mono_input() -> None:
    """separate_drums() accepts 1D (mono) tensor without raising."""
    loader = MidiModelLoader()

    mock_backend = MagicMock()
    mock_stems = {k: torch.zeros(2, 100) for k in LARSNET_STEM_KEYS}
    mock_backend._model.return_value = mock_stems
    loader._larsnet_backend = mock_backend

    mono_audio = torch.zeros(44100)  # 1D mono

    with patch("soundfile.write"):
        result = loader.separate_drums(mono_audio, job_id="mono-job")

    assert len(result) == 5


# ---------------------------------------------------------------------------
# convert_drum_to_midi_with_larsnet — eviction sequencing (INFRA-03)
# ---------------------------------------------------------------------------


def test_eviction_before_adtof() -> None:
    """LarsNet is evicted before ADTOF loads in convert_drum_to_midi_with_larsnet()."""
    call_log: list[str] = []

    mock_larsnet = MagicMock()
    mock_larsnet._model.return_value = {k: torch.zeros(2, 100) for k in LARSNET_STEM_KEYS}
    mock_larsnet.evict.side_effect = lambda: call_log.append("evict_larsnet")

    mock_adtof = MagicMock()
    mock_adtof.predict.side_effect = lambda p: (call_log.append("adtof_predict"), [])[-1]

    loader = MidiModelLoader()
    loader._larsnet_backend = mock_larsnet

    def fake_ensure_adtof() -> MagicMock:
        call_log.append("ensure_adtof")
        return mock_adtof

    with patch.object(loader, "_ensure_adtof", side_effect=fake_ensure_adtof):
        with patch("soundfile.write"):
            loader.convert_drum_to_midi_with_larsnet(
                torch.zeros(2, 44100), "seq-test"
            )

    assert "evict_larsnet" in call_log, "evict_larsnet must be called"
    assert "ensure_adtof" in call_log, "_ensure_adtof must be called"
    evict_idx = call_log.index("evict_larsnet")
    adtof_idx = call_log.index("ensure_adtof")
    assert evict_idx < adtof_idx, (
        f"evict_larsnet ({evict_idx}) must precede ensure_adtof ({adtof_idx})"
    )


def test_convert_drum_returns_substems_and_events() -> None:
    """convert_drum_to_midi_with_larsnet() returns (dict, list)."""
    mock_larsnet = MagicMock()
    mock_larsnet._model.return_value = {k: torch.zeros(2, 100) for k in LARSNET_STEM_KEYS}

    mock_adtof = MagicMock()
    mock_adtof.predict.return_value = [(0.1, 0.2, 35, 100)]

    loader = MidiModelLoader()
    loader._larsnet_backend = mock_larsnet

    with patch.object(loader, "_ensure_adtof", return_value=mock_adtof):
        with patch("soundfile.write"):
            sub_stems, events = loader.convert_drum_to_midi_with_larsnet(
                torch.zeros(2, 44100), "return-type-test"
            )

    assert isinstance(sub_stems, dict)
    assert isinstance(events, list)
    assert set(sub_stems.keys()) == set(LARSNET_STEM_KEYS)
