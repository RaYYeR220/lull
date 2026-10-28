"""Suite B scorer: what Lull says about real videos, against what human describers said.

    uv run --directory eval python -m suite_b.score --run elephants_dream=RUN_DIR \\
        --run woodshole=RUN_DIR --run aquatic_connectivity=RUN_DIR --run csc_intro=RUN_DIR
    uv run --directory eval python -m suite_b.score --run elephants_dream=RUN_DIR \\
        --offset elephants_dream=60       # a run on a clip that starts 60 s into the video

A run directory is a finished Lull run of the reference video (cues.json, manifest.json); this
scorer never runs the pipeline. Before anything else it checks the pin: facts.sha256 matches
the reference set on disk, the annotated tag `suite-b-prereg` exists and is an ancestor of
HEAD and carries the same first line, and refs/, facts/, rules.json, refs.py and facts.py are
unchanged since the tag. The rules it applies are read from rules.json (pinned with the facts).

Per reference fact, the judges (Suite A's: Nemotron 3 Ultra, reasoning on; Qwen3 235B Instruct
as the second judge) name the cues that convey it, from the cue texts alone (neutral ids, no
times, no tracks); the window of rules.json then decides which of those count. Dialogue overlap
is measured for every side against the same speech spans, those of the Lull run's listen
output. results/ is written only when every run passed in covers its whole video, the tree is
clean and the pin holds; anything else (a clip, a smoke) goes to out/partial/.

Spend: --cap (default $0.50) caps what one invocation may spend, checked before each judge call.
Judge answers are cached by their exact request under out/judge/ (Suite A's Judge).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from lull.tf import ULTRA

from suite_a import score as A
from suite_a.score import (
    RESULTS,
    SECOND_JUDGE,
    Budget,
    Judge,
    Refused,
    agreement,
    bootstrap_ratio,
    cue_span,
    dirty,
    git_run,
    guard_results_write,
    merge,
    now_utc,
    overlap_seconds,
    published,
)
from suite_b.facts import FACTS, RULES_PATH
from suite_b.facts import check as check_reference_set
from suite_b.refs import (
    DATA_DEFAULT,
    OUT,
    REFS,
    SPAN_JOIN_S,
    VIDEOS,
    clamped,
    merge_spans,
    word_count,
    write_json,
)

PREREG_TAG = "suite-b-prereg"
SHA_FILE = "eval/suite_b/facts.sha256"
PINNED = ("eval/suite_b/refs", "eval/suite_b/facts", "eval/suite_b/facts.sha256",
          "eval/suite_b/rules.json", "eval/suite_b/refs.py", "eval/suite_b/facts.py")  # fmt: skip
JUDGE_CACHE = OUT / "judge"
PARTIAL = OUT / "partial"
FULL_TOLERANCE_S = 1.0  # a run whose video is this close to the reference's covers all of it
ESTIMATE = 0.06  # USD reserved before each judge call
CAP_DEFAULT = 0.50
JUDGED_KINDS = ("visual", "on_screen_text", "interpretation")  # sound / not_in_video: unscored
EPS = 1e-6


# -- the pin and the reference set ---------------------------------------------------------------


def pin(git=git_run) -> tuple[dict, str]:
    """(the tag record, the reference-set digest), or Refused."""
    try:
        digest = check_reference_set()
    except SystemExit as e:
        raise Refused(str(e)) from None
    prereg = A.verify_prereg(digest, git, tag=PREREG_TAG, sha_file=SHA_FILE, pinned=PINNED,
                             what="the reference set")  # fmt: skip
    return prereg, digest


def load_reference_set() -> tuple[dict, dict]:
    rules = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    refs = {}
    for v in VIDEOS:
        doc = json.loads((REFS / f"{v}.json").read_text(encoding="utf-8"))
        facts = json.loads((FACTS / f"{v}.json").read_text(encoding="utf-8"))["facts"]
        refs[v] = {"doc": doc, "facts": facts}
    return rules, refs


# -- a Lull run ----------------------------------------------------------------------------------


def lull_cue(c: dict, track: str, offset: float) -> dict:
    """A published cue on the reference video's timeline: a standard cue over its voiced
    interval [start, start + audio_dur], an extended cue at its freeze point."""
    if track == "standard":
        s, e = cue_span(c)
        return {"id": c["id"], "track": track, "text": c["text"], "start": s + offset,
                "end": e + offset}  # fmt: skip
    t = float(c.get("freeze_at", c["start"])) + offset
    return {"id": c["id"], "track": track, "text": c["text"], "start": t, "end": t}


def load_run(video: str, path: Path, offset: float, ref_duration: float) -> dict:
    path = Path(path)
    if not (path / "cues.json").exists():
        raise Refused(f"{video}: {path} has no cues.json (not a finished Lull run)")
    cues_json = json.loads((path / "cues.json").read_text(encoding="utf-8"))
    manifest = {}
    if (path / "manifest.json").exists():
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    listen = {}
    if (path / "stages" / "listen.json").exists():
        listen = json.loads((path / "stages" / "listen.json").read_text(encoding="utf-8"))
        listen = listen.get("data", listen)
    summary = manifest.get("summary", {})
    duration = (manifest.get("video") or {}).get("duration") or listen.get("duration")
    if duration is None:
        raise Refused(f"{video}: {path} records no video duration")
    duration = float(duration)
    produced = summary.get("extended_produced", True) is not False
    std, ext = published(cues_json, extended_produced=produced)
    speech, source = cues_json.get("speech"), "cues.json speech"
    if not speech and listen.get("speech"):
        speech, source = listen["speech"], "stages/listen.json speech"
    spans = [(float(s["start"]) + offset, float(s["end"]) + offset) for s in speech or []]
    cues = [lull_cue(c, "standard", offset) for c in std]
    cues += [lull_cue(c, "extended", offset) for c in ext]
    return {
        "video": video,
        "path": str(path),
        "run_id": cues_json.get("run_id") or manifest.get("run_id"),
        "offset_s": offset,
        "clip": [offset, offset + duration],
        "full": abs(offset) < EPS and abs(duration - ref_duration) <= FULL_TOLERANCE_S,
        "duration_s": duration,
        "video_sha256": (manifest.get("video") or {}).get("sha256"),
        "engine": manifest.get("versions"),
        "options": manifest.get("options"),
        "cue_counts": summary.get("cues"),
        "extended_produced": produced,
        "cues": sorted(cues, key=lambda c: (c["start"], c["id"])),
        "speech": spans if speech else None,
        "speech_source": source if speech else None,
    }


def listen_speech(video: str, data: Path) -> list[tuple[float, float]]:
    """Speech spans from the engine's own listen stage on the original video (used only when
    a run carries none)."""
    from lull.speech import NemoSpeechHTTP
    from lull.stages.listen import listen

    speech = NemoSpeechHTTP.from_settings()
    if not speech.health():
        raise Refused("the run has no speech spans and the speech server is not answering")
    media = Path(data) / VIDEOS[video]["media"]["file"]
    res = listen(media, OUT / "listen" / video, speech, wps=2.5)
    return [(s.start, s.end) for s in res.speech]


# -- scope, windows -------------------------------------------------------------------------------


def in_scope(fact: dict, clip: list[float], full: bool) -> bool:
    """A fact a run can be scored on: its anchor lies in the clip (a preface fact: only when
    the run covers the whole video)."""
    if fact.get("placement") == "before_program":
        return full
    return clip[0] - EPS <= fact["anchor_time"] <= clip[1] + EPS


def window(fact: dict, window_s: float, duration: float) -> tuple[float, float]:
    if fact.get("placement") == "before_program":
        return 0.0, duration
    return fact["anchor_time"] - window_s, fact["anchor_end"] + window_s


def plays_in(cue: dict, lo: float, hi: float) -> bool:
    """A cue's interval (a point for an extended cue) meets the closed window [lo, hi]."""
    return cue["start"] <= hi + EPS and cue["end"] >= lo - EPS


