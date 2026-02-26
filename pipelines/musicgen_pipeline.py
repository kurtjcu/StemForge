"""Stable Audio Open generation pipeline for StemForge.

Wraps ``diffusers.StableAudioPipeline`` behind the standard StemForge
pipeline interface.

Conditioning sources
--------------------
* **Text prompt** — always required; passed directly to the model.
* **Audio conditioning** (``init_audio_path``) — any audio file.
  Loaded with ``utils.audio_io.read_audio``, resampled to the model's
  native 44 100 Hz, and passed as ``initial_audio_waveforms`` to the
  pipeline.  The VAE encodes the audio and the diffusion process blends
  the resulting latents with the text-conditioned generation.
* **MIDI conditioning** (``midi_path``) — a MIDI file parsed for BPM,
  key signature, and GM instrument families; the extracted tags are
  appended to the text prompt so the model receives them as language cues.

Typical usage
-------------
::

    pipeline = MusicGenPipeline()
    pipeline.configure(MusicGenConfig(
        prompt="upbeat jazz piano trio, walking bass, brushed drums",
        duration_seconds=30.0,
        steps=100,
        init_audio_path=Path("vocals_stem.wav"),
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
import math
import pathlib
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import torch

from models.musicgen_loader import MusicGenModelLoader
from utils.audio_io import read_audio, write_audio
from utils.errors import (
    InvalidInputError,
    ModelLoadError,
    PipelineExecutionError,
)


log = logging.getLogger("stemforge.pipelines.musicgen")

# StableAudioPipeline generates at most this many seconds per call.
_MAX_CHUNK_SECONDS = 47.0

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

        result.update({
            "bpm":         bpm,
            "key":         key_sig,
            "instruments": families,
            "description": ", ".join(parts),
        })
    except Exception as exc:
        log.warning("MIDI info extraction failed for %s: %s", midi_path, exc)
    return result


def enrich_prompt_from_midi(base_prompt: str, midi_path: pathlib.Path) -> str:
    """Return *base_prompt* extended with MIDI-extracted tags.

    Tags are appended as a comma-separated suffix.  If extraction fails
    the original prompt is returned unchanged.
    """
    desc = extract_midi_info(midi_path).get("description", "")
    if not desc:
        return base_prompt
    return f"{base_prompt}, {desc}" if base_prompt else desc


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
        Target clip length in seconds.  Durations longer than 47 s are
        split into equal chunks (each ≤ 47 s) and concatenated.
    steps:
        Number of diffusion sampling steps.  More → higher quality, slower.
    cfg_scale:
        Classifier-free guidance scale.  Higher → more prompt-faithful.
    negative_prompt:
        Text describing what to avoid in the generated audio.
    init_audio_path:
        Optional audio file for init-audio conditioning.  The file is
        resampled to 44 100 Hz and truncated / padded to *duration_seconds*.
        The VAE encodes it and the diffusion process uses the resulting
        latents as a starting point alongside the text conditioning.
    midi_path:
        Optional MIDI file whose BPM, key, and instruments are appended to
        the text prompt.
    output_dir:
        Directory where the generated WAV is written.
    vocal_preservation:
        Enable Vocal Preservation Mode.  When True and *init_audio_path* is
        set, applies *conditioning_strength* scaling and optional per-window
        generation (timing_lock).  Degrades gracefully to negative-prompt-
        only when no audio conditioning source is provided.
    conditioning_strength:
        Scales the init audio waveform amplitude before VAE encoding.
        1.0 = full reference, 0.0 = effectively no audio conditioning.
        Only applied when *init_audio_path* is set.
    timing_lock:
        When True (and *vocal_preservation* is True), divides the source
        audio into *window_size_seconds* windows and generates each window
        separately so rhythmic alignment is preserved.  Chunks are joined
        with a 50 ms crossfade.
    window_size_seconds:
        Window length in seconds for timing-locked generation.
    """
    model_name:            str   = "stabilityai/stable-audio-open-1.0"
    prompt:                str   = ""
    duration_seconds:      float = 30.0
    steps:                 int   = 100
    cfg_scale:             float = 7.0
    negative_prompt:       str   = "low quality, distorted, noise, clipping"
    init_audio_path:       pathlib.Path | None = None
    midi_path:             pathlib.Path | None = None
    output_dir:            pathlib.Path | None = None
    vocal_preservation:    bool  = False
    conditioning_strength: float = 0.7
    timing_lock:           bool  = True
    window_size_seconds:   float = 10.0


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class MusicGenResult:
    """Artefacts produced by a completed generation job."""
    audio_path:       pathlib.Path
    sample_rate:      int
    duration_seconds: float
    prompt_used:      str = ""   # actual prompt sent to the model (after MIDI enrichment)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class MusicGenPipeline:
    """Stable Audio Open generation pipeline.

    Lifecycle: configure → load_model → run → clear.
    """

    def __init__(self) -> None:
        self._config: MusicGenConfig | None = None
        self._pipeline: Any = None
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
        self._pipeline, self._model_config = self._loader.load(self._config.model_name)
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
        if not self.is_loaded or self._pipeline is None:
            raise PipelineExecutionError(
                "load_model() must be called before run().",
                pipeline_name="musicgen",
            )
        if self._config is None:
            raise PipelineExecutionError(
                "configure() must be called before run().",
                pipeline_name="musicgen",
            )

        cfg    = self._config
        prompt = (input_data.strip() or cfg.prompt.strip())
        if not prompt:
            raise InvalidInputError("Prompt must not be empty.", field="prompt")

        # MIDI enrichment
        if cfg.midi_path and cfg.midi_path.exists():
            prompt = enrich_prompt_from_midi(prompt, cfg.midi_path)
            log.info("Enriched prompt: %s", prompt)

        self._progress(5.0, "Preparing generation …")

        assert self._model_config is not None
        sample_rate: int = self._model_config["sample_rate"]

        # Detect device via transformer parameters
        try:
            device_str = next(self._pipeline.transformer.parameters()).device.type
        except Exception:
            device_str = "cpu"

        self._progress(10.0, f"Generating on {device_str.upper()} …")

        # ------------------------------------------------------------------
        # Vocal Preservation Mode — windowed per-window generation
        # ------------------------------------------------------------------
        has_audio_cond = cfg.init_audio_path and cfg.init_audio_path.exists()

        if cfg.vocal_preservation and cfg.timing_lock and has_audio_cond:
            chunks = self._run_vp_windowed(cfg, prompt, sample_rate)
        else:
            chunks = self._run_standard(cfg, prompt, sample_rate, has_audio_cond)

        self._progress(92.0, "Writing audio …")

        waveform_np: np.ndarray = self._crossfade_chunks(chunks, sample_rate)

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
        """Release pipeline weights from memory and reset state."""
        self._loader.evict()
        self._pipeline = None
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

    # ------------------------------------------------------------------
    # Generation workers
    # ------------------------------------------------------------------

    def _make_step_callback(
        self,
        steps: int,
        band_start: float,
        band_end: float,
    ) -> Callable:
        """Return a diffusion step callback scoped to a progress band."""
        progress_cb = self._progress_cb

        def _cb(step: int, timestep: int, latents: torch.Tensor) -> None:
            if progress_cb is not None:
                pct = band_start + (step / max(steps, 1)) * (band_end - band_start)
                try:
                    progress_cb(pct, f"Generating… {step + 1}/{steps}")
                except Exception:
                    pass

        return _cb

    def _call_pipeline(
        self,
        prompt: str,
        negative_prompt: str,
        duration: float,
        steps: int,
        cfg_scale: float,
        sample_rate: int,
        init_waveforms: torch.Tensor | None,
        step_callback: Callable,
        chunk_label: str,
    ) -> np.ndarray:
        """Call the StableAudioPipeline for one segment, return (2, samples)."""
        try:
            pipe_output = self._pipeline(
                prompt                      = prompt,
                negative_prompt             = negative_prompt,
                audio_end_in_s              = duration,
                num_inference_steps         = steps,
                guidance_scale              = cfg_scale,
                num_waveforms_per_prompt    = 1,
                initial_audio_waveforms     = init_waveforms,
                initial_audio_sampling_rate = (
                    torch.tensor(sample_rate) if init_waveforms is not None else None
                ),
                callback                    = step_callback,
                callback_steps              = 1,
                output_type                 = "pt",
            )
        except Exception as exc:
            raise PipelineExecutionError(
                f"Generation failed on {chunk_label}: {exc}",
                pipeline_name="musicgen",
            ) from exc
        return pipe_output.audios[0].float().cpu().numpy()  # (2, samples)

    def _run_standard(
        self,
        cfg: "MusicGenConfig",
        prompt: str,
        sample_rate: int,
        has_audio_cond: bool,
    ) -> list[np.ndarray]:
        """Standard chunked generation (durations > 47 s split into pieces).

        Audio conditioning is applied to the first chunk only so the
        reference timbre is not repeated on every 47-second boundary.
        """
        n_chunks  = max(1, math.ceil(cfg.duration_seconds / _MAX_CHUNK_SECONDS))
        chunk_dur = cfg.duration_seconds / n_chunks

        strength = cfg.conditioning_strength if cfg.vocal_preservation else 1.0
        init_audio: torch.Tensor | None = None
        if has_audio_cond:
            # Pass a full _MAX_CHUNK_SECONDS window so the model's internal
            # buffer is filled and no silence padding dilutes conditioning.
            init_audio = self._load_init_audio(
                cfg.init_audio_path, sample_rate, _MAX_CHUNK_SECONDS, strength=strength
            )

        chunks: list[np.ndarray] = []
        for i in range(n_chunks):
            band_start = 10.0 + i       * (80.0 / n_chunks)
            band_end   = 10.0 + (i + 1) * (80.0 / n_chunks)
            if n_chunks > 1:
                self._progress(band_start, f"Generating chunk {i + 1}/{n_chunks}…")
            chunk = self._call_pipeline(
                prompt          = prompt,
                negative_prompt = cfg.negative_prompt,
                duration        = chunk_dur,
                steps           = cfg.steps,
                cfg_scale       = cfg.cfg_scale,
                sample_rate     = sample_rate,
                init_waveforms  = init_audio if i == 0 else None,
                step_callback   = self._make_step_callback(cfg.steps, band_start, band_end),
                chunk_label     = f"chunk {i + 1}/{n_chunks}",
            )
            chunks.append(chunk)
        return chunks

    def _run_vp_windowed(
        self,
        cfg: "MusicGenConfig",
        prompt: str,
        sample_rate: int,
    ) -> list[np.ndarray]:
        """Vocal Preservation windowed generation.

        Divides the source audio into ``cfg.window_size_seconds`` windows.
        Each window is generated separately conditioned on the corresponding
        audio slice, preserving rhythmic alignment across the full duration.
        """
        win_sec = min(cfg.window_size_seconds, _MAX_CHUNK_SECONDS)
        n_windows = max(1, math.ceil(cfg.duration_seconds / win_sec))

        # Load full source audio once
        full_np, _ = read_audio(cfg.init_audio_path, mono=False, target_rate=sample_rate)
        if full_np.shape[0] == 1:
            full_np = np.concatenate([full_np, full_np], axis=0)
        full_np = full_np.astype(np.float32) * float(cfg.conditioning_strength)

        win_samples  = int(win_sec * sample_rate)
        # Always give the model a full _MAX_CHUNK_SECONDS conditioning slice
        # so its internal buffer is filled and no silence dilutes the signal.
        cond_samples = int(_MAX_CHUNK_SECONDS * sample_rate)
        chunks: list[np.ndarray] = []

        for i in range(n_windows):
            band_start = 10.0 + i       * (80.0 / n_windows)
            band_end   = 10.0 + (i + 1) * (80.0 / n_windows)
            self._progress(band_start, f"VP window {i + 1}/{n_windows}…")

            src_start = i * win_samples
            if src_start < full_np.shape[1]:
                window_np = full_np[:, src_start:src_start + cond_samples]
                # Pad with silence only if the source ends before cond_samples
                if window_np.shape[1] < cond_samples:
                    pad = np.zeros((2, cond_samples - window_np.shape[1]), dtype=np.float32)
                    window_np = np.concatenate([window_np, pad], axis=1)
            else:
                # Source exhausted — generate without audio conditioning
                window_np = None  # type: ignore[assignment]

            init_tensor = self._array_to_tensor(window_np) if window_np is not None else None

            chunk = self._call_pipeline(
                prompt          = prompt,
                negative_prompt = cfg.negative_prompt,
                duration        = win_sec,
                steps           = cfg.steps,
                cfg_scale       = cfg.cfg_scale,
                sample_rate     = sample_rate,
                init_waveforms  = init_tensor,
                step_callback   = self._make_step_callback(cfg.steps, band_start, band_end),
                chunk_label     = f"VP window {i + 1}/{n_windows}",
            )
            chunks.append(chunk)
        return chunks

    # ------------------------------------------------------------------
    # Tensor / audio helpers
    # ------------------------------------------------------------------

    def _array_to_tensor(self, waveform_np: np.ndarray) -> torch.Tensor:
        """Convert a (2, samples) float32 array to a pipeline-compatible tensor.

        Returns shape ``(1, 2, samples)`` on the model's device and dtype.
        """
        tensor = torch.from_numpy(waveform_np.astype(np.float32)).unsqueeze(0)
        try:
            param = next(self._pipeline.transformer.parameters())
            tensor = tensor.to(device=param.device, dtype=param.dtype)
        except Exception:
            pass
        return tensor

    def _crossfade_chunks(
        self,
        chunks: list[np.ndarray],
        sr: int,
        fade_ms: float = 50.0,
    ) -> np.ndarray:
        """Join a list of (2, samples) arrays with a linear crossfade.

        Parameters
        ----------
        chunks:
            List of stereo arrays to join in order.
        sr:
            Sample rate, used to convert *fade_ms* to samples.
        fade_ms:
            Crossfade duration in milliseconds (default 50 ms).
        """
        if len(chunks) == 1:
            return chunks[0]
        fade_n = min(int(fade_ms / 1000.0 * sr), chunks[0].shape[1] // 2)
        if fade_n == 0:
            return np.concatenate(chunks, axis=1)

        fade_out = np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
        fade_in  = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)

        result = chunks[0]
        for nxt in chunks[1:]:
            tail   = result[:, -fade_n:] * fade_out
            head   = nxt[:, :fade_n]     * fade_in
            overlap = tail + head
            result = np.concatenate(
                [result[:, :-fade_n], overlap, nxt[:, fade_n:]],
                axis=1,
            )
        return result

    def _load_init_audio(
        self,
        path: pathlib.Path,
        target_sr: int,
        seconds: float,
        strength: float = 1.0,
    ) -> torch.Tensor:
        """Load *path*, resample to *target_sr*, truncate to *seconds*.

        Parameters
        ----------
        strength:
            Multiplier applied to the waveform before encoding.
            1.0 = full reference, 0.0 = silence.

        Returns a tensor of shape ``(1, 2, samples)`` on the pipeline's
        device, suitable for ``initial_audio_waveforms``.
        """
        waveform_np, _ = read_audio(path, mono=False, target_rate=target_sr)
        # Ensure stereo — shape (2, samples)
        if waveform_np.shape[0] == 1:
            waveform_np = np.concatenate([waveform_np, waveform_np], axis=0)
        # Truncate to target duration
        target_samples = int(target_sr * seconds)
        waveform_np = waveform_np[:, :target_samples]
        if strength != 1.0:
            waveform_np = waveform_np * float(strength)
        return self._array_to_tensor(waveform_np)
