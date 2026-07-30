"""Extract speech-recognition-ready audio from the source video.

    python -m subtitler.steps.audio <video>

Writes three files to work/audio/:

    <video>.raw.wav   16 kHz mono, untouched levels
    <video>.wav       the same, loudness-normalised: the default engine input
    <video>.flac      compressed copy of the normalised audio, for uploads

16 kHz mono is what Whisper resamples to internally, so it is done once here
rather than per engine. No denoise and no bandpass: aggressive filtering
measurably hurts Whisper on speech. The raw file is kept because normalisation
occasionally amplifies room noise more than it helps a quiet speaker, and
comparing the two is the fastest way to find out.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ..core import ROOT, WORK, find_source

LOUDNORM = "loudnorm=I=-16:TP=-1.5:LRA=11"


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True
    )


def extract(video: str, force: bool = False) -> dict[str, Path]:
    src = find_source(video)
    out = WORK / "audio"
    out.mkdir(parents=True, exist_ok=True)

    raw, wav, flac = out / f"{video}.raw.wav", out / f"{video}.wav", out / f"{video}.flac"
    if not force and wav.exists() and flac.exists():
        print(f"audio already extracted for {video} (use --force to redo)")
        return {"raw": raw, "wav": wav, "flac": flac}

    _ffmpeg("-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(raw))
    _ffmpeg("-i", str(raw), "-af", LOUDNORM, "-ar", "16000", "-ac", "1",
            "-c:a", "pcm_s16le", str(wav))
    _ffmpeg("-i", str(wav), "-c:a", "flac", str(flac))

    for p in (raw, wav, flac):
        print(f"wrote {p.relative_to(ROOT)}  ({p.stat().st_size / 1e6:.1f} MB)")
    return {"raw": raw, "wav": wav, "flac": flac}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video", help="basename of the file in media/, without extension")
    ap.add_argument("--force", action="store_true", help="re-extract even if present")
    args = ap.parse_args(argv)
    try:
        extract(args.video, force=args.force)
    except FileNotFoundError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
