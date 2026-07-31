"""The API path: a job runs start to finish without anyone watching.

Streaming is not optional here. A transcript's worth of translation is a long
generation, and a non-streaming request of that size runs into the request
timeout rather than finishing.
"""

from __future__ import annotations

import sys

from ..core import WORK, cfg


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, model: str | None = None):
        self.model = model or cfg("llm", "model")

    def complete(self, key: str, prompt: str, max_tokens: int = 8000) -> str:
        from anthropic import Anthropic

        from . import has_api_key

        if not has_api_key():
            sys.exit(
                "ANTHROPIC_API_KEY is not set.\n"
                "Either add it to .env, or set llm.provider = \"agent\" in "
                "config.toml to answer the prompts in-session instead."
            )

        client = Anthropic()
        with client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            # Translation and glossary work is judgement, not lookup: the model
            # is weighing register, ambiguity and prior context.
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()

        text = "".join(b.text for b in message.content if b.type == "text").strip()

        # Kept for the same reason the agent path keeps its files: when a
        # translation reads oddly, the first question is what was actually
        # asked and answered.
        log = WORK / "prompts"
        log.mkdir(parents=True, exist_ok=True)
        (log / f"{key}.prompt.md").write_text(prompt, encoding="utf-8")
        (log / f"{key}.answer.txt").write_text(text, encoding="utf-8")

        if message.stop_reason == "max_tokens":
            print(
                f"WARNING: {key} hit the {max_tokens}-token ceiling and was cut "
                f"off mid-answer. Expect missing items.",
                file=sys.stderr,
            )
        return text
