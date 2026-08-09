"""The two LLM paths, and the parsing that has to survive either.

The premise of having two paths is that they are interchangeable. If the agent
path saw a different briefing from the API path, a job's quality would depend on
whether a key happened to be set, which is the opposite of the point.
"""

import json

import pytest

from dubtitler import llm
from dubtitler.llm.agent import AgentLLM


# ---------------------------------------------------------------- parsing

@pytest.mark.parametrize("raw", [
    '{"0": "a", "1": "b"}',
    '```json\n{"0": "a", "1": "b"}\n```',
    '```\n{"0": "a", "1": "b"}\n```',
    'Here you go:\n{"0": "a", "1": "b"}',
    '{"0": "a", "1": "b"}\n\nLet me know if you need changes.',
])
def test_json_survives_the_wrappings_models_add(raw):
    assert llm.parse_json(raw) == {"0": "a", "1": "b"}


def test_unparseable_reply_still_raises():
    with pytest.raises(json.JSONDecodeError):
        llm.parse_json("no object here at all")


def test_translations_containing_braces_are_not_truncated():
    """The recovery path looks for the outermost braces, so a nested object
    must not be cut short by an inner one."""
    assert llm.parse_json('{"0": "a", "1": {"x": "y"}}') == {"0": "a", "1": {"x": "y"}}


# ---------------------------------------------------------------- agent path

def test_agent_writes_the_prompt_and_stops(tmp_path, monkeypatch):
    monkeypatch.setattr("dubtitler.core.WORK", tmp_path)
    monkeypatch.setattr("dubtitler.llm.agent.WORK", tmp_path)

    engine = AgentLLM()
    with pytest.raises(llm.NeedsAgent) as excinfo:
        engine.complete("job.translate", "BRIEFING TEXT")

    prompt_path = excinfo.value.prompt
    assert prompt_path.read_text() == "BRIEFING TEXT"
    assert not excinfo.value.answer.exists()


def test_agent_resumes_once_the_answer_exists(tmp_path, monkeypatch):
    monkeypatch.setattr("dubtitler.llm.agent.WORK", tmp_path)
    engine = AgentLLM()

    with pytest.raises(llm.NeedsAgent) as excinfo:
        engine.complete("job.translate", "BRIEFING TEXT")
    excinfo.value.answer.write_text('{"0": "oversatt"}')

    assert engine.complete("job.translate", "BRIEFING TEXT") == '{"0": "oversatt"}'


def test_an_empty_answer_file_does_not_count_as_answered(tmp_path, monkeypatch):
    """Otherwise a stray touch of the file silently skips the work."""
    monkeypatch.setattr("dubtitler.llm.agent.WORK", tmp_path)
    engine = AgentLLM()
    prompt_path, answer_path = engine.paths("job.translate")
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text("   \n")

    with pytest.raises(llm.NeedsAgent):
        engine.complete("job.translate", "BRIEFING")


def test_the_prompt_is_refreshed_so_an_answer_never_pairs_with_a_stale_brief(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("dubtitler.llm.agent.WORK", tmp_path)
    engine = AgentLLM()

    with pytest.raises(llm.NeedsAgent):
        engine.complete("k", "FIRST VERSION")
    with pytest.raises(llm.NeedsAgent):
        engine.complete("k", "SECOND VERSION")

    assert engine.paths("k")[0].read_text() == "SECOND VERSION"


# ---------------------------------------------------------------- selection

def test_auto_picks_the_agent_when_there_is_no_key(monkeypatch):
    monkeypatch.setattr("dubtitler.llm.has_api_key", lambda: False)
    assert llm.get("auto").name == "agent"


def test_auto_picks_the_api_when_a_key_is_present(monkeypatch):
    monkeypatch.setattr("dubtitler.llm.has_api_key", lambda: True)
    assert llm.get("auto").name == "anthropic"


def test_an_unknown_provider_is_an_error_not_a_silent_default():
    with pytest.raises(ValueError):
        llm.get("gpt")
