# Lull: evaluation results

Generated 2026-10-28T20:57:04+00:00 from commit `ef2b6b387cd3` by `eval/report.py` from the complete results in `eval/results/`. Method: `eval/README.md`. Every suite's numbers come from a scorer that checked its pre-registration first; the supplementary claims breakdown and the cost-accounting draft were added after the scored runs, without model calls. The order of pre-registration and runs rests on commit ancestry and commit times (each tag names its pinned commit; a tag object's own creation time is not the evidence). Intervals are 95 % percentile bootstraps (10 000 resamples, seed 20261028).

## Suite A: planted facts, hidden answer key

Pre-registration: tag `suite-a-prereg` -> commit `194d4d0803aa4a9195abbb450b35d7d132631ae1` (2026-10-28T02:12:13+01:00), key sha256 `354967ad20310ab6013c5fd884520f57120dbaa56d81b927f2d0c3ec35b7ce96`. Scored 2026-10-28T12:17:02+00:00 at commit `72dfc2c4e0f4`; engine 0.2.0+980454e1b9cd77d0; engine models nvidia/Nemotron-3-Ultra-550b-a55b, nvidia/Nemotron-3.5-Super-VL, nvidia/Nemotron-3_5-Lightning. Primary judge `nvidia/Nemotron-3-Ultra-550b-a55b` (temperature 0, reasoning on; also the engine's planner); second judge `Qwen/Qwen3-235B-A22B-Instruct-2507`. Hardware: AMD Ryzen 5 5600X 6-Core Processor; GPU NVIDIA GeForce RTX 4060, 8188 MiB, 610.88; Windows-11-10.0.26300-SP0; speech http://127.0.0.1:8089 (local); models remote: Nebius Token Factory. Spend over the referenced runs $1.0470; every referenced model call after the pre-registration: True.

12 lectures, 8.84 min, 264.8 s without narration; failed runs: none.

Pre-registered metrics (value [95% CI over videos] (num/den)):

| metric | value |
|---|---|
| overlap with narration, standard cues (s) | 0.000 |
| extended freeze points inside narration | 0 |
| capacity violations (standard cues outside their pause) | 0 |
| visual-fact recall, standard | 2.6% [0.5%, 5.1%] (6/235) |
| visual-fact recall, with extended | 14.0% [7.9%, 19.7%] (33/235) |
| timely recall, with extended | 14.0% [7.9%, 19.7%] (33/235) |
| exact title match, standard / with extended | 16.7% [0.0%, 36.7%] (5/30) / 53.3% [28.1%, 76.3%] (16/30) |
| exact chart-value match, standard / with extended | 0.0% [0.0%, 0.0%] (0/24) / 12.5% [0.0%, 33.3%] (3/24) |
| hallucination, claims | 18.1% [4.0%, 37.2%] (19/105) |
| hallucination, cues with an unsupported claim | 25.8% [7.1%, 43.8%] (8/31) |
| absent decoys stated (judge) | 0/48 |
| decoy numbers in cues | 0 |
| redundancy, cues restating narration | 0.0% [0.0%, 0.0%] (0/31) |
| spoken facts restated by a cue | 7.8% [2.0%, 14.3%] (4/51) |
| model cost per minute (USD; CI of model + web search) | 0.0267 [0.0235, 0.0301] |
| processing seconds per minute | 79.2 [73.7, 84.7] |

Recall by kind of fact, standard / with extended (pre-registered):

- bullet: 0.0% [0.0%, 0.0%] (0/113) / 6.2% [0.0%, 14.4%] (7/113)
- chart_trend: 0.0% [0.0%, 0.0%] (0/11) / 9.1% [0.0%, 27.3%] (1/11)
- chart_value: 0.0% [0.0%, 0.0%] (0/24) / 12.5% [0.0%, 33.3%] (3/24)
- label: 1.8% [0.0%, 5.4%] (1/57) / 10.5% [1.9%, 20.0%] (6/57)
- title: 16.7% [0.0%, 36.7%] (5/30) / 53.3% [28.1%, 76.3%] (16/30)

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

Every claim the pre-registered hallucination metric counted as unsupported, with its kind as the run's cues.json records it and a class from a manual review: each claim read against the slide on screen at its cue (key.json slide inventory and style text) and the slide image in suite_a/out/<lecture>/slide_<n>.png; done after the scored run, no model calls. Made from the scored results (`suite_a.json`, sha256 `4d60aef6bf50`) and the judge answers already given; the pre-registered number is unchanged.

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

## Suite B: Lull against human audio description

Pre-registration: tag `suite-b-prereg-2` -> commit `f308f9034fd87b7c8ac7dcb52c4f601ac06cddd6` (2026-10-28T04:54:30+01:00), reference set sha256 `7d8b9fdfec4936aca0350efb712ed39e006aee77e4df9a2f239eb53e0a372209` (first pin `suite-b-prereg`). Scored 2026-10-28T20:41:40+00:00 at commit `466867dce8e4`. Primary judge `nvidia/Nemotron-3-Ultra-550b-a55b`; second judge `Qwen/Qwen3-235B-A22B-Instruct-2507`; judge prompt sha256 `78e6e4f632209283febe150df2f41ad9007b6e022a67cca558bf6b595d5257da`. Window +/-15 s (+/-30 s for blocks over 30 s). Scoring spend $0.8649; every scoring call after the pre-registration: True.

| video | Lull run | engine | covers (s) |
|---|---|---|---|
| elephants_dream | `final-ed2` | 0.2.0 | 0-658.094 |
| woodshole | `final-woodshole` | 0.2.0 | 0-173.311 |
| aquatic_connectivity | `final-aquatic_connectivity` | 0.2.0 | 0-265.054 |
| csc_intro | `final-csc_intro` | 0.2.0 | 0-44.878 |

Recall of the describers' scored facts (visual, on-screen text) [95% CI over reference lines]. Elephants Dream: window primary, anywhere supplementary. USGS: window and anywhere with equal weight (a USGS anchor is the AD version's freeze point); one describer per USGS video, so no human ceiling there.

