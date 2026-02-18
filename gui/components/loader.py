"""
File loader UI component for StemForge.

Provides a panel that lets the user browse the filesystem, select an
audio file, and exposes the chosen path to the rest of the application.
Supported formats are validated at the UI layer before any pipeline is
invoked.
"""

import os
import pathlib
import logging

from utils.audio_io import read_audio


SUPPORTED_EXTENSIONS: tuple[str, ...] = (".wav", ".flac", ".mp3", ".ogg", ".aiff")


class LoaderPanel:
    """UI panel for selecting and loading an input audio file.

    Emits a notification to registered listeners whenever a new valid
    file path is chosen.
    """

    def __init__(self) -> None:
        pass

    def browse(self) -> None:
        """Open a file-chooser dialog and store the validated path."""
        pass

    def get_path(self) -> pathlib.Path | None:
        """Return the currently selected file path, or *None* if unset."""
        pass

    def reset(self) -> None:
        """Clear the current file selection and reset the display."""
        pass

    def _validate_extension(self, path: pathlib.Path) -> bool:
        """Return *True* when *path* has a supported audio extension."""
        pass

    def _notify_listeners(self, path: pathlib.Path) -> None:
        """Invoke all registered path-change callbacks."""
        pass

    def add_listener(self, callback: object) -> None:
        """Register *callback* to be called when the selected path changes."""
        pass
