"""Tests for the compose backend adapter layer.

Covers:
- claimed_gpu_indices() returns correct sets from embedded / remote / disabled
- /api/device compose block structure and values
- EmbeddedComposeBackend.sync_gpu_claim() stale-claim self-healing
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ---------------------------------------------------------------------------
# claimed_gpu_indices()
# ---------------------------------------------------------------------------

class TestClaimedGpuIndices:
    """claimed_gpu_indices() must be safe to call from any context."""

    def test_returns_empty_when_no_backend(self):
        """With no backend configured, returns an empty set."""
        import backend.compose_backend as cb
        orig = cb._backend
        try:
            cb._backend = None
            assert cb.claimed_gpu_indices() == set()
        finally:
            cb._backend = orig

    def test_returns_empty_for_remote_backend(self):
        """RemoteComposeBackend has no local GPU — always returns empty."""
        from backend.compose_backend.remote import RemoteComposeBackend
        import backend.compose_backend as cb
        orig = cb._backend
        try:
            cb._backend = RemoteComposeBackend(base_url="http://remote:8001")
            assert cb.claimed_gpu_indices() == set()
        finally:
            cb._backend = orig

    def test_returns_empty_when_embedded_idle(self):
        """EmbeddedComposeBackend with inactive GPU event returns empty set."""
        from backend.compose_backend.embedded import EmbeddedComposeBackend
        import backend.compose_backend as cb

        backend = EmbeddedComposeBackend.__new__(EmbeddedComposeBackend)
        backend._gpu_active = threading.Event()  # not set
        backend._gpu_indices = [0]

        orig = cb._backend
        try:
            cb._backend = backend
            assert cb.claimed_gpu_indices() == set()
        finally:
            cb._backend = orig

    def test_returns_indices_when_embedded_active(self):
        """EmbeddedComposeBackend with active GPU event returns its indices."""
        from backend.compose_backend.embedded import EmbeddedComposeBackend
        import backend.compose_backend as cb

        backend = EmbeddedComposeBackend.__new__(EmbeddedComposeBackend)
        backend._gpu_active = threading.Event()
        backend._gpu_active.set()
        backend._gpu_indices = [1]

        orig = cb._backend
        try:
            cb._backend = backend
            # Mock acestep_state to report "running"
            mock_status = {"status": "running"}
            with patch("backend.services.acestep_state.get_status", return_value=mock_status):
                result = cb.claimed_gpu_indices()
            assert result == {1}
        finally:
            cb._backend = orig

    def test_stale_claim_cleared_on_crashed_process(self):
        """If GPU event is set but AceStep is not running, claim is cleared."""
        from backend.compose_backend.embedded import EmbeddedComposeBackend
        import backend.compose_backend as cb

        backend = EmbeddedComposeBackend.__new__(EmbeddedComposeBackend)
        backend._gpu_active = threading.Event()
        backend._gpu_active.set()
        backend._gpu_indices = [0]

        orig = cb._backend
        try:
            cb._backend = backend
            mock_status = {"status": "crashed"}
            with patch("backend.services.acestep_state.get_status", return_value=mock_status):
                result = cb.claimed_gpu_indices()
            assert result == set(), "Stale claim should be cleared"
            assert not backend._gpu_active.is_set(), "Event should be cleared"
        finally:
            cb._backend = orig

    def test_multi_gpu_embedded(self):
        """EmbeddedComposeBackend with multiple GPUs returns all indices."""
        from backend.compose_backend.embedded import EmbeddedComposeBackend
        import backend.compose_backend as cb

        backend = EmbeddedComposeBackend.__new__(EmbeddedComposeBackend)
        backend._gpu_active = threading.Event()
        backend._gpu_active.set()
        backend._gpu_indices = [0, 1]

        orig = cb._backend
        try:
            cb._backend = backend
            mock_status = {"status": "running"}
            with patch("backend.services.acestep_state.get_status", return_value=mock_status):
                result = cb.claimed_gpu_indices()
            assert result == {0, 1}
        finally:
            cb._backend = orig


# ---------------------------------------------------------------------------
# /api/device compose block
# ---------------------------------------------------------------------------

class TestDeviceInfoComposeBlock:
    """The /api/device endpoint should always include a 'compose' key."""

    def _call_device_info(self):
        """Import and call device_info, fully mocking torch and compose."""
        from backend.api.system import device_info
        return device_info()

    def test_compose_block_disabled(self):
        """Compose block shows disabled when no backend configured."""
        import backend.compose_backend as cb
        from backend.compose_backend.protocol import BackendMode
        orig = cb._backend
        try:
            cb._backend = None
            result = self._call_device_info()
        finally:
            cb._backend = orig

        assert "compose" in result
        assert result["compose"]["mode"] == BackendMode.DISABLED.value
        assert result["compose"]["status"] == "disabled"

    def test_compose_block_embedded_idle(self):
        """Compose block shows embedded/starting when backend is alive but idle."""
        from backend.compose_backend.embedded import EmbeddedComposeBackend
        from backend.compose_backend.protocol import BackendMode, ComposeStatus
        import backend.compose_backend as cb

        backend = EmbeddedComposeBackend.__new__(EmbeddedComposeBackend)
        backend._gpu_active = threading.Event()
        backend._gpu_indices = []

        mock_status = ComposeStatus(
            mode=BackendMode.EMBEDDED,
            status="starting",
            models_loaded=False,
        )
        backend.get_status = AsyncMock(return_value=mock_status)

        orig = cb._backend
        try:
            cb._backend = backend
            result = self._call_device_info()
        finally:
            cb._backend = orig

        assert result["compose"]["mode"] == "embedded"
        assert result["compose"]["gpu_busy"] is False
        assert result["compose"]["claimed_gpu_indices"] == []

    def test_compose_block_embedded_active(self):
        """Compose block shows gpu_busy=True while generation is running."""
        from backend.compose_backend.embedded import EmbeddedComposeBackend
        from backend.compose_backend.protocol import BackendMode, ComposeStatus
        import backend.compose_backend as cb

        backend = EmbeddedComposeBackend.__new__(EmbeddedComposeBackend)
        backend._gpu_active = threading.Event()
        backend._gpu_active.set()
        backend._gpu_indices = [0]

        mock_status = ComposeStatus(
            mode=BackendMode.EMBEDDED,
            status="running",
            models_loaded=True,
        )
        backend.get_status = AsyncMock(return_value=mock_status)

        orig = cb._backend
        try:
            cb._backend = backend
            mock_state = {"status": "running"}
            with patch("backend.services.acestep_state.get_status", return_value=mock_state):
                result = self._call_device_info()
        finally:
            cb._backend = orig

        assert result["compose"]["gpu_busy"] is True
        assert result["compose"]["claimed_gpu_indices"] == [0]

    def test_compose_block_remote(self):
        """Compose block for remote mode shows no local GPU indices."""
        from backend.compose_backend.remote import RemoteComposeBackend
        from backend.compose_backend.protocol import BackendMode, ComposeStatus
        import backend.compose_backend as cb

        backend = RemoteComposeBackend.__new__(RemoteComposeBackend)
        backend._base_url = "http://remote:8001"
        mock_status = ComposeStatus(
            mode=BackendMode.REMOTE,
            status="running",
            models_loaded=True,
        )
        backend.get_status = AsyncMock(return_value=mock_status)

        orig = cb._backend
        try:
            cb._backend = backend
            result = self._call_device_info()
        finally:
            cb._backend = orig

        assert result["compose"]["mode"] == "remote"
        assert result["compose"]["claimed_gpu_indices"] == []
        assert result["compose"]["gpu_busy"] is False
