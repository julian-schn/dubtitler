"""ElevenLabs Scribe.

Word timings are tighter than Whisper's, which makes it the better spine when
both are configured: cue boundaries are derived from word timings, so the
engine with the better timings should own them. It reports no confidence
numbers, so pair it with an engine that does.
"""

from __future__ import annotations

import math
from pathlib import Path

from ..core import Transcript, Word
from ..langpack import load as load_pack
from . import Base, group_words

def _probability(logprob) -> float | None:
    """Scribe reports a log-probability; `Word.prob` is a plain probability.

    Storing the raw number here reads every word as maximally uncertain, since
    log-probabilities are at most zero and the confidence floors are positive.
    That flags the entire transcript, which looks like a tuning problem rather
    than a unit mix-up.
    """
    if logprob is None:
        return None
    return min(1.0, math.exp(float(logprob)))


class ElevenLabsScribe(Base):
    name = "elevenlabs"
    audio = "flac"
    package = "elevenlabs"
    env_key = "ELEVENLABS_API_KEY"
    default_model = "scribe_v2"

    def transcribe(self, audio: Path, language: str) -> Transcript:
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=self.key())
        with audio.open("rb") as fh:
            result = client.speech_to_text.convert(
                file=fh,
                model_id=self.model(),
                language_code=load_pack(language).iso3,
                diarize=True,
                # We want speech, not "(laughter)" markers, which would end up
                # in the subtitles as if they had been spoken.
                tag_audio_events=False,
            )

        stream = []
        for item in result.words:
            # Scribe emits spacing and audio_event entries alongside real words.
            if getattr(item, "type", "word") != "word":
                continue
            text = (item.text or "").strip()
            if not text:
                continue
            logprob = getattr(item, "logprob", None)
            stream.append((
                Word(w=text, start=float(item.start), end=float(item.end),
                     prob=_probability(logprob)),
                getattr(item, "speaker_id", None),
            ))

        return Transcript(
            video=audio.stem,
            engine=self.name,
            language=language,
            segments=group_words(stream),
        )


ENGINE = ElevenLabsScribe
