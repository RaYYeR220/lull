"""Local paths in published results: repository-relative inside the repository, else the last
component (a run folder's name or a file name); nothing else changes."""

import json
from pathlib import Path

import public_paths as P

ROOT = Path(r"C:\work\lull-eval")


def test_inside_the_repository_paths_become_repository_relative():
    assert P.public_path(r"C:\work\lull-eval\eval\suite_a\out\a01\video.mp4", ROOT) == (
        "eval/suite_a/out/a01/video.mp4"
    )
    assert P.public_path("c:/WORK/lull-eval/eval/x.tar.gz", ROOT) == "eval/x.tar.gz"


def test_outside_paths_keep_only_their_last_component():
    assert P.public_path(r"C:\work\lull-runs\final\ed2", ROOT) == "ed2"
    assert P.public_path("C:/work/lull-runs/final/inputs/ed90.mp4", ROOT) == "ed90.mp4"
    assert P.public_path("/c/Users/someone/runs/ed2/", ROOT) == "ed2"
    assert P.public_path("~/.lull/ledger.db", ROOT) == "ledger.db"


def test_other_strings_are_left_alone():
    for s in ("http://127.0.0.1:8089 (local)", "/v1/audio/speech: closed", "final-ed2",
              "eval/suite_a/out/a01/video.mp4", "Slide 2 / 4", ""):  # fmt: skip
        assert P.public_path(s, ROOT) == s


def test_public_walks_documents_and_is_idempotent():
    doc = {"runs": [{"path": r"C:\work\lull-runs\final\ed2", "run_id": "final-ed2"}],
           "bundle": {"path": r"C:\work\lull-eval\eval\b.tar.gz", "sha256": "ab"},
           "n": 3, "ok": True, "none": None}  # fmt: skip
    once = P.public(doc, ROOT)
    assert once == {"runs": [{"path": "ed2", "run_id": "final-ed2"}],
                    "bundle": {"path": "eval/b.tar.gz", "sha256": "ab"},
                    "n": 3, "ok": True, "none": None}  # fmt: skip
    assert P.public(once, ROOT) == once
    assert doc["runs"][0]["path"].startswith("C:")  # the input is not changed


def test_scrub_folder_rewrites_only_files_with_local_paths(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"path": r"C:\work\lull-runs\x"}), "utf-8")
    clean = json.dumps({"path": "eval/x"})
    (tmp_path / "b.json").write_text(clean, encoding="utf-8")
    (tmp_path / "c.md").write_text(r"C:\work\lull-runs\x", encoding="utf-8")
    assert P.scrub_folder(tmp_path, ROOT) == ["a.json"]
    assert json.loads((tmp_path / "a.json").read_text("utf-8")) == {"path": "x"}
    assert (tmp_path / "b.json").read_text("utf-8") == clean
    assert P.scrub_folder(tmp_path, ROOT) == []
