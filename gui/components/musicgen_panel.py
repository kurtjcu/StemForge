"""MusicGen audio-generation panel for StemForge.

Left column: text prompt, model selector, duration slider, creativity
and variety knobs, optional melody-stem picker, and a Generate button.
Right column: progress bar, status, result info, waveform preview, and
a Save button.

The pipeline runs on a daemon thread; all DearPyGUI updates are
thread-safe calls to dpg.set_value / dpg.configure_item.

NOTE: MusicGenPipeline methods are currently stubs — the panel is fully
wired and will produce output as soon as the pipeline is implemented.
"""

import pathlib
import logging
import threading

import dearpygui.dearpygui as dpg

from pipelines.musicgen_pipeline import MusicGenPipeline, MusicGenConfig, MusicGenResult
from gui.state import app_state, copy_to_clipboard, set_widget_text, get_widget_text, make_copy_callback
from gui.constants import _MUSICGEN_DIR
from gui.components.waveform_widget import WaveformWidget
from gui.components.file_browser import FileBrowser


log = logging.getLogger("stemforge.gui.musicgen_panel")

# Model list — populated when Stable Audio Open integration is complete.
MUSICGEN_MODELS: tuple[str, ...] = ()
_MODEL_LABEL: dict[str, str] = {}
_MODEL_PLACEHOLDER = "— not yet configured —"

_P = "mg"


def _t(name: str) -> str:
    return f"{_P}_{name}"


