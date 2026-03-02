"""SFX Stem Builder API — clip placement, rendering, and mix integration."""

from __future__ import annotations

import json
import logging
import pathlib
import shutil
import uuid

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.services.session_store import session, TrackState
from backend.services.sfx_renderer import (
    render_sfx,
    generate_waveform_peaks,
    CANVAS_SAMPLE_RATE,
    CANVAS_CHANNELS,
)
from utils.paths import SFX_DIR, MUSICGEN_DIR, STEMS_DIR
from utils.audio_io import probe, SUPPORTED_EXTENSIONS

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sfx", tags=["sfx"])


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

class CreateSFXRequest(BaseModel):
    name: str = "Untitled SFX"
    mode: str = "manual"  # "reference" or "manual"
    reference_stem_path: str | None = None
    duration_ms: int = 10000  # used in manual mode


class AddPlacementRequest(BaseModel):
    clip_path: str
    start_ms: int = 0
    volume: float = Field(1.0, ge=0.0, le=2.0)
    fade_in_ms: int = Field(0, ge=0)
    fade_out_ms: int = Field(0, ge=0)
    fade_curve: str = "linear"


class UpdatePlacementRequest(BaseModel):
    clip_path: str | None = None
    start_ms: int | None = None
    volume: float | None = Field(None, ge=0.0, le=2.0)
    fade_in_ms: int | None = Field(None, ge=0)
    fade_out_ms: int | None = Field(None, ge=0)
    fade_curve: str | None = None


class UpdateSFXSettingsRequest(BaseModel):
    name: str | None = None
    apply_limiter: bool | None = None
    duration_ms: int | None = None  # resize canvas; clips beyond new end are truncated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_sfx_id() -> str:
    return "sfx_" + uuid.uuid4().hex[:6]


def _next_placement_id(manifest: dict) -> str:
    existing = [int(p["id"][1:]) for p in manifest["placements"] if p["id"].startswith("p")]
    n = max(existing, default=0) + 1
    return f"p{n}"


def _validate_clip_path(path_str: str) -> pathlib.Path:
    """Resolve and validate that clip path exists and is within allowed dirs."""
    p = pathlib.Path(path_str).resolve()
    allowed = [MUSICGEN_DIR.resolve(), STEMS_DIR.resolve(), SFX_DIR.resolve()]
    if not any(str(p).startswith(str(root)) for root in allowed):
        raise HTTPException(403, "Clip path outside allowed directories")
    if not p.exists():
        raise HTTPException(404, f"Clip file not found: {p.name}")
    return p


