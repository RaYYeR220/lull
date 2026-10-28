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

## Supplementary: the unsupported claims (added after the scored run, no new calls)

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
