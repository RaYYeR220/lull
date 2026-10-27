"""Read access to run directories: the run list, one run's manifest and cues, its files.

Everything here tolerates damage: a manifest that is not JSON marks its run failed, fields of
the wrong type read as missing, and a run whose summary still raises is left out of the list.
What a manifest returns is a whitelist of its fields with local paths stripped from every
string. JSON files are parsed once per change (keyed by mtime and size; the Studio, the one
writer in this process, also forgets a file it rewrites), and the run list is kept for
LIST_TTL seconds, built from the MAX_LIVE most recently modified live folders only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lull.api.clean import Scrubber, default_roots
from lull.api.settings import ApiSettings

log = logging.getLogger(__name__)

RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
CUE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KEYFRAME_RE = re.compile(r"^[\w.-]+\.jpg$")
MAX_LIVE = 20
LIST_TTL = 5.0
JSON_CACHE = 256
MANIFEST, CUES, STUDIO, ERROR = "manifest.json", "cues.json", "studio.json", "error.json"
OPTION_KEYS = ("mode", "profile", "voice", "language", "source_url")
VERSION_KEYS = ("engine", "models", "tts")
SUMMARY_KEYS = (
    "cues", "coverage", "overlap_seconds", "overlap", "held", "f113", "lint_warnings",
    "cost_usd", "cost_per_min", "calls_by_role_model", "wcag", "reviewed", "extended_produced",
    "extended_error", "levels", "tts_disclosure", "research",
)  # fmt: skip


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _str(x: Any) -> str | None:
    return x if isinstance(x, str) else None


def _num(x: Any) -> float | int | None:
    return x if isinstance(x, int | float) and not isinstance(x, bool) else None


def _dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir() and RUN_ID_RE.match(p.name)]


def _mtime(d: Path) -> float:
    try:
        return d.stat().st_mtime
    except OSError:
        return 0.0


def _mtime_iso(d: Path) -> str:
    return datetime.fromtimestamp(_mtime(d), UTC).isoformat(timespec="seconds")


def safe_rel(rel: Any) -> bool:
    """True for a plain relative path inside a run: forward slashes, no empty, '.' or '..'
    part, no drive, stream (':') or NUL."""
    if not isinstance(rel, str) or not rel or len(rel) > 512:
        return False
    if rel.startswith("/") or any(c in rel for c in ("\\", "\x00", ":")):
        return False
    return all(p not in ("", ".", "..") for p in rel.split("/"))


def contained_file(d: Path, rel: str) -> Path | None:
    """d/rel when it is a file that resolves inside d (no symlink out of it)."""
    if not safe_rel(rel):
        return None
    target = (d / rel).resolve()
    if not target.is_relative_to(d.resolve()) or not target.is_file():
        return None
    return target


def audio_url(run_id: str, cue: dict) -> str | None:
    """The cue's audio route, versioned by the file it plays so a new take is not cached."""
    path, cue_id = cue.get("audio_path"), cue.get("id")
    if not isinstance(path, str) or not isinstance(cue_id, str) or not CUE_ID_RE.match(cue_id):
        return None
    tag = hashlib.sha256(f"{path}|{cue.get('audio_dur')}".encode()).hexdigest()[:10]
    return f"/v1/runs/{run_id}/cues/{cue_id}/audio?v={tag}"


def cue_view(run_id: str, cue: dict) -> dict:
    """A cue as visitors get it: its audio as a URL, never a file path."""
    out = {k: v for k, v in cue.items() if k != "audio_path"}
    out["audio_url"] = audio_url(run_id, cue)
    return out


