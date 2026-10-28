import tarfile

import pytest

from suite_a import score
from suite_a.score import (
    PARTIAL,
    RESULTS,
    Budget,
    Judge,
    Refused,
    absent_part,
    aggregate,
    agreement,
    bootstrap_ratio,
    capacity_violations,
    chart_ties,
    claim_in_text,
    claims_request,
    coverage_request,
    cue_span,
    decoy_number_hits,
    destination,
    dirty,
    exact_matches,
    first_complete_all,
    freeze_points_in_speech,
    guard_results_write,
    judge_cues,
    judge_vectors,
    negative_probes,
    overlap_seconds,
    probe_cues,
    probe_scores,
    published,
    run_inventory,
    select_reuse,
    silence,
    spend_of,
    tagged,
    title_match,
    value_match,
    verify_prereg,
    video_metrics,
    write_bundle,
)

# -- fakes ----------------------------------------------------------------------------------------

CHART = {"type": "line", "title": "ATP Output", "x_label": "Hour", "y_label": "Molecules",
         "points": [{"label": "H1", "value": 11.0}, {"label": "H2", "value": 23.0},
                    {"label": "H3", "value": 23.0}], "y_ticks": [0, 10, 20]}  # fmt: skip

ENTRY = {
    "id": "t01",
    "subject": "testing",
    "duration": 60.0,
    "slides": [
        {"index": 1, "start": 0.0, "end": 30.0, "title": "Cell Energy",
         "bullets": ["Cells need energy", "Mitochondria make ATP"], "chart": None,
         "inventory": "Slide 1 ..."},
        {"index": 2, "start": 30.0, "end": 60.0, "title": "Output Over Time",
         "bullets": ["Oxygen helps"], "chart": CHART, "inventory": "Slide 2 ..."},
    ],
    "narration": [
        {"slide": 1, "index": 1, "text": "Cells need energy.", "start": 2.0, "end": 5.0},
        {"slide": 2, "index": 1, "text": "Enzymes speed reactions.", "start": 33.0, "end": 36.0},
    ],
    "visual_facts": [
        {"id": "t01-v01", "slide": 1, "kind": "title", "text": 'The slide title reads "Cell '
         'Energy".', "anchor": "Cell Energy", "value": None, "slide_start": 0.0,
         "slide_end": 30.0, "exact": {"type": "title", "text": "Cell Energy"}},
        {"id": "t01-v02", "slide": 2, "kind": "chart_value", "text": 'The highest value in the '
         'line chart "ATP Output" is 23, for "H2".', "anchor": "H2", "value": 23.0,
         "slide_start": 30.0, "slide_end": 60.0,
         "exact": {"type": "number", "value": 23.0, "label": "H2"}},
        {"id": "t01-v03", "slide": 2, "kind": "bullet", "text": 'A bullet reads "Oxygen helps".',
         "anchor": "Oxygen helps", "value": None, "slide_start": 30.0, "slide_end": 60.0,
         "exact": None},
    ],
    "spoken_facts": [
        {"id": "t01-s01", "slide": 1, "kind": "bullet", "text": 'A bullet reads "Cells need '
         'energy".', "anchor": "Cells need energy", "slide_start": 0.0, "slide_end": 30.0},
    ],
    "absent_decoys": [
        {"id": "t01-d01", "kind": "chart_value", "text": "The chart shows Lactate at 8.3.",
         "numbers": [8.3]},
        {"id": "t01-d02", "kind": "image", "text": "A photo of a microscope.", "numbers": []},
    ],
}  # fmt: skip


def cue(cid, start, dur, status="verified", gap="g001", text="x", claims=None, **kw):
    return {"id": cid, "gap_id": gap, "start": start, "end": start + dur, "audio_dur": dur,
            "status": status, "text": text,
            "claims": [{"text": t, "verdict": "supported", "at": None} for t in (claims or [text])],
            **kw}  # fmt: skip


