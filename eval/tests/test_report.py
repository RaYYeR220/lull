"""The results report: which results it takes, the site's schema, known failures, refusals.
Fixtures only (tests/fixtures/report/: smoke outputs relabelled per test, synthetic controls;
tests/fixtures/web/: the site's own sample). No network."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import report as R
import report_schema as S
from suite_a.score import Refused

FIX = Path(__file__).parent / "fixtures" / "report"
WEB_SAMPLE = Path(__file__).parent / "fixtures" / "web" / "benchmarks.sample.json"
SITE_SCHEMA = (
    Path(__file__).resolve().parents[3] / "lull-web" / "web" / "lib" / "benchmarks-schema.ts"
)


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def results_dir(tmp_path, *, a_complete=True, b_complete=False, controls=True):
    """A results/ folder: Suite A's smoke relabelled complete (or not), Suite B's smoke as the
    scorer wrote it (or relabelled), the synthetic controls."""
    d = tmp_path / "results"
    d.mkdir(parents=True)
    a = load("suite_a_smoke.json")
    a["complete"] = a_complete
    b = load("suite_b_smoke.json")
    if b_complete:
        b["complete"], b["smoke"] = True, False
    (d / "suite_a.json").write_text(json.dumps(a), encoding="utf-8")
    (d / "suite_b.json").write_text(json.dumps(b), encoding="utf-8")
    if controls:
        for n in ("controls_negative.json", "ablation.json"):
            shutil.copy(FIX / n, d / n)
    return d


TAGS = {
    "suite-a-prereg": "194d4d0803aa4a9195abbb450b35d7d132631ae1",
    "suite-b-prereg-2": "f308f9034fd87b7c8ac7dcb52c4f601ac06cddd6",
    "controls-prereg-2": "f30231c37c0d7a7a07593f8d9ba3a8decbbdb129",
    "HEAD": "f30231c37c0d7a7a07593f8d9ba3a8decbbdb129",
}


class FakeGit:
    """A clean repository whose tags pin exactly the files on disk."""

    def __init__(self, changed: str = "", dirty: str = ""):
        self.changed, self.dirty = changed, dirty

    def __call__(self, *args):
        from controls.negative import prereg_digest
        from suite_a.render import KEY_SHA_PATH
        from suite_b.facts import SHA_PATH

        if args[0] == "rev-parse":
            return 0, TAGS.get(args[-1].split("^")[0], TAGS["HEAD"]) + "\n"
        if args[0] == "merge-base":
            return 0, ""
        if args[0] == "diff":
            return 0, self.changed
        if args[0] == "status":
            return 0, self.dirty
        if args[0] == "show" and ":" in args[1]:
            f = args[1].split(":", 1)[1]
            if f.endswith("key.sha256"):
                return 0, KEY_SHA_PATH.read_text(encoding="utf-8")
            if f.endswith("facts.sha256"):
                return 0, SHA_PATH.read_text(encoding="utf-8")
            if f.endswith("prereg.sha256"):
                return 0, f"{prereg_digest()}  controls/prereg.json\n"
        if args[0] == "show":
            return 0, "2026-10-28T05:21:36+01:00\n"
        raise AssertionError(args)


# -- what is included ------------------------------------------------------------------------------


def test_only_complete_results_are_included(tmp_path):
    inc, skipped = R.load_results(results_dir(tmp_path))
    assert set(inc) == {"suite_a", "controls_negative", "ablation"}
    assert "suite_b" in skipped and "not complete" in skipped["suite_b"]
    inc, skipped = R.load_results(results_dir(tmp_path / "x", a_complete=False, controls=False))
    assert inc == {} and skipped["suite_a"].startswith("not complete")
    assert "controls_negative" in skipped and "missing" in skipped["controls_negative"]


def test_a_smoke_is_never_included_even_when_labelled_complete(tmp_path):
    d = results_dir(tmp_path, b_complete=True)
    b = json.loads((d / "suite_b.json").read_text(encoding="utf-8"))
    b["smoke"] = True
    (d / "suite_b.json").write_text(json.dumps(b), encoding="utf-8")
    inc, skipped = R.load_results(d)
    assert "suite_b" not in inc and "smoke" in skipped["suite_b"]


# -- the site's schema -----------------------------------------------------------------------------


def test_the_python_port_reads_the_sites_own_sample():
    got = S.parse_benchmarks(json.loads(WEB_SAMPLE.read_text(encoding="utf-8")))
    assert got["fixture"] is True and got["suite_a"]["aggregate"]["videos"] == 2
    assert got["other_suites"] == [] and len(got["known_failures"]) == 2


@pytest.mark.parametrize(
    "mutate, words",
    [
        (lambda b: b.pop("generated_at"), ".generated_at must be a non-empty string"),
        (lambda b: b.update(known_failures={}), ".known_failures must be a list"),
        (lambda b: b["known_failures"].append({"title": "x"}), "detail must be a non-empty"),
        (
            lambda b: b["suites"]["suite_a"]["aggregate"].pop("recall_standard"),
            "suites.suite_a.aggregate.recall_standard must be a ratio",
        ),
        (
            lambda b: b["suites"]["suite_a"]["aggregate"]["recall_with_extended"].update(num=True),
            "recall_with_extended.num must be a number",
        ),
        (
            lambda b: b["suites"]["suite_a"]["videos"][0].pop("duration_s"),
            "suites.suite_a.videos[0].duration_s must be a number",
        ),
    ],
)
def test_the_port_names_the_field_that_does_not_hold(mutate, words):
    b = json.loads(WEB_SAMPLE.read_text(encoding="utf-8"))
    mutate(b)
    with pytest.raises(S.BenchmarksError, match=words.replace("[", r"\[").replace("]", r"\]")):
        S.parse_benchmarks(b)


def test_benchmarks_hold_the_scorer_objects_and_validate(tmp_path):
    inc, _ = R.load_results(results_dir(tmp_path, b_complete=True))
    fails = [{"title": "t", "detail": "d"}]
    out = R.benchmarks(inc, fails, "abc123", "2026-10-28T07:00:00+00:00")
    assert out["suites"]["suite_a"] == inc["suite_a"]  # the scorer's own object
    assert out["suites"]["suite_b"] == inc["suite_b"]
    assert out["suites"]["controls"] == {"negative": inc["controls_negative"],
                                         "ablation": inc["ablation"]}  # fmt: skip
    assert out["fixture"] is True  # built from fixtures: the site refuses it in production
    got = S.parse_benchmarks(out)
    assert set(got["other_suites"]) == {"suite_b", "controls"}


@pytest.mark.skipif(shutil.which("node") is None or not SITE_SCHEMA.exists(),
                    reason="node or the site's validator is not available")  # fmt: skip
def test_the_sites_own_validator_accepts_the_output(tmp_path):
    inc, _ = R.load_results(results_dir(tmp_path, b_complete=True))
    out = R.benchmarks(inc, [{"title": "t", "detail": "d"}], "abc123", "2026-10-28T07:00:00Z")
    f = tmp_path / "benchmarks.json"
    f.write_text(json.dumps(out), encoding="utf-8")
    script = Path(__file__).parent / "node_check_benchmarks.mjs"
    res = subprocess.run(["node", str(script), str(SITE_SCHEMA), str(f)], capture_output=True,
                         text=True, timeout=60)  # fmt: skip
    got = json.loads(res.stdout.strip().splitlines()[-1])
    assert got["ok"] and got["suiteA"] == 2 and set(got["other"]) == {"suite_b", "controls"}
    broken = dict(out, known_failures=[{"title": "only"}])
    f.write_text(json.dumps(broken), encoding="utf-8")
    res = subprocess.run(["node", str(script), str(SITE_SCHEMA), str(f)], capture_output=True,
                         text=True, timeout=60)  # fmt: skip
    assert res.returncode == 1 and "detail" in res.stdout


# -- known failures --------------------------------------------------------------------------------

README = """# Lull evaluation

