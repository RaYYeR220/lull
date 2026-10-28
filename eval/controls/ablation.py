"""Ablation: Suite A with Lull's frame verification switched off.

    LULL_NO_VERIFY=1 uv run --directory eval python -m controls.ablation --all

Added after the pre-registration of Suite A; pre-registered under the annotated tag
`controls-prereg` (prereg.json). Suite A's own pin applies unchanged (its tag, key sha256 and
pinned files are checked as the Suite A scorer checks them), and so does the controls' pin.

The arm WITHOUT verification: Suite A's twelve lectures run again by Suite A's own run
function, with the same options, into controls/out/ablation/runs/ (run ids
`ablation-<id>-<stamp>`; never Suite A's folders), in a process started with
LULL_NO_VERIFY=1; every run's manifest must record verification "disabled". Cues the engine
voiced without checking (status "unverified") count as published there. They are judged by
Suite A's primary judge with Suite A's requests (coverage, claims, voiced text), answers
cached in controls/out/ablation/judge/, and measured by Suite A's video_metrics.

The arm WITH verification: the Suite A counting run (results/suite_a.json, complete, on the
same engine fingerprint).

Reported side by side, with the paired difference (without - with) and its 95 % bootstrap
interval over videos: hallucination over claims (Suite A's pre-registered metric), over cues,
over voiced statements (supplementary in Suite A; the like-for-like comparison here, because
the claim split comes from the verifier that this arm switches off) and cues with an
unsupported voiced statement; recall standard and with extended; decoys stated. Written to
results/ablation.json and results/ABLATION.md (never results/suite_a.json) only for all
twelve videos from a clean tree; a subset goes to controls/out/partial/.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from controls import verify_adapter as VA
from controls.negative import pin as controls_pin
from suite_a import score as A
from suite_a.score import Refused, git_run, guard_results_write

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "ablation"
RUNS = OUT / "runs"
JUDGE_CACHE = OUT / "judge"
PARTIAL = HERE / "out" / "partial"
RESULT_NAME = "ablation.json"
MD_NAME = "ABLATION.md"
PREFIX = "ablation"
BOOT_N, BOOT_SEED = 10_000, 20261028
CAP_DEFAULT = 2.00

METRICS = {
    "hallucination_claims": ["hallucination", "claims"],
    "hallucination_cues": ["hallucination", "cues"],
    "hallucination_voiced_text": ["supplementary", "hallucination_voiced_text"],
    "voiced_cues_with_unsupported": ["supplementary", "voiced_cues_with_unsupported"],
    "recall_standard": ["recall", "standard"],
    "recall_with_extended": ["recall", "with_extended"],
}
HEADLINE = "hallucination_voiced_text"


# -- guards ----------------------------------------------------------------------------------------


def require_switch() -> None:
    if os.environ.get(VA.NO_VERIFY_ENV) != "1":
        raise Refused(
            f"start the ablation with {VA.NO_VERIFY_ENV}=1 in the environment (the "
            "engine reads it; setting it inside this process may come too late)"
        )


def check_with_arm(results: dict, fingerprint: str) -> None:
    if not results.get("complete"):
        raise Refused("results/suite_a.json is not a complete Suite A run")
    if results.get("engine_fingerprint") != fingerprint:
        raise Refused(
            f"the Suite A run is on engine {results.get('engine_fingerprint')}, this "
            f"process on {fingerprint}: the arms must share the engine"
        )


def check_disabled(manifest: dict, run_id: str) -> None:
    if not VA.verification_disabled(manifest):
        raise Refused(
            f"{run_id}: the manifest does not record verification 'disabled' (is the "
            f"engine's {VA.NO_VERIFY_ENV} switch merged?)"
        )


def destination(all_videos: bool, scored: int, total: int) -> Path:
    return A.RESULTS if all_videos and scored == total else PARTIAL


# -- cues and numbers ------------------------------------------------------------------------------


def as_published(cues_json: dict) -> dict:
    """A copy in which cues voiced without verification (status "unverified") carry the
    status Suite A counts as published for their track; the engine's status is kept in
    `engine_status`."""
    out = copy.deepcopy(cues_json)
    for track, status in (("cues", "verified"), ("extended", "extended")):
        for c in out.get(track, []):
            if c.get("status") == "unverified":
                c["engine_status"], c["status"] = "unverified", status
    return out


def claim_split_share(cues: list[dict]) -> float | None:
    """Share of published cues that carry the verifier's claim split (else Suite A judges the
    whole text as one claim)."""
    if not cues:
        return None
    return sum(bool(c.get("claims")) for c in cues) / len(cues)


def with_claim_split(video: dict) -> float | None:
    """The claim-split share of a Suite A counting run's video, from its run folder."""
    path = A.RUNS / str(video.get("run_id")) / "cues.json"
    if not path.exists():
        return None
    cues_json = json.loads(path.read_text(encoding="utf-8"))
    run = {"engine": {"extended_produced": video.get("extended_produced", True)}}
    return claim_split_share(A.run_cues(run, cues_json))


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def pair_of(video: dict, path: list[str]):
    x = video
    for p in path:
        x = x.get(p) if isinstance(x, dict) else None
    return tuple(x) if isinstance(x, list | tuple) and len(x) == 2 else None


