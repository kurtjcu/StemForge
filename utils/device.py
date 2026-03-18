"""Platform-aware PyTorch device selection and GPU enumeration."""

from __future__ import annotations

import sys
from dataclasses import dataclass

import torch


@dataclass(frozen=True)
class GpuInfo:
    """Snapshot of a single CUDA GPU."""

    index: int
    name: str
    total_vram_mb: int
    free_vram_mb: int


def enumerate_gpus() -> list[GpuInfo]:
    """Return info for all visible CUDA GPUs. Empty list if no CUDA."""
    if not torch.cuda.is_available():
        return []
    gpus: list[GpuInfo] = []
    for i in range(torch.cuda.device_count()):
        name = torch.cuda.get_device_name(i)
        free, total = torch.cuda.mem_get_info(i)
        gpus.append(GpuInfo(
            index=i,
            name=name,
            total_vram_mb=total >> 20,
            free_vram_mb=free >> 20,
        ))
    return gpus


def get_device(gpu_index: int | None = None) -> torch.device:
    """Return the best available torch device.

    If *gpu_index* is given and CUDA is available, returns ``cuda:N``.
    Otherwise falls back to CUDA (default GPU) > MPS > CPU.
    """
    if torch.cuda.is_available():
        if gpu_index is not None:
            return torch.device(f"cuda:{gpu_index}")
        return torch.device("cuda")
    if sys.platform == "darwin" and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def is_mps() -> bool:
    """Return True if the best available device is MPS (Apple Silicon)."""
    return get_device().type == "mps"
