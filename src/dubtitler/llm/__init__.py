"""One interface for the steps that need a model, with two ways to satisfy it.

`AnthropicLLM` calls the API, so a whole job can run unattended.

`AgentLLM` writes the prompt to a file and stops, for when there is no API key.
A coding agent (or a person, or a chat window) answers it, drops the answer next
to the prompt, and the same command run again picks up where it left off.

The prompt text is built by the step and is byte-identical down both paths.
That is the point: a job translated through the API and a job translated by an
agent must not quietly differ in quality because the briefing differed.

Choosing between them is `llm.provider` in config.toml. On "auto" the presence
of ANTHROPIC_API_KEY decides.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

from ..core import cfg


class NeedsAgent(Exception):
    """Raised by AgentLLM: a prompt is waiting for an answer.

    Carries the paths rather than a message so the caller can print an
    instruction that fits the step it came from.
    """

    def __init__(self, prompt: Path, answer: Path):
        self.prompt = prompt
        self.answer = answer
        super().__init__(f"answer needed: {prompt}")


class LLM(Protocol):
    name: str

    def complete(self, key: str, prompt: str, max_tokens: int = 8000) -> str:
        """Return the model's reply to `prompt`.

        `key` names this particular request, e.g. "img-2891.translate". It has
        to be stable across runs, because the agent path uses it to pair an
        answer file with the prompt that asked for it.
        """


# --------------------------------------------------------------------------
# parsing replies
# --------------------------------------------------------------------------

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


def parse_json(text: str):
    """Parse a JSON reply, tolerating a markdown fence around it.

    Models wrap JSON in a fence often enough that failing on it would mean
    losing a paid response to a formatting habit.
    """
    cleaned = FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # A model that adds a sentence before or after the object is still
        # giving us the object; find it rather than discard the whole reply.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(cleaned[start: end + 1])


# --------------------------------------------------------------------------
# choosing an implementation
# --------------------------------------------------------------------------

def has_api_key() -> bool:
    import os

    from dotenv import load_dotenv

    from ..core import ROOT

    load_dotenv(ROOT / ".env")
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def get(provider: str | None = None) -> LLM:
    choice = provider or cfg("llm", "provider")
    if choice == "auto":
        choice = "anthropic" if has_api_key() else "agent"

    if choice == "anthropic":
        from .anthropic_api import AnthropicLLM

        return AnthropicLLM()
    if choice == "agent":
        from .agent import AgentLLM

        return AgentLLM()
    raise ValueError(f"unknown llm provider {choice!r} (use auto, anthropic or agent)")
