"""Supplementary, added after the scored run (no new model calls): the claims Suite A's
pre-registered hallucination metric counted as unsupported, each with its kind from the run's
cues.json and a class from a manual review against the answer key and the rendered slides
(suite_a/claims_review.json), and the same rate over atomic claims only. The pre-registered
number is copied, never changed.

    uv run --directory eval python -m suite_a.claims_diagnosis

writes results/suite_a_claims.json and adds the section to results/SUITE_A.md; RESULTS.md
shows it under Suite A (eval/report.py)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from suite_a.score import RESULTS, RUNS, Refused, bootstrap_ratio, dirty, git_run

HERE = Path(__file__).resolve().parent
REVIEW = HERE / "claims_review.json"
OUT_JSON = "suite_a_claims.json"
MARK = "## Supplementary: the unsupported claims"
CLASSES = {
    "a": "true of the rendered slide but absent from the key (styling, colours, layout, "
    "slide numbers)",
    "b": "a whole-sentence claim (kind sentence) that repeats atomic claims of the same cue",
    "c": "a real error: contradicts the key or the slide",
    "d": "other",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def claim_kinds(cues_doc: dict) -> dict[str, str]:
    """Claim key -> kind, numbered as the scorer numbers them (claims with text, from 1; a cue
    without any is judged as one whole-text claim)."""
    out: dict[str, str] = {}
    for c in (cues_doc.get("cues") or []) + (cues_doc.get("extended") or []):
        claims = [x for x in c.get("claims") or [] if str(x.get("text", "")).strip()]
        if not claims:
            out[f"{c['id']}#1"] = "whole-text"
        for i, x in enumerate(claims, 1):
            out[f"{c['id']}#{i}"] = str(x.get("kind") or "atomic")
    return out


def _check_review(entry: dict | None, ref: str, kind: str, text: str) -> dict:
    if entry is None:
        raise Refused(f"no review for {ref} ('{text}')")
    if entry.get("claim") != text:
        raise Refused(f"the review of {ref} says '{entry.get('claim')}'; the scored claim is "
                      f"'{text}'")  # fmt: skip
    cls = entry.get("class")
    if cls not in CLASSES:
        raise Refused(f"unknown class '{cls}' for {ref}")
    if kind == "sentence" and cls != "b":
        raise Refused(f"{ref}: sentence claims are class b")
    if cls == "b" and kind != "sentence":
        raise Refused(f"{ref}: only sentence claims are class b (this one is {kind})")
    return entry


def diagnose(result: dict, runs: Path, review: dict, *, source_sha256: str) -> dict:
    """The unsupported claims with kind and class, counts per kind and class, the atomic-only
    rate (bootstrap over videos as the pre-registered rate). Refused when the review and the
    scored claims disagree in any way."""
    entries = dict(review.get("claims") or {})
    rows, pairs = [], []
    by_kind: dict[str, list[int]] = {"atomic": [0, 0], "sentence": [0, 0]}
    for v in result.get("videos", []):
        if v.get("failed"):
            continue
        cues = json.loads((Path(runs) / v["run_id"] / "cues.json").read_text(encoding="utf-8"))
        kinds = claim_kinds(cues)
        claims = (v.get("detail") or {}).get("claims") or []
        unsupported = [x for x in claims if x["verdict"] != "supported"]
        if [len(unsupported), len(claims)] != list((v.get("hallucination") or {}).get("claims")
                                                   or [0, 0]):  # fmt: skip
            raise Refused(f"{v['id']}: the detail lists {len(unsupported)}/{len(claims)} "
                          f"unsupported claims; the scorer counted "
                          f"{(v.get('hallucination') or {}).get('claims')}")  # fmt: skip
        atomic = [0, 0]
        for x in claims:
            if x["key"] not in kinds:
                raise Refused(f"{v['id']} {x['key']} is not in {v['run_id']}/cues.json")
            kind = kinds[x["key"]]
            bad = x["verdict"] != "supported"
            k = by_kind.setdefault(kind, [0, 0])
            k[0], k[1] = k[0] + bad, k[1] + 1
            if kind == "atomic":
                atomic[0], atomic[1] = atomic[0] + bad, atomic[1] + 1
            if bad:
                ref = f"{v['id']} {x['key']}"
                e = _check_review(entries.pop(ref, None), ref, kind, x["claim"])
                rows.append({"video": v["id"], "key": x["key"], "cue": x["cue"],
                             "track": x.get("track"), "kind": kind, "class": e["class"],
                             "group": e.get("group", ""), "claim": x["claim"],
                             "reason": x.get("reason", ""), "note": e.get("note", "")})  # fmt: skip
        pairs.append((atomic[0], atomic[1]))
    if entries:
        raise Refused(f"{sorted(entries)[0]} is not an unsupported claim of these results")
    counts = {k: sum(r["class"] == k for r in rows) for k in CLASSES}
    return {
        "added": "after the scored run; no new model calls",
        "source": {"file": "suite_a.json", "sha256": source_sha256,
                   "invocation": result.get("invocation"), "date": result.get("date")},
        "review": {k: review.get(k) for k in ("reviewed", "method", "summary") if review.get(k)},
        "preregistered": (result.get("aggregate") or {}).get("hallucination_claims"),
        "atomic_only": bootstrap_ratio(pairs),
        "by_kind": by_kind,
        "classes": {k: {"label": label, "count": counts[k]} for k, label in CLASSES.items()},
        "claims": rows,
    }  # fmt: skip


def _pct(r: dict | None) -> str:
    if not r or r.get("value") is None:
        return "n/a"
    ci = r.get("ci95") or [None, None]
    s = f"{r['value']:.1%}"
    if ci[0] is not None:
        s += f" [{ci[0]:.1%}, {ci[1]:.1%}]"
    return s + f" ({int(r['num'])}/{int(r['den'])})"


def _cell(s: object) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def markdown(doc: dict, level: int = 2) -> list[str]:
    h = "#" * level
    kinds = doc.get("by_kind") or {}
    groups: dict[str, int] = {}
    for r in doc["claims"]:
        if r["class"] == "d" and r.get("group"):
            groups[r["group"]] = groups.get(r["group"], 0) + 1
    rev = doc.get("review") or {}
    lines = [
        f"{h} {MARK.lstrip('# ')} (added after the scored run, no new calls)", "",
        "Every claim the pre-registered hallucination metric counted as unsupported, with its "
        "kind as the run's cues.json records it and a class from a manual review against the "
        "answer key (slide inventory, style text) and the rendered slide images"
        + (f" ({rev['method']})" if rev.get("method") else "") + ". Made from the scored "
        f"results (`{doc['source']['file']}`, sha256 `{doc['source']['sha256'][:12]}`) and the "
        "judge answers already given; the pre-registered number is unchanged.", "",
        "| measure | value |", "|---|---|",
        f"| hallucination, claims (pre-registered, unchanged) | {_pct(doc.get('preregistered'))} |",
        f"| hallucination, atomic claims only | {_pct(doc.get('atomic_only'))} |",
    ]  # fmt: skip
    for kind, (bad, n) in kinds.items():
        lines.append(f"| unsupported / all claims of kind {kind} | {bad}/{n} |")
    lines += ["", "| class | claims |", "|---|---|"]
    for k, c in doc["classes"].items():
        extra = ""
        if k == "d" and groups:
            extra = " (" + ", ".join(f"{g} {n}" for g, n in groups.items()) + ")"
        lines.append(f"| ({k}) {c['label']} | {c['count']}{extra} |")
    if rev.get("summary"):
        lines += ["", rev["summary"]]
    lines += [
        "",
        "| lecture | claim | track | kind | class | claim text | judge's reason | review |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in doc["claims"]:
        cls = r["class"] + (f", {r['group']}" if r.get("group") else "")
        lines.append(f"| {r['video']} | {r['key']} | {r.get('track') or ''} | {r['kind']} | "
                     f"{cls} | {_cell(r['claim'])} | {_cell(r['reason'])} | "
                     f"{_cell(r['note'])} |")  # fmt: skip
    return lines


def with_section(text: str, section: list[str]) -> str:
    """The markdown with the section at its end, replacing an earlier copy."""
    at = text.find(MARK)
    base = text[:at] if at >= 0 else text
    return base.rstrip("\n") + "\n\n" + "\n".join(section).rstrip() + "\n"


def main(argv: list[str] | None = None, *, git=git_run) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", type=Path, default=RESULTS)
    ap.add_argument("--runs", type=Path, default=RUNS)
    ap.add_argument("--review", type=Path, default=REVIEW)
    args = ap.parse_args(argv)
    if changes := dirty(git):
        raise Refused("results/ is written only from a clean tree; uncommitted changes: "
                      + "; ".join(changes[:8]))  # fmt: skip
    src = args.results / "suite_a.json"
    result = json.loads(src.read_text(encoding="utf-8"))
    review = json.loads(args.review.read_text(encoding="utf-8"))
    doc = diagnose(result, args.runs, review, source_sha256=sha256_file(src))
    (args.results / OUT_JSON).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                                         encoding="utf-8", newline="\n")  # fmt: skip
    md = args.results / "SUITE_A.md"
    md.write_text(with_section(md.read_text(encoding="utf-8"), markdown(doc)), encoding="utf-8",
                  newline="\n")  # fmt: skip
    c = doc["classes"]
    print(f"wrote {args.results / OUT_JSON}; atomic only {_pct(doc['atomic_only'])}; classes "
          + ", ".join(f"{k} {v['count']}" for k, v in c.items()))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
