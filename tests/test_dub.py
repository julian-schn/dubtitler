"""Dubbing: the fit arithmetic, the ducking envelope, and one real mix.

The fit and the envelope are pure functions and are tested as such. They are the
parts that fail silently — a clip that drifts or an original that never ducks
both produce a file that plays perfectly and is wrong — so they get exercised
directly rather than through the output.

The end-to-end case builds its own twelve-second video, so the suite carries no
media of its own. It runs the `rehearsal` voice, so it needs no model, no key
and no network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from dubtitler.steps.dub import (
    Fit, duck_expr, graph, headroom, speed_for, stretch_for,
)

REPO = Path(__file__).resolve().parents[1]

FIT = Fit(tolerance=0.05, speed_max=1.15, atempo_max=1.10,
          max_overrun=1.0, guard=0.15)


# --------------------------------------------------------------------------
# headroom
# --------------------------------------------------------------------------

def test_headroom_is_never_negative():
    """Back-to-back sentences leave no room, not negative room.

    A negative allowance would subtract from the clip's own span, which reads as
    "start earlier" — not something the mixer can offer, and it would silently
    pull every following clip out of sync.
    """
    assert headroom(10.0, 10.0, FIT) == 0.0
    assert headroom(10.0, 10.05, FIT) == 0.0


def test_headroom_stops_short_of_the_next_clip():
    # 2s of silence, minus the 0.15s guard.
    assert headroom(10.0, 12.0, FIT) == pytest.approx(1.0)      # capped
    assert headroom(10.0, 10.5, FIT) == pytest.approx(0.35)


def test_the_last_sentence_gets_the_full_overrun():
    assert headroom(10.0, None, FIT) == FIT.max_overrun


# --------------------------------------------------------------------------
# levers
# --------------------------------------------------------------------------

def test_a_clip_that_already_fits_is_left_alone():
    assert speed_for(4.0, 4.0, FIT) == 1.0
    assert stretch_for(4.0, 4.0, 1.0, FIT) == (1.0, 0.0, True)


def test_overshoot_inside_the_tolerance_is_left_alone():
    """4.15s in a 4s slot is 3.75% over, under the 5% tolerance.

    Stretching this would cost audio quality to fix something inaudible.
    """
    assert speed_for(4.15, 4.0, FIT) == 1.0
    assert stretch_for(4.15, 4.0, 1.0, FIT)[0] == 1.0


def test_speed_is_capped_at_speed_max():
    # Would need 2x to fit; the engine is only asked for 1.15.
    assert speed_for(8.0, 4.0, FIT) == pytest.approx(1.15)
    # A mild overshoot asks for exactly what it needs, not the cap.
    assert speed_for(4.4, 4.0, FIT) == pytest.approx(1.1)


def test_atempo_is_capped_and_then_overrun_takes_the_rest():
    atempo, overrun, fits = stretch_for(5.0, 4.0, 1.0, FIT)
    assert atempo == pytest.approx(1.10)            # capped
    assert overrun == pytest.approx(5.0 / 1.10 - 4.0)
    assert fits


def test_a_clip_that_cannot_be_made_to_fit_is_reported():
    """No room and far too long: keep it, flag it, let a human shorten the line.

    The alternative is stretching past atempo_max, which is audible, or cutting
    the clip off mid-word. Both are worse than one line in a summary.
    """
    atempo, overrun, fits = stretch_for(9.0, 4.0, 0.0, FIT)
    assert atempo == pytest.approx(1.10)
    assert overrun == 0.0
    assert not fits


def test_a_zero_length_sentence_does_not_divide_by_zero():
    assert speed_for(2.0, 0.0, FIT) == 1.0
    assert stretch_for(2.0, 0.0, 0.0, FIT) == (1.0, 0.0, True)


# --------------------------------------------------------------------------
# ducking
#
# The expression is valid Python as well as valid ffmpeg — only t, max, min and
# arithmetic — so these evaluate it rather than matching on its text.
# --------------------------------------------------------------------------

def gain(expr: str, t: float) -> float:
    return eval(expr, {"__builtins__": {}}, {"t": t, "max": max, "min": min})


def test_no_regions_leaves_the_original_alone():
    assert duck_expr([], 0.18, 0.25) == "1"


def test_the_original_is_ducked_only_while_the_dub_speaks():
    expr = duck_expr([(2.0, 4.0)], 0.18, 0.25)
    assert gain(expr, 0.0) == pytest.approx(1.0)      # before
    assert gain(expr, 3.0) == pytest.approx(0.18)     # under the dub
    assert gain(expr, 9.0) == pytest.approx(1.0)      # after


def test_the_duck_ramps_rather_than_steps():
    """A step change in level is audible as a click; a 0.25s ramp is not."""
    expr = duck_expr([(2.0, 4.0)], 0.18, 0.25)
    assert gain(expr, 1.75) == pytest.approx(1.0)     # ramp starts
    mid = gain(expr, 1.875)
    assert 0.18 < mid < 1.0                           # halfway down
    assert gain(expr, 2.0) == pytest.approx(0.18)     # fully ducked


def test_overlapping_regions_duck_once():
    """Two clips that overlap must not stack into a double duck.

    Summing the envelopes would push the original below the configured level and,
    with three overlaps, past silence into a negative gain.
    """
    expr = duck_expr([(2.0, 4.0), (3.0, 5.0)], 0.18, 0.25)
    assert gain(expr, 3.5) == pytest.approx(0.18)
    assert gain(expr, 4.5) == pytest.approx(0.18)     # still inside the second
    assert gain(expr, 6.0) == pytest.approx(1.0)


def test_a_zero_fade_does_not_divide_by_zero():
    expr = duck_expr([(2.0, 4.0)], 0.18, 0.0)
    assert gain(expr, 3.0) == pytest.approx(0.18)
    assert gain(expr, 0.0) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# engine selection
# --------------------------------------------------------------------------

def test_kokoro_refuses_a_language_it_cannot_speak():
    """Kokoro covers eight languages and Norwegian is not one of them.

    The message has to name the way out: whoever hits this is usually starting
    their first job in a new language pair.
    """
    from dubtitler import tts

    problem = tts.get("kokoro").speaks("nb")
    assert problem is not None
    assert "cannot speak 'nb'" in problem
    assert "elevenlabs" in problem and "rehearsal" in problem


def test_kokoro_accepts_a_language_it_does_speak():
    from dubtitler import tts

    assert tts.get("kokoro").speaks("en") is None


def test_rehearsal_and_elevenlabs_speak_anything():
    from dubtitler import tts

    assert tts.get("rehearsal").speaks("nb") is None
    assert tts.get("elevenlabs").speaks("nb") is None


def test_an_unknown_engine_names_the_known_ones():
    from dubtitler import tts

    with pytest.raises(KeyError, match="kokoro"):
        tts.get("nope")


def test_kokoro_models_live_outside_the_job(monkeypatch):
    """340 MB must not be re-fetched for every clone.

    This repo is cloned per job, so resolving the model directory against the
    job root meant a fresh download each time — and the failure looked like a
    missing-file error rather than a design mistake.
    """
    from dubtitler.core import ROOT
    from dubtitler.tts.kokoro import models_dir

    monkeypatch.delenv("DUBTITLER_MODELS", raising=False)
    assert ROOT not in models_dir().parents

    monkeypatch.setenv("DUBTITLER_MODELS", "/somewhere/shared")
    assert models_dir() == Path("/somewhere/shared")


# --------------------------------------------------------------------------
# the graph
# --------------------------------------------------------------------------

def test_the_graph_sums_rather_than_averages():
    """amix defaults to averaging, which drops the original 3 dB per clip added.

    That is the ducking done by accident and in the wrong direction, and it gets
    quieter the more the person talks.
    """
    from dubtitler.steps.dub import Clip

    clips = [Clip(id=0, start=1.0, path=Path("a.wav"), duration=2.0,
                  atempo=1.0, overrun=0.0, fits=True)]
    g = graph(clips, "1", 48000, "I=-16:TP=-1.5:LRA=11")
    assert "normalize=0" in g
    assert "adelay=1000:all=1" in g
    assert "atempo" not in g          # 1.0 adds no filter


# --------------------------------------------------------------------------
# end to end
# --------------------------------------------------------------------------

SENTENCES = [
    {"id": 0, "start": 0.5, "end": 3.5, "speaker": None,
     "source": "Hallo, wie geht es dir heute?",
     "target": "Hei, hvordan har du det i dag?", "words": []},
    {"id": 1, "start": 4.0, "end": 7.0, "speaker": None,
     "source": "Mir geht es gut, danke der Nachfrage.",
     "target": "Jeg har det bra, takk som spor.", "words": []},
    {"id": 2, "start": 8.0, "end": 11.0, "speaker": None,
     "source": "Das freut mich sehr zu hoeren.",
     "target": "Det var veldig hyggelig aa hoere.", "words": []},
]

CONFIG = """
[project]
name = "Dub test"
source_lang = "de"
target_lang = "nb"