## Suite A: planted facts

### Deviations from the pre-registration

Everything below was decided after the pre-registration commit.

1. **Scorer rules the key does not spell out**, applied to primary metrics: a verdict
   the judge leaves out counts against the system.
2. **a08's tie** (its line chart shows the highest value at three categories).

### Supplementary

- not a deviation

## Suite B: human description

### Deviations: what changed between the two pins

1. **First pin**: tag `suite-b-prereg`.
2. **Second pin**: changed:
   - the ASR corrections;
   - blocks.

## Controls: does the verifier catch false descriptions?

### Deviations: the two pins

The first pin, tag `controls-prereg`, points to commit `3e80579`. After a review the second
pin added:

- per-rule reporting;
- the discriminative false accept.
"""

MANUAL = """# Known failures (hand-maintained)

Merged into the results report.

## Run-to-run variance

The same video gives different descriptions run to run.

## TTS nondeterminism

Magpie returns slightly different audio for the same sentence.
"""


def test_readme_deviations_are_parsed_per_suite():
    devs = R.readme_deviations(README)
    assert [(d["suite"], d["title"]) for d in devs] == [
        ("suite_a", "Scorer rules the key does not spell out"),
        ("suite_a", "a08's tie"),
        ("suite_b", "First pin"),
        ("suite_b", "Second pin"),
        ("controls", "per-rule reporting"),
        ("controls", "the discriminative false accept"),
    ]
    assert "the ASR corrections" in devs[3]["detail"] and "blocks" in devs[3]["detail"]
    assert "**" not in devs[0]["detail"] and "a verdict the judge leaves out" in devs[0]["detail"]


def test_manual_failures_are_merged():
    assert R.manual_failures(MANUAL) == [
        {"title": "Run-to-run variance",
         "detail": "The same video gives different descriptions run to run."},
        {"title": "TTS nondeterminism",
         "detail": "Magpie returns slightly different audio for the same sentence."}]  # fmt: skip
    text = (Path(R.__file__).parent / "known_failures.md").read_text(encoding="utf-8")
    titles = [f["title"].lower() for f in R.manual_failures(text)]
    for want in ("run-to-run", "human", "pause", "tts"):
        assert any(want in t for t in titles), want


def test_held_reasons_are_classed_and_counted():
    assert (
        R.reason_class("compression kept 5 of about 12 words, twice")
        == "compression kept N of about N words, twice"
    )
    assert R.reason_class("not verified after 3 rounds: a; b") == "not verified after N rounds"
    assert R.reason_class("") == "(no reason given)"


def write_run(path, held):
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps({"summary": {"held": held}}), encoding="utf-8")


def test_known_failures_come_from_the_data(tmp_path):
    d = results_dir(tmp_path, b_complete=True)
    a = json.loads((d / "suite_a.json").read_text(encoding="utf-8"))
    a["videos"][0]["failed"] = "BudgetExceeded: run cap"
    a["videos"][1]["judge_problems"] = ["claim c1#2 not judged (counted unsupported)"]
    (d / "suite_a.json").write_text(json.dumps(a), encoding="utf-8")
    b = json.loads((d / "suite_b.json").read_text(encoding="utf-8"))
    b["runs"][0]["path"] = str(tmp_path / "b_run")
    (d / "suite_b.json").write_text(json.dumps(b), encoding="utf-8")
    runs = tmp_path / "a_runs"
    write_run(
        runs / a["videos"][0]["run_id"],
        [{"id": "c3", "reason": "compression kept 5 of about 12 words, twice"}],
    )
    write_run(
        runs / a["videos"][1]["run_id"],
        [
            {"id": "c4", "reason": "compression kept 4 of about 10 words, twice"},
            {"id": "x1", "reason": "nothing left once the unverified claims were removed; 1"},
        ],
    )
    write_run(tmp_path / "b_run", [{"id": "c9", "reason": "not verified after 3 rounds: x"}])
    inc, _ = R.load_results(d)
    fails = R.known_failures(inc, README, MANUAL, suite_a_runs=runs)
    titles = [f["title"] for f in fails]
    assert any("a01" in t and "failed" in t for t in titles)
    assert any("a02" in t and "1 judge problem" in t for t in titles)
    held = [f for f in fails if "held" in f["title"].lower()]
    assert any("compression kept N of about N words, twice" in f["title"]
               and f["detail"].startswith("2 cues") for f in held)  # fmt: skip
    assert any("not verified after N rounds" in f["title"] and "Suite B" in f["title"]
               for f in held)  # fmt: skip
    asr = next(f for f in fails if "ASR" in f["title"])
    assert (
        asr["title"].startswith("Suite B: 15 suspicious ASR terms")
        and "lower bound" in asr["detail"]
    )
    fa = [f for f in fails if f["title"].startswith("Negative control: false accept")]
    assert len(fa) == 3  # every false accept in the fixture, the other-video one included
    assert sum("Deviation" in t for t in titles) == 6
    assert titles[-2:] == ["Run-to-run variance", "TTS nondeterminism"]
    assert all(f["title"] and f["detail"] for f in fails)


def test_deviations_only_for_included_suites(tmp_path):
    inc, _ = R.load_results(results_dir(tmp_path, controls=False))
    fails = R.known_failures(inc, README, "", suite_a_runs=tmp_path)
    devs = [f["title"] for f in fails if f["title"].startswith("Deviation")]
    assert devs and all("Suite A" in t for t in devs)


# -- the report and refusals -----------------------------------------------------------------------


def test_results_markdown_carries_cis_tags_shas_models_commit_date_hardware(tmp_path):
    d = results_dir(tmp_path, b_complete=True)
    md = R.main_text(d, git=FakeGit(), readme=README, manual=MANUAL, suite_a_runs=tmp_path,
                     now="2026-10-28T07:00:00+00:00")  # fmt: skip
    a = load("suite_a_smoke.json")
    for want in ("suite-a-prereg", a["key_sha256"], "suite-b-prereg-2", "controls-prereg-2",
                 "nvidia/Nemotron-3-Ultra-550b-a55b", "Qwen/Qwen3-235B-A22B-Instruct-2507",
                 a["git_commit"][:12], a["date"], "AMD Ryzen", "95% CI", "UPPER BOUND",
                 "Known failures", "Deviations", "FIXTURE"):  # fmt: skip
        assert want in md, want
    r = a["aggregate"]["recall_with_extended"]
    assert f"{r['value']:.1%} [{r['ci95'][0]:.1%}, {r['ci95'][1]:.1%}]" in md


def test_out_web_refuses_a_dirty_tree_or_a_failed_pin_or_nothing_to_publish(tmp_path):
    d = results_dir(tmp_path)
    out = tmp_path / "web" / "benchmarks.json"
    kw = {"readme": README, "manual": MANUAL, "suite_a_runs": tmp_path, "now": "2026-10-28"}
    with pytest.raises(Refused, match="uncommitted"):
        R.write_web(d, out, git=FakeGit(dirty=" M eval/report.py\n"), **kw)
    with pytest.raises(Refused, match="changed since"):
        R.write_web(d, out, git=FakeGit(changed="eval/suite_a/key.json\n"), **kw)
    empty = results_dir(tmp_path / "e", a_complete=False, controls=False)
    with pytest.raises(Refused, match="nothing complete"):
        R.write_web(empty, out, git=FakeGit(), **kw)
    assert not out.exists()
    R.write_web(d, out, git=FakeGit(), **kw)
    S.parse_benchmarks(json.loads(out.read_text(encoding="utf-8")))


def test_results_must_match_the_pinned_key_and_reference_set(tmp_path):
    d = results_dir(tmp_path)
    a = json.loads((d / "suite_a.json").read_text(encoding="utf-8"))
    a["key_sha256"] = "0" * 64
    (d / "suite_a.json").write_text(json.dumps(a), encoding="utf-8")
    inc, _ = R.load_results(d)
    with pytest.raises(Refused, match="key"):
        R.verify_pins(inc, git=FakeGit())


def test_a_decimal_point_does_not_end_a_deviation_title():
    text = "## Controls\n\n### Deviations: x\n\n- the 0.5 s event-overlap rule;\n- U.S. maps.\n"
    assert [d["title"] for d in R.readme_deviations(text)] == [
        "the 0.5 s event-overlap rule", "U.S. maps"]  # fmt: skip


def test_the_run_log_is_appended_to_results_md(tmp_path):
    d = results_dir(tmp_path)
    log = "# Run log\n\nKept by hand.\n\n| step | outcome |\n|---|---|\n| 1 | done |\n"
    md = R.main_text(d, git=FakeGit(), readme=README, manual=MANUAL, suite_a_runs=tmp_path,
                     now="2026-10-28T07:00:00+00:00", run_log=log)  # fmt: skip
    tail = md[md.index("## Run log") :]
    assert "Kept by hand." in tail and "| 1 | done |" in tail and "\n# Run log" not in tail
    assert "## Run log\n\nKept by hand." in tail
    assert md.index("## Known failures") < md.index("## Run log")
    plain = R.main_text(d, git=FakeGit(), readme=README, manual=MANUAL, suite_a_runs=tmp_path,
                        now="2026-10-28T07:00:00+00:00", run_log="")  # fmt: skip
    assert "## Run log" not in plain
