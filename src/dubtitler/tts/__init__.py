"""Text-to-speech voices as interchangeable plugins.

The same shape as `stt/`: one module per engine, named in `REGISTRY`, imported
lazily so that a job using one voice does not need the others installed.

One thing differs from a speech-to-text engine, and it drives the design here.
A transcription engine will attempt any language you hand it. A voice will not:
each model speaks a fixed set, and the local one speaks eight. So `speaks()`
answers that before anything is synthesised, in the same "return a reason rather
than raise" style as `available()`, because the alternative is discovering it
after half a video has been rendered or paid for.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Protocol

# name in config.toml -> module in this package
REGISTRY: dict[str, str] = {
    "kokoro": "kokoro",
    "elevenlabs": "elevenlabs",
    "rehearsal": "rehearsal",
}


class Voice(Protocol):
    """What a text-to-speech plugin has to provide."""

    name: str
    native_speed: bool   # whether synth() honours `speed`
    paid: bool           # whether a run costs money, which gates the spend check

    def available(self) -> str | None:
        """None if the engine can run, else a sentence saying what is missing."""

    def speaks(self, lang: str) -> str | None:
        """None if the engine can speak `lang`, else a sentence saying it cannot."""

    def synth(self, text: str, lang: str, out: Path, speed: float = 1.0) -> None:
        """Write one WAV of `text`.

        `speed` is the fit chain's second lever. Engines with no rate control
        declare `native_speed = False` and ignore it; the caller then falls
        through to time-stretching, which is lever three.
        """


class Base:
    """Shared defaults. Plugins subclass this and override what differs."""

    name = "unnamed"
    package = ""            # import name of the SDK, checked by available()
    env_key = ""            # environment variable holding the API key, if any
    native_speed = False
    paid = False
    # Empty means "any language". A voice that speaks a fixed set lists it in
    # the same two-letter codes config.toml and lang/ use.
    languages: set[str] = set()

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

    def speaks(self, lang: str) -> str | None:
        """Whether this voice covers `lang`, and what to do about it if not.

        The message names the way out, because the person who hits this is
        usually starting their first job in a new language pair and has no
        reason to know which engines cover what.
        """
        if not self.languages or lang in self.languages:
            return None
        supported = ", ".join(sorted(self.languages))
        return (
            f"{self.name} cannot speak {lang!r}\n"
            f"  supported: {supported}\n"
            f'  for {lang} use engine = "elevenlabs" (paid)\n'
            f'  or engine = "rehearsal" (free placeholder)'
        )

    def __repr__(self) -> str:
        return f"<voice {self.name}>"


def get(name: str) -> Voice:
    """Instantiate one voice by its config name."""
    if name not in REGISTRY:
        known = ", ".join(sorted(REGISTRY))
        raise KeyError(f"unknown tts engine {name!r} (known: {known})")
    module = importlib.import_module(f".{REGISTRY[name]}", __package__)
    return module.ENGINE()
