"""Negative control: does Lull's frame verifier accept a correct-looking description checked
against the WRONG footage?

    uv run --directory eval python -m controls.negative --suite-a \\
        --showcase elephants_dream=RUN_DIR::VIDEO --showcase woodshole=RUN_DIR::VIDEO ...

Pre-registered in prereg.json under the annotated tag `controls-prereg-2` (the first pin,
`controls-prereg`, stays in the history); the harness refuses to run unless the tag exists, is
an ancestor of HEAD, carries prereg.sha256 = the sha256 of the prereg.json on disk, and
prereg.json, prereg.sha256, negative.py and ablation.py are unchanged since the tag. The
engine is reached only through verify_adapter.py.

Selection: every published verified cue (standard cues with status "verified", extended cues
with status "extended") of the Suite A counting run (`--suite-a`: the runs listed in
results/suite_a.json) and of the showcase runs passed in, ordered by (video, start, id); when
there are more than MAX_CUES, MAX_CUES of them drawn with random.Random(SEED), kept in order.

Per cue, the verifier checks the cue's final text twice: on the TRUE clip (the engine's own
verification window for the cue's events) and on a MISMATCHED clip of the same length: in a
Suite A lecture the centre of another slide (one the true window does not touch, long enough,
not carrying the title of a slide it touches; the nearest); in a showcase video a window at
least AWAY_S from the true one sharing at least MIN_EVENT_OVERLAP_S with a watch event and
nothing with the cue's events (the nearest on a GRID_S grid); otherwise from the next video
(in label order) that has room ("other video").

Metrics, each with a 95 % percentile bootstrap over the cues, per mismatch rule and for the
headline (same-video rules only, never the other-video fallback): false accept (the
mismatched clip supports every claim, and there is at least one; an UPPER BOUND on verifier
failure), per-claim false accept, true accept (the true clip supports every claim; optimistic,
the cues already passed verification), and for Suite A the discriminative false accept (the
cue conveys a fact of the true slide that the mismatched slide does not show).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

from controls import verify_adapter as VA
from suite_a import score as A
from suite_a.score import Refused, bootstrap_ratio, git_run, guard_results_write
from suite_a.text import contains_phrase, same_number

HERE = Path(__file__).resolve().parent
EVAL_ROOT = HERE.parent
OUT = HERE / "out" / "negative"
PARTIAL = HERE / "out" / "partial"
PREREG = HERE / "prereg.json"
TAG = "controls-prereg-2"  # the second pin; the first was "controls-prereg"
FIRST_TAG = "controls-prereg"
SHA_FILE = "eval/controls/prereg.sha256"
PINNED = ("eval/controls/prereg.json", "eval/controls/prereg.sha256",
          "eval/controls/negative.py", "eval/controls/ablation.py")  # fmt: skip

MAX_CUES = 40
SEED = 20261028
AWAY_S = 30.0
GRID_S = 0.5
MIN_EVENT_OVERLAP_S = 0.5  # a window meets an event when they share at least this much
SAME_VIDEO = ("another slide", "other events, 30 s away")  # the headline's rules
ESTIMATE = 0.01  # USD reserved before each verifier call
CAP_DEFAULT = 0.50
EPS = 1e-9


# -- the pin ---------------------------------------------------------------------------------------


def prereg_digest() -> str:
    return hashlib.sha256(PREREG.read_bytes()).hexdigest()


def pin(git=git_run) -> dict:
    """The tag record, or Refused (Suite A's check with the controls' tag and files)."""
    return A.verify_prereg(prereg_digest(), git, tag=TAG, sha_file=SHA_FILE, pinned=PINNED,
                           what="controls/prereg.json")  # fmt: skip


# -- selection -------------------------------------------------------------------------------------


def candidates(run: dict) -> list[dict]:
    """A run's published verified cues, each tagged with its track."""
    cj = run["cues_json"]
    out = [{**c, "track": "standard"} for c in cj.get("cues", [])
           if c.get("gap_id") is not None and c.get("status") == "verified"]  # fmt: skip
    out += [{**c, "track": "extended"} for c in cj.get("extended", [])
            if c.get("gap_id") is None and c.get("status") == "extended"]  # fmt: skip
    return out


