"""A subtitling pipeline: speech to text, human review, translation, cues.

Clone this repository per job, point `config.toml` at a language pair, drop the
media in `media/`, and run the steps in order. Every stage writes a file a human
can open and correct, and no stage overwrites a correction it did not make.
"""

__version__ = "0.1.0"
