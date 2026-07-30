"""Shared foundations: config, the transcript schema, slugs, and table files.

Everything language-specific lives in a language pack (see `langpack.py`), not
here. This module should never need to know which languages a job uses.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
import unicodedata
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
        "llm": {"provider": "auto", "model": "claude-opus-5"},
        "subtitles": {
            "max_line": 42, "max_lines": 2, "max_cps": 17.0,
            "min_duration": 0.833, "max_duration": 7.0,
            "lead_out": 1.5, "gap_frames": 2,
        },
        "render": {
            "font": "Arial", "font_size": 22, "box_opacity": 0.85,
            "margin": 40, "crf": 18,
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
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(src)],
            capture_output=True, text=True, check=True,
        )
        return float(out.stdout.strip())
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
