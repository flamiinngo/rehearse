"""Local speech-to-text with word timings (faster-whisper), loaded once per model name.

Hermes's own STT returns plain text; pace, pauses and filler positions need per-word
timestamps, so this calls the same engine Hermes pins with ``word_timestamps=True``.
"""

from __future__ import annotations

import threading

from .delivery import Word

# Whisper tends to "clean up" disfluencies; a disfluent prompt keeps the ums it hears.
_VERBATIM_PROMPT = "Umm, let me think, uh... like, hmm. Okay, so, um, here's what I'm, uh, thinking."

_models: dict[str, object] = {}
_lock = threading.Lock()
_run = threading.Lock()  # live interviews analyse answers in parallel; the model runs one at a time


class SpeechUnavailable(RuntimeError):
    """faster-whisper is not installed for this platform (Intel macOS, Windows on ARM)."""


def _model(name: str):
    with _lock:
        if name not in _models:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SpeechUnavailable(
                    "Local transcription is not available on this machine (faster-whisper is not installed)."
                ) from exc
            _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return _models[name]


def is_ready(name: str) -> bool:
    return name in _models


def warm(name: str) -> None:
    """Download (first run only) and load the model so the first answer is not the slow one."""
    _model(name)


def transcribe(path: str, *, model: str = "base", language: str = "") -> tuple[list[Word], str, float]:
    """``(words, language, duration_seconds)`` for an audio or video file."""
    from faster_whisper import decode_audio

    m = _model(model)
    with _run:
        return _transcribe(m, decode_audio(path), language)


def _transcribe(m, audio, language):
    lang = language or m.detect_language(audio)[0]
    segments, info = m.transcribe(
        audio, language=lang, word_timestamps=True, vad_filter=False,
        initial_prompt=_VERBATIM_PROMPT if lang == "en" else None,
    )
    words = [Word(w.word, float(w.start), float(w.end)) for seg in segments for w in (seg.words or [])]
    return words, info.language, float(info.duration)
