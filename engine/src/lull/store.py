"""Stage cache: each stage's result kept as JSON in the run directory, keyed by its inputs.

`run_dir/stages/<stage>.json` holds `{"key": ..., "data": ...}`. A stage whose key still matches
is not run again, so a run stopped by a spend cap or a crash resumes where it stopped, and a
finished run repeats without a single model call. The key is the sha256 of a canonical JSON of
everything the stage's result depends on (`stage_key`); any change to those inputs is a miss.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path, PurePath

from pydantic import BaseModel

log = logging.getLogger(__name__)

STAGES_DIR = "stages"
_STAGE = re.compile(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$")


def _plain(obj: object) -> object:
    """JSON stand-ins for the non-JSON values stage inputs hold."""
    if isinstance(obj, BaseModel):
        return obj.model_dump()  # python mode: sets stay sets and are sorted below
    if isinstance(obj, set | frozenset):
        return sorted(obj, key=canonical_json)
    if isinstance(obj, PurePath):
        return obj.as_posix()
    raise TypeError(f"stage input of type {type(obj).__name__} has no canonical JSON form")


def canonical_json(obj: object) -> str:
    """`obj` as JSON with sorted keys, no whitespace, sets sorted and models dumped."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_plain
    )


def stage_key(inputs: object) -> str:
    """sha256 (hex) of the canonical JSON of a stage's inputs."""
    return hashlib.sha256(canonical_json(inputs).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Store:
    """Cached stage results under `run_dir/stages/`; a stage name may nest ("finalize/c001")."""

    def __init__(self, run_dir: Path) -> None:
        self.root = Path(run_dir) / STAGES_DIR

    def path(self, stage: str) -> Path:
        if not _STAGE.match(stage):
            raise ValueError(f"store: unsafe stage name {stage!r}")
        return self.root.joinpath(*stage.split("/")).with_suffix(".json")

    def cached(self, stage: str, key: str) -> dict | None:
        """The data saved for `stage` under `key`, or None (absent, other key or damaged)."""
        path = self.path(stage)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, ValueError) as e:
            log.warning("store: %s cache is unreadable, recomputing: %s", stage, e)
            return None
        if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict):
            log.warning("store: %s cache is not a {key, data} entry, recomputing", stage)
            return None
        return entry["data"] if entry.get("key") == key else None

    def save(self, stage: str, key: str, data: dict) -> None:
        """Write `data` for `stage` under `key`, atomically (a crash never leaves half a file)."""
        path = self.path(stage)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps({"key": key, "data": data}, ensure_ascii=False).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
