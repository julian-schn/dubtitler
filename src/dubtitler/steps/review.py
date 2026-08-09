"""Serve the review app.

    python -m dubtitler.steps.review [--port 8731] [--no-browser]

Both human gates live here: the transcript gate, where engine disagreements are
settled against the audio, and the translation gate, where the source and its
translation sit side by side. Edits save straight back into the files the rest
of the pipeline reads.

Bound to the loopback address only. The save routes write into the repository,
so this is not something to expose.
"""

from __future__ import annotations

import argparse
import sys

from ..webui.server import serve


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # An earlier version filtered argv by hand for anything not starting with a
    # dash, which quietly read `--port 8731` as a video named "8731".
    ap.add_argument("--port", type=int, default=8731)
    ap.add_argument("--no-browser", action="store_true",
                    help="do not open a browser window")
    args = ap.parse_args(argv)
    try:
        serve(port=args.port, open_browser=not args.no_browser)
    except OSError as e:
        sys.exit(f"could not start on port {args.port}: {e}\n"
                 f"Something else may still be holding it: "
                 f"lsof -ti :{args.port}")


if __name__ == "__main__":
    main()
