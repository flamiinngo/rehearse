"""One rehearsal take, end to end: transcribe, measure, coach, score, store.

The single path behind the agent tools, the Desktop page and messaging voice notes.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from . import coach, delivery, face, speech
from .sessions import Store, best_by_question

logger = logging.getLogger(__name__)


def start(llm, store: Store, *, scenario: str, subject: str, details: str = "", count: int = 5) -> dict:
    key = coach.resolve(scenario)
    if not subject.strip():
        raise ValueError("Say what you are rehearsing for: a role, a talk title or a topic.")
    return store.create(scenario=key, mode=coach.SCENARIOS[key].mode, subject=subject.strip(), details=details.strip(),
                        plan=coach.plan(llm, scenario=key, subject=subject, details=details, count=count))


def scenario_of(session: dict) -> str:
    """Sessions saved before scenarios existed carry only a mode."""
    return session.get("scenario") or ("job" if session.get("mode") == "interview" else "presentation")


def _target(question: dict) -> tuple[float, float]:
    t = float(question.get("target_seconds") or 90)
    return (t * 0.5, t * 1.3)


def _measure_media(store: Store, path: str, *, model: str, language: str, target,
                   on_stage) -> tuple[dict, str, list[str]]:
    notes: list[str] = []
    on_stage("transcribing")
    words, lang, duration = speech.transcribe(path, model=model, language=language)
    on_stage("eye_contact")
    samples = None
    try:
        samples = face.sample_video(path, face.ensure_model(store.root)) or None
        if samples and all(s.state == "none" for s in samples):
            samples = None  # audio-only card, covered camera, slides: not a 0% score
            notes.append("No face was visible in the video, so eye contact was not measured.")
    except face.FaceUnavailable as exc:
        notes.append(f"Eye contact was not measured: {exc}")
    except Exception:
        logger.exception("rehearse: face analysis failed for %s", path)
        notes.append("Eye contact was not measured: the video could not be read.")
    report = delivery.analyze(words, duration=duration, face=samples, target_seconds=target).as_dict()
    report["language"] = lang
    report["measured"] = "audio+video" if samples else "audio"
    transcript = " ".join(w.text.strip() for w in words)
    return report, transcript, notes


def _measure_text(text: str, target) -> dict:
    report = delivery.analyze(delivery.words_from_text(text), target_seconds=target).as_dict()
    # Timings were synthesized from a text transcript: only word choice is real.
    for key in ("wpm", "long_pauses", "longest_pause", "start_delay", "pace_timeline", "duration"):
        report[key] = None
    report["moments"] = [m for m in report["moments"] if m["kind"] in ("filler", "repeat")]
    report["scores"] = {"fillers": report["scores"]["fillers"]}
    report["score"] = report["scores"]["fillers"]
    report["measured"] = "text"
    return report


def analyze(llm, store: Store, *, session_id: str, question_index: int, media_path: str = "",
            answer_text: str = "", model: str = "base", language: str = "", keep_recording: bool = True,
            on_stage=lambda stage: None, presence: dict | None = None) -> dict:
    session = store.load(session_id)
    questions = session["questions"]
    if not 0 <= question_index < len(questions):
        raise ValueError(f"question_index must be between 0 and {len(questions) - 1}")
    question = questions[question_index]
    target = _target(question)

    notes: list[str] = []
    if media_path:
        report, transcript, notes = _measure_media(store, media_path, model=model, language=language, target=target,
                                                   on_stage=on_stage)
    elif answer_text.strip():
        report, transcript = _measure_text(answer_text, target), answer_text.strip()
        notes.append("Measured from text only: pace, pauses and eye contact need a recording.")
    else:
        raise ValueError("Give a recording (media_path) or the answer as text (answer_text).")

    on_stage("feedback")
    text_only = report["measured"] == "text"
    fb = None
    if not transcript.strip():
        # A muted or wrong microphone: say so, rather than ask the model to judge an empty answer.
        notes.append("No speech was heard in this take. Check that the right microphone is selected and not muted.")
    else:
        try:
            fb = coach.feedback(llm, scenario=scenario_of(session), subject=session["subject"],
                                question=question["question"], transcript=transcript,
                                delivery=None if text_only else report)
        except coach.CoachError as exc:
            if text_only:
                raise  # a text answer has nothing to score without the model
            notes.append(f"Content feedback is missing: {exc}")  # the measured delivery is still worth keeping
    if fb is None:
        overall = report["score"]
    else:
        overall = fb["content_score"] if text_only else round((report["score"] + fb["content_score"]) / 2)

    previous = best_by_question(session).get(question_index)
    recording = ""
    if media_path and keep_recording:
        recording = _keep(store, session_id, media_path, question_index)
    elif media_path and _inside(store, media_path):
        Path(media_path).unlink(missing_ok=True)  # an upload the user chose not to keep

    attempt = store.add_attempt(session_id, {
        "question_index": question_index, "question": question["question"], "transcript": transcript,
        "delivery": report, "feedback": fb, "overall": overall, "recording": recording, "notes": notes,
        "presence": presence,
        "improvement": None if previous is None else overall - previous.get("overall", 0),
    })
    return attempt


def _keep(store: Store, session_id: str, media_path: str, question_index: int) -> str:
    src = Path(media_path)
    dest_dir = store.recordings_dir(session_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Unique per take: live-interview answers are scored side by side, so a count read at the start collides.
    dest = dest_dir / f"q{question_index + 1:02d}-{uuid.uuid4().hex[:8]}{src.suffix or '.webm'}"
    if _inside(store, media_path):
        shutil.move(str(src), dest)
    else:
        shutil.copy2(src, dest)
    return str(dest.relative_to(store.root))


def _inside(store: Store, path: str) -> bool:
    return Path(path).resolve().is_relative_to(store.root.resolve())


def progress(store: Store) -> dict:
    rows = store.list()
    scored = [r for r in rows if r["score"] is not None]
    trend = [{"id": r["id"], "title": r["title"], "score": r["score"], "fillers_per_minute": r["fillers_per_minute"]}
             for r in reversed(scored[:10])]
    return {
        "sessions": len(rows), "practised": len(scored), "trend": trend,
        "first_score": trend[0]["score"] if trend else None, "latest_score": trend[-1]["score"] if trend else None,
    }
