"""Everything the app shows, derived from the pipeline's own output.

Nothing here is precomputed and there is no build step. Each request re-reads
the files on disk, which is what makes a video that finishes the pipeline appear
on the dashboard by refreshing the browser. The data is small, tens of KB of
JSON per video, so the simplicity costs nothing.

Audio is the one exception: encoding a six-minute MP3 takes a few seconds, so it
is written to work/review/audio/ and reused. That directory is a cache and is
safe to delete at any time.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..core import OUT, WORK, cfg, ffmpeg, find_source, slug
from ..langpack import load as load_pack
from ..project import read_glossary, release_slug, title_for
from ..review import existing_text
from . import flags

REVIEW = WORK / "review"
STT = WORK / "stt"
AUDIO_CACHE = REVIEW / "audio"

# 48 kbps mono: about 2 MB for six minutes. Encoded from the source video rather
# than from the 16 kHz copy the engines used, because dialect is markedly easier
# to catch at full bandwidth and hearing the dialect is the entire point.
AUDIO_ARGS = ["-vn", "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "48k"]

MAX_OCCURRENCES = 6  # beyond this a glossary row stops being scannable


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def videos() -> list[dict]:
    out = []
    for p in sorted(REVIEW.glob("*.review.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append({"video": d["video"], "slug": d["slug"],
                    "title": title_for(d["video"]) or d["video"]})
    return out


def video_for_slug(s: str) -> str | None:
    for v in videos():
        if v["slug"] == s:
            return v["video"]
    return None


def _sentences(video: str) -> list[dict]:
    p = STT / f"{video}.sentences.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def review_json(video: str) -> dict:
    p = REVIEW / f"{slug(video)}.review.json"
    if not p.exists():
        raise FileNotFoundError(f"no review data for {video}, run steps.diff")
    return json.loads(p.read_text(encoding="utf-8"))


def live_text(video: str, n: int) -> list[str] | None:
    """Segment texts from the markdown the human actually edits.

    The markdown wins over the JSON. Reading the JSON alone would show
    pre-correction text in a page whose whole purpose is showing corrections.
    """
    return existing_text(REVIEW / f"{video}.{cfg('project', 'source_lang')}.md", n)


def progress(video: str) -> dict:
    p = REVIEW / f"{slug(video)}.progress.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"reviewed": []}


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------

def status(video: str, segments: list[dict], sents: list[dict]) -> dict:
    """Where this video has got to, derived from which artifacts exist.

    Deliberately not tracked in a state file: the files are the truth, and a
    state file goes stale the moment anyone re-runs a step by hand.
    """
    target = cfg("project", "target_lang")
    translated = sum(1 for s in sents if (s.get("target") or "").strip())
    reviewed = len(progress(video).get("reviewed", []))
    name = release_slug(video)
    srt = OUT / f"{name}.{target}.srt"
    burned = OUT / f"{name}.{target}.burned.mp4"

    if burned.exists():
        stage = "rendered"
    elif srt.exists():
        stage = "cues built"
    elif translated:
        stage = "translated"
    elif reviewed:
        stage = "in review"
    else:
        stage = "transcribed"

    return {"stage": stage, "segments": len(segments),
            "flagged": sum(1 for s in segments if s["flags"]),
            "reviewed": reviewed, "sentences": len(sents),
            "translated": translated,
            "has_srt": srt.exists(), "has_render": burned.exists()}


def dashboard() -> dict:
    items = []
    for v in videos():
        segs = review_json(v["video"])["segments"]
        items.append({**v, **status(v["video"], segs, _sentences(v["video"]))})

    rows, _, _ = read_glossary()
    source = load_pack(cfg("project", "source_lang"))
    target = load_pack(cfg("project", "target_lang"))
    return {
        "project": cfg("project", "name"),
        "source": {"code": source.code, "name": source.name or source.code},
        "target": {"code": target.code, "name": target.name or target.code},
        "videos": items,
        "glossary": {"terms": len(rows),
                     "approved": sum(1 for r in rows if "✅" in r["notes"])},
    }


# --------------------------------------------------------------------------
# gate 1, transcript
# --------------------------------------------------------------------------

def transcript(video: str) -> dict:
    data = review_json(video)
    segs = data["segments"]
    live = live_text(video, len(segs))
    if live:
        for s, t in zip(segs, live):
            s["text"] = t
    return {"video": data["video"], "slug": data["slug"],
            "title": title_for(video) or video,
            "engines": data.get("engines", []),
            "segments": segs, "reviewed": progress(video).get("reviewed", [])}


# --------------------------------------------------------------------------
# gate 2, translation
# --------------------------------------------------------------------------

def translation(video: str) -> dict:
    """Source, target and back-translation side by side, with timings.

    Timings come straight from the sentence records, so the same audio element
    the transcript view uses can play a sentence here too.
    """
    source = load_pack(cfg("project", "source_lang"))
    target = load_pack(cfg("project", "target_lang"))
    terms = flags.glossary_terms(source, target)

    rows = []
    for s in _sentences(video):
        src = s.get("source", "")
        tgt = s.get("target") or ""
        back = s.get("back") or ""
        rows.append({"id": s["id"], "start": s["start"], "end": s["end"],
                     "speaker": s.get("speaker"), "source": src, "target": tgt,
                     "back": back,
                     "flags": flags.sentence_flags(tgt, back, src, terms,
                                                   source, target)})
    return {"video": video, "slug": slug(video),
            "title": title_for(video) or video, "sentences": rows}


# --------------------------------------------------------------------------
# glossary
# --------------------------------------------------------------------------

def _corpus() -> list[dict]:
    out = []
    for v in videos():
        segs = review_json(v["video"])["segments"]
        live = live_text(v["video"], len(segs))
        if live:
            for s, t in zip(segs, live):
                s["text"] = t
        out.append({"video": v["video"], "slug": v["slug"], "segments": segs})
    return out


def occurrences(term: str, corpus: list[dict]) -> list[dict]:
    """Segments where a term appears, with timings.

    Matched on a stem rather than the whole word, because inflecting languages
    will not repeat a term in the same form twice; matched at a word boundary
    rather than as a bare substring, so `Gott` does not light up every
    `gottesfürchtig`.
    """
    if not term or term.startswith("~~"):
        return []
    stem = re.escape(term.split(" ")[0].rstrip("s"))
    pattern = re.compile(rf"\b{stem}\w*", re.IGNORECASE)

    hits = []
    for v in corpus:
        for seg in v["segments"]:
            if pattern.search(seg["text"]):
                hits.append({"slug": v["slug"], "video": v["video"],
                             "start": seg["start"], "end": seg["end"],
                             "text": seg["text"]})
                if len(hits) >= MAX_OCCURRENCES:
                    return hits
    return hits


def glossary() -> dict:
    rows, _, _ = read_glossary()
    corpus = _corpus()
    return {"terms": [{"term": r["term"], "translation": r["translation"],
                       "notes": r["notes"], "count": r["count"],
                       "approved": "✅" in r["notes"],
                       "occurrences": occurrences(r["term"], corpus)}
                      for r in rows]}


# --------------------------------------------------------------------------
# audio
# --------------------------------------------------------------------------

def audio_path(video: str) -> Path:
    """Path to the review MP3, encoding it on first use.

    Cached on disk so the cost is paid once per video ever, rather than once per
    page. An earlier design inlined the audio into each generated page, which
    put the same six minutes of sound into every file that mentioned it.
    """
    AUDIO_CACHE.mkdir(parents=True, exist_ok=True)
    out = AUDIO_CACHE / f"{slug(video)}.mp3"
    if out.exists() and out.stat().st_size > 0:
        return out

    src = find_source(video)
    tmp = out.with_suffix(".part")
    # `-f mp3` is required because ffmpeg picks the muxer from the extension,
    # and the temp file deliberately does not end in .mp3 so that a killed
    # encode can never be mistaken for a finished one.
    ffmpeg("-i", str(src), *AUDIO_ARGS, "-f", "mp3", str(tmp))
    tmp.replace(out)  # atomic
    return out
