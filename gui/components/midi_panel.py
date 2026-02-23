"""MIDI generation panel for StemForge.

Replaces the old BasicPitchPanel in the MIDI tab.

Layout
------
Left column (width 360):
  · Stems section  — checkboxes auto-populated by DemucsPanel or loaded
                     manually via FileBrowser; vocal stems are labelled
                     with a "(vocal)" hint so the user knows they will use
                     the faster-whisper + PYIN path instead of BasicPitch.
  · Text prompt    — optional; enables text-only or hybrid mode.
  · Musical params — Key, BPM, time signature, duration.
  · Note detection — onset/frame/min_note (ignored for vocal stems).
  · Extract MIDI button.

Right column:
  · Progress bar + status.
  · Results: per-stem note counts and merged MIDI file path.
  · Copy path button.  MIDI is auto-saved to ~/Music/StemForge.

Three modes (detected automatically from user inputs):
  * Stems only  — at least one stem checked, no prompt.
  * Text only   — no stems checked, prompt provided.
  * Hybrid      — stems checked AND prompt provided.

Inter-panel wiring
------------------
DemucsPanel calls notify_stems_ready(stem_paths) after a successful
separation run (wired in gui/app.py).  stem_paths keys are internal
stem names ("vocals", "drums", "bass", "other").

Result listeners may be registered via add_result_listener(cb).  Each
callback is invoked with (midi_path, stem_midi_data) after a successful
run so that the Generate panel can detect available MIDI conditioning.
"""

import json
import pathlib
import logging
import threading
from typing import Any, Callable

import soundfile as sf
import dearpygui.dearpygui as dpg

from pipelines.midi_pipeline import MidiPipeline, MidiConfig
from models.registry import BASICPITCH
from gui.state import app_state, set_widget_text, make_copy_callback
from gui.constants import _MIDI_DIR
from gui.ui_queue import schedule_ui
from gui.components.demucs_panel import STEM_TARGETS, _STEM_LABEL
from gui.components.file_browser import FileBrowser
from gui.components.midi_player_widget import MidiPlayerWidget, _ALL_MIDI_PLAYERS

_BP_ONSET_DEFAULT: float = BASICPITCH.default_onset
_BP_FRAME_DEFAULT: float = BASICPITCH.default_frame
_BP_MIN_NOTE_DEFAULT: float = BASICPITCH.default_min_note_ms
_BP_ONSET_RANGE: tuple[float, float] = BASICPITCH.onset_range
_BP_FRAME_RANGE: tuple[float, float] = BASICPITCH.frame_range
_BP_MIN_NOTE_RANGE: tuple[float, float] = BASICPITCH.min_note_range


log = logging.getLogger("stemforge.gui.midi_panel")

_P = "midi"

# Stems routed to faster-whisper + PYIN instead of BasicPitch.
_VOCAL_INTERNAL = frozenset({"vocals"})

# Musical key options presented in the combo box.
_KEYS: list[str] = [
    "Any",
    "C major", "G major", "D major", "A major", "E major", "B major",
    "F major", "Bb major", "Eb major", "Ab major", "Db major",
    "A minor", "E minor", "B minor", "F# minor", "C# minor",
    "D minor", "G minor", "C minor", "F minor", "Bb minor",
]

_TIME_SIGS: list[str] = ["4/4", "3/4", "6/8", "5/4", "7/8", "2/4"]

# Ace-Step cot_timesignature stores just the numerator as a string.
_COT_TS_MAP: dict[str, str] = {
    "2": "2/4", "3": "3/4", "4": "4/4", "5": "5/4", "6": "6/8", "7": "7/8",
}


def _load_acestep_meta(audio_path: pathlib.Path) -> dict | None:
    """Return the parsed Ace-Step JSON sidecar for *audio_path*, or None."""
    json_path = audio_path.with_suffix(".json")
    if not json_path.exists():
        return None
    try:
        with json_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        log.debug("Could not read Ace-Step metadata from %s", json_path)
        return None


