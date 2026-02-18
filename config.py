"""
StemForge application configuration.

Centralises all tuneable settings — model cache locations, sample rates,
output directories, and environment variable overrides — in one place so
that no magic constants are scattered across the codebase.

Loading order (highest priority wins)
--------------------------------------
1. Explicit keyword arguments passed to :class:`StemForgeConfig`.
2. Environment variables (see *Environment variables* section below).
3. Values in a ``stemforge.toml`` file in the current working directory.
4. Built-in defaults defined in this module.

Environment variables
---------------------
STEMFORGE_CACHE_DIR
    Root directory for all downloaded model weights.
    Overrides :data:`DEFAULT_CACHE_DIR`.
STEMFORGE_OUTPUT_DIR
    Default directory for all pipeline output files.
    Overrides :data:`DEFAULT_OUTPUT_DIR`.
STEMFORGE_DEMUCS_SAMPLE_RATE
    Target sample rate for Demucs output stems in Hz.
    Overrides :data:`DEFAULT_DEMUCS_SAMPLE_RATE`.
STEMFORGE_BASICPITCH_SAMPLE_RATE
    Internal sample rate used by the BasicPitch model in Hz.
    Overrides :data:`DEFAULT_BASICPITCH_SAMPLE_RATE`.
STEMFORGE_MUSICGEN_SAMPLE_RATE
    Sample rate of MusicGen-generated audio in Hz.
    Overrides :data:`DEFAULT_MUSICGEN_SAMPLE_RATE`.
STEMFORGE_LOG_LEVEL
    Logging verbosity (e.g. ``DEBUG``, ``INFO``, ``WARNING``).
    Overrides :data:`DEFAULT_LOG_LEVEL`.
"""

import os
import pathlib
import logging
from typing import Any

from utils.errors import InvalidInputError


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

DEFAULT_CACHE_DIR: pathlib.Path = pathlib.Path.home() / ".cache" / "stemforge"
"""Root cache directory.  Sub-directories for each model are derived from this."""

DEFAULT_DEMUCS_CACHE_DIR: pathlib.Path = DEFAULT_CACHE_DIR / "demucs"
"""Local directory where Demucs checkpoint files are stored."""

DEFAULT_BASICPITCH_CACHE_DIR: pathlib.Path = DEFAULT_CACHE_DIR / "basicpitch"
"""Local directory where BasicPitch model files are stored."""

DEFAULT_MUSICGEN_CACHE_DIR: pathlib.Path = DEFAULT_CACHE_DIR / "musicgen"
"""Local directory where MusicGen transformer and codec checkpoints are stored."""

DEFAULT_OUTPUT_DIR: pathlib.Path = pathlib.Path.home() / "stemforge_output"
"""Root output directory.  Pipeline-specific sub-directories are derived from this."""

DEFAULT_DEMUCS_OUTPUT_DIR: pathlib.Path = DEFAULT_OUTPUT_DIR / "stems"
"""Directory where Demucs writes separated stem WAV files."""

DEFAULT_BASICPITCH_OUTPUT_DIR: pathlib.Path = DEFAULT_OUTPUT_DIR / "midi"
"""Directory where BasicPitch writes transcribed MIDI files."""

DEFAULT_MUSICGEN_OUTPUT_DIR: pathlib.Path = DEFAULT_OUTPUT_DIR / "generated"
"""Directory where MusicGen writes generated audio files."""


# ---------------------------------------------------------------------------
# Default sample rates (Hz)
# ---------------------------------------------------------------------------

DEFAULT_DEMUCS_SAMPLE_RATE: int = 44_100
"""Sample rate at which HTDemucs and MDX models operate and output stems."""

DEFAULT_BASICPITCH_SAMPLE_RATE: int = 22_050
"""Sample rate required by the BasicPitch model for frame-level pitch inference."""

DEFAULT_MUSICGEN_SAMPLE_RATE: int = 32_000
"""Sample rate of audio produced by MusicGen via the EnCodec decoder."""


# ---------------------------------------------------------------------------
# Default model identifiers
# ---------------------------------------------------------------------------

