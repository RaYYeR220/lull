"""The showcase export: a finished run folder as the small set of files the website plays.

    lull export-showcase RUN_DIR OUT_DIR [--id ID] [--title T] [--credit C]

OUT_DIR gets
  video.mp4            the original picture and sound (the "Original" track of described.mp4),
                       at most 480 lines, H.264 CRF 30 + AAC 96k stereo, index first
                       (faststart)
  described-audio.m4a  the described mix, as published
  descriptions.vtt     the standard description track, as published
  extended.vtt         the extended descriptions, as published
  audio/<cue id>.m4a   every published cue's own voice (AAC 64k mono)
  keyframes/           as published
  showcase.json        the data the page draws (SCHEMA below); every path in it is relative

showcase.json: {id, title, credit, duration, loudness (dBFS of the original audio per 100 ms,
floor -120, 1 decimal), speech [[start, end]...], gaps [{id, start, end, capacity}] (the
usable windows the scheduler filled), cues [{id, start, end, text, status, ahead, extended}]
(the published standard and extended cues; an extended cue starts at its freeze point; end =
start + its audio), words [{t, s, e}], audio {original, described}, cueAudioBase,
verifyExample {cueId, cue, claims [{text, verdict}]}, costUsd, summary}.

summary is what the report says about the run: counts (cues by status), coverage
{importantEvents, described, ratio, describedWithExtended, ratioWithExtended, undescribed},
repetitive [{id, text}] (salient events the plan judged repetitive), f113 [{gapId, start,
end, eventIds}], overlapSeconds, wcag [{criterion, level, name, status, blockers}], models,
spend {modelUsdThisRun, webSearchesThisRun, webSearchListUsdThisRun (notional, not charged),
modelUsdAllRunsOfRunId}. A run published before these were recorded gives empty values.

The verify example is the first published cue whose verification history shows a claim the
frames did not support (it was cut before the cue was voiced): its draft as first checked and
every verdict on it. Without one it is the first verified cue with its claims.

costUsd is the model spend of the attempt that published (summary.spend has every run under
the run id beside it). A reused OUT_DIR loses the cue audio and keyframes of an earlier
export.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
import tempfile
from pathlib import Path

from pydantic import ValidationError

from lull import media
from lull.stages import mix as mixer
from lull.store import STAGES_DIR, Store
from lull.types import Check, Cue

log = logging.getLogger(__name__)

SHOWCASE = "showcase.json"
VIDEO = "video.mp4"
AUDIO_DIR = "audio"
KEYFRAMES = "keyframes"
COPIED = (mixer.AD_AUDIO, "descriptions.vtt", "extended.vtt")
MAX_HEIGHT = 480
VIDEO_CRF = 30
VIDEO_AUDIO = "96k"
CUE_AUDIO = "64k"
HOP = 0.1  # seconds per loudness value
FLOOR_DB = -120.0


class ShowcaseError(ValueError):
    """The folder is not a finished run (a file the export needs is missing or unreadable)."""


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        raise ShowcaseError(
            f"{path.name} not found in {path.parent.name}: not a finished run"
        ) from None
    except (OSError, ValueError) as e:
        raise ShowcaseError(f"cannot read {path.name}: {e}") from e
    if not isinstance(data, dict):
        raise ShowcaseError(f"{path.name} is not a JSON object")
    return data


def _r(x: float) -> float:
    return round(float(x), 3)


def run_summary(manifest: dict) -> dict:
    """The showcase's `summary` (see the module doc) from the manifest publish wrote."""
    s = manifest.get("summary") or {}
    cov = s.get("coverage") or {}
    spend = s.get("spend") or {}
    return {
        "counts": dict(s.get("cues") or {}),
        "coverage": {
            "importantEvents": cov.get("important_events", cov.get("salient_events")),
            "described": cov.get("described"),
            "ratio": cov.get("ratio"),
            "describedWithExtended": cov.get("described_with_extended"),
            "ratioWithExtended": cov.get("ratio_with_extended"),
            "undescribed": list(cov.get("undescribed") or []),
        },
        "repetitive": [
            {"id": r.get("id"), "text": r.get("text")} for r in s.get("repetitive") or []
        ],  # fmt: skip
        "f113": [
            {
                "gapId": f.get("gap_id"),
                "start": f.get("start"),
                "end": f.get("end"),
                "eventIds": list(f.get("event_ids") or []),
            }
            for f in s.get("f113") or []
        ],  # fmt: skip
        "overlapSeconds": s.get("overlap_seconds"),
        "wcag": [
            {
                "criterion": w.get("criterion"),
                "level": w.get("level"),
                "name": w.get("name"),
                "status": w.get("status"),
                "blockers": list(w.get("reasons") or []),
            }
            for w in s.get("wcag") or []
        ],  # fmt: skip
        "models": list((manifest.get("versions") or {}).get("models") or []),
        "spend": {
            "modelUsdThisRun": spend.get("model_usd_this_run", s.get("cost_usd")),
            "webSearchesThisRun": spend.get("tavily_searches_this_run"),
            "webSearchListUsdThisRun": spend.get("tavily_notional_usd_this_run"),
            "modelUsdAllRunsOfRunId": spend.get("model_usd_all_runs"),
        },
    }


