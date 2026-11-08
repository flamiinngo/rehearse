"""Receives a take recorded on the Desktop Rehearse page and parks it for analysis.

Only stores the file: transcription and feedback run in the agent half (it owns
``ctx.llm``), which the page reaches through plugin RPC with the returned id.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

router = APIRouter()

MAX_BYTES = 400 * 1024 * 1024
STALE_SECONDS = 24 * 3600
_EXTENSIONS = {"video/webm": ".webm", "audio/webm": ".webm", "video/mp4": ".mp4", "audio/mp4": ".m4a",
               "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav", "video/quicktime": ".mov"}


def uploads_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir

    return Path(plugin_data_dir("rehearse")) / "uploads"


def _sweep(folder: Path) -> None:
    cutoff = time.time() - STALE_SECONDS
    for old in folder.glob("*"):
        if old.is_file() and old.stat().st_mtime < cutoff:
            old.unlink(missing_ok=True)


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    ext = _EXTENSIONS.get(content_type)
    if ext is None:
        raise HTTPException(status_code=415, detail="Only audio or video recordings can be rehearsed.")
    folder = uploads_dir()
    folder.mkdir(parents=True, exist_ok=True)
    _sweep(folder)
    upload_id = uuid.uuid4().hex
    dest = folder / f"{upload_id}{ext}"
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="That recording is too long to analyse in one take.")
            out.write(chunk)
    return {"upload_id": f"{upload_id}{ext}", "bytes": size}
