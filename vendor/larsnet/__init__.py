"""LarsNet drum sub-separation — vendored from polimi-ispl/larsnet.

Source: https://github.com/polimi-ispl/larsnet
License: CC BY-NC 4.0 (weights only; code is MIT)

Notes
-----
The upstream source (larsnet.py) uses a flat ``from unet import ...`` style
that requires the package directory itself to be on sys.path. We add it here
so the upstream files remain unmodified.
"""
import os
import sys

_pkg_dir = os.path.dirname(os.path.abspath(__file__))
if _pkg_dir not in sys.path:
    sys.path.insert(0, _pkg_dir)

from .larsnet import LarsNet  # noqa: E402

__all__ = ["LarsNet"]
