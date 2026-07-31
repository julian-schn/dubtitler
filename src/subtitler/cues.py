"""Cut translated sentences into subtitle cues, snapped to the source timings.

Two independent problems, kept separate:

*Where to break.* A cue has to fit a screen and a reading speed, and it should
break where the sentence breathes. Both are scoring problems, and both consult
the target language's pack: which words may start a line, which must never end
one. In Norwegian, breaking between an article and its noun reads as the
særskriving error that native speakers are most sensitive to.

*When to show it.* Never guessed. A cue covering the first 40% of a sentence's
characters gets the timespan of the source words covering the first 40% of that
sentence. Word order differs between languages, but cue boundaries still land
on the audio, because they are derived from the audio.

The functions here take a `Limits` and a `Pack` rather than reading config or
globals, so a test can exercise them at any width in any language.
"""

from __future__ import annotations

from dataclasses import dataclass

from .core import config
from .langpack import Pack

STRIP = ",.;:!?»«\"'"


@dataclass(frozen=True)
class Limits:
    max_line: int = 42
    max_lines: int = 2
    max_cps: float = 17.0
    min_duration: float = 0.833
    max_duration: float = 7.0
    lead_out: float = 1.5
    gap: float = 0.084

    @classmethod
    def from_config(cls) -> "Limits":
        s = config()["subtitles"]
        return cls(**{k: v for k, v in s.items() if k in cls.__dataclass_fields__})

    @property
    def budget(self) -> int:
        """Characters a single cue can hold across all its lines."""
        return self.max_line * self.max_lines


# --------------------------------------------------------------------------
# where to break
# --------------------------------------------------------------------------

def wrap(text: str, limits: Limits, pack: Pack) -> list[str] | None:
    """Lay text out as at most `max_lines` lines, or None if it will not fit.

    Returning None is how the caller learns the cue must be split rather than
    wrapped. Among the fitting break points, the most balanced one wins, unless
    it would strand a word that binds to what follows it.
    """
    if len(text) <= limits.max_line:
        return [text]
    if limits.max_lines < 2:
        return None

    words = text.split()
    best, best_score = None, None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if len(a) > limits.max_line or len(b) > limits.max_line:
            continue
        score = abs(len(a) - len(b))
        if words[i - 1].strip(STRIP).casefold() in pack.never_trail:
            score += 40
        if words[i].strip(STRIP).casefold() in pack.break_before:
            score -= 12
        if words[i - 1].endswith((",", ";", ":")):
            score -= 8
        if best_score is None or score < best_score:
            best, best_score = [a, b], score
    return best


def hard_wrap(text: str, limits: Limits) -> list[str]:
    """Last resort. Breaks between words only, never inside one.

    Reached only when a single token is longer than a line. Cutting inside a
    token once split `1938` into `193` and `8`, which is the kind of error that
    survives review because it looks like a rendering artifact.
    """
    lines, cur = [], ""
    for w in text.split():
        candidate = f"{cur} {w}".strip()
        if cur and len(candidate) > limits.max_line:
            lines.append(cur)
            cur = w
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    return lines or [text]


def force_wrappable(text: str, limits: Limits, pack: Pack) -> list[str]:
    """Split until every piece can be laid out within the line limits.

    A chunk can fit the character budget and still be unwrappable, because
    breaks may only fall between words. Without this pass those chunks reach
    `hard_wrap` and come out as three lines.
    """
    if wrap(text, limits, pack) is not None:
        return [text]
    words = text.split()
    if len(words) < 2:
        return [text]  # a single oversized token; hard_wrap deals with it

    best, best_score = None, None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if wrap(a, limits, pack) is None or wrap(b, limits, pack) is None:
            continue
        score = abs(len(a) - len(b))
        if words[i - 1].strip(STRIP).casefold() in pack.never_trail:
            score += 40
        if words[i - 1].endswith((",", ";", ":", ".")):
            score -= 15
        if best_score is None or score < best_score:
            best, best_score = (a, b), score

    if best is None:
        mid = len(words) // 2
        return (force_wrappable(" ".join(words[:mid]), limits, pack)
                + force_wrappable(" ".join(words[mid:]), limits, pack))
    return [best[0], best[1]]


def split_points(text: str, limits: Limits, pack: Pack) -> list[str]:
    """Break one sentence into cue-sized chunks.

    Every break point that fits inside a cue is scored and the best wins.
    Filling the available width is rewarded, otherwise the first comma in a
    long sentence wins by default and strands a two-word runt; so is breaking
    where the sentence actually breathes.
    """
    chunks, rest = [], text.strip()

    while len(rest) > limits.budget:
        words = rest.split()
        best, best_score = None, None
        length = 0
        for i, w in enumerate(words):
            length += len(w) + 1
            if length > limits.budget:
                break
            cut = i + 1
            if cut >= len(words):
                break
            bare = w.strip(STRIP).casefold()
            nxt = words[cut].strip(STRIP).casefold()

            score = min(length, limits.budget) / limits.budget
            if w.endswith((".", "!", "?", "…")):
                score += 0.45
            elif w.endswith((",", ";", ":")):
                score += 0.30
            if nxt in pack.break_before:
                score += 0.15
            if bare in pack.never_trail:
                score -= 0.80
            # A scrap left behind reads worse than a slightly early break.
            if len(" ".join(words[cut:])) < 12:
                score -= 0.50

            if best_score is None or score > best_score:
                best, best_score = cut, score

        if best is None:
            best = 1  # a single word longer than the budget; emit and move on
        chunks.append(" ".join(words[:best]))
        rest = " ".join(words[best:])
    if rest:
        chunks.append(rest)

    return [part for c in chunks for part in force_wrappable(c, limits, pack)]