| reference | facts (lines) | window, standard | window, with extended | anywhere, standard | anywhere, with extended |
|---|---|---|---|---|---|
| hunt | 124 (63) | 0.0% [0.0%, 0.0%] (0/124) | 0.8% [0.0%, 2.8%] (1/124) | 1.6% [0.0%, 4.3%] (2/124) | 2.4% [0.0%, 5.7%] (3/124) |
| pfeiffer | 152 (59) | 1.3% [0.0%, 3.4%] (2/152) | 1.3% [0.0%, 3.4%] (2/152) | 2.6% [0.6%, 5.5%] (4/152) | 3.3% [0.7%, 6.4%] (5/152) |
| usgs_ad | 198 (116) | 0.0% [0.0%, 0.0%] (0/198) | 4.5% [1.5%, 8.5%] (9/198) | 0.0% [0.0%, 0.0%] (0/198) | 7.1% [2.9%, 12.2%] (14/198) |
- usgs_ad without the 9 facts holding unresolved ASR terms: window, with extended 4.2% [1.1%, 8.2%] (8/189) (all-facts USGS recall is a lower bound)

Human baseline (supplementary; same judge, prompt and window):

- hunt_lines_vs_pfeiffer_facts: window 38.8% [27.6%, 50.6%] (59/152); anywhere 40.1% [28.8%, 52.0%] (61/152)
- pfeiffer_lines_vs_hunt_facts: window 42.7% [32.8%, 53.3%] (53/124); anywhere 46.0% [36.1%, 56.5%] (57/124)

Second judge (supplementary):

- hunt: window, with extended 6.5% [2.4%, 11.3%] (8/124); agreement 94.4% agreement, kappa 0.21 (n=124)
- pfeiffer: window, with extended 2.6% [0.6%, 5.3%] (4/152); agreement 96.1% agreement, kappa -0.02 (n=152)
- usgs_ad: window, with extended 17.7% [11.7%, 24.7%] (35/198); agreement 85.9% agreement, kappa 0.31 (n=198)

### Talking over speech

Against the reference transcript of the original (refs/asr: same recogniser, words only, independent of the Lull run):

| video | side | description s | over speech s | share | note |
|---|---|---|---|---|---|
| elephants_dream | lull_standard | 141.5 | 0.0 | 0.0% |  |
| elephants_dream | pfeiffer_vtt | 405.7 | 11.9 | 2.9% | display time, not voiced |
| elephants_dream | hunt_vtt | 328.8 | 3.7 | 1.1% | display time (his voice: hunt_wav) |
| elephants_dream | hunt_wav | 265.0 | 2.7 | 1.0% | voiced |
| elephants_dream | lull_extended freeze points inside speech | | 6 | | |
| woodshole | lull_standard | 0.0 | 0.0 | n/a |  |
| woodshole | lull_extended freeze points inside speech | | 22 | | |
| aquatic_connectivity | lull_standard | 10.2 | 0.0 | 0.0% |  |
| aquatic_connectivity | lull_extended freeze points inside speech | | 14 | | |
| csc_intro | lull_standard | 4.8 | 0.0 | 0.0% |  |
| csc_intro | lull_extended freeze points inside speech | | 1 | | |

Against the Lull run's own speech detection (its listen output, diarized turns included; the same spans for every side):

| video | side | description s | over speech s | share | note |
|---|---|---|---|---|---|
| elephants_dream | lull_standard | 141.5 | 0.0 | 0.0% | 0 by construction against its own speech detection |
| elephants_dream | pfeiffer_vtt | 405.7 | 15.6 | 3.9% | display time, not voiced |
| elephants_dream | hunt_vtt | 328.8 | 5.2 | 1.6% | display time (his voice: hunt_wav) |
| elephants_dream | hunt_wav | 265.0 | 3.4 | 1.3% | voiced |
| elephants_dream | lull_extended freeze points inside speech | | 7 | | |
| woodshole | lull_standard | 0.0 | 0.0 | n/a | 0 by construction against its own speech detection |
| woodshole | lull_extended freeze points inside speech | | 22 | | |
| woodshole | USGS AD on its own timeline | 257.0 | 0.0 | 0.0% | not comparable (see below); original narration missing from the AD version 13.4 s |
| aquatic_connectivity | lull_standard | 10.2 | 0.0 | 0.0% | 0 by construction against its own speech detection |
| aquatic_connectivity | lull_extended freeze points inside speech | | 14 | | |
| aquatic_connectivity | USGS AD on its own timeline | 112.6 | 0.0 | 0.0% | not comparable (see below); original narration missing from the AD version 0.0 s |
| csc_intro | lull_standard | 4.8 | 0.0 | 0.0% | 0 by construction against its own speech detection |
| csc_intro | lull_extended freeze points inside speech | | 1 | | |
| csc_intro | USGS AD on its own timeline | 19.2 | 0.0 | 0.0% | not comparable (see below); original narration missing from the AD version 0.0 s |

