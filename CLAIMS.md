# Claims and their evidence

Every claim that the README, the website or the demo video makes about Lull, where to check
it, and how far it goes. Paths are relative to the repository root. Test names are written
`file::function`.

## Timing: it never talks over dialogue

| Claim | Evidence | Limits |
| --- | --- | --- |
| Pauses are measured from word timings, not guessed by a model. | `engine/src/lull/timeline.py` (gaps from speech spans, 0.2 s guard each side, capacity in words). `engine/tests/test_timeline.py::test_two_gaps`, `::test_protected_splits_gap`, `::test_usable_windows_round_inward_and_capacity_follows_the_rounded_window`. | Only as good as the speech recogniser's word times. |
| A line never holds more words than its pause can carry. | `engine/src/lull/stages/schedule.py` (CP-SAT). `engine/tests/stages/test_schedule.py::test_capacity_never_exceeded`, `::test_random_instances_keep_invariants_and_solver_beats_greedy`. | Capacity uses the voice's measured rate; the real audio is checked again (next row). |
| A line is never voiced after the visual it describes. | `engine/tests/stages/test_schedule.py::test_event_ending_before_a_gap_is_never_placed_there`, `::test_a_visual_ending_within_a_word_of_the_gap_start_is_not_placed`. | A line may come just *before* its visual, introduced with "In a moment, …", when no pause during the visual fits. |
| Durations are measured from the real voiced audio. A long line is sped up by at most 10 %, shortened, or held. | `engine/src/lull/stages/voice.py` (`MAX_TEMPO = 1.10`). `engine/tests/stages/test_finalize.py::test_audio_five_percent_over_gets_tempo`, `::test_never_fits_is_held_after_four_rounds`. | |
| Nothing is published if a voiced line overlaps speech or another line ("0 overlaps"). | `engine/tests/stages/test_publish.py::test_publish_refuses_on_overlap_and_writes_nothing`. Suite A "overlap with narration": 0.0 s (`eval/RESULTS.md`). | Covers **recognised** speech. Singing, whispering and crowd speech the recogniser misses are not protected, and nor are music and sound effects. |

## Content: it never speaks an unsupported claim

| Claim | Evidence | Limits |
| --- | --- | --- |
| Every line is checked against the frames it describes, claim by claim and sentence by sentence, before it is voiced. | `engine/src/lull/stages/finalize.py`, prompts `engine/src/lull/prompts/split.md` and `verify.md`. `engine/tests/stages/test_finalize.py::test_all_supported_and_short_audio_is_verified`. | The judge is a model (Nemotron 3.5 Super VL) looking at sampled frames, so it can be wrong. |
| A claim the frames don't show, or contradict, is cut, and the line is checked again. The cut never changes what the kept words say. | `engine/tests/stages/test_finalize.py::test_not_visible_claim_is_removed_and_reverified`, `::test_contradicted_claim_is_removed`, `::test_a_claim_cut_never_drops_another_visual`, and the mend tests in the same file. | |
| A line that still fails is held for a person and never voiced. | `engine/tests/stages/test_finalize.py::test_claim_rejected_again_after_removal_is_held`, `::test_everything_removed_is_held`. `engine/tests/stages/test_publish.py::test_held_cues_are_never_mixed_even_with_audio`. | Holding loses coverage: a held moment goes undescribed until a person edits it. |
| The frame check rejects most lines when shown footage that doesn't contain them. | Negative control, pre-registered under `controls-prereg-2`: `eval/controls/negative.py`; result 13.2% (5 of 38; 95% CI 2.6–23.7%), an upper bound; 0 of 11 discriminative false accepts on Suite A (`eval/RESULTS.md`). Ablation (frame check off on Suite A): with the frame check off, 5.9% of voiced statements were unsupported against 11.0% with it on (difference −5.1 points, 95% CI −18.4 to +4.5), and recall with the extended track was 14.9 points higher (95% CI +6.2 to +23.1). On these synthetic lectures the frame check did not measurably reduce unsupported statements, and it cost recall. | Measured on Suite A and the showcase runs only. See [Known limitations of verification](engine/README.md#known-limitations-of-verification). |
| Unsupported claims in published lines: 18.1% of claims (19 of 105; 95% CI 4.0–37.2%). A supplementary breakdown, made after the scored run without new calls, finds one real misreading among the 19; the others are paraphrases the key's exact-wording rule does not accept, title fragments, or whole-sentence claims repeating atomic ones. | Suite A, judged against a hidden answer key (`eval/suite_a/score.py`; `eval/results/suite_a.json`; `eval/RESULTS.md`). | Synthetic lectures; the judge (Nemotron 3 Ultra) also plans the descriptions, so a second judge from another model family bounds self-preference (agreement in `eval/RESULTS.md`). |

