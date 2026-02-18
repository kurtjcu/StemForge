"""
Demucs source-separation UI panel for StemForge.

Exposes controls for selecting the Demucs model variant, configuring
stem targets (vocals, drums, bass, other), triggering separation, and
displaying per-stem progress feedback to the user.
"""

import os
import pathlib
import logging
import threading

from pipelines.demucs_pipeline import DemucsPipeline, DemucsConfig, DemucsResult


DEMUCS_MODELS: tuple[str, ...] = ("htdemucs", "htdemucs_ft", "mdx_extra", "mdx_extra_q")

STEM_TARGETS: tuple[str, ...] = ("vocals", "drums", "bass", "other")


class DemucsPanel:
    """UI panel for configuring and running the Demucs separation pipeline.

    Provides model selection, stem checkboxes, a run button, and a
    progress indicator.  Results (file paths of separated stems) are
    passed to registered result listeners.
    """

    def __init__(self) -> None:
        pass

    def set_input_path(self, path: pathlib.Path) -> None:
        """Update the source audio path shown in the panel."""
        pass

    def get_selected_model(self) -> str:
        """Return the currently selected Demucs model identifier."""
        pass

    def get_selected_stems(self) -> list[str]:
        """Return the list of stem names the user has enabled."""
        pass

    def run(self) -> None:
        """Validate inputs then launch the Demucs pipeline in a background thread."""
        pass

    def cancel(self) -> None:
        """Request cancellation of any running separation job."""
        pass

    def _on_progress(self, percent: float, stem: str) -> None:
        """Update the progress bar for the given *stem* at *percent* completion."""
        pass

    def _on_complete(self, stem_paths: dict[str, pathlib.Path]) -> None:
        """Handle pipeline completion and pass results to listeners."""
        pass

    def _on_error(self, exc: Exception) -> None:
        """Display an error message when the pipeline raises an exception."""
        pass

    def add_result_listener(self, callback: object) -> None:
        """Register *callback* to receive the dict of stem paths on success."""
        pass
