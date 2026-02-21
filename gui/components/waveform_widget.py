"""Reusable waveform visualizer and playback control widget for StemForge.

Each WaveformWidget renders:
  · Play and Stop buttons + a time label.
  · A DearPyGUI plot with a line_series (downsampled waveform) and a
    vline_series (playback cursor).
  · Click-to-seek: clicking anywhere on the plot jumps to that position.

Module-level globals
--------------------
_ALL_WIDGETS  — every live instance; tick_all() is called once per frame
                from the manual render loop in gui/app.py.
_active_widget — the single widget currently playing audio (exclusive).

Usage
-----
    widget = WaveformWidget("my_prefix")  # in __init__
    widget.build_ui()                     # inside dpg parent context
    widget.load_async(path)               # start background load
    # tick_all() is called each frame by app.py automatically
"""

import pathlib
import logging
import threading
import time

import numpy as np
import dearpygui.dearpygui as dpg

from utils.audio_io import read_audio


log = logging.getLogger("stemforge.gui.waveform_widget")

# Module-level registry and exclusive-playback pointer
_ALL_WIDGETS: list["WaveformWidget"] = []
_active_widget: "WaveformWidget | None" = None
_MAX_PLOT_POINTS = 2000


def tick_all() -> None:
    """Advance every registered widget by one render frame.

    Call this once per frame inside the manual DearPyGUI render loop.
    """
    for w in _ALL_WIDGETS:
        try:
            w.tick()
        except Exception as exc:
            log.debug("WaveformWidget.tick error: %s", exc)


