"""Demucs source-separation panel for StemForge.

Provides a two-column layout: settings on the left (model selector,
part checkboxes, Run button) and results on the right (progress bar,
status, per-stem waveform previews and Show-file buttons).  The
pipeline runs on a daemon thread so the render loop is never blocked.

Result listeners
----------------
Callers may register callbacks via add_result_listener(cb).  After a
successful run, every callback is invoked with the stem_paths dict so
that the MIDI tab can be updated automatically.
"""

import pathlib
import logging
import threading
import traceback
from typing import Callable

import dearpygui.dearpygui as dpg

from pipelines.demucs_pipeline import DemucsPipeline, DemucsConfig, DemucsResult
from gui.state import app_state
from gui.constants import _STEMS_DIR
from gui.components.waveform_widget import WaveformWidget


log = logging.getLogger("stemforge.gui.demucs_panel")

DEMUCS_MODELS: tuple[str, ...] = ("htdemucs", "htdemucs_ft", "mdx_extra", "mdx_extra_q")
STEM_TARGETS:  tuple[str, ...] = ("vocals", "drums", "bass", "other")

_MODEL_DESC: dict[str, str] = {
    "htdemucs":    "Best overall quality — good for most music",
    "htdemucs_ft": "Fine-tuned variant — sharper on pop and rock",
    "mdx_extra":   "MDX architecture — excellent vocal isolation",
    "mdx_extra_q": "MDX quality mode — cleanest results, slowest",
}

_STEM_LABEL: dict[str, str] = {
    "vocals": "Singing voice",
    "drums":  "Drums & percussion",
    "bass":   "Bass",
    "other":  "Everything else",
}

_P = "demucs"


def _t(name: str) -> str:
    return f"{_P}_{name}"


