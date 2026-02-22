"""WSL detection and PulseAudio routing for StemForge.

Call :func:`configure_audio` once at startup (before any sounddevice call).
It is a no-op on native Linux; on WSL it sets PULSE_SERVER and returns a
human-readable status string suitable for logging.

Detection
---------
WSL is identified by the presence of "microsoft" in ``/proc/version``.
This covers both WSL 1 and WSL 2, and all current Windows versions.

Audio routing
-------------
* **WSLg (Windows 11 / recent Windows 10)** — the WSLg PulseAudio socket is
  discovered at ``/mnt/wslg/runtime-dir/pulse/native`` and PULSE_SERVER is
  set automatically.
* **Existing PULSE_SERVER** — if the user has already exported PULSE_SERVER
  (e.g. a TCP address for a remote PulseAudio server on Windows 10), it is
  left untouched.
* **No socket, no env var** — a warning is logged; audio will likely fail
  until the user configures PulseAudio manually (see README).
"""

import logging
import os
import pathlib

log = logging.getLogger("stemforge.utils.wsl")

# WSLg ships a local PulseAudio socket at this well-known path.
_WSLG_SOCKET = pathlib.Path("/mnt/wslg/runtime-dir/pulse/native")

# The env var PortAudio / libpulse read to locate the PulseAudio server.
_PULSE_SERVER_VAR = "PULSE_SERVER"


def is_wsl() -> bool:
    """Return ``True`` when running inside Windows Subsystem for Linux.

    Reads ``/proc/version`` once and checks for the ``microsoft`` token that
    all WSL variants (1 and 2) include in their kernel version string.
    Returns ``False`` on any OS where ``/proc/version`` does not exist.
    """
    try:
        return "microsoft" in pathlib.Path("/proc/version").read_text().lower()
    except OSError:
        return False


def configure_audio() -> str | None:
    """Configure the process environment for audio playback.

    Must be called before the first ``sounddevice.play()`` call so that
    PortAudio picks up the correct PulseAudio server address.

    Returns
    -------
    str | None
        A human-readable status message, or ``None`` when not running on WSL
        (no action needed on native Linux).
    """
    if not is_wsl():
        return None

    existing = os.environ.get(_PULSE_SERVER_VAR)
    if existing:
        log.info("WSL detected — PULSE_SERVER already set: %r", existing)
        return f"WSL audio: PULSE_SERVER={existing!r} (user-configured)"

    if _WSLG_SOCKET.exists():
        uri = f"unix:{_WSLG_SOCKET}"
        os.environ[_PULSE_SERVER_VAR] = uri
        log.info("WSL detected — using WSLg PulseAudio socket: %s", uri)
        return f"WSL audio: WSLg socket ({_WSLG_SOCKET})"

    log.warning(
        "WSL detected but no PulseAudio socket found at %s and "
        "PULSE_SERVER is not set.  Audio playback will not work until "
        "PulseAudio is configured (see README).",
        _WSLG_SOCKET,
    )
    return "WSL audio: no PulseAudio socket found — audio disabled (see README)"
