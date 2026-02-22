"""Multi-track mixer panel for StemForge.

Combines audio stems (from DemucsPanel / RoformerPanel) with MIDI-rendered
accompaniment (from MidiPanel), offering per-track instrument selection,
volume, and mute controls, then exports the result to a single WAV file.

Inter-panel wiring (set up in gui/app.py)
-----------------------------------------
    _demucs.add_result_listener(_mix.notify_stems_ready)
    _midi.add_result_listener(_mix.notify_midi_ready)

Layout
------
Left column (~380 px):
  · Soundfont path + Browse button
  · Dynamic track list: checkbox, label, instrument combo (MIDI), volume
  · Note about vocal MIDI limitation

Right column:
  · Render Mix button + Play / Stop / Rewind + time display
  · Progress bar + status
  · Result: duration, file path, Copy path, Save As
"""

from __future__ import annotations

import logging
import pathlib
import threading
import time
from dataclasses import dataclass

import numpy as np
import dearpygui.dearpygui as dpg

from gui.state import app_state, set_widget_text, make_copy_callback
from gui.constants import _MIX_DIR
from gui.components.file_browser import FileBrowser
from gui.components.demucs_panel import _STEM_LABEL
from gui.components.midi_player_widget import (
    _ALL_MIDI_PLAYERS,
    find_soundfont,
    _STEM_DEFAULT_PROGRAM,
    _STEM_IS_DRUM,
)

log = logging.getLogger("stemforge.gui.mix_panel")

_P = "mix"

# Reverse map: display label → internal Demucs stem name
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
    """Sanitise a display label for use in a DPG tag (no spaces or symbols)."""
    return (
        label.replace(" ", "_")
             .replace("&", "and")
             .replace("/", "_")
             .replace(".", "_")
             .replace("(", "")
             .replace(")", "")
    )


@dataclass
class TrackState:
    """Mutable per-track UI state, preserved across track-list rebuilds."""
    source: str = "audio"    # "audio" or "midi"
    enabled: bool = True
    volume: float = 1.0      # 0.0 – 1.0
    program: int = 0         # GM program number (MIDI tracks only)
    is_drum: bool = False


