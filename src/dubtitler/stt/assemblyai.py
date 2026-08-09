"""AssemblyAI Universal.

Another independent architecture, with per-word confidence. Its language
coverage is narrower than Whisper's, so `available()` passing does not
guarantee the job's language is supported; a rejected language surfaces as an
error from the API rather than as silent nonsense.
"""

from __future__ import annotations

from pathlib import Path

from ..core import Transcript, Word
from . import Base, group_words


class AssemblyAI(Base):
    name = "assemblyai"
    audio = "flac"
    package = "assemblyai"
    env_key = "ASSEMBLYAI_API_KEY"

    def transcribe(self, audio: Path, language: str) -> Transcript:
        import assemblyai as aai

        aai.settings.api_key = self.key()
        config = aai.TranscriptionConfig(
            language_code=language,
            punctuate=True,
            speaker_labels=True,
        )
        result = aai.Transcriber(config=config).transcribe(str(audio))
        if result.status == "error":
            raise RuntimeError(f"assemblyai: {result.error}")

        stream = [
            (
                Word(
                    w=w.text.strip(),
                    # AssemblyAI reports milliseconds; everything else here is
                    # seconds.
                    start=w.start / 1000.0,
                    end=w.end / 1000.0,
                    prob=float(w.confidence),
                ),
                getattr(w, "speaker", None),
            )
            for w in (result.words or [])
        ]

        return Transcript(
            video=audio.stem,
            engine=self.name,
            language=language,
            segments=group_words(stream),
        )


ENGINE = AssemblyAI
