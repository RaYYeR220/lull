"""Publish: the files an institution ships, written only when the overlap gate passes.

descriptions.vtt      voiced standard cues (verified, edited or approved), end = start + audio
extended.vtt          voiced extended cues at their freeze points (the first sentence boundary
                      after the visual, frame-aligned), each after a `NOTE extended
                      pause=<seconds>` line
described-audio.m4a   the programme ducked under the descriptions (see lull.stages.mix)
described.mp4         video + "Described" track 1 + "Original" track 2
extended.mp4          the extended version, when an extended cue is voiced (a failure to make
                      it is reported, not fatal)
cues.json             every cue, held ones included, for the Studio (audio paths relative to
                      the run directory; extended cues with their freeze point), the pauses
                      (the windows the scheduler filled) and the speech spans
script.md             the description script: timecode, status, text, claims, findings, note
report.html           WCAG status, coverage, overlap with speech, held cues, F113 flags,
                      reviewer warnings, research glossary, model calls and cost, TTS disclosure
keyframes/<id>.jpg    the frame at the middle of each cue's first evidence event
manifest.json         sha256 and size of every file above, the run summary and versions

The gate treats speech and everything outside the gaps' usable windows (speech guards,
protected sound) as off limits: a voiced standard cue that reaches into it, or into another
cue, stops publishing before any file is written. Publishing is all or nothing: every output
is written into a staging folder (`.publish-*`) inside the run directory; only once the
manifest is written too do the old outputs move into a backup folder (`.publish-old-*`) and
the new ones move in, and the backup is deleted only after the last move. A failure at any
point, moves included, puts the previous outputs back as they were. One publish runs per run
directory at a time: it holds `.publish.lock` (created exclusively, holding its pid, host and
a random token) and touches it every LOCK_HEARTBEAT_S while it works; the heartbeat and the
release act only while the file still holds its token. Another publish waits for it, and
breaks it only when it has gone quiet (no heartbeat for LOCK_QUIET_S) and either its
publisher no longer runs on this host or it is older than LOCK_STALE_S (a publisher that
hung, or one on another host); one breaker at a time sets it aside, judges it there and
removes it (see _break). Every status in the report is computed here;
"met" is printed only when every published line was reviewed by a person, and reads "met
(automated checks)" until then.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import socket
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
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
from lull.stages.plan import said_already
from lull.stages.research import ResearchResult
from lull.stages.schedule import SALIENT
from lull.timeline import complement, merge_spans
from lull.types import Cue, Gap, RunOptions, Span, VisualEvent, Word

log = logging.getLogger(__name__)

STANDARD_VTT = "descriptions.vtt"
EXTENDED_VTT = "extended.vtt"
CUES_JSON = "cues.json"
SCRIPT = "script.md"
REPORT = "report.html"
MANIFEST = "manifest.json"
KEYFRAMES = "keyframes"
F113_MIN_PAUSE = 2.0  # seconds of usable pause
MIN_COVERAGE = 0.8  # important events described, for 1.2.5 / 1.2.7 to be met
IMPORTANT_PRIORITY = 3  # the plan rates an event this high: a description is owed
MAX_DEMOTED = 0.25  # share of the watch-salient events the plan may judge repetitive
MET_NOTHING_VISUAL = "met (no visual information beyond the soundtrack)"  # G203
REVIEWED = ("edited", "approved")  # statuses a person has signed off
STATUSES = ("verified", "edited", "approved", "held", "extended", "unverified")  # counted
TTS_NAME = mixer.TTS_NAME
MET, MET_AUTOMATED = "met", "met (automated checks)"
REVIEW, NOT_CLAIMED = "needs review", "not claimed"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_LOCAL_PATH = re.compile(  # C:\dir\file, \\host\share\file, /dir/file -> file
    r"(?:\b[A-Za-z]:[\\/]|\\\\|(?<![\w.])/)(?:[^\s;,'\"<>|:\\/]+[\\/])+"
    r"([^\s;,'\"<>|:\\/]*)"
)
STAGING = ".publish-"
BACKUP = ".publish-old-"  # the previous outputs while the new ones move in
LOCK = ".publish.lock"
LOCK_HEARTBEAT_S = 60.0  # a publish touches its lock this often
LOCK_QUIET_S = 150.0  # no touch for this long: the lock is stale ...
LOCK_STALE_S = 600.0  # ... and breakable when its publisher is gone, or it is this old
LOCK_POLL_S = 0.2
LOCK_CREATE_TRIES = 50  # a lock file that cannot be created (or removed) this many times: stop
BREAKING_STALE_S = 30.0  # a breaker's marker this old was left by a crash
OWNER_RECHECK_S = 0.05  # a lock that cannot be read is looked at once more after this
_TEMP_DIRS = (".mix-", ".extended-", STAGING)
OUTPUTS = frozenset(
    {
        STANDARD_VTT,
        EXTENDED_VTT,
        CUES_JSON,
        SCRIPT,
        REPORT,
        KEYFRAMES,
        mixer.AD_AUDIO,
        mixer.AD_VIDEO,
        mixer.EXTENDED_VIDEO,
    }
)  # fmt: skip  (top-level names publish owns, besides the manifest)
_EPS = 1e-9


class PublishError(ValueError):
    """The run folder holds files publish did not write under the names it writes."""


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


def important(e: VisualEvent, priority: dict[str, int] | None = None) -> bool:
    """Whether a description is owed for `e`: the plan rates it priority 3
    or more; an event the plan gave no priority counts when the watch rated it salience 4 or
    more. Events the dialogue already conveys (R21) never count."""
    if said_already(e):
        return False
    if priority and e.id in priority:
        return priority[e.id] >= IMPORTANT_PRIORITY
    return e.salience >= SALIENT


def repetitive(events: Sequence[VisualEvent], priority: dict[str, int] | None) -> list[VisualEvent]:
    """Watch-salient events the plan rated 1-2, by start: judged repetitive by the planner,
    listed in the report and never counted as undescribed."""
    return [
        e
        for e in sorted(events, key=lambda e: (e.t_start, e.id))
        if priority and e.id in priority and e.salience >= SALIENT and not said_already(e)
        and priority[e.id] < IMPORTANT_PRIORITY
    ]  # fmt: skip


def _says(c: Cue) -> list[str]:
    """The events a cue's final text still describes (all of them for a cue finalised before
    this was recorded)."""
    return list(c.event_ids) if c.described is None else list(c.described)


def _in(eid: str, described: set[str], duplicates: dict[str, str]) -> bool:
    """`eid` is described, itself or through the twin the plan kept for it."""
    return eid in described or duplicates.get(eid) in described


def coverage(
    cues: Sequence[Cue], extended: Sequence[Cue], events: Sequence[VisualEvent], *,
    extended_produced: bool, duplicates: dict[str, str] | None = None,
    priority: dict[str, int] | None = None,
) -> dict:  # fmt: skip
    """Important events (see `important`: plan priority >= 3, else watch salience >= 4)
    described by a voiced standard cue (an event whose sentence a rewrite cut does not count,
    see Cue.described), and also counting voiced extended cues when the extended version
    exists. An event the plan dropped as a near-duplicate (`duplicates`: dropped -> kept) is
    described when its twin is. `repetitive` lists the watch-salient events the plan rated
    1-2. Ratios are None without important events; `salient_events` repeats
    `important_events` under its old name."""
    dup = duplicates or {}
    ordered = sorted(events, key=lambda e: (e.t_start, e.id))
    salient = [e.id for e in ordered if important(e, priority)]
    std = {i for c in mixer.voiced(cues) for i in _says(c)}
    ext = set()
    if extended_produced:
        ext = {i for c in mixer.voiced_extended(extended) for i in _says(c)}
    described = [i for i in salient if _in(i, std, dup)]
    with_ext = [i for i in salient if _in(i, std | ext, dup)]

    def ratio(n: int) -> float | None:
        return round(n / len(salient), 3) if salient else None

    return {
        "important_events": len(salient),
        "salient_events": len(salient),
        "described": len(described),
        "ratio": ratio(len(described)),
        "described_with_extended": len(with_ext),
        "ratio_with_extended": ratio(len(with_ext)),
        "undescribed": [i for i in salient if i not in with_ext],
        "repetitive": [e.id for e in repetitive(events, priority)],
        "watch_salient": sum(e.salience >= SALIENT and not said_already(e) for e in events),
    }


def f113_flags(
    gaps: Sequence[Gap], cues: Sequence[Cue], events: Sequence[VisualEvent], *,
    duplicates: dict[str, str] | None = None, priority: dict[str, int] | None = None,
) -> list[dict]:  # fmt: skip
    """Pauses of >= 2 s usable time with no voiced cue while an important event (see
    `important`) of any kind that no voiced standard cue describes is on screen (WCAG F113, a
    failure of 1.2.5). A held cue leaves its pause empty. Events the dialogue conveys (R21),
    events the plan judged repetitive and near-duplicates whose twin is described are not
    missing."""
    dup = duplicates or {}
    spoken = mixer.voiced(cues)
    used = {c.gap_id for c in spoken}
    described = {i for c in spoken for i in _says(c)}
    candidates = sorted(
        (e for e in events if important(e, priority) and not _in(e.id, described, dup)),
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


def _missing(cov: dict | None, key: str) -> list[str]:
    """Why coverage blocks a pass: fewer than MIN_COVERAGE of the important events described
    (`key`: "described" or "described_with_extended")."""
    total = (cov or {}).get("important_events", (cov or {}).get("salient_events"))
    if not total:
        return []
    done = cov[key]
    if done >= MIN_COVERAGE * total - _EPS:
        return []
    n = total - done
    return [f"{_n(n, 'important event')} undescribed ({done} of {total} described)"]


def _planner(cov: dict | None) -> list[str]:
    """Why the plan's ratings block a pass: it judged more than
    MAX_DEMOTED of the watch-salient events repetitive, or rated nothing important although the
    watch saw salient events."""
    if not cov or cov.get("watch_salient") is None:
        return []  # a summary from before these were recorded
    salient, demoted = cov["watch_salient"], len(cov.get("repetitive") or [])
    out = []
    if salient and demoted > MAX_DEMOTED * salient + _EPS:
        out.append(f"{demoted} of {salient} salient events judged repetitive by the planner "
                   f"(limit {MAX_DEMOTED * 100:.0f} %)")  # fmt: skip
    if salient and not cov.get("important_events"):
        out.append("no event was rated important")
    return out


def _nothing_visual(cov: dict | None) -> bool:
    """No important and no salient event at all: the soundtrack carries everything (G203)."""
    return bool(cov) and cov.get("watch_salient") == 0 and not cov.get("important_events")


def wcag_status(
    *, standard: int, extended_produced: bool, held: int, overlap_s: float, f113: int,
    reviewed: bool = False, extended_error: str | None = None, coverage: dict | None = None,
    verified: bool = True,
) -> list[dict]:  # fmt: skip
    """1.2.3, 1.2.5 and 1.2.7 as met / met (automated checks) / needs review / not claimed,
    with what meets them and why not.

    1.2.5 passes when a described version exists (standard track G78/G173, or the extended
    version G8) and nothing overlaps, no F113 pause is left, no cue is held and at least
    MIN_COVERAGE of the important events are described by the standard track (by the extended
    version when it is the only one); every technique used for it also meets 1.2.3, which does
    not depend on coverage. 1.2.7 is claimed only for extended.mp4, on the same checks with
    extended descriptions counted (a held cue or an unused pause is missing from it too). A
    pass reads "met" only when every published line was reviewed by a person.

    The plan's ratings cannot buy a pass: 1.2.5 and 1.2.7 need review when the plan judged
    more than MAX_DEMOTED of the watch-salient events repetitive, or rated nothing important
    while the watch saw salient events. With no important and no salient event at all (a
    talking head: the soundtrack holds all the information, G203) all three read
    MET_NOTHING_VISUAL unless another check fails.
    """
    nothing_visual = _nothing_visual(coverage)
    blockers = []
    if standard == 0 and not extended_produced and not nothing_visual:
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
    if not verified:  # the evaluation's ablation: nothing was checked against the frames
        blockers.append("claim verification was off for this run (LULL_NO_VERIFY)")
    passed = MET if reviewed else MET_AUTOMATED
    if nothing_visual:
        passed = MET_NOTHING_VISUAL
    status = passed if not blockers else REVIEW
    only_extended = standard == 0 and extended_produced
    planner = _planner(coverage)
    blockers_125 = (
        blockers
        + _missing(coverage, "described_with_extended" if only_extended else "described")
        + planner
    )
    blockers_127 = blockers + _missing(coverage, "described_with_extended") + planner
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
    if nothing_visual:
        g203 = "the soundtrack carries all the information (G203)"
        via_123 = f"{via_123}; {g203}"
        ad.append(g203)
    if nothing_visual and not extended_produced:
        row_127 = (passed if not blockers_127 else REVIEW, ad[-1], blockers_127)
    elif extended_produced:
        row_127 = (passed if not blockers_127 else REVIEW,
                   "extended.mp4 (G8 extended audio description) and extended.vtt",
                   blockers_127)  # fmt: skip
    elif extended_error:
        row_127 = (NOT_CLAIMED, "", [f"extended version not produced: {extended_error}"])
    else:
        row_127 = (NOT_CLAIMED, "", ["extended version not produced"])
    return [
        {"criterion": "1.2.3", "level": "A",
         "name": "Audio Description or Media Alternative (Prerecorded)",
         "status": status, "via": via_123, "reasons": list(blockers)},
        {"criterion": "1.2.5", "level": "AA", "name": "Audio Description (Prerecorded)",
         "status": passed if not blockers_125 else REVIEW, "via": " and ".join(ad),
         "reasons": blockers_125},
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


def _script_entry(c: Cue, visual: float | None = None) -> list[str]:
    when = timecode(c.start)
    if c.audio_dur is not None and c.status != "held":
        when += f" to {timecode(c.start + c.audio_dur)}"
    lines = [f"- {when} · {c.id} · {c.status}", f"  - Text: {_one_line(c.text)}"]
    if visual is not None and abs(visual - c.start) > 1e-6:
        lines.append(f"  - The picture holds here for a visual from {timecode(visual)}")
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


def _entries(cues: Sequence[Cue], empty: str, visual: dict[str, float] | None = None) -> list[str]:
    ordered = sorted(cues, key=lambda c: (c.start, c.id))
    seen = visual or {}
    return [line for c in ordered for line in _script_entry(c, seen.get(c.id))] or [empty]


def script_md(video_name: str, cues: Sequence[Cue], extended: Sequence[Cue], opts: RunOptions,
              disclosure: str, visual: dict[str, float] | None = None) -> str:  # fmt: skip
    """The description script. Extended cues come at the time the tracks give them (publish
    passes them moved to their freeze points), with `visual` the time each one's visual
    starts."""
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
        *_entries(extended, "No extended description.", visual),
    ]
    return "\n".join(out) + "\n"


# -- files ---------------------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def scrub_paths(text: str) -> str:
    """`text` with every local path cut to its file name: a report may travel."""
    return _LOCAL_PATH.sub(lambda m: m.group(1), text)


def _write(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("utf-8"))  # LF on every platform
    return path


def _alive(pid: int) -> bool:
    """Whether process `pid` runs on this host (never signals it)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # access denied: it exists
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _breakable(path: Path) -> bool:
    """A lock may be broken when it is quiet (not touched for LOCK_QUIET_S) and either the
    process it names is gone from this host or it is older than LOCK_STALE_S."""
    try:
        age = time.time() - path.stat().st_mtime
        owner = path.read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        return False  # released in between, or unreadable for now
    if age <= LOCK_QUIET_S:
        return False
    if age > LOCK_STALE_S:
        return True
    try:
        pid, host = int(owner[0]), owner[1]
    except (IndexError, ValueError):
        return False  # nobody to check: only the age can tell
    return host == socket.gethostname() and not _alive(pid)