class MixPanel:
    """Multi-track mixer: audio stems + MIDI-rendered accompaniment."""

    def __init__(self) -> None:
        # Stem paths received from upstream panels
        self._audio_stems: dict[str, pathlib.Path] = {}   # internal_name → path
        self._midi_stems: dict[str, pathlib.Path] = {}     # display_label → path

        # Per-track state (display_label → TrackState)
        self._track_states: dict[str, TrackState] = {}

        # Rendered mix result
        self._rendered_mix: np.ndarray | None = None  # (2, samples) float32
        self._mix_duration: float = 0.0
        self._mix_sr: int = 44100
        self._mix_path: pathlib.Path | None = None

        # Soundfont
        self._sf2_path: pathlib.Path | None = find_soundfont()

        # Playback state
        self._playing: bool = False
        self._play_start: float = 0.0
        self._play_offset: float = 0.0

        # Background render thread
        self._thread: threading.Thread | None = None

        # File browsers (built later at top DPG level)
        self._sf2_browser: FileBrowser | None = None
        self._save_browser: FileBrowser | None = None

        # Register in the global MIDI player list so tick_all_midi() /
        # stop_all_midi() handle this panel alongside MidiPlayerWidget.
        _ALL_MIDI_PLAYERS.append(self)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        with dpg.group(horizontal=True):

            # ---- Left column: soundfont + track list ------------------
            with dpg.child_window(width=390, height=-1, border=False):

                # Soundfont
                dpg.add_text("Soundfont", color=(175, 175, 255, 255))
                sf2_str = str(self._sf2_path) if self._sf2_path else "(none found — install fluid-soundfont-gm)"
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

                dpg.add_spacer(height=12)
                dpg.add_separator()

                # Track list
                dpg.add_text("Tracks", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)
                dpg.add_text(
                    "Run Separate and Extract MIDI first.",
                    tag=_t("tracks_empty"),
                    color=(120, 120, 140, 255),
                    wrap=370,
                )

                # Track rows are appended here dynamically.
                with dpg.group(tag=_t("tracks_group")):
                    pass

                dpg.add_spacer(height=12)
                dpg.add_text(
                    "Note: vocal MIDI uses GM choir sounds — no lyrics.",
                    color=(100, 100, 120, 255),
                    wrap=370,
                )

            # ---- Right column: controls + result ----------------------
            with dpg.child_window(width=-1, height=-1, border=False):

                # Render + playback controls
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="  Render Mix  ",
                        tag=_t("render_btn"),
                        callback=self._on_render_click,
                        height=36,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(
                            "Mix all enabled tracks and save to a WAV file.\n"
                            "MIDI tracks are rendered via FluidSynth.\n"
                            "Audio tracks are loaded directly from the stems."
                        )
                    dpg.add_spacer(width=10)
                    dpg.add_button(
                        label="Play",
                        tag=_t("play_btn"),
                        callback=self._on_play,
                        width=70,
                        enabled=False,
                    )
                    dpg.add_button(
                        label="Stop",
                        tag=_t("stop_btn"),
                        callback=self._on_stop,
                        width=70,
                        enabled=False,
                    )
                    dpg.add_button(
                        label="<<",
                        tag=_t("rewind_btn"),
                        callback=self._on_rewind,
                        width=50,
                        enabled=False,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text("Rewind to start")
                    dpg.add_text("", tag=_t("time"), color=(160, 160, 160, 255))

                dpg.add_spacer(height=8)
                dpg.add_progress_bar(
                    tag=_t("progress"),
                    default_value=0.0,
                    width=-1,
                    height=18,
                )
                dpg.add_spacer(height=4)
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
                dpg.add_text("Result", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)

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

    def build_save_dialog(self) -> None:
        """Create file browsers at the top DPG level (outside all windows)."""
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
            extensions=frozenset({".wav", ".flac", ".ogg"}),
            mode="save",
        )
        self._save_browser.build()

    # ------------------------------------------------------------------
    # Inter-panel notifications
    # ------------------------------------------------------------------

    def notify_stems_ready(self, stem_paths: dict[str, pathlib.Path]) -> None:
        """Called by DemucsPanel / RoformerPanel after successful separation.

        *stem_paths* uses internal Demucs names ("vocals", "drums", …).
        """
        self._audio_stems = dict(stem_paths)
        self._rebuild_tracks()

    def notify_midi_ready(
        self,
        midi_path: pathlib.Path,
        stem_midi_paths: dict[str, pathlib.Path],
    ) -> None:
        """Called by MidiPanel after successful MIDI extraction.

        *stem_midi_paths* keys are display labels ("Singing voice", "Bass", …).
        """
        self._midi_stems = dict(stem_midi_paths)
        self._rebuild_tracks()

    # ------------------------------------------------------------------
    # Track list management
    # ------------------------------------------------------------------

    def _rebuild_tracks(self) -> None:
        """Recompute the track list and refresh the UI."""
        # Build the unified set of display labels.
        all_labels: set[str] = set()
        for internal in self._audio_stems:
            all_labels.add(_STEM_LABEL.get(internal, internal))
        all_labels.update(self._midi_stems.keys())

        # Update _track_states — preserve existing user settings.
        new_states: dict[str, TrackState] = {}
        for label in all_labels:
            if label in self._track_states:
                new_states[label] = self._track_states[label]
            else:
                internal = _REVERSE_STEM_LABEL.get(label)
                has_audio = internal is not None and internal in self._audio_stems
                has_midi = label in self._midi_stems
                is_vocal = label in ("Singing voice", "vocals")

                # Vocal defaults to audio; others default to MIDI when available.
                if has_audio and (is_vocal or not has_midi):
                    source = "audio"
                elif has_midi:
                    source = "midi"
                else:
                    source = "audio"

                new_states[label] = TrackState(
                    source=source,
                    enabled=True,
                    volume=1.0,
                    program=_STEM_DEFAULT_PROGRAM.get(label, 0),
                    is_drum=_STEM_IS_DRUM.get(label, False),
                )

        self._track_states = new_states
        self._update_tracks_ui()

    def _update_tracks_ui(self) -> None:
        """Delete and recreate track rows in the DPG tracks_group."""
        group_tag = _t("tracks_group")
        empty_tag = _t("tracks_empty")

        if not dpg.does_item_exist(group_tag):
            return  # build_ui() not called yet

        dpg.delete_item(group_tag, children_only=True)

        has_tracks = bool(self._track_states)
        if dpg.does_item_exist(empty_tag):
            dpg.configure_item(empty_tag, show=not has_tracks)

        for label, state in self._track_states.items():
            safe = _safe(label)
            source_hint = "audio" if state.source == "audio" else "midi"

            with dpg.group(tag=_t(f"track_{safe}_group"), parent=group_tag):

                # Row 1: enable checkbox + label
                dpg.add_checkbox(
                    label=f"{label}  ({source_hint})",
                    tag=_t(f"track_{safe}_check"),
                    default_value=state.enabled,
                    callback=self._on_track_check,
                    user_data=label,
                )

                # Row 2: instrument selector (MIDI only) + volume
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
                            width=195,
                            callback=self._on_track_program,
                            user_data=label,
                        )
                    elif state.source == "midi" and state.is_drum:
                        dpg.add_text(
                            "Standard Drums",
                            color=(140, 140, 180, 255),
                        )
                    else:
                        dpg.add_text("(audio stem)", color=(100, 150, 100, 255))

                    dpg.add_spacer(width=8)
                    dpg.add_slider_int(
                        label="Vol%",
                        tag=_t(f"track_{safe}_vol"),
                        default_value=int(state.volume * 100),
                        min_value=0,
                        max_value=100,
                        width=110,
                        callback=self._on_track_volume,
                        user_data=label,
                    )

                dpg.add_separator()
                dpg.add_spacer(height=2)

    # ------------------------------------------------------------------
    # Callbacks — track controls
    # ------------------------------------------------------------------

    def _on_track_check(self, sender, app_data, user_data) -> None:
        label = user_data
        if label in self._track_states:
            self._track_states[label].enabled = bool(app_data)

    def _on_track_volume(self, sender, app_data, user_data) -> None:
        label = user_data
        if label in self._track_states:
            self._track_states[label].volume = int(app_data) / 100.0

    def _on_track_program(self, sender, app_data, user_data) -> None:
        label = user_data
        if label in self._track_states and app_data in _GM_INSTRUMENTS:
            self._track_states[label].program = _GM_INSTRUMENTS.index(app_data)

    # ------------------------------------------------------------------
    # Callbacks — buttons
    # ------------------------------------------------------------------

    def _on_render_click(self, sender, app_data, user_data) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._render_mix, daemon=True)
        self._thread.start()

    def _on_play(self, sender, app_data, user_data) -> None:
        self._start_play(self._play_offset)

    def _on_stop(self, sender, app_data, user_data) -> None:
        self._stop()

    def _on_rewind(self, sender, app_data, user_data) -> None:
        if self._playing:
            self._start_play(0.0)
        else:
            self._play_offset = 0.0
            if dpg.does_item_exist(_t("time")) and self._mix_duration > 0:
                dm, ds = divmod(int(self._mix_duration), 60)
                dpg.set_value(_t("time"), f"0:00 / {dm}:{ds:02d}")

    def _on_sf2_browse(self, sender, app_data, user_data) -> None:
        if self._sf2_browser:
            self._sf2_browser.show()

    def _on_sf2_selected(self, path: pathlib.Path) -> None:
        self._sf2_path = path
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
            sf.write(str(path), self._rendered_mix.T, self._mix_sr)
            self._mix_path = path
            set_widget_text(_t("result_file"), str(path))
        except Exception as exc:
            log.error("MixPanel save error: %s", exc)
            self._set_status(f"Save error: {exc}")

    # ------------------------------------------------------------------
    # Background render
    # ------------------------------------------------------------------

    def _render_mix(self) -> None:
        if dpg.does_item_exist(_t("render_btn")):
            dpg.configure_item(_t("render_btn"), enabled=False)
        dpg.set_value(_t("progress"), 0.0)
        self._set_status("Rendering...")

        try:
            import pretty_midi
            import soundfile as sf
            from utils.audio_io import read_audio

            enabled = {k: v for k, v in self._track_states.items() if v.enabled}
            if not enabled:
                self._set_status("No enabled tracks.")
                return

            tracks: list[np.ndarray] = []   # each (2, samples) float32
            volumes: list[float] = []
            n = len(enabled)

            for i, (label, state) in enumerate(enabled.items()):
                if state.source == "midi" and label in self._midi_stems:
                    if self._sf2_path is None:
                        self._set_status(
                            "Error: no soundfont found. "
                            "Install fluid-soundfont-gm."
                        )
                        return

                    midi_obj = pretty_midi.PrettyMIDI(str(self._midi_stems[label]))
                    for inst in midi_obj.instruments:
                        if state.is_drum:
                            inst.is_drum = True
                        elif not inst.is_drum:
                            inst.program = state.program

                    audio_mono = midi_obj.fluidsynth(
                        fs=44100,
                        sf2_path=str(self._sf2_path),
                    )
                    # pretty_midi returns (samples,) mono — convert to (2, samples)
                    audio = np.stack([audio_mono, audio_mono])
                    tracks.append(audio)
                    volumes.append(state.volume)

                elif state.source == "audio":
                    internal = _REVERSE_STEM_LABEL.get(label)
                    if internal and internal in self._audio_stems:
                        waveform, sr = read_audio(self._audio_stems[internal], mono=False)
                        if sr != 44100:
                            import librosa
                            waveform = np.stack([
                                librosa.resample(
                                    waveform[c], orig_sr=sr, target_sr=44100,
                                )
                                for c in range(waveform.shape[0])
                            ])
                        # Ensure stereo
                        if waveform.shape[0] == 1:
                            waveform = np.concatenate([waveform, waveform], axis=0)
                        tracks.append(waveform)
                        volumes.append(state.volume)

                dpg.set_value(_t("progress"), (i + 1) / n * 0.9)

            if not tracks:
                self._set_status("No tracks could be rendered (check stems/MIDI).")
                return

            # Pad all tracks to the length of the longest one.
            max_len = max(t.shape[1] for t in tracks)
            padded = []
            for t, vol in zip(tracks, volumes):
                pad = max_len - t.shape[1]
                if pad > 0:
                    t = np.pad(t, ((0, 0), (0, pad)))
                padded.append(t * vol)

            mix = np.sum(padded, axis=0)  # (2, max_len)

            # Normalise to prevent clipping.
            peak = float(np.max(np.abs(mix)))
            if peak > 1e-6:
                mix = (mix / peak).astype(np.float32)
            else:
                mix = mix.astype(np.float32)

            self._rendered_mix = mix
            self._mix_sr = 44100
            self._mix_duration = max_len / 44100

            # Save to _MIX_DIR.
            _MIX_DIR.mkdir(parents=True, exist_ok=True)
            src_name = app_state.audio_path.stem if app_state.audio_path else "mix"
            out_path = _MIX_DIR / f"{src_name}_mix.wav"
            sf.write(str(out_path), mix.T, 44100)
            self._mix_path = out_path
            app_state.mix_path = out_path

            dpg.set_value(_t("progress"), 1.0)
            dm, ds = divmod(int(self._mix_duration), 60)
            self._set_status(f"Done — {dm}:{ds:02d}")

            if dpg.does_item_exist(_t("result_duration")):
                dpg.set_value(
                    _t("result_duration"),
                    f"Duration: {self._mix_duration:.1f} s · 44100 Hz",
                )
            set_widget_text(_t("result_file"), str(out_path))

            for btn in (_t("play_btn"), _t("rewind_btn"), _t("save_btn")):
                if dpg.does_item_exist(btn):
                    dpg.configure_item(btn, enabled=True)

        except Exception as exc:
            log.exception("MixPanel render failed")
            self._set_status(f"Error: {exc}")
            dpg.set_value(_t("progress"), 0.0)
        finally:
            if dpg.does_item_exist(_t("render_btn")):
                dpg.configure_item(_t("render_btn"), enabled=True)

    # ------------------------------------------------------------------
    # Playback  (tick() called each frame by tick_all_midi via _ALL_MIDI_PLAYERS)
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Update time display during playback. Called each frame."""
        if not self._playing:
            return
        pos = self._play_offset + (time.time() - self._play_start)
        if pos >= self._mix_duration:
            self._stop()
            self._play_offset = 0.0
            pos = 0.0
        if dpg.does_item_exist(_t("time")):
            m, s = divmod(int(pos), 60)
            dm, ds = divmod(int(self._mix_duration), 60)
            dpg.set_value(_t("time"), f"{m}:{s:02d} / {dm}:{ds:02d}")

    def _start_play(self, offset: float = 0.0) -> None:
        # Stop waveform widgets.
        from gui.components.waveform_widget import stop_all as stop_all_waveforms
        stop_all_waveforms()

        # Stop all other MIDI players (not self).
        for w in _ALL_MIDI_PLAYERS:
            if w is not self:
                try:
                    w._stop()
                except Exception:
                    pass

        if self._rendered_mix is None:
            return

        self._play_offset = offset
        self._play_start = time.time()
        self._playing = True

        if dpg.does_item_exist(_t("stop_btn")):
            dpg.configure_item(_t("stop_btn"), enabled=True)

        mix = self._rendered_mix
        sr = self._mix_sr
        start_sample = int(offset * sr)

        def _play_audio() -> None:
            try:
                import sounddevice as sd
                sd.play(mix[:, start_sample:].T, samplerate=sr)
            except Exception as exc:
                log.error("MixPanel playback error: %s", exc)

        threading.Thread(target=_play_audio, daemon=True).start()

    def _stop(self) -> None:
        if self._playing:
            self._play_offset = min(
                self._play_offset + (time.time() - self._play_start),
                self._mix_duration,
            )
            self._playing = False
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
        if dpg.does_item_exist(_t("stop_btn")):
            dpg.configure_item(_t("stop_btn"), enabled=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        set_widget_text(_t("status"), msg)
