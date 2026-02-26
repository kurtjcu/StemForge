"""MusicGen (Stable Audio Open) audio-generation panel for StemForge.

Left column: text prompt, model, duration, steps, CFG scale,
             audio conditioning (stem or file + influence level),
             MIDI conditioning (MIDI-tab or file), Generate button.
Right column: progress bar, status, result info, waveform preview,
              Save As button.

The pipeline runs on a daemon thread; all DearPyGUI updates are
thread-safe calls to dpg.set_value / dpg.configure_item.
"""

from __future__ import annotations

import logging
import pathlib
import shutil
import threading
from typing import Callable

import mido
import soundfile as sf
import dearpygui.dearpygui as dpg

from pipelines.musicgen_pipeline import MusicGenPipeline, MusicGenConfig, MusicGenResult
from utils.errors import ModelLoadError
from gui.state import app_state, set_widget_text, make_copy_callback
from gui.constants import _MUSICGEN_DIR, _MIDI_DIR
from gui.ui_queue import schedule_ui
from gui.components.waveform_widget import WaveformWidget
from gui.components.file_browser import FileBrowser
from gui.components.demucs_panel import _STEM_LABEL


log = logging.getLogger("stemforge.gui.musicgen_panel")

_STABLE_AUDIO_MODEL = "stabilityai/stable-audio-open-1.0"
_COND_TYPES    = ("None", "Audio", "MIDI", "Mix")
_AUDIO_SOURCES = ("None", "Stem from Separate tab", "Load audio file")
_MIDI_SOURCES  = ("None", "From MIDI tab", "Load MIDI file")

_P = "mg"


def _t(name: str) -> str:
    return f"{_P}_{name}"


