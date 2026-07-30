"""Deepgram Nova.

Architecturally unrelated to Whisper, so its mistakes are different mistakes,
which is exactly what a consensus vote needs. It reports a per-word confidence,
so it can also drive the confidence flags on its own.
"""

from __future__ import annotations

from pathlib import Path

from ..core import Transcript, Word
from . import Base, group_words

MODEL = "nova-3"


class Deepgram(Base):
    name = "deepgram"
    audio = "flac"
    package = "deepgram"
    env_key = "DEEPGRAM_API_KEY"

    def transcribe(self, audio: Path, language: str) -> Transcript:
        from deepgram import DeepgramClient, FileSource, PrerecordedOptions

        client = DeepgramClient(self.key())
        source: FileSource = {"buffer": audio.read_bytes()}
        options = PrerecordedOptions(
            model=MODEL,
            language=language,
            punctuate=True,
            diarize=True,
            smart_format=True,
        )
        result = client.listen.rest.v("1").transcribe_file(source, options)

        alt = result["results"]["channels"][0]["alternatives"][0]
        stream = [
            (
                Word(
                    # `punctuated_word` is the display form; `word` is stripped
                    # bare, and segment breaks depend on the punctuation.
                    w=(w.get("punctuated_word") or w["word"]).strip(),
                    start=float(w["start"]),
                    end=float(w["end"]),
                    prob=float(w.get("confidence", 0.0)),
                ),
                str(w["speaker"]) if w.get("speaker") is not None else None,
            )
            for w in alt.get("words", [])
        ]

        return Transcript(
            video=audio.stem,
            engine=self.name,
            language=language,
            segments=group_words(stream),
        )


ENGINE = Deepgram
