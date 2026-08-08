"""Kokoro, an 82M-parameter model that runs locally on CPU.

The default voice, because a dub costs nothing to re-run and no audio leaves the
machine — which matters for testimony, where the recording is often the reason
the job is confidential in the first place.

It speaks eight languages, and neither German nor Norwegian is among them. That
is not a gap to work around: this repository's own reference job is de -> nb and
therefore cannot use this engine at all. `Base.speaks()` refuses up front and
names ElevenLabs, which does cover them.

The model is two files rather than a pip dependency, so `available()` checks for
them and prints the two commands that fetch them.
"""

from __future__ import annotations

from pathlib import Path

from ..core import ROOT, cfg
from . import Base

MODELS = ROOT / "models"
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

# Pack code -> the language string kokoro-onnx expects. The two are close but
# not identical, and passing a pack code straight through yields silence rather
# than an error.
LANGS = {
    "en": "en-us",
    "es": "es",
    "fr": "fr-fr",
    "hi": "hi",
    "it": "it",
    "ja": "ja",
    "pt": "pt-br",
    "zh": "cmn",
}

# One default voice per language. A kokoro voice name encodes its language in
# the first letter, so these are not interchangeable: an English voice asked to
# read Italian produces confident nonsense rather than an error.
VOICES = {
    "en": "af_heart",
    "es": "ef_dora",
    "fr": "ff_siwis",
    "hi": "hf_alpha",
    "it": "if_sara",
    "ja": "jf_alpha",
    "pt": "pf_dora",
    "zh": "zf_xiaobei",
}


class Kokoro(Base):
    name = "kokoro"
    package = "kokoro_onnx"
    native_speed = True     # create() takes a speed multiplier directly
    paid = False
    languages = set(LANGS)

    def _paths(self) -> tuple[Path, Path]:
        """Where the two model files live. `voice_model` overrides the onnx."""
        override = cfg("dubbing", "voice_model")
        model = Path(override) if override else MODELS / MODEL_FILE
        return model, model.parent / VOICES_FILE

    def available(self) -> str | None:
        if missing := super().available():
            return missing
        model, voices = self._paths()
        absent = [p for p in (model, voices) if not p.exists()]
        if absent:
            names = ", ".join(p.name for p in absent)
            return (
                f"kokoro model files are missing ({names})\n"
                f"  mkdir -p {MODELS.relative_to(ROOT)} && cd {MODELS.relative_to(ROOT)}\n"
                f"  curl -LO {RELEASE}/{MODEL_FILE}\n"
                f"  curl -LO {RELEASE}/{VOICES_FILE}"
            )
        return None

    def _model(self):
        """The loaded model, kept for the life of the run.

        Loading the onnx graph takes a second or two, which is nothing once and
        several minutes across a video's worth of sentences.
        """
        if getattr(self, "_loaded", None) is None:
            from kokoro_onnx import Kokoro as KokoroModel

            model, voices = self._paths()
            self._loaded = KokoroModel(str(model), str(voices))
        return self._loaded

    def synth(self, text: str, lang: str, out: Path, speed: float = 1.0) -> None:
        import soundfile

        samples, sample_rate = self._model().create(
            text,
            voice=cfg("dubbing", "voice") or VOICES[lang],
            speed=speed,
            lang=LANGS[lang],
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        soundfile.write(str(out), samples, sample_rate)


ENGINE = Kokoro