USGS AD lives in pauses the AD version inserts; its own-timeline overlap compares two parts of one transcript and cannot show simultaneous speech, so it is not comparable to a standard track, which has to fit the original's own pauses (see the README).

Extended freeze points inside speech (against the reference transcript; extended cues): elephants_dream 6 of 31, woodshole 22 of 22, aquatic_connectivity 14 of 14, csc_intro 1 of 1. Suite A pre-registers 0 for its lectures; these videos miss that bar. In continuous narration the extended track pauses the picture between words, inside a sentence: the narration is paused, not talked over, but the sentence is interrupted.

### Description density (words per minute of video)

| video | side | words | per min |
|---|---|---|---|
| elephants_dream | lull_standard | 416 | 37.93 |
| elephants_dream | lull_with_extended | 807 | 73.58 |
| elephants_dream | pfeiffer | 979 | 89.26 |
| elephants_dream | hunt | 761 | 69.38 |
| woodshole | lull_standard | 0 | 0.0 |
| woodshole | lull_with_extended | 317 | 109.74 |
| woodshole | usgs_ad | 1023 | 354.16 |
| woodshole | usgs_ad_per_ad_version_min | 1023 | 149.95 |
| aquatic_connectivity | lull_standard | 26 | 5.89 |
| aquatic_connectivity | lull_with_extended | 282 | 63.84 |
| aquatic_connectivity | usgs_ad | 335 | 75.83 |
| aquatic_connectivity | usgs_ad_per_ad_version_min | 335 | 55.53 |
| csc_intro | lull_standard | 8 | 10.7 |
| csc_intro | lull_with_extended | 29 | 38.77 |
| csc_intro | usgs_ad | 79 | 105.62 |
| csc_intro | usgs_ad_per_ad_version_min | 79 | 74.23 |

Judge probe, primary `nvidia/Nemotron-3-Ultra-550b-a55b`: positives credited 14/14, near misses rejected 19/19, cross-credits 0.

Judge probe, second `Qwen/Qwen3-235B-A22B-Instruct-2507`: positives credited 14/14, near misses rejected 19/19, cross-credits 0.

## Controls: does the verifier catch false descriptions?

### Negative control