def select(runs: list[dict], max_n: int = MAX_CUES, seed: int = SEED) -> list[tuple[str, dict]]:
    """(video label, cue) for the control, ordered by (video, start, id)."""
    pool = [(r["label"], c) for r in runs for c in candidates(r)]
    pool.sort(key=lambda x: (x[0], float(x[1]["start"]), x[1]["id"]))
    if len(pool) > max_n:
        keep = sorted(random.Random(seed).sample(range(len(pool)), max_n))
        pool = [pool[i] for i in keep]
    return pool


# -- mismatched windows ----------------------------------------------------------------------------


def _centred(lo: float, hi: float, length: float) -> tuple[float, float]:
    mid = (lo + hi) / 2
    return mid - length / 2, mid + length / 2


def _titles(slides: list[dict], lo: float, hi: float) -> set[str]:
    return {s["title"] for s in slides if s.get("title") and s["start"] < hi and s["end"] > lo}


def _pick_slide(slides: list[dict], length: float, near: float, skip_index: set,
                skip_titles: set[str]):  # fmt: skip
    free = [s for s in slides if s["index"] not in skip_index
            and s["end"] - s["start"] >= length and s.get("title") not in skip_titles]  # fmt: skip
    if not free:
        return None
    return min(free, key=lambda s: (abs(s["start"] - near), s["start"]))


def slide_mismatch(true: tuple[float, float], slides: list[dict]):
    """The centre of the slide nearest the true window (by start) among those it does not
    touch, that last at least as long and do not carry the title of a slide it touches;
    None when there is none."""
    s = _slide_choice(true, slides)
    return None if s is None else _centred(s["start"], s["end"], true[1] - true[0])


def _slide_choice(true, slides):
    lo, hi = true
    touched = {s["index"] for s in slides if s["start"] < hi and s["end"] > lo}
    return _pick_slide(slides, hi - lo, lo, touched, _titles(slides, lo, hi))


def _overlap(x: dict, s: float, e: float) -> float:
    return min(x["t_end"], e) - max(x["t_start"], s)


def event_overlap(window, events: dict) -> dict[str, float]:
    """The events a window overlaps, with the seconds they share."""
    s, e = window
    return {i: round(_overlap(x, s, e), 3) for i, x in events.items() if _overlap(x, s, e) > 0}


def _meets(events: dict, s: float, e: float) -> set[str]:
    return {i for i, x in events.items() if _overlap(x, s, e) >= MIN_EVENT_OVERLAP_S - EPS}


def _touches(events: dict, s: float, e: float) -> set[str]:
    return {i for i, x in events.items() if _overlap(x, s, e) > 0}


def event_mismatch(true, cue_ids: set[str], events: dict, duration: float,
                   away: float = AWAY_S, grid: float = GRID_S):  # fmt: skip
    """A window as long as the true one, at least `away` seconds from it, inside the video,
    sharing at least MIN_EVENT_OVERLAP_S with one watch event and no time at all with any of
    the cue's: the nearest such start on the grid (ties: the later one); None when none."""
    lo, hi = true
    length = hi - lo
    best = None
    for k in range(int((duration - length) / grid + EPS) + 1):
        s = k * grid
        e = s + length
        if not (s >= hi + away or e <= lo - away):
            continue
        if not _meets(events, s, e) or _touches(events, s, e) & set(cue_ids):
            continue
        key = (abs(s - lo), -s)
        if best is None or key < best[0]:
            best = (key, (s, e))
    return best[1] if best else None


def _any_window(run: dict, length: float, near: float, skip_titles: set[str]):
    """In another video: (window, slide index) for the slide nearest `near` with room and
    another title, or the event-covered window nearest `near`."""
    if run.get("slides"):
        s = _pick_slide(run["slides"], length, near, set(), skip_titles)
        return None if s is None else (_centred(s["start"], s["end"], length), s["index"])
    best = None
    for k in range(int((run["duration"] - length) / GRID_S + EPS) + 1):
        s = k * GRID_S
        if _meets(run.get("events") or {}, s, s + length):
            key = (abs(s - near), s)
            if best is None or key < best[0]:
                best = (key, (s, s + length))
    return None if best is None else (best[1], None)