def _owner(path: Path) -> str | None:
    """The token the lock file at `path` holds (None when it is gone or unreadable)."""
    try:
        fields = path.read_text(encoding="utf-8").split()
    except OSError:
        return None
    return fields[3] if len(fields) >= 4 else None


def _ours(path: Path, token: str) -> bool:
    """Whether the lock at `path` still holds `token`; a lock that cannot be read (a moment
    of not found) is read once more after OWNER_RECHECK_S before the answer is no."""
    owner = _owner(path)
    if owner is None:
        time.sleep(OWNER_RECHECK_S)
        owner = _owner(path)
    return owner == token


def _break(path: Path) -> bool:
    """Break the lock at `path` if it may be broken, and say whether this call did.

    One waiter breaks at a time: it must first create `<lock>.breaking` exclusively (two
    renames of one file can both report success on Windows, so the rename alone does not
    pick a winner; a marker older than BREAKING_STALE_S is a crashed breaker's and goes).
    The lock is then renamed aside to `<lock>.broken-<token>`, judged there and removed, so
    what is judged is exactly what is deleted; a lock that turns out fresh is put back unless
    a new one was taken meanwhile."""
    marker = path.with_name(path.name + ".breaking")
    try:
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        try:
            if time.time() - marker.stat().st_mtime > BREAKING_STALE_S:
                marker.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    try:
        aside = path.with_name(f"{path.name}.broken-{secrets.token_hex(8)}")
        try:
            os.rename(path, aside)
        except OSError:
            return False  # gone already: released, or broken just before
        if _breakable(aside):
            aside.unlink(missing_ok=True)
            return True
        try:
            os.link(aside, path)  # back where it was, never over a lock taken meanwhile
        except FileExistsError:
            log.warning("publish: a fresh lock was taken while %s was set aside", path.name)
        except OSError:
            try:
                os.rename(aside, path)
                return False
            except OSError:
                log.warning("publish: could not put the lock %s back", path.name)
        aside.unlink(missing_ok=True)
        return False
    finally:
        marker.unlink(missing_ok=True)


