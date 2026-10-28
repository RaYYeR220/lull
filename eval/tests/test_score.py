import json

import pytest

from suite_a import score
from suite_a.score import (
    Budget,
    Judge,
    aggregate,
    bootstrap_ratio,
    capacity_violations,
    claims_request,
    coverage_request,
    cue_span,
    decoy_number_hits,
    exact_matches,
    freeze_points_in_speech,
    judge_cues,
    overlap_seconds,
    probe_cues,
    probe_scores,
    published,
    tagged,
    title_match,
    value_match,
    video_metrics,
)

# -- fakes ----------------------------------------------------------------------------------------

ENTRY = {
    "id": "t01",
    "subject": "testing",
    "duration": 60.0,
    "slides": [
        {"index": 1, "start": 0.0, "end": 30.0, "inventory": "Slide 1 ..."},
        {"index": 2, "start": 30.0, "end": 60.0, "inventory": "Slide 2 ..."},
    ],
    "narration": [
        {"slide": 1, "index": 1, "text": "Cells need energy.", "start": 2.0, "end": 5.0},
        {"slide": 2, "index": 1, "text": "Enzymes speed reactions.", "start": 33.0, "end": 36.0},
    ],
    "visual_facts": [
        {"id": "t01-v01", "slide": 1, "kind": "title", "text": 'The slide title reads "Cell '
         'Energy".', "anchor": "Cell Energy", "value": None, "slide_start": 0.0,
         "slide_end": 30.0, "exact": {"type": "title", "text": "Cell Energy"}},
        {"id": "t01-v02", "slide": 2, "kind": "chart_value", "text": "The highest value is 23, "
         'for "H3".', "anchor": "H3", "value": 23.0, "slide_start": 30.0, "slide_end": 60.0,
         "exact": {"type": "number", "value": 23.0, "label": "H3"}},
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
                {"id": "c001", "restates_narration": False},
                {"id": "c002", "restates_narration": False},
                {"id": "x001", "restates_narration": True},
            ],
        },
    }


# -- overlap and timing ----------------------------------------------------------------------------


def test_overlap_seconds_merges_and_intersects():
    assert overlap_seconds([(0, 2), (1, 3)], [(2.5, 10)]) == pytest.approx(0.5)
    assert overlap_seconds([(0, 1)], [(1, 2)]) == 0.0
    assert overlap_seconds([(0, 4)], [(1, 2), (3, 5)]) == pytest.approx(2.0)


def test_cue_span_uses_audio_duration():
    assert cue_span({"start": 3.0, "audio_dur": 1.5, "end": 9.0}) == (3.0, 4.5)
    assert cue_span({"start": 3.0, "audio_dur": None, "end": 4.0}) == (3.0, 4.0)


def test_published_leaves_held_cues_out():
    std, ext = published(cues_json())
    assert [c["id"] for c in std] == ["c001", "c002"]
    assert [c["id"] for c in ext] == ["x001"]


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


# -- exact match -----------------------------------------------------------------------------------


def test_title_match_is_a_whole_phrase():
    assert title_match("Cell Energy", ['A slide reads: "Cell energy".'])
    assert not title_match("Cell Energy", ["A slide about cells and energy."])


def test_value_match_needs_value_and_label_in_one_cue():
    assert value_match(23.0, "H3", ["The line peaks at 23 for H3."])
    assert value_match(1200.0, "Equator", ["Equator receives 1,200 millimetres."])
    assert value_match(45.0, "Aerobic", ["Aerobic shows forty-five."])
    assert not value_match(23.0, "H3", ["The line peaks at 23.", "H3 is highest."])
    assert not value_match(23.0, "H3", ["H3 shows 2.3"])


def test_exact_matches_count_by_kind():
    out = exact_matches(ENTRY["visual_facts"], ["A slide reads: Cell Energy."])
    assert out == {"title": {"matched": 1, "of": 1}, "chart_value": {"matched": 0, "of": 1}}


def test_decoy_numbers_found_in_cues():
    assert decoy_number_hits(ENTRY["absent_decoys"], ["Lactate reaches 8.3."]) == ["t01-d01"]
    assert decoy_number_hits(ENTRY["absent_decoys"], ["Lactate reaches 83."]) == []


# -- bootstrap -------------------------------------------------------------------------------------


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


# -- metrics from a fake judge -------------------------------------------------------------------


def run_record():
    return {"run_id": "suite-a-t01-x", "error": None,
            "engine": {"cost_model_usd": 0.03, "cost_tavily_usd": 0.0, "processing_s": 90.0,
                       "cue_counts": {"verified": 2, "held": 1, "extended": 1}}}  # fmt: skip


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
    assert m["exact_match"]["standard"]["chart_value"]["matched"] == 1


