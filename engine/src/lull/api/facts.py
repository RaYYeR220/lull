"""What the web reads about a run besides its manifest and cues, worked out from the run folder
for any run, finished or not.

    source    {kind: sample | upload | fork, sample_id?, forked_from?, origin?}
    status    running (being described here now) | done (published) | failed (error.json) |
              stopped (neither: the process stopped mid-run; its owner may resume it)
    error     error.json as visitors may see it: {type, message, stage, scope?}
    speech    [[start, end], ...] and gaps [{id, start, end, capacity}] (the usable windows),
              as published in cues.json, at the run's voice rate `wps`
    loudness  dBFS of the original sound per LOUDNESS_HOP seconds (floor FLOOR_DB, 1 decimal),
              the shape of the showcase export's showcase.json; worked out once, when a live
              run publishes or a run is forked, and kept in loudness.json (a request only
              reads it; a recorded run brings its own or has none)
    original  the source video the run described: its upload or sample kept in the run
              folder, or for a recorded run the sample of the same name and sha256
"""

from __future__ import annotations

import functools
import json
import logging
import math
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

from lull import media
from lull.store import file_sha256
from lull.voices import FALLBACK_WPS, voice_wps

log = logging.getLogger(__name__)

MANIFEST, CUES, REQUEST, ERROR = "manifest.json", "cues.json", "request.json", "error.json"
LOUDNESS = "loudness.json"
LOUDNESS_HOP = 0.1  # seconds per loudness value, as in showcase.json
FLOOR_DB = -120.0
SAMPLE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")  # called with fullmatch
ERROR_KEYS = ("type", "message", "stage", "scope")


def read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _r(x: Any) -> float | None:
    return round(float(x), 3) if isinstance(x, int | float) and not isinstance(x, bool) else None


# -- paths inside a run ---------------------------------------------------------------------------


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


@functools.lru_cache(maxsize=64)
def _sha_of(path: str, mtime_ns: int, size: int) -> str:
    return file_sha256(Path(path))


def sample_sha256(path: Path) -> str:
    """The file's sha256, hashed once per version of it (path, mtime, size)."""
    st = path.stat()
    return _sha_of(str(path), st.st_mtime_ns, st.st_size)


sample_sha256.cache_clear = _sha_of.cache_clear  # type: ignore[attr-defined]


def source_video(run_dir: Path, manifest: dict, request: dict, samples_dir: Path) -> Path | None:
    """The video the run described: its upload or sample (request.json input), else
    input/<name>, else the sample of that name when its sha256 matches the manifest's."""
    rel = request.get("input")
    if safe_rel(rel) and (found := contained_file(run_dir, rel)) is not None:
        return found
    video = _dict(manifest.get("video"))
    name, sha = video.get("name"), video.get("sha256")
    if safe_rel(name) and "/" not in name:
        if (found := contained_file(run_dir, f"input/{name}")) is not None:
            return found
        sample = samples_dir / name
        if isinstance(sha, str) and sample.is_file() and sample_sha256(sample) == sha:
            return sample
    return None


# -- where it came from, where it stands ------------------------------------------------------


def run_source(kind: str, manifest: dict, request: dict, samples_dir: Path) -> dict[str, Any]:
    """{kind: sample | upload | fork, ...}. A recorded (showcase) run is a sample's; a live run
    says what its request.json says, and a live run that does not say is taken for an upload
    (the private kind)."""
    if kind == "showcase":
        name = _dict(manifest.get("video")).get("name")
        stem = Path(name).stem if isinstance(name, str) else ""
        known = SAMPLE_ID_RE.fullmatch(stem) and (samples_dir / f"{stem}.mp4").is_file()
        return {"kind": "sample", "sample_id": stem if known else None}
    src = _dict(request.get("source"))
    if src.get("kind") == "sample" and isinstance(src.get("id"), str):
        return {"kind": "sample", "sample_id": src["id"]}
    if src.get("kind") == "fork" and isinstance(src.get("id"), str):
        out: dict[str, Any] = {"kind": "fork", "forked_from": src["id"]}
        out["origin"] = src.get("origin") if src.get("origin") in ("sample", "upload") else "upload"
        if isinstance(src.get("sample_id"), str):
            out["sample_id"] = src["sample_id"]
        return out
    return {"kind": "upload"}


