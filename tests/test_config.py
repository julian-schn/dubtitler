"""The config surfaces that steps read, and the one-level merge they rely on.

These exist because the merge in `core.config()` is exactly one level deep. A
section written in config.toml replaces the defaults for that section wholesale
rather than merging into them, which is fine for flat sections and silently
destructive for nested ones. That is documented in CLAUDE.md; this is the test
that keeps it true.
"""

from __future__ import annotations

import pytest

from dubtitler import core, stt


@pytest.fixture
def job_config(tmp_path, monkeypatch):
    """Point core at a temp root and write it a config.toml."""

    def write(text: str):
        (tmp_path / "config.toml").write_text(text, encoding="utf-8")
        monkeypatch.setattr(core, "ROOT", tmp_path)
        core.config.cache_clear()
        return core.config()

    yield write
    core.config.cache_clear()


# --------------------------------------------------------------------------
# defaults
# --------------------------------------------------------------------------

def test_a_missing_section_still_has_its_defaults(job_config):
    """A job that never heard of [flags] must still flag things."""
    c = job_config("[project]\nname = 'x'\n")
    assert c["flags"]["word_prob_floor"] == 0.5
    assert c["flags"]["weak_word_cluster"] == 2
    assert c["audio"]["loudnorm"]
    assert c["models"] == {}


def test_partial_sections_keep_the_defaults_they_omit(job_config):
    """Writing one key of a flat section must not blank the rest.

    This is what the one-level merge buys, and the reason every section that
    steps read is flat.
    """
    c = job_config("[flags]\nword_prob_floor = 0.7\n")
    assert c["flags"]["word_prob_floor"] == 0.7
    assert c["flags"]["word_prob_alarm"] == 0.35      # untouched
    assert c["flags"]["avg_logprob_floor"] == -0.6


# --------------------------------------------------------------------------
# [models]
# --------------------------------------------------------------------------

def test_engines_fall_back_to_their_own_model(job_config):
    job_config("[project]\nname = 'x'\n")
    assert stt.get("elevenlabs").model() == "scribe_v2"
    assert stt.get("deepgram").model() == "nova-3"
    assert stt.get("openai").model() == "whisper-1"


def test_a_pinned_model_overrides_the_default(job_config):
    """Pinning is the point: a job re-run months later should not silently
    follow whatever a provider has started calling "latest"."""
    job_config('[models]\nelevenlabs = "scribe_v1"\n')
    assert stt.get("elevenlabs").model() == "scribe_v1"
    assert stt.get("deepgram").model() == "nova-3"     # others unaffected


def test_an_empty_pin_means_the_engine_default(job_config):
    """An empty string is how config.toml spells "I have not chosen"."""
    job_config('[models]\nelevenlabs = ""\n')
    assert stt.get("elevenlabs").model() == "scribe_v2"


def test_whisper_local_pins_per_backend(job_config):
    """The two local runtimes name models differently, so the default depends
    on the platform while an explicit pin does not."""
    job_config('[models]\nwhisper-local = "medium"\n')
    assert stt.get("whisper-local").model() == "medium"

    job_config("[project]\nname = 'x'\n")
    assert stt.get("whisper-local").model() in (
        "mlx-community/whisper-large-v3-mlx", "large-v3",
    )
