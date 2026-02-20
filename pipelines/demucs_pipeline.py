"""
Demucs source-separation pipeline for StemForge.

Orchestrates the full lifecycle of a Demucs separation job: loading the
model via the model loader, preprocessing the input waveform (resampling,
channel normalisation), running inference, and writing the separated stems
to a temporary or user-specified output directory.

Typical usage
-------------
::

    pipeline = DemucsPipeline()
    pipeline.configure(DemucsConfig(model_name="htdemucs", stems=["vocals", "drums"], ...))
    pipeline.load_model()
    result = pipeline.run(pathlib.Path("song.wav"))
    pipeline.clear()
"""

import pathlib
import logging
from typing import Any, Callable

import numpy as np
import torch

from models.demucs_loader import DemucsModelLoader
from utils.audio_io import read_audio, write_audio, convert_channels
from utils.errors import AudioProcessingError, InvalidInputError, ModelLoadError, PipelineExecutionError
from pipelines.resample import Resampler


log = logging.getLogger("stemforge.pipelines.demucs")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class DemucsConfig:
    """Immutable configuration snapshot for a single Demucs job.

    Parameters
    ----------
    model_name:
        Identifier of the Demucs model variant to use
        (e.g. ``'htdemucs'``, ``'htdemucs_ft'``, ``'mdx_extra'``).
    stems:
        Ordered list of stem names to extract.  Must be a subset of
        ``['vocals', 'drums', 'bass', 'other']``.
    output_dir:
        Directory where the separated stem WAV files will be written.
        The directory is created if it does not already exist.
    sample_rate:
        Target sample rate for the output files in Hz.  The model always
        operates at its native rate; this applies to the final write only.
    """

    model_name: str
    stems: list[str]
    output_dir: pathlib.Path
    sample_rate: int

    def __init__(
        self,
        model_name: str,
        stems: list[str],
        output_dir: pathlib.Path,
        sample_rate: int = 44100,
    ) -> None:
        self.model_name = model_name
        self.stems = list(stems)
        self.output_dir = pathlib.Path(output_dir)
        self.sample_rate = int(sample_rate)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

