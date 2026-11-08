"""The interviewer and the coach: question plans and content feedback through ``ctx.llm``.

Runs on whatever model the user already has selected; no keys of its own.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

MODES = {
    "interview": "a job interview",
    "presentation": "a presentation, talk or speech, followed by audience questions",
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "opening": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why": {"type": "string"},
                    "target_seconds": {"type": "integer"},
                    "tip": {"type": "string"},
                },
                "required": ["question", "why", "target_seconds", "tip"],
            },
        },
    },
    "required": ["title", "opening", "questions"],
}

FEEDBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "content_score": {"type": "integer"},
        "summary": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "improvements": {"type": "array", "items": {"type": "string"}},
        "stronger_answer": {"type": "string"},
        "follow_up": {"type": "string"},
    },
    "required": ["content_score", "summary", "strengths", "improvements", "stronger_answer", "follow_up"],
}

_PLAN_INSTRUCTIONS = """You are a realistic, encouraging practice partner for {what}.
Write a rehearsal plan for: {subject}.
Extra context from the user (may be empty): {details}

Rules:
- {count} questions, in the order a real {asker} would ask them, opening easy and building to the hard ones.
- {mode_rule}
- target_seconds is a realistic ideal answer length.
- why: one sentence on what the asker is really testing. tip: one concrete, actionable sentence.
- opening: one or two warm sentences the {asker} says before the first question, spoken aloud.
- Plain spoken English, no markdown."""

# Only the active mode's rule goes in the prompt: given both, models apply both.
_MODE_RULES = {
    "interview": ("Mix background, behavioural (\"tell me about a time\"), role-specific and one curveball "
                  "question; behavioural answers run 60-120 seconds. Use the role, seniority and any job description "
                  "given; never invent facts about the user."),
    "presentation": ("Question 1 is exactly \"Deliver your presentation.\" with target_seconds set to the talk "
                     "length the user gave (default 300). The rest are the questions a sharp audience member would ask "
                     "about this topic afterwards, 30-90 seconds each."),
}

_FEEDBACK_INSTRUCTIONS = """You are a candid, kind coach reviewing one practice answer in {what} about: {subject}.
Judge only the CONTENT here; delivery numbers are measured separately and given for context.

Score content_score 0-100 on: answers the actual question, structure (e.g. situation, task, action, result),
specific evidence and numbers, relevance to {subject}, and a clear ending.
- summary: one sentence verdict.
- strengths: 1-3 specific things that worked, quoting the answer where possible.
- improvements: 1-3 specific, actionable changes, most important first. Use the delivery numbers when they
  explain a problem (e.g. rushing through the result).
- stronger_answer: a short outline (3-5 lines) of a stronger version built ONLY from what the user said; mark
  any gap they should fill with [brackets] instead of inventing facts.
- follow_up: the follow-up question a real {asker} would most likely ask next.
Plain English, no markdown headings."""


class CoachError(RuntimeError):
    """The model returned nothing usable, or could not be reached."""


def _asker(mode: str) -> str:
    return "interviewer" if mode == "interview" else "audience member"


def friendly(exc: Exception) -> str:
    """One plain sentence for a failed model call (rate limits, auth, timeouts all arrive as provider errors)."""
    text = str(exc).lower()
    if "429" in text or "rate limit" in text or "rate_limited" in text:
        return "Your AI model is busy right now (rate-limited). Wait a few minutes or switch models, then try again."
    if "401" in text or "403" in text or "auth" in text:
        return "Your AI model rejected the request. Check you are signed in to your provider in Hermes."
    if "timeout" in text or "timed out" in text:
        return "Your AI model took too long to answer. Try again, or switch to a faster model."
    return "Your AI model could not be reached. Try again in a moment or switch models."


def _call(llm, **kwargs):
    try:
        return llm.complete_structured(**kwargs)
    except Exception as exc:  # provider SDKs raise their own types; the user needs the sentence, the log keeps the rest
        logger.warning("rehearse: %s failed: %s", kwargs.get("purpose"), exc)
        raise CoachError(friendly(exc)) from exc


def plan(llm, *, mode: str, subject: str, details: str = "", count: int = 5) -> dict:
    count = max(2, min(int(count or 5), 10))
    result = _call(
        llm,
        instructions=_PLAN_INSTRUCTIONS.format(
            what=MODES[mode], subject=subject, details=details or "(none)", count=count, asker=_asker(mode),
            mode_rule=_MODE_RULES[mode]),
        input=[{"type": "text", "text": f"Mode: {mode}\nSubject: {subject}\nDetails: {details or '(none)'}"}],
        json_schema=PLAN_SCHEMA, schema_name="rehearsal_plan", purpose="rehearse.plan",
        temperature=0.7, max_tokens=1800,
    )
    data = result.parsed
    if not isinstance(data, dict) or not data.get("questions"):
        raise CoachError("The model did not return a question plan. Try again or switch models.")
    for q in data["questions"]:
        q["target_seconds"] = max(15, min(int(q.get("target_seconds") or 90), 1800))
    return data


def feedback(llm, *, mode: str, subject: str, question: str, transcript: str, delivery: dict | None) -> dict:
    numbers = _delivery_summary(delivery) if delivery else "(text answer: no audio measurements)"
    result = _call(
        llm,
        instructions=_FEEDBACK_INSTRUCTIONS.format(what=MODES[mode], subject=subject, asker=_asker(mode)),
        input=[{"type": "text", "text": f"Question: {question}\n\nAnswer transcript:\n{transcript or '(silence)'}"
                f"\n\nMeasured delivery: {numbers}"}],
        json_schema=FEEDBACK_SCHEMA, schema_name="answer_feedback", purpose="rehearse.feedback",
        temperature=0.3, max_tokens=1200,
    )
    data = result.parsed
    if not isinstance(data, dict) or "content_score" not in data:
        raise CoachError("The model did not return feedback. Try again or switch models.")
    data["content_score"] = max(0, min(int(data["content_score"]), 100))
    return data


def _delivery_summary(d: dict) -> str:
    keep = ("duration", "wpm", "filler_count", "fillers", "long_pauses", "facing_ratio", "start_delay")
    return json.dumps({k: d.get(k) for k in keep})
