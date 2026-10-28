# Lull: evaluation results

Generated 2026-10-28T12:37:13+00:00 from commit `8c01c24a87af` by `eval/report.py` from the complete results in `eval/results/`. Method: `eval/README.md`. Every number comes from a scorer that checked its pre-registration first; intervals are 95 % percentile bootstraps (10 000 resamples, seed 20261028).

Not included: Suite B (missing); negative control (missing); ablation (missing).

## Suite A: planted facts, hidden answer key

Pre-registration: tag `suite-a-prereg` -> commit `194d4d0803aa4a9195abbb450b35d7d132631ae1` (2026-10-28T02:12:13+01:00), key sha256 `354967ad20310ab6013c5fd884520f57120dbaa56d81b927f2d0c3ec35b7ce96`. Scored 2026-10-28T12:17:02+00:00 at commit `72dfc2c4e0f4`; engine 0.2.0+980454e1b9cd77d0; engine models nvidia/Nemotron-3-Ultra-550b-a55b, nvidia/Nemotron-3.5-Super-VL, nvidia/Nemotron-3_5-Lightning. Primary judge `nvidia/Nemotron-3-Ultra-550b-a55b` (temperature 0, reasoning on; also the engine's planner); second judge `Qwen/Qwen3-235B-A22B-Instruct-2507`. Hardware: AMD Ryzen 5 5600X 6-Core Processor; GPU NVIDIA GeForce RTX 4060, 8188 MiB, 610.88; Windows-11-10.0.26300-SP0; speech http://127.0.0.1:8089 (local); models remote: Nebius Token Factory. Spend over the referenced runs $1.0470; every referenced model call after the pre-registration: True.

12 lectures, 8.84 min, 264.8 s without narration; failed runs: none.

Pre-registered metrics (value [95% CI over videos] (num/den)):

| metric | value |
|---|---|
| overlap with narration, standard cues (s) | 0.000 |
| extended freeze points inside narration | 0 |
| standard cues outside their pause | 0 |
| visual-fact recall, standard | 2.6% [0.5%, 5.1%] (6/235) |
| visual-fact recall, with extended | 14.0% [7.9%, 19.7%] (33/235) |
| timely recall, with extended | 14.0% [7.9%, 19.7%] (33/235) |
| hallucination, claims | 18.1% [4.0%, 37.2%] (19/105) |
| hallucination, cues with an unsupported claim | 25.8% [7.1%, 43.8%] (8/31) |
| absent decoys stated (judge) | 0/48 |
| decoy numbers in cues | 0 |
| redundancy, cues restating narration | 0.0% [0.0%, 0.0%] (0/31) |
| spoken facts restated by a cue | 7.8% [2.0%, 14.3%] (4/51) |
| model cost per minute (USD; CI of model + web search) | 0.0267 [0.0235, 0.0301] |
| processing seconds per minute | 79.2 [73.7, 84.7] |

Supplementary (added after the pre-registration):

| metric | value |
|---|---|
| hallucination, voiced text | 11.0% [0.0%, 27.1%] (10/91) |
| voiced statements unverifiable | 15.4% [6.7%, 24.0%] (14/91) |
| cues with an unsupported voiced statement | 12.9% [0.0%, 27.5%] (4/31) |
| tie-aware recall, standard / with extended | 2.6% [0.5%, 5.1%] (6/235) / 14.0% [7.9%, 19.7%] (33/235) |
| claims the cue text does not say | 0.0% [0.0%, 0.0%] (0/105) |
| decoys stated, absent part only | 0 |
| second judge: recall with extended | 18.7% [10.4%, 27.0%] (44/235) |
| second judge: hallucination, claims | 9.5% [3.2%, 17.7%] (10/105) |
| judges agree on visual facts | 92.8% agreement, kappa 0.74 (n=235) |
| judges agree on claims | 76.2% agreement, kappa 0.02 (n=105) |
| judges agree on voiced cues | 87.1% agreement, kappa 0.43 (n=31) |

Judge probe (a01, a05, a09): facts credited to their own cue 65/65, near misses not credited 42/42, cross-credits 0.

| lecture | recall std | recall +ext | unsupported claims | overlap s | run |
|---|---|---|---|---|---|
| a01 biology | 1/24 | 6/24 | 0/3 | 0.000 | suite-a-a01-20261028T115030Z |
| a02 economics | 0/17 | 1/17 | 0/6 | 0.000 | suite-a-a02-20261028T115030Z |
| a03 physics | 0/17 | 3/17 | 3/13 | 0.000 | suite-a-a03-20261028T115030Z |
| a04 history | 1/21 | 1/21 | 0/3 | 0.000 | suite-a-a04-20261028T115030Z |
| a05 statistics | 0/19 | 5/19 | 0/15 | 0.000 | suite-a-a05-20261028T115030Z |
| a06 chemistry | 0/20 | 3/20 | 0/18 | 0.000 | suite-a-a06-20261028T115030Z |
| a07 geography | 3/23 | 3/23 | 7/10 | 0.000 | suite-a-a07-20261028T115030Z |
| a08 computer science | 0/19 | 0/19 | 0/0 | 0.000 | suite-a-a08-20261028T115030Z |
| a09 art history | 1/22 | 5/22 | 0/17 | 0.000 | suite-a-a09-20261028T115030Z |
| a10 medicine | 0/18 | 0/18 | 4/6 | 0.000 | suite-a-a10-20261028T115030Z |
| a11 climate | 0/21 | 6/21 | 5/11 | 0.000 | suite-a-a11-20261028T115030Z |
| a12 engineering | 0/14 | 0/14 | 0/3 | 0.000 | suite-a-a12-20261028T115030Z |

## Deviations from the pre-registrations

- **Suite A**: Scorer rules the key does not spell out, applied to primary metrics: a verdict the judge leaves out counts against the system (fact not conveyed, claim unsupported, cue redundant, decoy stated) and is listed under "judge problems"; extended cues count only when the run reports extended_produced.
- **Suite A**: a08's tie (its line chart shows the highest value, 100000, at three categories; the key names the first). Primary recall and exact match follow the key as written (the judge sees the fact as the key states it; the exact match needs the key's label). Supplementary, reported next to the primary: tie-aware recall (a fact is also credited when a cue states the highest value with any tied category, checked deterministically) and tie-aware exact match. Added after the pre-registration.
- **Suite A**: Supplementary metrics (next section), all labelled "supplementary" in every report: hallucination of the voiced text (its own call; a cue without an answer counts as one unsupported statement), claims the cue text does not say, decoys stated in their absent part only, the second judge with agreement and kappa, the probe's near misses, cross-credits and the voiced-text probe.
- **Suite A**: Reporting: silence and pause counts per video, hardware, the run inventory, the zero-spend rebuild bundle, the pinned tag.

