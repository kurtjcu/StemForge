"""Multi-track mixer panel for StemForge.

Each audio stem and each extracted MIDI file appears as a SEPARATE track
row, so the user can mix, e.g., the original bass audio OFF and a MIDI
bass with acoustic-bass instrument ON.

Track IDs (keys in _track_states)
----------------------------------
  "{label}:audio"   — audio stem from Separate tab or manual load
  "{label}:midi"    — MIDI file from MIDI tab or manual load

Tracks can be collapsed (OFF) to hide details and exclude from mixing,
or expanded (ON) to show controls and include in playback/render.

Inter-panel wiring (set up in gui/app.py)
-----------------------------------------
    _demucs.add_result_listener(_mix.notify_stems_ready)
    _midi.add_result_listener(_mix.notify_midi_ready)
    _mix.add_result_listener(_export.notify_mix_ready)
"""

from __future__ import annotations

import logging
import pathlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import dearpygui.dearpygui as dpg

from gui.state import app_state, set_widget_text, make_copy_callback
from gui.constants import _MIX_DIR
from gui.ui_queue import schedule_ui
from gui.components.file_browser import FileBrowser
from gui.components.demucs_panel import _STEM_LABEL
from gui.components.waveform_widget import WaveformWidget, _ALL_WIDGETS, _get_plot_font
from gui.components.midi_player_widget import (
    _ALL_MIDI_PLAYERS,
    find_soundfont,
    _STEM_DEFAULT_PROGRAM,
    _STEM_IS_DRUM,
)

log = logging.getLogger("stemforge.gui.mix_panel")

_P = "mix"

# Reverse map: display label → internal Demucs/Roformer stem name
_REVERSE_STEM_LABEL: dict[str, str] = {v: k for k, v in _STEM_LABEL.items()}

# GM instrument names, indexed by program number 0–127
try:
    import pretty_midi as _pm
    _GM_INSTRUMENTS: list[str] = [_pm.program_to_instrument_name(i) for i in range(128)]
except Exception:
    _GM_INSTRUMENTS = [f"Program {i}" for i in range(128)]


def _t(name: str) -> str:
    return f"{_P}_{name}"


def _safe(s: str) -> str:
    """Sanitise a string for use as part of a DPG tag."""
    return (
        s.replace(" ", "_")
         .replace("&", "and")
         .replace("/", "_")
         .replace(".", "_")
         .replace("(", "")
         .replace(")", "")
         .replace(":", "_")
    )


def _label_from_tid(tid: str) -> str:
    """Return the display label portion of a track ID (strip ':audio'/':midi')."""
    return tid.rsplit(":", 1)[0]


# ---------------------------------------------------------------------------
# ON / OFF toggle button themes  (lazy-initialised in build_ui)
# ---------------------------------------------------------------------------

_TOGGLE_ON_THEME: int | None = None
_TOGGLE_OFF_THEME: int | None = None


