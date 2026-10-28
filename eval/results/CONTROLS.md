# Controls: negative control

Pinned by tag `controls-prereg-2` (commit `f30231c37c0d`, 2026-10-28T05:21:36+01:00); commit `63307fd8eb0b`; 2026-10-28T14:33:25+00:00. Verifier: `nvidia/Nemotron-3.5-Super-VL`, temperature 0.0 (the pipeline's settings, through controls/verify_adapter.py). Spend $0.0825.

## Headline: same-video mismatches (38 cues; rules: another slide, other events, 30 s away)

| metric | value (95% CI over cues) |
|---|---|
| false accept (UPPER BOUND on verifier failure: a claim may be true of the other footage too) | 13.2% (5/38) [3%, 24%] |
| per-claim false accept | 21.5% (23/107) [9%, 36%] |
| discriminative false accept, Suite A (11 cues: the cue conveys a fact of the true slide the mismatched slide does not show) | 0.0% (0/11) [0%, 0%] |
| true accept on the true clip (optimistic: these cues already passed verification; edited cues excluded) | 100.0% (38/38) [100%, 100%] |

## By mismatch rule (the other-video fallback is never in the headline)

| rule | cues | false accept | per-claim false accept | true accept |
|---|---|---|---|---|
| another slide | 11 | 0.0% (0/11) [0%, 0%] | 3.0% (1/33) [0%, 11%] | 100.0% (11/11) [100%, 100%] |
| other events, 30 s away | 27 | 18.5% (5/27) [4%, 33%] | 29.7% (22/74) [12%, 49%] | 100.0% (27/27) [100%, 100%] |
| other video | 2 | 0.0% (0/2) [0%, 0%] | 0.0% (0/5) [0%, 0%] | 100.0% (2/2) [100%, 100%] |

## Every false accept

- bbb120 c002 (standard): "A grassy field with scattered trees stretches under a pink and blue sky." | true clip 2.70-14.70 s | mismatched bbb120 45.00-57.00 s (other events, 30 s away) | raw only | events in the mismatched window {'e007': 3.7, 'e008': 8.3}
- bbb120 c020 (standard): "In a moment, the rabbit leaps up on its hind legs, reaching for a butterfly near a tree." | true clip 115.28-120.00 s | mismatched bbb120 80.50-85.22 s (other events, 30 s away) | raw only | events in the mismatched window {'e013': 1.2, 'e014': 3.519}
- ed90 c010 (standard): "Both stand facing each other, gesturing." | true clip 65.00-73.00 s | mismatched ed90 27.00-35.00 s (other events, 30 s away) | raw only | events in the mismatched window {'e006': 4.0, 'e007': 4.0}
- ed90 c013 (standard): "The two figures fall through the dark space, tumbling among the wires and platforms." | true clip 81.00-90.00 s | mismatched ed90 42.00-51.00 s (other events, 30 s away) | raw only | events in the mismatched window {'e008': 8.8, 'e009': 0.2}
- tos180 c012 (standard): "The robot's lit eyes show in close-up." | true clip 94.90-99.00 s | mismatched tos180 129.00-133.10 s (other events, 30 s away) | raw only | events in the mismatched window {'e036': 1.258, 'e037': 2.842}

## True clips not fully supported

(none)
