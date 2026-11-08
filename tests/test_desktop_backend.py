import importlib.util
import json
import time
from pathlib import Path

import pytest

from rehearse import bridge, jobs
from rehearse.sessions import Store

from test_pipeline import FakeLLM


class FakeCtx:
    def __init__(self):
        self.llm = FakeLLM()


class FakeRuntime:
    def __init__(self, root):
        self.root = root

    def store(self):
        return Store(self.root)

    def speech_settings(self):
        return {"model": "base", "language": "", "keep_recording": True}


def wait(job_id):
    for _ in range(200):
        s = jobs.status(job_id)
        if s["state"] != "running":
            return s
        time.sleep(0.01)
    raise AssertionError("job never finished")


@pytest.fixture
def methods(tmp_path):
    return bridge.methods(FakeCtx(), FakeRuntime(tmp_path))


def test_plan_runs_as_a_background_job_and_lands_in_history(methods):
    started = methods["plan.create"]({"mode": "interview", "subject": "nurse", "count": 3})
    done = wait(started["job"])
    assert done["state"] == "done" and done["result"]["questions"]
    listed = methods["sessions.list"]({})
    assert listed["sessions"][0]["id"] == done["result"]["id"]


@pytest.mark.parametrize("upload_id", ["../../secrets.webm", "C:\\x.webm", "abc.webm", "0" * 32 + ".exe", ""])
def test_analyze_refuses_anything_but_a_rehearse_upload_id(methods, upload_id):
    session = wait(methods["plan.create"]({"mode": "interview", "subject": "nurse"})["job"])["result"]
    with pytest.raises(ValueError):
        methods["take.analyze"]({"session_id": session["id"], "question_index": 0, "upload_id": upload_id})


def test_analyze_explains_a_missing_recording(methods):
    session = wait(methods["plan.create"]({"mode": "interview", "subject": "nurse"})["job"])["result"]
    with pytest.raises(ValueError, match="not found"):
        methods["take.analyze"]({"session_id": session["id"], "question_index": 0, "upload_id": "a" * 32 + ".webm"})


def test_params_are_type_checked(methods):
    with pytest.raises(ValueError):
        methods["plan.create"]({"mode": "interview", "subject": ["not", "text"]})
    with pytest.raises(ValueError):
        methods["plan.create"]({"mode": "interview", "subject": "x", "count": "5"})


def test_bad_plan_surfaces_as_a_readable_job_error(methods):
    done = wait(methods["plan.create"]({"mode": "karaoke", "subject": "x"})["job"])
    assert done["state"] == "error" and "Pick one of" in done["error"]


def test_unknown_job_reports_missing(methods):
    assert methods["job.status"]({"job": "nope"})["state"] == "missing"


def test_delete_session(methods):
    session = wait(methods["plan.create"]({"mode": "presentation", "subject": "Q3"})["job"])["result"]
    methods["session.delete"]({"session_id": session["id"]})
    with pytest.raises(ValueError):
        methods["session.get"]({"session_id": session["id"]})


def test_bridge_always_answers_json_and_never_raises(methods):
    call = lambda raw: json.loads(bridge.handle(methods, raw))
    assert call('job.status {"job": "x"}')["ok"]["state"] == "missing"
    assert "unknown method" in call("format.disk {}")["error"]
    assert call("plan.create {not json")["error"]
    assert call('plan.create ["a"]')["error"] == "params must be a JSON object"
    assert call('plan.create {"mode": "interview"}')["error"] == "subject is required"

    def boom(params):
        raise ZeroDivisionError

    assert "unexpected problem" in json.loads(bridge.handle({"x": boom}, "x {}"))["error"]


# ---- upload endpoint ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    path = Path(__file__).resolve().parent.parent / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("rehearse_plugin_api", path)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    monkeypatch.setattr(api, "uploads_dir", lambda: tmp_path / "uploads")
    app = fastapi.FastAPI()
    app.include_router(api.router)
    return TestClient(app), api, tmp_path / "uploads"


def test_upload_stores_a_recording_under_an_opaque_id(client):
    http, _, folder = client
    r = http.post("/upload", files={"file": ("take.webm", b"\x1aE\xdf\xa3 fake", "video/webm")})
    assert r.status_code == 200
    upload_id = r.json()["upload_id"]
    assert bridge._UPLOAD_ID.match(upload_id) and (folder / upload_id).read_bytes().startswith(b"\x1aE")


def test_upload_refuses_non_media(client):
    http, _, folder = client
    r = http.post("/upload", files={"file": ("x.py", b"print(1)", "text/x-python")})
    assert r.status_code == 415
    assert not folder.exists() or list(folder.glob("*")) == []


def test_upload_refuses_oversize_and_leaves_nothing(client, monkeypatch):
    http, api, folder = client
    monkeypatch.setattr(api, "MAX_BYTES", 10)
    r = http.post("/upload", files={"file": ("take.webm", b"x" * 64, "video/webm")})
    assert r.status_code == 413 and list(folder.glob("*")) == []