def _save_manifest(manifest: dict) -> None:
    """Persist manifest to JSON and update session."""
    out_dir = SFX_DIR / manifest["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    session.add_sfx_manifest(manifest)


def _render_and_save(manifest: dict) -> pathlib.Path:
    """Save manifest, render WAV, return rendered path."""
    _save_manifest(manifest)
    return render_sfx(manifest)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/create")
def create_sfx(req: CreateSFXRequest) -> dict:
    """Create a new SFX stem with a blank canvas."""
    sfx_id = _new_sfx_id()

    if req.mode == "reference" and req.reference_stem_path:
        ref = pathlib.Path(req.reference_stem_path).resolve()
        if not ref.exists():
            raise HTTPException(404, "Reference stem not found")
        info = probe(ref)
        sr = info.sample_rate
        channels = info.channels
        total_samples = info.num_frames
        duration_ms = int(info.duration * 1000)
    else:
        sr = CANVAS_SAMPLE_RATE
        channels = CANVAS_CHANNELS
        duration_ms = max(1000, req.duration_ms)
        total_samples = int(sr * duration_ms / 1000)

    mix_track_id = f"sfx_{sfx_id}"
    manifest = {
        "id": sfx_id,
        "name": req.name,
        "mode": req.mode,
        "reference_stem_path": req.reference_stem_path,
        "sample_rate": sr,
        "channels": channels,
        "total_samples": total_samples,
        "duration_ms": duration_ms,
        "apply_limiter": False,
        "placements": [],
        "mix_track_id": mix_track_id,
    }

    rendered_path = _render_and_save(manifest)

    return {
        "id": sfx_id,
        "name": req.name,
        "duration_ms": duration_ms,
        "rendered_path": str(rendered_path),
        "mix_track_id": mix_track_id,
    }


@router.get("")
def list_sfx() -> dict:
    """List all SFX stems in session (summary only)."""
    summaries = []
    for sfx_id in session.sfx_manifest_ids:
        m = session.get_sfx_manifest(sfx_id)
        if m:
            summaries.append({
                "id": m["id"],
                "name": m["name"],
                "duration_ms": m["duration_ms"],
                "placement_count": len(m.get("placements", [])),
            })
    return {"sfx_stems": summaries}


@router.get("/available-clips")
def available_clips(exclude_id: str | None = Query(None)) -> dict:
    """List clips available for SFX placement, grouped by source."""
    clips: list[dict] = []

    # 1. Session — synth outputs
    if MUSICGEN_DIR.exists():
        for f in sorted(MUSICGEN_DIR.rglob("*.wav")):
            clips.append({"path": str(f), "name": f.name, "group": "session"})

    # 2. Saved SFX canvases — rendered.wav where manifest.json exists
    if SFX_DIR.exists():
        for sfx_dir in sorted(SFX_DIR.iterdir()):
            if not sfx_dir.is_dir():
                continue
            manifest_path = sfx_dir / "manifest.json"
            rendered_path = sfx_dir / "rendered.wav"
            if not manifest_path.exists() or not rendered_path.exists():
                continue
            sfx_id = sfx_dir.name
            if exclude_id and sfx_id == exclude_id:
                continue
            try:
                with open(manifest_path) as f:
                    m = json.load(f)
                clips.append({
                    "path": str(rendered_path),
                    "name": m.get("name", sfx_id),
                    "group": "saved_sfx",
                    "duration_ms": m.get("duration_ms", 0),
                    "clip_count": len(m.get("placements", [])),
                })
            except (json.JSONDecodeError, OSError):
                continue

    # 3. Imported external samples
    imports_dir = SFX_DIR / "imports"
    if imports_dir.exists():
        for f in sorted(imports_dir.iterdir()):
            if f.suffix.lower() in {".wav", ".flac", ".mp3", ".ogg"}:
                # Strip UUID prefix: {uuid8}_{original}
                raw_name = f.name
                if len(raw_name) > 9 and raw_name[8] == "_":
                    display_name = raw_name[9:]
                else:
                    display_name = raw_name
                clips.append({
                    "path": str(f),
                    "name": display_name,
                    "group": "imported",
                })

    return {"clips": clips}


@router.post("/upload-clip")
async def upload_clip(file: UploadFile = File(...)) -> dict:
    """Import an external audio file for use as an SFX clip."""
    filename = file.filename or "clip.wav"
    ext = pathlib.Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported format '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}",
        )

    imports_dir = SFX_DIR / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    dest = imports_dir / safe_name

    content = await file.read()
    dest.write_bytes(content)

    return {"path": str(dest), "name": filename, "group": "imported"}


@router.get("/{sfx_id}")
def get_sfx(sfx_id: str) -> dict:
    """Return full manifest, rendered path, and waveform peaks."""
    manifest = session.get_sfx_manifest(sfx_id)
    if not manifest:
        raise HTTPException(404, f"SFX '{sfx_id}' not found")

    # Backfill clip_duration_ms / clip_name on old manifests
    needs_backfill = False
    for p in manifest.get("placements", []):
        if "clip_duration_ms" not in p or "clip_name" not in p:
            cp = pathlib.Path(p.get("clip_path", ""))
            try:
                info = probe(cp)
                p["clip_duration_ms"] = int(info.duration * 1000)
                p["clip_name"] = cp.name
                needs_backfill = True
            except Exception:
                p.setdefault("clip_duration_ms", 0)
                p.setdefault("clip_name", cp.name)
    if needs_backfill:
        _save_manifest(manifest)

    rendered_path = SFX_DIR / sfx_id / "rendered.wav"
    peaks = []
    if rendered_path.exists():
        peaks = generate_waveform_peaks(rendered_path)

    return {
        "manifest": manifest,
        "rendered_path": str(rendered_path) if rendered_path.exists() else None,
        "waveform_peaks": peaks,
    }


