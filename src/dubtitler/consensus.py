"""Compare N transcripts of the same audio and find the spots worth reviewing.

With two engines all you can say is "they differ", and a human has to adjudicate
every difference. With three or more you can say "two of these three read it the
same way", which turns most differences into a default answer and leaves only
genuine splits for a person.

The whole design rests on one property: the flag has to be *rare enough to
read*. Raw token disagreement between two good engines runs around 20%, so
flagging a segment because any token differs flags essentially every segment,
and the review document gets skimmed instead of read. Significance filtering is
what makes the difference, and it is why `canonical()` folding lives in the
language pack rather than being a general string normaliser.

A note on engine choice: a vote is only worth something when the voters fail
independently. Three Whisper variants agreeing tells you very little, because
they share a training recipe and therefore share their blind spots.
"""

from __future__ import annotations

import difflib
from collections import Counter
from dataclasses import dataclass, field

from .core import Transcript, Word
from .langpack import Pack

# One engine missing this many words in a row is a recall gap: it did not hear
# speech that the others did. That is a different and more serious failure than
# disagreeing about a word, so it counts as significant on its own.
DROPPED_PHRASE_WORDS = 2

# Tokens shorter than this are almost always function words in the languages
# tested, and act as a backstop when a pack's function-word list is thin.
MIN_CONTENT_LEN = 4


@dataclass
class Region:
    """A stretch of the spine transcript that the engines do not agree on."""

    start: int                      # first spine word index, inclusive
    end: int                        # last spine word index, exclusive
    readings: dict[str, str] = field(default_factory=dict)  # engine -> text
    significant: bool = False
    dropped: bool = False
    majority: str | None = None     # the reading a strict majority produced
    outvoted: bool = False          # the spine is not in that majority

    def note(self, pack: Pack) -> str:
        """One line a human can act on, grouping engines by what they heard.

        Readings are truncated. One engine starting late or missing a passage
        produces a contested region tens of words long, and a note that dumps
        all of it into the review document is worse than no note: the reader
        skips the whole line, including the short ones around it.
        """
        groups: dict[str, list[str]] = {}
        for engine, text in self.readings.items():
            groups.setdefault(_fold(text, pack), []).append(engine)

        parts = []
        for _canon, engines in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            sample = next(t for e, t in self.readings.items() if e in engines)
            parts.append(f"`{_shorten(sample)}` ({', '.join(engines)})")
        return " vs ".join(parts)


NOTE_WORDS = 8


def _shorten(text: str) -> str:
    """A reading as it appears in the review document.

    Long readings keep both ends: the start says what the passage is, and the
    end is where the two engines usually parted company.
    """
    if not text:
        return "(nothing)"
    words = text.split()
    if len(words) <= NOTE_WORDS * 2:
        return text
    head = " ".join(words[:NOTE_WORDS])
    tail = " ".join(words[-NOTE_WORDS:])
    return f"{head} … [{len(words) - NOTE_WORDS * 2} more] … {tail}"


def _fold(text: str, pack: Pack) -> str:
    return " ".join(pack.canonical(t) for t in text.split())


def _disputed(readings: dict[str, str], pack: Pack) -> dict[str, list[str]]:
    """Per engine, the words in its reading that not every engine has.

    A region can span words the engines agree on: two disagreements a word
    apart are merged into one region so the note reads as a phrase rather than
    as fragments. Judging significance on everything the region covers would
    then let an agreed content word in the middle promote a dispute that is
    really about the function words around it.
    """
    counts = {e: Counter(pack.canonical(t) for t in text.split())
              for e, text in readings.items()}
    agreed: Counter = None  # type: ignore[assignment]
    for c in counts.values():
        agreed = c.copy() if agreed is None else (agreed & c)

    out: dict[str, list[str]] = {}
    for engine, text in readings.items():
        remaining = agreed.copy()
        extra = []
        for token in text.split():
            canon = pack.canonical(token)
            if remaining[canon] > 0:
                remaining[canon] -= 1
            else:
                extra.append(token)
        out[engine] = extra
    return out


