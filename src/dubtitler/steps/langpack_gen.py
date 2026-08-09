"""Generate a language pack for a language that does not have one yet.

    python -m dubtitler.steps.langpack_gen <code>

Writes lang/<code>.toml. From that moment it is an ordinary file: review it,
correct it, commit it. Nothing consults a model at runtime, so two runs of the
pipeline over the same audio always produce the same flags.

This is what lets a new language pair work on day one instead of waiting for
someone to hand-write word lists. The lists are the kind of thing a model
produces well and a human verifies quickly, which is the right division of
labour; the reverse would be neither.

An existing pack is never overwritten. Use --force only when you mean to throw
away corrections someone made by hand.
"""

from __future__ import annotations

import argparse
import sys
import tomllib

from .. import llm
from ..core import ROOT
from ..langpack import pack_path

SCHEMA = """\
name              the language's English name
iso3              ISO 639-2/T three-letter code
ner_model         spaCy model name for this language, or "" if none exists
notes             a short paragraph on what is distinctive about writing and
                  breaking lines in this language, addressed to a translator
function_words    unstressed grammatical words: articles, pronouns, auxiliaries,
                  prepositions, conjunctions, common fillers and hesitations
negations         every word that can negate a clause
break_before      words a subtitle line may start with
never_trail       words a subtitle line must not end with
elision_suffixes  single-character endings that are dialect or inflection only
abbreviations     abbreviations whose trailing dot does not end a sentence,
                  written lowercase and without the final dot
transliterate     non-ASCII letters to their conventional ASCII spelling
numbers           spelled-out numerals: units 0-9, teens 10-19, tens 20-90,
                  scale (hundred, thousand), and `joiner`
"""


def prompt(code: str) -> str:
    return f"""Write a language pack for the language with code "{code}", as TOML.

It configures a subtitling pipeline. Two things use it: cross-engine comparison
of speech-to-text output, and line breaking in subtitles.

Fields:

{SCHEMA}

Rules that matter:

- Every root-level key must appear BEFORE any [table] header. In TOML a key
  written after a header belongs to that table, so a misplaced list silently
  loads as empty and the pipeline stops flagging anything.
- `elision_suffixes` folds away endings that are dialect or inflection rather
  than transcription error. German uses ["n", "e"] so that `hab`, `habe` and
  `haben` compare equal. Leave it EMPTY unless an ending in this language
  genuinely carries no meaning, since folding a meaningful ending hides real
  errors.
- `function_words` is the list that decides which engine disagreements a human
  is asked to review. Too short and the review queue is unreadable; too long
  and real errors are filtered out. Aim for the genuinely unstressed words.
- Number tables are keyed by the ASCII form produced after `transliterate` is
  applied, so a German pack writes `fuenf`, not `fünf`.
- `joiner` is the particle in compounds like German `dreiundzwanzig` ("und").
  Use "" for languages that do not join that way.

Return ONLY the TOML. No prose, no markdown fence. Include short `#` comments
where a choice is not obvious.
"""


def validate(text: str, code: str) -> dict:
    """Parse and sanity-check a generated pack before it reaches disk.

    The ordering trap is worth checking explicitly: a pack with its lists
    nested under [transliterate] parses fine and then flags nothing, which
    looks like a tuning problem rather than a syntax one.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[1:])
    if cleaned.endswith("```"):
        cleaned = "\n".join(cleaned.splitlines()[:-1])

    data = tomllib.loads(cleaned)

    problems = []
    for key in ("function_words", "negations", "break_before", "never_trail"):
        if not isinstance(data.get(key), list) or not data[key]:
            problems.append(f"{key} is missing or empty (a misplaced [table] header?)")
    if not data.get("name"):
        problems.append("name is missing")
    units = data.get("numbers", {}).get("units")
    if not units:
        problems.append("numbers.units is missing")
    if problems:
        raise ValueError("generated pack is not usable:\n  " + "\n  ".join(problems))

    return {"toml": cleaned, "data": data}


def run(code: str, force: bool = False, provider: str | None = None) -> bool:
    path = pack_path(code)
    if path.exists() and not force:
        print(f"{path.relative_to(ROOT)} already exists, leaving it alone.")
        return False

    engine = llm.get(provider)
    print(f"generating a {code} language pack via {engine.name} ...", flush=True)
    try:
        reply = engine.complete(f"langpack.{code}", prompt(code), 8000)
    except llm.NeedsAgent as e:
        from ..llm.agent import instruction

        print(instruction(e))
        sys.exit(2)

    checked = validate(reply, code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(checked["toml"].rstrip() + "\n", encoding="utf-8")

    d = checked["data"]
    print(f"wrote {path.relative_to(ROOT)}")
    print(f"  {d['name']}: {len(d['function_words'])} function words, "
          f"{len(d['negations'])} negations, {len(d['never_trail'])} never-trail")
    print("\nThis was generated, not verified. Read it before trusting the flags,")
    print("then commit it: from here on it is a normal file that belongs to you.")
    return True


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("code", help="two-letter language code, e.g. es")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing pack, discarding hand corrections")
    ap.add_argument("--provider", choices=["auto", "anthropic", "agent"])
    args = ap.parse_args(argv)
    run(args.code, force=args.force, provider=args.provider)


if __name__ == "__main__":
    main()
