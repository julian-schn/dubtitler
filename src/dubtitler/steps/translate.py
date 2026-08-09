"""Translate the sentences into the target language.

    python -m dubtitler.steps.translate <video>

Whole sentences go in, whole sentences come out. Cue splitting happens
afterwards in `resegment`; see the header of `sentences.py` for why translating
cue by cue produces confident nonsense.

With ANTHROPIC_API_KEY set this runs unattended. Without it, the same briefing
is written to work/prompts/ for an agent to answer, and running the command
again picks the answer up. `--apply FILE` merges a JSON mapping by hand.

The whole transcript is sent as context every time, even when only part of it
is being asked for. Later sentences routinely disambiguate earlier ones, and a
model translating the second half of a testimony without having seen the first
half makes exactly the mistakes a human reviewer then has to hunt for.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import llm
from ..core import PROJECT, ROOT, WORK, cfg
from ..langpack import load as load_pack

# One request per video where possible. Each batch resends the whole briefing,
# so splitting doubles the input cost and gives the model less to work with.
BATCH = 60
MAX_TOKENS = 16000


def load_sentences(video: str) -> tuple[list[dict], Path]:
    path = WORK / "stt" / f"{video}.sentences.json"
    if not path.exists():
        sys.exit(f"missing {path.relative_to(ROOT)}, run steps.sentences first")
    return json.loads(path.read_text(encoding="utf-8")), path


def _read(name: str) -> str:
    path = PROJECT / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def briefing(video: str, sentences: list[dict], ids: list[int] | None = None) -> str:
    """The full translator's brief. Identical down the API and agent paths."""
    source = load_pack(cfg("project", "source_lang"))
    target = load_pack(cfg("project", "target_lang"))
    numbered = "\n".join(f"[{s['id']}] {s['source']}" for s in sentences)

    scope = (
        f"\nReturn translations for ids {ids[0]} through {ids[-1]} only.\n"
        if ids else ""
    )

    return f"""You are translating from {source.name} into {target.name} for
subtitles, for the project "{cfg('project', 'name')}". Follow the guidelines
exactly.

<guidelines>
{_read('guidelines.md')}
</guidelines>

<glossary>
{_read('glossary.md')}
</glossary>

<sources>
{_read('sources.md')}
</sources>

<target-language-notes>
{target.notes.strip()}
</target-language-notes>

The full transcript, in order, is below. Use all of it as context: later
sentences often disambiguate earlier ones.

<transcript video="{video}">
{numbered}
</transcript>

Translate every sentence you are asked for. Preserve the speaker's register:
this is speech, not prose. Do not merge or split sentences, the ids have to
line up with timings.
{scope}
Return ONLY a JSON object mapping each id (as a string) to its translation.
No prose, no markdown fence, no commentary.

Example shape: {{"0": "First sentence.", "1": "Second sentence."}}
"""


def apply(video: str, mapping: dict) -> int:
    sentences, path = load_sentences(video)
    n = 0
    for s in sentences:
        value = mapping.get(str(s["id"]))
        if isinstance(value, str) and value.strip():
            s["target"] = value.strip()
            n += 1
    path.write_text(json.dumps(sentences, ensure_ascii=False, indent=2), encoding="utf-8")

    missing = [s["id"] for s in sentences if not s.get("target")]
    print(f"applied {n}/{len(sentences)} translations -> {path.relative_to(ROOT)}")
    if missing:
        print(f"WARNING: {len(missing)} sentence(s) still untranslated: "
              f"{missing[:20]}{' ...' if len(missing) > 20 else ''}")
    return n


def run(video: str, provider: str | None = None) -> int:
    sentences, _ = load_sentences(video)
    if not sentences:
        sys.exit("no sentences to translate")

    engine = llm.get(provider)
    batches = [sentences[i: i + BATCH] for i in range(0, len(sentences), BATCH)]
    out: dict[str, str] = {}

    for n, batch in enumerate(batches):
        ids = [s["id"] for s in batch]
        key = f"{video}.translate" if len(batches) == 1 else f"{video}.translate.{n}"
        scope = ids if len(batches) > 1 else None
        print(f"translating sentences {ids[0]}-{ids[-1]} via {engine.name} ...",
              flush=True)
        try:
            reply = engine.complete(key, briefing(video, sentences, scope), MAX_TOKENS)
        except llm.NeedsAgent as e:
            from ..llm.agent import instruction

            print(instruction(e))
            sys.exit(2)
        out.update(llm.parse_json(reply))

    return apply(video, out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    ap.add_argument("--provider", choices=["auto", "anthropic", "agent"],
                    help="override llm.provider from config.toml")
    ap.add_argument("--apply", metavar="FILE",
                    help="merge a JSON mapping of id to translation, no model call")
    args = ap.parse_args(argv)

    if args.apply:
        apply(args.video, json.loads(Path(args.apply).read_text(encoding="utf-8")))
        return
    run(args.video, args.provider)


if __name__ == "__main__":
    main()
