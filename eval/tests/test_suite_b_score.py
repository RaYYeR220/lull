"""Suite B scorer: the pin, windows, overlap, the judge request and the per-fact outcomes,
end to end on a synthetic run with fake judges. No network."""

import json

import pytest

from suite_a.score import Budget, Judge, Refused, verify_prereg
from suite_b import score as B
from suite_b.facts import RULES

# -- the pin ---------------------------------------------------------------------------------------


class FakeGit:
    def __init__(self, **overrides):
        self.table = {
            "rev-parse": (0, "5d628cbaaaa\n"),
            "merge-base": (0, ""),
            "show-sha": (0, "abc  suite_b reference set\nxx  refs/a.json\n"),
            "diff": (0, ""),
            "show-date": (0, "2026-10-28T04:07:27+01:00\n"),
            "status": (0, ""),
        }
        self.table.update(overrides)
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "show":
            return self.table["show-sha" if ":" in args[1] else "show-date"]
        return self.table[args[0]]


def check(git):
    return verify_prereg("abc", git, tag=B.PREREG_TAG, sha_file=B.SHA_FILE, pinned=B.PINNED,
                         what="the reference set")  # fmt: skip


def test_pin_checks_the_suite_b_tag_and_its_files():
    git = FakeGit()
    assert check(git)["tag"] == "suite-b-prereg-2"
    assert ("show", "suite-b-prereg-2:eval/suite_b/facts.sha256") in git.calls
    diff = next(c for c in git.calls if c[0] == "diff")
    assert "eval/suite_b/facts" in diff and "eval/suite_b/rules.json" in diff


@pytest.mark.parametrize(
    "override, words",
    [
        ({"rev-parse": (1, "")}, "suite-b-prereg-2 not found"),
        ({"merge-base": (1, "")}, "not an ancestor"),
        ({"show-sha": (0, "fff  suite_b reference set\n")}, "tagged facts.sha256"),
        ({"diff": (0, "eval/suite_b/rules.json\n")}, "rules.json"),
    ],
)
def test_pin_refusals(override, words):
    with pytest.raises(Refused, match=words):
        check(FakeGit(**override))


def test_the_committed_reference_set_passes_its_own_check():
    digest = B.check_reference_set()
    rules, refs = B.load_reference_set()
    assert rules == json.loads(json.dumps(RULES)) and len(digest) == 64
    assert set(refs) == {"elephants_dream", "woodshole", "aquatic_connectivity", "csc_intro"}


# -- windows and scope ----------------------------------------------------------------------------


W = {"window_s": 15.0, "long_block_s": 30.0, "long_block_window_s": 30.0}


def fact(fid, t, end=None, kind="visual", placement="timed", src="pfeiffer"):
    return {"id": fid, "ref_cue_id": fid.split(".")[0], "source": src, "kind": kind,
            "text": f"fact {fid}", "anchor_time": t, "anchor_end": t if end is None else end,
            "placement": placement}  # fmt: skip


def test_window_spans_the_line_and_a_preface_spans_the_video():
    assert B.window(fact("p-01.1", 30.0, 40.0), W, 600) == (15.0, 55.0)
    assert B.window(fact("u-001.1", 12.0), W, 600) == (-3.0, 27.0)
    assert B.window(fact("u-002.1", 0.0, placement="before_program"), W, 44.9) == (0.0, 44.9)


def test_plays_in_for_intervals_and_freeze_points():
    std = {"start": 50.0, "end": 54.0, "track": "standard"}
    assert B.plays_in(std, 54.0, 70.0) and B.plays_in(std, 30.0, 50.0)
    assert not B.plays_in(std, 54.1, 70.0)
    ext = {"start": 20.0, "end": 20.0, "track": "extended"}
    assert B.plays_in(ext, 5.0, 20.0) and not B.plays_in(ext, 20.5, 40.0)


def test_scope_of_a_clip_and_of_a_preface():
    clip = [60.0, 150.0]
    assert B.in_scope(fact("a.1", 60.0), clip, full=False)
    assert B.in_scope(fact("a.1", 150.0), clip, full=False)
    assert not B.in_scope(fact("a.1", 59.9), clip, full=False)
    pre = fact("u.1", 0.0, placement="before_program")
    assert not B.in_scope(pre, [0.0, 44.0], full=False) and B.in_scope(pre, [0.0, 44.0], True)