def human_cues(lines: list[dict], clip: list[float]) -> list[dict]:
    """A describer's lines as cues (for the baseline): those that meet the clip."""
    out = []
    for c in lines:
        if c["end"] >= clip[0] - EPS and c["start"] <= clip[1] + EPS:
            out.append({"id": c["id"], "track": "line", "text": c["text"], "start": c["start"],
                        "end": c["end"]})  # fmt: skip
    return out


# -- the judge -----------------------------------------------------------------------------------


def judge_request(rules: dict, video: str, doc: dict, facts: list[dict], cues: list[dict]):
    """The pinned prompt (rules.json) for these facts and cues; cue ids are neutral (k001 ... in
    time order). Returns (prompt, schema, neutral id -> real cue id)."""
    j = rules["judge"]
    order = sorted(cues, key=lambda c: (c["start"], c["id"]))
    kid = {c["id"]: f"k{i:03d}" for i, c in enumerate(order, start=1)}
    cue_text = "\n".join(j["cue_line"].format(id=kid[c["id"]], text=" ".join(c["text"].split()))
                         for c in order)  # fmt: skip
    fact_text = "\n".join(j["fact_line"].format(id=f["id"], kind=f["kind"], text=f["text"])
                          for f in facts)  # fmt: skip
    prompt = j["prompt"].format(title=doc["title"], about=doc["about"],
                                characters=rules["characters"][video],
                                cues=cue_text or "(no cues)", facts=fact_text)  # fmt: skip
    schema = {
        "type": "object",
        "properties": {"facts": A._items_schema([f["id"] for f in facts], list(kid.values()))},
        "required": ["facts"],
        "additionalProperties": False,
    }
    return prompt, schema, {v: k for k, v in kid.items()}


