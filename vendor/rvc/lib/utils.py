# Vendored from Applio (https://github.com/IAHispano/Applio)
# Copyright 2023 IAHispano
# Licensed under the MIT License
# https://opensource.org/licenses/MIT

import os
import sys
import soxr
import librosa
import soundfile as sf
import numpy as np
import re
import unicodedata
from torch import nn

import logging
from transformers import HubertModel
import warnings

# Remove this to see warnings about transformers models
warnings.filterwarnings("ignore")

logging.getLogger("fairseq").setLevel(logging.ERROR)
logging.getLogger("faiss.loader").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("torch").setLevel(logging.ERROR)


def _get_rvc_cache_dir():
    """Return the RVC model cache directory within StemForge's cache."""
    from utils.cache import get_model_cache_dir
    return get_model_cache_dir("rvc")


class HubertModelWithFinalProj(HubertModel):
    def __init__(self, config):
        super().__init__(config)
        self.final_proj = nn.Linear(config.hidden_size, config.classifier_proj_size)


def load_audio_16k(file):
    # this is used by f0 and feature extractions that load preprocessed 16k files, so there's no need to resample
    try:
        audio, sr = librosa.load(file, sr=16000)
    except Exception as error:
        raise RuntimeError(f"An error occurred loading the audio: {error}")

    return audio.flatten()


def load_audio(file, sample_rate):
    try:
        file = file.strip(" ").strip('"').strip("\n").strip('"').strip(" ")
        audio, sr = sf.read(file)
        if len(audio.shape) > 1:
            audio = librosa.to_mono(audio.T)
        if sr != sample_rate:
            audio = librosa.resample(
                audio, orig_sr=sr, target_sr=sample_rate, res_type="soxr_vhq"
            )
    except Exception as error:
        raise RuntimeError(f"An error occurred loading the audio: {error}")

    return audio.flatten()


def load_audio_infer(
    file,
    sample_rate,
    **kwargs,
):
    formant_shifting = kwargs.get("formant_shifting", False)
    try:
        file = file.strip(" ").strip('"').strip("\n").strip('"').strip(" ")
        if not os.path.isfile(file):
            raise FileNotFoundError(f"File not found: {file}")
        audio, sr = sf.read(file)
        if len(audio.shape) > 1:
            audio = librosa.to_mono(audio.T)
        if sr != sample_rate:
            audio = librosa.resample(
                audio, orig_sr=sr, target_sr=sample_rate, res_type="soxr_vhq"
            )
        if formant_shifting:
            formant_qfrency = kwargs.get("formant_qfrency", 0.8)
            formant_timbre = kwargs.get("formant_timbre", 0.8)

            from stftpitchshift import StftPitchShift

            pitchshifter = StftPitchShift(1024, 32, sample_rate)
            audio = pitchshifter.shiftpitch(
                audio,
                factors=1,
                quefrency=formant_qfrency * 1e-3,
                distortion=formant_timbre,
            )
    except Exception as error:
        raise RuntimeError(f"An error occurred loading the audio: {error}")
    return np.array(audio).flatten()


def format_title(title):
    formatted_title = unicodedata.normalize("NFC", title)
    formatted_title = re.sub(r"[\u2500-\u257F]+", "", formatted_title)
    formatted_title = re.sub(r"[^\w\s.-]", "", formatted_title, flags=re.UNICODE)
    formatted_title = re.sub(r"\s+", "_", formatted_title)
    return formatted_title


def load_embedding(embedder_model, custom_embedder=None):
    from huggingface_hub import hf_hub_download

    rvc_cache = _get_rvc_cache_dir()
    embedder_root = os.path.join(str(rvc_cache), "embedders")

    embedding_list = {
        "contentvec": os.path.join(embedder_root, "contentvec"),
        "spin": os.path.join(embedder_root, "spin"),
        "spin-v2": os.path.join(embedder_root, "spin-v2"),
        "chinese-hubert-base": os.path.join(embedder_root, "chinese_hubert_base"),
        "japanese-hubert-base": os.path.join(embedder_root, "japanese_hubert_base"),
        "korean-hubert-base": os.path.join(embedder_root, "korean_hubert_base"),
    }

    # HuggingFace repo paths for auto-download
    _HF_REPO = "IAHispano/Applio"
    _HF_PATHS = {
        "contentvec": "Resources/embedders/contentvec",
        "spin": "Resources/embedders/spin",
        "spin-v2": "Resources/embedders/spin-v2",
        "chinese-hubert-base": "Resources/embedders/chinese_hubert_base",
        "japanese-hubert-base": "Resources/embedders/japanese_hubert_base",
        "korean-hubert-base": "Resources/embedders/korean_hubert_base",
    }

    if embedder_model == "custom":
        if os.path.exists(custom_embedder):
            model_path = custom_embedder
        else:
            print(f"Custom embedder not found: {custom_embedder}, using contentvec")
            model_path = embedding_list["contentvec"]
    else:
        model_path = embedding_list[embedder_model]
        bin_file = os.path.join(model_path, "pytorch_model.bin")
        json_file = os.path.join(model_path, "config.json")
        os.makedirs(model_path, exist_ok=True)
        hf_subpath = _HF_PATHS.get(embedder_model, _HF_PATHS["contentvec"])
        if not os.path.exists(bin_file):
            hf_hub_download(
                repo_id=_HF_REPO,
                filename=f"{hf_subpath}/pytorch_model.bin",
                local_dir=embedder_root,
                local_dir_use_symlinks=False,
            )
            # hf_hub_download puts it in a subfolder; move if needed
            dl_path = os.path.join(embedder_root, hf_subpath, "pytorch_model.bin")
            if os.path.exists(dl_path) and not os.path.exists(bin_file):
                os.makedirs(os.path.dirname(bin_file), exist_ok=True)
                os.rename(dl_path, bin_file)
        if not os.path.exists(json_file):
            hf_hub_download(
                repo_id=_HF_REPO,
                filename=f"{hf_subpath}/config.json",
                local_dir=embedder_root,
                local_dir_use_symlinks=False,
            )
            dl_path = os.path.join(embedder_root, hf_subpath, "config.json")
            if os.path.exists(dl_path) and not os.path.exists(json_file):
                os.rename(dl_path, json_file)

    models = HubertModelWithFinalProj.from_pretrained(model_path)
    return models
