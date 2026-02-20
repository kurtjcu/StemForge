"""
BasicPitch MIDI-extraction panel for StemForge.

Left column: stem selector, two rotary knobs (note sensitivity and note
sustain), a duration slider for filtering short notes, and a Run button.
Right column: progress bar, status, note count, and a Save MIDI button.

The pipeline runs on a daemon thread; all DearPyGUI updates are
thread-safe calls to dpg.set_value / dpg.configure_item.
"""

import pathlib
import logging
import threading
import traceback

import dearpygui.dearpygui as dpg

from pipelines.basicpitch_pipeline import BasicPitchPipeline, BasicPitchConfig, BasicPitchResult
from gui.state import app_state
from gui.constants import _MIDI_DIR
from gui.components.demucs_panel import STEM_TARGETS, _STEM_LABEL


log = logging.getLogger("stemforge.gui.basicpitch_panel")

_P = "bp"   # tag namespace


def _t(name: str) -> str:
    return f"{_P}_{name}"


class BasicPitchPanel:
    """BasicPitch MIDI-extraction panel."""

    def __init__(self) -> None:
        self._pipeline = BasicPitchPipeline()
        self._thread: threading.Thread | None = None
        self._midi_path: pathlib.Path | None = None

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        with dpg.group(horizontal=True):

            # ---- Left column: settings --------------------------------
            with dpg.child_window(width=320, height=-1, border=False):

                dpg.add_text("Source part", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Which separated part to convert to MIDI.\n"
                        "Run the Separate step first."
                    )
                dpg.add_combo(
                    items=[_STEM_LABEL[s] for s in STEM_TARGETS],
                    default_value=_STEM_LABEL["vocals"],
                    tag=_t("stem"),
                    width=-1,
                )

                dpg.add_spacer(height=18)

                # Two knobs side by side
                with dpg.group(horizontal=True):
                    with dpg.group():
                        dpg.add_text("Note\nsensitivity", color=(175, 175, 255, 255))
                        with dpg.tooltip(dpg.last_item()):
                            dpg.add_text(
                                "How confident the AI must be before it recognises\n"
                                "the start of a note.\n\n"
                                "Turn up  →  fewer false notes detected.\n"
                                "Turn down →  catches quiet or soft notes."
                            )
                        dpg.add_knob_float(
                            tag=_t("onset"),
                            min_value=0.0,
                            max_value=1.0,
                            default_value=0.5,
                        )

                    dpg.add_spacer(width=20)

                    with dpg.group():
                        dpg.add_text("Note\nsustain", color=(175, 175, 255, 255))
                        with dpg.tooltip(dpg.last_item()):
                            dpg.add_text(
                                "How confident the AI must be that a note is still\n"
                                "ringing on each audio frame.\n\n"
                                "Turn up  →  only clearly audible notes are kept.\n"
                                "Turn down →  picks up quiet, fading notes."
                            )
                        dpg.add_knob_float(
                            tag=_t("frame"),
                            min_value=0.0,
                            max_value=1.0,
                            default_value=0.3,
                        )

                dpg.add_spacer(height=14)
                dpg.add_text("Shortest note", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Notes shorter than this are thrown away.\n\n"
                        "Slide right →  removes noise and ghost notes.\n"
                        "Slide left  →  keeps fast ornaments and trills."
                    )
                dpg.add_slider_float(
                    tag=_t("min_note"),
                    min_value=20.0,
                    max_value=500.0,
                    default_value=58.0,
                    width=-1,
                    format="%.0f ms",
                )

                dpg.add_spacer(height=20)
                dpg.add_button(
                    label="  Extract MIDI  ",
                    tag=_t("run_btn"),
                    callback=self._on_run_click,
                    width=-1,
                    height=40,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Convert the selected part to a MIDI file.\n"
                        "The BasicPitch model will be loaded on first run."
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
                dpg.add_text("Idle", tag=_t("status"), color=(160, 160, 160, 255))

                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_text("Result", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)
                dpg.add_text("—", tag=_t("note_count"), color=(220, 220, 220, 255))
                dpg.add_text("—", tag=_t("midi_file"), color=(140, 140, 140, 255), wrap=350)

                dpg.add_spacer(height=12)
                dpg.add_button(
                    label="  Save MIDI as…  ",
                    tag=_t("save_btn"),
                    callback=self._on_save_click,
                    width=160,
                    enabled=False,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Copy the MIDI file to a location you choose.")

    def build_save_dialog(self) -> None:
        """Create the Save As file dialog at the top DearPyGUI level."""
        with dpg.file_dialog(
            directory_selector=False,
            show=False,
            callback=self._on_save_selected,
            cancel_callback=lambda s, a: None,
            tag=_t("save_dialog"),
            width=720,
            height=440,
            modal=True,
        ):
            dpg.add_file_extension(".mid{.mid}", color=(100, 180, 255, 255))
            dpg.add_file_extension(".*")

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_run_click(self, sender, app_data, user_data) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _on_save_click(self, sender, app_data, user_data) -> None:
        dpg.configure_item(_t("save_dialog"), show=True)

    def _on_save_selected(self, sender, app_data) -> None:
        if not self._midi_path or not self._midi_path.exists():
            return
        dest_str = app_data.get("file_path_name", "")
        if not dest_str:
            return
        dest = pathlib.Path(dest_str)
        if not dest.suffix:
            dest = dest.with_suffix(".mid")
        try:
            import shutil
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._midi_path, dest)
            dpg.set_value(_t("status"), f"Saved → {dest}")
        except Exception as exc:
            dpg.set_value(_t("status"), f"Save failed: {exc}")

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        dpg.configure_item(_t("run_btn"), enabled=False)
        dpg.configure_item(_t("save_btn"), enabled=False)
        dpg.set_value(_t("progress"), 0.0)

        try:
            stem_label = dpg.get_value(_t("stem"))
            # Reverse-map friendly label → stem key
            stem_key = next(
                (k for k, v in _STEM_LABEL.items() if v == stem_label),
                "vocals",
            )
            stem_paths = app_state.stem_paths
            stem_path = stem_paths.get(stem_key)

            if stem_path is None:
                dpg.set_value(
                    _t("status"),
                    f"'{stem_label}' not available.  Run Separate first.",
                )
                return

            config = BasicPitchConfig(
                onset_threshold=dpg.get_value(_t("onset")),
                frame_threshold=dpg.get_value(_t("frame")),
                minimum_note_length=dpg.get_value(_t("min_note")),
                output_dir=_MIDI_DIR,
            )
            self._pipeline.configure(config)

            if not self._pipeline.is_loaded:
                dpg.set_value(
                    _t("status"),
                    "Loading model — first run may take a moment…",
                )
                self._pipeline.load_model()

            def _progress(pct: float) -> None:
                dpg.set_value(_t("progress"), pct / 100.0)
                dpg.set_value(_t("status"), "Transcribing…")

            self._pipeline.set_progress_callback(_progress)
            result = self._pipeline.run(stem_path)

            self._midi_path = result.midi_path
            app_state.midi_path = result.midi_path

            dpg.set_value(_t("progress"), 1.0)
            dpg.set_value(
                _t("status"),
                f"Done — {len(result.note_events)} notes found",
            )
            dpg.set_value(_t("note_count"), f"{len(result.note_events)} notes")
            dpg.set_value(_t("midi_file"), str(result.midi_path))
            dpg.configure_item(_t("save_btn"), enabled=True)

        except Exception as exc:
            traceback.print_exc()
            dpg.set_value(_t("status"), f"Error: {exc}")
            dpg.set_value(_t("progress"), 0.0)
        finally:
            dpg.configure_item(_t("run_btn"), enabled=True)

    # ------------------------------------------------------------------
    # Legacy stub methods
    # ------------------------------------------------------------------

    def set_stem_paths(self, stem_paths: dict) -> None:
        pass

    def get_selected_stem(self):
        return None

    def get_onset_threshold(self) -> float:
        return dpg.get_value(_t("onset")) if dpg.does_item_exist(_t("onset")) else 0.5

    def get_frame_threshold(self) -> float:
        return dpg.get_value(_t("frame")) if dpg.does_item_exist(_t("frame")) else 0.3

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
