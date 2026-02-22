"""Stable Audio Open generation pipeline for StemForge.

Wraps ``stable_audio_tools.inference.generation.generate_diffusion_cond``
behind the standard StemForge pipeline interface.

Conditioning sources
--------------------
* **Text prompt** — always required; passed directly to the model.
* **Audio conditioning** (``init_audio_path``) — any audio file.
  Loaded with ``utils.audio_io.read_audio``, resampled to the model's
  native 44 100 Hz, and passed as ``init_audio`` to the diffusion sampler.
  ``init_noise_level`` (0.1–1.0) controls how strongly the init audio
  shapes the output: 0.1 = closely follow init, 1.0 = ignore init.
* **MIDI conditioning** (``midi_path``) — a MIDI file parsed for BPM,
  key signature, and GM instrument families; the extracted tags are
  appended to the text prompt so the model receives them as language cues.

Typical usage
-------------
::

    pipeline = MusicGenPipeline()
    pipeline.configure(MusicGenConfig(
        model_name="stabilityai/stable-audio-open-1.0",
        prompt="upbeat jazz piano trio, walking bass, brushed drums",
        duration_seconds=30.0,
        steps=100,
        init_audio_path=Path("vocals_stem.wav"),
        init_noise_level=0.6,
        midi_path=Path("extracted.mid"),
        output_dir=Path("~/.local/share/stemforge/output/musicgen"),
    ))
    pipeline.load_model()
    result = pipeline.run("")   # prompt already in config
    pipeline.clear()
"""

from __future__ import annotations

import datetime
import logging
import pathlib
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import torch

from models.musicgen_loader import MusicGenModelLoader
from utils.audio_io import read_audio, write_audio
from utils.errors import (
    AudioProcessingError,
    InvalidInputError,
    ModelLoadError,
    PipelineExecutionError,
)


log = logging.getLogger("stemforge.pipelines.musicgen")

# ---------------------------------------------------------------------------
# General MIDI instrument family map (program 0–127)
# ---------------------------------------------------------------------------

def _gm_family(program: int) -> str | None:
    """Return a human-readable family name for a General MIDI program number."""
    if program <   8: return "piano"
    if program <  16: return "chromatic percussion"
    if program <  24: return "organ"
    if program <  32: return "guitar"
    if program <  40: return "bass"
    if program <  48: return "strings"
    if program <  56: return "ensemble"
    if program <  64: return "brass"
    if program <  72: return "reed"
    if program <  80: return "pipe"
    if program <  88: return "synth lead"
    if program <  96: return "synth pad"
    if program < 104: return "synth effects"
    if program < 112: return "ethnic"
    if program < 120: return "percussive"
    return None


def extract_midi_info(midi_path: pathlib.Path) -> dict[str, Any]:
    """Parse *midi_path* and return a dict of extracted musical metadata.

    Returns
    -------
    dict with keys:
        ``bpm`` (int), ``key`` (str), ``instruments`` (list[str]),
        ``description`` (str) — ready to append to a text prompt.

    Never raises; returns defaults on any parse error.
    """
    result: dict[str, Any] = {"bpm": 120, "key": "", "instruments": [], "description": ""}
    try:
        import mido  # type: ignore[import]
        mid = mido.MidiFile(str(midi_path))

        bpm = 120
        key_sig = ""
        programs: set[int] = set()

        for track in mid.tracks:
            for msg in track:
                if msg.type == "set_tempo" and bpm == 120:
                    bpm = max(1, round(60_000_000 / msg.tempo))
                if msg.type == "key_signature" and not key_sig:
                    key_sig = msg.key
                if msg.type == "program_change" and getattr(msg, "channel", 0) != 9:
                    programs.add(msg.program)

        families = sorted({f for p in programs if (f := _gm_family(p)) is not None})
        parts: list[str] = [f"{bpm} BPM"]
        if key_sig:
            parts.append(key_sig)
        parts.extend(families)

        result["bpm"] = bpm
        result["key"] = key_sig
        result["instruments"] = families
        result["description"] = ", ".join(parts)
    except Exception as exc:
        log.warning("MIDI info extraction failed for %s: %s", midi_path, exc)
    return result


