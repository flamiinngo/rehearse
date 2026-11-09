"""Delivery metrics from word timings: fillers, pace, pauses, length, eye contact.

Pure functions over plain data so the same numbers come out of the Desktop page,
the agent tools and the tests. Every finding carries a timestamp ("moment") so a
replay can jump straight to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# English plus the unambiguous hesitation sounds of French, German and Dutch.
FILLERS = frozenset({"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm", "eh", "ehm",
                     "euh", "äh", "ähm", "öh", "uhmm"})
# Only filler when a comma follows ("it was, like, huge"), never in "I like Python" or ", so we shipped".
COMMA_FILLERS = frozenset({"like", "so", "well", "right", "okay"})
COMMA_PHRASES = (("you", "know"), ("i", "mean"))
CRUTCH_WORDS = frozenset({"basically", "actually", "literally", "honestly", "totally", "obviously"})

PAUSE_SECONDS = 2.0
PACE_WINDOW_SECONDS = 20.0
PACE_IDEAL = (120.0, 160.0)
FAST_WPM = 175.0
SLOW_WPM = 105.0
LOOK_AWAY_SPAN_SECONDS = 2.5

_STRIP = re.compile(r"^[^\w']+|[^\w']+$")


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float

    @property
    def norm(self) -> str:
        return _STRIP.sub("", self.text.strip().lower())

    @property
    def comma_after(self) -> bool:
        return self.text.rstrip().endswith(",")


@dataclass
class Moment:
    t: float
    kind: str  # filler | pause | fast | slow | away | repeat
    label: str
    length: float = 0.0

    def as_dict(self) -> dict:
        return {"t": round(self.t, 2), "kind": self.kind, "label": self.label, "length": round(self.length, 2)}


@dataclass
class FaceSample:
    t: float
    state: str  # facing | away | none


@dataclass
class DeliveryReport:
    duration: float
    word_count: int
    wpm: float
    fillers: dict[str, int]
    filler_count: int
    fillers_per_minute: float
    crutch_words: dict[str, int]
    repeats: int
    long_pauses: int
    longest_pause: float
    start_delay: float
    pace_timeline: list[dict]
    facing_ratio: float | None
    moments: list[Moment] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)
    score: int = 0

    def as_dict(self) -> dict:
        data = {k: v for k, v in self.__dict__.items() if k != "moments"}
        data["moments"] = [m.as_dict() for m in sorted(self.moments, key=lambda m: m.t)]
        return data


def _filler_spans(words: list[Word]) -> list[tuple[int, str]]:
    """Index of each filler's first word and its label, comma-sensitive for ambiguous ones."""
    found: list[tuple[int, str]] = []
    i = 0
    while i < len(words):
        w = words[i]
        prev_comma = i > 0 and words[i - 1].comma_after
        pair = (w.norm, words[i + 1].norm) if i + 1 < len(words) else None
        if pair in COMMA_PHRASES and (words[i + 1].comma_after or prev_comma):
            found.append((i, " ".join(pair)))
            i += 2
            continue
        if w.norm in FILLERS:
            found.append((i, w.norm))
        elif w.norm in COMMA_FILLERS and w.comma_after and _filler_context(words, i):
            found.append((i, w.norm))
        i += 1
    return found


# Words that make the next one part of the sentence even before a comma: "as well,", "I think so,",
# "things I like,", "that's right,", "it's okay,".
_BOUND_AFTER = {
    "well": {"as", "very", "pretty", "quite", "so", "really", "went", "go", "goes", "do", "did", "done", "doing"},
    "so": {"think", "hope", "guess", "believe", "said", "suppose", "not", "do", "did", "more", "even", "and"},
    "like": {"i", "we", "they", "you", "would", "really", "don't", "didn't", "do", "to", "it"},
    "right": {"that's", "is", "was", "turn", "the", "exactly", "all"},
    "okay": {"is", "was", "it's", "that's", "be", "are", "seems", "seemed"},
}


def _filler_context(words: list[Word], i: int) -> bool:
    w = words[i].norm
    if i > 0 and not words[i - 1].comma_after and words[i - 1].norm in _BOUND_AFTER.get(w, ()):
        return False
    # "so," and "well," opening the answer are normal; mid-answer they are verbal tics.
    return w == "like" or i > 0


def _pace_timeline(words: list[Word], spoken: list[Word]) -> list[dict]:
    if not spoken:
        return []
    start, end = words[0].start, words[-1].end
    out = []
    t = start
    while t < end:
        stop = min(t + PACE_WINDOW_SECONDS, end)
        n = sum(1 for w in spoken if t <= w.start < stop)
        span = stop - t
        if span >= 5.0:
            out.append({"t": round(t, 2), "length": round(span, 2), "wpm": round(n / span * 60.0, 1)})
        t = stop
    return out


