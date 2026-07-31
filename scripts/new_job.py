#!/usr/bin/env python3
"""Strip a fresh clone back to a blank job.

    make new-job

Run once, on a clone, before starting real work. It clears the previous job's
data and leaves the tool untouched: the packs, the steps and the tests all
stay, because those are what you cloned it for.

Deliberately interactive and deliberately explicit about what it will delete.
It is destructive, it is easy to run in the wrong directory, and the thing it
deletes is exactly the thing nobody has backed up yet.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Job data. Everything here belongs to the previous job and nothing here is
# needed to run the next one.
CLEAR_DIRS = ["work", "out", "media"]
CLEAR_FILES = ["project/glossary.md", "project/videos.md", "project/guidelines.md"]

BLANK_CONFIG_NAME = "Untitled"


def describe() -> tuple[list[Path], list[Path]]:
    dirs = [ROOT / d for d in CLEAR_DIRS if (ROOT / d).exists()
            and any(p.name != ".gitkeep" for p in (ROOT / d).rglob("*"))]
    files = [ROOT / f for f in CLEAR_FILES if (ROOT / f).exists()]
    return dirs, files


def main() -> None:
    dirs, files = describe()
    if not dirs and not files:
        print("already blank, nothing to clear.")
        return

    print(f"This will permanently delete, under {ROOT}:\n")
    for d in dirs:
        n = sum(1 for p in d.rglob("*") if p.is_file())
        size = sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
        print(f"  {d.name}/    {n} file(s), {size / 1e6:.0f} MB")
    for f in files:
        print(f"  {f.relative_to(ROOT)}")

    print("\nKept: the steps, the language packs, the tests, config.toml.")
    print("Not kept anywhere else. If the previous job's deliverables matter,")
    print("copy out/ somewhere first.\n")

    # Refuse rather than assume consent when there is nobody to ask. A
    # destructive step reached from a script or a CI job is almost always a
    # mistake, and the traceback from a bare input() hides that.
    if not sys.stdin.isatty():
        sys.exit("new-job needs a terminal to confirm in; nothing was deleted.")

    if input("type the word 'blank' to proceed: ").strip() != "blank":
        sys.exit("cancelled, nothing was deleted.")

    for d in dirs:
        for child in d.iterdir():
            if child.name == ".gitkeep":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    for f in files:
        f.unlink()

    config = ROOT / "config.toml"
    text = config.read_text(encoding="utf-8")
    if 'name = ' in text:
        import re

        text = re.sub(r'^name = ".*"$', f'name = "{BLANK_CONFIG_NAME}"', text,
                      count=1, flags=re.M)
        config.write_text(text, encoding="utf-8")

    print("\ndone. Next:")
    print("  1. edit config.toml: the project name and the language pair")
    print("  2. drop the source videos in media/")
    print("  3. make all V=<basename>")


if __name__ == "__main__":
    main()
