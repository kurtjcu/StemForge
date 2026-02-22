"""Multi-track mixer panel for StemForge.

Combines audio stems (from Separate tab) with MIDI-rendered accompaniment
(from MIDI tab), providing per-track On/Off muting, instrument selection,
and volume.  Two playback modes:

  • Master Play — premixes all ON tracks in memory and plays the result.
  • Solo Preview — plays a single track while advancing the time cursor on
    every other track display, so the user can hear one track in context.

Both share a single master clock; all per-track waveform displays and MIDI
time bars are advanced from that clock each frame by tick().

Render Mix saves the current ON tracks as a FLAC file and makes it
available on the Export panel via app_state.mix_path.

Inter-panel wiring (set up in gui/app.py)
-----------------------------------------
    _demucs.add_result_listener(_mix.notify_stems_ready)
    _midi.add_result_listener(_mix.notify_midi_ready)
"""

from __future__ import annotations

import logging
import pathlib
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np
import dearpygui.dearpygui as dpg

from gui.state import app_state, set_widget_text, make_copy_callback
from gui.constants import _MIX_DIR
from gui.components.file_browser import FileBrowser
from gui.components.demucs_panel import _STEM_LABEL
from gui.components.waveform_widget import WaveformWidget, _ALL_WIDGETS
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


def _safe(label: str) -> str:
    """Sanitise a display label for use in a DPG tag."""
    return (
        label.replace(" ", "_")
             .replace("&", "and")
             .replace("/", "_")
             .replace(".", "_")
             .replace("(", "")
             .replace(")", "")
    )


# ---------------------------------------------------------------------------
# ON / OFF toggle button themes  (lazy-initialised the first time build_ui runs)
# ---------------------------------------------------------------------------

_TOGGLE_ON_THEME: int | None = None
_TOGGLE_OFF_THEME: int | None = None


