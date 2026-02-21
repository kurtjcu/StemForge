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


# ---------------------------------------------------------------------------
# Widget text shadow-store
# ---------------------------------------------------------------------------

_widget_cache: dict[str, str] = {}


def set_widget_text(tag: str, text) -> None:
    """Update a DPG text widget and cache the value for reliable retrieval.

    Use instead of dpg.set_value() for every status and error message widget.
    get_widget_text() is guaranteed to return whatever was last passed here,
    regardless of which internal DPG field set_value/get_value actually
    address for mvText items.  Never raises.
    """
    import dearpygui.dearpygui as dpg
    s = "" if text is None else str(text)
    _widget_cache[tag] = s
    try:
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, s)
    except Exception:
        pass


def get_widget_text(tag: str) -> str:
    """Return the last string written to *tag* via set_widget_text."""
    return _widget_cache.get(tag, "")


def make_copy_callback(tag: str):
    """Return a 3-parameter DPG callback that copies get_widget_text(tag).

    Prefer this over ``lambda s, a, u, _k=tag: ...`` patterns: the lambda
    approach relies on DPG never passing a 4th positional argument, which
    some DPG versions do (passing ``None``), silently overriding the default
    and breaking the tag lookup.  A closure is unaffected by argument count.
    """
    def _cb(sender, app_data, user_data):
        copy_to_clipboard(get_widget_text(tag))
    return _cb


def copy_to_clipboard(text) -> None:
    """Copy *text* to the system clipboard.

    Accepts any value: None becomes an empty string; non-strings are
    converted with str() so dpg.get_value() results never cause a crash.

    Backend priority:
      1. wl-copy / xclip / xsel    — OS-level clipboard (most reliable).
      2. pyperclip.copy()          — cross-platform Python library.
      3. dpg.set_clipboard_text()  — DearPyGUI internal API (last resort).

    Never raises; silently does nothing when no backend succeeds.
    """
    import os
    import subprocess
    import dearpygui.dearpygui as dpg

    payload = "" if text is None else str(text)

    # Primary: OS subprocess — most reliable on Linux/Wayland/X11
    env = os.environ.copy()
    for cmd in (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["pbcopy"],
        ["clip.exe"],
    ):
        try:
            subprocess.run(
                cmd,
                input=payload.encode(),
                check=True,
                capture_output=True,
                env=env,
            )
            return
        except Exception:
            continue

    # Secondary: pyperclip
    try:
        import pyperclip
        pyperclip.copy(payload)
        return
    except Exception:
        pass

    # Last resort: DearPyGUI internal clipboard (may not reach OS clipboard)
    try:
        dpg.set_clipboard_text(payload)
    except Exception:
        pass
