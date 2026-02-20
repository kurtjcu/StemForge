"""
Thread-safe application state for the StemForge GUI.

All panels import the module-level ``app_state`` singleton and read or
write it from background pipeline threads.  Every property access is
protected by a single :class:`threading.Lock`.
"""

import pathlib
import threading
from typing import Optional


class AppState:
    """Shared mutable state across all GUI panels.

    Attributes written by background threads and read by the main render
    thread (and vice-versa) are accessed only through the property
    accessors defined here so that the lock is always held.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._audio_path: Optional[pathlib.Path] = None
        self._stem_paths: dict[str, pathlib.Path] = {}
        self._midi_path: Optional[pathlib.Path] = None
        self._musicgen_path: Optional[pathlib.Path] = None

    # ------------------------------------------------------------------
    # audio_path
    # ------------------------------------------------------------------

    @property
    def audio_path(self) -> Optional[pathlib.Path]:
        with self._lock:
            return self._audio_path

    @audio_path.setter
    def audio_path(self, value: Optional[pathlib.Path]) -> None:
        with self._lock:
            self._audio_path = value

    # ------------------------------------------------------------------
    # stem_paths
    # ------------------------------------------------------------------

    @property
    def stem_paths(self) -> dict[str, pathlib.Path]:
        with self._lock:
            return dict(self._stem_paths)

    @stem_paths.setter
    def stem_paths(self, value: dict[str, pathlib.Path]) -> None:
        with self._lock:
            self._stem_paths = dict(value)

    # ------------------------------------------------------------------
    # midi_path
    # ------------------------------------------------------------------

    @property
    def midi_path(self) -> Optional[pathlib.Path]:
        with self._lock:
            return self._midi_path

    @midi_path.setter
    def midi_path(self, value: Optional[pathlib.Path]) -> None:
        with self._lock:
            self._midi_path = value

    # ------------------------------------------------------------------
    # musicgen_path
    # ------------------------------------------------------------------

    @property
    def musicgen_path(self) -> Optional[pathlib.Path]:
        with self._lock:
            return self._musicgen_path

    @musicgen_path.setter
    def musicgen_path(self, value: Optional[pathlib.Path]) -> None:
        with self._lock:
            self._musicgen_path = value

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Reset all pipeline outputs (does not clear audio_path)."""
        with self._lock:
            self._stem_paths = {}
            self._midi_path = None
            self._musicgen_path = None


# Module-level singleton — import this from every panel and callback.
app_state: AppState = AppState()
