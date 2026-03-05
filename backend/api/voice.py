"""Voice conversion endpoint (RVC via vendored Applio)."""

from __future__ import annotations

import os
import pathlib
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import session
from backend.services import pipeline_manager
from utils.cache import get_model_cache_dir

router = APIRouter(prefix="/api", tags=["voice"])

# Voice models live in ~/.cache/stemforge/voice_models/
VOICE_MODELS_DIR = get_model_cache_dir("voice_models")

# Known voice models available for auto-download from HuggingFace.
# Each entry: (display_name, hf_repo, hf_filename, hf_index_filename_or_None)
KNOWN_MODELS: list[tuple[str, str, str, str | None]] = [
    (
        "Marcello-v2",
        "Rejekts/project",
        "Marcello-v2/Marcello-v2.pth",
        "Marcello-v2/added_IVF1063_Flat_nprobe_1_Marcello-v2_v2.index",
    ),
    (
        "Trump",
        "Rejekts/project",
        "Trump/Trump.pth",
        "Trump/added_IVF1875_Flat_nprobe_1_Trump-Trump_v2.index",
    ),
    (
        "Obama",
        "Rejekts/project",
        "Obama/Obama.pth",
        "Obama/added_IVF2124_Flat_nprobe_1_Obama-Obama_v2.index",
    ),
]


class VoiceConvertRequest(BaseModel):
    audio_path: str
    model_name: str
    pitch: int = 0
    f0_method: str = "rmvpe"
    index_rate: float = 0.3
    protect: float = 0.33


class VoiceModelInfo(BaseModel):
    name: str
    has_index: bool
    size_mb: float
    downloaded: bool


def _find_model_files(name: str) -> tuple[pathlib.Path | None, pathlib.Path | None]:
    """Find .pth and .index files for a named voice model in the cache."""
    model_dir = VOICE_MODELS_DIR / name
    pth = None
    index = None

    if model_dir.is_dir():
        for f in model_dir.iterdir():
            if f.suffix == ".pth" and pth is None:
                pth = f
            elif f.suffix == ".index" and index is None:
                index = f

    # Also check flat layout (file directly in voice_models/)
    if pth is None:
        flat = VOICE_MODELS_DIR / f"{name}.pth"
        if flat.exists():
            pth = flat
            idx = VOICE_MODELS_DIR / f"{name}.index"
            if idx.exists():
                index = idx

    return pth, index


def _download_known_model(name: str) -> tuple[pathlib.Path, pathlib.Path | None]:
    """Download a known model from HuggingFace if not already cached."""
    from huggingface_hub import hf_hub_download

    entry = None
    for display_name, repo, pth_path, idx_path in KNOWN_MODELS:
        if display_name == name:
            entry = (repo, pth_path, idx_path)
            break

    if entry is None:
        raise ValueError(f"Unknown voice model: {name}")

    repo, pth_hf, idx_hf = entry

    # Download .pth
    local_pth = hf_hub_download(
        repo_id=repo,
        filename=pth_hf,
        cache_dir=str(VOICE_MODELS_DIR / ".hf_cache"),
        local_dir=str(VOICE_MODELS_DIR),
        local_dir_use_symlinks=False,
    )

    # Download .index if available
    local_idx = None
    if idx_hf:
        local_idx = hf_hub_download(
            repo_id=repo,
            filename=idx_hf,
            cache_dir=str(VOICE_MODELS_DIR / ".hf_cache"),
            local_dir=str(VOICE_MODELS_DIR),
            local_dir_use_symlinks=False,
        )

    return pathlib.Path(local_pth), pathlib.Path(local_idx) if local_idx else None


def _run_voice_convert(req: VoiceConvertRequest, job_id: str) -> dict:
    """Execute RVC pipeline (runs in background thread)."""
    from pipelines.rvc_pipeline import RvcConfig

    pipeline = pipeline_manager.get_rvc()

    # Resolve model files
    model_path, index_path = _find_model_files(req.model_name)

    if model_path is None:
        # Try auto-download
        job_manager.update_progress(job_id, 0.05, f"Downloading model {req.model_name}...")
        try:
            model_path, index_path = _download_known_model(req.model_name)
        except ValueError:
            raise RuntimeError(f"Voice model not found: {req.model_name}")

    config = RvcConfig(
        model_path=model_path,
        index_path=index_path,
        pitch=req.pitch,
        f0_method=req.f0_method,
        index_rate=req.index_rate,
        protect=req.protect,
    )

    def _cb(pct, stage=""):
        job_manager.update_progress(job_id, pct, stage)

    pipeline.configure(config)
    pipeline.set_progress_callback(_cb)
    job_manager.update_progress(job_id, 0.1, "Loading RVC engine...")
    pipeline.load_model()

    result = pipeline.run(pathlib.Path(req.audio_path))

    # Store in session for cross-tab integration
    label = f"Voice ({req.model_name})"
    session.add_voice_path(label, result.output_path)

    # Auto-add as mix track
    from backend.services.session_store import TrackState
    track = TrackState(
        track_id=str(uuid.uuid4()),
        label=label,
        source="audio",
        path=result.output_path,
        enabled=True,
        volume=0.8,
    )
    session.add_track(track)

    return {
        "output_path": str(result.output_path),
        "duration": result.duration_seconds,
        "model_name": req.model_name,
    }


@router.post("/voice/convert")
def start_voice_convert(req: VoiceConvertRequest) -> dict:
    audio = pathlib.Path(req.audio_path)
    if not audio.exists():
        raise HTTPException(422, f"Audio file not found: {req.audio_path}")

    job_id = job_manager.create_job("voice_convert")
    job_manager.run_job(job_id, _run_voice_convert, req, job_id)
    return {"job_id": job_id}


@router.get("/voice/models")
def list_voice_models() -> dict:
    """List available voice models (downloaded + known downloadable)."""
    models: list[dict] = []
    seen_names: set[str] = set()

    # Scan local cache for downloaded models
    if VOICE_MODELS_DIR.is_dir():
        for item in sorted(VOICE_MODELS_DIR.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                pth, idx = _find_model_files(item.name)
                if pth:
                    size_mb = pth.stat().st_size / (1024 * 1024)
                    models.append({
                        "name": item.name,
                        "has_index": idx is not None,
                        "size_mb": round(size_mb, 1),
                        "downloaded": True,
                    })
                    seen_names.add(item.name)
            elif item.suffix == ".pth":
                name = item.stem
                idx = item.with_suffix(".index")
                size_mb = item.stat().st_size / (1024 * 1024)
                models.append({
                    "name": name,
                    "has_index": idx.exists(),
                    "size_mb": round(size_mb, 1),
                    "downloaded": True,
                })
                seen_names.add(name)

    # Add known models not yet downloaded
    for display_name, _, _, idx_path in KNOWN_MODELS:
        if display_name not in seen_names:
            models.append({
                "name": display_name,
                "has_index": idx_path is not None,
                "size_mb": 0,
                "downloaded": False,
            })

    return {"models": models}
