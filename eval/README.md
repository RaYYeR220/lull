# Lull evaluation

Every number Lull publishes about itself comes from a script in this folder that anyone can
re-run. Results carry the git commit, the model ids, the date and the sha256 of an answer key
that was pinned before the scorer existed.

`eval/` is its own uv project (`pyproject.toml`, `uv.lock`); it installs the engine from
`../engine` in editable mode and adds matplotlib. Commands below run from the repository root
with `uv run --directory eval ...` (or from `eval/` without `--directory eval`).

## Suite A: planted facts, hidden answer key

Twelve short synthetic lectures (biology, economics, physics, history, statistics, chemistry,
geography, computer science, art history, medicine, climate, engineering; 3-4 slides, two
charts each, 8.8 minutes in all) whose narration leaves facts on the slides unsaid. The
question: does Lull describe what a blind student would miss, without inventing anything,
without repeating the lecturer, and without ever talking over speech, and at what cost and
speed.

### Pre-registration (verifiable)

The answer key was committed before the scorer was committed and before any pipeline or judge
call — see the spend ledger.

- Annotated tag **`suite-a-prereg`** -> commit `194d4d0803aa4a9195abbb450b35d7d132631ae1`
  ("eval: suite A answer key", 2026-10-28 01:12:13 UTC) holds the specs, the generator, the
  renderer, `suite_a/key.json` and `suite_a/key.sha256`
  (`354967ad20310ab6013c5fd884520f57120dbaa56d81b927f2d0c3ec35b7ce96`). The tag is published
  with the public repository.
- The scorer was first committed at 01:17:16 UTC (`5551575`); the first pipeline call of Suite
  A is in the ledger at 01:17:50 UTC. Only the lecture generation (`suite-a-specs`, 00:42-00:57
  UTC) precedes the key, as it must. Every results file lists every `suite-a-*` run id found in
  the ledger and `out/runs/` with its first call time, outcome, engine fingerprint and spend,
  and states whether all referenced calls came after the tagged commit.
- The scorer refuses to run unless the tag exists, is an ancestor of HEAD, its
  `key.sha256` equals the hash of the `key.json` being loaded, and the key, the specs and
  `specs.py`, `render.py`, `style.py`, `text.py` are unchanged since the tag (`git diff`). It
  refuses to write `results/` while `eval/` or `engine/` has uncommitted changes. Main is
  brought into this branch by merge, never by rebase, so the tagged commit stays an ancestor
  of the scored one.
- The metric rules are in `key.json["rules"]`, in the tagged commit. Everything added later is
  listed under "Supplementary" below and labelled so in every report.

"Hidden" means hidden from the engine: the pipeline is given `video.mp4` and nothing else (no
file name hint, no source URL, research off).

**Which run counts.** The first complete `--all` invocation on the final engine (one engine
fingerprint) counts. Once it exists, `--all` refuses to start new pipeline runs on that
fingerprint; `--all --reuse-runs` rebuilds that invocation's report from its runs and the
judge cache at zero spend. Runs on different engine fingerprints are never mixed. A failed run
stays failed (its facts count as missed).

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

### Room to describe

Half of the 530.6 s of video has no narration: 264.8 s of silence in 96 pauses, 84 of them at
least 2 s long (the lead-in and the 2 s tail included).

| lecture | subject | s | silence s | pauses | pauses >= 2 s | visual facts | spoken facts |
|---|---|---|---|---|---|---|---|
| a01 | biology | 48.6 | 25.6 | 9 | 7 | 24 | 4 |
| a02 | economics | 40.4 | 19.9 | 7 | 6 | 17 | 6 |
| a03 | physics | 47.9 | 24.4 | 9 | 8 | 17 | 4 |
| a04 | history | 43.7 | 19.6 | 7 | 6 | 21 | 3 |
| a05 | statistics | 42.9 | 21.4 | 9 | 6 | 19 | 7 |
| a06 | chemistry | 37.0 | 20.5 | 7 | 7 | 20 | 3 |
| a07 | geography | 54.9 | 27.0 | 9 | 8 | 23 | 5 |
| a08 | computer science | 41.3 | 19.9 | 7 | 7 | 19 | 3 |
| a09 | art history | 54.4 | 28.0 | 9 | 9 | 22 | 5 |
| a10 | medicine | 35.7 | 18.0 | 7 | 6 | 18 | 3 |
| a11 | climate | 43.4 | 22.2 | 9 | 8 | 21 | 4 |
| a12 | engineering | 40.4 | 18.3 | 7 | 6 | 14 | 4 |

The same columns appear next to recall for every video in `results/SUITE_A.md`.

### What is measured (pre-registered, from `key.json["rules"]`)

