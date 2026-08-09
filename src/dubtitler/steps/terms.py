"""Collect glossary candidates from the corrected transcripts.

    python -m dubtitler.steps.terms [<video> ...] [--propose]

Appends candidates to project/glossary.md without disturbing rows anyone has
already filled in, so it is safe to re-run when another video arrives. Counts
are refreshed; translations never are.

`--propose` additionally asks a model to fill the empty translation column.
Those are proposals, and the file says so: the point of a glossary is that a
human decided once how a recurring name is rendered, and every subtitle then
agrees with that decision.

Extraction uses named-entity recognition rather than a capitalisation rule.
In German, which capitalises every noun, "capitalised mid-sentence therefore
proper noun" fires on essentially every content word and yields pure noise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

from .. import llm
from ..core import WORK, cfg, slug
from ..langpack import Pack, load as load_pack
from ..project import read_glossary, write_glossary

MIN_COUNT = 3            # for repeated nouns that NER did not catch
MIN_NOUN_LEN = 7
ENT_LABELS = {"PER", "ORG", "LOC", "MISC"}

# Only numbers a translator could get *wrong* earn a row: years, and figures
# with a separator. A bare age translates as itself and would bury the rest.
NUMBER_LIKE = re.compile(r"\b(?:19|20)\d{2}\b|\b\d{1,3}(?:[.\s]\d{3})+\b|\b\d+,\d+\b")


def corrected_text(video: str) -> str:
    """The transcript as the human left it, not as the engines produced it.

    Reading the engine output here was a real bug on the reference job: terms
    that had been corrected at gate 1 were extracted in their original wrong
    spelling, and then found no audio when someone tried to check them.
    """
    path = WORK / "review" / f"{slug(video)}.review.json"
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    return " ".join(s["text"] for s in data["segments"]).strip()


def extract(text: str, pack: Pack) -> tuple[Counter, dict[str, str]]:
    counts: Counter[str] = Counter()
    kinds: dict[str, str] = {}

    try:
        import spacy

        nlp = spacy.load(pack.ner_model) if pack.ner_model else None
    except Exception:
        nlp = None

    if nlp is None:
        print(f"note: spaCy {pack.ner_model or 'model'} not installed, "
              f"only numbers will be extracted.\n"
              f"      uv run python -m spacy download {pack.ner_model}",
              file=sys.stderr)
    else:
        doc = nlp(text)
        for ent in doc.ents:
            if ent.label_ not in ENT_LABELS:
                continue
            parts = ent.text.strip(" ,.;:!?").split()
            # A span whose first token is a function word is a mis-span; keep
            # the tail. Spontaneous speech makes NER do this constantly.
            while parts and pack.is_function_word(parts[0]):
                parts.pop(0)
            term = " ".join(parts)
            if len(term) < 2 or pack.is_function_word(term):
                continue
            counts[term] += 1
            kinds[term] = ent.label_

        for tok in doc:
            if tok.pos_ == "NOUN" and len(tok.text) > MIN_NOUN_LEN and not tok.is_stop:
                counts[tok.lemma_] += 1
                kinds.setdefault(tok.lemma_, "TERM")
        for term in [t for t, c in counts.items()
                     if kinds.get(t) == "TERM" and c < MIN_COUNT]:
            del counts[term]

    for m in NUMBER_LIKE.finditer(text):
        counts[m.group(0)] += 1
        kinds.setdefault(m.group(0), "NUM")

    return counts, kinds


def propose(rows: list[dict], provider: str | None) -> int:
    """Fill empty translation cells with model proposals."""
    todo = [r for r in rows if not r["translation"].strip()]
    if not todo:
        print("every term already has a translation")
        return 0

    source = load_pack(cfg("project", "source_lang"))
    target = load_pack(cfg("project", "target_lang"))
    listing = "\n".join(f"[{i}] {r['term']}  ({r['kind'] or 'term'})"
                        for i, r in enumerate(todo))

    text = f"""These terms come from a {source.name} transcript and need
{target.name} renderings for subtitles, for the project
"{cfg('project', 'name')}".

<terms>
{listing}
</terms>

Rules:

- Proper nouns usually keep their original form. When that is your judgement,
  repeat the source form rather than leaving it blank, so the decision is
  explicit.
- Place names take their established {target.name} form if one exists.
- Years and figures: give the form written out as {target.name} convention
  requires.
- These are subtitles, so prefer the short natural rendering.

Return ONLY a JSON object mapping each index (as a string) to the rendering.
Example: {{"0": "Stuttgart", "1": "andre verdenskrig"}}
"""

    engine = llm.get(provider)
    print(f"proposing {len(todo)} translation(s) via {engine.name} ...", flush=True)
    try:
        reply = engine.complete(f"glossary.{target.code}", text, 8000)
    except llm.NeedsAgent as e:
        from ..llm.agent import instruction

        print(instruction(e))
        sys.exit(2)

    mapping = llm.parse_json(reply)
    n = 0
    for i, row in enumerate(todo):
        value = mapping.get(str(i))
        if isinstance(value, str) and value.strip():
            row["translation"] = value.strip()
            n += 1
    print(f"proposed {n}/{len(todo)}. Review them before translating.")
    return n


def run(videos: list[str], do_propose: bool = False, provider: str | None = None) -> int:
    pack = load_pack(cfg("project", "source_lang"))

    targets = videos or sorted(
        p.name[: -len(".merged.json")] for p in (WORK / "stt").glob("*.merged.json")
    )
    text = " ".join(corrected_text(v) for v in targets).strip()
    if not text:
        sys.exit("no corrected transcripts found, run steps.diff first")

    counts, kinds = extract(text, pack)
    rows, pre, post = read_glossary()
    by_term = {r["term"]: r for r in rows}

    added = 0
    for term, count in counts.most_common():
        if term in by_term:
            by_term[term]["count"] = str(count)  # refresh, keep the translation
            continue
        row = {"term": term, "translation": "", "kind": kinds.get(term, ""),
               "notes": "", "count": str(count)}
        rows.append(row)
        by_term[term] = row
        added += 1

    if do_propose:
        propose(rows, provider)

    def sort_key(r):
        try:
            n = int(r.get("count") or 0)
        except ValueError:
            n = 0
        return (-n, r["term"].casefold())

    rows.sort(key=sort_key)
    write_glossary(rows, pre, post)

    untranslated = sum(1 for r in rows if not r["translation"].strip())
    print("wrote project/glossary.md")
    print(f"{len(rows)} terms total, {added} new, {untranslated} awaiting translation")
    return added


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("videos", nargs="*")
    ap.add_argument("--propose", action="store_true",
                    help="ask a model to fill the empty translation column")
    ap.add_argument("--provider", choices=["auto", "anthropic", "agent"])
    args = ap.parse_args(argv)
    run(args.videos, args.propose, args.provider)


if __name__ == "__main__":
    main()