@router.post("/{sfx_id}/placements")
def add_placement(sfx_id: str, req: AddPlacementRequest) -> dict:
    """Add a clip placement, re-render."""
    manifest = session.get_sfx_manifest(sfx_id)
    if not manifest:
        raise HTTPException(404, f"SFX '{sfx_id}' not found")

    clip_p = _validate_clip_path(req.clip_path)
    clip_info = probe(clip_p)

    pid = _next_placement_id(manifest)
    placement = {
        "id": pid,
        "clip_path": req.clip_path,
        "clip_name": clip_p.name,
        "clip_duration_ms": int(clip_info.duration * 1000),
        "start_ms": req.start_ms,
        "volume": req.volume,
        "fade_in_ms": req.fade_in_ms,
        "fade_out_ms": req.fade_out_ms,
        "fade_curve": req.fade_curve,
    }
    manifest["placements"].append(placement)

    rendered_path = _render_and_save(manifest)

    return {
        "placement_id": pid,
        "rendered_path": str(rendered_path),
        "placement_count": len(manifest["placements"]),
    }


@router.put("/{sfx_id}/placements/{placement_id}")
def update_placement(sfx_id: str, placement_id: str, req: UpdatePlacementRequest) -> dict:
    """Update an existing placement, re-render."""
    manifest = session.get_sfx_manifest(sfx_id)
    if not manifest:
        raise HTTPException(404, f"SFX '{sfx_id}' not found")

    placement = None
    for p in manifest["placements"]:
        if p["id"] == placement_id:
            placement = p
            break
    if not placement:
        raise HTTPException(404, f"Placement '{placement_id}' not found")

    if req.clip_path is not None:
        new_clip_p = _validate_clip_path(req.clip_path)
        clip_info = probe(new_clip_p)
        placement["clip_path"] = req.clip_path
        placement["clip_name"] = new_clip_p.name
        placement["clip_duration_ms"] = int(clip_info.duration * 1000)
    if req.start_ms is not None:
        placement["start_ms"] = req.start_ms
    if req.volume is not None:
        placement["volume"] = req.volume
    if req.fade_in_ms is not None:
        placement["fade_in_ms"] = req.fade_in_ms
    if req.fade_out_ms is not None:
        placement["fade_out_ms"] = req.fade_out_ms
    if req.fade_curve is not None:
        placement["fade_curve"] = req.fade_curve

    rendered_path = _render_and_save(manifest)

    return {
        "placement_id": placement_id,
        "rendered_path": str(rendered_path),
    }


@router.delete("/{sfx_id}/placements/{placement_id}")
def delete_placement(sfx_id: str, placement_id: str) -> dict:
    """Remove a placement, re-render."""
    manifest = session.get_sfx_manifest(sfx_id)
    if not manifest:
        raise HTTPException(404, f"SFX '{sfx_id}' not found")

    before = len(manifest["placements"])
    manifest["placements"] = [p for p in manifest["placements"] if p["id"] != placement_id]
    if len(manifest["placements"]) == before:
        raise HTTPException(404, f"Placement '{placement_id}' not found")

    rendered_path = _render_and_save(manifest)

    return {
        "rendered_path": str(rendered_path),
        "placement_count": len(manifest["placements"]),
    }


