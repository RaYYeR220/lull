"""A Python port of the website's benchmarks.json validator (lull-web: web/lib/benchmarks-schema.ts,
parseBenchmarks / parseSuiteA, as of 2026-10-28).

The site reads `{generated_at, git_commit, fixture?, suites: {suite_a?, ...}, known_failures}`;
suite_a is the Suite A scorer's own results object. A field the page needs that is missing or of
the wrong kind raises BenchmarksError naming it, with the site's wording; everything else is
ignored, and suites other than suite_a are only listed (the page does not draw them yet). The
report validates its output with this port before writing it; the tests also run the site's own
TypeScript validator through node when it is available.
"""

from __future__ import annotations

import math


class BenchmarksError(ValueError):
    def __init__(self, where: str, what: str) -> None:
        super().__init__(f"benchmarks.json: {where} {what}")


def _is_obj(v) -> bool:
    return isinstance(v, dict)


def _is_num(v) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


class _At:
    """The site's `at(o, key, where)` readers."""

    def __init__(self, o: dict, key: str, where: str) -> None:
        self.v, self.here = o.get(key), f"{where}.{key}"

    def str(self) -> str:
        if not isinstance(self.v, str) or not self.v:
            raise BenchmarksError(self.here, "must be a non-empty string")
        return self.v

    def num(self) -> float:
        if not _is_num(self.v):
            raise BenchmarksError(self.here, "must be a number")
        return self.v

    def obj(self) -> dict:
        if not _is_obj(self.v):
            raise BenchmarksError(self.here, "must be an object")
        return self.v

    def opt_str(self):
        return self.v if isinstance(self.v, str) and self.v else None

    def opt_num(self):
        return self.v if _is_num(self.v) else None

    def opt_bool(self):
        return self.v if isinstance(self.v, bool) else None

    def opt_obj(self):
        return self.v if _is_obj(self.v) else None

    def strings(self) -> list[str]:
        return [x for x in self.v if isinstance(x, str)] if isinstance(self.v, list) else []


def _pair(v):
    if isinstance(v, list) and len(v) == 2 and _is_num(v[0]) and _is_num(v[1]):
        return [v[0], v[1]]
    return None


def _ratio(v, where: str) -> dict:
    if not _is_obj(v):
        raise BenchmarksError(where, "must be a ratio {value, num, den, ci95}")
    num = _At(v, "num", where).num()
    den = _At(v, "den", where).num()
    value = None if v.get("value") is None else _At(v, "value", where).num()
    return {"value": value, "num": num, "den": den, "ci95": _pair(v.get("ci95"))}


def _opt_ratio(v, where: str):
    if v is None:
        return None
    return _ratio(v, where) if _is_obj(v) else None


def _video(v, i: int) -> dict:
    where = f"suites.suite_a.videos[{i}]"
    if not _is_obj(v):
        raise BenchmarksError(where, "must be an object")
    return {"id": _At(v, "id", where).str(), "duration_s": _At(v, "duration_s", where).num(),
            "failed": v.get("failed") or None}  # fmt: skip


def parse_suite_a(raw) -> dict:
    where = "suites.suite_a"
    if not _is_obj(raw):
        raise BenchmarksError(where, "must be an object")
    pre = _At(raw, "preregistration", where).obj()
    agg = _At(raw, "aggregate", where).obj()
    aw = f"{where}.aggregate"

    def a(k):
        return _At(agg, k, aw)

    def r(k):
        return _ratio(agg.get(k), f"{aw}.{k}")

    def opt_r(k):
        return _opt_ratio(agg.get(k), f"{aw}.{k}")

    cost, proc = a("cost").obj(), a("processing").obj()
    return {
        "complete": raw.get("complete") is True,
        "date": _At(raw, "date", where).str(),
        "git_commit": _At(raw, "git_commit", where).str(),
        "preregistration": {k: _At(pre, k, f"{where}.preregistration").str()
                            for k in ("tag", "commit", "committed")},  # fmt: skip
        "key_sha256": _At(raw, "key_sha256", where).str(),
        "aggregate": {
            "videos": a("videos").num(),
            "minutes": a("minutes").num(),
            "overlap_s_total": a("overlap_s_total").num(),
            "recall_standard": r("recall_standard"),
            "recall_with_extended": r("recall_with_extended"),
            "hallucination_claims": r("hallucination_claims"),
            "redundancy_cues": r("redundancy_cues"),
            "decoys_stated": a("decoys_stated").num(),
            "decoys_total": a("decoys_total").num(),
            **{k: opt_r(k) for k in (
                "timely_recall_with_extended", "hallucination_cues", "spoken_facts_restated",
                "recall_tie_aware_standard", "recall_tie_aware_with_extended",
                "hallucination_voiced_text", "voiced_unverifiable", "voiced_cues_with_unsupported",
                "claims_not_in_cue_text", "hallucination_claims_in_cue_text")},
            "cost": {"model_usd": _At(cost, "model_usd", f"{aw}.cost").num(),
                     "per_min_ci95": _pair(cost.get("per_min_ci95"))},
            "processing": {"seconds": _At(proc, "seconds", f"{aw}.processing").num(),
                           "s_per_min_ci95": _pair(proc.get("s_per_min_ci95"))},
        },
        "videos": [_video(v, i) for i, v in enumerate(raw.get("videos") or [])
                   if isinstance(raw.get("videos"), list)],
    }  # fmt: skip


def parse_benchmarks(raw) -> dict:
    """The whole file; BenchmarksError names the first field that does not hold."""
    if not _is_obj(raw):
        raise BenchmarksError("the file", "must be a JSON object")
    suites = _At(raw, "suites", "").obj()
    failures = raw.get("known_failures")
    if not isinstance(failures, list):
        raise BenchmarksError(".known_failures", "must be a list of {title, detail}")
    out = {  # in the site's order, so the first error is the site's first error
        "generated_at": _At(raw, "generated_at", "").str(),
        "git_commit": _At(raw, "git_commit", "").str(),
        "fixture": raw.get("fixture") is True,
        "suite_a": parse_suite_a(suites["suite_a"]) if "suite_a" in suites else None,
        "other_suites": [k for k, v in suites.items() if k != "suite_a" and v is not None],
    }
    known = []
    for i, x in enumerate(failures):
        if not _is_obj(x):
            raise BenchmarksError(f".known_failures[{i}]", "must be an object")
        known.append({"title": _At(x, "title", f".known_failures[{i}]").str(),
                      "detail": _At(x, "detail", f".known_failures[{i}]").str()})  # fmt: skip
    return {**out, "known_failures": known}