class MusicGenPanel:
    """Stable Audio Open generation panel."""

    def __init__(self) -> None:
        self._pipeline = MusicGenPipeline()
        self._thread: threading.Thread | None = None
        self._result_path: pathlib.Path | None = None
        self._waveform = WaveformWidget("mg")

        # Stem paths from the Separate tab (keyed by internal stem name)
        self._stem_paths: dict[str, pathlib.Path] = {}
        # MIDI paths produced by the MIDI tab: label → path
        # "All stems" is always the merged file; per-stem entries follow.
        self._tab_merged_midi_obj: "Any | None" = None   # in-memory PrettyMIDI
        self._tab_midi_paths: dict[str, pathlib.Path] = {}
        # Manually loaded paths
        self._loaded_audio_path: pathlib.Path | None = None
        self._loaded_midi_path: pathlib.Path | None = None
        self._mix_path: pathlib.Path | None = None

        # File browsers (created in build_save_dialog)
        self._save_browser: FileBrowser | None = None
        self._audio_browser: FileBrowser | None = None
        self._midi_browser: FileBrowser | None = None

        # Listeners notified with (audio_path,) after a successful run
        self._result_listeners: list[Callable[[pathlib.Path], None]] = []

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        with dpg.group(horizontal=True):

            # ---- Left column: settings --------------------------------
            with dpg.child_window(width=360, height=-1, border=False):

                dpg.add_text("Describe the music", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Write a plain-English description of what you want.\n"
                        "Be specific about style, instruments, and mood.\n\n"
                        "Examples:\n"
                        "  - upbeat jazz piano with walking bass\n"
                        "  - slow ambient guitar, heavy reverb\n"
                        "  - energetic lo-fi hip-hop drum loop"
                    )
                dpg.add_input_text(
                    tag=_t("prompt"),
                    hint="e.g. upbeat jazz piano with walking bass",
                    multiline=True,
                    width=-1,
                    height=80,
                )

                dpg.add_spacer(height=12)
                dpg.add_text("Model", color=(175, 175, 255, 255))
                dpg.add_combo(
                    items=["Stable Audio Open 1.0"],
                    default_value="Stable Audio Open 1.0",
                    tag=_t("model"),
                    width=-1,
                )

                # --- Vocal Preservation Mode ----------------------------
                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_spacer(height=6)
                with dpg.group(horizontal=True):
                    dpg.add_checkbox(
                        label="Vocal Preservation Mode",
                        tag=_t("vp_enabled"),
                        default_value=False,
                        callback=self._on_vp_enable_change,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(
                            "Tune how strongly the conditioning audio guides generation.\n\n"
                            "Works with Audio or Mix conditioning.\n"
                            "For best vocal results:\n"
                            "  1. Separate vocals in the Separate tab.\n"
                            "  2. Optionally extract MIDI in the MIDI tab.\n"
                            "  3. Blend wav + MIDI in the Mix tab.\n"
                            "  4. Select Mix as conditioning here."
                        )

                with dpg.group(tag=_t("vp_group"), show=False):
                    dpg.add_spacer(height=6)

                    dpg.add_text("Conditioning Strength", color=(140, 140, 160, 255))
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(
                            "Scales the conditioning audio amplitude before the\n"
                            "model encodes it.  1.0 = full reference influence.\n"
                            "0.0 = conditioning ignored (same as no audio source)."
                        )
                    dpg.add_slider_float(
                        tag=_t("vp_strength"),
                        min_value=0.0,
                        max_value=1.0,
                        default_value=0.7,
                        width=-1,
                        format="%.2f",
                    )

                    dpg.add_spacer(height=6)
                    dpg.add_checkbox(
                        label="Timing Lock",
                        tag=_t("vp_timing_lock"),
                        default_value=True,
                        callback=self._on_timing_lock_change,
                    )
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(
                            "Generate audio window-by-window, each conditioned\n"
                            "on the corresponding slice of the source audio.\n"
                            "Preserves rhythmic alignment across the full clip."
                        )

                    with dpg.group(tag=_t("vp_window_group")):
                        dpg.add_spacer(height=4)
                        dpg.add_text("Window Size", color=(140, 140, 160, 255))
                        with dpg.tooltip(dpg.last_item()):
                            dpg.add_text(
                                "Duration of each generation window.\n"
                                "Shorter = tighter temporal alignment, more boundaries.\n"
                                "Longer = smoother but less aligned."
                            )
                        dpg.add_combo(
                            items=["5 s", "10 s", "15 s", "30 s"],
                            default_value="10 s",
                            tag=_t("vp_window_size"),
                            width=-1,
                        )

                    dpg.add_spacer(height=6)
                    dpg.add_text("Negative Prompt", color=(140, 140, 160, 255))
                    with dpg.tooltip(dpg.last_item()):
                        dpg.add_text(
                            "Describe characteristics to avoid in the output.\n"
                            "Passed to the model as a negative guidance signal."
                        )
                    dpg.add_input_text(
                        tag=_t("vp_negative_prompt"),
                        default_value=(
                            "distorted vocals, off-key singing, robotic artifacts, "
                            "background noise, clipping"
                        ),
                        multiline=False,
                        width=-1,
                    )

                dpg.add_spacer(height=12)
                dpg.add_text("Duration", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "How many seconds of audio to generate.\n"
                        "Model hard limit is 47 s at 44,100 Hz.\n"
                        "Longer clips take more time and memory.\n"
                        "Auto-set when a conditioning source is selected."
                    )
                dpg.add_slider_int(
                    tag=_t("duration"),
                    min_value=5,
                    max_value=600,
                    default_value=30,
                    width=-1,
                    format="%d s",
                )

                dpg.add_spacer(height=8)
                dpg.add_text("Diffusion steps", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Number of denoising steps.\n"
                        "More → higher quality, slower.\n"
                        "100 is a good balance."
                    )
                dpg.add_slider_int(
                    tag=_t("steps"),
                    min_value=20,
                    max_value=200,
                    default_value=100,
                    width=-1,
                    format="%d steps",
                )

                dpg.add_spacer(height=8)
                dpg.add_text("CFG scale", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Classifier-free guidance scale.\n"
                        "Higher → more prompt-faithful.\n"
                        "Lower → more varied output."
                    )
                dpg.add_slider_float(
                    tag=_t("cfg"),
                    min_value=1.0,
                    max_value=15.0,
                    default_value=7.0,
                    width=-1,
                    format="%.1f",
                )

                # --- Conditioning (mutually exclusive) ---
                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_spacer(height=6)
                dpg.add_text("Conditioning  (optional)", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Choose one conditioning input to guide generation.\n\n"
                        "Audio - reference audio clip (stem or file).\n"
                        "        The model blends its latent with your prompt.\n\n"
                        "MIDI  - MIDI file provides BPM, key, and instruments\n"
                        "        which are appended to your text prompt.\n\n"
                        "Mix   - use the rendered mix from the Mix tab as the\n"
                        "        audio conditioning reference."
                    )
                dpg.add_combo(
                    items=list(_COND_TYPES),
                    default_value=_COND_TYPES[0],
                    tag=_t("cond_type"),
                    callback=self._on_cond_type_change,
                    width=-1,
                )

                # ---- Audio sub-group ----
                with dpg.group(tag=_t("audio_cond_group"), show=False):
                    dpg.add_spacer(height=6)
                    dpg.add_text("Source", color=(140, 140, 160, 255))
                    dpg.add_combo(
                        items=list(_AUDIO_SOURCES),
                        default_value=_AUDIO_SOURCES[0],
                        tag=_t("audio_src"),
                        callback=self._on_audio_source_change,
                        width=-1,
                    )
                    # Stem picker (visible when audio_src = "Stem from Separate tab")
                    with dpg.group(tag=_t("stem_group"), show=False):
                        dpg.add_spacer(height=4)
                        dpg.add_text(
                            "Run Separate first to see stems here.",
                            tag=_t("stem_hint"),
                            color=(140, 140, 140, 255),
                        )
                        dpg.add_radio_button(
                            items=["None"],
                            default_value="None",
                            tag=_t("stem_radio"),
                            callback=self._on_stem_radio_change,
                        )
                    # File browse (visible when audio_src = "Load audio file")
                    with dpg.group(tag=_t("audio_file_group"), show=False):
                        dpg.add_spacer(height=4)
                        dpg.add_button(
                            label="  Browse…  ",
                            tag=_t("audio_browse_btn"),
                            callback=self._on_browse_audio,
                            width=-1,
                        )
                        dpg.add_text(
                            "No file selected",
                            tag=_t("audio_file_label"),
                            color=(140, 140, 140, 255),
                            wrap=340,
                        )

                # ---- MIDI sub-group ----
                with dpg.group(tag=_t("midi_cond_group"), show=False):
                    dpg.add_spacer(height=6)
                    dpg.add_text("Source", color=(140, 140, 160, 255))
                    dpg.add_combo(
                        items=list(_MIDI_SOURCES),
                        default_value=_MIDI_SOURCES[0],
                        tag=_t("midi_src"),
                        callback=self._on_midi_source_change,
                        width=-1,
                    )
                    # MIDI tab picker (visible when midi_src = "From MIDI tab")
                    with dpg.group(tag=_t("midi_tab_group"), show=False):
                        dpg.add_spacer(height=4)
                        dpg.add_text(
                            "Run Extract MIDI first to see options here.",
                            tag=_t("midi_hint"),
                            color=(140, 140, 140, 255),
                        )
                        dpg.add_radio_button(
                            items=["All stems"],
                            default_value="All stems",
                            tag=_t("midi_radio"),
                            callback=self._on_midi_radio_change,
                            enabled=False,
                        )
                    # MIDI file browse (visible when midi_src = "Load MIDI file")
                    with dpg.group(tag=_t("midi_file_group"), show=False):
                        dpg.add_spacer(height=4)
                        dpg.add_button(
                            label="  Browse…  ",
                            tag=_t("midi_browse_btn"),
                            callback=self._on_browse_midi,
                            width=-1,
                        )
                        dpg.add_text(
                            "No file selected",
                            tag=_t("midi_file_label"),
                            color=(140, 140, 140, 255),
                            wrap=340,
                        )

                # ---- Mix sub-group ----
                with dpg.group(tag=_t("mix_cond_group"), show=False):
                    dpg.add_spacer(height=6)
                    dpg.add_text(
                        "Render a mix in the Mix tab first.",
                        tag=_t("mix_status_label"),
                        color=(140, 140, 140, 255),
                        wrap=340,
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
                        "The model is loaded on the first run (~2 GB download)."
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
                        width=80,
                    )
                    dpg.add_text(default_value="", tag=_t("status"), color=(160, 160, 160, 255))

                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_text("Result", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)
                dpg.add_text("-", tag=_t("duration_info"), color=(220, 220, 220, 255))
                dpg.add_text("-", tag=_t("audio_file"), color=(140, 140, 140, 255), wrap=350)

                dpg.add_spacer(height=8)
                self._waveform.build_ui()

                dpg.add_spacer(height=8)
                dpg.add_button(
                    label="  Save as  ",
                    tag=_t("save_btn"),
                    callback=self._on_save_click,
                    width=150,
                    enabled=False,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Copy the generated file to a location you choose.")

    def build_save_dialog(self) -> None:
        """Create file browsers at the top DearPyGUI level."""
        self._save_browser = FileBrowser(
            tag="mg_save_browser",
            callback=self._on_file_save_selected,
            extensions=frozenset({".wav", ".flac", ".ogg"}),
            mode="save",
        )
        self._save_browser.build()

        self._audio_browser = FileBrowser(
            tag="mg_audio_browser",
            callback=self._on_audio_file_selected,
            extensions=frozenset({".wav", ".flac", ".mp3", ".ogg", ".aif", ".aiff"}),
            mode="open",
        )
        self._audio_browser.build()

        self._midi_browser = FileBrowser(
            tag="mg_midi_browser",
            callback=self._on_midi_file_selected,
            extensions=frozenset({".mid", ".midi"}),
            mode="open",
        )
        self._midi_browser.build()

    # ------------------------------------------------------------------
    # Inter-panel notifications
    # ------------------------------------------------------------------

    def notify_stems_ready(self, stem_paths: dict[str, pathlib.Path]) -> None:
        """Called by DemucsPanel after a successful separation run (may be bg thread)."""
        self._stem_paths = dict(stem_paths)
        labels = ["None"] + [_STEM_LABEL.get(k, k) for k in stem_paths]

        def _update():
            if dpg.does_item_exist(_t("stem_radio")):
                dpg.configure_item(_t("stem_radio"), items=labels)
                dpg.set_value(_t("stem_radio"), "None")
            if dpg.does_item_exist(_t("stem_hint")):
                dpg.configure_item(_t("stem_hint"), show=False)
        schedule_ui(_update)

    def notify_midi_ready(
        self,
        midi_path: pathlib.Path,
        stem_midi_data: dict,
    ) -> None:
        """Called by MidiPanel after a successful MIDI extraction run (may be bg thread)."""
        self._tab_merged_midi_obj = midi_path   # first arg is now a PrettyMIDI object
        self._tab_midi_paths = {"All stems": None}  # placeholder for radio items
        labels = list(self._tab_midi_paths.keys())

        def _update():
            if dpg.does_item_exist(_t("midi_radio")):
                dpg.configure_item(_t("midi_radio"), items=labels, enabled=True)
                dpg.set_value(_t("midi_radio"), labels[0])
            if dpg.does_item_exist(_t("midi_hint")):
                dpg.configure_item(_t("midi_hint"), show=False)
            # Auto-switch to MIDI tab source if user hasn't chosen anything yet
            if dpg.does_item_exist(_t("midi_src")):
                if dpg.get_value(_t("midi_src")) == "None":
                    dpg.set_value(_t("midi_src"), "From MIDI tab")
                    self._on_midi_source_change(None, "From MIDI tab", None)
        schedule_ui(_update)

    def notify_mix_ready(self, mix_path: pathlib.Path) -> None:
        """Called by MixPanel after a successful render (may be bg thread)."""
        self._mix_path = mix_path
        label = mix_path.name if mix_path else "No mix rendered yet"
        def _update():
            if dpg.does_item_exist(_t("mix_status_label")):
                dpg.set_value(_t("mix_status_label"), label)
        schedule_ui(_update)

    def add_result_listener(
        self,
        callback: Callable[[pathlib.Path], None],
    ) -> None:
        """Register *callback* invoked with the output audio path after a successful run."""
        self._result_listeners.append(callback)

    # ------------------------------------------------------------------
    # Callbacks — conditioning type selector
    # ------------------------------------------------------------------

    def _on_cond_type_change(self, sender, app_data, user_data) -> None:
        cond = app_data if app_data is not None else dpg.get_value(_t("cond_type"))
        for tag, visible in (
            (_t("audio_cond_group"), cond == "Audio"),
            (_t("midi_cond_group"),  cond == "MIDI"),
            (_t("mix_cond_group"),   cond == "Mix"),
        ):
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, show=visible)
        self._auto_set_duration(cond)

    # ------------------------------------------------------------------
    # Callbacks — Vocal Preservation Mode
    # ------------------------------------------------------------------

    def _on_vp_enable_change(self, sender, app_data, user_data) -> None:
        enabled = app_data if app_data is not None else dpg.get_value(_t("vp_enabled"))
        if dpg.does_item_exist(_t("vp_group")):
            dpg.configure_item(_t("vp_group"), show=bool(enabled))

    def _on_timing_lock_change(self, sender, app_data, user_data) -> None:
        locked = app_data if app_data is not None else dpg.get_value(_t("vp_timing_lock"))
        if dpg.does_item_exist(_t("vp_window_group")):
            dpg.configure_item(_t("vp_window_group"), enabled=bool(locked))

    # ------------------------------------------------------------------
    # Callbacks — audio conditioning
    # ------------------------------------------------------------------

    def _on_audio_source_change(self, sender, app_data, user_data) -> None:
        src = app_data if app_data is not None else dpg.get_value(_t("audio_src"))
        is_stem = src == "Stem from Separate tab"
        is_file = src == "Load audio file"
        if dpg.does_item_exist(_t("stem_group")):
            dpg.configure_item(_t("stem_group"), show=is_stem)
        if dpg.does_item_exist(_t("audio_file_group")):
            dpg.configure_item(_t("audio_file_group"), show=is_file)
        self._auto_set_duration("Audio")

    def _on_midi_source_change(self, sender, app_data, user_data) -> None:
        src = app_data if app_data is not None else dpg.get_value(_t("midi_src"))
        is_tab  = src == "From MIDI tab"
        is_file = src == "Load MIDI file"
        if dpg.does_item_exist(_t("midi_tab_group")):
            dpg.configure_item(_t("midi_tab_group"), show=is_tab)
        if dpg.does_item_exist(_t("midi_file_group")):
            dpg.configure_item(_t("midi_file_group"), show=is_file)
        self._auto_set_duration("MIDI")

    def _on_stem_radio_change(self, sender, app_data, user_data) -> None:
        label = app_data
        if label == "None":
            return
        key = next((k for k, v in _STEM_LABEL.items() if v == label), None)
        if key and key in self._stem_paths:
            self._set_duration_from_path(self._stem_paths[key])

    def _on_midi_radio_change(self, sender, app_data, user_data) -> None:
        if self._tab_merged_midi_obj is not None:
            try:
                secs = int(round(self._tab_merged_midi_obj.get_end_time()))
                secs = max(5, min(600, secs))
                if dpg.does_item_exist(_t("duration")):
                    dpg.set_value(_t("duration"), secs)
            except Exception:
                pass

    def _set_duration_from_path(self, path: pathlib.Path) -> None:
        """Read audio duration from *path* metadata and update the duration slider."""
        try:
            info = sf.info(str(path))
            seconds = int(round(info.frames / info.samplerate))
            seconds = max(5, min(600, seconds))
            if dpg.does_item_exist(_t("duration")):
                dpg.set_value(_t("duration"), seconds)
        except Exception:
            pass

    def _set_duration_from_midi(self, path: pathlib.Path) -> None:
        """Read MIDI duration from *path* and update the duration slider."""
        try:
            mid = mido.MidiFile(str(path))
            seconds = int(round(mid.length))
            seconds = max(5, min(600, seconds))
            if dpg.does_item_exist(_t("duration")):
                dpg.set_value(_t("duration"), seconds)
        except Exception:
            pass

    def _auto_set_duration(self, cond_type: str | None = None) -> None:
        """Auto-set the duration slider from whatever conditioning source is active."""
        if cond_type is None:
            cond_type = dpg.get_value(_t("cond_type")) if dpg.does_item_exist(_t("cond_type")) else "None"

        if cond_type == "Audio":
            audio_src = dpg.get_value(_t("audio_src")) if dpg.does_item_exist(_t("audio_src")) else "None"
            if audio_src == "Stem from Separate tab":
                label = dpg.get_value(_t("stem_radio")) if dpg.does_item_exist(_t("stem_radio")) else "None"
                if label and label != "None":
                    key = next((k for k, v in _STEM_LABEL.items() if v == label), None)
                    if key and key in self._stem_paths:
                        self._set_duration_from_path(self._stem_paths[key])
            elif audio_src == "Load audio file" and self._loaded_audio_path:
                self._set_duration_from_path(self._loaded_audio_path)

        elif cond_type == "MIDI":
            midi_src = dpg.get_value(_t("midi_src")) if dpg.does_item_exist(_t("midi_src")) else "None"
            if midi_src == "From MIDI tab" and self._tab_merged_midi_obj is not None:
                try:
                    secs = int(round(self._tab_merged_midi_obj.get_end_time()))
                    secs = max(5, min(600, secs))
                    if dpg.does_item_exist(_t("duration")):
                        dpg.set_value(_t("duration"), secs)
                except Exception:
                    pass
            elif midi_src == "Load MIDI file" and self._loaded_midi_path:
                self._set_duration_from_midi(self._loaded_midi_path)

        elif cond_type == "Mix":
            if self._mix_path and self._mix_path.exists():
                self._set_duration_from_path(self._mix_path)

    def _on_browse_audio(self, sender, app_data, user_data) -> None:
        if self._audio_browser:
            self._audio_browser.show()

    def _on_browse_midi(self, sender, app_data, user_data) -> None:
        if self._midi_browser:
            self._midi_browser.show()

    def _on_audio_file_selected(self, path: pathlib.Path) -> None:
        self._loaded_audio_path = path
        if dpg.does_item_exist(_t("audio_file_label")):
            dpg.set_value(_t("audio_file_label"), str(path))
        self._set_duration_from_path(path)

    def _on_midi_file_selected(self, path: pathlib.Path) -> None:
        self._loaded_midi_path = path
        if dpg.does_item_exist(_t("midi_file_label")):
            dpg.set_value(_t("midi_file_label"), str(path))
        self._set_duration_from_midi(path)

    # ------------------------------------------------------------------
    # Callbacks — run / save
    # ------------------------------------------------------------------

    def _on_run_click(self, sender, app_data, user_data) -> None:
        if self._thread and self._thread.is_alive():
            return
        # Capture all UI values on the main thread
        ui = self._capture_run_inputs()
        self._thread = threading.Thread(target=self._run, args=(ui,), daemon=True)
        self._thread.start()

    def _capture_run_inputs(self) -> dict:
        """Read all DPG widget values needed by _run() — main thread only."""
        prompt     = dpg.get_value(_t("prompt")).strip()   if dpg.does_item_exist(_t("prompt"))     else ""
        duration   = float(dpg.get_value(_t("duration")))  if dpg.does_item_exist(_t("duration"))   else 30.0
        steps      = int(dpg.get_value(_t("steps")))        if dpg.does_item_exist(_t("steps"))      else 100
        cfg        = float(dpg.get_value(_t("cfg")))        if dpg.does_item_exist(_t("cfg"))        else 7.0
        cond_type  = dpg.get_value(_t("cond_type"))         if dpg.does_item_exist(_t("cond_type"))  else "None"
        audio_src  = dpg.get_value(_t("audio_src"))         if dpg.does_item_exist(_t("audio_src"))  else "None"
        midi_src   = dpg.get_value(_t("midi_src"))          if dpg.does_item_exist(_t("midi_src"))   else "None"
        stem_label = dpg.get_value(_t("stem_radio"))        if dpg.does_item_exist(_t("stem_radio")) else "None"
        # Vocal Preservation Mode
        vp_enabled  = bool(dpg.get_value(_t("vp_enabled")))         if dpg.does_item_exist(_t("vp_enabled"))         else False
        vp_strength = float(dpg.get_value(_t("vp_strength")))       if dpg.does_item_exist(_t("vp_strength"))        else 0.7
        vp_lock     = bool(dpg.get_value(_t("vp_timing_lock")))     if dpg.does_item_exist(_t("vp_timing_lock"))     else True
        vp_win_str  = dpg.get_value(_t("vp_window_size"))           if dpg.does_item_exist(_t("vp_window_size"))     else "10 s"
        vp_neg      = dpg.get_value(_t("vp_negative_prompt")).strip() if dpg.does_item_exist(_t("vp_negative_prompt")) else ""
        vp_win_sec  = float(vp_win_str.rstrip(" s") or "10")
        return dict(
            prompt=prompt, duration=duration, steps=steps, cfg=cfg,
            cond_type=cond_type, audio_src=audio_src, midi_src=midi_src, stem_label=stem_label,
            vp_enabled=vp_enabled, vp_strength=vp_strength, vp_lock=vp_lock,
            vp_win_sec=vp_win_sec, vp_neg=vp_neg,
        )

    def _on_save_click(self, sender, app_data, user_data) -> None:
        if self._save_browser:
            self._save_browser.show()

    def _on_file_save_selected(self, dest: pathlib.Path) -> None:
        if not self._result_path or not self._result_path.exists():
            return
        if not dest.suffix:
            dest = dest.with_suffix(".wav")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self._result_path, dest)
            set_widget_text(_t("status"), f"Saved → {dest}")
        except Exception as exc:
            set_widget_text(_t("status"), f"Save failed: {exc}")

    # ------------------------------------------------------------------
    # Background thread
    # ------------------------------------------------------------------

    def _run(self, ui: dict) -> None:
        schedule_ui(lambda: dpg.configure_item(_t("run_btn"), enabled=False))
        schedule_ui(lambda: dpg.configure_item(_t("save_btn"), enabled=False))
        schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))

        def _progress(pct: float, stage: str) -> None:
            schedule_ui(lambda _p=pct: dpg.set_value(_t("progress"), _p / 100.0))
            set_widget_text(_t("status"), stage)

        try:
            prompt = ui["prompt"]
            if not prompt:
                set_widget_text(_t("status"), "Enter a text prompt first.")
                return

            duration   = ui["duration"]
            steps      = ui["steps"]
            cfg        = ui["cfg"]
            cond_type  = ui["cond_type"]
            audio_src  = ui["audio_src"]
            midi_src   = ui["midi_src"]
            stem_label = ui["stem_label"]

            init_audio_path: pathlib.Path | None = None
            midi_path: pathlib.Path | None = None

            if cond_type == "Audio":
                if audio_src == "Stem from Separate tab":
                    if stem_label and stem_label != "None":
                        key = next((k for k, v in _STEM_LABEL.items() if v == stem_label), None)
                        if key and key in self._stem_paths:
                            init_audio_path = self._stem_paths[key]
                elif audio_src == "Load audio file":
                    init_audio_path = self._loaded_audio_path

            elif cond_type == "MIDI":
                if midi_src == "From MIDI tab":
                    if self._tab_merged_midi_obj is not None:
                        import time as _time
                        _MIDI_DIR.mkdir(parents=True, exist_ok=True)
                        midi_path = _MIDI_DIR / f"merged_tmp_{int(_time.time())}.mid"
                        self._tab_merged_midi_obj.write(str(midi_path))
                elif midi_src == "Load MIDI file":
                    midi_path = self._loaded_midi_path

            elif cond_type == "Mix":
                init_audio_path = self._mix_path

            # Cap duration to the audio source's actual length so the model
            # never has to pad a conditioning clip.  Also syncs the slider.
            if init_audio_path and init_audio_path.exists():
                try:
                    _info = sf.info(str(init_audio_path))
                    _audio_secs = _info.frames / _info.samplerate
                    if duration > _audio_secs:
                        duration = max(5.0, float(int(round(_audio_secs))))
                        schedule_ui(lambda _d=int(duration): (
                            dpg.set_value(_t("duration"), _d)
                            if dpg.does_item_exist(_t("duration")) else None
                        ))
                except Exception:
                    pass

            vp_enabled = ui["vp_enabled"]
            # When VP is active, its negative prompt overrides the default.
            effective_neg = ui["vp_neg"] if vp_enabled and ui["vp_neg"] else "low quality, distorted, noise, clipping"

            config = MusicGenConfig(
                model_name             = _STABLE_AUDIO_MODEL,
                prompt                 = prompt,
                duration_seconds       = duration,
                steps                  = steps,
                cfg_scale              = cfg,
                negative_prompt        = effective_neg,
                init_audio_path        = init_audio_path,
                midi_path              = midi_path,
                output_dir             = _MUSICGEN_DIR,
                vocal_preservation     = vp_enabled,
                conditioning_strength  = ui["vp_strength"],
                timing_lock            = ui["vp_lock"],
                window_size_seconds    = ui["vp_win_sec"],
            )
            self._pipeline.configure(config)
            self._pipeline.set_progress_callback(_progress)

            _progress(2.0, "Loading model…")
            self._pipeline.load_model()

            result: MusicGenResult = self._pipeline.run("")

            self._result_path = result.audio_path
            app_state.musicgen_path = result.audio_path
            set_widget_text(
                _t("duration_info"),
                f"{result.duration_seconds:.1f} s  ·  {result.sample_rate} Hz",
            )
            set_widget_text(_t("audio_file"), str(result.audio_path))
            self._waveform.load_async(result.audio_path)
            schedule_ui(lambda: dpg.configure_item(_t("save_btn"), enabled=True))
            _progress(100.0, f"Done - {result.duration_seconds:.1f} s")
            for cb in self._result_listeners:
                try:
                    cb(result.audio_path)
                except Exception:
                    pass

        except ModelLoadError as exc:
            log.error("Model load failed: %s", exc)
            msg = str(exc)
            if any(kw in msg.lower() for kw in ("gated", "403", "401", "authorized", "token", "login")):
                set_widget_text(
                    _t("status"),
                    "HuggingFace auth required - set HF_TOKEN in .env "
                    "or run: huggingface-cli login",
                )
            else:
                set_widget_text(_t("status"), "Model load failed - see log for details")
            schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))
        except Exception as exc:
            log.exception("Generation failed")
            set_widget_text(_t("status"), f"Error - see log for details: {type(exc).__name__}")
            schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))
        finally:
            schedule_ui(lambda: dpg.configure_item(_t("run_btn"), enabled=True))