def _span_moments(samples: list[FaceSample]) -> list[Moment]:
    """Collapse consecutive non-facing samples into look-away spans long enough to notice."""
    moments: list[Moment] = []
    run_start = None
    for cur in samples:
        away = cur.state != "facing"
        if away and run_start is None:
            run_start = cur.t
        elif not away and run_start is not None:
            _close_span(moments, run_start, cur.t)
            run_start = None
    if run_start is not None and samples:
        _close_span(moments, run_start, samples[-1].t)
    return moments


def _close_span(moments: list[Moment], start: float, end: float) -> None:
    if end - start >= LOOK_AWAY_SPAN_SECONDS:
        moments.append(Moment(start, "away", f"Looked away for {end - start:.0f}s", end - start))


def _clamp(value: float) -> int:
    return int(round(max(0.0, min(100.0, value))))


def _length_score(duration: float, target: tuple[float, float] | None) -> int:
    if not target:
        return 100
    low, high = target
    if low <= duration <= high:
        return 100
    miss = low - duration if duration < low else duration - high
    return _clamp(100 - miss / max(high, 1.0) * 150)


def _pace_score(wpm: float) -> int:
    low, high = PACE_IDEAL
    if low <= wpm <= high:
        return 100
    return _clamp(100 - (low - wpm if wpm < low else wpm - high) * 2)


_WEIGHTS = {"fillers": 0.3, "pace": 0.25, "eye_contact": 0.25, "pauses": 0.1, "length": 0.1}


def analyze(words: list[Word], *, duration: float | None = None, face: list[FaceSample] | None = None,
            target_seconds: tuple[float, float] | None = None) -> DeliveryReport:
    """Score one take. ``target_seconds`` is the ideal answer length range; ``face`` is optional."""
    words = [w for w in words if w.norm]
    filler_spans = _filler_spans(words)
    filler_idx = {i for i, label in filler_spans for i in range(i, i + len(label.split()))}
    spoken = [w for i, w in enumerate(words) if i not in filler_idx]

    span = (words[-1].end - words[0].start) if words else 0.0
    minutes = max(span, 1.0) / 60.0
    wpm = len(spoken) / minutes if spoken else 0.0

    fillers: dict[str, int] = {}
    moments: list[Moment] = []
    for i, label in filler_spans:
        fillers[label] = fillers.get(label, 0) + 1
        moments.append(Moment(words[i].start, "filler", f'"{label}"'))
    crutch: dict[str, int] = {}
    for w in spoken:
        if w.norm in CRUTCH_WORDS:
            crutch[w.norm] = crutch.get(w.norm, 0) + 1

    repeats = 0
    for a, b in zip(spoken, spoken[1:]):
        if a.norm == b.norm and not a.norm.isdigit() and b.start - a.end < 1.0:
            repeats += 1
            moments.append(Moment(a.start, "repeat", f'Repeated "{a.norm}"'))

    gaps = [(a.end, b.start - a.end) for a, b in zip(words, words[1:]) if b.start - a.end >= PAUSE_SECONDS]
    for at, length in gaps:
        moments.append(Moment(at, "pause", f"Paused {length:.1f}s", length))

    timeline = _pace_timeline(words, spoken)
    for win in timeline:
        if win["wpm"] > FAST_WPM:
            moments.append(Moment(win["t"], "fast", f"Rushing: {win['wpm']:.0f} wpm", win["length"]))
        elif 0 < win["wpm"] < SLOW_WPM:
            moments.append(Moment(win["t"], "slow", f"Dragging: {win['wpm']:.0f} wpm", win["length"]))

    facing_ratio = None
    if face:
        facing_ratio = sum(1 for s in face if s.state == "facing") / len(face)
        moments.extend(_span_moments(face))

    filler_count = sum(fillers.values())
    per_minute = filler_count / minutes if words else 0.0
    scores = {
        "fillers": _clamp(100 - per_minute * 12),
        "pace": _pace_score(wpm) if spoken else 0,
        "pauses": _clamp(100 - max(0, len(gaps) - 1) * 15),
        "length": _length_score(span, target_seconds),
    }
    if facing_ratio is not None:
        scores["eye_contact"] = _clamp(facing_ratio * 110)
    weight = sum(_WEIGHTS[k] for k in scores)
    overall = sum(scores[k] * _WEIGHTS[k] for k in scores) / weight if words else 0

    return DeliveryReport(
        duration=round(duration if duration is not None else span, 2), word_count=len(spoken),
        wpm=round(wpm, 1), fillers=fillers, filler_count=filler_count,
        fillers_per_minute=round(per_minute, 2), crutch_words=crutch, repeats=repeats,
        long_pauses=len(gaps), longest_pause=round(max((g for _, g in gaps), default=0.0), 2),
        start_delay=round(words[0].start, 2) if words else 0.0, pace_timeline=timeline,
        facing_ratio=None if facing_ratio is None else round(facing_ratio, 3),
        moments=moments, scores=scores, score=_clamp(overall),
    )


def words_from_text(text: str, wpm: float = 140.0) -> list[Word]:
    """Approximate timings for a plain transcript (a messaging voice note arrives as text only)."""
    step = 60.0 / wpm
    return [Word(tok, i * step, i * step + step * 0.8) for i, tok in enumerate(text.split())]
