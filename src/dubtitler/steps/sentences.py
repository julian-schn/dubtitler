"""Reconstitute whole sentences from the word-timed transcript.

    python -m dubtitler.steps.sentences <video>

Writes work/stt/<video>.sentences.json:
    [{id, start, end, speaker, source, words:[{w,start,end}]}...]

This step and `resegment` exist to keep two concerns apart, and that separation
is the reason the pipeline produces usable output at all.

Subtitle cues are cut to fit a screen and a reading speed. Sentences are cut by
grammar. In a verb-final language the word that fixes a clause's meaning
routinely lands in the last cue of that clause, so translating cue by cue asks
the model to commit to a reading before it has seen the evidence. It will
comply, fluently, and the error is invisible in the target language.

So: translate whole sentences, then cut the translation back into cues against
the source word timings. Any change that reintroduces cue-by-cue translation
breaks the output in a way that is hard to see and easy to ship.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from ..core import ROOT, WORK, Transcript, cfg
from ..langpack import Pack, load as load_pack

SENTENCE_END = re.compile(r"[.!?…]+[\"'»)\]]*$")

# Seconds. A hard ceiling so that a speaker who never pauses for breath still
# produces something a model can translate and a human can check.
MAX_SENTENCE = 20.0


def ends_sentence(token: str, pack: Pack) -> bool:
    if not SENTENCE_END.search(token):
        return False
    stem = token.rstrip(".!?…\"'»)]").casefold()
    if stem in pack.abbreviations:
        return False
    # A single capital plus a dot is an initial ("W. Hoffmann"), not a full stop.
    if len(stem) == 1 and token[0].isupper():
        return False
    return True


def run(video: str) -> list[dict]:
    merged = WORK / "stt" / f"{video}.merged.json"
    if not merged.exists():
        sys.exit(f"missing {merged.relative_to(ROOT)}, run steps.diff first")

    pack = load_pack(cfg("project", "source_lang"))
    tr = Transcript.load(merged)

    sentences: list[dict] = []
    buf: list[dict] = []
    speaker: str | None = None

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        sentences.append({
            "id": len(sentences),
            "start": buf[0]["start"],
            "end": buf[-1]["end"],
            "speaker": speaker,
            "source": " ".join(w["w"] for w in buf).strip(),
            "words": buf,
        })
        buf = []

    for seg in tr.segments:
        if buf and seg.speaker != speaker:
            flush()  # a speaker change always ends a sentence
        if not buf:
            speaker = seg.speaker

        words = list(seg.words)
        if not words:
            # A segment with no word timings still has to contribute; spread
            # its tokens across its own span rather than dropping the text.
            toks = seg.text.split()
            if not toks:
                continue
            step = (seg.end - seg.start) / len(toks)
            words = [
                type("W", (), {"w": t, "start": seg.start + i * step,
                               "end": seg.start + (i + 1) * step})()
                for i, t in enumerate(toks)
            ]

        for w in words:
            buf.append({"w": w.w, "start": round(w.start, 3), "end": round(w.end, 3)})
            long_enough = (buf[-1]["end"] - buf[0]["start"]) >= MAX_SENTENCE
            if ends_sentence(w.w, pack) or long_enough:
                flush()
                speaker = seg.speaker
    flush()

    out = WORK / "stt" / f"{video}.sentences.json"
    out.write_text(json.dumps(sentences, ensure_ascii=False, indent=2), encoding="utf-8")

    chars = sum(len(s["source"]) for s in sentences)
    print(f"wrote {out.relative_to(ROOT)}  ({len(sentences)} sentences, {chars} chars)")
    return sentences


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    run(ap.parse_args(argv).video)


if __name__ == "__main__":
    main()
