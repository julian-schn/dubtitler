"""The consensus vote is the one genuinely new behaviour in the rewrite.

Two engines can only say "these differ". Three can say "two of us read it this
way", which is the difference between a review queue a human works through and
one they abandon. These tests pin that, and pin the significance filter that
keeps the queue short enough to be worked through at all.
"""

import pytest

from subtitler import consensus
from subtitler.core import Segment, Transcript, Word
from subtitler.langpack import load as load_pack


def make(engine: str, text: str) -> Transcript:
    """A transcript with one word per second, so indices and times line up."""
    words = [
        Word(w=w, start=float(i), end=float(i) + 0.9, prob=0.99)
        for i, w in enumerate(text.split())
    ]
    seg = Segment(id=0, start=0.0, end=float(len(words)), text=text, words=words)
    return Transcript(video="t", engine=engine, language="de", segments=[seg])


@pytest.fixture
def de():
    return load_pack("de")


def regions(spine, others, pack):
    return consensus.compare(spine, others, pack)


# ---------------------------------------------------------------- agreement

def test_identical_transcripts_produce_nothing(de):
    text = "sie hat das Haus verkauft"
    assert regions(make("a", text), {"b": make("b", text)}, de) == []


def test_formatting_differences_are_not_disagreements(de):
    """Folded by the pack: ss against ß, and a spelled-out number against a
    digit. Engines argue about these constantly and it never means anything."""
    spine = make("a", "im Jahr neunzehnhundertachtunddreißig war das grosse Haus")
    other = make("b", "im Jahr 1938 war das große Haus")
    assert regions(spine, {"b": other}, de) == []


def test_function_word_disagreement_is_not_significant(de):
    spine = make("a", "sie ist in dem Haus geblieben")
    other = make("b", "sie war in dem Haus geblieben")
    found = regions(spine, {"b": other}, de)
    assert found
    assert not any(r.significant for r in found)


# ---------------------------------------------------------------- the vote

def test_majority_of_three_outvotes_the_spine(de):
    """The payoff for a third engine: the spine is the odd one out and the
    review doc can say what to change it to."""
    spine = make("a", "da war ich ganz einfaltig gewesen")
    found = regions(
        spine,
        {"b": make("b", "da war ich ganz einfältig gewesen"),
         "c": make("c", "da war ich ganz einfältig gewesen")},
        de,
    )
    sig = [r for r in found if r.significant]
    assert len(sig) == 1
    assert sig[0].outvoted
    assert sig[0].majority is not None


def test_spine_in_the_majority_is_not_outvoted(de):
    spine = make("a", "sie wohnte in Gmünd damals")
    found = regions(
        spine,
        {"b": make("b", "sie wohnte in Gmünd damals"),
         "c": make("c", "sie wohnte in München damals")},
        de,
    )
    sig = [r for r in found if r.significant]
    assert len(sig) == 1
    assert not sig[0].outvoted
    assert sig[0].majority is not None


def test_two_engines_never_produce_a_majority(de):
    """A tie is exactly the case a human has to settle, so it must not be
    silently resolved in the spine's favour."""
    spine = make("a", "sie wohnte in Gmünd damals")
    found = regions(spine, {"b": make("b", "sie wohnte in München damals")}, de)
    sig = [r for r in found if r.significant]
    assert len(sig) == 1
    assert sig[0].majority is None
    assert not sig[0].outvoted


def test_even_split_of_four_produces_no_majority(de):
    spine = make("a", "sie wohnte in Gmünd damals")
    found = regions(
        spine,
        {"b": make("b", "sie wohnte in Gmünd damals"),
         "c": make("c", "sie wohnte in München damals"),
         "d": make("d", "sie wohnte in München damals")},
        de,
    )
    sig = [r for r in found if r.significant]
    assert len(sig) == 1
    assert sig[0].majority is None


# ---------------------------------------------------------------- recall gaps

def test_dropped_phrase_is_significant_even_without_content_words(de):
    """One engine hearing speech another missed is a recall gap, a different
    and worse failure than arguing about a word."""
    spine = make("a", "und dann ist sie doch noch mal in das Haus gegangen")
    other = make("b", "und dann in das Haus gegangen")
    sig = [r for r in regions(spine, {"b": other}, de) if r.significant]
    assert sig
    assert any(r.dropped for r in sig)


def test_extra_words_from_another_engine_are_not_lost(de):
    """An insertion has no spine index of its own; if it is not anchored to the
    preceding word it disappears from the comparison entirely."""
    spine = make("a", "sie kam aus Stuttgart")
    other = make("b", "sie kam damals aus Stuttgart Bad Cannstatt")
    found = regions(spine, {"b": other}, de)
    assert found
    assert any("Cannstatt" in r.readings["b"] for r in found)


# ---------------------------------------------------------------- degradation

def test_single_engine_has_nothing_to_compare(de):
    assert consensus.compare(make("a", "sie hat das Haus verkauft"), {}, de) == []


def test_note_groups_engines_by_what_they_heard(de):
    spine = make("a", "sie wohnte in München damals")
    found = regions(
        spine,
        {"b": make("b", "sie wohnte in Gmünd damals"),
         "c": make("c", "sie wohnte in Gmünd damals")},
        de,
    )
    note = next(r for r in found if r.significant).note(de)
    assert "b, c" in note
    assert "Gmünd" in note and "München" in note


def test_a_long_reading_is_shortened_in_the_note(de):
    """An engine that starts late produces one enormous contested region. A
    note printing all of it gets skipped, and the useful short notes around it
    get skipped with it."""
    spine = make("a", " ".join(f"ord{i}" for i in range(40)))
    found = regions(spine, {"b": make("b", "ord0 ord39")}, de)
    note = next(r for r in found if r.significant).note(de)
    assert "more]" in note
    assert len(note) < 300
    # Both ends of the contested span survive: ord0 and ord39 matched, so the
    # region runs from ord1 to ord38.
    assert "ord1 " in note and "ord38" in note


def test_a_short_reading_is_left_alone(de):
    spine = make("a", "sie wohnte i München damals")
    found = regions(spine, {"b": make("b", "sie wohnte i Gmünd damals")}, de)
    note = next(r for r in found if r.significant).note(de)
    assert "more]" not in note
