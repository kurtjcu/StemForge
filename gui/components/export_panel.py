"""
Export panel for StemForge.

Collects all pipeline outputs (separated stems, MIDI file, generated
audio) and lets the user copy or transcode them to a folder of their
choice.  The export job runs on a daemon thread.
"""

import pathlib
import logging
import shutil
import threading
import traceback
from typing import Callable

import dearpygui.dearpygui as dpg

from utils.audio_io import read_audio, write_audio
from gui.state import app_state, set_widget_text, make_copy_callback
from gui.components.file_browser import FileBrowser
from gui.ui_queue import schedule_ui


log = logging.getLogger("stemforge.gui.export_panel")

EXPORT_FORMATS: tuple[str, ...] = ("wav", "flac", "mp3", "ogg")

_P = "exp"   # tag namespace

# Potential artefacts in display order: (state_key, display_label)
_ARTEFACT_DEFS: tuple[tuple[str, str], ...] = (
    ("vocals",   "Singing voice stem"),
    ("drums",    "Drums & percussion stem"),
    ("bass",     "Bass stem"),
    ("other",    "Everything else stem"),
    ("guitar",   "Guitar stem"),
    ("piano",    "Piano stem"),
    ("midi",     "MIDI transcription"),
    ("musicgen", "Generated audio"),
    ("mix",      "Mix render (FLAC)"),
)

_STEM_KEYS: tuple[str, ...] = ("vocals", "drums", "bass", "other", "guitar", "piano")


def _t(name: str) -> str:
    return f"{_P}_{name}"


