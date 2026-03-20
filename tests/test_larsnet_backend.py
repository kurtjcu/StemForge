"""Tests for LarsNetBackend — mock-based lifecycle tests.

Covers:
- Default state: _model=None, _device="cpu"
- _build_absolute_config(): reads config.yaml template, rewrites paths to absolute
- load() succeeds when checkpoints present (mock LarsNet constructor)
- load() raises ModelLoadError when checkpoints missing
- evict() sets _model to None, calls .cpu() on each sub-model
- evict() calls torch.cuda.empty_cache() when device starts with "cuda"
"""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_checkpoints(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create the 5 expected checkpoint files inside tmp_path/larsnet/."""
    stems = ("kick", "snare", "toms", "hihat", "cymbals")
    cache_dir = tmp_path / "larsnet"
    for stem in stems:
        stem_dir = cache_dir / stem
        stem_dir.mkdir(parents=True)
        (stem_dir / f"pretrained_{stem}_unet.pth").write_bytes(b"fake_weights")
    return cache_dir


# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------

class TestDefaultState:
    def test_model_is_none_at_init(self):
        from pipelines.larsnet_backend import LarsNetBackend
        backend = LarsNetBackend()
        assert backend._model is None

    def test_device_is_cpu_at_init(self):
        from pipelines.larsnet_backend import LarsNetBackend
        backend = LarsNetBackend()
        assert backend._device == "cpu"

    def test_is_loaded_false_at_init(self):
        from pipelines.larsnet_backend import LarsNetBackend
        backend = LarsNetBackend()
        assert backend.is_loaded is False


# ---------------------------------------------------------------------------
# _build_absolute_config
# ---------------------------------------------------------------------------

class TestBuildAbsoluteConfig:
    def test_returns_dict(self, tmp_path):
        from pipelines.larsnet_backend import _build_absolute_config
        result = _build_absolute_config(tmp_path)
        assert isinstance(result, dict)

    def test_inference_models_key_present(self, tmp_path):
        from pipelines.larsnet_backend import _build_absolute_config
        result = _build_absolute_config(tmp_path)
        assert "inference_models" in result

    def test_all_five_stems_present(self, tmp_path):
        from pipelines.larsnet_backend import _build_absolute_config
        result = _build_absolute_config(tmp_path)
        stems = set(result["inference_models"].keys())
        assert stems == {"kick", "snare", "toms", "hihat", "cymbals"}

    def test_paths_are_absolute(self, tmp_path):
        from pipelines.larsnet_backend import _build_absolute_config
        result = _build_absolute_config(tmp_path)
        for stem, path_str in result["inference_models"].items():
            assert pathlib.Path(path_str).is_absolute(), (
                f"Path for {stem} is not absolute: {path_str}"
            )

    def test_paths_contain_cache_dir(self, tmp_path):
        from pipelines.larsnet_backend import _build_absolute_config
        result = _build_absolute_config(tmp_path)
        for stem, path_str in result["inference_models"].items():
            assert str(tmp_path) in path_str, (
                f"Path for {stem} does not contain cache_dir: {path_str}"
            )

    def test_kick_path_pattern(self, tmp_path):
        from pipelines.larsnet_backend import _build_absolute_config
        result = _build_absolute_config(tmp_path)
        kick_path = pathlib.Path(result["inference_models"]["kick"])
        assert kick_path == tmp_path / "kick" / "pretrained_kick_unet.pth"

    def test_cymbals_path_pattern(self, tmp_path):
        from pipelines.larsnet_backend import _build_absolute_config
        result = _build_absolute_config(tmp_path)
        cymbals_path = pathlib.Path(result["inference_models"]["cymbals"])
        assert cymbals_path == tmp_path / "cymbals" / "pretrained_cymbals_unet.pth"


# ---------------------------------------------------------------------------
# load() — missing weights guard
# ---------------------------------------------------------------------------

class TestLoadMissingWeights:
    def test_raises_model_load_error_when_no_checkpoints(self, tmp_path):
        from pipelines.larsnet_backend import LarsNetBackend
        from utils.errors import ModelLoadError

        backend = LarsNetBackend()
        with patch("pipelines.larsnet_backend.get_model_cache_dir") as mock_cache:
            # Cache dir exists but no checkpoint files inside
            empty_cache = tmp_path / "larsnet"
            empty_cache.mkdir()
            mock_cache.return_value = empty_cache

            with pytest.raises(ModelLoadError):
                backend.load(device="cpu")

    def test_error_mentions_download_script(self, tmp_path):
        from pipelines.larsnet_backend import LarsNetBackend
        from utils.errors import ModelLoadError

        backend = LarsNetBackend()
        with patch("pipelines.larsnet_backend.get_model_cache_dir") as mock_cache:
            empty_cache = tmp_path / "larsnet"
            empty_cache.mkdir()
            mock_cache.return_value = empty_cache

            with pytest.raises(ModelLoadError) as exc_info:
                backend.load(device="cpu")
            assert "download_larsnet_weights.sh" in str(exc_info.value)

    def test_error_has_model_name_larsnet_drums(self, tmp_path):
        from pipelines.larsnet_backend import LarsNetBackend
        from utils.errors import ModelLoadError

        backend = LarsNetBackend()
        with patch("pipelines.larsnet_backend.get_model_cache_dir") as mock_cache:
            empty_cache = tmp_path / "larsnet"
            empty_cache.mkdir()
            mock_cache.return_value = empty_cache

            with pytest.raises(ModelLoadError) as exc_info:
                backend.load(device="cpu")
            assert exc_info.value.model_name == "larsnet-drums"


# ---------------------------------------------------------------------------
# load() — success path (mock LarsNet)
# ---------------------------------------------------------------------------

class TestLoadSuccess:
    def test_model_is_non_none_after_load(self, tmp_path):
        from pipelines.larsnet_backend import LarsNetBackend

        cache_dir = _make_fake_checkpoints(tmp_path)
        mock_larsnet_instance = MagicMock()
        mock_larsnet_instance.models = {"kick": MagicMock(), "snare": MagicMock()}

        with (
            patch("pipelines.larsnet_backend.get_model_cache_dir", return_value=cache_dir),
            patch("pipelines.larsnet_backend._write_absolute_config", return_value=cache_dir / "_larsnet_config.yaml"),
            patch.dict("sys.modules", {"larsnet": MagicMock(LarsNet=MagicMock(return_value=mock_larsnet_instance))}),
        ):
            backend = LarsNetBackend()
            backend.load(device="cpu")
            assert backend._model is not None

    def test_is_loaded_true_after_load(self, tmp_path):
        from pipelines.larsnet_backend import LarsNetBackend

        cache_dir = _make_fake_checkpoints(tmp_path)
        mock_larsnet_instance = MagicMock()
        mock_larsnet_instance.models = {}

        with (
            patch("pipelines.larsnet_backend.get_model_cache_dir", return_value=cache_dir),
            patch("pipelines.larsnet_backend._write_absolute_config", return_value=cache_dir / "_larsnet_config.yaml"),
            patch.dict("sys.modules", {"larsnet": MagicMock(LarsNet=MagicMock(return_value=mock_larsnet_instance))}),
        ):
            backend = LarsNetBackend()
            backend.load(device="cpu")
            assert backend.is_loaded is True

    def test_device_set_after_load(self, tmp_path):
        from pipelines.larsnet_backend import LarsNetBackend

        cache_dir = _make_fake_checkpoints(tmp_path)
        mock_larsnet_instance = MagicMock()
        mock_larsnet_instance.models = {}

        with (
            patch("pipelines.larsnet_backend.get_model_cache_dir", return_value=cache_dir),
            patch("pipelines.larsnet_backend._write_absolute_config", return_value=cache_dir / "_larsnet_config.yaml"),
            patch.dict("sys.modules", {"larsnet": MagicMock(LarsNet=MagicMock(return_value=mock_larsnet_instance))}),
        ):
            backend = LarsNetBackend()
            backend.load(device="cuda:0")
            assert backend._device == "cuda:0"


# ---------------------------------------------------------------------------
# evict()
# ---------------------------------------------------------------------------

class TestEvict:
    def _make_loaded_backend(self):
        """Return a backend with a mock model pre-set (no real load)."""
        from pipelines.larsnet_backend import LarsNetBackend

        backend = LarsNetBackend()
        mock_model = MagicMock()
        # 3 fake sub-models
        sub_kick = MagicMock()
        sub_snare = MagicMock()
        sub_toms = MagicMock()
        mock_model.models = {"kick": sub_kick, "snare": sub_snare, "toms": sub_toms}
        backend._model = mock_model
        backend._device = "cpu"
        return backend, mock_model

    def test_evict_sets_model_to_none(self):
        backend, _ = self._make_loaded_backend()
        with patch("torch.cuda.empty_cache"):
            backend.evict()
        assert backend._model is None

    def test_evict_calls_cpu_on_each_sub_model(self):
        backend, mock_model = self._make_loaded_backend()
        sub_models = list(mock_model.models.values())
        with patch("torch.cuda.empty_cache"):
            backend.evict()
        for sub in sub_models:
            sub.cpu.assert_called_once()

    def test_evict_does_not_call_empty_cache_on_cpu_device(self):
        backend, _ = self._make_loaded_backend()
        backend._device = "cpu"
        with patch("torch.cuda.empty_cache") as mock_empty_cache:
            backend.evict()
        mock_empty_cache.assert_not_called()

    def test_evict_calls_empty_cache_on_cuda_device(self):
        backend, _ = self._make_loaded_backend()
        backend._device = "cuda:0"
        with patch("torch.cuda.empty_cache") as mock_empty_cache:
            backend.evict()
        mock_empty_cache.assert_called_once()

    def test_evict_is_idempotent_when_already_unloaded(self):
        from pipelines.larsnet_backend import LarsNetBackend
        backend = LarsNetBackend()
        # Should not raise even though _model is None
        backend.evict()
        assert backend._model is None