def _get_toggle_themes() -> tuple[int, int]:
    """Return (on_theme, off_theme) DPG theme tags, creating them if needed."""
    global _TOGGLE_ON_THEME, _TOGGLE_OFF_THEME
    if _TOGGLE_ON_THEME is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button,       (35, 110, 35, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (55, 150, 55, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (75, 180, 75, 255))
        _TOGGLE_ON_THEME = t
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button,       (55, 55, 65, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (75, 75, 90, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,  (65, 65, 80, 255))
        _TOGGLE_OFF_THEME = t
    return _TOGGLE_ON_THEME, _TOGGLE_OFF_THEME


# ---------------------------------------------------------------------------
# Per-track state
# ---------------------------------------------------------------------------

@dataclass
class TrackState:
    """Mutable per-track UI state — preserved across track-list rebuilds."""
    source: str = "audio"    # "audio" or "midi"
    enabled: bool = True
    volume: float = 1.0      # 0.0 – 1.0
    program: int = 0         # GM program number (MIDI tracks only)
    is_drum: bool = False
    manual_path: pathlib.Path | None = None


# ---------------------------------------------------------------------------
# MixPanel
# ---------------------------------------------------------------------------

class MixPanel:
    """Multi-track mixer: audio stems + MIDI-rendered accompaniment."""

    def __init__(self) -> None:
        # Stem paths received from upstream panels
        self._audio_stems: dict[str, pathlib.Path] = {}   # internal_name → path
        self._midi_stems_data: dict[str, Any] = {}        # display_label → PrettyMIDI

        # Manually loaded additional tracks
        self._manual_audio: dict[str, pathlib.Path] = {}  # display_label → path
        self._manual_midi: dict[str, pathlib.Path] = {}   # display_label → path (files)

        # Per-track state  key = track ID, e.g. "Bass:audio" or "Bass:midi"
        self._track_states: dict[str, TrackState] = {}

        # Per-track rendered audio cache: cache_key → (2, samples) float32
        self._track_audio_cache: dict[str, np.ndarray] = {}

        # Per-track WaveformWidget instances (audio tracks only), keyed by tid
        self._track_waveforms: dict[str, WaveformWidget] = {}

        # Master clock — drives "Play All" and per-track waveform cursors
        self._master_playing: bool = False
        self._master_start: float = 0.0
        self._master_offset: float = 0.0
        self._master_duration: float = 0.0
        self._master_audio: np.ndarray | None = None
        self._master_sr: int = 44100

        # Solo preview — single track plays while master cursor advances
        self._solo_playing: bool = False
        self._solo_tid: str = ""
        self._solo_start: float = 0.0
        self._solo_offset: float = 0.0
        self._solo_duration: float = 0.0

        # Rendered mix saved to disk (distinct from master_audio which is not saved)
        self._rendered_mix: np.ndarray | None = None
        self._mix_duration: float = 0.0
        self._mix_path: pathlib.Path | None = None
        self._mix_sr: int = 44100

        # Soundfont
        self._sf2_path: pathlib.Path | None = find_soundfont()

        # Result listeners — called with (mix_path,) after successful render
        self._result_listeners: list[Callable[[pathlib.Path], None]] = []

        # Background threads
        self._render_thread: threading.Thread | None = None
        self._preview_thread: threading.Thread | None = None

        # File browsers (built later at top DPG level)
        self._sf2_browser: FileBrowser | None = None
        self._save_browser: FileBrowser | None = None
        self._add_audio_browser: FileBrowser | None = None
        self._add_midi_browser: FileBrowser | None = None

        # Register in the duck-typed MIDI/mix list so stop_all_midi() /
        # tick_all_midi() include this panel alongside MidiPlayerWidget.
        _ALL_MIDI_PLAYERS.append(self)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        # ---- Master Playback (top) -------------------------------------
        dpg.add_text("Master Playback", color=(175, 175, 255, 255))
        dpg.add_spacer(height=4)

        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Play All",
                tag=_t("master_play_btn"),
                callback=self._on_master_play,
                width=82,
                height=32,
            )
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("Premix all ON tracks and play.")
            dpg.add_button(
                label="Stop All",
                tag=_t("master_stop_btn"),
                callback=self._on_master_stop,
                width=82,
                height=32,
            )
            dpg.add_button(
                label="<<",
                tag=_t("master_rewind_btn"),
                callback=self._on_master_rewind,
                width=44,
                height=32,
            )
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("Rewind all tracks to start")
            dpg.add_spacer(width=12)
            dpg.add_text("", tag=_t("master_time"), color=(160, 160, 160, 255))

        with dpg.plot(
            tag=_t("master_plot"),
            height=60,
            width=-1,
            no_menus=True,
            no_box_select=True,
        ):
            dpg.add_plot_axis(
                dpg.mvXAxis,
                tag=_t("master_xaxis"),
            )
            dpg.add_plot_axis(
                dpg.mvYAxis,
                tag=_t("master_yaxis"),
                no_tick_marks=True,
                no_tick_labels=True,
            )
            dpg.add_inf_line_series(
                [0.0],
                tag=_t("master_cursor"),
                parent=_t("master_yaxis"),
            )

        with dpg.theme() as _mct:
            with dpg.theme_component(dpg.mvInfLineSeries):
                dpg.add_theme_color(
                    dpg.mvPlotCol_Line, (255, 210, 0, 220),
                    category=dpg.mvThemeCat_Plots,
                )
                dpg.add_theme_style(
                    dpg.mvPlotStyleVar_LineWeight, 2.0,
                    category=dpg.mvThemeCat_Plots,
                )
        dpg.bind_item_theme(_t("master_cursor"), _mct)
        _pf = _get_plot_font()
        if _pf is not None:
            dpg.bind_item_font(_t("master_plot"), _pf)
        # Initialise axis so the plot shows 0 at the left (not centred at 0)
        dpg.set_axis_limits(_t("master_xaxis"), 0.0, 1.0)
        dpg.set_axis_limits(_t("master_yaxis"), -1.0, 1.0)

        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Copy",
                callback=make_copy_callback(_t("status")),
                width=46,
            )
            dpg.add_text(
                "Ready",
                tag=_t("status"),
                color=(160, 160, 160, 255),
                wrap=600,
            )

        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # ---- Compact soundfont row ------------------------------------
        with dpg.group(horizontal=True):
            dpg.add_text("Soundfont:", color=(140, 140, 180, 255))
            dpg.add_spacer(width=6)
            sf2_str = (
                str(self._sf2_path) if self._sf2_path
                else "(none — install fluid-soundfont-gm)"
            )
            dpg.add_input_text(
                tag=_t("sf2_path"),
                default_value=sf2_str,
                readonly=True,
                width=400,
            )
            dpg.add_spacer(width=6)
            dpg.add_button(
                label="Browse",
                callback=self._on_sf2_browse,
                height=22,
            )

        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # ---- Track list header + add-file buttons --------------------
        with dpg.group(horizontal=True):
            dpg.add_text("Tracks", color=(175, 175, 255, 255))
            dpg.add_spacer(width=10)
            dpg.add_button(
                label="+ Audio file",
                callback=self._on_add_audio_click,
                height=22,
            )
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("Load an audio file (wav/flac/mp3) as an additional track.")
            dpg.add_spacer(width=4)
            dpg.add_button(
                label="+ MIDI file",
                callback=self._on_add_midi_click,
                height=22,
            )
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text("Load a MIDI file as an additional track.")

        dpg.add_spacer(height=4)
        dpg.add_text(
            "Run Separate and/or Extract MIDI first, or load files above.",
            tag=_t("tracks_empty"),
            color=(120, 120, 140, 255),
            wrap=780,
        )

        # ---- Scrollable track list (leaves room for render below) ----
        with dpg.child_window(
            tag=_t("tracks_scroll"),
            height=-180,
            border=False,
        ):
            with dpg.group(tag=_t("tracks_group")):
                pass

        # ---- Render section (bottom, pinned) -------------------------
        dpg.add_separator()
        dpg.add_spacer(height=6)

        dpg.add_button(
            label="  Render Mix to FLAC  ",
            tag=_t("render_btn"),
            callback=self._on_render_click,
            width=-1,
            height=40,
        )
        with dpg.tooltip(dpg.last_item()):
            dpg.add_text(
                "Mix all ON tracks and save as a FLAC file.\n"
                "Also available on the Export tab."
            )

        dpg.add_spacer(height=4)
        dpg.add_text("", tag=_t("result_duration"), color=(220, 220, 220, 255))
        with dpg.group(horizontal=True):
            dpg.add_text("File:", color=(140, 140, 180, 255))
            dpg.add_text(
                "(none)",
                tag=_t("result_file"),
                color=(140, 140, 140, 255),
                wrap=600,
            )
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_button(
                label="Copy path",
                callback=make_copy_callback(_t("result_file")),
                width=80,
            )
            dpg.add_button(
                label="Save as",
                tag=_t("save_btn"),
                callback=self._on_save_as,
                width=80,
                enabled=False,
            )
        dpg.add_spacer(height=4)
        dpg.add_text(
            "Note: vocal MIDI uses GM choir — no lyrics.",
            color=(100, 100, 120, 255),
        )

    def build_save_dialog(self) -> None:
        """Create all file browsers at the top DPG level (outside all windows)."""
        self._sf2_browser = FileBrowser(
            tag="mix_sf2_browser",
            callback=self._on_sf2_selected,
            extensions=frozenset({".sf2", ".sf3"}),
            mode="open",
        )
        self._sf2_browser.build()

        self._save_browser = FileBrowser(
            tag="mix_save_browser",
            callback=self._on_save_selected,
            extensions=frozenset({".flac", ".wav", ".ogg"}),
            mode="save",
        )
        self._save_browser.build()

        self._add_audio_browser = FileBrowser(
            tag="mix_add_audio_browser",
            callback=self._on_audio_file_selected,
            extensions=frozenset({".wav", ".flac", ".mp3", ".ogg", ".aiff", ".aif"}),
            mode="open",
        )
        self._add_audio_browser.build()

        self._add_midi_browser = FileBrowser(
            tag="mix_add_midi_browser",
            callback=self._on_midi_file_selected,
            extensions=frozenset({".mid", ".midi"}),
            mode="open",
        )
        self._add_midi_browser.build()

    def add_result_listener(self, cb: Callable[[pathlib.Path], None]) -> None:
        """Register a callback fired with the mix FLAC path after render."""
        self._result_listeners.append(cb)

    # ------------------------------------------------------------------
    # Inter-panel notifications
    # ------------------------------------------------------------------

    def notify_stems_ready(self, stem_paths: dict[str, pathlib.Path]) -> None:
        """Called by Separate panel after successful separation (may be bg thread)."""
        self._audio_stems = dict(stem_paths)
        schedule_ui(self._rebuild_tracks)

    def notify_midi_ready(
        self,
        midi_path: pathlib.Path,
        stem_midi_data: dict[str, Any],
    ) -> None:
        """Called by MIDI panel after successful extraction (may be bg thread)."""
        self._midi_stems_data = dict(stem_midi_data)
        schedule_ui(self._rebuild_tracks)

    # ------------------------------------------------------------------
    # Manual file loading
    # ------------------------------------------------------------------

    def _on_add_audio_click(self, sender, app_data, user_data) -> None:
        if self._add_audio_browser:
            self._add_audio_browser.show()

    def _on_add_midi_click(self, sender, app_data, user_data) -> None:
        if self._add_midi_browser:
            self._add_midi_browser.show()

    def _on_audio_file_selected(self, path: pathlib.Path) -> None:
        self._manual_audio[path.stem] = path
        self._rebuild_tracks()

    def _on_midi_file_selected(self, path: pathlib.Path) -> None:
        self._manual_midi[path.stem] = path
        self._rebuild_tracks()

    # ------------------------------------------------------------------
    # Track list management
    # ------------------------------------------------------------------

    def _all_audio(self) -> dict[str, pathlib.Path]:
        """Merged audio sources: pipeline stems + manual loads."""
        result: dict[str, pathlib.Path] = {}
        for internal, path in self._audio_stems.items():
            result[_STEM_LABEL.get(internal, internal)] = path
        result.update(self._manual_audio)
        return result

    def _all_midi(self) -> dict[str, Any]:
        """Merged MIDI sources: in-memory PrettyMIDI objects + manual file paths.

        Values are either PrettyMIDI objects (from pipeline) or pathlib.Path
        objects (from manual file loads).  _render_single_track dispatches on
        type.
        """
        result: dict[str, Any] = dict(self._midi_stems_data)
        result.update(self._manual_midi)   # manual file paths override if same label
        return result

    def _rebuild_tracks(self) -> None:
        """Recompute the track list and refresh the UI.

        Each audio file and each MIDI file become a SEPARATE track row with
        its own track ID ("{label}:audio" or "{label}:midi").  This lets the
        user keep both the original bass audio and a MIDI bass side-by-side.
        """
        all_audio = self._all_audio()
        all_midi = self._all_midi()

        # Build wanted track IDs in display order (audio first, then MIDI)
        wanted_tids: list[str] = []
        for label in all_audio:
            wanted_tids.append(f"{label}:audio")
        for label in all_midi:
            wanted_tids.append(f"{label}:midi")

        # Build new_states: preserve existing settings, create defaults for new.
        new_states: dict[str, TrackState] = {}
        for tid in wanted_tids:
            if tid in self._track_states:
                new_states[tid] = self._track_states[tid]
            else:
                label = _label_from_tid(tid)
                source = tid.rsplit(":", 1)[1]  # "audio" or "midi"

                manual_path: pathlib.Path | None = None
                if source == "audio" and label in self._manual_audio:
                    manual_path = self._manual_audio[label]
                elif source == "midi" and label in self._manual_midi:
                    manual_path = self._manual_midi[label]

                new_states[tid] = TrackState(
                    source=source,
                    enabled=True,
                    volume=1.0,
                    program=_STEM_DEFAULT_PROGRAM.get(label, 0),
                    is_drum=_STEM_IS_DRUM.get(label, False),
                    manual_path=manual_path,
                )

        self._track_states = new_states
        self._update_tracks_ui()

    def _clear_track_waveforms(self) -> None:
        """Remove stale WaveformWidget instances from the global registry."""
        for wf in list(self._track_waveforms.values()):
            if wf in _ALL_WIDGETS:
                _ALL_WIDGETS.remove(wf)
        self._track_waveforms.clear()

    def _get_audio_path(self, label: str, state: TrackState) -> pathlib.Path | None:
        if state.manual_path and state.source == "audio":
            return state.manual_path
        internal = _REVERSE_STEM_LABEL.get(label)
        if internal and internal in self._audio_stems:
            return self._audio_stems[internal]
        return self._all_audio().get(label)

    def _update_tracks_ui(self) -> None:
        """Delete and recreate track rows inside tracks_group."""
        group_tag = _t("tracks_group")
        empty_tag = _t("tracks_empty")

        if not dpg.does_item_exist(group_tag):
            return  # build_ui() not yet called

        self._clear_track_waveforms()
        dpg.delete_item(group_tag, children_only=True)

        has_tracks = bool(self._track_states)
        if dpg.does_item_exist(empty_tag):
            dpg.configure_item(empty_tag, show=not has_tracks)

        on_theme, off_theme = _get_toggle_themes()

        for tid, state in self._track_states.items():
            label = _label_from_tid(tid)
            source = state.source
            safe = _safe(tid)
            expanded = state.enabled

            src_color = (100, 170, 100, 255) if source == "audio" else (150, 130, 220, 255)

            with dpg.group(tag=_t(f"track_{safe}_group"), parent=group_tag):

                # --- Header row: ON/OFF · label (always visible) -------
                with dpg.group(horizontal=True):
                    toggle_tag = _t(f"track_{safe}_toggle")
                    dpg.add_button(
                        label="ON" if expanded else "OFF",
                        tag=toggle_tag,
                        callback=self._on_track_toggle,
                        user_data=tid,
                        width=44,
                    )
                    dpg.bind_item_theme(
                        toggle_tag,
                        on_theme if expanded else off_theme,
                    )

                    dpg.add_text(
                        f"  {label}",
                        color=src_color,
                    )

                # --- Expandable detail (hidden when collapsed) ---------
                with dpg.group(
                    tag=_t(f"track_{safe}_detail"),
                    show=expanded,
                ):
                    # Controls row: Play · Stop · volume knob
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            label="Play",
                            tag=_t(f"track_{safe}_preview_btn"),
                            callback=self._on_track_preview,
                            user_data=tid,
                            width=50,
                        )
                        dpg.add_button(
                            label="Stop",
                            tag=_t(f"track_{safe}_stop_btn"),
                            callback=self._on_track_stop,
                            user_data=tid,
                            width=50,
                        )
                        dpg.add_spacer(width=8)
                        dpg.add_knob_float(
                            label="Vol",
                            tag=_t(f"track_{safe}_vol"),
                            default_value=state.volume * 10.0,
                            min_value=0.0,
                            max_value=10.0,
                            callback=self._on_track_volume,
                            user_data=tid,
                        )

                    # Instrument selector (MIDI tracks only)
                    if source == "midi" and not state.is_drum:
                        default_name = (
                            _GM_INSTRUMENTS[state.program]
                            if 0 <= state.program < len(_GM_INSTRUMENTS)
                            else _GM_INSTRUMENTS[0]
                        )
                        dpg.add_combo(
                            items=_GM_INSTRUMENTS,
                            default_value=default_name,
                            tag=_t(f"track_{safe}_program"),
                            width=-1,
                            callback=self._on_track_program,
                            user_data=tid,
                        )
                    elif source == "midi" and state.is_drum:
                        dpg.add_input_text(
                            default_value="Standard Drums",
                            readonly=True,
                            width=-1,
                        )

                    # Waveform (audio) or cursor bar (MIDI)
                    if source == "audio":
                        wf = WaveformWidget(
                            tag_prefix=f"mix_{safe}",
                            on_seek_callback=self._on_waveform_seek,
                        )
                        self._track_waveforms[tid] = wf
                        wf.build_ui(show_controls=False, plot_height=50)
                        path = self._get_audio_path(label, state)
                        if path:
                            wf.load_async(path)
                    else:
                        # MIDI track: plot with inf_line cursor
                        all_midi = self._all_midi()
                        midi_source = all_midi.get(label)
                        midi_dur = self._master_duration
                        if midi_dur == 0.0 and midi_source is not None:
                            try:
                                if not isinstance(midi_source, pathlib.Path):
                                    midi_dur = float(midi_source.get_end_time())
                            except Exception:
                                pass
                        if midi_dur == 0.0:
                            midi_dur = 300.0  # fallback
                        xaxis_tag = _t(f"track_{safe}_xaxis")
                        yaxis_tag = _t(f"track_{safe}_yaxis")
                        cursor_tag = _t(f"track_{safe}_cursor")
                        with dpg.plot(
                            tag=_t(f"track_{safe}_plot"),
                            height=65,
                            width=-1,
                            no_menus=True,
                            no_box_select=True,
                        ):
                            dpg.add_plot_axis(
                                dpg.mvXAxis,
                                tag=xaxis_tag,
                            )
                            dpg.add_plot_axis(
                                dpg.mvYAxis,
                                tag=yaxis_tag,
                                no_tick_marks=True,
                                no_tick_labels=True,
                            )
                            dpg.add_inf_line_series(
                                [0.0],
                                tag=cursor_tag,
                                parent=yaxis_tag,
                            )
                        with dpg.theme() as _ct:
                            with dpg.theme_component(dpg.mvInfLineSeries):
                                dpg.add_theme_color(
                                    dpg.mvPlotCol_Line, (255, 210, 0, 220),
                                    category=dpg.mvThemeCat_Plots,
                                )
                                dpg.add_theme_style(
                                    dpg.mvPlotStyleVar_LineWeight, 2.0,
                                    category=dpg.mvThemeCat_Plots,
                                )
                        dpg.bind_item_theme(cursor_tag, _ct)
                        _pf = _get_plot_font()
                        if _pf is not None:
                            dpg.bind_item_font(_t(f"track_{safe}_plot"), _pf)
                        dpg.set_axis_limits(xaxis_tag, 0.0, midi_dur)
                        dpg.set_axis_limits(yaxis_tag, -1.0, 1.0)
                        dpg.add_text(
                            "0:00",
                            tag=_t(f"track_{safe}_timetext"),
                            color=(140, 140, 160, 255),
                        )

                dpg.add_spacer(height=4)
                dpg.add_separator()
                dpg.add_spacer(height=4)

    # ------------------------------------------------------------------
    # Callbacks — per-track controls
    # ------------------------------------------------------------------

    def _on_track_toggle(self, sender, app_data, user_data) -> None:
        tid = user_data
        state = self._track_states.get(tid)
        if not state:
            return
        state.enabled = not state.enabled
        expanded = state.enabled
        on_theme, off_theme = _get_toggle_themes()
        safe = _safe(tid)

        toggle_tag = _t(f"track_{safe}_toggle")
        if dpg.does_item_exist(toggle_tag):
            dpg.configure_item(toggle_tag, label="ON" if expanded else "OFF")
            dpg.bind_item_theme(toggle_tag, on_theme if expanded else off_theme)

        detail_tag = _t(f"track_{safe}_detail")
        if dpg.does_item_exist(detail_tag):
            dpg.configure_item(detail_tag, show=expanded)

        self._master_audio = None  # stale — track set changed

    def _on_track_volume(self, sender, app_data, user_data) -> None:
        tid = user_data
        if tid in self._track_states:
            self._track_states[tid].volume = float(app_data) / 10.0
        self._master_audio = None

    def _on_track_program(self, sender, app_data, user_data) -> None:
        tid = user_data
        if tid in self._track_states and app_data in _GM_INSTRUMENTS:
            self._track_states[tid].program = _GM_INSTRUMENTS.index(app_data)
        # Invalidate MIDI cache for this track.
        stale = [k for k in self._track_audio_cache if k.startswith(f"{tid}:")]
        for k in stale:
            del self._track_audio_cache[k]
        self._master_audio = None

    def _on_track_preview(self, sender, app_data, user_data) -> None:
        """Start solo preview of a single track."""
        tid = user_data
        state = self._track_states.get(tid)
        if not state:
            return
        self._preview_thread = threading.Thread(
            target=self._do_solo_preview,
            args=(tid, state),
            daemon=True,
        )
        self._preview_thread.start()

    def _on_track_stop(self, sender, app_data, user_data) -> None:
        """Stop solo preview or master playback."""
        self._stop_solo()
        self._stop_master()
        self._set_status("Stopped")


    # ------------------------------------------------------------------
    # Callbacks — master transport
    # ------------------------------------------------------------------

    def _on_master_play(self, sender, app_data, user_data) -> None:
        if self._render_thread and self._render_thread.is_alive():
            return
        self._render_thread = threading.Thread(
            target=self._prepare_master_and_play,
            daemon=True,
        )
        self._render_thread.start()

    def _on_master_stop(self, sender, app_data, user_data) -> None:
        self._stop_master()
        self._stop_solo()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        self._set_status("Stopped")

    def _on_master_rewind(self, sender, app_data, user_data) -> None:
        was_playing = self._master_playing or self._solo_playing
        self._stop_master()
        self._stop_solo()
        self._master_offset = 0.0
        self._update_all_cursors(0.0, self._master_duration)
        if was_playing and self._master_audio is not None:
            self._start_master_play(0.0)

    # ------------------------------------------------------------------
    # Waveform click-to-seek
    # ------------------------------------------------------------------

    def _on_waveform_seek(self, pos: float) -> None:
        """Seek master clock when user clicks a track waveform."""
        if self._master_duration > 0:
            pos = min(pos, self._master_duration)
        was_playing = self._master_playing
        self._stop_master()
        self._stop_solo()
        self._master_offset = pos
        self._update_all_cursors(pos, self._master_duration)
        if was_playing and self._master_audio is not None:
            self._start_master_play(pos)

    # ------------------------------------------------------------------
    # Solo preview
    # ------------------------------------------------------------------

    def _do_solo_preview(self, tid: str, state: TrackState) -> None:
        """Background worker: render (if needed) then solo-play one track."""
        self._stop_master()
        self._stop_solo()

        from gui.components.waveform_widget import stop_all as _stop_waveforms
        _stop_waveforms()
        for w in _ALL_MIDI_PLAYERS:
            if w is not self:
                try:
                    w._stop()
                except Exception:
                    pass

        label = _label_from_tid(tid)
        cache_key = self._make_cache_key(tid, state)
        if cache_key not in self._track_audio_cache:
            self._set_status(f"Rendering: {label}…")
            audio = self._render_single_track(label, state)
            if audio is None:
                self._set_status(f"Failed to render: {label}")
                return
            self._track_audio_cache[cache_key] = audio

        audio = self._track_audio_cache[cache_key]
        duration = audio.shape[1] / self._mix_sr

        if self._master_duration == 0.0:
            self._master_duration = duration

        self._solo_tid = tid
        self._solo_duration = duration
        self._solo_offset = 0.0
        self._solo_start = time.time()
        self._solo_playing = True
        self._set_status(f"Solo: {label} ({state.source})")

        try:
            import sounddevice as sd
            sd.play(audio.T, samplerate=self._mix_sr)
        except Exception as exc:
            log.error("MixPanel solo preview error: %s", exc)
            self._solo_playing = False

    def _stop_solo(self) -> None:
        if self._solo_playing:
            elapsed = time.time() - self._solo_start
            self._solo_offset = min(self._solo_offset + elapsed, self._solo_duration)
            self._solo_playing = False
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Master Play All
    # ------------------------------------------------------------------

    def _prepare_master_and_play(self) -> None:
        """Background worker: render all ON tracks, mix, then play."""
        self._stop_master()
        self._stop_solo()

        from gui.components.waveform_widget import stop_all as _stop_waveforms
        _stop_waveforms()
        for w in _ALL_MIDI_PLAYERS:
            if w is not self:
                try:
                    w._stop()
                except Exception:
                    pass

        enabled = {tid: s for tid, s in self._track_states.items() if s.enabled}
        if not enabled:
            self._set_status("No ON tracks to play.")
            return

        self._set_status("Preparing mix…")
        tracks: list[np.ndarray] = []
        volumes: list[float] = []
        n = len(enabled)

        for i, (tid, state) in enumerate(enabled.items()):
            label = _label_from_tid(tid)
            self._set_status(f"Loading {i + 1}/{n}: {label} ({state.source})")
            cache_key = self._make_cache_key(tid, state)
            if cache_key not in self._track_audio_cache:
                audio = self._render_single_track(label, state)
                if audio is None:
                    continue
                self._track_audio_cache[cache_key] = audio
            tracks.append(self._track_audio_cache[cache_key])
            volumes.append(state.volume)

        if not tracks:
            self._set_status("No tracks could be rendered.")
            return

        max_len = max(t.shape[1] for t in tracks)
        padded = [np.pad(t, ((0, 0), (0, max_len - t.shape[1]))) * v
                  for t, v in zip(tracks, volumes)]
        mix = np.sum(padded, axis=0).astype(np.float32)
        peak = float(np.max(np.abs(mix)))
        if peak > 1e-6:
            mix /= peak

        self._master_audio = mix
        self._master_duration = max_len / self._master_sr
        dur = self._master_duration

        def _set_axes():
            if dpg.does_item_exist(_t("master_xaxis")):
                dpg.set_axis_limits(_t("master_xaxis"), 0.0, dur)
                dpg.set_axis_limits(_t("master_yaxis"), -1.0, 1.0)
        schedule_ui(_set_axes)

        self._start_master_play(0.0)

    def _start_master_play(self, offset: float = 0.0) -> None:
        if self._master_audio is None:
            return
        self._master_offset = offset
        self._master_start = time.time()
        self._master_playing = True
        self._set_status("Playing…")

        audio = self._master_audio
        sr = self._master_sr
        start_sample = int(offset * sr)

        def _play_audio() -> None:
            try:
                import sounddevice as sd
                sd.play(audio[:, start_sample:].T, samplerate=sr)
            except Exception as exc:
                log.error("MixPanel master playback error: %s", exc)

        threading.Thread(target=_play_audio, daemon=True).start()

    def _stop_master(self) -> None:
        if self._master_playing:
            elapsed = time.time() - self._master_start
            self._master_offset = min(self._master_offset + elapsed, self._master_duration)
            self._master_playing = False
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Background render to FLAC (Render Mix button)
    # ------------------------------------------------------------------

    def _on_render_click(self, sender, app_data, user_data) -> None:
        if self._render_thread and self._render_thread.is_alive():
            return
        self._render_thread = threading.Thread(target=self._render_mix, daemon=True)
        self._render_thread.start()

    def _render_mix(self) -> None:
        schedule_ui(lambda: dpg.configure_item(_t("render_btn"), enabled=False)
                    if dpg.does_item_exist(_t("render_btn")) else None)
        self._set_status("Rendering…")

        try:
            import soundfile as sf

            enabled = {tid: s for tid, s in self._track_states.items() if s.enabled}
            if not enabled:
                self._set_status("No ON tracks to render.")
                return

            tracks: list[np.ndarray] = []
            volumes: list[float] = []

            for tid, state in enabled.items():
                label = _label_from_tid(tid)
                cache_key = self._make_cache_key(tid, state)
                if cache_key not in self._track_audio_cache:
                    audio = self._render_single_track(label, state)
                    if audio is None:
                        continue
                    self._track_audio_cache[cache_key] = audio
                tracks.append(self._track_audio_cache[cache_key])
                volumes.append(state.volume)

            if not tracks:
                self._set_status("No tracks could be rendered.")
                return

            max_len = max(t.shape[1] for t in tracks)
            padded = [np.pad(t, ((0, 0), (0, max_len - t.shape[1]))) * v
                      for t, v in zip(tracks, volumes)]
            mix = np.sum(padded, axis=0).astype(np.float32)
            peak = float(np.max(np.abs(mix)))
            if peak > 1e-6:
                mix /= peak

            self._rendered_mix = mix
            self._mix_duration = max_len / 44100

            _MIX_DIR.mkdir(parents=True, exist_ok=True)
            src_name = app_state.audio_path.stem if app_state.audio_path else "mix"
            out_path = _MIX_DIR / f"{src_name}_mix.flac"
            sf.write(str(out_path), mix.T, 44100, format="FLAC", subtype="PCM_24")
            self._mix_path = out_path
            app_state.mix_path = out_path

            for cb in self._result_listeners:
                try:
                    cb(out_path)
                except Exception:
                    pass

            dm, ds = divmod(int(self._mix_duration), 60)
            self._set_status(f"Done — {dm}:{ds:02d}")

            dur_text = f"Duration: {self._mix_duration:.1f} s · 44100 Hz · FLAC"
            path_str = str(out_path)

            def _show_result():
                if dpg.does_item_exist(_t("result_duration")):
                    dpg.set_value(_t("result_duration"), dur_text)
                if dpg.does_item_exist(_t("save_btn")):
                    dpg.configure_item(_t("save_btn"), enabled=True)
            schedule_ui(_show_result)
            set_widget_text(_t("result_file"), path_str)

        except Exception as exc:
            log.exception("MixPanel render failed")
            self._set_status(f"Error: {exc}")
        finally:
            schedule_ui(lambda: dpg.configure_item(_t("render_btn"), enabled=True)
                        if dpg.does_item_exist(_t("render_btn")) else None)

    # ------------------------------------------------------------------
    # Render a single track to (2, samples) float32
    # ------------------------------------------------------------------

    def _make_cache_key(self, tid: str, state: TrackState) -> str:
        """Return a stable cache key for this track's current settings."""
        if state.source == "midi":
            return f"{tid}:{state.program}:{state.is_drum}"
        return tid  # audio: file doesn't change, so tid alone is sufficient

    def _render_single_track(
        self,
        label: str,
        state: TrackState,
    ) -> "np.ndarray | None":
        """Render a single track to a (2, samples) float32 stereo array."""
        all_midi = self._all_midi()

        if state.source == "midi" and label in all_midi:
            if self._sf2_path is None:
                self._set_status("No soundfont — install fluid-soundfont-gm.")
                return None
            try:
                import copy
                import pretty_midi
                midi_source = all_midi[label]
                if isinstance(midi_source, pathlib.Path):
                    midi_obj = pretty_midi.PrettyMIDI(str(midi_source))
                else:
                    midi_obj = copy.deepcopy(midi_source)
                for inst in midi_obj.instruments:
                    if state.is_drum:
                        inst.is_drum = True
                    elif not inst.is_drum:
                        inst.program = state.program
                audio_mono = midi_obj.fluidsynth(
                    fs=self._mix_sr, sf2_path=str(self._sf2_path)
                )
                return np.stack([audio_mono, audio_mono])
            except Exception as exc:
                log.error("MixPanel MIDI render error (%s): %s", label, exc)
                return None

        if state.source == "audio":
            from utils.audio_io import read_audio
            path = self._get_audio_path(label, state)
            if path is None:
                return None
            try:
                waveform, sr = read_audio(path, mono=False)
                if sr != self._mix_sr:
                    import librosa
                    waveform = np.stack([
                        librosa.resample(waveform[c], orig_sr=sr, target_sr=self._mix_sr)
                        for c in range(waveform.shape[0])
                    ])
                if waveform.shape[0] == 1:
                    waveform = np.concatenate([waveform, waveform], axis=0)
                return waveform.astype(np.float32)
            except Exception as exc:
                log.error("MixPanel audio read error (%s): %s", label, exc)
                return None

        return None

    # ------------------------------------------------------------------
    # Soundfont and Save As callbacks
    # ------------------------------------------------------------------

    def _on_sf2_browse(self, sender, app_data, user_data) -> None:
        if self._sf2_browser:
            self._sf2_browser.show()

    def _on_sf2_selected(self, path: pathlib.Path) -> None:
        self._sf2_path = path
        self._track_audio_cache.clear()
        self._master_audio = None
        if dpg.does_item_exist(_t("sf2_path")):
            dpg.set_value(_t("sf2_path"), str(path))

    def _on_save_as(self, sender, app_data, user_data) -> None:
        if self._save_browser:
            self._save_browser.show()

    def _on_save_selected(self, path: pathlib.Path) -> None:
        if self._rendered_mix is None:
            return
        try:
            import soundfile as sf
            path.parent.mkdir(parents=True, exist_ok=True)
            fmt = path.suffix.lstrip(".").upper() or "FLAC"
            subtype = "PCM_24" if fmt == "FLAC" else None
            sf.write(str(path), self._rendered_mix.T, self._mix_sr,
                     format=fmt, subtype=subtype)
            self._mix_path = path
            set_widget_text(_t("result_file"), str(path))
        except Exception as exc:
            log.error("MixPanel save error: %s", exc)
            self._set_status(f"Save error: {exc}")

    # ------------------------------------------------------------------
    # Tick — called each frame by tick_all_midi() via _ALL_MIDI_PLAYERS
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Update all track cursors from the master clock each frame."""
        # Click-to-seek: master plot
        if (self._master_duration > 0
                and dpg.does_item_exist(_t("master_plot"))
                and dpg.is_item_hovered(_t("master_plot"))
                and dpg.is_mouse_button_released(dpg.mvMouseButton_Left)):
            raw = dpg.get_plot_mouse_pos()
            seek = float(raw[0])
            if 0.0 <= seek <= self._master_duration:
                self._on_waveform_seek(seek)

        # Click-to-seek: MIDI track plots
        if self._master_duration > 0:
            for tid, state in self._track_states.items():
                if state.source == "midi":
                    plot_tag = _t(f"track_{_safe(tid)}_plot")
                    if (dpg.does_item_exist(plot_tag)
                            and dpg.is_item_hovered(plot_tag)
                            and dpg.is_mouse_button_released(dpg.mvMouseButton_Left)):
                        raw = dpg.get_plot_mouse_pos()
                        seek = float(raw[0])
                        if 0.0 <= seek <= self._master_duration:
                            self._on_waveform_seek(seek)
                        break

        pos: float | None = None
        duration: float = self._master_duration

        if self._master_playing:
            pos = self._master_offset + (time.time() - self._master_start)
            if pos >= duration:
                self._stop_master()
                self._set_status("Done")
                pos = 0.0

        elif self._solo_playing:
            pos = self._solo_offset + (time.time() - self._solo_start)
            duration = self._solo_duration
            if pos >= duration:
                self._stop_solo()
                pos = 0.0

        if pos is not None:
            self._update_all_cursors(pos, duration)

    def _update_all_cursors(self, pos: float, duration: float) -> None:
        """Push master position to all per-track displays and the master HUD."""
        m, s = divmod(int(pos), 60)
        dm, ds = divmod(int(duration), 60)
        time_str = f"{m}:{s:02d} / {dm}:{ds:02d}"

        if dpg.does_item_exist(_t("master_time")):
            dpg.set_value(_t("master_time"), time_str)

        if dpg.does_item_exist(_t("master_cursor")):
            dpg.set_value(_t("master_cursor"), [[pos]])

        # Per-track audio waveform cursors
        for wf in self._track_waveforms.values():
            wf.set_cursor(pos)

        # Per-track MIDI cursors + time text
        for tid, state in self._track_states.items():
            if state.source == "midi":
                safe = _safe(tid)
                cursor_tag = _t(f"track_{safe}_cursor")
                if dpg.does_item_exist(cursor_tag):
                    dpg.set_value(cursor_tag, [[pos]])
                time_tag = _t(f"track_{safe}_timetext")
                if dpg.does_item_exist(time_tag):
                    dpg.set_value(time_tag, f"{m}:{s:02d}")

    # ------------------------------------------------------------------
    # Duck-typed _stop() — called by stop_all_midi() for cross-exclusion
    # ------------------------------------------------------------------

    def _stop(self) -> None:
        """Stop all Mix panel playback."""
        self._stop_master()
        self._stop_solo()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        set_widget_text(_t("status"), msg)
