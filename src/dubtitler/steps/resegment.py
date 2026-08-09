"""Turn translated sentences into subtitle files.

    python -m dubtitler.steps.resegment <video>

Reads work/stt/<video>.sentences.json, which `translate` filled in, and writes
one SRT per language plus work/stt/<video>.cues.json for the review page and QC.

Deliverables are named after the video's title, not its camera filename, so a
client receives `interview-with-a-neighbour.nb.srt`. Everything under work/
keeps the source-derived slug: retitling renames what is delivered and orphans
nothing.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import cues as cuelib
from ..core import OUT, ROOT, WORK, cfg, media_duration, ts
from ..langpack import load as load_pack
from ..project import release_slug


def to_srt(cues: list[dict]) -> str:
    out = []
    for c in cues:
        out.append(str(c["index"]))
        out.append(f"{ts(c['start'])} --> {ts(c['end'])}")
        out.append(c["text"])
        out.append("")
    return "\n".join(out)


def run(video: str) -> dict[str, list[dict]]:
    path = WORK / "stt" / f"{video}.sentences.json"
    if not path.exists():
        sys.exit(f"missing {path.relative_to(ROOT)}, run steps.sentences first")
    sentences = json.loads(path.read_text(encoding="utf-8"))

    limits = cuelib.Limits.from_config()
    duration = media_duration(video)
    OUT.mkdir(parents=True, exist_ok=True)
    name = release_slug(video)

    results: dict[str, list[dict]] = {}
    # The source-language SRT is a reference artifact for the side-by-side
    # review, but it is wrapped with its own pack: bad breaks there make the
    # comparison harder to read, which defeats its only purpose.
    for code, field in ((cfg("project", "source_lang"), "source"),
                        (cfg("project", "target_lang"), "target")):
        pack = load_pack(code)
        built = cuelib.build(sentences, field, limits, pack, duration)
        if not built:
            print(f"skipping {code}: no text in the '{field}' field")
            continue
        (OUT / f"{name}.{code}.srt").write_text(to_srt(built), encoding="utf-8")
        results[code] = built
        print(f"wrote out/{name}.{code}.srt  ({len(built)} cues)")

    (WORK / "stt" / f"{video}.cues.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not results:
        sys.exit("nothing written: has anything been translated yet?")
    return results


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    run(ap.parse_args(argv).video)


if __name__ == "__main__":
    main()