def cues_json():
    return {
        "cues": [
            cue("c001", 6.0, 2.0, text="A slide reads: Cell Energy."),
            cue(
                "c002",
                37.0,
                3.0,
                gap="g002",
                text="A line chart peaks at 23 for H3.",
                claims=["A line chart is shown", "it peaks at 23 for H3"],
            ),  # fmt: skip
            cue("c003", 20.0, 2.0, status="held", text="held text"),
        ],
        "extended": [
            cue(
                "x001",
                31.0,
                4.0,
                status="extended",
                gap=None,
                freeze_at=31.0,
                text="A photo of a microscope.",
            ),  # fmt: skip
        ],
        "gaps": [
            {"id": "g001", "usable_start": 5.2, "usable_end": 32.8},
            {"id": "g002", "usable_start": 36.2, "usable_end": 39.0},
        ],
    }


def verdicts():
    return {
        "coverage": {
            "visual_facts": [
                {"id": "t01-v01", "reason": "read", "cue_ids": ["c001"]},
                {"id": "t01-v02", "reason": "value", "cue_ids": ["c002"]},
                {"id": "t01-v03", "reason": "none", "cue_ids": []},
            ],
            "spoken_facts": [{"id": "t01-s01", "reason": "no", "cue_ids": []}],
            "decoys": [
                {"id": "t01-d01", "reason": "no", "cue_ids": []},
                {"id": "t01-d02", "reason": "stated", "cue_ids": ["x001"]},
            ],
        },
        "claims": {
            "claims": [
                {"key": "c001#1", "reason": "ok", "verdict": "supported"},
                {"key": "c002#1", "reason": "ok", "verdict": "supported"},
                {"key": "c002#2", "reason": "ok", "verdict": "supported"},
                {"key": "x001#1", "reason": "no photo", "verdict": "unsupported"},
            ],
            "cues": [
                {"id": "c001", "restates_narration": False, "text_unsupported": False},
                {"id": "c002", "restates_narration": False, "text_unsupported": False},
                {"id": "x001", "restates_narration": True, "text_unsupported": True},
            ],
        },
    }


def run_record(**kw):
    return {"run_id": "suite-a-t01-x", "error": None, "engine_fingerprint": "fp",
            "engine": {"cost_model_usd": 0.03, "cost_tavily_usd": 0.0, "processing_s": 90.0,
                       "extended_produced": True,
                       "cue_counts": {"verified": 2, "held": 1, "extended": 1}}, **kw}  # fmt: skip


class FakeGit:
    """Answers git commands from a table: (args...) -> (returncode, stdout)."""

    def __init__(self, **overrides):
        self.table = {
            "rev-parse": (0, "194d4d0aaaa\n"),
            "merge-base": (0, ""),
            "show-key": (0, "abc123  key.json\n"),
            "diff": (0, ""),
            "show-date": (0, "2026-10-28T02:12:13+01:00\n"),
            "status": (0, ""),
        }
        self.table.update(overrides)

    def __call__(self, *args):
        if args[0] == "show":
            return self.table["show-key" if ":" in args[1] else "show-date"]
        return self.table[args[0]]


# -- the pre-registration pin ----------------------------------------------------------------------


def test_prereg_ok_returns_tag_and_commit():
    out = verify_prereg("abc123", git=FakeGit())
    assert out["tag"] == "suite-a-prereg" and out["commit"] == "194d4d0aaaa"


@pytest.mark.parametrize(
    "override, words",
    [
        ({"rev-parse": (1, "")}, "not found"),
        ({"merge-base": (1, "")}, "not an ancestor"),
        ({"show-key": (0, "fff  key.json\n")}, "tagged key.sha256"),
        ({"show-key": (128, "")}, "tagged key.sha256"),
        ({"diff": (0, "eval/suite_a/render.py\n")}, "render.py"),
    ],
)
def test_prereg_refusals(override, words):
    with pytest.raises(Refused, match=words):
        verify_prereg("abc123", git=FakeGit(**override))


def test_dirty_tree_blocks_results_but_not_partial():
    git = FakeGit(status=(0, " M engine/src/lull/tf.py\n"))
    assert dirty(git) == [" M engine/src/lull/tf.py"]
    with pytest.raises(Refused, match="uncommitted"):
        guard_results_write(RESULTS, git)
    guard_results_write(PARTIAL, git)  # a subset may be scored from a dirty tree
    guard_results_write(RESULTS, FakeGit())


