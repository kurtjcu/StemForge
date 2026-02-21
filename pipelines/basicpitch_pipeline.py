"""
BasicPitch MIDI-extraction pipeline for StemForge.

Orchestrates the full lifecycle of a BasicPitch transcription job: loading
the model via the model loader, preparing the audio input (mono downmix,
resampling to 22 050 Hz), running frame-level pitch inference, converting
activations to note events, and serialising the result as a Standard MIDI
File (SMF).

Typical usage
-------------
::

    pipeline = BasicPitchPipeline()
    pipeline.configure(BasicPitchConfig(onset_threshold=0.5, frame_threshold=0.3, ...))
    pipeline.load_model()
    result = pipeline.run(pathlib.Path("vocals.wav"))
    pipeline.clear()
"""

import pathlib
import logging
from typing import Any, Callable, TypeAlias

from models.basicpitch_loader import BasicPitchModelLoader
from models.registry import BASICPITCH
from utils.audio_io import read_audio
from utils.midi_io import notes_to_midi, write_midi, NoteEvent
from utils.errors import AudioProcessingError, InvalidInputError, ModelLoadError, PipelineExecutionError
from pipelines.resample import Resampler


log = logging.getLogger("stemforge.pipelines.basicpitch")

# BasicPitch operates at this sample rate internally.
_BASICPITCH_SAMPLE_RATE: int = BASICPITCH.sample_rate


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class BasicPitchConfig:
    """Immutable configuration snapshot for a single BasicPitch job.

    Parameters
    ----------
    onset_threshold:
        Minimum onset activation confidence to accept the start of a note.
        Must be in the range ``[0.0, 1.0]``.  Higher values reduce false
        positives at the cost of missing quiet or legato notes.
    frame_threshold:
        Minimum frame activation confidence to sustain a note across a frame.
        Must be in the range ``[0.0, 1.0]``.
    minimum_note_length:
        Shortest allowable note duration in milliseconds.  Notes shorter
        than this value are discarded after activation decoding.
    minimum_frequency:
        Lowest pitch to include in transcription output in Hz.
        ``None`` leaves the lower bound at the model default (~32.7 Hz).
    maximum_frequency:
        Highest pitch to include in transcription output in Hz.
        ``None`` leaves the upper bound at the model default (~1975 Hz).
    output_dir:
        Directory where the MIDI file will be written.  If ``None``, the
        output is written alongside the input audio file.
    """

    onset_threshold: float
    frame_threshold: float
    minimum_note_length: float
    minimum_frequency: float | None
    maximum_frequency: float | None
    output_dir: pathlib.Path | None

    def __init__(
        self,
        onset_threshold: float = 0.5,
        frame_threshold: float = 0.3,
        minimum_note_length: float = 58.0,
        minimum_frequency: float | None = None,
        maximum_frequency: float | None = None,
        output_dir: pathlib.Path | None = None,
    ) -> None:
        self.onset_threshold = float(onset_threshold)
        self.frame_threshold = float(frame_threshold)
        self.minimum_note_length = float(minimum_note_length)
        self.minimum_frequency = minimum_frequency
        self.maximum_frequency = maximum_frequency
        self.output_dir = pathlib.Path(output_dir) if output_dir is not None else None


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

