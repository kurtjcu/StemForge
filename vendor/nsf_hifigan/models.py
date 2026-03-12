"""NSF-HiFiGAN Generator — inference only.

Vendored from DDSP-SVC (MIT license). Discriminator and loss classes
stripped since they are only needed for training.

Source: https://github.com/yxlllc/DDSP-SVC/blob/master/nsf_hifigan/models.py
"""

import json
import os

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn import Conv1d, ConvTranspose1d
from torch.nn.utils import weight_norm, remove_weight_norm

from .env import AttrDict
from .utils import init_weights, get_padding

LRELU_SLOPE = 0.1


def load_model(model_path, device='cuda'):
    """Load a pretrained NSF-HiFiGAN generator."""
    h = load_config(model_path)
    generator = Generator(h).to(device)
    cp_dict = torch.load(model_path, map_location=device, weights_only=True)
    generator.load_state_dict(cp_dict['generator'])
    generator.eval()
    generator.remove_weight_norm()
    del cp_dict
    return generator, h


def load_config(model_path):
    """Load config.json from the same directory as the model checkpoint."""
    config_file = os.path.join(os.path.split(model_path)[0], 'config.json')
    with open(config_file) as f:
        data = f.read()
    json_config = json.loads(data)
    h = AttrDict(json_config)
    return h


# ---------------------------------------------------------------------------
# Residual blocks
# ---------------------------------------------------------------------------

class ResBlock1(nn.Module):
    def __init__(self, h, channels, kernel_size=3, dilation=(1, 3, 5)):
        super().__init__()
        self.h = h
        self.convs1 = nn.ModuleList([
            weight_norm(Conv1d(
                channels, channels, kernel_size, 1,
                dilation=dilation[i],
                padding=get_padding(kernel_size, dilation[i]),
            ))
            for i in range(3)
        ])
        self.convs1.apply(init_weights)
        self.convs2 = nn.ModuleList([
            weight_norm(Conv1d(
                channels, channels, kernel_size, 1,
                dilation=1,
                padding=get_padding(kernel_size, 1),
            ))
            for _ in range(3)
        ])
        self.convs2.apply(init_weights)

    def forward(self, x):
        for c1, c2 in zip(self.convs1, self.convs2):
            xt = F.leaky_relu(x, LRELU_SLOPE)
            xt = c1(xt)
            xt = F.leaky_relu(xt, LRELU_SLOPE)
            xt = c2(xt)
            x = xt + x
        return x

    def remove_weight_norm(self):
        for l in self.convs1:
            remove_weight_norm(l)
        for l in self.convs2:
            remove_weight_norm(l)


class ResBlock2(nn.Module):
    def __init__(self, h, channels, kernel_size=3, dilation=(1, 3)):
        super().__init__()
        self.h = h
        self.convs = nn.ModuleList([
            weight_norm(Conv1d(
                channels, channels, kernel_size, 1,
                dilation=dilation[i],
                padding=get_padding(kernel_size, dilation[i]),
            ))
            for i in range(2)
        ])
        self.convs.apply(init_weights)

    def forward(self, x):
        for c in self.convs:
            xt = F.leaky_relu(x, LRELU_SLOPE)
            xt = c(xt)
            x = xt + x
        return x

    def remove_weight_norm(self):
        for l in self.convs:
            remove_weight_norm(l)


# ---------------------------------------------------------------------------
# Neural source-filter modules
# ---------------------------------------------------------------------------

class SineGen(nn.Module):
    """Sine waveform generator for harmonic excitation."""

    def __init__(self, samp_rate, harmonic_num=0,
                 sine_amp=0.1, noise_std=0.003,
                 voiced_threshold=0):
        super().__init__()
        self.sine_amp = sine_amp
        self.noise_std = noise_std
        self.harmonic_num = harmonic_num
        self.dim = self.harmonic_num + 1
        self.sampling_rate = samp_rate
        self.voiced_threshold = voiced_threshold

    def _f02sine(self, f0, upp):
        rad = f0 / self.sampling_rate * torch.arange(1, upp + 1, device=f0.device)
        rad2 = torch.fmod(rad[..., -1:].float() + 0.5, 1.0) - 0.5
        rad_acc = rad2.cumsum(dim=1).fmod(1.0).to(f0)
        rad += F.pad(rad_acc, (0, 0, 1, -1))
        rad = rad.reshape(f0.shape[0], -1, 1)
        rad = torch.multiply(
            rad,
            torch.arange(1, self.dim + 1, device=f0.device).reshape(1, 1, -1),
        )
        rand_ini = torch.rand(1, 1, self.dim, device=f0.device)
        rand_ini[..., 0] = 0
        rad += rand_ini
        sines = torch.sin(2 * np.pi * rad)
        return sines

    @torch.no_grad()
    def forward(self, f0, upp):
        f0 = f0.unsqueeze(-1)
        sine_waves = self._f02sine(f0, upp) * self.sine_amp
        uv = (f0 > self.voiced_threshold).float()
        uv = F.interpolate(
            uv.transpose(2, 1), scale_factor=float(upp), mode='nearest',
        ).transpose(2, 1)
        noise_amp = uv * self.noise_std + (1 - uv) * self.sine_amp / 3
        noise = noise_amp * torch.randn_like(sine_waves)
        sine_waves = sine_waves * uv + noise
        return sine_waves


