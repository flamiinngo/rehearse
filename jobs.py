"""Background jobs for the Desktop page.

A plugin RPC call holds the client's request lane until it returns, so slow work
(an LLM plan, transcribing a take) runs on a thread and the page polls
``job.status``. Polling also works unchanged under plugin host isolation.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Callable

logger = logging.getLogger(__name__)

_KEEP_SECONDS = 15 * 60
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def submit(kind: str, work: Callable[[Callable[[str], None]], dict]) -> str:
    """Run ``work(set_stage)`` on a thread; returns the job id at once."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _prune()
        _jobs[job_id] = {"id": job_id, "kind": kind, "state": "running", "stage": "starting",
                         "started": time.time(), "result": None, "error": None}

    def set_stage(stage: str) -> None:
        with _lock:
            _jobs[job_id]["stage"] = stage

    def run() -> None:
        try:
            result = work(set_stage)
            update = {"state": "done", "stage": "done", "result": result}
        except Exception as exc:  # surfaced to the page as a readable message
            if not isinstance(exc, (ValueError, RuntimeError, KeyError, FileNotFoundError)):
                logger.exception("rehearse: %s job failed", kind)
            update = {"state": "error", "error": _message(exc)}
        with _lock:
            _jobs[job_id].update(update, finished=time.time())

    threading.Thread(target=run, name=f"rehearse-{kind}", daemon=True).start()
    return job_id


def status(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else {"id": job_id, "state": "missing", "error": "That job is no longer running."}


def _message(exc: Exception) -> str:
    if isinstance(exc, KeyError):
        return "That rehearsal session no longer exists."
    return str(exc) or exc.__class__.__name__


def _prune() -> None:
    cutoff = time.time() - _KEEP_SECONDS
    for job_id in [j for j, job in _jobs.items() if job.get("finished", time.time()) < cutoff]:
        del _jobs[job_id]
