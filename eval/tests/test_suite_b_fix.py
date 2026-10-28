"""Suite B corrections: evidence-backed ASR corrections, long-block windows, unresolved terms,
the judge probe, results that need all four videos, and the overlap presentation."""

import json

import pytest

from suite_b import probe as P
from suite_b import score as B
from suite_b.refs import CorrectionError, apply_corrections, mark_blocks

FRAMES = [{"video": "w", "time_s": 83.5, "frame_sha256": "ab",
           "text": "DSAS v5 Toolbar\nTransect layer selection\nTransects"}]  # fmt: skip
NARRATION = "At the Woods Hole Coastal and Marine Science Center, we"
WORDS = [{"text": t, "start": i, "end": i + 0.5} for i, t in enumerate(NARRATION.split())]
DSAS = {"video": "w", "ref_line": "usgs-033", "wrong": "DSOS V5 toolbar",
        "right": "DSAS v5 Toolbar",
        "evidence": {"type": "frame", "time_s": 83.5, "text": "DSAS v5 Toolbar"}}  # fmt: skip


def spec(*corrections, unresolved=()):
    return {"frames": FRAMES, "corrections": list(corrections), "unresolved": list(unresolved)}


def cue(cid, text):
    return {"id": cid, "text": text}


def test_a_correction_with_frame_evidence_keeps_the_asr_text():
    c = dict(DSAS)
    out = apply_corrections("w", [cue("usgs-033", "Toolbar text reads DSOS V5 toolbar."),
                                  cue("usgs-034", "Other.")], spec(c), WORDS)  # fmt: skip
    assert out[0]["text"] == "Toolbar text reads DSAS v5 Toolbar."
    assert out[0]["asr_text"] == "Toolbar text reads DSOS V5 toolbar."
    assert out[0]["corrections"] == [{"wrong": "DSOS V5 toolbar", "right": "DSAS v5 Toolbar",
                                      "evidence": "frame 83.5 s"}]  # fmt: skip
    assert out[1] == cue("usgs-034", "Other.")  # untouched lines carry no correction fields


def test_a_correction_with_narration_evidence():
    c = {"video": "w", "ref_line": "usgs-002", "wrong": "Woodshole Coastal",
         "right": "Woods Hole Coastal",
         "evidence": {"type": "narration",
                      "text": "the Woods Hole Coastal and Marine"}}  # fmt: skip
    out = apply_corrections("w", [cue("usgs-002", "Video shows the Woodshole Coastal center.")],
                            spec(c), WORDS)  # fmt: skip
    assert out[0]["text"] == "Video shows the Woods Hole Coastal center."
    assert out[0]["corrections"][0]["evidence"] == "original narration"


@pytest.mark.parametrize(
    "change, words",
    [
        ({"right": "DSAS v6 Toolbar"}, "does not say"),  # evidence lacks the right term
        (
            {"evidence": {"type": "frame", "time_s": 83.5, "text": "DSAS v6 Toolbar"}},
            "not in the transcription",
        ),  # excerpt not in the frame's recorded text
        ({"evidence": {"type": "frame", "time_s": 12.0, "text": "DSAS v5 Toolbar"}}, "no frame"),
        ({"wrong": "DSOS V9 toolbar"}, "not in usgs-033"),
        ({"ref_line": "usgs-999"}, "unknown line"),
        ({"evidence": {"type": "narration", "text": "Woods Hole Science"}}, "not in the original"),
        ({"evidence": {"type": "description", "text": "DSAS v5 Toolbar"}}, "evidence type"),
    ],
)
def test_corrections_without_support_are_refused(change, words):
    c = {**DSAS, **change}
    with pytest.raises(CorrectionError, match=words):
        apply_corrections("w", [cue("usgs-033", "Toolbar text reads DSOS V5 toolbar.")], spec(c),
                          WORDS)  # fmt: skip


def test_blocks_group_the_lines_of_one_gap_and_a_cue_is_its_own_block():
    usgs = [{"id": "a", "start": 10.0, "end": 20.0, "anchor_range": [5.0, 5.0]},
            {"id": "b", "start": 21.0, "end": 45.0, "anchor_range": [5.0, 5.0]},
            {"id": "c", "start": 50.0, "end": 52.0, "anchor_range": [9.0, 9.0]}]  # fmt: skip
    mark_blocks(usgs, by_gap=True)
    assert usgs[0]["block"] == usgs[1]["block"] != usgs[2]["block"]
    assert usgs[0]["block_s"] == usgs[1]["block_s"] == 35.0 and usgs[2]["block_s"] == 2.0
    vtt = [{"id": "p", "start": 590.5, "end": 624.0}, {"id": "q", "start": 625.0, "end": 630.0}]
    mark_blocks(vtt, by_gap=False)
    assert (
        vtt[0]["block_s"] == 33.5
        and vtt[1]["block_s"] == 5.0
        and vtt[0]["block"] != vtt[1]["block"]
    )