def test_overlap_with_narration_is_measured():
    cj = cues_json()
    cj["cues"][0]["start"] = 4.0  # 4.0-6.0 against narration 2.0-5.0
    m = video_metrics(ENTRY, run_record(), cj, verdicts())
    assert m["overlap_s"] == pytest.approx(1.0)


def test_missing_judge_rows_are_reported_and_counted_against():
    v = verdicts()
    v["claims"]["claims"] = v["claims"]["claims"][:-1]
    v["coverage"]["visual_facts"] = v["coverage"]["visual_facts"][1:]
    m = video_metrics(ENTRY, run_record(), cues_json(), v)
    assert m["hallucination"]["claims"] == [1, 4]
    assert m["recall"]["standard"] == [1, 3]
    assert any("not judged" in p for p in m["judge_problems"])


def test_a_failed_run_publishes_nothing_and_misses_everything():
    rec = {"run_id": "r", "error": "BudgetExceeded: run", "engine": {}}
    verdicts_empty = judge_cues(ENTRY, [], judge=None, run_id="unused")
    m = video_metrics(ENTRY, rec, None, verdicts_empty)
    assert m["failed"] == "BudgetExceeded: run"
    assert m["recall"]["with_extended"] == [0, 3]
    assert m["hallucination"]["claims"] == [0, 0]


def test_aggregate_pools_videos():
    a = video_metrics(ENTRY, run_record(), cues_json(), verdicts())
    b = video_metrics({**ENTRY, "id": "t02"}, run_record(), cues_json(), verdicts())
    agg = aggregate([a, b])
    assert agg["recall_standard"]["value"] == pytest.approx(4 / 6)
    assert agg["hallucination_claims"]["value"] == pytest.approx(2 / 8)
    assert agg["decoys_stated"] == 2 and agg["decoys_total"] == 4
    assert agg["cost"]["model_usd_per_min"] == pytest.approx(0.03)
    assert agg["processing"]["s_per_min"] == pytest.approx(90.0)
    assert agg["cue_counts"] == {"verified": 4, "held": 2, "extended": 2}


# -- judge requests and caching --------------------------------------------------------------------


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
    assert 'supported", "at"' not in prompt  # the engine's own verdicts never reach the judge


class FakeTF:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def json(self, *a, **kw):
        self.calls += 1
        return self.answers.pop(0)


class FakeLedger:
    def spent(self, run_id=None):
        return 0.0


def test_judge_answers_are_cached_by_request(tmp_path, monkeypatch):
    monkeypatch.setattr(score, "JUDGE_CACHE", tmp_path)
    v = verdicts()
    tf = FakeTF([v["coverage"], v["claims"]])
    judge = Judge(tf, Budget(FakeLedger(), cap=1.0))
    cues = tagged(*published(cues_json()))
    first = judge_cues(ENTRY, cues, judge, "run")
    again = judge_cues(ENTRY, cues, Judge(FakeTF([]), Budget(FakeLedger(), cap=1.0)), "run")
    assert first == again == v and tf.calls == 2
    assert len(list(tmp_path.glob("*.json"))) == 2
    saved = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert saved["run_id"] == "run"


def test_budget_refuses_a_step_over_the_cap():
    class Spent:
        def spent(self, run_id=None):
            return 0.9

    b = Budget(Spent(), cap=1.0)
    b.add_run("r")
    with pytest.raises(SystemExit):
        b.check(0.2, "a judge call")


def verdict(c):
    return "supported" if c["id"].startswith("pv") else "unsupported"


def test_probe_scores_count_own_cue_credit():
    cues = probe_cues(ENTRY)
    assert [c["id"] for c in cues] == ["pv01", "pv02", "pv03", "pd01", "pd02"]
    v = {
        "coverage": {
            "visual_facts": [{"id": f"t01-v0{i}", "reason": "", "cue_ids": [f"pv0{i}"]}
                             for i in (1, 2)] + [{"id": "t01-v03", "reason": "", "cue_ids": []}],
            "spoken_facts": [],
            "decoys": [{"id": "t01-d01", "reason": "", "cue_ids": ["pd01"]},
                       {"id": "t01-d02", "reason": "", "cue_ids": ["pd02"]}],
        },
        "claims": {"claims": [{"key": f"{c['id']}#1", "reason": "", "verdict": verdict(c)}
                              for c in cues], "cues": []},
    }  # fmt: skip
    s = probe_scores(ENTRY, cues, v)
    assert s["facts_credited_to_own_cue"] == [2, 3]
    assert s["decoys_credited_to_own_cue"] == [2, 2]
    assert s["fact_claims_supported"] == [3, 3]
    assert s["decoy_claims_unsupported"] == [2, 2]
