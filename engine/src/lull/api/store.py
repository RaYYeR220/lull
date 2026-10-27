"""Read-only access to finished run directories."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lull.api.settings import ApiSettings

RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
MAX_LIVE = 20


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name)]


def _mtime_iso(d: Path) -> str:
    return datetime.fromtimestamp(d.stat().st_mtime, UTC).isoformat(timespec="seconds")


class RunStore:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings

    def find(self, run_id: str) -> tuple[str, Path] | None:
        """Return (kind, dir) for a run id, or None. Showcase wins over live."""
        if not RUN_ID_RE.match(run_id) or run_id in (".", ".."):
            return None
        for kind, root in (
            ("showcase", self.settings.showcase_dir),
            ("live", self.settings.runs_dir),
        ):
            d = root / run_id
            if d.is_dir():
                return kind, d
        return None

    def summary(self, kind: str, d: Path) -> dict[str, Any]:
        manifest = _read_json(d / "manifest.json")
        if isinstance(manifest, dict):
            status = "published"
            video = manifest.get("video") or {}
            summ = manifest.get("summary") or {}
            created = manifest.get("created")
            title = video.get("name") or d.name
            duration = video.get("duration")
            cost = summ.get("cost_usd")
            cues = summ.get("cues")
        else:
            status = "failed" if (d / "error.json").exists() else "running"
            title, duration, cost, cues, created = d.name, None, None, None, None
        return {
            "id": d.name,
            "kind": kind,
            "title": title,
            "duration": duration,
            "created": created or _mtime_iso(d),
            "status": status,
            "cost_usd": cost,
            "cues": cues,
        }

    def list(self) -> list[dict[str, Any]]:
        show = [self.summary("showcase", d) for d in _dirs(self.settings.showcase_dir)]
        show.sort(key=lambda r: r["id"])
        live = [self.summary("live", d) for d in _dirs(self.settings.runs_dir)]
        live.sort(key=lambda r: r["created"], reverse=True)
        return show + live[:MAX_LIVE]

    def detail(self, d: Path) -> dict[str, Any] | None:
        manifest = _read_json(d / "manifest.json")
        if not isinstance(manifest, dict):
            return None
        return {"manifest": manifest, "cues": _read_json(d / "cues.json")}

    def servable(self, d: Path, rel: str) -> Path | None:
        """Resolve rel to a file that is a manifest output or directly under keyframes/."""
        if not rel or rel.startswith("/") or "\\" in rel or "\x00" in rel:
            return None
        parts = rel.split("/")
        if any(p in ("", ".", "..") for p in parts):
            return None
        manifest = _read_json(d / "manifest.json")
        outputs = manifest.get("outputs", {}) if isinstance(manifest, dict) else {}
        if rel not in outputs and not (parts[0] == "keyframes" and len(parts) == 2):
            return None
        target = (d / rel).resolve()
        if not target.is_relative_to(d.resolve()) or not target.is_file():
            return None
        return target
