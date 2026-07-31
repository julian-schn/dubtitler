"""Local review app: one server, every view, every video.

Stdlib only, bound to the loopback address. The save routes write into the
repository, so this must never be reachable from the network.

Audio is served from disk with Range support rather than embedded in the page.
That is what makes the app scale: one MP3 per video, shared by the transcript,
translation and glossary views and cached by the browser, instead of a copy
inlined into every generated page.
"""

from __future__ import annotations

import json
import mimetypes
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from ..core import cfg, slug
from ..project import (
    read_glossary, read_videos, write_glossary, write_videos,
)
from ..review import render
from . import data

HOST = "127.0.0.1"
STATIC = Path(__file__).parent / "static"
MAX_BODY = 8 << 20
CHUNK = 64 << 10

_lock = threading.Lock()


def log(msg: str) -> None:
    """Print, without letting a dead stdout turn a successful save into a
    reported failure. That happens whenever the server is launched with its
    output redirected to a pipe that later closes."""
    try:
        print(msg, flush=True)
    except OSError:
        pass


# --------------------------------------------------------------------------
# save handlers
# --------------------------------------------------------------------------

def save_transcript(video: str, payload: dict) -> str:
    """Rewrite the review markdown from the submitted segment texts.

    Goes through the same renderer the diff step uses, so headers, speaker
    tags, flags and notes come out identical and the corrections step keeps
    parsing the result. The segment count can never change, which is the
    invariant that step refuses on.
    """
    records = data.review_json(video)["segments"]
    segments = payload.get("segments") or []
    if len(segments) != len(records):
        raise ValueError(
            f"expected {len(records)} segments, got {len(segments)}, refusing "
            "to write a transcript the pipeline cannot re-import"
        )

    language = cfg("project", "source_lang")
    with _lock:
        merged = []
        for rec, incoming in zip(records, segments):
            text = (incoming.get("text") or "").strip()
            # An empty box is far likelier to be a slip than a deliberate
            # deletion, so keep what was there.
            merged.append({**rec, "text": text or rec["text"]})

        md = data.REVIEW / f"{video}.{language}.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(render(video, language, merged), encoding="utf-8")

        reviewed = sorted(set(payload.get("reviewed") or []))
        (data.REVIEW / f"{slug(video)}.progress.json").write_text(
            json.dumps({"reviewed": reviewed}, indent=2), encoding="utf-8"
        )
    log(f"  saved {md.name}  ({len(reviewed)}/{len(records)} reviewed)")
    return md.name


def save_glossary(payload: dict) -> str:
    """Rewrite the glossary table, leaving the prose around it untouched."""
    terms = payload.get("terms") or []
    if not terms:
        raise ValueError("no terms in payload")

    with _lock:
        rows, preamble, postamble = read_glossary()
        by_term = {r["term"]: r for r in rows}
        approved = 0
        for t in terms:
            row = by_term.get(t.get("term"))
            if row is None:
                # The page was built from an older table. Skip rather than
                # invent a row, so a stale tab cannot resurrect a deleted term.
                continue
            row["translation"] = (t.get("translation") or "").strip() or row["translation"]
            row["notes"] = (t.get("notes") or "").strip()
            if t.get("approved"):
                approved += 1
                if "✅" not in row["notes"]:
                    row["notes"] = ("✅ approved · " + row["notes"]).rstrip(" ·")
            else:
                row["notes"] = re.sub(r"^✅ approved · ?", "", row["notes"])
        write_glossary(rows, preamble, postamble)
    log(f"  saved project/glossary.md  ({approved}/{len(rows)} approved)")
    return "project/glossary.md"