## Coverage and quality

| Claim | Evidence | Limits |
| --- | --- | --- |
| It describes what a blind student would miss: 2.6% (6 of 235; 95% CI 0.5–5.1%) of visual facts (standard track), 14.0% (33 of 235; 95% CI 7.9–19.7%) with the extended track. | Suite A, pre-registered under `suite-a-prereg` (`eval/README.md`, `eval/RESULTS.md`). | 12 synthetic lectures, 8.8 minutes. Not real classrooms. |
| Against human audio description: on Elephants Dream Lull conveyed 0.8% of Hunt's and 1.3% of Pfeiffer's described facts near the moment they describe (2.4% and 3.3% anywhere in the film); on the USGS videos, 4.5% (7.1% anywhere). The two human describers convey 38.8% and 42.7% of each other's facts. Lull conveys little of what the human describers chose to say. | Suite B, pre-registered under `suite-b-prereg-2` (`eval/suite_b/`, `eval/RESULTS.md`). | Human describers choose differently from each other; recall against one is not a score out of 100. Elephants Dream has two describers (the human baseline); the USGS videos have one. |
| It doesn't repeat what the speaker says. | Suite A redundancy: 0 of 31 published lines judged redundant; 4 of the 51 spoken facts also appear in a line (`eval/RESULTS.md`). Lint rules in `engine/src/lull/stages/lint.py`, tests in `engine/tests/stages/test_lint.py`. | |
| Lines follow description practice: present tense, third person, no "we see", no guessed emotions, on-screen text read verbatim. | `engine/src/lull/stages/lint.py`; `engine/tests/stages/test_lint.py`. | Lint checks form, not judgement. A person's review is still the standard. |

## The report and the review

| Claim | Evidence | Limits |
| --- | --- | --- |
| WCAG 1.2.3, 1.2.5 and 1.2.7 rows are computed from the run's own data. | `engine/src/lull/stages/publish.py`. `engine/tests/stages/test_publish.py::test_wcag_needs_review_on_overlap_f113_or_held`, `::test_wcag_met_needs_four_in_five_salient_events_described`, `::test_wcag_states_why_the_extended_version_is_missing`. | "Important events" come from the watch model's own inventory. |
| A row reads "met" only after a person has reviewed every published line. | `engine/tests/stages/test_publish.py::test_wcag_says_automated_checks_until_every_line_is_reviewed`, `::test_approved_cues_are_published_and_count_as_reviewed`. Website: `web/lib/wcag.ts`; `web/test/wcag.test.ts`. | |
| Unused pauses next to undescribed content are flagged (WCAG failure F113). | `engine/tests/stages/test_publish.py::test_f113_flags_crafted_case`. | |
| The website's pages agree with the report: the same counts, held lines included. | `web/test/held-lines.test.tsx` ("the watch page's checks and the report give the same counts"). | |
| A person can edit a line; only that line is checked and voiced again. | `engine/tests/api/test_studio.py`; `web/e2e/studio.spec.ts`. | Edits are rate-limited and count against the spend caps. |

## Cost, speed, and the live service

