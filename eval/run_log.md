# Run log

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