class DemucsResult:
    """Artefacts produced by a completed Demucs separation job.

    Parameters
    ----------
    stem_paths:
        Mapping of stem name (e.g. ``'vocals'``) to the absolute path of
        the written audio file.
    sample_rate:
        Sample rate shared by all output files in Hz.
    duration_seconds:
        Duration of the separated audio in seconds.
    """

    stem_paths: dict[str, pathlib.Path]
    sample_rate: int
    duration_seconds: float

    def __init__(
        self,
        stem_paths: dict[str, pathlib.Path],
        sample_rate: int,
        duration_seconds: float,
    ) -> None:
        self.stem_paths = dict(stem_paths)
        self.sample_rate = int(sample_rate)
        self.duration_seconds = float(duration_seconds)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class DemucsPipeline:
    """Interface for the Demucs source-separation pipeline.

    Manages the full lifecycle of a separation job independently of any
    GUI framework.  The class is intentionally stateful so that a loaded
    model can be reused across multiple :meth:`run` calls without paying
    the weight-loading cost each time.

    Lifecycle
    ---------
    1. ``pipeline = DemucsPipeline()``
    2. ``pipeline.configure(config)`` — supply a :class:`DemucsConfig`.
    3. ``pipeline.load_model()``       — load weights into memory.
    4. ``result = pipeline.run(path)`` — separate one audio file.
    5. ``pipeline.clear()``            — release memory and reset state.

    Steps 4 and 5 may be repeated with different inputs before clearing.

    Notes
    -----
    * All private methods (prefixed ``_``) are part of the internal
      execution graph; callers should use only the public interface.
    * The class does *not* start background threads; thread management
      is the caller's responsibility.
    """

    is_loaded: bool
    _config: DemucsConfig | None
    _model: Any
    _loader: DemucsModelLoader | None
    _resampler: Resampler | None
    _progress_callback: Callable[[float, str], None] | None
    _device: str

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
        self._device = "cpu"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, config: DemucsConfig) -> None:
        """Set or replace the pipeline configuration.

        May be called before or after :meth:`load_model`.  If called after
        the model is already loaded and ``config.model_name`` has changed,
        the existing model is evicted and :meth:`load_model` must be called
        again before the next :meth:`run`.

        Parameters
        ----------
        config:
            A fully populated :class:`DemucsConfig` instance.
        """
        if (
            self.is_loaded
            and self._config is not None
            and config.model_name != self._config.model_name
            and self._loader is not None
        ):
            self._loader.evict(self._config.model_name)
            self._model = None
            self.is_loaded = False
        self._config = config

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def load_model(self) -> None:
        """Load the Demucs model weights specified in the current configuration.

        Fetches the checkpoint from the local cache (downloading it first if
        absent), verifies its checksum, and retains the loaded model in
        ``self._model`` for subsequent :meth:`run` calls.

        Raises
        ------
        :class:`~utils.errors.PipelineExecutionError`
            If :meth:`configure` has not been called prior to this method.
        :class:`~utils.errors.ModelLoadError`
            If the checkpoint cannot be read from disk or the download fails,
            or if the model identifier is not recognised.

        Post-condition
        --------------
        ``self.is_loaded`` is ``True`` and the model is ready for inference.
        """
        if self._config is None:
            raise PipelineExecutionError(
                "configure() must be called before load_model().",
                pipeline_name="demucs",
            )
        if self._loader is None:
            self._loader = DemucsModelLoader()

        try:
            self._model = self._loader.load(self._config.model_name)
        except ModelLoadError:
            raise
        except Exception as exc:
            raise ModelLoadError(
                f"Unexpected error loading model '{self._config.model_name}': {exc}",
                model_name=self._config.model_name,
            ) from exc

        # Move model to GPU once at load time so each run() call is fast.
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model = self._model.to(self._device)
        log.info("DemucsPipeline: model '%s' on %s", self._config.model_name, self._device)
        self.is_loaded = True

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self, input_data: pathlib.Path) -> DemucsResult:
        """Separate *input_data* into the stems listed in the configuration.

        Executes the full preprocessing → inference → postprocessing chain
        and writes one audio file per requested stem to
        ``config.output_dir``.

        Parameters
        ----------
        input_data:
            Absolute path to the source audio file to separate.
            Supported formats: WAV, FLAC, MP3, OGG, AIFF.

        Returns
        -------
        DemucsResult
            Mapping of stem names to output file paths, plus metadata.

        Raises
        ------
        :class:`~utils.errors.PipelineExecutionError`
            If :meth:`load_model` has not been called successfully, or if
            inference produces invalid (NaN/infinite) values.
        :class:`~utils.errors.InvalidInputError`
            If *input_data* does not exist on disk or has an unsupported
            file extension.
        :class:`~utils.errors.AudioProcessingError`
            If reading the input file or writing a stem output file fails.
        """
        if not self.is_loaded:
            raise PipelineExecutionError(
                "load_model() must be called before run().",
                pipeline_name="demucs",
            )
        if not input_data.exists():
            raise InvalidInputError(
                f"Input file not found: {input_data}", field="input_data"
            )

        self._report(5.0, "preprocessing")
        mix = self._preprocess(input_data)

        self._report(10.0, "separating")
        raw_stems = self._run_inference(mix)

        # Duration is frame count at model's native rate — invariant across output rate.
        ref_stem = next(iter(raw_stems.values()))
        duration = ref_stem.shape[-1] / self._model.samplerate

        self._report(90.0, "writing stems")
        stem_paths = self._postprocess(raw_stems)

        self._report(100.0, "done")
        return DemucsResult(stem_paths, self._config.sample_rate, duration)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Release model weights from memory and reset all pipeline state.

        After this call the pipeline is in the same state as immediately
        after :meth:`__init__`: no model is loaded and :meth:`run` will
        raise :class:`RuntimeError` until :meth:`load_model` is called again.

        Safe to call even if no model has been loaded.

        Post-condition
        --------------
        ``self.is_loaded`` is ``False`` and ``self._model`` is ``None``.
        """
        if self._loader is not None and self._config is not None:
            self._loader.evict(self._config.model_name)
        self._model = None
        self._resampler = None
        self.is_loaded = False

    # ------------------------------------------------------------------
    # Progress reporting
    # ------------------------------------------------------------------

    def set_progress_callback(self, callback: Callable[[float, str], None]) -> None:
        """Register a callback invoked periodically during separation.

        Parameters
        ----------
        callback:
            A callable with signature ``callback(percent: float, stem: str)``.
            *percent* is in the range ``[0.0, 100.0]``; *stem* identifies
            which stem is currently being processed.
        """
        self._progress_callback = callback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _report(self, pct: float, stage: str) -> None:
        if self._progress_callback is not None:
            self._progress_callback(pct, stage)

    def _preprocess(self, input_path: pathlib.Path) -> Any:
        """Load the audio file, downmix to stereo, and resample to the model rate.

        Parameters
        ----------
        input_path:
            Path to the source audio file.

        Returns
        -------
        Any
            Normalised waveform tensor ready for inference, shape
            ``(channels, samples)``.
        """
        from demucs.audio import convert_audio

        try:
            waveform, sr = read_audio(input_path)
        except (InvalidInputError, AudioProcessingError):
            raise
        except Exception as exc:
            raise AudioProcessingError(
                f"Failed to read {input_path}: {exc}", path=str(input_path)
            ) from exc

        # --- Stereo normalisation -----------------------------------------
        # demucs.audio.convert_audio asserts src_channels in {1, model.audio_channels}.
        # We guarantee that precondition here so the assertion always passes.
        #
        #   mono (1-ch)      → broadcast to 2 channels
        #   stereo (2-ch)    → pass through unchanged
        #   surround (N > 2) → mix down to mono, then broadcast to stereo
        n_ch = waveform.shape[0]
        if n_ch == 1:
            waveform = convert_channels(waveform, 2)
        elif n_ch > 2:
            waveform = convert_channels(waveform, 1)  # arbitrary N → mono
            waveform = convert_channels(waveform, 2)  # mono → stereo
        # n_ch == 2: already stereo, no-op

        # (channels, samples) numpy → (1, channels, samples) float32 tensor
        mix = torch.from_numpy(waveform).unsqueeze(0).to(torch.float32)

        # convert_audio handles both resampling and channel count enforcement
        # simultaneously, matching the model's native samplerate + audio_channels.
        mix = convert_audio(mix, sr, self._model.samplerate, self._model.audio_channels)

        return mix

    def _run_inference(self, waveform: Any) -> dict[str, np.ndarray]:
        """Pass *waveform* through the loaded Demucs model.

        Parameters
        ----------
        waveform:
            Preprocessed waveform from :meth:`_preprocess`.

        Returns
        -------
        dict[str, Any]
            Raw per-stem waveform tensors keyed by stem name.
        """
        from demucs.apply import apply_model

        try:
            with torch.no_grad():
                # apply_model returns (batch, stems, channels, samples)
                sources = apply_model(
                    self._model,
                    waveform.to(self._device),
                    progress=False,
                )
        except Exception as exc:
            raise PipelineExecutionError(
                f"Demucs inference failed: {exc}", pipeline_name="demucs"
            ) from exc

        # Drop batch dim → (stems, channels, samples)
        sources = sources[0]

        if torch.isnan(sources).any() or torch.isinf(sources).any():
            raise PipelineExecutionError(
                "Demucs produced NaN/Inf values — check input audio.",
                pipeline_name="demucs",
            )

        stem_names: list[str] = self._model.sources  # e.g. ['drums', 'bass', 'other', 'vocals']
        requested = set(self._config.stems)

        raw: dict[str, np.ndarray] = {}
        for i, name in enumerate(stem_names):
            if name in requested:
                raw[name] = sources[i].cpu().numpy()  # (channels, samples) float32

        missing = requested - set(raw.keys())
        if missing:
            raise PipelineExecutionError(
                f"Requested stems not in model output: {missing}. "
                f"Model supports: {stem_names}",
                pipeline_name="demucs",
            )

        return raw

    def _postprocess(self, raw_stems: dict[str, Any]) -> dict[str, pathlib.Path]:
        """Clip, rescale, and write each raw stem tensor to an audio file.

        Parameters
        ----------
        raw_stems:
            Output of :meth:`_run_inference`.

        Returns
        -------
        dict[str, pathlib.Path]
            Mapping of stem name to the path of the written audio file.
        """
        model_rate = self._model.samplerate
        out_rate = self._config.sample_rate

        # Build a resampler only when the output rate differs from the model rate.
        if model_rate != out_rate:
            post_resampler: Resampler | None = Resampler(model_rate, out_rate)
            log.debug("Post-process resampling: %d → %d Hz", model_rate, out_rate)
        else:
            post_resampler = None

        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        paths: dict[str, pathlib.Path] = {}

        for idx, (name, stem_waveform) in enumerate(raw_stems.items()):
            pct = 90.0 + (idx / len(raw_stems)) * 8.0
            self._report(pct, f"writing {name}")

            stem_waveform = np.clip(stem_waveform, -1.0, 1.0)
            if post_resampler is not None:
                stem_waveform = post_resampler(stem_waveform)

            out_path = self._config.output_dir / f"{name}.wav"
            try:
                write_audio(stem_waveform, out_rate, out_path)
            except Exception as exc:
                raise AudioProcessingError(
                    f"Failed to write stem '{name}' to {out_path}: {exc}",
                    path=str(out_path),
                ) from exc

            log.debug("Wrote stem '%s' → %s", name, out_path)
            paths[name] = out_path

        return paths
