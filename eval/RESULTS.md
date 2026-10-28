# Lull: evaluation results

Generated 2026-10-28T12:52:03+00:00 from commit `b03457c5057d` by `eval/report.py` from the complete results in `eval/results/`. Method: `eval/README.md`. Every number comes from a scorer that checked its pre-registration first; intervals are 95 % percentile bootstraps (10 000 resamples, seed 20261028).

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

### Supplementary: the unsupported claims (added after the scored run, no new calls)

Every claim the pre-registered hallucination metric counted as unsupported, with its kind as the run's cues.json records it and a class from a manual review: each claim read against the slide on screen at its cue (key.json slide inventory and style text) and the slide image in suite_a/out/<lecture>/slide_<n>.png; done after the scored run, no model calls. Made from the scored results (`suite_a.json`, sha256 `e3e27ed37bf3`) and the judge answers already given; the pre-registered number is unchanged.

| measure | value |
|---|---|
| hallucination, claims (pre-registered, unchanged) | 18.1% [4.0%, 37.2%] (19/105) |
| hallucination, atomic claims only | 16.9% [3.5%, 36.4%] (15/89) |
| unsupported / all claims of kind atomic | 15/89 |
| unsupported / all claims of kind sentence | 4/16 |

| class | claims |
|---|---|
| (a) true of the rendered slide but absent from the key (styling, colours, layout, slide numbers) | 0 |
| (b) a whole-sentence claim (kind sentence) that repeats atomic claims of the same cue | 4 |
| (c) a real error: contradicts the key or the slide | 1 |
| (d) other | 14 (paraphrase 8, title fragment 5, framing word 1) |

No unsupported claim is about styling, colours, layout or slide numbers: the key covers those (style text, slide inventory), and the one slide-number claim, a03 x001#6 'marked 1 of 4', was judged supported. One claim is a real error (a03 x002#1, chart values read as times); the rest are paraphrases or split-off fragments of on-screen text, which the pre-registered rule counts as unsupported because quoted on-screen text must match exactly, and whole-sentence claims that repeat them.

