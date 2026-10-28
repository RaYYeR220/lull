"""Suite B judge probe: synthetic cues for known reference facts (probes.json), judged with the
pinned prompt by both judges, so the judges' strictness is measured on items whose answer is
known.

    uv run --directory eval python -m suite_b.probe      # writes probe_results.json

Per group (one video), one call per judge with the group's facts and all its cues, shown in a
fixed shuffled order under neutral ids like any run's cues. Sensitivity: positives credited to
their fact. Specificity: near misses not credited to their fact. Cross-credits: a cue
credited to a fact it was not written for (reported, not scored). Answers are cached with the
scorer's judge answers (out/judge/), so the scorer reports the probe at zero cost.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import UTC, datetime

from suite_a import score as A
from suite_a.score import SECOND_JUDGE, Judge
from suite_b.facts import PROBES_PATH as PROBES
from suite_b.refs import HERE, write_json

RESULTS_PATH = HERE / "probe_results.json"


def group_inputs(group: dict, refs: dict) -> tuple[list[dict], list[dict]]:
    """The group's facts (from the committed facts) and its cues in the fixed shuffled order
    (`start` = position; the judge sees neither the order's origin nor the cue ids)."""
    facts_by_id = {f["id"]: f for f in refs[group["video"]]["facts"]}
    facts = [facts_by_id[i] for i in group["facts"]]
    seed = json.loads(PROBES.read_text(encoding="utf-8"))["seed"]
    order = list(group["cues"])
    random.Random(f"{seed}/{group['video']}").shuffle(order)
    cues = [{"id": c["id"], "track": "probe", "text": c["text"], "start": float(i),
             "end": float(i)} for i, c in enumerate(order)]  # fmt: skip
    return facts, cues


def probe_scores(group: dict, verdicts: dict) -> dict:
    """Sensitivity, specificity, per cue type and cross-credits of one group's verdicts."""
    sens, spec, by_type = [0, 0], [0, 0], {}
    missed, false_credits, cross = [], [], []
    cues = {c["id"]: c for c in group["cues"]}
    for c in group["cues"]:
        credited = c["id"] in (verdicts.get(c["for"]) or {}).get("cue_ids", [])
        ok = credited if c["expect"] == "credit" else not credited
        tally = sens if c["expect"] == "credit" else spec
        tally[0], tally[1] = tally[0] + ok, tally[1] + 1
        t = by_type.setdefault(c["type"], [0, 0])
        t[0], t[1] = t[0] + ok, t[1] + 1
        if not ok:
            (missed if c["expect"] == "credit" else false_credits).append(c["id"])
    for fid in group["facts"]:
        for cid in (verdicts.get(fid) or {}).get("cue_ids", []):
            if cid in cues and cues[cid]["for"] != fid:
                cross.append([cid, fid])
    return {"sensitivity": sens, "specificity": spec, "by_type": by_type, "missed": missed,
            "false_credits": false_credits, "cross_credits": cross}  # fmt: skip


def total(per_group: list[dict]) -> dict:
    out = {"sensitivity": [0, 0], "specificity": [0, 0], "by_type": {}, "cross_credits": 0}
    for s in per_group:
        for k in ("sensitivity", "specificity"):
            out[k] = [out[k][0] + s[k][0], out[k][1] + s[k][1]]
        for t, (a, b) in s["by_type"].items():
            x = out["by_type"].setdefault(t, [0, 0])
            x[0], x[1] = x[0] + a, x[1] + b
        out["cross_credits"] += len(s["cross_credits"])
    return out


def run_probe(judges: dict, rules: dict, refs: dict, stamp: str) -> dict:
    """Both judges on every group: per judge, per group scores (with the verdicts) and totals."""
    from suite_b import score as B

    probes = json.loads(PROBES.read_text(encoding="utf-8"))
    out = {}
    for name, judge in judges.items():
        per = []
        for g in probes["groups"]:
            facts, cues = group_inputs(g, refs)
            rid = f"suite-b-{'probe' if name == 'primary' else 'probe2'}-{g['video']}-{stamp}"
            doc = refs[g["video"]]["doc"]
            verdicts, problems = B.judge_facts(judge, rules, g["video"], doc, facts, cues, rid,
                                               "suite_b_probe")  # fmt: skip
            per.append({"video": g["video"], **probe_scores(g, verdicts),
                        "verdicts": verdicts, "problems": problems})  # fmt: skip
        out[name] = {"model": judge.model, "groups": per, "total": total(per)}
    return out


def main(argv: list[str] | None = None) -> int:
    from lull.tf import ULTRA

    from suite_b import score as B

    rules, refs = B.load_reference_set()
    c = A.clients(0.30, need_speech=False)
    judges = {"primary": Judge(c["tf"], c["budget"], model=ULTRA, thinking=True,
                               cache=B.JUDGE_CACHE),
              "second": Judge(c["tf"], c["budget"], model=SECOND_JUDGE, thinking=False,
                              cache=B.JUDGE_CACHE)}  # fmt: skip
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    result = run_probe(judges, rules, refs, stamp)
    rows = [r for r in c["ledger"].runs() if r["run_id"].startswith("suite-b-probe")]
    paid = set().union(*(j.paid_by for j in judges.values()))
    doc = {"date": datetime.now(UTC).isoformat(timespec="seconds"),
           "prompt_sha256": B.prompt_sha(rules), "spend_usd": round(A.spend_of(paid, rows), 6),
           "run_ids": sorted(paid), **result}  # fmt: skip
    write_json(RESULTS_PATH, doc)
    for name, r in result.items():
        t = r["total"]
        print(f"{name} {r['model']}: sensitivity {t['sensitivity']}, specificity "
              f"{t['specificity']}, cross-credits {t['cross_credits']}")  # fmt: skip
        for g in r["groups"]:
            print(f"   {g['video']}: missed {g['missed']} false {g['false_credits']} "
                  f"cross {g['cross_credits']}")  # fmt: skip
    print(f"probe spend ${doc['spend_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
