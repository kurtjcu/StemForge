"""
MIDI model loader for StemForge.

Wraps the BasicPitch audio-to-MIDI model and provides a high-level
``convert_audio_to_midi()`` facade that handles model loading, inference,
duration clipping, and optional key filtering in a single call.

Unlike the other loaders, this one intentionally stays slightly above the
raw-model level: it owns the BasicPitchModelLoader as a delegate and
encapsulates the BasicPitch inference protocol so the pipeline layer never
has to import ``basic_pitch.inference`` directly.

GPU / CPU note
--------------
BasicPitch runs on CPU only (the loader sets ``CUDA_VISIBLE_DEVICES=-1``
before TF is imported via ``BasicPitchModelLoader``).  The MIDI pipeline
is therefore purely CPU-bound; Demucs/MusicGen GPU memory is unaffected.
"""

import pathlib
import logging
from typing import Any

from models.basicpitch_loader import BasicPitchModelLoader
from models.registry import DEFAULT_WHISPER_SPEC
from utils.cache import get_model_cache_dir
from utils.midi_io import NoteEvent, LyricEvent, filter_to_key
from utils.errors import ModelLoadError, PipelineExecutionError


log = logging.getLogger("stemforge.models.midi_loader")


class MidiModelLoader:
    """Facade that converts audio files to MIDI note events.

    Exposes two conversion paths:

    * :meth:`convert_audio_to_midi` — uses BasicPitch (good for instruments).
    * :meth:`convert_vocal_to_midi` — uses faster-whisper for word timing
      and librosa PYIN for pitch estimation (better for sung vocals).

    Lifecycle mirrors all other StemForge loaders::

        loader = MidiModelLoader()
        loader.load()
        notes = loader.convert_audio_to_midi(path, key="C major", bpm=120.0)
        loader.evict()

    Parameters
    ----------
    (none — delegates cache configuration to BasicPitchModelLoader defaults)
    """

    def __init__(self) -> None:
        self._bp_loader = BasicPitchModelLoader()
        self._model: Any | None = None          # BasicPitch TF model
        self._whisper_model: Any | None = None  # faster-whisper WhisperModel
        self._adtof_backend: Any | None = None  # AdtofBackend (lazy-loaded)
        self._larsnet_backend: Any | None = None  # LarsNetBackend (lazy-loaded)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def load(self) -> Any:
        """Load the BasicPitch TF SavedModel.  Returns the model object.

        Idempotent: if already loaded, returns the cached instance
        without re-loading.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If TensorFlow or basic-pitch are not installed, or the
            SavedModel cannot be deserialised.
        """
        if self._model is not None:
            return self._model
        try:
            self._model = self._bp_loader.load()
        except ModelLoadError:
            raise
        except Exception as exc:
            raise ModelLoadError(
                f"Unexpected error loading MIDI model: {exc}",
                model_name="basicpitch",
            ) from exc
        log.info("MidiModelLoader: BasicPitch model ready (CPU-only).")
        return self._model

    @property
    def is_loaded(self) -> bool:
        """``True`` if the model is currently in memory."""
        return self._model is not None

    def evict(self) -> None:
        """Release all models from memory and trigger GC."""
        self._bp_loader.evict()
        self._model = None
        self._whisper_model = None
        self.evict_drum_model()
        self.evict_larsnet()
        log.debug("MidiModelLoader: models evicted.")

    # ------------------------------------------------------------------
    # Internal: ADTOF lazy loader
    # ------------------------------------------------------------------

    def _ensure_adtof(self) -> Any:
        """Load the ADTOF drum transcription backend on first use."""
        if self._adtof_backend is not None:
            return self._adtof_backend
        try:
            from pipelines.adtof_backend import AdtofBackend
        except ImportError as exc:
            raise ModelLoadError(
                "ADTOF backend is not available.",
                model_name="adtof-drums",
            ) from exc
        backend = AdtofBackend()
        backend.load()
        self._adtof_backend = backend
        log.info("MidiModelLoader: ADTOF drum backend ready.")
        return self._adtof_backend

    def evict_drum_model(self) -> None:
        """Evict the ADTOF backend only, leaving BasicPitch intact."""
        if self._adtof_backend is not None:
            self._adtof_backend.evict()
            self._adtof_backend = None
            log.debug("MidiModelLoader: ADTOF backend evicted.")

    # ------------------------------------------------------------------
    # Internal: LarsNet lazy loader
    # ------------------------------------------------------------------

    def _ensure_larsnet(self) -> Any:
        """Load the LarsNet drum sub-separation backend on first use.

        Idempotent: second call returns the cached backend without re-loading.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If pipelines.larsnet_backend is not importable or weights are missing.
        """
        if self._larsnet_backend is not None:
            return self._larsnet_backend
        import time
        try:
            from pipelines.larsnet_backend import LarsNetBackend
        except ImportError as exc:
            raise ModelLoadError(
                "LarsNet backend is not available.",
                model_name="larsnet-drums",
            ) from exc
        backend = LarsNetBackend()
        t0 = time.perf_counter()
        backend.load()
        elapsed = time.perf_counter() - t0
        self._larsnet_backend = backend
        log.info("MidiModelLoader: LarsNet backend ready in %.2fs.", elapsed)
        return self._larsnet_backend

    def evict_larsnet(self) -> None:
        """Evict the LarsNet backend only, leaving other models intact."""
        if self._larsnet_backend is not None:
            self._larsnet_backend.evict()
            self._larsnet_backend = None
            log.debug("MidiModelLoader: LarsNet backend evicted.")

    # ------------------------------------------------------------------
    # LarsNet drum sub-separation
    # ------------------------------------------------------------------

    def separate_drums(
        self,
        audio_tensor: "Any",  # torch.Tensor shape (2, n_samples), float32, 44100 Hz
        job_id: str,
    ) -> "dict[str, pathlib.Path]":
        """Separate drum stem into 5 sub-stems using LarsNet.

        Parameters
        ----------
        audio_tensor:
            Stereo drum audio tensor, shape (2, n_samples), float32 at 44100 Hz.
            Mono (1D) input is automatically expanded to stereo.
        job_id:
            Unique identifier for this separation job; used for the output directory.

        Returns
        -------
        dict[str, Path]
            Keys match LARSNET_STEM_KEYS; values are paths to written WAV files
            under ``STEMS_DIR / "drum_sub" / {job_id} /``.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If LarsNet weights are not found.
        :class:`~utils.errors.PipelineExecutionError`
            If LarsNet inference fails.
        """
        import soundfile as sf
        from models.registry import LARSNET_STEM_KEYS
        from utils.paths import STEMS_DIR

        # Normalise tensor shape to (2, n_samples)
        if audio_tensor.ndim == 1:
            audio_tensor = audio_tensor.unsqueeze(0).expand(2, -1)
        elif audio_tensor.shape[0] != 2:
            audio_tensor = audio_tensor.T  # (n_samples, 2) -> (2, n_samples)

        backend = self._ensure_larsnet()
        out_dir = STEMS_DIR / "drum_sub" / job_id
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            stems_tensors = backend._model(audio_tensor)
        except Exception as exc:
            raise PipelineExecutionError(
                f"LarsNet separation failed: {exc}",
                pipeline_name="larsnet",
            ) from exc

        result: dict[str, pathlib.Path] = {}
        for stem_name in LARSNET_STEM_KEYS:
            wav_path = out_dir / f"{stem_name}.wav"
            waveform = stems_tensors[stem_name].cpu().numpy().T  # (n_samples, 2)
            sf.write(str(wav_path), waveform, samplerate=44100)
            result[stem_name] = wav_path

        log.debug(
            "separate_drums: job_id=%s -> %d stems written to %s",
            job_id, len(result), out_dir,
        )
        return result

    def convert_drum_to_midi_with_larsnet(
        self,
        audio_tensor: "Any",  # torch.Tensor
        job_id: str,
        *,
        duration: float = 0.0,
    ) -> "tuple[dict[str, pathlib.Path], list]":
        """Run LarsNet+ADTOF drum MIDI mode.

        Orchestrates: separation → evict LarsNet → load ADTOF → transcribe.

        The LarsNet eviction before ADTOF load is the INFRA-03 VRAM safety
        contract. The loader enforces this sequence; the pipeline layer MUST
        NOT reorder it.

        Parameters
        ----------
        audio_tensor:
            Stereo drum audio tensor (2, n_samples), float32 at 44100 Hz.
        job_id:
            Unique job identifier used for sub-stem output directory.
        duration:
            If positive, clip MIDI events to this many seconds.

        Returns
        -------
        tuple[dict[str, Path], list[NoteEvent]]
            ``(sub_stems, events)`` — sub-stem paths and GM drum note events.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If LarsNet or ADTOF cannot be loaded.
        :class:`~utils.errors.PipelineExecutionError`
            If separation or transcription fails.
        """
        import tempfile
        import soundfile as sf

        # Step 1: Separate into sub-stems (loads LarsNet if needed)
        sub_stems = self.separate_drums(audio_tensor, job_id)

        # Step 2: VRAM safety — evict LarsNet before ADTOF loads (INFRA-03)
        self.evict_larsnet()

        # Step 3: Write audio_tensor to temp WAV for AdtofBackend.predict()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = pathlib.Path(tmp.name)
        sf.write(str(tmp_path), audio_tensor.cpu().numpy().T, samplerate=44100)

        try:
            events = self.convert_drum_to_midi(tmp_path, duration=duration)
        finally:
            tmp_path.unlink(missing_ok=True)

        return sub_stems, events

    # ------------------------------------------------------------------
    # Internal: Whisper lazy loader
    # ------------------------------------------------------------------

    def _ensure_whisper(self) -> Any:
        """Load the faster-whisper model on first use."""
        if self._whisper_model is not None:
            return self._whisper_model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ModelLoadError(
                "faster-whisper is not installed — cannot transcribe vocals.",
                model_name="faster-whisper",
            ) from exc
        spec = DEFAULT_WHISPER_SPEC
        log.info("Loading faster-whisper '%s' on CPU…", spec.model_size)
        self._whisper_model = WhisperModel(
            spec.model_size,
            device=spec.device,
            compute_type=spec.compute_type,
            download_root=str(get_model_cache_dir("whisper")),
        )
        log.info("faster-whisper model ready.")
        return self._whisper_model

    # ------------------------------------------------------------------
    # High-level conversion
    # ------------------------------------------------------------------

    def convert_audio_to_midi(
        self,
        path: pathlib.Path,
        *,
        prompt: str | None = None,
        duration: float = 0.0,
        key: str = "Any",
        signature: str = "4/4",
        bpm: float = 120.0,
        onset_threshold: float = 0.5,
        frame_threshold: float = 0.3,
        minimum_note_length: float = 58.0,
    ) -> list[NoteEvent]:
        """Transcribe *path* to a list of note events.

        Parameters
        ----------
        path:
            Audio file to transcribe.  Any format supported by
            ``utils.audio_io.SUPPORTED_EXTENSIONS``.
        prompt:
            Optional text description.  Currently logged but not used for
            conditioning; reserved for future text-guided post-processing.
        duration:
            If positive, clip note events to ``[0, duration)`` seconds.
            Pass ``0`` (default) to keep all detected notes.
        key:
            Musical key for pitch snapping, e.g. ``'C major'`` or
            ``'A minor'``.  ``'Any'`` skips key filtering entirely.
        signature:
            Time signature string (informational; not passed to BasicPitch).
        bpm:
            Tempo in BPM (informational; used when building the merged MIDI
            tempo map in the pipeline layer, not by this method).
        onset_threshold:
            BasicPitch onset confidence threshold in ``[0, 1]``.
        frame_threshold:
            BasicPitch frame confidence threshold in ``[0, 1]``.
        minimum_note_length:
            Minimum note duration in milliseconds.

        Returns
        -------
        list[NoteEvent]
            ``(start_sec, end_sec, pitch_midi, velocity)`` tuples,
            sorted by ``start_sec``.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If the model has not been loaded and cannot be loaded on demand.
        :class:`~utils.errors.PipelineExecutionError`
            If BasicPitch inference raises an unexpected error.
        """
        if self._model is None:
            self.load()

        if prompt:
            log.debug(
                "convert_audio_to_midi: text prompt received (%.60r) — "
                "reserved for future conditioning.",
                prompt,
            )

        try:
            note_events_raw = self._model.predict(
                path,
                onset_threshold=onset_threshold,
                frame_threshold=frame_threshold,
                minimum_note_length=minimum_note_length,
            )
        except PipelineExecutionError:
            raise
        except Exception as exc:
            raise PipelineExecutionError(
                f"Audio-to-MIDI transcription failed for '{path.name}': {exc}",
                pipeline_name="midi",
            ) from exc

        # Normalise BasicPitch raw output to NoteEvent tuples
        events: list[NoteEvent] = []
        for item in note_events_raw:
            start_t, end_t, pitch, amplitude, *_ = item
            velocity = max(1, min(127, int(float(amplitude) * 127)))
            events.append((float(start_t), float(end_t), int(pitch), velocity))
        events.sort(key=lambda e: e[0])

        # Optional duration clip
        if duration > 0.0:
            events = [
                (s, min(e, duration), p, v)
                for s, e, p, v in events
                if s < duration
            ]

        # Key snap: off-key pitches are moved to the nearest in-scale semitone
        if key and key != "Any":
            events = filter_to_key(events, key)

        log.debug(
            "convert_audio_to_midi: %s → %d notes (key=%r, onset=%.2f, frame=%.2f)",
            path.name, len(events), key, onset_threshold, frame_threshold,
        )
        return events

    def convert_vocal_to_midi(
        self,
        path: pathlib.Path,
        *,
        duration: float = 0.0,
        key: str = "Any",
        bpm: float = 120.0,
        language: str | None = None,
    ) -> tuple[list[NoteEvent], list[LyricEvent]]:
        """Transcribe a vocal stem to MIDI using faster-whisper + librosa PYIN.

        Word timing from faster-whisper is combined with probabilistic pitch
        estimation (librosa PYIN) to produce one note per word.  Pitches are
        snapped to *key* after estimation.

        Parameters
        ----------
        path:
            Vocal audio file (mono or stereo).
        duration:
            Clip output to this many seconds if positive.
        key:
            Musical key for pitch snapping (e.g. ``'A minor'``).
        bpm:
            Tempo (informational; used only in the pipeline's merge step).
        language:
            ISO-639-1 language code hint for Whisper (e.g. ``'en'``).
            ``None`` lets Whisper auto-detect.

        Returns
        -------
        tuple[list[NoteEvent], list[LyricEvent]]
            ``(notes, lyrics)`` — one note per transcribed word, and a
            parallel list of ``(time_seconds, word_text)`` lyric events
            suitable for embedding as MIDI type-0x05 meta messages.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If faster-whisper or librosa is not installed.
        :class:`~utils.errors.PipelineExecutionError`
            If transcription or pitch estimation fails.
        """
        try:
            import numpy as np
            import librosa
        except ImportError as exc:
            raise ModelLoadError(
                "librosa is required for vocal pitch estimation — is it installed?",
                model_name="librosa",
            ) from exc

        whisper = self._ensure_whisper()

        # Load audio at 16 kHz for Whisper and also at 22 050 Hz for PYIN.
        try:
            y_pyin, sr_pyin = librosa.load(str(path), sr=22_050, mono=True)
            y_whisper, _sr_w = librosa.load(str(path), sr=16_000, mono=True)
        except Exception as exc:
            raise PipelineExecutionError(
                f"Failed to load vocal audio '{path.name}': {exc}",
                pipeline_name="midi",
            ) from exc

        # Pre-compute PYIN pitch track for the whole file.
        try:
            f0, voiced_flag, _voiced_probs = librosa.pyin(
                y_pyin,
                fmin=librosa.note_to_hz("C2"),
                fmax=librosa.note_to_hz("C7"),
                sr=sr_pyin,
            )
        except Exception as exc:
            raise PipelineExecutionError(
                f"Pitch estimation failed for '{path.name}': {exc}",
                pipeline_name="midi",
            ) from exc

        frame_times = librosa.frames_to_time(
            range(len(f0)), sr=sr_pyin, hop_length=512
        )

        # Transcribe with faster-whisper to get word-level timestamps.
        try:
            segments_iter, _info = whisper.transcribe(
                y_whisper,
                language=language,
                word_timestamps=True,
                vad_filter=True,
            )
            segments = list(segments_iter)
        except Exception as exc:
            raise PipelineExecutionError(
                f"Whisper transcription failed for '{path.name}': {exc}",
                pipeline_name="midi",
            ) from exc

        # Build one NoteEvent + LyricEvent per voiced word.
        events: list[NoteEvent] = []
        lyrics: list[LyricEvent] = []
        for segment in segments:
            words = getattr(segment, "words", None) or []
            for word in words:
                start = float(word.start)
                end = float(word.end)
                if end <= start:
                    continue
                if duration > 0.0 and start >= duration:
                    continue

                # Find PYIN frames that fall within [start, end].
                mask = (frame_times >= start) & (frame_times < end) & voiced_flag
                voiced_f0 = f0[mask]

                if len(voiced_f0) == 0 or np.all(np.isnan(voiced_f0)):
                    # Unvoiced window — skip (rest / consonant).
                    continue

                median_hz = float(np.nanmedian(voiced_f0))
                if median_hz <= 0.0:
                    continue

                midi_pitch = int(round(librosa.hz_to_midi(median_hz)))
                midi_pitch = max(0, min(127, midi_pitch))

                clipped_end = min(end, duration) if duration > 0.0 else end
                velocity = min(127, max(1, int(abs(word.probability) * 100)))
                events.append((start, clipped_end, midi_pitch, velocity))

                # Strip leading/trailing punctuation but keep apostrophes.
                text = word.word.strip().strip('.,!?;:"()[]{}…—–')
                if text:
                    lyrics.append((start, text))

        events.sort(key=lambda e: e[0])
        lyrics.sort(key=lambda l: l[0])

        if key and key != "Any":
            events = filter_to_key(events, key)

        log.debug(
            "convert_vocal_to_midi: %s → %d notes, %d lyric events from %d segments",
            path.name, len(events), len(lyrics), len(segments),
        )
        return events, lyrics

    def convert_drum_to_midi(
        self,
        path: pathlib.Path,
        *,
        duration: float = 0.0,
    ) -> list[NoteEvent]:
        """Transcribe a drum stem to GM note events via ADTOF.

        Parameters
        ----------
        path:
            Drum audio file (must be 44100 Hz).
        duration:
            If positive, clip events to this many seconds.

        Returns
        -------
        list[NoteEvent]
            GM channel-10 note events from ADTOF_5CLASS_GM_NOTE.

        Raises
        ------
        :class:`~utils.errors.ModelLoadError`
            If the ADTOF backend cannot be loaded.
        :class:`~utils.errors.PipelineExecutionError`
            If drum transcription fails.
        """
        backend = self._ensure_adtof()
        try:
            events = backend.predict(path)
        except PipelineExecutionError:
            raise
        except Exception as exc:
            raise PipelineExecutionError(
                f"Drum transcription failed for '{path.name}': {exc}",
                pipeline_name="midi",
            ) from exc

        if duration > 0.0:
            events = [(s, min(e, duration), p, v) for s, e, p, v in events if s < duration]

        log.debug("convert_drum_to_midi: %s -> %d drum events", path.name, len(events))
        return events
