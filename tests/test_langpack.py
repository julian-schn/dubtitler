"""The shipped packs must keep the behaviour they were tuned to produce.

The German numbers here are not arbitrary examples: folding spelled-out numbers
against digits, and folding dialect elision, is what took cross-engine flagging
on the reference job from 87% of segments to 61%. A regression in `canonical()`
does not fail loudly, it just floods the review gate, so it is pinned here.
"""

import pytest

from subtitler import langpack

SHIPPED = ["de", "nb", "en"]


@pytest.mark.parametrize("code", SHIPPED)
def test_pack_loads_with_content(code):
    """Guards the TOML trap: a root key placed below a [table] header silently
    becomes a member of that table, and the pack loads as empty rather than
    failing."""
    p = langpack.load(code)
    assert p.name
    assert p.function_words
    assert p.negations
    assert p.never_trail
    assert p.break_before
    assert p.numbers.get("units")


@pytest.mark.parametrize("code", SHIPPED)
def test_ordinary_words_are_not_numbers(code):
    p = langpack.load(code)
    for word in ("haus", "kirke", "table", "grandmother"):
        assert p.number(p.norm_token(word)) is None


@pytest.mark.parametrize(
    "word,value",
    [
        ("fünfzehn", 15),
        ("sechzehn", 16),
        ("dreiundzwanzig", 23),
        ("neunzehnhundertachtunddreißig", 1938),
        ("hundert", 100),
        ("1938", 1938),
    ],
)
def test_german_numbers(word, value):
    de = langpack.load("de")
    assert de.number(de.norm_token(word)) == value


@pytest.mark.parametrize(
    "a,b",
    [("hab", "habe"), ("haben", "habe"), ("denk", "denke"), ("neue", "neuen"),
     ("fünfzehn", "15"), ("dreissig", "dreißig")],
)
def test_german_folds_dialect_and_numbers(a, b):
    de = langpack.load("de")
    assert de.canonical(a) == de.canonical(b)


@pytest.mark.parametrize("a,b", [("Andresen", "Anderssen"), ("München", "Gmünd")])
def test_german_keeps_real_differences(a, b):
    """Folding must not be so aggressive that a misheard name survives it."""
    de = langpack.load("de")
    assert de.canonical(a) != de.canonical(b)


def test_sz_casefold_trap():
    """casefold() maps ß onto ss, so the lists have to be folded the same way
    or `Bloß` never matches `bloß`."""
    de = langpack.load("de")
    assert de.is_function_word("Bloß")
    assert de.is_function_word("bloß")


def test_norwegian_does_not_fold_inflection():
    """Unlike German dialect elision, a Norwegian ending usually carries
    grammatical information, so folding it would hide real errors."""
    nb = langpack.load("nb")
    assert nb.canonical("hus") != nb.canonical("huset")


@pytest.mark.parametrize("code,word,value", [("nb", "femten", 15), ("en", "fifteen", 15)])
def test_other_packs_parse_numbers(code, word, value):
    p = langpack.load(code)
    assert p.number(p.norm_token(word)) == value


def test_missing_pack_is_empty_not_an_error():
    """A language nobody has generated a pack for still runs, just with every
    disagreement counting as significant."""
    p = langpack.load("zz-nonexistent")
    assert p.function_words == set()
    assert p.canonical("Ordet") == "ordet"