def save_titles(payload: dict) -> str:
    """Update titles in project/videos.md.

    The source column is deliberately not writable from the UI. It is how every
    file under work/ is addressed, so a typo there would orphan a whole video's
    intermediates. Only the title changes.
    """
    items = payload.get("videos") or []
    if not items:
        raise ValueError("no videos in payload")

    with _lock:
        rows, preamble, postamble = read_videos()
        by_source = {r["source"]: r for r in rows}
        changed = 0
        for item in items:
            row = by_source.get(item.get("video"))
            title = (item.get("title") or "").strip()
            if row is None or not title or row["title"] == title:
                continue
            row["title"] = title
            row["notes"] = "edited"
            changed += 1
        write_videos(rows, preamble, postamble)
    log(f"  saved project/videos.md  ({changed} title(s) changed)")
    return "project/videos.md"


# --------------------------------------------------------------------------
# request handling
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "subtitler-review"

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def _static(self, name: str) -> None:
        p = (STATIC / name).resolve()
        if not p.is_file() or STATIC.resolve() not in p.parents:
            self._json(404, {"error": "not found"})
            return
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        self._send(200, p.read_bytes(), ctype)

    def _audio(self, s: str) -> None:
        """Serve the review MP3 with Range support.

        Without Range the browser has to download the whole file before seeking
        is reliable, and seeking to a segment is what these views are for.
        """
        video = data.video_for_slug(s)
        if not video:
            self._json(404, {"error": f"unknown video {s!r}"})
            return
        try:
            path = data.audio_path(video)  # encodes on first request
        except Exception as e:
            self._json(500, {"error": f"audio encode failed: {e}"})
            return

        size = path.stat().st_size
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        start, end, partial = 0, size - 1, False
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = int(m.group(2))
            else:  # suffix range: bytes=-N
                start = max(0, size - int(m.group(2)))
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            partial = True

        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(CHUNK, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return  # the browser seeked away; not an error
                remaining -= len(chunk)

    def do_GET(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path in ("/", "/index.html"):
                self._static("index.html")
            elif path.startswith("/static/"):
                self._static(path[len("/static/"):])
            elif path == "/api/state":
                self._json(200, data.dashboard())
            elif path == "/api/glossary":
                self._json(200, data.glossary())
            elif path.startswith("/api/video/"):
                s = path[len("/api/video/"):]
                video = data.video_for_slug(s)
                if not video:
                    self._json(404, {"error": f"unknown video {s!r}"})
                    return
                self._json(200, {"transcript": data.transcript(video),
                                 "translation": data.translation(video)})
            elif path.startswith("/audio/"):
                self._audio(path[len("/audio/"):].removesuffix(".mp3"))
            elif path == "/health":
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not found"})
        except Exception as e:
            self._json(500, {"error": str(e)})

    do_HEAD = do_GET

    def do_POST(self):  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                raise ValueError("bad content length")
            payload = json.loads(self.rfile.read(n))

            if path == "/api/glossary":
                wrote = save_glossary(payload)
            elif path == "/api/videos":
                wrote = save_titles(payload)
            elif path.startswith("/api/transcript/"):
                s = path[len("/api/transcript/"):]
                video = data.video_for_slug(s)
                if not video:
                    raise ValueError(f"unknown video {s!r}")
                wrote = save_transcript(video, payload)
            else:
                self._json(404, {"error": "not found"})
                return
        except Exception as e:
            self._json(400, {"error": str(e)})
            return
        self._json(200, {"ok": True, "wrote": wrote})

    def log_message(self, *a):  # keep the console to our own messages
        pass


def serve(port: int = 8731, open_browser: bool = True) -> None:
    httpd = ThreadingHTTPServer((HOST, port), Handler)
    url = f"http://{HOST}:{port}/"
    vids = data.videos()
    language = cfg("project", "source_lang")
    log(f"\nreview app on {url}")
    log(f"  {len(vids)} video(s): {', '.join(v['video'] for v in vids) or 'none'}")
    log(f"  edits save straight to work/review/*.{language}.md and "
        f"project/glossary.md")
    log("  ctrl-c to stop\n")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("\nstopped.")
    finally:
        httpd.server_close()
