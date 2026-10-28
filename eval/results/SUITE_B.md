# Suite B: results

Reference set sha256 `7d8b9fdfec4936aca0350efb712ed39e006aee77e4df9a2f239eb53e0a372209`, pinned by tag `suite-b-prereg-2` (commit `f308f9034fd8`, 2026-10-28T04:54:30+01:00); scorer commit `466867dce8e4`; 2026-10-28T20:41:40+00:00. Primary judge `nvidia/Nemotron-3-Ultra-550b-a55b`; second judge `Qwen/Qwen3-235B-A22B-Instruct-2507`. Window +/-15 s (+/-30 s for blocks over 30 s). Scoring spend: $0.8649 (probe, judged before the pin: $0.0154).

| video | Lull run | engine | covers (s) | published cues std / ext |
|---|---|---|---|---|
| elephants_dream | `final-ed2` | 0.2.0 | 0-658.094 | 36 / 31 |
| woodshole | `final-woodshole` | 0.2.0 | 0-173.311 | 0 / 22 |
| aquatic_connectivity | `final-aquatic_connectivity` | 0.2.0 | 0-265.054 | 4 / 14 |
| csc_intro | `final-csc_intro` | 0.2.0 | 0-44.878 | 1 / 1 |

## What Lull conveys of what the describers said

Scored facts: visual and on-screen text. Elephants Dream: the window is primary, anywhere supplementary. USGS: window and anywhere carry equal weight (a USGS anchor is the freeze point of the AD version, shared by every line of a block); USGS has one describer per video, so there is no human ceiling for it.

| reference | facts (lines) | window, standard | window, with extended | anywhere, standard | anywhere, with extended |
|---|---|---|---|---|---|
| hunt | 124 (63) | 0.0% (0/124) [0%, 0%] | 0.8% (1/124) [0%, 3%] | 1.6% (2/124) [0%, 4%] | 2.4% (3/124) [0%, 6%] |
| pfeiffer | 152 (59) | 1.3% (2/152) [0%, 3%] | 1.3% (2/152) [0%, 3%] | 2.6% (4/152) [1%, 6%] | 3.3% (5/152) [1%, 6%] |
| usgs_ad | 198 (116) | 0.0% (0/198) [0%, 0%] | 4.5% (9/198) [1%, 8%] | 0.0% (0/198) [0%, 0%] | 7.1% (14/198) [3%, 12%] |

Without the facts that still hold an unresolved ASR term (refs/asr_corrections.json); with any left, the all-facts USGS recall is a lower bound:

- hunt: 0 facts set aside; window, with extended 0.8% (1/124) [0%, 3%]; anywhere, with extended 2.4% (3/124) [0%, 6%]
- pfeiffer: 0 facts set aside; window, with extended 1.3% (2/152) [0%, 3%]; anywhere, with extended 3.3% (5/152) [1%, 6%]
- usgs_ad: 9 facts set aside; window, with extended 4.2% (8/189) [1%, 8%]; anywhere, with extended 6.9% (13/189) [3%, 12%]

By kind (window, with extended):

- hunt: on_screen_text 0.0% (0/24) [0%, 0%]; visual 1.0% (1/100) [0%, 3%]; (supplementary) interpretation 0.0% (0/1) [0%, 0%]
- pfeiffer: on_screen_text 0.0% (0/37) [0%, 0%]; visual 1.7% (2/115) [0%, 4%]; (supplementary) interpretation 0.0% (0/8) [0%, 0%]
- usgs_ad: on_screen_text 12.7% (7/55) [3%, 26%]; visual 1.4% (2/143) [0%, 4%]; (supplementary) interpretation 0.0% (0/1) [0%, 0%]

Human baseline (supplementary; same judge, prompt and window, the judge is not told who wrote the cues):

- hunt_lines_vs_pfeiffer_facts: window 38.8% (59/152) [28%, 51%]; anywhere 40.1% (61/152) [29%, 52%]
- pfeiffer_lines_vs_hunt_facts: window 42.7% (53/124) [33%, 53%]; anywhere 46.0% (57/124) [36%, 56%]

Second judge (supplementary):

