"""The no-key path: the prompt becomes a file, and a person or agent answers it.

The step runs twice. The first run writes work/prompts/<key>.prompt.md and
stops. Whoever is driving reads that file, does the work, and saves the reply as
work/prompts/<key>.answer.txt. The second run finds the answer and continues.

This is not a degraded mode. It is how the reference job was actually done, and
it has one advantage over the API path worth keeping: the briefing and the reply
are both sitting on disk in a form a human can read, diff and correct, which is
exactly what you want for the step that decides what the subtitles say.

Answers are kept, not consumed. Re-running a step must not silently re-ask for
work that was already done; delete the answer file to force a new prompt.
"""

from __future__ import annotations

from pathlib import Path

from ..core import ROOT, WORK
from . import NeedsAgent


class AgentLLM:
    name = "agent"

    def paths(self, key: str) -> tuple[Path, Path]:
        base = WORK / "prompts"
        return base / f"{key}.prompt.md", base / f"{key}.answer.txt"

    def complete(self, key: str, prompt: str, max_tokens: int = 8000) -> str:
        prompt_path, answer_path = self.paths(key)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)

        # The prompt is rewritten every run so that an answer is never paired
        # with a stale briefing: if the inputs changed, the file on disk shows
        # what the current question is.
        prompt_path.write_text(prompt, encoding="utf-8")

        if answer_path.exists():
            text = answer_path.read_text(encoding="utf-8").strip()
            if text:
                return text

        raise NeedsAgent(prompt_path, answer_path)


def instruction(exc: NeedsAgent) -> str:
    """What to print when a step stops for an answer."""
    return (
        f"\nThis step needs a model and no ANTHROPIC_API_KEY is set.\n\n"
        f"  1. read   {exc.prompt.relative_to(ROOT)}\n"
        f"  2. answer it exactly as the prompt asks\n"
        f"  3. save the reply to {exc.answer.relative_to(ROOT)}\n"
        f"  4. run this same command again\n\n"
        f"If you are in a coding agent, steps 1 to 3 are one turn: read the "
        f"file, write the answer file.\n"
    )