def test_subset_never_writes_results():
    assert destination(all_videos=False, scored=12, total=12) == PARTIAL
    assert destination(all_videos=True, scored=11, total=12) == PARTIAL
    assert destination(all_videos=True, scored=12, total=12) == RESULTS


# -- run selection ---------------------------------------------------------------------------------

RECORDS = [
    {"run_id": "suite-a-a01-20261028T010000Z", "video": "a01", "engine_fingerprint": "fpA",
     "started_utc": "2026-10-28T01:00:00+00:00", "error": None},
    {"run_id": "suite-a-a01-20261028T020000Z", "video": "a01", "engine_fingerprint": "fpB",
     "started_utc": "2026-10-28T02:00:00+00:00", "error": None},
    {"run_id": "suite-a-a01-20261028T030000Z", "video": "a01", "engine_fingerprint": "fpB",
     "started_utc": "2026-10-28T03:00:00+00:00", "error": "BudgetExceeded: run"},
]  # fmt: skip
INVOCATIONS = [
    {"stamp": "20261028T030000Z", "mode": "all", "complete": True, "engine_fingerprint": "fpB",
     "runs": {"a01": "suite-a-a01-20261028T030000Z"}},
    {"stamp": "20261028T020000Z", "mode": "all", "complete": True, "engine_fingerprint": "fpB",
     "runs": {"a01": "suite-a-a01-20261028T020000Z"}},
    {"stamp": "20261028T010000Z", "mode": "all", "complete": False, "engine_fingerprint": "fpA",
     "runs": {"a01": "suite-a-a01-20261028T010000Z"}},
]  # fmt: skip


def test_subset_reuse_takes_the_latest_attempt_on_this_engine_even_when_it_failed():
    got = select_reuse(RECORDS, [], "fpB", ["a01"], "subset")
    assert got["a01"]["run_id"].endswith("030000Z") and got["a01"]["error"]
    assert select_reuse(RECORDS, [], "fpA", ["a01"], "subset")["a01"]["run_id"].endswith("010000Z")
    with pytest.raises(Refused, match="no run"):
        select_reuse(RECORDS, [], "fpC", ["a01"], "subset")


def test_all_reuse_takes_the_first_complete_all_invocation_on_this_engine():
    assert first_complete_all(INVOCATIONS, "fpB")["stamp"] == "20261028T020000Z"
    assert first_complete_all(INVOCATIONS, "fpA") is None  # incomplete
    got = select_reuse(RECORDS, INVOCATIONS, "fpB", ["a01"], "all")
    assert got["a01"]["run_id"].endswith("020000Z")
    with pytest.raises(Refused, match="no complete --all"):
        select_reuse(RECORDS, INVOCATIONS, "fpA", ["a01"], "all")


def test_inventory_lists_every_suite_run_with_outcome_and_spend():
    rows = [
        {"run_id": "suite-a-a01-20261028T020000Z", "calls": 11, "cost_usd": 0.02,
         "first_ts": 1.0, "last_ts": 2.0},
        {"run_id": "suite-a-judge-a01-20261028T020000Z", "calls": 2, "cost_usd": 0.03,
         "first_ts": 3.0, "last_ts": 4.0},
        {"run_id": "suite-a-judge2-a01-20261028T020000Z", "calls": 2, "cost_usd": 0.001,
         "first_ts": 3.0, "last_ts": 4.0},
        {"run_id": "suite-a-gone-20261028T000000Z", "calls": 1, "cost_usd": 0.01,
         "first_ts": 0.5, "last_ts": 0.6},
        {"run_id": "ed90-t17", "calls": 20, "cost_usd": 0.04, "first_ts": 0.1, "last_ts": 0.2},
    ]  # fmt: skip
    inv = {r["run_id"]: r for r in run_inventory(RECORDS, rows)}
    assert "ed90-t17" not in inv
    assert inv["suite-a-a01-20261028T030000Z"]["outcome"].startswith("failed")
    assert inv["suite-a-a01-20261028T020000Z"]["outcome"] == "published"
    assert inv["suite-a-a01-20261028T020000Z"]["cost_usd"] == 0.02
    assert inv["suite-a-judge-a01-20261028T020000Z"]["kind"] == "judge"
    assert inv["suite-a-judge2-a01-20261028T020000Z"]["kind"] == "second judge"
    assert inv["suite-a-gone-20261028T000000Z"]["outcome"] == "no run folder"
    ids = {"suite-a-a01-20261028T020000Z", "suite-a-judge-a01-20261028T020000Z"}
    assert spend_of(ids, rows) == pytest.approx(0.05)