def paired_difference(with_pairs, without_pairs, n: int = BOOT_N, seed: int = BOOT_SEED) -> dict:
    """Pooled rates of both arms over the same videos, their difference (without - with) and
    its 95 % percentile bootstrap over videos (the same resampled videos in both arms;
    resamples with a zero denominator in either arm dropped)."""
    w = np.array(with_pairs, dtype=float)
    o = np.array(without_pairs, dtype=float)
    if len(w) == 0 or w[:, 1].sum() == 0 or o[:, 1].sum() == 0:
        return {"with": None, "without": None, "difference": None, "ci95": [None, None],
                "videos": len(w)}  # fmt: skip
    rw, ro = w[:, 0].sum() / w[:, 1].sum(), o[:, 0].sum() / o[:, 1].sum()
    idx = np.random.default_rng(seed).integers(0, len(w), size=(n, len(w)))
    dw, do = w[idx, 1].sum(axis=1), o[idx, 1].sum(axis=1)
    keep = (dw > 0) & (do > 0)
    diff = o[idx, 0].sum(axis=1)[keep] / do[keep] - w[idx, 0].sum(axis=1)[keep] / dw[keep]
    lo, hi = np.percentile(diff, [2.5, 97.5])
    return {"with": float(rw), "without": float(ro), "difference": float(ro - rw),
            "ci95": [float(lo), float(hi)], "videos": len(w)}  # fmt: skip


def compare(with_videos: list[dict], without_videos: list[dict], expected: int) -> dict:
    """Every metric over the videos both arms have, paired by video id; Refused unless all
    `expected` videos are paired (results/ needs all twelve: no headline on fewer)."""
    by = {v["id"]: v for v in with_videos}
    both = [(by[v["id"]], v) for v in without_videos if v["id"] in by]
    if not (len(both) == len(with_videos) == len(without_videos) == expected):
        raise Refused(
            f"{len(both)} of {expected} videos paired (with-arm "
            f"{len(with_videos)}, without {len(without_videos)}): no comparison"
        )
    out = {}
    for name, path in METRICS.items():
        pairs = [(pair_of(a, path), pair_of(b, path)) for a, b in both]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        out[name] = paired_difference([a for a, _ in pairs], [b for _, b in pairs])
    dec = [((len(a["decoys"]["stated"]), a["decoys"]["of"]),
            (len(b["decoys"]["stated"]), b["decoys"]["of"])) for a, b in both
           if "decoys" in a and "decoys" in b]  # fmt: skip
    out["decoys_stated"] = paired_difference([a for a, _ in dec], [b for _, b in dec])
    return out


# -- report ----------------------------------------------------------------------------------------


