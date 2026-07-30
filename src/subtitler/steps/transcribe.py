"""Run every configured speech-to-text engine over one video.

    python -m subtitler.steps.transcribe <video> [--engine NAME] [--force]

Writes work/stt/<video>.<engine>.json, one file per engine, all in the same
schema. Engines that cannot run are reported and skipped rather than aborting
the others, so a missing API key costs you one opinion instead of the run.

Existing transcripts are kept unless --force. Transcription is the expensive
step, in money for the cloud engines and in minutes for the local one, and
re-running the review afterwards must not silently pay for it again.
"""

from __future__ import annotations

import argparse
import sys
import time

from ..core import ROOT, WORK, cfg
from .. import stt


def transcribe(video: str, engines: list[str], language: str, force: bool = False) -> list[str]:
    """Returns the engine names that produced a usable transcript."""
    out_dir = WORK / "stt"
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = WORK / "audio"

    done: list[str] = []
    for name in engines:
        target = out_dir / f"{video}.{name}.json"
        if target.exists() and not force:
            print(f"  {name:14} already transcribed, skipping")
            done.append(name)
            continue

        engine = stt.get(name)
        why = engine.available()
        if why:
            print(f"  {name:14} unavailable: {why}", file=sys.stderr)
            continue

        audio = audio_dir / f"{video}.{engine.audio}"
        if not audio.exists():
            print(f"  {name:14} needs {audio.name}, run steps.audio first", file=sys.stderr)
            continue

        print(f"  {name:14} transcribing {audio.name} ...", flush=True)
        t0 = time.monotonic()
        try:
            result = engine.transcribe(audio, language)
        except Exception as e:
            # One engine failing is a lost opinion, not a lost run. The
            # consensus degrades gracefully all the way down to one engine.
            print(f"  {name:14} FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            continue

        result.video = video
        result.save(target)
        words = sum(len(s.words) for s in result.segments)
        print(f"  {name:14} {len(result.segments)} segments, {words} words, "
              f"{time.monotonic() - t0:.0f}s -> {target.relative_to(ROOT)}")
        done.append(name)

    return done


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--engine", action="append", dest="engines",
                    help="run only this engine (repeatable); default is config.toml")
    ap.add_argument("--force", action="store_true", help="re-transcribe even if present")
    args = ap.parse_args(argv)

    engines = args.engines or cfg("stt", "engines")
    language = cfg("project", "source_lang")
    print(f"[{args.video}]  language={language}  engines={', '.join(engines)}")

    done = transcribe(args.video, engines, language, force=args.force)
    if not done:
        sys.exit("no engine produced a transcript")
    if len(done) == 1:
        print(f"\nnote: only {done[0]} ran. Flags will come from its own "
              f"confidence numbers, with no cross-engine check.")


if __name__ == "__main__":
    main()
