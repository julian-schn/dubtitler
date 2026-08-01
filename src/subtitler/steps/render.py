"""Produce the video deliverables from the finished SRT.

    python -m subtitler.steps.render <video> [--lang nb] [--skip-burn]

Three artifacts, in increasing order of how much they cost:

    out/<name>.<lang>.srt            the sidecar, already written by resegment
    out/<name>.<lang>.softsubs.mp4   muxed in, toggleable, nothing re-encoded
    out/<name>.<lang>.burned.mp4     burned in, plays anywhere

The soft-muxed file is the one worth sending: one MP4, subtitles the viewer can
switch off, and because video and audio are copied it is identical in quality to
the source and takes seconds. Burn-in is the fallback for players that ignore
embedded tracks, and it re-encodes.

Styling goes through ASS rather than burning the SRT directly. SRT carries no
styling at all, so burning it means accepting libass defaults, which is where
the thin outline traced around each letter comes from.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ..core import (
    OUT, ROOT, WORK, FFmpegError, cfg, config, ffmpeg, ffprobe, find_source,
)
from ..langpack import load as load_pack
from ..project import release_slug

# The design was tuned at this height; every pixel size below scales off it.
REFERENCE_HEIGHT = 720


def alpha(opacity: float) -> str:
    """ASS alpha for a given opacity, as the &HAABBGGRR string libass wants.

    Inverted: 00 is fully opaque and FF fully transparent, so 85% opacity is
    0x26, not 0xD9. Getting this backwards produces a nearly invisible box that
    reads as a rendering bug rather than as a setting.
    """
    value = round((1.0 - max(0.0, min(1.0, opacity))) * 255)
    return f"&H{value:02X}000000"


def style_ass(path: Path, height: int) -> dict:
    """Rewrite ffmpeg's default ASS style for this frame height.

    Sizes in ASS are absolute pixels, so a size tuned on 720p renders tiny on a
    1080p source. Both reference videos were different heights, so this is not
    hypothetical.
    """
    r = config()["render"]
    k = height / REFERENCE_HEIGHT
    font_size = round(r["font_size"] * k)
    margin = round(r["margin"] * k)
    opacity = float(r["box_opacity"])

    if opacity > 0:
        # BorderStyle=3 fills a box behind the whole line instead of tracing an
        # outline around each glyph. libass and VSFilter disagree about whether
        # it is filled from OutlineColour or BackColour, so both are set the
        # same: correct under either, and free.
        border_style, box = 3, alpha(opacity)
        thickness = max(2, round(4 * k))  # box padding, not stroke width
        colours = f"{box},{box}"
    else:
        border_style, thickness = 1, max(1, round(2 * k))
        colours = "&H00000000,&H80000000"

    style = (
        f"Style: Default,{r['font']},{font_size},&H00FFFFFF,&H000000FF,{colours},"
        f"0,0,0,0,100,100,0,0,{border_style},{thickness},0,2,"
        f"{margin},{margin},{margin},1"
    )

    text = path.read_text(encoding="utf-8")
    text, n = re.subn(r"^Style: Default.*$", style, text, count=1, flags=re.M)
    if not n:
        sys.exit(f"no Default style line in {path.name}: ffmpeg's ASS output changed")
    # PlayResY has to match the real height or libass rescales the sizes again.
    text = re.sub(r"^PlayResY:.*$", f"PlayResY: {height}", text, count=1, flags=re.M)
    path.write_text(text, encoding="utf-8")

    return {"font_size": font_size, "margin": margin, "thickness": thickness,
            "opacity": opacity, "border_style": border_style}


def run(video: str, lang: str | None = None, skip_burn: bool = False) -> list[Path]:
    lang = lang or cfg("project", "target_lang")
    src = find_source(video)
    name = release_slug(video)
    srt = OUT / f"{name}.{lang}.srt"
    if not srt.exists():
        sys.exit(f"missing {srt.relative_to(ROOT)}, run steps.resegment first")

    # MP4 stores languages as ISO 639-2. A two-letter code is accepted on the
    # command line and then silently dropped, leaving a track players label
    # "Unknown". Some packs prefer a macrolanguage code here for the same
    # reason; see `mp4_lang`.
    iso3 = load_pack(lang).mp4_lang
    written: list[Path] = []

    soft = OUT / f"{name}.{lang}.softsubs.mp4"
    print(f"muxing soft subtitles (language={iso3}) ...", flush=True)
    ffmpeg("-i", str(src), "-i", str(srt), "-c", "copy", "-c:s", "mov_text",
           "-metadata:s:s:0", f"language={iso3}", str(soft))
    written.append(soft)

    tag = ffprobe(soft, "stream_tags=language", stream="s:0")
    if iso3 not in tag:
        print(f"warning: subtitle language tag did not stick (got {tag!r})",
              file=sys.stderr)

    if skip_burn:
        print("skipping burn-in")
    else:
        height = int(ffprobe(src, "stream=height", stream="v:0"))
        ass = WORK / f"{video}.{lang}.ass"
        ass.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg("-i", str(srt), str(ass))
        info = style_ass(ass, height)
        print(f"styling for {height}p: font={info['font_size']} "
              f"margin={info['margin']} opacity={info['opacity']:.0%}")

        burned = OUT / f"{name}.{lang}.burned.mp4"
        print("burning in (re-encodes the video, a minute or two) ...", flush=True)
        ffmpeg("-i", str(src), "-vf", f"ass={ass}",
               "-c:v", "libx264", "-preset", "slow",
               "-crf", str(config()["render"]["crf"]), "-pix_fmt", "yuv420p",
               "-c:a", "copy", str(burned))
        written.append(burned)

    print()
    for p in [srt, *written]:
        print(f"wrote {p.relative_to(ROOT)}  ({p.stat().st_size / 1e6:.1f} MB)")
    return written


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--lang", help="defaults to project.target_lang")
    ap.add_argument("--skip-burn", action="store_true",
                    help="soft-mux only, skips the slow re-encode")
    args = ap.parse_args(argv)
    try:
        run(args.video, args.lang, args.skip_burn)
    except FFmpegError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
