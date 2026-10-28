"""Controls: the negative control's selection, mismatch and metrics, the verification adapter,
and the ablation's bookkeeping. Fakes only: no network, no media."""

import json
import random

import pytest

from controls import ablation as AB
from controls import negative as N
from controls import verify_adapter as VA
from suite_a.score import Refused

# -- fake runs -------------------------------------------------------------------------------------


def cue(cid, start, status, gap="g1", events=("e1",), text=None):
    return {"id": cid, "gap_id": gap, "start": start, "end": start + 3, "status": status,
            "event_ids": list(events), "text": text or f"cue {cid}"}  # fmt: skip


def event(eid, t0, t1):
    return {"id": eid, "t_start": t0, "t_end": t1, "kind": "action", "text": eid,
            "verbatim_text": None, "salience": 3, "said_in_dialogue": False}  # fmt: skip


def run(label, cues, extended=(), events=(), duration=120.0, slides=None):
    return {"label": label, "duration": duration, "video": f"{label}.mp4",
            "cues_json": {"cues": list(cues), "extended": list(extended)},
            "events": {e["id"]: e for e in events}, "slides": slides}  # fmt: skip


# -- selection -------------------------------------------------------------------------------------


def test_selection_takes_verified_standard_and_extended_cues_in_order():
    a = run("a01", [cue("c2", 20, "verified"), cue("c1", 5, "verified"), cue("c3", 30, "held"),
                    cue("c4", 40, "edited")],
            extended=[cue("x1", 10, "extended", gap=None),
                      cue("x2", 12, "held", gap=None)])  # fmt: skip
    b = run("a00", [cue("c1", 50, "verified")])
    got = [(r, c["id"], c["track"]) for r, c in N.select([a, b])]
    assert got == [("a00", "c1", "standard"), ("a01", "c1", "standard"),
                   ("a01", "x1", "extended"), ("a01", "c2", "standard")]  # fmt: skip


def test_selection_samples_40_with_the_fixed_seed():
    runs = [run(f"v{k}", [cue(f"c{i:02d}", i, "verified") for i in range(20)]) for k in range(3)]
    first = N.select(runs)
    assert len(first) == N.MAX_CUES == 40
    assert first == N.select(list(reversed(runs)))  # input order does not matter
    keys = [(r, c["start"]) for r, c in first]
    assert keys == sorted(keys)  # kept in (video, start) order
    assert N.select(runs, seed=1) != first


# -- mismatched windows ----------------------------------------------------------------------------

SLIDES = [{"index": 1, "start": 0.0, "end": 12.36},
          {"index": 2, "start": 12.36, "end": 22.6},
          {"index": 3, "start": 22.6, "end": 35.44},
          {"index": 4, "start": 35.44, "end": 48.56}]  # fmt: skip


def overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def test_slide_mismatch_is_a_whole_other_slide_of_the_same_length():
    w = N.slide_mismatch((10.0, 16.0), SLIDES)
    assert w == pytest.approx((26.02, 32.02))  # slide 3, the nearest one the true window misses
    assert N.slide_mismatch((0.0, 48.0), SLIDES) is None  # every slide is touched
    assert N.slide_mismatch((0.0, 11.0), SLIDES[:2]) is None  # slide 2 is too short for 11 s


def test_slide_mismatch_never_touches_the_true_window_or_its_slides():
    rng = random.Random(7)
    for _ in range(500):
        lo = rng.uniform(0, 44)
        hi = min(48.56, lo + rng.uniform(4, 12))
        w = N.slide_mismatch((lo, hi), SLIDES)
        if w is None:
            continue
        assert w[1] - w[0] == pytest.approx(hi - lo)
        assert not overlaps(w, (lo, hi))
        touched = [s for s in SLIDES if overlaps((s["start"], s["end"]), (lo, hi))]
        assert not any(overlaps(w, (s["start"], s["end"])) for s in touched)


EVENTS = {e["id"]: e for e in [event("e1", 0, 20), event("e2", 20, 45), event("e3", 45, 80),
                               event("e4", 80, 120)]}  # fmt: skip


def test_event_mismatch_is_30_s_away_and_shares_no_event():
    w = N.event_mismatch((22.0, 30.0), {"e2"}, EVENTS, 120.0)
    assert w is not None and w[1] - w[0] == pytest.approx(8.0)
    assert w[0] >= 60.0 or w[1] <= -8.0
    inside = {e for e, x in EVENTS.items() if x["t_start"] < w[1] and x["t_end"] > w[0]}
    assert "e2" not in inside and inside
    long_event = {"e9": event("e9", 0, 120)}
    assert N.event_mismatch((22.0, 30.0), {"e9"}, long_event, 120.0) is None