@router.patch("/{sfx_id}")
def update_sfx_settings(sfx_id: str, req: UpdateSFXSettingsRequest) -> dict:
    """Update SFX name or limiter setting."""
    manifest = session.get_sfx_manifest(sfx_id)
    if not manifest:
        raise HTTPException(404, f"SFX '{sfx_id}' not found")

    needs_render = False
    if req.name is not None:
        manifest["name"] = req.name
    if req.apply_limiter is not None and req.apply_limiter != manifest.get("apply_limiter"):
        manifest["apply_limiter"] = req.apply_limiter
        needs_render = True
    if req.duration_ms is not None:
        new_ms = max(0, req.duration_ms)
        manifest["duration_ms"] = new_ms
        manifest["total_samples"] = int(manifest["sample_rate"] * new_ms / 1000)
        needs_render = True

    if needs_render:
        rendered_path = _render_and_save(manifest)
    else:
        _save_manifest(manifest)
        rendered_path = SFX_DIR / sfx_id / "rendered.wav"

    return {
        "id": sfx_id,
        "name": manifest["name"],
        "apply_limiter": manifest["apply_limiter"],
        "rendered_path": str(rendered_path),
    }


@router.post("/{sfx_id}/send-to-mix")
def send_to_mix(sfx_id: str) -> dict:
    """Add the rendered SFX WAV as an audio track in the Mix tab (idempotent)."""
    manifest = session.get_sfx_manifest(sfx_id)
    if not manifest:
        raise HTTPException(404, f"SFX '{sfx_id}' not found")

    rendered_path = SFX_DIR / sfx_id / "rendered.wav"
    if not rendered_path.exists():
        raise HTTPException(400, "SFX not yet rendered")

    # Return existing auto-track if it's still in the session
    existing_id = manifest.get("mix_track_id")
    if existing_id:
        for track in session.mix_tracks:
            if track.track_id == existing_id:
                return {"track_id": existing_id, "label": track.label}

    # Create new track (old manifests, or if the auto-track was removed)
    track_id = existing_id or f"sfx_{uuid.uuid4().hex[:8]}"
    track = TrackState(
        track_id=track_id,
        label=f"SFX: {manifest['name']}",
        source="audio",
        path=rendered_path,
    )
    session.add_track(track)

    if not manifest.get("mix_track_id"):
        manifest["mix_track_id"] = track_id
        _save_manifest(manifest)

    return {"track_id": track_id, "label": track.label}


@router.delete("/{sfx_id}")
def delete_sfx(sfx_id: str) -> dict:
    """Delete SFX manifest, files, and any associated mix track."""
    if not session.remove_sfx_manifest(sfx_id):
        raise HTTPException(404, f"SFX '{sfx_id}' not found")

    # Remove files
    sfx_dir = SFX_DIR / sfx_id
    if sfx_dir.exists():
        shutil.rmtree(sfx_dir)

    # Remove any mix tracks that reference this SFX
    for track in session.mix_tracks:
        if track.path and str(SFX_DIR / sfx_id) in str(track.path):
            session.remove_track(track.track_id)

    return {"status": "deleted", "id": sfx_id}


@router.get("/{sfx_id}/stream")
def stream_sfx(sfx_id: str) -> FileResponse:
    """Stream the rendered SFX WAV."""
    manifest = session.get_sfx_manifest(sfx_id)
    if not manifest:
        raise HTTPException(404, f"SFX '{sfx_id}' not found")

    rendered_path = SFX_DIR / sfx_id / "rendered.wav"
    if not rendered_path.exists():
        raise HTTPException(404, "Rendered WAV not found")

    return FileResponse(rendered_path, media_type="audio/wav")


@router.get("/{sfx_id}/reference-waveform")
def reference_waveform(sfx_id: str) -> dict:
    """Return waveform peaks for the reference stem (if any)."""
    manifest = session.get_sfx_manifest(sfx_id)
    if not manifest:
        raise HTTPException(404, f"SFX '{sfx_id}' not found")

    ref_path_str = manifest.get("reference_stem_path")
    if not ref_path_str:
        return {"peaks": [], "has_reference": False}

    ref_path = pathlib.Path(ref_path_str)
    if not ref_path.exists():
        return {"peaks": [], "has_reference": False}

    peaks = generate_waveform_peaks(ref_path)
    return {"peaks": peaks, "has_reference": True}