def _cues(entries: list[dict], voiced) -> list[tuple[Cue, dict]]:
    try:
        pairs = [(Cue.model_validate(e), e) for e in entries]
    except ValidationError as e:
        raise ShowcaseError(f"cues.json holds a cue that does not load: {e}") from e
    keep = {c.id for c in voiced([c for c, _ in pairs])}
    return [(c, e) for c, e in pairs if c.id in keep]


def _history(run_dir: Path, cue_id: str) -> list[Check]:
    """The verification rounds the finalize cache kept for a cue (none for older runs)."""
    path = Store(run_dir).path(f"finalize/{cue_id}")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_bytes().decode("utf-8")).get("data") or {}
        return [Check.model_validate(c) for c in data.get("checks") or []]
    except (OSError, ValueError, AttributeError) as e:
        log.warning("showcase: the finalize history of %s does not load: %s", cue_id, e)
        return []


def _claims(claims) -> list[dict]:
    return [{"text": k.text, "verdict": k.verdict} for k in claims]


def verify_example(run_dir: Path, published: list[Cue]) -> dict | None:
    """See the module doc: the first published cue (time order) whose history holds a claim
    that was not supported, as first drafted; else the first verified cue as published."""
    for c in published:
        for check in _history(run_dir, c.id):
            if any(k.verdict != "supported" for k in check.claims):
                return {"cueId": c.id, "cue": check.text, "claims": _claims(check.claims)}
    for c in published:
        if c.status == "verified" and c.claims:
            return {"cueId": c.id, "cue": c.text, "claims": _claims(c.claims)}
    return None


def _loudness(wav: Path | None, duration: float) -> list[float]:
    """One value per HOP of the video's duration (the encoder's padding is cut off)."""
    n = max(1, math.ceil(duration / HOP - 1e-6))
    values = [] if wav is None else media.loudness_profile(wav, hop=HOP)
    out = [round(max(db, FLOOR_DB), 1) for _, db in values[:n]]
    return out + [FLOOR_DB] * (n - len(out))


def _ffmpeg(*args: str) -> None:
    media._run([media._bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args])


def _video(src: Path, info: media.Probe, dst: Path) -> bool:
    """The original picture and sound at most MAX_HEIGHT lines; True when it has sound."""
    original = 1 if info.audio_streams >= 2 else None  # track 2 "Original"; none: no sound
    sound = ["-map", "0:a:1", "-c:a", "aac", "-b:a", VIDEO_AUDIO, "-ac", "2"] if original else []
    height = f"trunc(min({MAX_HEIGHT},ih)/2)*2"
    _ffmpeg(
        *media.input_args(src), "-map", "0:v:0", *sound,
        "-vf", f"scale=-2:'{height}'", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", str(VIDEO_CRF), "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst),
    )  # fmt: skip
    return original is not None


