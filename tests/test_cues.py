"""Cue splitting, wrapping and timing.

Most of these pin a bug that reached the screen on the reference job. Cue
defects share an unpleasant property: they are invisible in the files and
obvious in the finished video, so the cost of catching one late is a re-render
and another review pass.
"""

import pytest

from subtitler import cues as cuelib
from subtitler.cues import Limits
from subtitler.langpack import load as load_pack


@pytest.fixture
def limits():
    return Limits()


@pytest.fixture
def nb():
    return load_pack("nb")


def sentence(text: str, words: list[tuple[str, float, float]]) -> dict:
    return {
        "id": 0, "speaker": None, "target": text, "source": text,
        "start": words[0][1], "end": words[-1][2],
        "words": [{"w": w, "start": s, "end": e} for w, s, e in words],
    }


def evenly(text: str, per_word: float = 0.5) -> dict:
    words = [(w, i * per_word, (i + 1) * per_word - 0.01)
             for i, w in enumerate(text.split())]
    return sentence(text, words)


# ---------------------------------------------------------------- wrapping

def test_short_text_stays_on_one_line(limits, nb):
    assert cuelib.wrap("Takk for alt.", limits, nb) == ["Takk for alt."]


def test_wrapping_never_exceeds_the_line_limit(limits, nb):
    text = "Og da tenkte jeg at dette kanskje var noe som kunne vare lenge nok"
    lines = cuelib.wrap(text, limits, nb)
    assert lines and all(len(ln) <= limits.max_line for ln in lines)


def test_a_line_does_not_end_on_a_word_that_binds_rightward(limits, nb):
    """Breaking between an article and its noun reads as særskriving, the
    error native Norwegian readers notice first."""
    text = "Hun fortalte om en veldig lang og ganske vanskelig reise hjem"
    lines = cuelib.wrap(text, limits, nb)
    assert lines
    assert lines[0].split()[-1].casefold() not in nb.never_trail


def test_unwrappable_text_reports_failure_rather_than_overflowing(limits, nb):
    """Returning None is how the caller learns to split instead of wrap."""
    text = " ".join(["Kongsberg"] * 12)
    assert cuelib.wrap(text, limits, nb) is None


def test_hard_wrap_never_cuts_inside_a_word(limits):
    """`1938` once came out as `193` / `8`, which survives review because it
    reads as a rendering artifact rather than an error."""
    lines = cuelib.hard_wrap("hun ble født i 1938 og vokste opp der", limits)
    assert "1938" in " ".join(lines).split() or any("1938" in ln for ln in lines)
    for line in lines:
        for token in line.split():
            assert token in "hun ble født i 1938 og vokste opp der".split()


# ---------------------------------------------------------------- splitting

def test_every_chunk_can_be_laid_out_within_the_limits(limits, nb):
    """A chunk can fit the character budget and still be unwrappable, because
    breaks fall only between words. Those have to be split again."""
    text = ("Og så fortalte hun at det hadde vært en veldig lang og vanskelig "
            "tid for hele familien, men at de likevel opplevde mye godt "
            "underveis, og det var noe hun aldri glemte siden.")
    for chunk in cuelib.split_points(text, limits, nb):
        assert cuelib.wrap(chunk, limits, nb) is not None


def test_splitting_does_not_strand_a_runt(limits, nb):
    text = ("Det var en gang en mann som bodde i en liten by langt mot nord, "
            "og han hadde aldri sett havet før den dagen.")
    chunks = cuelib.split_points(text, limits, nb)
    assert all(len(c) >= 12 for c in chunks[:-1])


# ---------------------------------------------------------------- timing

def test_cue_timings_come_from_the_source_words(limits, nb):
    s = evenly("En to tre fire fem seks sju åtte ni ti", per_word=1.0)
    built = cuelib.build([s], "target", limits, nb)
    assert built[0]["start"] == pytest.approx(0.0, abs=0.01)
    assert built[-1]["end"] <= s["words"][-1]["end"] + limits.lead_out + 0.01


def test_a_cue_is_never_shorter_than_it_is_readable(limits, nb):
    s = evenly("Dette er en ganske lang setning som må leses", per_word=0.05)
    for c in cuelib.build([s], "target", limits, nb):
        chars = len(c["text"].replace("\n", " "))
        assert c["end"] - c["start"] >= chars / limits.max_cps - 0.01
        assert c["end"] - c["start"] >= limits.min_duration - 0.001


def test_cues_never_overlap_and_keep_their_gap(limits, nb):
    sentences = []
    for i in range(6):
        s = evenly("Dette er en setning som varer en stund her", per_word=0.12)
        for w in s["words"]:
            w["start"] += i * 1.0
            w["end"] += i * 1.0
        s["id"] = i
        sentences.append(s)
    built = cuelib.build(sentences, "target", limits, nb)
    for prev, cur in zip(built, built[1:]):
        assert cur["start"] - prev["end"] >= limits.gap - 0.001


def test_lead_out_extends_into_silence_but_not_past_the_next_cue(limits, nb):
    a = evenly("Første setning her.", per_word=0.3)
    b = evenly("Andre setning kommer mye senere.", per_word=0.3)
    for w in b["words"]:
        w["start"] += 10.0
        w["end"] += 10.0
    b["id"] = 1

    built = cuelib.build([a, b], "target", limits, nb)
    spoken_end = a["words"][-1]["end"]
    assert built[0]["end"] > spoken_end                       # held into silence
    assert built[0]["end"] <= built[1]["start"] - limits.gap + 0.001


def test_lead_out_stops_at_the_end_of_the_picture(limits, nb):
    s = evenly("Siste setning i filmen.", per_word=0.3)
    duration = s["words"][-1]["end"] + 0.2
    built = cuelib.build([s], "target", limits, nb, duration=duration)
    assert built[-1]["end"] <= duration + 0.001


def test_no_cue_exceeds_the_maximum_duration(limits, nb):
    s = evenly("Kort.", per_word=0.2)
    built = cuelib.build([s], "target", limits, nb, duration=600.0)
    assert built[0]["end"] - built[0]["start"] <= limits.max_duration + 0.001


def test_untranslated_sentences_produce_no_cues(limits, nb):
    s = evenly("Noe tekst her.")
    s["target"] = ""
    assert cuelib.build([s], "target", limits, nb) == []


# ---------------------------------------------------------------- limits

def test_limits_come_from_config():
    assert Limits.from_config().max_line == 42


def test_a_narrower_limit_actually_narrows_the_output(nb):
    """Guards against a limit being read from config and then ignored."""
    narrow = Limits(max_line=24)
    text = "Og så fortalte hun at det hadde vært en lang og vanskelig tid"
    for chunk in cuelib.split_points(text, narrow, nb):
        lines = cuelib.wrap(chunk, narrow, nb) or cuelib.hard_wrap(chunk, narrow)
        assert all(len(ln) <= 24 for ln in lines)
