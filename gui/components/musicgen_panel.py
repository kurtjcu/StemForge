"""
MusicGen audio-generation UI panel for StemForge.

Exposes controls for entering a text prompt, selecting a MusicGen model
variant and duration, optionally conditioning on a melody stem, and
triggering generation.  Generated audio can be previewed or forwarded
to the Export panel.
"""

import os
import pathlib
import logging
import threading
from typing import Callable

from pipelines.musicgen_pipeline import MusicGenPipeline, MusicGenConfig, MusicGenResult


MUSICGEN_MODELS: tuple[str, ...] = (
    "facebook/musicgen-small",
    "facebook/musicgen-medium",
    "facebook/musicgen-large",
    "facebook/musicgen-melody",
)


class MusicGenPanel:
    """UI panel for configuring and running the MusicGen generation pipeline.

    Provides a text prompt field, model selector, duration spinbox,
    optional melody-conditioning toggle, a run button, and a waveform
    preview of the generated audio.
    """

    _pipeline: MusicGenPipeline | None
    _thread: threading.Thread | None
    _melody_path: pathlib.Path | None
    _result_listeners: list[Callable[[pathlib.Path], None]]

    def __init__(self) -> None:
        pass

    def set_melody_path(self, path: pathlib.Path) -> None:
        """Pre-fill the melody conditioning input with a separated stem path."""
        pass

    def get_prompt(self) -> str:
        """Return the text prompt entered by the user."""
        pass

    def get_selected_model(self) -> str:
        """Return the currently selected MusicGen model identifier."""
        pass

    def get_duration_seconds(self) -> float:
        """Return the requested generation duration in seconds."""
        pass

    def get_melody_conditioning(self) -> pathlib.Path | None:
        """Return the melody conditioning file path, or *None* if disabled."""
        pass

    def run(self) -> None:
        """Validate inputs then launch the MusicGen pipeline in a background thread."""
        pass

    def cancel(self) -> None:
        """Request cancellation of any running generation job."""
        pass

    def _on_progress(self, percent: float) -> None:
        """Update the progress indicator at *percent* completion."""
        pass

    def _on_complete(self, audio_path: pathlib.Path) -> None:
        """Handle pipeline completion, refresh the preview, and notify listeners."""
        pass

    def _on_error(self, exc: Exception) -> None:
        """Display an error message when the pipeline raises an exception."""
        pass

    def _refresh_waveform(self, audio_path: pathlib.Path) -> None:
        """Render a waveform thumbnail for the generated audio file."""
        pass

    def add_result_listener(self, callback: Callable[[pathlib.Path], None]) -> None:
        """Register *callback* to receive the generated audio path on success."""
        pass
