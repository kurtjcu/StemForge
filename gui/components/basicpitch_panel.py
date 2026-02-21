"""BasicPitch MIDI-extraction panel for StemForge.

Layout
------
Left column:
  · "Settings mode"  ○ Global  ○ Per stem  (radio button)
  · [Global controls] — Note sensitivity knob, Note sustain knob,
                        Shortest note slider  (visible in Global mode)
  · "Available stems" header + status text
  · For each of 4 stems (pre-created, initially hidden):
      [☑] Stem label
      [Per-stem controls] — same 3 knobs/slider (visible in Per stem mode)
  · [Extract MIDI] button

Right column:
  · Progress bar + status
  · Per-stem note counts
  · Save MIDI button (saves last processed stem's MIDI)

Inter-panel wiring
------------------
DemucsPanel calls notify_stems_ready(stem_paths) via the result-listener
mechanism wired in gui/app.py.  This shows the stem rows and records
which stems are available for extraction.
"""

import pathlib
import logging
import threading
import traceback

import dearpygui.dearpygui as dpg

from pipelines.basicpitch_pipeline import BasicPitchPipeline, BasicPitchConfig, BasicPitchResult
from gui.state import app_state, copy_to_clipboard, set_widget_text, get_widget_text, make_copy_callback
from gui.constants import _MIDI_DIR
from gui.components.demucs_panel import STEM_TARGETS, _STEM_LABEL
from gui.components.file_browser import FileBrowser


log = logging.getLogger("stemforge.gui.basicpitch_panel")

_P = "bp"


def _t(name: str) -> str:
    return f"{_P}_{name}"


def _knob_pair(tag_a: str, label_a: str, tip_a: str,
               tag_b: str, label_b: str, tip_b: str,
               default_a: float = 0.5, default_b: float = 0.3) -> None:
    """Render two knobs side by side with labels and tooltips."""
    with dpg.group(horizontal=True):
        with dpg.group():
            dpg.add_text(label_a, color=(175, 175, 255, 255))
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text(tip_a)
            dpg.add_knob_float(
                tag=tag_a,
                min_value=0.0,
                max_value=1.0,
                default_value=default_a,
            )

        dpg.add_spacer(width=16)

        with dpg.group():
            dpg.add_text(label_b, color=(175, 175, 255, 255))
            with dpg.tooltip(dpg.last_item()):
                dpg.add_text(tip_b)
            dpg.add_knob_float(
                tag=tag_b,
                min_value=0.0,
                max_value=1.0,
                default_value=default_b,
            )


