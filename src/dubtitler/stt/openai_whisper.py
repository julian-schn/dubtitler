"""Whisper large-v2 through OpenAI's hosted API.

The same model family as the local engine, so it is a poor third opinion:
its errors correlate with `whisper-local`'s, and a consensus vote is only worth
anything when the voters fail independently. Use it when running Whisper
locally is impractical, not as a way to get from two engines to three.
"""

from __future__ import annotations

from pathlib import Path

from ..core import Segment, Transcript, Word
from . import Base

class OpenAIWhisper(Base):
    name = "openai"
    audio = "flac"
    package = "openai"
    env_key = "OPENAI_API_KEY"
    default_model = "whisper-1"

    def transcribe(self, audio: Path, language: str) -> Transcript:
        from openai import OpenAI

        client = OpenAI(api_key=self.key())
        with audio.open("rb") as fh:
            result = client.audio.transcriptions.create(
                model=self.model(),
                file=fh,
                language=language,
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )

        words = [
            Word(w=w.word.strip(), start=float(w.start), end=float(w.end))
            for w in (getattr(result, "words", None) or [])
        ]

        segments = []
        for i, s in enumerate(getattr(result, "segments", None) or []):
            start, end = float(s.start), float(s.end)
            segments.append(
                Segment(
                    id=i,
                    start=start,
                    end=end,
                    text=s.text.strip(),
                    # The API returns words and segments as two flat lists with
                    # no link between them, so words are matched back to their
                    # segment by time.
                    words=[w for w in words if w.start < end and w.end > start],
                    avg_logprob=float(getattr(s, "avg_logprob", 0.0) or 0.0),
                    no_speech_prob=float(getattr(s, "no_speech_prob", 0.0) or 0.0),
                )
            )

        return Transcript(
            video=audio.stem, engine=self.name, language=language, segments=segments
        )


ENGINE = OpenAIWhisper
