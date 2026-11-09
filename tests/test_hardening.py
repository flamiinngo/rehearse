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


class Silent(SloppyLLM):
    def complete_structured(self, **kw):
        if kw["purpose"] != "rehearse.plan":
            pytest.fail("the model was asked to judge an empty answer")
        return SimpleNamespace(parsed={"title": "t", "opening": "o", "questions": [
            {"question": f"Q{i}", "target_seconds": 60} for i in range(12)]})


def test_plan_keeps_only_the_number_of_questions_asked_for(tmp_path):
    s = pipeline.start(Silent(), Store(tmp_path), scenario="job", subject="x", count=3)
    assert len(s["questions"]) == 3


def test_a_silent_take_says_check_the_microphone_instead_of_inventing_feedback(tmp_path, monkeypatch):
    store = Store(tmp_path)
    s = pipeline.start(Silent(), store, scenario="job", subject="x", count=2)
    monkeypatch.setattr(pipeline, "_measure_media", lambda *a, **k: (
        {"measured": "audio", "score": 0, "word_count": 0}, "", []))
    a = pipeline.analyze(Silent(), store, session_id=s["id"], question_index=0, media_path=str(tmp_path / "x.webm"),
                         keep_recording=False)
    assert a["overall"] == 0 and a["feedback"] is None
    assert any("microphone" in n for n in a["notes"])


def test_doubled_words_that_are_grammatical_are_not_stumbles():
    assert analyze(timed("we had had a rough quarter and the the plan changed")).repeats == 1


@pytest.mark.parametrize("rotation", [90, -90, 180, 0])
def test_phone_video_stored_sideways_is_turned_upright(rotation):
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    upright = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    # What a phone stores: the picture turned against its rotation flag.
    stored = {90: cv2.rotate(upright, cv2.ROTATE_90_COUNTERCLOCKWISE), -90: cv2.rotate(upright, cv2.ROTATE_90_CLOCKWISE),
              180: cv2.rotate(upright, cv2.ROTATE_180), 0: upright}[rotation]
    assert (face._upright(stored, rotation, cv2) == upright).all()


class _Seg(SimpleNamespace):
    pass


class FakeWhisper:
    def __init__(self, segments):
        self.segments = segments

    def detect_language(self, audio):
        return ("en", 1.0)

    def transcribe(self, audio, **kw):
        return iter(self.segments), SimpleNamespace(language="en", duration=len(audio) / 16000)


def _words(*texts):
    return [SimpleNamespace(word=t, start=i * 0.4, end=i * 0.4 + 0.3) for i, t in enumerate(texts)]


def test_silence_and_invented_words_are_not_an_answer():
    np = pytest.importorskip("numpy")
    from rehearse import speech

    muted = np.zeros(32000, dtype=np.float32)
    assert speech._transcribe(FakeWhisper([]), muted, "")[0] == []
    hiss = (np.random.default_rng(0).standard_normal(32000) * 0.01).astype(np.float32)
    ghost = _Seg(no_speech_prob=0.51, avg_logprob=-1.09, words=_words(" Okay.", " Okay."))
    real = _Seg(no_speech_prob=0.05, avg_logprob=-0.3, words=_words(" I", " led", " the", " project"))
    words, _, _ = speech._transcribe(FakeWhisper([ghost, real]), hiss, "")
    assert [w.text.strip() for w in words] == ["I", "led", "the", "project"]


def test_unreadable_recording_is_one_plain_sentence(tmp_path, monkeypatch):
    pytest.importorskip("faster_whisper")
    from rehearse import speech

    monkeypatch.setitem(speech._models, "fake", FakeWhisper([]))
    bad = tmp_path / "take.webm"
    bad.write_bytes(b"\x00not a video" * 500)
    with pytest.raises(ValueError, match="could not be read") as err:
        speech.transcribe(str(bad), model="fake")
    assert str(tmp_path) not in str(err.value)


@pytest.mark.parametrize("name, key", [("Job interview", "job"), ("Investor Pitch", "pitch"), ("Speech or toast", "speech"),
                                       ("  visa  ", "visa"), ("Class talk or thesis defence", "academic")])
def test_the_names_the_error_lists_are_accepted(name, key):
    assert coach.resolve(name) == key


@pytest.mark.parametrize("index", [None, "first", "2nd?"])
def test_a_bad_question_number_from_the_model_is_a_readable_error(tmp_path, index):
    import json
    from rehearse import tools

    ctx = FakeCtx()
    handlers = tools.make_handlers(ctx, FakeRuntime(tmp_path))
    sid = json.loads(handlers["rehearse_start"]({"scenario": "job", "subject": "analyst"}))["session_id"]
    out = json.loads(handlers["rehearse_analyze"]({"session_id": sid, "question_index": index, "answer_text": "I led it"}))
    assert "question_index must be between" in out["error"] or out.get("overall") is not None
