"""
StemForge — main DearPyGUI application.

Creates a single primary window containing:
  · A persistent loader bar at the top (file browse, path display, clear).
  · A Stop audio button to interrupt any in-progress playback.
  · A tabbed workspace: Separate · MIDI · Generate · Export.

All panel singletons are created here, their UIs are built inside the
correct DearPyGUI parent contexts, and top-level file dialogs are
registered before the render loop starts.
"""

import logging

import dearpygui.dearpygui as dpg

from gui.constants import _STEMS_DIR, _MIDI_DIR, _MUSICGEN_DIR, _EXPORT_DIR
from gui.components.loader import LoaderPanel
from gui.components.demucs_panel import DemucsPanel
from gui.components.basicpitch_panel import BasicPitchPanel
from gui.components.musicgen_panel import MusicGenPanel
from gui.components.export_panel import ExportPanel


log = logging.getLogger("stemforge.gui.app")

_VP_WIDTH  = 1280
_VP_HEIGHT = 820


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

def _build_theme() -> int:
    """Return a custom dark-blue theme tag."""
    with dpg.theme() as theme_id:
        with dpg.theme_component(dpg.mvAll):
            # Background palette
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,        ( 18,  18,  24, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,         ( 24,  24,  32, 255))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg,         ( 28,  28,  38, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,         ( 30,  30,  45, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,   ( 50,  50,  90, 255))
            # Tabs
            dpg.add_theme_color(dpg.mvThemeCol_Tab,             ( 38,  38,  58, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered,      ( 70,  70, 130, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TabActive,       ( 90,  90, 160, 255))
            # Buttons
            dpg.add_theme_color(dpg.mvThemeCol_Button,          ( 60,  60, 120, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,   ( 80,  80, 150, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,    (100, 100, 180, 255))
            # Input fields
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg,         ( 38,  38,  55, 255))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered,  ( 55,  55,  80, 255))
            # Combo / list headers
            dpg.add_theme_color(dpg.mvThemeCol_Header,          ( 60,  60, 120, 255))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered,   ( 80,  80, 150, 255))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive,    (100, 100, 180, 255))
            # Sliders
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab,      (120, 120, 200, 255))
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive,(150, 150, 240, 255))
            # Checkboxes
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark,       (150, 150, 240, 255))
            # Progress bars (rendered using the histogram colour)
            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram,   (100, 100, 200, 255))
            # Text
            dpg.add_theme_color(dpg.mvThemeCol_Text,            (220, 220, 230, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled,    ( 90,  90, 100, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Separator,       ( 60,  60,  80, 255))
            # Rounding
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,   6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,  8)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding,   6)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding,    4)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding,     6)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding,    8, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,     8, 6)
    return theme_id


# ---------------------------------------------------------------------------
# Stop-audio helper
# ---------------------------------------------------------------------------

def _stop_audio(sender, app_data, user_data) -> None:
    """Stop any audio currently playing via sounddevice."""
    try:
        import sounddevice as sd
        sd.stop()
    except Exception as exc:
        log.debug("Stop audio: %s", exc)


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Create the DearPyGUI viewport and run the event loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    )

    # ---- DearPyGUI setup -----------------------------------------------
    dpg.create_context()

    theme = _build_theme()
    dpg.bind_theme(theme)

    # Pre-create output directories so pipelines never have to.
    for d in (_STEMS_DIR, _MIDI_DIR, _MUSICGEN_DIR, _EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # ---- Panel singletons ----------------------------------------------
    _loader     = LoaderPanel()
    _demucs     = DemucsPanel()
    _basicpitch = BasicPitchPanel()
    _musicgen   = MusicGenPanel()
    _export     = ExportPanel()

    # ---- Top-level file dialogs (must live outside all windows) --------
    _loader.build_file_dialog()
    _basicpitch.build_save_dialog()
    _musicgen.build_save_dialog()
    _export.build_dir_dialog()

    # ---- Primary window ------------------------------------------------
    with dpg.window(tag="primary_window"):

        # App title
        dpg.add_text("StemForge", color=(175, 175, 255, 255))
        dpg.add_text(
            "Stem separation  ·  MIDI extraction  ·  Music generation",
            color=(100, 100, 120, 255),
        )
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # Loader bar + stop button on the same row
        with dpg.group(horizontal=False):
            _loader.build_ui()
            dpg.add_spacer(height=4)
            with dpg.group(horizontal=True):
                dpg.add_button(
                    label="■  Stop audio",
                    callback=_stop_audio,
                    width=110,
                    height=26,
                )
                with dpg.tooltip(dpg.last_item()):
                    dpg.add_text("Stop any audio that is currently playing.")

        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # Tab bar
        with dpg.tab_bar():

            with dpg.tab(label="  Separate  "):
                _demucs.build_ui()

            with dpg.tab(label="  MIDI  "):
                _basicpitch.build_ui()

            with dpg.tab(label="  Generate  "):
                _musicgen.build_ui()

            with dpg.tab(label="  Export  "):
                _export.build_ui()

    # ---- Viewport & render loop ----------------------------------------
    dpg.create_viewport(
        title="StemForge",
        width=_VP_WIDTH,
        height=_VP_HEIGHT,
        min_width=900,
        min_height=600,
    )
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("primary_window", True)
    dpg.start_dearpygui()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
