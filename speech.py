"""Local speech-to-text with word timings (faster-whisper), loaded once per model name.

Hermes's own STT returns plain text; pace, pauses and filler positions need per-word
timestamps, so this calls the same engine Hermes pins with ``word_timestamps=True``.
"""

from __future__ import annotations

import logging
import threading

from .delivery import Word

# Whisper tends to "clean up" disfluencies; a disfluent prompt keeps the ums it hears.
_VERBATIM_PROMPT = "Umm, let me think, uh... like, hmm. Okay, so, um, here's what I'm, uh, thinking."

logger = logging.getLogger(__name__)

_models: dict[str, object] = {}
_lock = threading.Lock()
_run = threading.Lock()  # live interviews analyse answers in parallel; the model runs one at a time


class SpeechUnavailable(RuntimeError):
    """faster-whisper is not installed for this platform (Windows on ARM)."""


def _model(name: str):
    with _lock:
        if name not in _models:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SpeechUnavailable(
                    "Local transcription is not available on this machine (faster-whisper is not installed)."
                ) from exc
            try:
                _models[name] = WhisperModel(name, device="cpu", compute_type="int8")
            except Exception as exc:  # Hugging Face and network errors come in many types; the log keeps the detail
                logger.warning("rehearse: speech model %s failed to load: %s", name, exc)
                raise SpeechUnavailable(
                    f"The speech model ({name}) could not be downloaded or loaded. Check your internet connection "
                    "and try again; it is a one-time download."
                ) from exc
        return _models[name]


def is_ready(name: str) -> bool:
    return name in _models


def warm(name: str) -> None:
    """Download (first run only) and load the model so the first answer is not the slow one."""
    _model(name)


def transcribe(path: str, *, model: str = "base", language: str = "") -> tuple[list[Word], str, float]:
    """``(words, language, duration_seconds)`` for an audio or video file."""
    m = _model(model)  # first, so a missing engine is the plain SpeechUnavailable sentence
    import av
    from faster_whisper import decode_audio

    try:
        audio = decode_audio(path)
    except av.FFmpegError:
        raise ValueError("That recording could not be read. Record the answer again, or upload a different file.") from None
    with _run:
        return _transcribe(m, audio, language)


SILENT_PEAK = 0.002  # a muted microphone records (near) zeros
# Whisper invents words in silence ("Okay. Okay. Okay."); its own filter misses some. A segment that is
# probably silence AND low-confidence is dropped. Real speech, even hesitant, scores far above both.
NO_SPEECH = 0.4
LOW_LOGPROB = -1.0


def _transcribe(m, audio, language):
    duration = len(audio) / 16000.0
    if not len(audio) or float(abs(audio).max()) < SILENT_PEAK:
        return [], language or "", duration
    lang = language or m.detect_language(audio)[0]
    segments, info = m.transcribe(
        audio, language=lang, word_timestamps=True, vad_filter=False,
        initial_prompt=_VERBATIM_PROMPT if lang == "en" else None,
    )
    words = [Word(w.word, float(w.start), float(w.end)) for seg in segments
             if not (seg.no_speech_prob > NO_SPEECH and seg.avg_logprob < LOW_LOGPROB) for w in (seg.words or [])]
    return words, info.language, float(info.duration)