[dubbing]
engine = "rehearsal"
"""


@pytest.fixture
def dubjob(tmp_path: Path):
    """A minimal job with a real, tiny video, ready for `steps.dub`."""
    root = tmp_path / "job"
    for sub in ("media", "out", "project", "work/stt"):
        (root / sub).mkdir(parents=True)
    shutil.copytree(REPO / "lang", root / "lang")
    (root / "config.toml").write_text(CONFIG, encoding="utf-8")
    (root / "work" / "stt" / "CLIP.sentences.json").write_text(
        json.dumps(SENTENCES, ensure_ascii=False), encoding="utf-8"
    )

    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=128x128:rate=10:duration=12",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=12",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         str(root / "media" / "CLIP.mp4")],
        check=True, capture_output=True,
    )

    env = {**os.environ, "DUBTITLER_ROOT": str(root),
           "PYTHONPATH": str(REPO / "src")}

    def dub(*args: str) -> subprocess.CompletedProcess:
        result = subprocess.run(
            [sys.executable, "-m", "dubtitler.steps.dub", "CLIP", *args],
            env=env, capture_output=True, text=True, cwd=REPO,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"steps.dub exited {result.returncode}\n"
                f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
            )
        return result

    return type("DubJob", (), {"root": root, "dub": staticmethod(dub)})


def test_rehearsal_produces_both_deliverables(dubjob):
    dubjob.dub()

    audio = dubjob.root / "out" / "clip.nb.dub.m4a"
    video = dubjob.root / "out" / "clip.nb.dub.mp4"
    assert audio.exists() and audio.stat().st_size > 0
    assert video.exists() and video.stat().st_size > 0

    # One cached clip per sentence, so a re-run costs nothing on a paid engine.
    clips = list((dubjob.root / "work" / "dub" / "CLIP" / "nb").glob("*.wav"))
    assert len(clips) >= len(SENTENCES)


def test_the_dub_keeps_the_video_and_the_full_duration(dubjob):
    """The picture is copied, not re-encoded, and the dub covers the source."""
    from dubtitler.core import ffprobe

    dubjob.dub()
    video = dubjob.root / "out" / "clip.nb.dub.mp4"

    assert float(ffprobe(video, "format=duration")) == pytest.approx(12.0, abs=0.5)
    assert ffprobe(video, "stream=codec_name", stream="v:0") == "h264"
    assert ffprobe(video, "stream=codec_name", stream="a:0") == "aac"


def test_a_second_run_reuses_the_synthesised_clips(dubjob):
    dubjob.dub()
    clip = dubjob.root / "work" / "dub" / "CLIP" / "nb" / "0.wav"
    before = clip.stat().st_mtime_ns

    dubjob.dub()
    assert clip.stat().st_mtime_ns == before

    dubjob.dub("--force")
    assert clip.stat().st_mtime_ns != before