# -- overlap and density --------------------------------------------------------------------------


def test_overlap_is_clipped_and_shared_by_all_sides():
    speech = [(0.0, 5.0), (10.0, 12.0)]
    row = B.overlap_row([(4.0, 6.0), (11.0, 13.0), (20.0, 30.0)], speech, (0.0, 25.0))
    assert row == {"description_s": 9.0, "overlap_s": 2.0, "share": round(2 / 9, 4),
                   "intervals": 3}  # fmt: skip
    assert B.overlap_row([], speech, (0.0, 25.0))["share"] is None
    assert B.clip_spans([(55.0, 65.0), (140.0, 160.0), (10.0, 20.0)], (60.0, 150.0)) == [
        (60.0, 65.0), (140.0, 150.0)]  # fmt: skip


def test_freeze_points_inside_speech_and_density():
    cues = [{"id": "x1", "track": "extended", "start": 3.0, "end": 3.0},
            {"id": "x2", "track": "extended", "start": 7.0, "end": 7.0},
            {"id": "c1", "track": "standard", "start": 2.0, "end": 4.0}]  # fmt: skip
    assert B.freeze_in_speech(cues, [(0.0, 5.0)]) == ["x1"]
    assert B.density(["Two men run.", "A door opens!"], 0.5) == {"words": 6, "per_min": 12.0}


# -- the judge request ----------------------------------------------------------------------------

DOC = {"title": "A Film", "about": "a short test film", "kind": "vtt",
       "media": {"duration_s": 200.0}}  # fmt: skip


def test_judge_request_is_blind_to_ids_tracks_and_times():
    cues = [{"id": "x002", "track": "extended", "text": "A door opens.", "start": 70.0,
             "end": 70.0},
            {"id": "c001", "track": "standard", "text": "Two men\nrun.", "start": 61.0,
             "end": 64.0}]  # fmt: skip
    facts = [fact("pfeiffer-08.1", 69.0), fact("pfeiffer-08.2", 69.0, kind="on_screen_text")]
    prompt, schema, back = B.judge_request(RULES, "elephants_dream", DOC, facts, cues)
    assert back == {"k001": "c001", "k002": "x002"}  # neutral ids in time order
    assert "k001 | Two men run.\nk002 | A door opens." in prompt
    for leak in ("c001", "x002", "standard", "extended", "61.0", "69.0"):
        assert leak not in prompt
    assert "pfeiffer-08.2 (on_screen_text): fact pfeiffer-08.2" in prompt
    assert RULES["characters"]["elephants_dream"] in prompt
    items = schema["properties"]["facts"]
    assert items["minItems"] == items["maxItems"] == 2
    assert items["items"]["properties"]["cue_ids"]["items"]["enum"] == ["k001", "k002"]
    assert items["items"]["properties"]["id"]["enum"] == ["pfeiffer-08.1", "pfeiffer-08.2"]


class FakeTF:
    """Answers each call with fn(prompt, schema)."""

    def __init__(self, fn):
        self.fn, self.calls = fn, []

    def json(self, model, messages, schema, **kw):
        self.calls.append((model, kw["thinking"], kw["temperature"], kw["max_tokens"]))
        return self.fn(messages[0]["content"], schema)


class FakeLedger:
    def spent(self, run_id=None, since=None):
        return 0.0


def credit_all_but_last(prompt, schema):
    ids = schema["properties"]["facts"]["items"]["properties"]["id"]["enum"]
    cues = schema["properties"]["facts"]["items"]["properties"]["cue_ids"]["items"]["enum"]
    return {"facts": [{"id": i, "reason": "r", "cue_ids": cues} for i in ids[:-1]]}


