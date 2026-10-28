"""Results report: eval/RESULTS.md and the website's benchmarks.json, from the COMPLETE results
the suites' scorers wrote to results/.

    uv run --directory eval python report.py                    # RESULTS.md
    uv run --directory eval python report.py \\
        --out-web ../lull-web/web/public/benchmarks.json         # also the site's file

Included: results/suite_a.json, suite_b.json, controls_negative.json and ablation.json, each
only when its scorer marked it complete (never a smoke or a partial run). Before anything is
written, every included result is checked against its pre-registration with the suites' own
checks (Suite A: key.json, its tag and pinned files; Suite B: facts.sha256 and its tag;
controls: prereg.json and its tag), and the result must name that same tag, commit and key or
reference-set sha256. `--out-web` also refuses while eval/ or engine/ has uncommitted changes,
when nothing is complete, and when the file would not pass the site's validator
(report_schema.py, a port of lull-web/web/lib/benchmarks-schema.ts).

benchmarks.json: {generated_at, git_commit, fixture?, suites: {suite_a?, suite_b?, controls?:
{negative?, ablation?}}, known_failures: [{title, detail}]}. Each suite is its scorer's own
object, unchanged. `known_failures` is generated from the data (failed runs, judge problems,
held-cue reason classes, Suite B's unresolved ASR terms, negative-control false accepts, the
README's "Deviations" sections of the included suites), then eval/known_failures.md (hand-kept).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import report_schema as S
from suite_a import score as A
from suite_a.score import Refused, git_run

EVAL_ROOT = Path(__file__).resolve().parent
RESULTS = EVAL_ROOT / "results"
README = EVAL_ROOT / "README.md"
MANUAL = EVAL_ROOT / "known_failures.md"
RUN_LOG = EVAL_ROOT / "run_log.md"  # kept by hand while the scored runs happen
OUT_MD = EVAL_ROOT / "RESULTS.md"
SOURCES = {"suite_a": "suite_a.json", "suite_b": "suite_b.json",
           "controls_negative": "controls_negative.json", "ablation": "ablation.json"}  # fmt: skip
SUITE_NAMES = {"suite_a": "Suite A", "suite_b": "Suite B", "controls": "Controls"}
SECTIONS = (("Suite A", "suite_a"), ("Suite B", "suite_b"), ("Controls", "controls"))


# -- what is included ------------------------------------------------------------------------------


def load_results(folder: Path = RESULTS) -> tuple[dict, dict]:
    """(included results by name, the reason each other one is left out)."""
    inc, skipped = {}, {}
    for name, file in SOURCES.items():
        path = Path(folder) / file
        if not path.exists():
            skipped[name] = "missing"
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("complete") is not True:
            skipped[name] = f"not complete ({doc.get('note') or 'the scorer marked it partial'})"
        elif doc.get("smoke"):
            skipped[name] = "a smoke (never included)"
        else:
            inc[name] = doc
    return inc, skipped


def _same_pin(recorded: dict, pin: dict, what: str) -> None:
    if (recorded or {}).get("tag") != pin["tag"] or (recorded or {}).get("commit") != pin["commit"]:
        raise Refused(f"{what} names the pin {(recorded or {}).get('tag')} @ "
                      f"{str((recorded or {}).get('commit'))[:12]}; the current pin is "
                      f"{pin['tag']} @ {pin['commit'][:12]}")  # fmt: skip


def verify_pins(inc: dict, git=git_run) -> dict:
    """The pre-registration record of every included result, checked with the suites' own
    checks; Refused when one fails or a result names another pin, key or reference set."""
    pins: dict = {}

    def suite_a_pin() -> dict:
        if "suite_a" not in pins:
            _, digest = A.load_key()
            pins["suite_a"] = {**A.verify_prereg(digest, git), "key_sha256": digest}
        return pins["suite_a"]

    if "suite_a" in inc:
        pin = suite_a_pin()
        if inc["suite_a"].get("key_sha256") != pin["key_sha256"]:
            raise Refused(f"suite_a.json was scored against key {inc['suite_a'].get('key_sha256')}"
                          f"; the pinned key is {pin['key_sha256']}")  # fmt: skip
        _same_pin(inc["suite_a"].get("preregistration"), pin, "suite_a.json")
    if "suite_b" in inc:
        from suite_b import score as B

        pre, digest = B.pin(git)
        pins["suite_b"] = {**pre, "reference_set_sha256": digest}
        if inc["suite_b"].get("reference_set_sha256") != digest:
            raise Refused(f"suite_b.json was scored against reference set "
                          f"{inc['suite_b'].get('reference_set_sha256')}; the pinned set is "
                          f"{digest}")  # fmt: skip
        _same_pin(inc["suite_b"].get("preregistration"), pre, "suite_b.json")
    if "controls_negative" in inc or "ablation" in inc:
        from controls import negative as N

        pins["controls"] = N.pin(git)
        if "controls_negative" in inc:
            _same_pin(inc["controls_negative"].get("preregistration"), pins["controls"],
                      "controls_negative.json")  # fmt: skip
        if "ablation" in inc:
            pre = inc["ablation"].get("preregistration") or {}
            _same_pin(pre.get("controls"), pins["controls"], "ablation.json")
            _same_pin(pre.get("suite_a"), suite_a_pin(), "ablation.json (Suite A pin)")
    return pins


# -- known failures --------------------------------------------------------------------------------


def reason_class(reason: str) -> str:
    """A held cue's reason without its particulars: the part before the first ':' or ';',
    numbers replaced by N."""
    head = re.split(r"[:;]", reason or "", maxsplit=1)[0]
    head = re.sub(r"\d+(?:\.\d+)?", "N", head).strip()
    return head or "(no reason given)"


def _strip_md(text: str) -> str:
    return " ".join(text.replace("**", "").replace("`", "").split())


def readme_deviations(text: str) -> list[dict]:
    """The items of every "Deviations" section of the README: {suite, title, detail}. A suite is
    the "## " section the deviations sit in; an item is a top-level list entry with its
    continuation and nested lines; its title is its bold lead, else its first clause."""
    out, suite, inside, item = [], None, False, None

    def flush():
        nonlocal item
        if item is not None and suite:
            detail = _strip_md(" ".join(item))
            bold = re.match(r"\s*\*\*(.+?)\*\*", item[0])
            # a clause ends at ; or : or a full stop, not at "0.5" or "U.S."
            first = re.split(r"[;:]|(?<!\b[A-Z])\.(?=\s|$)", detail, maxsplit=1)[0]
            title = bold.group(1) if bold else first
            out.append({"suite": suite, "title": title.strip()[:100], "detail": detail})
        item = None

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            if line.startswith("## "):
                suite = next((k for name, k in SECTIONS if line[3:].startswith(name)), None)
                inside = False
            elif line.startswith("### "):
                inside = line[4:].startswith("Deviations")
            continue
        if not inside:
            continue
        m = re.match(r"^(?:\d+\.|-)\s+(.*)$", line)
        if m:
            flush()
            item = [m.group(1)]
        elif item is not None and line.strip():
            item.append(re.sub(r"^\s*-\s+", "", line))
        elif item is not None and not line.strip():
            continue
    flush()
    return out


def manual_failures(text: str) -> list[dict]:
    """The hand-kept failures: one per "## " heading, its paragraphs as the detail."""
    out, title, body = [], None, []
    for line in text.splitlines() + ["## "]:
        if line.startswith("## "):
            if title and " ".join(body).strip():
                out.append({"title": title, "detail": " ".join(" ".join(body).split())})
            title, body = line[3:].strip(), []
        elif title is not None:
            body.append(line)
    return out


def _held(manifests: list[Path]) -> tuple[Counter, int, int]:
    counts, found, missing = Counter(), 0, 0
    for m in manifests:
        if not m.exists():
            missing += 1
            continue
        found += 1
        summary = json.loads(m.read_text(encoding="utf-8")).get("summary") or {}
        counts.update(reason_class(h.get("reason", "")) for h in summary.get("held") or [])
    return counts, found, missing


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def known_failures(inc: dict, readme_text: str, manual_text: str,
                   suite_a_runs: Path = A.RUNS) -> list[dict]:  # fmt: skip
    out: list[dict] = []
    held: list[tuple[str, list[Path]]] = []
    if "suite_a" in inc:
        for v in inc["suite_a"].get("videos", []):
            if v.get("failed"):
                out.append({"title": f"Suite A {v['id']}: the run failed",
                            "detail": f"{v['failed']} (run {v.get('run_id')}); its facts count "
                                      "as missed."})  # fmt: skip
            probs = v.get("judge_problems") or []
            if probs:
                more = f"; and {len(probs) - 5} more" if len(probs) > 5 else ""
                out.append({"title": f"Suite A {v['id']}: {_plural(len(probs), 'judge problem')}",
                            "detail": "; ".join(probs[:5]) + more + ". Each counts against "
                                      "Lull."})  # fmt: skip
        held.append(("Suite A", [Path(suite_a_runs) / str(v.get("run_id")) / "manifest.json"
                                 for v in inc["suite_a"].get("videos", [])]))  # fmt: skip
    if "suite_b" in inc:
        for v in inc["suite_b"].get("videos", []):
            probs = v.get("judge_problems") or []
            if probs:
                out.append({"title": f"Suite B {v['video']}: "
                                     f"{_plural(len(probs), 'judge problem')}",
                            "detail": "; ".join(probs[:5]) + "."})  # fmt: skip
        held.append(("Suite B", [Path(r["path"]) / "manifest.json"
                                 for r in inc["suite_b"].get("runs", [])]))  # fmt: skip
    for suite, manifests in held:
        counts, found, missing = _held(manifests)
        for cls, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
            note = f"; {_plural(missing, 'run folder')} not found" if missing else ""
            out.append({"title": f"{suite}: held cues, \"{cls}\"",
                        "detail": f"{_plural(n, 'cue')} held for this reason in "
                                  f"{_plural(found, 'run')}{note}. A held cue is not "
                                  "voiced."})  # fmt: skip
    if "suite_b" in inc:
        out += _asr_failures(inc["suite_b"])
    if "controls_negative" in inc:
        for r in inc["controls_negative"].get("rows", []):
            if not r["mismatched"]["fully_supported"]:
                continue
            mm = r["mismatch"]
            disc = {True: "discriminative (the cue conveys a fact the mismatched slide lacks)",
                    False: "not discriminative (its facts are on the mismatched slide too)",
                    None: "raw only (no key for this video)"}[r.get("discriminative")]  # fmt: skip
            out.append({"title": f"Negative control: false accept, {r['video']} {r['cue_id']} "
                                 f"({mm['rule']})",
                        "detail": f"\"{r['text']}\" was fully supported on {mm['video']} "
                                  f"{mm['window'][0]:.2f}-{mm['window'][1]:.2f} s (its true "
                                  f"clip {r['true_window'][0]:.2f}-{r['true_window'][1]:.2f} s); "
                                  f"{disc}."})  # fmt: skip
    present = {k for k in ("suite_a", "suite_b") if k in inc}
    if "controls_negative" in inc or "ablation" in inc:
        present.add("controls")
    for d in readme_deviations(readme_text):
        if d["suite"] in present:
            out.append({"title": f"Deviation, {SUITE_NAMES[d['suite']]}: {d['title']}",
                        "detail": d["detail"]})  # fmt: skip
    out += manual_failures(manual_text)
    return [f for f in out if f["title"].strip() and f["detail"].strip()]


def _asr_failures(suite_b: dict) -> list[dict]:
    from suite_b.refs import CORRECTIONS_PATH

    unresolved = json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8")).get("unresolved", [])
    if not unresolved:
        return []
    per = Counter(u["video"] for u in unresolved)
    terms = ", ".join(f'"{u["term"]}"' for u in unresolved)
    refs = (suite_b.get("summary") or {}).get("references", {})
    flagged = sum((e.get("flagged_facts") or 0) for e in refs.values())
    return [{"title": f"Suite B: {len(unresolved)} suspicious ASR terms left as heard",
             "detail": f"{', '.join(f'{v} {n}' for v, n in sorted(per.items()))}: {terms}. No "
                       "on-screen text or narration gave the right words, so they stay as the "
                       "recogniser heard them and USGS recall over all facts is a lower bound; "
                       f"{_plural(flagged, 'scored fact')} holding them are set aside in the "
                       "recall reported without them."}]  # fmt: skip


# -- benchmarks.json -------------------------------------------------------------------------------


def benchmarks(inc: dict, failures: list[dict], git_commit: str, generated_at: str) -> dict:
    suites: dict = {}
    for name in ("suite_a", "suite_b"):
        if name in inc:
            suites[name] = inc[name]
    controls = {k: inc[n] for k, n in (("negative", "controls_negative"), ("ablation", "ablation"))
                if n in inc}  # fmt: skip
    if controls:
        suites["controls"] = controls
    out = {"generated_at": generated_at, "git_commit": git_commit, "suites": suites,
           "known_failures": failures}  # fmt: skip
    if any("_fixture" in v for v in inc.values()):
        out = {"fixture": True, **out}
    return out


# -- RESULTS.md ------------------------------------------------------------------------------------


def pct(r: dict | None) -> str:
    """value [95% CI] (num/den), or n/a."""
    if not r or r.get("value") is None:
        return "n/a"
    ci = r.get("ci95") or [None, None]
    s = f"{r['value']:.1%}"
    if ci[0] is not None:
        s += f" [{ci[0]:.1%}, {ci[1]:.1%}]"
    return s + f" ({int(r['num'])}/{int(r['den'])})"


def frac(x) -> str:
    return "n/a" if not x else f"{x[0]}/{x[1]}"


def kappa(x: dict | None) -> str:
    if not x or x.get("agreement") is None:
        return "n/a"
    k = "undefined" if x.get("kappa") is None else f"{x['kappa'] + 0.0:.2f}"
    return f"{x['agreement']:.1%} agreement, kappa {k} (n={x['n']})"


def _total(x) -> object:
    """A count, or the sum of a dict of counts."""
    return sum(v for v in x.values() if isinstance(v, int | float)) if isinstance(x, dict) else x


def _num(x, fmt: str = "{:.4f}") -> str:
    return "n/a" if x is None else fmt.format(x)


def _ci(ci, fmt: str = "{:.4f}") -> str:
    return "" if not ci or ci[0] is None else f" [{fmt.format(ci[0])}, {fmt.format(ci[1])}]"


def suite_a_md(a: dict) -> list[str]:
    g, pre, hw = a["aggregate"], a["preregistration"], a.get("hardware") or {}
    sj = a.get("second_judge") or {}
    cost, proc = g.get("cost", {}), g.get("processing", {})
    lines = [
        "## Suite A: planted facts, hidden answer key", "",
        f"Pre-registration: tag `{pre['tag']}` -> commit `{pre['commit']}` ({pre['committed']}), "
        f"key sha256 `{a['key_sha256']}`. Scored {a['date']} at commit `{a['git_commit'][:12]}`"
        f"{' (dirty tree)' if a.get('git_dirty') else ''}; engine {a.get('engine_fingerprint')}; "
        f"engine models {', '.join(a.get('engine_models') or []) or 'n/a'}. Primary judge "
        f"`{a.get('judge_model')}` (temperature 0, reasoning on; also the engine's planner); "
        f"second judge `{a.get('second_judge_model')}`. Hardware: {hw.get('cpu')}; GPU "
        f"{', '.join(hw.get('gpus') or ['none'])}; {hw.get('os')}; speech "
        f"{hw.get('speech_server')}; models {hw.get('models')}. Spend over the referenced runs "
        f"{_num(a.get('spend_usd'), '${:.4f}')}; every referenced model call after the "
        f"pre-registration: {a.get('ledger_calls_after_preregistration')}.", "",
        f"{g['videos']} lectures, {g['minutes']:.2f} min, {_num(g.get('silence_s'), '{:.1f}')} s "
        f"without narration; failed runs: {', '.join(g.get('failed_runs') or []) or 'none'}.", "",
        "Pre-registered metrics (value [95% CI over videos] (num/den)):", "",
        "| metric | value |", "|---|---|",
        f"| overlap with narration, standard cues (s) | {g['overlap_s_total']:.3f} |",
        f"| extended freeze points inside narration | {g.get('extended_freeze_in_speech')} |",
        f"| standard cues outside their pause | {g.get('capacity_violations')} |",
        f"| visual-fact recall, standard | {pct(g['recall_standard'])} |",
        f"| visual-fact recall, with extended | {pct(g['recall_with_extended'])} |",
        f"| timely recall, with extended | {pct(g.get('timely_recall_with_extended'))} |",
        f"| hallucination, claims | {pct(g['hallucination_claims'])} |",
        f"| hallucination, cues with an unsupported claim | {pct(g.get('hallucination_cues'))} |",
        f"| absent decoys stated (judge) | {g['decoys_stated']}/{g['decoys_total']} |",
        f"| decoy numbers in cues | {g.get('decoy_number_hits')} |",
        f"| redundancy, cues restating narration | {pct(g['redundancy_cues'])} |",
        f"| spoken facts restated by a cue | {pct(g.get('spoken_facts_restated'))} |",
        f"| model cost per minute (USD; CI of model + web search) | "
        f"{_num(cost.get('model_usd_per_min'))}{_ci(cost.get('per_min_ci95'))} |",
        f"| processing seconds per minute | {_num(proc.get('s_per_min'), '{:.1f}')}"
        f"{_ci(proc.get('s_per_min_ci95'), '{:.1f}')} |",
        "", "Supplementary (added after the pre-registration):", "",
        "| metric | value |", "|---|---|",
        f"| hallucination, voiced text | {pct(g.get('hallucination_voiced_text'))} |",
        f"| voiced statements unverifiable | {pct(g.get('voiced_unverifiable'))} |",
        f"| cues with an unsupported voiced statement | "
        f"{pct(g.get('voiced_cues_with_unsupported'))} |",
        f"| tie-aware recall, standard / with extended | "
        f"{pct(g.get('recall_tie_aware_standard'))} / "
        f"{pct(g.get('recall_tie_aware_with_extended'))} |",
        f"| claims the cue text does not say | {pct(g.get('claims_not_in_cue_text'))} |",
        f"| decoys stated, absent part only | {g.get('decoys_absent_part')} |",
    ]  # fmt: skip
    if sj:
        agg, agr = sj.get("aggregate", {}), sj.get("agreement", {})
        lines += [f"| second judge: recall with extended | {pct(agg.get('recall'))} |",
                  f"| second judge: hallucination, claims | {pct(agg.get('hallucination'))} |",
                  f"| judges agree on visual facts | {kappa(agr.get('facts'))} |",
                  f"| judges agree on claims | {kappa(agr.get('claims'))} |",
                  f"| judges agree on voiced cues | {kappa(agr.get('voiced_cues'))} |"]  # fmt: skip
    probe = (a.get("judge_probe") or {}).get("total")
    if probe:
        lines += ["", f"Judge probe ({', '.join(a['judge_probe'].get('videos', []))}): facts "
                  f"credited to their own cue {frac(probe.get('facts_credited_to_own_cue'))}, "
                  f"near misses not credited {frac(probe.get('negatives_not_credited'))}, "
                  f"cross-credits {_total(probe.get('cross_credits'))}."]  # fmt: skip
    lines += ["", "| lecture | recall std | recall +ext | unsupported claims | overlap s | run |",
              "|---|---|---|---|---|---|"]  # fmt: skip
    for v in a.get("videos", []):
        rec = v.get("recall", {})
        lines.append(f"| {v['id']} {v.get('subject', '')} | {frac(rec.get('standard'))} | "
                     f"{frac(rec.get('with_extended'))} | "
                     f"{frac((v.get('hallucination') or {}).get('claims'))} | "
                     f"{_num(v.get('overlap_s'), '{:.3f}')} | "
                     f"{('FAILED: ' + v['failed']) if v.get('failed') else v.get('run_id')}"
                     " |")  # fmt: skip
    return lines


def suite_b_md(b: dict) -> list[str]:
    from suite_b import score as B

    pre, s = b["preregistration"], b["summary"]
    lines = [
        "## Suite B: Lull against human audio description", "",
        f"Pre-registration: tag `{pre['tag']}` -> commit `{pre['commit']}` ({pre['committed']}), "
        f"reference set sha256 `{b['reference_set_sha256']}` (first pin "
        f"`{b.get('first_preregistration_tag', 'suite-b-prereg')}`). Scored {b['date']} at commit "
        f"`{b['git_commit'][:12]}`{' (dirty tree)' if b.get('git_dirty') else ''}. Primary judge "
        f"`{b['judge_model']}`; second judge `{b.get('second_judge_model')}`; judge prompt sha256 "
        f"`{b.get('judge_prompt_sha256')}`. Window +/-{b['window_s']:g} s (+/-"
        f"{b.get('long_block_window_s', 30):g} s for blocks over 30 s). Scoring spend "
        f"{_num(b.get('spend_usd'), '${:.4f}')}; every scoring call after the pre-registration: "
        f"{b.get('ledger_calls_after_preregistration')}.", "",
        "| video | Lull run | engine | covers (s) |", "|---|---|---|---|",
    ]  # fmt: skip
    for r in b.get("runs", []):
        lines.append(
            f"| {r['video']} | `{r['run_id']}` | {(r.get('engine') or {}).get('engine')} | "
            f"{r['clip'][0]:g}-{r['clip'][1]:g}{'' if r['full'] else ' (clip)'} |"
        )
    lines += ["", "Recall of the describers' scored facts (visual, on-screen text) [95% CI over "
              "reference lines]. Elephants Dream: window primary, anywhere supplementary. USGS: "
              "window and anywhere with equal weight (a USGS anchor is the AD version's freeze "
              "point); one describer per USGS video, so no human ceiling there.", "",
              "| reference | facts (lines) | window, standard | window, with extended | anywhere, "
              "standard | anywhere, with extended |", "|---|---|---|---|---|---|"]  # fmt: skip
    for src, e in s["references"].items():
        r = e["recall"]
        lines.append(f"| {src} | {r['facts']} ({r['lines']}) | {pct(r['standard_window'])} | "
                     f"{pct(r['both_window'])} | {pct(r['standard_anywhere'])} | "
                     f"{pct(r['both_anywhere'])} |")  # fmt: skip
    for src, e in s["references"].items():
        if e.get("flagged_facts"):
            lines.append(f"- {src} without the {e['flagged_facts']} facts holding unresolved ASR "
                         f"terms: window, with extended "
                         f"{pct(e['recall_without_unresolved']['both_window'])} (all-facts USGS "
                         "recall is a lower bound)")  # fmt: skip
    if s.get("baseline"):
        lines += ["", "Human baseline (supplementary; same judge, prompt and window):", ""]
        lines += [f"- {n}: window {pct(r['both_window'])}; anywhere {pct(r['both_anywhere'])}"
                  for n, r in s["baseline"].items()]  # fmt: skip
    if any("second_judge" in e for e in s["references"].values()):
        lines += ["", "Second judge (supplementary):", ""]
        for src, e in s["references"].items():
            sj = e.get("second_judge")
            if sj:
                lines.append(f"- {src}: window, with extended {pct(sj['recall']['both_window'])}"
                             f"; agreement {kappa(sj.get('agreement_window'))}")  # fmt: skip
    lines += [x.replace("## ", "### ", 1) if x.startswith("## ") else x
              for x in B.overlap_section(b.get("videos", []))]  # fmt: skip
    lines += ["", "### Description density (words per minute of video)", "",
              "| video | side | words | per min |", "|---|---|---|---|"]  # fmt: skip
    for v in b.get("videos", []):
        for side, row in (v.get("density") or {}).items():
            if isinstance(row, dict):
                lines.append(f"| {v['video']} | {side} | {row['words']} | {row['per_min']} |")
    for name, r in (b.get("judge_probe") or {}).items():
        t = r["total"]
        lines.append(f"\nJudge probe, {name} `{r['model']}`: positives credited "
                     f"{frac(t['sensitivity'])}, near misses rejected {frac(t['specificity'])}, "
                     f"cross-credits {t['cross_credits']}.")  # fmt: skip
    return lines


def controls_md(inc: dict) -> list[str]:
    lines = ["## Controls: does the verifier catch false descriptions?", ""]
    neg = inc.get("controls_negative")
    if neg:
        h, pre, m = neg["metrics"]["headline"], neg["preregistration"], neg["metrics"]
        lines += [
            "### Negative control", "",
            f"Pre-registration: tag `{pre['tag']}` -> commit `{pre['commit']}` "
            f"({pre['committed']}). Run {neg['date']} at commit `{neg['git_commit'][:12]}`. "
            f"Verifier `{neg['verifier']['model']}`, temperature {neg['verifier']['temperature']}"
            f" (the pipeline's settings). Spend {_num(neg.get('spend_usd'), '${:.4f}')}.", "",
            f"Headline over the same-video rules ({h['rows']} cues; value [95% CI over cues]):",
            "", "| metric | value |", "|---|---|",
            f"| false accept: UPPER BOUND on verifier failure (a claim may be true of the "
            f"other footage too) | {pct(h['false_accept'])} |",
            f"| per-claim false accept | {pct(h['false_accept_claims'])} |",
            f"| discriminative false accept, Suite A ({h['suite_a_rows']} cues) | "
            f"{pct(h['discriminative_false_accept'])} |",
            f"| true accept on the true clip (optimistic: cues that passed verification) | "
            f"{pct(h['true_accept'])} |", "",
            "| mismatch rule | cues | false accept | per-claim false accept | true accept |",
            "|---|---|---|---|---|",
        ]  # fmt: skip
        lines += [f"| {rule} | {x['rows']} | {pct(x['false_accept'])} | "
                  f"{pct(x['false_accept_claims'])} | {pct(x['true_accept'])} |"
                  for rule, x in m["by_rule"].items()]  # fmt: skip
        lines += ["", "Every false accept is listed under Known failures."]
    abl = inc.get("ablation")
    if abl:
        pre = abl["preregistration"]
        lines += ["", "### Ablation: Suite A without frame verification", "",
                  f"Pre-registration: tag `{pre['controls']['tag']}` (Suite A pin "
                  f"`{pre['suite_a']['tag']}`). Run {abl['date']} at commit "
                  f"`{abl['git_commit'][:12]}` on engine {abl.get('engine_fingerprint')}; the "
                  f"with-arm is {abl['with_arm']['results']}. Spend "
                  f"{_num(abl.get('spend_usd'), '${:.4f}')}.", "",
                  "| metric | with verification | without | difference [95% CI over videos] |",
                  "|---|---|---|---|"]  # fmt: skip
        for name, d in abl["comparison"].items():
            diff = "n/a" if d["difference"] is None else (
                f"{d['difference']:+.1%} [{d['ci95'][0]:+.1%}, {d['ci95'][1]:+.1%}]")  # fmt: skip
            w = "n/a" if d["with"] is None else f"{d['with']:.1%}"
            o = "n/a" if d["without"] is None else f"{d['without']:.1%}"
            mark = " (headline)" if name == abl.get("headline") else ""
            lines.append(f"| {name}{mark} | {w} | {o} | {diff} |")
        cs = abl.get("claim_split") or {}
        lines += ["", f"Share of cues carrying the verifier's claim split: with {cs.get('with')}, "
                  f"without {cs.get('without')} (without it, the claims metric is judged per "
                  "whole cue; the voiced-text metric splits both arms the same way)."]  # fmt: skip
    return lines


def results_markdown(inc: dict, skipped: dict, failures: list[dict], meta: dict,
                     run_log: str = "") -> str:  # fmt: skip
    fixture = any("_fixture" in v for v in inc.values())
    lines = ["# Lull: evaluation results", ""]
    if fixture:
        lines += ["**FIXTURE DATA: this page was rendered from test fixtures, not results.**", ""]
    lines += [f"Generated {meta['now']} from commit `{meta['git_commit'][:12]}`"
              f"{' (dirty tree)' if meta['dirty'] else ''} by `eval/report.py` from the complete "
              "results in `eval/results/`. Method: `eval/README.md`. Every number comes from a "
              "scorer that checked its pre-registration first; intervals are 95 % percentile "
              "bootstraps (10 000 resamples, seed 20261028).", ""]  # fmt: skip
    if not inc:
        lines += ["No complete results yet.", ""]
    names = {"suite_a": "Suite A", "suite_b": "Suite B", "controls_negative": "negative control",
             "ablation": "ablation"}  # fmt: skip
    if skipped:
        lines += ["Not included: " + "; ".join(f"{names[k]} ({why})" for k, why in skipped.items())
                  + ".", ""]  # fmt: skip
    if "suite_a" in inc:
        lines += suite_a_md(inc["suite_a"]) + [""]
    if "suite_b" in inc:
        lines += suite_b_md(inc["suite_b"]) + [""]
    if "controls_negative" in inc or "ablation" in inc:
        lines += controls_md(inc) + [""]
    devs = [f for f in failures if f["title"].startswith("Deviation")]
    lines += ["## Deviations from the pre-registrations", ""]
    lines += [f"- **{f['title'].split(': ', 1)[0].replace('Deviation, ', '')}**: {f['detail']}"
              for f in devs] or ["(none for the included suites)"]  # fmt: skip
    lines += ["", "## Known failures", ""]
    lines += [f"- **{f['title']}**: {f['detail']}" for f in failures
              if not f["title"].startswith("Deviation")] or ["(none)"]  # fmt: skip
    body = "\n".join(x for x in run_log.splitlines() if not x.startswith("# ")).strip("\n")
    body = body.splitlines()
    if "\n".join(body).strip():
        lines += ["", "## Run log", "", *body]
    return "\n".join(lines).rstrip() + "\n"


# -- entry points ----------------------------------------------------------------------------------


def _texts(readme: str | None, manual: str | None) -> tuple[str, str]:
    r = README.read_text(encoding="utf-8") if readme is None else readme
    m = (
        (MANUAL.read_text(encoding="utf-8") if MANUAL.exists() else "")
        if manual is None
        else manual
    )
    return r, m


def _now(now: str | None) -> str:
    return now or datetime.now(UTC).isoformat(timespec="seconds")


def main_text(results: Path = RESULTS, *, git=git_run, readme: str | None = None,
              manual: str | None = None, suite_a_runs: Path = A.RUNS,
              now: str | None = None, run_log: str | None = None) -> str:  # fmt: skip
    """RESULTS.md for the complete results (their pins checked first), with run_log.md
    (kept by hand during the scored runs) at the end."""
    inc, skipped = load_results(results)
    if inc:
        verify_pins(inc, git)
    readme_text, manual_text = _texts(readme, manual)
    failures = known_failures(inc, readme_text, manual_text, suite_a_runs)
    meta = {"now": _now(now), "git_commit": git("rev-parse", "HEAD")[1].strip(),
            "dirty": bool(A.dirty(git))}  # fmt: skip
    if run_log is None:
        run_log = RUN_LOG.read_text(encoding="utf-8") if RUN_LOG.exists() else ""
    return results_markdown(inc, skipped, failures, meta, run_log)


def write_web(results: Path, out: Path, *, git=git_run, readme: str | None = None,
              manual: str | None = None, suite_a_runs: Path = A.RUNS,
              now: str | None = None) -> dict:  # fmt: skip
    """benchmarks.json for the site, or Refused (nothing complete, a dirty tree, a failed pin,
    a file the site's validator would reject)."""
    inc, _ = load_results(results)
    if not inc:
        raise Refused("nothing complete to publish: no suite has complete results in results/")
    if changes := A.dirty(git):
        raise Refused("benchmarks.json is written only from a clean tree; uncommitted changes: "
                      + "; ".join(changes[:8]))  # fmt: skip
    verify_pins(inc, git)
    readme_text, manual_text = _texts(readme, manual)
    failures = known_failures(inc, readme_text, manual_text, suite_a_runs)
    doc = benchmarks(inc, failures, git("rev-parse", "HEAD")[1].strip(), _now(now))
    try:
        S.parse_benchmarks(doc)
    except S.BenchmarksError as e:
        raise Refused(f"the site would reject it: {e}") from None
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
                   newline="\n")  # fmt: skip
    return doc


def main(argv: list[str] | None = None, *, git=git_run, **kw) -> int:
    """The CLI. The web file is written first: a fresh RESULTS.md makes eval/ dirty, and the
    web file is written only from a clean tree. `git` and `kw` (main_text / write_web
    arguments) are for tests."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", type=Path, default=RESULTS)
    ap.add_argument("--out-md", type=Path, default=OUT_MD)
    ap.add_argument("--out-web", type=Path, help="also write the site's benchmarks.json here")
    args = ap.parse_args(argv)
    if args.out_web:
        doc = write_web(args.results, args.out_web, git=git, **kw)
        print(f"wrote {args.out_web} ({', '.join(doc['suites'])}; "
              f"{len(doc['known_failures'])} known failures)")  # fmt: skip
    text = main_text(args.results, git=git, **kw)
    args.out_md.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {args.out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
