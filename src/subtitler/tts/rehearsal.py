"""A placeholder voice: no model, no key, no network, no cost.

Exists so the whole dubbing chain — synthesis, fitting, ducking, muxing — can be
run end to end before anything is spent on a real voice, and so the tests can
cover it without downloading a model or holding an API key.

It emits a tone of roughly the length the sentence would take to say, derived
from the pack's speaking rate. That is enough to check the thing everyone gets
wrong first: whether clips land where the speech lands, and whether the ducking
follows them.
"""

from __future__ import annotations

from pathlib import Path

from ..core import cfg, ffmpeg
from ..langpack import load as load_pack
from . import Base

# Pitched low and wobbled, so a rehearsal mix is audibly a rehearsal. A steady
# sine sits close enough to a test tone that people have mistaken the output for
# a broken render rather than a deliberate placeholder.
FREQ = 220
TREMOLO = "tremolo=f=5:d=0.7"

MIN_DURATION = 0.2


class Rehearsal(Base):
    name = "rehearsal"
    native_speed = True     # nothing is synthesised, so any length is free
    paid = False

    def synth(self, text: str, lang: str, out: Path, speed: float = 1.0) -> None:
        rate = load_pack(lang).speaking_rate
        seconds = max(MIN_DURATION, len(text) / rate / max(speed, 0.01))
        sample_rate = cfg("dubbing", "sample_rate")
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg(
            "-f", "lavfi",
            "-i", f"sine=frequency={FREQ}:duration={seconds:.3f}:sample_rate={sample_rate}",
            "-af", TREMOLO,
            "-c:a", "pcm_s16le", str(out),
        )


ENGINE = Rehearsal
