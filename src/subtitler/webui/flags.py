"""Translation-quality flags for the gate-2 view.

Only three classes are reported, because only these can be *wrong* rather than
merely different from a back-translation. An earlier version compared content
words wholesale and flagged 85 of 108 sentences, which is the same as flagging
none: the reviewer stops reading the flags and starts reading everything, which
is what the flags existed to avoid.

Length and reading speed are deliberately absent. `resegment` splits long
sentences into cues by design, and `qc` is the authority on the cues it actually
produced. Flagging them here would only bury the meaning problems, which are the
part a human can actually help with.
"""

from __future__ import annotations

import re

from ..langpack import Pack
from ..project import read_glossary

MIN_TERM_LEN = 4
MIN_STEM = 4
MAX_REPORTED = 5


def glossary_terms(source: Pack, target: Pack) -> set[str]:
    """Canonical forms of every glossary term, both sides.

    This is the proper-noun detector, and it is an exact human-approved list
    rather than a heuristic. A capitalisation rule cannot work in the German
    direction, where every noun is capitalised.

    Multi-word entries are filtered word by word so that a term like
    `Jugendbund für entschiedenes Christentum` does not put the preposition
    `für` on the watch list.
    """
    try:
        rows, _, _ = read_glossary()
    except Exception:
        return set()

    out: set[str] = set()
    for row in rows:
        for side, pack in ((row["term"], source), (row["translation"], target)):
            for tok in re.findall(r"\w+", side or "", flags=re.UNICODE):
                if len(tok) >= MIN_TERM_LEN and not pack.is_function_word(tok):
                    out.add(source.canonical(tok))
                    out.add(target.canonical(tok))
    return out


def polite(text: str, pack: Pack) -> bool:
    """True if a polite-address form appears where it is genuinely polite.

    In several languages the polite pronoun is spelled like an ordinary one and
    distinguished only by capitalisation, which makes a sentence-initial
    occurrence ambiguous. Norwegian `Dem kjenner jeg nesten ikke` is ordinary
    Bokmål; the same word mid-sentence is the polite form. So position decides,
    and a sentence-initial hit is never reported.
    """
    if not pack.polite_forms:
        return False
    pattern = re.compile(r"\b(" + "|".join(re.escape(f) for f in pack.polite_forms) + r")\b")
    for m in pattern.finditer(text):
        if m.start() == 0:
            continue
        before = text[:m.start()].rstrip()
        if before.endswith((".", "!", "?", ":", "»", "”", ",")):
            continue
        return True
    return False


def drift(source_text: str, back: str, terms: set[str], pack: Pack) -> list[str]:
    """Meaning that failed to survive the round trip through the target language.

    Morphological variants are folded away by a shared stem, because a
    back-translation restructures sentences constantly and none of that is
    information. What is left is words that vanished or appeared, and only
    three kinds of those are reported: numbers, glossary terms, and negations.

    Negations earn their place: dropping one inverts the sentence and the result
    still reads as perfectly fluent, so nothing downstream will catch it.
    """
    if not back:
        return []

    def index(text: str) -> dict[str, str]:
        return {pack.canonical(t): t
                for t in re.findall(r"\w+", text, flags=re.UNICODE)}

    a, b = index(source_text), index(back)

    def missing(x: dict[str, str], y: dict[str, str]) -> list[str]:
        out = []
        for key, original in x.items():
            if key in y or len(key) < 3 or pack.is_function_word(original):
                continue
            if any(len(key) >= MIN_STEM and key[:MIN_STEM] == other[:MIN_STEM]
                   for other in y):
                continue
            if (any(c.isdigit() for c in key) or key in terms
                    or pack.is_negation(original)):
                out.append(original)
        return out

    notes = []
    lost, gained = missing(a, b), missing(b, a)
    if lost:
        notes.append("lost: " + ", ".join(sorted(set(lost))[:MAX_REPORTED]))
    if gained:
        notes.append("added: " + ", ".join(sorted(set(gained))[:MAX_REPORTED]))
    return notes


def sentence_flags(target_text: str, back: str, source_text: str,
                   terms: set[str], source: Pack, target: Pack) -> list[str]:
    """Every gate-2 flag for one sentence."""
    if not target_text:
        return ["untranslated"]
    out = []
    if polite(target_text, target):
        out.append(f"polite form ({target.name})")
    return out + drift(source_text, back, terms, source)
