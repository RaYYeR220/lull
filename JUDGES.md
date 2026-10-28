# Evaluate Lull in 5 minutes

No account or key needed. The recorded run plays from the website itself. The live steps (4
and 5) use the hosted API, under a spend cap, at 3 runs an hour per visitor.

## The path

**1. Hear the difference (30 s).** Open https://lull-omega.vercel.app and press **Hear the difference**.
- *Elephants Dream* plays with its described soundtrack.
- Switch **Soundtrack** between **Original** and **Described** while it plays.
- What to notice:
  - each description lands in a pause and stops before the next line of dialogue;
  - the timeline under the title draws the speech, the pauses and each line as it lands.

**2. How it never talks over speech (30 s).** Scroll to **Zero overlap, by construction**.
- The close-up shows one pause: the last word before it, the 0.2 s guard on each side, and the
  line inside.
- What to notice: the figure is drawn from the run's own word timings, not an illustration.
  "Over speech" reads 0.00 s.

**3. How it refuses to invent (30 s).** Scroll to **A line it can't prove, it doesn't say**.
- What to notice:
  - the first draft said the platform breaks apart;
  - the frames don't show that, so that claim is struck and only the supported words are spoken;
  - a line that still fails is held for a person, never spoken.

**4. A recorded run, and its honest report (1 min).** Open https://lull-omega.vercel.app/watch/ed90.
- **What was checked** says how many lines passed the frame check and how many were held for
  review. It reads **Needs review**, because no person has reviewed this run yet.
- Open **Conformance report**. Each WCAG row (1.2.3, 1.2.5, 1.2.7) lists what blocks it:
  - held lines;
  - unused pauses next to undescribed content (WCAG failure F113);
  - coverage of the important moments.
- What to notice: nothing reads "met". A row can only read "met" after a person reviews every
  line.

**5. Describe a clip live (about 3 minutes of waiting).** Open https://lull-omega.vercel.app/try.
1. Pick **Tears of Steel (excerpt)** and press **Describe**.
2. Watch it run:
   - the stage rail lights up listen → watch → research → plan → schedule → finalize → publish;
   - **Model calls** lists every call, newest first: which Nemotron did what, its tokens and its
     cost. The Tavily search is counted, not priced;
   - lines appear on the timeline as they pass or fail the frame check. A held line shows its
     reason.
3. When it finishes, the result shows the spend (a few cents) and the WCAG status.

Keep the page open while it runs. Closing it stops the run, and reopening it in the same
browser resumes it from where it stopped.

**6. Review in the studio (1 min).** From the finished run, press **Review in the studio**.
1. Choose a line on the timeline. The drawer shows:
   - the frame it was checked against;
   - each claim's verdict;
   - who wrote, checked and voiced it.
2. Edit the text and **Save**. Only that line is linted, checked against its frames and voiced
   again.
3. Press **Publish**. The files, the coverage and the WCAG rows are worked out again.

The overlap gate still applies: an edit that would overlap speech can't be published.

**7. The numbers, and how they were fixed in advance (30 s).** Open https://lull-omega.vercel.app/benchmarks.
- What to notice:
  - the method comes first;
  - the pre-registration tag, the answer key's sha256 and the scoring commit are on the page;
  - every share has its 95 % interval;
  - known failures are listed.

**8. The API and MCP in one minute.** One call lists what the hosted service can describe:

```sh
curl -s https://lull-api-494617981995.us-central1.run.app/v1/samples
```

To describe a sample from an agent, add `https://lull-api-494617981995.us-central1.run.app/mcp` as an MCP server and call
`describe_video(sample_id="ed45", mode="standard")`. The full reference, with every refusal, is
at https://lull-omega.vercel.app/developers.

## How to verify our claims yourself

- **The answer key came first.**
  - `git show suite-a-prereg` gives the commit that holds `eval/suite_a/key.json`.
  - `sha256sum eval/suite_a/key.json` must equal `eval/suite_a/key.sha256`.
  - The scorer's first commit and every pipeline call are later. The results file lists each
    run with its first call time.
  - The scorer refuses to run if the key or the generator changed since the tag.
- **Re-run the scoring without spending.**
  1. Unpack the results bundle attached to the release into `eval/suite_a/out/`. Its sha256 is
     in `eval/results/suite_a.json`.
  2. Run `uv run --directory eval python -m suite_a.score --all --reuse-runs`. It rebuilds the
     report from the recorded runs and the cached judge answers at zero spend.

  `eval/README.md` gives every suite's commands.
- **No overlap, checked from the files.** From a live run's page (step 5), download
  `cues.json` (it has the speech spans) and `descriptions.vtt`. No cue's interval may intersect a speech
  span. `engine/tests/stages/test_publish.py::test_publish_refuses_on_overlap_and_writes_nothing`
  shows the gate refusing.
- **Held lines are never voiced.** In any run's `cues.json`, held cues have no audio. The
  described audio is mixed from voiced cues only
  (`engine/tests/stages/test_publish.py::test_held_cues_are_never_mixed_even_with_audio`).
- **The engine's tests run offline.** In `engine/`, `uv run pytest -m "not live"` uses fakes for
  the models and the speech server.
- **Every claim mapped to its evidence:** [CLAIMS.md](CLAIMS.md).
