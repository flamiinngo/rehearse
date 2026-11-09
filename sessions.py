"""Rehearsal sessions on disk: one JSON file per session, recordings beside it.

Lives under the plugin's profile-scoped data root, so it survives plugin updates and
stays out of other profiles. Everything a user recorded is listed and deletable.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

_lock = threading.RLock()  # re-entrant: add_attempt holds it across load + save


class SessionNotFound(KeyError):
    pass


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    def recordings_dir(self, session_id: str) -> Path:
        return self.root / "recordings" / _safe_id(session_id)

    def create(self, *, scenario: str, mode: str, subject: str, details: str, plan: dict) -> dict:
        session = {
            "id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
            "created_at": time.time(), "scenario": scenario, "mode": mode, "subject": subject, "details": details,
            "title": plan.get("title") or subject, "opening": plan.get("opening", ""),
            "questions": plan["questions"], "attempts": [],
        }
        self.save(session)
        return session

    def load(self, session_id: str) -> dict:
        path = self.sessions_dir / f"{_safe_id(session_id)}.json"
        try:
            with _lock:  # Windows refuses os.replace onto a file another thread is reading
                return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise SessionNotFound(session_id) from None

    def save(self, session: dict) -> None:
        with _lock:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
            path = self.sessions_dir / f"{_safe_id(session['id'])}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(session, indent=1), encoding="utf-8")
            os.replace(tmp, path)

    def latest(self) -> dict | None:
        sessions = self.list()
        return self.load(sessions[0]["id"]) if sessions else None

    def list(self) -> list[dict]:
        if not self.sessions_dir.exists():
            return []
        rows = []
        for path in self.sessions_dir.glob("*.json"):
            try:
                with _lock:
                    s = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows.append(summarize(s))
        return sorted(rows, key=lambda r: r["created_at"], reverse=True)

    def add_attempt(self, session_id: str, attempt: dict) -> dict:
        with _lock:  # answers of a live interview finish concurrently: never lose one
            session = self.load(session_id)
            attempt = {"n": len(session["attempts"]) + 1, "at": time.time(), **attempt}
            session["attempts"].append(attempt)
            self.save(session)
        return attempt

    def update(self, session_id: str, **fields) -> dict:
        with _lock:
            session = self.load(session_id)
            session.update(fields)
            self.save(session)
        return session

    def delete(self, session_id: str) -> None:
        (self.sessions_dir / f"{_safe_id(session_id)}.json").unlink(missing_ok=True)
        shutil.rmtree(self.recordings_dir(session_id), ignore_errors=True)


def _safe_id(session_id: str) -> str:
    clean = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    if not clean:
        raise SessionNotFound(session_id)
    return clean


def best_by_question(session: dict) -> dict[int, dict]:
    best: dict[int, dict] = {}
    for a in session.get("attempts", []):
        q = a.get("question_index", 0)
        if q not in best or a.get("overall", 0) > best[q].get("overall", 0):
            best[q] = a
    return best


def summarize(session: dict) -> dict:
    attempts = session.get("attempts", [])
    best = best_by_question(session)
    avg = round(sum(a.get("overall", 0) for a in best.values()) / len(best)) if best else None
    fillers = [a["delivery"]["fillers_per_minute"] for a in attempts if a.get("delivery")]
    return {
        "id": session["id"], "created_at": session["created_at"], "mode": session["mode"],
        "scenario": session.get("scenario") or ("job" if session["mode"] == "interview" else "presentation"),
        "title": session.get("title", session["subject"]), "subject": session["subject"],
        "questions": len(session.get("questions", [])), "answered": len(best), "attempts": len(attempts),
        "score": avg, "fillers_per_minute": round(sum(fillers) / len(fillers), 2) if fillers else None,
    }


def latest_by_question(session: dict) -> dict[int, dict]:
    latest: dict[int, dict] = {}
    for a in session.get("attempts", []):
        latest[a.get("question_index", 0)] = a
    return latest


def interview_numbers(session: dict) -> dict:
    """Aggregates across the latest answer to every question: the measured half of the final account."""
    takes = latest_by_question(session)
    rows, fillers, wpm, eye, live, cues, steadiness = [], [], [], [], [], {}, []
    for i, q in enumerate(session.get("questions", [])):
        a = takes.get(i)
        if a is None:
            rows.append({"index": i, "question": q["question"], "answered": False})
            continue
        d = a.get("delivery") or {}
        p = a.get("presence") or {}
        rows.append({"index": i, "question": q["question"], "answered": True, "overall": a.get("overall"),
                     "fillers": d.get("filler_count"), "wpm": d.get("wpm"), "facing": d.get("facing_ratio"),
                     "summary": (a.get("feedback") or {}).get("summary")})
        if d.get("fillers_per_minute") is not None:
            fillers.append(d["fillers_per_minute"])
        if d.get("wpm"):
            wpm.append(d["wpm"])
        if d.get("facing_ratio") is not None:
            eye.append(d["facing_ratio"])
        if p.get("facing_ratio") is not None:
            live.append(p["facing_ratio"])
        for cue, n in (p.get("cues") or {}).items():
            cues[cue] = cues.get(cue, 0) + n
        if p.get("steadiness"):
            steadiness.append(p["steadiness"])
    scored = [r["overall"] for r in rows if r.get("answered") and r.get("overall") is not None]
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    return {
        "questions": rows, "answered": len(scored), "score": round(sum(scored) / len(scored)) if scored else None,
        "fillers_per_minute": avg(fillers), "wpm": avg(wpm), "eye_contact": avg(eye), "live_facing": avg(live),
        "top_cues": sorted(cues.items(), key=lambda kv: -kv[1])[:3],
        "composure": _composure(steadiness),
    }


def _composure(per_answer: list[str]) -> str | None:
    """The interview's head movement: steady unless most answers were not."""
    if not per_answer:
        return None
    restless = sum(1 for s in per_answer if s != "steady")
    if restless * 2 <= len(per_answer):
        return "steady"
    return "fidgety" if sum(1 for s in per_answer if s == "fidgety") * 2 > len(per_answer) else "a little restless"
