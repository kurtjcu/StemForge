"""System endpoints: health, device info, models, session."""

from __future__ import annotations

from fastapi import APIRouter

from backend.services.session_store import session

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/device")
def device_info() -> dict:
    import torch
    from utils.device import get_device

    dev = get_device()
    info: dict = {"device": str(dev)}

    if dev.type == "cuda":
        info["gpu_name"] = torch.cuda.get_device_name(0)
        total = torch.cuda.get_device_properties(0).total_memory
        info["vram_gb"] = round(total / (1024 ** 3), 1)
    elif dev.type == "mps":
        info["gpu_name"] = "Apple Silicon (MPS)"

    return info


@router.get("/models")
def list_models() -> dict:
    from models.registry import (
        list_specs, DemucsSpec, RoformerSpec, BasicPitchSpec,
        WhisperSpec, StableAudioSpec,
    )

    def _serialize(spec) -> dict:
        d = {
            "model_id": spec.model_id,
            "display_name": spec.display_name,
            "description": spec.description,
            "device": spec.device,
            "sample_rate": spec.sample_rate,
            "available_stems": list(getattr(spec, "available_stems", [])),
        }
        if spec.license_warning:
            d["license_warning"] = spec.license_warning
        return d

    return {
        "demucs": [_serialize(s) for s in list_specs(DemucsSpec)],
        "roformer": [_serialize(s) for s in list_specs(RoformerSpec)],
        "basicpitch": [_serialize(s) for s in list_specs(BasicPitchSpec)],
        "whisper": [_serialize(s) for s in list_specs(WhisperSpec)],
        "stable_audio": [_serialize(s) for s in list_specs(StableAudioSpec)],
    }


@router.get("/session")
def get_session() -> dict:
    return session.to_dict()


@router.delete("/session")
def clear_session() -> dict:
    session.clear()
    return {"status": "cleared"}
