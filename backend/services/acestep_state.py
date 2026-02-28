"""Thread-safe AceStep subprocess status, shared between run.py and the compose router."""

import threading
from typing import Any

_lock = threading.Lock()
_state: dict[str, Any] = {"status": "disabled", "port": 8001, "exit_code": None, "error": None}

# status: disabled | starting | running | crashed


def get_status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def set_status(status: str, **kwargs: Any) -> None:
    with _lock:
        _state["status"] = status
        _state.update(kwargs)


def get_port() -> int:
    with _lock:
        return _state["port"]