Pre-registration: tag `controls-prereg-2` -> commit `f30231c37c0d7a7a07593f8d9ba3a8decbbdb129` (2026-10-28T05:21:36+01:00). Run 2026-10-28T14:33:25+00:00 at commit `63307fd8eb0b`. Verifier `nvidia/Nemotron-3.5-Super-VL`, temperature 0.0 (the pipeline's settings). Spend $0.0825.

Headline over the same-video rules (38 cues; value [95% CI over cues]):

| metric | value |
|---|---|
| false accept: UPPER BOUND on verifier failure (a claim may be true of the other footage too) | 13.2% [2.6%, 23.7%] (5/38) |
| per-claim false accept | 21.5% [8.8%, 36.4%] (23/107) |
| discriminative false accept, Suite A (11 cues) | 0.0% [0.0%, 0.0%] (0/11) |
| true accept on the true clip (optimistic: cues that passed verification) | 100.0% [100.0%, 100.0%] (38/38) |

| mismatch rule | cues | false accept | per-claim false accept | true accept |
|---|---|---|---|---|
| another slide | 11 | 0.0% [0.0%, 0.0%] (0/11) | 3.0% [0.0%, 10.7%] (1/33) | 100.0% [100.0%, 100.0%] (11/11) |
| other events, 30 s away | 27 | 18.5% [3.7%, 33.3%] (5/27) | 29.7% [12.5%, 48.8%] (22/74) | 100.0% [100.0%, 100.0%] (27/27) |
| other video | 2 | 0.0% [0.0%, 0.0%] (0/2) | 0.0% [0.0%, 0.0%] (0/5) | 100.0% [100.0%, 100.0%] (2/2) |

Every false accept is listed under Known failures.

### Ablation: Suite A without frame verification

Pre-registration: tag `controls-prereg-2` (Suite A pin `suite-a-prereg`). Run 2026-10-28T14:52:55+00:00 at commit `f33995831726` on engine 0.2.0+980454e1b9cd77d0; the with-arm is results/suite_a.json. Spend $0.7873.

| metric | with verification | without | difference [95% CI over videos] |
|---|---|---|---|
| hallucination_claims | 18.1% | 19.5% | +1.4% [-16.2%, +16.1%] |
| hallucination_cues | 25.8% | 19.5% | -6.3% [-27.7%, +14.0%] |
| hallucination_voiced_text (headline) | 11.0% | 5.9% | -5.1% [-18.4%, +4.5%] |
| voiced_cues_with_unsupported | 12.9% | 17.1% | +4.2% [-13.6%, +22.5%] |
| recall_standard | 2.6% | 0.9% | -1.7% [-4.5%, +0.5%] |
| recall_with_extended | 14.0% | 28.9% | +14.9% [+6.2%, +23.1%] |
| decoys_stated | 0.0% | 0.0% | +0.0% [+0.0%, +0.0%] |

With verification off, recall with the extended track went from 14.0% to 28.9% (the interval of the difference excludes 0), and unsupported statements did not change significantly (every hallucination difference's interval includes 0).

Share of cues carrying the verifier's claim split: with 1.0, without 0.0 (without it, the claims metric is judged per whole cue; the voiced-text metric splits both arms the same way).

## Deviations from the pre-registrations

- **Suite A**: Scorer rules the key does not spell out, applied to primary metrics: a verdict the judge leaves out counts against the system (fact not conveyed, claim unsupported, cue redundant, decoy stated) and is listed under "judge problems"; extended cues count only when the run reports extended_produced.
- **Suite A**: a08's tie (its line chart shows the highest value, 100000, at three categories; the key names the first). Primary recall and exact match follow the key as written (the judge sees the fact as the key states it; the exact match needs the key's label). Supplementary, reported next to the primary: tie-aware recall (a fact is also credited when a cue states the highest value with any tied category, checked deterministically) and tie-aware exact match. Added after the pre-registration.
- **Suite A**: Supplementary metrics (next section), all labelled "supplementary" in every report: hallucination of the voiced text (its own call; a cue without an answer counts as one unsupported statement), claims the cue text does not say, decoys stated in their absent part only, the second judge with agreement and kappa, the probe's near misses, cross-credits and the voiced-text probe.
- **Suite A**: Reporting: silence and pause counts per video, hardware, the run inventory, the zero-spend rebuild bundle, the pinned tag.
- **Suite B**: First pin: tag suite-b-prereg -> commit 5d628cb2099d23adbbb9faabba9801b9af9d942e, 2026-10-28 03:07:27 UTC, reference set 4c6aca38...b66.
- **Suite B**: A smoke preceded the second pin: the scorer (first committed at 03:14:18 UTC) was run on the existing ed90 run, Elephants Dream 60-150 s, between 03:14 and 03:18 UTC (Lull 0/46 facts with Ultra, 8/46 with Qwen; below). Its facts (ED, 60-150 s) did not change.
- **Suite B**: Second pin: tag suite-b-prereg-2 -> commit f308f90, 03:54:30 UTC. Changed: the 17 ASR corrections above; the facts of the 15 corrected lines (only those) were split again with the same, unchanged extraction prompt (149 -> 151 woodshole facts); every other fact is byte-identical in content (blocks added as fields); blocks and the long-block rule: the facts of a block longer than 30 s get a +/-30 s window instead of +/-15 s; USGS: the anywhere variant is reported with the same weight as the windowed one (the anchors are freeze points); recall is also reported without the facts that still hold an unresolved term, and with any left the all-facts USGS recall is a lower bound; the judge probe (probes.json, 33 hand-written cues for 14 facts of two videos, none in the smoke clip): 14 positives (paraphrases with descriptions instead of names, a fact plus an extra detail) that must be credited and 19 near misses (wrong count, colour or value, swapped character, gist only, invented detail) that must not. With the first prompt Ultra got 14/14 and 19/19, Qwen 13/14 and 17/19 (it credited "Three people stand on a small bridge" for two people, and a swapped character). One rule was added to the judge prompt: counts, numbers, colours, names, written words and who does what to whom must agree with the fact (its examples are not from the probe). With it both judges got 14/14 and 19/19 (probe_results.json; the first run in probe_results_prompt1.json). Nothing else in the prompt changed; results need runs of all four videos; the overlap presentation rule (below); NOTICE files.
- **Suite B**: Engine commits of the scored runs: Suite A, the negative control and the ablation ran on the engine of main 61e9d81 (merged into the evaluation branch as 7168ea5). The first recorded run of the full Elephants Dream (run id final-ed) failed in publish on a deterministic engine bug: a keyframe time after the file's last video frame. It is listed under the known failures and stays listed. The bug was fixed on main 0b83117 (keyframe and freeze times clamped to the video stream's last frame; nothing else changed), merged, and the full film was run once more under a fresh run id (final-ed2) with the same command and options (paused once for planned machine maintenance, then resumed from its stage cache). Suite B is scored on that run and on the three USGS runs made on 61e9d81 (Suite B does not require one engine across its runs).
- **Suite B**: Batch splitting on cut-off answers: added after the second Suite B attempt, in the scorer only (no change to prompts, rules or facts). When a judge's answer (either judge, any batch) is cut off by its token limit, even after the engine's retry with twice the limit, the batch is judged again in halves with the same prompt, schema, model and temperature, down to single facts; only a single fact whose answer is still cut off falls under the pre-registered rule (no verdict: not conveyed, listed). Every split is listed with the judge problems; saved answers and cut-offs are reused, not paid for twice.
- **Suite B**: Account credit outage: at 12:34 UTC the provider refused every call (HTTP 402, account credits exhausted) while the recorded runs were being made; the account credits were topped up and the runs resumed from their stage caches at 13:36 UTC. Suite A's scored run had finished before; the controls ran after.
- **Suite B**: Three Suite B invocations: the first stopped on the per-run cap of $0.30 that the scorer takes from Suite A's client setup (it needs more for a full film's judging), the second on a judge answer cut off by its token limit, the third (with the batch splitting above and a higher per-run cap) completed. The first two wrote nothing; their saved judge answers were reused.
- **Controls**: per-rule reporting, with a same-video headline called an upper bound;
- **Controls**: the discriminative false accept;
- **Controls**: the true-accept disclosure;
- **Controls**: the 0.5 s event-overlap rule and per-row event overlap;
- **Controls**: the title rule for slide fallbacks;
- **Controls**: the ablation's all-videos-paired check.

## Known failures

