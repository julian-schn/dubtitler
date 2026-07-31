# subtitler

Turns a recording of someone speaking into subtitles in another language, with
two places where a human looks at the result and can change it.

Built for testimony and speech, the kind of material where the speaker is not
reading from a script: dialect, false starts, half-finished sentences, names
that no speech engine has heard before. It is a template, not a library. Clone
it per job; the job's data and the tool live in the same directory.

## What it does

```
video  →  audio  →  N speech engines  →  gate 1: fix the transcript
                                              ↓
   subtitles  ←  cues  ←  gate 2: read the translation  ←  translate
```

Two ideas do most of the work.

**More than one speech engine.** A single engine cannot tell you where it is
wrong. Two disagreeing marks the spots worth a human's attention; three or more
turn that into a vote, where the odd one out is usually the error. The flags are
filtered hard, because a review document that flags everything gets skimmed
instead of read.

**Translate sentences, then cut them into cues.** Cues are cut to fit a screen;
sentences are cut by grammar. In a verb-final language the word that fixes a
clause's meaning routinely lands in the last cue of that clause, so translating
cue by cue asks the model to commit before it has seen the evidence. It will
comply, fluently, and the error is invisible in the target language. Timing
survives the round trip because each cue takes the timespan of the source words
covering the same fraction of the sentence.

## Getting started

```bash
git clone <this repo> my-job && cd my-job
make new-job                    # clear the previous job's data
uv sync --extra whisper-local   # plus an extra per cloud engine you want
```

Edit `config.toml`: the project name, the language pair, which engines to run.
Drop the source videos in `media/`. Then:

```bash
make all V=IMG_2891
```

It runs until it reaches something only a person can supply, and says what.

## The two gates

```bash
make review        # http://127.0.0.1:8731
```

**Gate 1, the transcript.** Flagged segments, each with its audio, the words the
engines disagreed on underlined. Fix what is wrong and tick it off. Edits save
straight into `work/review/<video>.<lang>.md`, which is a plain markdown file if
you would rather work there.

**Gate 2, the translation.** Source and translation side by side, with the
back-translation diffed against the source so the words that changed meaning are
the ones highlighted. Flags here are deliberately few: only losses that can be
*wrong* rather than merely different.

Between them, `make terms` collects glossary candidates and the glossary view
approves them, so a recurring name is rendered the same way in every video.

## Languages

Any pair. German, Norwegian Bokmål and English ship verified in `lang/`. For
anything else:

```bash
make langpack LANG_CODE=es
```

An LLM writes the pack, you read it, you commit it. From then on it is an
ordinary file, and nothing consults a model at runtime, so the flags are the
same on every run.

A pack holds what the pipeline needs to know about a language: function words,
negations, line-break preferences, number words, abbreviations, polite address.
All of it is the kind of thing a model produces well and a human verifies
quickly.

## Speech engines

| engine | cost | confidence numbers | notes |
|---|---|---|---|
| `whisper-local` | free | per word and per segment | MLX on Apple silicon, faster-whisper elsewhere |
| `elevenlabs` | paid | none | the tightest word timings, so the best spine |
| `deepgram` | paid | per word | architecturally unrelated to Whisper |
| `assemblyai` | paid | per word | narrower language coverage |
| `openai` | paid | per segment | Whisper again, so a poor third opinion |

Set them in `config.toml`. A vote is only worth something when the voters fail
independently, so two Whisper variants agreeing tells you very little.

Adding one is a module in `src/subtitler/stt/` that returns a `Transcript`.

## The model

Anything that needs judgement — translation, the glossary, titles, the
translation brief, language packs — goes through one interface with two
implementations. With `ANTHROPIC_API_KEY` set, a job runs unattended. Without
it, the step writes its prompt to `work/prompts/` and stops; an agent or a
person answers it, saves the reply alongside, and the same command continues.

Both paths send identical prompt text, so a job's quality never depends on which
route it took.

## Output

```
out/<title>.<lang>.srt            sidecar
out/<title>.<lang>.softsubs.mp4   muxed in, toggleable, nothing re-encoded
out/<title>.<lang>.burned.mp4     burned in, plays anywhere
```

The soft-muxed file is usually the one to send. Deliverables are named from the
title in `project/videos.md`, not from the camera filename, so retitling renames
what the client receives and orphans nothing.

## Keeping a clone up to date

```bash
git remote add upstream <this repo>
git fetch upstream && git merge upstream/main
```

The tool lives entirely in `src/`, `lang/` and `tests/`; job data lives in
`media/`, `work/`, `out/` and `project/`. Merges stay clean because the two do
not overlap.

## Tests

```bash
make test     # everything
make check    # the end-to-end run over the reference job
```

`make check` runs the real steps over a real job, transcripts to subtitles, and
compares the result with what was delivered. It is the regression net for cue
timing and confidence flagging, the two things that break silently: a cue 200 ms
short looks fine in the file and clips on screen.