def _take(path: Path, token: str) -> bool:
    """Create the lock file holding this process's pid and host and `token`; False when it
    exists. A lock that cannot be written is not left behind."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        f = os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    try:
        with f:
            f.write(f"{os.getpid()} {socket.gethostname()} {time.time():.0f} {token}\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return True


@contextmanager
def _lock(run_dir: Path) -> Iterator[None]:
    """Hold run_dir/.publish.lock while publishing (see the module doc): wait while another
    publish holds it, break it only when _breakable says so (see _break), and touch it every
    LOCK_HEARTBEAT_S from a daemon thread while it is held. The lock carries a random token:
    the heartbeat touches it and the release removes it only while it still holds ours."""
    path = run_dir / LOCK
    token = secrets.token_hex(16)
    refused = 0
    while True:
        try:
            if _take(path, token):
                break
            refused = 0
        except (FileNotFoundError, PermissionError):
            # Windows, for a moment, cannot create a file whose name was just deleted
            refused += 1
            if refused >= LOCK_CREATE_TRIES or not run_dir.is_dir():
                raise
            time.sleep(LOCK_POLL_S)
            continue
        # judged in place first (cheap, moves nothing): a live, heartbeating lock is never
        # set aside; _break judges again, authoritatively, on the file it moved
        if path.exists() and _breakable(path) and _break(path):
            log.warning("publish: broke a stale lock in %s", run_dir)
            continue
        time.sleep(LOCK_POLL_S)
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(LOCK_HEARTBEAT_S):
            if not _ours(path, token):
                log.warning("publish: the lock in %s is no longer ours", run_dir)
                return
            try:
                os.utime(path, None)
            except OSError as e:
                log.warning("publish: could not touch the lock: %s", e)

    beat, started = None, False
    try:
        beat = threading.Thread(target=heartbeat, name="publish-lock", daemon=True)
        beat.start()
        started = True
        yield
    finally:
        stop.set()
        if started:
            beat.join(timeout=5)
        if _ours(path, token):
            _release(path)


def _release(path: Path) -> None:
    """Remove our lock file. Windows cannot delete a file another thread is reading at that
    moment (a waiter judging it), so a refusal is tried again a few times; a lock that still
    cannot go is logged, and waiters break it once it is stale."""
    for attempt in range(LOCK_CREATE_TRIES):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt == LOCK_CREATE_TRIES - 1:
                log.warning("publish: could not remove the lock %s", path)
                return
            time.sleep(OWNER_RECHECK_S)


_rename = os.replace  # one name to stand in for a filesystem in the tests


def _move(src: Path, dst: Path) -> None:
    """`src` renamed to `dst`. A filesystem that cannot rename a folder (a flat namespace
    such as Cloud Storage FUSE: EXDEV, EPERM, ENOTSUP) gets the folder's entries moved one by
    one into `dst`, and the emptied folder removed; a file that cannot be renamed raises as
    before, so the commit rolls back."""
    try:
        _rename(src, dst)
    except OSError:
        if not src.is_dir() or src.is_symlink():
            raise
        log.info("publish: cannot rename the folder %s; moving its files one by one", src.name)
        dst.mkdir(exist_ok=True)
        for item in sorted(src.iterdir()):
            _move(item, dst / item.name)
        src.rmdir()


def _replace_with(item: Path, dst: Path) -> None:
    if dst.is_dir():
        shutil.rmtree(dst)
    elif dst.exists():
        dst.unlink()
    _move(item, dst)


def _recover(run_dir: Path) -> None:
    """Undo what a publish that crashed while moving left behind (`.publish-old-*`):
    - no manifest in place: it stopped before its manifest moved in, so every backed-up output
      goes back, over whatever new one had moved in already;
    - the manifest in place and one in the backup: the commit finished and only removing the
      backup failed, so the backup just goes;
    - otherwise (it stopped while moving the old outputs aside): each backed-up output whose
      place is empty goes back.
    The backup folder is removed afterwards."""
    for backup in sorted(p for p in run_dir.iterdir() if p.is_dir() and p.name.startswith(BACKUP)):
        finished = (run_dir / MANIFEST).exists() and (backup / MANIFEST).exists()
        midway = not (run_dir / MANIFEST).exists()
        if midway and not (backup / MANIFEST).exists():
            # a first publish (nothing published before, and _guard let no foreign file stay
            # under an output name): every output-named entry is its own, half moved in
            for name in sorted(OUTPUTS):
                dst = run_dir / name
                if dst.is_dir():
                    shutil.rmtree(dst)
                elif dst.exists():
                    dst.unlink()
        if not finished:
            for item in sorted(backup.iterdir()):
                dst = run_dir / item.name
                if midway:
                    _replace_with(item, dst)
                elif not dst.exists():
                    _move(item, dst)
        log.warning("publish: an interrupted publish left %s (%s)", backup.name,
                    "removed" if finished else "its outputs were put back")  # fmt: skip
        shutil.rmtree(backup, ignore_errors=True)


def _guard(run_dir: Path, video: Path) -> None:
    """Raise PublishError rather than replace what publish did not write: the input video
    under an output name in the run folder, or output-named files in a folder no publish has
    written (no manifest)."""
    root = run_dir.resolve()
    try:
        top = video.resolve().relative_to(root).parts[0]
    except ValueError:
        top = None
    if top in OUTPUTS | {MANIFEST}:
        raise PublishError(
            f"the input video is {top} in the run folder, a name publish writes; move it "
            "out of the folder first"
        )
    if (run_dir / MANIFEST).exists():
        return
    foreign = sorted(name for name in OUTPUTS if (run_dir / name).exists())
    if foreign:
        raise PublishError(
            "the run folder holds files publish did not write: " + ", ".join(foreign)
            + "; move them or publish into another folder"
        )  # fmt: skip


def _clear_temp(run_dir: Path) -> None:
    """Temp and staging folders a crashed run left behind (only ever called under the lock,
    so no other publish is using them)."""
    for p in run_dir.iterdir():
        if p.is_dir() and p.name.startswith(_TEMP_DIRS) and not p.name.startswith(BACKUP):
            shutil.rmtree(p, ignore_errors=True)


def _previous(run_dir: Path) -> set[str]:
    """Top-level names the previous publish listed in its manifest (none when unreadable)."""
    try:
        outputs = json.loads((run_dir / MANIFEST).read_bytes().decode("utf-8"))["outputs"]
    except (OSError, ValueError, KeyError, TypeError):
        return set()
    return {str(name).split("/")[0] for name in outputs} & OUTPUTS


def _commit(staging: Path, run_dir: Path, names: Iterable[str]) -> None:
    """Swap the staged outputs in: every old output (what the previous manifest lists, any
    name publish owns, and the manifest) moves into a backup folder, then the new outputs move
    in (keyframes/ as a whole), the manifest last. A failure on any move moves the new ones
    back out and the old ones back in; the backup is deleted only after success."""
    tops = sorted({name.split("/")[0] for name in names})
    old = sorted(_previous(run_dir) | OUTPUTS | set(tops) | {MANIFEST})
    backup = run_dir / (BACKUP + staging.name[len(STAGING) :])
    backup.mkdir()
    saved: list[str] = []
    placed: list[str] = []
    try:
        for name in old:
            if (run_dir / name).exists():
                _move(run_dir / name, backup / name)
                saved.append(name)
        for name in [*tops, MANIFEST]:
            _move(staging / name, run_dir / name)
            placed.append(name)
    except BaseException:
        try:
            for name in reversed(placed):
                _move(run_dir / name, staging / name)
            for name in reversed(saved):
                _move(backup / name, run_dir / name)
        except OSError:
            log.exception("publish: could not undo the move; the old outputs are in %s", backup)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    shutil.rmtree(staging, ignore_errors=True)


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


def _cue_json(c: Cue, run_dir: Path, freeze_at: dict[str, float] | None = None) -> dict:
    data = c.model_dump(mode="json")
    if freeze_at and c.id in freeze_at:
        data["freeze_at"] = round(freeze_at[c.id], 6)
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
    duplicates: dict[str, str] | None = None,
    priority: dict[str, int] | None = None,
    since: float | None = None,
    words: list[Word] | None = None,
    reviewer: dict[str, list[str]] | None = None,
    verify: bool = True,
) -> PublishResult:
    """Write every output into `run_dir` (see the module doc), all or nothing. Raises
    OverlapError, writing nothing, when a voiced standard cue overlaps speech, a guard,
    protected sound or another cue; any other failure leaves the previous outputs untouched.
    `speech` is the listen stage's speech spans; `duplicates` the events the plan dropped as
    near-duplicates of another (dropped -> kept), counted as described with their twin;
    `priority` the plan's priorities, which decide what is important (see `important`);
    `since` when this attempt started (epoch seconds): its calls are the ones reported as this
    run's, next to the model spend of every attempt under the run id; `words` the transcript,
    whose sentence ends decide where the extended version stops (mix.hold_point); `reviewer`
    the event ids a person should look at: "plan_unanswered" (salient events the plan wrote
    nothing for, even when asked again) and "undescribed" (salient events that got no
    description anywhere), listed in the manifest and under "For the reviewer"; `verify`
    False records an evaluation run without claim verification (manifest `verification:
    "disabled"`, no WCAG row met)."""
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

    info = media.probe(video)
    media.assert_safe_container(info)  # never demux a playlist or a concat list
    run_dir.mkdir(parents=True, exist_ok=True)
    with _lock(run_dir):
        _recover(run_dir)
        _guard(run_dir, video)
        _clear_temp(run_dir)
        staging = Path(tempfile.mkdtemp(dir=run_dir, prefix=STAGING))
        try:
            out = _stage(
                staging, run_dir, video, info, cues, extended, events, gaps, research, ledger,
                run_id, opts, speech, duplicates, overlap, priority, since, words or [],
                reviewer or {}, verify,
            )  # fmt: skip
            _commit(staging, run_dir, out)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    files = {name: run_dir / name for name in out}
    log.info("publish: %d files in %s", len(files) + 1, run_dir)
    return PublishResult(files=files, manifest=run_dir / MANIFEST)


def _cached_spend(run_dir: Path, run_id: str) -> dict | None:
    """The spend, calls and models of the publish whose manifest is in `run_dir`, when it was
    for `run_id` (None otherwise, or when it cannot be read)."""
    try:
        old = json.loads((run_dir / MANIFEST).read_bytes().decode("utf-8"))
        summary = old["summary"]
        if old.get("run_id") != run_id or not isinstance(summary.get("spend"), dict):
            return None
        return {
            "spend": summary["spend"],
            "calls": {"by_role_model": list(summary.get("calls_by_role_model") or [])},
            "models": list((old.get("versions") or {}).get("models") or []),
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _continuous_note(c: Cue, words: list[Word], speech: list[Span]) -> Cue:
    """`c` with a note when its picture holds at a word boundary in continuous speech (no
    sentence end or gap between words within reach: see mix.hold)."""
    if mixer.hold(c.start, words=words, speech=speech)[1] != "word":
        return c
    note = mixer.CONTINUOUS_NOTE
    return c.model_copy(update={"note": f"{c.note}; {note}" if c.note else note})


def own_claims(c: Cue) -> Cue:
    """`c` with only the claims judged on its current text: none when the text changed after
    its last check (an edit), since every verdict belongs to the text it was given to. A cue
    without a check history keeps its claims."""
    if not c.claims or not c.checks:
        return c
    if " ".join(c.checks[-1].text.split()) == " ".join(c.text.split()):
        return c
    log.info("publish: %s changed after its last check; its %d claims are not published",
             c.id, len(c.claims))  # fmt: skip
    return c.model_copy(update={"claims": []})


def _stage(
    run_dir: Path, final: Path, video: Path, info: media.Probe, cues: list[Cue],
    extended: list[Cue], events: list[VisualEvent], gaps: list[Gap],
    research: ResearchResult | None, ledger: Ledger, run_id: str, opts: RunOptions,
    speech: list[Span], duplicates: dict[str, str] | None, overlap: dict,
    priority: dict[str, int] | None = None, since: float | None = None,
    words: list[Word] | None = None, reviewer: dict[str, list[str]] | None = None,
    verify: bool = True,
) -> dict[str, Path]:  # fmt: skip
    """Every output written into the staging folder `run_dir` (paths inside the outputs are
    relative to `final`, where they will live); returns the outputs by published name, the
    manifest left out."""
    cues, extended = [own_claims(c) for c in cues], [own_claims(c) for c in extended]
    # an unverified cue means verification was off, whatever the caller passes (a republish)
    verify = verify and not any(c.status == "unverified" for c in cues + extended)
    everything = cues + extended
    spoken, spoken_ext = mixer.voiced(cues), mixer.voiced_extended(extended)
    out: dict[str, Path] = {}
    levels = mixer.measure_levels(video, info, spoken + spoken_ext)
    mixed = mixer.mix(video, cues, run_dir, info=info, language=opts.language, levels=levels)
    out[mixer.AD_AUDIO], out[mixer.AD_VIDEO] = mixed.ad_audio, mixed.ad_video
    ext_error = None
    try:
        ext_video = mixer.extended_video(
            video, extended, run_dir, standard=cues, speech=speech, words=words or [], info=info,
            language=opts.language, levels=levels,
        )  # fmt: skip
    except MediaError as e:
        log.warning("publish: extended version failed, publishing without it: %s", e)
        (run_dir / mixer.EXTENDED_VIDEO).unlink(missing_ok=True)
        ext_video, ext_error = None, scrub_paths(_one_line(e))[:300]
    if ext_video is not None:
        out[mixer.EXTENDED_VIDEO] = ext_video
    out.update(_keyframes(video, everything, events, run_dir, info.duration))

    fps, freezes = mixer.freeze_points(
        spoken_ext, info, standard=spoken, speech=speech, words=words or []
    )
    freeze_at = {c.id: float(f.frame / fps) for f in freezes for c in f.cues}
    extended = [_continuous_note(c, words or [], speech) if c.id in freeze_at else c
                for c in extended]  # fmt: skip
    # one timeline for every output: a voiced extended cue sits at its freeze point in the
    # extended track, script.md and cues.json (freeze_at) alike
    placed = [
        c.model_copy(update={"start": freeze_at[c.id]}) if c.id in freeze_at else c
        for c in extended
    ]

    counts = Counter(c.status for c in everything)
    held = [c for c in sorted(everything, key=lambda c: (c.start, c.id)) if c.status == "held"]
    flags = f113_flags(gaps, cues, events, duplicates=duplicates, priority=priority)
    cov = coverage(cues, extended, events, extended_produced=ext_video is not None,
                   duplicates=duplicates, priority=priority)  # fmt: skip
    judged = [{"id": e.id, "text": e.text} for e in repetitive(events, priority)]
    signed_off = reviewed(everything)
    wcag = wcag_status(
        standard=len(spoken), extended_produced=ext_video is not None, held=len(held),
        overlap_s=overlap["total_s"], f113=len(flags), reviewed=signed_off,
        extended_error=ext_error, coverage=cov, verified=verify,
    )  # fmt: skip
    disclosure = tts_disclosure(everything)
    calls = ledger.summary(run_id, since=since)
    spend = {
        "model_usd_this_run": round(ledger.spent(run_id=run_id, since=since), 6),
        "tavily_searches_this_run": ledger.searches(run_id=run_id, since=since),
        "tavily_notional_usd_this_run": round(
            ledger.spent(run_id=run_id, since=since, tavily=True)
            - ledger.spent(run_id=run_id, since=since), 6
        ),
        "model_usd_all_runs": round(ledger.spent(run_id=run_id), 6),
    }  # fmt: skip
    models = sorted({c.model for c in ledger.calls(run_id)
                     if since is None or c.ts >= since})  # fmt: skip
    prior = _cached_spend(final, run_id) if since is not None and not models else None
    if prior is not None:  # every stage came from the cache: keep what made these outputs
        spend, calls, models = prior["spend"], prior["calls"], prior["models"]
    cost = spend["model_usd_this_run"]
    minutes = info.duration / 60
    per_min = round(cost / minutes, 6) if minutes > 0 else None
    warnings = [
        {"cue_id": c.id, "rule": f.get("rule"), "message": f.get("message"), "text": c.text}
        for c in sorted(everything, key=lambda c: (c.start, c.id))
        for f in c.findings
        if f.get("severity") == "warning"
    ]
    by_id = {e.id: e for e in events}
    for_reviewer = {
        kind: [{"id": i, "text": by_id[i].text if i in by_id else ""}
               for i in (reviewer or {}).get(kind, [])]
        for kind in ("plan_unanswered", "undescribed")
    }  # fmt: skip
    summary = {
        "cues": {s: counts.get(s, 0) for s in STATUSES},
        "for_reviewer": for_reviewer,
        "coverage": cov,
        "repetitive": judged,  # watch-salient, rated 1-2 by the plan: not counted
        "overlap_seconds": overlap["total_s"],
        "overlap": overlap,
        "held": [{"id": c.id, "reason": c.note} for c in held],
        "f113": flags,
        "lint_warnings": [{k: w[k] for k in ("cue_id", "rule", "message")} for w in warnings],
        "cost_usd": cost,  # model spend of this run
        "cost_per_min": per_min,
        "spend": spend,
        "spend_from_cache": prior is not None,  # this attempt made no call: the spend that
        # made these outputs, from the manifest of the publish before
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
            "errors": research.errors, "notes": research.notes,
        },
    }  # fmt: skip

    language = opts.language
    out[STANDARD_VTT] = _write(run_dir / STANDARD_VTT, to_vtt(cues, language=language))
    out[EXTENDED_VTT] = _write(run_dir / EXTENDED_VTT, to_extended_vtt(placed, language=language))
    cue_data = {
        "run_id": run_id,
        "language": language,
        "cues": [_cue_json(c, final) for c in _by_time(cues)],
        "extended": [_cue_json(c, final, freeze_at) for c in _by_time(extended)],
        "gaps": [g.model_dump(mode="json") for g in sorted(gaps, key=lambda g: g.usable_start)],
        "speech": [s.model_dump(mode="json") for s in merge_spans(speech)],
    }
    out[CUES_JSON] = _write(
        run_dir / CUES_JSON, json.dumps(cue_data, indent=2, ensure_ascii=False) + "\n"
    )
    visual = {c.id: c.start for c in extended if c.id in freeze_at}
    out[SCRIPT] = _write(
        run_dir / SCRIPT, script_md(video.name, cues, placed, opts, disclosure, visual)
    )
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
        "verification": "enabled" if verify else "disabled",
        "versions": {"engine": lull.__version__, "models": models, "tts": TTS_NAME},
    }
    _write(run_dir / MANIFEST, json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return out