def is_content(token: str, pack: Pack) -> bool:
    """True if a disagreement on this token could change the meaning.

    Unstressed function words are where engines disagree most and where it
    matters least: German `im` against `am`, `mir` against `mich`. A
    disagreement confined to those is engine noise.
    """
    if pack.is_function_word(token):
        return False
    bare = pack.norm_token(token)
    return len(bare) >= MIN_CONTENT_LEN or any(c.isdigit() for c in bare)


def _cover(spine: list[Word], other: list[Word], pack: Pack):
    """Map every spine word index onto the other engine's word range.

    Returns (cover, blocks) where cover[i] is a half-open range into `other`,
    and blocks are the spine ranges the two do not agree on.
    """
    a = [pack.canonical(w.w) for w in spine]
    b = [pack.canonical(w.w) for w in other]

    cover: list[list[int]] = [[0, 0] for _ in spine]
    blocks: list[tuple[int, int]] = []

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
        a=a, b=b, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                cover[i1 + k] = [j1 + k, j1 + k + 1]
            continue

        if i1 == i2:
            # The other engine heard words here that the spine did not. There is
            # no spine index to hang them on, so they attach to the preceding
            # word and widen its range; otherwise the extra words vanish from
            # the comparison entirely.
            anchor = max(0, i1 - 1)
            if cover:
                cover[anchor][1] = max(cover[anchor][1], j2)
                blocks.append((anchor, anchor + 1))
            continue

        for i in range(i1, i2):
            cover[i] = [j1, j2]
        blocks.append((i1, i2))

    return cover, blocks


def _merge(blocks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Collapse overlapping spine ranges so each contested spot is judged once."""
    out: list[list[int]] = []
    for lo, hi in sorted(blocks):
        if out and lo <= out[-1][1]:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [(lo, hi) for lo, hi in out]


def compare(
    spine: Transcript, others: dict[str, Transcript], pack: Pack
) -> list[Region]:
    """Find and score every region the engines disagree on.

    `others` maps engine name to transcript. With an empty `others` this
    returns nothing, which is correct: a single engine has no one to disagree
    with, and its flags come from its own confidence numbers instead.
    """
    spine_words = spine.words()
    if not spine_words or not others:
        return []

    covers: dict[str, list[list[int]]] = {}
    all_blocks: list[tuple[int, int]] = []
    for name, other in others.items():
        cover, blocks = _cover(spine_words, other.words(), pack)
        covers[name] = cover
        all_blocks.extend(blocks)

    n_voters = len(others) + 1
    regions: list[Region] = []

    for lo, hi in _merge(all_blocks):
        spine_text = " ".join(w.w for w in spine_words[lo:hi])
        readings = {spine.engine: spine_text}

        for name, other in others.items():
            cover = covers[name]
            j_lo = min(cover[i][0] for i in range(lo, hi))
            j_hi = max(cover[i][1] for i in range(lo, hi))
            words = other.words()[j_lo:j_hi]
            readings[name] = " ".join(w.w for w in words)

        votes = Counter(_fold(t, pack) for t in readings.values())
        top, top_n = votes.most_common(1)[0]
        spine_fold = _fold(spine_text, pack)

        # A strict majority is more than half the voters, so two of three counts
        # and two of four does not. Ties stay unresolved on purpose: an even
        # split is exactly the case a human should look at.
        has_majority = top_n * 2 > n_voters

        disputed = _disputed(readings, pack)
        sizes = [len(v) for v in disputed.values()]
        dropped = min(sizes) == 0 and max(sizes) >= DROPPED_PHRASE_WORDS
        contentful = any(
            is_content(t, pack) for tokens in disputed.values() for t in tokens
        )

        regions.append(
            Region(
                start=lo,
                end=hi,
                readings=readings,
                significant=dropped or contentful,
                dropped=dropped,
                majority=top if has_majority else None,
                outvoted=has_majority and top != spine_fold,
            )
        )

    return regions
