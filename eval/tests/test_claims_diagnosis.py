"""The supplementary breakdown of Suite A's unsupported claims (added after the scored run)."""

import json
from pathlib import Path

import pytest

from suite_a import claims_diagnosis as D
from suite_a.score import Refused


def claim(key, text, verdict, track="standard"):
    return {"key": key, "cue": key.split("#")[0], "track": track, "claim": text,
            "verdict": verdict, "reason": f"judge on {text}"}  # fmt: skip


def fixture(tmp_path: Path) -> tuple[dict, Path, dict]:
    runs = tmp_path / "runs"
    (runs / "r1").mkdir(parents=True)
    (runs / "r2").mkdir(parents=True)
    (runs / "r1" / "cues.json").write_text(json.dumps({
        "cues": [{"id": "c001", "text": "Title Tit.", "claims": [
            {"text": "Title", "kind": "atomic"}, {"text": " ", "kind": "atomic"},
            {"text": "Tit", "kind": "atomic"}, {"text": "Title Tit", "kind": "sentence"}]}],
        "extended": [{"id": "x001", "text": "whole", "claims": []}]}),
        encoding="utf-8")  # fmt: skip
    (runs / "r2" / "cues.json").write_text(json.dumps({
        "cues": [{"id": "c001", "text": "Points at 5 s.", "claims": [
            {"text": "wrong value", "kind": "atomic"}, {"text": "right", "kind": "atomic"}]}],
        "extended": []}), encoding="utf-8")  # fmt: skip
    result = {
        "aggregate": {"hallucination_claims": {"value": 3 / 7, "num": 3.0, "den": 7.0,
                                               "ci95": [0.1, 0.8]}},  # fmt: skip
        "videos": [
            {"id": "v1", "run_id": "r1", "hallucination": {"claims": [2, 4]},
             "detail": {"claims": [claim("c001#1", "Title", "supported"),
                                   claim("c001#2", "Tit", "unsupported"),
                                   claim("c001#3", "Title Tit", "unsupported"),
                                   claim("x001#1", "whole", "supported", "extended")]}},
            {"id": "v2", "run_id": "r2", "hallucination": {"claims": [1, 2]},
             "detail": {"claims": [claim("c001#1", "wrong value", "unsupported"),
                                   claim("c001#2", "right", "supported")]}},
            {"id": "v3", "failed": "the run failed"},
        ],
    }  # fmt: skip
    review = {"claims": {
        "v1 c001#2": {"claim": "Tit", "class": "d", "group": "title fragment", "note": "n1"},
        "v1 c001#3": {"claim": "Title Tit", "class": "b", "note": "repeats #1-#2"},
        "v2 c001#1": {"claim": "wrong value", "class": "c", "note": "contradicts the chart"},
    }}  # fmt: skip
    return result, runs, review


def test_kinds_follow_the_scorers_claim_numbering():
    kinds = D.claim_kinds({"cues": [{"id": "c1", "text": "t", "claims": [
        {"text": "a", "kind": "atomic"}, {"text": "", "kind": "atomic"},
        {"text": "a b", "kind": "sentence"}]}],
        "extended": [{"id": "x1", "text": "whole", "claims": []}]})  # fmt: skip
    assert kinds == {"c1#1": "atomic", "c1#2": "sentence", "x1#1": "whole-text"}


def test_atomic_only_rate_kinds_and_classes(tmp_path):
    result, runs, review = fixture(tmp_path)
    doc = D.diagnose(result, runs, review, source_sha256="abc")
    a = doc["atomic_only"]
    assert (a["num"], a["den"], a["value"]) == (2.0, 4.0, 0.5)
    assert a["ci95"][0] is not None
    assert doc["by_kind"] == {"atomic": [2, 4], "sentence": [1, 1], "whole-text": [0, 1]}
    assert {k: v["count"] for k, v in doc["classes"].items()} == {"a": 0, "b": 1, "c": 1, "d": 1}
    assert doc["preregistered"] == result["aggregate"]["hallucination_claims"]
    assert doc["source"]["sha256"] == "abc"
    assert [(c["video"], c["key"], c["kind"], c["class"]) for c in doc["claims"]] == [
        ("v1", "c001#2", "atomic", "d"), ("v1", "c001#3", "sentence", "b"),
        ("v2", "c001#1", "atomic", "c")]  # fmt: skip
    first = doc["claims"][0]
    assert first["reason"] == "judge on Tit" and first["group"] == "title fragment"