# --------------------------------------------------------------------------
# when to show it
# --------------------------------------------------------------------------

def span_for(words: list[dict], frac_start: float, frac_end: float) -> tuple[float, float]:
    """Timespan of the source words covering a character fraction of a sentence."""
    if not words:
        return 0.0, 0.0
    total = sum(len(w["w"]) + 1 for w in words)
    acc = 0.0
    start, end = words[0]["start"], words[-1]["end"]
    hit_start = False
    for w in words:
        lo = acc / total
        acc += len(w["w"]) + 1
        hi = acc / total
        if not hit_start and hi > frac_start:
            start, hit_start = w["start"], True
        if lo < frac_end:
            end = w["end"]
    return start, max(end, start + 0.05)


def needed_duration(cue: dict, limits: Limits) -> float:
    """The shortest this cue may be and still be readable."""
    chars = len(cue["text"].replace("\n", " "))
    return max(limits.min_duration, chars / limits.max_cps)


def lead_out(cues: list[dict], limits: Limits, duration: float | None) -> None:
    """Hold each cue into the silence after it.

    Only ever lengthens. Speech-derived timings end a cue on its last word,
    which is why subtitles built this way feel clipped: the cue vanishes the
    instant the speaker stops, and the eye reads that as cutting early. Capped
    at once by the next cue, the maximum on-screen duration, and the end of the
    picture.
    """
    for i, c in enumerate(cues):
        if i + 1 < len(cues):
            limit = cues[i + 1]["start"] - limits.gap
        elif duration:
            limit = duration
        else:
            limit = c["end"] + limits.lead_out
        c["end"] = max(c["end"], min(c["end"] + limits.lead_out, limit,
                                     c["start"] + limits.max_duration))


def enforce_timing(cues: list[dict], limits: Limits,
                   duration: float | None = None) -> list[dict]:
    """Apply duration, reading-speed and gap rules, then renumber."""
    for c in cues:
        need = needed_duration(c, limits)
        if c["end"] - c["start"] < need:
            c["end"] = c["start"] + need
        if c["end"] - c["start"] > limits.max_duration:
            c["end"] = c["start"] + limits.max_duration

    cues.sort(key=lambda c: c["start"])
    for i in range(1, len(cues)):
        prev, cur = cues[i - 1], cues[i]
        if cur["start"] >= prev["end"] + limits.gap:
            continue
        # Shorten the previous cue, but never below what it needs to stay
        # readable: doing that is what reintroduces reading-speed violations
        # after they have been fixed. Any overlap left over is absorbed by
        # shifting this cue later instead.
        floor = prev["start"] + needed_duration(prev, limits)
        prev["end"] = max(floor, min(prev["end"], cur["start"] - limits.gap))
        if cur["start"] < prev["end"] + limits.gap:
            shift = prev["end"] + limits.gap - cur["start"]
            cur["start"] += shift
            cur["end"] = max(cur["end"] + shift,
                             cur["start"] + needed_duration(cur, limits))

    lead_out(cues, limits, duration)

    for i, c in enumerate(cues, 1):
        c["index"] = i
        c["start"] = round(c["start"], 3)
        c["end"] = round(c["end"], 3)
        c["cps"] = round(
            len(c["text"].replace("\n", " ")) / max(c["end"] - c["start"], 0.001), 1
        )
    return cues


def build(sentences: list[dict], field: str, limits: Limits, pack: Pack,
          duration: float | None = None) -> list[dict]:
    """Cues for one language, from the sentences and the source word timings."""
    cues: list[dict] = []
    for s in sentences:
        text = (s.get(field) or "").strip()
        if not text:
            continue
        chunks = split_points(text, limits, pack)
        total = sum(len(c) for c in chunks) or 1
        acc = 0
        for chunk in chunks:
            f0 = acc / total
            acc += len(chunk)
            f1 = acc / total
            start, end = span_for(s["words"], f0, f1)
            lines = wrap(chunk, limits, pack) or hard_wrap(chunk, limits)
            cues.append({
                "sentence_id": s["id"],
                "start": start,
                "end": end,
                "lines": lines,
                "text": "\n".join(lines),
                "speaker": s.get("speaker"),
            })
    return enforce_timing(cues, limits, duration)
