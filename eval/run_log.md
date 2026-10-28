# Run log

The scored runs on the final engine (main 61e9d81 merged into feat/eval as
7168ea5). Kept by hand while the runs happened, 2026-10-28, times UTC. Spend is the change of
the model ledger (~/.lull/ledger.db) over the step; the ledger stood at $2.0125 before the
task. LULL_CAP_DAY=10. Nothing below was run twice to get a better number.

| step | started | finished | wall | spend | outcome |
|---|---|---|---|---|---|
| 0. merge main, adapter for the final verifier | about 11:33 | 11:49:40 | about 17 min | $0 | merged without conflicts (tf.py already had the optional seed); engine tests 782 passed, 6 skipped; eval tests 190 passed |
| 1. Suite A scored run (`suite_a.score --all --cap 2.00`) | 11:50:29 | 12:17:05 | 26.6 min | $0.9146 (scorer: $1.0470 over the referenced run ids, cached probe answers included) | invocation 20261028T115030Z, 12/12 published, none failed; results committed (1ee9ece) |
| 2. recorded runs (`lull describe --mode extended`, fresh run ids final-*) | 12:18:06 | 12:37:58 (stopped) | 20 min | $0.3648 | STOPPED: Token Factory answered 402 `credit_balance_exhausted` ("You have exhausted your budget. Please add funds") at 12:34; an infrastructure failure, not a pipeline result |
| 2, resumed once from cache after the top-up (the one infrastructure retry) | 13:36:47 | 14:21:20 | 44.6 min | $0.5052 | 6 of 7 published; `ed` FAILED in publish (a pipeline failure, below); site exports ed90, bbb120, tos180 |
| 3. Suite B | 20:14:12 | 20:41:40 | three invocations (below) | $1.0147 | not run at first (`ed` failed); after the full-film re-run on 0b83117: two invocations stopped without results, the third completed (results/suite_b.json, SUITE_B.md) |
| 4a. negative control (`controls.negative --suite-a --showcase ed90,bbb120,tos180 --cap 0.50`) | 14:21:59 | 14:33:25 | 11.4 min | $0.0825 | results/controls_negative.json, CONTROLS.md (the Suite A runs and three site runs; `ed` failed). An invocation at 14:21:50 stopped in 1 s before any call: the run paths were given in a form Windows Python could not open; no spend |
| 4b. ablation (`LULL_NO_VERIFY=1 controls.ablation --all --cap 2.00`) | 14:33:47 | 14:52:56 | 19.2 min | $0.7873 | results/ablation.json, ABLATION.md; 12/12 runs, same engine fingerprint as Suite A (0.2.0+980454e1b9cd77d0) |
| 5. Report | | | | $0 | RESULTS.md and results/benchmarks.json from the complete results (Suite A, Suite B, negative control, ablation) |

Task spend: ledger $2.0125 before the task, $4.6670 after step 4 ($2.6545), $5.9754 after the full-film re-run and Suite B: **$3.9629** of the $8 budget (the re-run and Suite B: $1.3084 of their $2.00).

Step 2 in detail (inputs in lull-runs/final/inputs/: ed90.mp4 copied from the existing ed90
excerpt, ED 60-150 s, sha256 0daa75ac9328b0c0...; bbb120.mp4 = Big Buck Bunny 0:00-2:00 and
tos180.mp4 = Tears of Steel 3:30-6:30, cut with ffmpeg; ED and the USGS videos used as they are):

- `ed` (Elephants Dream full, entertainment, LULL_CAP_RUN=1.50): listen, watch, research and
  plan finished; finalize stopped at the 402 after cue c041 (4:56 of 10:58); exit 3; 170
  calls, $0.3648. Its finished stages are cached in lull-runs/final/ed: the same command
  resumes where it stopped.
- `ed90`, `bbb120`, `tos180`, `woodshole`, `aquatic_connectivity`, `csc_intro` (started one
  after another by the driver, 12:34:51 to 12:37:40): listen finished (local speech), watch
  hit the 402 at its first call; exit 3 each; no spend. (Corrected: an earlier version of this
  log said the last four were not started and bbb120 was stopped; the driver's own log,
  lull-runs/final/logs/recorded.log, shows all six ran to the 402.)

### The one infrastructure interruption: the provider balance (HTTP 402)

- **12:34** Token Factory answered every call with 402 `credit_balance_exhausted`. Interrupted:
  `ed` in finalize after cue c041 (4:56 of 10:58); `ed90`, `bbb120`, `tos180`, `woodshole`,
  `aquatic_connectivity`, `csc_intro` at their first watch call, after listen. Ledger at the
  stop $3.2919 (task spend $1.2794).
- The user topped the account up (promo credits). **13:36:47** the runs were resumed once, in
  the same order, with the same command, `--out` and `--run-id` (LULL_CAP_DAY=10; `ed`
  LULL_CAP_RUN=1.50; LULL_TAVILY_DAY=100 so the day's search cap of 60 could not cut a run's
  research short; the day ended at 51 searches). Resumed from cache: `ed` its listen, watch,
  research, plan and schedule stages and the 34 finalize cues done before the stop (finalize
  went on at c042); the other six their listen stage.
- **14:21:20** the resume finished. Ledger $3.2919 before, $3.7971 after: **$0.5052** (`ed`
  $0.0963 more, $0.4611 over both attempts; `ed90` $0.0387; `bbb120` $0.0672; `tos180`
  $0.1298; `woodshole` $0.0738; `aquatic_connectivity` $0.0659; `csc_intro` $0.0336). No run
  was retried a second time.

