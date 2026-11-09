from types import SimpleNamespace

import pytest

from rehearse import bridge, coach, face, pipeline
from rehearse.sessions import Store

from test_delivery import timed
from test_desktop_backend import FakeCtx, FakeRuntime
from rehearse.delivery import analyze


@pytest.mark.parametrize("text", [
    "I did the backend as well, and the frontend",
    "I think so, because the data was clear",
    "It was the tool they like, so we kept it",
    "The data was late, so we rebuilt the pipeline",
    "That's right, we shipped in May",
])
def test_words_that_belong_to_the_sentence_are_not_fillers(text):
    assert analyze(timed(text)).fillers == {}


def test_a_set_off_filler_mid_answer_still_counts():
    assert analyze(timed("We shipped it, well, mostly on time")).fillers == {"well": 1}


def test_two_takes_of_one_question_keep_both_recordings(tmp_path):
    store = Store(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    kept = []
    for body in (b"first take", b"second take"):
        src = uploads / f"{body.hex()[:32]:0<32}.webm"
        src.write_bytes(body)
        kept.append(pipeline._keep(store, "s1", str(src), 0))
    assert kept[0] != kept[1]
    assert {(tmp_path / k).read_bytes() for k in kept} == {b"first take", b"second take"}


class SloppyLLM:
    """A free model that ignores the schema's number types and leaves a question blank."""

    def complete_structured(self, **kw):
        if kw["purpose"] == "rehearse.plan":
            return SimpleNamespace(parsed={"title": "t", "opening": "o", "questions": [
                {"question": "Tell me about yourself.", "target_seconds": "90 seconds"},
                {"question": "  ", "target_seconds": 60},
                "not a question",
            ]})
        return SimpleNamespace(parsed={"content_score": "85/100", "summary": "s", "strengths": [],
                                       "improvements": [], "stronger_answer": "", "follow_up": ""})


def test_loose_model_output_still_makes_a_usable_plan_and_score(tmp_path):
    store, llm = Store(tmp_path), SloppyLLM()
    s = pipeline.start(llm, store, scenario="job", subject="analyst")
    assert [q["question"] for q in s["questions"]] == ["Tell me about yourself."]
    assert s["questions"][0]["target_seconds"] == 90 and s["questions"][0]["tip"] == ""
    a = pipeline.analyze(llm, store, session_id=s["id"], question_index=0, answer_text="I analyse data")
    assert a["feedback"]["content_score"] == 85


def test_live_check_never_downloads_the_face_model(tmp_path, monkeypatch):
    monkeypatch.setattr(face.urllib.request, "urlopen", lambda *a, **k: pytest.fail("downloaded on a live frame"))
    table = bridge.methods(FakeCtx(), FakeRuntime(tmp_path))
    out = bridge.call(table, "presence.check", {"image": "aGVsbG8="})
    assert "getting ready" in out["error"]


def test_plan_with_no_real_questions_is_a_readable_error(tmp_path):
    class Empty:
        def complete_structured(self, **kw):
            return SimpleNamespace(parsed={"questions": [{"question": ""}]})

    with pytest.raises(coach.CoachError, match="question plan"):
        pipeline.start(Empty(), Store(tmp_path), scenario="job", subject="x")


def test_live_presence_with_no_face_is_not_measured_rather_than_zero(tmp_path):
    blind = [{"face": False, "facing": False, "framing": "none", "cue": "We can't see your face", "center": None}] * 6
    summary = face.summarize_presence(blind)
    assert summary["facing_ratio"] is None and summary["cues"] == {"We can't see your face": 6}
    store = Store(tmp_path)
    s = store.create(scenario="job", mode="interview", subject="x", details="",
                     plan={"title": "t", "opening": "", "questions": [{"question": "q"}]})
    store.add_attempt(s["id"], {"question_index": 0, "overall": 50, "delivery": {}, "presence": summary})
    from rehearse.sessions import interview_numbers
    assert interview_numbers(store.load(s["id"]))["live_facing"] is None
