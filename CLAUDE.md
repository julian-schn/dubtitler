# Project conventions

A subtitling pipeline, cloned per job. `README.md` covers running it. This file
covers the things that are not obvious from the code and are expensive to
rediscover.

## Non-obvious things worth knowing before changing anything

**Translate whole sentences, re-cut into cues afterwards.** In a verb-final
language the word fixing a clause's meaning routinely lands in the last cue, so
cue-by-cue translation commits to a reading before the evidence arrives. The
model complies fluently and the error is invisible in the target language.
`steps/sentences.py` and `cues.py` exist to keep the two concerns apart. Any
change that reintroduces cue-by-cue translation breaks the output in a way that
is hard to see and easy to ship.

**Cue timing is derived from the source word stream, never guessed.** A cue
covering the first 40% of a sentence's characters gets the timespan of the
source words covering the first 40% of that sentence. Word order differs between
languages; cue boundaries still land on the audio.

**Correcting a segment's text must also rebuild its word list.** Everything
downstream reads `Segment.words`, not `Segment.text`. Assigning to `.text` alone
lets a gate-1 correction reach the review page, which renders the markdown, and
never reach the subtitles, which are built from the words. A delivered job once
shipped with a cue missing for exactly this reason. Go through
`core.set_text`, which both `steps/diff.py` and `steps/corrections.py` use.

**Confidence flags are filtered on purpose, don't simplify them back.** Two good
engines disagree on roughly 20% of tokens on this material. At that rate, "flag
the segment if any token differs" flags 87% of segments and the review document
becomes unreadable. `consensus.py` therefore:

- compares via the language pack's `canonical()`, which folds number words
  against digits (`fünfzehn` == `15`) and dialect elision (`hab` == `habe`),
  these being formatting and dialect rather than transcription error;
- counts a disagreement only if it drops a phrase or disputes a **content**
  word, judged on the words the engines do not share rather than everything the
  region covers;
- requires a *cluster* of weak words, or one truly weak one, not a single
  below-threshold word in a twelve-word segment;
- treats names and numbers as **escalators**, not triggers: a name every engine
  agrees on needs no review, and flagging every segment containing one buries
  the segments that do.

This is a deliberate recall-for-precision trade. Some genuine differences no
longer flag. That is correct here: a gate nobody reads catches nothing.

**Readings are voted on, confidence is not.** A reading is a claim about what
was said, so a strict majority settles it and the spine being outvoted is the
strongest signal the pipeline produces. A confidence number is an engine
reporting that it struggled, so one engine reporting it is enough. Majority-
voting confidence lets an engine that never reports low numbers permanently veto
the only flag a single-engine job has.

**`str.casefold()` maps ß → ss.** Word lists have to be folded the same way they
are queried, or `Bloß` silently fails to match `bloß`. `Pack.norm_token()` folds
umlauts and ß deliberately, so that engine disagreements about spelling do not
register as transcription disagreements.

**Quotations are quoted, not translated.** If the recording quotes a text with a
standard published translation in the target language, `project/sources.md`
records the edition and the verbatim wording. A re-translation of a familiar
passage reads as wrong to anyone who knows it, however accurate it is.

**The review markdown is a contract with exactly one parser.**
`review.render()` writes it, called by `steps/diff.py` and by the review app on
every autosave; `steps/corrections.py` parses what it produces. Those two
writers once disagreed about a header and the file churned on every save. If a
new surface needs to write this file, route it through `render()` rather than
reimplementing the format. It resolves the language name from the code itself so
callers cannot disagree.

**The review app derives every view from disk at request time.** No precomputed
views, no generated HTML, no build step. That is what makes a new video appear
on the dashboard by refreshing the browser. Audio is the sole cached artifact
(`work/review/audio/`, safe to delete). Resist adding a generate step back.

**`<slug>.review.json` is the structured source of truth for review surfaces.**
`steps/diff.py` derives it and the markdown from one list of records, so the
flags on the page can never disagree with the flags in the file. Anything
needing segment flags, notes or word timings reads that JSON rather than
scraping the markdown.

**Two slugs, and they are not interchangeable.** `core.slug(video)` derives from
the *source filename* and names everything under `work/` plus the app's API
routes; it must stay stable forever, because changing it orphans every
intermediate. `project.release_slug(video)` derives from the *title* and names
only deliverables in `out/`, so a client receives a descriptive filename.
Retitling therefore renames deliverables and nothing else.

**Sentence ids are positions, not identities.** They shift whenever a correction
changes a sentence boundary. `translate --apply` keys on them, which is fine
immediately after `sentences` and wrong after re-running it. Anything storing
translations across a re-run should key them on source text, not on ids.