| run | profile | outcome | cues verified / held / extended | wall (resume) |
|---|---|---|---|---|
| ed | entertainment | FAILED in publish (below) | 43 / 21 / 43 | 884 s |
| ed90 | entertainment | published | 10 / 1 / 2 | 231 s |
| bbb120 | entertainment | published | 12 / 6 / 2 | 316 s |
| tos180 | entertainment | published | 9 / 6 / 14 | 416 s |
| woodshole | education | published | 0 / 5 / 22 | 367 s |
| aquatic_connectivity | education | published | 4 / 4 / 14 | 343 s |
| csc_intro | education | published | 1 / 0 / 1 | 114 s |

**`ed` failed (a pipeline failure, counted as failed, not re-run).** Finalize finished (787.9
s) and the mixes were written; then the keyframe step asked ffmpeg for a frame at 656.267 s
(cue c083's event). This file's video stream ends at 653.757 s (its audio at 658.074 s, the
container at 658.094 s), and the engine clamps keyframe times to the container duration less
0.1 s, so ffmpeg wrote no frame and publish raised `MediaError` (exit 1). Reproduced locally
without model calls (the same batched ffmpeg command over the cached cues: only that time
fails). Consequences: no `ed` showcase export, and Suite B cannot produce results, which need
runs of all four videos.

Site exports (`lull export-showcase`) in lull-runs/showcase-final/: `ed90` ("Elephants Dream",
excerpt 1:00-2:30), `bbb120` ("Big Buck Bunny", 0:00-2:00), `tos180` ("Tears of Steel
(development clip, used to tune the engine)", 3:30-6:30), each with its CC BY credit. The USGS
runs are not exported (music licences).

### The full-film re-run on main 0b83117 (a disclosed deviation)

- main 0b83117 ("engine: keyframes stay inside the video stream"; keyframe and freeze times
  clamped to the video stream's last frame, nothing else) merged as 1a85db9; engine tests 788
  passed, 6 skipped; eval tests 210 passed.
- **15:14:25** the full film was run once more under a fresh run id, `final-ed2` (same command
  and options; LULL_CAP_RUN=1.50; out lull-runs/final/ed2). Ledger before: $4.6670.

### Second interruption: speech server stopped at the user's request (deliberate pause)

- **15:48:40** the user needed the GPU and the local speech server was stopped on request.
  `final-ed2` stopped in finalize at its next speech call (SpeechError, connection closed;
  exit 1; 2055 s). Not retried; nothing else started; no model spend during the pause.
- Cached in lull-runs/final/ed2/stages: listen, watch (164 events), research (5 terms), plan,
  schedule, and 70 finalize cues (the last logged: x076 at 5:02 of 10:58).
- Spend so far: $0.2666 (403 Token Factory calls) and 5 searches; ledger $4.9335.
- To resume when the server is back: the same command with the same `--out` and `--run-id`,
  from the stage cache. That counts as the same single re-run.

### The full-film re-run resumed after the pause; Suite B attempts

- **20:07:16** `final-ed2` resumed from its stage cache once the speech server was back (same
  command and run id): listen, watch, research, plan, schedule and the 70 finalize cues
  reused. It published 294 s later: 36 verified, 28 held, 31 extended. This attempt $0.0272;
  `final-ed2` in all $0.2937 (481 Token Factory calls) and 5 searches.
- Exported as the site's `ed` into lull-runs/showcase-final/ed ("Elephants Dream", CC BY 2.5
  credit). Its showcase.json `costUsd` is the publishing attempt's spend ($0.0272); the full
  run's $0.2937 is in the manifest's `summary.spend.model_usd_all_runs` (the export's `summary.spend.modelUsdAllRunsOfRunId`): the cost the site shows for a showcase should come from that field. For ed90, bbb120 and tos180 both are the same (their first attempts spent nothing).
- **20:14:12** Suite B, first invocation (`--cap 1.20`, the deviation notes committed before
  it): stopped after 604 s, nothing written. The ledger's per-run-id cap of $0.30, which
  Suite B takes from Suite A's client setup unless LULL_CAP_RUN is set, stopped the Elephants
  Dream human-baseline judge at $0.2686 (6 Ultra calls). Spend $0.5064 (Lull judged: 8 Ultra
  and 8 Qwen calls; baseline: 6 Ultra and 4 Qwen); every answer is kept in the judge cache.
- **20:25:23** second invocation (LULL_CAP_RUN=0.60; `--cap 0.70`, so Suite B in all stays
  within the $1.20; the cached answers were reused at no cost): stopped after 130 s, nothing
  written. The next baseline batch's Ultra answer was cut off at its token limit twice
  (16,000, then 32,000 output tokens; `TFUnusable`), an error the scorer does not catch.
  Spend $0.1498.
- Suite B spend so far $0.6562. This step (the re-run and Suite B) $0.9499 of its $2.00;
  ledger $5.6169. Stopped here for a decision; Suite B has no results.
- **20:33:44** third invocation, with the scorer's batch splitting on cut-off answers (466867d;
  LULL_CAP_RUN=0.60; `--cap 1.05`, what was left of the step's $2.00): completed in 476 s,
  results/suite_b.json and SUITE_B.md. The Elephants Dream baseline batch that was cut off in
  the second invocation was answered within its limit this time, so no batch was split and no
  cut-off was recorded. Spend $0.3585. The scorer reports $0.8649, the run ids whose answers it
  used (this invocation and the first); the second invocation's $0.1498 is not among them.
  Suite B in all: $1.0147; ledger $5.9754.
