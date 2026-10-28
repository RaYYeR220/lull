# Lull engine

The `lull` command turns a prerecorded video into audio description. It finds the pauses in
the soundtrack, writes descriptions that fit them, checks every line against the frames, voices
the lines and publishes the described video, the description tracks and a report. Descriptions
are written and voiced in English only.

## What it needs

- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).
- ffmpeg and ffprobe 7 or newer on `PATH`. The engine refuses an older ffmpeg.
- A NeMo-Speech.cpp server for speech recognition, diarization and text to speech. By default
  the engine looks for it at `http://127.0.0.1:8089`.
- A Nebius Token Factory API key for the Nemotron models. A Tavily API key is optional and
  only used to look names and terms up.

## Install

```sh
cd engine
uv sync
uv run lull --help
```

## Start the speech server

```sh
nemo-speech serve --host 127.0.0.1 --port 8089 \
  --asr-model parakeet-tdt --diar-model nemotron-3-diarization --tts-model magpie \
  --device cuda --read-timeout 300 --write-timeout 300
```

`lull describe` checks the server before it starts and stops with a message if the server is
not answering.

## Settings

The engine reads these from the environment, or from a `.env` file in the current folder.

| Variable | Default | Meaning |
|---|---|---|
| `NEBIUS_API_KEY` | none | Token Factory key (required) |
| `TAVILY_API_KEY` | none | web search for names and terms (optional; without it nothing is looked up on the web) |
| `LULL_SPEECH_URL` | `http://127.0.0.1:8089` | the speech server |
| `LULL_SPEECH_KEY` | none | bearer token for the speech server, if it wants one |
| `LULL_CAP_RUN` | `0.50` | most model spend in USD for one run |
| `LULL_CAP_DAY` | `3.00` | most model spend in USD per UTC day |
| `LULL_CAP_TOTAL` | `35.00` | most model spend in USD ever recorded in the ledger |
| `LULL_TAVILY_RUN` | `5` | most web searches for one run |
| `LULL_TAVILY_DAY` | `60` | most web searches per UTC day |
| `LULL_HOME` | `~/.lull` | where the spend ledger and voice calibrations live |

Caps count model calls only. Each call is checked against its estimate before it is sent, and
a call that would cross a cap is refused. A cap of `0` refuses every call, and `none` switches
a cap off. Web searches are counted rather than priced. When research reaches its search cap
it stops looking things up and the run records `research capped: N searches`.

## Commands

```sh
# describe a video (standard mode; extended.mp4 is built whenever an extended line is voiced)
uv run lull describe talk.mp4 --out runs/talk

# ask the plan for full long versions for the extended track, for a film or cartoon
uv run lull describe film.mp4 --mode extended --profile entertainment --run-id film-1

# measure a voice's speaking rate once (words per second), used to fit lines to pauses
uv run lull calibrate-voice --voice Sofia

# model spend and searches: everything, or one run by role and model
uv run lull ledger
uv run lull ledger --run film-1

# the small set of files the website plays, from a finished run
uv run lull export-showcase runs/film-1 site/film-1 --credit "Blender Foundation (CC BY)"
```

`--profile` is `education` (slides, charts, on-screen text) or `entertainment` (people,
actions, settings). `--source-url` points research at a page about the video, and
`--no-research` turns research off. A rerun into the same run folder reuses every stage that
already finished. Any change to the engine's code, prompts or templates counts as a new
engine and runs every stage again.

## Outputs

`describe` writes into the run folder:

| File | What it is |
|---|---|
| `described.mp4` | the video with a "Described" audio track and the "Original" track |
| `described-audio.m4a` | the programme ducked under the descriptions |
| `extended.mp4` | the extended version, where the picture holds while a description plays |
| `descriptions.vtt` | the standard description track (WebVTT, `Kind: descriptions`) |
| `extended.vtt` | the extended descriptions at the points where the picture holds |
| `script.md` | the description script: times, status, text, claims, findings, notes |
| `cues.json` | every cue, held ones included, with the pauses and the speech spans |
| `report.html` | WCAG 1.2.3, 1.2.5 and 1.2.7 status, coverage, overlap, held cues, F113 pauses, notes for the reviewer, research, model calls and spend |
| `keyframes/` | one frame per cue |
| `manifest.json` | the sha256 and size of every file, the run summary and the versions |

