"""File loader panel for StemForge.

Renders a compact header bar that sits above all tabs.  The user
browses to an audio file via a custom FileBrowser modal; on selection
the file is validated, its metadata probed, a waveform preview is
loaded, and the path is stored in the shared app_state.
"""

import pathlib
import logging
from typing import Callable

import dearpygui.dearpygui as dpg

from utils.audio_io import SUPPORTED_EXTENSIONS as _AUDIO_EXT, probe
from gui.state import app_state, copy_to_clipboard, set_widget_text, get_widget_text, make_copy_callback
from gui.components.waveform_widget import WaveformWidget
from gui.components.file_browser import FileBrowser


# Re-export as a sorted tuple for callers that need an ordered sequence.
SUPPORTED_EXTENSIONS: tuple[str, ...] = tuple(sorted(_AUDIO_EXT))

log = logging.getLogger("stemforge.gui.loader")

# DearPyGUI tag constants
_TAG_PATH   = "loader_path"
_TAG_INFO   = "loader_info"


class LoaderPanel:
    """Compact audio-file loader rendered at the top of the main window."""

    _on_load_callbacks: list[Callable[[pathlib.Path], None]]

    def __init__(self) -> None:
        self._on_load_callbacks = []
        self._waveform = WaveformWidget("loader")
        self._browser: FileBrowser = FileBrowser(
            tag="loader_browser",
            callback=self._on_file_selected,
            extensions=_AUDIO_EXT,
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        """Add widgets to the currently active DearPyGUI parent context."""
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Browse",
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
                label="Clear",
                callback=self._on_clear,
                width=90,
                height=32,
            )
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("Remove the loaded file and reset all pipeline results.")

        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Copy",
                callback=make_copy_callback(_TAG_INFO),
                width=90,
            )
            dpg.add_text(default_value="", tag=_TAG_INFO, color=(160, 160, 160, 255))

        # Waveform preview
        self._waveform.build_ui()

    def build_file_browser(self) -> None:
        """Create the custom file browser modal at the top DearPyGUI level."""
        self._browser.build()

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
        set_widget_text(_TAG_INFO,"")
        self._waveform.clear()

    # ------------------------------------------------------------------
    # Callbacks (may be called from main thread only)
    # ------------------------------------------------------------------

    def _on_browse(self, sender, app_data, user_data) -> None:
        self._browser.show()

    def _on_clear(self, sender, app_data, user_data) -> None:
        self.reset()

    def _on_file_selected(self, path: pathlib.Path) -> None:
        """Receive a pathlib.Path from FileBrowser and validate it."""
        if path.suffix.lower() not in _AUDIO_EXT:
            set_widget_text(
                _TAG_INFO,
                f"Unsupported format '{path.suffix}'.  "
                f"Accepted: {', '.join(sorted(_AUDIO_EXT))}",
            )
            return

        try:
            info = probe(path)
        except Exception as exc:
            set_widget_text(_TAG_INFO,f"Could not read file: {exc}")
            log.error("LoaderPanel probe error: %s", exc)
            return

        ch_label = {1: "Mono", 2: "Stereo"}.get(info.channels, f"{info.channels}-ch")
        mins, secs = divmod(int(info.duration), 60)
        dpg.set_value(_TAG_PATH, str(path))
        set_widget_text(
            _TAG_INFO,
            f"{path.name}   |   {ch_label}   |   {info.sample_rate / 1_000:.1f} kHz   |   {mins}:{secs:02d}",
        )

        app_state.audio_path = path
        self._waveform.load_async(path)

        for cb in self._on_load_callbacks:
            try:
                cb(path)
            except Exception as exc:
                log.error("LoaderPanel callback error: %s", exc)

    # ------------------------------------------------------------------
    # Compatibility stubs
    # ------------------------------------------------------------------

    def browse(self) -> None:
        pass

    def _validate_extension(self, path: pathlib.Path) -> bool:
        return path.suffix.lower() in _AUDIO_EXT

    def _notify_listeners(self, path: pathlib.Path) -> None:
        pass

    def add_listener(self, callback: Callable[[pathlib.Path], None]) -> None:
        self.add_on_load_callback(callback)

    def build_file_dialog(self) -> None:
        """Compatibility shim — delegates to build_file_browser()."""
        self.build_file_browser()
