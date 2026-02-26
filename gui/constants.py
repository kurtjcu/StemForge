"""
Output-directory constants shared across all GUI components.

Defining them here (instead of in gui/app.py) prevents circular imports:
panels need the paths at module load time, but app.py imports the panels.
"""

import pathlib

from utils.platform import get_data_dir

_OUTPUT_BASE  = get_data_dir() / "output"
_STEMS_DIR    = _OUTPUT_BASE / "stems"
_MIDI_DIR     = pathlib.Path.home() / "Music" / "StemForge"
_MUSICGEN_DIR = _OUTPUT_BASE / "musicgen"
_MIX_DIR      = _OUTPUT_BASE / "mix"
_EXPORT_DIR   = _OUTPUT_BASE / "exports"
