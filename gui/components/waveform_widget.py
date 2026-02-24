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
from gui.ui_queue import schedule_ui
from gui.audio_player import audio_play, audio_stop


log = logging.getLogger("stemforge.gui.waveform_widget")

# Module-level registry and exclusive-playback pointer
_ALL_WIDGETS: list["WaveformWidget"] = []
_active_widget: "WaveformWidget | None" = None
_MAX_PLOT_POINTS = 2000

# ---------------------------------------------------------------------------
# Small font for plot tick labels
# ---------------------------------------------------------------------------

_PLOT_FONT_CANDIDATES = [
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
_plot_font: int | None = None
_plot_font_tried: bool = False

# Lazy-init border theme for WAV plots — green matching audio track label color
_wav_plot_theme: int | None = None
_wav_plot_theme_tried: bool = False


def _get_wav_plot_theme() -> "int | None":
    """Return a green theme for WAV plots, created once on first call.

    Green = (100, 170, 100), matching audio track label color in mix_panel.
    Three colours are set on the bound plot item:
      - mvThemeCol_Border       — outer 1-px border, always visible (full opacity)
      - mvThemeCol_FrameBgHovered — entire widget background on hover (matches border)
      - mvPlotCol_FrameBg       — fill of the frame area surrounding the waveform
                                   data canvas (axis-label margin) so the border
                                   colour wraps the whole visualisation.
    """
    global _wav_plot_theme, _wav_plot_theme_tried
    if _wav_plot_theme_tried:
        return _wav_plot_theme
    _wav_plot_theme_tried = True
    try:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Border,          (100, 170, 100, 255))
                dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,  (100, 170, 100,  80))
                dpg.add_theme_color(
                    dpg.mvPlotCol_FrameBg, (100, 170, 100, 50),
                    category=dpg.mvThemeCat_Plots,
                )
        _wav_plot_theme = t
    except Exception as exc:
        log.debug("Could not create WAV plot theme: %s", exc)
    return _wav_plot_theme


def _get_plot_font() -> int | None:
    """Return an 11-px font for plot tick labels, created once on first call.

    Returns None if no suitable font file is found (labels fall back to the
    global default font size instead).
    """
    global _plot_font, _plot_font_tried
    if _plot_font_tried:
        return _plot_font
    _plot_font_tried = True
    for candidate in _PLOT_FONT_CANDIDATES:
        p = pathlib.Path(candidate)
        if p.exists():
            try:
                with dpg.font_registry():
                    _plot_font = dpg.add_font(str(p), 11)
            except Exception as exc:
                log.debug("Could not load plot font at %s: %s", p, exc)
            return _plot_font
    return None


def tick_all() -> None:
    """Advance every registered widget by one render frame.

    Call this once per frame inside the manual DearPyGUI render loop.
    """
    for w in _ALL_WIDGETS:
        try:
            w.tick()
        except Exception as exc:
            log.debug("WaveformWidget.tick error: %s", exc)


def stop_all() -> None:
    """Stop playback on every registered widget (e.g. for a global Stop All button)."""
    for w in _ALL_WIDGETS:
        try:
            w._stop()
        except Exception:
            pass