def _get_toggle_themes() -> tuple[int, int]:
    """Return (on_theme, off_theme) DPG theme tags, creating them if needed."""
    global _TOGGLE_ON_THEME, _TOGGLE_OFF_THEME
    if _TOGGLE_ON_THEME is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button,        (35, 110, 35, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (55, 150, 55, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (75, 180, 75, 255))
        _TOGGLE_ON_THEME = t
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button,        (55, 55, 65, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,  (75, 75, 90, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,   (65, 65, 80, 255))
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
    # For manually-loaded tracks, store the path here.
    manual_path: pathlib.Path | None = None


# ---------------------------------------------------------------------------
# MixPanel
# ---------------------------------------------------------------------------

class MixPanel:
    """Multi-track mixer: audio stems + MIDI-rendered accompaniment."""

    def __init__(self) -> None:
        # Stem paths received from upstream panels
        self._audio_stems: dict[str, pathlib.Path] = {}   # internal_name → path
        self._midi_stems: dict[str, pathlib.Path] = {}    # display_label → path

        # Manually loaded additional tracks
        self._manual_audio: dict[str, pathlib.Path] = {}  # display_label → path
        self._manual_midi: dict[str, pathlib.Path] = {}   # display_label → path

        # Per-track state (display_label → TrackState)
        self._track_states: dict[str, TrackState] = {}

        # Per-track rendered audio cache: cache_key → (2, samples) float32
        self._track_audio_cache: dict[str, np.ndarray] = {}

        # Per-track WaveformWidget instances (audio tracks only)
        self._track_waveforms: dict[str, WaveformWidget] = {}

        # Master clock — used by both "Play All" and "Solo Preview"
        self._master_playing: bool = False
        self._master_start: float = 0.0
        self._master_offset: float = 0.0
        self._master_duration: float = 0.0
        self._master_audio: np.ndarray | None = None
        self._master_sr: int = 44100

        # Solo preview — single track plays; master clock still advances
        self._solo_playing: bool = False
        self._solo_label: str = ""
        self._solo_start: float = 0.0
        self._solo_offset: float = 0.0
        self._solo_duration: float = 0.0

        # Rendered mix (for Save As, different from master_audio which is not saved)
        self._rendered_mix: np.ndarray | None = None
        self._mix_duration: float = 0.0
        self._mix_path: pathlib.Path | None = None
        self._mix_sr: int = 44100

        # Soundfont
        self._sf2_path: pathlib.Path | None = find_soundfont()

        # Background threads
        self._render_thread: threading.Thread | None = None
        self._preview_thread: threading.Thread | None = None

        # Result listeners — called with (mix_path,) after a successful render.
        self._result_listeners: list[Callable[[pathlib.Path], None]] = []

        # File browsers (built later at top DPG level)
        self._sf2_browser: FileBrowser | None = None
        self._save_browser: FileBrowser | None = None
        self._add_audio_browser: FileBrowser | None = None
        self._add_midi_browser: FileBrowser | None = None

        # Register in the duck-typed MIDI/mix player list so stop_all_midi()
        # and tick_all_midi() include this panel.
        _ALL_MIDI_PLAYERS.append(self)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        with dpg.group(horizontal=True):

            # ---- Left column: soundfont + track list ------------------
            with dpg.child_window(width=450, height=-1, border=False):

                # Soundfont picker
                dpg.add_text("Soundfont", color=(175, 175, 255, 255))
                sf2_str = (
                    str(self._sf2_path) if self._sf2_path
                    else "(none — install fluid-soundfont-gm)"
                )
                dpg.add_input_text(
                    tag=_t("sf2_path"),
                    default_value=sf2_str,
                    readonly=True,
                    width=-1,
                )
                dpg.add_button(
                    label="  Browse soundfont  ",
                    callback=self._on_sf2_browse,
                    height=28,
                )

                dpg.add_spacer(height=10)
                dpg.add_separator()
                dpg.add_spacer(height=6)

                # Track list header + add-file buttons
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
                    wrap=420,
                )

                # Scrollable track list area
                with dpg.child_window(
                    tag=_t("tracks_scroll"),
                    height=-1,
                    border=False,
                    no_scrollbar=False,
                ):
                    with dpg.group(tag=_t("tracks_group")):
                        pass

            # ---- Right column: master controls + render ---------------
            with dpg.child_window(width=-1, height=-1, border=False):

                dpg.add_text("Master Playback", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)

                # Master transport buttons
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="Play All",
                        tag=_t("master_play_btn"),
                        callback=self._on_master_play,
                        width=90,
                        height=34,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(
                            "Premix all ON tracks and play the result.\n"
                            "Per-track audio is cached — MIDI is only re-rendered\n"
                            "when you change the instrument."
                        )
                    dpg.add_button(
                        label="Stop All",
                        tag=_t("master_stop_btn"),
                        callback=self._on_master_stop,
                        width=90,
                        height=34,
                    )
                    dpg.add_button(
                        label="<<",
                        tag=_t("master_rewind_btn"),
                        callback=self._on_master_rewind,
                        width=50,
                        height=34,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text("Rewind all tracks to start")
                    dpg.add_text("", tag=_t("master_time"), color=(160, 160, 160, 255))

                dpg.add_spacer(height=6)
                dpg.add_progress_bar(
                    tag=_t("master_progress"),
                    default_value=0.0,
                    width=-1,
                    height=16,
                )
                dpg.add_spacer(height=6)

                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="Copy",
                        callback=make_copy_callback(_t("status")),
                        width=50,
                    )
                    dpg.add_text(
                        "Ready",
                        tag=_t("status"),
                        color=(160, 160, 160, 255),
                        wrap=380,
                    )

                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_spacer(height=8)

                dpg.add_text("Render", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)

                dpg.add_button(
                    label="  Render Mix to FLAC  ",
                    tag=_t("render_btn"),
                    callback=self._on_render_click,
                    height=34,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Mix all ON tracks and save as a FLAC file.\n"
                        "The result is also available on the Export tab."
                    )

                dpg.add_spacer(height=10)
                dpg.add_text("", tag=_t("result_duration"), color=(220, 220, 220, 255))
                dpg.add_text("File:", color=(140, 140, 180, 255))
                dpg.add_text(
                    "(none)",
                    tag=_t("result_file"),
                    color=(140, 140, 140, 255),
                    wrap=450,
                )

                dpg.add_spacer(height=8)
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="Copy path",
                        callback=make_copy_callback(_t("result_file")),
                        width=90,
                    )
                    dpg.add_spacer(width=8)
                    dpg.add_button(
                        label="Save as",
                        tag=_t("save_btn"),
                        callback=self._on_save_as,
                        width=90,
                        enabled=False,
                    )

                dpg.add_spacer(height=14)
                dpg.add_text(
                    "Note: vocal MIDI uses GM choir sounds — no lyrics.",
                    color=(100, 100, 120, 255),
                    wrap=420,
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
        """Called by Separate panel after successful separation."""
        self._audio_stems = dict(stem_paths)
        self._rebuild_tracks()

    def notify_midi_ready(
        self,
        midi_path: pathlib.Path,
        stem_midi_paths: dict[str, pathlib.Path],
    ) -> None:
        """Called by MIDI panel after successful extraction."""
        self._midi_stems = dict(stem_midi_paths)
        self._rebuild_tracks()

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

    def _all_midi(self) -> dict[str, pathlib.Path]:
        """Merged MIDI sources: pipeline extractions + manual loads."""
        result = dict(self._midi_stems)
        result.update(self._manual_midi)
        return result

    def _rebuild_tracks(self) -> None:
        """Recompute the track list and refresh the UI."""
        all_audio = self._all_audio()
        all_midi = self._all_midi()

        all_labels: set[str] = set(all_audio) | set(all_midi)

        new_states: dict[str, TrackState] = {}
        for label in all_labels:
            if label in self._track_states:
                # Preserve existing user settings.
                new_states[label] = self._track_states[label]
            else:
                has_audio = label in all_audio
                has_midi = label in all_midi
                is_vocal = label in ("Singing voice", "vocals")

                if has_audio and (is_vocal or not has_midi):
                    source = "audio"
                elif has_midi:
                    source = "midi"
                else:
                    source = "audio"

                manual_path: pathlib.Path | None = None
                if label in self._manual_audio:
                    manual_path = self._manual_audio[label]
                elif label in self._manual_midi:
                    manual_path = self._manual_midi[label]

                new_states[label] = TrackState(
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
            return  # build_ui() not called yet

        # Remove stale WaveformWidgets before deleting their DPG items.
        self._clear_track_waveforms()
        dpg.delete_item(group_tag, children_only=True)

        has_tracks = bool(self._track_states)
        if dpg.does_item_exist(empty_tag):
            dpg.configure_item(empty_tag, show=not has_tracks)

        on_theme, off_theme = _get_toggle_themes()

        for label, state in self._track_states.items():
            safe = _safe(label)
            source_hint = "audio" if state.source == "audio" else "midi"

            with dpg.group(tag=_t(f"track_{safe}_group"), parent=group_tag):

                # --- Row 1: ON/OFF toggle · label · Play · Stop -------
                with dpg.group(horizontal=True):
                    toggle_tag = _t(f"track_{safe}_toggle")
                    dpg.add_button(
                        label="ON" if state.enabled else "OFF",
                        tag=toggle_tag,
                        callback=self._on_track_toggle,
                        user_data=label,
                        width=44,
                    )
                    dpg.bind_item_theme(
                        toggle_tag,
                        on_theme if state.enabled else off_theme,
                    )
                    with dpg.tooltip(toggle_tag):
                        dpg.add_text("Toggle mute. ON = included in playback/render.")

                    dpg.add_text(
                        f"  {label}  ({source_hint})",
                        color=(210, 210, 230, 255),
                    )
                    dpg.add_spacer(width=6)
                    dpg.add_button(
                        label="Play",
                        tag=_t(f"track_{safe}_preview_btn"),
                        callback=self._on_track_preview,
                        user_data=label,
                        width=52,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(
                            "Solo preview: plays only this track while\n"
                            "advancing the time cursor on all tracks."
                        )
                    dpg.add_button(
                        label="Stop",
                        tag=_t(f"track_{safe}_stop_btn"),
                        callback=self._on_track_stop,
                        user_data=label,
                        width=52,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text("Stop solo preview (or master playback).")

                # --- Row 2: instrument (MIDI) + volume slider ----------
                with dpg.group(horizontal=True):
                    if state.source == "midi" and not state.is_drum:
                        default_name = (
                            _GM_INSTRUMENTS[state.program]
                            if 0 <= state.program < len(_GM_INSTRUMENTS)
                            else _GM_INSTRUMENTS[0]
                        )
                        dpg.add_combo(
                            items=_GM_INSTRUMENTS,
                            default_value=default_name,
                            tag=_t(f"track_{safe}_program"),
                            width=185,
                            callback=self._on_track_program,
                            user_data=label,
                        )
                    elif state.source == "midi" and state.is_drum:
                        dpg.add_text("Standard Drums", color=(140, 140, 180, 255))
                    else:
                        dpg.add_text("(audio stem)", color=(100, 150, 100, 255))

                    dpg.add_spacer(width=8)
                    dpg.add_slider_int(
                        label="Vol%",
                        tag=_t(f"track_{safe}_vol"),
                        default_value=int(state.volume * 100),
                        min_value=0,
                        max_value=100,
                        width=120,
                        callback=self._on_track_volume,
                        user_data=label,
                    )

                # --- Row 3: waveform (audio) or time bar (MIDI) -------
                if state.source == "audio":
                    wf = WaveformWidget(
                        tag_prefix=f"mix_{safe}",
                        on_seek_callback=self._on_waveform_seek,
                    )
                    self._track_waveforms[label] = wf
                    wf.build_ui(show_controls=False, plot_height=55)
                    path = self._get_audio_path(label, state)
                    if path:
                        wf.load_async(path)
                else:
                    # MIDI track: simple progress bar + time text
                    dpg.add_progress_bar(
                        tag=_t(f"track_{safe}_timebar"),
                        default_value=0.0,
                        width=-1,
                        height=10,
                    )
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
        label = user_data
        state = self._track_states.get(label)
        if not state:
            return
        state.enabled = not state.enabled
        on_theme, off_theme = _get_toggle_themes()
        safe = _safe(label)
        toggle_tag = _t(f"track_{safe}_toggle")
        if dpg.does_item_exist(toggle_tag):
            dpg.configure_item(toggle_tag, label="ON" if state.enabled else "OFF")
            dpg.bind_item_theme(toggle_tag, on_theme if state.enabled else off_theme)
        # Invalidate master_audio — it no longer reflects the new track set.
        self._master_audio = None

    def _on_track_volume(self, sender, app_data, user_data) -> None:
        label = user_data
        if label in self._track_states:
            self._track_states[label].volume = int(app_data) / 100.0
        # Master audio is stale if volume changed.
        self._master_audio = None

    def _on_track_program(self, sender, app_data, user_data) -> None:
        label = user_data
        if label in self._track_states and app_data in _GM_INSTRUMENTS:
            self._track_states[label].program = _GM_INSTRUMENTS.index(app_data)
        # Invalidate cached render for this MIDI track.
        stale = [k for k in self._track_audio_cache if k.startswith(f"{label}:")]
        for k in stale:
            del self._track_audio_cache[k]
        self._master_audio = None

    def _on_track_preview(self, sender, app_data, user_data) -> None:
        """Solo-preview a single track."""
        label = user_data
        state = self._track_states.get(label)
        if not state:
            return
        self._preview_thread = threading.Thread(
            target=self._do_solo_preview,
            args=(label, state),
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
    # Waveform click-to-seek (called from WaveformWidget.tick)
    # ------------------------------------------------------------------

    def _on_waveform_seek(self, pos: float) -> None:
        """Seek the master clock; called when user clicks a track waveform."""
        was_playing_master = self._master_playing
        self._stop_master()
        self._stop_solo()
        self._master_offset = pos
        self._update_all_cursors(pos, self._master_duration)
        if was_playing_master and self._master_audio is not None:
            self._start_master_play(pos)

    # ------------------------------------------------------------------
    # Solo preview
    # ------------------------------------------------------------------

    def _do_solo_preview(self, label: str, state: TrackState) -> None:
        """Background worker: render (if needed) and solo-play one track."""
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

        cache_key = (
            f"{label}:{state.program}:{state.is_drum}"
            if state.source == "midi"
            else label
        )
        if cache_key not in self._track_audio_cache:
            self._set_status(f"Rendering: {label}…")
            audio = self._render_single_track(label, state)
            if audio is None:
                self._set_status(f"Failed to render: {label}")
                return
            self._track_audio_cache[cache_key] = audio

        audio = self._track_audio_cache[cache_key]
        duration = audio.shape[1] / self._mix_sr

        # Set master duration if not yet set (so cursors have a reference)
        if self._master_duration == 0.0:
            self._master_duration = duration

        self._solo_label = label
        self._solo_duration = duration
        self._solo_offset = 0.0
        self._solo_start = time.time()
        self._solo_playing = True
        self._set_status(f"Solo: {label}")

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

        enabled = {k: v for k, v in self._track_states.items() if v.enabled}
        if not enabled:
            self._set_status("No ON tracks to play.")
            return

        self._set_status("Preparing mix…")
        tracks: list[np.ndarray] = []
        volumes: list[float] = []
        n = len(enabled)

        for i, (label, state) in enumerate(enabled.items()):
            self._set_status(f"Loading {i + 1}/{n}: {label}")
            cache_key = (
                f"{label}:{state.program}:{state.is_drum}"
                if state.source == "midi"
                else label
            )
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
        padded = []
        for t, vol in zip(tracks, volumes):
            pad = max_len - t.shape[1]
            padded.append(np.pad(t, ((0, 0), (0, pad))) * vol)

        mix = np.sum(padded, axis=0).astype(np.float32)
        peak = float(np.max(np.abs(mix)))
        if peak > 1e-6:
            mix /= peak

        self._master_audio = mix
        self._master_duration = max_len / self._master_sr

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
            self._master_offset = min(
                self._master_offset + elapsed, self._master_duration
            )
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
        if dpg.does_item_exist(_t("render_btn")):
            dpg.configure_item(_t("render_btn"), enabled=False)
        self._set_status("Rendering…")

        try:
            import soundfile as sf

            enabled = {k: v for k, v in self._track_states.items() if v.enabled}
            if not enabled:
                self._set_status("No ON tracks to render.")
                return

            tracks: list[np.ndarray] = []
            volumes: list[float] = []
            n = len(enabled)

            for i, (label, state) in enumerate(enabled.items()):
                cache_key = (
                    f"{label}:{state.program}:{state.is_drum}"
                    if state.source == "midi"
                    else label
                )
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
            padded = []
            for t, vol in zip(tracks, volumes):
                pad = max_len - t.shape[1]
                padded.append(np.pad(t, ((0, 0), (0, pad))) * vol)

            mix = np.sum(padded, axis=0).astype(np.float32)
            peak = float(np.max(np.abs(mix)))
            if peak > 1e-6:
                mix /= peak

            self._rendered_mix = mix
            self._mix_duration = max_len / 44100

            # Save as FLAC
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

            if dpg.does_item_exist(_t("result_duration")):
                dpg.set_value(
                    _t("result_duration"),
                    f"Duration: {self._mix_duration:.1f} s · 44100 Hz · FLAC",
                )
            set_widget_text(_t("result_file"), str(out_path))

            if dpg.does_item_exist(_t("save_btn")):
                dpg.configure_item(_t("save_btn"), enabled=True)

        except Exception as exc:
            log.exception("MixPanel render failed")
            self._set_status(f"Error: {exc}")
        finally:
            if dpg.does_item_exist(_t("render_btn")):
                dpg.configure_item(_t("render_btn"), enabled=True)

    # ------------------------------------------------------------------
    # Render a single track to (2, samples) float32
    # ------------------------------------------------------------------

    def _render_single_track(
        self,
        label: str,
        state: TrackState,
    ) -> "np.ndarray | None":
        all_audio = self._all_audio()
        all_midi = self._all_midi()

        if state.source == "midi" and label in all_midi:
            if self._sf2_path is None:
                self._set_status("No soundfont — install fluid-soundfont-gm.")
                return None
            try:
                import pretty_midi
                midi_obj = pretty_midi.PrettyMIDI(str(all_midi[label]))
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
        self._track_audio_cache.clear()  # Invalidate all MIDI caches.
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
            # Detect format from extension; default FLAC.
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
            if pos >= self._solo_duration:
                self._stop_solo()
                pos = 0.0
            # Use solo duration as the reference for the progress fraction
            duration = self._solo_duration

        if pos is not None:
            self._update_all_cursors(pos, duration)

    def _update_all_cursors(self, pos: float, duration: float) -> None:
        """Push master position to all per-track displays and the master HUD."""
        m, s = divmod(int(pos), 60)
        dm, ds = divmod(int(duration), 60)
        time_str = f"{m}:{s:02d} / {dm}:{ds:02d}"

        if dpg.does_item_exist(_t("master_time")):
            dpg.set_value(_t("master_time"), time_str)

        frac = (pos / duration) if duration > 0 else 0.0
        if dpg.does_item_exist(_t("master_progress")):
            dpg.set_value(_t("master_progress"), frac)

        # Per-track audio waveform cursors
        for wf in self._track_waveforms.values():
            wf.set_cursor(pos)

        # Per-track MIDI time bars
        for label, state in self._track_states.items():
            if state.source == "midi":
                safe = _safe(label)
                bar_tag = _t(f"track_{safe}_timebar")
                if dpg.does_item_exist(bar_tag):
                    dpg.set_value(bar_tag, frac)
                time_tag = _t(f"track_{safe}_timetext")
                if dpg.does_item_exist(time_tag):
                    dpg.set_value(time_tag, f"{m}:{s:02d}")

    # ------------------------------------------------------------------
    # Duck-typed _stop() — called by stop_all_midi() for cross-exclusion
    # ------------------------------------------------------------------

    def _stop(self) -> None:
        """Stop all Mix panel playback (called by stop_all_midi)."""
        self._stop_master()
        self._stop_solo()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        set_widget_text(_t("status"), msg)