- **Suite A: held cues, "the failed claims share every word with claims that passed"**: 10 cues held for this reason in 12 runs. A held cue is not voiced.
- **Suite A: held cues, "nothing left once the unverified claims were removed"**: 1 cue held for this reason in 12 runs. A held cue is not voiced.
- **Suite B: held cues, "nothing left once the unverified claims were removed"**: 13 cues held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "the failed claims share every word with claims that passed"**: 9 cues held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "does not fit"**: 4 cues held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "compression left N sentence for N visuals, twice"**: 2 cues held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "not verified as voiced"**: 2 cues held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "each claim is supported but a sentence is not"**: 1 cue held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "not verified after N repairs"**: 1 cue held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "not verified after N rounds"**: 1 cue held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "the cut would leave "a" without "into""**: 1 cue held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "the cut would leave "a" without "up""**: 1 cue held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "the cut would leave "stand" without "Emo""**: 1 cue held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: held cues, "the verifier rejected a removed claim again"**: 1 cue held for this reason in 4 runs. A held cue is not voiced.
- **Suite B: 15 suspicious ASR terms left as heard**: aquatic_connectivity 2, woodshole 13: "Signed", "clover", "Athymmetry", "crashed down", "Title Creek", "Aeros", "pescals", "Start", "anti flux", "unpounded", "Well Fleet", "clover", "entries", "Word Science", "role.org". No on-screen text or narration gave the right words, so they stay as the recogniser heard them and USGS recall over all facts is a lower bound; 9 scored facts holding them are set aside in the recall reported without them.
- **Negative control: false accept, bbb120 c002 (other events, 30 s away)**: "A grassy field with scattered trees stretches under a pink and blue sky." was fully supported on bbb120 45.00-57.00 s (its true clip 2.70-14.70 s); raw only (no key for this video).
- **Negative control: false accept, bbb120 c020 (other events, 30 s away)**: "In a moment, the rabbit leaps up on its hind legs, reaching for a butterfly near a tree." was fully supported on bbb120 80.50-85.22 s (its true clip 115.28-120.00 s); raw only (no key for this video).
- **Negative control: false accept, ed90 c010 (other events, 30 s away)**: "Both stand facing each other, gesturing." was fully supported on ed90 27.00-35.00 s (its true clip 65.00-73.00 s); raw only (no key for this video).
- **Negative control: false accept, ed90 c013 (other events, 30 s away)**: "The two figures fall through the dark space, tumbling among the wires and platforms." was fully supported on ed90 42.00-51.00 s (its true clip 81.00-90.00 s); raw only (no key for this video).
- **Negative control: false accept, tos180 c012 (other events, 30 s away)**: "The robot's lit eyes show in close-up." was fully supported on tos180 129.00-133.10 s (its true clip 94.90-99.00 s); raw only (no key for this video).
- **Run-to-run variance**: Lull's descriptions come from remote models that do not reproduce an answer for a fixed seed (Nemotron 3.5 Lightning ignored the seed in our checks), so the same video gives different cues from one run to the next. Each suite scores one pre-registered run (Suite A: the first complete --all invocation on the final engine) and does not average over runs; the intervals are bootstrapped over videos or reference lines, not over repeated runs.
- **Lull conveys little of what the human describers chose to say**: In the scored Suite B, the primary judge credited Lull's cues (standard and extended, inside the window) with 1 of JJ Hunt's 124 Elephants Dream facts (0.8 %), 2 of Silvia Pfeiffer's 152 (1.3 %) and 9 of the USGS describers' 198 (4.5 %); one human describer judged against the other gets 38.8 % and 42.7 %. Over the full film Lull voiced 37.9 words per minute on the standard track (73.6 with the extended track); Pfeiffer's track has 89.3 and Hunt's 69.4.
- **Extended freezes fall inside continuous narration**: Suite A pre-registers 0 extended freeze points inside speech for its lectures. On Suite B's real videos the extended track paused the picture inside speech at 6 of 31 extended cues (Elephants Dream), 22 of 22 (woodshole), 14 of 14 (aquatic_connectivity) and 1 of 1 (csc_intro), against the reference transcript. In continuous narration the extended track pauses the picture between words, mid-sentence: the narration is paused, not talked over, but the sentence is interrupted.
- **Suite B woodshole: the standard track voiced nothing**: On woodshole the standard track voiced 0 words: none of its cues was verified (5 held, 0 verified); everything Lull said there was on the extended track (22 cues).
- **The two Suite B judges disagree**: The second judge (Qwen3 235B) credits Lull far more often than the primary one (Nemotron 3 Ultra) on USGS: 17.7 % against 4.5 % of the facts (window, with extended), raw agreement 85.9 %, kappa 0.31. On Pfeiffer's facts the raw agreement is 96.1 % but kappa is -0.02 (both judges credit almost nothing, so the agreement is no better than chance). The primary judge's numbers are the pre-registered ones.
- **Lecture pauses swallowed before the speech-span fix**: Before the engine's corrections the listen stage widened narration spans by 0.2-0.6 s and merged sentences across planned pauses of about 1.9 s, so a Suite A lecture had only 4-5 speech-free windows and the standard track voiced little more than slide titles; extended cues froze the picture before their slide appeared. Found in the Suite A smoke (a01, a02, pre-fix engine) and fixed in the engine; the scored run is on the fixed engine and the smoke numbers are not results.
- **TTS nondeterminism**: Magpie TTS returns slightly different audio for the same sentence from one call to the next (seed and temperature parameters did not change that in our checks): a01 re-rendered from an empty cache came out 48.56 s instead of 48.40 s. Suite A's lectures are rendered once and cached (the render tarball reproduces them bit for bit), and Lull's own cue durations vary slightly between runs.
- **Elephants Dream (full film) failed in publish**: The scored recorded run of the full film (final-ed) finished its descriptions (43 verified, 21 held, 43 extended) and then failed while saving keyframes: a cue's keyframe time, 656.3 s, lies after the last video frame of this file (the video stream ends at 653.8 s, the audio at 658.1 s), because the engine bounds keyframe times by the container's duration rather than the video stream's. ffmpeg returned no frame and the run stopped without publishing. Counted as a pipeline failure. The bug was then fixed in the engine (main 0b83117, keyframes kept inside the video stream) and the full film was run once more (final-ed2); the site's full-film showcase comes from that run, and so does Suite B (see the Suite B deviations).

