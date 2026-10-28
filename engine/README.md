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

## Agents (MCP)

Lull is also an MCP server, so an AI agent (Claude Desktop, any MCP client) can ask it to
describe a video and fetch the result. There are two servers.

| Tool | What it does |
|---|---|
| `list_samples` | the sample clips: id, title, duration, credit, licence |
| `describe_video` | describe a sample (`sample_id`) or, locally, a file (`path`), in `standard` or `extended` mode. It sends progress per stage (seconds, model calls and spend so far), then returns the run id, status, cue counts, coverage, the WCAG 1.2.3/1.2.5/1.2.7 rows, model spend and searches |
| `get_tracks` | a finished run's cues as text with timecodes and status, plus the URLs (hosted) or paths (local) of `descriptions.vtt`, `extended.vtt`, `described-audio.m4a` and `described.mp4` |
| `get_report` | a finished run's conformance report as its manifest says it: the WCAG rows with what blocks each, held lines with their reasons, coverage, spend |
| `review_queue` | local only: runs with lines held for a person, newest first |

**Local, over stdio** (`uv run lull mcp`). It runs on your machine with your keys and your
spend caps. `describe_video` also takes a path to a video file (mp4, mov, webm or mkv; it must
exist and be a regular file). Runs go under `LULL_HOME/runs`, and a run already made is reused
from its cache. The speech server must be running (see above). To use it from Claude Desktop,
put this in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lull": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/lull/engine", "lull", "mcp"],
      "env": {
        "NEBIUS_API_KEY": "your Token Factory key",
        "TAVILY_API_KEY": "your Tavily key"
      }
    }
  }
}
```

**Hosted, over streamable HTTP** (`https://<the API>/mcp`). The hosted server describes the
licensed samples only. It never fetches a URL, reads a path or takes an upload: a fetch from
the cloud host could reach its metadata server and internal addresses. A tool call carrying
an argument the tool does not declare (`url`, `path`, `file`...) is refused, not ignored. The
hosted server has the website's limits: one description at a time (`busy`), each visitor's
start limits (`rate_limited`), and the demo's spend caps (`budget`). Refusals come back as tool
errors that start with those codes.

A hosted `describe_video` also returns an `edit_token`, which owns the run: send it as
`Authorization: Bearer <token>` to edit the run in the Studio, and give it only to the person
who should. Nothing logs it. In a client that takes a URL (Claude Desktop: Settings,
Connectors, add a custom connector), give the address; in one that reads JSON:

```json
{ "mcpServers": { "lull": { "type": "http", "url": "https://<the API>/mcp" } } }
```

A description takes a few minutes. Leaving the call (closing the client) stops the run; its
finished stages stay cached.

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

## Models and voices

| Model | Used for | Licence |
|---|---|---|
| `nvidia/Nemotron-3.5-Super-VL` (Token Factory) | watching the video, checking each line against the frames | the provider's terms |
| `nvidia/Nemotron-3-Ultra-550b-a55b` (Token Factory) | planning the descriptions | the provider's terms |
| `nvidia/Nemotron-3_5-Lightning` (Token Factory) | research terms, repairs, cuts, compression | the provider's terms |
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