| metric | how |
|---|---|
| overlap with narration | seconds of published standard-cue audio inside the narration spans of `timeline.json`; must be 0. Extended cues: freeze points strictly inside speech (must be 0) |
| capacity | standard cues whose audio leaves their pause's usable window |
| visual-fact recall | per fact, the judge names the published cues that convey its specific content; standard track alone and with the extended track; also timely (the cue starts while the slide is up) and by kind |
| exact match | no model: the slide title as a phrase in a cue; a chart value (digits or words) with its category in one cue |
| hallucination | per claim of a published cue (the run's own claim split in `cues.json`), supported by the key or not; also per cue. Content only: a true description at the wrong time is not a hallucination; timing errors show only in timely recall |
| decoys | absent decoys any cue states (judge), and decoy-only numbers appearing in a cue (deterministic) |
| redundancy | published cues that only restate the narration; spoken facts restated by any cue |
| cost and speed | ledger spend of the run's id (model and Tavily apart) per video minute; wall-clock seconds of `lull.pipeline.run` per video minute (the hardware is recorded in the results: CPU, GPU, OS; models run remotely, speech locally); verified / held / extended counts |

Aggregates are pooled over videos with 95 % percentile bootstrap intervals (10 000 resamples,
seed 20261028).

**The judge.** Nemotron 3 Ultra (temperature 0, reasoning on, strict JSON whose enums and item
counts force one verdict per item), two calls per video; it never sees the engine's own
verdicts. Ultra is also the engine's planner (it writes the descriptions' first drafts), so a
self-preference bias is possible; the second judge below is there to bound it. Answers are
cached by their exact request under `out/judge/`, with the run id that paid for them, so a
report is rebuilt without new calls and no video is judged twice.

**Rules applied by the scorer that the key does not spell out** (decided before the scored
run, disclosed here): a verdict the judge leaves out counts against the system (fact not
conveyed, claim unsupported, cue redundant, decoy stated, a cue without a voiced-text answer
one unsupported statement) and is listed
under "judge problems"; extended cues count only when the run reports
`extended_produced`; a08's line chart shows its highest value (100000) at three categories
and the key names the first, so any of the three is accepted, in the exact match and in the
judge's instructions.

### Supplementary (added after the 2026-10-28 smoke; not pre-registered)

Labelled "supplementary" in every report:

