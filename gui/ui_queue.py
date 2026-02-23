"""Thread-safe UI update queue for StemForge.

DearPyGUI is NOT thread-safe — widget manipulation must happen on the
main thread only.  Background threads (pipeline workers, audio loaders)
must never call ``dpg.*`` directly.

Instead, they call :func:`schedule_ui` to enqueue a callable, and the
main render loop calls :func:`flush_ui` once per frame to execute all
pending callbacks on the main thread.

Usage (background thread)::

    from gui.ui_queue import schedule_ui
    schedule_ui(lambda: dpg.set_value("my_tag", 42))

Usage (main render loop in app.py)::

    from gui.ui_queue import flush_ui
    while dpg.is_dearpygui_running():
        flush_ui()
        dpg.render_dearpygui_frame()
"""

import collections
import logging
import threading
from typing import Callable

log = logging.getLogger("stemforge.gui.ui_queue")

_queue: collections.deque[Callable[[], None]] = collections.deque()
_lock = threading.Lock()


def schedule_ui(callback: Callable[[], None]) -> None:
    """Enqueue *callback* for execution on the main (render) thread.

    Safe to call from any thread.  The callback will run during the
    next :func:`flush_ui` call in the render loop.
    """
    with _lock:
        _queue.append(callback)


def flush_ui() -> None:
    """Execute all pending UI callbacks.  Call once per frame on the main thread."""
    with _lock:
        batch = list(_queue)
        _queue.clear()
    for cb in batch:
        try:
            cb()
        except Exception:
            log.exception("UI queue callback failed")