RULES = {"window_s": 15.0, "long_block_s": 30.0, "long_block_window_s": 30.0}


def test_a_long_block_widens_the_window():
    f = {"anchor_time": 86.56, "anchor_end": 86.56, "placement": "timed", "block_s": 33.6}
    assert B.window(f, RULES, 173.3) == pytest.approx((56.56, 116.56))
    assert B.window({**f, "block_s": 30.0}, RULES, 173.3) == pytest.approx((71.56, 101.56))
    assert B.window({**f, "block_s": None}, RULES, 173.3) == pytest.approx((71.56, 101.56))


def test_facts_with_unresolved_terms_are_flagged():
    unresolved = [{"video": "w", "ref_line": "usgs-009", "term": "clover"},
                  {"video": "x", "ref_line": "usgs-010", "term": "clover"}]  # fmt: skip
    facts = [{"id": "usgs-009.1", "ref_cue_id": "usgs-009", "text": "Piping Clover chicks nest."},
             {"id": "usgs-009.2", "ref_cue_id": "usgs-009", "text": "The nest is on a beach."},
             {"id": "usgs-010.1", "ref_cue_id": "usgs-010", "text": "A clover field."}]  # fmt: skip
    assert B.flagged(facts, "w", unresolved) == {"usgs-009.1"}


def test_recall_without_flagged_facts():
    def row(i, line, ok, flag):
        return {"id": i, "ref_cue_id": line, "kind": "visual", "video": "w", "flagged": flag,
                **dict.fromkeys(B.VARIANTS, ok)}  # fmt: skip

    rows = [
        row("a.1", "a", True, False),
        row("a.2", "a", False, True),
        row("b.1", "b", False, False),
    ]
    assert B.recall(rows, ("visual",))["both_window"]["value"] == pytest.approx(1 / 3)
    clean = B.recall([r for r in rows if not r["flagged"]], ("visual",))
    assert clean["both_window"]["value"] == 0.5 and clean["facts"] == 2


def test_results_need_all_four_videos_each_whole():
    full = {v: {"video": v, "full": True} for v in B.VIDEOS}
    assert B.counts_as_results(list(full.values()), smoke=False)
    assert not B.counts_as_results(list(full.values()), smoke=True)
    assert not B.counts_as_results([full["elephants_dream"]], smoke=False)
    clip = {**full, "csc_intro": {"video": "csc_intro", "full": False}}
    assert not B.counts_as_results(list(clip.values()), smoke=False)


# -- the probe -------------------------------------------------------------------------------------


def test_probe_scores():
    group = {"facts": ["f1", "f2"],
             "cues": [{"id": "c1", "for": "f1", "type": "positive_paraphrase", "expect": "credit"},
                      {"id": "c2", "for": "f1", "type": "wrong_count", "expect": "no_credit"},
                      {"id": "c3", "for": "f2", "type": "positive_extra", "expect": "credit"},
                      {"id": "c4", "for": "f2", "type": "swapped_character",
                       "expect": "no_credit"}]}  # fmt: skip
    verdicts = {"f1": {"cue_ids": ["c1", "c2"]}, "f2": {"cue_ids": ["c1"]}}
    s = P.probe_scores(group, verdicts)
    assert s["sensitivity"] == [1, 2] and s["specificity"] == [1, 2]
    assert s["by_type"]["wrong_count"] == [0, 1] and s["by_type"]["swapped_character"] == [1, 1]
    assert s["by_type"]["positive_extra"] == [0, 1]
    assert s["cross_credits"] == [["c1", "f2"]]
    assert s["missed"] == ["c3"] and s["false_credits"] == ["c2"]


