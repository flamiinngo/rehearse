"""The Desktop page's backend calls, reached through the plugin's own REST namespace.

The page POSTs ``{method, params}`` to ``/api/plugins/rehearse/app``
(``dashboard/plugin_api.py``), which hands it to this table in the loaded plugin.
Two other doors do not work for a page that is not a chat: plugin JSON-RPC is not
in v0.23.0, and ``command.dispatch`` over the Desktop/Webapp connection refuses any
call without a live session (``4001 not_found``, even for ``/help``). The table
lives in the agent half because only ``register(ctx)`` holds ``ctx.llm``.

Any authenticated client can call these with any params, so every input is
validated and nothing reaches beyond this plugin's own data.
"""

from __future__ import annotations

import logging
import re

from . import coach, face, jobs, pipeline, speech, voice
from .sessions import SessionNotFound, summarize

logger = logging.getLogger(__name__)

_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}\.(webm|mp4|m4a|ogg|wav|mov|mp3)$")


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

    def voice_speak(params, **_):
        return voice.speak(ctx, runtime.store().root, _text(params, "text", voice.MAX_CHARS, required=True))

    return {
        "scenarios": scenarios, "voice.speak": voice_speak, "engine.prepare": engine_prepare, "plan.create": plan_create, "take.analyze": take_analyze,
        "job.status": job_status, "sessions.list": sessions_list, "session.get": session_get,
        "session.delete": session_delete,
    }


def call(table: dict, method: str, params) -> dict:
    """``{"ok": result}`` or ``{"error": message}``; never raises."""
    handler = table.get(method)
    if handler is None:
        return {"error": f"unknown method: {method}"}
    try:
        if not isinstance(params, dict):
            raise ValueError("params must be a JSON object")
        return {"ok": handler(params)}
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception:
        logger.exception("rehearse: app call %s failed", method)
        return {"error": "Rehearse hit an unexpected problem. Check the Hermes log for details."}
