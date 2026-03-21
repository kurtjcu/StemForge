"""Tests for SessionStore.drum_sub_stem_paths field (INFRA-04)."""
import pathlib
import pytest
from backend.services.session_store import SessionStore


def test_drum_sub_stem_paths_default_empty():
    s = SessionStore()
    assert s.drum_sub_stem_paths == {}


def test_drum_sub_stem_paths_setter():
    s = SessionStore()
    paths = {
        "kick": pathlib.Path("/tmp/kick.wav"),
        "snare": pathlib.Path("/tmp/snare.wav"),
        "toms": pathlib.Path("/tmp/toms.wav"),
        "hihat": pathlib.Path("/tmp/hihat.wav"),
        "cymbals": pathlib.Path("/tmp/cymbals.wav"),
    }
    s.drum_sub_stem_paths = paths
    result = s.drum_sub_stem_paths
    assert len(result) == 5
    assert result["kick"] == pathlib.Path("/tmp/kick.wav")
    assert result["cymbals"] == pathlib.Path("/tmp/cymbals.wav")


def test_drum_sub_stem_paths_isolation_from_stem_paths():
    """Setting drum_sub_stem_paths must NOT affect stem_paths, and vice versa."""
    s = SessionStore()
    s.stem_paths = {"drums": pathlib.Path("/tmp/drums.wav")}
    s.drum_sub_stem_paths = {"kick": pathlib.Path("/tmp/kick.wav")}

    # stem_paths does NOT contain kick
    assert "kick" not in s.stem_paths
    # drum_sub_stem_paths does NOT contain drums
    assert "drums" not in s.drum_sub_stem_paths

    # Each has exactly the key it was assigned
    assert list(s.stem_paths.keys()) == ["drums"]
    assert list(s.drum_sub_stem_paths.keys()) == ["kick"]


def test_drum_sub_stem_paths_clear():
    s = SessionStore()
    s.drum_sub_stem_paths = {"kick": pathlib.Path("/tmp/kick.wav")}
    assert len(s.drum_sub_stem_paths) == 1
    s.clear()
    assert s.drum_sub_stem_paths == {}


def test_drum_sub_stem_paths_in_to_dict():
    s = SessionStore()
    s.drum_sub_stem_paths = {"kick": pathlib.Path("/tmp/kick.wav")}
    d = s.to_dict()
    assert "drum_sub_stem_paths" in d
    assert d["drum_sub_stem_paths"]["kick"] == "/tmp/kick.wav"


def test_add_drum_sub_stem_path():
    s = SessionStore()
    s.add_drum_sub_stem_path("kick", pathlib.Path("/tmp/kick.wav"))
    s.add_drum_sub_stem_path("snare", pathlib.Path("/tmp/snare.wav"))
    result = s.drum_sub_stem_paths
    assert len(result) == 2
    assert result["kick"] == pathlib.Path("/tmp/kick.wav")
    assert result["snare"] == pathlib.Path("/tmp/snare.wav")