def judge_facts(judge: Judge, rules: dict, video: str, doc: dict, facts: list[dict],
                cues: list[dict], run_id: str, stage: str) -> tuple[dict, list[str]]:  # fmt: skip
    """fact id -> {"cue_ids": real cue ids, "reason"}; facts in anchor order, at most
    facts_per_call per call. A fact without a verdict counts as not conveyed (listed)."""
    out, problems = {}, []
    if not facts:
        return out, problems
    if not cues:
        return {f["id"]: {"cue_ids": [], "reason": "no cues"} for f in facts}, problems
    per = int(rules["judge"]["facts_per_call"])
    ordered = sorted(facts, key=lambda f: f["anchor_time"])
    for i in range(0, len(ordered), per):
        chunk = ordered[i : i + per]
        prompt, schema, back = judge_request(rules, video, doc, chunk, cues)
        answer = judge.ask(prompt, schema, run_id, stage, ESTIMATE)
        rows, repeated = A.by_id(answer.get("facts") or [])
        problems += [f"{x}: judged twice (first answer kept)" for x in repeated]
        for f in chunk:
            row = rows.get(f["id"])
            if row is None:
                problems.append(f"{f['id']}: no verdict from {judge.model} (not conveyed)")
                out[f["id"]] = {"cue_ids": [], "reason": None}
                continue
            out[f["id"]] = {"cue_ids": [back[k] for k in row.get("cue_ids", []) if k in back],
                            "reason": row.get("reason")}  # fmt: skip
    return out, problems


def outcomes(facts: list[dict], verdicts: dict, cues: list[dict], window_s: float,
             duration: float) -> list[dict]:  # fmt: skip
    """Per fact: the cues the judge credits, and whether one of them counts within the window
    and anywhere, on the standard track alone and on both tracks."""
    by_id = {c["id"]: c for c in cues}
    out = []
    for f in facts:
        lo, hi = window(f, window_s, duration)
        credited = [by_id[i] for i in (verdicts.get(f["id"]) or {}).get("cue_ids", [])
                    if i in by_id]  # fmt: skip
        in_win = [c for c in credited if plays_in(c, lo, hi)]
        out.append({
            "id": f["id"], "ref_cue_id": f["ref_cue_id"], "source": f["source"],
            "kind": f["kind"], "anchor_time": f["anchor_time"], "window": [lo, hi],
            "credited": [c["id"] for c in credited],
            "reason": (verdicts.get(f["id"]) or {}).get("reason"),
            "standard_window": any(c["track"] != "extended" for c in in_win),
            "both_window": bool(in_win),
            "standard_anywhere": any(c["track"] != "extended" for c in credited),
            "both_anywhere": bool(credited),
        })  # fmt: skip
    return out


VARIANTS = ("standard_window", "both_window", "standard_anywhere", "both_anywhere")


def recall(rows: list[dict], kinds) -> dict:
    """Conveyed / facts for each variant, pooled, with a 95% bootstrap over reference lines."""
    rows = [r for r in rows if r["kind"] in kinds]
    lines: dict[str, list[dict]] = {}
    for r in rows:
        lines.setdefault(f"{r.get('video', '')}/{r['ref_cue_id']}", []).append(r)
    out = {"facts": len(rows), "lines": len(lines)}
    for v in VARIANTS:
        pairs = [(sum(x[v] for x in g), len(g)) for g in lines.values()]
        out[v] = bootstrap_ratio(pairs) if pairs else {"value": None, "num": 0, "den": 0,
                                                       "ci95": [None, None]}  # fmt: skip
    return out