def test_the_probe_set_is_well_formed():
    probes = json.loads(P.PROBES.read_text(encoding="utf-8"))
    rules, refs = B.load_reference_set()
    types = set()
    for g in probes["groups"]:
        facts = {f["id"]: f for f in refs[g["video"]]["facts"]}
        assert all(fid in facts for fid in g["facts"]), g["video"]
        ids = [c["id"] for c in g["cues"]]
        assert len(ids) == len(set(ids))
        for c in g["cues"]:
            assert c["for"] in g["facts"] and c["expect"] in ("credit", "no_credit")
            assert (c["expect"] == "credit") == c["type"].startswith("positive")
            types.add(c["type"])
    assert types >= {"positive_paraphrase", "positive_extra", "wrong_count", "wrong_colour",
                     "wrong_value", "swapped_character", "gist_only",
                     "invented_detail"}  # fmt: skip


def test_probe_requests_use_the_pinned_prompt(tmp_path):
    probes = json.loads(P.PROBES.read_text(encoding="utf-8"))
    rules, refs = B.load_reference_set()
    g = probes["groups"][0]
    facts, cues = P.group_inputs(g, refs)
    prompt, schema, back = B.judge_request(rules, g["video"], refs[g["video"]]["doc"], facts, cues)
    assert "probe" not in prompt.lower() and "positive" not in prompt
    assert set(back.values()) == {c["id"] for c in g["cues"]}


# -- the report ------------------------------------------------------------------------------------


def test_the_overlap_section_leads_with_the_independent_transcript():
    row = {"description_s": 10.0, "overlap_s": 0.0, "share": 0.0, "intervals": 1}
    video = {"video": "elephants_dream", "judge_problems": [],
             "overlap": {"speech_source": "cues.json speech", "speech_s": 5.0,
                         "lull_standard": row, "pfeiffer_vtt": row, "hunt_vtt": row,
                         "hunt_wav": row, "lull_extended_freeze_points_in_speech": [],
                         "supplementary_against_reference_asr": {
                             "speech_s": 4.0, "lull_standard": row, "pfeiffer_vtt": row,
                             "hunt_vtt": row, "hunt_wav": row,
                             "lull_extended_freeze_points_in_speech": []}},
             "density": {"minutes": 1.0}}  # fmt: skip
    lines = B.overlap_section([video])
    text = "\n".join(lines)
    assert text.index("reference transcript") < text.index("own speech detection")
    own = [x for x in lines if "| lull_standard" in x and "own" in x.lower()]
    assert own and all("0 by construction against its own speech detection" in x for x in own)
    assert all("display time, not voiced" in x for x in lines if "| pfeiffer_vtt" in x)


# -- re-extraction of corrected lines only ---------------------------------------------------------


def test_corrected_lines_are_regrouped_for_reextraction():
    from suite_b import facts as F

    cues = [{"id": "a"}, {"id": "b", "corrections": [1]}, {"id": "c", "corrections": [1]},
            {"id": "d"}, {"id": "e", "corrections": [1]}]  # fmt: skip
    assert F.corrected_groups(cues) == [["b", "c"], ["e"]]
    view = F.base_view([{"id": "a", "text": "new", "asr_text": "old"}, {"id": "b", "text": "x"}])
    assert view == [{"id": "a", "text": "old", "asr_text": "old"}, {"id": "b", "text": "x"}]


def test_replaced_facts_keep_the_line_order():
    from suite_b import facts as F

    base = [{"id": "a.1", "ref_cue_id": "a"}, {"id": "b.1", "ref_cue_id": "b"},
            {"id": "b.2", "ref_cue_id": "b"}, {"id": "c.1", "ref_cue_id": "c"}]  # fmt: skip
    new = [{"id": "b.1", "ref_cue_id": "b", "text": "n"}]
    out = F.replace_facts(base, new, {"b"})
    assert [f["id"] for f in out] == ["a.1", "b.1", "c.1"] and out[1]["text"] == "n"


def test_the_probe_is_not_counted_as_scoring():
    rows = [{"run_id": "suite-b-probe-w-1", "first_ts": 10.0, "cost_usd": 0.01},
            {"run_id": "suite-b-judge-w-2", "first_ts": 30.0, "cost_usd": 0.02}]  # fmt: skip
    refd = {"suite-b-probe-w-1", "suite-b-judge-w-2"}
    out = B.spend_and_timing(rows, refd, prereg_ts=20.0)
    assert out["ledger_calls_after_preregistration"] is True
    assert out["spend_usd"] == 0.02 and out["probe_spend_usd"] == 0.01
    late = B.spend_and_timing(rows, refd, prereg_ts=40.0)
    assert late["ledger_calls_after_preregistration"] is False