# -- overlap, timing, silence ----------------------------------------------------------------------


def test_overlap_seconds_merges_and_intersects():
    assert overlap_seconds([(0, 2), (1, 3)], [(2.5, 10)]) == pytest.approx(0.5)
    assert overlap_seconds([(0, 1)], [(1, 2)]) == 0.0
    assert overlap_seconds([(0, 4)], [(1, 2), (3, 5)]) == pytest.approx(2.0)


def test_cue_span_uses_audio_duration():
    assert cue_span({"start": 3.0, "audio_dur": 1.5, "end": 9.0}) == (3.0, 4.5)
    assert cue_span({"start": 3.0, "audio_dur": None, "end": 4.0}) == (3.0, 4.0)


def test_published_leaves_held_cues_out_and_gates_extended():
    std, ext = published(cues_json())
    assert [c["id"] for c in std] == ["c001", "c002"]
    assert [c["id"] for c in ext] == ["x001"]
    assert published(cues_json(), extended_produced=False)[1] == []


def test_freeze_points_inside_speech_are_found():
    speech = [(2.0, 5.0), (33.0, 36.0)]
    assert freeze_points_in_speech([{"id": "x", "start": 34.0}], speech) == ["x"]
    assert freeze_points_in_speech([{"id": "x", "start": 33.0}], speech) == []
    assert freeze_points_in_speech([{"id": "x", "start": 9.0, "freeze_at": 4.0}], speech) == ["x"]


def test_capacity_violation_when_audio_leaves_the_window():
    gaps = [{"id": "g1", "usable_start": 1.0, "usable_end": 3.0}]
    assert capacity_violations([cue("a", 1.0, 2.0, gap="g1")], gaps) == []
    assert capacity_violations([cue("b", 1.5, 2.0, gap="g1")], gaps) == ["b"]
    assert capacity_violations([cue("c", 0.5, 1.0, gap="g1")], gaps) == ["c"]


def test_silence_counts_every_pause():
    s = silence(ENTRY)
    assert s == {"seconds": pytest.approx(54.0), "pauses": 3, "pauses_ge_2s": 3}
    short = {**ENTRY, "narration": [{"start": 1.0, "end": 5.0}, {"start": 6.5, "end": 59.0}]}
    assert silence(short) == {"seconds": pytest.approx(3.5), "pauses": 3, "pauses_ge_2s": 0}


# -- exact match, ties, decoys -------------------------------------------------------------------


def test_title_match_is_a_whole_phrase():
    assert title_match("Cell Energy", ['A slide reads: "Cell energy".'])
    assert not title_match("Cell Energy", ["A slide about cells and energy."])


def test_value_match_needs_value_and_label_in_one_cue():
    assert value_match(23.0, "H3", ["The line peaks at 23 for H3."])
    assert value_match(1200.0, "Equator", ["Equator receives 1,200 millimetres."])
    assert value_match(45.0, "Aerobic", ["Aerobic shows forty-five."])
    assert not value_match(23.0, "H3", ["The line peaks at 23.", "H3 is highest."])
    assert not value_match(23.0, "H3", ["H3 shows 2.3"])


def test_a_tied_highest_value_accepts_any_tied_category():
    assert chart_ties(ENTRY) == {"t01-v02": ["H3"]}
    out = exact_matches(ENTRY["visual_facts"], ["The line peaks at 23 for H3."], chart_ties(ENTRY))
    assert out["chart_value"] == {"matched": 1, "of": 1}
    prompt, _ = coverage_request(ENTRY, tagged(*published(cues_json())))
    assert 'also shown for "H3"' in prompt


