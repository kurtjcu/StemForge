"""
Logging configuration for StemForge.

Sets up a consistent logging hierarchy for all StemForge modules.  A
rotating file handler keeps disk usage bounded while a coloured console
handler aids readability during development.  Both handlers are attached
to the root ``stemforge`` logger so that all child loggers inherit the
configuration automatically.
"""

import os
import logging
import logging.handlers
import pathlib
import sys


LOG_DIR: pathlib.Path = pathlib.Path.home() / ".local" / "share" / "stemforge" / "logs"
LOG_FILE: pathlib.Path = LOG_DIR / "stemforge.log"
MAX_BYTES: int = 10 * 1024 * 1024   # 10 MiB per file
BACKUP_COUNT: int = 5


def configure_logging(
    level: int = logging.INFO,
    log_file: pathlib.Path = LOG_FILE,
    enable_console: bool = True,
) -> logging.Logger:
    """Configure and return the root ``stemforge`` logger.

    Creates *log_file*'s parent directory if it does not exist, attaches a
    :class:`~logging.handlers.RotatingFileHandler`, and optionally attaches
    a :class:`~logging.StreamHandler` to ``sys.stderr``.

    Parameters
    ----------
    level:
        Minimum severity level for both handlers (e.g. ``logging.DEBUG``).
    log_file:
        Path to the rotating log file.
    enable_console:
        When *True*, also emit log records to ``sys.stderr``.

    Returns
    -------
    logging.Logger
        Configured ``stemforge`` root logger.
    """
    root = logging.getLogger("stemforge")

    # Avoid adding duplicate handlers if called more than once
    if root.handlers:
        return root

    root.setLevel(level)

    file_fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(file_fmt)
        root.addHandler(fh)
    except OSError:
        # If the log directory can't be created (e.g. read-only fs in tests),
        # carry on without the file handler rather than crashing.
        pass

    # Console handler (stderr) — compact format, coloured level name
    if enable_console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(level)
        ch.setFormatter(_ColouredFormatter(
            fmt="%(levelname)-8s  %(name)s  %(message)s",
        ))
        root.addHandler(ch)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger of the ``stemforge`` hierarchy.

    Parameters
    ----------
    name:
        Dotted module name, e.g. ``'stemforge.pipelines.demucs'``.
        The ``stemforge.`` prefix is prepended automatically if absent.
    """
    if not name.startswith("stemforge."):
        name = f"stemforge.{name}"
    return logging.getLogger(name)


class _ColouredFormatter(logging.Formatter):
    """Formatter that adds ANSI colour codes to log level names on capable terminals."""

    LEVEL_COLOURS: dict[int, str] = {
        logging.DEBUG: "\033[36m",     # cyan
        logging.INFO: "\033[32m",      # green
        logging.WARNING: "\033[33m",   # yellow
        logging.ERROR: "\033[31m",     # red
        logging.CRITICAL: "\033[35m",  # magenta
    }
    RESET: str = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        if self._supports_colour():
            colour = self.LEVEL_COLOURS.get(record.levelno, "")
            record = logging.makeLogRecord(record.__dict__)
            record.levelname = f"{colour}{record.levelname}{self.RESET}"
        return super().format(record)

    def _supports_colour(self) -> bool:
        """Return *True* when the current stderr supports ANSI escape codes."""
        # Respect the NO_COLOR convention (https://no-color.org)
        if os.environ.get("NO_COLOR"):
            return False
        stream = getattr(self, "stream", sys.stderr)
        return hasattr(stream, "isatty") and stream.isatty()
