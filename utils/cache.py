"""Centralised model-cache directory resolution.

Every model loader should call :func:`get_model_cache_dir` instead of
hard-coding ``~/.cache/stemforge/…``.  The base directory can be
overridden by setting the **MODEL_LOCATION** environment variable
(or passing ``--model-dir`` to the launcher), which lets multiple users
share a single download cache on the same machine.
"""

import os
import pathlib


def get_model_cache_base() -> pathlib.Path:
    """Return the root model-cache directory.

    Resolution order:
    1. ``MODEL_LOCATION`` environment variable (if set and non-empty).
    2. ``~/.cache/stemforge/`` (default).
    """
    env = os.environ.get("MODEL_LOCATION", "").strip()
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".cache" / "stemforge"


def get_model_cache_dir(subdir: str) -> pathlib.Path:
    """Return ``<base>/<subdir>``, creating it if necessary."""
    path = get_model_cache_base() / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path