def run_status(run_dir: Path, active: bool) -> str:
    """running (described in this process now), failed (error.json: it wins over a manifest
    that a run given up by its wall clock wrote after it was stopped; or a manifest that does
    not read), done, or stopped (neither: its owner can resume it)."""
    if active:
        return "running"
    if (run_dir / ERROR).exists():
        return "failed"
    if isinstance(read_json(run_dir / MANIFEST), dict):
        return "done"
    if (run_dir / MANIFEST).exists():
        return "failed"
    return "stopped"


def public_error(run_dir: Path) -> dict | None:
    err = read_json(run_dir / ERROR)
    if not isinstance(err, dict):
        return None
    return {k: err[k] for k in ERROR_KEYS if k in err}


def duration(manifest: dict, request: dict) -> float | None:
    for d in (
        _dict(manifest.get("video")).get("duration"),
        _dict(request.get("video")).get("duration"),
    ):
        if _r(d) is not None and d > 0:
            return _r(d)
    return None


def voice_rate(manifest: dict, request: dict) -> float | None:
    """The words per second the run was described at: what request.json recorded, else the
    voice's rate here."""
    wps = request.get("wps")
    if isinstance(wps, int | float) and not isinstance(wps, bool) and wps > 0:
        return float(wps)
    voice = _dict(manifest.get("options")).get("voice") or _dict(request.get("options")).get(
        "voice"
    )
    if not isinstance(voice, str):
        return None
    try:
        return voice_wps(voice)[0]
    except ValueError:  # voices.json does not load
        return FALLBACK_WPS


def speech_spans(cues_doc: Any) -> list[list[float]] | None:
    spans = _dict(cues_doc).get("speech")
    if not isinstance(spans, list):
        return None
    return [
        [_r(s.get("start")), _r(s.get("end"))]
        for s in spans
        if isinstance(s, dict) and _r(s.get("start")) is not None and _r(s.get("end")) is not None
    ]


def gap_windows(cues_doc: Any) -> list[dict] | None:
    gaps = _dict(cues_doc).get("gaps")
    if not isinstance(gaps, list):
        return None
    out = []
    for g in gaps:
        g = _dict(g)
        start, end, cap = (
            _r(g.get("usable_start")),
            _r(g.get("usable_end")),
            g.get("capacity_words"),
        )
        if isinstance(g.get("id"), str) and start is not None and end is not None:
            out.append({"id": g["id"], "start": start, "end": end, "capacity": cap})
    return out


# -- the loudness envelope ----------------------------------------------------------------------


def envelope(video: Path, length: float | None) -> list[float]:
    """dBFS of `video`'s first sound stream per LOUDNESS_HOP, floor FLOOR_DB, one value per hop
    of `length` (ffmpeg: never on a request)."""
    with tempfile.TemporaryDirectory(prefix="lull-loudness-") as tmp:
        wav = Path(tmp) / "original.wav"
        try:
            media.extract_wav(video, wav)
        except media.MediaError:  # no sound: silence throughout
            values = []
        else:
            values = media.loudness_profile(wav, hop=LOUDNESS_HOP)
    n = math.ceil(length / LOUDNESS_HOP - 1e-6) if length else len(values)
    n = max(1, n)
    out = [round(max(db, FLOOR_DB), 1) for _, db in values[:n]]
    return out + [FLOOR_DB] * (n - len(out))


def keep_loudness(run_dir: Path, video: Path, length: float | None) -> list[float] | None:
    """Work out the envelope of `video` and keep it in run_dir/loudness.json (written whole or
    not at all). Called when a live run publishes and when a run is forked."""
    try:
        values = envelope(video, length)
    except (media.MediaError, OSError, ValueError):
        log.warning("loudness of %s could not be worked out", video.name, exc_info=True)
        return None
    tmp = run_dir / f".loudness-{secrets.token_hex(4)}.tmp"
    try:
        tmp.write_text(json.dumps({"hop": LOUDNESS_HOP, "values": values}), encoding="utf-8")
        os.replace(tmp, run_dir / LOUDNESS)
    except OSError:
        tmp.unlink(missing_ok=True)
        log.warning("loudness.json could not be written in %s", run_dir.name, exc_info=True)
        return None
    return values


def read_loudness(run_dir: Path) -> list[float] | None:
    """The kept envelope, or None: a request never works one out."""
    values = _dict(read_json(run_dir / LOUDNESS)).get("values")
    if not isinstance(values, list):
        return None
    return [v for v in values if isinstance(v, int | float) and not isinstance(v, bool)]
