# Lull evaluation

Every number Lull publishes about itself comes from a script in this folder that anyone can
re-run. Results carry the git commit, the model ids, the date and the sha256 of an answer key
that was pinned before the scorer existed. The order of pre-registration and runs rests on
commit ancestry and commit times: each tag names its pinned commit, and a tag object's own
creation time is not the evidence.

`eval/` is its own uv project (`pyproject.toml`, `uv.lock`); it installs the engine from
`../engine` in editable mode and adds matplotlib. Commands below run from the repository root
with `uv run --directory eval ...` (or from `eval/` without `--directory eval`).

Third-party data: `suite_b/refs/` and `suite_b/facts/` hold text derived from two Elephants
Dream description tracks (Silvia Pfeiffer, CC BY 4.0; JJ Hunt, CC BY-SA 4.0, shared alike) and
from public-domain USGS videos; the `NOTICE.md` files there give the licence and attribution
of each file. The code is under the repository's licence.

## Suite A: planted facts, hidden answer key

Twelve short synthetic lectures (biology, economics, physics, history, statistics, chemistry,
geography, computer science, art history, medicine, climate, engineering; 3-4 slides, two
charts each, 8.8 minutes in all) whose narration leaves facts on the slides unsaid. The
question: does Lull describe what a blind student would miss (while measuring how much it
invents), without repeating the lecturer, and without ever talking over speech, and at what
cost and speed.

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

**The primary judge requests are locked.** For every video, the coverage and claims requests
the scorer sends (the judge cache key: sorted-key JSON of model, prompt, schema, temperature,
reasoning and max_tokens, for a fixed set of cues) are byte-identical to those of the scorer
as first committed (`5551575`, before any judge call). The expected hashes are in
`tests/fixtures/prereg_requests.json`, generated by `python -m tests.prereg_requests` from
5551575's own `score.py` (read with `git show` and imported); `test_score.py` checks the
current scorer against the fixture and, where the git history is present, regenerates the
fixture from 5551575 and checks it is unchanged.

### Deviations from the pre-registration

Everything below was decided after the pre-registration commit and before the scored run;
the primary metrics and both primary judge requests are otherwise exactly as pre-registered.

1. **Scorer rules the key does not spell out**, applied to primary metrics: a verdict the
   judge leaves out counts against the system (fact not conveyed, claim unsupported, cue
   redundant, decoy stated) and is listed under "judge problems"; extended cues count only
   when the run reports `extended_produced`.