- **Hallucination, voiced text**: a separate judge call per cue that sees only the cue's
  final voiced text, the visual and spoken facts of the slide or slides on screen while it
  plays (for an extended cue, the slide at its freeze point) and the decoys; never the run's
  claim split. The judge splits the text into the statements a listener is told and calls each
  supported, unsupported or unverifiable by those facts. Metric: unsupported statements /
  voiced statements; also reported: unverifiable statements and cues with an unsupported
  statement. Because it judges against what is on screen at that moment, a correct
  description of another slide counts as unsupported here: unlike the pre-registered metric,
  this one includes timing. Added because the smoke showed that the run's claim split can
  include claims the voiced text never says (a four-word title cue carried five claims about
  its slide's bullets), so the pre-registered claim rate is computed partly on unvoiced
  claims; a first version that asked for this verdict inside the claims call was dropped
  (the claim list leaked into it, and the claims call is kept exactly as pre-registered). Also
  reported: the share of claims the cue text does not say (word overlap, no model) and the
  hallucination rate over the claims it does say.
- **Decoys, absent part only**: a decoy counts only when a crediting cue says the part of the
  decoy that is absent from the video (its absent number, or at least half of its words that
  occur nowhere in the video), so a cue that correctly names a category a decoy also names is
  not counted. The pre-registered decoy rule stays primary.
- **Second judge**: Qwen3 235B A22B Instruct 2507 (another model family on Nebius Token
  Factory; no reasoning, temperature 0) makes the same calls (coverage, claims, voiced text);
  reported: its recall, hallucination and voiced-text hallucination, and its agreement with
  Ultra (raw agreement and Cohen's kappa) per visual fact, per claim and per cue (any
  unsupported voiced statement).
- **Judge probe specificity**: the pre-registered probe (synthetic cues stating each visual
  fact and each decoy, on a01, a05, a09) gains near-miss negatives that must not be credited:
  a wrong highest value, a title with one word swapped, a gist that only names a chart or a
  list, and a bullet with its second half dropped; cross-credits (a fact credited to another
  fact's cue, a decoy to a fact cue) are counted from the same verdicts. The voiced-text call
  gets its own probe on the same videos, five cues on the first chart slide: two true (the
  highest value, a bullet) that must be all supported, and three near misses (a wrong number,
  the value given to another category, an invented bullet) that must draw an unsupported
  statement (the invented bullet may also be unverifiable). Both judges answer it.

### Smoke (a01 and a02 on the pre-fix engine; not results)

Run before the engine's fix round, two videos, reported only to show the pipeline end to end:
overlap 0.000 s; recall 3/41 standard, 9/41 with extended; 0/27 claims unsupported; 0/8 decoys;
0/7 cues redundant; $0.0238 per minute (Tavily $0); 87.7 s of processing per minute (AMD Ryzen
5 5600X, RTX 4060). Second-judge calibration on the same runs: 92.7 % agreement on the 41
visual facts (kappa 0.81; Qwen credited 12, Ultra 9), 100 % on the 27 claims (kappa undefined:
both judged every claim supported). Probe on a01: 24/24 facts and 4/4 decoys credited to their
own cue, 15/15 near misses not credited, 6/6 near-miss claims unsupported, no cross-credits.
Voiced text: 10/14 statements unsupported with Ultra (7/12 with Qwen), 0 unverifiable, 3/7
cues with an unsupported statement for both judges (kappa 1.00 on the 7 cues); all of them
are the three extended cues, whose freeze point lands on the previous slide, so they describe
a slide that is not on screen (the four standard cues, slide titles, are all supported).
Voiced-text probe on a01: sensitivity 2/2 and specificity 3/3 for both judges.

What the smoke found in the engine (reported to its fix round): the listen stage widened the
narration spans by 0.2-0.6 s and merged sentences across planned pauses of about 1.9 s, so
the standard track had only 4-5 windows per lecture and voiced slide titles only; extended
cues froze the picture before their slide appeared (the visual's start fell inside a widened
speech span and the freeze moved back to its start, 3+ s early); compressed cues kept claims
their text no longer says.

### Reproduce

```sh
uv sync --directory eval
# NEBIUS_API_KEY in the environment; a speech server at LULL_SPEECH_URL (default
# http://127.0.0.1:8089)
uv run --directory eval python -m suite_a.specs --check   # the committed specs still validate
uv run --directory eval pytest -q                         # unit tests, no network
uv run --directory eval python -m suite_a.audit           # rewrites suite_a/AUDIT.md
uv run --directory eval python -m suite_a.score --all --cap 2.00   # 12 runs + judges -> results/
```

The rendered lectures are not committed (`eval/suite_a/out/` is ignored). They are in
`suite_a_render.tar.gz` (sha256
`c32591c5b7f9d550771dad08044f4ae0c9badef2bc156246a1e8c2f7cd1fe23f`: videos, timelines, slides
and the TTS cache), attached to the repository's first public release (URL filled in at
publish time); unpack it into `eval/suite_a/out/`. `python -m suite_a.render` re-renders bit
for bit from that TTS cache. Magpie TTS samples: the same sentence comes back slightly
different from one server call to the next (seed and temperature parameters did not change
that in our checks), so a render from an empty cache gives other durations and does not match
the key. Lightning did not reproduce an answer for a fixed seed either, so the committed specs,
not a regeneration, are the source of truth.

The scored run also writes `out/bundles/suite_a_results_<stamp>.tar.gz` (its runs without
media, the judge answers it used, the ledger rows and the invocation record); unpacked into
`eval/suite_a/out/`, `--all --reuse-runs` rebuilds the report at zero spend. Its sha256 is in
`results/suite_a.json`.

`--videos a01,a02` scores a subset into `out/partial/` (never `results/`); `--cap` limits what
one invocation may spend (default $3.00); `--no-second-judge` and `--no-probe` skip the
supplementary calls.

### Known limitations

- The judges are models. The primary one is strict by instruction, temperature 0, probed on
  known positives and near misses, and is also the engine's planner; the second judge is from
  another family but answers without reasoning. Recall, hallucination, decoys and redundancy
  are their verdicts; overlap, capacity and the exact matches are not.
- Synthetic slides are easier than real lectures: clean text, one fixed style, no presenter,
  no handwriting, no motion. Read these numbers as an upper bound for slide lectures.
- The lectures are short (36-55 s) and dense (14-24 visual facts each, about half the time
  silent); a describer has to choose, and recall counts every fact equally.
- Content was generated: it is textbook-level but not reviewed by subject experts, and some
  charts are illustrative rather than real data (a08's plateau, a05's meta slides); some
  decoys are a wrong value for a real category (a04 "Mesopotamia at 12,500", a06 "Zero Order
  at 0.25"), still absent from the video.
- Word-overlap rules decide what counts as said. `suite_a/AUDIT.md` lists every borderline
  case: the 21 items the rules called "partly said" (not scored), the visual facts that still
  share at least a quarter of their words with the slide's narration, and the 19 spoken facts
  the narration says in other words rather than as a phrase.
- Twelve videos give wide intervals; the bootstrap is over videos, not facts.
