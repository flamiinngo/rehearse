from rehearse.delivery import FaceSample, Word, analyze, words_from_text


def timed(text, start=0.0, step=0.4):
    """Words at a steady 150 wpm unless a token is ``<pause:N>``."""
    out, t = [], start
    for tok in text.split():
        if tok.startswith("<pause:"):
            t += float(tok[7:-1])
            continue
        out.append(Word(tok, t, t + step * 0.8))
        t += step
    return out


def test_comma_set_off_like_is_a_filler_but_a_verb_is_not():
    r = analyze(timed("I like Python because, like, it reads well"))
    assert r.fillers == {"like": 1}


def test_hesitation_sounds_and_phrases_are_counted_with_timestamps():
    r = analyze(timed("So, um, I think, you know, it was uh fine"))
    assert r.fillers == {"um": 1, "you know": 1, "uh": 1}
    assert [m["label"] for m in r.as_dict()["moments"] if m["kind"] == "filler"] == ['"um"', '"you know"', '"uh"']


def test_long_pause_is_reported_where_it_happened():
    r = analyze(timed("I led the project <pause:3.5> and shipped it"))
    assert r.long_pauses == 1
    pause = next(m for m in r.moments if m.kind == "pause")
    assert 3.0 < pause.length < 4.0 and abs(pause.t - (3 * 0.4 + 0.32)) < 0.01


def test_pace_excludes_fillers():
    clean = analyze(timed("we cut costs by half " * 10))
    padded = analyze(timed("we um cut uh costs " * 10))
    assert clean.wpm > 140
    assert padded.word_count == 30 and padded.filler_count == 20


def test_more_fillers_score_lower():
    clean = analyze(timed("I built the dashboard and it saved the team hours every week " * 4))
    messy = analyze(timed("I um built the uh dashboard and um it like, saved uh hours " * 4))
    assert messy.scores["fillers"] < clean.scores["fillers"]
    assert messy.score < clean.score


def test_answer_far_past_target_length_is_penalised():
    double = timed("word " * 600)  # 240s against a 120s ceiling
    assert analyze(double, target_seconds=(30, 120)).scores["length"] == 0
    assert 0 < analyze(timed("word " * 400), target_seconds=(30, 120)).scores["length"] < 100
    assert analyze(timed("word " * 150), target_seconds=(30, 120)).scores["length"] == 100


def test_look_away_spans_become_moments_and_lower_eye_contact():
    face = [FaceSample(t / 2, "away" if 4 <= t < 12 else "facing") for t in range(20)]
    r = analyze(timed("this is my answer " * 5), face=face)
    assert r.facing_ratio == 0.6
    away = [m for m in r.moments if m.kind == "away"]
    assert len(away) == 1 and away[0].t == 2.0 and away[0].length == 4.0


def test_repeated_word_stumble_is_flagged():
    assert analyze(timed("I I think the the plan worked")).repeats == 2


def test_empty_take_scores_zero_without_crashing():
    r = analyze([])
    assert r.score == 0 and r.word_count == 0


def test_text_transcript_gets_synthetic_timings():
    words = words_from_text("um so I, like, led it")
    assert len(words) == 6 and words[1].start > words[0].start