def test_event_mismatch_rules_hold_for_random_windows():
    rng = random.Random(3)
    for _ in range(300):
        lo = rng.uniform(0, 110)
        hi = min(120.0, lo + rng.uniform(4, 12))
        ids = {e for e, x in EVENTS.items() if x["t_start"] < hi and x["t_end"] > lo}
        w = N.event_mismatch((lo, hi), ids, EVENTS, 120.0)
        if w is None:
            continue
        assert w[0] >= hi + N.AWAY_S - 1e-9 or w[1] <= lo - N.AWAY_S + 1e-9
        assert 0 <= w[0] and w[1] <= 120.0 + 1e-9
        inside = {e for e, x in EVENTS.items() if x["t_start"] < w[1] and x["t_end"] > w[0]}
        assert not inside & ids and inside


def test_mismatch_falls_back_to_another_video():
    short = run("a01", [], duration=10.0, slides=[{"index": 1, "start": 0.0, "end": 10.0}])
    other = run("a02", [], duration=48.56, slides=SLIDES)
    m = N.mismatched("a01", (2.0, 8.0), {"e1"}, [short, other])
    assert m["video"] == "a02" and m["rule"] == "other video"
    assert m["window"][1] - m["window"][0] == pytest.approx(6.0)
    same = N.mismatched("a02", (10.0, 16.0), {"e1"}, [short, other])
    assert same["video"] == "a02" and same["rule"] == "another slide"
    film = run("ed", [], events=EVENTS.values())
    assert N.mismatched("ed", (22.0, 30.0), {"e2"}, [film])["rule"] == "other events, 30 s away"


# -- metrics ---------------------------------------------------------------------------------------


def claims(*verdicts):
    return [{"text": f"claim {i}", "verdict": v, "at": None} for i, v in enumerate(verdicts)]


def test_outcome_needs_every_claim_supported_and_at_least_one():
    assert N.outcome(claims("supported", "supported"))["fully_supported"]
    assert not N.outcome(claims("supported", "not_visible"))["fully_supported"]
    assert not N.outcome([])["fully_supported"]
    assert N.outcome(claims("supported", "contradicted")) == {
        "claims": 2, "supported": 1, "fully_supported": False}  # fmt: skip


def test_rates_and_their_intervals():
    rows = [{"true": N.outcome(claims("supported")), "mismatched": N.outcome(claims("supported"))},
            {"true": N.outcome(claims("supported", "supported")),
             "mismatched": N.outcome(claims("not_visible", "supported"))},
            {"true": N.outcome(claims("not_visible")),
             "mismatched": N.outcome(claims("contradicted"))},
            {"true": N.outcome(claims("supported")), "mismatched": N.outcome([])}]  # fmt: skip
    m = N.metrics(rows)
    assert m["false_accept"]["value"] == pytest.approx(1 / 4)
    assert m["false_accept_claims"]["value"] == pytest.approx(2 / 4)
    assert m["true_accept"]["value"] == pytest.approx(3 / 4)
    for k in ("false_accept", "false_accept_claims", "true_accept"):
        lo, hi = m[k]["ci95"]
        assert lo <= m[k]["value"] <= hi
    assert N.metrics(rows) == m  # fixed seed


# -- the adapter -----------------------------------------------------------------------------------


def test_the_adapter_is_the_only_caller_of_the_engine_verifier(monkeypatch, tmp_path):
    from lull.stages import verify as V
    from lull.types import Claim

    seen = {}

    def fake_verify(text, clip, tf, **kw):
        seen.update(text=text, clip=clip, tf=tf, **kw)
        return [Claim(text="A slide is shown.", verdict="supported", at=2.0)]

    monkeypatch.setattr(V, "verify", fake_verify)
    out = VA.check("A slide is shown.", tmp_path / "c.mp4", offset=10.0, tf="TF", run_id="r")
    assert out == [{"text": "A slide is shown.", "verdict": "supported", "at": 2.0}]
    assert seen == {"text": "A slide is shown.", "clip": tmp_path / "c.mp4", "tf": "TF",
                    "run_id": "r", "names": (), "offset": 10.0}  # fmt: skip
    lo, hi = VA.clip_window([event("e1", 10.0, 11.0)], 60.0)
    assert (lo, hi) == (V.clip_window(
        [V.VisualEvent(**event("e1", 10.0, 11.0))], 60.0))  # fmt: skip
    for path in (N.__file__, AB.__file__):
        text = open(path, encoding="utf-8").read()
        assert "lull.stages.verify" not in text and "stages import verify" not in text


def test_verification_switch_is_read_from_the_manifest():
    assert VA.verification_disabled({"verification": "disabled"})
    assert VA.verification_disabled({"summary": {"verification": "disabled"}})
    assert not VA.verification_disabled({"summary": {}})


# -- the ablation ----------------------------------------------------------------------------------


def test_unverified_cues_count_as_published_in_the_ablation():
    cj = {"cues": [cue("c1", 1, "unverified"), cue("c2", 5, "held")],
          "extended": [cue("x1", 9, "unverified", gap=None)]}  # fmt: skip
    out = AB.as_published(cj)
    assert [(c["id"], c["status"], c.get("engine_status")) for c in out["cues"]] == [
        ("c1", "verified", "unverified"),
        ("c2", "held", None),
    ]
    assert out["extended"][0]["status"] == "extended"
    assert cj["cues"][0]["status"] == "unverified"  # the run's own file is untouched