| lecture | claim | track | kind | class | claim text | judge's reason | review |
|---|---|---|---|---|---|---|---|
| a03 | x001#5 | extended | atomic | d, paraphrase | action-reaction pairs | The exact phrase 'action-reaction pairs' is not on the slide; the slide says 'Action and reaction are always paired'. | Slide 1 bullet 3 reads 'Action and reaction are always paired': the same content in other words; the pre-registered rule wants on-screen text quoted exactly. |
| a03 | x001#7 | extended | sentence | b | A slide titled Newton's Three Laws lists three laws: inertia, force causes acceleration, action-reaction pairs; marked 1 of 4 | The claim includes the exact phrase 'action-reaction pairs' which does not match the slide text, and the summary does not exactly match the bullet wording. | Repeats claims #1 to #6 of the same cue; unsupported only through #5 (the paraphrase). |
| a03 | x002#1 | extended | atomic | c | A slide titled Velocity Over Time shows a line graph of velocity versus time with data points at 0.5 to 8.5 seconds | Slide 2 shows a line chart titled 'Speed vs Time' with points at specific times and velocities, not 'data points at 0.5 to 8.5 seconds'. | The chart 'Speed vs Time' has its points at t=0s to t=8s; 0.5 to 8.5 are their velocities in m/s, not times (slide_2.png). The title, the line chart and its axes are right. |
| a07 | c004#1 | standard | atomic | d, title fragment | Temperature | Slide 2 does not display 'Temperature' as a standalone text element. | One word of the slide 2 title 'Temperature and Precipitation', split off by the claim split; the whole title (#3) was judged supported. |
| a07 | c004#2 | standard | atomic | d, title fragment | Precipitation | Slide 2 does not display 'Precipitation' as a standalone text element. | As #1: one word of the slide 2 title; the whole title (#3) was judged supported. |
| a07 | c006#1 | standard | atomic | d, framing word | Slide | The word 'Slide' does not appear on any slide. | The framing word of 'Slide: Annual Rainfall by Region', split off as a claim; the rule does not doubt framing words, but the split made it a claim of its own. |
| a07 | c006#2 | standard | atomic | d, title fragment | Annual Rainfall | 'Annual Rainfall' is only a substring of the title, not a separate text element. | Part of the slide 3 title 'Annual Rainfall by Region' (slide_3.png). |
| a07 | c006#3 | standard | atomic | d, title fragment | by Region | 'by Region' is only a substring of the title, not a separate text element. | Part of the slide 3 title 'Annual Rainfall by Region'. |
| a07 | c006#4 | standard | atomic | d, title fragment | Region | 'Region' appears only as part of the title or as 'Regions' on axis label, not as a standalone element. | The last word of the slide 3 title 'Annual Rainfall by Region'. |
| a07 | c006#5 | standard | sentence | b | Slide: Annual Rainfall by Region | The phrase includes 'Slide:' which is not on screen, and the full string does not match any slide text. | Repeats claims #1 to #4 of the same cue: the slide 3 title after the framing word 'Slide:'. |
| a10 | x002#1 | extended | atomic | d, paraphrase | slide lists heart pumps five liters per minute | Slide 2 shows 'Heart pumps ~5 liters per minute at rest', not 'five liters per minute' exactly (missing tilde and 'at rest'). | Slide 2 bullet 1 reads 'Heart pumps ~5 liters per minute at rest': the same figure, quoted without '~' and 'at rest' (the qualifier is lost). |
| a10 | x002#3 | extended | atomic | d, paraphrase | heart rate regulation | Slide 2 says 'Heart rate regulated by autonomic nervous system', not 'heart rate regulation' (different word form). | Slide 2 bullet 4 reads 'Heart rate regulated by autonomic nervous system', shortened. |
| a10 | x002#4 | extended | sentence | b | Slide lists heart pumps five liters per minute, stroke volume, heart rate regulation | The compound claim includes unsupported phrases ('five liters per minute' missing tilde and 'at rest', 'heart rate regulation' not exact). | Repeats claims #1 to #3 of the same cue. |
| a10 | x003#1 | extended | atomic | d, paraphrase | Slide lists resting heart rate, exercise increase, age effect, and a line chart | Slide 3 has 'Resting heart rate' exactly, but 'exercise increase' (vs 'Exercise increases') and 'age effect' (vs 'Age affects') are not exact matches. | Slide 3 bullets 1, 2 and 4 ('Resting heart rate varies by fitness level', 'Exercise increases heart rate significantly', 'Age affects maximum heart rate capacity') shortened; the line chart is right. |
| a11 | x001#1 | extended | atomic | d, paraphrase | Slide titled Global Temperature Rise lists: human activity drives warming, greenhouse gases trap heat, carbon dioxide levels increased, average temperatures rising worldwide | Bullet wording differs from slide (e.g., 'warming trends' vs 'warming'). | The title is exact; the four bullets of slide 1 are shortened ('Human activity drives warming trends', 'Greenhouse gases trap heat in the atmosphere', 'Carbon dioxide levels have increased significantly', 'Average temperatures are rising worldwide'). |
| a11 | x003#3 | extended | atomic | d, paraphrase | sea levels rising steadily | Slide says 'Sea levels are rising steadily'; claim omits 'are'. | Slide 3 reads 'Sea levels are rising steadily'; 'are' dropped. |
| a11 | x003#4 | extended | atomic | d, paraphrase | storm intensity increasing | Slide says 'Storm intensity is increasing'; claim omits 'is'. | Slide 3 reads 'Storm intensity is increasing'; 'is' dropped. |
| a11 | x003#5 | extended | atomic | d, paraphrase | precipitation patterns shifting | Slide says 'Precipitation patterns are shifting'; claim omits 'are'. | Slide 3 reads 'Precipitation patterns are shifting'; 'are' dropped. |
| a11 | x003#6 | extended | sentence | b | Slide titled Extreme Weather Events lists four points: heatwaves becoming more frequent, sea levels rising steadily, storm intensity increasing, precipitation patterns shifting | Listed points do not match slide text exactly (missing 'are' in three items). | Repeats claims #1 to #5 of the same cue; unsupported only through #3 to #5. |

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

## Cost accounting (draft)

A draft for comparison with the provider's billing, not yet checked against it. Model ledger `~/.lull/ledger.db` as of 2026-10-28T12:51:33+00:00: 1520 calls from 2026-10-27T20:18:51+00:00 to 2026-10-28T12:34:48+00:00 (UTC), every run on this machine (engine development, the eval and its judges). `tokens in` is the whole prompt as the provider reported it (`usage.prompt_tokens`), cached part included; `tokens out` is `usage.completion_tokens`. Token Factory total in the ledger: $3.2919.

