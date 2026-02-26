"""Platform-aware PyTorch device selection."""
import sys

import torch


def get_device() -> torch.device:
    """Return the best available torch device: CUDA > MPS > CPU."""
    if torch.cuda.is_available():
        return torch.device("cuda")
    if sys.platform == "darwin" and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def is_mps() -> bool:
    """Return True if the best available device is MPS (Apple Silicon)."""
    return get_device().type == "mps"