**The dub never replaces the original, and it is timed off sentences.** The
source stays audible under the voiceover at `dubbing.duck`. That is the archival
convention for testimony, and it is what lets someone who speaks the source
language check the dub against what was said; muxing the dub as the only audio
track throws that away irreversibly. Clips are placed on sentence spans rather
than cue spans for the same reason cues are not translated: a cue is a
reading-speed artefact, and cutting a spoken line at one gives a voice a
fragment no person would utter as a unit.

**A voice speaks a fixed set of languages; a transcription engine does not.**
This is the one place `tts/` diverges from `stt/`. Kokoro, the free local
default, covers eight languages and neither German nor Norwegian is among them,
so a job targeting either has to use ElevenLabs however the rest is configured —
the tool was built for `de -> nb`, which Kokoro cannot serve at all.
`tts.Base.speaks()`
reports that before anything is synthesised, and `steps/dub.py` checks it
*before* `available()`: installing a missing package cannot make an engine speak
a language it has no voices for, so reporting the dependency first sends the
reader to fix the wrong thing.

**Dub fit levers are ordered by what they cost, and overrun is last.** Running
into the following silence is free in audio terms and is still the last resort,
because a clip that runs past its sentence is drifting out of sync with a
speaker who is on camera. Stretching stops at `atempo_max` rather than doing
whatever it takes: past that the artefact is audible, and a sentence listed in
the run's summary for a human to shorten is better than one that ships sounding
wrong. A fourth lever, asking the model to rewrite the line shorter, is
deliberately not implemented — it would put an LLM round-trip in the dub path.

## Pipeline invariants

- Every engine normalises into the `Transcript` schema in `core.py`. Adding one
  means a module in `stt/` that emits that shape and a line in `REGISTRY`.
  `tts/` mirrors that shape for voices, with `speaks()` added.
- Nothing shells out to ffmpeg directly. `core.ffmpeg` and `core.ffprobe` are
  the only call sites, and they raise `FFmpegError` carrying stderr so a step
  can `sys.exit(str(e))` and show the real complaint. Before they existed, one
  caller reported stderr, one let it through to the terminal and one swallowed
  it into a traceback. Long filter graphs go through `ffmpeg(script=...)`, which
  writes them to a file rather than the command line.
- `Word.prob` is a probability in 0..1, never a log-probability. Engines report
  both conventions; a log probability near zero means near-certain while a
  probability near zero means the opposite, so an engine reporting logprobs
  converts in its own module.
- The review document round-trips: `steps/corrections.py` parses the `##`
  headers back into timings. Do not add or remove headers when editing, only the
  text between them.
- `steps/qc.py` is the gate before render. It must exit 0.
- `steps/diff.py` carries existing review text across a re-run and refreshes
  only the flags. It used to overwrite unconditionally, which silently reverted
  every gate-1 correction; corrections live in that file and nowhere else. If
  the segment count differs it backs the old file up rather than guessing.
- Subtitle limits live in `config.toml` and nowhere else. `cues.py` enforces
  them, `steps/qc.py` verifies them, and `steps/guidelines.py` appends them to
  the translation brief rather than letting a model write them.
- The line between `config.toml` and a module constant is whether a *job* would
  reasonably differ. Language pair, engines, models, flag thresholds, subtitle
  limits and dub settings are config. Sentence-splitting heuristics
  (`stt/__init__.py`, `steps/sentences.py`), the review app's audio cache format
  (`webui/data.py` `AUDIO_ARGS`) and prompt batch sizes are constants: changing
  them changes what the tool *is*, not how one job is set up.
- Every section steps read is **flat**, because the merge in `core.config()` is
  one level deep: a nested table in `config.toml` replaces its whole section and
  every key the human did not write disappears. `[models]` is keyed by engine
  name for this reason rather than being a `[stt.models]` sub-table.
  `tests/test_config.py` guards it.
- No step overwrites a human's work silently. `steps/terms.py` preserves filled
  glossary rows, `steps/titles.py` never retitles, `steps/guidelines.py` and
  `steps/langpack_gen.py` refuse to regenerate without `--force`.

## Language packs

Everything language-specific belongs in `lang/<code>.toml`, not in code. If a
step needs a word list, a pronoun set or an abbreviation list, that is a pack
key. The test for whether something belongs there: would it be wrong in another
language?

TOML trap worth knowing: every root-level key must appear **before** the first
`[table]` header. A list written after one silently becomes a member of that
table, the pack loads with empty word lists, and the only symptom is that
flagging goes quiet. `tests/test_langpack.py` guards this.

## Style

- Steps are runnable standalone: `python -m subtitler.steps.<name> VIDEO`.
- Each step prints what it wrote, relative to the repo root.
- Comments explain why, not what. A comment that restates the code is noise; a
  comment recording which alternative was tried and why it failed is the reason
  this file is short.