def test_paired_difference_over_videos():
    with_v = [(1, 10), (0, 10), (2, 10)]
    without = [(3, 10), (1, 10), (5, 10)]  # each video worse by 0.2, 0.1 and 0.3
    d = AB.paired_difference(with_v, without)
    assert d["with"] == pytest.approx(0.1) and d["without"] == pytest.approx(0.3)
    assert d["difference"] == pytest.approx(0.2)
    lo, hi = d["ci95"]
    assert lo <= 0.2 <= hi and lo > 0  # every video got worse without verification
    assert AB.paired_difference(with_v, without) == d


def test_ablation_refuses_without_the_switch_or_with_another_engine(monkeypatch):
    monkeypatch.delenv("LULL_NO_VERIFY", raising=False)
    with pytest.raises(Refused, match="LULL_NO_VERIFY=1"):
        AB.require_switch()
    monkeypatch.setenv("LULL_NO_VERIFY", "1")
    AB.require_switch()
    good = {"complete": True, "engine_fingerprint": "fp", "videos": [{"id": "a01"}]}
    AB.check_with_arm(good, "fp")
    with pytest.raises(Refused, match="complete"):
        AB.check_with_arm({**good, "complete": False}, "fp")
    with pytest.raises(Refused, match="engine"):
        AB.check_with_arm(good, "other")
    with pytest.raises(Refused, match="disabled"):
        AB.check_disabled({"summary": {}}, "ablation-a01-x")


def test_ablation_writes_its_own_files_only():
    assert AB.RESULT_NAME == "ablation.json"
    assert AB.destination(all_videos=True, scored=12, total=12).name == "results"
    assert AB.destination(all_videos=False, scored=2, total=12) == AB.PARTIAL
    assert "suite_a" not in AB.RESULT_NAME


def test_ablation_runs_land_in_their_own_folder(monkeypatch, tmp_path):
    import lull.pipeline as P

    from suite_a import score as A

    calls = {}

    def fake_run(video, run_dir, opts, **kw):
        calls.update(run_dir=run_dir, run_id=kw["run_id"])

    monkeypatch.setattr(P, "run", fake_run)
    monkeypatch.setattr(A, "engine_numbers", lambda *a: {})

    class B:
        def check(self, *a):
            pass

        def add_run(self, *a):
            pass

    entry = {"id": "a01", "duration": 48.0}
    rec = A.run_video(entry, tmp_path / "v.mp4", {"tf": None, "speech": None, "ledger": None},
                      "T", B(), runs=tmp_path / "runs", prefix="ablation")  # fmt: skip
    assert rec["run_id"] == "ablation-a01-T" and calls["run_id"] == "ablation-a01-T"
    assert calls["run_dir"] == tmp_path / "runs" / "ablation-a01-T"


# -- the pin ---------------------------------------------------------------------------------------


class FakeGit:
    def __init__(self, **over):
        self.table = {"rev-parse": (0, "abc\n"), "merge-base": (0, ""),
                      "show-sha": (0, "d00d  controls/prereg.json\n"), "diff": (0, ""),
                      "show-date": (0, "2026-10-28T05:00:00+00:00\n")}  # fmt: skip
        self.table.update(over)
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "show":
            return self.table["show-sha" if ":" in args[1] else "show-date"]
        return self.table[args[0]]


def test_controls_pin(monkeypatch):
    monkeypatch.setattr(N, "prereg_digest", lambda: "d00d")
    git = FakeGit()
    assert N.pin(git)["tag"] == "controls-prereg"
    diff = next(c for c in git.calls if c[0] == "diff")
    assert "eval/controls/negative.py" in diff and "eval/controls/prereg.json" in diff
    assert "eval/controls/verify_adapter.py" not in diff  # the adapter may follow the engine
    with pytest.raises(Refused, match="controls-prereg not found"):
        N.pin(FakeGit(**{"rev-parse": (1, "")}))
    with pytest.raises(Refused, match="tagged prereg.sha256"):
        N.pin(FakeGit(**{"show-sha": (0, "beef  controls/prereg.json\n")}))


def test_prereg_file_states_the_rules():
    rules = json.loads(N.PREREG.read_text(encoding="utf-8"))
    neg, abl = rules["negative_control"], rules["ablation"]
    assert neg["max_cues"] == N.MAX_CUES and neg["seed"] == N.SEED and neg["away_s"] == N.AWAY_S
    assert neg["grid_s"] == N.GRID_S
    assert set(neg) >= {"selection", "true_clip", "mismatch", "metrics", "verifier"}
    assert set(abl) >= {"runs", "with_arm", "metrics", "headline", "difference"}