def _parse_ts(meta: dict) -> str | None:
    """Extract a time-signature string from Ace-Step metadata, or None."""
    raw = meta.get("timesignature", "").strip()
    if not raw:
        raw = str(meta.get("cot_timesignature", "")).strip()
    if not raw:
        return None
    if "/" in raw:
        return raw if raw in _TIME_SIGS else None
    return _COT_TS_MAP.get(raw)


def _t(name: str) -> str:
    return f"{_P}_{name}"


def _safe_tag(label: str) -> str:
    """Sanitise a display label for use in a DPG tag."""
    return (
        label.replace(" ", "_")
             .replace("&", "and")
             .replace("/", "_")
             .replace(".", "_")
             .replace("(", "")
             .replace(")", "")
    )


class MidiPanel:
    """MIDI generation panel using the MidiPipeline backend."""

    def __init__(self) -> None:
        self._pipeline = MidiPipeline()
        self._thread: threading.Thread | None = None
        self._merged_midi_data: Any = None   # PrettyMIDI; only written on explicit Save
        # Stems populated by notify_stems_ready() — internal name → path.
        self._available_stems: dict[str, pathlib.Path] = {}
        # Manually loaded extra stems — display label → path.
        self._manual_stems: dict[str, pathlib.Path] = {}
        # Callbacks invoked with (midi_path, stem_midi_data) on success.
        self._result_listeners: list[
            Callable[[pathlib.Path, dict[str, Any]], None]
        ] = []

        # Per-stem MIDI players — populated after each extraction run.
        self._stem_players: dict[str, MidiPlayerWidget] = {}
        # Per-stem in-memory MIDI objects — used by "Save as" buttons.
        self._stem_midi_data: dict[str, Any] = {}
        # State for the shared "Save MIDI as..." dialog.
        self._save_midi_data: Any = None   # PrettyMIDI object to write on save
        self._save_midi_browser: FileBrowser | None = None

        self._stem_browser = FileBrowser(
            tag="midi_stem_browser",
            callback=self._on_stem_file_selected,
            extensions=frozenset({".wav", ".flac", ".mp3", ".ogg", ".aiff", ".aif"}),
            mode="open",
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        with dpg.group(horizontal=True):

            # ---- Left column: controls --------------------------------
            with dpg.child_window(width=370, height=-1, border=False):

                # -- Stems section --
                dpg.add_text("Stems", color=(175, 175, 255, 255))
                dpg.add_text(
                    "Run Separate first, or load a file below.",
                    tag=_t("stems_status"),
                    color=(160, 160, 160, 255),
                    wrap=340,
                )
                dpg.add_spacer(height=4)

                # Pre-created stem checkboxes (initially hidden).
                for stem in STEM_TARGETS:
                    display = _STEM_LABEL[stem]
                    hint = " (vocal)" if stem in _VOCAL_INTERNAL else ""
                    with dpg.group(tag=_t(f"stem_{stem}_group"), show=False):
                        dpg.add_checkbox(
                            label=f"{display}{hint}",
                            tag=_t(f"stem_{stem}_check"),
                            default_value=False,
                            callback=self._on_stem_check,
                            user_data=stem,
                        )
                        dpg.add_spacer(height=2)

                # Container for dynamically added manual stems.
                dpg.add_spacer(height=4)
                with dpg.group(tag=_t("manual_stems_group")):
                    pass  # populated by _on_stem_file_selected

                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="  Load stem file  ",
                        callback=self._on_load_stem_click,
                        height=28,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(
                            "Load an individual audio stem directly\n"
                            "(bypasses the Separate step)."
                        )

                dpg.add_spacer(height=12)
                dpg.add_separator()

                # -- Text prompt --
                dpg.add_text("Text prompt", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Optional - describes the musical style.\n\n"
                        "- With stems: used as conditioning metadata and key hint.\n"
                        "- Without stems: generates a chord progression only."
                    )
                dpg.add_input_text(
                    tag=_t("prompt"),
                    hint="e.g. C major jazz ballad, slow tempo",
                    width=-1,
                )

                dpg.add_spacer(height=12)
                dpg.add_separator()

                # -- Musical parameters --
                dpg.add_text("Musical parameters", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)

                dpg.add_text("Key", color=(140, 140, 180, 255))
                dpg.add_combo(
                    items=_KEYS,
                    default_value="Any",
                    tag=_t("key"),
                    width=-1,
                )

                dpg.add_spacer(height=6)
                dpg.add_text("Time signature", color=(140, 140, 180, 255))
                dpg.add_combo(
                    items=_TIME_SIGS,
                    default_value="4/4",
                    tag=_t("time_sig"),
                    width=-1,
                )

                dpg.add_spacer(height=6)
                dpg.add_text("BPM", color=(140, 140, 180, 255))
                dpg.add_drag_int(
                    tag=_t("bpm"),
                    default_value=120,
                    min_value=20,
                    max_value=300,
                    speed=0.5,
                    width=-1,
                )

                dpg.add_spacer(height=6)
                dpg.add_text("Duration (seconds)", color=(140, 140, 180, 255))
                dpg.add_slider_float(
                    tag=_t("duration"),
                    default_value=30.0,
                    min_value=1.0,
                    max_value=600.0,
                    format="%.0f s",
                    width=-1,
                )

                dpg.add_spacer(height=12)
                dpg.add_separator()

                # -- Note detection (instruments only — not used for vocals) --
                with dpg.group(horizontal=True):
                    dpg.add_text("Note detection", color=(175, 175, 255, 255))
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(
                            "BasicPitch settings applied to instrument stems.\n"
                            "Vocal stems always use faster-whisper + PYIN."
                        )
                    dpg.add_spacer(width=10)
                    dpg.add_button(
                        label="Reset to defaults",
                        callback=self._on_reset_note_detection,
                        height=20,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text("Restore Sensitivity, Sustain, and Shortest note to defaults.")
                dpg.add_spacer(height=4)

                with dpg.group(horizontal=True):
                    # -- Sensitivity knob --
                    with dpg.group():
                        dpg.add_text("Sensitivity", color=(140, 140, 180, 255))
                        dpg.add_knob_float(
                            tag=_t("onset"),
                            min_value=_BP_ONSET_RANGE[0],
                            max_value=_BP_ONSET_RANGE[1],
                            default_value=_BP_ONSET_DEFAULT,
                            callback=self._on_onset_knob,
                        )
                        with dpg.group(horizontal=True):
                            dpg.add_text(f"{_BP_ONSET_RANGE[0]:.2f}", color=(100, 100, 130, 255))
                            dpg.add_drag_float(
                                tag=_t("onset_val"),
                                default_value=_BP_ONSET_DEFAULT,
                                min_value=_BP_ONSET_RANGE[0],
                                max_value=_BP_ONSET_RANGE[1],
                                speed=0.005,
                                format="%.2f",
                                width=62,
                                callback=self._on_onset_drag,
                            )
                            dpg.add_text(f"{_BP_ONSET_RANGE[1]:.2f}", color=(100, 100, 130, 255))

                    dpg.add_spacer(width=16)

                    # -- Sustain knob --
                    with dpg.group():
                        dpg.add_text("Sustain", color=(140, 140, 180, 255))
                        dpg.add_knob_float(
                            tag=_t("frame"),
                            min_value=_BP_FRAME_RANGE[0],
                            max_value=_BP_FRAME_RANGE[1],
                            default_value=_BP_FRAME_DEFAULT,
                            callback=self._on_frame_knob,
                        )
                        with dpg.group(horizontal=True):
                            dpg.add_text(f"{_BP_FRAME_RANGE[0]:.2f}", color=(100, 100, 130, 255))
                            dpg.add_drag_float(
                                tag=_t("frame_val"),
                                default_value=_BP_FRAME_DEFAULT,
                                min_value=_BP_FRAME_RANGE[0],
                                max_value=_BP_FRAME_RANGE[1],
                                speed=0.005,
                                format="%.2f",
                                width=62,
                                callback=self._on_frame_drag,
                            )
                            dpg.add_text(f"{_BP_FRAME_RANGE[1]:.2f}", color=(100, 100, 130, 255))

                dpg.add_spacer(height=6)
                dpg.add_text("Shortest note", color=(140, 140, 180, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Notes shorter than this are discarded.\n"
                        "Slide right to remove noise and ghost notes."
                    )
                dpg.add_slider_float(
                    tag=_t("min_note"),
                    default_value=_BP_MIN_NOTE_DEFAULT,
                    min_value=_BP_MIN_NOTE_RANGE[0],
                    max_value=_BP_MIN_NOTE_RANGE[1],
                    format="%.0f ms",
                    width=-1,
                )

                dpg.add_spacer(height=14)
                dpg.add_button(
                    label="  Extract MIDI  ",
                    tag=_t("run_btn"),
                    callback=self._on_run_click,
                    width=-1,
                    height=40,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Convert stems to MIDI, or generate a chord progression from\n"
                        "the musical parameters above (if no stems are selected).\n\n"
                        "The model is loaded on the first run.\n"
                        "Output is auto-saved to ~/Music/StemForge."
                    )

            # ---- Right column: results --------------------------------
            with dpg.child_window(width=-1, height=-1, border=False):

                dpg.add_text("Progress", color=(175, 175, 255, 255))
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
                        default_value="",
                        tag=_t("status"),
                        color=(160, 160, 160, 255),
                        wrap=350,
                    )

                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_text("Results", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)

                # Per-stem note counts (empty until a run completes).
                for stem in STEM_TARGETS:
                    dpg.add_text(
                        "",
                        tag=_t(f"result_{stem}"),
                        color=(220, 220, 220, 255),
                    )
                # Extra tracks (manual stems, generated chord progression).
                dpg.add_text("", tag=_t("result_extra"), color=(220, 220, 220, 255))

                dpg.add_spacer(height=8)
                dpg.add_text("Merged MIDI:", color=(140, 140, 180, 255))
                dpg.add_text("", tag=_t("midi_file"), color=(140, 140, 140, 255), wrap=350)

                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="Copy path",
                        callback=make_copy_callback(_t("midi_file")),
                        width=90,
                    )
                    dpg.add_button(
                        label="Save merged",
                        tag=_t("save_merged_btn"),
                        callback=self._on_save_merged_click,
                        width=100,
                        enabled=False,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text("Save the merged multi-track MIDI to a file.")

                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_text("Stem Players", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)
                dpg.add_text(
                    "Extract MIDI to see per-stem players here.",
                    tag=_t("players_empty"),
                    color=(120, 120, 140, 255),
                    wrap=350,
                )
                # Per-stem player widgets are added here dynamically after each run.
                with dpg.group(tag=_t("players_group")):
                    pass

    def build_browsers(self) -> None:
        """Create the stem file browser at the top DearPyGUI level."""
        self._stem_browser.build()
        self._save_midi_browser = FileBrowser(
            tag="midi_save_midi_browser",
            callback=self._on_save_midi_selected,
            extensions=frozenset({".mid", ".midi"}),
            mode="save",
        )
        self._save_midi_browser.build()

    # ------------------------------------------------------------------
    # Inter-panel wiring
    # ------------------------------------------------------------------

    def notify_stems_ready(self, stem_paths: dict[str, pathlib.Path]) -> None:
        """Called by DemucsPanel after a successful separation run.

        *stem_paths* uses internal names ("vocals", "drums", "bass", "other").
        Shows stem checkboxes for available stems and hides the rest.
        Also auto-loads an Ace-Step JSON sidecar from the source audio file
        if one exists.

        May be called from a background thread — all DPG calls are scheduled.
        """
        self._available_stems = dict(stem_paths)

        count = sum(1 for stem in STEM_TARGETS if stem in stem_paths)
        status = (
            f"{count} stem(s) ready - select and click Extract MIDI."
            if count else "Run Separate first, or load a file below."
        )

        def _update_ui():
            for stem in STEM_TARGETS:
                available = stem in stem_paths
                if dpg.does_item_exist(_t(f"stem_{stem}_group")):
                    dpg.configure_item(_t(f"stem_{stem}_group"), show=available)
            if dpg.does_item_exist(_t("stems_status")):
                dpg.set_value(_t("stems_status"), status)
        schedule_ui(_update_ui)

        # Auto-set duration from the first available stem.
        if stem_paths:
            self._set_duration_from_path(next(iter(stem_paths.values())))

        # Pre-fill musical parameters from an Ace-Step JSON sidecar if present.
        if (src := app_state.audio_path) and (meta := _load_acestep_meta(src)):
            self.apply_acestep_meta(meta)
            log.info("MidiPanel: pre-filled parameters from %s", src.with_suffix(".json").name)

    def apply_acestep_meta(self, meta: dict) -> None:
        """Pre-fill musical parameters from an Ace-Step JSON sidecar dict.

        Safe to call from any thread; all DPG calls are scheduled on the main
        thread.  Silently skips any widget that does not yet exist.
        """
        bpm_val = meta.get("bpm")
        keyscale = meta.get("keyscale", "").strip()
        ts = _parse_ts(meta)
        dur_raw = meta.get("duration")
        caption = meta.get("caption", "").strip()

        def _apply():
            def _set(tag: str, value) -> None:
                if dpg.does_item_exist(tag):
                    dpg.set_value(tag, value)

            if bpm_val:
                _set(_t("bpm"), int(bpm_val))
            if keyscale and keyscale in _KEYS:
                _set(_t("key"), keyscale)
            if ts:
                _set(_t("time_sig"), ts)
            if dur_raw:
                dur = float(dur_raw)
                if dpg.does_item_exist(_t("duration")):
                    dpg.configure_item(_t("duration"), max_value=max(600.0, dur))
                    dpg.set_value(_t("duration"), dur)
            if caption:
                if dpg.does_item_exist(_t("prompt")) and not dpg.get_value(_t("prompt")).strip():
                    dpg.set_value(_t("prompt"), caption)

        schedule_ui(_apply)

        log.debug(
            "MidiPanel: applied Ace-Step metadata — bpm=%s key=%s ts=%s dur=%s",
            meta.get("bpm"), keyscale, ts, dur_raw,
        )

    def add_result_listener(
        self,
        callback: Callable[[pathlib.Path, dict[str, Any]], None],
    ) -> None:
        """Register *callback* invoked with (midi_path, stem_midi_data) on success."""
        self._result_listeners.append(callback)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_stem_check(self, sender, app_data, user_data) -> None:
        """Called when a separated-stem checkbox is toggled. app_data=new bool, user_data=stem key."""
        if app_data:  # just checked — set duration to this stem's length
            stem = user_data
            if stem in self._available_stems:
                self._set_duration_from_path(self._available_stems[stem])

    def _read_duration(self, path: pathlib.Path) -> float | None:
        """Return duration in seconds from audio file metadata, or None on error."""
        try:
            info = sf.info(str(path))
            return info.frames / info.samplerate
        except Exception:
            return None

    def _apply_duration(self, seconds: float) -> None:
        """Write *seconds* to the duration slider, expanding the ceiling if needed."""
        seconds = max(1.0, min(600.0, seconds))
        schedule_ui(lambda _s=seconds: (
            dpg.configure_item(_t("duration"), max_value=max(600.0, _s)),
            dpg.set_value(_t("duration"), _s),
        ) if dpg.does_item_exist(_t("duration")) else None)

    def _set_duration_from_path(self, path: pathlib.Path) -> None:
        """Convenience: read duration from *path* and apply it to the slider."""
        if (d := self._read_duration(path)) is not None:
            self._apply_duration(d)

    def _on_onset_knob(self, sender, app_data, user_data) -> None:
        if dpg.does_item_exist(_t("onset_val")):
            dpg.set_value(_t("onset_val"), app_data)

    def _on_onset_drag(self, sender, app_data, user_data) -> None:
        if dpg.does_item_exist(_t("onset")):
            dpg.set_value(_t("onset"), app_data)

    def _on_frame_knob(self, sender, app_data, user_data) -> None:
        if dpg.does_item_exist(_t("frame_val")):
            dpg.set_value(_t("frame_val"), app_data)

    def _on_frame_drag(self, sender, app_data, user_data) -> None:
        if dpg.does_item_exist(_t("frame")):
            dpg.set_value(_t("frame"), app_data)

    def _on_reset_note_detection(self, sender, app_data, user_data) -> None:
        for tag, val in (
            (_t("onset"),     _BP_ONSET_DEFAULT),
            (_t("onset_val"), _BP_ONSET_DEFAULT),
            (_t("frame"),     _BP_FRAME_DEFAULT),
            (_t("frame_val"), _BP_FRAME_DEFAULT),
            (_t("min_note"),  _BP_MIN_NOTE_DEFAULT),
        ):
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, val)

    def _on_run_click(self, sender, app_data, user_data) -> None:
        if self._thread and self._thread.is_alive():
            return
        # Capture all UI values on the main thread before spawning bg work
        ui_vals = self._capture_run_inputs()
        self._thread = threading.Thread(target=self._run, args=(ui_vals,), daemon=True)
        self._thread.start()

    def _capture_run_inputs(self) -> dict:
        """Read all DPG widget values needed by _run() — main thread only."""
        prompt = (
            dpg.get_value(_t("prompt")).strip()
            if dpg.does_item_exist(_t("prompt")) else ""
        ) or None
        key      = dpg.get_value(_t("key"))      if dpg.does_item_exist(_t("key"))      else "Any"
        time_sig = dpg.get_value(_t("time_sig")) if dpg.does_item_exist(_t("time_sig")) else "4/4"
        bpm      = float(dpg.get_value(_t("bpm")))      if dpg.does_item_exist(_t("bpm"))      else 120.0
        duration = float(dpg.get_value(_t("duration"))) if dpg.does_item_exist(_t("duration")) else 30.0
        onset    = dpg.get_value(_t("onset"))    if dpg.does_item_exist(_t("onset"))    else 0.5
        frame    = dpg.get_value(_t("frame"))    if dpg.does_item_exist(_t("frame"))    else 0.3
        min_note = dpg.get_value(_t("min_note")) if dpg.does_item_exist(_t("min_note")) else 58.0

        # Determine which stems are checked
        stems: dict[str, pathlib.Path] = {}
        for stem in STEM_TARGETS:
            check_tag = _t(f"stem_{stem}_check")
            if (
                stem in self._available_stems
                and dpg.does_item_exist(check_tag)
                and dpg.get_value(check_tag)
            ):
                stems[_STEM_LABEL[stem]] = self._available_stems[stem]
        for label, path in self._manual_stems.items():
            check_tag = _t(f"manual_{label}_check")
            if dpg.does_item_exist(check_tag) and dpg.get_value(check_tag):
                stems[label] = path

        return dict(
            prompt=prompt, key=key, time_sig=time_sig, bpm=bpm,
            duration=duration, onset=onset, frame=frame, min_note=min_note,
            stems=stems,
        )

    def _on_load_stem_click(self, sender, app_data, user_data) -> None:
        self._stem_browser.show()

    def _on_stem_file_selected(self, path: pathlib.Path) -> None:
        """Add a manually loaded audio file as a stem for MIDI extraction.

        Also checks for an Ace-Step JSON sidecar alongside the selected file.
        """
        label = path.stem
        self._manual_stems[label] = path

        self._set_duration_from_path(path)

        if meta := _load_acestep_meta(path):
            self.apply_acestep_meta(meta)
            log.info("MidiPanel: pre-filled parameters from %s", path.with_suffix(".json").name)

        row_tag = _t(f"manual_{label}_group")
        if not dpg.does_item_exist(row_tag):
            with dpg.group(
                tag=row_tag,
                horizontal=True,
                parent=_t("manual_stems_group"),
            ):
                dpg.add_checkbox(
                    label=label,
                    tag=_t(f"manual_{label}_check"),
                    default_value=True,
                )
                dpg.add_text("(manual)", color=(140, 140, 140, 255))

        if dpg.does_item_exist(_t("stems_status")):
            total = (
                sum(1 for s in STEM_TARGETS if s in self._available_stems)
                + len(self._manual_stems)
            )
            dpg.set_value(
                _t("stems_status"),
                f"{total} stem(s) ready - select and click Extract MIDI.",
            )

    # ------------------------------------------------------------------
    # Per-stem player management
    # ------------------------------------------------------------------

    def _rebuild_stem_players(
        self,
        stem_midi_data: dict[str, Any],
    ) -> None:
        """Clear old per-stem players and create new ones after extraction.

        *stem_midi_data* maps display label → PrettyMIDI object.
        Called from the pipeline background thread — all DPG widget creation
        is scheduled on the main thread.
        """
        # Remove old player instances from the global tick/stop list.
        for player in self._stem_players.values():
            try:
                _ALL_MIDI_PLAYERS.remove(player)
            except ValueError:
                pass
        self._stem_players.clear()
        self._stem_midi_data = dict(stem_midi_data)

        def _build():
            # Clear the dynamic group and update the empty-state label.
            if dpg.does_item_exist(_t("players_group")):
                dpg.delete_item(_t("players_group"), children_only=True)
            if dpg.does_item_exist(_t("players_empty")):
                dpg.configure_item(_t("players_empty"), show=not bool(stem_midi_data))

            for label, midi_obj in stem_midi_data.items():
                safe = _safe_tag(label)
                player = MidiPlayerWidget(f"midi_{safe}")
                self._stem_players[label] = player

                with dpg.group(parent=_t("players_group")):
                    dpg.add_text(label, color=(200, 200, 255, 255))
                    player.build_ui()

                    dpg.add_text(
                        "(not saved to disk)",
                        color=(120, 120, 140, 255),
                        wrap=340,
                    )
                    dpg.add_button(
                        label="Save as...",
                        callback=self._make_save_cb(midi_obj),
                        width=90,
                    )

                    dpg.add_separator()
                    dpg.add_spacer(height=6)

                player.load_from_midi(midi_obj, label)

        schedule_ui(_build)

    def _make_save_cb(self, midi_obj: Any):
        """Return a DPG callback that writes *midi_obj* to a user-chosen path."""
        def _cb(sender, app_data, user_data):
            self._save_midi_data = midi_obj
            if self._save_midi_browser:
                self._save_midi_browser.show()
        return _cb

    def _on_save_midi_selected(self, dest: pathlib.Path) -> None:
        """Write the pending in-memory MIDI object to *dest*."""
        if self._save_midi_data is None:
            return
        from utils.midi_io import write_midi
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_midi(self._save_midi_data, dest)
            log.info("Saved MIDI to %s", dest)
        except Exception as exc:
            log.error("MidiPanel save MIDI error: %s", exc)

    def _on_save_merged_click(self, sender, app_data, user_data) -> None:
        """Show the save browser to write the merged multi-track MIDI."""
        if self._merged_midi_data is None:
            return
        self._save_midi_data = self._merged_midi_data
        if self._save_midi_browser:
            self._save_midi_browser.show()

    # ------------------------------------------------------------------
    # Background pipeline execution
    # ------------------------------------------------------------------

    def _run(self, ui_vals: dict) -> None:
        schedule_ui(lambda: dpg.configure_item(_t("run_btn"), enabled=False))
        schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))
        set_widget_text(_t("status"), "")

        # Clear previous results.
        def _clear_results():
            for stem in STEM_TARGETS:
                if dpg.does_item_exist(_t(f"result_{stem}")):
                    dpg.set_value(_t(f"result_{stem}"), "")
            if dpg.does_item_exist(_t("result_extra")):
                dpg.set_value(_t("result_extra"), "")
        schedule_ui(_clear_results)
        set_widget_text(_t("midi_file"), "")

        try:
            # ---- Inputs (pre-captured on main thread) ------------------
            prompt   = ui_vals["prompt"]
            key      = ui_vals["key"]
            time_sig = ui_vals["time_sig"]
            bpm      = ui_vals["bpm"]
            duration = ui_vals["duration"]
            onset    = ui_vals["onset"]
            frame    = ui_vals["frame"]
            min_note = ui_vals["min_note"]
            stems    = ui_vals["stems"]

            if not stems and not prompt:
                set_widget_text(
                    _t("status"),
                    "Nothing to process.  "
                    "Select at least one stem, or enter a text prompt.",
                )
                return

            # ---- Configure and run pipeline ----------------------------
            config = MidiConfig(
                prompt=prompt,
                duration_seconds=duration,
                key=key,
                time_signature=time_sig,
                bpm=bpm,
                onset_threshold=onset,
                frame_threshold=frame,
                minimum_note_length=min_note,
                output_dir=_MIDI_DIR,
            )
            self._pipeline.configure(config)

            if not self._pipeline.is_loaded:
                set_widget_text(_t("status"), "Loading model - first run may take a moment...")

            self._pipeline.load_model()

            def _progress(pct: float) -> None:
                schedule_ui(lambda _p=pct: dpg.set_value(_t("progress"), _p / 100.0))
                set_widget_text(_t("status"), f"{pct:.0f}%")

            self._pipeline.set_progress_callback(_progress)

            mode_label = (
                "text-only" if not stems else
                "hybrid" if prompt else "stems"
            )
            set_widget_text(_t("status"), f"Running ({mode_label} mode)...")
            result = self._pipeline.run(stems)

            # ---- Update state and results ------------------------------
            self._merged_midi_data = result.merged_midi_data

            # Build result strings for UI
            stem_results: dict[str, str] = {}
            for stem in STEM_TARGETS:
                display = _STEM_LABEL[stem]
                count = result.note_counts.get(display)
                if count is not None:
                    stem_results[stem] = f"{display}: {count} notes"

            extra_lines = [
                f"{lbl}: {cnt} notes"
                for lbl, cnt in result.note_counts.items()
                if lbl not in set(_STEM_LABEL.values())
            ]
            extra_text = "\n".join(extra_lines) if extra_lines else ""
            total = result.total_notes
            track_count = len(result.note_counts)

            def _show_results():
                for stem, text in stem_results.items():
                    if dpg.does_item_exist(_t(f"result_{stem}")):
                        dpg.set_value(_t(f"result_{stem}"), text)
                if extra_text and dpg.does_item_exist(_t("result_extra")):
                    dpg.set_value(_t("result_extra"), extra_text)
                if dpg.does_item_exist(_t("save_merged_btn")):
                    dpg.configure_item(_t("save_merged_btn"), enabled=True)
                dpg.set_value(_t("progress"), 1.0)
            schedule_ui(_show_results)

            set_widget_text(_t("midi_file"), "(not saved — click Save merged to write)")
            set_widget_text(
                _t("status"),
                f"Done - {total} notes, {track_count} track(s)",
            )

            # Rebuild per-stem player widgets.
            self._rebuild_stem_players(result.stem_midi_data)

            # Notify downstream panels (e.g. Mix, Generate, Export).
            for cb in self._result_listeners:
                try:
                    cb(result.merged_midi_data, result.stem_midi_data)
                except Exception:
                    log.exception("MidiPanel result listener raised")

        except Exception as exc:
            log.exception("MidiPanel._run failed")
            set_widget_text(_t("status"), f"Error: {exc}")
            schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))

        finally:
            schedule_ui(lambda: dpg.configure_item(_t("run_btn"), enabled=True))