DEFAULT_DEMUCS_MODEL: str = "htdemucs"
"""Default Demucs model variant used when no explicit model is selected."""

DEFAULT_BASICPITCH_MODEL_FORMAT: str = "onnx"
"""Preferred serialisation format for the BasicPitch model (``'onnx'`` or ``'savedmodel'``)."""

DEFAULT_MUSICGEN_MODEL: str = "facebook/musicgen-small"
"""Default MusicGen HuggingFace model identifier."""


# ---------------------------------------------------------------------------
# Miscellaneous defaults
# ---------------------------------------------------------------------------

DEFAULT_LOG_LEVEL: int = logging.INFO
"""Default logging verbosity level."""

DEFAULT_EXPORT_FORMAT: str = "wav"
"""Default audio export format used by the Export panel."""

DEFAULT_BIT_DEPTH: int = 16
"""Default PCM bit depth for WAV and FLAC export."""


# ---------------------------------------------------------------------------
# Environment variable helpers (stubs — no real logic)
# ---------------------------------------------------------------------------


def _env_path(var: str, default: pathlib.Path) -> pathlib.Path:
    """Return a :class:`pathlib.Path` read from environment variable *var*, or *default*.

    Parameters
    ----------
    var:
        Name of the environment variable to inspect (e.g.
        ``'STEMFORGE_CACHE_DIR'``).
    default:
        Path returned when *var* is absent from the environment or set to
        an empty string.

    Returns
    -------
    pathlib.Path
        Resolved path constructed from the environment value, or *default*
        if the variable is not set.
    """
    pass


def _env_int(var: str, default: int) -> int:
    """Return an integer read from environment variable *var*, or *default*.

    Parameters
    ----------
    var:
        Name of the environment variable to inspect.
    default:
        Value returned when *var* is absent, empty, or cannot be parsed as
        a base-10 integer.

    Returns
    -------
    int
        Parsed integer from the environment value, or *default*.
    """
    pass


def _env_str(var: str, default: str) -> str:
    """Return a non-empty string read from environment variable *var*, or *default*.

    Parameters
    ----------
    var:
        Name of the environment variable to inspect.
    default:
        Value returned when *var* is absent or set to an empty string.

    Returns
    -------
    str
        The environment variable value stripped of leading/trailing whitespace,
        or *default*.
    """
    pass


def _env_log_level(var: str, default: int) -> int:
    """Return a :mod:`logging` level integer from environment variable *var*.

    Accepts both numeric strings (e.g. ``'20'``) and canonical level-name
    strings (e.g. ``'INFO'``, ``'DEBUG'``).  Falls back to *default* when
    *var* is absent, empty, or cannot be mapped to a recognised log level.

    Parameters
    ----------
    var:
        Name of the environment variable to inspect.
    default:
        Logging level integer returned when *var* is not usable.

    Returns
    -------
    int
        A :mod:`logging` level constant such as :data:`logging.INFO`.
    """
    pass


# ---------------------------------------------------------------------------
# Configuration class
# ---------------------------------------------------------------------------