# -- overlap and density --------------------------------------------------------------------------


def clip_spans(spans, clip) -> list[tuple[float, float]]:
    lo, hi = clip
    return [(max(a, lo), min(b, hi)) for a, b in spans if min(b, hi) > max(a, lo)]


def overlap_row(intervals, speech, clip) -> dict:
    iv = merge(clip_spans(intervals, clip))
    total = sum(b - a for a, b in iv)
    ov = overlap_seconds(iv, merge(clip_spans(speech, clip)))
    return {"description_s": round(total, 3), "overlap_s": round(ov, 3),
            "share": round(ov / total, 4) if total > 0 else None,
            "intervals": len(iv)}  # fmt: skip


def freeze_in_speech(cues: list[dict], speech) -> list[str]:
    ext = [{"id": c["id"], "start": c["start"]} for c in cues if c["track"] == "extended"]
    return A.freeze_points_in_speech(ext, merge(speech))


def lines_in(lines: list[dict], clip, full: bool, key: str) -> list[dict]:
    out = []
    for c in lines:
        if c.get("placement") == "before_program":
            if full:
                out.append(c)
        elif clip[0] - EPS <= c[key] <= clip[1] + EPS:
            out.append(c)
    return out


def density(texts: list[str], minutes: float) -> dict:
    words = sum(word_count(t) for t in texts)
    return {"words": words, "per_min": round(words / minutes, 2) if minutes > 0 else None}


def overlap_and_density(run: dict, ref: dict) -> dict:
    doc, clip, full = ref["doc"], run["clip"], run["full"]
    speech, cues = run["speech"], run["cues"]
    minutes = (clip[1] - clip[0]) / 60
    std = [c for c in cues if c["track"] == "standard"]
    ov = {"speech_source": run["speech_source"],
          "speech_s": round(sum(b - a for a, b in merge(clip_spans(speech, clip))), 3),
          "lull_standard": overlap_row([(c["start"], c["end"]) for c in std], speech, clip),
          "lull_extended_freeze_points_in_speech": freeze_in_speech(cues, speech)}  # fmt: skip
    dens = {"minutes": round(minutes, 4),
            "lull_standard": density([c["text"] for c in std], minutes),
            "lull_with_extended": density([c["text"] for c in cues], minutes)}  # fmt: skip
    if doc["kind"] == "vtt":
        for src, r in doc["references"].items():
            ov[f"{src}_vtt"] = overlap_row([(c["start"], c["end"]) for c in r["cues"]], speech,
                                           clip)  # fmt: skip
            dens[src] = density([c["text"] for c in lines_in(r["cues"], clip, full, "start")],
                                minutes)  # fmt: skip
        ov["hunt_wav"] = overlap_row([tuple(x) for x in doc["hunt_audio"]["vad"]["intervals"]],
                                     speech, clip)  # fmt: skip
    else:
        al, lines = doc["alignment"], doc["references"]["usgs_ad"]["cues"]
        inside = lines_in(lines, clip, full, "anchor_time")
        dens["usgs_ad"] = density([c["text"] for c in inside], minutes)
        if full:
            a_dur = doc["ad_media"]["duration_s"]
            own = overlap_row(al["description_in_ad_version"], al["narration_in_ad_version"],
                              (0.0, a_dur))  # fmt: skip
            missing = al["narration_missing_from_ad_version"]
            own["narration_missing_from_ad_version_s"] = round(
                sum(m["original"][1] - m["original"][0] for m in missing), 3
            )
            own["narration_missing_runs"] = len(missing)
            ov["usgs_ad_own_timeline"] = own
            dens["usgs_ad_per_ad_version_min"] = density([c["text"] for c in lines],
                                                         a_dur / 60)  # fmt: skip
    ov["supplementary_against_reference_asr"] = against_reference_asr(ov, run, doc)
    return {"overlap": ov, "density": dens}


