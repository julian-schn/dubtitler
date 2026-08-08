"""Align the engines, flag what is worth listening to, write the review doc.

    python -m subtitler.steps.diff <video>

Reads every work/stt/<video>.<engine>.json that exists. Writes:

    work/stt/<video>.merged.json      machine truth, keeps all word timings
    work/review/<video>.<lang>.md     human gate 1, edit this directly
    work/review/<slug>.review.json    what the review app renders

Both surfaces are rendered from one list of records, which is what keeps the
flags on the page identical to the flags in the file.

Re-running this step refreshes the *flags* without discarding corrections: the
engines have not changed their minds, the human has.
"""

from __future__ import annotations

import argparse
import json
import sys

from .. import consensus
from ..core import ROOT, WORK, Transcript, cfg, set_text, slug
from ..langpack import Pack, load as load_pack
from ..review import existing_text, render

MAX_NOTES = 6

# Which engine's word timings the cues are ultimately derived from. Scribe's are
# tighter than Whisper's, so it wins when both are present. Overridden by
# `stt.spine` in config.toml.
SPINE_PREFERENCE = ["elevenlabs", "deepgram", "assemblyai", "whisper-local", "openai"]


# Other things this step's own pipeline writes beside the engine transcripts,
# in the same directory and matching the same glob.
NOT_ENGINES = {"merged", "sentences", "cues"}


def load_all(video: str) -> dict[str, Transcript]:
    """Every engine transcript for this video.

    Discovered by listing rather than by consulting the configured engine list,
    so a transcript produced by an engine that has since been removed from
    config.toml still counts as an opinion. Anything that is not shaped like a
    transcript is skipped rather than crashing the step.
    """
    found = {}
    for path in sorted((WORK / "stt").glob(f"{video}.*.json")):
        engine = path.name[len(video) + 1: -len(".json")]
        if engine in NOT_ENGINES:
            continue
        try:
            found[engine] = Transcript.load(path)
        except (KeyError, TypeError, json.JSONDecodeError):
            print(f"note: ignoring {path.name}, not a transcript", file=sys.stderr)
    return found


def pick_spine(available: dict[str, Transcript], configured: str) -> str:
    if configured and configured in available:
        return configured
    if configured:
        print(f"note: spine {configured!r} has no transcript, choosing another",
              file=sys.stderr)
    for name in SPINE_PREFERENCE:
        if name in available:
            return name
    return next(iter(available))


def confidence_at(transcripts: dict[str, Transcript], start: float, end: float) -> dict:
    """Project every engine's own confidence numbers onto a time span.

    One engine reporting low confidence is enough, deliberately: unlike a
    reading, a confidence number is not a claim about what was said, it is the
    engine reporting that it struggled. Putting it to a majority vote lets an
    engine that never reports low confidence permanently veto the signal from
    an engine that does, which quietly destroys the only flag a single-engine
    job has.

    The engine that raised each flag is named, so a consistently pessimistic
    engine shows up as a pattern in the review doc rather than as noise.
    """
    low_logprob: list[str] = []
    not_speech: list[str] = []
    low_prob: list[str] = []
    worst_logprob = worst_nospeech = worst_prob = None
    weak_total = 0

    for name, tr in transcripts.items():
        seg_logprob = seg_nospeech = None
        min_prob = None
        n_weak = 0
        saw_probs = False

        for s in tr.segments:
            if s.end <= start or s.start >= end:
                continue
            if s.avg_logprob is not None:
                seg_logprob = (s.avg_logprob if seg_logprob is None
                               else min(seg_logprob, s.avg_logprob))
            if s.no_speech_prob is not None:
                seg_nospeech = (s.no_speech_prob if seg_nospeech is None
                                else max(seg_nospeech, s.no_speech_prob))
            for w in s.words:
                if w.prob is None or w.start >= end or w.end <= start:
                    continue
                saw_probs = True
                min_prob = w.prob if min_prob is None else min(min_prob, w.prob)
                if w.prob < cfg("flags", "word_prob_floor"):
                    n_weak += 1

        if seg_logprob is not None:
            if seg_logprob < cfg("flags", "avg_logprob_floor"):
                low_logprob.append(name)
            worst_logprob = (seg_logprob if worst_logprob is None
                             else min(worst_logprob, seg_logprob))
        if seg_nospeech is not None:
            if seg_nospeech > cfg("flags", "no_speech_ceiling"):
                not_speech.append(name)
            worst_nospeech = (seg_nospeech if worst_nospeech is None
                              else max(worst_nospeech, seg_nospeech))
        if saw_probs:
            if (min_prob < cfg("flags", "word_prob_alarm")
                    or n_weak >= cfg("flags", "weak_word_cluster")):
                low_prob.append(name)
                weak_total = max(weak_total, n_weak)
            worst_prob = min_prob if worst_prob is None else min(worst_prob, min_prob)

    return {
        "low_logprob": low_logprob,
        "not_speech": not_speech,
        "low_prob": low_prob,
        "logprob": worst_logprob,
        "nospeech": worst_nospeech,
        "prob": worst_prob,
        "weak": weak_total,
    }


def load_ner(pack: Pack):
    """The pack's named-entity model, or None if it is not installed.

    Not a nicety in every language: German capitalises every noun, so the usual
    "capitalised mid-sentence means proper noun" heuristic fires on almost every
    content word and is useless there.
    """
    if not pack.ner_model:
        return None
    try:
        import spacy

        return spacy.load(pack.ner_model)
    except Exception:
        return None


