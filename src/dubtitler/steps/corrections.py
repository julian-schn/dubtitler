"""Re-import the hand-corrected review doc, preserving every timing.

    python -m dubtitler.steps.corrections <video>

Reads work/review/<video>.<lang>.md, the file the human edited, and rewrites
work/stt/<video>.merged.json with the corrected text.

Word timings survive corrections. If a segment's word count is unchanged the
original per-word times are kept exactly, because the edit was almost certainly
a one-for-one substitution. Otherwise the words are respaced across the
segment's span. Segment start and end are never touched either way, so cue
timing stays anchored to the audio no matter how heavily the text was rewritten.
"""

from __future__ import annotations

import argparse
import re
import sys

from ..core import ROOT, WORK, Transcript, cfg, parse_ts, set_text

HEADER = re.compile(
    r"^##\s*\[(?P<start>[\d:.,]+)\s*(?:→|->)\s*(?P<end>[\d:.,]+)\]"
    r"(?:\s*\[(?P<speaker>[^\]]+)\])?"
)


def parse_review(text: str) -> list[tuple[float, float, str | None, str]]:
    blocks: list[tuple[float, float, str | None, str]] = []
    cur: tuple[float, float, str | None] | None = None
    body: list[str] = []

    def flush() -> None:
        if cur is not None:
            blocks.append((*cur, " ".join(body).strip()))

    for line in text.splitlines():
        m = HEADER.match(line)
        if m:
            flush()
            cur = (parse_ts(m.group("start")), parse_ts(m.group("end")),
                   m.group("speaker"))
            body = []
            continue
        if cur is None:
            continue  # preamble before the first header
        stripped = line.strip()
        if not stripped or stripped.startswith(">") or stripped == "---":
            continue
        body.append(stripped)
    flush()
    return [b for b in blocks if b[3]]


def run(video: str) -> int:
    language = cfg("project", "source_lang")
    merged_path = WORK / "stt" / f"{video}.merged.json"
    review_path = WORK / "review" / f"{video}.{language}.md"
    for p in (merged_path, review_path):
        if not p.exists():
            sys.exit(f"missing {p.relative_to(ROOT)}, run steps.diff first")

    tr = Transcript.load(merged_path)
    blocks = parse_review(review_path.read_text(encoding="utf-8"))

    # Refusing here rather than guessing at an alignment. A wrong guess would
    # attach corrected text to the wrong timings, which is invisible in the
    # file and obvious only in the finished video.
    if len(blocks) != len(tr.segments):
        sys.exit(
            f"segment count mismatch: the review doc has {len(blocks)}, "
            f"merged.json has {len(tr.segments)}.\n"
            "Headers must not be added or removed, only the text between them."
        )

    changed = 0
    for seg, (_start, _end, speaker, text) in zip(tr.segments, blocks):
        changed += set_text(seg, text)
        if speaker:
            seg.speaker = speaker

    if not tr.engine.endswith("+corrected"):
        tr.engine += "+corrected"
    tr.save(merged_path)
    print(f"applied {changed} corrected segment(s) -> {merged_path.relative_to(ROOT)}")
    if changed == 0:
        print("(nothing changed, did you save the review doc?)")
    return changed


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    run(ap.parse_args(argv).video)


if __name__ == "__main__":
    main()
