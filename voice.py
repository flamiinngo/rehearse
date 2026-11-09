"""The interviewer's natural voice: Hermes's own text-to-speech.

Edge neural voices by default, or whatever provider the user set up in Hermes (ElevenLabs,
OpenAI, a local engine). Each question is spoken once and cached, so "hear it again" is instant.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

MAX_CHARS = 1500  # the opening plus the first question, spoken whole
MAX_CACHED = 300
_MIME = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav", ".m4a": "audio/mp4"}


def speak(ctx, root: Path, text: str) -> dict:
    text = text.strip()[:MAX_CHARS]
    if not text:
        raise ValueError("text is required")
    folder = root / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    audio = next((p for p in folder.glob(f"{key}.*") if p.suffix in _MIME), None)
    if audio is None:
        try:
            out = json.loads(ctx.dispatch_tool("text_to_speech", {"text": text, "output_path": str(folder / f"{key}.mp3")}))
        except ValueError:
            out = {}
        if not out.get("success") or not out.get("file_path"):
            raise ValueError(out.get("error") or "Hermes text-to-speech is not available.")
        audio = Path(out["file_path"])
        _prune(folder)
    return {"audio": base64.b64encode(audio.read_bytes()).decode("ascii"), "mime": _MIME.get(audio.suffix, "audio/mpeg")}


def _prune(folder: Path) -> None:
    files = sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime)
    for old in files[:-MAX_CACHED]:
        old.unlink(missing_ok=True)
