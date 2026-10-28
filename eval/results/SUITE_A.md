# Suite A: results

Key sha256 `354967ad20310ab6013c5fd884520f57120dbaa56d81b927f2d0c3ec35b7ce96`, pinned by tag `suite-a-prereg` (commit `194d4d0803aa`, 2026-10-28T02:12:13+01:00); scorer commit `72dfc2c4e0f4`; 2026-10-28T12:17:02+00:00. Engine 0.2.0+980454e1b9cd77d0. Primary judge `nvidia/Nemotron-3-Ultra-550b-a55b` (also the engine's planner); second judge `Qwen/Qwen3-235B-A22B-Instruct-2507`. Spend over the referenced run ids: $1.0470.

Hardware: AMD Ryzen 5 5600X 6-Core Processor; GPU NVIDIA GeForce RTX 4060, 8188 MiB, 610.88; Windows-11-10.0.26300-SP0. Models run remotely on Nebius Token Factory; speech runs locally.

12 videos, 8.84 min, 264.8 s without narration in 96 pauses (84 of them >= 2 s). Failed runs: none.

| metric | value (95% CI over videos) |
|---|---|
| overlap with narration (s, standard cues) | 0.000 |
| extended freeze points inside narration | 0 |
| standard cues outside their pause | 0 |
| visual-fact recall, standard | 2.6% [0.5%, 5.1%] |
| visual-fact recall, with extended | 14.0% [7.9%, 19.7%] |
| timely recall, with extended | 14.0% [7.9%, 19.7%] |
| (supplementary, added after the pre-registration) tie-aware recall, standard / with extended | 2.6% [0.5%, 5.1%] / 14.0% [7.9%, 19.7%] |
| exact title match, standard / with extended | 16.7% [0.0%, 36.7%] / 53.3% [28.1%, 76.3%] |
| exact chart-value match, standard / with extended | 0.0% [0.0%, 0.0%] / 12.5% [0.0%, 33.3%] |
| (supplementary, added after the pre-registration) tie-aware exact chart-value match, standard / with extended | 0.0% [0.0%, 0.0%] / 12.5% [0.0%, 33.3%] |
| hallucination, claims (content only) | 18.1% [4.0%, 37.2%] |
| hallucination, cues with an unsupported claim | 25.8% [7.1%, 43.8%] |
| absent decoys stated (judge) | 0/48 |
| decoy numbers in cues (deterministic) | 0 |
| redundancy, cues restating narration | 0.0% [0.0%, 0.0%] |
| spoken facts restated by a cue | 7.8% [2.0%, 14.3%] |
| model cost per minute | $0.0267 |
| Tavily cost per minute | $0.0000 |
| processing seconds per minute | 79.17 |
| cues (all runs) | verified 10, edited 0, approved 0, held 11, extended 21, unverified 0 |

Supplementary (added after the pre-registration, not pre-registered):

| supplementary metric | value |
|---|---|
| hallucination, voiced text (unsupported / voiced statements; text-only call against the facts on screen) | 11.0% [0.0%, 27.1%] |
| voiced statements the key neither confirms nor contradicts | 15.4% [6.7%, 24.0%] |
| cues with an unsupported voiced statement | 12.9% [0.0%, 27.5%] |
| claims the cue text does not say | 0.0% [0.0%, 0.0%] |
| hallucination over claims the text says | 18.1% [4.0%, 37.2%] |
| decoys stated, absent part only | 0/48 |
| second judge: recall, with extended | 18.7% [10.4%, 27.0%] |
| second judge: hallucination, claims | 9.5% [3.2%, 17.7%] |
| judges agree on visual facts | 92.8% agreement, kappa 0.74 (n=235) |
| judges agree on claims | 76.2% agreement, kappa 0.02 (n=105) |
| second judge: hallucination, voiced text | 4.5% [1.4%, 7.5%] |
| judges agree on voiced cues (any unsupported statement) | 87.1% agreement, kappa 0.43 (n=31) |

Recall by fact kind (standard / with extended):

- bullet: 0.0% [0.0%, 0.0%] / 6.2% [0.0%, 14.4%]
- chart_trend: 0.0% [0.0%, 0.0%] / 9.1% [0.0%, 27.3%]
- chart_value: 0.0% [0.0%, 0.0%] / 12.5% [0.0%, 33.3%]
- label: 1.8% [0.0%, 5.4%] / 10.5% [1.9%, 20.0%]
- title: 16.7% [0.0%, 36.7%] / 53.3% [28.1%, 76.3%]

| video | subject | min | silence s | pauses | pauses >= 2 s | recall std | recall +ext | halluc. claims | decoys | redundant cues | overlap s | $/min | s/min |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| a01 | biology | 0.81 | 25.6 | 9 | 7 | 1/24 | 6/24 | 0/3 | 0 | 0/3 | 0.000 | 0.0200 | 78.6 |
| a02 | economics | 0.67 | 19.9 | 7 | 6 | 0/17 | 1/17 | 0/6 | 0 | 0/2 | 0.000 | 0.0196 | 69.1 |
| a03 | physics | 0.80 | 24.4 | 9 | 8 | 0/17 | 3/17 | 3/13 | 0 | 0/4 | 0.000 | 0.0204 | 70.4 |
| a04 | history | 0.73 | 19.6 | 7 | 6 | 1/21 | 1/21 | 0/3 | 0 | 0/1 | 0.000 | 0.0227 | 67.6 |
| a05 | statistics | 0.71 | 21.4 | 9 | 6 | 0/19 | 5/19 | 0/15 | 0 | 0/4 | 0.000 | 0.0250 | 80.8 |
| a06 | chemistry | 0.62 | 20.5 | 7 | 7 | 0/20 | 3/20 | 0/18 | 0 | 0/2 | 0.000 | 0.0271 | 93.4 |
| a07 | geography | 0.92 | 27.0 | 9 | 8 | 3/23 | 3/23 | 7/10 | 0 | 0/4 | 0.000 | 0.0352 | 81.2 |
| a08 | computer science | 0.69 | 19.9 | 7 | 7 | 0/19 | 0/19 | 0/0 | 0 | 0/0 | 0.000 | 0.0280 | 64.5 |
| a09 | art history | 0.91 | 28.0 | 9 | 9 | 1/22 | 5/22 | 0/17 | 0 | 0/4 | 0.000 | 0.0359 | 94.0 |
| a10 | medicine | 0.59 | 18.0 | 7 | 6 | 0/18 | 0/18 | 4/6 | 0 | 0/3 | 0.000 | 0.0266 | 81.7 |
| a11 | climate | 0.72 | 22.2 | 9 | 8 | 0/21 | 6/21 | 5/11 | 0 | 0/3 | 0.000 | 0.0300 | 90.2 |
| a12 | engineering | 0.67 | 18.3 | 7 | 6 | 0/14 | 0/14 | 0/3 | 0 | 0/1 | 0.000 | 0.0259 | 76.3 |

Judge probe (synthetic cues from the key; videos a01, a05, a09). Sensitivity: facts credited to their own cue 65/65, decoys 12/12, true claims supported 65/65, decoy claims unsupported 12/12. Specificity: near misses not credited 42/42, near-miss claims unsupported 15/15. Cross-credits: {'fact_to_other_fact_cue': 0, 'decoy_to_fact_cue': 0, 'fact_to_decoy_cue': 0}.

- bullet_partial: 9/9 not credited
- gist_only: 18/18 not credited
- title_swap: 9/9 not credited
- wrong_value: 6/6 not credited

Voiced-text probe (2 true cues, 3 near misses per video): sensitivity 6/6, specificity 9/9 (invented_bullet 3/3, positive 6/6, swapped_label 3/3, wrong_number 3/3); second judge: sensitivity 6/6, specificity 9/9.

Notes: the pre-registered hallucination metric judges the claims' content only; timing errors (a description at the wrong time) show only in timely recall. A verdict the judge left out counts against the system (fact not conveyed, claim unsupported, cue redundant, decoy stated, a cue without a voiced-text answer one unsupported statement) and is listed below. Suite-a run ids found: 61 (listed in suite_a.json with outcome, fingerprint and spend).
