"""Export and zip download endpoints."""

from __future__ import annotations

import io
import pathlib
import uuid
import zipfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.services.job_manager import job_manager
from backend.services.session_store import session
from utils.paths import EXPORT_DIR

router = APIRouter(prefix="/api/export", tags=["export"])


class ExportRequest(BaseModel):
    items: list[str]          # list of file paths to export
    format: str = "wav"       # wav, flac, mp3, ogg


class ZipRequest(BaseModel):
    items: list[str]          # list of file paths to zip


def _run_export(items: list[str], fmt: str, job_id: str) -> dict:
    """Convert selected items to target format."""
    from utils.audio_io import read_audio, write_audio

    progress_cb = job_manager.make_progress_callback(job_id)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    exported = []
    for i, item_path in enumerate(items):
        progress_cb(i / len(items), f"Exporting {pathlib.Path(item_path).name}...")
        src = pathlib.Path(item_path)
        if not src.exists():
            continue

        dest = EXPORT_DIR / f"{src.stem}.{fmt}"
        if src.suffix.lstrip(".").lower() == fmt:
            # Same format — just copy
            import shutil
            shutil.copy2(src, dest)
        else:
            waveform, sr = read_audio(src)
            write_audio(waveform, sr, dest, fmt=fmt)

        exported.append(str(dest))

    progress_cb(1.0, "Done")
    return {"exported": exported}


@router.post("")
def start_export(req: ExportRequest) -> dict:
    if not req.items:
        raise HTTPException(422, "No items to export")
    if req.format not in ("wav", "flac", "mp3", "ogg"):
        raise HTTPException(422, f"Unsupported format: {req.format}")

    job_id = job_manager.create_job("export")
    job_manager.run_job(job_id, _run_export, req.items, req.format, job_id)
    return {"job_id": job_id}


@router.post("/download-zip")
def download_zip(req: ZipRequest) -> StreamingResponse:
    if not req.items:
        raise HTTPException(422, "No items to zip")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item_path in req.items:
            p = pathlib.Path(item_path)
            if p.exists():
                zf.write(p, p.name)

    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=stemforge_export.zip"},
    )
