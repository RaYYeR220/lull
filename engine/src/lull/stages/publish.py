"""Publish: the files an institution ships, written only when the overlap gate passes.

descriptions.vtt      voiced standard cues (verified, edited or approved), end = start + audio
extended.vtt          voiced extended cues at their freeze points (speech-free, frame-aligned),
                      each after a `NOTE extended pause=<seconds>` line
described-audio.m4a   the programme ducked under the descriptions (see lull.stages.mix)
described.mp4         video + "Described" track 1 + "Original" track 2
extended.mp4          the extended version, when an extended cue is voiced (a failure to make
                      it is reported, not fatal)
cues.json             every cue, held ones included, for the Studio (audio paths relative to
                      the run directory)
script.md             the description script: timecode, status, text, claims, findings, note
report.html           WCAG status, coverage, overlap with speech, held cues, F113 flags,
                      reviewer warnings, research glossary, model calls and cost, TTS disclosure
keyframes/<id>.jpg    the frame at the middle of each cue's first evidence event
manifest.json         sha256 and size of every file above, the run summary and versions

The gate treats speech and everything outside the gaps' usable windows (speech guards,
protected sound) as off limits: a voiced standard cue that reaches into it, or into another
cue, stops publishing before any file is written. Every status in the report is computed here;
"met" is printed only when every published line was reviewed by a person, and reads "met
(automated checks)" until then.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel

import lull
from lull import media
from lull.ledger import Ledger
from lull.media import MediaError
from lull.stages import mix as mixer
from lull.stages.finalize import assert_no_overlap, overlap_report
from lull.stages.research import ResearchResult
from lull.stages.schedule import SALIENT
from lull.timeline import complement, merge_spans
from lull.types import Cue, Gap, RunOptions, Span, VisualEvent

log = logging.getLogger(__name__)

STANDARD_VTT = "descriptions.vtt"
EXTENDED_VTT = "extended.vtt"
CUES_JSON = "cues.json"
SCRIPT = "script.md"
REPORT = "report.html"
MANIFEST = "manifest.json"
KEYFRAMES = "keyframes"
F113_MIN_PAUSE = 2.0  # seconds of usable pause
REVIEWED = ("edited", "approved")  # statuses a person has signed off
STATUSES = ("verified", "edited", "approved", "held", "extended")  # counted in the summary
TTS_NAME = mixer.TTS_NAME
MET, MET_AUTOMATED = "met", "met (automated checks)"
REVIEW, NOT_CLAIMED = "needs review", "not claimed"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_TEMP_DIRS = (".mix-", ".extended-")
_EPS = 1e-9


class PublishResult(BaseModel):
    files: dict[str, Path]  # published name (keyframes/<id>.jpg included) -> path
    manifest: Path


# -- WebVTT ----------------------------------------------------------------------------------


def timecode(t: float) -> str:
    """HH:MM:SS.mmm"""
    ms = max(0, round(t * 1000))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _vtt_text(text: str) -> str:
    return _one_line(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _vtt_header(language: str) -> str:
    return f"WEBVTT\nKind: descriptions\nLanguage: {_one_line(language)}\n"


def _vtt_cue(c: Cue) -> str:
    return (
        f"{c.id}\n{timecode(c.start)} --> {timecode(c.start + c.audio_dur)}\n{_vtt_text(c.text)}\n"
    )


def to_vtt(cues: Iterable[Cue], *, language: str = "en") -> str:
    """The standard description track: voiced (verified, edited or approved) standard cues in
    time order; held and extended cues are left out. No cue gives a header-only file."""
    return "\n".join([_vtt_header(language), *(_vtt_cue(c) for c in mixer.voiced(cues))])


def to_extended_vtt(cues: Iterable[Cue], *, language: str = "en") -> str:
    """Voiced extended cues on the original timeline (publish moves each to its freeze
    point), each after a NOTE giving how long the programme pauses for it."""
    blocks = [
        f"NOTE extended pause={c.audio_dur:.3f}\n\n{_vtt_cue(c)}"
        for c in mixer.voiced_extended(cues)
    ]
    return "\n".join([_vtt_header(language), *blocks])


# -- what the report computes ------------------------------------------------------------------


def outside_pauses(gaps: Sequence[Gap], cues: Sequence[Cue]) -> list[Span]:
    """Everything outside the gaps' usable windows (speech, guards, protected sound), up to
    past the last gap or cue: a voiced standard cue must not touch it."""
    ends = [g.usable_end for g in gaps] + [c.start + (c.audio_dur or 0.0) for c in cues]
    end = max(ends, default=0.0) + 1.0
    windows = [Span(start=g.usable_start, end=g.usable_end) for g in gaps]
    return complement(windows, 0.0, end)


def coverage(
    cues: Sequence[Cue], extended: Sequence[Cue], events: Sequence[VisualEvent], *,
    extended_produced: bool,
) -> dict:  # fmt: skip
    """Salient events (salience >= 4) described by a voiced standard cue, and also counting
    voiced extended cues when the extended version exists. Ratios are None without salient
    events."""
    ordered = sorted(events, key=lambda e: (e.t_start, e.id))
    salient = [e.id for e in ordered if e.salience >= SALIENT]
    std = {i for c in mixer.voiced(cues) for i in c.event_ids}
    ext = set()
    if extended_produced:
        ext = {i for c in mixer.voiced_extended(extended) for i in c.event_ids}
    described = [i for i in salient if i in std]
    with_ext = [i for i in salient if i in std or i in ext]

    def ratio(n: int) -> float | None:
        return round(n / len(salient), 3) if salient else None

    return {
        "salient_events": len(salient),
        "described": len(described),
        "ratio": ratio(len(described)),
        "described_with_extended": len(with_ext),
        "ratio_with_extended": ratio(len(with_ext)),
        "undescribed": [i for i in salient if i not in with_ext],
    }


def f113_flags(
    gaps: Sequence[Gap], cues: Sequence[Cue], events: Sequence[VisualEvent]
) -> list[dict]:
    """Pauses of >= 2 s usable time with no voiced cue while a salient (>= 4) event of any
    kind that no voiced standard cue describes is on screen (WCAG F113, a failure of 1.2.5).
    A held cue leaves its pause empty."""
    spoken = mixer.voiced(cues)
    used = {c.gap_id for c in spoken}
    described = {i for c in spoken for i in c.event_ids}
    candidates = sorted(
        (e for e in events if e.salience >= SALIENT and e.id not in described),
        key=lambda e: (e.t_start, e.id),
    )
    flags = []
    for g in sorted(gaps, key=lambda g: (g.usable_start, g.id)):
        if g.usable_end - g.usable_start < F113_MIN_PAUSE - _EPS or g.id in used:
            continue
        evs = [e for e in candidates if e.t_start < g.usable_end and e.t_end > g.usable_start]
        if evs:
            flags.append({
                "gap_id": g.id,
                "start": g.usable_start,
                "end": g.usable_end,
                "seconds": round(g.usable_end - g.usable_start, 3),
                "event_ids": [e.id for e in evs],
                "events": [{"id": e.id, "kind": e.kind, "text": e.text} for e in evs],
            })  # fmt: skip
    return flags


def reviewed(cues: Sequence[Cue]) -> bool:
    """True when at least one line is published and every published (voiced) line was
    approved or edited by a person."""
    cues = list(cues)
    published = mixer.voiced(cues) + mixer.voiced_extended(cues)
    return bool(published) and all(c.status in REVIEWED for c in published)


def _n(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def wcag_status(
    *, standard: int, extended_produced: bool, held: int, overlap_s: float, f113: int,
    reviewed: bool = False, extended_error: str | None = None,
) -> list[dict]:  # fmt: skip
    """1.2.3, 1.2.5 and 1.2.7 as met / met (automated checks) / needs review / not claimed,
    with what meets them and why not.

    1.2.5 passes when a described version exists (standard track G78/G173, or the extended
    version G8) and nothing overlaps, no F113 pause is left and no cue is held; every
    technique used for it also meets 1.2.3. 1.2.7 is claimed only for extended.mp4, on the
    same checks (a held cue or an unused pause is missing from it too). A pass reads "met"
    only when every published line was reviewed by a person.
    """
    blockers = []
    if standard == 0 and not extended_produced:
        blockers.append(
            "no description is voiced in the standard track and no extended version was produced"
        )
    if overlap_s > 0:
        blockers.append(f"descriptions overlap speech or each other for {overlap_s:.3f} s")
    if f113:
        blockers.append(
            f"{_n(f113, 'pause')} of 2 s or more left unused while important visual content "
            "goes undescribed (F113)"
        )
    if held:
        blockers.append(f"{_n(held, 'description')} held for review")
    passed = MET if reviewed else MET_AUTOMATED
    status = passed if not blockers else REVIEW
    ad = []
    if standard:
        ad.append("described.mp4 and described-audio.m4a (G173 described version, "
                  "G78 selectable audio track)")  # fmt: skip
    if extended_produced:
        ad.append("extended.mp4 (G8 extended audio description)")
    texts = (["descriptions.vtt"] if standard else []) + (
        ["extended.vtt"] if extended_produced else []
    )
    via_123 = ", ".join([*texts, "script.md"]) + (f", with {' and '.join(ad)}" if ad else "")
    if extended_produced:
        row_127 = (status, "extended.mp4 (G8 extended audio description) and extended.vtt",
                   list(blockers))  # fmt: skip
    elif extended_error:
        row_127 = (NOT_CLAIMED, "", [f"extended version not produced: {extended_error}"])
    else:
        row_127 = (NOT_CLAIMED, "", ["extended version not produced"])
    return [
        {"criterion": "1.2.3", "level": "A",
         "name": "Audio Description or Media Alternative (Prerecorded)",
         "status": status, "via": via_123, "reasons": list(blockers)},
        {"criterion": "1.2.5", "level": "AA", "name": "Audio Description (Prerecorded)",
         "status": status, "via": " and ".join(ad), "reasons": list(blockers)},
        {"criterion": "1.2.7", "level": "AAA", "name": "Extended Audio Description (Prerecorded)",
         "status": row_127[0], "via": row_127[1], "reasons": row_127[2]},
    ]  # fmt: skip


def tts_disclosure(cues: Sequence[Cue]) -> str:
    """The synthetic-speech disclosure (ACB): "reviewed" only when every published line was
    approved or edited by a person."""
    cues = list(cues)
    if not (mixer.voiced(cues) or mixer.voiced_extended(cues)):
        return "No description was voiced in this run."
    tail = (
        "every line was reviewed before publishing."
        if reviewed(cues)
        else "lines are awaiting review."
    )
    return f"Descriptions are voiced with synthetic speech ({TTS_NAME}); {tail}"


# -- script --------------------------------------------------------------------------------------


def _script_entry(c: Cue) -> list[str]:
    when = timecode(c.start)
    if c.audio_dur is not None and c.status != "held":
        when += f" to {timecode(c.start + c.audio_dur)}"
    lines = [f"- {when} · {c.id} · {c.status}", f"  - Text: {_one_line(c.text)}"]
    if c.claims:
        claims = "; ".join(
            f"{_one_line(k.text)} — {k.verdict.replace('_', ' ')}"
            + (f" at {timecode(k.at)}" if k.at is not None else "")
            for k in c.claims
        )
        lines.append(f"  - Claims: {claims}")
    if c.findings:
        found = "; ".join(
            f"{f.get('rule')} ({f.get('severity', 'error')}): {_one_line(f.get('message', ''))}"
            for f in c.findings
        )
        lines.append(f"  - Findings: {found}")
    if c.note:
        lines.append(f"  - Note: {_one_line(c.note)}")
    return lines


def _entries(cues: Sequence[Cue], empty: str) -> list[str]:
    ordered = sorted(cues, key=lambda c: (c.start, c.id))
    return [line for c in ordered for line in _script_entry(c)] or [empty]


def script_md(video_name: str, cues: Sequence[Cue], extended: Sequence[Cue], opts: RunOptions,
              disclosure: str) -> str:  # fmt: skip
    out = [
        f"# Audio description script: {video_name}",
        "",
        f"- Language: {opts.language}",
        f"- Voice: {opts.voice} ({TTS_NAME})",
        f"- {disclosure}",
        "",
        "## Standard descriptions",
        "",
        *_entries(cues, "No standard description."),
        "",
        "## Extended descriptions (the video pauses for these)",
        "",
        *_entries(extended, "No extended description."),
    ]
    return "\n".join(out) + "\n"


# -- files ---------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("utf-8"))  # LF on every platform
    return path


def _clear_stale(run_dir: Path) -> None:
    """Outputs a rerun may not write again (extended.mp4, keyframes) and temp dirs a crashed
    run left behind; the other outputs are always overwritten."""
    (run_dir / mixer.EXTENDED_VIDEO).unlink(missing_ok=True)
    shutil.rmtree(run_dir / KEYFRAMES, ignore_errors=True)
    for p in run_dir.iterdir():
        if p.is_dir() and p.name.startswith(_TEMP_DIRS):
            shutil.rmtree(p, ignore_errors=True)


def _keyframes(
    video: Path, cues: Sequence[Cue], events: Sequence[VisualEvent], run_dir: Path,
    duration: float,
) -> dict[str, Path]:  # fmt: skip
    by_id = {e.id: e for e in events}
    last = max(0.0, duration - 0.1)
    shots = []
    for c in sorted(cues, key=lambda c: (c.start, c.id)):
        ev = by_id.get(c.event_ids[0]) if c.event_ids else None
        t = (ev.t_start + ev.t_end) / 2 if ev is not None else c.start
        shots.append((min(max(t, 0.0), last), run_dir / KEYFRAMES / f"{c.id}.jpg"))
    if not shots:
        return {}
    (run_dir / KEYFRAMES).mkdir(parents=True, exist_ok=True)
    media.frames_at(video, shots)
    return {f"{KEYFRAMES}/{dst.name}": dst for _, dst in shots}


def _by_time(cues: Iterable[Cue]) -> list[Cue]:
    return sorted(cues, key=lambda c: (c.start, c.id))


def _cue_json(c: Cue, run_dir: Path) -> dict:
    data = c.model_dump(mode="json")
    if c.audio_path is not None:
        try:
            rel = os.path.relpath(Path(c.audio_path).resolve(), run_dir.resolve())
            data["audio_path"] = Path(rel).as_posix()
        except ValueError:  # another drive: keep it absolute
            data["audio_path"] = Path(c.audio_path).as_posix()
    return data


def _duration(seconds: float) -> str:
    s = round(seconds)
    h, rest = divmod(s, 3600)
    return f"{h}:{rest // 60:02d}:{rest % 60:02d}" if h else f"{rest // 60}:{rest % 60:02d}"


def _render_report(context: dict) -> str:
    env = Environment(autoescape=True, undefined=StrictUndefined, trim_blocks=True,
                      lstrip_blocks=True)  # fmt: skip
    source = (files("lull") / "templates" / "report.html").read_text(encoding="utf-8")
    return env.from_string(source).render(**context)


def _check_ids(cues: Sequence[Cue]) -> None:
    seen: set[str] = set()
    for c in cues:
        if not _SAFE_ID.match(c.id):
            raise ValueError(f"publish: cue id {c.id!r} is not a safe file name")
        if c.id in seen:
            raise ValueError(f"publish: duplicate cue id {c.id!r}")
        seen.add(c.id)


# -- publish -------------------------------------------------------------------------------------


def publish(
    run_dir: Path,
    video: Path,
    cues: list[Cue],
    extended: list[Cue],
    events: list[VisualEvent],
    gaps: list[Gap],
    research: ResearchResult | None,
    ledger: Ledger,
    run_id: str,
    opts: RunOptions,
    *,
    speech: list[Span],
) -> PublishResult:
    """Write every output into `run_dir` (see the module doc). Raises OverlapError, writing
    nothing, when a voiced standard cue overlaps speech, a guard, protected sound or another
    cue. `speech` is the listen stage's speech spans."""
    run_dir, video = Path(run_dir), Path(video)
    cues, extended, speech = list(cues), list(extended), list(speech)
    everything = cues + extended
    _check_ids(everything)
    outside = outside_pauses(gaps, cues)
    off_limits = merge_spans(outside + speech)
    assert_no_overlap(cues, off_limits)
    gate = overlap_report(cues, off_limits)
    talk = overlap_report(cues, speech)
    overlap = {
        "speech_s": talk["speech_s"],
        "between_cues_s": talk["between_cues_s"],
        "outside_pauses_s": overlap_report(cues, outside)["speech_s"],
        "total_s": gate["total_s"],
    }
    spoken, spoken_ext = mixer.voiced(cues), mixer.voiced_extended(extended)

    info = media.probe(video)
    run_dir.mkdir(parents=True, exist_ok=True)
    _clear_stale(run_dir)
    out: dict[str, Path] = {}
    levels = mixer.measure_levels(video, info, spoken + spoken_ext)
    mixed = mixer.mix(video, cues, run_dir, info=info, language=opts.language, levels=levels)
    out[mixer.AD_AUDIO], out[mixer.AD_VIDEO] = mixed.ad_audio, mixed.ad_video
    ext_error = None
    try:
        ext_video = mixer.extended_video(
            video, extended, run_dir, standard=cues, speech=speech, info=info,
            language=opts.language, levels=levels,
        )  # fmt: skip
    except MediaError as e:
        log.warning("publish: extended version failed, publishing without it: %s", e)
        (run_dir / mixer.EXTENDED_VIDEO).unlink(missing_ok=True)
        ext_video, ext_error = None, _one_line(e)[:300]
    if ext_video is not None:
        out[mixer.EXTENDED_VIDEO] = ext_video
    out.update(_keyframes(video, everything, events, run_dir, info.duration))

    fps, freezes = mixer.freeze_points(spoken_ext, info, standard=spoken, speech=speech)
    freeze_at = {c.id: float(f.frame / fps) for f in freezes for c in f.cues}
    extended_vtt = [
        c.model_copy(update={"start": freeze_at[c.id]}) if c.id in freeze_at else c
        for c in extended
    ]

    counts = Counter(c.status for c in everything)
    held = [c for c in sorted(everything, key=lambda c: (c.start, c.id)) if c.status == "held"]
    flags = f113_flags(gaps, cues, events)
    cov = coverage(cues, extended, events, extended_produced=ext_video is not None)
    signed_off = reviewed(everything)
    wcag = wcag_status(
        standard=len(spoken), extended_produced=ext_video is not None, held=len(held),
        overlap_s=overlap["total_s"], f113=len(flags), reviewed=signed_off,
        extended_error=ext_error,
    )  # fmt: skip
    disclosure = tts_disclosure(everything)
    calls = ledger.summary(run_id)
    cost = round(calls["total_cost_usd"], 6)
    minutes = info.duration / 60
    per_min = round(cost / minutes, 6) if minutes > 0 else None
    warnings = [
        {"cue_id": c.id, "rule": f.get("rule"), "message": f.get("message"), "text": c.text}
        for c in sorted(everything, key=lambda c: (c.start, c.id))
        for f in c.findings
        if f.get("severity") == "warning"
    ]
    summary = {
        "cues": {s: counts.get(s, 0) for s in STATUSES},
        "coverage": cov,
        "overlap_seconds": overlap["total_s"],
        "overlap": overlap,
        "held": [{"id": c.id, "reason": c.note} for c in held],
        "f113": flags,
        "lint_warnings": [{k: w[k] for k in ("cue_id", "rule", "message")} for w in warnings],
        "cost_usd": cost,
        "cost_per_min": per_min,
        "calls_by_role_model": calls["by_role_model"],
        "wcag": wcag,
        "reviewed": signed_off,
        "extended_produced": ext_video is not None,
        "extended_error": ext_error,
        "levels": {"programme_lufs": levels.programme,
                   "cue_target_lufs": mixer.cue_target(levels.programme)},
        "tts_disclosure": disclosure,
        "research": None if research is None else {
            "skipped": research.skipped, "terms": len(research.glossary),
            "errors": research.errors,
        },
    }  # fmt: skip

    language = opts.language
    out[STANDARD_VTT] = _write(run_dir / STANDARD_VTT, to_vtt(cues, language=language))
    out[EXTENDED_VTT] = _write(
        run_dir / EXTENDED_VTT, to_extended_vtt(extended_vtt, language=language)
    )
    cue_data = {
        "run_id": run_id,
        "language": language,
        "cues": [_cue_json(c, run_dir) for c in _by_time(cues)],
        "extended": [_cue_json(c, run_dir) for c in _by_time(extended)],
    }
    out[CUES_JSON] = _write(
        run_dir / CUES_JSON, json.dumps(cue_data, indent=2, ensure_ascii=False) + "\n"
    )
    out[SCRIPT] = _write(run_dir / SCRIPT, script_md(video.name, cues, extended, opts, disclosure))
    created = datetime.now(UTC).isoformat(timespec="seconds")
    context = {
        "video": video.name, "duration": _duration(info.duration), "run_id": run_id,
        "created": created, "opts": opts, "wcag": wcag, "summary": summary,
        "standard_count": len(spoken), "extended_count": len(spoken_ext),
        "held": [{"id": c.id, "at": timecode(c.start), "text": c.text, "reason": c.note or ""}
                 for c in held],
        "flags": [{**f, "from": timecode(f["start"]), "to": timecode(f["end"])} for f in flags],
        "warnings": warnings, "research": research, "calls": calls["by_role_model"],
        "outputs": sorted(n for n in [*out, REPORT, MANIFEST] if not n.startswith(KEYFRAMES)),
        "keyframes": sum(n.startswith(KEYFRAMES) for n in out),
    }  # fmt: skip
    out[REPORT] = _write(run_dir / REPORT, _render_report(context))

    models = sorted({c.model for c in ledger.calls(run_id)})
    manifest = {
        "run_id": run_id,
        "created": created,
        "video": {
            "name": video.name,
            "sha256": _sha256(video),
            "duration": round(info.duration, 3),
        },
        "options": opts.model_dump(mode="json"),
        "outputs": {
            name: {"sha256": _sha256(path), "bytes": path.stat().st_size}
            for name, path in sorted(out.items())
        },
        "summary": summary,
        "versions": {"engine": lull.__version__, "models": models, "tts": TTS_NAME},
    }
    path = _write(run_dir / MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    log.info("publish: %d files in %s", len(out) + 1, run_dir)
    return PublishResult(files=out, manifest=path)