class DemucsPanel:
    """Demucs separation panel — builds and manages its own DearPyGUI widgets."""

    def __init__(self) -> None:
        self._pipeline = DemucsPipeline()
        self._current_model: str | None = None
        self._thread: threading.Thread | None = None
        self._stem_paths: dict[str, pathlib.Path] = {}
        self._result_listeners: list[Callable[[dict[str, pathlib.Path]], None]] = []
        # Pre-create one WaveformWidget per stem
        self._stem_waveforms: dict[str, WaveformWidget] = {
            stem: WaveformWidget(f"stem_{stem}") for stem in STEM_TARGETS
        }

    # ------------------------------------------------------------------
    # UI construction  (call inside the target dpg parent context)
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        with dpg.group(horizontal=True):

            # ---- Left column: settings --------------------------------
            with dpg.child_window(width=300, height=-1, border=False):
                dpg.add_text("Separation model", color=(175, 175, 255, 255))
                dpg.add_combo(
                    items=list(DEMUCS_MODELS),
                    default_value=DEMUCS_MODELS[0],
                    tag=_t("model"),
                    callback=self._on_model_change,
                    width=-1,
                )
                dpg.add_text(
                    _MODEL_DESC[DEMUCS_MODELS[0]],
                    tag=_t("model_desc"),
                    color=(140, 140, 140, 255),
                    wrap=280,
                )

                dpg.add_spacer(height=14)
                dpg.add_text("Parts to separate", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Tick the parts you want.\n"
                        "Separating fewer parts is faster."
                    )
                for stem in STEM_TARGETS:
                    dpg.add_checkbox(
                        label=_STEM_LABEL[stem],
                        default_value=True,
                        tag=_t(f"stem_{stem}"),
                    )

                dpg.add_spacer(height=20)
                dpg.add_button(
                    label="  Separate  ",
                    tag=_t("run_btn"),
                    callback=self._on_run_click,
                    width=-1,
                    height=40,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Split the loaded audio into individual parts.\n"
                        "The first run downloads model weights (~300 MB)."
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
                    dpg.add_text("Idle", tag=_t("status"), color=(160, 160, 160, 255))
                    _st = _t("status")
                    dpg.add_button(
                        label="Copy",
                        callback=lambda s, a, u, _k=_st: dpg.set_clipboard_text(dpg.get_value(_k)),
                        width=50,
                    )

                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_text("Separated parts", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)

                for stem in STEM_TARGETS:
                    with dpg.group(tag=_t(f"row_{stem}"), show=False):
                        # Label + Show-file button on one line
                        with dpg.group(horizontal=True):
                            dpg.add_text(
                                _STEM_LABEL[stem],
                                color=(220, 220, 220, 255),
                            )
                            dpg.add_button(
                                label="Show file",
                                tag=_t(f"open_{stem}"),
                                callback=self._make_open_cb(stem),
                                width=80,
                            )
                            with dpg.tooltip(dpg.last_item()):
                                dpg.add_text("Reveal this file in your file manager.")
                        # Per-stem waveform preview below
                        self._stem_waveforms[stem].build_ui()
                        dpg.add_spacer(height=6)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_result_listener(
        self, callback: Callable[[dict[str, pathlib.Path]], None]
    ) -> None:
        """Register a callback invoked with stem_paths after a successful run."""
        self._result_listeners.append(callback)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_model_change(self, sender, app_data, user_data) -> None:
        dpg.set_value(_t("model_desc"), _MODEL_DESC.get(app_data, ""))

    def _on_run_click(self, sender, app_data, user_data) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _make_open_cb(self, stem: str):
        def _cb(s, a, u):
            self._open_stem(stem)
        return _cb

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Runs entirely on a daemon thread — all dpg calls are thread-safe."""
        dpg.configure_item(_t("run_btn"), enabled=False)
        dpg.set_value(_t("progress"), 0.0)

        try:
            audio = app_state.audio_path
            if audio is None:
                dpg.set_value(_t("status"), "Load an audio file first (Browse button above).")
                return

            stems = [s for s in STEM_TARGETS if dpg.get_value(_t(f"stem_{s}"))]
            if not stems:
                dpg.set_value(_t("status"), "Tick at least one part to separate.")
                return

            model_name = dpg.get_value(_t("model"))

            if self._current_model != model_name:
                if self._current_model is not None:
                    dpg.set_value(_t("status"), "Unloading previous model…")
                    self._pipeline.clear()
                self._current_model = model_name

            config = DemucsConfig(
                model_name=model_name,
                stems=stems,
                output_dir=_STEMS_DIR,
            )
            self._pipeline.configure(config)

            if not self._pipeline.is_loaded:
                dpg.set_value(
                    _t("status"),
                    "Loading model — first run may take a minute while weights download…",
                )
                self._pipeline.load_model()

            def _progress(pct: float, stage: str) -> None:
                dpg.set_value(_t("progress"), pct / 100.0)
                dpg.set_value(_t("status"), stage)

            self._pipeline.set_progress_callback(_progress)
            result = self._pipeline.run(audio)

            app_state.stem_paths = result.stem_paths
            self._stem_paths = result.stem_paths

            dpg.set_value(_t("progress"), 1.0)
            dpg.set_value(
                _t("status"),
                f"Done — {len(result.stem_paths)} parts  ({result.duration_seconds:.1f} s)",
            )

            for stem_name in STEM_TARGETS:
                show = stem_name in result.stem_paths
                dpg.configure_item(_t(f"row_{stem_name}"), show=show)
                if show:
                    self._stem_waveforms[stem_name].load_async(
                        result.stem_paths[stem_name]
                    )

            # Notify any registered listeners (e.g. BasicPitchPanel)
            for cb in self._result_listeners:
                try:
                    cb(result.stem_paths)
                except Exception as exc:
                    log.error("DemucsPanel result listener error: %s", exc)

        except Exception as exc:
            traceback.print_exc()
            dpg.set_value(_t("status"), f"Error: {exc}")
            dpg.set_value(_t("progress"), 0.0)
        finally:
            dpg.configure_item(_t("run_btn"), enabled=True)

    # ------------------------------------------------------------------
    # File reveal helper
    # ------------------------------------------------------------------

    def _open_stem(self, stem: str) -> None:
        path = self._stem_paths.get(stem)
        if not path:
            return
        import subprocess, sys
        if sys.platform == "linux":
            subprocess.Popen(["xdg-open", str(path.parent)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["explorer", "/select,", str(path)])

    # ------------------------------------------------------------------
    # Legacy stub methods
    # ------------------------------------------------------------------

    def set_input_path(self, path: pathlib.Path) -> None:
        pass

    def get_selected_model(self) -> str:
        return dpg.get_value(_t("model")) if dpg.does_item_exist(_t("model")) else DEMUCS_MODELS[0]

    def get_selected_stems(self) -> list[str]:
        return [s for s in STEM_TARGETS if dpg.get_value(_t(f"stem_{s}"))]

    def run(self) -> None:
        self._on_run_click(None, None, None)

    def cancel(self) -> None:
        pass

    def _on_progress(self, percent: float, stem: str) -> None:
        pass

    def _on_complete(self, stem_paths: dict) -> None:
        pass

    def _on_error(self, exc: Exception) -> None:
        pass