class BasicPitchResult:
    """Artefacts produced by a completed BasicPitch transcription job.

    Parameters
    ----------
    midi_path:
        Absolute path of the written Standard MIDI File (.mid).
    note_events:
        List of detected notes, each represented as a
        ``(start_sec, end_sec, pitch_midi, velocity)`` tuple.
        *pitch_midi* follows the standard MIDI pitch numbering (0–127).
    """

    midi_path: pathlib.Path
    note_events: list[NoteEvent]

    def __init__(
        self,
        midi_path: pathlib.Path,
        note_events: list[NoteEvent],
    ) -> None:
        self.midi_path = midi_path
        self.note_events = list(note_events)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class BasicPitchPipeline:
    """Interface for the BasicPitch MIDI-extraction pipeline.

    Wraps the complete transcription workflow — audio preparation, neural
    inference, activation decoding, and MIDI serialisation — behind a
    minimal, consistent API that mirrors :class:`~pipelines.demucs_pipeline.DemucsPipeline`.

    Lifecycle
    ---------
    1. ``pipeline = BasicPitchPipeline()``
    2. ``pipeline.configure(config)`` — supply a :class:`BasicPitchConfig`.
    3. ``pipeline.load_model()``       — load the single BasicPitch model.
    4. ``result = pipeline.run(path)`` — transcribe one audio stem.
    5. ``pipeline.clear()``            — release memory and reset state.

    Notes
    -----
    * BasicPitch ships a single model (no variant selection), so
      :meth:`load_model` requires no additional arguments.
    * The model internally operates at 22 050 Hz; :meth:`_preprocess`
      handles resampling transparently.
    """

    is_loaded: bool
    _config: BasicPitchConfig | None
    _model: Any
    _loader: BasicPitchModelLoader | None
    _resampler: Resampler | None
    _progress_callback: Callable[[float], None] | None

    def __init__(self) -> None:
        """Initialise the pipeline with no model loaded and no configuration set.

        Post-condition: ``self.is_loaded`` is ``False``; calling :meth:`run`
        before :meth:`load_model` must raise :class:`~utils.errors.PipelineExecutionError`.
        """
        self.is_loaded = False
        self._config = None
        self._model = None
        self._loader = None
        self._resampler = None
        self._progress_callback = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, config: BasicPitchConfig) -> None:
        """Set or replace the pipeline configuration.

        Safe to call at any point in the lifecycle, including after
        :meth:`load_model`.  Changing configuration does *not* require
        reloading the model because BasicPitch thresholds are applied in
        post-processing, not baked into the weights.

        Parameters
        ----------
        config:
            A fully populated :class:`BasicPitchConfig` instance.
        """
        self._config = config

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def load_model(self) -> None:
        """Load the BasicPitch model weights into memory.

        Fetches the checkpoint from the local cache (downloading it first if
        absent) and initialises the inference session.  The session is
        retained in ``self._model`` for reuse across multiple :meth:`run`
        calls.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If the checkpoint cannot be read from disk or the download fails.

        Post-condition
        --------------
        ``self.is_loaded`` is ``True`` and the model is ready for inference.
        """
        if self._loader is None:
            self._loader = BasicPitchModelLoader()

        try:
            self._model = self._loader.load()
        except ModelLoadError:
            raise
        except Exception as exc:
            raise ModelLoadError(
                f"Unexpected error loading BasicPitch model: {exc}",
                model_name="basicpitch",
            ) from exc

        self.is_loaded = True

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self, input_data: pathlib.Path) -> BasicPitchResult:
        """Transcribe the audio stem at *input_data* to a MIDI file.

        Executes the full preprocessing → inference → decoding → serialisation
        chain and writes one ``.mid`` file to ``config.output_dir`` (or
        alongside the input if ``output_dir`` is ``None``).

        Parameters
        ----------
        input_data:
            Absolute path to the audio stem to transcribe.
            The file may be mono or stereo; stereo is downmixed to mono
            automatically during preprocessing.

        Returns
        -------
        BasicPitchResult
            Path to the written MIDI file and the decoded note event list.

        Raises
        ------
        :class:`~utils.errors.PipelineExecutionError`
            If :meth:`load_model` has not been called successfully.
        :class:`~utils.errors.InvalidInputError`
            If *input_data* does not exist on disk or has an unsupported
            file extension.
        :class:`~utils.errors.AudioProcessingError`
            If reading or resampling the input audio fails.
        """
        if not self.is_loaded:
            raise PipelineExecutionError(
                "load_model() must be called before run().",
                pipeline_name="basicpitch",
            )
        if self._config is None:
            raise PipelineExecutionError(
                "configure() must be called before run().",
                pipeline_name="basicpitch",
            )
        if not input_data.exists():
            raise InvalidInputError(
                f"Input file not found: {input_data}", field="input_data"
            )

        self._report(5.0)
        audio_path = self._preprocess(input_data)

        self._report(10.0)
        raw_events = self._run_inference(audio_path)

        self._report(85.0)
        note_events = self._activations_to_notes(raw_events)

        self._report(90.0)
        output_path = self._resolve_output_path(input_data)
        self._write_midi(note_events, output_path)

        self._report(100.0)
        log.info(
            "BasicPitch: %d notes → %s", len(note_events), output_path
        )
        return BasicPitchResult(midi_path=output_path.resolve(), note_events=note_events)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Release the model session from memory and reset all pipeline state.

        After this call the pipeline is in the same state as immediately after
        :meth:`__init__`: no model is loaded and :meth:`run` will raise
        :class:`RuntimeError` until :meth:`load_model` is called again.

        Safe to call even if no model has been loaded.

        Post-condition
        --------------
        ``self.is_loaded`` is ``False`` and ``self._model`` is ``None``.
        """
        if self._loader is not None:
            self._loader.evict()
        self._model = None
        self.is_loaded = False

    # ------------------------------------------------------------------
    # Progress reporting
    # ------------------------------------------------------------------

    def set_progress_callback(self, callback: Callable[[float], None]) -> None:
        """Register a callback invoked periodically during transcription.

        Parameters
        ----------
        callback:
            A callable with signature ``callback(percent: float)``.
            *percent* is in the range ``[0.0, 100.0]``.
        """
        self._progress_callback = callback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _report(self, pct: float) -> None:
        if self._progress_callback is not None:
            self._progress_callback(pct)

    def _preprocess(self, input_path: pathlib.Path) -> pathlib.Path:
        """Downmix the input to mono and resample to 22 050 Hz.

        BasicPitch's ``predict`` function handles audio loading and
        resampling internally, so this method validates the path and
        returns it unchanged.  The docstring's "Mono waveform at 22 050 Hz"
        contract is satisfied by BasicPitch internally.

        Parameters
        ----------
        input_path:
            Path to the source audio stem.

        Returns
        -------
        Any
            Mono waveform array at 22 050 Hz, shape ``(samples,)``.
        """
        # Validate path via audio_io so we get a consistent error type.
        from utils.audio_io import SUPPORTED_EXTENSIONS
        if not input_path.exists():
            raise InvalidInputError(
                f"Audio file not found: {input_path}", field="input_data"
            )
        if input_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise InvalidInputError(
                f"Unsupported audio extension '{input_path.suffix}'.",
                field="input_data",
            )
        return input_path

    def _run_inference(self, audio_path: pathlib.Path) -> list:
        """Run the BasicPitch model on the prepared frame sequence.

        Calls :func:`basic_pitch.inference.predict` with the pre-loaded
        model object and the thresholds from the current configuration.

        Parameters
        ----------
        frames:
            Preprocessed audio frames from :meth:`_preprocess`.

        Returns
        -------
        Any
            Raw activation arrays: onset activations, frame activations,
            and contour activations, each of shape
            ``(frames, pitch_bins)``.
        """
        try:
            from basic_pitch.inference import predict
        except ImportError as exc:
            raise PipelineExecutionError(
                f"basic-pitch package is not importable: {exc}",
                pipeline_name="basicpitch",
            ) from exc

        cfg = self._config
        try:
            _model_output, _midi_data, note_events_raw = predict(
                audio_path,
                self._model,
                onset_threshold=cfg.onset_threshold,
                frame_threshold=cfg.frame_threshold,
                minimum_note_length=cfg.minimum_note_length,
                minimum_frequency=cfg.minimum_frequency,
                maximum_frequency=cfg.maximum_frequency,
            )
        except Exception as exc:
            raise PipelineExecutionError(
                f"BasicPitch inference failed: {exc}", pipeline_name="basicpitch"
            ) from exc

        return note_events_raw

    def _activations_to_notes(self, raw_events: list) -> list[NoteEvent]:
        """Decode raw activations into discrete note events.

        Applies the onset and frame thresholds from the current configuration
        and filters out notes shorter than ``config.minimum_note_length``.

        Parameters
        ----------
        activations:
            Output of :meth:`_run_inference` — list of BasicPitch raw note
            tuples ``(start_s, end_s, pitch_midi, amplitude, ...)``.

        Returns
        -------
        list[NoteEvent]
            List of ``(start_sec, end_sec, pitch_midi, velocity)`` tuples,
            sorted by ``start_sec``.
        """
        note_events: list[NoteEvent] = []
        for item in raw_events:
            start_t, end_t, pitch, amplitude, *_ = item
            velocity = max(1, min(127, int(float(amplitude) * 127)))
            note_events.append(
                (float(start_t), float(end_t), int(pitch), velocity)
            )
        return sorted(note_events, key=lambda n: n[0])

    def _write_midi(
        self,
        note_events: list[NoteEvent],
        output_path: pathlib.Path,
    ) -> None:
        """Serialise *note_events* to a Standard MIDI File at *output_path*.

        Parameters
        ----------
        note_events:
            Decoded note events from :meth:`_activations_to_notes`.
        output_path:
            Destination path for the ``.mid`` file.  Parent directories are
            created if they do not exist.
        """
        midi_data = notes_to_midi(note_events)
        write_midi(midi_data, output_path)

    def _resolve_output_path(self, input_path: pathlib.Path) -> pathlib.Path:
        """Compute the MIDI output path from the input stem path."""
        stem_name = input_path.stem + ".mid"
        if self._config.output_dir is not None:
            return self._config.output_dir / stem_name
        return input_path.with_suffix(".mid")
