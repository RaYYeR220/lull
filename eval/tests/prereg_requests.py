"""The request lock: the primary judge requests (coverage and claims) of the current scorer must
be byte-identical to those of the scorer as first committed (5551575), before any judge call.

    uv run python -m tests.prereg_requests   # rewrites tests/fixtures/prereg_requests.json

The expected hashes come from 5551575's own code: its score.py is read with
`git show 5551575:eval/suite_a/score.py`, imported (it uses the same specs/render/style/text
modules, unchanged since the tag), and asked for the requests of a fixed set of cues per
video. A request's hash is the judge cache key: sha256 of the sorted-key JSON of model,
prompt, schema, temperature, reasoning and max_tokens. The fixture is committed so the lock
holds without git history; test_score.py also regenerates it from git when the history is
there and checks it is unchanged.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PREREG_SCORER = "55515753f17eb8b764d77cd2389123e43fe5ba16"  # "eval: suite A scorer"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "prereg_requests.json"
REPO = Path(__file__).resolve().parents[2]
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"


def lock_cues(entry: dict) -> list[dict]:
    """A fixed cue set per video: a standard cue with two claims, a standard cue without
    claims (the text stands in), an extended cue with a freeze point."""
    first, last = entry["slides"][0], entry["slides"][-1]
    facts = entry["visual_facts"]
    return [
        {"id": "c001", "track": "standard", "start": first["start"] + 0.5,
         "text": facts[0]["text"], "claims": [{"text": facts[0]["text"]},
                                              {"text": "A slide is shown."}]},
        {"id": "c002", "track": "standard", "start": first["start"] + 1.5,
         "text": "A person points at the screen.", "claims": []},
        {"id": "x001", "track": "extended", "start": last["start"] + 0.25,
         "freeze_at": last["start"] + 0.25, "text": facts[-1]["text"],
         "claims": [{"text": facts[-1]["text"]}]},
    ]  # fmt: skip


def digest(prompt: str, schema: dict, max_tokens: int) -> str:
    request = {"model": ULTRA, "prompt": prompt, "schema": schema, "temperature": 0.0,
               "thinking": True, "max_tokens": max_tokens}  # fmt: skip
    return hashlib.sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()


def hashes(module, key: dict) -> dict[str, dict[str, str]]:
    out = {}
    for entry in key["videos"]:
        cues = lock_cues(entry)
        cov = module.coverage_request(entry, cues)
        cla = module.claims_request(entry, cues)
        out[entry["id"]] = {
            "coverage": digest(*cov, module.JUDGE_MAX_TOKENS),
            "claims": digest(*cla, module.JUDGE_MAX_TOKENS),
        }
    return out


def preregistered_module(commit: str = PREREG_SCORER):
    """5551575's score.py, imported from git history (None when git or the commit is not
    there)."""
    try:
        show = ["git", "show", f"{commit}:eval/suite_a/score.py"]
        out = subprocess.run(show, cwd=REPO, capture_output=True, encoding="utf-8", check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    path = Path(tempfile.mkdtemp()) / "score_prereg.py"
    path.write_text(out.stdout, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("suite_a_score_prereg", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look themselves up here
    spec.loader.exec_module(module)
    return module


def main() -> None:
    from suite_a.score import load_key

    key, key_sha = load_key()
    module = preregistered_module()
    if module is None:
        raise SystemExit(f"git show {PREREG_SCORER} failed: run inside the repository")
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    doc = {"commit": PREREG_SCORER, "key_sha256": key_sha, "requests": hashes(module, key)}
    FIXTURE.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {FIXTURE}")


if __name__ == "__main__":
    main()