class WaveformWidget:
    """Play/Stop buttons + waveform plot + animated playback cursor."""

    def __init__(self, tag_prefix: str, on_seek_callback=None) -> None:
        self._p = tag_prefix
        self._waveform: np.ndarray | None = None   # shape: (1, samples), float32
        self._sr: int = 44100
        self._duration: float = 0.0
        self._playing: bool = False
        self._play_start: float = 0.0    # wall-clock time when play started
        self._play_offset: float = 0.0   # seek offset in seconds
        # Optional external seek handler — used by Mix tab for synchronized display.
        # When set, clicking the plot calls this callback instead of starting play.
        self._on_seek_callback = on_seek_callback
        _ALL_WIDGETS.append(self)

    # ------------------------------------------------------------------
    # Tag helper
    # ------------------------------------------------------------------

    def _tag(self, name: str) -> str:
        return f"wf_{self._p}_{name}"

    # ------------------------------------------------------------------
    # UI construction  (call inside the target dpg parent context)
    # ------------------------------------------------------------------

    def build_ui(self, show_controls: bool = True, plot_height: int = 80) -> None:
        """Add Play/Stop buttons and waveform plot to the active context.

        Parameters
        ----------
        show_controls:
            When False, omit the Play/Stop/Rewind row — useful when the Mix
            tab owns playback and wants a display-only waveform.
        plot_height:
            Height of the waveform plot in pixels.
        """
        with dpg.group(horizontal=False):
            if show_controls:
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
                    dpg.add_button(
                        label="<<",
                        tag=self._tag("rewind_btn"),
                        callback=self._on_rewind,
                        width=50,
                        enabled=False,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text("Rewind to start")
                    dpg.add_text(
                        "",
                        tag=self._tag("time"),
                        color=(160, 160, 160, 255),
                    )

            # Waveform plot
            with dpg.plot(
                tag=self._tag("plot"),
                height=plot_height,
                width=-1,
                no_title=True,
                no_menus=True,
                no_box_select=True,
            ):
                dpg.add_plot_axis(
                    dpg.mvXAxis,
                    tag=self._tag("xaxis"),
                )
                dpg.set_axis_limits(self._tag("xaxis"), 0.0, 1.0)
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

            pass  # click-to-seek is handled in tick() via is_item_hovered

        # Bind small font to the plot so tick labels don't crowd the view
        _pf = _get_plot_font()
        if _pf is not None:
            dpg.bind_item_font(self._tag("plot"), _pf)

        # Style the cursor as a bright yellow line so it's visible on dark themes
        with dpg.theme() as cursor_theme:
            with dpg.theme_component(dpg.mvInfLineSeries):
                dpg.add_theme_color(dpg.mvPlotCol_Line, (255, 210, 0, 220), category=dpg.mvThemeCat_Plots)
                dpg.add_theme_style(dpg.mvPlotStyleVar_LineWeight, 2.0, category=dpg.mvThemeCat_Plots)
        dpg.bind_item_theme(self._tag("cursor"), cursor_theme)

        # Green border marks this as a WAV plot (matches audio track label color)
        _wpt = _get_wav_plot_theme()
        if _wpt is not None:
            dpg.bind_item_theme(self._tag("plot"), _wpt)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_cursor(self, pos: float) -> None:
        """Set cursor position externally without affecting playback state.

        Called by the Mix tab each frame to synchronise all track displays
        to the master clock, regardless of whether this widget is playing.
        """
        if dpg.does_item_exist(self._tag("cursor")):
            dpg.set_value(self._tag("cursor"), [[pos]])

    def load_async(self, path: pathlib.Path) -> None:
        """Clear current data and start loading *path* in a background thread."""
        self.clear()
        threading.Thread(target=self._load, args=(path,), daemon=True).start()

    def clear(self) -> None:
        """Stop playback, reset plot, and disable buttons."""
        self._stop()
        self._play_offset = 0.0
        self._waveform = None
        self._duration = 0.0
        if dpg.does_item_exist(self._tag("wave")):
            dpg.set_value(self._tag("wave"), [[], []])
        if dpg.does_item_exist(self._tag("cursor")):
            dpg.set_value(self._tag("cursor"), [[0.0]])
        if dpg.does_item_exist(self._tag("xaxis")):
            dpg.set_axis_limits(self._tag("xaxis"), 0.0, 1.0)
        for btn in (self._tag("play_btn"), self._tag("stop_btn"), self._tag("rewind_btn")):
            if dpg.does_item_exist(btn):
                dpg.configure_item(btn, enabled=False)
        if dpg.does_item_exist(self._tag("time")):
            dpg.set_value(self._tag("time"), "")

    def tick(self) -> None:
        """Advance playback cursor; called once per render frame by tick_all()."""
        # Click-to-seek: check while the plot is hovered in the render thread
        if (self._waveform is not None and self._duration > 0
                and dpg.does_item_exist(self._tag("plot"))
                and dpg.is_item_hovered(self._tag("plot"))
                and dpg.is_mouse_button_released(dpg.mvMouseButton_Left)):
            mouse_pos = dpg.get_plot_mouse_pos()
            seek = float(mouse_pos[0])
            if 0.0 <= seek <= self._duration:
                if self._on_seek_callback is not None:
                    # Display-only mode: delegate seek to the Mix panel master clock.
                    self._on_seek_callback(seek)
                else:
                    self._start_play(offset=seek)

        if not self._playing:
            return
        pos = self._play_offset + (time.time() - self._play_start)
        if pos >= self._duration:
            self._stop()
            self._play_offset = 0.0   # reset to start after natural end
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

            def _apply_ui():
                if dpg.does_item_exist(self._tag("wave")):
                    dpg.set_value(self._tag("wave"), [xs, ys])
                if dpg.does_item_exist(self._tag("xaxis")):
                    dpg.set_axis_limits(self._tag("xaxis"), 0.0, duration)
                if dpg.does_item_exist(self._tag("yaxis")):
                    dpg.set_axis_limits(self._tag("yaxis"), -1.0, 1.0)
                if dpg.does_item_exist(self._tag("play_btn")):
                    dpg.configure_item(self._tag("play_btn"), enabled=True)
                if dpg.does_item_exist(self._tag("rewind_btn")):
                    dpg.configure_item(self._tag("rewind_btn"), enabled=True)
            schedule_ui(_apply_ui)
        except Exception as exc:
            log.error("WaveformWidget load error (%s): %s", path, exc)

    # ------------------------------------------------------------------
    # Button callbacks
    # ------------------------------------------------------------------

    def _on_play(self, sender, app_data, user_data) -> None:
        self._start_play(offset=self._play_offset)

    def _on_stop(self, sender, app_data, user_data) -> None:
        self._stop()

    def _on_rewind(self, sender, app_data, user_data) -> None:
        """Reset playback position to the beginning."""
        if self._playing:
            self._start_play(offset=0.0)
        else:
            self._play_offset = 0.0
            if dpg.does_item_exist(self._tag("cursor")):
                dpg.set_value(self._tag("cursor"), [[0.0]])
            if dpg.does_item_exist(self._tag("time")) and self._duration > 0:
                dm, ds = divmod(int(self._duration), 60)
                dpg.set_value(self._tag("time"), f"0:00 / {dm}:{ds:02d}")

    # ------------------------------------------------------------------
    # Playback internals
    # ------------------------------------------------------------------

    def _start_play(self, offset: float = 0.0) -> None:
        global _active_widget

        # Stop any MIDI / mix playback (lazy import avoids circular dependency).
        from gui.components.midi_player_widget import stop_all_midi
        stop_all_midi()

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
        audio_play(waveform[:, start_sample:].T, samplerate=sr)

    def _stop(self) -> None:
        global _active_widget
        if self._playing:
            # Save position so Play resumes from here
            self._play_offset = min(
                self._play_offset + (time.time() - self._play_start),
                self._duration,
            )
            self._playing = False
            audio_stop()
        if _active_widget is self:
            _active_widget = None
        stop_btn = self._tag("stop_btn")
        schedule_ui(
            lambda _t=stop_btn: dpg.configure_item(_t, enabled=False)
            if dpg.does_item_exist(_t) else None
        )
