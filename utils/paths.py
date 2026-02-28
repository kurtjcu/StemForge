"""Output-directory constants shared across all layers.

Extracted from ``gui/constants.py`` so that pipelines and backend code can
import these paths without depending on the GUI package.
"""

import pathlib

from utils.platform import get_data_dir

OUTPUT_BASE  = get_data_dir() / "output"
STEMS_DIR    = OUTPUT_BASE / "stems"
MIDI_DIR     = pathlib.Path.home() / "Music" / "StemForge"
MUSICGEN_DIR = OUTPUT_BASE / "musicgen"
MIX_DIR      = OUTPUT_BASE / "mix"
EXPORT_DIR   = OUTPUT_BASE / "exports"