def enrich_prompt_from_midi(base_prompt: str, midi_path: pathlib.Path) -> str:
    """Return *base_prompt* extended with MIDI-extracted tags.

    Tags are appended as a comma-separated suffix so the model sees them
    naturally as part of the text prompt.  If extraction fails the original
    prompt is returned unchanged.
    """
    info = extract_midi_info(midi_path)
    desc = info.get("description", "")
    if not desc:
        return base_prompt
    if base_prompt:
        return f"{base_prompt}, {desc}"
    return desc


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class MusicGenConfig:
    """Immutable configuration snapshot for a single generation job.

    Parameters
    ----------
    model_name:
        HuggingFace repo ID for the Stable Audio Open model.
    prompt:
        Natural-language description of the music to generate.
    duration_seconds:
        Target clip length (1–47 s; model hard limit is ~47 s at 44 100 Hz).
    steps:
        Number of diffusion sampling steps.  More → higher quality, slower.
    cfg_scale:
        Classifier-free guidance scale.  Higher → more prompt-faithful.
    sigma_min / sigma_max:
        Noise schedule bounds for the ``dpmpp-3m-sde`` sampler.
    sampler_type:
        Sampler name passed to ``generate_diffusion_cond``.
    init_audio_path:
        Optional audio file for init-audio conditioning.  The file is
        resampled to 44 100 Hz and truncated / padded to *duration_seconds*.
    init_noise_level:
        Amount of noise injected into *init_audio* before the reverse
        diffusion pass (0.1 = strongly follow audio, 1.0 = ignore audio).
        Only used when *init_audio_path* is set.
    midi_path:
        Optional MIDI file whose BPM, key, and instruments are appended to
        the text prompt.
    output_dir:
        Directory where the generated WAV is written.
    """
    model_name:       str   = "stabilityai/stable-audio-open-1.0"
    prompt:           str   = ""
    duration_seconds: float = 30.0
    steps:            int   = 100
    cfg_scale:        float = 7.0
    sigma_min:        float = 0.3
    sigma_max:        float = 500.0
    sampler_type:     str   = "dpmpp-3m-sde"
    init_audio_path:  pathlib.Path | None = None
    init_noise_level: float = 0.7
    midi_path:        pathlib.Path | None = None
    output_dir:       pathlib.Path | None = None


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class MusicGenResult:
    """Artefacts produced by a completed generation job."""
    audio_path:       pathlib.Path
    sample_rate:      int
    duration_seconds: float
    prompt_used:      str   = ""   # actual prompt sent to the model (after MIDI enrichment)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class MusicGenPipeline:
    """Stable Audio Open generation pipeline.

    Lifecycle: configure → load_model → run → clear.
    """

    def __init__(self) -> None:
        self._config: MusicGenConfig | None = None
        self._model: Any = None
        self._model_config: dict | None = None
        self._loader = MusicGenModelLoader()
        self._progress_cb: Callable[[float, str], None] | None = None
        self.is_loaded: bool = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(self, config: MusicGenConfig) -> None:
        if self._config is not None and self._config.model_name != config.model_name:
            self.clear()
        self._config = config

    # ------------------------------------------------------------------
    # Model management
    # ------------------------------------------------------------------

    def load_model(self) -> None:
        if self._config is None:
            raise PipelineExecutionError(
                "configure() must be called before load_model().",
                pipeline_name="musicgen",
            )
        if self.is_loaded and self._loader.is_cached(self._config.model_name):
            return
        self._model, self._model_config = self._loader.load(self._config.model_name)
        self.is_loaded = True

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self, input_data: str) -> MusicGenResult:
        """Generate audio conditioned on *input_data* (text prompt).

        If *input_data* is empty, ``config.prompt`` is used instead.
        The final prompt may be further enriched from ``config.midi_path``.

        Parameters
        ----------
        input_data:
            Text prompt.  Pass ``""`` to use ``config.prompt``.

        Returns
        -------
        MusicGenResult

        Raises
        ------
        PipelineExecutionError
            If ``load_model()`` has not been called successfully.
        InvalidInputError
            If the final effective prompt is empty.
        """
        if not self.is_loaded or self._model is None:
            raise PipelineExecutionError(
                "load_model() must be called before run().",
                pipeline_name="musicgen",
            )
        if self._config is None:
            raise PipelineExecutionError(
                "configure() must be called before run().",
                pipeline_name="musicgen",
            )

        # --- Resolve prompt ---
        prompt = (input_data.strip() or self._config.prompt.strip())
        if not prompt:
            raise InvalidInputError("Prompt must not be empty.", field="prompt")

        # --- MIDI enrichment ---
        midi_path = self._config.midi_path
        if midi_path and midi_path.exists():
            prompt = enrich_prompt_from_midi(prompt, midi_path)
            log.info("Enriched prompt: %s", prompt)

        self._progress(5.0, "Preparing generation …")

        # --- Diffusion parameters ---
        cfg = self._config
        assert self._model_config is not None
        sample_rate: int = self._model_config["sample_rate"]   # 44100
        sample_size: int = int(sample_rate * cfg.duration_seconds)

        conditioning = [{
            "prompt":         prompt,
            "seconds_start":  0,
            "seconds_total":  cfg.duration_seconds,
        }]

        # --- Audio init conditioning ---
        init_audio = None
        init_noise_level = 1.0   # default: ignore init, pure text generation
        if cfg.init_audio_path and cfg.init_audio_path.exists():
            init_audio = self._load_init_audio(
                cfg.init_audio_path, sample_rate, cfg.duration_seconds
            )
            init_noise_level = float(cfg.init_noise_level)

        # --- Device ---
        try:
            device_str = next(self._model.parameters()).device.type
        except StopIteration:
            device_str = "cpu"

        self._progress(10.0, f"Generating on {device_str.upper()} …")

        # --- Generation ---
        try:
            from stable_audio_tools.inference.generation import (  # type: ignore[import]
                generate_diffusion_cond,
            )
        except ImportError as exc:
            raise PipelineExecutionError(
                f"stable-audio-tools not available: {exc}",
                pipeline_name="musicgen",
            ) from exc

        try:
            output = generate_diffusion_cond(
                self._model,
                steps           = cfg.steps,
                cfg_scale       = cfg.cfg_scale,
                conditioning    = conditioning,
                sample_size     = sample_size,
                sample_rate     = sample_rate,
                device          = device_str,
                init_audio      = init_audio,
                init_noise_level= init_noise_level,
                sigma_min       = cfg.sigma_min,
                sigma_max       = cfg.sigma_max,
                sampler_type    = cfg.sampler_type,
            )
        except Exception as exc:
            raise PipelineExecutionError(
                f"Generation failed: {exc}",
                pipeline_name="musicgen",
            ) from exc

        self._progress(90.0, "Writing audio …")

        # output shape: (1, channels, samples) or (batch, channels, samples)
        waveform_np: np.ndarray = output.squeeze(0).cpu().float().numpy()  # (2, samples)

        # --- Write to disk ---
        out_dir = cfg.output_dir or (pathlib.Path.home() / "Music" / "StemForge")
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"stemforge_gen_{ts}.wav"
        write_audio(waveform_np, sample_rate, out_path)

        duration = waveform_np.shape[1] / sample_rate
        self._progress(100.0, f"Done — {duration:.1f} s")
        log.info("Generated %s (%.1f s)", out_path.name, duration)

        return MusicGenResult(
            audio_path       = out_path.resolve(),
            sample_rate      = sample_rate,
            duration_seconds = duration,
            prompt_used      = prompt,
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Release model weights from memory and reset state."""
        self._loader.evict()
        self._model = None
        self._model_config = None
        self.is_loaded = False

    # ------------------------------------------------------------------
    # Progress reporting
    # ------------------------------------------------------------------

    def set_progress_callback(self, callback: Callable[[float, str], None]) -> None:
        """Register ``callback(percent, stage)`` invoked during generation."""
        self._progress_cb = callback

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _progress(self, pct: float, stage: str) -> None:
        if self._progress_cb is not None:
            try:
                self._progress_cb(pct, stage)
            except Exception:
                pass

    def _load_init_audio(
        self,
        path: pathlib.Path,
        target_sr: int,
        seconds: float,
    ) -> tuple[int, torch.Tensor]:
        """Load *path*, resample to *target_sr*, truncate to *seconds*.

        Returns ``(sample_rate, tensor)`` where tensor has shape
        ``(1, 2, samples)`` on the same device as the model.
        """
        waveform_np, _ = read_audio(path, mono=False, target_rate=target_sr)
        # Ensure stereo — shape (2, samples)
        if waveform_np.shape[0] == 1:
            waveform_np = np.concatenate([waveform_np, waveform_np], axis=0)
        # Truncate to target duration
        target_samples = int(target_sr * seconds)
        waveform_np = waveform_np[:, :target_samples]

        tensor = torch.from_numpy(waveform_np).unsqueeze(0)  # (1, 2, samples)
        try:
            device = next(self._model.parameters()).device
            tensor = tensor.to(device)
        except (StopIteration, Exception):
            pass
        return (target_sr, tensor)
