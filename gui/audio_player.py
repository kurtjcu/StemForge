"""Centralized audio playback serialization for StemForge.

All PortAudio operations (sounddevice.play / sounddevice.stop) go through
this module so they are serialized under a single lock and always run on
daemon threads — never blocking the DearPyGUI render thread.
"""

import logging
import threading

import sounddevice as sd

log = logging.getLogger("stemforge.gui.audio_player")

_lock = threading.Lock()


def audio_play(audio, samplerate: int) -> None:
    """Stop any current playback and start *audio* — non-blocking.

    Runs sd.stop() + sd.play() atomically under the module lock on a
    daemon thread so the caller (often the main render thread) never blocks.
    """

    def _do():
        with _lock:
            try:
                sd.stop()
                sd.play(audio, samplerate=samplerate)
            except Exception as exc:
                log.error("audio_play error: %s", exc)

    threading.Thread(target=_do, daemon=True).start()


def audio_stop() -> None:
    """Stop playback — non-blocking.

    Runs sd.stop() under the module lock on a daemon thread.
    """

    def _do():
        with _lock:
            try:
                sd.stop()
            except Exception as exc:
                log.error("audio_stop error: %s", exc)

    threading.Thread(target=_do, daemon=True).start()
