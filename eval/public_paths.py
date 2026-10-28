"""Local paths as the published results give them: a path inside the repository becomes
repository-relative (forward slashes); any other local path keeps only its last component (a
run folder's name, a file name). Every other string is left as it is. The scorers write their
results this way, and the results report rewrites results/*.json the same way before it reads
them (for the pinned harnesses, whose code cannot change), so a regeneration stays clean."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# a whole string that starts like a local absolute path: a drive letter, a POSIX home or drive
# mount (/c/..., /home/..., /Users/...), or ~/
_LOCAL = re.compile(r"^(?:[A-Za-z]:[\\/]|/(?:[A-Za-z]|home|Users|mnt|tmp)/|~[\\/])")


def public_path(value: str, repo_root: Path = REPO_ROOT) -> str:
    if not isinstance(value, str) or not _LOCAL.match(value):
        return value
    s = value.replace("\\", "/").rstrip("/")
    root = str(repo_root).replace("\\", "/").rstrip("/")
    if s.lower().startswith(root.lower() + "/"):
        return s[len(root) + 1 :]
    return s.rsplit("/", 1)[-1]


def public(doc, repo_root: Path = REPO_ROOT):
    """A copy of a JSON document with every local path made public."""
    if isinstance(doc, dict):
        return {k: public(v, repo_root) for k, v in doc.items()}
    if isinstance(doc, list):
        return [public(v, repo_root) for v in doc]
    if isinstance(doc, str):
        return public_path(doc, repo_root)
    return doc


def scrub_folder(folder: Path, repo_root: Path = REPO_ROOT) -> list[str]:
    """Rewrites the *.json files of a folder that hold local paths; returns their names."""
    changed = []
    for path in sorted(Path(folder).glob("*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        clean = public(doc, repo_root)
        if clean != doc:
            path.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8", newline="\n")  # fmt: skip
            changed.append(path.name)
    return changed
