"""The questioner and the coach: question plans and content feedback through ``ctx.llm``.

Runs on whatever model the user already has selected; no keys of its own. Each scenario
brings its own questioner, question style and judging criteria, so a visa officer, a case
interviewer and an investor each sound like themselves.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Scenario:
    mode: str  # interview | presentation: drives the opening "deliver it" take and target lengths
    label: str
    what: str
    asker: str
    style: str  # what the questioner asks and how
    judge: str  # what good content looks like here


SCENARIOS: dict[str, Scenario] = {
    "job": Scenario(
        "interview", "Job interview", "a job interview", "interviewer",
        "Mix background, behavioural (\"tell me about a time\"), role-specific and one curveball question. "
        "Behavioural answers run 60-120 seconds.",
        "answers the actual question; situation-task-action-result structure; specific evidence and numbers; "
        "relevance to the role; a clear ending"),
    "technical": Scenario(
        "interview", "Technical interview", "a technical interview, answered out loud", "technical interviewer",
        "Ask the candidate to explain concepts, walk through how they would design or debug something, and talk "
        "through trade-offs. No whiteboard: every question must be answerable by speaking. 60-180 seconds each.",
        "correctness; thinking out loud in clear steps; naming trade-offs and assumptions; checking edge cases; "
        "concise explanations a colleague could follow"),
    "case": Scenario(
        "interview", "Case interview", "a consulting or product case interview", "case interviewer",
        "Open with one business case, then probe: structure the problem, estimate a number, interpret a finding, "
        "recommend. 60-180 seconds each.",
        "a clear structure stated up front; hypothesis-driven reasoning; sensible estimates shown step by step; "
        "a decisive recommendation with risks"),
    "admission": Scenario(
        "interview", "University or scholarship", "a university, programme or scholarship admission interview",
        "admissions panel member",
        "Motivation, academic interests, a challenge overcome, contribution to the community, future goals, and "
        "one thoughtful curveball. 60-120 seconds each.",
        "genuine motivation; specific examples from the applicant's own life; reflection and growth; clear goals "
        "linked to this programme"),
    "visa": Scenario(
        "interview", "Visa or immigration", "a visa or immigration interview", "consular officer",
        "Short, direct questions a consular officer asks about purpose of travel, ties to home, finances, plans and "
        "the specific programme or job. Answers run 15-45 seconds.",
        "truthful, direct, consistent answers; short and specific; calm and confident. Never suggest altering, "
        "embellishing or omitting facts"),
    "residency": Scenario(
        "interview", "Medical or residency", "a medical school, residency or clinical job interview", "interviewer",
        "Motivation for medicine and the specialty, a clinical or ethical scenario, teamwork, handling a mistake, "
        "and patient communication. 60-120 seconds each.",
        "empathy and patient focus; sound ethical reasoning; specific experiences; honesty about mistakes and what "
        "was learnt"),
    "media": Scenario(
        "interview", "Media or press", "a media, press or TV interview", "journalist",
        "A journalist's questions, including one tough or awkward one and one that invites a quotable answer. "
        "Answers run 20-60 seconds.",
        "a clear key message stated early; short quotable sentences; staying calm and on message under a tough "
        "question; no jargon"),
    "podcast": Scenario(
        "interview", "Podcast guest", "an appearance as a podcast guest", "podcast host",
        "A friendly host's questions: the guest's story, their expertise, a story listeners will remember, a "
        "practical takeaway, and a closing question. 60-150 seconds each.",
        "storytelling with a hook; concrete examples; a useful takeaway; conversational, not scripted"),
    "presentation": Scenario(
        "presentation", "Presentation", "a presentation or talk, followed by audience questions", "audience member",
        "The questions a sharp audience member would ask afterwards, 30-90 seconds each.",
        "a clear point stated early; logical flow; evidence; a memorable close; answers that address the "
        "question asked"),
    "pitch": Scenario(
        "presentation", "Investor pitch", "a startup pitch to investors, followed by investor questions", "investor",
        "Tough investor questions afterwards: market size, traction, competition, why this team, business model, "
        "the ask. 30-90 seconds each.",
        "the problem and the customer made vivid; traction and numbers; why now and why this team; a specific ask; "
        "direct answers without hedging"),
    "demo": Scenario(
        "presentation", "Product demo", "a live or recorded product demo, followed by questions", "viewer",
        "The questions a judge, customer or user asks after seeing the demo: what it does, who it is for, how it "
        "works, what is next. 30-60 seconds each.",
        "showing the value in the first seconds; one clear story through the product; the wow moment; plain "
        "language; ending on what to do next"),
    "sales": Scenario(
        "presentation", "Sales pitch", "a sales pitch to a prospective customer", "prospective customer",
        "The objections and questions a buyer raises: price, fit, risk, timing, alternatives. 30-90 seconds each.",
        "focus on the customer's problem; specific outcomes and proof; handling objections without getting "
        "defensive; a clear next step"),
    "academic": Scenario(
        "presentation", "Class talk or thesis defence", "a class presentation or thesis defence, followed by "
        "questions from the committee", "committee member",
        "Questions on method, evidence, limitations, alternative explanations and contribution. 45-120 seconds "
        "each.",
        "a clear research question; justified method; honest limitations; precise answers that engage with the "
        "challenge"),
    "speech": Scenario(
        "presentation", "Speech or toast", "a speech or toast at an event", "guest",
        "After the speech, two or three warm follow-ups a guest might ask, 20-45 seconds each.",
        "warmth and sincerity; one or two vivid stories; right length for the room; a clear, heartfelt close"),
}

# Back-compat with the original two modes and the chat command's words.
ALIASES = {"interview": "job", "talk": "presentation", "toast": "speech", "thesis": "academic",
           "defence": "academic", "defense": "academic", "university": "admission", "scholarship": "admission",
           "immigration": "visa", "medical": "residency", "press": "media", "investor": "pitch"}

MODES = {"interview": "an interview", "presentation": "a presentation or talk, followed by questions"}

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

_INTEGRITY = ("Never invent facts about the user, and never help them misrepresent, embellish or hide facts: "
              "coach honest answers told well.")

_PLAN_INSTRUCTIONS = """You are a realistic, encouraging practice partner for {what}.
Write a rehearsal plan for: {subject}.
Extra context from the user (may be empty): {details}

