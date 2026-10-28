"""Studio: a describer edits, approves and holds the cues of a finished run, then publishes it
again. The overlap gate is never bypassed.

PATCH /v1/runs/{id}/cues/{cue_id} {text}   autofix + lint first: an error finding answers 422
    with the findings and no model is called. Otherwise the spend caps are checked (402) and the
    engine's finalize_cue re-checks the text as it does a planned one: verify against the cue's
    frames, voice, fit. The cue comes back "edited", or "held" with the reason in its note.
POST /v1/runs/{id}/cues/{cue_id}/approve   verified, edited or extended (a voiced extended cue)
    -> approved; anything else is 409. Approving an approved cue changes nothing.
POST /v1/runs/{id}/cues/{cue_id}/hold {reason?}   any cue -> held, the reason in its note.
POST /v1/runs/{id}/publish   the cues as they are now through assert_no_overlap and the engine's
    publish (mix included): a new manifest, or 409 with the offending cue ids and nothing written.
POST /v1/runs/{id}/fork   a published run (showcase or live) copied into a new live run, no
    model call: 201 {run_id, edit_token}. Anyone may fork; forks are rate limited.

Who may change a run. A live run takes changes only with its owner's edit token (Authorization:
Bearer, lull.api.owner), 403 forbidden otherwise; showcase runs are read-only (403) unless
LULL_SHOWCASE_EDITABLE=1. A run the live executor is describing right now is busy (409); other
runs can be edited meanwhile. Changes to one run go one at a time (a per-run asyncio.Lock,
waited on for at most LOCK_WAIT_S, then 409 busy); edits, approvals and holds are rate limited
per visitor, publishing and forking have limits of their own. Across all runs at most MAX_EDITS
edits are re-checked and MAX_PUBLISHES runs published at once (503 + Retry-After past that:
both keep the CPU busy). Publishing a run with no change since it was published does nothing
and answers the run.

Where edits live. cues.json is the Studio's working copy of a run's cues. Every change writes,
in this order: studio.json (dirty: the run differs from what was published; and the change as an
override, below), cues.json (atomically; audio paths relative to the run folder, as publish
writes them) and the cue's finalize stage record, stages/finalize/<cue id>.json, with its data
replaced and its key kept, so a rerun over the folder with the cache intact takes the person's
version without a model call. Publishing writes every output again from cues.json and sets dirty
false; the overrides stay.

The overlay. studio.json "overrides" keeps, per cue id, the person's last change: text, status,
note, take and check results; `base`, the hash of the engine's text it was first made on;
`authored`, whether the person wrote the text; the start it was made at; and for a held edit
`previous`, the last voiced version (its take stays on disk). After any engine run over the
folder (a resume through the API, before its `done` goes out; one outside the API, such as the
CLI, the next time the Studio loads the run) the overrides go back on top of the engine's cues
by these rules (apply_overlay): a cue id the engine no longer has leaves its override unused;
text the person wrote wins, keeping the engine's timing; an approval or hold of the engine's own
text holds only while that text is unchanged. Putting edits back marks the run dirty.

An edit is finalised in a folder of its own, work/edits/<cue id>-<token>/, so the take the
published mix used is never overwritten. A failed edit (an engine error, a spend cap) leaves
the cue and its take as they were and removes only its own folder. A held edit is stored (the
cue is held, the reason in its note) and keeps the take it would replace, as `previous`. A newer
voiced edit removes the takes it supersedes. Edit folders that neither cues.json nor
studio.json refers to (left by a crash mid-edit) are swept away by housekeeping.

Housekeeping (Studio.housekeep) runs at startup, hourly while the instance lives, and when
/v1/health finds it overdue (an instance that slept through its timer). It scans in a thread,
then removes, one run at a time under the run's lock and hidden from the routes, in a thread:
uploads older than LULL_UPLOAD_RETENTION_DAYS; forks past their expiry (a day after they were
made, and never past the upload they came from: a fork of an upload goes with it, its copy of
the original too); run folders still marked .pending an hour on (an upload or fork that never
finished) and folders with neither that mark nor a request.json an hour on (left over); sample
runs past the newest KEEP_SAMPLE_RUNS. Nothing the executor is describing,
a stopped run's thread is finishing, or a change holds is touched.

The engine is reached only through the adapters below (load_context, check_text, edit_context,
finalize_edit, republish), which rebuild what finalize and publish need from the run's stage
records the way lull.pipeline does.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import logging
import os
import secrets
import shutil
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from lull import media
from lull.api import facts
from lull.api.live import (
    Live,
    LiveError,
    budget_message,
    check_client_header,
    client_ip,
    new_run_id,
    wait_words,
)
from lull.api.owner import TOKEN_KEY, holds_token, new_token
from lull.api.ratelimit import DAY, HOUR, RateLimiter
from lull.api.settings import ApiSettings
from lull.api.spend import public_spend
from lull.api.store import (
    CUE_ID_RE,
    CUES,
    KEYFRAME_RE,
    MANIFEST,
    PENDING,
    STUDIO,
    RunStore,
    contained_file,
    cue_view,
    safe_rel,
)
from lull.ledger import BudgetExceeded, Ledger
from lull.pipeline import cue_context
from lull.speech import NemoSpeechHTTP, SpeechClient
from lull.stages import finalize as finalize_mod
from lull.stages import mix as mixer
from lull.stages.finalize import (
    CueContext,
    OverlapError,
    assert_no_overlap,
    finalize_cue,
    introduce_names,
    introduced_in,
)
from lull.stages.lint import Finding, autofix, lint
from lull.stages.listen import ListenResult
from lull.stages.plan import PlanResult
from lull.stages.publish import publish
from lull.stages.research import ResearchResult
from lull.store import Store
from lull.tf import TokenFactory
from lull.timeline import find_gaps, split_gaps
from lull.types import Cue, Gap, RunOptions, VisualEvent
from lull.voices import FALLBACK_WPS, voice_wps

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)

EDIT_ESTIMATE = 0.01  # USD the spend check reserves for one edit
EDITS_PER_MIN, EDITS_PER_VISITOR_DAY, EDITS_PER_DAY = 30, 200, 2000  # approvals, holds too
PUBLISHES_PER_MIN, PUBLISHES_PER_VISITOR_DAY, PUBLISHES_PER_DAY = 4, 50, 500
MAX_EDITS, MAX_PUBLISHES = 2, 1  # re-checked / published at once, across all runs
OVERLOADED_RETRY_S = 10
LOCK_WAIT_S = 2.0  # a change waits this long for another change to the same run, then 409
FORKS_PER_HOUR, FORKS_PER_VISITOR_DAY, FORKS_PER_DAY = 5, 10, 100  # each copies the media
FORK_TTL_S = 24 * 3600.0  # a fork is removed a day after it was made (or its upload's end)
HOUSEKEEP_EVERY_S = 3600.0
PENDING_TTL_S = 3600.0  # a folder still being filled after this long never will be
KEEP_SAMPLE_RUNS = 50  # visitors' runs of the samples, newest first
MAX_TEXT = 1000
MAX_REASON = 300
MAX_BODY = 64 * 1024
APPROVABLE = ("verified", "edited", "extended")
REQUEST, WORK, EDITS = "request.json", "work", "edits"
READ_ONLY = "Recorded runs are read-only. Fork one to edit a copy of your own."
FORBIDDEN = "Only the person who started this run can change it. Fork it to edit a copy."
DESCRIBING = "This run is still being described. Try again once it has finished."
NOT_READY = "This run has not been published yet, so there is nothing to edit."
OVERLAP = (
    "Nothing was published: these descriptions would overlap speech, the edge of their pause "
    "or each other."
)


# -- the run as finalize and publish need it ----------------------------------------------------


class NotEditable(Exception):
    """The run folder lacks what re-checking or publishing needs."""


class _Events(BaseModel):  # the watch stage record
    events: list[VisualEvent]


@dataclass(frozen=True)
class RunContext:
    run_id: str  # the engine's run id: ledger records and model calls
    run_dir: Path
    video: Path
    opts: RunOptions
    wps: float
    heard: ListenResult
    events: list[VisualEvent]
    gaps: list[Gap]
    research: ResearchResult | None
    planned: PlanResult
    cast: dict[str, str]
    named: set[str]  # names a listener must be introduced to: cast and glossary people
    proper: set[str]  # spellings to keep: cast and the whole glossary
    info: media.Probe | None  # the probed video (extended cues' freeze frames)
    reviewer: dict[str, list[str]]  # the plan's unanswered and undescribed events, by id

    def gap(self, gap_id: str | None) -> Gap | None:
        return next((g for g in self.gaps if g.id == gap_id), None)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _stage(run_dir: Path, name: str, model: type[M], *, required: bool = True) -> M | None:
    """A stage's last result, whatever its cache key (the inputs were the run's own)."""
    data = _dict(_read_json(Store(run_dir).path(name))).get("data")
    if not isinstance(data, dict):
        if required:
            raise NotEditable(f"its {name} record is missing")
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        raise NotEditable(f"its {name} record does not load") from None


def _options(manifest: dict, request: dict) -> RunOptions:
    for opts in (manifest.get("options"), request.get("options")):
        if isinstance(opts, dict):
            try:
                return RunOptions.model_validate(opts)
            except ValidationError:
                continue
    return RunOptions()


def _wps(voice: str) -> float:
    try:
        return voice_wps(voice)[0]
    except ValueError:  # voices.json does not load
        return FALLBACK_WPS


def source_video(run_dir: Path, manifest: dict, request: dict, samples_dir: Path) -> Path:
    """The video the run described (lull.api.facts.source_video), or NotEditable."""
    found = facts.source_video(run_dir, manifest, request, samples_dir)
    if found is None:
        raise NotEditable("its source video is not kept")
    return found


def load_context(run_dir: Path, samples_dir: Path) -> RunContext:
    manifest = _dict(_read_json(run_dir / MANIFEST))
    request = _dict(_read_json(run_dir / REQUEST))
    opts = _options(manifest, request)
    video = source_video(run_dir, manifest, request, samples_dir)
    heard = _stage(run_dir, "listen", ListenResult)
    events = _stage(run_dir, "watch", _Events).events
    planned = _stage(run_dir, "plan", PlanResult)
    research = _stage(run_dir, "research", ResearchResult, required=False)
    wps = _wps(opts.voice)
    glossary = research.glossary if research is not None else []
    spelled = {g.canonical for g in glossary if g.canonical.strip()}
    people = {g.canonical for g in glossary if g.kind == "person" and g.canonical.strip()}
    try:
        info: media.Probe | None = media.probe(video)
    except media.MediaError:
        info = None
    # the gaps as lull.pipeline works them out: from the speech, at this voice's rate
    gaps = find_gaps(heard.speech, heard.duration, protected=heard.protected, wps=wps)
    for_reviewer = _dict(_dict(manifest.get("summary")).get("for_reviewer"))
    reviewer = {
        kind: [e["id"] for e in for_reviewer.get(kind) or [] if isinstance(_dict(e).get("id"), str)]
        for kind in ("plan_unanswered", "undescribed")
    }
    run_id = manifest.get("run_id")
    return RunContext(
        run_id=run_id if isinstance(run_id, str) and run_id else run_dir.name,
        run_dir=run_dir,
        video=video,
        opts=opts,
        wps=wps,
        heard=heard.model_copy(update={"gaps": gaps}),
        events=events,
        gaps=split_gaps(gaps, wps=wps),
        research=research,
        planned=planned,
        cast=dict(planned.cast),
        named=set(planned.cast) | people,
        proper=set(planned.cast) | spelled,
        info=info,
        reviewer=reviewer,
    )


# -- cues.json and the stage records ------------------------------------------------------------


@dataclass
class CueDoc:
    """cues.json: the standard and extended cues (audio paths absolute while loaded)."""

    meta: dict
    cues: list[Cue]
    extended: list[Cue]

    def all(self) -> list[Cue]:
        return self.cues + self.extended

    def get(self, cue_id: str) -> Cue | None:
        return next((c for c in self.all() if c.id == cue_id), None)

    def replace(self, cue: Cue) -> None:
        for group in (self.cues, self.extended):
            for i, c in enumerate(group):
                if c.id == cue.id:
                    group[i] = cue
                    return
        raise KeyError(cue.id)


def _cue_in(run_dir: Path, data: Any) -> Cue:
    cue = Cue.model_validate(data)
    if cue.audio_path is not None and not Path(cue.audio_path).is_absolute():
        cue = cue.model_copy(update={"audio_path": str((run_dir / cue.audio_path).absolute())})
    return cue


def load_cues(run_dir: Path) -> CueDoc | None:
    raw = _read_json(run_dir / CUES)
    if not isinstance(raw, dict):
        return None
    groups = []
    for key in ("cues", "extended"):
        items = raw.get(key, [])
        if not isinstance(items, list):
            raise NotEditable("its cues do not load")
        try:
            groups.append([_cue_in(run_dir, c) for c in items])
        except ValidationError:
            raise NotEditable("its cues do not load") from None
    meta = {k: v for k, v in raw.items() if k not in ("cues", "extended")}
    return CueDoc(meta, *groups)


def cue_json(cue: Cue, run_dir: Path) -> dict:
    """A cue as cues.json keeps it: its audio path relative to the run folder."""
    data = cue.model_dump(mode="json")
    if cue.audio_path is not None:
        try:
            rel = os.path.relpath(Path(cue.audio_path).resolve(), run_dir.resolve())
            data["audio_path"] = Path(rel).as_posix()
        except ValueError:  # another drive: keep it absolute
            data["audio_path"] = Path(cue.audio_path).as_posix()
    return data


def _write_json(path: Path, obj: Any) -> None:
    """Written whole or not at all; a reader holding the file open (Windows) is waited for."""
    body = (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    tmp = path.with_name(f".{path.stem}-{secrets.token_hex(4)}.tmp")
    tmp.write_bytes(body)
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def mark(run_dir: Path, *, dirty: bool, overrides: dict[str, dict] | None = None) -> None:
    """studio.json: dirty or just published, and the overrides, with `overrides` merged in."""
    prev = _dict(_read_json(run_dir / STUDIO))
    if dirty:
        changes = prev.get("changes") if isinstance(prev.get("changes"), int) else 0
        state: dict[str, Any] = {"dirty": True, "changes": changes + 1, "updated": _now()}
        if isinstance(prev.get("published"), str):
            state["published"] = prev["published"]
    else:
        now = _now()
        state = {"dirty": False, "changes": 0, "updated": now, "published": now}
    merged = {**_dict(prev.get("overrides")), **(overrides or {})}
    if merged:
        state["overrides"] = merged
    _write_json(run_dir / STUDIO, state)


# -- the overlay: the person's changes, put back over any engine rerun --------------------------

OVERLAY_FIELDS = ("text", "status", "note", "claims", "findings", "audio_path", "audio_dur",
                  "tempo", "end")  # fmt: skip
STATUS_FIELDS = ("status", "note")  # what an approval or a hold changes
MOVED: dict[str, Any] = {  # the person's text on a cue that is not where it was checked
    "status": "held", "note": "moved since it was checked", "claims": [], "findings": [],
    "audio_path": None, "audio_dur": None, "end": None, "tempo": 1.0,
}  # fmt: skip


def refreshed(cue: Cue) -> Cue:
    """`cue` with its coverage data (`described`) worked out again by the engine after its
    text changed, so coverage and F113 never count words that are gone."""
    return finalize_mod.refresh_described(cue)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def overrides_of(run_dir: Path) -> dict[str, dict]:
    raw = _dict(_dict(_read_json(run_dir / STUDIO)).get("overrides"))
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def _voiced(cue: Cue) -> bool:
    return cue.audio_path is not None and bool(cue.audio_dur) and Path(cue.audio_path).is_file()


def override(run_dir: Path, old: Cue, new: Cue, prior: dict | None, *, authored: bool) -> dict:
    """The overlay entry for a change from `old` to `new`; `prior` is the cue's entry so far.
    `base` is the hash of the engine's text the first change was made on. A held edit keeps
    the last voiced version as `previous` (its take stays on disk)."""
    prior = prior or {}
    data = cue_json(new, run_dir)
    entry: dict[str, Any] = {k: data[k] for k in OVERLAY_FIELDS}
    entry.update(
        base=prior.get("base") or text_hash(old.text),
        authored=bool(authored or prior.get("authored")),
        start=new.start,  # where it was checked: the slot the take and the checks fit
        gap_id=new.gap_id,
        event_ids=list(new.event_ids),
        updated=_now(),
    )
    if new.status == "held" and new.audio_path is None:  # a held edit: no take of its own
        if _voiced(old):
            before = cue_json(old, run_dir)
            entry["previous"] = {k: before[k] for k in OVERLAY_FIELDS}
        elif isinstance(prior.get("previous"), dict):
            entry["previous"] = prior["previous"]
    elif not _voiced(new) and isinstance(prior.get("previous"), dict):
        entry["previous"] = prior["previous"]  # an approval or hold keeps it
    return entry


def apply_overlay(run_dir: Path, doc: CueDoc, overrides: dict[str, dict]) -> list[str]:
    """Put the overrides back over `doc` (the engine's cues); returns the cue ids it changed.

    Precedence, cue by cue:
    1. a cue id the engine no longer has: its override waits in studio.json, unused;
    2. a cue that moved (cue ids are positional: after a rerun the same id can be another
       slot): its start, pause or visuals differ from those the change was checked at. Text the
       person wrote comes back held ("moved since it was checked"), without a take or checks,
       to be edited again; an approval or hold is not applied. Nothing checked elsewhere is
       ever marked edited or approved here;
    3. text the person wrote (authored) wins over the engine's text for that id, with its take
       and check results;
    4. an approval or hold of the engine's own text holds only while the engine's text is the
       one it was made on (base); otherwise it is not applied (kept, unused).
    """
    changed = []
    for cue in doc.all():
        o = overrides.get(cue.id)
        if o is None:
            continue
        moved = (o.get("start"), o.get("gap_id"), o.get("event_ids")) != (
            cue.start,
            cue.gap_id,
            list(cue.event_ids),
        )
        if moved and o.get("authored") and isinstance(o.get("text"), str):
            fields = {"text": o["text"], **MOVED}
        elif moved:
            continue
        elif o.get("authored"):
            fields = {k: o[k] for k in OVERLAY_FIELDS if k in o}
        elif o.get("base") == text_hash(cue.text):
            fields = {k: o[k] for k in STATUS_FIELDS if k in o}
        else:
            continue
        try:
            new = _cue_in(run_dir, {**cue_json(cue, run_dir), **fields})
        except ValidationError:
            log.warning("studio: the override of %s does not load", cue.id)
            continue
        if new.text != cue.text:  # the person's words over the engine's: coverage changes
            new = refreshed(new)
        if new != cue:
            doc.replace(new)
            changed.append(cue.id)
    return changed


def reconcile(run_dir: Path) -> tuple[CueDoc | None, list[str]]:
    """cues.json with the overlay put back (and stored, the run marked dirty, when that
    changed anything): after an engine rerun, the person's changes are on top again."""
    doc = load_cues(run_dir)
    if doc is None:
        return None, []
    overrides = overrides_of(run_dir)
    changed = apply_overlay(run_dir, doc, overrides) if overrides else []
    if changed:
        save_change(run_dir, doc, [c for c in doc.all() if c.id in changed])
    return doc, changed


def save_cues(run_dir: Path, doc: CueDoc) -> None:
    def by_time(cues: list[Cue]) -> list[dict]:
        return [cue_json(c, run_dir) for c in sorted(cues, key=lambda c: (c.start, c.id))]

    _write_json(
        run_dir / CUES, {**doc.meta, "cues": by_time(doc.cues), "extended": by_time(doc.extended)}
    )


def sync_stage(run_dir: Path, cue: Cue) -> None:
    """The cue's finalize record holds the person's version, under the key it had."""
    store, name = Store(run_dir), f"finalize/{cue.id}"
    entry = _dict(_read_json(store.path(name)))
    if isinstance(entry.get("key"), str):  # relative audio path, as the engine keeps it
        store.save(name, entry["key"], cue_json(cue, run_dir))


def save_change(
    run_dir: Path, doc: CueDoc, changed: list[Cue], overrides: dict[str, dict] | None = None
) -> None:
    """studio.json (dirty, the overrides) first, then cues.json, then the stage records: a
    crash in between leaves an overlay that the next load puts back."""
    mark(run_dir, dirty=True, overrides=overrides)
    save_cues(run_dir, doc)
    for cue in changed:
        sync_stage(run_dir, cue)


# -- the engine ---------------------------------------------------------------------------------


def edit_context(rc: RunContext, doc: CueDoc, cue: Cue) -> CueContext:
    """What finalising `cue` needs about the rest of the programme, from the cues as they are
    now (lull.pipeline.cue_context)."""
    if cue.gap_id is not None and rc.gap(cue.gap_id) is None:
        raise NotEditable(f"its pause {cue.gap_id} is not in the listen record")
    known = {e.id for e in rc.events}
    if not cue.event_ids or any(i not in known for i in cue.event_ids):
        raise NotEditable(f"the visuals of cue {cue.id} are not in the watch record")
    try:
        return cue_context(
            cue,
            wps=rc.wps,
            duration=rc.heard.duration,
            cast=rc.cast,
            named=rc.named,
            proper_nouns=rc.proper,
            words=rc.heard.words,
            events=rc.events,
            speech=rc.heard.speech,
            finished=[c for c in doc.all() if c.id != cue.id],
            info=rc.info,
        )
    except ValueError as e:
        raise NotEditable(str(e)) from None


def check_text(text: str, ctx: CueContext) -> tuple[str, list[Finding]]:
    """The style pass finalize opens with, without its model repair: descriptors for cast
    names not yet introduced, autofix, then lint. Returns the text checked and its findings."""
    named, _, _ = introduce_names(text, ctx.cast, ctx.introduced, ctx.verbatim)
    checked = autofix(named)
    introduced = set(ctx.introduced) | introduced_in(checked, ctx.cast)
    keep = [v for v in ctx.verbatim if v and v in checked]  # on-screen text quoted (R19)
    findings = lint(
        checked, verbatim=keep, named=set(ctx.named) | set(ctx.cast), introduced=introduced
    )
    return checked, findings


Clients = Callable[[Ledger], tuple[TokenFactory, SpeechClient]]


def real_clients(ledger: Ledger) -> tuple[TokenFactory, SpeechClient]:
    return TokenFactory.from_settings(ledger), NemoSpeechHTTP.from_settings()


@dataclass(frozen=True)
class EditJob:
    """One edit: `finalizer(job)` re-checks it in a worker thread."""

    ctx: RunContext
    cue: Cue  # as stored
    text: str  # the describer's
    cue_ctx: CueContext
    work: Path  # a folder of its own for the clip and the take
    ledger: Ledger
    clients: Clients


def finalize_edit(job: EditJob) -> Cue:
    """The engine's finalize_cue on the describer's text: verified (standard) or extended
    (no gap) when it passes, held with a note when it does not."""
    rc, cue = job.ctx, job.cue
    gap = rc.gap(cue.gap_id) if cue.gap_id is not None else None
    events = {e.id: e for e in rc.events if e.id in cue.event_ids}
    fresh = cue.model_copy(
        update={
            "text": job.text,
            "status": "verified" if gap is not None else "extended",
            "end": None,
            "claims": [],
            "audio_path": None,
            "audio_dur": None,
            "tempo": 1.0,
            "findings": [],
            "note": None,
            "described": None,
        }
    )
    tf, speech = job.clients(job.ledger)
    try:
        return finalize_cue(
            fresh, gap, events, rc.video, job.work, tf, speech, rc.opts, job.cue_ctx,
            run_id=rc.run_id,
        )  # fmt: skip
    finally:
        close = getattr(tf, "close", None)
        if close is not None:
            close()


def republish(rc: RunContext, doc: CueDoc, ledger: Ledger) -> Path:
    """The overlap gate, then the engine's publish (it gates again, guards and protected sound
    included, before it writes a file). Returns the manifest path."""
    assert_no_overlap(doc.cues, rc.heard.speech)
    result = publish(
        rc.run_dir, rc.video, doc.cues, doc.extended, rc.events, rc.gaps, rc.research, ledger,
        rc.run_id, rc.opts, speech=rc.heard.speech, words=rc.heard.words,
        duplicates=rc.planned.duplicates, priority=rc.planned.priority, reviewer=rc.reviewer,
    )  # fmt: skip
    return result.manifest


def unvoiced(doc: CueDoc) -> list[str]:
    """Cues publish would voice whose take is missing."""
    out = []
    for c in doc.all():
        voiced = mixer.STANDARD_VOICED if c.gap_id is not None else mixer.EXTENDED_VOICED
        if c.status in voiced and (
            c.audio_path is None or not c.audio_dur or not Path(c.audio_path).is_file()
        ):
            out.append(c.id)
    return out


def _tidy(run_dir: Path, work: Path, old: Cue, new: Cue, previous: str | None = None) -> None:
    """After a stored edit: remove its clip and its folder when no take came of it. A voiced
    new take supersedes the old cue's take and the `previous` one (the last voiced version of
    a held cue), so those edit folders go; a held edit keeps them (the overlay's previous).
    Takes outside work/edits (the engine's own) are never removed."""
    shutil.rmtree(work / "verify", ignore_errors=True)
    edits = (run_dir / WORK / EDITS).resolve()

    def edit_dir(path: str | None) -> Path | None:
        if path is None:
            return None
        p = (Path(path) if Path(path).is_absolute() else run_dir / path).resolve()
        if not p.is_relative_to(edits) or p == edits:
            return None
        return edits / p.relative_to(edits).parts[0]

    keep = edit_dir(new.audio_path)
    if keep is None or keep != work.resolve():
        shutil.rmtree(work, ignore_errors=True)
    if keep is None:  # held: the takes it would replace stay
        return
    for gone in {edit_dir(old.audio_path), edit_dir(previous)} - {None, keep}:
        shutil.rmtree(gone, ignore_errors=True)


def _audio_paths(obj: Any) -> list[str]:
    """Every "audio_path" string anywhere in `obj` (JSON data)."""
    if isinstance(obj, dict):
        found = [v for k, v in obj.items() if k == "audio_path" and isinstance(v, str)]
        return found + [p for v in obj.values() for p in _audio_paths(v)]
    if isinstance(obj, list):
        return [p for v in obj for p in _audio_paths(v)]
    return []


def runs_with_edits(roots: list[Path]) -> list[Path]:
    """The run folders under `roots` that have edit folders."""
    return [edits.parent.parent for root in roots for edits in root.glob(f"*/{WORK}/{EDITS}")]


def sweep_edits(run_dir: Path) -> int:
    """Remove the run's work/edits/* folders that neither cues.json nor studio.json refers to
    (takes left by a crash mid-edit). Only under the run's lock: an edit in progress would look
    the same. Returns how many folders went."""
    edits = run_dir / WORK / EDITS
    if not edits.is_dir():
        return 0
    refs = _audio_paths(_read_json(run_dir / CUES)) + _audio_paths(_read_json(run_dir / STUDIO))
    used = set()
    for ref in refs:
        p = Path(ref) if Path(ref).is_absolute() else run_dir / ref
        try:
            rel = p.resolve().relative_to(edits.resolve())
        except ValueError:
            continue
        if rel.parts:
            used.add(rel.parts[0])
    removed = 0
    for d in edits.iterdir():
        if d.is_dir() and d.name not in used:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def _when(text: Any) -> float | None:
    try:
        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return None


def scan_runs(
    runs_dir: Path,
    now: float,
    *,
    upload_ttl: float,
    fork_ttl: float,
    pending_ttl: float,
    keep_samples: int,
) -> list[str]:
    """The live run folders due to go (see the module notes). Reads only. A folder with
    neither .pending nor request.json (a store that hung, a removal cut short) is left over
    and goes after `pending_ttl` too."""
    due: list[str] = []
    samples: list[tuple[float, str]] = []
    for d in sorted(runs_dir.iterdir()) if runs_dir.is_dir() else []:
        if not d.is_dir():
            continue
        try:
            marked = (d / PENDING).stat().st_mtime
        except OSError:
            marked = None
        if marked is not None:
            if now - marked >= pending_ttl:
                due.append(d.name)
            continue
        if not (d / REQUEST).exists():  # neither being filled nor a run: left over
            try:
                if now - d.stat().st_mtime >= pending_ttl:
                    due.append(d.name)
            except OSError:
                pass
            continue
        req = _dict(_read_json(d / REQUEST))
        kind = _dict(req.get("source")).get("kind")
        made = _made_at(d, req)
        if kind == "upload" and now - made >= upload_ttl:
            due.append(d.name)
        elif kind == "fork" and now >= (_when(req.get("expires")) or made + fork_ttl):
            due.append(d.name)
        elif kind == "sample":
            samples.append((made, d.name))
    samples.sort(reverse=True)
    return due + [run_id for _, run_id in samples[keep_samples:]]


# -- the Studio's state -------------------------------------------------------------------------


class Studio:
    """The editing routes' state. Tests swap `clients`, `finalizer`, `publisher`, the limiters
    and the caps."""

    def __init__(self, settings: ApiSettings, store: RunStore, live: Live) -> None:
        self.settings, self.store, self.live = settings, store, live
        self.limiter = RateLimiter(
            per_client=EDITS_PER_MIN,
            client_window=60.0,
            per_client_day=EDITS_PER_VISITOR_DAY,
            per_day=EDITS_PER_DAY,
        )
        self.publish_limiter = RateLimiter(
            per_client=PUBLISHES_PER_MIN,
            client_window=60.0,
            per_client_day=PUBLISHES_PER_VISITOR_DAY,
            per_day=PUBLISHES_PER_DAY,
        )
        self.clients: Clients = real_clients
        self.finalizer: Callable[[EditJob], Cue] = finalize_edit
        self.publisher: Callable[[RunContext, CueDoc, Ledger], Path] = republish
        self.fork_limiter = RateLimiter(
            per_client=FORKS_PER_HOUR,
            client_window=HOUR,
            per_client_day=FORKS_PER_VISITOR_DAY,
            per_day=FORKS_PER_DAY,
        )
        self.max_edits, self.max_publishes = MAX_EDITS, MAX_PUBLISHES
        self.editing = self.publishing = 0  # across all runs, now
        self.fork_ttl, self.housekeep_every = FORK_TTL_S, HOUSEKEEP_EVERY_S
        self.pending_ttl, self.keep_sample_runs = PENDING_TTL_S, KEEP_SAMPLE_RUNS
        self.last_housekeep: float | None = None  # time.monotonic() of the last pass's start
        self._housekeeping = False
        self._housekeep_task: asyncio.Task | None = None
        self.upload_ttl = settings.lull_upload_retention_days * DAY  # uploads and their runs
        self.lock_wait = LOCK_WAIT_S
        self._locks: dict[str, asyncio.Lock] = {}
        live.executor.after_run.append(self.after_run)

    def _in_use(self, run_id: str) -> bool:
        executor = self.live.executor
        lock = self._locks.get(run_id)
        return (
            executor.get(run_id) is not None
            or executor.lingers(run_id)
            or (lock is not None and lock.locked())
        )

    async def housekeep(self, now: float | None = None) -> list[str]:
        """One housekeeping pass (see the module notes); the run ids removed. The deciding
        reads and the deleting run in threads; each removal holds the run's lock and hides the
        run from the routes (404) while it goes."""
        if self._housekeeping:
            return []
        self._housekeeping = True
        self.last_housekeep = time.monotonic()
        try:
            return await self._housekeep(time.time() if now is None else now)
        finally:
            self._housekeeping = False

    async def _housekeep(self, now: float) -> list[str]:
        due = await anyio.to_thread.run_sync(
            functools.partial(
                scan_runs,
                self.settings.runs_dir,
                now,
                upload_ttl=self.upload_ttl,
                fork_ttl=self.fork_ttl,
                pending_ttl=self.pending_ttl,
                keep_samples=self.keep_sample_runs,
            )
        )
        gone: list[str] = []
        for run_id in due:
            if self._in_use(run_id):
                continue
            lock = self.lock(run_id)
            await lock.acquire()  # free (checked just now): returns at once
            self.store.removing.add(run_id)
            try:
                run_dir = self.settings.runs_dir / run_id
                await anyio.to_thread.run_sync(
                    functools.partial(shutil.rmtree, run_dir, ignore_errors=True)
                )
            finally:
                self.store.removing.discard(run_id)
                lock.release()
                self._locks.pop(run_id, None)
            gone.append(run_id)
        if gone:
            self.store.invalidate()
            log.info("housekeeping: %d runs removed", len(gone))
        roots = [self.settings.runs_dir, self.settings.showcase_dir]
        swept = 0
        for run_dir in await anyio.to_thread.run_sync(runs_with_edits, roots):
            if self._in_use(run_dir.name):
                continue
            lock = self.lock(run_dir.name)
            await lock.acquire()
            try:
                swept += await anyio.to_thread.run_sync(sweep_edits, run_dir)
            finally:
                lock.release()
        if swept:
            log.info("housekeeping: removed %d edit folders nothing refers to", swept)
        return gone

    async def housekeeping_forever(self) -> None:
        """A pass now (startup), then every `housekeep_every` seconds while serving."""
        while True:
            try:
                await self.housekeep()
            except Exception:
                log.exception("housekeeping failed")
            await asyncio.sleep(self.housekeep_every)

    def housekeep_if_overdue(self) -> None:
        """Start a pass when the last one began more than `housekeep_every` ago: a scaled-to-
        zero instance runs no timers, so the health check (the keep-warm) starts it."""
        last = self.last_housekeep
        running = self._housekeep_task is not None and not self._housekeep_task.done()
        if running or self._housekeeping or last is None:
            return
        if time.monotonic() - last >= self.housekeep_every:
            self._housekeep_task = asyncio.create_task(self._housekeep_logged())

    async def _housekeep_logged(self) -> None:
        try:
            await self.housekeep()
        except Exception:
            log.exception("housekeeping failed")

    def lock(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    @contextlib.asynccontextmanager
    async def locked(self, run_id: str) -> AsyncIterator[None]:
        """The run's lock, waited on for at most `lock_wait` seconds (then 409 busy): a waiter
        holds a request slot, so nobody waits long."""
        lock = self.lock(run_id)
        try:
            with anyio.fail_after(self.lock_wait):
                await lock.acquire()
        except TimeoutError:
            raise LiveError(
                409,
                "busy",
                "Another change to this run is being made. Try again in a moment.",
                headers={"Retry-After": "2"},
            ) from None
        try:
            yield
        finally:
            lock.release()

    def writable(self, run_id: str, request: Request) -> Path:
        """The run folder, when the request may change it: a live run with its owner's edit
        token (403 forbidden otherwise), a showcase run only where they are editable."""
        found = self.store.find(run_id)
        if found is None:
            raise LiveError(404, "not_found", "There is no run with that id.")
        kind, run_dir = found
        if kind == "showcase":
            if not self.settings.lull_showcase_editable:
                raise LiveError(403, "read_only", READ_ONLY)
        elif not holds_token(run_dir, request):
            raise LiveError(403, "forbidden", FORBIDDEN)
        self.not_described(run_id)
        return run_dir

    def after_run(self, job: Any) -> dict | None:
        """After an engine run over a folder the Studio changed: the overlay put back (in the
        live executor's worker thread, before the run's `done` goes out)."""
        if not overrides_of(job.run_dir):
            return None
        try:
            _, changed = reconcile(job.run_dir)
        except NotEditable:
            log.warning("studio: %s: the cues do not load after the run", job.run_id)
            return None
        if not changed:
            return None
        self.store.invalidate(job.run_dir)
        return {"studio_edits": changed}

    def not_described(self, run_id: str) -> None:
        """409 while the live executor describes the run, or a stopped run's thread is still
        finishing its last stage."""
        executor = self.live.executor
        if executor.get(run_id) is not None or executor.lingers(run_id):
            raise LiveError(409, "busy", DESCRIBING, headers={"Retry-After": "60"})

    def check_rate(
        self,
        request: Request,
        limiter: RateLimiter | None = None,
        what: str = "changes",
        *,
        count: bool = True,
    ) -> None:
        """The visitor's short window, their share of the day, everyone's day (429). The
        routes check once early without counting (`count=False`) and again, counted, once they
        hold the run's lock: a change refused busy (409) costs the visitor nothing."""
        limiter = limiter or self.limiter
        key = limiter.client_key(client_ip(request, self.settings.lull_proxy_hops))
        stop = limiter.retry_after(key)
        if stop is not None:
            limit, wait = stop
            if limit == "client":
                window = "minute" if limiter.client_window <= 60 else "hour"
                message = f"Too many {what} in a {window}. Try again in {wait_words(wait)}."
            elif limit == "client_day":
                message = (
                    f"You have made {limiter.per_client_day} {what} today, as many as one "
                    f"visitor may. Try again in {wait_words(wait)}."
                )
            else:
                message = (
                    f"The Studio has had too many {what} today. Try again in {wait_words(wait)}."
                )
            raise LiveError(429, "rate_limited", message, headers={"Retry-After": str(wait)})
        if count:
            limiter.hit(key)

    def spent_by(self, kind: str, run_dir: Path) -> float:
        """The model spend that counts against the run's cap: what it was forked with, its own
        ledger rows, and for a recorded run what its manifest says it spent."""
        request = _dict(_read_json(run_dir / REQUEST))
        manifest = _dict(_read_json(run_dir / MANIFEST))
        before = request.get("spend_before")
        before = float(before) if isinstance(before, int | float) and before > 0 else 0.0
        run_id = manifest.get("run_id") if isinstance(manifest.get("run_id"), str) else None
        own = self.live.ledger.spent(run_id=run_id or run_dir.name)
        if kind == "showcase":
            spend = public_spend(_dict(manifest.get("summary")))
            recorded = spend["model_usd_all_runs"] or spend["model_usd"] or 0.0
            own = max(own, float(recorded))
        return round(before + own, 6)

    def ledger_for(self, run_dir: Path) -> Ledger:
        """The live ledger, counting what a fork was made with against its run cap."""
        before = _dict(_read_json(run_dir / REQUEST)).get("spend_before")
        if isinstance(before, int | float) and before > 0:
            return CarriedLedger(self.live.ledger, float(before))  # type: ignore[return-value]
        return self.live.ledger

    def check_budget(self, run_id: str, ledger: Ledger | None = None) -> None:
        try:
            (ledger or self.live.ledger).check(run_id, EDIT_ESTIMATE)
        except BudgetExceeded as e:
            raise LiveError(402, "budget", budget_message(e.scope), scope=e.scope) from None

    async def load(self, run_dir: Path) -> CueDoc:
        """The run's cues with the overlay put back (an engine rerun since the last change,
        through the CLI say, is reconciled here)."""
        if not (run_dir / MANIFEST).is_file():
            raise LiveError(409, "not_ready", NOT_READY)
        try:
            doc, changed = await anyio.to_thread.run_sync(reconcile, run_dir)
        except NotEditable as e:
            raise not_editable(e) from None
        if doc is None:
            raise LiveError(409, "not_ready", NOT_READY)
        if changed:
            self.store.invalidate(run_dir)
        return doc

    async def context(self, run_dir: Path) -> RunContext:
        try:
            return await anyio.to_thread.run_sync(load_context, run_dir, self.settings.samples_dir)
        except NotEditable as e:
            raise not_editable(e) from None

    async def save(self, run_dir: Path, doc: CueDoc, old: Cue, new: Cue, *, authored: bool) -> None:
        """One cue changed from `old` to `new`: the overlay entry, cues.json, its stage record."""
        prior = overrides_of(run_dir).get(new.id)
        entry = override(run_dir, old, new, prior, authored=authored)
        doc.replace(new)
        try:
            await anyio.to_thread.run_sync(save_change, run_dir, doc, [new], {new.id: entry})
        finally:
            self.store.invalidate(run_dir)

    def scrub(self, run_dir: Path, text: str) -> str:
        return self.live.scrubber(run_dir)(text)[:300]


class CarriedLedger:
    """A fork's ledger: the live one, with what the fork's source had spent counted against
    the run cap too, so forking does not hand out a fresh cap."""

    def __init__(self, inner: Ledger, before: float) -> None:
        self.inner, self.before = inner, before

    def check(self, run_id: str, estimate: float) -> None:
        self.inner.check(run_id, estimate)
        cap = self.inner.cap_run
        if cap is None:
            return
        spent = self.before + self.inner.spent(run_id=run_id)
        if spent + estimate > cap:
            raise BudgetExceeded(
                "run",
                f"run cap ${cap:.2f} would be exceeded (spent ${spent:.4f}, with the run it was "
                f"copied from, + estimate ${estimate:.4f})",
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def not_editable(e: NotEditable) -> LiveError:
    return LiveError(409, "not_editable", f"This run cannot be edited: {e}.")


def overloaded() -> LiveError:
    return LiveError(
        503,
        "overloaded",
        "The Studio is busy with other changes. Try again in a few seconds.",
        headers={"Retry-After": str(OVERLOADED_RETRY_S)},
    )


def no_cue() -> LiveError:
    return LiveError(404, "not_found", "There is no cue with that id in this run.")


def _studio(request: Request) -> Studio:
    return request.app.state.studio


async def _body(request: Request) -> dict:
    """The JSON object sent (an empty body is {}), refused past MAX_BODY bytes."""
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY:
            raise LiveError(413, "too_large", "This request is too large.")
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise LiveError(422, "invalid", "Send a JSON object.")
    return body


def _cue(doc: CueDoc, cue_id: str) -> Cue:
    cue = doc.get(cue_id)
    if cue is None:
        raise no_cue()
    return cue


def _view(run_dir: Path, cue: Cue) -> dict:
    return cue_view(run_dir.name, cue_json(cue, run_dir))


# -- routes -------------------------------------------------------------------------------------

router = APIRouter()


@router.patch("/v1/runs/{run_id}/cues/{cue_id}")
async def edit_cue(run_id: str, cue_id: str, request: Request) -> dict:
    studio = _studio(request)
    run_dir = studio.writable(run_id, request)
    if not CUE_ID_RE.fullmatch(cue_id):
        raise no_cue()
    studio.check_rate(request, count=False)
    text = (await _body(request)).get("text")
    if not isinstance(text, str) or not text.strip():
        raise LiveError(422, "invalid", "Send the new description as text.")
    if len(text) > MAX_TEXT:
        raise LiveError(422, "invalid", f"A description takes at most {MAX_TEXT} characters.")

    async with studio.locked(run_id):
        studio.check_rate(request)
        studio.not_described(run_id)
        doc = await studio.load(run_dir)
        cue = _cue(doc, cue_id)
        rc = await studio.context(run_dir)
        try:
            ctx = edit_context(rc, doc, cue)
        except NotEditable as e:
            raise not_editable(e) from None
        checked, findings = check_text(text, ctx)
        if not checked:
            raise LiveError(422, "invalid", "Send the new description as text.")
        errors = sorted({f.rule for f in findings if f.severity == "error"})
        if errors:
            raise LiveError(
                422,
                "lint",
                f"This description breaks {', '.join(errors)}. Fix it and save again.",
                text=checked,
                findings=[f.model_dump(mode="json") for f in findings],
            )
        ledger = studio.ledger_for(run_dir)
        studio.check_budget(rc.run_id, ledger)
        if studio.editing >= studio.max_edits:
            raise overloaded()

        work = run_dir / WORK / EDITS / f"{cue.id}-{secrets.token_hex(4)}"
        job = EditJob(rc, cue, text, ctx, work, ledger, studio.clients)
        studio.editing += 1
        try:
            done = await anyio.to_thread.run_sync(studio.finalizer, job)
        except BudgetExceeded as e:  # only this edit's folder goes: the stored take stays
            await anyio.to_thread.run_sync(lambda: shutil.rmtree(work, ignore_errors=True))
            raise LiveError(402, "budget", budget_message(e.scope), scope=e.scope) from None
        except Exception as e:
            log.exception("studio: the edit of %s/%s failed", run_id, cue.id)
            await anyio.to_thread.run_sync(lambda: shutil.rmtree(work, ignore_errors=True))
            message = f"The edit could not be checked: {studio.scrub(run_dir, str(e))}"
            raise LiveError(502, "engine", message) from None
        finally:
            studio.editing -= 1
        if done.status != "held":
            done = done.model_copy(update={"status": "edited"})
        done = refreshed(done)  # new words: the coverage data is worked out again
        previous = _dict(_dict(overrides_of(run_dir).get(cue.id)).get("previous"))
        await studio.save(run_dir, doc, cue, done, authored=True)
        await anyio.to_thread.run_sync(_tidy, run_dir, work, cue, done, previous.get("audio_path"))
    return _view(run_dir, done)


@router.post("/v1/runs/{run_id}/cues/{cue_id}/approve")
async def approve_cue(run_id: str, cue_id: str, request: Request) -> dict:
    studio = _studio(request)
    run_dir = studio.writable(run_id, request)
    if not CUE_ID_RE.fullmatch(cue_id):
        raise no_cue()
    studio.check_rate(request, count=False)
    async with studio.locked(run_id):
        studio.check_rate(request)
        studio.not_described(run_id)
        doc = await studio.load(run_dir)
        cue = _cue(doc, cue_id)
        if cue.status == "approved":
            return _view(run_dir, cue)
        voiced = cue.audio_path is not None and cue.audio_dur and Path(cue.audio_path).is_file()
        if cue.status not in APPROVABLE or not voiced:
            raise LiveError(
                409,
                "invalid_transition",
                f"A {cue.status} cue cannot be approved. Edit it so it is checked again.",
                cue_status=cue.status,
            )
        new = cue.model_copy(update={"status": "approved"})
        await studio.save(run_dir, doc, cue, new, authored=False)
    return _view(run_dir, new)


@router.post("/v1/runs/{run_id}/cues/{cue_id}/hold")
async def hold_cue(run_id: str, cue_id: str, request: Request) -> dict:
    studio = _studio(request)
    run_dir = studio.writable(run_id, request)
    if not CUE_ID_RE.fullmatch(cue_id):
        raise no_cue()
    reason = (await _body(request)).get("reason")
    if reason is not None and not isinstance(reason, str):
        raise LiveError(422, "invalid", "The reason must be text.")
    reason = " ".join((reason or "").split())
    if len(reason) > MAX_REASON:
        raise LiveError(422, "invalid", f"A reason takes at most {MAX_REASON} characters.")
    studio.check_rate(request, count=False)
    async with studio.locked(run_id):
        studio.check_rate(request)
        studio.not_described(run_id)
        doc = await studio.load(run_dir)
        cue = _cue(doc, cue_id)
        if reason:
            note = f"held by a reviewer: {reason}"
        elif cue.status == "held" and cue.note:
            note = cue.note  # already held: its reason stays
        else:
            note = "held by a reviewer"
        new = cue.model_copy(update={"status": "held", "note": note})
        await studio.save(run_dir, doc, cue, new, authored=False)
    return _view(run_dir, new)


@router.post("/v1/runs/{run_id}/publish")
async def publish_run(run_id: str, request: Request) -> dict:
    studio = _studio(request)
    run_dir = studio.writable(run_id, request)
    async with studio.locked(run_id):
        studio.not_described(run_id)
        doc = await studio.load(run_dir)
        if not studio.store.dirty(run_dir):  # nothing changed since it was published
            return await _detail(studio, run_dir)
        studio.check_rate(request, studio.publish_limiter, "publishes")
        rc = await studio.context(run_dir)
        missing = unvoiced(doc)
        if missing:
            raise LiveError(
                409,
                "not_voiced",
                "Nothing was published: these cues have lost their audio. Edit them again.",
                cue_ids=missing,
            )
        if studio.publishing >= studio.max_publishes:
            raise overloaded()
        studio.publishing += 1
        try:
            await anyio.to_thread.run_sync(studio.publisher, rc, doc, studio.live.ledger)
        except OverlapError as e:
            raise LiveError(409, "overlap", OVERLAP, cue_ids=e.cue_ids) from None
        except Exception as e:
            log.exception("studio: publishing %s failed", run_id)
            message = f"Publishing failed: {studio.scrub(run_dir, str(e))}"
            raise LiveError(500, "publish_failed", message) from None
        finally:
            studio.publishing -= 1
            studio.store.invalidate(run_dir)
        await anyio.to_thread.run_sync(lambda: mark(run_dir, dirty=False))
        studio.store.invalidate(run_dir)
        return await _detail(studio, run_dir)  # under the lock: no change can slip in


async def _detail(studio: Studio, run_dir: Path) -> dict:
    """The run as GET /v1/runs/{id} gives it (in a thread: it may work out the loudness)."""
    return await anyio.to_thread.run_sync(
        functools.partial(studio.store.detail, run_dir, owner=True)  # the writer owns it
    )


# -- forks --------------------------------------------------------------------------------------


def _made_at(run_dir: Path, request: dict) -> float:
    """When a run was made: request.json's created, else its folder's mtime."""
    try:
        return datetime.fromisoformat(request["created"]).timestamp()
    except (KeyError, TypeError, ValueError):
        try:
            return run_dir.stat().st_mtime
        except OSError:
            return time.time()


def _copy(src: Path, dst: Path) -> None:
    """A real copy, never a hard link: publishing the fork rewrites its outputs in place, and
    a link would rewrite the source run's with them."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def _rebase(obj: Any, src: Path, dst: Path) -> Any:
    """`obj` (JSON data) with every absolute path into `src` pointed at the same place in
    `dst`."""
    if isinstance(obj, str):
        p = Path(obj)
        if not p.is_absolute():
            return obj
        try:
            return str(dst / p.resolve().relative_to(src.resolve()))
        except (ValueError, OSError):
            return obj
    if isinstance(obj, dict):
        return {k: _rebase(v, src, dst) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rebase(v, src, dst) for v in obj]
    return obj


def fork_run(
    src: Path,
    dst: Path,
    *,
    run_id: str,
    token_hash: str,
    client: str,
    origin: dict | None = None,
    envelope: Callable[[Path, Path, float | None], object] | None = None,
    spend_before: float = 0.0,
    expires: float | None = None,
    samples_dir: Path | None = None,
) -> None:
    """Copy what the Studio and a new publish need from the published run `src` into the new
    live run folder `dst`, left out of the run list until it is complete: the manifest and
    cues.json (under the new run id), studio.json, the outputs and keyframes, the stage
    records (absolute paths into `src` pointed at `dst`), the takes the cues and the overlay
    use, and the source video when the run keeps it (a recorded run's is found among the
    samples). Not events.jsonl: the fork's history starts with it. No model is called.
    `spend_before` (what the source had spent) counts against the fork's run cap; `expires`
    (epoch seconds) is when housekeeping removes it."""
    dst.mkdir(parents=True)
    (dst / PENDING).touch()
    manifest = _dict(_read_json(src / MANIFEST))
    request = _dict(_read_json(src / REQUEST))
    origin = origin or {}

    copied: set[str] = set()

    def copy(rel: Any) -> bool:
        if rel in copied:
            return True
        found = contained_file(src, rel) if safe_rel(rel) else None
        if found is not None:
            _copy(found, dst / rel)
            copied.add(rel)
        return found is not None

    for rel in _dict(manifest.get("outputs")):
        if rel not in (CUES, MANIFEST):
            copy(rel)
    for p in sorted((src / "keyframes").glob("*.jpg")):
        if KEYFRAME_RE.fullmatch(p.name):
            copy(f"keyframes/{p.name}")
    takes = _audio_paths(_read_json(src / CUES)) + _audio_paths(_read_json(src / STUDIO))
    for rel in dict.fromkeys(takes):
        copy(rel)
    name = _dict(manifest.get("video")).get("name")
    video = next(
        (r for r in (request.get("input"), f"input/{name}" if isinstance(name, str) else None)
         if r is not None and copy(r)),
        None,
    )  # fmt: skip
    for p in sorted((src / "stages").rglob("*.json")):
        data = _read_json(p)
        if data is not None:
            out = dst / p.relative_to(src)
            out.parent.mkdir(parents=True, exist_ok=True)
            _write_json(out, _rebase(data, src, dst))
    cues = _read_json(src / CUES)
    if isinstance(cues, dict):
        _write_json(dst / CUES, {**cues, "run_id": run_id} if "run_id" in cues else cues)
    if (src / STUDIO).is_file():
        _copy(src / STUDIO, dst / STUDIO)
    if (src / facts.LOUDNESS).is_file():  # the same original sound
        _copy(src / facts.LOUDNESS, dst / facts.LOUDNESS)
    elif envelope is not None:  # worked out now, at fork time, never on a request
        samples = samples_dir if samples_dir is not None else Path()
        original = dst / video if video else facts.source_video(src, manifest, request, samples)
        if original is not None:
            envelope(dst, original, facts.duration(manifest, request))
    options = request.get("options") if isinstance(request.get("options"), dict) else None
    options = options or _dict(manifest.get("options"))
    duration = _dict(manifest.get("video")).get("duration")
    _write_json(
        dst / REQUEST,
        {
            "run_id": run_id,
            "created": _now(),
            **({"expires": _iso(expires)} if expires is not None else {}),
            "source": {"kind": "fork", "id": src.name, "name": name, **origin},
            **({"input": video} if video else {}),
            "options": {k: options[k] for k in ("mode", "voice") if k in options},
            "video": {"duration": duration},
            "client": client,
            "spend_before": spend_before,
            TOKEN_KEY: token_hash,
        },
    )
    _write_json(dst / MANIFEST, {**manifest, "run_id": run_id})  # last: then it is a run
    (dst / PENDING).unlink()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def fork_expiry(studio: Studio, kind: str, src: Path, now: float) -> float:
    """When a fork of `src` goes: a day on, but never after the upload it comes from (a fork
    of an upload, or of a fork of one, keeps a copy of the visitor's video)."""
    until = now + studio.fork_ttl
    if kind != "live":
        return until
    req = _dict(_read_json(src / REQUEST))
    source, made = _dict(req.get("source")), _made_at(src, req)
    if source.get("kind") == "fork":
        if source.get("origin") != "sample":
            until = min(until, _when(req.get("expires")) or made + studio.fork_ttl)
    elif source.get("kind") != "sample":  # an upload (a run that says nothing is taken for one)
        until = min(until, made + studio.upload_ttl)
    return until


def fork_origin(studio: Studio, kind: str, src: Path) -> dict:
    """What a fork of `src` came from in the end: a sample's run (recorded or live) or an
    upload, and which sample; a fork of a fork keeps its origin."""
    manifest = _dict(_read_json(src / MANIFEST))
    source = facts.run_source(
        kind, manifest, _dict(_read_json(src / REQUEST)), studio.settings.samples_dir
    )
    origin = source.get("origin") if source["kind"] == "fork" else source["kind"]
    out = {"origin": origin if origin in ("sample", "upload") else "upload"}
    if isinstance(source.get("sample_id"), str):
        out["sample_id"] = source["sample_id"]
    return out


@router.post("/v1/runs/{run_id}/fork")
async def fork(run_id: str, request: Request) -> JSONResponse:
    """A published run (showcase or live) copied into a new live run that the caller owns:
    {run_id, edit_token}, never cached. Anyone may fork (with X-Lull-Client); no model is
    called."""
    check_client_header(request)
    studio = _studio(request)
    found = studio.store.find(run_id)
    if found is None:
        raise LiveError(404, "not_found", "There is no run with that id.")
    src = found[1]
    studio.not_described(run_id)
    if not (src / MANIFEST).is_file():
        raise LiveError(409, "not_ready", NOT_READY)
    studio.check_rate(request, studio.fork_limiter, "copies", count=False)
    studio.check_rate(request, count=False)
    new_id, (token, token_hash) = new_run_id(), new_token()
    client = studio.limiter.client_key(client_ip(request, studio.settings.lull_proxy_hops))
    dst = studio.settings.runs_dir / new_id
    async with studio.locked(run_id):  # the source holds still while it is copied
        studio.check_rate(request, studio.fork_limiter, "copies")
        studio.check_rate(request)
        try:
            await anyio.to_thread.run_sync(
                functools.partial(
                    fork_run,
                    src,
                    dst,
                    run_id=new_id,
                    token_hash=token_hash,
                    client=client[:16],
                    origin=fork_origin(studio, found[0], src),
                    envelope=studio.live.envelope,
                    spend_before=studio.spent_by(found[0], src),
                    expires=fork_expiry(studio, found[0], src, time.time()),
                    samples_dir=studio.settings.samples_dir,
                )  # fmt: skip
            )
        except Exception:
            log.exception("studio: forking %s failed", run_id)
            await anyio.to_thread.run_sync(lambda: shutil.rmtree(dst, ignore_errors=True))
            raise LiveError(500, "fork_failed", "The run could not be copied.") from None
    studio.store.invalidate()
    return JSONResponse(
        {"run_id": new_id, "edit_token": token},
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )
