"""Speak the translation over the original audio.

    python -m dubtitler.steps.dub <video>

Voiceover, not replacement. The original stays audible underneath at `duck`,
ducking out of the way while the dub speaks and back up between sentences. That
is the archival convention for testimony, and it is also the only way a listener
who speaks the source language can check the dub against what was actually said.
Replacing the audio outright throws that away and cannot be undone downstream.

Timing comes from the sentences, not the cues. A sentence already carries the
timespan of the source words that produced it, so a dub clip lands on the same
speech the subtitles were cut from. Cues are a reading-speed artefact and would
chop a spoken line into pieces no voice can deliver.

Runs off `translate`, not `render`: it needs the translated sentences and never
looks at the SRTs.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from .. import tts
from ..core import (
    OUT, ROOT, WORK, FFmpegError, cfg, config, ffmpeg, ffprobe, find_source,
)
from ..project import release_slug

# ffmpeg's volume expression divides by the fade length. A pack or config that
# asks for no fade at all would divide by zero, so the ramp is clamped to
# something short enough to be inaudible.
MIN_FADE = 0.001


@dataclass
class Fit:
    """The levers, read once from config."""

    tolerance: float
    speed_max: float
    atempo_max: float
    max_overrun: float
    guard: float

    @classmethod
    def from_config(cls) -> "Fit":
        d = config()["dubbing"]
        return cls(
            tolerance=float(d["tolerance"]),
            speed_max=float(d["speed_max"]),
            atempo_max=float(d["atempo_max"]),
            max_overrun=float(d["max_overrun"]),
            guard=float(d["guard"]),
        )


# --------------------------------------------------------------------------
# fitting a clip to its timespan
#
# Pure arithmetic, no ffmpeg and no I/O, because this is the part that decides
# whether the dub drifts and it should be the part that is trivial to test.
# --------------------------------------------------------------------------

def headroom(end: float, next_start: float | None, fit: Fit) -> float:
    """Seconds a clip may run past its sentence without crowding the next one.

    Never negative: back-to-back sentences leave no room at all, and a negative
    allowance would read as "start earlier", which is not on offer.
    """
    if next_start is None:
        return fit.max_overrun
    return max(0.0, min(fit.max_overrun, next_start - end - fit.guard))


def speed_for(raw: float, target: float, fit: Fit) -> float:
    """Lever 2: what to ask the engine for, so it speaks this in `target`.

    Capped at `speed_max`. Asking an engine to read much faster than that stops
    sounding like a person reading quickly and starts sounding like a tape
    played fast, which is what lever 3 already does and does better.
    """
    if target <= 0 or raw <= target * (1 + fit.tolerance):
        return 1.0
    return min(raw / target, fit.speed_max)


def stretch_for(raw: float, target: float, room: float,
                fit: Fit) -> tuple[float, float, bool]:
    """Levers 3 and 4: time-stretch, then overrun. Returns (atempo, overrun, fits).

    Overrun is last on purpose even though it costs no audio quality. A clip
    that runs past its sentence is a clip drifting out of sync with the picture,
    and on this material the speaker is on camera.
    """
    slack = target * (1 + fit.tolerance)
    if target <= 0 or raw <= slack:
        return 1.0, 0.0, True

    atempo = min(raw / target, fit.atempo_max)
    after = raw / atempo
    if after <= slack:
        return atempo, 0.0, True

    overrun = min(room, after - target)
    return atempo, overrun, after <= target + overrun + target * fit.tolerance


def duck_expr(regions: list[tuple[float, float]], duck: float, fade: float) -> str:
    """A `volume` expression holding the original down while the dub speaks.

    One trapezoid per region — ramp in over `fade`, hold, ramp out — combined
    with max() so overlapping regions duck once rather than twice. Built as a
    string and tested as a string: it is invisible when wrong, and the failure
    mode is an original that stays loud under the whole dub.
    """
    if not regions:
        return "1"

    fade = max(fade, MIN_FADE)
    envs = []
    for start, end in regions:
        rise, fall = start - fade, end + fade
        envs.append(
            f"max(0,min(1,min((t-{rise:.3f})/{fade:.3f},({fall:.3f}-t)/{fade:.3f})))"
        )

    combined = envs[0]
    for env in envs[1:]:
        combined = f"max({combined},{env})"
    return f"1-{1 - duck:.3f}*({combined})"


# --------------------------------------------------------------------------
# synthesis
# --------------------------------------------------------------------------

@dataclass
class Clip:
    id: int
    start: float
    path: Path
    duration: float     # after atempo
    atempo: float
    overrun: float
    fits: bool

    @property
    def end(self) -> float:
        return self.start + self.duration


def load_sentences(video: str) -> list[dict]:
    path = WORK / "stt" / f"{video}.sentences.json"
    if not path.exists():
        sys.exit(f"missing {path.relative_to(ROOT)}, run steps.sentences first")
    sentences = json.loads(path.read_text(encoding="utf-8"))

    untranslated = [s["id"] for s in sentences if not s.get("target")]
    if untranslated:
        sys.exit(
            f"{len(untranslated)} sentences have no translation "
            f"(first: {untranslated[0]}), run steps.translate first"
        )
    return sentences


def resolve(lang: str) -> tts.Voice:
    """The configured voice, or an exit explaining which part is missing."""
    name = cfg("dubbing", "engine")
    try:
        voice = tts.get(name)
    except KeyError as e:
        sys.exit(str(e))
    # Language before availability, deliberately. An engine that cannot speak
    # the target language will not become able to by installing its package, so
    # reporting the missing dependency first sends the reader to fix the wrong
    # thing and then hit this anyway.
    for problem in (voice.speaks(lang), voice.available()):
        if problem:
            sys.exit(problem)
    return voice


def synthesise(voice: tts.Voice, sentences: list[dict], lang: str,
               out_dir: Path, fit: Fit, force: bool) -> list[Clip]:
    """One clip per sentence, fitted to its timespan.

    Two passes at most. The first is always at the engine's natural rate and is
    cached under the sentence id; the second, if the line came out too long and
    the engine has rate control, is cached separately so that a re-run measures
    the natural take rather than re-speeding an already-sped one.
    """
    clips: list[Clip] = []
    starts = [float(s["start"]) for s in sentences]

    for i, s in enumerate(sentences):
        sid = int(s["id"])
        start, end = float(s["start"]), float(s["end"])
        span = end - start
        room = headroom(end, starts[i + 1] if i + 1 < len(starts) else None, fit)

        natural = out_dir / f"{sid}.wav"
        if force or not natural.exists():
            voice.synth(s["target"], lang, natural)
        raw = float(ffprobe(natural, "format=duration"))

        path = natural
        speed = speed_for(raw, span, fit) if voice.native_speed else 1.0
        if speed > 1.0:
            fast = out_dir / f"{sid}@{speed:.3f}.wav"
            if force or not fast.exists():
                voice.synth(s["target"], lang, fast, speed=speed)
            path = fast
            raw = float(ffprobe(fast, "format=duration"))

        atempo, overrun, fits = stretch_for(raw, span, room, fit)
        clips.append(Clip(
            id=sid, start=start, path=path,
            duration=raw / atempo, atempo=atempo, overrun=overrun, fits=fits,
        ))
    return clips


# --------------------------------------------------------------------------
# mixing
# --------------------------------------------------------------------------

def graph(clips: list[Clip], expr: str, rate: int, loudnorm: str) -> str:
    """The filter graph: ducked original plus every clip, delayed into place.

    Written to a file by `core.ffmpeg(script=...)` rather than passed inline —
    on a full video this is tens of kilobytes and would be unreadable in a
    process listing when something goes wrong.
    """
    fmt = f"aresample={rate},aformat=sample_fmts=fltp:channel_layouts=stereo"
    lines = [f"[0:a]{fmt},volume=volume='{expr}':eval=frame[orig];"]

    labels = []
    for n, clip in enumerate(clips, start=1):
        tempo = f"atempo={clip.atempo:.4f}," if clip.atempo > 1.0 else ""
        delay = int(round(clip.start * 1000))
        lines.append(f"[{n}:a]{tempo}{fmt},adelay={delay}:all=1[c{n}];")
        labels.append(f"[c{n}]")

    # normalize=0 so amix sums rather than averaging: averaging would drop the
    # original by 3 dB for every clip added, which is the ducking done wrong.
    lines.append(f"{''.join(labels)}amix=inputs={len(clips)}:normalize=0[bed];")
    lines.append(f"[orig][bed]amix=inputs=2:normalize=0,loudnorm={loudnorm}[out]")
    return "\n".join(lines)


def mix(video: str, clips: list[Clip], lang: str) -> list[Path]:
    """Write the dub track, then mux it against the source video."""
    d = config()["dubbing"]
    src = find_source(video)
    name = release_slug(video)
    OUT.mkdir(parents=True, exist_ok=True)

    expr = duck_expr([(c.start, c.end) for c in clips],
                     float(d["duck"]), float(d["duck_fade"]))
    script = graph(clips, expr, int(d["sample_rate"]), d["loudnorm"])

    audio = OUT / f"{name}.{lang}.dub.m4a"
    inputs = ["-i", str(src)]
    for clip in clips:
        inputs += ["-i", str(clip.path)]
    print(f"mixing {len(clips)} clips over the ducked original ...", flush=True)
    ffmpeg(*inputs, "-map", "[out]", "-c:a", "aac", "-b:a", str(d["bitrate"]),
           "-ar", str(d["sample_rate"]), str(audio), script=script)

    # Video copied, not re-encoded: the dub changes nothing about the picture.
    dubbed = OUT / f"{name}.{lang}.dub.mp4"
    print("muxing against the video ...", flush=True)
    ffmpeg("-i", str(src), "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "copy", "-c:a", "copy", "-shortest", str(dubbed))
    return [audio, dubbed]


# --------------------------------------------------------------------------

def run(video: str, spend: bool = False, force: bool = False) -> list[Path]:
    lang = cfg("project", "target_lang")
    # Engine before data: a voice that cannot speak this language is a config
    # mistake worth hearing about immediately, whatever state the job is in.
    voice = resolve(lang)
    sentences = load_sentences(video)
    fit = Fit.from_config()

    chars = sum(len(s["target"]) for s in sentences)
    if voice.paid and chars > int(cfg("dubbing", "char_warn")) and not spend:
        sys.exit(
            f"{chars} characters through {voice.name}, which is billed per "
            f"character\n  re-run with --spend to go ahead"
        )

    out_dir = WORK / "dub" / video / lang
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"{voice.name}: {len(sentences)} sentences, {chars} characters", flush=True)

    clips = synthesise(voice, sentences, lang, out_dir, fit, force)
    written = mix(video, clips, lang)

    stretched = [c.id for c in clips if c.atempo > 1.0]
    overrun = [c.id for c in clips if c.overrun > 0]
    if stretched:
        print(f"time-stretched {len(stretched)} clips: {_ids(stretched)}")
    if overrun:
        print(f"ran into the following silence on {len(overrun)}: {_ids(overrun)}")

    # One summary rather than a warning per clip. A line that still does not fit
    # is the only thing here a human has to act on, and it should not be buried
    # under the ones the fit chain handled.
    tight = [c.id for c in clips if not c.fits]
    if tight:
        print(f"\n{len(tight)} sentences could not be made to fit, and overlap "
              f"what follows:\n  {_ids(tight)}\n"
              f"  shorten the translation for these in the review app.",
              file=sys.stderr)

    print()
    for p in written:
        print(f"wrote {p.relative_to(ROOT)}  ({p.stat().st_size / 1e6:.1f} MB)")
    return written


def _ids(ids: list[int], limit: int = 20) -> str:
    shown = ", ".join(str(i) for i in ids[:limit])
    return shown if len(ids) <= limit else f"{shown} … and {len(ids) - limit} more"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--spend", action="store_true",
                    help="go ahead on a paid engine above dubbing.char_warn")
    ap.add_argument("--force", action="store_true",
                    help="re-synthesise every clip instead of reusing work/dub/")
    args = ap.parse_args(argv)
    try:
        run(args.video, spend=args.spend, force=args.force)
    except (FileNotFoundError, FFmpegError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