@pytest.mark.parametrize("change, words", [
    (lambda r: r["claims"].pop("v2 c001#1"), "no review for v2 c001#1"),
    (lambda r: r["claims"].update({"v1 c001#1": {"claim": "Title", "class": "d", "note": ""}}),
     "v1 c001#1 is not an unsupported claim"),
    (lambda r: r["claims"]["v1 c001#2"].update(claim="Tot"), "says 'Tot'"),
    (lambda r: r["claims"]["v1 c001#3"].update({"class": "d"}), "sentence claims are class b"),
    (lambda r: r["claims"]["v1 c001#2"].update({"class": "b"}), "only sentence claims"),
    (lambda r: r["claims"]["v2 c001#1"].update({"class": "e"}), "unknown class 'e'"),
])  # fmt: skip
def test_the_review_must_match_the_scored_claims(tmp_path, change, words):
    result, runs, review = fixture(tmp_path)
    change(review)
    with pytest.raises(Refused, match=words):
        D.diagnose(result, runs, review, source_sha256="abc")


def test_counts_must_match_what_the_scorer_counted(tmp_path):
    result, runs, review = fixture(tmp_path)
    result["videos"][0]["hallucination"]["claims"] = [2, 5]
    with pytest.raises(Refused, match="v1"):
        D.diagnose(result, runs, review, source_sha256="abc")


def test_markdown_is_labelled_keeps_the_prereg_number_and_lists_every_claim(tmp_path):
    result, runs, review = fixture(tmp_path)
    md = "\n".join(D.markdown(D.diagnose(result, runs, review, source_sha256="abc"), level=3))
    assert md.startswith("### ") and "added after the scored run, no new calls" in md
    assert "42.9% [10.0%, 80.0%] (3/7)" in md and "50.0%" in md and "(2/4)" in md
    for text in ("Tit", "Title Tit", "wrong value", "contradicts the chart", "judge on Tit"):
        assert text in md, text
    for label in D.CLASSES.values():
        assert label in md


def test_the_section_is_appended_once_and_replaced_after(tmp_path):
    result, runs, review = fixture(tmp_path)
    sec = D.markdown(D.diagnose(result, runs, review, source_sha256="abc"))
    base = "# Suite A: results\n\nNotes.\n"
    once = D.with_section(base, sec)
    assert once.startswith(base) and once.endswith("\n") and once.count(D.MARK) == 1
    assert D.with_section(once, sec) == once
    other = D.with_section(once, [D.MARK + " (added after the scored run, no new calls)", "", "x"])
    assert other.startswith(base) and other.count(D.MARK) == 1 and other.rstrip().endswith("x")


def test_main_writes_only_from_a_clean_tree(tmp_path):
    result, runs, review = fixture(tmp_path)
    res = tmp_path / "results"
    res.mkdir()
    (res / "suite_a.json").write_text(json.dumps(result), encoding="utf-8")
    (res / "SUITE_A.md").write_text("# Suite A: results\n", encoding="utf-8")
    rev = tmp_path / "review.json"
    rev.write_text(json.dumps(review), encoding="utf-8")
    args = ["--results", str(res), "--runs", str(runs), "--review", str(rev)]
    with pytest.raises(Refused, match="clean tree"):
        D.main(args, git=lambda *a: (0, " M eval/suite_a/score.py\n"))
    assert not (res / "suite_a_claims.json").exists()
    assert D.main(args, git=lambda *a: (0, "")) == 0
    doc = json.loads((res / "suite_a_claims.json").read_text(encoding="utf-8"))
    assert doc["source"]["sha256"] == D.sha256_file(res / "suite_a.json")
    assert D.MARK in (res / "SUITE_A.md").read_text(encoding="utf-8")