def reference_speech(doc: dict) -> list[tuple[float, float]]:
    """Speech spans independent of the run: the committed transcript of the original video
    (refs/asr/, same recogniser, no diarization), words clamped as the engine does and joined
    across gaps under SPAN_JOIN_S."""
    data = json.loads((REFS / "asr" / f"{doc['video']}.json").read_text(encoding="utf-8"))
    words = clamped(data["words"])
    return merge_spans(((w["start"], w["end"]) for w in words), join=SPAN_JOIN_S)


def against_reference_asr(ov: dict, run: dict, doc: dict) -> dict:
    """Supplementary: the same sides against reference_speech instead of the run's spans, so
    Lull is not measured only against what its own listen stage heard."""
    speech, clip, cues = reference_speech(doc), run["clip"], run["cues"]
    std = [(c["start"], c["end"]) for c in cues if c["track"] == "standard"]
    out = {"speech_s": round(sum(b - a for a, b in clip_spans(speech, clip)), 3),
           "lull_standard": overlap_row(std, speech, clip),
           "lull_extended_freeze_points_in_speech": freeze_in_speech(cues, speech)}  # fmt: skip
    if doc["kind"] == "vtt":
        for src, r in doc["references"].items():
            out[f"{src}_vtt"] = overlap_row([(c["start"], c["end"]) for c in r["cues"]], speech,
                                            clip)  # fmt: skip
        out["hunt_wav"] = overlap_row([tuple(x) for x in doc["hunt_audio"]["vad"]["intervals"]],
                                      speech, clip)  # fmt: skip
    return out


# -- one video ------------------------------------------------------------------------------------


def score_video(run: dict, ref: dict, rules: dict, judges: dict, stamp: str,
                baseline: bool) -> dict:  # fmt: skip
    video, doc = run["video"], ref["doc"]
    duration = float(doc["media"]["duration_s"])
    facts = [f for f in ref["facts"] if f["kind"] in JUDGED_KINDS
             and in_scope(f, run["clip"], run["full"])]  # fmt: skip
    by_source: dict[str, list[dict]] = {}
    for f in facts:
        by_source.setdefault(f["source"], []).append(f)
    out = {"video": video, "run_id": run["run_id"], "clip": run["clip"], "full": run["full"],
           "references": {}, "baseline": {}, "judge_problems": []}  # fmt: skip
    w = float(rules["window_s"])
    for src, fs in sorted(by_source.items()):
        entry = {}
        for name, judge in judges.items():
            rid = f"suite-b-{'judge' if name == 'primary' else 'judge2'}-{video}-{stamp}"
            verdicts, problems = judge_facts(judge, rules, video, doc, fs, run["cues"], rid,
                                             f"suite_b_{src}")  # fmt: skip
            rows = outcomes(fs, verdicts, run["cues"], w, duration)
            for r in rows:
                r["video"] = video
            entry[name] = {"rows": rows, "problems": problems}
            out["judge_problems"] += [f"{name} {src}: {p}" for p in problems]
        out["references"][src] = entry
    if baseline and doc["kind"] == "vtt" and len(doc["references"]) == 2:
        a, b = sorted(doc["references"])
        for cue_src, fact_src in ((a, b), (b, a)):
            cues = human_cues(doc["references"][cue_src]["cues"], run["clip"])
            fs = by_source.get(fact_src, [])
            entry = {}
            for name, judge in judges.items():
                rid = f"suite-b-{'base' if name == 'primary' else 'base2'}-{video}-{stamp}"
                verdicts, problems = judge_facts(judge, rules, video, doc, fs, cues, rid,
                                                 f"suite_b_baseline_{cue_src}")  # fmt: skip
                rows = outcomes(fs, verdicts, cues, w, duration)
                for r in rows:
                    r["video"] = video
                entry[name] = {"rows": rows, "problems": problems}
                out["judge_problems"] += [f"{name} baseline {cue_src}: {p}" for p in problems]
            out["baseline"][f"{cue_src}_lines_vs_{fact_src}_facts"] = entry
    out.update(overlap_and_density(run, ref))
    return out


# -- aggregation ----------------------------------------------------------------------------------