def test_judge_facts_maps_ids_back_chunks_and_lists_missing(tmp_path):
    tf = FakeTF(credit_all_but_last)
    judge = Judge(tf, Budget(FakeLedger(), cap=1.0), cache=tmp_path)
    rules = {**RULES, "judge": {**RULES["judge"], "facts_per_call": 2}}
    facts = [fact(f"p-0{i}.1", 60.0 + i) for i in range(1, 4)]
    cues = [{"id": "c001", "track": "standard", "text": "x", "start": 61.0, "end": 62.0}]
    out, problems = B.judge_facts(judge, rules, "elephants_dream", DOC, facts, cues, "r1", "s")
    assert len(tf.calls) == 2  # 3 facts, 2 per call
    assert tf.calls[0] == ("nvidia/Nemotron-3-Ultra-550b-a55b", True, 0.0, 16000)
    assert out["p-01.1"]["cue_ids"] == ["c001"] and out["p-02.1"]["cue_ids"] == []
    assert out["p-03.1"]["cue_ids"] == []
    assert problems == [
        "p-02.1: no verdict from nvidia/Nemotron-3-Ultra-550b-a55b (not conveyed)",
        "p-03.1: no verdict from nvidia/Nemotron-3-Ultra-550b-a55b (not conveyed)",
    ]
    again = Judge(FakeTF(lambda p, s: pytest.fail("cached")), Budget(FakeLedger(), 1.0),
                  cache=tmp_path)  # fmt: skip
    assert B.judge_facts(again, rules, "elephants_dream", DOC, facts, cues, "r2", "s")[0] == out
    assert again.paid_by == {"r1"}


def test_outcomes_apply_the_window_per_track():
    cues = [{"id": "c1", "track": "standard", "text": "", "start": 10.0, "end": 12.0},
            {"id": "x1", "track": "extended", "text": "", "start": 100.0,
             "end": 100.0}]  # fmt: skip
    facts = [fact("p-01.1", 20.0), fact("p-02.1", 95.0), fact("p-03.1", 300.0)]
    verdicts = {"p-01.1": {"cue_ids": ["c1", "x1"]}, "p-02.1": {"cue_ids": ["x1"]},
                "p-03.1": {"cue_ids": ["c1"]}}  # fmt: skip
    rows = {r["id"]: r for r in B.outcomes(facts, verdicts, cues, W, 400.0)}
    assert rows["p-01.1"]["standard_window"] and rows["p-01.1"]["both_window"]
    assert not rows["p-02.1"]["standard_window"] and rows["p-02.1"]["both_window"]
    assert not rows["p-02.1"]["standard_anywhere"] and rows["p-02.1"]["both_anywhere"]
    assert not rows["p-03.1"]["both_window"] and rows["p-03.1"]["standard_anywhere"]


def test_recall_pools_over_lines_and_kinds():
    rows = [{"id": "a.1", "ref_cue_id": "a", "kind": "visual", "video": "v", **dict.fromkeys(
             B.VARIANTS, True)},
            {"id": "a.2", "ref_cue_id": "a", "kind": "visual", "video": "v", **dict.fromkeys(
             B.VARIANTS, False)},
            {"id": "b.1", "ref_cue_id": "b", "kind": "sound", "video": "v", **dict.fromkeys(
             B.VARIANTS, True)}]  # fmt: skip
    r = B.recall(rows, ("visual", "on_screen_text"))
    assert r["facts"] == 2 and r["lines"] == 1 and r["both_window"]["value"] == 0.5
    assert B.recall(rows, ("interpretation",))["both_window"]["value"] is None


# -- a synthetic run end to end -------------------------------------------------------------------