- hunt: window, with extended 6.5% (8/124) [2%, 11%]; agreement with the primary judge: window 94.4%, kappa 0.21 (n=124); anywhere 88.7%, kappa 0.09 (n=124)
- pfeiffer: window, with extended 2.6% (4/152) [1%, 5%]; agreement with the primary judge: window 96.1%, kappa -0.02 (n=152); anywhere 95.4%, kappa 0.34 (n=152)
- usgs_ad: window, with extended 17.7% (35/198) [12%, 25%]; agreement with the primary judge: window 85.9%, kappa 0.31 (n=198); anywhere 80.3%, kappa 0.26 (n=198)

## Talking over speech

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
| elephants_dream | lull_extended freeze points inside speech | | 7 | | 0 by construction against its own speech detection |
| woodshole | lull_standard | 0.0 | 0.0 | n/a | 0 by construction against its own speech detection |
| woodshole | lull_extended freeze points inside speech | | 22 | | 0 by construction against its own speech detection |
| woodshole | USGS AD on its own timeline | 257.0 | 0.0 | 0.0% | not comparable (see below); original narration missing from the AD version 13.4 s |
| aquatic_connectivity | lull_standard | 10.2 | 0.0 | 0.0% | 0 by construction against its own speech detection |
| aquatic_connectivity | lull_extended freeze points inside speech | | 14 | | 0 by construction against its own speech detection |
| aquatic_connectivity | USGS AD on its own timeline | 112.6 | 0.0 | 0.0% | not comparable (see below); original narration missing from the AD version 0.0 s |
| csc_intro | lull_standard | 4.8 | 0.0 | 0.0% | 0 by construction against its own speech detection |
| csc_intro | lull_extended freeze points inside speech | | 1 | | 0 by construction against its own speech detection |
| csc_intro | USGS AD on its own timeline | 19.2 | 0.0 | 0.0% | not comparable (see below); original narration missing from the AD version 0.0 s |

USGS AD lives in pauses the AD version inserts; its own-timeline overlap compares two parts of one transcript and cannot show simultaneous speech, so it is not comparable to a standard track, which has to fit the original's own pauses (see the README).

## Description density (words per minute of video)

| video | minutes | side | words | per min |
|---|---|---|---|---|
| elephants_dream | 10.97 | lull_standard | 416 | 37.93 |
| elephants_dream | 10.97 | lull_with_extended | 807 | 73.58 |
| elephants_dream | 10.97 | pfeiffer | 979 | 89.26 |
| elephants_dream | 10.97 | hunt | 761 | 69.38 |
| woodshole | 2.89 | lull_standard | 0 | 0.0 |
| woodshole | 2.89 | lull_with_extended | 317 | 109.74 |
| woodshole | 2.89 | usgs_ad | 1023 | 354.16 |
| woodshole | 2.89 | usgs_ad_per_ad_version_min | 1023 | 149.95 |
| aquatic_connectivity | 4.42 | lull_standard | 26 | 5.89 |
| aquatic_connectivity | 4.42 | lull_with_extended | 282 | 63.84 |
| aquatic_connectivity | 4.42 | usgs_ad | 335 | 75.83 |
| aquatic_connectivity | 4.42 | usgs_ad_per_ad_version_min | 335 | 55.53 |
| csc_intro | 0.75 | lull_standard | 8 | 10.7 |
| csc_intro | 0.75 | lull_with_extended | 29 | 38.77 |
| csc_intro | 0.75 | usgs_ad | 79 | 105.62 |
| csc_intro | 0.75 | usgs_ad_per_ad_version_min | 79 | 74.23 |

## Judge probe (probes.json; synthetic cues whose answer is known)

- primary `nvidia/Nemotron-3-Ultra-550b-a55b`: positives credited 14/14, near misses rejected 19/19, cross-credits 0 (gist_only 5/5, invented_detail 3/3, positive_extra 2/2, positive_paraphrase 12/12, swapped_character 3/3, wrong_colour 2/2, wrong_count 4/4, wrong_value 2/2)
- second `Qwen/Qwen3-235B-A22B-Instruct-2507`: positives credited 14/14, near misses rejected 19/19, cross-credits 0 (gist_only 5/5, invented_detail 3/3, positive_extra 2/2, positive_paraphrase 12/12, swapped_character 3/3, wrong_colour 2/2, wrong_count 4/4, wrong_value 2/2)