Publishing is all or nothing. It stops before any file is written if a voiced line would
overlap speech or another line. A line the frames do not support is held for a person to
review and is never voiced. A WCAG criterion reads "met" only after a person has reviewed
every published line. Until then it reads "met (automated checks)" or "needs review".

## Known limitations of verification

Every voiced line is checked against the frames, but the checks have limits. Where a check
cannot decide, the line is held for a person rather than voiced.

- **Claims are built from the line's own words.** A line is split into small claims, each tied
  to the exact words it comes from, and each claim may use only those words. Hyphenated
  words and numbers count as several words ("red-jacketed", "004.77"), so the split often
  needs a second try. When it fails twice, the line is checked one whole sentence at a time.
- **Whole sentences are judged by one model.** Besides the small claims, every sentence is
  judged as written. That is what catches a detail attached to the wrong person ("the man in
  the red coat" when the red coat is someone else's) or a lost "not". It works only as well
  as the model reads the sentence. The judge always gives the same answer for the same
  frames, so asking again cannot turn a refusal into a pass, but a wrong reading stays wrong.
- **A sentence ends at a full stop followed by a space.** "Mr. Smith waves." counts as two
  sentences, so that line is judged in pieces and is usually held. A number such as "004.77"
  stays whole.
- **Some safe cuts are refused.** When a claim fails, the engine removes its words itself. It
  refuses a cut that would change what the remaining words say: it never removes "not",
  "no" or "without" on their own, a word that describes the next one ("toy gun" to "gun",
  "nearly falls" to "falls", "a red door" to "a door"), a preposition before words that stay,
  or part of a number or hyphenated word. These rules look at words and their order, not at
  grammar, so a cut that would have been fine is sometimes refused, and the line is held.
- **The grammar fix after a cut may only leave out whole sentences.** It cannot add, move or
  change a word. When it leaves a sentence out, the cue's note says so ("sentence dropped:
  ..."). A dropped sentence may have described an event that then goes undescribed.
- **Names and pronouns come from the words they describe.** A claim may say "she" or a name
  only where its own words do. The check does not work out who "she" is; the whole-sentence
  judgement is what sees that.
- **No verdict means no voice.** When the judge gives no verdict for a claim or a sentence,
  or its answer is cut off, the line is held as it is. This costs lines, not accuracy.
- **Only the clip around the line is seen.** Each line is judged against the frames around
  its events. Something that is visible elsewhere in the video counts as not visible.
- **Words that only place the line in time are not checked.** "In a moment", "now" and
  "then" are left out of the claims.

## Models and voices

| Model | Used for | Licence |
|---|---|---|
| `nvidia/Nemotron-3.5-Super-VL` (Token Factory) | watching the video, checking each line against the frames | the provider's terms |
| `nvidia/Nemotron-3-Ultra-550b-a55b` (Token Factory) | planning the descriptions | the provider's terms |
| `nvidia/Nemotron-3_5-Lightning` (Token Factory) | research terms, splitting lines into claims, repairs, grammar after a cut, compression | the provider's terms |
| Parakeet TDT (speech server) | speech recognition | CC-BY-4.0 |
| `nemotron-3-diarization` (speech server) | who speaks when | see its model card |
| Magpie TTS (speech server) | the voice of the descriptions | NVIDIA Open Model License |

The descriptions are synthetic speech. Every report and script says so, and the described
audio track carries the disclosure in its metadata.

## Tests

```sh
uv run pytest -m "not live"               # offline: fakes for the models and the speech server
LULL_LIVE=1 uv run pytest -m live         # calls the real Token Factory API and costs money
uv run ruff check src tests && uv run ruff format --check src tests
```

## Development clips

Description quality is developed and measured only on Tears of Steel (Blender Foundation, CC BY
3.0; no reference description, in no evaluation suite) and on clips made for the tests. The
evaluation's videos and their references are never used to develop or tune the engine.

## Evaluation switches

`LULL_NO_VERIFY=1` (an environment variable, not a command-line flag) is for the evaluation's
ablation only. Finalize then checks no claim against the frames and voices the lint-clean,
fitted line with status `unverified`. The manifest records `verification: "disabled"` and no
WCAG row can read "met"; each lists that claim verification was off. Never publish such a run
as a described version.
