"""Voice conversion endpoint (RVC via vendored Applio)."""

from __future__ import annotations

import os
import pathlib
import shutil
import uuid

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import session
from backend.services import pipeline_manager
from utils.cache import get_model_cache_dir
from utils.paths import VOICE_DIR

router = APIRouter(prefix="/api", tags=["voice"])

_VOICE_UPLOAD_DIR = VOICE_DIR / "uploads"
_AUDIO_EXTS = {".wav", ".flac", ".mp3", ".ogg", ".aiff", ".m4a", ".wma", ".opus"}

# Voice models live in ~/.cache/stemforge/voice_models/
VOICE_MODELS_DIR = get_model_cache_dir("voice_models")

# Known voice models available for auto-download from HuggingFace.
# Each entry: (display_name, hf_repo, hf_pth_filename, hf_index_filename_or_None)
# Models from binant/ use flat model.pth/model.index; we download into per-name subdirs.
KNOWN_MODELS: list[tuple[str, str, str, str | None]] = [
    (
        "Donald Trump",
        "binant/Donald_Trump__RVC_v2_",
        "model.pth",
        "model.index",
    ),
    (
        "SpongeBob",
        "binant/SpongeBob_SquarePants__RVC_v2_",
        "model.pth",
        "model.index",
    ),
    (
        "Kurt Cobain",
        "binant/Kurt_Cobain__From_Nirvana___RVC_v2__150_Epochs",
        "model.pth",
        "model.index",
    ),
    (
        "Hatsune Miku",
        "binant/Hatsune_Miku__RVC_v2_",
        "model.pth",
        "model.index",
    ),
    (
        "Peter Griffin",
        "binant/Peter_Griffin__Family_Guy___RVC_V2__300_Epoch",
        "model.pth",
        "model.index",
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

    # Download into a per-model subdirectory so flat filenames don't collide
    model_dir = VOICE_MODELS_DIR / name
    model_dir.mkdir(parents=True, exist_ok=True)

    # Download .pth
    local_pth = hf_hub_download(
        repo_id=repo,
        filename=pth_hf,
        local_dir=str(model_dir),
    )

    # Download .index if available
    local_idx = None
    if idx_hf:
        local_idx = hf_hub_download(
            repo_id=repo,
            filename=idx_hf,
            local_dir=str(model_dir),
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


@router.post("/voice/upload")
async def upload_voice_audio(file: UploadFile) -> dict:
    """Upload audio for voice conversion (independent of AceStep)."""
    if not file.filename:
        raise HTTPException(422, "No file provided")

    ext = pathlib.Path(file.filename).suffix.lower()
    if ext not in _AUDIO_EXTS:
        raise HTTPException(422, f"Unsupported audio format: {ext}")

    _VOICE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = _VOICE_UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"

    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    return {"path": str(dest)}


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


@router.get("/voice/models/search")
def search_voice_models(q: str = "") -> dict:
    """Search HuggingFace for RVC voice models matching a query.

    Only returns repos that actually contain .pth model files.
    """
    from huggingface_hub import HfApi

    query = q.strip()
    if not query or len(query) < 2:
        return {"results": []}

    api = HfApi()
    results = []
    try:
        models = list(api.list_models(search=f"rvc {query}", limit=30))
        for m in models:
            # Verify the repo actually has .pth files before listing
            try:
                files = list(api.list_repo_files(m.id))
            except Exception:
                continue
            pth_files = [f for f in files if f.endswith(".pth")]
            if not pth_files:
                continue

            repo_name = m.id.split("/")[-1] if "/" in m.id else m.id
            display = repo_name.replace("_", " ").replace("--", " - ").replace("  ", " ")
            for suffix in ["RVC v2", "RVC V2", "RVC v2 ", "RVC V2 "]:
                display = display.replace(suffix, "").strip()
            results.append({
                "repo_id": m.id,
                "display": display.strip(),
                "downloads": m.downloads or 0,
            })
            if len(results) >= 15:
                break
        results.sort(key=lambda x: x["downloads"], reverse=True)
    except Exception:
        pass

    return {"results": results}


class VoiceModelImportRequest(BaseModel):
    repo_id: str
    name: str = ""


@router.post("/voice/models/import")
def import_voice_model(req: VoiceModelImportRequest) -> dict:
    """Import an RVC voice model from a HuggingFace repo.

    Scans the repo for .pth and .index files and downloads them.
    """
    from huggingface_hub import HfApi, hf_hub_download

    repo_id = req.repo_id.strip()
    if not repo_id or "/" not in repo_id:
        raise HTTPException(422, "Invalid repo ID — expected format: owner/repo")

    api = HfApi()
    try:
        files = list(api.list_repo_files(repo_id))
    except Exception as exc:
        raise HTTPException(404, f"Repository not found: {repo_id} ({exc})")

    pth_files = [f for f in files if f.endswith(".pth")]
    idx_files = [f for f in files if f.endswith(".index")]

    # Filter out common pretrain weights (D/G/f0 prefixed)
    voice_pths = [f for f in pth_files if not any(
        pathlib.PurePosixPath(f).name.startswith(p) for p in ("D", "G", "f0")
    )]
    if not voice_pths:
        # Fall back to all .pth files
        voice_pths = pth_files

    if not voice_pths:
        raise HTTPException(
            422, f"No .pth model files found in {repo_id}. "
            "This repo may not contain an RVC voice model.",
        )

    # Use first .pth found
    pth_hf = voice_pths[0]

    # Derive display name: user-provided, or from repo name
    name = req.name.strip() or repo_id.split("/")[-1].replace("_", " ").replace("--", " ")

    model_dir = VOICE_MODELS_DIR / name
    model_dir.mkdir(parents=True, exist_ok=True)

    local_pth = hf_hub_download(repo_id=repo_id, filename=pth_hf, local_dir=str(model_dir))

    local_idx = None
    if idx_files:
        try:
            local_idx = hf_hub_download(
                repo_id=repo_id, filename=idx_files[0], local_dir=str(model_dir),
            )
        except Exception:
            pass  # Index is optional

    size_mb = pathlib.Path(local_pth).stat().st_size / (1024 * 1024)
    return {
        "name": name,
        "has_index": local_idx is not None,
        "size_mb": round(size_mb, 1),
        "downloaded": True,
    }


@router.post("/voice/models/upload")
async def upload_voice_model(file: UploadFile, name: str = "") -> dict:
    """Upload an RVC .pth model file (and optionally .index) from disk."""
    if not file.filename:
        raise HTTPException(422, "No file provided")

    ext = pathlib.Path(file.filename).suffix.lower()
    if ext not in (".pth", ".index"):
        raise HTTPException(422, "Only .pth and .index files are accepted")

    # Derive model name from filename if not provided
    model_name = name.strip() or pathlib.Path(file.filename).stem
    model_dir = VOICE_MODELS_DIR / model_name
    model_dir.mkdir(parents=True, exist_ok=True)

    dest = model_dir / file.filename
    with open(dest, "wb") as out:
        shutil.copyfileobj(file.file, out)

    pth, idx = _find_model_files(model_name)
    size_mb = pth.stat().st_size / (1024 * 1024) if pth else 0
    return {
        "name": model_name,
        "has_pth": pth is not None,
        "has_index": idx is not None,
        "size_mb": round(size_mb, 1),
    }


@router.delete("/voice/models/{name}")
def delete_voice_model(name: str) -> dict:
    """Delete a downloaded voice model from the cache."""
    model_dir = VOICE_MODELS_DIR / name
    if model_dir.is_dir():
        shutil.rmtree(model_dir)
        return {"deleted": name}

    # Check flat layout
    flat_pth = VOICE_MODELS_DIR / f"{name}.pth"
    if flat_pth.exists():
        flat_pth.unlink()
        flat_idx = VOICE_MODELS_DIR / f"{name}.index"
        if flat_idx.exists():
            flat_idx.unlink()
        return {"deleted": name}

    raise HTTPException(404, f"Voice model not found: {name}")