| Claim | Evidence | Limits |
| --- | --- | --- |
| Model spend per minute of video: $0.027 (95% CI $0.024–0.030). Processing time per minute: 79 s (95% CI 74–85 s) on the recorded hardware. | Suite A, from the spend ledger and wall clock (`eval/RESULTS.md`, with the hardware). | Model calls only. Excludes hosting and the speech server's compute; Tavily searches run on free credits and are counted, not priced. |
| Every model call is shown with its role, model, tokens, latency and cost. | `engine/src/lull/ledger.py`; `engine/tests/test_ledger.py`. The live call feed: `web/components/try/CallFeed.tsx`, `web/e2e/try.spec.ts`. | |
| Spend caps are enforced before every call. A cap of 0 refuses every call. | `engine/tests/test_ledger.py::test_spend_caps_count_model_calls_only`, `::test_no_cap_is_none_and_a_zero_cap_refuses_every_call`, `::test_searches_are_capped_per_run_and_per_day`. | |
| The research stage calls Tavily at runtime. | `engine/src/lull/stages/research.py`; `engine/tests/stages/test_research.py::test_two_terms_two_searches_and_ledger`; the live test `engine/tests/stages/test_research_live.py`. | Up to 3 searches per hosted run. Research is skipped without a Tavily key. |
| A run that loses its viewer stops spending at once, and its owner can resume it. | `engine/tests/api/test_live.py::test_a_stopped_run_makes_no_model_call_after_the_stop`, `::test_a_stopped_run_is_replayed_to_its_owner_only`. Website: `web/e2e/try.spec.ts` (the stopped-and-resumed run). | |
| Visitors' runs are never listed publicly. Uploads are deleted after 7 days, copies after 24 hours. | `engine/tests/api/test_app.py::test_the_list_is_the_recorded_runs_only`; the retention sweep in `engine/src/lull/api/studio.py`. | Anyone with a run's link can open it, as the upload notice says. |
| The hosted MCP server takes sample ids only, never a URL, a path or an upload. | `engine/tests/api/test_agents.py::test_the_hosted_server_takes_no_url_path_or_upload`, `::test_refusals_carry_the_apis_codes`. | |
| The embed works pasted onto any site. | `web/e2e/developers.spec.ts` ("the embed snippet works pasted as given onto another site"). | It plays files served with open CORS: the recorded runs, or your own copies. |

## The website's own accessibility

| Claim | Evidence | Limits |
| --- | --- | --- |
| Every page passes axe with no moderate, serious or critical findings, at 1440 and 390 px. | The axe tests in `web/e2e/*.spec.ts`. | Automated checks find some problems, not all. The site hasn't been tested with blind users yet. |
| No page scrolls sideways at 320 px (WCAG 1.4.10). | `web/e2e/smoke.spec.ts` ("no page scrolls sideways at 320px"). | |
| The player works from the keyboard and offers mixed audio, spoken cues, screen-reader mode and the extended version. | `web/e2e/player.spec.ts`, `web/e2e/player-lifecycle.spec.ts`. | |
| Reports print readably. | `web/e2e/report.spec.ts` ("prints as ink on paper"). | |

## Process

| Claim | Evidence | Limits |
| --- | --- | --- |
| The answer key was fixed before anything was scored. | Annotated tags `suite-a-prereg`, `suite-b-prereg-2` and `controls-prereg-2`. The scorers refuse to run if the key, rules or generators changed since their tag (`eval/README.md`, "Pre-registration"). | Deviations from the pre-registration are listed in `eval/README.md` and `eval/RESULTS.md`. |
| The engine wasn't tuned on the evaluation's videos. | `engine/README.md`, "Development clips". | Stated practice; the tags show when the keys were fixed, not what we looked at before. |

## Claims we deliberately don't make

- **That a video described by Lull conforms to WCAG.** Lull reports what its checks found. A
  person's review is part of the bar, and the report says "needs review" until it happens.
- **That Lull replaces human describers.** Suite B measures how much of what people chose to
  say Lull also says. It is not a quality ranking, and people still review.
- **That Lull never makes a mistake.** The frame judge is a model, and its false-accept rate is
  published.
- **That the frame check lowers unsupported statements overall.** On our synthetic lectures the
  ablation did not show a measurable reduction, and the check cost recall. What it does do is
  refuse to voice what it could not confirm.
- **That the overlap guarantee covers every sound.** It covers recognised speech.
- **Real-time or live description,** sign language, or any language other than English.
- **Identifying people.** Lull describes appearance until the video itself names someone.
- **A dollar figure for web searches.** They are counted, not priced.
- **Results beyond what was measured.** Suite A is synthetic lectures and Suite B is four
  videos. No claim is made about other genres or lengths.
- **That the demo's "live" footage is faster than real time.** The demo video shortens a
  real run and says so.