def build_records(video: str, transcripts: dict[str, Transcript], pack: Pack,
                  spine_name: str) -> list[dict]:
    spine = transcripts[spine_name]
    others = {k: v for k, v in transcripts.items() if k != spine_name}
    regions = consensus.compare(spine, others, pack)

    nlp = load_ner(pack)
    if nlp is None and pack.ner_model:
        print(f"note: spaCy {pack.ner_model} not installed, entity flagging off.")

    records: list[dict] = []
    offset = 0

    for seg in spine.segments:
        n = len(seg.words)
        lo, hi = offset, offset + n
        here = [r for r in regions if r.start < hi and r.end > lo and r.significant]

        flags: list[str] = []
        notes: list[str] = []

        if any(r.dropped for r in here):
            flags.append("dropped-phrase")
        if any(not r.dropped for r in here):
            flags.append("word-mismatch")
        # The strongest signal a multi-engine setup produces: the transcript we
        # are building on is the one the others disagree with.
        if any(r.outvoted for r in here):
            flags.append("outvoted")
        for r in here[:MAX_NOTES]:
            notes.append(r.note(pack))

        conf = confidence_at(transcripts, seg.start, seg.end)
        if conf["low_logprob"]:
            flags.append(f"low-logprob({conf['logprob']:.2f})")
        if conf["low_prob"]:
            flags.append(f"low-confidence({conf['prob']:.2f}×{conf['weak']})")
        if conf["not_speech"]:
            flags.append(f"maybe-not-speech({conf['nospeech']:.2f})")
        for label, engines in (("low-logprob", conf["low_logprob"]),
                               ("low-confidence", conf["low_prob"]),
                               ("maybe-not-speech", conf["not_speech"])):
            if engines and len(transcripts) > 1:
                notes.append(f"{label} reported by: {', '.join(engines)}")

        # Names and numbers are the costliest things to get wrong, but a name
        # every engine agrees on and is confident about needs no review. They
        # escalate an already-uncertain segment; they do not flag on their own.
        uncertain = bool(flags)
        if uncertain and any(c.isdigit() for c in seg.text):
            flags.append("check-number")
        if nlp is not None:
            ents = list(dict.fromkeys(e.text for e in nlp(seg.text).ents))
            if ents and uncertain:
                flags.append("check-name")
                notes.append("entities: " + ", ".join(ents))
            elif ents:
                notes.append("entities (all engines agree): " + ", ".join(ents))

        # Mark the exact words the engines fought over, so the review app can
        # underline the syllable instead of the whole segment.
        disputed = {i - lo for r in here for i in range(max(r.start, lo), min(r.end, hi))}

        records.append({
            "id": len(records),
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "speaker": seg.speaker,
            "text": seg.text.strip(),
            "flags": flags,
            "notes": notes,
            "words": [
                {"w": w.w, "start": round(w.start, 3), "end": round(w.end, 3),
                 "disputed": i in disputed}
                for i, w in enumerate(seg.words)
            ],
        })
        offset += n

    return records


def run(video: str) -> list[dict]:
    transcripts = load_all(video)
    if not transcripts:
        sys.exit(f"no transcripts in work/stt/ for {video!r}, run steps.transcribe first")

    language = cfg("project", "source_lang")
    pack = load_pack(language)
    spine_name = pick_spine(transcripts, cfg("stt", "spine"))

    print(f"engines: {', '.join(sorted(transcripts))}  (spine: {spine_name})")
    if len(transcripts) == 1:
        print("note: single engine, flags come from its own confidence numbers.")
    elif len(transcripts) == 2:
        print("note: two engines, so disagreements have no majority to break "
              "them. A third engine turns most of them into a default answer.")

    records = build_records(video, transcripts, pack, spine_name)

    review_dir = WORK / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    md = review_dir / f"{video}.{language}.md"

    # Corrections made at gate 1 live in this markdown file and nowhere else.
    # Segment structure is identical whenever the engine output is, so text can
    # be carried across position by position. When it is not, the old file is
    # backed up instead of being silently replaced.
    kept = existing_text(md, len(records))
    if kept:
        for rec, text in zip(records, kept):
            rec["text"] = text
        n = sum(1 for t in kept if t)
        print(f"kept {n} corrected segment(s) from {md.name}")
    elif md.exists():
        backup = md.with_suffix(".md.bak")
        backup.write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"WARNING: {md.name} has a different segment count than the new "
              f"transcription, its text cannot be carried over.\n"
              f"         previous version saved as {backup.name}", file=sys.stderr)

    spine = transcripts[spine_name]
    spine.engine = f"{spine_name}+consensus"
    # Through set_text, so that carried-over corrections reach the word list as
    # well as the text. Everything downstream reads the words.
    for seg, rec in zip(spine.segments, records):
        set_text(seg, rec["text"])
    merged = WORK / "stt" / f"{video}.merged.json"
    spine.save(merged)

    md.write_text(render(video, language, records), encoding="utf-8")

    data = review_dir / f"{slug(video)}.review.json"
    data.write_text(json.dumps({
        "video": video, "slug": slug(video), "language": language,
        "engine": spine.engine, "engines": sorted(transcripts),
        "segments": records,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    flagged = sum(1 for r in records if r["flags"])
    total = len(records)
    for p in (merged, md, data):
        print(f"wrote {p.relative_to(ROOT)}")
    print(f"{flagged}/{total} segments flagged for review "
          f"({100 * flagged / total if total else 0:.0f}%)")
    return records


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("video")
    run(ap.parse_args(argv).video)


if __name__ == "__main__":
    main()