## Cost accounting (draft)

A draft for comparison with the provider's billing, not yet checked against it. The engine's model ledger as of 2026-10-28T20:56:52+00:00: 3109 calls from 2026-10-27T20:18:51+00:00 to 2026-10-28T20:41:40+00:00 (UTC), every run on the evaluation machine (engine development, the evaluation and its judges). `tokens in` is the whole prompt as the provider reported it (`usage.prompt_tokens`), cached part included; `tokens out` is `usage.completion_tokens`. Token Factory total in the ledger: $5.9754.

| provider | model | calls | tokens in | of which cached | tokens out | cost (ledger) | cost (recomputed) |
|---|---|---|---|---|---|---|---|
| tavily | tavily-search (72 credits) | 72 | 0 | 0 | 0 | $0.5760 | $0.5760 |
| tokenfactory | Qwen/Qwen3-235B-A22B-Instruct-2507 | 116 | 145,551 | 42,608 | 86,611 | $0.0747 | $0.0747 |
| tokenfactory | nvidia/Nemotron-3-Ultra-550b-a55b | 328 | 653,973 | 8,704 | 1,302,631 | $4.5542 | $4.5542 |
| tokenfactory | nvidia/Nemotron-3.5-Super-VL | 1,066 | 3,061,487 | 280,704 | 355,329 | $1.1709 | $1.1709 |
| tokenfactory | nvidia/Nemotron-3_5-Lightning | 1,527 | 1,199,397 | 0 | 431,977 | $0.1756 | $0.1756 |

Per UTC day (billing periods are usually days):

| day | provider | model | calls | tokens in | tokens out | cost (ledger) |
|---|---|---|---|---|---|---|
| 2026-10-27 | tavily | tavily-search | 16 | 0 | 0 | $0.1280 |
| 2026-10-27 | tokenfactory | nvidia/Nemotron-3-Ultra-550b-a55b | 6 | 25,143 | 17,931 | $0.0789 |
| 2026-10-27 | tokenfactory | nvidia/Nemotron-3.5-Super-VL | 100 | 226,086 | 15,944 | $0.0822 |
| 2026-10-27 | tokenfactory | nvidia/Nemotron-3_5-Lightning | 19 | 8,436 | 1,694 | $0.0009 |
| 2026-10-28 | tavily | tavily-search | 56 | 0 | 0 | $0.4480 |
| 2026-10-28 | tokenfactory | Qwen/Qwen3-235B-A22B-Instruct-2507 | 116 | 145,551 | 86,611 | $0.0747 |
| 2026-10-28 | tokenfactory | nvidia/Nemotron-3-Ultra-550b-a55b | 322 | 628,830 | 1,284,700 | $4.4753 |
| 2026-10-28 | tokenfactory | nvidia/Nemotron-3.5-Super-VL | 966 | 2,835,401 | 339,385 | $1.0887 |
| 2026-10-28 | tokenfactory | nvidia/Nemotron-3_5-Lightning | 1,508 | 1,190,961 | 430,283 | $0.1747 |

Prices, USD per 1M tokens (input / cached input / output):