def markdown(result: dict) -> str:
    def rate(x):
        return "n/a" if x is None else f"{x:.1%}"

    lines = [f"# Ablation: Suite A without frame verification"
             f"{'' if result['complete'] else ' (PARTIAL)'}", "",
             "Added after the pre-registration of Suite A; pre-registered under tag "
             f"`{result['preregistration']['controls']['tag']}`. Suite A pin "
             f"`{result['preregistration']['suite_a']['tag']}`. Engine "
             f"{result['engine_fingerprint']}; commit `{result['git_commit'][:12]}`; "
             f"{result['date']}. Spend ${result['spend_usd']:.4f}.", "",
             "| metric | with verification | without | difference (95% CI over videos) |",
             "|---|---|---|---|"]  # fmt: skip
    for name, d in result["comparison"].items():
        ci = d["ci95"]
        diff = "n/a" if d["difference"] is None else (
            f"{d['difference']:+.1%} [{ci[0]:+.1%}, {ci[1]:+.1%}]")  # fmt: skip
        mark = " (headline)" if name == HEADLINE else ""
        lines.append(f"| {name}{mark} | {rate(d['with'])} | {rate(d['without'])} | {diff} |")
    lines += ["", f"Claim split carried by the cues (share of published cues): with "
              f"{result['claim_split']['with']}, without {result['claim_split']['without']}. "
              "Without the verifier there is no claim split, so the claims metric is judged "
              "per whole cue in that arm; the voiced-text metric splits both arms the same "
              "way and is the headline."]  # fmt: skip
    return "\n".join(lines) + "\n"


# -- main ------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    from suite_b.refs import write_json

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true")
    which.add_argument("--videos", help="comma-separated ids (partial)")
    ap.add_argument("--cap", type=float, default=CAP_DEFAULT)
    args = ap.parse_args(argv)

    require_switch()
    controls = controls_pin()
    key, key_sha = A.load_key()
    suite_a_pin = A.verify_prereg(key_sha)
    wanted = None if args.all else args.videos.split(",")
    entries = [e for e in key["videos"] if wanted is None or e["id"] in wanted]
    videos = {e["id"]: A.check_render(e) for e in entries}
    from lull.pipeline import engine_fingerprint

    fingerprint = engine_fingerprint()
    path = A.RESULTS / "suite_a.json"
    if not path.exists():
        raise Refused("results/suite_a.json not found: the Suite A scored run comes first")
    with_arm = json.loads(path.read_text(encoding="utf-8"))
    check_with_arm(with_arm, fingerprint)
    folder = destination(args.all, len(entries), len(key["videos"]))
    guard_results_write(folder)

    c = A.clients(args.cap, need_speech=True)
    judge = A.Judge(c["tf"], c["budget"], cache=JUDGE_CACHE)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    without, split = [], []
    for e in entries:
        rec = A.run_video(e, videos[e["id"]], c, stamp, c["budget"], runs=RUNS, prefix=PREFIX)
        run_dir = Path(rec["run_dir"])
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        check_disabled(manifest, rec["run_id"])
        cues_json = as_published(json.loads((run_dir / "cues.json").read_text(encoding="utf-8")))
        cues = A.run_cues(rec, cues_json)
        split.append(claim_split_share(cues))
        jid = f"{PREFIX}-judge-{e['id']}-{stamp}"
        verdicts = A.judge_cues(e, cues, judge, jid)
        voiced = A.judge_voiced(e, cues, judge, jid)
        without.append(A.video_metrics(e, rec, cues_json, verdicts, voiced=voiced))
        print(f"{e['id']}: {rec['error'] or 'published'}", flush=True)
    with_videos = [v for v in with_arm["videos"] if v["id"] in videos]
    with_split = [with_claim_split(v) for v in with_videos]
    rows = [r for r in c["ledger"].runs() if r["run_id"].startswith(f"{PREFIX}-")]
    result = {
        "control": "ablation", "complete": folder == A.RESULTS, "date": A.now_utc(),
        "git_commit": git_run("rev-parse", "HEAD")[1].strip(),
        "preregistration": {"controls": controls, "suite_a": suite_a_pin},
        "engine_fingerprint": fingerprint, "verification": "disabled",
        "with_arm": {"results": "results/suite_a.json", "date": with_arm.get("date"),
                     "invocation": (with_arm.get("invocation") or {}).get("stamp")},
        "claim_split": {"with": _mean(with_split), "without": _mean(split)},
        "comparison": compare(with_videos, without, expected=len(entries)),
        "headline": HEADLINE,
        "videos": without,
        "spend_usd": round(A.spend_of(c["budget"].run_ids | judge.paid_by, rows), 6),
    }  # fmt: skip
    guard_results_write(folder)
    write_json(folder / RESULT_NAME, result)
    (folder / MD_NAME).write_text(markdown(result), encoding="utf-8", newline="\n")
    print(f"spent ${result['spend_usd']:.4f}; wrote {folder / RESULT_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
