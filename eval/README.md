# Lull evaluation

Every number Lull publishes about itself comes from a script in this folder that anyone can
re-run. Results carry the git commit, the model ids, the date and the sha256 of an answer key
that was committed before the scorer existed.

`eval/` is its own uv project (`pyproject.toml`, `uv.lock`); it installs the engine from
`../engine` in editable mode and adds matplotlib. Run everything from `eval/`.

## Suite A: planted facts, hidden answer key

Twelve short synthetic lectures (biology, economics, physics, history, statistics, chemistry,
geography, computer science, art history, medicine, climate, engineering; 3-4 slides, two
charts each, 8.8 minutes in all) whose narration deliberately leaves facts on the slides
unsaid. The question: does Lull describe what a blind student would miss, without inventing
anything, without repeating the lecturer, and without ever talking over speech, and at what
cost and speed.

**Pre-registration.** Commit `194d4d0803aa4a9195abbb450b35d7d132631ae1`
("eval: suite A answer key") holds the specs, the renderer, `suite_a/key.json` and
`suite_a/key.sha256` (`354967ad20310ab6013c5fd884520f57120dbaa56d81b927f2d0c3ec35b7ce96`). The
scorer (`suite_a/score.py`) was written and committed after it; it refuses to run if
`key.json` no longer hashes to `key.sha256` or if a rendered video is not the one the key was
built from. The metric rules the scorer follows are written in `key.json["rules"]`, in the
pre-registration commit.

"Hidden" means hidden from the engine: the pipeline is given `video.mp4` and nothing else (no
file name hint, no source URL, research off).

### How the lectures were made

1. `python -m suite_a.specs` asks Nemotron 3.5 Lightning (thinking off, temperature 0.7,
   strict JSON schema, a fixed seed per subject) for the slides, charts and four absent
   decoys of each lecture, then for the narration, telling it which one bullet per slide to
   say and which words and numbers never to say. The schema fixes the structure (slide count,
   which slides have a chart and of which type, item counts). A deterministic validator
   rejects any answer that breaks a rule (a chart value said in digits or words, the chosen
   bullet not said, fewer than two unsaid facts on a slide, a decoy that is on screen ...);
   rejected attempts are retried with the reasons and logged in
   `suite_a/specs/generation_log.json`. Pauses before each sentence (1.5-4.0 s) are drawn from
   `random.Random(seed)`, not chosen by the model.
2. The facts are not the model's opinion: `derive_facts` reads them off each slide by rule.
   A title, bullet, chart title or axis label is a **visual fact** when the slide's narration
   shares less than half of its content words and never says it as a phrase; a **spoken
   fact** when it shares at least 60 % (or says it as a phrase); in between it is "partly
   said" and not scored. Each chart adds its highest value; each line chart its direction.
   `python -m suite_a.specs --check` re-validates and re-derives every committed spec.
3. `python -m suite_a.render` draws the slides (matplotlib, 1280x720, one fixed style, the
   bundled DejaVu Sans), voices each sentence with NVIDIA Magpie TTS (voice "Aria"; Lull
   describes in "Sofia") through the local speech server, trims the TTS silence, lays out the
   timeline in samples (slide changes on frame boundaries, the planned pause before every
   sentence) and encodes H.264/AAC with ffmpeg, writing `out/<id>/video.mp4` and
   `timeline.json`. Then it writes `key.json` (per video: each slide's full inventory and time
   range, the narration spans, 235 visual facts, 51 spoken facts, 48 decoys) and
   `key.sha256`.

### What is measured (from `key.json["rules"]`)

| metric | how |
|---|---|
| overlap with narration | seconds of published standard-cue audio inside the narration spans of `timeline.json`; must be 0. Extended cues: freeze points strictly inside speech (must be 0) |
| capacity | standard cues whose audio leaves their pause's usable window |
| visual-fact recall | per fact, the judge names the published cues that convey its specific content; standard track alone and with the extended track; also timely (the cue starts while the slide is up) and by kind |
| exact match | no model: the slide title as a phrase in a cue; a chart value (digits or words) with its category in one cue |
| hallucination | per claim of a published cue (the run's own claim split), supported by the key or not; also per cue |
| decoys | absent decoys any cue states (judge), and decoy-only numbers appearing in a cue (deterministic) |
| redundancy | published cues that only restate the narration; spoken facts restated by any cue |
| cost and speed | ledger spend of the run's id (model and Tavily apart) per video minute; wall-clock seconds of `lull.pipeline.run` per video minute; verified / held / extended counts |

Aggregates are pooled over videos with 95 % percentile bootstrap intervals (10 000 resamples,
seed 20261028). The judge is Nemotron 3 Ultra (temperature 0, reasoning on, strict JSON), two
calls per video; it never sees the engine's own verdicts. Its answers are cached by their
exact request under `out/judge/`, so a report is rebuilt without new calls and no video is
judged twice. A judge probe scores synthetic cues made from the key itself (each visual fact
and each decoy stated once) on a01, a05 and a09: a sanity check of the judge, reported next to
the results.

### Reproduce

```sh
cd eval
uv sync
# keys: NEBIUS_API_KEY in the environment; a speech server at LULL_SPEECH_URL
# (default http://127.0.0.1:8089)
uv run python -m suite_a.specs --check          # the committed specs still validate
uv run python -m suite_a.render                 # re-render (uses out/tts/ when present)
uv run pytest -q                                # unit tests, no network
LULL_CAP_RUN=0.30 uv run python -m suite_a.score --all   # 12 runs + judge -> results/
```

`--videos a01,a02` scores a subset into `out/partial/` (a smoke, never `results/`);
`--reuse-runs` judges the latest finished runs again (from the cache when unchanged); `--cap`
limits what one invocation may spend (default $3.00).

The videos are not committed (`out/` is ignored). Magpie TTS samples: the same sentence comes
back slightly different from one server call to the next (seed and temperature parameters did
not change that in our checks), so a render from an empty TTS cache gives other durations and
another key. The renderer caches every TTS answer; with the published tarball of `out/`
(videos, timelines, slides, TTS cache) every video re-renders bit for bit, which we checked.
Lightning likewise did not reproduce an answer for a fixed seed, so the committed specs, not
a regeneration, are the source of truth.

### Known limitations

- The judge is a model. It is strict by instruction, temperature 0 and probed on known cues,
  but recall, hallucination, decoys and redundancy are its verdicts; overlap, capacity and
  the exact matches are not.
- Synthetic slides are easier than real lectures: clean text, one fixed style, no presenter,
  no handwriting, no motion. Read these numbers as an upper bound for slide lectures.
- The lectures are short (35-55 s) and dense (15-25 visual facts each) with pauses of only
  1.5-4.0 s, so the standard track can hold a small share of the facts by design; the
  extended track (the video pauses) is reported separately.
- Content was generated: it is textbook-level but not reviewed by subject experts, and some
  charts are illustrative rather than real data.
- Word-overlap rules decide what counts as said. They are strict about unsaid facts (less
  than half the content words) but a paraphrase with other words could still slip through;
  the spec log and `--check` make every decision auditable.
- Twelve videos give wide intervals; the bootstrap is over videos, not facts.
