"""Speech-to-text engines as interchangeable plugins.

Every engine normalises into `core.Transcript`, so nothing downstream knows or
cares which one produced a transcript. Adding an engine means writing one module
here and listing it in `REGISTRY`.

Engines are imported lazily. Each one has its own heavy or paid dependency, and
a job that uses two of them should not need the other four installed.

Why more than one at a time: a single engine cannot tell you where it is wrong.
Two engines disagreeing marks the spots worth a human's attention, and three or
more turn that into a vote, where the odd one out is usually the error.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Protocol

from ..core import Segment, Transcript, Word

# name in config.toml -> module in this package
REGISTRY: dict[str, str] = {
    "whisper-local": "whisper_local",
    "elevenlabs": "elevenlabs",
    "openai": "openai_whisper",
    "deepgram": "deepgram",
    "assemblyai": "assemblyai",
}


class Engine(Protocol):
    """What a speech-to-text plugin has to provide."""

    name: str
    audio: str  # which file `steps.audio` prepared: "wav" or "flac"

    def available(self) -> str | None:
        """None if the engine can run, else a sentence saying what is missing.

        Returning a reason rather than raising lets the pipeline report every
        problem at once, before spending money or GPU time on the engines that
        do work.
        """

    def transcribe(self, audio: Path, language: str) -> Transcript:
        """Transcribe one audio file. `language` is a two-letter pack code."""


class Base:
    """Shared defaults. Plugins subclass this and override what differs."""

    name = "unnamed"
    audio = "wav"
    package = ""        # import name of the SDK, checked by available()
    env_key = ""        # environment variable holding the API key, if any
    default_model = ""  # the engine's own model id, overridable from config

    def model(self) -> str:
        """The model id to run: config's `[models]` entry, else the default.

        Engines pin a known-good model rather than tracking whichever one a
        provider currently calls "latest": a job re-run months later should
        produce the same transcript. The config key exists so upgrading is a
        deliberate edit rather than a code change.
        """
        from ..core import config

        return config()["models"].get(self.name) or self.default_model

    def key(self) -> str | None:
        import os

        from dotenv import load_dotenv

        from ..core import ROOT

        load_dotenv(ROOT / ".env")
        return os.environ.get(self.env_key) or None

    def available(self) -> str | None:
        if self.package:
            try:
                __import__(self.package)
            except ImportError:
                return f"{self.package} is not installed (uv sync --extra {self.name})"
        if self.env_key and not self.key():
            return f"{self.env_key} is not set (add it to .env)"
        return None

    def __repr__(self) -> str:
        return f"<engine {self.name}>"


# --------------------------------------------------------------------------
# shared: flat word stream -> segments
# --------------------------------------------------------------------------

SENTENCE_END = ".?!…"
PAUSE_SPLIT = 1.2   # seconds of silence that force a break
MAX_SEGMENT = 12.0  # hard ceiling, so nothing becomes unreviewable


def group_words(words: list[tuple[Word, str | None]]) -> list[Segment]:
    """Turn a flat (word, speaker) stream into reviewable segments.

    Most cloud engines return words with no segmentation at all. Breaking at
    sentence-final punctuation gives the most natural units; the pause and
    length limits are fallbacks for a stretch of speech the engine punctuated
    poorly or not at all.
    """
    segments: list[Segment] = []
    buf: list[Word] = []
    speaker: str | None = None
    prev_end: float | None = None

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        segments.append(
            Segment(
                id=len(segments),
                start=buf[0].start,
                end=buf[-1].end,
                text=" ".join(w.w for w in buf).strip(),
                words=list(buf),
                speaker=speaker,
            )
        )
        buf = []

    for word, spk in words:
        gap = (word.start - prev_end) if prev_end is not None else 0.0
        span = (word.end - buf[0].start) if buf else 0.0
        if buf and (spk != speaker or gap >= PAUSE_SPLIT or span >= MAX_SEGMENT):
            flush()
        if not buf:
            speaker = spk
        buf.append(word)
        prev_end = word.end
        if word.w and word.w[-1] in SENTENCE_END:
            flush()
    flush()
    return segments


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def get(name: str) -> Engine:
    """Instantiate one engine by its config name."""
    if name not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        raise KeyError(f"unknown stt engine {name!r} (known: {known})")
    module = importlib.import_module(f".{REGISTRY[name]}", __package__)
    return module.ENGINE()


def get_all(names: list[str]) -> list[Engine]:
    return [get(n) for n in names]
