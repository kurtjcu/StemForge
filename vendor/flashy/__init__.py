# Minimal Flashy package for Audiocraft compatibility

from . import distrib, adversarial, utils, nn, state

__version__ = "0.0.3a"


class BaseSolver:
    """
    Minimal stub for flashy.BaseSolver.
    Audiocraft only checks for its existence.
    """
    pass

class Formatter:
    """
    Minimal stub for flashy.Formatter.
    Audiocraft only checks for its existence.
    """
    pass