class WaveformWidget:
    """Play/Stop buttons + waveform plot + animated playback cursor."""

    def __init__(self, tag_prefix: str) -> None:
        self._p = tag_prefix
        self._waveform: np.ndarray | None = None   # shape: (1, samples), float32
        self._sr: int = 44100
        self._duration: float = 0.0
        self._playing: bool = False
        self._play_start: float = 0.0    # wall-clock time when play started
        self._play_offset: float = 0.0   # seek offset in seconds
        _ALL_WIDGETS.append(self)

    # ------------------------------------------------------------------
    # Tag helper
    # ------------------------------------------------------------------

    def _tag(self, name: str) -> str:
        return f"wf_{self._p}_{name}"

    # ------------------------------------------------------------------
    # UI construction  (call inside the target dpg parent context)
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        """Add Play/Stop buttons and waveform plot to the active context."""
        with dpg.group(horizontal=False):
            # Control row
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="Play",
                    tag=self._tag("play_btn"),
                    callback=self._on_play,
                    width=70,
                    enabled=False,
                )
                dpg.add_button(
                    label="Stop",
                    tag=self._tag("stop_btn"),
                    callback=self._on_stop,
                    width=70,
                    enabled=False,
                )
                dpg.add_text(
                    "",
                    tag=self._tag("time"),
                    color=(160, 160, 160, 255),
                )

            # Waveform plot
            with dpg.plot(
                tag=self._tag("plot"),
                height=80,
                width=-1,
                no_title=True,
                no_menus=True,
                no_box_select=True,
            ):
                dpg.add_plot_axis(
                    dpg.mvXAxis,
                    tag=self._tag("xaxis"),
                    no_tick_marks=True,
                    no_tick_labels=True,
                )
                dpg.add_plot_axis(
                    dpg.mvYAxis,
                    tag=self._tag("yaxis"),
                    no_tick_marks=True,
                    no_tick_labels=True,
                )
                dpg.add_line_series(
                    [], [],
                    tag=self._tag("wave"),
                    parent=self._tag("yaxis"),
                )
                dpg.add_inf_line_series(
                    [0.0],
                    tag=self._tag("cursor"),
                    parent=self._tag("yaxis"),
                )

            # Click-to-seek handler bound to the plot
            with dpg.item_handler_registry(tag=self._tag("plot_hreg")):
                dpg.add_item_clicked_handler(callback=self._on_plot_click)
            dpg.bind_item_handler_registry(self._tag("plot"), self._tag("plot_hreg"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_async(self, path: pathlib.Path) -> None:
        """Clear current data and start loading *path* in a background thread."""
        self.clear()
        threading.Thread(target=self._load, args=(path,), daemon=True).start()

    def clear(self) -> None:
        """Stop playback, reset plot, and disable buttons."""
        self._stop()
        self._waveform = None
        self._duration = 0.0
        if dpg.does_item_exist(self._tag("wave")):
            dpg.set_value(self._tag("wave"), [[], []])
        if dpg.does_item_exist(self._tag("cursor")):
            dpg.set_value(self._tag("cursor"), [[0.0]])
        for btn in (self._tag("play_btn"), self._tag("stop_btn")):
            if dpg.does_item_exist(btn):
                dpg.configure_item(btn, enabled=False)
        if dpg.does_item_exist(self._tag("time")):
            dpg.set_value(self._tag("time"), "")

    def tick(self) -> None:
        """Advance playback cursor; called once per render frame by tick_all()."""
        if not self._playing:
            return
        pos = self._play_offset + (time.time() - self._play_start)
        if pos >= self._duration:
            self._stop()
            pos = 0.0

        if dpg.does_item_exist(self._tag("cursor")):
            dpg.set_value(self._tag("cursor"), [[pos]])
        if dpg.does_item_exist(self._tag("time")):
            m, s = divmod(int(pos), 60)
            dm, ds = divmod(int(self._duration), 60)
            dpg.set_value(self._tag("time"), f"{m}:{s:02d} / {dm}:{ds:02d}")

    # ------------------------------------------------------------------
    # Background loader
    # ------------------------------------------------------------------

    def _load(self, path: pathlib.Path) -> None:
        try:
            waveform, sr = read_audio(path, mono=True)
            samples = waveform.shape[1]
            duration = samples / sr

            step = max(1, samples // _MAX_PLOT_POINTS)
            ys = waveform[0, ::step].tolist()
            xs = np.linspace(0.0, duration, len(ys)).tolist()

            self._waveform = waveform
            self._sr = sr
            self._duration = duration

            if dpg.does_item_exist(self._tag("wave")):
                dpg.set_value(self._tag("wave"), [xs, ys])
            if dpg.does_item_exist(self._tag("xaxis")):
                dpg.set_axis_limits(self._tag("xaxis"), 0.0, duration)
            if dpg.does_item_exist(self._tag("yaxis")):
                dpg.set_axis_limits(self._tag("yaxis"), -1.0, 1.0)
            if dpg.does_item_exist(self._tag("play_btn")):
                dpg.configure_item(self._tag("play_btn"), enabled=True)
        except Exception as exc:
            log.error("WaveformWidget load error (%s): %s", path, exc)

    # ------------------------------------------------------------------
    # Button callbacks
    # ------------------------------------------------------------------

    def _on_play(self, sender, app_data, user_data) -> None:
        self._start_play(offset=0.0)

    def _on_stop(self, sender, app_data, user_data) -> None:
        self._stop()

    def _on_plot_click(self, sender, app_data, user_data) -> None:
        if self._waveform is None:
            return
        mouse_pos = dpg.get_plot_mouse_pos()
        seek = max(0.0, min(float(mouse_pos[0]), self._duration))
        self._start_play(offset=seek)

    # ------------------------------------------------------------------
    # Playback internals
    # ------------------------------------------------------------------

    def _start_play(self, offset: float = 0.0) -> None:
        global _active_widget
        # Stop whichever widget is currently playing (exclusive)
        if _active_widget is not None and _active_widget is not self:
            _active_widget._stop()
        _active_widget = self

        if self._waveform is None:
            return

        self._play_offset = offset
        self._play_start = time.time()
        self._playing = True

        if dpg.does_item_exist(self._tag("stop_btn")):
            dpg.configure_item(self._tag("stop_btn"), enabled=True)

        waveform = self._waveform
        sr = self._sr
        start_sample = int(offset * sr)

        def _audio() -> None:
            try:
                import sounddevice as sd
                sd.play(waveform[:, start_sample:].T, samplerate=sr)
            except Exception as exc:
                log.error("WaveformWidget audio playback error: %s", exc)

        threading.Thread(target=_audio, daemon=True).start()

    def _stop(self) -> None:
        global _active_widget
        if self._playing:
            self._playing = False
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
        if _active_widget is self:
            _active_widget = None
        if dpg.does_item_exist(self._tag("stop_btn")):
            dpg.configure_item(self._tag("stop_btn"), enabled=False)
