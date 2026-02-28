"""Thread-safe session state replacing gui/state.py AppState.

Holds the current audio file, stem paths, MIDI data (in-memory PrettyMIDI),
generation results, and mix state.  All access is lock-protected.
"""

from __future__ import annotations

import pathlib
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TrackState:
    track_id: str
    label: str
    source: str               # "audio" or "midi"
    path: pathlib.Path | None = None
    midi_data: Any = None     # PrettyMIDI object for MIDI tracks
    enabled: bool = True
    volume: float = 0.8
    program: int = 0          # GM program number
    is_drum: bool = False


class SessionStore:
    """Thread-safe session state singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._audio_path: pathlib.Path | None = None
        self._audio_info: dict[str, Any] | None = None
        self._stem_paths: dict[str, pathlib.Path] = {}
        self._merged_midi_data: Any = None           # PrettyMIDI
        self._stem_midi_data: dict[str, Any] = {}    # label → PrettyMIDI
        self._musicgen_path: pathlib.Path | None = None
        self._mix_path: pathlib.Path | None = None
        self._mix_tracks: list[TrackState] = []
        self._compose_paths: list[dict[str, Any]] = []

    # -- audio_path --
    @property
    def audio_path(self) -> pathlib.Path | None:
        with self._lock:
            return self._audio_path

    @audio_path.setter
    def audio_path(self, value: pathlib.Path | None) -> None:
        with self._lock:
            self._audio_path = value

    # -- audio_info --
    @property
    def audio_info(self) -> dict[str, Any] | None:
        with self._lock:
            return self._audio_info

    @audio_info.setter
    def audio_info(self, value: dict[str, Any] | None) -> None:
        with self._lock:
            self._audio_info = value

    # -- stem_paths --
    @property
    def stem_paths(self) -> dict[str, pathlib.Path]:
        with self._lock:
            return dict(self._stem_paths)

    @stem_paths.setter
    def stem_paths(self, value: dict[str, pathlib.Path]) -> None:
        with self._lock:
            self._stem_paths = dict(value)

    # -- merged_midi_data --
    @property
    def merged_midi_data(self) -> Any:
        with self._lock:
            return self._merged_midi_data

    @merged_midi_data.setter
    def merged_midi_data(self, value: Any) -> None:
        with self._lock:
            self._merged_midi_data = value

    # -- stem_midi_data --
    @property
    def stem_midi_data(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._stem_midi_data)

    @stem_midi_data.setter
    def stem_midi_data(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._stem_midi_data = dict(value)

    # -- musicgen_path --
    @property
    def musicgen_path(self) -> pathlib.Path | None:
        with self._lock:
            return self._musicgen_path

    @musicgen_path.setter
    def musicgen_path(self, value: pathlib.Path | None) -> None:
        with self._lock:
            self._musicgen_path = value

    # -- mix_path --
    @property
    def mix_path(self) -> pathlib.Path | None:
        with self._lock:
            return self._mix_path

    @mix_path.setter
    def mix_path(self, value: pathlib.Path | None) -> None:
        with self._lock:
            self._mix_path = value

    # -- mix_tracks --
    @property
    def mix_tracks(self) -> list[TrackState]:
        with self._lock:
            return list(self._mix_tracks)

    @mix_tracks.setter
    def mix_tracks(self, value: list[TrackState]) -> None:
        with self._lock:
            self._mix_tracks = list(value)

    # -- compose_paths --
    @property
    def compose_paths(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._compose_paths)

    @compose_paths.setter
    def compose_paths(self, value: list[dict[str, Any]]) -> None:
        with self._lock:
            self._compose_paths = list(value)

    def add_compose_path(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._compose_paths.append(entry)

    def add_track(self, track: TrackState) -> None:
        with self._lock:
            self._mix_tracks.append(track)

    def remove_track(self, track_id: str) -> bool:
        with self._lock:
            before = len(self._mix_tracks)
            self._mix_tracks = [t for t in self._mix_tracks if t.track_id != track_id]
            return len(self._mix_tracks) < before

    def get_track(self, track_id: str) -> TrackState | None:
        with self._lock:
            for t in self._mix_tracks:
                if t.track_id == track_id:
                    return t
            return None

    def clear(self) -> None:
        with self._lock:
            self._audio_path = None
            self._audio_info = None
            self._stem_paths = {}
            self._merged_midi_data = None
            self._stem_midi_data = {}
            self._musicgen_path = None
            self._mix_path = None
            self._mix_tracks = []
            self._compose_paths = []

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "audio_path": str(self._audio_path) if self._audio_path else None,
                "audio_info": self._audio_info,
                "stem_paths": {k: str(v) for k, v in self._stem_paths.items()},
                "has_merged_midi": self._merged_midi_data is not None,
                "stem_midi_labels": list(self._stem_midi_data.keys()),
                "musicgen_path": str(self._musicgen_path) if self._musicgen_path else None,
                "mix_path": str(self._mix_path) if self._mix_path else None,
                "compose_paths": list(self._compose_paths),
                "mix_tracks": [
                    {
                        "track_id": t.track_id,
                        "label": t.label,
                        "source": t.source,
                        "path": str(t.path) if t.path else None,
                        "enabled": t.enabled,
                        "volume": t.volume,
                        "program": t.program,
                        "is_drum": t.is_drum,
                    }
                    for t in self._mix_tracks
                ],
            }


# Module-level singleton
session = SessionStore()
