"""Temporal Convolutional Network for audio effects modelling.

Vendored from https://github.com/csteinmetz1/micro-tcn (Apache 2.0)
Copyright 2022 Christian J. Steinmetz

Stripped to inference-only: no pytorch_lightning, no training loops.
The pretrained LA-2A compressor checkpoint uses nparams=2 (peak_reduction, gain),
nblocks=4, kernel_size=5, dilation_growth=10, channel_width=32, causal=True.
"""

from __future__ import annotations

import torch
import torch.nn as nn


def center_crop(x: torch.Tensor, length: int) -> torch.Tensor:
    start = (x.shape[-1] - length) // 2
    stop = start + length
    return x[..., start:stop]


def causal_crop(x: torch.Tensor, length: int) -> torch.Tensor:
    stop = x.shape[-1] - 1
    start = stop - length
    return x[..., start:stop]


class FiLM(nn.Module):
    def __init__(self, num_features: int, cond_dim: int) -> None:
        super().__init__()
        self.num_features = num_features
        self.bn = nn.BatchNorm1d(num_features, affine=False)
        self.adaptor = nn.Linear(cond_dim, num_features * 2)

    def forward(self, x: torch.Tensor, cond: torch.Tensor) -> torch.Tensor:
        cond = self.adaptor(cond)
        g, b = torch.chunk(cond, 2, dim=-1)
        g = g.permute(0, 2, 1)
        b = b.permute(0, 2, 1)
        x = self.bn(x)
        x = (x * g) + b
        return x


class TCNBlock(nn.Module):
    def __init__(
        self,
        in_ch: int,
        out_ch: int,
        kernel_size: int = 3,
        dilation: int = 1,
        grouped: bool = False,
        causal: bool = False,
        conditional: bool = False,
    ) -> None:
        super().__init__()
        self.causal = causal

        groups = out_ch if grouped and (in_ch % out_ch == 0) else 1

        self.conv1 = nn.Conv1d(
            in_ch, out_ch,
            kernel_size=kernel_size,
            padding=0,
            dilation=dilation,
            groups=groups,
            bias=False,
        )
        if grouped:
            self.conv1b = nn.Conv1d(out_ch, out_ch, kernel_size=1)

        if conditional:
            self.film = FiLM(out_ch, 32)
        else:
            self.bn = nn.BatchNorm1d(out_ch)

        self.relu = nn.PReLU(out_ch)
        self.res = nn.Conv1d(in_ch, out_ch, kernel_size=1, groups=in_ch, bias=False)

    def forward(self, x: torch.Tensor, cond: torch.Tensor) -> torch.Tensor:
        x_in = x
        x = self.conv1(x)
        if hasattr(self, "film"):
            x = self.film(x, cond)
        else:
            x = self.bn(x)
        x = self.relu(x)

        x_res = self.res(x_in)
        if self.causal:
            x = x + causal_crop(x_res, x.shape[-1])
        else:
            x = x + center_crop(x_res, x.shape[-1])
        return x


class TCNModel(nn.Module):
    """Inference-only TCN for learned audio effects (e.g. LA-2A compressor).

    Parameters
    ----------
    nparams : int
        Number of conditioning parameters (2 for LA-2A: peak_reduction, gain).
    ninputs, noutputs : int
        Audio channels in/out (1 for mono).
    nblocks : int
        Number of dilated TCN blocks.
    kernel_size : int
        Convolution kernel size.
    dilation_growth : int
        Dilation multiplier per block.
    channel_width : int
        Number of channels per block (when channel_growth == 1).
    channel_growth : int
        Per-block channel multiplier (1 = fixed width).
    stack_size : int
        Dilation cycle length.
    causal : bool
        Use causal padding/cropping.
    grouped : bool
        Use depthwise-separable convolutions.
    """

    def __init__(
        self,
        nparams: int,
        ninputs: int = 1,
        noutputs: int = 1,
        nblocks: int = 10,
        kernel_size: int = 3,
        dilation_growth: int = 1,
        channel_growth: int = 1,
        channel_width: int = 32,
        stack_size: int = 10,
        grouped: bool = False,
        causal: bool = False,
        **kwargs,
    ) -> None:
        super().__init__()
        self.nparams = nparams
        self.causal = causal

        if nparams > 0:
            self.gen = nn.Sequential(
                nn.Linear(nparams, 16),
                nn.ReLU(),
                nn.Linear(16, 32),
                nn.ReLU(),
                nn.Linear(32, 32),
                nn.ReLU(),
            )

        self.blocks = nn.ModuleList()
        out_ch = ninputs
        for n in range(nblocks):
            in_ch = out_ch
            if channel_growth > 1:
                out_ch = in_ch * channel_growth
            else:
                out_ch = channel_width

            dilation = dilation_growth ** (n % stack_size)
            self.blocks.append(
                TCNBlock(
                    in_ch, out_ch,
                    kernel_size=kernel_size,
                    dilation=dilation,
                    causal=causal,
                    grouped=grouped,
                    conditional=nparams > 0,
                )
            )

        self.output = nn.Conv1d(out_ch, noutputs, kernel_size=1)

    def forward(self, x: torch.Tensor, params: torch.Tensor) -> torch.Tensor:
        cond = self.gen(params) if self.nparams > 0 else None
        for block in self.blocks:
            x = block(x, cond)
        return torch.tanh(self.output(x))

    def compute_receptive_field(self) -> int:
        """Return the receptive field size in samples."""
        # Reconstruct hparams from the model structure
        nblocks = len(self.blocks)
        kernel_size = self.blocks[0].conv1.kernel_size[0]
        dilation_growth = 1
        stack_size = nblocks
        if nblocks > 1:
            d0 = self.blocks[0].conv1.dilation[0]
            d1 = self.blocks[1].conv1.dilation[0]
            if d0 > 0:
                dilation_growth = d1 // d0 if d0 != 0 else 1
            stack_size = nblocks

        rf = kernel_size
        for n in range(1, nblocks):
            dilation = dilation_growth ** (n % stack_size)
            rf += (kernel_size - 1) * dilation
        return rf