def mismatched(label: str, true, cue_ids: set[str], runs: list[dict]) -> dict | None:
    """{"video", "window", "rule", "slide"}: same video when its rule finds room, else the
    next video in label order that has it (a slide there must not carry the title of a slide
    the true window touches)."""
    by = {r["label"]: r for r in runs}
    me = by[label]
    if me.get("slides"):
        s = _slide_choice(true, me["slides"])
        if s is not None:
            w = _centred(s["start"], s["end"], true[1] - true[0])
            return {"video": label, "window": [w[0], w[1]], "rule": "another slide",
                    "slide": s["index"]}  # fmt: skip
    elif me.get("events"):
        w = event_mismatch(true, cue_ids, me["events"], me["duration"])
        if w is not None:
            return {"video": label, "window": [w[0], w[1]], "rule": "other events, 30 s away",
                    "slide": None}  # fmt: skip
    titles = _titles(me.get("slides") or [], *true)
    order = sorted(by)
    i = order.index(label)
    for other in order[i + 1 :] + order[:i]:
        got = _any_window(by[other], true[1] - true[0], true[0], titles)
        if got is not None:
            (lo, hi), slide = got
            return {"video": other, "window": [lo, hi], "rule": "other video", "slide": slide}
    return None


# -- discriminative false accepts (Suite A) --------------------------------------------------------


def _slide_items(slide: dict) -> list[str]:
    chart = slide.get("chart") or {}
    return [slide.get("title") or "", *slide.get("bullets", []), chart.get("title") or "",
            chart.get("x_label") or "", chart.get("y_label") or "",
            *[p["label"] for p in chart.get("points", [])]]  # fmt: skip


def absent_from(fact: dict, slide: dict) -> bool:
    """Whether a key visual fact is NOT shown by `slide`: a chart value when its chart has no
    point with that category and value; a trend when it has no line chart; anything else
    when its text is no item of the slide (title, bullet, chart title, axis or point label)
    as a phrase."""
    chart = slide.get("chart") or {}
    if fact["kind"] == "chart_value":
        return not any(p["label"] == fact["anchor"] and same_number(float(p["value"]),
                                                                    float(fact["value"]))
                       for p in chart.get("points", []))  # fmt: skip
    if fact["kind"] == "chart_trend":
        return chart.get("type") != "line"
    return not any(contains_phrase(item, fact["anchor"]) for item in _slide_items(slide) if item)


def _exact(fact: dict, text: str) -> bool:
    if fact["kind"] == "title":
        return A.title_match(fact["anchor"], [text])
    if fact["kind"] == "chart_value":
        return A.value_match(float(fact["value"]), fact["anchor"], [text])
    return False


def discriminative_facts(cue_id: str, cue_text: str, true_slides: set[int], mismatch_slide: dict,
                         entry: dict, covered: dict | None) -> list[str]:  # fmt: skip
    """The key visual facts of the true slide(s) the cue conveys that the mismatched slide
    does not show. Conveyed: the Suite A primary judge's coverage verdict for this cue in the
    scored run (`covered`: cue id -> credited fact ids); when the run has no verdicts, an exact
    match of the slide title or of a chart value with its category."""
    facts = [f for f in entry["visual_facts"] if f["slide"] in true_slides]
    if covered is not None:
        mine = set(covered.get(cue_id, []))
        conveyed = [f for f in facts if f["id"] in mine]
    else:
        conveyed = [f for f in facts if _exact(f, cue_text)]
    return [f["id"] for f in conveyed if absent_from(f, mismatch_slide)]


def pair_context(label: str, cue: dict, true, mm: dict, by: dict) -> dict:
    """What a row records besides the verdicts: the watch events the mismatched window
    overlaps (seconds shared), those of the cue among them (none by the rule, for the same
    video), and for a Suite A lecture the true slides and the discriminative facts."""
    me, other = by[label], by[mm["video"]]
    shared = event_overlap(mm["window"], other.get("events") or {})
    mine = set(cue.get("event_ids", []))
    ctx = {"event_overlap": shared,
           "cue_event_overlap": {i: x for i, x in shared.items() if i in mine}
           if mm["video"] == label else {},
           "suite_a": me.get("entry") is not None, "true_slides": [], "discriminative": None,
           "discriminative_facts": []}  # fmt: skip
    if me.get("entry") is not None and other.get("slides") and mm.get("slide") is not None:
        lo, hi = true
        true_slides = {s["index"] for s in me["slides"] if s["start"] < hi and s["end"] > lo}
        slide = next(s for s in other["slides"] if s["index"] == mm["slide"])
        facts = discriminative_facts(cue["id"], cue["text"], true_slides, slide, me["entry"],
                                     me.get("covered"))  # fmt: skip
        ctx.update(true_slides=sorted(true_slides), discriminative=bool(facts),
                   discriminative_facts=facts)  # fmt: skip
    return ctx