def test_exact_matches_count_by_kind():
    out = exact_matches(ENTRY["visual_facts"], ["A slide reads: Cell Energy."], {})
    assert out == {"title": {"matched": 1, "of": 1}, "chart_value": {"matched": 0, "of": 1}}


def test_decoy_numbers_found_in_cues():
    assert decoy_number_hits(ENTRY["absent_decoys"], ["Lactate reaches 8.3."]) == ["t01-d01"]
    assert decoy_number_hits(ENTRY["absent_decoys"], ["Lactate reaches 83."]) == []


def test_absent_part_of_a_decoy():
    words, numbers = absent_part(ENTRY["absent_decoys"][0], ENTRY)
    assert words == {"lactate"} and numbers == [8.3]
    words, numbers = absent_part(ENTRY["absent_decoys"][1], ENTRY)
    assert words == {"photo", "microscope"} and numbers == []


# -- bootstrap and agreement -----------------------------------------------------------------------


def test_bootstrap_constant_ratio_has_a_zero_width_interval():
    r = bootstrap_ratio([(1, 2), (2, 4), (3, 6)], n=500)
    assert r["value"] == pytest.approx(0.5)
    assert r["ci95"] == [pytest.approx(0.5), pytest.approx(0.5)]


def test_bootstrap_is_pooled_seeded_and_brackets_the_value():
    pairs = [(0, 10), (10, 10), (5, 10), (2, 4)]
    a = bootstrap_ratio(pairs, n=2000)
    assert a == bootstrap_ratio(pairs, n=2000)
    assert a["value"] == pytest.approx(17 / 34)
    lo, hi = a["ci95"]
    assert 0.0 <= lo <= a["value"] <= hi <= 1.0


def test_bootstrap_empty_and_zero_denominator():
    assert bootstrap_ratio([])["value"] is None
    assert bootstrap_ratio([(0, 0)])["value"] is None


def test_agreement_and_kappa():
    same = agreement([True, False, True, False], [True, False, True, False])
    assert same == {"n": 4, "agree": 4, "agreement": 1.0, "kappa": 1.0}
    half = agreement([True, True, False, False], [True, False, True, False])
    assert half["agreement"] == 0.5 and half["kappa"] == pytest.approx(0.0)
    assert agreement([True, True], [True, True])["kappa"] is None  # undefined: no variation
    assert agreement([], [])["agreement"] is None


# -- metrics from a fake judge ---------------------------------------------------------------------


def test_video_metrics_from_fake_verdicts():
    m = video_metrics(ENTRY, run_record(), cues_json(), verdicts())
    assert m["overlap_s"] == 0.0
    assert m["recall"]["standard"] == [2, 3]
    assert m["recall"]["with_extended"] == [2, 3]
    assert m["recall"]["timely_with_extended"] == [2, 3]
    assert m["hallucination"]["claims"] == [1, 4]
    assert m["hallucination"]["by_track"]["extended"] == [1, 1]
    assert m["hallucination"]["cues"] == [1, 3]
    assert m["decoys"]["stated"] == ["t01-d02"]
    assert m["redundancy"]["cues"] == [1, 3]
    assert m["redundancy"]["spoken_facts_restated"] == [0, 1]
    assert m["judge_problems"] == []
    assert m["exact_match"]["standard"]["chart_value"]["matched"] == 1  # the tie
    sup = m["supplementary"]
    assert sup["hallucination_voiced_text"] == [1, 3]
    assert sup["decoys_absent_part"] == ["t01-d02"]
    assert sup["claims_not_in_cue_text"] == [0, 4]
    assert m["silence"]["pauses"] == 3


def test_extended_cues_are_not_scored_when_no_extended_version_was_produced():
    rec = run_record()
    rec["engine"]["extended_produced"] = False
    m = video_metrics(ENTRY, rec, cues_json(), verdicts())
    assert m["published"] == {"standard": 2, "extended": 0}
    assert m["hallucination"]["claims"] == [0, 3]


