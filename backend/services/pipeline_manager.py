"""Lazy-loaded pipeline singletons with GPU memory management.

Each pipeline is instantiated on first use and cached.  A global lock
prevents concurrent model loads (which would race for GPU memory).

The ``gpu_lock`` serialises GPU pipeline execution across all users.
Background worker threads must hold it for their entire
configure → load → run → evict cycle.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from typing import Any, Generator

log = logging.getLogger("stemforge.pipeline_manager")

_lock = threading.Lock()
_pipelines: dict[str, Any] = {}

# Serialise GPU pipeline execution — held by background job threads
# for the full configure → load → run → evict cycle so that two users
# cannot race for GPU memory.
_gpu_lock = threading.Lock()


@contextlib.contextmanager
def gpu_session() -> Generator[None, None, None]:
    """Context manager that serialises GPU pipeline execution.

    Usage in a background worker::

        with pipeline_manager.gpu_session():
            pipeline = pipeline_manager.get_demucs()
            pipeline.configure(cfg)
            pipeline.load_model()
            result = pipeline.run(path)
            pipeline_manager.evict("demucs")
    """
    _gpu_lock.acquire()
    try:
        yield
    finally:
        _gpu_lock.release()


def _get_or_create(name: str) -> Any:
    """Return cached pipeline instance, creating it on first call."""
    if name in _pipelines:
        return _pipelines[name]

    with _lock:
        # Double-check after acquiring lock
        if name in _pipelines:
            return _pipelines[name]

        log.info("Creating pipeline: %s", name)

        if name == "demucs":
            from pipelines.demucs_pipeline import DemucsPipeline
            _pipelines[name] = DemucsPipeline()

        elif name == "roformer":
            from pipelines.roformer_pipeline import RoformerPipeline
            _pipelines[name] = RoformerPipeline()

        elif name == "midi":
            from pipelines.midi_pipeline import MidiPipeline
            _pipelines[name] = MidiPipeline()

        elif name == "musicgen":
            from pipelines.musicgen_pipeline import MusicGenPipeline
            _pipelines[name] = MusicGenPipeline()

        elif name == "rvc":
            from pipelines.rvc_pipeline import RvcPipeline
            _pipelines[name] = RvcPipeline()

        elif name == "enhance":
            from pipelines.enhance_pipeline import EnhancePipeline
            _pipelines[name] = EnhancePipeline()

        elif name == "autotune":
            from pipelines.autotune_pipeline import AutotunePipeline
            _pipelines[name] = AutotunePipeline()

        elif name == "effects":
            from pipelines.effects_pipeline import EffectsPipeline
            _pipelines[name] = EffectsPipeline()

        else:
            raise ValueError(f"Unknown pipeline: {name!r}")

        return _pipelines[name]


def get_demucs() -> Any:
    return _get_or_create("demucs")


def get_roformer() -> Any:
    return _get_or_create("roformer")


def get_midi() -> Any:
    return _get_or_create("midi")


def get_musicgen() -> Any:
    return _get_or_create("musicgen")


def get_rvc() -> Any:
    return _get_or_create("rvc")


def get_enhance() -> Any:
    return _get_or_create("enhance")


def get_autotune() -> Any:
    return _get_or_create("autotune")


def get_effects() -> Any:
    return _get_or_create("effects")


def evict(name: str) -> None:
    """Release GPU memory for the named pipeline."""
    with _lock:
        pipeline = _pipelines.pop(name, None)
        if pipeline:
            try:
                pipeline.clear()
                log.info("Evicted pipeline: %s", name)
            except Exception:
                log.exception("Error evicting pipeline %s", name)
    # Return CUDA memory to the allocator even if pipeline wasn't found
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