# -- metrics ---------------------------------------------------------------------------------------


def outcome(claims: list[dict]) -> dict:
    """A check's claims counted; fully supported = at least one claim and all supported."""
    n = len(claims)
    k = sum(c["verdict"] == "supported" for c in claims)
    return {"claims": n, "supported": k, "fully_supported": n > 0 and k == n}


def _block(rows: list[dict]) -> dict:
    return {
        "rows": len(rows),
        "false_accept": bootstrap_ratio(
            [(r["mismatched"]["fully_supported"], 1) for r in rows]),
        "false_accept_claims": bootstrap_ratio(
            [(r["mismatched"]["supported"], r["mismatched"]["claims"]) for r in rows]),
        "true_accept": bootstrap_ratio([(r["true"]["fully_supported"], 1) for r in rows]),
    }  # fmt: skip


def metrics(rows: list[dict]) -> dict:
    """Per mismatch rule, and the headline over the same-video rules only (never the
    other-video fallback): false accept per cue and per claim and true accept, each with a 95%
    bootstrap over the cues (Suite A's bootstrap_ratio). The headline false accept is an UPPER
    BOUND on verifier failure (a claim can be true of the other footage too); the
    discriminative false accept counts, over the Suite A same-video rows, only false accepts of
    cues that convey a fact of the true slide the mismatched slide does not show."""
    rules = sorted({r["mismatch"]["rule"] for r in rows})
    same = [r for r in rows if r["mismatch"]["rule"] in SAME_VIDEO]
    suite_a = [r for r in same if r.get("suite_a")]
    disc = bootstrap_ratio([(r["mismatched"]["fully_supported"] and bool(r.get("discriminative")),
                             1) for r in suite_a])  # fmt: skip
    return {
        "cues": len(rows),
        "by_rule": {rule: _block([r for r in rows if r["mismatch"]["rule"] == rule])
                    for rule in rules},
        "headline": {"rules": list(SAME_VIDEO), "upper_bound": True, **_block(same),
                     "discriminative_false_accept": disc, "suite_a_rows": len(suite_a)},
    }  # fmt: skip


# -- inputs ----------------------------------------------------------------------------------------


def run_input(label: str, run_dir: Path, video: Path, slides: list[dict] | None = None,
              entry: dict | None = None, covered: dict | None = None) -> dict:  # fmt: skip
    run_dir = Path(run_dir)
    cues_json = json.loads((run_dir / "cues.json").read_text(encoding="utf-8"))
    watch = json.loads((run_dir / "stages" / "watch.json").read_text(encoding="utf-8"))
    events = {e["id"]: e for e in (watch.get("data") or watch)["events"]}
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    return {"label": label, "run_dir": str(run_dir), "run_id": cues_json.get("run_id"),
            "video": str(video), "duration": float(manifest["video"]["duration"]),
            "cues_json": cues_json, "events": events, "slides": slides, "entry": entry,
            "covered": covered}  # fmt: skip


def suite_a_inputs() -> list[dict]:
    """The Suite A counting run: the runs listed in results/suite_a.json, with each lecture's
    slide timeline from the pinned key."""
    path = A.RESULTS / "suite_a.json"
    if not path.exists():
        raise Refused("results/suite_a.json not found: run the Suite A scored run first")
    res = json.loads(path.read_text(encoding="utf-8"))
    if not res.get("complete"):
        raise Refused("results/suite_a.json is not a complete Suite A run")
    key, _ = A.load_key()
    entries = {e["id"]: e for e in key["videos"]}
    out = []
    for v in res["videos"]:
        e = entries[v["id"]]
        slides = [{k: s.get(k) for k in ("index", "start", "end", "title", "bullets", "chart")}
                  for s in e["slides"]]  # fmt: skip
        out.append(run_input(v["id"], A.RUNS / v["run_id"], A.check_render(e), slides, e,
                             coverage_by_cue(v)))  # fmt: skip
    return out