def summarize(videos: list[dict], rules: dict) -> dict:
    scored, supp = tuple(rules["scored_kinds"]), tuple(rules["supplementary_kinds"])
    pools: dict[str, list[dict]] = {}
    pools2: dict[str, list[dict]] = {}
    base: dict[str, list[dict]] = {}
    for v in videos:
        for src, e in v["references"].items():
            pools.setdefault(src, []).extend(e["primary"]["rows"])
            if "second" in e:
                pools2.setdefault(src, []).extend(e["second"]["rows"])
        for name, e in v["baseline"].items():
            base.setdefault(name, []).extend(e["primary"]["rows"])
    out = {}
    for src, rows in pools.items():
        kinds = sorted({r["kind"] for r in rows if r["kind"] in scored})
        out[src] = {
            "recall": recall(rows, scored),
            "by_kind": {k: recall(rows, (k,)) for k in kinds},
            "interpretation": recall(rows, supp),
        }
        if src in pools2:
            rows2 = pools2[src]
            a = [r for r in rows if r["kind"] in scored]
            b = {r["id"] + "@" + r["video"]: r for r in rows2 if r["kind"] in scored}
            pairs = [
                (r, b[r["id"] + "@" + r["video"]]) for r in a if r["id"] + "@" + r["video"] in b
            ]
            out[src]["second_judge"] = {
                "recall": recall(rows2, scored),
                "agreement_window": agreement([x["both_window"] for x, _ in pairs],
                                              [y["both_window"] for _, y in pairs]),
                "agreement_anywhere": agreement([x["both_anywhere"] for x, _ in pairs],
                                                [y["both_anywhere"] for _, y in pairs]),
            }  # fmt: skip
    baseline = {name: recall(rows, scored) for name, rows in base.items()}
    return {"references": out, "baseline": baseline}


# -- report ---------------------------------------------------------------------------------------


def _pct(r: dict | None) -> str:
    if not r or r.get("value") is None:
        return "n/a"
    lo, hi = r["ci95"]
    ci = f" [{lo:.0%}, {hi:.0%}]" if lo is not None else ""
    return f"{r['value']:.1%} ({int(r['num'])}/{int(r['den'])}){ci}"


def _kappa(x: dict | None) -> str:
    if not x or x.get("agreement") is None:
        return "n/a"
    k = "undefined" if x["kappa"] is None else f"{x['kappa']:.2f}"
    return f"{x['agreement']:.1%}, kappa {k} (n={x['n']})"


