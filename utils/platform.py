"""Platform-aware filesystem paths."""
import sys
from pathlib import Path


def get_data_dir() -> Path:
    """Return the platform-appropriate application data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "stemforge"
    # Linux XDG
    return Path.home() / ".local" / "share" / "stemforge"
