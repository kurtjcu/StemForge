"""
StemForge main application entry point — Gradio UI.

Builds a tabbed Gradio interface that wires each tab to a stub function
in the corresponding component module.  No audio processing or model
logic is performed here; every callback delegates immediately to a panel
stub and returns a placeholder status string.

Tabs
----
1. Load Audio   — file upload handled by :mod:`gui.components.loader`
2. Demucs       — separation controls via :mod:`gui.components.demucs_panel`
3. BasicPitch   — MIDI extraction via :mod:`gui.components.basicpitch_panel`
4. MusicGen     — audio generation via :mod:`gui.components.musicgen_panel`
5. Export       — artefact export via :mod:`gui.components.export_panel`
"""

import gradio as gr

from gui.components.loader import LoaderPanel, SUPPORTED_EXTENSIONS
from gui.components.demucs_panel import DemucsPanel, DEMUCS_MODELS, STEM_TARGETS
from gui.components.basicpitch_panel import BasicPitchPanel
from gui.components.musicgen_panel import MusicGenPanel, MUSICGEN_MODELS
from gui.components.export_panel import ExportPanel, EXPORT_FORMATS

# ---------------------------------------------------------------------------
# Panel singletons — one instance per pipeline, shared across callbacks
# ---------------------------------------------------------------------------

_loader = LoaderPanel()
_demucs = DemucsPanel()
_basicpitch = BasicPitchPanel()
_musicgen = MusicGenPanel()
_export = ExportPanel()

# ---------------------------------------------------------------------------
# Tab 1 — Load Audio callbacks
# ---------------------------------------------------------------------------


def on_audio_upload(audio_path: str | None) -> str:
    """Delegate file-upload event to the loader panel stub.

    Parameters
    ----------
    audio_path:
        Temporary file path supplied by Gradio after the user uploads a file,
        or *None* if the component was cleared.

    Returns
    -------
    str
        Placeholder status message shown in the status box.
    """
    _loader.browse()
    if audio_path is None:
        return "No file selected."
    return f"[stub] Loaded: {audio_path}"


def on_clear_audio() -> str:
    """Delegate clear event to the loader panel stub.

    Returns
    -------
    str
        Placeholder status message shown in the status box.
    """
    _loader.reset()
    return "File cleared."


# ---------------------------------------------------------------------------
# Tab 2 — Demucs callbacks
# ---------------------------------------------------------------------------


def on_run_demucs(model: str, stems: list[str]) -> str:
    """Delegate the Run Separation button click to the Demucs panel stub.

    Parameters
    ----------
    model:
        Selected Demucs model identifier.
    stems:
        List of stem names the user has checked.

    Returns
    -------
    str
        Placeholder status message.
    """
    _demucs.run()
    stems_str = ", ".join(stems) if stems else "(none)"
    return f"[stub] Separation queued — model={model!r}, stems=[{stems_str}]"


# ---------------------------------------------------------------------------
# Tab 3 — BasicPitch callbacks
# ---------------------------------------------------------------------------


def on_run_basicpitch(
    stem_choice: str,
    onset_threshold: float,
    frame_threshold: float,
) -> str:
    """Delegate the Extract MIDI button click to the BasicPitch panel stub.

    Parameters
    ----------
    stem_choice:
        Name of the stem chosen for transcription.
    onset_threshold:
        Onset confidence threshold value from the slider (0.0 – 1.0).
    frame_threshold:
        Frame confidence threshold value from the slider (0.0 – 1.0).

    Returns
    -------
    str
        Placeholder status message.
    """
    _basicpitch.run()
    return (
        f"[stub] MIDI extraction queued — stem={stem_choice!r}, "
        f"onset={onset_threshold:.2f}, frame={frame_threshold:.2f}"
    )


# ---------------------------------------------------------------------------
# Tab 4 — MusicGen callbacks
# ---------------------------------------------------------------------------


def on_run_musicgen(
    prompt: str,
    model: str,
    duration: float,
    melody_path: str | None,
) -> str:
    """Delegate the Generate button click to the MusicGen panel stub.

    Parameters
    ----------
    prompt:
        Text description of the music to generate.
    model:
        Selected MusicGen model identifier.
    duration:
        Requested generation length in seconds.
    melody_path:
        Temporary path of an uploaded melody-conditioning file, or *None*.

    Returns
    -------
    str
        Placeholder status message.
    """
    _musicgen.run()
    melody_info = f", melody={melody_path!r}" if melody_path else ""
    return (
        f"[stub] Generation queued — model={model!r}, "
        f"duration={duration}s, prompt={prompt!r}{melody_info}"
    )


# ---------------------------------------------------------------------------
# Tab 5 — Export callbacks
# ---------------------------------------------------------------------------


def on_run_export(fmt: str, output_dir: str) -> str:
    """Delegate the Export button click to the export panel stub.

    Parameters
    ----------
    fmt:
        Selected export format string (e.g. ``'wav'``).
    output_dir:
        User-entered output directory path (may be empty).

    Returns
    -------
    str
        Placeholder status message.
    """
    _export.export()
    dest = output_dir.strip() or "(not set)"
    return f"[stub] Export queued — format={fmt!r}, destination={dest!r}"


# ---------------------------------------------------------------------------
# UI construction
# ---------------------------------------------------------------------------

