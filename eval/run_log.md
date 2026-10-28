# Run log

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
