from types import SimpleNamespace

import pytest

from rehearse import face, pipeline
from rehearse.sessions import SessionNotFound, Store


class FakeLLM:
    """Stands in for ctx.llm: returns a plan, then feedback with a scripted content score."""

    def __init__(self, scores=(60, 80)):
        self.scores = list(scores)
        self.calls = []

    def complete_structured(self, *, instructions, input, json_schema, **kw):
        self.calls.append(kw.get("purpose"))
        if kw.get("purpose") == "rehearse.plan":
            return SimpleNamespace(parsed={
                "title": "Data analyst mock interview", "opening": "Thanks for coming in.",
                "questions": [
                    {"question": "Tell me about yourself.", "why": "w", "target_seconds": 90, "tip": "t"},
                    {"question": "A time you used data to change a decision?", "why": "w", "target_seconds": 5000,
                     "tip": "t"},
                ],
            })
        return SimpleNamespace(parsed={
            "content_score": self.scores.pop(0), "summary": "s", "strengths": ["a"], "improvements": ["b"],
            "stronger_answer": "c", "follow_up": "d",
        })


def test_text_answer_end_to_end_tracks_improvement_on_retry(tmp_path):
    store, llm = Store(tmp_path), FakeLLM()
    s = pipeline.start(llm, store, mode="interview", subject="junior data analyst")
    assert s["questions"][1]["target_seconds"] == 1800

    first = pipeline.analyze(llm, store, session_id=s["id"], question_index=0, answer_text="um I, like, do data")
    assert first["delivery"]["measured"] == "text" and first["delivery"]["wpm"] is None
    assert first["delivery"]["fillers"] == {"um": 1, "like": 1}
    assert first["overall"] == 60 and first["improvement"] is None

    retry = pipeline.analyze(llm, store, session_id=s["id"], question_index=0, answer_text="I analyse sales data")
    assert retry["improvement"] == 20

    rows = store.list()
    assert rows[0]["answered"] == 1 and rows[0]["attempts"] == 2 and rows[0]["score"] == 80
    assert pipeline.progress(store)["latest_score"] == 80


def test_bad_inputs_are_explained(tmp_path):
    store, llm = Store(tmp_path), FakeLLM()
    with pytest.raises(ValueError):
        pipeline.start(llm, store, mode="karaoke", subject="x")
    s = pipeline.start(llm, store, mode="presentation", subject="Q3 results")
    with pytest.raises(ValueError):
        pipeline.analyze(llm, store, session_id=s["id"], question_index=7, answer_text="x")
    with pytest.raises(ValueError):
        pipeline.analyze(llm, store, session_id=s["id"], question_index=0)
    with pytest.raises(SessionNotFound):
        store.load("../../etc/passwd")


def test_delete_removes_session_and_recordings(tmp_path):
    store = Store(tmp_path)
    s = pipeline.start(FakeLLM(), store, mode="interview", subject="nurse")
    rec = store.recordings_dir(s["id"])
    rec.mkdir(parents=True)
    (rec / "take-01.webm").write_bytes(b"x")
    store.delete(s["id"])
    assert store.list() == [] and not rec.exists()


def _row(eyes, nose, mouth_y):
    (rex, rey), (lex, ley) = eyes
    return [0, 0, 100, 100, rex, rey, lex, ley, nose[0], nose[1], rex + 5, mouth_y, lex - 5, mouth_y, 0.9]


def test_frontal_face_counts_as_eye_contact_and_turned_head_does_not():
    assert face.classify(_row(((40, 40), (80, 40)), (60, 58), 75)) == "facing"
    assert face.classify(_row(((40, 40), (80, 40)), (78, 58), 75)) == "away"   # head turned
    assert face.classify(_row(((40, 40), (80, 40)), (60, 72), 75)) == "away"   # looking down


def test_corrupt_face_model_download_is_refused(tmp_path, monkeypatch):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"not the model"

    monkeypatch.setattr(face.urllib.request, "urlopen", lambda *a, **k: Resp())
    with pytest.raises(face.FaceUnavailable, match="checksum"):
        face.ensure_model(tmp_path)
    assert not (tmp_path / "models" / face.MODEL_NAME).exists()