def coverage_by_cue(video: dict) -> dict | None:
    """cue id -> the visual facts the Suite A primary judge credited to it in the scored run;
    None when the run kept no per-fact verdicts."""
    facts = (video.get("detail") or {}).get("facts")
    if facts is None:
        return None
    out: dict[str, list[str]] = {}
    for f in facts:
        for cid in f.get("cue_ids") or []:
            out.setdefault(cid, []).append(f["id"])
    return out


def showcase_input(spec: str) -> dict:
    """LABEL=RUN_DIR::VIDEO"""
    label, rest = spec.split("=", 1)
    run_dir, video = rest.split("::", 1)
    return run_input(label, Path(run_dir), Path(video))


# -- the run ---------------------------------------------------------------------------------------


def cached_check(text: str, clip: Path, offset: float, tf, budget, run_id: str) -> list[dict]:
    """The adapter's verifier answer, cached by (text, clip sha256, offset, settings)."""
    from suite_b.refs import sha256_file, write_json

    req = {"text": text, "clip": sha256_file(clip), "offset": offset, "settings": VA.settings()}
    key = hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()
    path = OUT / "cache" / f"{key}.json"
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        budget.add_run(saved["run_id"])
        return saved["claims"]
    budget.check(ESTIMATE, "a verifier call")
    budget.add_run(run_id)
    claims = VA.check(text, clip, offset=offset, tf=tf, run_id=run_id)
    write_json(path, {"request": req, "run_id": run_id, "claims": claims})
    return claims


def _pct(r: dict) -> str:
    if r["value"] is None:
        return "n/a"
    lo, hi = r["ci95"]
    return f"{r['value']:.1%} ({int(r['num'])}/{int(r['den'])}) [{lo:.0%}, {hi:.0%}]"


