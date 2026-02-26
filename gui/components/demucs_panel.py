"""Demucs + BS-Roformer source-separation panel for StemForge.

Two-column layout:
  Left  — engine selector, model combo, stem checkboxes (Demucs) or
          auto-analysis status (Roformer), Separate button (Demucs only).
  Right — progress bar, status, per-stem rows (waveform + Save As + Show file).

Engine behaviour
----------------
Demucs   — manual "Separate" button; runs user-selected stems.
Roformer — "Analyze" button triggers separation; auto-triggers on file load.
           Shows active stems based on RMS energy.

Result listeners
----------------
Register via add_result_listener(cb).  After a successful run (either
engine) every callback is invoked with the active stem_paths dict so
the MIDI tab updates automatically.
"""

from __future__ import annotations

import logging
import pathlib
import shutil
import subprocess
import sys
import threading
import traceback
from typing import Callable

import numpy as np
import dearpygui.dearpygui as dpg

from pipelines.demucs_pipeline import DemucsPipeline, DemucsConfig
from pipelines.roformer_pipeline import RoformerPipeline, RoformerConfig
from models.registry import list_specs, get_spec, DemucsSpec, RoformerSpec
from gui.state import app_state, set_widget_text, make_copy_callback
from gui.constants import _STEMS_DIR
from gui.ui_queue import schedule_ui
from gui.components.waveform_widget import WaveformWidget, stop_all as _stop_all_audio
from gui.components.file_browser import FileBrowser
from utils.audio_profile import profile_audio, recommend_separator, Recommendation


log = logging.getLogger("stemforge.gui.demucs_panel")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEMUCS_MODELS: tuple[str, ...] = tuple(
    s.model_id for s in list_specs(DemucsSpec)
    if not (sys.platform == "darwin" and s.model_id == "mdx_extra_q")
)
ROFORMER_MODELS: tuple[str, ...] = tuple(s.model_id for s in list_specs(RoformerSpec))

_DEMUCS_DESC: dict[str, str] = {s.model_id: s.description for s in list_specs(DemucsSpec)}
_ROFORMER_DESC: dict[str, str] = {s.model_id: s.description for s in list_specs(RoformerSpec)}

STEM_TARGETS: tuple[str, ...] = ("vocals", "drums", "bass", "other")
# All stems that can appear in any supported model (Demucs 4-stem + Roformer 6-stem)
_ALL_STEM_TARGETS: tuple[str, ...] = ("vocals", "drums", "bass", "other", "guitar", "piano")

_STEM_LABEL: dict[str, str] = {
    "vocals": "Singing voice",
    "drums":  "Drums & percussion",
    "bass":   "Bass",
    "other":  "Everything else",
    "guitar": "Guitar",
    "piano":  "Piano",
}

_ENGINES = ("Demucs", "BS-Roformer")

# Stem is "active" if stem_rms / mix_rms exceeds this ratio (~-40 dB)
_RMS_ACTIVE_RATIO = 0.01

_SPINNER_CPU_COLOR  = (220, 130,  50, 255)   # orange — CPU fallback warning
_SPINNER_IDLE_COLOR = (80,   80, 100, 255)   # dim — not running

_P = "demucs"


