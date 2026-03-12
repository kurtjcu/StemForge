"""NSF-HiFiGAN vocoder — vendored from DDSP-SVC (MIT license).

F0-conditioned neural vocoder for high-quality waveform synthesis from
mel spectrograms. Pretrained models from openvpi/vocoders (CC BY-NC-SA 4.0).

Source: https://github.com/yxlllc/DDSP-SVC/tree/master/nsf_hifigan
"""

from .models import Generator, load_model, load_config  # noqa: F401
from .stft import STFT  # noqa: F401