def markdown(result: dict) -> str:
    pre, s = result["preregistration"], result["summary"]
    title = "results" if result["complete"] else "PARTIAL RUN (not results)"
    if result.get("smoke"):
        title = "SMOKE (not results)"
    lines = [
        f"# Suite B: {title}",
        "",
        f"Reference set sha256 `{result['reference_set_sha256']}`, pinned by tag `{pre['tag']}` "
        f"(commit `{pre['commit'][:12]}`, {pre['committed']}); scorer commit "
        f"`{result['git_commit'][:12]}`{' (dirty tree)' if result['git_dirty'] else ''}; "
        f"{result['date']}. Primary judge `{result['judge_model']}`; second judge "
        f"`{result['second_judge_model']}`. Window +/-{result['window_s']:g} s. Spend over the "
        f"referenced run ids: ${result['spend_usd']:.4f}.",
        "",
    ]
    if result.get("note"):
        lines += [f"**{result['note']}**", ""]
    lines += ["| video | Lull run | engine | covers (s) | published cues std / ext |",
              "|---|---|---|---|---|"]  # fmt: skip
    for r in result["runs"]:
        n_std = sum(c["track"] == "standard" for c in r["cues"])
        lines.append(f"| {r['video']} | `{r['run_id']}` | {(r.get('engine') or {}).get('engine')} "
                     f"| {r['clip'][0]:g}-{r['clip'][1]:g}{'' if r['full'] else ' (clip)'} | "
                     f"{n_std} / {len(r['cues']) - n_std} |")  # fmt: skip
    if result["not_scored"]:
        lines += ["", f"Reference videos not scored: {', '.join(result['not_scored'])}."]
    lines += ["", "## What Lull conveys of what the describers said", "",
              "Scored facts: visual and on-screen text. Primary: within the window. "
              "Supplementary: anywhere in the video.", "",
              "| reference | facts (lines) | window, standard | window, with extended | "
              "anywhere, with extended (supp.) |", "|---|---|---|---|---|"]  # fmt: skip
    for src, e in s["references"].items():
        r = e["recall"]
        lines.append(f"| {src} | {r['facts']} ({r['lines']}) | {_pct(r['standard_window'])} | "
                     f"{_pct(r['both_window'])} | {_pct(r['both_anywhere'])} |")  # fmt: skip
    lines += ["", "By kind (window, with extended):", ""]
    for src, e in s["references"].items():
        kinds = "; ".join(f"{k} {_pct(x['both_window'])}" for k, x in e["by_kind"].items())
        lines.append(f"- {src}: {kinds}; (supplementary) interpretation "
                     f"{_pct(e['interpretation']['both_window'])}")  # fmt: skip
    if s["baseline"]:
        lines += ["", "Human baseline (supplementary; same judge, prompt and window, the "
                  "judge is not told who wrote the cues):", ""]  # fmt: skip
        for name, r in s["baseline"].items():
            lines.append(f"- {name}: window {_pct(r['both_window'])}; anywhere "
                         f"{_pct(r['both_anywhere'])}")  # fmt: skip
    if any("second_judge" in e for e in s["references"].values()):
        lines += ["", "Second judge (supplementary):", ""]
        for src, e in s["references"].items():
            sj = e.get("second_judge")
            if sj:
                lines.append(f"- {src}: window, with extended {_pct(sj['recall']['both_window'])}; "
                             f"agreement with the primary judge: window "
                             f"{_kappa(sj['agreement_window'])}; anywhere "
                             f"{_kappa(sj['agreement_anywhere'])}")  # fmt: skip
    lines += [
        "",
        "## Talking over speech (every side against the same speech spans)",
        "",
        "| video | side | description s | over speech s | share |",
        "|---|---|---|---|---|",
    ]
    for v in result["videos"]:
        ov = v["overlap"]
        for side, row in ov.items():
            if isinstance(row, dict) and "description_s" in row and side != "usgs_ad_own_timeline":
                share = "n/a" if row["share"] is None else f"{row['share']:.1%}"
                lines.append(f"| {v['video']} | {side} | {row['description_s']:.1f} | "
                             f"{row['overlap_s']:.1f} | {share} |")  # fmt: skip
        fz = ov["lull_extended_freeze_points_in_speech"]
        lines.append(f"| {v['video']} | lull_extended (freeze points inside speech) | | "
                     f"{len(fz)} | |")  # fmt: skip
        own = ov.get("usgs_ad_own_timeline")
        if own:
            share = "n/a" if own["share"] is None else f"{own['share']:.1%}"
            lines.append(
                f"| {v['video']} | USGS AD, own timeline (not comparable) | "
                f"{own['description_s']:.1f} | {own['overlap_s']:.1f} | {share} |"
            )
    lines += ["", "Supplementary: the same sides against the reference transcript of the "
              "original (refs/asr, same recogniser, no diarization), not the run's own spans:",
              "", "| video | side | over speech s | share |", "|---|---|---|---|"]  # fmt: skip
    for v in result["videos"]:
        for side, row in v["overlap"]["supplementary_against_reference_asr"].items():
            if isinstance(row, dict):
                share = "n/a" if row["share"] is None else f"{row['share']:.1%}"
                lines.append(f"| {v['video']} | {side} | {row['overlap_s']:.1f} | {share} |")
    lines += ["", "Speech spans: the Lull run's listen output. USGS AD lives in pauses the AD "
              "version inserts; its own-timeline overlap compares two parts of one transcript "
              "and cannot show simultaneous speech, so it is not comparable to a standard "
              "track, which has to fit the original's own pauses (see the README).", "",
              "## Description density (words per minute of video)", "",
              "| video | minutes | side | words | per min |", "|---|---|---|---|---|"]  # fmt: skip
    for v in result["videos"]:
        d = v["density"]
        for side, row in d.items():
            if isinstance(row, dict):
                lines.append(f"| {v['video']} | {d['minutes']:.2f} | {side} | {row['words']} | "
                             f"{row['per_min']} |")  # fmt: skip
    problems = [f"{v['video']}: {p}" for v in result["videos"] for p in v["judge_problems"]]
    if problems:
        lines += ["", "Judge problems:", ""] + [f"- {p}" for p in problems]
    return "\n".join(lines) + "\n"


# -- main -----------------------------------------------------------------------------------------


