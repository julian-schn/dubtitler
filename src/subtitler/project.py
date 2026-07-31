"""The per-job files a human owns: titles, glossary, guidelines, sources.

These are markdown so that reviewing them needs nothing but a text editor, and
tables so that code can still read them. Everything around a table is prose the
human wrote and is preserved verbatim on every rewrite: these files accumulate
research notes, and losing those to a re-run would be far worse than failing to
pick up a term.
"""

from __future__ import annotations

from .core import PROJECT, read_table, slug, write_table

VIDEOS = PROJECT / "videos.md"
GLOSSARY = PROJECT / "glossary.md"

VIDEOS_HEADER = "| Source | Title | Notes |"
VIDEOS_KEYS = ("source", "title", "notes")

GLOSSARY_HEADER = "| Term | Translation | Kind | Count |"
GLOSSARY_KEYS = ("term", "translation", "kind", "count")

VIDEOS_PREAMBLE = [
    "# Videos",
    "",
    "The title names the deliverables in `out/`, so a client receives",
    "`interview-with-a-neighbour.no.srt` rather than a camera filename.",
    "Retitling renames deliverables and nothing else; work in progress is keyed",
    "on the source filename and is never affected.",
    "",
]

GLOSSARY_PREAMBLE = [
    "# Glossary",
    "",
    "Terms extracted from the corrected transcripts. **Fill in the translation",
    "column before translation starts.** Once approved, every occurrence is",
    "rendered identically across every video in the job.",
    "",
    "Proper nouns usually stay in the source language. Write the source form",
    "again in the translation column to make that an explicit decision rather",
    "than a blank someone has to interpret.",
    "",
    "Kinds: PER person · ORG organisation · LOC place · MISC other named ·",
    "NUM number or year · TERM repeated domain noun",
    "",
]


def read_videos() -> tuple[list[dict], list[str], list[str]]:
    rows, pre, post = read_table(VIDEOS, VIDEOS_HEADER, VIDEOS_KEYS)
    return rows, pre or list(VIDEOS_PREAMBLE), post


def write_videos(rows, preamble, postamble) -> None:
    write_table(VIDEOS, VIDEOS_HEADER, VIDEOS_KEYS, rows, preamble, postamble)


def read_glossary() -> tuple[list[dict], list[str], list[str]]:
    rows, pre, post = read_table(GLOSSARY, GLOSSARY_HEADER, GLOSSARY_KEYS)
    return rows, pre or list(GLOSSARY_PREAMBLE), post


def write_glossary(rows, preamble, postamble) -> None:
    write_table(GLOSSARY, GLOSSARY_HEADER, GLOSSARY_KEYS, rows, preamble, postamble)


def title_for(video: str) -> str:
    for row in read_videos()[0]:
        if row["source"] == video:
            return row["title"]
    return ""


def release_slug(video: str) -> str:
    """The name a deliverable is published under.

    Deliberately different from `slug(video)`, which names everything under
    work/ and must stay stable forever because changing it orphans every
    intermediate file. This one follows the title, so retitling renames what
    the client receives and nothing else.
    """
    return slug(title_for(video) or video)