class StemForgeConfig:
    """Aggregate configuration for the entire StemForge application.

    Collects all tuneable settings into a single object so that pipelines,
    loaders, and GUI panels can receive a consistent, immutable view of the
    current configuration without reaching into global state or environment
    variables directly.

    Attribute groups
    ----------------
    *Paths*
        ``cache_dir``, ``demucs_cache_dir``, ``basicpitch_cache_dir``,
        ``musicgen_cache_dir``, ``output_dir``, ``demucs_output_dir``,
        ``basicpitch_output_dir``, ``musicgen_output_dir``

    *Sample rates*
        ``demucs_sample_rate``, ``basicpitch_sample_rate``,
        ``musicgen_sample_rate``

    *Model identifiers*
        ``demucs_model``, ``basicpitch_model_format``, ``musicgen_model``

    *Export*
        ``export_format``, ``bit_depth``

    *Logging*
        ``log_level``

    Parameters
    ----------
    All constructor parameters are optional.  When omitted, the attribute
    is resolved in priority order: environment variable → built-in default.
    """

    # Paths
    cache_dir: pathlib.Path
    demucs_cache_dir: pathlib.Path
    basicpitch_cache_dir: pathlib.Path
    musicgen_cache_dir: pathlib.Path
    output_dir: pathlib.Path
    demucs_output_dir: pathlib.Path
    basicpitch_output_dir: pathlib.Path
    musicgen_output_dir: pathlib.Path

    # Sample rates
    demucs_sample_rate: int
    basicpitch_sample_rate: int
    musicgen_sample_rate: int

    # Model identifiers
    demucs_model: str
    basicpitch_model_format: str
    musicgen_model: str

    # Export
    export_format: str
    bit_depth: int

    # Logging
    log_level: int

    def __init__(
        self,
        cache_dir: pathlib.Path | None = None,
        demucs_cache_dir: pathlib.Path | None = None,
        basicpitch_cache_dir: pathlib.Path | None = None,
        musicgen_cache_dir: pathlib.Path | None = None,
        output_dir: pathlib.Path | None = None,
        demucs_output_dir: pathlib.Path | None = None,
        basicpitch_output_dir: pathlib.Path | None = None,
        musicgen_output_dir: pathlib.Path | None = None,
        demucs_sample_rate: int | None = None,
        basicpitch_sample_rate: int | None = None,
        musicgen_sample_rate: int | None = None,
        demucs_model: str | None = None,
        basicpitch_model_format: str | None = None,
        musicgen_model: str | None = None,
        export_format: str | None = None,
        bit_depth: int | None = None,
        log_level: int | None = None,
    ) -> None:
        """Initialise configuration by merging explicit arguments, environment
        variables, and built-in defaults.

        Explicit keyword arguments always win.  For any argument left as
        ``None``, the corresponding ``STEMFORGE_*`` environment variable is
        consulted; if that is also absent the module-level default is used.
        """
        pass

    # ------------------------------------------------------------------
    # Alternative constructors
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "StemForgeConfig":
        """Construct a :class:`StemForgeConfig` populated entirely from environment variables.

        Any environment variable that is absent or empty falls back to the
        corresponding built-in default.  Equivalent to calling
        ``StemForgeConfig()`` with no arguments.

        Returns
        -------
        StemForgeConfig
            Configuration instance reflecting the current process environment.
        """
        pass

    @classmethod
    def from_file(cls, path: pathlib.Path) -> "StemForgeConfig":
        """Load configuration from a TOML file at *path*, then merge with environment and defaults.

        Values present in the file take precedence over environment variables.
        Missing keys fall back to environment variables, then built-in defaults.

        Parameters
        ----------
        path:
            Path to a ``stemforge.toml`` configuration file.

        Returns
        -------
        StemForgeConfig
            Configuration instance reflecting the merged settings.

        Raises
        ------
        FileNotFoundError
            If *path* does not exist on disk.
        ValueError
            If *path* is not a valid TOML file or contains unrecognised keys.
        """
        pass

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict snapshot of the current configuration.

        Path values are converted to strings so that the result is directly
        serialisable to JSON or TOML without further transformation.

        Returns
        -------
        dict[str, Any]
            Mapping of attribute name to current value.
        """
        pass

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> None:
        """Check that all settings are internally consistent.

        Performs the following checks (without modifying any state):

        * All sample rates are positive integers.
        * ``bit_depth`` is one of ``8``, ``16``, ``24``, or ``32``.
        * ``export_format`` is one of ``'wav'``, ``'flac'``, ``'mp3'``, ``'ogg'``.
        * ``basicpitch_model_format`` is one of ``'onnx'`` or ``'savedmodel'``.
        * ``demucs_model`` is a non-empty string.
        * ``musicgen_model`` is a non-empty string.
        * ``log_level`` is a recognised :mod:`logging` level integer.

        Raises
        ------
        :class:`~utils.errors.InvalidInputError`
            If any setting fails its validation check.  The ``field``
            attribute of the exception names the offending attribute.
        """
        pass

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """Return a developer-friendly string representation of the configuration."""
        pass