Rules:
- {count} questions, in the order a real {asker} would ask them, opening easy and building to the hard ones.
- {opening_rule}
- Question style: {style}
- target_seconds is a realistic ideal answer length for that question.
- why: one sentence on what the {asker} is really testing. tip: one concrete, actionable sentence.
- opening: one or two warm sentences the {asker} says before the first question, spoken aloud.
- {integrity}
- Write everything in the language of the subject and details (English if unclear). Plain spoken
  sentences, no markdown."""

_OPENING_RULES = {
    "interview": "Every item is a question the {asker} asks.",
    "presentation": ("Item 1 is exactly \"{deliver}\" with target_seconds set to the length the user gave "
                     "(default {default_len}). The rest are questions asked afterwards."),
}
_DELIVER = {"demo": ("Give your demo.", 180), "pitch": ("Give your pitch.", 180), "sales": ("Give your pitch.", 180),
            "speech": ("Give your speech.", 180), "academic": ("Give your presentation.", 600)}

_FEEDBACK_INSTRUCTIONS = """You are a candid, kind coach reviewing one practice answer in {what} about: {subject}.
Judge only the CONTENT here; delivery numbers are measured separately and given for context.

Score content_score 0-100 on what good looks like here: {judge}.
- summary: one sentence verdict.
- strengths: 1-3 specific things that worked, quoting the answer where possible.
- improvements: 1-3 specific, actionable changes, most important first. Use the delivery numbers when they
  explain a problem (e.g. rushing through the result).
- stronger_answer: a short outline (3-5 lines) of a stronger version built ONLY from what the user said; mark
  any gap they should fill with [brackets].
- follow_up: the follow-up question a real {asker} would most likely ask next.
- {integrity}
Write in the language of the answer. Plain sentences, no markdown headings."""


class CoachError(RuntimeError):
    """The model returned nothing usable, or could not be reached."""


def resolve(name: str) -> str:
    """Scenario key for a scenario name, alias or legacy mode; ``ValueError`` when unknown."""
    key = ALIASES.get((name or "").strip().lower(), (name or "").strip().lower())
    if key not in SCENARIOS:
        raise ValueError(f"Pick one of: {', '.join(s.label for s in SCENARIOS.values())}.")
    return key


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


def plan(llm, *, scenario: str, subject: str, details: str = "", count: int = 5) -> dict:
    s = SCENARIOS[scenario]
    count = max(2, min(int(count or 5), 10))
    deliver, default_len = _DELIVER.get(scenario, ("Deliver your presentation.", 300))
    opening_rule = _OPENING_RULES[s.mode].format(asker=s.asker, deliver=deliver, default_len=default_len)
    result = _call(
        llm,
        instructions=_PLAN_INSTRUCTIONS.format(
            what=s.what, subject=subject, details=details or "(none)", count=count, asker=s.asker,
            opening_rule=opening_rule, style=s.style, integrity=_INTEGRITY),
        input=[{"type": "text", "text": f"Scenario: {s.label}\nSubject: {subject}\nDetails: {details or '(none)'}"}],
        json_schema=PLAN_SCHEMA, schema_name="rehearsal_plan", purpose="rehearse.plan",
        temperature=0.7, max_tokens=1800,
    )
    data = result.parsed
    questions = [q for q in (data.get("questions") if isinstance(data, dict) else None) or []
                 if isinstance(q, dict) and isinstance(q.get("question"), str) and q["question"].strip()]
    if not questions:
        raise CoachError("The model did not return a question plan. Try again or switch models.")
    for q in questions:
        q["target_seconds"] = max(15, min(_as_int(q.get("target_seconds"), 90), 1800))
        q.setdefault("tip", "")
        q.setdefault("why", "")
    data["questions"] = questions
    return data


def _as_int(value, default: int) -> int:
    """Models sometimes answer "85/100" or "90 seconds" where a number was asked for."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    digits = re.match(r"\s*(\d+)", str(value or ""))
    return int(digits.group(1)) if digits else default


