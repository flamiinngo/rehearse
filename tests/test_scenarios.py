import re
from pathlib import Path

import pytest

from rehearse import coach, pipeline
from rehearse.delivery import analyze
from rehearse.sessions import Store

from test_delivery import timed
from test_pipeline import CapturingLLM, FakeLLM


@pytest.mark.parametrize("key", list(coach.SCENARIOS))
def test_every_scenario_briefs_its_own_questioner_and_keeps_the_integrity_rule(tmp_path, key):
    llm = CapturingLLM()
    s = pipeline.start(llm, Store(tmp_path), scenario=key, subject="something")
    prompt = llm.last_instructions
    sc = coach.SCENARIOS[key]
    assert sc.style in prompt and sc.asker in prompt
    assert "never help them misrepresent" in prompt
    assert s["scenario"] == key and s["mode"] == sc.mode


def test_talks_open_with_the_right_take_and_interviews_never_do(tmp_path):
    llm = CapturingLLM()
    pipeline.start(llm, Store(tmp_path), scenario="demo", subject="my plugin")
    assert '"Give your demo."' in llm.last_instructions
    pipeline.start(llm, Store(tmp_path), scenario="visa", subject="student visa")
    assert "Give your" not in llm.last_instructions and "Deliver your" not in llm.last_instructions


def test_feedback_judges_by_the_scenario(tmp_path):
    store, llm = Store(tmp_path), CapturingLLM()
    s = pipeline.start(llm, store, scenario="pitch", subject="seed round")
    pipeline.analyze(llm, store, session_id=s["id"], question_index=0, answer_text="We grew 30% a month")
    assert coach.SCENARIOS["pitch"].judge in llm.last_instructions


@pytest.mark.parametrize("word, key", [("interview", "job"), ("talk", "presentation"), ("Thesis", "academic"),
                                       ("scholarship", "admission"), ("investor", "pitch"), ("demo", "demo")])
def test_everyday_words_reach_the_right_scenario(word, key):
    assert coach.resolve(word) == key


def test_unknown_kind_lists_the_choices():
    with pytest.raises(ValueError, match="Product demo"):
        coach.resolve("karaoke")


def test_sessions_saved_before_scenarios_still_get_feedback(tmp_path):
    store, llm = Store(tmp_path), FakeLLM()
    s = pipeline.start(llm, store, scenario="job", subject="nurse")
    legacy = store.load(s["id"])
    del legacy["scenario"]
    store.save(legacy)
    assert pipeline.analyze(llm, store, session_id=s["id"], question_index=0, answer_text="I care")["overall"] == 60


def test_desktop_chips_match_the_python_catalogue():
    js = (Path(__file__).resolve().parent.parent / "desktop" / "plugin.js").read_text(encoding="utf-8")
    block = js[js.index("const SCENES"):js.index("const SCENE =")]
    assert re.findall(r"key: '([a-z]+)'", block) == list(coach.SCENARIOS)


def test_french_and_german_hesitations_count_as_fillers():
    assert analyze(timed("Alors, euh, je pense que äh c'est bon")).filler_count == 2