def write_run(folder, offset_cues=0.0):
    cues = {"run_id": "t-run", "cues": [
        {"id": "c001", "gap_id": "g1", "start": 2.0, "end": 5.0, "audio_dur": 3.0,
         "status": "verified", "text": "Two men run along a path."},
        {"id": "c002", "gap_id": "g2", "start": 20.0, "end": None, "audio_dur": None,
         "status": "held", "text": "Held cue."}],
        "extended": [{"id": "x001", "gap_id": None, "start": 9.0, "freeze_at": 9.5,
                      "audio_dur": 2.0, "status": "extended", "text": "Birds take off."}],
        "speech": [{"start": 4.0, "end": 6.0}, {"start": 9.0, "end": 10.0}]}  # fmt: skip
    manifest = {"run_id": "t-run", "video": {"name": "clip.mp4", "sha256": "ab", "duration": 30.0},
                "summary": {"cues": {"verified": 1, "held": 1, "extended": 1},
                            "extended_produced": True},
                "versions": {"engine": "0.2.0"}}  # fmt: skip
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "cues.json").write_text(json.dumps(cues), encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def test_load_run_maps_a_clip_onto_the_video(tmp_path):
    run = B.load_run("elephants_dream", write_run(tmp_path / "r"), 60.0, 658.094)
    assert run["clip"] == [60.0, 90.0] and not run["full"]
    assert [(c["id"], c["track"], c["start"], c["end"]) for c in run["cues"]] == [
        ("c001", "standard", 62.0, 65.0),
        ("x001", "extended", 69.5, 69.5),
    ]  # held cue left out
    assert run["speech"] == [(64.0, 66.0), (69.0, 70.0)]
    full = B.load_run("csc_intro", write_run(tmp_path / "f"), 0.0, 30.4)
    assert full["full"]
    with pytest.raises(Refused, match="no cues.json"):
        B.load_run("csc_intro", tmp_path / "missing", 0.0, 30.0)


def test_score_video_end_to_end_with_fake_judges(tmp_path):
    run = B.load_run("elephants_dream", write_run(tmp_path / "r"), 60.0, 658.094)
    rules, refs = B.load_reference_set()

    def credit_first_cue(prompt, schema):
        ids = schema["properties"]["facts"]["items"]["properties"]["id"]["enum"]
        return {"facts": [{"id": i, "reason": "r", "cue_ids": ["k001"]} for i in ids]}

    tf = FakeTF(credit_first_cue)
    judges = {"primary": Judge(tf, Budget(FakeLedger(), 1.0), cache=tmp_path / "j")}
    v = B.score_video(run, refs["elephants_dream"], rules, judges, "T", baseline=True)
    scope = [f for f in refs["elephants_dream"]["facts"]
             if 60.0 <= f["anchor_time"] <= 90.0 and f["kind"] in B.JUDGED_KINDS]  # fmt: skip
    rows = [r for e in v["references"].values() for r in e["primary"]["rows"]]
    assert {r["id"] for r in rows} == {f["id"] for f in scope}
    # k001 is the standard cue at 62-65 s: credited facts count when their window reaches it
    for r in rows:
        assert r["credited"] == ["c001"]
        assert r["standard_window"] == (r["window"][0] <= 65.0 and r["window"][1] >= 62.0)
    assert set(v["baseline"]) == {"hunt_lines_vs_pfeiffer_facts", "pfeiffer_lines_vs_hunt_facts"}
    ov = v["overlap"]
    assert ov["lull_standard"]["overlap_s"] == 1.0  # 62-65 against speech 64-66
    assert ov["lull_extended_freeze_points_in_speech"] == ["x001"]  # 69.5 inside 69-70
    assert set(ov) >= {"pfeiffer_vtt", "hunt_vtt", "hunt_wav"}
    # supplementary: the same sides against the committed transcript of the film, clipped
    sup = ov["supplementary_against_reference_asr"]
    assert set(sup) >= {"lull_standard", "pfeiffer_vtt", "hunt_vtt", "hunt_wav"}
    film = B.reference_speech(refs["elephants_dream"]["doc"])
    assert sup["speech_s"] == round(sum(b - a for a, b in B.clip_spans(film, [60.0, 90.0])), 3)
    assert v["density"]["lull_standard"]["words"] == 6
    summary = B.summarize([v], rules)
    md = B.markdown({
        "complete": False, "smoke": True, "note": "smoke", "preregistration": {
            "tag": "suite-b-prereg", "commit": "5d628cb2099d", "committed": "2026"},
        "reference_set_sha256": "abc", "git_commit": "deadbeef", "git_dirty": False,
        "date": "2026", "judge_model": "u", "second_judge_model": None, "window_s": 15.0,
        "spend_usd": 0.0, "runs": [run], "not_scored": ["woodshole"], "summary": summary,
        "videos": [v],
    })  # fmt: skip
    assert md.startswith("# Suite B: SMOKE (not results)") and "pfeiffer" in md


def test_a_clip_never_writes_results(tmp_path, monkeypatch):
    """A clip run goes to out/partial/ even from a clean tree."""
    monkeypatch.setattr(B, "pin", lambda git=None: ({"tag": "t", "commit": "c" * 12,
                                                     "committed": "2026-10-28T03:07:27+00:00"},
                                                    "d"))  # fmt: skip
    seen = {}
    monkeypatch.setattr(B, "guard_results_write", lambda folder: seen.setdefault("f", folder))

    class Stop(Exception):
        pass

    def stop(*a, **k):
        raise Stop

    monkeypatch.setattr(B.A, "clients", stop)
    with pytest.raises(Stop):
        B.main(["--run", f"elephants_dream={write_run(tmp_path / 'r')}", "--offset",
                "elephants_dream=60"])  # fmt: skip
    assert seen["f"] == B.PARTIAL


CUT = "truncated: finish_reason=length or empty content after retry with max_tokens=32000"


def cut_off_when(pred):
    """Credits every fact, but an answer about facts for which pred(ids) holds is cut off."""

    def fn(prompt, schema):
        from lull.tf import TFUnusable

        ids = schema["properties"]["facts"]["items"]["properties"]["id"]["enum"]
        if pred(ids):
            raise TFUnusable(CUT)
        cues = schema["properties"]["facts"]["items"]["properties"]["cue_ids"]["items"]["enum"]
        return {"facts": [{"id": i, "reason": "r", "cue_ids": cues} for i in ids]}

    return fn


def test_a_cut_off_batch_is_judged_again_in_halves_down_to_single_facts(tmp_path):
    tf = FakeTF(cut_off_when(lambda ids: len(ids) > 2 or "p-03.1" in ids))
    judge = Judge(tf, Budget(FakeLedger(), cap=1.0), cache=tmp_path)
    rules = {**RULES, "judge": {**RULES["judge"], "facts_per_call": 4}}
    facts = [fact(f"p-0{i}.1", 60.0 + i) for i in range(1, 6)]
    cues = [{"id": "c001", "track": "standard", "text": "x", "start": 61.0, "end": 62.0}]
    out, problems = B.judge_facts(judge, rules, "elephants_dream", DOC, facts, cues, "r1", "s")
    # [01-04] cut -> [01,02] ok, [03,04] cut -> [03] cut alone, [04] ok; then [05] ok
    assert len(tf.calls) == 6
    assert {k for k, v in out.items() if v["cue_ids"] == ["c001"]} == {
        "p-01.1", "p-02.1", "p-04.1", "p-05.1"}  # fmt: skip
    assert out["p-03.1"] == {"cue_ids": [], "reason": None}
    model = "nvidia/Nemotron-3-Ultra-550b-a55b"
    assert problems == [
        f"p-01.1 to p-04.1 (4 facts): answer from {model} cut off by its token limit; "
        "judged again in halves",
        f"p-03.1 to p-04.1 (2 facts): answer from {model} cut off by its token limit; "
        "judged again in halves",
        f"p-03.1: answer from {model} cut off by its token limit even alone; no verdict "
        "(not conveyed)",
    ]
    # the answers and the cut-offs are saved: judging again asks nothing
    again = Judge(FakeTF(lambda p, s: pytest.fail("asked again")), Budget(FakeLedger(), 1.0),
                  cache=tmp_path)  # fmt: skip
    out2, problems2 = B.judge_facts(again, rules, "elephants_dream", DOC, facts, cues, "r2", "s")
    assert out2 == out and problems2 == problems and again.paid_by == {"r1"}


def test_only_a_cut_off_answer_is_split(tmp_path):
    from lull.tf import TFUnusable

    def unparseable(prompt, schema):
        raise TFUnusable("truncated: unparseable output (x) after retry with max_tokens=32000")

    judge = Judge(FakeTF(unparseable), Budget(FakeLedger(), cap=1.0), cache=tmp_path)
    facts = [fact("p-01.1", 61.0), fact("p-02.1", 62.0)]
    cues = [{"id": "c001", "track": "standard", "text": "x", "start": 61.0, "end": 62.0}]
    with pytest.raises(TFUnusable, match="unparseable"):
        B.judge_facts(judge, RULES, "elephants_dream", DOC, facts, cues, "r1", "s")