| provider | model | calls | tokens in | of which cached | tokens out | cost (ledger) | cost (recomputed) |
|---|---|---|---|---|---|---|---|
| tavily | tavily-search (48 credits) | 48 | 0 | 0 | 0 | $0.3840 | $0.3840 |
| tokenfactory | Qwen/Qwen3-235B-A22B-Instruct-2507 | 94 | 94,010 | 18,928 | 48,231 | $0.0449 | $0.0449 |
| tokenfactory | nvidia/Nemotron-3-Ultra-550b-a55b | 216 | 422,198 | 8,704 | 713,274 | $2.5544 | $2.5544 |
| tokenfactory | nvidia/Nemotron-3.5-Super-VL | 563 | 1,718,068 | 267,648 | 202,910 | $0.6338 | $0.6338 |
| tokenfactory | nvidia/Nemotron-3_5-Lightning | 599 | 433,673 | 0 | 136,925 | $0.0589 | $0.0589 |

Per UTC day (billing periods are usually days):

| day | provider | model | calls | tokens in | tokens out | cost (ledger) |
|---|---|---|---|---|---|---|
| 2026-10-27 | tavily | tavily-search | 16 | 0 | 0 | $0.1280 |
| 2026-10-27 | tokenfactory | nvidia/Nemotron-3-Ultra-550b-a55b | 6 | 25,143 | 17,931 | $0.0789 |
| 2026-10-27 | tokenfactory | nvidia/Nemotron-3.5-Super-VL | 100 | 226,086 | 15,944 | $0.0822 |
| 2026-10-27 | tokenfactory | nvidia/Nemotron-3_5-Lightning | 19 | 8,436 | 1,694 | $0.0009 |
| 2026-10-28 | tavily | tavily-search | 32 | 0 | 0 | $0.2560 |
| 2026-10-28 | tokenfactory | Qwen/Qwen3-235B-A22B-Instruct-2507 | 94 | 94,010 | 48,231 | $0.0449 |
| 2026-10-28 | tokenfactory | nvidia/Nemotron-3-Ultra-550b-a55b | 210 | 397,055 | 695,343 | $2.4754 |
| 2026-10-28 | tokenfactory | nvidia/Nemotron-3.5-Super-VL | 463 | 1,491,982 | 186,966 | $0.5516 |
| 2026-10-28 | tokenfactory | nvidia/Nemotron-3_5-Lightning | 580 | 425,237 | 135,231 | $0.0580 |

Prices, USD per 1M tokens (input / cached input / output):

| model | the engine costs calls at | source | published | source |
|---|---|---|---|---|
| nvidia/Nemotron-3-Ultra-550b-a55b | 1.00 / 0.12 / 3.00 | engine/src/lull/ledger.py `PRICES` (unchanged since the ledger's first commit) | 1.00 / 0.12 / 3.00 | research notes, research-models.md (2026-10-27), condensed from the Token Factory model catalog (tokenfactory.nebius.com/api/public/models_info) |
| nvidia/Nemotron-3.5-Super-VL | 0.30 / 0.06 / 0.90 | engine/src/lull/ledger.py `PRICES` (unchanged since the ledger's first commit) | 0.30 / 0.06 / 0.90 | research notes, research-models.md (2026-10-27), condensed from the Token Factory model catalog (tokenfactory.nebius.com/api/public/models_info) |
| nvidia/Nemotron-3_5-Lightning | 0.06 / 0.06 / 0.24 | engine/src/lull/ledger.py `PRICES` (unchanged since the ledger's first commit) | 0.06 / n/a / 0.24 | research notes, research-models.md (2026-10-27), condensed from the Token Factory model catalog (tokenfactory.nebius.com/api/public/models_info) |
| Qwen/Qwen3-235B-A22B-Instruct-2507 | 0.20 / 0.05 / 0.60 | eval/suite_a/score.py `SECOND_JUDGE_PRICE` (Token Factory `/v1/models?verbose=true`, read 2026-10-28) | n/a | n/a |

Web search (Tavily) is costed at $0.008 per credit for reporting only; Tavily bills it, not Token Factory. Lightning: the notes record no cached-input price; the engine charges cached tokens at the input price, and the ledger has no cached Lightning tokens.

To check against the billing: the per-model token totals and the per-day costs above. What is not in this ledger and would show only in the billing: calls made with another `LULL_HOME` (for example inside the API container), requests sent outside the engine's client (manual probes), requests the provider billed but the client never saw answered (a timeout after the provider finished), and anything before the first call above. Whether the provider bills reasoning tokens inside `completion_tokens` (as the ledger assumes) is to be confirmed on the billing page.

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
