"""Give each video a human-readable name, derived from what is in it.

    python -m subtitler.steps.titles [<video> ...]

Titles name the deliverables in out/, so the client receives a descriptive
filename rather than IMG_1234. They live in project/videos.md.

An existing title is never overwritten. It was either written by a human or
already refined, and in both cases it is better than anything this step would
produce. Only blank rows are filled.

The heuristic fallback is deliberately dumb, the opening sentence, cut short.
Its job is to guarantee that nothing is ever untitled, not to be clever: a
model does the clever version when one is available, and the old
keyword-matching version of this step only worked because every keyword in it
was German.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

from .. import llm
from ..core import WORK, cfg, slug
from ..langpack import load as load_pack
from ..project import read_videos, write_videos

OPENING_CHARS = 1200
MAX_TITLE = 60


def opening(video: str) -> str:
    """The first stretch of the corrected transcript, or "" if there is none."""
    path = WORK / "review" / f"{slug(video)}.review.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    text = " ".join(s["text"] for s in data["segments"])
    return text[:OPENING_CHARS].strip()


def fallback(text: str) -> str:
    if not text:
        return ""
    first = re.split(r"(?<=[.!?])\s", text)[0]
    if len(first) <= MAX_TITLE:
        return first.rstrip(".!?")
    cut = first[:MAX_TITLE].rsplit(" ", 1)[0]
    return cut.rstrip(",;:").rstrip(".!?")


def prompt(video: str, text: str, language: str) -> str:
    return f"""Below is the opening of a transcript, in {language}.

Give it a short descriptive title in {language}, at most {MAX_TITLE} characters.

It will be used as a filename for the finished subtitles and shown to the
client, so name what this recording *is*: who is speaking and on what occasion,
if that is clear. A wedding speech for two named people should say so. A life
testimony should name the person. Do not invent details that are not in the
text, and do not use quotation marks.

<transcript video="{video}">
{text}
</transcript>

Return ONLY the title, on one line, with no explanation.
"""


def run(videos: list[str], provider: str | None = None) -> int:
    rows, pre, post = read_videos()
    known = {r["source"]: r for r in rows}
    language = load_pack(cfg("project", "source_lang")).name

    targets = videos or sorted(
        p.name.split(".")[0] for p in (WORK / "stt").glob("*.merged.json")
    )
    if not targets:
        sys.exit("no videos found, run steps.diff first")

    engine = None
    written = 0

    for video in targets:
        row = known.get(video)
        if row and row["title"].strip():
            print(f"  {video}: already titled, leaving it alone")
            continue

        text = opening(video)
        if not text:
            print(f"  {video}: no transcript yet, skipping")
            continue

        title = ""
        try:
            engine = engine or llm.get(provider)
            reply = engine.complete(f"{video}.title", prompt(video, text, language), 200)
            title = reply.strip().strip('"').splitlines()[0][:MAX_TITLE]
        except llm.NeedsAgent as e:
            from ..llm.agent import instruction

            print(instruction(e))
            sys.exit(2)
        except SystemExit:
            raise
        except Exception as exc:
            # A title is a convenience, not a gate. Falling back keeps the
            # pipeline moving and the result is still editable by hand.
            print(f"  {video}: model unavailable ({exc}), using the opening sentence")
            title = fallback(text)

        if not title:
            continue
        if row:
            row["title"] = title
        else:
            rows.append({"source": video, "title": title, "notes": ""})
        written += 1
        print(f'  {video}: "{title}"')

    if written:
        write_videos(rows, pre, post)
        print(f"\nwrote {len(rows)} row(s) to project/videos.md, {written} new")
        print("Edit any of them there; nothing overwrites a title once it is set.")
    else:
        print("\nnothing to title")
    return written


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("videos", nargs="*")
    ap.add_argument("--provider", choices=["auto", "anthropic", "agent"])
    args = ap.parse_args(argv)
    run(args.videos, args.provider)


if __name__ == "__main__":
    main()
