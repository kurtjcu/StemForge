"""Tests for MIDI API guards and sub-stems endpoint (GUARD-01, GUARD-02, SEP-03)."""
import pathlib
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.session_store import SessionStore, get_user_session


@pytest.fixture
def test_session():
    """Create a fresh SessionStore for each test."""
    session = SessionStore(user="test")
    return session


@pytest.fixture
def client(test_session):
    """FastAPI test client with overridden session dependency."""
    app.dependency_overrides[get_user_session] = lambda: test_session
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- SEP-03: GET /api/midi/sub-stems endpoint ---

def test_sub_stems_endpoint_empty(client, test_session):
    """GET /api/midi/sub-stems returns empty when no sub-stems in session."""
    resp = client.get("/api/midi/sub-stems")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sub_stems"] == {}
    assert data["count"] == 0


def test_sub_stems_endpoint_populated(client, test_session):
    """GET /api/midi/sub-stems returns 5 keys after storing sub-stems."""
    paths = {
        "kick": pathlib.Path("/tmp/kick.wav"),
        "snare": pathlib.Path("/tmp/snare.wav"),
        "toms": pathlib.Path("/tmp/toms.wav"),
        "hihat": pathlib.Path("/tmp/hihat.wav"),
        "cymbals": pathlib.Path("/tmp/cymbals.wav"),
    }
    test_session.drum_sub_stem_paths = paths
    resp = client.get("/api/midi/sub-stems")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 5
    assert "kick" in data["sub_stems"]
    assert "cymbals" in data["sub_stems"]
    assert data["sub_stems"]["kick"] == "/tmp/kick.wav"


# --- GUARD-01: LarsNet modes require drum stem in session ---

def test_guard01_no_drum_stem(client, test_session):
    """POST /api/midi/extract with larsnet mode and no drum stem returns 400."""
    # Set non-drum stems only
    test_session.stem_paths = {"vocals": pathlib.Path("/tmp/vocals.wav")}

    resp = client.post("/api/midi/extract", json={
        "stems": ["drums"],
        "drum_mode": "larsnet_adtof",
    })
    assert resp.status_code == 400
    assert "drum stem" in resp.json()["detail"].lower()


def test_guard01_larsnet_onset_also_guarded(client, test_session):
    """POST /api/midi/extract with larsnet_onset mode and no drum stem returns 400."""
    test_session.stem_paths = {"vocals": pathlib.Path("/tmp/vocals.wav")}

    resp = client.post("/api/midi/extract", json={
        "stems": ["drums"],
        "drum_mode": "larsnet_onset",
    })
    assert resp.status_code == 400
    assert "drum stem" in resp.json()["detail"].lower()


def test_guard01_adtof_only_skips_drum_guard(client, test_session):
    """POST /api/midi/extract with adtof_only mode does NOT trigger drum guard."""
    # Only vocals, no drum stem — adtof_only should not check for drum presence
    test_session.stem_paths = {"vocals": pathlib.Path("/tmp/vocals.wav")}

    resp = client.post("/api/midi/extract", json={
        "stems": ["vocals"],
        "drum_mode": "adtof_only",
    })
    # Should NOT be 400 about drum stems — may be 200 (job created) or other error
    # The key assertion: if it's 400, it must NOT be about drum stems
    if resp.status_code == 400:
        assert "drum stem" not in resp.json()["detail"].lower()


# --- GUARD-02: LarsNet weights must be present ---

def test_guard02_missing_weights(client, test_session):
    """POST /api/midi/extract with larsnet mode and missing weights returns 400."""
    test_session.stem_paths = {"drums": pathlib.Path("/tmp/drums.wav")}

    with patch("backend.api.midi.get_model_cache_dir") as mock_cache:
        mock_cache.return_value = pathlib.Path("/nonexistent/cache/larsnet")

        resp = client.post("/api/midi/extract", json={
            "stems": ["drums"],
            "drum_mode": "larsnet_adtof",
        })
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "weight" in detail.lower() or "download" in detail.lower()


def test_guard02_weights_present_passes(client, test_session, tmp_path):
    """POST /api/midi/extract with larsnet mode and weights present passes guards."""
    test_session.stem_paths = {"drums": pathlib.Path("/tmp/drums.wav")}

    # Create fake weight files
    cache_dir = tmp_path / "larsnet"
    cache_dir.mkdir()
    (cache_dir / "kick.pth").write_bytes(b"\x00")
    (cache_dir / "snare.pth").write_bytes(b"\x00")

    with patch("backend.api.midi.get_model_cache_dir") as mock_cache:
        mock_cache.return_value = cache_dir

        resp = client.post("/api/midi/extract", json={
            "stems": ["drums"],
            "drum_mode": "larsnet_adtof",
        })
        # Should pass the guards — will get 200 (job created) or fail later
        # for a different reason (actual pipeline not available), NOT 400 for weights
        if resp.status_code == 400:
            detail = resp.json()["detail"]
            assert "weight" not in detail.lower()
            assert "download" not in detail.lower()
