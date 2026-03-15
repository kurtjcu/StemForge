"""
RVC voice conversion pipeline for StemForge.

Wraps the vendored Applio RVC inference code (vendor/rvc/) into StemForge's
standard pipeline contract: configure → load_model → run → clear.

Typical usage
-------------
::

    pipeline = RvcPipeline()
    pipeline.configure(RvcConfig(
        model_path=Path("~/.cache/stemforge/voice_models/some_voice.pth"),
        pitch=0, f0_method="rmvpe",
    ))
    pipeline.load_model()
    result = pipeline.run(Path("vocals.wav"))
    pipeline.clear()
"""

import pathlib
import logging
import uuid
import time
from typing import Callable

from utils.errors import InvalidInputError, ModelLoadError, PipelineExecutionError
from utils.paths import VOICE_DIR

log = logging.getLogger("stemforge.pipelines.rvc")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class RvcConfig:
    """Configuration for a single RVC voice conversion job."""

    model_path: pathlib.Path
    index_path: pathlib.Path | None
    pitch: int
    f0_method: str
    index_rate: float
    protect: float
    filter_radius: int
    volume_envelope: float

    def __init__(
        self,
        model_path: pathlib.Path,
        index_path: pathlib.Path | None = None,
        pitch: int = 0,
        f0_method: str = "rmvpe",
        index_rate: float = 0.3,
        protect: float = 0.33,
        filter_radius: int = 3,
        volume_envelope: float = 0.0,
        output_dir: pathlib.Path | None = None,
    ) -> None:
        self.model_path = pathlib.Path(model_path)
        self.index_path = pathlib.Path(index_path) if index_path else None
        self.pitch = int(pitch)
        self.f0_method = str(f0_method)
        self.index_rate = float(index_rate)
        self.protect = float(protect)
        self.filter_radius = int(filter_radius)
        self.volume_envelope = float(volume_envelope)
        self.output_dir = pathlib.Path(output_dir) if output_dir else None


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

class RvcResult:
    """Artefacts produced by a completed RVC conversion job."""

    output_path: pathlib.Path
    duration_seconds: float

    def __init__(self, output_path: pathlib.Path, duration_seconds: float) -> None:
        self.output_path = pathlib.Path(output_path)
        self.duration_seconds = float(duration_seconds)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class RvcPipeline:
    """Voice conversion pipeline using vendored Applio RVC code."""

    def __init__(self) -> None:
        self._config: RvcConfig | None = None
        self._vc = None  # VoiceConverter instance
        self.is_loaded: bool = False
        self._progress_cb: Callable | None = None

    def set_progress_callback(self, cb: Callable) -> None:
        self._progress_cb = cb

    def _emit(self, progress: float, stage: str) -> None:
        if self._progress_cb:
            self._progress_cb(progress, stage)

    def configure(self, config: RvcConfig) -> None:
        self._config = config

    def load_model(self) -> None:
        """Instantiate the VoiceConverter (loads embedder + config, not voice model yet)."""
        if self.is_loaded:
            return
        try:
            self._emit(0.1, "Loading RVC engine")
            from vendor.rvc.infer.infer import VoiceConverter
            self._vc = VoiceConverter()
            self.is_loaded = True
            self._emit(0.2, "RVC engine ready")
            log.info("RVC VoiceConverter initialized")
        except Exception as exc:
            raise ModelLoadError(str(exc), model_name="rvc") from exc

    def run(self, audio_path: pathlib.Path) -> RvcResult:
        """Run voice conversion on the given audio file."""
        if not self._config:
            raise InvalidInputError("No RVC config set", field="config")
        if not self.is_loaded or not self._vc:
            raise PipelineExecutionError("RVC not loaded", pipeline_name="rvc")

        audio_path = pathlib.Path(audio_path)
        if not audio_path.exists():
            raise InvalidInputError(f"Audio file not found: {audio_path}", field="audio_path")

        cfg = self._config
        if not cfg.model_path.exists():
            raise InvalidInputError(f"Voice model not found: {cfg.model_path}", field="model_path")

        # Prepare output path
        out_dir = cfg.output_dir or VOICE_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        stem_name = audio_path.stem
        model_name = cfg.model_path.stem
        out_name = f"{stem_name}_{model_name}_{uuid.uuid4().hex[:8]}.wav"
        output_path = out_dir / out_name

        self._emit(0.3, "Loading voice model")
        log.info("RVC convert: %s → %s (model=%s, pitch=%d, f0=%s)",
                 audio_path.name, out_name, cfg.model_path.name, cfg.pitch, cfg.f0_method)

        try:
            start = time.time()

            self._emit(0.4, "Converting voice")
            self._vc.convert_audio(
                audio_input_path=str(audio_path),
                audio_output_path=str(output_path),
                model_path=str(cfg.model_path),
                index_path=str(cfg.index_path) if cfg.index_path else "",
                pitch=cfg.pitch,
                f0_method=cfg.f0_method,
                index_rate=cfg.index_rate,
                volume_envelope=cfg.volume_envelope,
                protect=cfg.protect,
                hop_length=128,
                split_audio=False,
                f0_autotune=False,
                clean_audio=False,
                export_format="WAV",
                post_process=False,
                embedder_model="contentvec",
            )

            elapsed = time.time() - start
            self._emit(0.9, "Measuring output")

            # Get duration
            import soundfile as sf
            info = sf.info(str(output_path))
            duration = info.duration

            self._emit(1.0, "Done")
            log.info("RVC conversion done in %.1fs → %s (%.1fs audio)", elapsed, out_name, duration)

            return RvcResult(output_path=output_path, duration_seconds=duration)

        except Exception as exc:
            # Clean up partial output
            if output_path.exists():
                output_path.unlink(missing_ok=True)
            raise PipelineExecutionError(str(exc), pipeline_name="rvc") from exc

    def clear(self) -> None:
        """Release GPU memory."""
        if self._vc:
            try:
                self._vc.cleanup_model()
            except Exception:
                pass
            self._vc = None
        self.is_loaded = False
        self._config = None

        import gc
        import torch
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        log.info("RVC pipeline cleared")