def markdown(result: dict) -> str:
    m, pre, h = result["metrics"], result["preregistration"], result["metrics"]["headline"]
    lines = [f"# Controls: negative control{'' if result['complete'] else ' (PARTIAL)'}", "",
             f"Pinned by tag `{pre['tag']}` (commit `{pre['commit'][:12]}`, {pre['committed']}); "
             f"commit `{result['git_commit'][:12]}`; {result['date']}. Verifier: "
             f"`{result['verifier']['model']}`, temperature {result['verifier']['temperature']} "
             f"(the pipeline's settings, through controls/verify_adapter.py). Spend "
             f"${result['spend_usd']:.4f}.", "",
             f"## Headline: same-video mismatches ({h['rows']} cues; rules: "
             f"{', '.join(h['rules'])})", "",
             "| metric | value (95% CI over cues) |", "|---|---|",
             f"| false accept (UPPER BOUND on verifier failure: a claim may be true of the "
             f"other footage too) | {_pct(h['false_accept'])} |",
             f"| per-claim false accept | {_pct(h['false_accept_claims'])} |",
             f"| discriminative false accept, Suite A ({h['suite_a_rows']} cues: the cue "
             f"conveys a fact of the true slide the mismatched slide does not show) | "
             f"{_pct(h['discriminative_false_accept'])} |",
             f"| true accept on the true clip (optimistic: these cues already passed "
             f"verification; edited cues excluded) | {_pct(h['true_accept'])} |", "",
             "## By mismatch rule (the other-video fallback is never in the headline)", "",
             "| rule | cues | false accept | per-claim false accept | true accept |",
             "|---|---|---|---|---|"]  # fmt: skip
    for rule, b in m["by_rule"].items():
        lines.append(
            f"| {rule} | {b['rows']} | {_pct(b['false_accept'])} | "
            f"{_pct(b['false_accept_claims'])} | {_pct(b['true_accept'])} |"
        )
    lines += ["", "## Every false accept", ""]
    fa = [r for r in result["rows"] if r["mismatched"]["fully_supported"]]
    for r in fa:
        disc = {True: "discriminative", False: "not discriminative", None: "raw only"}[
            r.get("discriminative")]  # fmt: skip
        if r.get("discriminative_facts"):
            disc += " " + ",".join(r["discriminative_facts"])
        lines.append(f"- {r['video']} {r['cue_id']} ({r['track']}): \"{r['text']}\" | true clip "
                     f"{r['true_window'][0]:.2f}-{r['true_window'][1]:.2f} s | mismatched "
                     f"{r['mismatch']['video']} {r['mismatch']['window'][0]:.2f}-"
                     f"{r['mismatch']['window'][1]:.2f} s ({r['mismatch']['rule']}) | {disc} | "
                     f"events in the mismatched window {r.get('event_overlap')}")  # fmt: skip
    if not fa:
        lines.append("(none)")
    lines += ["", "## True clips not fully supported", ""]
    miss = [r for r in result["rows"] if not r["true"]["fully_supported"]]
    lines += [f"- {r['video']} {r['cue_id']}: \"{r['text']}\" | {r['true_window'][0]:.2f}-"
              f"{r['true_window'][1]:.2f} s" for r in miss] or ["(none)"]  # fmt: skip
    if result["skipped"]:
        lines += ["", "Skipped: " + "; ".join(result["skipped"])]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from suite_b.refs import write_json

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--suite-a", action="store_true", help="the Suite A counting run's cues")
    ap.add_argument("--showcase", action="append", default=[], help="LABEL=RUN_DIR::VIDEO")
    ap.add_argument("--cap", type=float, default=CAP_DEFAULT)
    args = ap.parse_args(argv)
    prereg = pin()
    runs = (suite_a_inputs() if args.suite_a else []) + [showcase_input(s) for s in args.showcase]
    if not runs:
        raise Refused("nothing to check: pass --suite-a and/or --showcase")
    complete = bool(args.suite_a and args.showcase)
    folder = A.RESULTS if complete else PARTIAL
    guard_results_write(folder)
    c = A.clients(args.cap, need_speech=False)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"controls-negative-{stamp}"
    by = {r["label"]: r for r in runs}
    rows, skipped = [], []
    for label, cue in select(runs):
        r = by[label]
        evs = [r["events"][i] for i in cue.get("event_ids", []) if i in r["events"]]
        if not evs:
            skipped.append(f"{label} {cue['id']}: no watch events")
            continue
        true = VA.clip_window(evs, r["duration"])
        mm = mismatched(label, true, set(cue["event_ids"]), runs)
        if mm is None:
            skipped.append(f"{label} {cue['id']}: no mismatched window")
            continue
        clips = OUT / "clips" / stamp
        t_clip = VA.cut(Path(r["video"]), clips / f"{label}-{cue['id']}-true.mp4", *true)
        m_clip = VA.cut(Path(by[mm["video"]]["video"]),
                        clips / f"{label}-{cue['id']}-mismatched.mp4", *mm["window"])  # fmt: skip
        t_claims = cached_check(cue["text"], t_clip, true[0], c["tf"], c["budget"], run_id)
        m_claims = cached_check(cue["text"], m_clip, mm["window"][0], c["tf"], c["budget"], run_id)
        rows.append({"video": label, "run_id": r["run_id"], "cue_id": cue["id"],
                     "track": cue["track"], "text": cue["text"], "true_window": list(true),
                     "mismatch": mm, "true": outcome(t_claims), "mismatched": outcome(m_claims),
                     "true_claims": t_claims, "mismatched_claims": m_claims,
                     **pair_context(label, cue, true, mm, by)})  # fmt: skip
        print(f"{label} {cue['id']}: true {rows[-1]['true']['fully_supported']}, mismatched "
              f"{rows[-1]['mismatched']['fully_supported']}", flush=True)  # fmt: skip
    ledger_rows = [x for x in c["ledger"].runs() if x["run_id"].startswith("controls-negative-")]
    result = {
        "control": "negative", "complete": complete, "date": A.now_utc(),
        "git_commit": git_run("rev-parse", "HEAD")[1].strip(), "preregistration": prereg,
        "verifier": VA.settings(), "inputs": [{k: r[k] for k in ("label", "run_dir", "run_id",
                                                                  "video")} for r in runs],
        "metrics": metrics(rows), "rows": rows, "skipped": skipped,
        "spend_usd": round(A.spend_of(c["budget"].run_ids, ledger_rows), 6),
    }  # fmt: skip
    guard_results_write(folder)
    write_json(folder / "controls_negative.json", result)
    (folder / "CONTROLS.md").write_text(markdown(result), encoding="utf-8", newline="\n")
    print(f"spent ${result['spend_usd']:.4f}; wrote {folder / 'CONTROLS.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
