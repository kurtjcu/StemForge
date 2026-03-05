import os
import torch

from vendor.rvc.lib.predictors.RMVPE import RMVPE0Predictor
from torchfcpe import spawn_infer_model_from_pt
import torchcrepe
import numpy as np


def _predictor_cache_dir():
    """Return path to the RVC predictors cache directory."""
    from utils.cache import get_model_cache_dir
    return str(get_model_cache_dir("rvc") / "predictors")


def _ensure_predictor_model(filename, hf_filename=None):
    """Return the local path to a predictor model, downloading if needed."""
    cache_dir = _predictor_cache_dir()
    local_path = os.path.join(cache_dir, filename)
    if os.path.exists(local_path):
        return local_path

    # Auto-download from HuggingFace
    from huggingface_hub import hf_hub_download
    hf_name = hf_filename or filename
    hf_hub_download(
        repo_id="IAHispano/Applio",
        filename=f"Resources/predictors/{hf_name}",
        local_dir=cache_dir,
        local_dir_use_symlinks=False,
    )
    # hf_hub_download may put it in a subfolder
    dl_path = os.path.join(cache_dir, "Resources", "predictors", hf_name)
    if os.path.exists(dl_path) and not os.path.exists(local_path):
        os.rename(dl_path, local_path)
    return local_path


class RMVPE:
    def __init__(self, device, model_name="rmvpe.pt", sample_rate=16000, hop_size=160):
        self.device = device
        self.sample_rate = sample_rate
        self.hop_size = hop_size
        model_path = _ensure_predictor_model(model_name)
        self.model = RMVPE0Predictor(model_path, device=self.device)

    def get_f0(self, x, filter_radius=0.03):
        f0 = self.model.infer_from_audio(x, thred=filter_radius)
        return f0


class CREPE:
    def __init__(self, device, sample_rate=16000, hop_size=160):
        self.device = device
        self.sample_rate = sample_rate
        self.hop_size = hop_size

    def get_f0(self, x, f0_min=50, f0_max=1100, p_len=None, model="full"):
        if p_len is None:
            p_len = x.shape[0] // self.hop_size

        if not torch.is_tensor(x):
            x = torch.from_numpy(x)

        batch_size = 512

        f0, pd = torchcrepe.predict(
            x.float().to(self.device).unsqueeze(dim=0),
            self.sample_rate,
            self.hop_size,
            f0_min,
            f0_max,
            model=model,
            batch_size=batch_size,
            device=self.device,
            return_periodicity=True,
        )
        pd = torchcrepe.filter.median(pd, 3)
        f0 = torchcrepe.filter.mean(f0, 3)
        f0[pd < 0.1] = 0
        f0 = f0[0].cpu().numpy()

        return f0


class FCPE:
    def __init__(self, device, sample_rate=16000, hop_size=160):
        self.device = device
        self.sample_rate = sample_rate
        self.hop_size = hop_size
        model_path = _ensure_predictor_model("fcpe.pt")
        self.model = spawn_infer_model_from_pt(
            model_path,
            self.device,
            bundled_model=True,
        )

    def get_f0(self, x, p_len=None, filter_radius=0.006):
        if p_len is None:
            p_len = x.shape[0] // self.hop_size

        if not torch.is_tensor(x):
            x = torch.from_numpy(x)

        f0 = (
            self.model.infer(
                x.float().to(self.device).unsqueeze(0),
                sr=self.sample_rate,
                decoder_mode="local_argmax",
                threshold=filter_radius,
            )
            .squeeze()
            .cpu()
            .numpy()
        )

        return f0
