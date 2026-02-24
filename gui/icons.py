"""Feather Icon PNG texture loader for StemForge.

Call load_icons() once inside a ``with dpg.texture_registry():`` block
before building any UI that uses icons.  get_icon_tag() then returns the
DPG texture tag for a given logical kind, or None when the icon is
unavailable so callers can degrade gracefully.

Icon tags
---------
  icon_folder  — folder.png      (directories)
  icon_audio   — music.png       (wav, flac, mp3, ogg, …)
  icon_midi    — sliders.png     (mid, midi)
  icon_text    — file-text.png   (txt, json, md, toml, …)
  icon_file    — file.png        (everything else)

Logo textures (project-level assets/icons/)
---------
  logo_64   — 64×64  app logo (used in the app header)
  logo_256  — 256×256 app logo (used in the About box)
"""

import pathlib
import logging

import dearpygui.dearpygui as dpg


log = logging.getLogger("stemforge.gui.icons")

_ASSETS_DIR = pathlib.Path(__file__).parent / "assets" / "icons"

# Project-level assets (logo icons generated from StemForgeLogo.png)
_PROJECT_ASSETS_DIR = pathlib.Path(__file__).parent.parent / "assets" / "icons"

# Logo texture tags
LOGO_TAG      = "logo_64"
LOGO_SIZE     = 64
LOGO_TAG_256  = "logo_256"
LOGO_SIZE_256 = 256
_LOGO_FILE     = _PROJECT_ASSETS_DIR / "logo_64.png"
_LOGO_FILE_256 = _PROJECT_ASSETS_DIR / "logo_256.png"

# (logical kind, png filename, dpg tag)
_ICON_DEFS: tuple[tuple[str, str, str], ...] = (
    ("folder", "folder.png",    "icon_folder"),
    ("audio",  "music.png",     "icon_audio"),
    ("midi",   "sliders.png",   "icon_midi"),
    ("text",   "file-text.png", "icon_text"),
    ("file",   "file.png",      "icon_file"),
)

# Size at which all icon PNGs were exported — use same size in add_image()
ICON_SIZE: int = 18

_loaded_tags: set[str] = set()

_KIND_TO_TAG: dict[str, str] = {kind: tag for kind, _, tag in _ICON_DEFS}


def load_icons() -> None:
    """Register icon PNGs into the currently-active texture_registry context.

    Missing files are skipped with a warning; no exception is raised so the
    app starts even when the assets directory is absent.
    """
    for _kind, filename, tag in _ICON_DEFS:
        path = _ASSETS_DIR / filename
        if not path.exists():
            log.warning("Icon not found, skipping: %s", path)
            continue
        try:
            w, h, _channels, data = dpg.load_image(str(path))
            dpg.add_static_texture(w, h, data, tag=tag)
            _loaded_tags.add(tag)
            log.debug("Icon loaded: %s  tag=%s  %dx%d", filename, tag, w, h)
        except Exception as exc:
            log.error("Failed to load icon %s: %s", filename, exc)

    # Logo textures (64px header, 256px about box)
    for logo_file, logo_tag in ((_LOGO_FILE, LOGO_TAG), (_LOGO_FILE_256, LOGO_TAG_256)):
        if logo_file.exists():
            try:
                w, h, _channels, data = dpg.load_image(str(logo_file))
                dpg.add_static_texture(w, h, data, tag=logo_tag)
                log.debug("Logo loaded: %s  tag=%s  %dx%d", logo_file.name, logo_tag, w, h)
            except Exception as exc:
                log.error("Failed to load logo %s: %s", logo_file, exc)
        else:
            log.warning("Logo not found: %s", logo_file)


def get_icon_tag(kind: str) -> str | None:
    """Return the DPG texture tag for *kind*, or None if not available.

    Parameters
    ----------
    kind:
        One of ``'folder'``, ``'audio'``, ``'midi'``, ``'text'``, ``'file'``.
    """
    tag = _KIND_TO_TAG.get(kind)
    return tag if (tag is not None and tag in _loaded_tags) else None
