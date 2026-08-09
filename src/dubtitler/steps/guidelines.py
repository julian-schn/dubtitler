"""Write the translation brief for this job's language pair.

    python -m dubtitler.steps.guidelines

Writes project/guidelines.md. It is the largest single input to the translation
prompt, and after generation it belongs to the human: it is where a job's
decisions about register, names and quotation are recorded, and re-running this
step never overwrites it.

The brief is generated per pair rather than shipped, because most of what a
translator needs to be told is specific to the direction of travel. A German to
Norwegian brief has to warn about særskriving; an English to German one does
not, and would be worse for containing the warning.

Section 8 is not free-form: the subtitle limits come from config.toml, and the
same numbers are enforced by `resegment` and verified by `qc`. A brief that
disagreed with them would send a translator chasing a line length the pipeline
then rewrites anyway.
"""

from __future__ import annotations

import argparse
import sys

from .. import llm
from ..core import PROJECT, ROOT, cfg, config
from ..langpack import load as load_pack

PATH = PROJECT / "guidelines.md"


def limits_section() -> str:
    s = config()["subtitles"]
    return (
        f"- at most {s['max_lines']} lines per cue\n"
        f"- at most {s['max_line']} characters per line\n"
        f"- at most {s['max_cps']} characters per second of reading speed\n"
        f"- cues last between {s['min_duration']:.2f} and {s['max_duration']:.1f} "
        f"seconds\n"
    )


def prompt() -> str:
    source = load_pack(cfg("project", "source_lang"))
    target = load_pack(cfg("project", "target_lang"))

    return f"""Write a translation brief, as markdown, for a subtitling job
going from {source.name} into {target.name}. The project is called
"{cfg('project', 'name')}".

It is read by whoever or whatever does the translation, and it is the main
lever on output quality, so be specific and prescriptive rather than general.

Cover, as numbered sections:

1. What the register should be. The source is speech: spontaneous, sometimes
   dialect, with false starts. Say what survives into the subtitles and what
   is cleaned up.
2. The traps peculiar to this direction of translation. Name the actual
   interference errors a translator makes going from {source.name} into
   {target.name}, not generic advice.
3. Sentence structure, and what to do when the source sentence is far longer
   than a subtitle can carry.
4. Numbers, dates and measurements.
5. Proper nouns: when they are translated, when they are kept, and how the
   glossary decides.
6. Quotations. If the source quotes a text that has a standard published
   translation in {target.name}, that published wording is quoted rather than
   re-translated. Say how to record which edition was used.
7. Filler words, repetitions and self-corrections.
8. Reserved: the caller appends the subtitle limits, so write only the
   heading "## 8. Subtitle limits" and nothing under it.

Notes on {target.name} worth working in:

{target.notes.strip() or '(none recorded)'}

Return ONLY the markdown. No fence, no commentary.
"""


def run(force: bool = False, provider: str | None = None) -> bool:
    if PATH.exists() and not force:
        print(f"{PATH.relative_to(ROOT)} already exists, leaving it alone.")
        print("It is yours now; use --force only to regenerate from scratch.")
        return False

    engine = llm.get(provider)
    source = load_pack(cfg("project", "source_lang"))
    target = load_pack(cfg("project", "target_lang"))
    print(f"writing a {source.name} to {target.name} brief via {engine.name} ...",
          flush=True)
    try:
        body = engine.complete(
            f"guidelines.{source.code}-{target.code}", prompt(), 8000
        )
    except llm.NeedsAgent as e:
        from ..llm.agent import instruction

        print(instruction(e))
        sys.exit(2)

    text = body.strip()
    if text.startswith("```"):
        text = "\n".join(text.splitlines()[1:])
    if text.endswith("```"):
        text = "\n".join(text.splitlines()[:-1])

    # Appended rather than generated, so the brief and the enforcement can
    # never disagree about the numbers.
    if "## 8." not in text:
        text += "\n\n## 8. Subtitle limits\n"
    text = text.rstrip() + "\n\n" + limits_section()
    text += (
        "\nThese come from `config.toml`. `resegment` enforces them and `qc` "
        "verifies them,\nso they are the real limits, not a suggestion.\n"
    )

    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(text, encoding="utf-8")
    print(f"wrote {PATH.relative_to(ROOT)}")
    print("Read it before translating. From here on it is yours to edit.")
    return True


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true",
                    help="regenerate, discarding the current brief")
    ap.add_argument("--provider", choices=["auto", "anthropic", "agent"])
    args = ap.parse_args(argv)
    run(args.force, args.provider)


if __name__ == "__main__":
    main()