class SourceModuleHnNSF(nn.Module):
    """Source module producing harmonic excitation from F0."""

    def __init__(self, sampling_rate, harmonic_num=0, sine_amp=0.1,
                 add_noise_std=0.003, voiced_threshod=0):
        super().__init__()
        self.sine_amp = sine_amp
        self.noise_std = add_noise_std
        self.l_sin_gen = SineGen(
            sampling_rate, harmonic_num,
            sine_amp, add_noise_std, voiced_threshod,
        )
        self.l_linear = nn.Linear(harmonic_num + 1, 1)
        self.l_tanh = nn.Tanh()

    def forward(self, x, upp):
        sine_wavs = self.l_sin_gen(x, upp)
        sine_merge = self.l_tanh(self.l_linear(sine_wavs))
        return sine_merge


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class Generator(nn.Module):
    """NSF-HiFiGAN generator — F0-conditioned waveform synthesis."""

    def __init__(self, h):
        super().__init__()
        self.h = h
        self.num_kernels = len(h.resblock_kernel_sizes)
        self.num_upsamples = len(h.upsample_rates)
        self.m_source = SourceModuleHnNSF(
            sampling_rate=h.sampling_rate,
            harmonic_num=8,
        )
        self.noise_convs = nn.ModuleList()
        self.conv_pre = weight_norm(
            Conv1d(h.num_mels, h.upsample_initial_channel, 7, 1, padding=3),
        )
        resblock = ResBlock1 if h.resblock == '1' else ResBlock2

        self.ups = nn.ModuleList()
        for i, (u, k) in enumerate(zip(h.upsample_rates, h.upsample_kernel_sizes)):
            c_cur = h.upsample_initial_channel // (2 ** (i + 1))
            self.ups.append(weight_norm(ConvTranspose1d(
                h.upsample_initial_channel // (2 ** i),
                c_cur,
                k, u, padding=(k - u) // 2,
            )))
            if i + 1 < len(h.upsample_rates):
                stride_f0 = int(np.prod(h.upsample_rates[i + 1:]))
                self.noise_convs.append(Conv1d(
                    1, c_cur,
                    kernel_size=stride_f0 * 2,
                    stride=stride_f0,
                    padding=stride_f0 // 2,
                ))
            else:
                self.noise_convs.append(Conv1d(1, c_cur, kernel_size=1))

        self.resblocks = nn.ModuleList()
        ch = h.upsample_initial_channel
        for i in range(len(self.ups)):
            ch //= 2
            for k, d in zip(h.resblock_kernel_sizes, h.resblock_dilation_sizes):
                self.resblocks.append(resblock(h, ch, k, d))

        self.conv_post = weight_norm(Conv1d(ch, 1, 7, 1, padding=3))
        self.ups.apply(init_weights)
        self.conv_post.apply(init_weights)
        self.upp = int(np.prod(h.upsample_rates))

    def forward(self, x, f0):
        """Synthesize waveform from mel spectrogram and F0.

        Parameters
        ----------
        x : Tensor, shape (batch, n_mels, frames)
            Log-mel spectrogram.
        f0 : Tensor, shape (batch, frames)
            F0 in Hz (0 = unvoiced).

        Returns
        -------
        Tensor, shape (batch, 1, samples)
        """
        har_source = self.m_source(f0, self.upp).transpose(1, 2)
        x = self.conv_pre(x)
        for i in range(self.num_upsamples):
            x = F.leaky_relu(x, LRELU_SLOPE)
            x = self.ups[i](x)
            x_source = self.noise_convs[i](har_source)
            x = x + x_source
            xs = None
            for j in range(self.num_kernels):
                if xs is None:
                    xs = self.resblocks[i * self.num_kernels + j](x)
                else:
                    xs += self.resblocks[i * self.num_kernels + j](x)
            x = xs / self.num_kernels
        x = F.leaky_relu(x)
        x = self.conv_post(x)
        x = torch.tanh(x)
        return x

    def remove_weight_norm(self):
        for l in self.ups:
            remove_weight_norm(l)
        for l in self.resblocks:
            l.remove_weight_norm()
        remove_weight_norm(self.conv_pre)
        remove_weight_norm(self.conv_post)
