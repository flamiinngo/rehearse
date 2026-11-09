"""Agent-facing tools. Three of them, since every tool schema rides on every API call."""

from __future__ import annotations

import json
import logging

from . import coach, pipeline, speech
from .sessions import SessionNotFound

logger = logging.getLogger(__name__)

START = {
    "name": "rehearse_start",
    "description": (
        "Start a spoken rehearsal for any interview or talk: job, technical, case, admission, visa, medical, "
        "media or podcast interviews; presentations, investor or sales pitches, product demos, thesis "
        "defences, speeches. Returns a session_id and the questions. Then ask the user ONE question at a "
        "time, in order, and analyse each answer with rehearse_analyze before asking the next."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scenario": {"type": "string", "enum": list(coach.SCENARIOS),
                         "description": "The kind of rehearsal; 'job' or 'presentation' when unsure."},
            "subject": {"type": "string", "description": "The role (e.g. 'junior data analyst at a bank') or "
                                                        "the talk title/topic."},
            "details": {"type": "string", "description": "Optional: job description, seniority, company, talk "
                                                        "length, audience. Only what the user said."},
            "count": {"type": "integer", "description": "Number of questions, 2-10 (default 5)."},
        },
        "required": ["scenario", "subject"],
    },
}

ANALYZE = {
    "name": "rehearse_analyze",
    "description": (
        "Score one rehearsal answer: filler words, pace, pauses, answer length, eye contact (from video) "
        "and content feedback with a stronger-answer outline. Pass media_path for a recording (audio or "
        "video file), or answer_text when the answer arrived as text or as a transcribed voice message."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "From rehearse_start."},
            "question_index": {"type": "integer", "description": "0-based index of the question answered."},
            "media_path": {"type": "string", "description": "Path to the answer recording, if there is one."},
            "answer_text": {"type": "string", "description": "The answer as text, if there is no recording."},
        },
        "required": ["session_id", "question_index"],
    },
}

PROGRESS = {
    "name": "rehearse_progress",
    "description": "The user's rehearsal history: scores per session over time and filler-word trend.",
    "parameters": {"type": "object", "properties": {}},
}


def _ok(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"error": message})


def make_handlers(ctx, runtime):
    def start(args: dict, **_):
        try:
            s = pipeline.start(ctx.llm, runtime.store(), scenario=args.get("scenario") or args.get("mode") or "job",
                               subject=args.get("subject", ""), details=args.get("details", ""),
                               count=coach._as_int(args.get("count"), 5))
        except (ValueError, coach.CoachError) as exc:
            return _err(str(exc))
        return _ok({
            "session_id": s["id"], "title": s["title"], "opening": s["opening"],
            "questions": [{"index": i, "question": q["question"], "target_seconds": q["target_seconds"]}
                          for i, q in enumerate(s["questions"])],
            "how_to_run": "Say the opening, then ask question 0. Wait for the answer, call rehearse_analyze, share "
                          "the 2-3 most useful points, offer a retry or move on to the next question.",
            "desktop": ("In Hermes Desktop the user can do this as a live interview on camera: first time, open "
                        "Capabilities → Plugins and switch Rehearse On, then click Rehearse in the sidebar."),
        })

    def analyze(args: dict, **_):
        try:
            a = pipeline.analyze(ctx.llm, runtime.store(), session_id=args.get("session_id", ""),
                                 question_index=coach._as_int(args.get("question_index"), -1),
                                 media_path=args.get("media_path", ""), answer_text=args.get("answer_text", ""),
                                 **runtime.speech_settings())
        except SessionNotFound:
            return _err("No rehearsal session with that id. Start one with rehearse_start.")
        except (ValueError, coach.CoachError, speech.SpeechUnavailable, FileNotFoundError) as exc:
            return _err(str(exc))
        d = a["delivery"]
        return _ok({
            "overall": a["overall"], "improvement_vs_best_previous": a["improvement"],
            "delivery": {k: d.get(k) for k in ("measured", "score", "scores", "duration", "wpm", "filler_count",
                                                 "fillers", "long_pauses", "facing_ratio")},
            "key_moments": d["moments"][:8], "feedback": a["feedback"] or "unavailable (see notes)",
            "transcript": a["transcript"],
            "notes": a["notes"],
        })

    def progress(args: dict, **_):
        return _ok(pipeline.progress(runtime.store()))

    return {"rehearse_start": start, "rehearse_analyze": analyze, "rehearse_progress": progress}
