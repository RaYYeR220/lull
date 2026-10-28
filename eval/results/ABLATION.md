# Ablation: Suite A without frame verification

Added after the pre-registration of Suite A; pre-registered under tag `controls-prereg-2`. Suite A pin `suite-a-prereg`. Engine 0.2.0+980454e1b9cd77d0; commit `f33995831726`; 2026-10-28T14:52:55+00:00. Spend $0.7873.

| metric | with verification | without | difference (95% CI over videos) |
|---|---|---|---|
| hallucination_claims | 18.1% | 19.5% | +1.4% [-16.2%, +16.1%] |
| hallucination_cues | 25.8% | 19.5% | -6.3% [-27.7%, +14.0%] |
| hallucination_voiced_text (headline) | 11.0% | 5.9% | -5.1% [-18.4%, +4.5%] |
| voiced_cues_with_unsupported | 12.9% | 17.1% | +4.2% [-13.6%, +22.5%] |
| recall_standard | 2.6% | 0.9% | -1.7% [-4.5%, +0.5%] |
| recall_with_extended | 14.0% | 28.9% | +14.9% [+6.2%, +23.1%] |
| decoys_stated | 0.0% | 0.0% | +0.0% [+0.0%, +0.0%] |

Claim split carried by the cues (share of published cues): with 1.0, without 0.0. Without the verifier there is no claim split, so the claims metric is judged per whole cue in that arm; the voiced-text metric splits both arms the same way and is the headline.