def feedback(llm, *, scenario: str, subject: str, question: str, transcript: str, delivery: dict | None) -> dict:
    s = SCENARIOS[scenario]
    numbers = _delivery_summary(delivery) if delivery else "(text answer: no audio measurements)"
    result = _call(
        llm,
        instructions=_FEEDBACK_INSTRUCTIONS.format(what=s.what, subject=subject, asker=s.asker, judge=s.judge,
                                                   integrity=_INTEGRITY),
        input=[{"type": "text", "text": f"Question: {question}\n\nAnswer transcript:\n{transcript or '(silence)'}"
                f"\n\nMeasured delivery: {numbers}"}],
        json_schema=FEEDBACK_SCHEMA, schema_name="answer_feedback", purpose="rehearse.feedback",
        temperature=0.3, max_tokens=1200,
    )
    data = result.parsed
    if not isinstance(data, dict) or "content_score" not in data:
        raise CoachError("The model did not return feedback. Try again or switch models.")
    data["content_score"] = max(0, min(_as_int(data["content_score"], 0), 100))
    return data


def _delivery_summary(d: dict) -> str:
    keep = ("duration", "wpm", "filler_count", "fillers", "long_pauses", "facing_ratio", "start_delay")
    return json.dumps({k: d.get(k) for k in keep})


def catalog() -> list[dict]:
    """The scenario list the Desktop page renders."""
    return [{"key": k, "mode": s.mode, "label": s.label} for k, s in SCENARIOS.items()]


REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["strong", "promising", "not_yet"]},
        "headline": {"type": "string"},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "fixes": {"type": "array", "items": {"type": "string"}},
        "best_question": {"type": "integer"},
        "next_practice": {"type": "string"},
    },
    "required": ["verdict", "headline", "strengths", "fixes", "best_question", "next_practice"],
}

_REPORT_INSTRUCTIONS = """You are the {asker} debriefing a full practice session of {what} about: {subject}.
You get every question, the candidate's answer transcript, and the measured delivery for each answer.

The measured interview score is {score}/100.
Write the debrief a candid, kind hiring panel would give:
- verdict: strong (would advance, score 75+), promising (close, needs polish, 55-74) or not_yet (below 55).
- headline: one sentence on how the whole session came across.
- strengths: the 3 strongest things across ALL answers, specific, quoting the candidate where possible.
- fixes: the 3 changes that would most improve the next real {what_short}, most important first; use the
  delivery numbers (fillers, pace, eye contact, presence) where they matter.
- best_question: the number of the best answer (Question 1 is 1). Refer to questions by these numbers.
- next_practice: one sentence on exactly what to rehearse next.
- {integrity}
Write in the language of the answers. Plain sentences, no markdown."""


def verdict_for(score: int | None) -> str | None:
    """The verdict follows the measured score, so the badge never contradicts the number beside it."""
    if score is None:
        return None
    return "strong" if score >= 75 else "promising" if score >= 55 else "not_yet"


def interview_report(llm, *, scenario: str, subject: str, answers: list[dict], score: int | None = None) -> dict:
    s = SCENARIOS[scenario]
    body = "\n\n".join(
        f"Question {i + 1}: {a['question']}\nAnswer: {a['transcript'] or '(no answer)'}\nDelivery: {json.dumps(a['numbers'])}"
        for i, a in enumerate(answers))
    result = _call(
        llm,
        instructions=_REPORT_INSTRUCTIONS.format(asker=s.asker, what=s.what, what_short=s.label.lower(),
                                                 subject=subject, integrity=_INTEGRITY,
                                                 score="unknown" if score is None else score),
        input=[{"type": "text", "text": body}], json_schema=REPORT_SCHEMA, schema_name="interview_report",
        purpose="rehearse.report", temperature=0.3, max_tokens=1400,
    )
    data = result.parsed
    if not isinstance(data, dict) or data.get("verdict") not in ("strong", "promising", "not_yet"):
        raise CoachError("The model did not return a debrief. Try again or switch models.")
    data["best_question"] = max(0, min(_as_int(data.get("best_question"), 1) - 1, len(answers) - 1))  # stored 0-based
    data["verdict"] = verdict_for(score) or data["verdict"]
    return data
