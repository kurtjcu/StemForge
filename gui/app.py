"""
StemForge — main DearPyGUI application.

Creates a single primary window containing:
  · A persistent loader bar at the top (file browse, waveform preview, clear).
  · A tabbed workspace: Separate · MIDI · Generate · Export.

Each panel owns its own Play/Stop controls via WaveformWidget; there is no
global Stop button.  The manual render loop calls tick_all() every frame so
waveform cursors animate smoothly.
"""

import logging
import pathlib

import dearpygui.dearpygui as dpg

from gui.constants import _STEMS_DIR, _MIDI_DIR, _MUSICGEN_DIR, _EXPORT_DIR
from gui.components.loader import LoaderPanel
from gui.components.demucs_panel import DemucsPanel
from gui.components.midi_panel import MidiPanel
from gui.components.musicgen_panel import MusicGenPanel
from gui.components.export_panel import ExportPanel
from gui.components.waveform_widget import tick_all
from gui.icons import load_icons


log = logging.getLogger("stemforge.gui.app")

_VP_WIDTH  = 1280
_VP_HEIGHT = 820


# ---------------------------------------------------------------------------
# Font setup
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = [
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


def _setup_fonts() -> None:
    """Load DejaVuSans at 18 px and bind it as the global default font."""
    font_path: pathlib.Path | None = None
    for candidate in _FONT_CANDIDATES:
        p = pathlib.Path(candidate)
        if p.exists():
            font_path = p
            break

    if font_path is None:
        log.warning("DejaVuSans.ttf not found — using DearPyGUI built-in font size")
        return

    with dpg.font_registry():
        default_font = dpg.add_font(str(font_path), 18)
    dpg.bind_font(default_font)
    log.info("Loaded font: %s", font_path)


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

def _build_theme() -> int:
    """Return a custom dark-blue 3D-styled theme tag."""
    with dpg.theme() as theme_id:
        with dpg.theme_component(dpg.mvAll):
            # Background palette
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg,        ( 18,  18,  24, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg,         ( 24,  24,  32, 255))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg,         ( 28,  28,  38, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBg,         ( 30,  30,  45, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TitleBgActive,   ( 50,  50,  90, 255))
            # 3D border effect
            dpg.add_theme_color(dpg.mvThemeCol_Border,          ( 80,  80, 120, 255))
            dpg.add_theme_color(dpg.mvThemeCol_BorderShadow,    ( 10,  10,  15, 255))
            # Tabs — strong contrast between inactive/active
            dpg.add_theme_color(dpg.mvThemeCol_Tab,             ( 22,  22,  38, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered,      ( 60,  60, 120, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TabActive,       (110, 110, 200, 255))
            # Buttons — more depth
            dpg.add_theme_color(dpg.mvThemeCol_Button,          ( 50,  50, 110, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,   ( 75,  75, 150, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive,    (110, 110, 190, 255))
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
            # Progress bars
            dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram,   (100, 100, 200, 255))
            # Text
            dpg.add_theme_color(dpg.mvThemeCol_Text,            (220, 220, 230, 255))
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled,    ( 90,  90, 100, 255))
            dpg.add_theme_color(dpg.mvThemeCol_Separator,       ( 60,  60,  80, 255))
            # Shape / rounding
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding,   6)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding,  8)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding,   6)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding,    4)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding,     6)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding,    8, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing,     8, 6)
    return theme_id


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Create the DearPyGUI viewport and run the manual render loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    )

    # ---- DearPyGUI setup -----------------------------------------------
    dpg.create_context()

    _setup_fonts()

    with dpg.texture_registry(tag="stemforge_textures"):
        load_icons()

    theme = _build_theme()
    dpg.bind_theme(theme)

    # Pre-create output directories so pipelines never have to.
    for d in (_STEMS_DIR, _MIDI_DIR, _MUSICGEN_DIR, _EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # ---- Panel singletons ----------------------------------------------
    _loader   = LoaderPanel()
    _demucs   = DemucsPanel()
    _midi     = MidiPanel()
    _musicgen = MusicGenPanel()
    _export   = ExportPanel()

    # ---- Inter-panel wiring --------------------------------------------
    _demucs.add_result_listener(_midi.notify_stems_ready)
    _demucs.add_result_listener(_musicgen.notify_stems_ready)
    _demucs.add_result_listener(_export.notify_stems_ready)
    _midi.add_result_listener(_musicgen.notify_midi_ready)
    _midi.add_result_listener(_export.notify_midi_ready)
    _musicgen.add_result_listener(_export.notify_musicgen_ready)
    # Auto-trigger Roformer analysis when a new file is loaded
    _loader.add_on_load_callback(_demucs.on_file_loaded)

    # ---- Top-level dialogs / browser (must live outside all windows) ---
    _loader.build_file_browser()
    _demucs.build_save_dialog()
    _midi.build_browsers()
    _musicgen.build_save_dialog()
    _export.build_dir_dialog()

    # ---- Primary window ------------------------------------------------
    with dpg.window(tag="primary_window"):

        # App title
        dpg.add_text("StemForge", color=(175, 175, 255, 255))
        dpg.add_text(
            "Stem separation  |  MIDI extraction  |  Music generation",
            color=(100, 100, 120, 255),
        )
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # Loader bar (Browse + path display + Clear + waveform preview)
        _loader.build_ui()

        dpg.add_spacer(height=8)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # Tab bar
        with dpg.tab_bar():

            with dpg.tab(label="  Separate  "):
                _demucs.build_ui()

            with dpg.tab(label="  MIDI  "):
                _midi.build_ui()

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

    # Manual render loop so tick_all() can animate waveform cursors each frame
    while dpg.is_dearpygui_running():
        tick_all()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    main()
