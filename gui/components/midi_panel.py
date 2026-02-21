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
callback is invoked with (midi_path, stem_midi_paths) after a successful
run so that the Generate panel can detect available MIDI conditioning.
"""

import pathlib
import logging
import threading
from typing import Callable

import dearpygui.dearpygui as dpg

from pipelines.midi_pipeline import MidiPipeline, MidiConfig
from models.registry import BASICPITCH
from gui.state import app_state, set_widget_text, make_copy_callback
from gui.constants import _MIDI_DIR
from gui.components.demucs_panel import STEM_TARGETS, _STEM_LABEL
from gui.components.file_browser import FileBrowser

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


def _t(name: str) -> str:
    return f"{_P}_{name}"


class MidiPanel:
    """MIDI generation panel using the MidiPipeline backend."""

    def __init__(self) -> None:
        self._pipeline = MidiPipeline()
        self._thread: threading.Thread | None = None
        self._midi_path: pathlib.Path | None = None
        # Stems populated by notify_stems_ready() — internal name → path.
        self._available_stems: dict[str, pathlib.Path] = {}
        # Manually loaded extra stems — display label → path.
        self._manual_stems: dict[str, pathlib.Path] = {}
        # Callbacks invoked with (midi_path, stem_midi_paths) on success.
        self._result_listeners: list[
            Callable[[pathlib.Path, dict[str, pathlib.Path]], None]
        ] = []

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
                            default_value=True,
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
                        "Optional — describes the musical style.\n\n"
                        "· With stems: used as conditioning metadata and key hint.\n"
                        "· Without stems: generates a chord progression only."
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
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "For stems: notes beyond this point are clipped.\n"
                        "For text-only: the progression is extended to fill this duration."
                    )
                dpg.add_slider_float(
                    tag=_t("duration"),
                    default_value=30.0,
                    min_value=1.0,
                    max_value=120.0,
                    format="%.0f s",
                    width=-1,
                )

                dpg.add_spacer(height=12)
                dpg.add_separator()

                # -- Note detection (instruments only — not used for vocals) --
                dpg.add_text("Note detection", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "BasicPitch settings applied to instrument stems.\n"
                        "Vocal stems always use faster-whisper + PYIN."
                    )
                dpg.add_spacer(height=4)

                with dpg.group(horizontal=True):
                    with dpg.group():
                        dpg.add_text("Sensitivity", color=(140, 140, 180, 255))
                        with dpg.tooltip(dpg.last_item()):
                            dpg.add_text(
                                "Onset confidence threshold.\n"
                                "Higher = fewer false note detections."
                            )
                        dpg.add_knob_float(
                            tag=_t("onset"),
                            min_value=_BP_ONSET_RANGE[0],
                            max_value=_BP_ONSET_RANGE[1],
                            default_value=_BP_ONSET_DEFAULT,
                        )
                    dpg.add_spacer(width=20)
                    with dpg.group():
                        dpg.add_text("Sustain", color=(140, 140, 180, 255))
                        with dpg.tooltip(dpg.last_item()):
                            dpg.add_text(
                                "Frame confidence threshold.\n"
                                "Higher = shorter detected note durations."
                            )
                        dpg.add_knob_float(
                            tag=_t("frame"),
                            min_value=_BP_FRAME_RANGE[0],
                            max_value=_BP_FRAME_RANGE[1],
                            default_value=_BP_FRAME_DEFAULT,
                        )

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
                dpg.add_text("MIDI file:", color=(140, 140, 180, 255))
                dpg.add_text("", tag=_t("midi_file"), color=(140, 140, 140, 255), wrap=350)

                dpg.add_spacer(height=8)
                dpg.add_button(
                    label="Copy path",
                    callback=make_copy_callback(_t("midi_file")),
                    width=90,
                )

    def build_browsers(self) -> None:
        """Create the stem file browser at the top DearPyGUI level."""
        self._stem_browser.build()

    # ------------------------------------------------------------------
    # Inter-panel wiring
    # ------------------------------------------------------------------

    def notify_stems_ready(self, stem_paths: dict[str, pathlib.Path]) -> None:
        """Called by DemucsPanel after a successful separation run.

        *stem_paths* uses internal names ("vocals", "drums", "bass", "other").
        Shows stem checkboxes for available stems and hides the rest.
        """
        self._available_stems = dict(stem_paths)

        count = 0
        for stem in STEM_TARGETS:
            available = stem in stem_paths
            if dpg.does_item_exist(_t(f"stem_{stem}_group")):
                dpg.configure_item(_t(f"stem_{stem}_group"), show=available)
            if available:
                count += 1

        status = (
            f"{count} stem(s) ready — select and click Extract MIDI."
            if count else "Run Separate first, or load a file below."
        )
        if dpg.does_item_exist(_t("stems_status")):
            dpg.set_value(_t("stems_status"), status)

    def add_result_listener(
        self,
        callback: Callable[[pathlib.Path, dict[str, pathlib.Path]], None],
    ) -> None:
        """Register *callback* invoked with (midi_path, stem_midi_paths) on success."""
        self._result_listeners.append(callback)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_run_click(self, sender, app_data, user_data) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _on_load_stem_click(self, sender, app_data, user_data) -> None:
        self._stem_browser.show()

    def _on_stem_file_selected(self, path: pathlib.Path) -> None:
        """Add a manually loaded audio file as a stem for MIDI extraction."""
        label = path.stem
        self._manual_stems[label] = path

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
                f"{total} stem(s) ready — select and click Extract MIDI.",
            )

    # ------------------------------------------------------------------
    # Background pipeline execution
    # ------------------------------------------------------------------

    def _run(self) -> None:
        dpg.configure_item(_t("run_btn"), enabled=False)
        dpg.set_value(_t("progress"), 0.0)
        set_widget_text(_t("status"), "")

        # Clear previous results.
        for stem in STEM_TARGETS:
            if dpg.does_item_exist(_t(f"result_{stem}")):
                dpg.set_value(_t(f"result_{stem}"), "")
        if dpg.does_item_exist(_t("result_extra")):
            dpg.set_value(_t("result_extra"), "")
        if dpg.does_item_exist(_t("midi_file")):
            set_widget_text(_t("midi_file"), "")

        try:
            # ---- Collect inputs ----------------------------------------
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

            # Build stems dict: display label → path.
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
                set_widget_text(_t("status"), "Loading model — first run may take a moment…")

            self._pipeline.load_model()

            def _progress(pct: float) -> None:
                dpg.set_value(_t("progress"), pct / 100.0)
                set_widget_text(_t("status"), f"{pct:.0f}%")

            self._pipeline.set_progress_callback(_progress)

            mode_label = (
                "text-only" if not stems else
                "hybrid" if prompt else "stems"
            )
            set_widget_text(_t("status"), f"Running ({mode_label} mode)…")
            result = self._pipeline.run(stems)

            # ---- Update state and results ------------------------------
            self._midi_path = result.midi_path
            app_state.midi_path = result.midi_path
            app_state.midi_paths = result.stem_midi_paths

            for stem in STEM_TARGETS:
                display = _STEM_LABEL[stem]
                count = result.note_counts.get(display)
                if count is not None and dpg.does_item_exist(_t(f"result_{stem}")):
                    dpg.set_value(_t(f"result_{stem}"), f"{display}: {count} notes")

            extra_lines = [
                f"{lbl}: {cnt} notes"
                for lbl, cnt in result.note_counts.items()
                if lbl not in set(_STEM_LABEL.values())
            ]
            if extra_lines and dpg.does_item_exist(_t("result_extra")):
                dpg.set_value(_t("result_extra"), "\n".join(extra_lines))

            set_widget_text(_t("midi_file"), str(result.midi_path))
            dpg.set_value(_t("progress"), 1.0)
            set_widget_text(
                _t("status"),
                f"Done — {result.total_notes} notes, "
                f"{len(result.note_counts)} track(s)",
            )

            # Notify downstream panels (e.g. Generate).
            for cb in self._result_listeners:
                try:
                    cb(result.midi_path, result.stem_midi_paths)
                except Exception:
                    log.exception("MidiPanel result listener raised")

        except Exception as exc:
            log.exception("MidiPanel._run failed")
            set_widget_text(_t("status"), f"Error: {exc}")
            dpg.set_value(_t("progress"), 0.0)

        finally:
            dpg.configure_item(_t("run_btn"), enabled=True)
