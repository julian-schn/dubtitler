"""Shared foundations: config, the transcript schema, slugs, and table files.

Everything language-specific lives in a language pack (see `langpack.py`), not
here. This module should never need to know which languages a job uses.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import tomllib
import unicodedata
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

# Normally the repository this file lives in: a job is a clone, so the code and
# the job's data share a root. DUBTITLER_ROOT points somewhere else, which is
# how the end-to-end test drives the real steps over a fixture without writing
# into the repository.
ROOT = Path(os.environ.get("DUBTITLER_ROOT") or Path(__file__).resolve().parents[2])
MEDIA = ROOT / "media"
WORK = ROOT / "work"
OUT = ROOT / "out"
PROJECT = ROOT / "project"
LANG = ROOT / "lang"

VIDEO_EXTS = (".MP4", ".mp4", ".MOV", ".mov", ".m4v", ".M4V", ".mkv", ".webm")


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

@lru_cache(maxsize=1)
def config() -> dict:
    """The job's config.toml, with defaults filled in.

    Cached: it is read on nearly every call and never changes mid-run.
    """
    path = ROOT / "config.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    defaults = {
        "project": {"name": "Untitled", "source_lang": "de", "target_lang": "en"},
        "stt": {"engines": ["whisper-local"], "spine": ""},
        # Engine name -> model id. Flat, keyed by the same names as
        # stt.engines, so it merges one level deep like everything else. An
        # empty or absent entry means the engine's own built-in default.
        "models": {},
        "audio": {"loudnorm": "I=-16:TP=-1.5:LRA=11"},
        # What makes a segment worth a human's attention at gate 1. These are
        # the numbers behind the recall-for-precision trade described in
        # CLAUDE.md; raising them flags less and reads faster, lowering them
        # flags more and gets skimmed.
        "flags": {
            # Whisper's mean log-probability over a segment. Empirically, clean
            # speech sits above -0.45; below -0.6 real errors start clustering.
            "avg_logprob_floor": -0.6,
            "no_speech_ceiling": 0.5,
            # A single weak word in a twelve-word segment is normal, not a
            # signal. Flag on a cluster of weak words, or one truly weak word.
            "word_prob_floor": 0.5,
            "word_prob_alarm": 0.35,
            "weak_word_cluster": 2,
        },
        "llm": {"provider": "auto", "model": "claude-opus-5"},
        "subtitles": {
            "max_line": 42, "max_lines": 2, "max_cps": 17.0,
            "min_duration": 0.833, "max_duration": 7.0,
            "lead_out": 1.5, "gap": 0.084,
        },
        "render": {
            "font": "Arial", "font_size": 22, "box_opacity": 0.85,
            "margin": 40, "crf": 18,
        },
        # Flat on purpose. The merge below is one level deep, so a nested
        # [dubbing.fit] table in config.toml would replace this whole dict and
        # every sub-key the human did not write would raise KeyError in cfg().
        "dubbing": {
            "engine": "kokoro",
            "voice": "",            # engine's voice id; "" means its default
            "voice_model": "",      # engine's model id; "" means its default
            "duck": 0.18,           # original's level under the dub
            "duck_fade": 0.25,      # seconds ramping in and out of each duck
            "tolerance": 0.05,      # overshoot tolerated before anything is done
            "speed_max": 1.15,      # ceiling on native engine rate control
            "atempo_max": 1.10,     # ceiling on time-stretch, audible above this
            "max_overrun": 1.0,     # seconds a clip may run into the next silence
            "guard": 0.15,          # seconds kept clear before the next clip
            "sample_rate": 48000,
            "bitrate": "192k",
            "loudnorm": "I=-16:TP=-1.5:LRA=11",
            "char_warn": 20000,     # projected characters above which steps.dub needs --spend
        },
    }
    for section, values in defaults.items():
        merged = {**values, **data.get(section, {})}
        data[section] = merged
    return data


def cfg(section: str, key: str):
    return config()[section][key]


# --------------------------------------------------------------------------
# media
# --------------------------------------------------------------------------

class FFmpegError(RuntimeError):
    """ffmpeg or ffprobe exited non-zero, carrying what it said about it.

    Exists so callers can `sys.exit(str(e))` and show the user the actual
    complaint. Before this, one call site captured stderr and reported it, one
    let it through to the terminal, and one swallowed it into a traceback.
    """


def ffmpeg(*args: str, script: str | None = None) -> subprocess.CompletedProcess:
    """Run ffmpeg quietly, raising FFmpegError with its stderr on failure.

    `script` is a filter graph. It is written to a temp file and passed as
    a path rather than inline, so that a graph too long to
    read in a process listing is still sitting on disk after a failure. It is
    inserted immediately before the last argument, which every call site keeps
    as the output path.
    """
    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if script is None:
        return _run([*base, *args])

    # Deleted on the way out even if ffmpeg fails: keeping it would be more
    # useful, but this runs per video and the graph is regenerated verbatim.
    with tempfile.NamedTemporaryFile("w", suffix=".filter", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        return _run([*base, *args[:-1], *_script_option(path), args[-1]])
    finally:
        os.unlink(path)


@lru_cache(maxsize=1)
def _ffmpeg_major() -> int | None:
    out = _run(["ffmpeg", "-hide_banner", "-version"]).stdout
    m = re.search(r"version n?(\d+)\.", out)
    return int(m.group(1)) if m else None


def _script_option(path: str) -> list[str]:
    """How to hand ffmpeg a filter graph from a file.

    `-filter_complex_script` is the only spelling ffmpeg 6 knows, and current
    Homebrew builds reject it outright. `-/filter_complex` replaced it in 7.0,
    so neither works everywhere. Git builds report no release number and are
    assumed current.
    """
    major = _ffmpeg_major()
    if major is not None and major < 7:
        return ["-filter_complex_script", path]
    return ["-/filter_complex", path]


def ffprobe(path: Path | str, entry: str, stream: str | None = None) -> str:
    """One `-show_entries` value, e.g. ffprobe(src, "format=duration")."""
    select = ["-select_streams", stream] if stream else []
    out = _run(["ffprobe", "-v", "error", *select, "-show_entries", entry,
                "-of", "default=nw=1:nk=1", str(path)])
    return out.stdout.strip()


def _run(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        raise FFmpegError(f"{args[0]} is not installed or not on PATH") from None
    except subprocess.CalledProcessError as e:
        raise FFmpegError(f"{args[0]} failed:\n{e.stderr}") from None


def find_source(video: str) -> Path:
    """Locate media/<video>.<ext>, whatever the container extension is."""
    for ext in VIDEO_EXTS:
        p = MEDIA / f"{video}{ext}"
        if p.exists():
            return p
    raise FileNotFoundError(
        f"no source video for {video!r} in {MEDIA}/ "
        f"(looked for {', '.join(VIDEO_EXTS)})"
    )


def media_duration(video: str) -> float | None:
    """Length of the source in seconds, or None if it cannot be read.

    Used to stop the final cue running past the end of the picture, and by QC
    to catch the same thing.
    """
    try:
        src = find_source(video)
    except FileNotFoundError:
        return None
    try:
        return float(ffprobe(src, "format=duration"))
    except Exception:
        return None


# --------------------------------------------------------------------------
# naming
# --------------------------------------------------------------------------

# Transliterations applied before stripping to ASCII. NFKD alone drops the
# diacritic, giving `muller` for Müller where German expects `mueller`. Packs
# extend this for their own language.
BASE_TRANSLIT = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "æ": "ae", "ø": "oe", "å": "aa",
    "Æ": "Ae", "Ø": "Oe", "Å": "Aa",
}


def slug(name: str, translit: dict[str, str] | None = None) -> str:
    """Filesystem- and URL-safe identifier for a generated artifact."""
    s = name
    for a, b in (translit or BASE_TRANSLIT).items():
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "video"


# --------------------------------------------------------------------------
# transcript schema
# --------------------------------------------------------------------------

@dataclass
class Word:
    w: str
    start: float
    end: float
    # Confidence as a probability in 0..1, never a log-probability. Engines
    # report both conventions and they are easy to confuse, because a log
    # probability near zero means near-certain while a probability near zero
    # means the opposite. An engine reporting logprobs must convert here.
    prob: float | None = None


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    speaker: str | None = None
    avg_logprob: float | None = None
    no_speech_prob: float | None = None


@dataclass
class Transcript:
    """What every speech-to-text engine normalises into.

    Adding an engine means emitting this shape; nothing downstream needs to
    know which engine produced it.
    """

    video: str
    engine: str
    language: str
    segments: list[Segment] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        segs = [
            Segment(**{**s, "words": [Word(**w) for w in s.get("words", [])]})
            for s in raw["segments"]
        ]
        return cls(raw["video"], raw["engine"], raw["language"], segs)

    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    def full_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()


def respace(words: list[str], start: float, end: float) -> list[Word]:
    """Spread words across [start, end], weighted by length.

    Length is a crude proxy for duration but a monotonic one, which is all that
    is asked of it: the cue splitter only ever wants to know which words fall
    in the first N% of a sentence.
    """
    if not words:
        return []
    weights = [max(len(w), 1) for w in words]
    total = sum(weights)
    span = max(end - start, 0.001)
    out, t = [], start
    for w, weight in zip(words, weights):
        duration = span * weight / total
        out.append(Word(w=w, start=round(t, 3), end=round(t + duration, 3)))
        t += duration
    return out


def set_text(seg: Segment, text: str) -> bool:
    """Replace a segment's text and keep its word list consistent with it.

    Returns True if anything changed.

    Every step that accepts corrected text goes through this. Setting `text`
    alone leaves the segment's words holding the engine's original wording, and
    since everything downstream reads the words rather than the text, the
    correction reaches the review document and nothing else. That failure is
    invisible: the review page shows the corrected text because it reads the
    markdown, while the subtitles are built from what the engine said.

    Segment start and end are never touched, so cue timing stays anchored to
    the audio however heavily the text was rewritten.
    """
    text = text.strip()
    if not text or text == seg.text:
        return False
    new_words = text.split()
    if len(new_words) == len(seg.words):
        # Same word count: almost certainly a one-for-one substitution, so the
        # measured timings are still right and are worth more than an estimate.
        for w, nw in zip(seg.words, new_words):
            w.w = nw
    else:
        seg.words = respace(new_words, seg.start, seg.end)
    seg.text = text
    return True


# --------------------------------------------------------------------------
# timestamps
# --------------------------------------------------------------------------

def ts(seconds: float, sep: str = ",") -> str:
    """Seconds to an SRT timestamp. `sep='.'` gives the VTT and ASS style."""
    ms = int(round(max(seconds, 0.0) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def parse_ts(text: str) -> float:
    h, m, rest = text.strip().split(":")
    s, ms = re.split(r"[.,]", rest)
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


# --------------------------------------------------------------------------
# markdown tables as human-editable data
# --------------------------------------------------------------------------

def read_table(
    path: Path, header: str, keys: tuple[str, ...]
) -> tuple[list[dict], list[str], list[str]]:
    """Split a project markdown file into (rows, preamble, postamble).

    Only the contiguous table directly under `header` is data. Everything
    around it is prose the human wrote, handed back untouched so that
    rewriting the table can never eat their notes.
    """
    if not path.exists():
        return [], [], []
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == header)
    except StopIteration:
        return [], lines, []

    i = start + 1
    if i < len(lines) and lines[i].strip().startswith("|---"):
        i += 1

    rows = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        cells = (cells + [""] * len(keys))[: len(keys)]
        if cells[0]:
            rows.append(dict(zip(keys, cells)))
        i += 1
    return rows, lines[:start], lines[i:]


def write_table(
    path: Path, header: str, keys: tuple[str, ...],
    rows: list[dict], preamble: list[str], postamble: list[str],
) -> None:
    """Inverse of read_table(). A pipe in cell text would break the table."""
    body = [header, "|" + "---|" * len(keys)]
    for r in rows:
        body.append("| " + " | ".join(
            str(r.get(k, "")).replace("|", r"\|").replace("\n", " ").strip()
            for k in keys
        ) + " |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(preamble + body + postamble).rstrip() + "\n", encoding="utf-8"
    )