def export_showcase(
    run_dir: Path, out_dir: Path, *, id: str | None = None, title: str | None = None,
    credit: str = "",
) -> Path:  # fmt: skip
    """Write the showcase files for the finished run in `run_dir` into `out_dir` (see the
    module doc) and return the path of showcase.json. Raises ShowcaseError when `run_dir` is
    not a finished run or `out_dir` is the run folder, inside it or contains it (checked
    before anything is written or removed), MediaError when its media cannot be read."""
    run_dir, out_dir = Path(run_dir).absolute(), Path(out_dir).absolute()
    out_r, run_r = out_dir.resolve(), run_dir.resolve()
    if out_r == run_r or run_r in out_r.parents or out_r in run_r.parents:
        # inside it, it would land in the run; around it, clearing audio/ could reach in
        raise ShowcaseError(
            "the output folder is the run folder, inside it or around it: export somewhere else"
        )
    manifest = _json(run_dir / "manifest.json")
    published = _json(run_dir / "cues.json")
    listen = _json(run_dir / STAGES_DIR / "listen.json").get("data") or {}
    described = run_dir / mixer.AD_VIDEO
    for name in (mixer.AD_VIDEO, *COPIED):
        if not (run_dir / name).exists():
            raise ShowcaseError(f"{name} not found in {run_dir.name}: not a finished run")
    info = media.probe(described)
    media.assert_safe_container(info)
    video_name = (manifest.get("video") or {}).get("name")
    duration = float((manifest.get("video") or {}).get("duration") or info.duration)

    standard = _cues(published.get("cues") or [], mixer.voiced)
    extended = _cues(published.get("extended") or [], mixer.voiced_extended)
    rows = []
    for c, entry in standard + extended:
        start = c.start if c.gap_id is not None else float(entry.get("freeze_at", c.start))
        rows.append((start, c))
    rows.sort(key=lambda r: (r[0], r[1].id))

    out_dir.mkdir(parents=True, exist_ok=True)
    for folder in (AUDIO_DIR, KEYFRAMES):  # nothing from an earlier export lingers
        shutil.rmtree(out_dir / folder, ignore_errors=True)
    (out_dir / AUDIO_DIR).mkdir()
    video = out_dir / VIDEO
    has_sound = _video(described, info, video)
    with tempfile.TemporaryDirectory(prefix="lull-showcase-") as tmp:
        wav = media.extract_wav(video, Path(tmp) / "original.wav") if has_sound else None
        loudness = _loudness(wav, duration)
    for name in COPIED:
        shutil.copyfile(run_dir / name, out_dir / name)
    if (run_dir / KEYFRAMES).is_dir():
        shutil.copytree(run_dir / KEYFRAMES, out_dir / KEYFRAMES)
    for _, c in rows:
        src = Path(c.audio_path)
        src = src if src.is_absolute() else run_dir / src
        if not src.exists():
            raise ShowcaseError(f"the audio of cue {c.id} is missing ({Path(c.audio_path).name})")
        _ffmpeg(*media.wav_input_args(src), "-vn", "-ac", "1", "-c:a", "aac", "-b:a", CUE_AUDIO,
                str(out_dir / AUDIO_DIR / f"{c.id}.m4a"))  # fmt: skip

    data = {
        "id": id or run_dir.name,
        "title": title or Path(str(video_name or run_dir.name)).stem,
        "credit": credit,
        "duration": _r(duration),
        "loudness": loudness,
        "speech": [[_r(s["start"]), _r(s["end"])] for s in published.get("speech") or []],
        "gaps": [
            {
                "id": g["id"],
                "start": _r(g["usable_start"]),
                "end": _r(g["usable_end"]),
                "capacity": int(g["capacity_words"]),
            }
            for g in published.get("gaps") or []
        ],  # fmt: skip
        "cues": [
            {
                "id": c.id,
                "start": _r(start),
                "end": _r(start + c.audio_dur),
                "text": c.text,
                "status": c.status,
                "ahead": c.ahead,
                "extended": c.gap_id is None,  # the picture holds while it plays
            }
            for start, c in rows
        ],  # fmt: skip
        "words": [
            {"t": w["text"], "s": _r(w["start"]), "e": _r(w["end"])}
            for w in listen.get("words") or []
        ],
        "audio": {"original": VIDEO, "described": mixer.AD_AUDIO},
        "cueAudioBase": f"{AUDIO_DIR}/",
        "verifyExample": verify_example(run_dir, [c for _, c in rows]),
        "costUsd": round(float((manifest.get("summary") or {}).get("cost_usd") or 0.0), 6),
        "summary": run_summary(manifest),
    }
    path = out_dir / SHOWCASE
    path.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    log.info("showcase: %d cues from %s into %s", len(rows), run_dir.name, out_dir)
    return path
