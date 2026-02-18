"""
Export UI panel for StemForge.

Aggregates all pipeline outputs (separated stems, MIDI files, generated
audio) and lets the user choose an output directory, rename individual
files, select export formats, and trigger the final write operation.
"""

import os
import pathlib
import logging


EXPORT_FORMATS: tuple[str, ...] = ("wav", "flac", "mp3", "ogg")


class ExportPanel:
    """UI panel for reviewing and exporting all pipeline outputs.

    Displays a checklist of available artefacts (stems, MIDI, generated
    audio), format and bit-depth selectors, a destination folder picker,
    and an Export button.
    """

    def __init__(self) -> None:
        pass

    def add_artefact(self, label: str, path: pathlib.Path) -> None:
        """Register a new artefact to be shown in the export checklist."""
        pass

    def remove_artefact(self, label: str) -> None:
        """Remove an artefact entry from the checklist by label."""
        pass

    def get_selected_artefacts(self) -> list[pathlib.Path]:
        """Return the paths of artefacts the user has checked for export."""
        pass

    def get_output_directory(self) -> pathlib.Path | None:
        """Return the chosen output directory, or *None* if not yet set."""
        pass

    def get_export_format(self) -> str:
        """Return the selected export format string (e.g. ``'wav'``)."""
        pass

    def browse_output_directory(self) -> None:
        """Open a directory-chooser dialog and update the destination field."""
        pass

    def export(self) -> None:
        """Validate selections and write all checked artefacts to disk."""
        pass

    def reset(self) -> None:
        """Clear all artefacts and reset controls to their default state."""
        pass

    def _on_export_complete(self, output_paths: list[pathlib.Path]) -> None:
        """Notify the user that all files have been written successfully."""
        pass

    def _on_error(self, exc: Exception) -> None:
        """Display an error message when an export operation fails."""
        pass
