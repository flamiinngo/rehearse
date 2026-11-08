"""The Desktop page's backend calls, carried by the ``/rehearse`` slash command.

The page runs ``/rehearse app <method> <json>`` through the gateway's
``command.dispatch`` (present in every supported release; plugin JSON-RPC is not
in v0.23.0), and the handler answers with JSON. The command is the one surface
holding ``ctx.llm``. A raising plugin command is swallowed by the dispatcher, so
every outcome, errors included, comes back as JSON.

Any authenticated client can run any command with any arguments, so every input
is validated and nothing reaches beyond this plugin's own data.
"""

from __future__ import annotations

import json
import logging
import re

from . import coach, face, jobs, pipeline, speech
from .sessions import SessionNotFound, summarize

logger = logging.getLogger(__name__)

_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}\.(webm|mp4|m4a|ogg|wav|mov)$")


def _text(params: dict, key: str, limit: int, *, required: bool = False) -> str:
    value = params.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    value = value.strip()[:limit]
    if required and not value:
        raise ValueError(f"{key} is required")
    return value


def _int(params: dict, key: str, default: int = 0) -> int:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be a whole number")
    return value


def methods(ctx, runtime) -> dict:
    def engine_prepare(params, **_):
        settings = runtime.speech_settings()
        if speech.is_ready(settings["model"]) and face.model_ready(runtime.store().root):
            return {"ready": True}

        def work(stage):
            stage("speech_model")
            speech.warm(settings["model"])
            stage("face_model")
            face.ensure_model(runtime.store().root)
            return {"ready": True}

        return {"ready": False, "job": jobs.submit("engine", work)}

    def plan_create(params, **_):
        scenario = _text(params, "scenario", 20) or _text(params, "mode", 20, required=True)
        subject = _text(params, "subject", 300, required=True)
        details = _text(params, "details", 6000)
        count = _int(params, "count", 5)
        store = runtime.store()

        def work(stage):
            stage("planning")
            return pipeline.start(ctx.llm, store, scenario=scenario, subject=subject, details=details, count=count)

        return {"job": jobs.submit("plan", work)}

    def take_analyze(params, **_):
        session_id = _text(params, "session_id", 64, required=True)
        question_index = _int(params, "question_index")
        upload_id = _text(params, "upload_id", 64, required=True)
        if not _UPLOAD_ID.match(upload_id):
            raise ValueError("upload_id is not a Rehearse upload")
        store = runtime.store()
        media = store.root / "uploads" / upload_id
        if not media.is_file():
            raise ValueError("That recording was not found. Record the answer again.")

        def work(stage):
            return pipeline.analyze(ctx.llm, store, session_id=session_id, question_index=question_index,
                                    media_path=str(media), on_stage=stage, **runtime.speech_settings())

        return {"job": jobs.submit("analyze", work)}

    def job_status(params, **_):
        return jobs.status(_text(params, "job", 32, required=True))

    def sessions_list(params, **_):
        return {"sessions": runtime.store().list(), "progress": pipeline.progress(runtime.store())}

    def session_get(params, **_):
        try:
            session = runtime.store().load(_text(params, "session_id", 64, required=True))
        except SessionNotFound:
            raise ValueError("That rehearsal session no longer exists.") from None
        return {"session": session, "summary": summarize(session)}

    def session_delete(params, **_):
        runtime.store().delete(_text(params, "session_id", 64, required=True))
        return {"deleted": True}

    def scenarios(params, **_):
        return {"scenarios": coach.catalog()}

    return {
        "scenarios": scenarios, "engine.prepare": engine_prepare, "plan.create": plan_create, "take.analyze": take_analyze,
        "job.status": job_status, "sessions.list": sessions_list, "session.get": session_get,
        "session.delete": session_delete,
    }


def handle(table: dict, raw: str) -> str:
    """``<method> <json params>`` → JSON ``{"ok": result}`` or ``{"error": message}``."""
    name, _, payload = (raw or "").strip().partition(" ")
    handler = table.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown method: {name}"})
    try:
        params = json.loads(payload) if payload.strip() else {}
        if not isinstance(params, dict):
            raise ValueError("params must be a JSON object")
        return json.dumps({"ok": handler(params)}, ensure_ascii=False)
    except ValueError as exc:  # JSONDecodeError included
        return json.dumps({"error": str(exc)})
    except Exception:
        logger.exception("rehearse: app call %s failed", name)
        return json.dumps({"error": "Rehearse hit an unexpected problem. Check the Hermes log for details."})
