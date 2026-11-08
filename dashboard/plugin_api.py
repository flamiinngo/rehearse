"""The Desktop Rehearse page's backend: takes uploads, forwards page calls to the plugin.

``POST /upload`` parks a recorded take; ``POST /app`` runs one call from the page's
table (``bridge.py``) in the loaded agent half, which owns ``ctx.llm``. This router runs
in the same process as that half (the gateway, or the plugin host under isolation), so
it finds the module by its file path instead of importing it under a second name.
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

router = APIRouter()

_PLUGIN_INIT = Path(__file__).resolve().parent.parent / "__init__.py"


def _agent_half():
    """The loaded plugin package (the one whose ``__init__.py`` sits beside this folder), or None."""
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if path and getattr(module, "APP", None) is not None and Path(path).resolve() == _PLUGIN_INIT:
            return module
    return None


@router.post("/app")
def app(method: str = Body(...), params: dict = Body(default_factory=dict)):
    half = _agent_half()
    if half is None:
        return {"error": "Rehearse is not enabled for this Hermes profile. Enable it in Capabilities → Plugins, "
                         "then restart the gateway."}
    return half.handle_app(method, params)


MAX_BYTES = 400 * 1024 * 1024
STALE_SECONDS = 24 * 3600
_EXTENSIONS = {"video/webm": ".webm", "audio/webm": ".webm", "video/mp4": ".mp4", "audio/mp4": ".m4a",
               "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav", "video/quicktime": ".mov",
               "audio/mpeg": ".mp3", "audio/x-m4a": ".m4a", "audio/aac": ".m4a"}


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
