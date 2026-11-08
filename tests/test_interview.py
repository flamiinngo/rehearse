import json
import threading
from types import SimpleNamespace

import pytest

from rehearse import bridge, coach, face, jobs
from rehearse.sessions import Store, interview_numbers

from test_desktop_backend import FakeCtx, FakeRuntime, wait


def test_concurrent_answers_are_never_lost(tmp_path):
    store = Store(tmp_path)
    s = store.create(scenario="job", mode="interview", subject="x", details="",
                     plan={"title": "t", "opening": "", "questions": [{"question": "q"}] * 5})
    threads = [threading.Thread(target=store.add_attempt, args=(s["id"], {"question_index": i % 5, "overall": i}))
               for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.load(s["id"])["attempts"]) == 20


def test_presence_summary_reads_time_facing_cues_and_movement():
    still = [{"face": True, "facing": True, "framing": "ok", "cue": None, "center": [0.5, 0.5]}] * 8
    restless = [{"face": True, "facing": i % 2 == 0, "framing": "ok", "cue": None if i % 2 == 0 else "Look at the camera",
                 "center": [0.3 + 0.3 * (i % 2), 0.5]} for i in range(8)]
    calm, busy = face.summarize_presence(still), face.summarize_presence(restless)
    assert calm["facing_ratio"] == 1.0 and calm["steadiness"] == "steady" and calm["cues"] == {}
    assert busy["facing_ratio"] == 0.5 and busy["steadiness"] == "fidgety" and busy["cues"] == {"Look at the camera": 4}
    assert face.summarize_presence([]) is None


def test_numbers_use_the_latest_take_of_each_question(tmp_path):
    store = Store(tmp_path)
    s = store.create(scenario="job", mode="interview", subject="x", details="",
                     plan={"title": "t", "opening": "", "questions": [{"question": "a"}, {"question": "b"}, {"question": "c"}]})
    d = lambda f, w, e: {"filler_count": f, "fillers_per_minute": f, "wpm": w, "facing_ratio": e}  # noqa: E731
    store.add_attempt(s["id"], {"question_index": 0, "overall": 40, "delivery": d(8, 150, 0.5)})
    store.add_attempt(s["id"], {"question_index": 0, "overall": 80, "delivery": d(1, 140, 0.9),
                                "presence": {"facing_ratio": 0.8, "cues": {"Look at the camera": 3}}})
    store.add_attempt(s["id"], {"question_index": 1, "overall": 60, "delivery": d(3, 130, 0.7)})
    n = interview_numbers(store.load(s["id"]))
    assert n["score"] == 70 and n["answered"] == 2 and n["questions"][2]["answered"] is False
    assert n["fillers_per_minute"] == 2.0 and n["eye_contact"] == 0.8 and n["top_cues"] == [("Look at the camera", 3)]


class DebriefLLM:
    def complete_structured(self, **kw):
        if kw.get("purpose") == "rehearse.report":
            assert "Q0:" in kw["input"][0]["text"]
            return SimpleNamespace(parsed={"verdict": "promising", "headline": "h", "strengths": ["s"], "fixes": ["f"],
                                           "best_question": 9, "next_practice": "n"})
        raise AssertionError(kw.get("purpose"))


def test_final_account_combines_debrief_and_numbers(tmp_path):
    ctx = SimpleNamespace(llm=DebriefLLM())
    table = bridge.methods(ctx, FakeRuntime(tmp_path))
    store = Store(tmp_path)
    s = store.create(scenario="visa", mode="interview", subject="student visa", details="",
                     plan={"title": "t", "opening": "", "questions": [{"question": "Why Canada?"}]})
    store.add_attempt(s["id"], {"question_index": 0, "overall": 70, "transcript": "To study.",
                                "delivery": {"filler_count": 0, "wpm": 140}})
    report = wait(table["interview.report"]({"session_id": s["id"]})["job"])["result"]
    assert report["debrief"]["verdict"] == "promising" and report["debrief"]["best_question"] == 0
    assert report["numbers"]["score"] == 70 and store.load(s["id"])["report"] == report


def test_live_check_rejects_garbage(tmp_path):
    table = bridge.methods(FakeCtx(), FakeRuntime(tmp_path))
    with pytest.raises(ValueError):
        table["presence.check"]({"image": "not base64!!"})


def test_live_check_on_a_real_face(tmp_path):
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from pathlib import Path

    sample = Path(__file__).resolve().parent / "data" / "face.jpg"
    if not sample.exists():
        pytest.skip("no sample face image")
    model = face.ensure_model(tmp_path)
    jpeg = sample.read_bytes()
    out = face.check_frame(jpeg, model)
    assert out["face"] is True and out["framing"] in face.CUES or out["framing"] == "ok"
    blank = cv2.imencode(".jpg", np.zeros((240, 320, 3), np.uint8))[1].tobytes()
    assert face.check_frame(blank, model)["cue"] == "We can't see your face"