class ExportPanel:
    """Artefact export panel."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._default_dir = pathlib.Path.home() / "Music" / "StemForge"
        self._dir_browser = FileBrowser(
            tag="exp_dir_browser",
            callback=self._on_dir_selected,
            mode="dir",
        )

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def build_ui(self) -> None:
        with dpg.group(horizontal=True):

            # ---- Left column: artefacts + settings --------------------
            with dpg.child_window(width=340, height=-1, border=False):

                dpg.add_text("Export as sound files", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Tick the pipeline outputs you want to save.\n"
                        "All audio is written in the chosen sound format.\n"
                        "Items are greyed out until the pipeline that\n"
                        "produces them has finished successfully."
                    )
                for key, label in _ARTEFACT_DEFS:
                    dpg.add_checkbox(
                        label=label,
                        tag=_t(f"chk_{key}"),
                        default_value=False,
                        enabled=False,
                    )

                dpg.add_spacer(height=8)
                dpg.add_button(
                    label="Refresh list",
                    tag=_t("refresh_btn"),
                    callback=self._on_refresh,
                    width=-1,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Re-scan pipeline outputs and enable available items.")

                dpg.add_spacer(height=18)
                dpg.add_text("Audio format", color=(175, 175, 255, 255))
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text(
                        "Format to use when writing audio stems and\n"
                        "generated audio.  MIDI is always saved as .mid."
                    )
                dpg.add_combo(
                    items=list(EXPORT_FORMATS),
                    default_value="wav",
                    tag=_t("format"),
                    width=-1,
                )

                dpg.add_spacer(height=18)
                dpg.add_text("Destination folder", color=(175, 175, 255, 255))
                dpg.add_input_text(
                    tag=_t("outdir"),
                    default_value=str(self._default_dir),
                    width=-1,
                )
                dpg.add_button(
                    label="Browse",
                    tag=_t("browse_btn"),
                    callback=self._on_browse,
                    width=-1,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Choose where to save the exported files.")

                dpg.add_spacer(height=20)
                dpg.add_button(
                    label="  Export  ",
                    tag=_t("run_btn"),
                    callback=self._on_export_click,
                    width=-1,
                    height=40,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Write all ticked files to the destination folder.")

            # ---- Right column: progress + results ---------------------
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
                        width=90,
                    )
                    dpg.add_text(default_value="", tag=_t("status"), color=(160, 160, 160, 255))

                dpg.add_spacer(height=14)
                dpg.add_separator()
                dpg.add_text("Exported files", color=(175, 175, 255, 255))
                dpg.add_spacer(height=4)
                dpg.add_text(
                    "-",
                    tag=_t("result_list"),
                    color=(140, 140, 140, 255),
                    wrap=350,
                )

    def build_dir_dialog(self) -> None:
        """Create the custom directory browser at the top DearPyGUI level."""
        self._dir_browser.build()

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _on_refresh(self, sender, app_data, user_data) -> None:
        stem_paths = app_state.stem_paths
        midi_path  = app_state.midi_path
        mg_path    = app_state.musicgen_path

        for key in _STEM_KEYS:
            src = stem_paths.get(key)
            ok  = src is not None and src.exists()
            dpg.configure_item(_t(f"chk_{key}"), enabled=ok)
            dpg.set_value(_t(f"chk_{key}"), ok)

        midi_ok = midi_path is not None and midi_path.exists()
        dpg.configure_item(_t("chk_midi"), enabled=midi_ok)
        dpg.set_value(_t("chk_midi"), midi_ok)

        mg_ok = mg_path is not None and mg_path.exists()
        dpg.configure_item(_t("chk_musicgen"), enabled=mg_ok)
        dpg.set_value(_t("chk_musicgen"), mg_ok)

        mix_path = app_state.mix_path
        mix_ok = mix_path is not None and mix_path.exists()
        dpg.configure_item(_t("chk_mix"), enabled=mix_ok)
        dpg.set_value(_t("chk_mix"), mix_ok)

    # ------------------------------------------------------------------
    # Notification hooks (called by other panels after a successful run)
    # ------------------------------------------------------------------

    def notify_stems_ready(self, stem_paths: dict[str, pathlib.Path]) -> None:
        """Called by DemucsPanel after a successful separation run (may be bg thread)."""
        schedule_ui(lambda: self._on_refresh(None, None, None))

    def notify_midi_ready(
        self,
        merged_midi_data,
        stem_midi_data: dict,
    ) -> None:
        """Called by MidiPanel after a successful MIDI extraction run (may be bg thread)."""
        schedule_ui(lambda: self._on_refresh(None, None, None))

    def notify_musicgen_ready(self, path: pathlib.Path) -> None:
        """Called by MusicGenPanel after a successful generation run (may be bg thread)."""
        schedule_ui(lambda: self._on_refresh(None, None, None))

    def notify_mix_ready(self, path: pathlib.Path) -> None:
        """Called by MixPanel after a successful mix render (may be bg thread)."""
        schedule_ui(lambda: self._on_refresh(None, None, None))

    def _on_browse(self, sender, app_data, user_data) -> None:
        self._dir_browser.show()

    def _on_dir_selected(self, folder: pathlib.Path) -> None:
        dpg.set_value(_t("outdir"), str(folder))

    def _on_export_click(self, sender, app_data, user_data) -> None:
        if self._thread and self._thread.is_alive():
            return
        # Capture all UI values on the main thread
        ui = self._capture_export_inputs()
        self._thread = threading.Thread(target=self._run_export, args=(ui,), daemon=True)
        self._thread.start()

    def _capture_export_inputs(self) -> dict:
        """Read all DPG widget values needed by _run_export() — main thread only."""
        outdir_str = dpg.get_value(_t("outdir")).strip() if dpg.does_item_exist(_t("outdir")) else ""
        fmt = dpg.get_value(_t("format")) if dpg.does_item_exist(_t("format")) else "wav"

        checked_stems = [
            key for key in _STEM_KEYS
            if dpg.does_item_exist(_t(f"chk_{key}")) and dpg.get_value(_t(f"chk_{key}"))
        ]
        chk_midi = dpg.get_value(_t("chk_midi")) if dpg.does_item_exist(_t("chk_midi")) else False
        chk_mg   = dpg.get_value(_t("chk_musicgen")) if dpg.does_item_exist(_t("chk_musicgen")) else False
        chk_mix  = dpg.get_value(_t("chk_mix")) if dpg.does_item_exist(_t("chk_mix")) else False

        return dict(
            outdir_str=outdir_str, fmt=fmt,
            checked_stems=checked_stems, chk_midi=chk_midi,
            chk_musicgen=chk_mg, chk_mix=chk_mix,
        )

    # ------------------------------------------------------------------
    # Background export thread
    # ------------------------------------------------------------------

    def _run_export(self, ui: dict) -> None:
        schedule_ui(lambda: dpg.configure_item(_t("run_btn"), enabled=False))
        schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))
        schedule_ui(lambda: dpg.set_value(_t("result_list"), ""))

        try:
            outdir_str = ui["outdir_str"]
            if not outdir_str:
                set_widget_text(_t("status"), "Set a destination folder first.")
                return

            out = pathlib.Path(outdir_str)
            out.mkdir(parents=True, exist_ok=True)

            fmt         = ui["fmt"]
            stem_paths  = app_state.stem_paths
            midi_path   = app_state.midi_path
            mg_path     = app_state.musicgen_path
            mix_path    = app_state.mix_path

            # Collect (source, dest_name, is_audio)
            tasks: list[tuple[pathlib.Path, str, bool]] = []

            for key in ui["checked_stems"]:
                src = stem_paths.get(key)
                if src and src.exists():
                    tasks.append((src, f"{key}.{fmt}", True))

            if ui["chk_midi"]:
                if midi_path and midi_path.exists():
                    tasks.append((midi_path, midi_path.name, False))

            if ui["chk_musicgen"]:
                if mg_path and mg_path.exists():
                    tasks.append((mg_path, f"generated.{fmt}", True))

            if ui["chk_mix"]:
                if mix_path and mix_path.exists():
                    tasks.append((mix_path, f"mix.{fmt}", True))

            if not tasks:
                set_widget_text(_t("status"), "Nothing ticked - check at least one file.")
                return

            written: list[str] = []
            for i, (src, dest_name, is_audio) in enumerate(tasks):
                schedule_ui(lambda _p=i, _n=len(tasks): dpg.set_value(_t("progress"), _p / _n))
                set_widget_text(_t("status"), f"Writing {dest_name}...")
                dest = out / dest_name
                src_ext = src.suffix.lower().lstrip(".")
                if is_audio and src_ext != fmt:
                    waveform, sr = read_audio(src, mono=False)
                    write_audio(waveform, sr, dest)
                else:
                    shutil.copy2(src, dest)
                written.append(str(dest))

            schedule_ui(lambda: dpg.set_value(_t("progress"), 1.0))
            set_widget_text(_t("status"), f"Done - {len(written)} file(s) exported")
            result_text = "\n".join(written)
            schedule_ui(lambda: dpg.set_value(_t("result_list"), result_text))

        except Exception as exc:
            traceback.print_exc()
            set_widget_text(_t("status"), f"Error: {exc}")
            schedule_ui(lambda: dpg.set_value(_t("progress"), 0.0))
        finally:
            schedule_ui(lambda: dpg.configure_item(_t("run_btn"), enabled=True))

