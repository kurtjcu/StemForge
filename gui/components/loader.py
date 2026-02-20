"""
File loader panel for StemForge.

Renders a compact header bar that sits above all tabs.  The user
browses to an audio file via a DearPyGUI file dialog; on selection the
file is validated, its metadata probed, and the path stored in the
shared :data:`~gui.state.app_state`.
"""

import pathlib
import logging
from typing import Callable

import dearpygui.dearpygui as dpg

from utils.audio_io import SUPPORTED_EXTENSIONS as _AUDIO_EXT, probe
from gui.state import app_state


# Re-export as a sorted tuple for callers that need an ordered sequence.
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(sorted(_AUDIO_EXT))

log = logging.getLogger("stemforge.gui.loader")

# DearPyGUI tag constants
_TAG_PATH   = "loader_path"
_TAG_INFO   = "loader_info"
_TAG_DIALOG = "loader_file_dialog"


class LoaderPanel:
    """Compact audio-file loader rendered at the top of the main window."""

    _on_load_callbacks: list[Callable[[pathlib.Path], None]]

    def __init__(self) -> None:
        self._on_load_callbacks = []

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        """Add widgets to the currently active DearPyGUI parent context."""
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Browse…",
                callback=self._on_browse,
                width=90,
                height=32,
            )
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text(
                    "Open an audio file (WAV, FLAC, MP3, OGG, AIFF).\n"
                    "The file will be available to all pipelines."
                )

            dpg.add_input_text(
                tag=_TAG_PATH,
                hint="No file loaded",
                readonly=True,
                width=-140,
                height=32,
            )

            dpg.add_button(
                label="✕  Clear",
                callback=self._on_clear,
                width=90,
                height=32,
            )
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("Remove the loaded file and reset all pipeline results.")

        dpg.add_text("", tag=_TAG_INFO, color=(160, 160, 160, 255))

    def build_file_dialog(self) -> None:
        """Create the file dialog at the top DearPyGUI level (outside windows)."""
        with dpg.file_dialog(
            directory_selector=False,
            show=False,
            callback=self._on_file_selected,
            cancel_callback=lambda s, a: None,
            tag=_TAG_DIALOG,
            width=720,
            height=440,
            modal=True,
        ):
            dpg.add_file_extension(
                "Audio files{.wav,.flac,.mp3,.ogg,.aiff,.aif}",
                color=(100, 220, 100, 255),
            )
            dpg.add_file_extension(".*")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_on_load_callback(self, cb: Callable[[pathlib.Path], None]) -> None:
        """Register a callback invoked after a valid file is selected."""
        self._on_load_callbacks.append(cb)

    def get_path(self) -> pathlib.Path | None:
        return app_state.audio_path

    def reset(self) -> None:
        """Clear the loaded file."""
        app_state.audio_path = None
        app_state.clear()
        dpg.set_value(_TAG_PATH, "")
        dpg.set_value(_TAG_INFO, "")

    # ------------------------------------------------------------------
    # Callbacks (may be called from main thread only)
    # ------------------------------------------------------------------

    def _on_browse(self, sender, app_data, user_data) -> None:
        dpg.configure_item(_TAG_DIALOG, show=True)

    def _on_clear(self, sender, app_data, user_data) -> None:
        self.reset()

    def _on_file_selected(self, sender, app_data) -> None:
        path_str = app_data.get("file_path_name", "")
        if not path_str:
            return

        path = pathlib.Path(path_str)

        if path.suffix.lower() not in _AUDIO_EXT:
            dpg.set_value(
                _TAG_INFO,
                f"Unsupported format '{path.suffix}'.  "
                f"Accepted: {', '.join(sorted(_AUDIO_EXT))}",
            )
            return

        try:
            info = probe(path)
        except Exception as exc:
            dpg.set_value(_TAG_INFO, f"Could not read file: {exc}")
            log.error("LoaderPanel probe error: %s", exc)
            return

        ch_label = {1: "Mono", 2: "Stereo"}.get(info.channels, f"{info.channels}-ch")
        mins, secs = divmod(int(info.duration), 60)
        dpg.set_value(_TAG_PATH, str(path))
        dpg.set_value(
            _TAG_INFO,
            f"{path.name}   ·   {ch_label}   ·   {info.sample_rate / 1_000:.1f} kHz   ·   {mins}:{secs:02d}",
        )

        app_state.audio_path = path
        for cb in self._on_load_callbacks:
            try:
                cb(path)
            except Exception as exc:
                log.error("LoaderPanel callback error: %s", exc)

    # ------------------------------------------------------------------
    # Legacy stub methods (kept for compatibility with existing call sites)
    # ------------------------------------------------------------------

    def browse(self) -> None:
        pass

    def _validate_extension(self, path: pathlib.Path) -> bool:
        return path.suffix.lower() in _AUDIO_EXT

    def _notify_listeners(self, path: pathlib.Path) -> None:
        pass

    def add_listener(self, callback: Callable[[pathlib.Path], None]) -> None:
        self.add_on_load_callback(callback)
