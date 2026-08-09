"""Confidence flagging across N engines.

Confidence is handled differently from readings on purpose, and the difference
is easy to undo by accident: a reading is a claim about what was said, so it
gets voted on, while a confidence number is an engine reporting that it
struggled, so it counts on its own.
"""

import pytest

from dubtitler.core import Segment, Transcript, Word
from dubtitler.steps.diff import confidence_at, pick_spine


def transcript(engine: str, *, logprob=None, probs=None, nospeech=None) -> Transcript:
    words = [
        Word(w=f"w{i}", start=float(i), end=float(i) + 0.9, prob=p)
        for i, p in enumerate(probs or [])
    ]
    seg = Segment(
        id=0, start=0.0, end=10.0, text="w0 w1 w2", words=words,
        avg_logprob=logprob, no_speech_prob=nospeech,
    )
    return Transcript(video="t", engine=engine, language="de", segments=[seg])


def test_single_engine_low_confidence_flags():
    conf = confidence_at({"a": transcript("a", probs=[0.99, 0.28, 0.99])}, 0.0, 10.0)
    assert conf["low_prob"] == ["a"]


def test_a_confident_engine_cannot_veto_a_worried_one():
    """The regression this exists for: putting confidence to a majority vote
    lets an engine that never reports low numbers silently cancel the only
    signal a single-engine job would have had."""
    conf = confidence_at(
        {
            "worried": transcript("worried", probs=[0.99, 0.28, 0.99]),
            "confident": transcript("confident", probs=[0.99, 0.99, 0.99]),
        },
        0.0, 10.0,
    )
    assert conf["low_prob"] == ["worried"]


def test_engines_without_confidence_numbers_are_ignored():
    """An engine reporting nothing must not read as an engine reporting zero."""
    conf = confidence_at(
        {
            "silent": transcript("silent", probs=[None, None]),
            "worried": transcript("worried", probs=[0.28]),
        },
        0.0, 10.0,
    )
    assert conf["low_prob"] == ["worried"]


def test_a_cluster_of_weak_words_flags_even_without_an_alarm():
    """One weak word in a long segment is normal; several is a signal."""
    conf = confidence_at({"a": transcript("a", probs=[0.45, 0.45, 0.99])}, 0.0, 10.0)
    assert conf["low_prob"] == ["a"]
    assert conf["weak"] == 2


def test_one_weak_word_alone_is_not_enough():
    conf = confidence_at({"a": transcript("a", probs=[0.45, 0.99, 0.99])}, 0.0, 10.0)
    assert conf["low_prob"] == []


def test_confidence_outside_the_span_is_not_counted():
    conf = confidence_at({"a": transcript("a", probs=[0.99, 0.20])}, 0.0, 1.0)
    assert conf["low_prob"] == []


def test_worst_logprob_across_engines_is_reported():
    conf = confidence_at(
        {"a": transcript("a", logprob=-0.30), "b": transcript("b", logprob=-0.90)},
        0.0, 10.0,
    )
    assert conf["low_logprob"] == ["b"]
    assert conf["logprob"] == pytest.approx(-0.90)


# ---------------------------------------------------------------- spine choice

def test_configured_spine_wins():
    available = {"whisper-local": None, "elevenlabs": None}
    assert pick_spine(available, "whisper-local") == "whisper-local"


def test_spine_falls_back_when_the_configured_engine_did_not_run():
    """Cue timings come from the spine's word timings, so a missing spine has
    to fall back to a real transcript rather than abort the run."""
    assert pick_spine({"whisper-local": None}, "elevenlabs") == "whisper-local"


def test_tighter_timings_win_by_default():
    available = {"whisper-local": None, "elevenlabs": None}
    assert pick_spine(available, "") == "elevenlabs"
