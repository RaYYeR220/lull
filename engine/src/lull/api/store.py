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

from lull.api import facts
from lull.api.clean import Scrubber, default_roots
from lull.api.facts import contained_file, safe_rel  # noqa: F401 - used here and re-exported
from lull.api.settings import ApiSettings
from lull.api.spend import public_spend, public_summary

log = logging.getLogger(__name__)

# \Z, not $: "$" also matches before a final newline. Called with fullmatch.
RUN_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,80}\Z")
CUE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
KEYFRAME_RE = re.compile(r"[\w.-]+\.jpg\Z")
MAX_LIVE = 20
LIST_TTL = 5.0
JSON_CACHE = 256
MANIFEST, CUES, STUDIO, ERROR = "manifest.json", "cues.json", "studio.json", "error.json"
PENDING = ".pending"  # in a run folder still being filled (an upload, a fork): not listed
OPTION_KEYS = ("mode", "profile", "voice", "language", "source_url")
VERSION_KEYS = ("engine", "models", "tts")
SUMMARY_KEYS = (
    "cues", "coverage", "overlap_seconds", "overlap", "held", "f113", "lint_warnings",
    "cost_usd", "cost_per_min", "calls_by_role_model", "wcag", "reviewed", "extended_produced",
    "extended_error", "levels", "tts_disclosure", "research", "spend", "spend_from_cache",
    "repetitive", "for_reviewer",
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
    return [p for p in root.iterdir() if p.is_dir() and RUN_ID_RE.fullmatch(p.name)]


def _mtime(d: Path) -> float:
    try:
        return d.stat().st_mtime
    except OSError:
        return 0.0


def _mtime_iso(d: Path) -> str:
    return datetime.fromtimestamp(_mtime(d), UTC).isoformat(timespec="seconds")


def audio_url(run_id: str, cue: dict) -> str | None:
    """The cue's audio route, versioned by the file it plays so a new take is not cached."""
    path, cue_id = cue.get("audio_path"), cue.get("id")
    if not isinstance(path, str) or not isinstance(cue_id, str) or not CUE_ID_RE.fullmatch(cue_id):
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
        # whether a run id is being described in this process right now (set by the live side)
        self.active: Callable[[str], bool] = lambda run_id: False
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
        if not RUN_ID_RE.fullmatch(run_id) or run_id in (".", ".."):
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
        title, duration, cost, cues, created, spend = d.name, None, None, None, None, None
        if manifest is not None:
            video, summ = _dict(manifest.get("video")), _dict(manifest.get("summary"))
            title = _str(video.get("name")) or d.name
            duration, cost = _num(video.get("duration")), _num(summ.get("cost_usd"))
            spend = public_spend(summ)  # model spend and searches, nothing priced
            cost = spend["model_usd"] if spend["model_usd"] is not None else cost
            cues = summ.get("cues") if isinstance(summ.get("cues"), dict) else None
            created = _str(manifest.get("created"))
        status = facts.run_status(d, self.active(d.name))
        return {
            "id": d.name,
            "kind": kind,
            "title": self.scrub(title),
            "duration": duration,
            "created": created or _mtime_iso(d),
            "status": status,
            "cost_usd": cost,
            "spend": spend,
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
        ready = [d for d in _dirs(self.settings.runs_dir) if not (d / PENDING).exists()]
        newest = sorted(ready, key=_mtime, reverse=True)[:MAX_LIVE]
        live = self._summaries("live", newest)
        live.sort(key=lambda r: r["created"], reverse=True)
        result = show + live
        with self._lock:
            self._list = (now + LIST_TTL, result)
        return result

    def dirty(self, d: Path) -> bool:
        """True when the Studio changed a cue since the run was last published."""
        return _dict(self.read_json(d / STUDIO)).get("dirty") is True

    def kind_of(self, d: Path) -> str:
        return "showcase" if d.parent.resolve() == self.settings.showcase_dir.resolve() else "live"

    def detail(self, d: Path) -> dict[str, Any]:
        """Everything the web reads about the run in `d`, finished or not (lull.api.facts):
        manifest and cues once published, null before."""
        kind = self.kind_of(d)
        manifest = self.manifest(d)
        request = _dict(self.read_json(d / facts.REQUEST))
        published = self.read_json(d / CUES)
        samples = self.settings.samples_dir
        video = facts.source_video(d, manifest or {}, request, samples)
        length = facts.duration(manifest or {}, request)
        video_name = _str(_dict((manifest or {}).get("video")).get("name"))
        source_name = _str(_dict(request.get("source")).get("name"))
        return {
            "id": d.name,
            "kind": kind,
            "status": facts.run_status(d, self.active(d.name)),
            "source": facts.run_source(kind, manifest or {}, request, samples),
            "title": self.scrub(video_name or source_name or d.name),
            "duration": length,
            "created": _str((manifest or {}).get("created"))
            or _str(request.get("created"))
            or _mtime_iso(d),
            "error": self.scrub.tree(facts.public_error(d)),
            "manifest": self.public_manifest(manifest) if manifest is not None else None,
            "cues": self.public_cues(d),
            "dirty": self.dirty(d),
            "speech": facts.speech_spans(published),
            "gaps": facts.gap_windows(published),
            "wps": facts.voice_rate(manifest or {}, request),
            "loudness": facts.loudness(d, video, length) if video is not None else None,
            "loudness_hop_s": facts.LOUDNESS_HOP,
            "original": f"/v1/runs/{d.name}/original" if video is not None else None,
        }

    def original(self, d: Path) -> Path | None:
        """The source video of the run in `d` (see lull.api.facts), or None."""
        request = _dict(self.read_json(d / facts.REQUEST))
        return facts.source_video(d, self.manifest(d) or {}, request, self.settings.samples_dir)

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
            "summary": public_summary({k: summ[k] for k in SUMMARY_KEYS if k in summ}),
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
            out[key] = [
                {**cue_view(d.name, c), "extended": key == "extended"}  # the picture holds
                for c in items
                if isinstance(c, dict)
            ]
        return out

    # -- files ----------------------------------------------------------------------------------

    def cue_audio(self, d: Path, cue_id: str) -> Path | None:
        """The .wav take cues.json names for `cue_id`: relative, inside the run folder."""
        if not CUE_ID_RE.fullmatch(cue_id):
            return None
        doc = _dict(self.read_json(d / CUES))
        for key in ("cues", "extended"):
            items = doc.get(key)
            for cue in items if isinstance(items, list) else []:
                if isinstance(cue, dict) and cue.get("id") == cue_id:
                    rel = cue.get("audio_path")
                    if not safe_rel(rel) or not rel.lower().endswith(".wav"):
                        return None
                    return contained_file(d, rel)
        return None

    def servable(self, d: Path, rel: str) -> Path | None:
        """Resolve rel to a file that is a manifest output or a keyframe image."""
        if not safe_rel(rel):
            return None
        parts = rel.split("/")
        manifest = self.manifest(d)
        outputs = manifest.get("outputs") if manifest is not None else None
        listed = isinstance(outputs, dict) and rel in outputs
        keyframe = len(parts) == 2 and parts[0] == "keyframes" and KEYFRAME_RE.fullmatch(parts[1])
        if not (listed or keyframe):
            return None
        return contained_file(d, rel)