## Known failures

- **Suite A: held cues, "the failed claims share every word with claims that passed"**: 10 cues held for this reason in 12 runs. A held cue is not voiced.
- **Suite A: held cues, "nothing left once the unverified claims were removed"**: 1 cue held for this reason in 12 runs. A held cue is not voiced.
- **Run-to-run variance**: Lull's descriptions come from remote models that do not reproduce an answer for a fixed seed (Nemotron 3.5 Lightning ignored the seed in our checks), so the same video gives different cues from one run to the next. Each suite scores one pre-registered run (Suite A: the first complete --all invocation on the final engine) and does not average over runs; the intervals are bootstrapped over videos or reference lines, not over repeated runs.
- **Sparse descriptions compared with human audio description**: Lull says less than a human describer. In the Suite B smoke on Elephants Dream 60-150 s it voiced 55 words per minute (83 with the extended track) against 120 (Silvia Pfeiffer) and 95 (JJ Hunt), and the primary judge credited none of the describers' 46 facts in that clip to its cues. Lull holds or cuts a description it cannot verify against the frames and fits the standard track into the programme's own pauses, which leaves room for far fewer words than a describer who writes for every scene.
- **Lecture pauses swallowed before the speech-span fix**: Before the engine's fix round the listen stage widened narration spans by 0.2-0.6 s and merged sentences across planned pauses of about 1.9 s, so a Suite A lecture had only 4-5 speech-free windows and the standard track voiced little more than slide titles; extended cues froze the picture before their slide appeared. Found in the Suite A smoke (a01, a02, pre-fix engine) and fixed in the engine; the scored run is on the fixed engine and the smoke numbers are not results.
- **TTS nondeterminism**: Magpie TTS returns slightly different audio for the same sentence from one call to the next (seed and temperature parameters did not change that in our checks): a01 re-rendered from an empty cache came out 48.56 s instead of 48.40 s. Suite A's lectures are rendered once and cached (the render tarball reproduces them bit for bit), and Lull's own cue durations vary slightly between runs.