def _pairs(values: list[str], what: str) -> dict[str, str]:
    out = {}
    for v in values or []:
        if "=" not in v:
            raise Refused(f"--{what} wants VIDEO=VALUE, got {v!r}")
        k, x = v.split("=", 1)
        if k not in VIDEOS:
            raise Refused(f"unknown video {k!r} (known: {', '.join(VIDEOS)})")
        out[k] = x
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", action="append", required=True, help="VIDEO=RUN_DIR (repeatable)")
    ap.add_argument("--offset", action="append", help="VIDEO=SECONDS: the run is of a clip")
    ap.add_argument("--cap", type=float, default=CAP_DEFAULT, help="USD this invocation may spend")
    ap.add_argument("--no-second-judge", action="store_true")
    ap.add_argument("--no-baseline", action="store_true", help="skip the human baseline")
    ap.add_argument("--smoke", action="store_true", help="label the report a smoke")
    ap.add_argument("--data", type=Path, default=DATA_DEFAULT, help="lull-data (ASR fallback)")
    args = ap.parse_args(argv)

    prereg, digest = pin()
    rules, refs = load_reference_set()
    paths = _pairs(args.run, "run")
    offsets = {k: float(v) for k, v in _pairs(args.offset, "offset").items()}
    if set(offsets) - set(paths):
        raise Refused(f"--offset for a video without --run: {sorted(set(offsets) - set(paths))}")
    runs = []
    for video, p in paths.items():
        run = load_run(video, Path(p), offsets.get(video, 0.0),
                       float(refs[video]["doc"]["media"]["duration_s"]))  # fmt: skip
        if run["speech"] is None:
            if not run["full"]:
                raise Refused(f"{video}: a clip run without speech spans")
            run["speech"] = listen_speech(video, args.data)
            run["speech_source"] = "engine listen stage on the original video (fallback)"
        runs.append(run)
    complete = all(r["full"] for r in runs) and not args.smoke
    folder = RESULTS if complete else PARTIAL
    guard_results_write(folder)  # before spending anything

    c = A.clients(args.cap, need_speech=False)
    budget: Budget = c["budget"]
    judges = {"primary": Judge(c["tf"], budget, model=ULTRA, thinking=True, cache=JUDGE_CACHE)}
    if not args.no_second_judge:
        judges["second"] = Judge(c["tf"], budget, model=SECOND_JUDGE, thinking=False,
                                 cache=JUDGE_CACHE)  # fmt: skip
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    videos = []
    for run in runs:
        print(f"{run['video']}: judging {run['run_id']} ...", flush=True)
        videos.append(score_video(run, refs[run["video"]], rules, judges, stamp,
                                  baseline=not args.no_baseline))  # fmt: skip
    rows = [r for r in c["ledger"].runs() if r["run_id"].startswith("suite-b-")]
    referenced = set().union(*(j.paid_by for j in judges.values()))
    first_ts = [r["first_ts"] for r in rows if r["run_id"] in referenced and r.get("first_ts")]
    prereg_ts = datetime.fromisoformat(prereg["committed"]).timestamp()
    note = None
    if not complete:
        note = ("smoke on a clip: partial, not Suite B results" if args.smoke
                else "partial run (a clip or a subset): not Suite B results")  # fmt: skip
    result = {
        "suite": "B",
        "complete": complete,
        "smoke": args.smoke,
        "note": note,
        "date": now_utc(),
        "git_commit": git_run("rev-parse", "HEAD")[1].strip(),
        "git_dirty": bool(dirty()),
        "preregistration": prereg,
        "reference_set_sha256": digest,
        "ledger_calls_after_preregistration": bool(first_ts) and min(first_ts) > prereg_ts,
        "judge_model": ULTRA,
        "second_judge_model": None if args.no_second_judge else SECOND_JUDGE,
        "window_s": float(rules["window_s"]),
        "runs": runs,
        "not_scored": [v for v in VIDEOS if v not in paths],
        "summary": summarize(videos, rules),
        "videos": videos,
        "spend_usd": round(A.spend_of(referenced, rows), 6),
        "referenced_run_ids": sorted(referenced),
        "judge_files": sorted({Path(p).name for j in judges.values() for p in j.files}),
    }
    guard_results_write(folder)
    write_json(folder / "suite_b.json", result)
    (folder / "SUITE_B.md").write_text(markdown(result), encoding="utf-8", newline="\n")
    print(f"spent ${result['spend_usd']:.4f}; wrote {folder / 'suite_b.json'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
