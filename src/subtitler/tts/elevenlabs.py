"""ElevenLabs text-to-speech.

The paid option, and the only one here that speaks the languages this repository
was built for: `eleven_multilingual_v2` covers Norwegian and German, which
Kokoro does not. `languages` is left empty — the model's coverage is wide enough
that refusing a code up front would be more likely to be wrong than useful.

No usable rate control, so `native_speed` is False and the fit chain skips
straight to time-stretching. The API exposes stability and similarity knobs, not
a words-per-minute one, and asking a voice to "speak faster" through prompt-like
settings is not reproducible enough to build timing on.

The key is the same `ELEVENLABS_API_KEY` the Scribe engine in `stt/` uses, so a
job already transcribing with ElevenLabs needs no new credentials.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..core import cfg, ffmpeg
from . import Base

MODEL = "eleven_multilingual_v2"

# Requested as raw PCM rather than mp3: this is an intermediate that gets
# time-stretched and remixed, and a lossy hop through mp3 before either is
# avoidable cost to the output.
PCM_RATE = 24000
OUTPUT_FORMAT = f"pcm_{PCM_RATE}"

# ElevenLabs has no account-independent default voice, so a job that has not
# chosen one gets the oldest stable public voice rather than an error. Set
# `voice` in config.toml to anything else.
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"


class ElevenLabsVoice(Base):
    name = "elevenlabs"
    package = "elevenlabs"
    env_key = "ELEVENLABS_API_KEY"
    native_speed = False
    paid = True

    def synth(self, text: str, lang: str, out: Path, speed: float = 1.0) -> None:
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=self.key())
        stream = client.text_to_speech.convert(
            voice_id=cfg("dubbing", "voice") or DEFAULT_VOICE,
            model_id=cfg("dubbing", "voice_model") or MODEL,
            text=text,
            output_format=OUTPUT_FORMAT,
        )

        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", suffix=".pcm", delete=False) as fh:
            for chunk in stream:
                fh.write(chunk)
            raw = fh.name
        try:
            ffmpeg("-f", "s16le", "-ar", str(PCM_RATE), "-ac", "1",
                   "-i", raw, "-c:a", "pcm_s16le", str(out))
        finally:
            Path(raw).unlink(missing_ok=True)


ENGINE = ElevenLabsVoice
