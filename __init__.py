"""Rehearse: Hermes interviews you out loud and coaches your delivery and content."""

from __future__ import annotations

import logging
from pathlib import Path

from . import bridge, coach, pipeline, tools
from .sessions import Store

logger = logging.getLogger(__name__)

PLUGIN_ID = "rehearse"

_HELP = """Rehearse: practise out loud, get a scored replay.

  /rehearse <kind> <what for>     e.g. /rehearse job junior data analyst at a bank
                                       /rehearse visa student visa for a master's in Canada
                                       /rehearse demo my Hermes plugin, 90 seconds
  /rehearse history               your scores over time

Kinds: {kinds}

Best in Hermes Desktop: open the Rehearse page to answer on camera and see filler words,
pace, pauses and eye contact on a timeline. In chat or Telegram, answer by text or voice note.
Everything is analysed on this computer; recordings stay here and can be deleted."""


def _help() -> str:
    return _HELP.format(kinds=", ".join(coach.SCENARIOS))


class Runtime:
    """Per-plugin wiring resolved at call time, so it follows the active profile."""

    def __init__(self, ctx):
        self._ctx = ctx

    def store(self) -> Store:
        from plugins.plugin_storage import plugin_data_dir

        return Store(Path(plugin_data_dir(PLUGIN_ID)))

    def speech_settings(self) -> dict:
        return {
            "model": str(self._ctx.get_config("whisper_model", default="base") or "base"),
            "language": str(self._ctx.get_config("language", default="") or ""),
            "keep_recording": bool(self._ctx.get_config("keep_recordings", default=True)),
        }


def _format_plan(session: dict) -> str:
    lines = [f"{session['title']}  (session {session['id']})", "", session["opening"], ""]
    for i, q in enumerate(session["questions"], 1):
        lines.append(f"{i}. {q['question']}  (~{q['target_seconds']}s)")
    lines += ["", "Answer question 1 when you're ready: reply by text or voice note, or open the Rehearse "
              "page in Hermes Desktop to answer on camera."]
    return "\n".join(lines)


def _format_history(data: dict) -> str:
    if not data["trend"]:
        return "No rehearsals scored yet. Try: /rehearse job <role>"
    lines = [f"{data['practised']} rehearsals scored. First {data['first_score']} → latest {data['latest_score']}.", ""]
    for row in data["trend"]:
        fpm = f", {row['fillers_per_minute']} fillers/min" if row["fillers_per_minute"] is not None else ""
        lines.append(f"  {row['score']:>3}  {row['title']}{fpm}")
    return "\n".join(lines)


# The Desktop page's backend calls, set by register(). dashboard/plugin_api.py runs in this same
# process and finds this module by its file path; see bridge.py for why calls take that route.
APP: dict | None = None


def handle_app(method: str, params: dict) -> dict:
    if APP is None:
        return {"error": "Rehearse is not enabled for this Hermes profile. Enable it in Capabilities → Plugins."}
    return bridge.call(APP, method, params)


def _command(ctx, runtime: Runtime, raw: str) -> str:
    kind, _, subject = (raw or "").strip().partition(" ")
    kind = kind.lower()
    if kind == "history":
        return _format_history(pipeline.progress(runtime.store()))
    try:
        scenario = coach.resolve(kind)
    except ValueError:
        return _help()
    if not subject.strip():
        return _help()
    try:
        session = pipeline.start(ctx.llm, runtime.store(), scenario=scenario, subject=subject)
    except (ValueError, coach.CoachError) as exc:
        return str(exc)
    return _format_plan(session)


def register(ctx):
    runtime = Runtime(ctx)
    handlers = tools.make_handlers(ctx, runtime)
    for schema in (tools.START, tools.ANALYZE, tools.PROGRESS):
        ctx.register_tool(name=schema["name"], toolset=PLUGIN_ID, schema=schema, handler=handlers[schema["name"]])

    global APP
    APP = bridge.methods(ctx, runtime)
    ctx.register_command(PLUGIN_ID, lambda raw: _command(ctx, runtime, raw),
                         description="Practise an interview or presentation out loud",
                         args_hint="<job|technical|visa|pitch|demo|...> <what for> | history")

    skill = Path(__file__).parent / "skills" / "rehearse-coach" / "SKILL.md"
    ctx.register_skill("rehearse-coach", skill)
