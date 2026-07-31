"""The gate-1 review document: how it is written and how it is read back.

Both sides of this format live here because two things produce it, the diff step
and the review app's autosave, and one thing consumes it, the corrections step.
If the writer and the reader ever drift apart, corrections stop round-tripping
and the failure is silent.
"""

from __future__ import annotations

import re
from pathlib import Path

from .core import ts
from .langpack import load as load_pack

HEADER_RE = re.compile(
    r"^##\s*\[(?P<start>[\d:.,]+)\s*(?:→|->)\s*(?P<end>[\d:.,]+)\]"
)


def render(video: str, language: str, records: list[dict]) -> str:
    """Render review records as the gate-1 markdown document.

    `language` is a pack code; the display name is resolved here rather than by
    the caller. Both callers used to pass a name, and they disagreed: the diff
    step wrote "German" and every autosave rewrote it to "de", so the file
    churned on a line neither of them meant to touch.

    Each record needs: start, end, speaker, text, flags, notes.
    """
    name = load_pack(language).name or language
    lines = [
        f"# {video}: {name} transcript, human review",
        "",
        "Edit the text under each timecode. Leave the `##` headers alone, they",
        "carry the timings that the corrections step re-attaches.",
        "Lines starting with `>` are machine notes and are ignored on re-import;",
        "delete them or leave them, either is fine.",
        "",
        "⚠️ marks a span worth listening to. Everything else can be skimmed.",
        "",
        "Easier than editing this by hand: `make review` opens the same segments",
        "with their audio, and writes your edits back here.",
        "",
        "---",
        "",
    ]
    for r in records:
        mark = " ⚠️ " + ", ".join(r["flags"]) if r.get("flags") else ""
        spk = f" [{r['speaker']}]" if r.get("speaker") else ""
        lines.append(f"## [{ts(r['start'], '.')} → {ts(r['end'], '.')}]{spk}{mark}")
        lines.extend(f"> {note}" for note in r.get("notes", []))
        lines.append("")
        lines.append(r["text"].strip())
        lines.append("")
    return "\n".join(lines)


def existing_text(md: Path, n: int) -> list[str] | None:
    """Segment texts already in the review doc, or None if they cannot be reused.

    Returns None when the file is absent or its segment count differs from the
    new transcription. In that case the two are not positionally comparable, and
    the caller must back the old file up rather than guess at a mapping.
    """
    if not md.exists():
        return None
    texts: list[str] = []
    body: list[str] = []
    started = False
    for line in md.read_text(encoding="utf-8").splitlines():
        if HEADER_RE.match(line):
            if started:
                texts.append(" ".join(body).strip())
            started, body = True, []
            continue
        if not started:
            continue
        t = line.strip()
        if t and not t.startswith(">") and t != "---":
            body.append(t)
    if started:
        texts.append(" ".join(body).strip())
    return texts if len(texts) == n else None