def build_ui() -> gr.Blocks:
    """Construct and return the top-level Gradio Blocks application.

    Returns
    -------
    gr.Blocks
        Fully wired (but stub-backed) Gradio application object.
    """
    with gr.Blocks(title="StemForge", theme=gr.themes.Soft()) as demo:

        gr.Markdown(
            "# StemForge\n"
            "AI-powered **stem separation** · **MIDI extraction** · **music generation**"
        )

        with gr.Tabs():

            # ----------------------------------------------------------------
            # Tab 1 — Load Audio
            # ----------------------------------------------------------------
            with gr.Tab("Load Audio"):
                gr.Markdown("Upload a supported audio file to make it available to all pipelines.")

                audio_input = gr.Audio(
                    label="Input audio",
                    type="filepath",
                    sources=["upload"],
                )
                load_status = gr.Textbox(
                    label="Status",
                    interactive=False,
                    placeholder="No file loaded.",
                )
                clear_btn = gr.Button("Clear", variant="secondary")

                audio_input.change(
                    fn=on_audio_upload,
                    inputs=audio_input,
                    outputs=load_status,
                )
                clear_btn.click(
                    fn=on_clear_audio,
                    inputs=None,
                    outputs=load_status,
                )

            # ----------------------------------------------------------------
            # Tab 2 — Demucs
            # ----------------------------------------------------------------
            with gr.Tab("Demucs"):
                gr.Markdown("Split the loaded audio into individual stems.")

                with gr.Row():
                    demucs_model = gr.Dropdown(
                        label="Model",
                        choices=list(DEMUCS_MODELS),
                        value=DEMUCS_MODELS[0],
                        scale=1,
                    )
                    demucs_stems = gr.CheckboxGroup(
                        label="Stems to extract",
                        choices=list(STEM_TARGETS),
                        value=list(STEM_TARGETS),
                        scale=2,
                    )

                demucs_run_btn = gr.Button("Run Separation", variant="primary")
                demucs_status = gr.Textbox(
                    label="Status",
                    interactive=False,
                    placeholder="Idle.",
                )

                demucs_run_btn.click(
                    fn=on_run_demucs,
                    inputs=[demucs_model, demucs_stems],
                    outputs=demucs_status,
                )

            # ----------------------------------------------------------------
            # Tab 3 — BasicPitch
            # ----------------------------------------------------------------
            with gr.Tab("BasicPitch"):
                gr.Markdown("Transcribe a separated stem to a MIDI file.")

                bp_stem = gr.Dropdown(
                    label="Stem to transcribe",
                    choices=list(STEM_TARGETS),
                    value=STEM_TARGETS[0],
                )

                with gr.Row():
                    bp_onset = gr.Slider(
                        label="Onset threshold",
                        minimum=0.0,
                        maximum=1.0,
                        step=0.05,
                        value=0.5,
                        info="Minimum onset confidence to accept a note (0 – 1).",
                    )
                    bp_frame = gr.Slider(
                        label="Frame threshold",
                        minimum=0.0,
                        maximum=1.0,
                        step=0.05,
                        value=0.3,
                        info="Minimum frame confidence to sustain a note (0 – 1).",
                    )

                bp_run_btn = gr.Button("Extract MIDI", variant="primary")
                bp_status = gr.Textbox(
                    label="Status",
                    interactive=False,
                    placeholder="Idle.",
                )

                bp_run_btn.click(
                    fn=on_run_basicpitch,
                    inputs=[bp_stem, bp_onset, bp_frame],
                    outputs=bp_status,
                )

            # ----------------------------------------------------------------
            # Tab 4 — MusicGen
            # ----------------------------------------------------------------
            with gr.Tab("MusicGen"):
                gr.Markdown("Generate new audio from a text prompt, with optional melody conditioning.")

                mg_prompt = gr.Textbox(
                    label="Prompt",
                    placeholder="e.g. upbeat jazz with piano and walking bass",
                    lines=3,
                )

                with gr.Row():
                    mg_model = gr.Dropdown(
                        label="Model",
                        choices=list(MUSICGEN_MODELS),
                        value=MUSICGEN_MODELS[0],
                        scale=2,
                    )
                    mg_duration = gr.Slider(
                        label="Duration (seconds)",
                        minimum=1,
                        maximum=30,
                        step=1,
                        value=10,
                        scale=1,
                    )

                mg_melody = gr.Audio(
                    label="Melody conditioning (optional)",
                    type="filepath",
                    sources=["upload"],
                )

                mg_run_btn = gr.Button("Generate", variant="primary")
                mg_status = gr.Textbox(
                    label="Status",
                    interactive=False,
                    placeholder="Idle.",
                )

                mg_run_btn.click(
                    fn=on_run_musicgen,
                    inputs=[mg_prompt, mg_model, mg_duration, mg_melody],
                    outputs=mg_status,
                )

            # ----------------------------------------------------------------
            # Tab 5 — Export
            # ----------------------------------------------------------------
            with gr.Tab("Export"):
                gr.Markdown("Choose a format and destination, then write all pipeline outputs to disk.")

                with gr.Row():
                    exp_format = gr.Dropdown(
                        label="Export format",
                        choices=list(EXPORT_FORMATS),
                        value=EXPORT_FORMATS[0],
                        scale=1,
                    )
                    exp_output_dir = gr.Textbox(
                        label="Output directory",
                        placeholder="/path/to/output",
                        scale=3,
                    )

                exp_run_btn = gr.Button("Export", variant="primary")
                exp_status = gr.Textbox(
                    label="Status",
                    interactive=False,
                    placeholder="Idle.",
                )

                exp_run_btn.click(
                    fn=on_run_export,
                    inputs=[exp_format, exp_output_dir],
                    outputs=exp_status,
                )

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Build the UI and start the Gradio development server.

    Called by the ``stemforge`` console script defined in *pyproject.toml*.
    """
    demo = build_ui()
    demo.launch()


if __name__ == "__main__":
    main()
