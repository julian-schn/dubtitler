"""Validate the generated subtitles. Exits non-zero if anything is wrong.

    python -m dubtitler.steps.qc <video>

This is the gate before render. It re-derives every limit from config.toml and
checks the files on disk rather than the objects in memory, so it catches
problems introduced by writing and reading as well as by the splitter.

Checking the same limits that `resegment` enforces is not redundant. The
splitter is the thing most likely to be changed, and a limit it stops honouring
is invisible in the output until someone reads a subtitle that flashed past.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core import OUT, cfg, media_duration, parse_ts
from ..cues import Limits
from ..langpack import load as load_pack
from ..project import release_slug

# Rounding slack, so a cue measured at 17.04 does not fail a 17.0 limit.
CPS_TOLERANCE = 0.5
EPS = 0.001
MAX_REPORTED = 40


def parse_srt(path: Path) -> list[dict]:
    blocks, block = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            block.append(line)
        elif block:
            blocks.append(block)
            block = []
    if block:
        blocks.append(block)

    out = []
    for b in blocks:
        if len(b) < 2 or "-->" not in b[1]:
            continue
        start, end = (p.strip() for p in b[1].split("-->"))
        out.append({"index": b[0].strip(), "start": parse_ts(start),
                    "end": parse_ts(end), "lines": b[2:]})
    return out


def check(path: Path, limits: Limits, duration: float | None,
          expect_chars: set[str] | None = None) -> list[str]:
    errs: list[str] = []
    raw = path.read_bytes()
    try:
        text_all = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"{path.name}: not valid UTF-8 ({e})"]

    cues = parse_srt(path)
    if not cues:
        return [f"{path.name}: no cues parsed"]

    for i, c in enumerate(cues):
        tag = f"{path.name} cue {c['index']} @ {c['start']:.3f}"
        text = " ".join(c["lines"]).strip()

        if not text:
            errs.append(f"{tag}: empty cue")
            continue
        if len(c["lines"]) > limits.max_lines:
            errs.append(f"{tag}: {len(c['lines'])} lines (max {limits.max_lines})")
        for ln in c["lines"]:
            if len(ln) > limits.max_line:
                errs.append(
                    f"{tag}: line {len(ln)} chars (max {limits.max_line}): {ln!r}")

        dur = c["end"] - c["start"]
        if dur <= 0:
            errs.append(f"{tag}: non-positive duration")
            continue
        if dur < limits.min_duration - EPS:
            errs.append(f"{tag}: {dur:.3f}s shorter than {limits.min_duration}s")
        if dur > limits.max_duration + EPS:
            errs.append(f"{tag}: {dur:.3f}s longer than {limits.max_duration}s")

        cps = len(text) / dur
        if cps > limits.max_cps + CPS_TOLERANCE:
            errs.append(f"{tag}: {cps:.1f} chars/sec (max {limits.max_cps})")

        if i:
            prev = cues[i - 1]
            if c["start"] < prev["end"]:
                errs.append(f"{tag}: overlaps the previous cue")
            elif c["start"] - prev["end"] < limits.gap - EPS:
                errs.append(f"{tag}: gap {c['start'] - prev['end']:.3f}s "
                            f"below {limits.gap}s")

        if duration and c["end"] > duration + 0.5:
            errs.append(f"{tag}: ends at {c['end']:.2f}s, past the video's "
                        f"{duration:.2f}s")

    # An encoding accident usually destroys a language's non-ASCII letters
    # wholesale, so their total absence from a file that should be full of them
    # is the cheapest possible detector.
    if expect_chars and not any(ch in text_all for ch in expect_chars):
        errs.append(f"{path.name}: none of {''.join(sorted(expect_chars))} appear "
                    f"anywhere, which is suspicious for this language: check that "
                    f"the encoding survived")
    return errs


def run(video: str) -> int:
    limits = Limits.from_config()
    duration = media_duration(video)
    paths = sorted(OUT.glob(f"{release_slug(video)}.*.srt"))
    if not paths:
        sys.exit(f"no SRT files for {video}, run steps.resegment first")

    target_code = cfg("project", "target_lang")
    target_pack = load_pack(target_code)
    expected = {c for c in target_pack.transliterate if len(c) == 1}

    total = 0
    for p in paths:
        # Only the target file is checked for its language's own letters; the
        # source file is a reference artifact and may legitimately be ASCII.
        chars = expected if p.stem.endswith(f".{target_code}") else None
        errs = check(p, limits, duration, chars)
        total += len(errs)
        if errs:
            print(f"\n{p.name}: {len(errs)} problem(s)")
            for e in errs[:MAX_REPORTED]:
                print(f"  - {e}")
            if len(errs) > MAX_REPORTED:
                print(f"  ... and {len(errs) - MAX_REPORTED} more")
        else:
            print(f"{p.name}: OK ({len(parse_srt(p))} cues)")

    if total:
        sys.exit(f"\nQC FAILED: {total} problem(s)")
    print("\nQC passed.")
    return 0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    run(ap.parse_args(argv).video)


if __name__ == "__main__":
    main()