def _t(name: str) -> str:
    return f"{_P}_{name}"


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class DemucsPanel:
    """Separation panel — manages Demucs and BS-Roformer engines."""

    def __init__(self) -> None:
        self._demucs_pipeline = DemucsPipeline()
        self._roformer_pipeline = RoformerPipeline()
        self._current_model: str | None = None
        self._current_engine: str = "Demucs"
        self._thread: threading.Thread | None = None
        self._cancel_analysis: threading.Event = threading.Event()
        self._stem_paths: dict[str, pathlib.Path] = {}
        self._result_listeners: list[Callable[[dict[str, pathlib.Path]], None]] = []
        self._save_stem_name: str = ""
        self._last_recommendation: Recommendation | None = None
        self._recommend_thread: threading.Thread | None = None

        # One WaveformWidget per stem (covers all possible stems across all engines)
        self._stem_waveforms: dict[str, WaveformWidget] = {
            stem: WaveformWidget(f"stem_{stem}") for stem in _ALL_STEM_TARGETS
        }

        # Save-As file browser (created in build_save_dialog)
        self._save_browser: FileBrowser | None = None

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        with dpg.group(horizontal=True):

            # ---- Left column: settings --------------------------------
            with dpg.child_window(width=300, height=-1, border=False):
                dpg.add_text("Engine", color=(175, 175, 255, 255))
                dpg.add_combo(
                    items=list(_ENGINES),
                    default_value=_ENGINES[0],
                    tag=_t("engine"),
                    callback=self._on_engine_change,
                    width=-1,
                )

                dpg.add_spacer(height=8)
                dpg.add_text("Model", color=(175, 175, 255, 255))
                dpg.add_combo(
                    items=list(DEMUCS_MODELS),
                    default_value=DEMUCS_MODELS[0],
                    tag=_t("model"),
                    callback=self._on_model_change,
                    width=-1,
                )
                dpg.add_text(
                    _DEMUCS_DESC.get(DEMUCS_MODELS[0], ""),
                    tag=_t("model_desc"),
                    color=(140, 140, 140, 255),
                    wrap=280,
                )

                dpg.add_spacer(height=10)
                dpg.add_button(
                    label="  Help me choose  ",
                    tag=_t("recommend_btn"),
                    callback=self._on_recommend_click,
                    width=-1,
                    height=32,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Analyze the loaded audio and suggest the\n"
                        "best separation engine and model.\n"
                        "Takes less than a second."
                    )
                # Recommendation result (initially hidden)
                with dpg.group(tag=_t("recommend_group"), show=False):
                    dpg.add_spacer(height=4)
                    dpg.add_text(
                        "",
                        tag=_t("recommend_text"),
                        color=(120, 200, 120, 255),
                        wrap=280,
                    )
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            label="Apply",
                            tag=_t("recommend_apply_btn"),
                            callback=self._on_recommend_apply,
                            width=80,
                        )
                        with dpg.tooltip(dpg.last_item()):
                            dpg.add_text("Switch to the recommended engine and model.")
                        dpg.add_button(
                            label="Dismiss",
                            tag=_t("recommend_dismiss_btn"),
                            callback=self._on_recommend_dismiss,
                            width=80,
                        )

                dpg.add_spacer(height=14)

                # Demucs stem checkboxes (hidden in Roformer mode)
                with dpg.group(tag=_t("demucs_stem_group")):
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
                        "Run separation on the loaded audio.\n"
                        "First run downloads model weights (~300 MB).\n"
                        "For BS-Roformer, also runs on file load."
                    )

            # ---- Right column: results --------------------------------
            with dpg.child_window(width=-1, height=-1, border=False):
                with dpg.group(horizontal=True):
                    dpg.add_text("Progress", color=(175, 175, 255, 255))
                    dpg.add_spacer(width=8)
                    dpg.add_text(
                        "",
                        tag=_t("spinner"),
                        color=_SPINNER_IDLE_COLOR,
                    )
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
                with dpg.group(horizontal=True):
                    dpg.add_text("Separated parts", color=(175, 175, 255, 255))
                    dpg.add_spacer(width=12)
                    dpg.add_button(
                        label="Stop All",
                        tag=_t("stop_all_btn"),
                        callback=lambda s, a, u: _stop_all_audio(),
                        width=120,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text("Stop playback on all stems.")
                dpg.add_spacer(height=4)

                for stem in _ALL_STEM_TARGETS:
                    with dpg.group(tag=_t(f"row_{stem}"), show=False):
                        dpg.add_separator()
                        dpg.add_spacer(height=4)
                        # Waveform preview at the top of each row
                        self._stem_waveforms[stem].build_ui()
                        # Label + action buttons below the waveform
                        with dpg.group(horizontal=True):
                            # Checkbox controls MIDI tab availability
                            dpg.add_checkbox(
                                label="",
                                default_value=True,
                                tag=_t(f"result_chk_{stem}"),
                                callback=self._make_stem_check_cb(stem),
                            )
                            dpg.add_text(
                                _STEM_LABEL[stem],
                                color=(220, 220, 220, 255),
                            )
                            dpg.add_button(
                                label="Save As...",
                                tag=_t(f"save_{stem}"),
                                callback=self._make_save_cb(stem),
                                width=80,
                            )
                            with dpg.tooltip(dpg.last_item()):
                                dpg.add_text(
                                    "Save this stem to a location of your choice.\n"
                                    "Same format as the imported file - no conversion."
                                )
                            dpg.add_button(
                                label="Show file",
                                tag=_t(f"open_{stem}"),
                                callback=self._make_open_cb(stem),
                                width=80,
                            )
                            with dpg.tooltip(dpg.last_item()):
                                dpg.add_text("Reveal this file in your file manager.")
                        # RMS energy info (shown for Roformer)
                        dpg.add_text(
                            "",
                            tag=_t(f"rms_{stem}"),
                            color=(120, 180, 120, 255),
                        )
                        dpg.add_spacer(height=6)

    def build_save_dialog(self) -> None:
        """Create the Save As file browser at the top DearPyGUI level."""
        self._save_browser = FileBrowser(
            tag="demucs_save_browser",
            callback=self._on_save_selected,
            mode="save",
        )
        self._save_browser.build()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_result_listener(
        self, callback: Callable[[dict[str, pathlib.Path]], None]
    ) -> None:
        """Register a callback invoked with active stem_paths after a successful run."""
        self._result_listeners.append(callback)

    def on_file_loaded(self, path: pathlib.Path) -> None:
        """Called by LoaderPanel when a new file is selected.

        If the current engine is BS-Roformer, cancels any running analysis
        and starts a fresh one in a background thread.
        Demucs: no-op (user must click Separate).
        """
        if self._current_engine != "BS-Roformer":
            return
        self._start_auto_analyze(path)

    # ------------------------------------------------------------------
    # Engine switch
    # ------------------------------------------------------------------

    def _on_engine_change(self, sender, app_data, user_data) -> None:
        engine = app_data
        self._current_engine = engine
        # Cancel any in-progress run when switching engines
        self._cancel_analysis.set()

        if engine == "Demucs":
            dpg.configure_item(_t("model"), items=list(DEMUCS_MODELS), default_value=DEMUCS_MODELS[0])
            dpg.set_value(_t("model"), DEMUCS_MODELS[0])
            dpg.set_value(_t("model_desc"), _DEMUCS_DESC.get(DEMUCS_MODELS[0], ""))
            dpg.configure_item(_t("demucs_stem_group"), show=True)
            dpg.configure_item(_t("run_btn"), label="  Separate  ")
        else:
            dpg.configure_item(_t("model"), items=list(ROFORMER_MODELS), default_value=ROFORMER_MODELS[0])
            dpg.set_value(_t("model"), ROFORMER_MODELS[0])
            dpg.set_value(_t("model_desc"), _ROFORMER_DESC.get(ROFORMER_MODELS[0], ""))
            dpg.configure_item(_t("demucs_stem_group"), show=False)
            dpg.configure_item(_t("run_btn"), label="  Analyze  ")

    def _on_model_change(self, sender, app_data, user_data) -> None:
        if self._current_engine == "Demucs":
            dpg.set_value(_t("model_desc"), _DEMUCS_DESC.get(app_data, ""))
        else:
            dpg.set_value(_t("model_desc"), _ROFORMER_DESC.get(app_data, ""))
            # Cancel any in-progress analysis; user must click Analyze to re-run
            self._cancel_analysis.set()

    # ------------------------------------------------------------------
    # Demucs run
    # ------------------------------------------------------------------

    def _on_run_click(self, sender, app_data, user_data) -> None:
        if self._thread and self._thread.is_alive():
            return
        if self._current_engine == "BS-Roformer":
            path = app_state.audio_path
            if path is None:
                set_widget_text(_t("status"), "Load an audio file first.")
                return
            self._start_auto_analyze(path)
        else:
            # Capture UI values on the main thread before spawning bg work
            stems = [s for s in STEM_TARGETS if dpg.get_value(_t(f"stem_{s}"))]
            model_name = dpg.get_value(_t("model"))
            self._thread = threading.Thread(
                target=self._run_demucs, args=(stems, model_name), daemon=True,
            )
            self._thread.start()

    def _run_demucs(self, stems: list[str], model_name: str) -> None:
        schedule_ui(lambda: dpg.configure_item(_t("run_btn"), enabled=False))
        schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))

        try:
            audio = app_state.audio_path
            if audio is None:
                set_widget_text(_t("status"), "Load an audio file first (Browse button above).")
                return

            if not stems:
                set_widget_text(_t("status"), "Tick at least one part to separate.")
                return

            if self._current_model != model_name:
                if self._current_model is not None:
                    set_widget_text(_t("status"), "Unloading previous model...")
                    self._demucs_pipeline.clear()
                self._current_model = model_name

            config = DemucsConfig(model_name=model_name, stems=stems, output_dir=_STEMS_DIR)
            self._demucs_pipeline.configure(config)

            if not self._demucs_pipeline.is_loaded:
                set_widget_text(_t("status"), "Loading model - first run may take a minute...")
                self._demucs_pipeline.load_model()

            def _progress(pct: float, stage: str) -> None:
                schedule_ui(lambda _p=pct: dpg.set_value(_t("progress"), _p / 100.0))
                set_widget_text(_t("status"), stage)
                self._tick_spinner()   # Demucs runs on GPU internally; no CPU/GPU label

            self._demucs_pipeline.set_progress_callback(_progress)
            result = self._demucs_pipeline.run(audio)

            self._stem_paths = result.stem_paths
            self._apply_stem_results(result.stem_paths, rms_map=None)

            schedule_ui(lambda: dpg.set_value(_t("progress"), 1.0))
            set_widget_text(
                _t("status"),
                f"Done - {len(result.stem_paths)} parts  ({result.duration_seconds:.1f} s)",
            )

        except Exception as exc:
            traceback.print_exc()
            set_widget_text(_t("status"), f"Error: {exc}")
            schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))
        finally:
            schedule_ui(lambda: dpg.configure_item(_t("run_btn"), enabled=True))
            self._clear_spinner()

    # ------------------------------------------------------------------
    # Roformer auto-analysis
    # ------------------------------------------------------------------

    def _start_auto_analyze(self, path: pathlib.Path) -> None:
        """Cancel any running analysis and start a new one."""
        self._cancel_analysis.set()
        # Wait briefly for thread to notice cancellation
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._cancel_analysis = threading.Event()
        self._roformer_pipeline.set_cancel_event(self._cancel_analysis)
        # Capture UI value on main thread before spawning bg work
        model_id = (
            dpg.get_value(_t("model"))
            if dpg.does_item_exist(_t("model"))
            else ROFORMER_MODELS[0]
        )
        self._thread = threading.Thread(
            target=self._auto_analyze, args=(path, model_id), daemon=True
        )
        self._thread.start()

    def _auto_analyze(self, path: pathlib.Path, model_id: str) -> None:
        """Background thread: run Roformer, compute RMS, update UI."""
        schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))
        set_widget_text(_t("status"), "Starting analysis...")

        try:
            roformer_spec = get_spec(model_id)
            config = RoformerConfig(
                model_id=model_id,
                stems=list(roformer_spec.available_stems),
                output_dir=_STEMS_DIR,
                chunk_size=roformer_spec.default_chunk_size,
                num_overlap=roformer_spec.default_num_overlap,
            )
            self._roformer_pipeline.configure(config)

            if not self._roformer_pipeline.is_loaded:
                set_widget_text(_t("status"), "Loading BS-Roformer model - first run downloads ~300 MB...")

            self._roformer_pipeline.load_model()

            if self._cancel_analysis.is_set():
                return

            def _progress(pct: float, stage: str) -> None:
                if not self._cancel_analysis.is_set():
                    schedule_ui(lambda _p=pct: dpg.set_value(_t("progress"), _p / 100.0))
                    device = self._roformer_pipeline.last_device
                    # Show stage + device on same line; spinner gives liveness
                    set_widget_text(_t("status"), f"[{device}] {stage}")
                    self._tick_spinner(device)

            self._roformer_pipeline.set_progress_callback(_progress)
            result = self._roformer_pipeline.run(path)

            if self._cancel_analysis.is_set():
                return

            # Compute RMS for each stem and the mix
            from utils.audio_io import read_audio
            try:
                mix_np, _ = read_audio(path, target_rate=44_100, mono=False)
                mix_rms = float(np.sqrt(np.mean(mix_np ** 2))) + 1e-9
            except Exception:
                mix_rms = 1.0

            rms_map: dict[str, float] = {}
            for stem_name, stem_path in result.stem_paths.items():
                try:
                    stem_np, _ = read_audio(stem_path, target_rate=44_100, mono=False)
                    rms_map[stem_name] = float(np.sqrt(np.mean(stem_np ** 2)))
                except Exception:
                    rms_map[stem_name] = 0.0

            self._stem_paths = result.stem_paths
            active_paths: dict[str, pathlib.Path] = {
                s: p for s, p in result.stem_paths.items()
                if rms_map.get(s, 0.0) / mix_rms > _RMS_ACTIVE_RATIO
            }

            self._apply_stem_results(result.stem_paths, rms_map=rms_map, active=active_paths)

            schedule_ui(lambda: dpg.set_value(_t("progress"), 1.0))
            set_widget_text(
                _t("status"),
                f"Done - {len(result.stem_paths)} stems  ({result.duration_seconds:.1f} s)",
            )

        except Exception as exc:
            if not self._cancel_analysis.is_set():
                traceback.print_exc()
                set_widget_text(_t("status"), f"Error: {exc}")
                schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))
        finally:
            self._clear_spinner()

    # ------------------------------------------------------------------
    # UI updater (called from background thread — uses schedule_ui)
    # ------------------------------------------------------------------

    def _apply_stem_results(
        self,
        stem_paths: dict[str, pathlib.Path],
        rms_map: dict[str, float] | None,
        active: dict[str, pathlib.Path] | None = None,
    ) -> None:
        """Show/update stem rows, checkboxes, waveforms, and notify listeners."""
        if active is None:
            active = stem_paths  # Demucs: all produced stems are active

        for stem_name in _ALL_STEM_TARGETS:
            in_result = stem_name in stem_paths
            schedule_ui(
                lambda _sn=stem_name, _ir=in_result: dpg.configure_item(
                    _t(f"row_{_sn}"), show=_ir
                )
            )
            if not in_result:
                continue

            is_active = stem_name in active
            schedule_ui(
                lambda _sn=stem_name, _ia=is_active: dpg.set_value(
                    _t(f"result_chk_{_sn}"), _ia
                )
            )

            if rms_map is not None:
                rms = rms_map.get(stem_name, 0.0)
                db = 20.0 * np.log10(rms + 1e-9)
                tag_str = "active" if is_active else "below threshold"
                val = f"RMS: {rms:.4f} ({db:.1f} dB) - {tag_str}"
                schedule_ui(
                    lambda _sn=stem_name, _v=val: dpg.set_value(_t(f"rms_{_sn}"), _v)
                )
            else:
                schedule_ui(
                    lambda _sn=stem_name: dpg.set_value(_t(f"rms_{_sn}"), "")
                )

            self._stem_waveforms[stem_name].load_async(stem_paths[stem_name])

        app_state.stem_paths = active
        for cb in self._result_listeners:
            try:
                cb(active)
            except Exception as exc:
                log.error("DemucsPanel result listener error: %s", exc)

    # ------------------------------------------------------------------
    # Per-stem checkbox callback
    # ------------------------------------------------------------------

    def _make_stem_check_cb(self, stem: str) -> Callable:
        def _cb(sender, app_data, user_data):
            # Rebuild active stems from all checked rows
            active: dict[str, pathlib.Path] = {
                s: p
                for s in _ALL_STEM_TARGETS
                if s in self._stem_paths and dpg.get_value(_t(f"result_chk_{s}"))
                for p in [self._stem_paths[s]]
            }
            app_state.stem_paths = active
            for cb in self._result_listeners:
                try:
                    cb(active)
                except Exception as exc:
                    log.error("DemucsPanel checkbox listener error: %s", exc)
        return _cb

    # ------------------------------------------------------------------
    # Save As callbacks
    # ------------------------------------------------------------------

    def _make_save_cb(self, stem: str) -> Callable:
        def _cb(sender, app_data, user_data):
            self._save_stem_name = stem
            if self._save_browser is not None:
                self._save_browser.show()
        return _cb

    def _on_save_selected(self, dest: pathlib.Path) -> None:
        """Copy the stem file to the user-chosen destination."""
        stem = self._save_stem_name
        src = self._stem_paths.get(stem)
        if src is None or not src.exists():
            log.warning("Save As: stem %r not available", stem)
            return
        # Ensure destination has the correct extension (same as source)
        if dest.suffix.lower() != src.suffix.lower():
            dest = dest.with_suffix(src.suffix)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            log.info("Saved %s -> %s", stem, dest)
            set_widget_text(_t("status"), f"Saved {stem} -> {dest.name}")
        except Exception as exc:
            log.error("Save As failed: %s", exc)
            set_widget_text(_t("status"), f"Save failed: {exc}")

    # ------------------------------------------------------------------
    # Spinner helper
    # ------------------------------------------------------------------

    def _tick_spinner(self, device: str = "") -> None:
        """Show a CPU warning when on CPU, nothing extra when on GPU."""
        if device == "CPU":
            label = "GPU unavailable - using CPU fallback, this will run slowly..."
            color = _SPINNER_CPU_COLOR
        else:
            label = ""
            color = _SPINNER_IDLE_COLOR

        def _update(_l=label, _c=color):
            dpg.set_value(_t("spinner"), _l)
            dpg.configure_item(_t("spinner"), color=_c)
        schedule_ui(_update)

    def _clear_spinner(self) -> None:
        def _update():
            dpg.set_value(_t("spinner"), "")
            dpg.configure_item(_t("spinner"), color=_SPINNER_IDLE_COLOR)
        schedule_ui(_update)

    # ------------------------------------------------------------------
    # File reveal helper
    # ------------------------------------------------------------------

    def _make_open_cb(self, stem: str) -> Callable:
        def _cb(s, a, u):
            self._open_stem(stem)
        return _cb

    def _open_stem(self, stem: str) -> None:
        path = self._stem_paths.get(stem)
        if not path:
            return
        if sys.platform == "linux":
            subprocess.Popen(["xdg-open", str(path.parent)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["explorer", "/select,", str(path)])

    # ------------------------------------------------------------------
    # "Help me choose" recommendation callbacks
    # ------------------------------------------------------------------

    def _on_recommend_click(self, sender, app_data, user_data) -> None:
        if app_state.audio_path is None:
            set_widget_text(_t("status"), "Load an audio file first.")
            return
        if self._recommend_thread and self._recommend_thread.is_alive():
            return  # already analyzing — ignore double-click
        dpg.configure_item(_t("recommend_btn"), enabled=False)
        dpg.configure_item(_t("recommend_group"), show=False)
        set_widget_text(_t("status"), "Analyzing audio...")
        self._recommend_thread = threading.Thread(
            target=self._run_recommend,
            args=(app_state.audio_path,),
            daemon=True,
        )
        self._recommend_thread.start()

    def _run_recommend(self, path: pathlib.Path) -> None:
        """Background thread: profile audio and display recommendation."""
        try:
            profile = profile_audio(path)
            rec = recommend_separator(profile)
            self._last_recommendation = rec

            _CONF_COLOR = {
                "high":     (100, 210, 100, 255),   # green
                "moderate": (210, 190,  80, 255),   # yellow
                "low":      (210, 140,  60, 255),   # orange
            }
            color = _CONF_COLOR.get(rec.confidence, (160, 160, 160, 255))

            lines = [
                f"Recommendation: {rec.engine} / {rec.model_id}",
                f"Confidence: {rec.confidence.capitalize()}",
                "",
                rec.reason,
            ]
            if profile.analysis_note:
                lines.append("")
                lines.append(profile.analysis_note)

            text = "\n".join(lines)

            def _show_result():
                dpg.set_value(_t("recommend_text"), text)
                dpg.configure_item(_t("recommend_text"), color=color)
                dpg.configure_item(_t("recommend_group"), show=True)
            schedule_ui(_show_result)
            set_widget_text(_t("status"), "Analysis complete.")
        except Exception as exc:
            log.error("Recommendation failed: %s", exc)
            set_widget_text(_t("status"), f"Analysis error: {exc}")
        finally:
            schedule_ui(lambda: dpg.configure_item(_t("recommend_btn"), enabled=True))

    def _on_recommend_apply(self, sender, app_data, user_data) -> None:
        rec = self._last_recommendation
        if rec is None:
            return
        # Switch engine combo and fire the engine-change callback
        dpg.set_value(_t("engine"), rec.engine)
        self._on_engine_change(None, rec.engine, None)
        # Switch model combo and fire the model-change callback
        dpg.set_value(_t("model"), rec.model_id)
        self._on_model_change(None, rec.model_id, None)
        set_widget_text(
            _t("status"),
            f"Applied: {rec.engine} / {rec.model_id}. Ready to separate.",
        )
        dpg.configure_item(_t("recommend_group"), show=False)

    def _on_recommend_dismiss(self, sender, app_data, user_data) -> None:
        dpg.configure_item(_t("recommend_group"), show=False)
        set_widget_text(_t("status"), "")

    # ------------------------------------------------------------------
    # Legacy stubs
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
        self._cancel_analysis.set()

    def _on_progress(self, percent: float, stem: str) -> None:
        pass

    def _on_complete(self, stem_paths: dict) -> None:
        pass

    def _on_error(self, exc: Exception) -> None:
        pass