## Run log

Plan 4 Task 6, the scored runs on the final engine (main 61e9d81 merged into feat/eval as
7168ea5). Kept by hand while the runs happened, 2026-10-28, times UTC. Spend is the change of
the model ledger (~/.lull/ledger.db) over the step; the ledger stood at $2.0125 before the
task. LULL_CAP_DAY=10. Nothing below was run twice to get a better number.

| step | started | finished | wall | spend | outcome |
|---|---|---|---|---|---|
| 0. merge main, adapter for the final verifier | about 11:33 | 11:49:40 | about 17 min | $0 | merged without conflicts (tf.py already had the optional seed); engine tests 782 passed, 6 skipped; eval tests 190 passed |
| 1. Suite A scored run (`suite_a.score --all --cap 2.00`) | 11:50:29 | 12:17:05 | 26.6 min | $0.9146 (scorer: $1.0470 over the referenced run ids, cached probe answers included) | invocation 20261028T115030Z, 12/12 published, none failed; results committed (1ee9ece) |
| 2. recorded runs (`lull describe --mode extended`, fresh run ids final-*) | 12:18:06 | 12:36 (stopped) | 18 min | $0.3648 | STOPPED: Token Factory answered 402 `credit_balance_exhausted` ("You have exhausted your budget. Please add funds") at 12:34; an infrastructure failure, not a pipeline result |
| 3. Suite B | | | | $0 | not run: needs the ed and USGS runs |
| 4. Controls | | | | $0 | not run: needs model calls and the showcase runs |
| 5. Report (Suite A only) | | | | $0 | RESULTS.md and results/benchmarks.json from the complete results so far |

Step 2 in detail (inputs in lull-runs/final/inputs/: ed90.mp4 copied from the existing ed90
excerpt, ED 60-150 s, sha256 0daa75ac9328b0c0...; bbb120.mp4 = Big Buck Bunny 0:00-2:00 and
tos180.mp4 = Tears of Steel 3:30-6:30, cut with ffmpeg; ED and the USGS videos used as they are):

- `ed` (Elephants Dream full, entertainment, LULL_CAP_RUN=1.50): listen, watch, research and
  plan finished; finalize stopped at the 402 after cue c041 (4:56 of 10:58); exit 3; 170
  calls, $0.3648. Its finished stages are cached in lull-runs/final/ed: the same command
  resumes where it stopped.
- `ed90`: listen finished, watch hit the 402 at its first call; exit 3; no spend.
- `bbb120`: listen finished, watch hit the 402; stopped; no spend.
- `tos180`, `woodshole`, `aquatic_connectivity`, `csc_intro`: not started.

The failure policy allows one retry of an infrastructure failure. That retry needs the
provider account topped up first; it is the same `lull describe` command with the same
`--out` and `--run-id`, resuming from the cached stages. Nothing was retried here.