2. **a08's tie** (its line chart shows the highest value, 100000, at three categories; the key
   names the first). Primary recall and exact match follow the key as written (the judge sees
   the fact as the key states it; the exact match needs the key's label). Supplementary,
   reported next to the primary: tie-aware recall (a fact is also credited when a cue states
   the highest value with any tied category, checked deterministically) and tie-aware exact
   match. Added after the pre-registration.
3. **Supplementary metrics** (next section), all labelled "supplementary" in every report:
   hallucination of the voiced text (its own call; a cue without an answer counts as one
   unsupported statement), claims the cue text does not say, decoys stated in their absent
   part only, the second judge with agreement and kappa, the probe's near misses,
   cross-credits and the voiced-text probe.
4. **Reporting**: silence and pause counts per video, hardware, the run inventory, the
   zero-spend rebuild bundle, the pinned tag.

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

Run before the engine's corrections, two videos, reported only to show the pipeline end to end:
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

What the smoke found in the engine (reported for the engine's corrections): the listen stage widened the
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

## Suite B: Lull against human audio description on real video

Real films and documentaries described by people, and the same videos described by Lull. The
question: how much of what the human describers chose to say does Lull also convey, and how
do both sides behave around dialogue, measured with the same tools for both. Human describers
choose differently from each other by design, so recall against one of them is not a score
out of 100. Elephants Dream has two describers, so the suite also measures one against the
other (the human baseline); each USGS video has one describer, so there is no human ceiling
for USGS.

### References and their licences

| video | reference | licence |
|---|---|---|
| Elephants Dream (2006), (c) Orange Open Movie Team / Blender Foundation, [Wikimedia Commons 480p transcode](https://commons.wikimedia.org/wiki/File:Elephants_Dream_(2006).webm), 658.1 s | | CC BY 2.5 |
| | Silvia Pfeiffer, [text description track](http://html5videoguide.net/presentations/LCA_MM_Miniconf2013/elephant.audiodescriptions.vtt) (WebVTT, 63 cues; never voiced) | CC BY 4.0 |
| | JJ Hunt, [text description track and its voiced recording](https://github.com/OwenEdwards/ElephantsDreamAudioDescription) (WebVTT, 64 cues; WAV, 653.9 s) | CC BY-SA 4.0 |
| USGS Woods Hole Coastal and Marine Science Center, 173.3 s | USGS extended audio-described version (409.3 s), human-voiced | public domain (USGS); music used with permission |
| USGS The importance of aquatic connectivity for maintaining cold water species, 265.1 s | USGS extended audio-described version (362.0 s) | public domain (USGS); some materials used with permission |
| USGS Climate Science Champions: introduction, 44.9 s | USGS extended audio-described version (63.9 s) | public domain (USGS); music used with permission |

Source URLs, file sizes and the licence statements as found are in the data folder's
`MANIFEST.md`; the media are not in this repository. The committed files derived from the
description tracks keep their licences: what comes from Hunt's track is shared under CC BY-SA
4.0, what comes from Pfeiffer's under CC BY 4.0, the USGS material is public domain;
`suite_b/refs/NOTICE.md`, `suite_b/refs/asr/NOTICE.md` and `suite_b/facts/NOTICE.md` say
which file or part carries which licence and attribution.

### How the references were made (`suite_b/refs.py`)

- **Elephants Dream**: every WebVTT cue is a reference line timed by its own start and end
  (markup stripped). Hunt's voiced track is digital silence between descriptions, so an
  energy VAD finds his voiced intervals: 20 ms frames, voiced above -45 dBFS RMS, gaps under
  0.3 s bridged, runs under 0.1 s dropped (265.0 s voiced in 133 intervals; 99.9 % of it
  within 0.5 s of one of his cues). Both tracks fit this 480p cut of the film: shifted from
  -5 to +5 s against the film's dialogue (the committed transcript), each overlaps it least
  within 0.3 s of no shift (Hunt's voice: 2.7 s at 0, 6.1 s at +1 s, 10.9 s at -1 s).
- **USGS**: the original and the AD version are transcribed by the local speech server
  (Parakeet TDT 0.6B v3, word timestamps, the request the engine's speech client makes;
  answers committed in `refs/asr/`). difflib aligns the two token streams; then a chance
  match of at most two tokens inside the describer's words is dropped, near-identical tokens
  or runs ("woods hall" / "woodshole", "an" / "and") count as the same narration, and a
  describer's run beside a repeated phrase slides to its sentence edges. AD-version words
  left over are the describer's, cut into sentences ("U.S." does not end one; runs under two
  tokens are dropped and listed). Matched narration words map AD-version time to original
  time. **A USGS line's anchor is the freeze point**: the original time at which the AD
  version paused for it (the original time reached when the line starts, kept inside the
  original gap it falls in; `anchor_range` gives the gap). 89 of 90 woodshole lines and
  every line of the other two are voiced over such a pause, so all the lines of one pause
  share about the same anchor even when they describe footage the original shows a few
  seconds later. csc_intro's four lines are a 19 s preface summarising the whole video:
  their window is the whole video. Narration of the original that the AD version's
  transcript lacks is listed (woodshole: four phrases, 35 of 356 tokens: cut, talked over or
  misheard). `python -m suite_b.refs --spot-check` prints random lines with the words around
  them on both timelines.
- **ASR corrections** (`refs/asr_corrections.json`): a recogniser error in a USGS line is
  corrected only when the right words are on screen in a frame of the ORIGINAL video
  (extracted with ffmpeg, its text read by Nemotron 3.5 Super VL, `python -m
  suite_b.evidence`; the frame time, its sha256 and the full reading are in the file) or in
  the original narration; `refs.py` refuses a correction whose evidence does not contain its
  right text. 17 corrections in 15 woodshole lines (for example "DSOS V5 toolbar" -> "DSAS v5
  Toolbar", "Transsex" -> "Transect", "hydrogenamic" -> "hydrodynamic", "Quesset" ->
  "Quissett"); the line keeps what was heard in `asr_text`. 15 suspicious terms have no such
  evidence and stay as heard, listed under `unresolved` ("Piping clover" for plover twice,
  "Athymmetry" for bathymetry, "crashed down" for crouched down, "Title Creek", "Aeros",
  "pescals", "Start" for star, "anti flux", "unpounded", "Well Fleet", "entries", "Signed";
  aquatic_connectivity: "Word Science", "role.org").

Lines: Pfeiffer 63, Hunt 64, woodshole 90, aquatic_connectivity 23, csc_intro 4. A block is
what a describer voiced in one go (USGS: the lines of one AD-version pause; WebVTT: one cue);
three last longer than 30 s (woodshole 33.6 s and 60.4 s, Pfeiffer's credits cue 33.5 s).

### Facts and the pre-registration (`suite_b/facts.py`)

Nemotron 3 Ultra (temperature 0, thinking off, strict JSON schema; chunks of 10 lines, the
three before them shown for pronouns) split every line into atomic facts in the describer's
own words, each with a kind: visual, on-screen text, sound, interpretation (an inference
about what is not shown) or not in the video (the description's own credit and licence).
Scored: visual and on-screen text. 500 facts: Pfeiffer 172 (152 scored), Hunt 128 (124),
woodshole 151 (151), aquatic_connectivity 35 (34), csc_intro 14 (14). Extraction spend
$0.2228 (ledger run `suite-b-facts`; the prompt was revised twice for finer splits before the
first pin) plus $0.0120 for the corrected lines (`suite-b-facts-2`).

`rules.json`, committed with the facts, fixes what the scorer does: the windows, the scored
kinds, both judges and their settings, the judge prompt and schema, the character legend,
the overlap and density definitions and their presentation, the aggregation and when
results may be written. `facts.sha256` pins the set: its first line is the sha256 of the list
below it, the sha256 of every `refs/*.json` (`asr_corrections.json` included),
`refs/asr/*.json`, `facts/*.json`, `rules.json` and `probes.json`.

- Annotated tag **`suite-b-prereg-2`** -> commit `f308f9034fd87b7c8ac7dcb52c4f601ac06cddd6`
  (2026-10-28 03:54:30 UTC), reference set sha256
  `7d8b9fdfec4936aca0350efb712ed39e006aee77e4df9a2f239eb53e0a372209`. This is the pin the
  scorer checks. The first pin, tag `suite-b-prereg`, stays in the history (below).
- The scorer refuses to run unless `facts.sha256` matches the files, the tag exists and is
  an ancestor of HEAD, the tagged `facts.sha256` has the same first line, and `refs/`,
  `facts/`, `facts.sha256`, `rules.json`, `probes.json`, `refs.py`, `facts.py` and
  `evidence.py` are unchanged since the tag; it refuses to write `results/` while `eval/` or
  `engine/` has uncommitted changes (Suite A's checks, reused).

### Deviations: what changed between the two pins

1. **First pin**: tag `suite-b-prereg` -> commit `5d628cb2099d23adbbb9faabba9801b9af9d942e`,
   2026-10-28 03:07:27 UTC, reference set `4c6aca38...b66`.
2. **A smoke preceded the second pin**: the scorer (first committed at 03:14:18 UTC) was run
   on the existing `ed90` run, Elephants Dream 60-150 s, between 03:14 and 03:18 UTC (Lull
   0/46 facts with Ultra, 8/46 with Qwen; below). Its facts (ED, 60-150 s) did not change.
3. **Second pin**: tag `suite-b-prereg-2` -> commit `f308f90`, 03:54:30 UTC. Changed:
   - the 17 ASR corrections above; the facts of the 15 corrected lines (only those) were
     split again with the same, unchanged extraction prompt (149 -> 151 woodshole facts);
     every other fact is byte-identical in content (blocks added as fields);
   - blocks and the long-block rule: the facts of a block longer than 30 s get a +/-30 s
     window instead of +/-15 s;
   - USGS: the anywhere variant is reported with the same weight as the windowed one (the
     anchors are freeze points); recall is also reported without the facts that still hold
     an unresolved term, and with any left the all-facts USGS recall is a lower bound;
   - the judge probe (`probes.json`, 33 hand-written cues for 14 facts of two videos, none
     in the smoke clip): 14 positives (paraphrases with descriptions instead of names, a
     fact plus an extra detail) that must be credited and 19 near misses (wrong count,
     colour or value, swapped character, gist only, invented detail) that must not. With
     the first prompt Ultra got 14/14 and 19/19, Qwen 13/14 and 17/19 (it credited "Three
     people stand on a small bridge" for two people, and a swapped character). One rule
     was added to the judge prompt: counts, numbers, colours, names, written words and who
     does what to whom must agree with the fact (its examples are not from the probe).
     With it both judges got 14/14 and 19/19 (`probe_results.json`; the first run in
     `probe_results_prompt1.json`). Nothing else in the prompt changed;
   - results need runs of all four videos; the overlap presentation rule (below); NOTICE
     files.

### Deviations: the scored runs

- **Engine commits of the scored runs**: Suite A, the negative control and the ablation ran on the engine of main 61e9d81 (merged into the evaluation branch as 7168ea5). The first recorded run of the full Elephants Dream (run id final-ed) failed in publish on a deterministic engine bug: a keyframe time after the file's last video frame. It is listed under the known failures and stays listed. The bug was fixed on main 0b83117 (keyframe and freeze times clamped to the video stream's last frame; nothing else changed), merged, and the full film was run once more under a fresh run id (final-ed2) with the same command and options (paused once for planned machine maintenance, then resumed from its stage cache). Suite B is scored on that run and on the three USGS runs made on 61e9d81 (Suite B does not require one engine across its runs).
- **Batch splitting on cut-off answers**: added after the second Suite B attempt, in the scorer only (no change to prompts, rules or facts). When a judge's answer (either judge, any batch) is cut off by its token limit, even after the engine's retry with twice the limit, the batch is judged again in halves with the same prompt, schema, model and temperature, down to single facts; only a single fact whose answer is still cut off falls under the pre-registered rule (no verdict: not conveyed, listed). Every split is listed with the judge problems; saved answers and cut-offs are reused, not paid for twice.
- **Account credit outage**: at 12:34 UTC the provider refused every call (HTTP 402, account credits exhausted) while the recorded runs were being made; the account credits were topped up and the runs resumed from their stage caches at 13:36 UTC. Suite A's scored run had finished before; the controls ran after.
- **Three Suite B invocations**: the first stopped on the per-run cap of $0.30 that the scorer takes from Suite A's client setup (it needs more for a full film's judging), the second on a judge answer cut off by its token limit, the third (with the batch splitting above and a higher per-run cap) completed. The first two wrote nothing; their saved judge answers were reused.

### What is measured (`suite_b/score.py`, from `rules.json`)

| metric | how |
|---|---|
| recall | per scored fact, the judge names the cues that convey it; it counts when one of them plays within [anchor - 15 s, anchor end + 15 s] (+/-30 s for a block over 30 s; standard cue: its voiced interval meets the window; extended cue: its freeze point lies in it). Standard track alone and with extended; per reference, USGS pooled; also by kind. Elephants Dream: the window is primary, anywhere supplementary. USGS: window and anywhere with equal weight |
| recall without unresolved terms | the same without the facts that still hold an unresolved ASR term |
| human baseline (supplementary) | Elephants Dream only: Hunt's lines as the cues against Pfeiffer's facts, and the reverse; same judge, prompt and window |
| second judge (supplementary) | Qwen3 235B A22B Instruct 2507 makes the same calls; its recall, and raw agreement and Cohen's kappa with Ultra per fact (window and anywhere) |
| judge probe | both judges on `probes.json` with the pinned prompt, reported with every score (cached: no cost) |
| talking over speech | seconds of description inside speech and % of description time, every side against the same spans. First, against the committed transcript of the original (words only, independent of Lull); then against the Lull run's own speech detection (its listen output, diarized turns included), where Lull's standard track and its extended freeze points are 0 by construction. Lull: voiced intervals of published standard cues; extended cues as freeze points inside speech. Pfeiffer: WebVTT cue start to end, display time, never voiced. Hunt: his cues (display time) and his recording's VAD intervals (voiced) |
| USGS AD on its own timeline | the describer's lines against the narration words of the AD version (one transcript), plus the original narration the AD version lacks; not comparable to standard-track overlap (below) |
| density | description words per minute of video, each side (USGS also per minute of the AD version) |

**The judge** is Suite A's (Nemotron 3 Ultra, temperature 0, reasoning on; answers cached by
their exact request in `suite_b/out/judge/`). It sees the cue texts in time order under
neutral ids (k001, k002 ...) without times or tracks, so it judges content only and cannot
tell Lull's cues from a describer's; the window applies timing afterwards. Character names
are mapped by a legend in `rules.json` ("the old man" is Proog), so a cue need not know the
names. Recall is pooled over facts with a 95 % percentile bootstrap over reference lines
(each line with its facts; 10 000 resamples, seed 20261028).

**Why USGS overlap is not comparable.** The USGS describer speaks while the video is paused,
so the program cannot be talked over in those stretches by construction; and the AD version
is one mixed soundtrack, so a single transcript cannot show two voices at once (where the
describer did talk over the narrator, the narration goes missing from the transcript, which
is why those phrases are reported). Lull's standard track has to fit the original's own
pauses; its extended track is the like-for-like comparison (freeze points inside speech).

Results are written to `results/suite_b.json` and `results/SUITE_B.md` only with runs of all
four videos, each covering its whole video, from a clean tree; anything else goes to
`suite_b/out/partial/` and is labelled so.

### Smoke (not results)

On the existing `ed90` run: a 90 s clip of Elephants Dream from 60.0 s to 150.0 s (located by
audio cross-correlation, 0.998 at 60.000 s; not the film's first 90 s), run id `ed90-final-b`
on the pre-merge engine, facts anchored in 60-150 s (46 scored: 26 Pfeiffer, 20 Hunt).

| | smoke on the first pin (run 03:14 UTC) | smoke re-run on the second pin (run 03:57 UTC; pin commit 03:54:30 UTC) |
|---|---|---|
| Lull, Ultra, window with extended (Pfeiffer / Hunt) | 0/26 / 0/20 | 0/26 / 0/20 |
| Lull, Qwen, window with extended | 4/26 / 4/20 | 6/26 / 3/20 |
| judges agree per fact (Pfeiffer / Hunt) | 85 % / 80 % | 77 % / 85 % |
| human baseline, Ultra (Hunt's lines -> Pfeiffer's facts / reverse) | 8/26 / 4/20 | 8/26 / 4/20 |

Over speech, against the transcript of the film: Lull 0.0 of 28.6 s, Pfeiffer's cues (display
time) 0.2 of 71.5 s, Hunt's cues 0.1 of 49.5 s, Hunt's voice 0.0 of 44.2 s. Against the run's
own speech detection, which includes diarized turns over the birds' garbled noise: Lull 0.0
s (0 by construction against its own speech detection), Pfeiffer's cues 8.0 s (11.2 %,
display time, not voiced), Hunt's cues 4.9 s, Hunt's voice 4.3 s (9.7 %). Density: Lull 55
words per minute (83 with extended), Pfeiffer 120, Hunt 95. In this clip Lull described a
tilting, breaking walkway and falling figures where both describers saw panels flipping and
robotic birds attacking; Ultra's reasons say so fact by fact, while Qwen's credits rest on
inference or a partial match ("a walkway in a shaft ... implying it is suspended in mid-air";
"bald with a long nose" for "balding and stooped with sideburns and a beak-like nose").
Scoring spend $0.0640 (first pin) and $0.0785 (second pin).

### Reproduce

```sh
uv run --directory eval python -m suite_b.facts --check      # the reference set is as pinned
uv run --directory eval python -m suite_b.refs --spot-check  # random USGS lines in context
uv run --directory eval python -m suite_b.probe              # the judge probe (cached: free)
uv run --directory eval python -m suite_b.score --run elephants_dream=RUN_DIR \
    --run woodshole=RUN_DIR --run aquatic_connectivity=RUN_DIR --run csc_intro=RUN_DIR
```

`python -m suite_b.refs` rebuilds the references from the data folder (`--data`, default
`../lull-data` next to the repository), the committed transcripts and the corrections
(`--transcribe` asks the speech server again; the recogniser need not return the same words
twice). `python -m suite_b.facts` rebuilds the facts from its answer cache; the provider does
not promise the same answer twice, so the committed facts are the reference. `--offset
VIDEO=S` scores a run made on a clip that starts S seconds into the video; `--cap` limits the
spend (default $0.50).

### Known limitations

- Human references describe different things by design: two experts describing the same
  90 s conveyed 20-31 % of each other's facts in the smoke. Read Lull's recall next to that;
  for USGS there is no second describer and so no such ceiling.
- The judges are models: Ultra is strict and is also the engine's planner; Qwen passes the
  probe but on real cues credits inferences and partial matches (its smoke credits). The
  probe is small (33 hand-written cues) and has no "only implied" or "partly said" near
  miss.
- The USGS extraction is automatic: 15 suspicious terms remain as heard (listed), so USGS
  recall over all facts is a lower bound (recall without them is reported); anchors inside
  paused gaps are freeze points, not the moment the described footage plays (hence the
  equal-weight anywhere variant and the wider window for long blocks); four woodshole
  narration phrases are missing from the AD version's transcript. The spot-check reads
  lines in context; nobody listened.
- Fact granularity varies: most lines split cleanly, but some telegraphic USGS lines and a
  few ED lines stayed one compound fact ("Large waves and sign reading danger, no
  swimming."), which a cue then has to convey whole; pfeiffer-05 kept two naming facts ("The
  old man is Proog.") despite the rule against them. They were left as extracted (no hand
  edits). The kinds are the model's.
- WebVTT cue ends are display times, not voiced durations; Hunt's recording gives his real
  voiced intervals; Pfeiffer's track was never voiced.
- How much the human tracks overlap speech depends on what counts as speech: ~10 % against
  the run's diarized spans, under 1 % against the transcript's words.

## Controls: does the verifier catch false descriptions?

Lull checks every claim of a description against the frames it describes before voicing it.
Two controls measure how this check behaves: a negative control (does the verifier accept a
description checked against footage that does not show it?) and an ablation (how do
hallucination and recall on Suite A change when the check is switched off?). Both are pre-registered in
`controls/prereg.json` under the annotated tag **`controls-prereg-2`**, committed before
either was run; they run on the merged engine after the Suite A scored run.

### The pin and the one engine-facing file

Both harnesses refuse to run unless the tag exists and is an ancestor of HEAD, the tagged
`controls/prereg.sha256` is the sha256 of the `prereg.json` on disk, and `prereg.json`,
`prereg.sha256`, `negative.py` and `ablation.py` are unchanged since the tag (Suite A's
check, reused). Every call into the engine's verification goes through
`controls/verify_adapter.py` (the clip window, cutting the clip, the verifier call, the
verification-off switch); it is deliberately not pinned, so following a change of the
engine's signature is a one-place edit that leaves the pin intact. A test walks the import
statements of `negative.py` and `ablation.py` and fails if either imports the engine's stages
or media directly.

### Negative control (`controls/negative.py`)

- **Selection** (pre-registered, deterministic): every published verified cue (standard cues
  with status "verified", extended cues with status "extended") of the Suite A counting run
  and of the showcase runs passed in, ordered by (video, start, id); with more than 40, 40
  drawn with `random.Random(20261028)`, kept in that order.
- **True clip**: the engine's own verification window for the cue (its watch events, the
  pipeline's padding, cap, minimum and clip height).
- **Mismatched clip**, same length: in a Suite A lecture the centre of another slide (one the
  true window does not touch, long enough, not carrying the title of a slide it touches; the
  nearest by start); in a showcase video a window at least 30 s from the true one that shares
  at least 0.5 s with a watch event and no time with any of the cue's (the nearest on a 0.5 s
  grid); otherwise the next video that has room ("other video"). Every row records its rule,
  the mismatched window and slide, and the watch events it overlaps with the seconds shared.
- **Check**: the engine's verifier, with the pipeline's model and settings (Nemotron 3.5 Super
  VL, its prompt, temperature 0), on the cue's final text and each clip; no names are passed.
- **Metrics** (95 % bootstrap over cues), per mismatch rule with row counts, and a headline
  over the same-video rules only (another slide; other events 30 s away). Other-video rows
  are reported apart and never pooled into the headline.
  - **False accept** (the mismatched clip supports every claim) and per-claim false accept.
    The headline false accept is an **upper bound** on verifier failure: it also counts
    claims that are genuinely true of the other footage (a generic layout, a recurring
    setting).
  - **Discriminative false accept** (Suite A): a false accept counts only when the cue conveys
    at least one key visual fact of the true slide that the mismatched slide does not show.
    Conveyed means credited by the Suite A judge's coverage verdict for that cue in the
    scored run, or, without verdicts, an exact match of the slide title or of a chart value
    with its category. Showcase videos have no key and get the raw rate only.
  - **True accept** on the true clip: an optimistic upper bound on the verifier's
    consistency, because every selected cue already passed verification (and edited cues are
    excluded).
  - `results/CONTROLS.md` lists every false accept with its text, both clips' times, its rule
    and whether it is discriminative.

### Ablation (`controls/ablation.py`)

Suite A's 12 lectures run again with `LULL_NO_VERIFY=1`, using Suite A's run function and
options, into `controls/out/ablation/` under run ids `ablation-<id>-<stamp>`. The engine
switch must be set, and every run's manifest must say verification is "disabled". Cues voiced
unverified count as published.

The runs are judged by Suite A's primary judge and requests, measured by Suite A's code, and
compared with the Suite A counting run on the same engine. Every requested video must be
paired, and results need all 12. Both arms are reported side by side, with the paired
difference (without − with) and its 95 % bootstrap over videos, for these metrics:

- hallucination over claims (Suite A's pre-registered metric), over cues and over voiced
  statements;
- cues with an unsupported voiced statement;
- recall and decoys.

The headline is the voiced-statement rate, because the judge splits both arms' text the same
way. The claim split comes from the verifier, which this arm switches off.

The ablation was added after the pre-registration of Suite A, whose pin applies unchanged.
It writes to `results/ablation.json` and `results/ABLATION.md`, never to
`results/suite_a.json`.

### Deviations: the two pins

The first pin, tag `controls-prereg`, points to commit `3e80579` (2026-10-28 04:12:16 UTC).
After a review, and before any control or ablation run, the second pin `controls-prereg-2`
added the following (listed in `prereg.json` under `changes_since_first_pin`):

- per-rule reporting, with a same-video headline called an upper bound;
- the discriminative false accept;
- the true-accept disclosure;
- the 0.5 s event-overlap rule and per-row event overlap;
- the title rule for slide fallbacks;
- the ablation's all-videos-paired check.

### How to run (after the engine merge and the Suite A scored run)

```sh
uv run --directory eval pytest -q
uv run --directory eval python -m controls.negative --suite-a \
    --showcase elephants_dream=RUN_DIR::VIDEO --showcase woodshole=RUN_DIR::VIDEO \
    --showcase aquatic_connectivity=RUN_DIR::VIDEO --showcase csc_intro=RUN_DIR::VIDEO
LULL_NO_VERIFY=1 uv run --directory eval python -m controls.ablation --all
```

Expected spend:

- negative control: about 80 Super VL calls (2 per cue), about $0.25 (cap $0.50);
- ablation: the Suite A pipeline without the verifier's calls plus Suite A judging, about
  $0.7–0.8 (cap $2.00).

### Known limitations

- A mismatched window can still show what the cue says (a recurring setting, a title that
  stays on screen, a wire-filled shaft 30 s later). This is why the raw rate is an upper
  bound and why Suite A gets the discriminative rate. For showcase videos only the listed
  false accepts let a reader judge.
- The discriminative rate is only as good as the Suite A judge's coverage verdicts and the
  key's facts; a fact the key does not list cannot make a false accept discriminative.
- 40 cues give a wide interval. The selection is spread over the inputs as they come, not
  balanced between Suite A and the showcase videos.
- The verifier runs at temperature 0. True accept measures its consistency on cues it
  already passed, from a single draw per clip.
- No names are passed to the verifier, whereas the pipeline passes the cast names a line uses.
- The ablation's two arms ran at different times (the with-arm is the Suite A counting run),
  and the remote models may change in between.

## Results report

`python report.py` (from `eval/`) writes `RESULTS.md` from the complete results in `results/`:
Suite A, Suite B, the negative control and the ablation, each only when its scorer marked it
complete (never a smoke or a partial run), with the pre-registration tag, commit and key or
reference-set sha256, the model ids, the scoring commit and date, the hardware, every number
with its 95 % interval, the deviations and the known failures. `--out-web PATH` also writes
the website's `benchmarks.json` (`{generated_at, git_commit, suites: {suite_a, suite_b,
controls: {negative, ablation}}, known_failures}`, each suite its scorer's own object).

Before writing, every included result is checked with its suite's own pre-registration check,
and must name the same tag, commit and key or reference set. `--out-web` also refuses while
`eval/` or `engine/` has uncommitted changes, when nothing is complete, and when the file
would fail the site's validator: `report_schema.py` is a Python port of
`lull-web/web/lib/benchmarks-schema.ts`, and the tests also run the site's own validator
through node when node and the site are present. Known failures are generated from the data:
failed runs, judge problems, held-cue reason classes with counts, Suite B's unresolved ASR
terms, every negative-control false accept, and the "Deviations" sections above for the
included suites. They are followed by the hand-kept `known_failures.md`.