class BasicPitchPanel:
    """BasicPitch MIDI-extraction panel with per-stem or global settings."""

    def __init__(self) -> None:
        self._pipeline = BasicPitchPipeline()
        self._thread: threading.Thread | None = None
        self._midi_path: pathlib.Path | None = None
        self._midi_paths: dict[str, pathlib.Path] = {}
        self._available_stems: dict[str, pathlib.Path] = {}
        self._save_browser = FileBrowser(
            tag="bp_save_browser",
            callback=self._on_file_save_selected,
            extensions=frozenset({".mid", ".midi"}),
            mode="save",
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        with dpg.group(horizontal=True):

            # ---- Left column: settings --------------------------------
            with dpg.child_window(width=340, height=-1, border=False):

                # Mode selector
                dpg.add_text("Settings mode", color=(175, 175, 255, 255))
                dpg.add_radio_button(
                    items=["Global", "Per stem"],
                    default_value="Global",
                    tag=_t("mode"),
                    callback=self._on_mode_change,
                    horizontal=True,
                )

                dpg.add_spacer(height=12)

                # Global controls (visible in Global mode)
                with dpg.group(tag=_t("global_controls"), show=True):
                    _knob_pair(
                        _t("global_onset"), "Note\nsensitivity",
                        "How confident the AI must be before it recognises\n"
                        "the start of a note.\n\n"
                        "Turn up  →  fewer false notes detected.\n"
                        "Turn down →  catches quiet or soft notes.",
                        _t("global_frame"), "Note\nsustain",
                        "How confident the AI must be that a note is still\n"
                        "ringing on each audio frame.\n\n"
                        "Turn up  →  only clearly audible notes are kept.\n"
                        "Turn down →  picks up quiet, fading notes.",
                        default_a=0.5, default_b=0.3,
                    )
                    dpg.add_spacer(height=8)
                    dpg.add_text("Shortest note", color=(175, 175, 255, 255))
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(
                            "Notes shorter than this are thrown away.\n\n"
                            "Slide right →  removes noise and ghost notes.\n"
                            "Slide left  →  keeps fast ornaments and trills."
                        )
                    dpg.add_slider_float(
                        tag=_t("global_min_note"),
                        min_value=20.0,
                        max_value=500.0,
                        default_value=58.0,
                        width=-1,
                        format="%.0f ms",
                    )

                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_text("Available stems", color=(175, 175, 255, 255))
                dpg.add_text(
                    "Run Separate first",
                    tag=_t("stems_status"),
                    color=(160, 160, 160, 255),
                )
                dpg.add_spacer(height=6)

                # Per-stem rows (initially hidden, shown by notify_stems_ready)
                for stem in STEM_TARGETS:
                    with dpg.group(tag=_t(f"stem_{stem}_group"), show=False):
                        dpg.add_checkbox(
                            label=_STEM_LABEL[stem],
                            tag=_t(f"stem_{stem}_check"),
                            default_value=True,
                        )
                        # Per-stem controls (visible in Per stem mode)
                        with dpg.group(
                            tag=_t(f"stem_{stem}_per_controls"),
                            show=False,
                        ):
                            with dpg.group(horizontal=True):
                                dpg.add_spacer(width=16)
                                with dpg.group():
                                    _knob_pair(
                                        _t(f"stem_{stem}_onset"), "Sensitivity",
                                        "Note onset sensitivity for this stem.",
                                        _t(f"stem_{stem}_frame"), "Sustain",
                                        "Note sustain threshold for this stem.",
                                        default_a=0.5, default_b=0.3,
                                    )
                                    dpg.add_slider_float(
                                        tag=_t(f"stem_{stem}_min_note"),
                                        min_value=20.0,
                                        max_value=500.0,
                                        default_value=58.0,
                                        width=-1,
                                        format="%.0f ms",
                                    )
                        dpg.add_spacer(height=4)

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
                        "Convert selected stem(s) to MIDI files.\n"
                        "The BasicPitch model is loaded on the first run."
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
                with dpg.group(horizontal=True):
                    dpg.add_button(
                        label="Copy",
                        callback=make_copy_callback(_t("status")),
                        width=50,
                    )
                    dpg.add_text(default_value="", tag=_t("status"), color=(160, 160, 160, 255))

                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_text("Results", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)

                # Per-stem note counts
                for stem in STEM_TARGETS:
                    dpg.add_text(
                        "—",
                        tag=_t(f"result_{stem}"),
                        color=(220, 220, 220, 255),
                    )

                dpg.add_spacer(height=4)
                dpg.add_text("—", tag=_t("midi_file"), color=(140, 140, 140, 255), wrap=350)

                dpg.add_spacer(height=12)
                dpg.add_button(
                    label="  Save MIDI as  ",
                    tag=_t("save_btn"),
                    callback=self._on_save_click,
                    width=160,
                    enabled=False,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Copy the last MIDI file to a location you choose.")

    def build_save_dialog(self) -> None:
        """Create the custom Save browser at the top DearPyGUI level."""
        self._save_browser.build()

    # ------------------------------------------------------------------
    # Inter-panel wiring
    # ------------------------------------------------------------------

    def notify_stems_ready(self, stem_paths: dict[str, pathlib.Path]) -> None:
        """Called by DemucsPanel after a successful separation run.

        Shows stem rows for available stems, hides the rest, and updates
        the status line.
        """
        self._available_stems = stem_paths

        for stem in STEM_TARGETS:
            available = stem in stem_paths
            if dpg.does_item_exist(_t(f"stem_{stem}_group")):
                dpg.configure_item(_t(f"stem_{stem}_group"), show=available)

        count = len(stem_paths)
        status = (
            f"{count} stem(s) available" if count
            else "Run Separate first"
        )
        if dpg.does_item_exist(_t("stems_status")):
            dpg.set_value(_t("stems_status"), status)

    # ------------------------------------------------------------------
    # Mode toggle
    # ------------------------------------------------------------------

    def _on_mode_change(self, sender, app_data, user_data) -> None:
        is_global = app_data == "Global"
        if dpg.does_item_exist(_t("global_controls")):
            dpg.configure_item(_t("global_controls"), show=is_global)
        for stem in STEM_TARGETS:
            tag = _t(f"stem_{stem}_per_controls")
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, show=not is_global)

    # ------------------------------------------------------------------
    # Run callback
    # ------------------------------------------------------------------

    def _on_run_click(self, sender, app_data, user_data) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _on_save_click(self, sender, app_data, user_data) -> None:
        self._save_browser.show()

    def _on_file_save_selected(self, dest: pathlib.Path) -> None:
        if not self._midi_path or not self._midi_path.exists():
            return
        if not dest.suffix:
            dest = dest.with_suffix(".mid")
        try:
            import shutil
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._midi_path, dest)
            set_widget_text(_t("status"),f"Saved → {dest}")
        except Exception as exc:
            set_widget_text(_t("status"),f"Save failed: {exc}")

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        dpg.configure_item(_t("run_btn"), enabled=False)
        dpg.configure_item(_t("save_btn"), enabled=False)
        dpg.set_value(_t("progress"), 0.0)
        # Clear previous per-stem results
        for stem in STEM_TARGETS:
            if dpg.does_item_exist(_t(f"result_{stem}")):
                dpg.set_value(_t(f"result_{stem}"), "—")
        dpg.set_value(_t("midi_file"), "—")

        try:
            # Collect stems that are checked AND available
            mode = dpg.get_value(_t("mode"))
            checked = [
                s for s in STEM_TARGETS
                if s in self._available_stems
                and dpg.does_item_exist(_t(f"stem_{s}_check"))
                and dpg.get_value(_t(f"stem_{s}_check"))
            ]

            if not checked:
                set_widget_text(
                    _t("status"),
                    "No stems selected.  Run Separate first, then tick stems above.",
                )
                return

            if not self._pipeline.is_loaded:
                set_widget_text(_t("status"),"Loading model — first run may take a moment…")
                self._pipeline.load_model()

            total = len(checked)
            self._midi_paths = {}

            for i, stem in enumerate(checked):
                stem_path = self._available_stems[stem]

                if mode == "Global":
                    config = BasicPitchConfig(
                        onset_threshold=dpg.get_value(_t("global_onset")),
                        frame_threshold=dpg.get_value(_t("global_frame")),
                        minimum_note_length=dpg.get_value(_t("global_min_note")),
                        output_dir=_MIDI_DIR,
                    )
                else:
                    config = BasicPitchConfig(
                        onset_threshold=dpg.get_value(_t(f"stem_{stem}_onset")),
                        frame_threshold=dpg.get_value(_t(f"stem_{stem}_frame")),
                        minimum_note_length=dpg.get_value(_t(f"stem_{stem}_min_note")),
                        output_dir=_MIDI_DIR,
                    )

                self._pipeline.configure(config)

                base_frac = i / total

                def _progress(pct: float, _base=base_frac, _total=total) -> None:
                    overall = _base + (pct / 100.0) / _total
                    dpg.set_value(_t("progress"), overall)
                    set_widget_text(_t("status"),f"Processing {stem}… {pct:.0f}%")

                self._pipeline.set_progress_callback(_progress)
                result = self._pipeline.run(stem_path)

                self._midi_paths[stem] = result.midi_path
                self._midi_path = result.midi_path
                app_state.midi_path = result.midi_path

                note_count = len(result.note_events)
                if dpg.does_item_exist(_t(f"result_{stem}")):
                    dpg.set_value(
                        _t(f"result_{stem}"),
                        f"{_STEM_LABEL[stem]}: {note_count} notes",
                    )

            dpg.set_value(_t("progress"), 1.0)
            set_widget_text(_t("status"),f"Done — {total} stem(s) processed")
            dpg.set_value(_t("midi_file"), str(self._midi_path))
            dpg.configure_item(_t("save_btn"), enabled=True)

        except Exception as exc:
            traceback.print_exc()
            set_widget_text(_t("status"),f"Error: {exc}")
            dpg.set_value(_t("progress"), 0.0)
        finally:
            dpg.configure_item(_t("run_btn"), enabled=True)

    # ------------------------------------------------------------------
    # Legacy stub methods
    # ------------------------------------------------------------------

    def set_stem_paths(self, stem_paths: dict) -> None:
        self.notify_stems_ready(stem_paths)

    def get_selected_stem(self):
        return None

    def get_onset_threshold(self) -> float:
        return dpg.get_value(_t("global_onset")) if dpg.does_item_exist(_t("global_onset")) else 0.5

    def get_frame_threshold(self) -> float:
        return dpg.get_value(_t("global_frame")) if dpg.does_item_exist(_t("global_frame")) else 0.3

    def run(self) -> None:
        self._on_run_click(None, None, None)

    def cancel(self) -> None:
        pass

    def _on_progress(self, percent: float) -> None:
        pass

    def _on_complete(self, midi_path) -> None:
        pass

    def _on_error(self, exc: Exception) -> None:
        pass

    def _refresh_piano_roll(self, midi_path) -> None:
        pass

    def add_result_listener(self, callback) -> None:
        pass
