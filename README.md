# subtitler

An agent-powered pipeline that turns a recording of someone speaking into
**subtitles and a dub** in another language, with two places where a human looks
at the result and can change it.

Built for testimony and speech — the kind of material where the speaker is not
reading from a script: dialect, false starts, half-finished sentences, names no
speech engine has heard before. It is a template, not a library. Clone it per
job; the job's data and the tool live in the same directory.

```
video ──► audio ──► N speech engines ──► gate 1: fix the transcript
                                              │
                                              ▼
                                          sentences ──► translate
                                              │
                                    gate 2: read the translation
                                              │
                        ┌─────────────────────┴─────────────────────┐
                        ▼                                           ▼
                  cues ──► subtitles                        voices ──► dub
```

## Agent-powered, in a specific sense

Every step that needs judgement — translation, the glossary, titles, the
translation brief, language packs — goes through **one interface with two
interchangeable paths**, and the prompt text is byte-identical down both.

- **With `ANTHROPIC_API_KEY`,** a job runs unattended.
- **Without it,** the step writes `work/prompts/<key>.prompt.md` and stops. A
  coding agent, or a person, answers it and drops the reply beside it as
  `<key>.answer.txt`. The same command run again picks up where it left off.

The second path is not a degraded mode — it is how this tool was actually used
in production. It has one property worth keeping: the briefing and the reply
both sit on disk in a form you can read, diff and correct, which is exactly what
you want for the step that decides what the subtitles say.

`CLAUDE.md` ships with the repository as instructions for an agent working on
the tool itself. It records the decisions that are expensive to rediscover and
easy to undo by accident.

Nothing consults a model at *runtime* for flagging or timing. Those are
deterministic, so a re-run produces the same review document.

## Two ideas do most of the work

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

The dub inherits both: clips are placed on sentence spans, so a spoken line
arrives with the speech it was translated from.

## Getting started

You need **Python 3.11 or newer**, [uv](https://docs.astral.sh/uv/), and
**ffmpeg on `PATH`**. ffmpeg is not optional: it extracts the audio, burns the
subtitles, synthesises the dub and feeds the review player, so the first step of
the pipeline fails without it.

```bash
brew install ffmpeg        # macOS
sudo apt install ffmpeg    # Debian/Ubuntu
```

```bash
git clone <this repo> my-job && cd my-job
make new-job                    # clear the previous job's data
uv sync --extra whisper-local   # plus an extra per cloud engine you want
cp .env.example .env            # fill in the keys you actually use
```

Edit `config.toml`: the project name, the language pair, which engines to run.
Drop the source videos in `media/`. Then:

```bash
make all V=IMG_1234
```

It runs until it reaches something only a person can supply, and says what.

## The two gates

```bash
make review        # http://127.0.0.1:8731
```

**Gate 1, the transcript.** Flagged segments, each with its audio, the words the
engines disagreed on underlined. Fix what is wrong and tick it off. Edits save
straight into `work/review/<video>.<lang>.md`, a plain markdown file if you would
rather work there.

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
ordinary file and nothing consults a model at runtime, so the flags are the same
on every run.

A pack holds what the pipeline needs to know about a language: function words,
negations, line-break preferences, number words, abbreviations, polite address,
speaking rate. All of it is the kind of thing a model produces well and a human
verifies quickly.

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

## Voices and dubbing

```bash
make dub V=IMG_1234
```

| engine | cost | languages |
|---|---|---|
| `kokoro` | free, runs locally | en, es, fr, hi, it, ja, pt, zh |
| `elevenlabs` | paid | ~30, including Norwegian and German |
| `rehearsal` | free, offline | any — a placeholder tone, not speech |

`kokoro` is the default. It downloads two model files once into
`~/.cache/subtitler/models`, shared by every clone, and prints the commands if
they are missing. It handles the `de → en` pair this ships configured for.
**It cannot speak German or Norwegian**, though, so a job targeting either needs
`elevenlabs`; the step refuses at startup rather than part way through, and
names the alternatives. `rehearsal` speaks nothing at all: it
emits a tone of roughly the right length, so timing, ducking and muxing can be
checked end to end before spending anything.

Voiceover, not replacement. The original stays audible underneath, which is the
archival convention for testimony and the only way a listener who speaks the
source language can check the dub against what was said.

When a translation runs long for its timespan, three levers are pulled in order:
the engine's own rate control, then time-stretching, then overrun into the
following silence. Anything that still does not fit is listed at the end of the
run for a human to shorten — the pipeline will not stretch past the point where
the artefact becomes audible.

## Output

```
out/<title>.<lang>.srt            sidecar
out/<title>.<lang>.softsubs.mp4   muxed in, toggleable, nothing re-encoded
out/<title>.<lang>.burned.mp4     burned in, plays anywhere
out/<title>.<lang>.dub.mp4        video copied, dub over the ducked original
out/<title>.<lang>.dub.m4a        the dub mix on its own
```

The soft-muxed file is usually the one to send. Deliverables are named from the
title in `project/videos.md`, not from the camera filename, so retitling renames
what the client receives and orphans nothing.

## Configuration

Everything job-shaped lives in `config.toml`:

| section | what |
|---|---|
| `[project]` | name, source and target language |
| `[stt]` | which engines run, which one anchors the timings |
| `[models]` | pin an engine to a specific model id |
| `[audio]` | loudness normalisation for the audio the engines see |
| `[flags]` | what earns a segment a flag at gate 1 |
| `[llm]` | API or agent prompts, and which model |
| `[subtitles]` | line length, reading speed, cue durations |
| `[render]` | font, box opacity, burn-in quality |
| `[dubbing]` | voice, duck level, fit limits, spend guard |

Anything language-specific lives in `lang/<code>.toml` instead. API keys live in
`.env`. Sentence-splitting heuristics and prompt batch sizes are module
constants on purpose — changing them changes what the tool is, not how one job
is set up.

## Tests

```bash
make test     # everything
make check    # the end-to-end run: synthesis through to a muxed file
```

`make check` runs `tests/test_dub.py`, the only test that drives the real
binaries: it builds a video, synthesises over it, ducks, mixes and muxes. It
needs `ffmpeg` on `PATH` and nothing else — no key, no model, no network.

CI runs the whole suite on Ubuntu across 3.11 to 3.13 and on macOS, and also
runs one documented `python -m subtitler.steps.<name>` command. That last check
exists because pytest puts `src/` on the path itself, so the suite can pass
while every command in this README fails on a broken editable install.

## Keeping a clone up to date

```bash
git remote add upstream <this repo>
git fetch upstream && git merge upstream/main
```

The tool lives entirely in `src/`, `lang/` and `tests/`; job data lives in
`media/`, `work/`, `out/` and `project/`. Merges stay clean because the two do
not overlap.

## Status

Honest about what has and has not been run:

- **Subtitling** — used in production on a real job, start to finish.
- **Dubbing** — `rehearsal` and `kokoro` verified end to end. `elevenlabs` is
  wired and follows the documented API, but has not been executed against the
  live service.
- **No licence yet.** That means all rights are reserved and you do not have
  permission to use, modify or redistribute this. If you want to, ask.
