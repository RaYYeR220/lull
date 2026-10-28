# Run log

Plan 4 Task 6, the scored runs on the final engine (main 61e9d81 merged into feat/eval as
7168ea5). Kept by hand while the runs happened, 2026-10-28, times UTC. Spend is the change of
the model ledger (~/.lull/ledger.db) over the step; the ledger stood at $2.0125 before the
task. LULL_CAP_DAY=10. Nothing below was run twice to get a better number.

| step | started | finished | wall | spend | outcome |
|---|---|---|---|---|---|
| 0. merge main, adapter for the final verifier | about 11:33 | 11:49:40 | about 17 min | $0 | merged without conflicts (tf.py already had the optional seed); engine tests 782 passed, 6 skipped; eval tests 190 passed |
| 1. Suite A scored run (`suite_a.score --all --cap 2.00`) | 11:50:29 | 12:17:05 | 26.6 min | $0.9146 (scorer: $1.0470 over the referenced run ids, cached probe answers included) | invocation 20261028T115030Z, 12/12 published, none failed; results committed (1ee9ece) |
