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
import os
import pathlib
import subprocess

import dearpygui.dearpygui as dpg
from dotenv import load_dotenv

from utils.wsl import configure_audio as _configure_wsl_audio
from gui.constants import _STEMS_DIR, _MIDI_DIR, _MUSICGEN_DIR, _MIX_DIR, _EXPORT_DIR
from gui.components.loader import LoaderPanel
from gui.components.demucs_panel import DemucsPanel
from gui.components.midi_panel import MidiPanel
from gui.components.mix_panel import MixPanel
from gui.components.musicgen_panel import MusicGenPanel
from gui.components.export_panel import ExportPanel
from gui.components.waveform_widget import tick_all
from gui.components.midi_player_widget import tick_all_midi
from gui.icons import load_icons, LOGO_TAG, LOGO_SIZE, LOGO_TAG_256, LOGO_SIZE_256


log = logging.getLogger("stemforge.gui.app")

_VP_WIDTH  = 1280
_VP_HEIGHT = 820


# ---------------------------------------------------------------------------
# Font setup
# ---------------------------------------------------------------------------

_BUNDLED_FONT = pathlib.Path(__file__).parent.parent / "assets" / "fonts" / "DejaVuSans.ttf"

_FONT_CANDIDATES = [
    str(_BUNDLED_FONT),                                          # bundled — always checked first
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",        # Fedora
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",          # Debian/Ubuntu
    "/usr/share/fonts/TTF/DejaVuSans.ttf",                      # Arch
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",                   # other
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
# About box
# ---------------------------------------------------------------------------

_ABOUT_TAG    = "about_window"
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent
_LICENSE_PATH = _PROJECT_ROOT / "LICENSE"
_LICENSE_COMM = _PROJECT_ROOT / "LICENSE-COMMERCIAL"

_APP_VERSION   = "1.0.0"
_COPYRIGHT     = "Copyright © 2026 Todd Green"
_LICENSE_SHORT = "PolyForm Noncommercial License 1.0.0"
_CONTACT       = "tsondo@gmail.com"


def _open_file(path: pathlib.Path) -> None:
    """Open *path* in the system default viewer (xdg-open on Linux)."""
    try:
        subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        log.warning("Could not open %s: %s", path, exc)


def _build_about_window() -> None:
    """Create the hidden About modal.  Call once before the render loop."""
    with dpg.window(
        tag=_ABOUT_TAG,
        label="About StemForge",
        modal=True,
        show=False,
        no_resize=True,
        width=420,
        min_size=(420, 200),
    ):
        # Logo centred
        if dpg.does_item_exist(LOGO_TAG_256):
            pad = max(0, (420 - LOGO_SIZE_256) // 2 - 16)   # approx centre
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=pad)
                dpg.add_image(LOGO_TAG_256, width=LOGO_SIZE_256, height=LOGO_SIZE_256)

        dpg.add_spacer(height=8)

        # App name + version
        dpg.add_text("StemForge", color=(200, 175, 100, 255))
        dpg.add_text(f"Version {_APP_VERSION}", color=(160, 160, 180, 255))
        dpg.add_text(
            "AI-powered audio processing — source separation,\n"
            "MIDI extraction, mixing, and generative audio.",
            color=(140, 140, 160, 255),
            wrap=390,
        )

        dpg.add_spacer(height=10)
        dpg.add_separator()
        dpg.add_spacer(height=6)

        # Copyright
        dpg.add_text(_COPYRIGHT, color=(200, 200, 210, 255))

        dpg.add_spacer(height=8)

        # License — free tier
        dpg.add_text("License", color=(175, 175, 255, 255))
        dpg.add_text(
            f"Free for personal and non-commercial use under\nthe {_LICENSE_SHORT}.",
            color=(140, 140, 160, 255),
            wrap=390,
        )
        dpg.add_spacer(height=4)
        dpg.add_button(
            label="Open LICENSE",
            callback=lambda: _open_file(_LICENSE_PATH),
            width=160,
        )

        dpg.add_spacer(height=8)

        # Commercial license
        dpg.add_text("Commercial licensing", color=(175, 175, 255, 255))
        dpg.add_text(
            f"A separate commercial licence is available.\nContact: {_CONTACT}",
            color=(140, 140, 160, 255),
            wrap=390,
        )
        dpg.add_spacer(height=4)
        dpg.add_button(
            label="Open LICENSE-COMMERCIAL",
            callback=lambda: _open_file(_LICENSE_COMM),
            width=220,
        )

        dpg.add_spacer(height=12)
        dpg.add_separator()
        dpg.add_spacer(height=8)

        # Close button
        dpg.add_button(
            label="  Close  ",
            callback=lambda: dpg.configure_item(_ABOUT_TAG, show=False),
            width=-1,
        )


def _show_about() -> None:
    dpg.configure_item(_ABOUT_TAG, show=True)


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

def _load_env_and_login() -> None:
    """Load .env from the project root and set API credentials as env vars.

    Does NOT make any network calls — token validation happens lazily when
    a model is first downloaded.  This keeps startup fast and ensures the
    GUI is fully initialised before any auth error can surface.
    """
    _env_file = pathlib.Path(__file__).parent.parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
        logging.getLogger("stemforge.gui.app").info("Loaded environment from %s", _env_file)

    # Log token presence so the user can confirm their .env was picked up,
    # but never log the token value itself.
    if os.environ.get("HF_TOKEN", "").strip():
        logging.getLogger("stemforge.gui.app").info(
            "HF_TOKEN present — will be used for gated model downloads"
        )
    else:
        logging.getLogger("stemforge.gui.app").debug(
            "HF_TOKEN not set; falling back to ~/.cache/huggingface/token if present"
        )


def main() -> None:
    """Create the DearPyGUI viewport and run the manual render loop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    )

    # ---- Environment / auth (before any model or network access) --------
    _load_env_and_login()

    # ---- WSL audio routing (must run before any sounddevice call) ------
    wsl_msg = _configure_wsl_audio()
    if wsl_msg:
        log.info(wsl_msg)

    # ---- DearPyGUI setup -----------------------------------------------
    dpg.create_context()

    _setup_fonts()

    with dpg.texture_registry(tag="stemforge_textures"):
        load_icons()

    theme = _build_theme()
    dpg.bind_theme(theme)

    # Pre-create output directories so pipelines never have to.
    for d in (_STEMS_DIR, _MIDI_DIR, _MUSICGEN_DIR, _MIX_DIR, _EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)

    # ---- Panel singletons ----------------------------------------------
    _loader   = LoaderPanel()
    _demucs   = DemucsPanel()
    _midi     = MidiPanel()
    _mix      = MixPanel()
    _musicgen = MusicGenPanel()
    _export   = ExportPanel()

    # ---- Inter-panel wiring --------------------------------------------
    _demucs.add_result_listener(_midi.notify_stems_ready)
    _demucs.add_result_listener(_mix.notify_stems_ready)
    _demucs.add_result_listener(_musicgen.notify_stems_ready)
    _demucs.add_result_listener(_export.notify_stems_ready)
    _midi.add_result_listener(_mix.notify_midi_ready)
    _midi.add_result_listener(_musicgen.notify_midi_ready)
    _midi.add_result_listener(_export.notify_midi_ready)
    _musicgen.add_result_listener(_mix.notify_musicgen_ready)
    _musicgen.add_result_listener(_export.notify_musicgen_ready)
    _mix.add_result_listener(_export.notify_mix_ready)
    _mix.add_result_listener(_musicgen.notify_mix_ready)
    # Auto-trigger Roformer analysis when a new file is loaded
    _loader.add_on_load_callback(_demucs.on_file_loaded)

    # ---- Top-level dialogs / browser (must live outside all windows) ---
    _build_about_window()
    _loader.build_file_browser()
    _demucs.build_save_dialog()
    _midi.build_browsers()
    _mix.build_save_dialog()
    _musicgen.build_save_dialog()
    _export.build_dir_dialog()

    # ---- Primary window ------------------------------------------------
    with dpg.window(tag="primary_window"):

        # App header — logo (clickable → About) + tagline
        with dpg.group(horizontal=True):
            if dpg.does_item_exist(LOGO_TAG):
                dpg.add_image_button(
                    LOGO_TAG,
                    width=LOGO_SIZE,
                    height=LOGO_SIZE,
                    callback=_show_about,
                    tag="header_logo_btn",
                    frame_padding=0,
                )
                with dpg.tooltip("header_logo_btn"):
                    dpg.add_text("About StemForge")
            with dpg.group():
                dpg.add_spacer(height=16)
                dpg.add_text(
                    "Stem | Midi | Mix | AI",
                    color=(180, 150, 90, 255),
                )
        dpg.add_separator()
        dpg.add_spacer(height=4)

        # Tab bar
        with dpg.tab_bar():

            with dpg.tab(label="  Separate  "):
                # File loader lives only in this tab — other tabs have their own loaders
                _loader.build_ui()
                dpg.add_spacer(height=8)
                dpg.add_separator()
                dpg.add_spacer(height=6)
                _demucs.build_ui()

            with dpg.tab(label="  MIDI  "):
                _midi.build_ui()

            with dpg.tab(label="  Mix  "):
                _mix.build_ui()

            with dpg.tab(label="  Generate  "):
                _musicgen.build_ui()

            with dpg.tab(label="  Export  "):
                _export.build_ui()

    # ---- Viewport & render loop ----------------------------------------
    _icon_dir = pathlib.Path(__file__).parent.parent / "assets" / "icons"
    _icon_32  = str(_icon_dir / "logo_32.png")
    _icon_256 = str(_icon_dir / "logo_256.png")

    dpg.create_viewport(
        title="StemForge",
        width=_VP_WIDTH,
        height=_VP_HEIGHT,
        min_width=900,
        min_height=600,
        small_icon=_icon_32  if (_icon_dir / "logo_32.png").exists()  else "",
        large_icon=_icon_256 if (_icon_dir / "logo_256.png").exists() else "",
    )
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("primary_window", True)

    # Manual render loop so tick_all() / tick_all_midi() animate cursors each frame.
    # flush_ui() drains the thread-safe UI queue — background threads must never
    # call dpg.* directly; they use schedule_ui() instead (see gui/ui_queue.py).
    from gui.ui_queue import flush_ui

    while dpg.is_dearpygui_running():
        flush_ui()
        tick_all()
        tick_all_midi()
        dpg.render_dearpygui_frame()

    dpg.destroy_context()


if __name__ == "__main__":
    main()