class MusicGenPanel:
    """MusicGen audio-generation panel."""

    def __init__(self) -> None:
        self._pipeline = MusicGenPipeline()
        self._thread: threading.Thread | None = None
        self._result_path: pathlib.Path | None = None
        self._current_model: str | None = None
        self._waveform = WaveformWidget("mg")
        self._save_browser = FileBrowser(
            tag="mg_save_browser",
            callback=self._on_file_save_selected,
            extensions=frozenset({".wav", ".flac", ".ogg"}),
            mode="save",
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        with dpg.group(horizontal=True):

            # ---- Left column: settings --------------------------------
            with dpg.child_window(width=340, height=-1, border=False):

                dpg.add_text("Describe the music", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Write a plain-English description of what you want.\n"
                        "Be specific about style, instruments, and mood.\n\n"
                        "Examples:\n"
                        "  · upbeat jazz piano with walking bass\n"
                        "  · slow ambient guitar, heavy reverb\n"
                        "  · energetic lo-fi hip-hop drum loop"
                    )
                dpg.add_input_text(
                    tag=_t("prompt"),
                    hint="e.g. upbeat jazz piano with walking bass",
                    multiline=True,
                    width=-1,
                    height=90,
                )

                dpg.add_spacer(height=14)
                dpg.add_text("Model", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Small is the quickest to load; Large sounds best.\n"
                        "Choose Melody to hum or upload a tune for the AI\n"
                        "to follow as an additional guide."
                    )
                dpg.add_combo(
                    items=list(_MODEL_LABEL.values()) or [_MODEL_PLACEHOLDER],
                    default_value=next(iter(_MODEL_LABEL.values()), _MODEL_PLACEHOLDER),
                    tag=_t("model"),
                    callback=self._on_model_change,
                    width=-1,
                    enabled=bool(MUSICGEN_MODELS),
                )

                dpg.add_spacer(height=14)
                dpg.add_text("Length", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "How many seconds of audio to generate.\n"
                        "Longer clips take more time and memory."
                    )
                dpg.add_slider_int(
                    tag=_t("duration"),
                    min_value=5,
                    max_value=30,
                    default_value=10,
                    width=-1,
                    format="%d seconds",
                )

                dpg.add_spacer(height=14)
                with dpg.group(horizontal=True):
                    with dpg.group():
                        dpg.add_text("Creativity", color=(175, 175, 255, 255))
                        with dpg.tooltip(dpg.last_item()):
                            dpg.add_text(
                                "Controls how unpredictable the result is.\n\n"
                                "Turn up  →  more varied, experimental output.\n"
                                "Turn down →  safer, more predictable music."
                            )
                        dpg.add_knob_float(
                            tag=_t("temperature"),
                            min_value=0.2,
                            max_value=2.0,
                            default_value=1.0,
                        )

                    dpg.add_spacer(width=20)

                    with dpg.group():
                        dpg.add_text("Variety", color=(175, 175, 255, 255))
                        with dpg.tooltip(dpg.last_item()):
                            dpg.add_text(
                                "Number of token choices at each generation step.\n\n"
                                "Turn up  →  broader range of musical ideas.\n"
                                "Turn down →  sticks to the most likely sounds."
                            )
                        dpg.add_knob_float(
                            tag=_t("topk"),
                            min_value=10.0,
                            max_value=500.0,
                            default_value=250.0,
                        )

                dpg.add_spacer(height=14)
                dpg.add_text("Melody guide  (optional)", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Pick a separated stem for the AI to follow melodically.\n"
                        "Only effective with the Melody model variant.\n"
                        "Run Separate first, then pick a part here."
                    )
                dpg.add_combo(
                    items=["None"],
                    default_value="None",
                    tag=_t("melody"),
                    width=-1,
                    enabled=False,
                )
                dpg.add_button(
                    label="Refresh stems",
                    tag=_t("refresh_btn"),
                    callback=self._on_refresh_stems,
                    width=-1,
                    enabled=False,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Re-read which separated parts are available.")

                dpg.add_spacer(height=14)
                dpg.add_text("MIDI conditioning  (optional)", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "MIDI file produced by the MIDI tab.\n"
                        "Will be used as a structural/harmonic guide\n"
                        "when the generation pipeline supports it."
                    )
                dpg.add_text(
                    "No MIDI available — run Extract MIDI first.",
                    tag=_t("midi_status"),
                    color=(140, 140, 140, 255),
                    wrap=300,
                )

                dpg.add_spacer(height=20)
                dpg.add_button(
                    label="  Generate  ",
                    tag=_t("run_btn"),
                    callback=self._on_run_click,
                    width=-1,
                    height=40,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Generate audio from your description.\n"
                        "The model is loaded on the first run."
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
                dpg.add_text("Result", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)
                dpg.add_text("—", tag=_t("duration_info"), color=(220, 220, 220, 255))
                dpg.add_text("—", tag=_t("audio_file"), color=(140, 140, 140, 255), wrap=350)

                dpg.add_spacer(height=8)
                # Waveform preview replaces the old ▶ Play button
                self._waveform.build_ui()

                dpg.add_spacer(height=8)
                dpg.add_button(
                    label="  Save as  ",
                    tag=_t("save_btn"),
                    callback=self._on_save_click,
                    width=110,
                    enabled=False,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Copy the generated file to a location you choose.")

    def build_save_dialog(self) -> None:
        """Create the custom Save browser at the top DearPyGUI level."""
        self._save_browser.build()

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def notify_midi_ready(
        self,
        midi_path: pathlib.Path,
        stem_midi_paths: dict[str, pathlib.Path],
    ) -> None:
        """Called by MidiPanel after a successful extraction run."""
        if dpg.does_item_exist(_t("midi_status")):
            dpg.set_value(_t("midi_status"), str(midi_path))

    def _on_model_change(self, sender, app_data, user_data) -> None:
        pass

    def _on_refresh_stems(self, sender, app_data, user_data) -> None:
        from gui.components.demucs_panel import _STEM_LABEL
        stems = app_state.stem_paths
        items = ["None"] + [_STEM_LABEL.get(k, k) for k in stems]
        dpg.configure_item(_t("melody"), items=items)
        dpg.set_value(_t("melody"), "None")

    def _on_run_click(self, sender, app_data, user_data) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _on_save_click(self, sender, app_data, user_data) -> None:
        self._save_browser.show()

    def _on_file_save_selected(self, dest: pathlib.Path) -> None:
        if not self._result_path or not self._result_path.exists():
            return
        if not dest.suffix:
            dest = dest.with_suffix(".wav")
        try:
            import shutil
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._result_path, dest)
            set_widget_text(_t("status"),f"Saved → {dest}")
        except Exception as exc:
            set_widget_text(_t("status"),f"Save failed: {exc}")

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        dpg.configure_item(_t("run_btn"), enabled=False)
        dpg.set_value(_t("progress"), 0.0)
        try:
            set_widget_text(_t("status"), "Generation pipeline not yet implemented.")
        finally:
            dpg.configure_item(_t("run_btn"), enabled=True)

    # ------------------------------------------------------------------
    # Legacy stub methods
    # ------------------------------------------------------------------

    def set_melody_path(self, path: pathlib.Path) -> None:
        pass

    def get_prompt(self) -> str:
        return dpg.get_value(_t("prompt")) if dpg.does_item_exist(_t("prompt")) else ""

    def get_selected_model(self) -> str:
        label = dpg.get_value(_t("model")) if dpg.does_item_exist(_t("model")) else ""
        return next((k for k, v in _MODEL_LABEL.items() if v == label), "")

    def get_duration_seconds(self) -> float:
        return float(dpg.get_value(_t("duration"))) if dpg.does_item_exist(_t("duration")) else 10.0

    def get_melody_conditioning(self) -> pathlib.Path | None:
        return None

    def run(self) -> None:
        self._on_run_click(None, None, None)

    def cancel(self) -> None:
        pass

    def add_result_listener(self, callback) -> None:
        pass