def test_overlap_with_narration_is_measured():
    cj = cues_json()
    cj["cues"][0]["start"] = 4.0  # 4.0-6.0 against narration 2.0-5.0
    m = video_metrics(ENTRY, run_record(), cj, verdicts())
    assert m["overlap_s"] == pytest.approx(1.0)


def test_missing_verdicts_count_against_the_system_and_are_reported():
    v = verdicts()
    v["claims"]["claims"] = v["claims"]["claims"][:-1]
    v["claims"]["cues"] = v["claims"]["cues"][:1]
    v["coverage"]["visual_facts"] = v["coverage"]["visual_facts"][1:]
    v["coverage"]["decoys"] = []
    v["coverage"]["spoken_facts"] = []
    m = video_metrics(ENTRY, run_record(), cues_json(), v)
    assert m["hallucination"]["claims"] == [1, 4]  # missing claim: unsupported
    assert m["recall"]["standard"] == [1, 3]  # missing fact: not conveyed
    assert m["decoys"]["stated"] == ["t01-d01", "t01-d02"]  # missing decoy: stated
    assert m["redundancy"]["cues"] == [2, 3]  # missing cue verdict: redundant
    assert m["redundancy"]["spoken_facts_restated"] == [1, 1]
    assert m["supplementary"]["hallucination_voiced_text"] == [2, 3]
    assert len(m["judge_problems"]) >= 6


def test_a_failed_run_publishes_nothing_and_misses_everything():
    rec = {"run_id": "r", "error": "BudgetExceeded: run", "engine": {}}
    verdicts_empty = judge_cues(ENTRY, [], judge=None, run_id="unused")
    m = video_metrics(ENTRY, rec, None, verdicts_empty)
    assert m["failed"] == "BudgetExceeded: run"
    assert m["recall"]["with_extended"] == [0, 3]
    assert m["hallucination"]["claims"] == [0, 0]
    assert m["decoys"]["stated"] == []


def test_aggregate_pools_videos():
    a = video_metrics(ENTRY, run_record(), cues_json(), verdicts())
    b = video_metrics({**ENTRY, "id": "t02"}, run_record(), cues_json(), verdicts())
    agg = aggregate([a, b])
    assert agg["recall_standard"]["value"] == pytest.approx(4 / 6)
    assert agg["hallucination_claims"]["value"] == pytest.approx(2 / 8)
    assert agg["hallucination_voiced_text"]["value"] == pytest.approx(2 / 6)
    assert agg["decoys_stated"] == 2 and agg["decoys_total"] == 4
    assert agg["decoys_absent_part"] == 2
    assert agg["cost"]["model_usd_per_min"] == pytest.approx(0.03)
    assert agg["processing"]["s_per_min"] == pytest.approx(90.0)
    assert agg["cue_counts"] == {"verified": 4, "held": 2, "extended": 2}
    assert agg["silence_s"] == pytest.approx(108.0) and agg["pauses_ge_2s"] == 6


def test_claim_in_text_ignores_framing_words():
    text = "Glycolysis and Krebs Cycle."
    assert claim_in_text("The slide is titled Glycolysis and Krebs Cycle.", text)
    assert not claim_in_text("The slide lists Glycolysis occurs in cytoplasm.", text)
    assert claim_in_text("A slide appears", text)


def test_supplementary_counts_claims_the_text_does_not_say():
    cj = cues_json()
    cj["cues"][0]["claims"].append({"text": "The slide lists Oxygen helps", "verdict": "x"})
    v = verdicts()
    v["claims"]["claims"].append({"key": "c001#2", "reason": "", "verdict": "supported"})
    m = video_metrics(ENTRY, run_record(), cj, v)
    assert m["supplementary"]["claims_not_in_cue_text"] == [1, 5]
    assert m["supplementary"]["unsupported_claims_in_cue_text"][1] == 4


# -- judge requests, caching, second judge ---------------------------------------------------------


