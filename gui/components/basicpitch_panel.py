"""
BasicPitch MIDI-extraction UI panel for StemForge.

Allows the user to select which separated stem to transcribe, adjust
onset/frame confidence thresholds, trigger MIDI extraction, and preview
or export the resulting MIDI file.
"""

import os
import pathlib
import logging
import threading

from pipelines.basicpitch_pipeline import BasicPitchPipeline, BasicPitchConfig, BasicPitchResult


class BasicPitchPanel:
    """UI panel for configuring and running the BasicPitch MIDI extraction pipeline.

    Provides stem selector, confidence threshold sliders, a run button,
    a basic piano-roll preview area, and MIDI export controls.
    """

    def __init__(self) -> None:
        pass

    def set_stem_paths(self, stem_paths: dict[str, pathlib.Path]) -> None:
        """Populate the stem selector drop-down with available separated stems."""
        pass

    def get_selected_stem(self) -> pathlib.Path | None:
        """Return the path of the stem currently chosen for transcription."""
        pass

    def get_onset_threshold(self) -> float:
        """Return the onset detection confidence threshold (0.0 – 1.0)."""
        pass

    def get_frame_threshold(self) -> float:
        """Return the frame activation confidence threshold (0.0 – 1.0)."""
        pass

    def run(self) -> None:
        """Validate inputs then launch the BasicPitch pipeline in a background thread."""
        pass

    def cancel(self) -> None:
        """Request cancellation of any running transcription job."""
        pass

    def _on_progress(self, percent: float) -> None:
        """Update the progress indicator at *percent* completion."""
        pass

    def _on_complete(self, midi_path: pathlib.Path) -> None:
        """Handle pipeline completion, refresh the preview, and notify listeners."""
        pass

    def _on_error(self, exc: Exception) -> None:
        """Display an error message when the pipeline raises an exception."""
        pass

    def _refresh_piano_roll(self, midi_path: pathlib.Path) -> None:
        """Render a lightweight piano-roll preview from the MIDI file."""
        pass

    def add_result_listener(self, callback: object) -> None:
        """Register *callback* to receive the MIDI path on success."""
        pass
