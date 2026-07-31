"""Whisper large-v3 running locally: MLX on Apple Silicon, faster-whisper elsewhere.

Free, offline, and the only common engine that reports per-word probabilities
and a per-segment mean log-probability. Those numbers are what let a
single-engine job still have a review gate, so this is the sensible default
engine even when a cloud engine is also configured.
"""

from __future__ import annotations

import platform
from pathlib import Path

from ..core import Segment, Transcript, Word
from . import Base

MLX_MODEL = "mlx-community/whisper-large-v3-mlx"
CT2_MODEL = "large-v3"


def _on_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


class WhisperLocal(Base):
    name = "whisper-local"
    audio = "wav"

    def _backend(self) -> str:
        return "mlx" if _on_apple_silicon() else "ct2"

    def available(self) -> str | None:
        module = "mlx_whisper" if self._backend() == "mlx" else "faster_whisper"
        try:
            __import__(module)
        except ImportError:
            return f"{module} is not installed (uv sync --extra whisper-local)"
        return None

    def transcribe(self, audio: Path, language: str) -> Transcript:
        if self._backend() == "mlx":
            segments = self._mlx(audio, language)
        else:
            segments = self._ct2(audio, language)
        return Transcript(
            video=audio.stem, engine=self.name, language=language, segments=segments
        )

    # `condition_on_previous_text` is off in both backends. With it on, one bad
    # segment poisons every following segment through the prompt, and the
    # resulting repetition loops are much harder to spot in a cross-engine diff
    # than isolated errors are.

    def _mlx(self, audio: Path, language: str) -> list[Segment]:
        import mlx_whisper

        result = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=MLX_MODEL,
            language=language,
            word_timestamps=True,
            condition_on_previous_text=False,
            verbose=False,
        )
        return [
            Segment(
                id=i,
                start=float(s["start"]),
                end=float(s["end"]),
                text=s["text"].strip(),
                words=[
                    Word(
                        w=w["word"].strip(),
                        start=float(w["start"]),
                        end=float(w["end"]),
                        prob=float(w.get("probability", 0.0)),
                    )
                    for w in s.get("words", [])
                ],
                avg_logprob=float(s.get("avg_logprob", 0.0)),
                no_speech_prob=float(s.get("no_speech_prob", 0.0)),
            )
            for i, s in enumerate(result["segments"])
        ]

    def _ct2(self, audio: Path, language: str) -> list[Segment]:
        from faster_whisper import WhisperModel

        model = WhisperModel(CT2_MODEL, compute_type="auto")
        stream, _ = model.transcribe(
            str(audio),
            language=language,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        return [
            Segment(
                id=i,
                start=float(s.start),
                end=float(s.end),
                text=s.text.strip(),
                words=[
                    Word(
                        w=w.word.strip(),
                        start=float(w.start),
                        end=float(w.end),
                        prob=float(w.probability),
                    )
                    for w in (s.words or [])
                ],
                avg_logprob=float(s.avg_logprob),
                no_speech_prob=float(s.no_speech_prob),
            )
            for i, s in enumerate(stream)
        ]


ENGINE = WhisperLocal