def test_requests_list_every_item_and_force_complete_answers():
    cues = tagged(*published(cues_json()))
    prompt, schema = coverage_request(ENTRY, cues)
    assert "t01-v03 (slide 2, bullet)" in prompt and "x001 | extended" in prompt
    vf = schema["properties"]["visual_facts"]
    assert vf["minItems"] == vf["maxItems"] == 3
    assert vf["items"]["properties"]["cue_ids"]["items"]["enum"] == ["c001", "c002", "x001"]
    prompt, schema = claims_request(ENTRY, cues)
    assert "claim c002#2: it peaks at 23 for H3" in prompt
    assert schema["properties"]["claims"]["items"]["properties"]["key"]["enum"] == [
        "c001#1", "c002#1", "c002#2", "x001#1"]  # fmt: skip
    cue_item = schema["properties"]["cues"]["items"]
    assert set(cue_item["required"]) == {"id", "restates_narration", "text_unsupported"}
    assert 'supported", "at"' not in prompt  # the engine's own verdicts never reach the judge


class FakeTF:
    def __init__(self, answers):
        self.answers, self.calls, self.models = list(answers), 0, []

    def json(self, model, *a, **kw):
        self.calls += 1
        self.models.append((model, kw["thinking"], kw["temperature"]))
        return self.answers.pop(0)


class FakeLedger:
    def spent(self, run_id=None):
        return 0.0


def test_judge_answers_are_cached_by_request_and_remember_who_paid(tmp_path, monkeypatch):
    monkeypatch.setattr(score, "JUDGE_CACHE", tmp_path)
    v = verdicts()
    tf = FakeTF([v["coverage"], v["claims"]])
    judge = Judge(tf, Budget(FakeLedger(), cap=1.0))
    cues = tagged(*published(cues_json()))
    first = judge_cues(ENTRY, cues, judge, "run-1")
    again_judge = Judge(FakeTF([]), Budget(FakeLedger(), cap=1.0))
    again = judge_cues(ENTRY, cues, again_judge, "run-2")
    assert first == again == v and tf.calls == 2
    assert again_judge.paid_by == {"run-1"} and judge.paid_by == {"run-1"}
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_second_judge_uses_its_own_model_without_reasoning(tmp_path, monkeypatch):
    monkeypatch.setattr(score, "JUDGE_CACHE", tmp_path)
    v = verdicts()
    tf = FakeTF([v["coverage"], v["claims"]])
    judge = Judge(tf, Budget(FakeLedger(), cap=1.0), model=score.SECOND_JUDGE, thinking=False)
    judge_cues(ENTRY, tagged(*published(cues_json())), judge, "run-2nd")
    assert tf.models == [(score.SECOND_JUDGE, False, 0.0)] * 2


def test_judge_vectors_for_agreement():
    cues = tagged(*published(cues_json()))
    vec = judge_vectors(ENTRY, cues, verdicts())
    assert vec["facts"] == {"t01-v01": True, "t01-v02": True, "t01-v03": False}
    assert vec["claims"] == {"c001#1": True, "c002#1": True, "c002#2": True, "x001#1": False}


def test_budget_refuses_a_step_over_the_cap():
    class Spent:
        def spent(self, run_id=None):
            return 0.9

    b = Budget(Spent(), cap=1.0)
    b.add_run("r")
    with pytest.raises(SystemExit):
        b.check(0.2, "a judge call")


# -- the judge probe -------------------------------------------------------------------------------


def test_negative_probes_are_near_misses_of_their_target():
    neg = negative_probes(ENTRY)
    kinds = [n["probe"]["type"] for n in neg]
    assert kinds == ["wrong_value", "title_swap", "gist_only", "gist_only", "bullet_partial"]
    wrong = neg[0]
    assert wrong["probe"]["targets"] == ["t01-v02"] and "29.9" in wrong["text"]
    assert wrong["probe"]["claim_expected"] == "unsupported"
    assert neg[1]["text"] == 'The slide title reads "Cell Methods".'
    assert neg[2]["probe"]["targets"] == ["t01-v02"] and neg[3]["probe"]["targets"] == ["t01-v03"]
    assert neg[4]["text"] == 'A bullet mentions "Oxygen".'
    assert [n["id"] for n in neg] == ["pn01", "pn02", "pn03", "pn04", "pn05"]