class RunStore:
    def __init__(
        self, settings: ApiSettings, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.settings = settings
        self.clock = clock
        self._lock = threading.Lock()
        self._list: tuple[float, list[dict]] | None = None
        self._json: OrderedDict[Path, tuple[tuple[int, int], Any]] = OrderedDict()
        self._scrub: Scrubber | None = None

    # -- cached reads -----------------------------------------------------------------------

    def read_json(self, path: Path) -> Any:
        """The parsed file, or None (missing or not JSON); parsed again only once it changes."""
        try:
            st = path.stat()
        except OSError:
            self.forget(path)
            return None
        sig = (st.st_mtime_ns, st.st_size)
        with self._lock:
            hit = self._json.get(path)
            if hit is not None and hit[0] == sig:
                self._json.move_to_end(path)
                return hit[1]
        data = _read_json(path)
        with self._lock:
            self._json[path] = (sig, data)
            self._json.move_to_end(path)
            while len(self._json) > JSON_CACHE:
                self._json.popitem(last=False)
        return data

    def forget(self, path: Path) -> None:
        with self._lock:
            self._json.pop(path, None)

    def invalidate(self, d: Path | None = None) -> None:
        """Drop the run list, and the cached files of run folder `d`."""
        with self._lock:
            self._list = None
            if d is not None:
                for p in [p for p in self._json if p.parent == d]:
                    del self._json[p]

    def manifest(self, d: Path) -> dict | None:
        m = self.read_json(d / MANIFEST)
        return m if isinstance(m, dict) else None

    @property
    def scrub(self) -> Scrubber:
        if self._scrub is None:
            data = self.settings.lull_data
            self._scrub = Scrubber(data.absolute(), default_roots(data), names_only=True)
        return self._scrub

    # -- runs ---------------------------------------------------------------------------------

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
        manifest = self.manifest(d)
        title, duration, cost, cues, created = d.name, None, None, None, None
        if manifest is not None:
            status = "published"
            video, summ = _dict(manifest.get("video")), _dict(manifest.get("summary"))
            title = _str(video.get("name")) or d.name
            duration, cost = _num(video.get("duration")), _num(summ.get("cost_usd"))
            cues = summ.get("cues") if isinstance(summ.get("cues"), dict) else None
            created = _str(manifest.get("created"))
        elif (d / MANIFEST).exists() or (d / ERROR).exists():
            status = "failed"  # a manifest that does not read is no publication
        else:
            status = "running"
        return {
            "id": d.name,
            "kind": kind,
            "title": self.scrub(title),
            "duration": duration,
            "created": created or _mtime_iso(d),
            "status": status,
            "cost_usd": cost,
            "cues": cues,
        }

    def _summaries(self, kind: str, dirs: list[Path]) -> list[dict[str, Any]]:
        out = []
        for d in dirs:
            try:
                out.append(self.summary(kind, d))
            except Exception:
                log.exception("run list: %s %s left out", kind, d.name)
        return out

    def list(self) -> list[dict[str, Any]]:
        now = self.clock()
        with self._lock:
            if self._list is not None and now < self._list[0]:
                return self._list[1]
        show = self._summaries("showcase", _dirs(self.settings.showcase_dir))
        show.sort(key=lambda r: r["id"])
        newest = sorted(_dirs(self.settings.runs_dir), key=_mtime, reverse=True)[:MAX_LIVE]
        live = self._summaries("live", newest)
        live.sort(key=lambda r: r["created"], reverse=True)
        result = show + live
        with self._lock:
            self._list = (now + LIST_TTL, result)
        return result

    def dirty(self, d: Path) -> bool:
        """True when the Studio changed a cue since the run was last published."""
        return _dict(self.read_json(d / STUDIO)).get("dirty") is True

    def detail(self, d: Path) -> dict[str, Any] | None:
        manifest = self.manifest(d)
        if manifest is None:
            return None
        return {
            "manifest": self.public_manifest(manifest),
            "cues": self.public_cues(d),
            "dirty": self.dirty(d),
        }

    def public_manifest(self, m: dict) -> dict[str, Any]:
        """The manifest fields a visitor gets, with local paths stripped from every string."""
        video, options = _dict(m.get("video")), _dict(m.get("options"))
        summ, versions = _dict(m.get("summary")), _dict(m.get("versions"))
        outputs = {
            name: {"sha256": _str(info.get("sha256")), "bytes": _num(info.get("bytes"))}
            for name, info in ((n, _dict(i)) for n, i in _dict(m.get("outputs")).items())
            if safe_rel(name)
        }
        out = {
            "run_id": _str(m.get("run_id")),
            "created": _str(m.get("created")),
            "video": {
                "name": _str(video.get("name")),
                "duration": _num(video.get("duration")),
                "sha256": _str(video.get("sha256")),
            },
            "options": {
                k: options[k] for k in OPTION_KEYS if k in options and _str(options[k]) is not None
            },
            "outputs": outputs,
            "summary": {k: summ[k] for k in SUMMARY_KEYS if k in summ},
            "versions": {k: versions[k] for k in VERSION_KEYS if k in versions},
        }
        return self.scrub.tree(out)

    def public_cues(self, d: Path) -> dict[str, Any] | None:
        doc = self.read_json(d / CUES)
        if not isinstance(doc, dict):
            return None
        out: dict[str, Any] = {k: doc[k] for k in ("run_id", "language") if k in doc}
        for key in ("cues", "extended"):
            items = doc.get(key)
            items = items if isinstance(items, list) else []
            out[key] = [cue_view(d.name, c) for c in items if isinstance(c, dict)]
        return out

    # -- files ----------------------------------------------------------------------------------

    def servable(self, d: Path, rel: str) -> Path | None:
        """Resolve rel to a file that is a manifest output or a keyframe image."""
        if not safe_rel(rel):
            return None
        parts = rel.split("/")
        manifest = self.manifest(d)
        outputs = manifest.get("outputs") if manifest is not None else None
        listed = isinstance(outputs, dict) and rel in outputs
        keyframe = len(parts) == 2 and parts[0] == "keyframes" and KEYFRAME_RE.match(parts[1])
        if not (listed or keyframe):
            return None
        return contained_file(d, rel)