| model | the engine costs calls at | source | published | source |
|---|---|---|---|---|
| nvidia/Nemotron-3-Ultra-550b-a55b | 1.00 / 0.12 / 3.00 | engine/src/lull/ledger.py `PRICES` (unchanged since the ledger's first commit) | 1.00 / 0.12 / 3.00 | the project's research notes on models (2026-10-27), condensed from the Token Factory model catalog (tokenfactory.nebius.com/api/public/models_info) |
| nvidia/Nemotron-3.5-Super-VL | 0.30 / 0.06 / 0.90 | engine/src/lull/ledger.py `PRICES` (unchanged since the ledger's first commit) | 0.30 / 0.06 / 0.90 | the project's research notes on models (2026-10-27), condensed from the Token Factory model catalog (tokenfactory.nebius.com/api/public/models_info) |
| nvidia/Nemotron-3_5-Lightning | 0.06 / 0.06 / 0.24 | engine/src/lull/ledger.py `PRICES` (unchanged since the ledger's first commit) | 0.06 / n/a / 0.24 | the project's research notes on models (2026-10-27), condensed from the Token Factory model catalog (tokenfactory.nebius.com/api/public/models_info) |
| Qwen/Qwen3-235B-A22B-Instruct-2507 | 0.20 / 0.05 / 0.60 | eval/suite_a/score.py `SECOND_JUDGE_PRICE` (Token Factory `/v1/models?verbose=true`, read 2026-10-28) | n/a | n/a |

Web search (Tavily) is costed at $0.008 per credit for reporting only; Tavily bills it, not Token Factory. Lightning: the notes record no cached-input price; the engine charges cached tokens at the input price, and the ledger has no cached Lightning tokens.

To check against the billing: the per-model token totals and the per-day costs above. What is not in this ledger and would show only in the billing: calls made with another `LULL_HOME` (for example inside the API container), requests sent outside the engine's client (manual probes), requests the provider billed but the client never saw answered (a timeout after the provider finished), and anything before the first call above. Whether the provider bills reasoning tokens inside `completion_tokens` (as the ledger assumes) is to be confirmed on the billing page.

## Run log

The scored runs on the final engine (main 61e9d81, merged into the evaluation branch as
7168ea5), 2026-10-28, times UTC, kept by hand while they happened. Spend is the change of the
engine's model ledger over each step; the ledger stood at $2.0125 before the scored runs began.
LULL_CAP_DAY=10.

What ran more than once, and why (nothing was run again to get a better number):

- The recorded runs were interrupted once by the provider (account credits exhausted, HTTP
  402) and resumed from their stage caches with the same commands and run ids.
- The full Elephants Dream run (`final-ed`) failed in publish on a deterministic engine bug.
  After the engine fix (main 0b83117, crash fix only) the film was run once more under a fresh
  run id (`final-ed2`); that run was paused once for planned machine maintenance and resumed
  from its stage cache. The failed attempt stays listed.
- Suite B was invoked three times: the first invocation stopped on an inherited per-run spend
  cap, the second on a judge answer cut off by its token limit; neither wrote results. The
  third, after a disclosed scorer change (batch splitting on cut-off answers), completed and
  reused the saved judge answers.
- One negative-control invocation stopped in 1 s, before any model call, because a run path
  was passed in a form the scorer could not open.

| step | started | finished | wall | spend | outcome |
|---|---|---|---|---|---|
| 0. merge main, adapter for the final verifier | about 11:33 | 11:49:40 | about 17 min | $0 | merged without conflicts (tf.py already had the optional seed); engine tests 782 passed, 6 skipped; eval tests 190 passed |
| 1. Suite A scored run (`suite_a.score --all --cap 2.00`) | 11:50:29 | 12:17:05 | 26.6 min | $0.9146 (scorer: $1.0470 over the referenced run ids, cached probe answers included) | invocation 20261028T115030Z, 12/12 published, none failed; results committed (1ee9ece) |
| 2. recorded runs (`lull describe --mode extended`, fresh run ids final-*) | 12:18:06 | 12:37:58 (interrupted) | 20 min | $0.3648 | interrupted: Token Factory answered 402 `credit_balance_exhausted` at 12:34; an infrastructure failure, not a pipeline result |
| 2, resumed from the stage caches after the account credits were topped up | 13:36:47 | 14:21:20 | 44.6 min | $0.5052 | 6 of 7 published; `ed` FAILED in publish (a pipeline failure, below); site exports ed90, bbb120, tos180 |
| 2, full-film re-run on main 0b83117 (`final-ed2`) | 15:14:25 | 20:12:10 | 34.3 min + 4.9 min (paused 15:48-20:07) | $0.2937 | published; exported as the site's `ed` |
| 3. Suite B | 20:14:12 | 20:41:40 | three invocations (below) | $1.0147 | the first two stopped without results; the third completed (results/suite_b.json, SUITE_B.md) |
| 4a. negative control (`controls.negative --suite-a --showcase ed90,bbb120,tos180 --cap 0.50`) | 14:21:59 | 14:33:25 | 11.4 min | $0.0825 | results/controls_negative.json, CONTROLS.md (the Suite A runs and three site runs; `ed` had failed) |
| 4b. ablation (`LULL_NO_VERIFY=1 controls.ablation --all --cap 2.00`) | 14:33:47 | 14:52:56 | 19.2 min | $0.7873 | results/ablation.json, ABLATION.md; 12/12 runs, same engine fingerprint as Suite A (0.2.0+980454e1b9cd77d0) |
| 5. Report | | | | $0 | RESULTS.md and results/benchmarks.json from the complete results (Suite A, Suite B, negative control, ablation) |

Spend of the scored runs: ledger $2.0125 before, $5.9754 after: **$3.9629** (of which the
full-film re-run and Suite B $1.3084).

### The recorded runs

Inputs: ed90 is the existing Elephants Dream excerpt 60-150 s (sha256 0daa75ac9328b0c0...);
bbb120 is Big Buck Bunny 0:00-2:00 and tos180 Tears of Steel 3:30-6:30, both cut with ffmpeg;
the full film and the USGS videos are used as they are.

- `ed` (Elephants Dream, full film, entertainment, LULL_CAP_RUN=1.50): listen, watch, research
  and plan finished; finalize was interrupted by the 402 after cue c041 (4:56 of 10:58); exit 3;
  170 calls, $0.3648.
- `ed90`, `bbb120`, `tos180`, `woodshole`, `aquatic_connectivity`, `csc_intro` (started one
  after another, 12:34:51 to 12:37:40): listen finished (local speech), watch met the 402 at
  its first call; exit 3 each; no spend. (Corrected: an earlier version of this log said the
  last four were not started and bbb120 was stopped; the runs' own logs show all six reached
  the 402.)

### First interruption: account credits exhausted (HTTP 402)

- **12:34** Token Factory answered every call with 402 `credit_balance_exhausted`. Interrupted:
  `ed` in finalize after cue c041 (4:56 of 10:58); the other six at their first watch call,
  after listen. Ledger at the interruption $3.2919.
- The account credits were topped up. **13:36:47** the runs were resumed, in the same order,
  with the same command, `--out` and `--run-id` (LULL_CAP_DAY=10; `ed` LULL_CAP_RUN=1.50;
  LULL_TAVILY_DAY=100 so the day's search cap of 60 could not cut a run's research short; the
  day ended at 51 searches). Resumed from the stage caches: `ed` its listen, watch, research,
  plan and schedule stages and the 34 finalize cues done before the interruption (finalize
  went on at c042); the other six their listen stage.
- **14:21:20** the resumed runs finished. Ledger $3.2919 before, $3.7971 after: **$0.5052**
  (`ed` $0.0963 more, $0.4611 over both attempts; `ed90` $0.0387; `bbb120` $0.0672; `tos180`
  $0.1298; `woodshole` $0.0738; `aquatic_connectivity` $0.0659; `csc_intro` $0.0336).

| run | profile | outcome | cues verified / held / extended | wall (resumed) |
|---|---|---|---|---|
| ed | entertainment | FAILED in publish (below) | 43 / 21 / 43 | 884 s |
| ed90 | entertainment | published | 10 / 1 / 2 | 231 s |
| bbb120 | entertainment | published | 12 / 6 / 2 | 316 s |
| tos180 | entertainment | published | 9 / 6 / 14 | 416 s |
| woodshole | education | published | 0 / 5 / 22 | 367 s |
| aquatic_connectivity | education | published | 4 / 4 / 14 | 343 s |
| csc_intro | education | published | 1 / 0 / 1 | 114 s |

**`ed` failed (a pipeline failure; the film was re-run once on the crash fix, below).**
Finalize finished (787.9 s) and the mixes were written; then the keyframe step asked ffmpeg
for a frame at 656.267 s (cue c083's event). This file's video stream ends at 653.757 s (its
audio at 658.074 s, the container at 658.094 s), and the engine bounded keyframe times by the
container's duration less 0.1 s, so ffmpeg wrote no frame and publish raised `MediaError`
(exit 1). Reproduced without model calls (the same batched ffmpeg command over the cached
cues: only that time fails).

Site exports (`lull export-showcase`): `ed90` ("Elephants Dream", excerpt 1:00-2:30), `bbb120`
("Big Buck Bunny", 0:00-2:00), `tos180` ("Tears of Steel (development clip, used to tune the
engine)", 3:30-6:30), each with its CC BY credit. The USGS runs are not exported (music
licences).

### The full-film re-run on main 0b83117 (a disclosed deviation)

- main 0b83117 ("engine: keyframes stay inside the video stream"; keyframe and freeze times
  clamped to the video stream's last frame, nothing else) merged as 1a85db9; engine tests 788
  passed, 6 skipped; eval tests 210 passed.
- **15:14:25** the full film was run once more under a fresh run id, `final-ed2` (same command
  and options; LULL_CAP_RUN=1.50). Ledger before: $4.6670.

### Second interruption: planned machine maintenance

- **15:48:40** the local speech server was stopped for planned machine maintenance (the GPU
  was needed elsewhere). `final-ed2` stopped in finalize at its next speech call (SpeechError,
  connection closed; exit 1; 2055 s). Nothing else ran and nothing was spent during the pause.
- Cached: listen, watch (164 events), research (5 terms), plan, schedule, and 70 finalize cues
  (the last logged: x076 at 5:02 of 10:58). Spend so far $0.2666 (403 Token Factory calls) and
  5 searches.
- **20:07:16** `final-ed2` resumed from its stage cache with the same command and run id: the
  cached stages and the 70 finalize cues were reused. It published 294 s later: 36 verified,
  28 held, 31 extended. This attempt $0.0272; `final-ed2` in all $0.2937 (481 Token Factory
  calls) and 5 searches.
- Exported as the site's `ed` ("Elephants Dream", CC BY 2.5 credit). Its showcase.json
  `costUsd` is the publishing attempt's spend ($0.0272); the whole run's $0.2937 is in the
  manifest's `summary.spend.model_usd_all_runs` (the export's
  `summary.spend.modelUsdAllRunsOfRunId`), and the cost the site shows for a showcase should
  come from that field. For ed90, bbb120 and tos180 both are the same (their first attempts
  spent nothing).

### Suite B: three invocations

- **20:14:12** first invocation (`--cap 1.20`, the deviation notes committed before it):
  stopped after 604 s, nothing written. The per-run-id spend cap of $0.30, which Suite B
  takes from Suite A's client setup unless LULL_CAP_RUN is set, stopped the Elephants Dream
  human-baseline judge at $0.2686 (6 Ultra calls). Spend $0.5064 (Lull judged: 8 Ultra and 8
  Qwen calls; baseline: 6 Ultra and 4 Qwen); every answer was kept in the judge cache.
- **20:25:23** second invocation (LULL_CAP_RUN=0.60; `--cap 0.70`, so that Suite B in all
  stays within $1.20; the saved answers were reused at no cost): stopped after 130 s, nothing
  written. The next baseline batch's Ultra answer was cut off at its token limit twice (16,000,
  then 32,000 output tokens; `TFUnusable`), an error the scorer did not catch. Spend $0.1498.
- The scorer was then changed (disclosed deviation, test-first): a batch whose answer is cut
  off is judged again in halves, down to single facts.
- **20:33:44** third invocation (466867d; LULL_CAP_RUN=0.60; `--cap 1.05`): completed in 476 s,
  results/suite_b.json and SUITE_B.md. The baseline batch cut off in the second invocation was
  answered within its limit this time, so no batch was split and no cut-off was recorded. Spend
  $0.3585. The scorer reports $0.8649, the run ids whose answers it used (this invocation and
  the first); the second invocation's $0.1498 is not among them. Suite B in all: $1.0147;
  ledger $5.9754.