def verdict(c):
    return "supported" if c["id"].startswith("pv") else "unsupported"


def test_probe_scores_sensitivity_specificity_and_cross_credits():
    cues = probe_cues(ENTRY)
    assert [c["id"] for c in cues][:5] == ["pv01", "pv02", "pv03", "pd01", "pd02"]
    assert [c["id"] for c in cues][5:] == ["pn01", "pn02", "pn03", "pn04", "pn05"]
    v = {
        "coverage": {
            "visual_facts": [
                {"id": "t01-v01", "reason": "", "cue_ids": ["pv01"]},
                {"id": "t01-v02", "reason": "", "cue_ids": ["pv02", "pn01"]},  # fooled
                {"id": "t01-v03", "reason": "", "cue_ids": ["pv01"]},  # cross credit, missed
            ],
            "spoken_facts": [],
            "decoys": [{"id": "t01-d01", "reason": "", "cue_ids": ["pd01"]},
                       {"id": "t01-d02", "reason": "", "cue_ids": ["pd02", "pv03"]}],
        },
        "claims": {"claims": [{"key": f"{c['id']}#1", "reason": "", "verdict": verdict(c)}
                              for c in cues], "cues": []},
    }  # fmt: skip
    s = probe_scores(ENTRY, cues, v)
    assert s["facts_credited_to_own_cue"] == [2, 3]
    assert s["decoys_credited_to_own_cue"] == [2, 2]
    assert s["fact_claims_supported"] == [3, 3]
    assert s["decoy_claims_unsupported"] == [2, 2]
    assert s["negatives_not_credited"] == [4, 5]
    assert s["negative_claims_unsupported"] == [2, 2]
    assert s["by_negative_type"]["wrong_value"] == [0, 1]
    assert s["cross_credits"] == {"fact_to_other_fact_cue": 1, "decoy_to_fact_cue": 1,
                                  "fact_to_decoy_cue": 0}  # fmt: skip


# -- the bundle ------------------------------------------------------------------------------------


def test_bundle_keeps_what_rebuilds_the_report_and_drops_media(tmp_path):
    out = tmp_path / "out"
    run = out / "runs" / "suite-a-a01-x"
    (run / "work").mkdir(parents=True)
    (run / "work" / "big.wav").write_bytes(b"0" * 10)
    (run / "described.mp4").write_bytes(b"0")
    (run / "cues.json").write_text("{}", encoding="utf-8")
    (run / "suite_a_run.json").write_text("{}", encoding="utf-8")
    judge = out / "judge" / "abc.json"
    judge.parent.mkdir(parents=True)
    judge.write_text("{}", encoding="utf-8")
    dst = tmp_path / "bundle.tar.gz"
    digest = write_bundle(dst, out, [run], [judge], {"runs/ledger_rows.json": {"rows": []}})
    names = set(tarfile.open(dst).getnames())
    assert "runs/suite-a-a01-x/cues.json" in names and "judge/abc.json" in names
    assert "runs/ledger_rows.json" in names
    assert not any(n.endswith((".mp4", ".wav")) or "/work" in n for n in names)
    assert len(digest) == 64


def test_markdown_names_the_tag_and_labels_supplementary_rows():
    m = video_metrics(ENTRY, run_record(), cues_json(), verdicts())
    result = {
        "complete": False, "key_sha256": "abc", "git_commit": "d" * 40, "git_dirty": False,
        "preregistration": {"tag": "suite-a-prereg", "commit": "c" * 40,
                            "committed": "2026-10-28T02:12:13+01:00"},
        "date": "2026-10-28", "engine_fingerprint": "fp", "judge_model": "U",
        "second_judge_model": "Q", "aggregate": aggregate([m]), "videos": [m],
        "judge_probe": None, "second_judge": None, "spend_usd": 0.1, "runs_inventory": [],
        "hardware": {"cpu": "cpu", "gpus": ["gpu"], "os": "os"},
    }  # fmt: skip
    md = score.markdown(result)
    assert "suite-a-prereg" in md and "PARTIAL" in md
    assert "supplementary" in md and "pauses >= 2 s" in md
