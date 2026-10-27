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

Showcase runs are read-only (403) unless LULL_SHOWCASE_EDITABLE=1. A run the live executor is
describing right now is busy (409); other runs can be edited meanwhile. Changes to one run go
one at a time (a per-run asyncio.Lock, waited on); edits are rate limited per visitor.

Where edits live. cues.json is the Studio's working copy of a run's cues. Every change writes,
in this order: studio.json {"dirty": true} (the run differs from what was published), cues.json
(atomically; audio paths relative to the run folder, as publish writes them) and the cue's
finalize stage record, stages/finalize/<cue id>.json, with its data replaced and its key kept.
That key is the one of the cue as the plan composed it, so an engine rerun over the folder (a
resume, or the CLI on the same output folder) takes the person's version from the cache instead
of finalising the plan's text again; its audio path is absolute, as the engine keeps it.
Publishing writes every output again from cues.json and sets dirty false.

An edit is finalised in a folder of its own, work/edits/<cue id>-<token>/, so the take the
published mix used is never overwritten: a failed or held edit leaves the cue and its audio as
they were, and the take a newer edit replaces is removed.

The engine is reached only through the adapters below (load_context, check_text, edit_context,
finalize_edit, republish), which rebuild what finalize and publish need from the run's stage
records the way lull.pipeline does.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import anyio
from fastapi import APIRouter, Request
from pydantic import BaseModel, ValidationError

from lull.api.live import Live, LiveError, budget_message, client_ip
from lull.api.ratelimit import DAY, RateLimiter
from lull.api.settings import ApiSettings
from lull.api.store import (
    CUE_ID_RE,
    CUES,
    MANIFEST,
    STUDIO,
    RunStore,
    contained_file,
    cue_view,
    safe_rel,
)
from lull.ledger import BudgetExceeded, Ledger
from lull.pipeline import cue_context
from lull.speech import NemoSpeechHTTP, SpeechClient
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
from lull.store import Store, file_sha256
from lull.tf import TokenFactory
from lull.timeline import split_gaps
from lull.types import Cue, Gap, RunOptions, VisualEvent
from lull.voices import FALLBACK_WPS, voice_wps

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)

EDIT_ESTIMATE = 0.01  # USD the spend check reserves for one edit
EDITS_PER_MIN = 30
EDITS_PER_DAY = 2000
MAX_TEXT = 1000
MAX_REASON = 300
MAX_BODY = 64 * 1024
APPROVABLE = ("verified", "edited", "extended")
REQUEST, WORK, EDITS = "request.json", "work", "edits"
READ_ONLY = "Recorded runs are read-only. Describe a video of your own to edit its cues."
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
    cast: dict[str, str]
    names: set[str]

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
    """The video the run described: its upload (request.json), else input/<name>, else the
    sample of that name when its sha256 matches the manifest's."""
    rel = request.get("input")
    if safe_rel(rel) and (found := contained_file(run_dir, rel)) is not None:
        return found
    video = _dict(manifest.get("video"))
    name, sha = video.get("name"), video.get("sha256")
    if safe_rel(name) and "/" not in name:
        if (found := contained_file(run_dir, f"input/{name}")) is not None:
            return found
        sample = samples_dir / name
        if isinstance(sha, str) and sample.is_file() and file_sha256(sample) == sha:
            return sample
    raise NotEditable("its source video is not kept")


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
    names = set(planned.cast)
    if research is not None:
        names |= {g.canonical for g in research.glossary if g.canonical.strip()}
    run_id = manifest.get("run_id")
    return RunContext(
        run_id=run_id if isinstance(run_id, str) and run_id else run_dir.name,
        run_dir=run_dir,
        video=video,
        opts=opts,
        wps=wps,
        heard=heard,
        events=events,
        gaps=split_gaps(heard.gaps, wps=wps),
        research=research,
        cast=dict(planned.cast),
        names=names,
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


def mark(run_dir: Path, *, dirty: bool) -> None:
    prev = _dict(_read_json(run_dir / STUDIO))
    if dirty:
        changes = prev.get("changes") if isinstance(prev.get("changes"), int) else 0
        state = {"dirty": True, "changes": changes + 1, "updated": _now()}
        if isinstance(prev.get("published"), str):
            state["published"] = prev["published"]
    else:
        now = _now()
        state = {"dirty": False, "changes": 0, "updated": now, "published": now}
    _write_json(run_dir / STUDIO, state)


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
    if isinstance(entry.get("key"), str):
        store.save(name, entry["key"], cue.model_dump(mode="json"))


def save_change(run_dir: Path, doc: CueDoc, changed: list[Cue]) -> None:
    mark(run_dir, dirty=True)
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
            names=rc.names,
            words=rc.heard.words,
            events=rc.events,
            finished=[c for c in doc.all() if c.id != cue.id],
        )
    except ValueError as e:
        raise NotEditable(str(e)) from None


def check_text(text: str, ctx: CueContext) -> tuple[str, list[Finding]]:
    """The style pass finalize opens with, without its model repair: descriptors for cast
    names not yet introduced, autofix, then lint. Returns the text checked and its findings."""
    named, _, _ = introduce_names(text, ctx.cast, ctx.introduced)
    checked = autofix(named)
    introduced = set(ctx.introduced) | introduced_in(checked, ctx.cast)
    return checked, lint(checked, named=set(ctx.named) | set(ctx.cast), introduced=introduced)


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
        rc.run_id, rc.opts, speech=rc.heard.speech,
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


def _tidy(run_dir: Path, work: Path, old: Cue, new: Cue | None) -> None:
    """Remove the edit's clip, the edit folder when no take came of it, and the previous
    edit's take."""
    shutil.rmtree(work / "verify", ignore_errors=True)
    edits = (run_dir / WORK / EDITS).resolve()

    def edit_dir(cue: Cue | None) -> Path | None:
        if cue is None or cue.audio_path is None:
            return None
        p = Path(cue.audio_path).resolve()
        if not p.is_relative_to(edits) or p == edits:
            return None
        return edits / p.relative_to(edits).parts[0]

    keep = edit_dir(new)
    if keep is None or keep != work.resolve():
        shutil.rmtree(work, ignore_errors=True)
    gone = edit_dir(old)
    if gone is not None and gone != keep:
        shutil.rmtree(gone, ignore_errors=True)


# -- the Studio's state -------------------------------------------------------------------------


class Studio:
    """The editing routes' state. Tests swap `clients`, `finalizer`, `publisher`, `limiter`."""

    def __init__(self, settings: ApiSettings, store: RunStore, live: Live) -> None:
        self.settings, self.store, self.live = settings, store, live
        self.limiter = RateLimiter(
            per_client=EDITS_PER_MIN, client_window=60.0, per_day=EDITS_PER_DAY, day_window=DAY
        )
        self.clients: Clients = real_clients
        self.finalizer: Callable[[EditJob], Cue] = finalize_edit
        self.publisher: Callable[[RunContext, CueDoc, Ledger], Path] = republish
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    def writable(self, run_id: str) -> Path:
        found = self.store.find(run_id)
        if found is None:
            raise LiveError(404, "not_found", "There is no run with that id.")
        kind, run_dir = found
        if kind == "showcase" and not self.settings.lull_showcase_editable:
            raise LiveError(403, "read_only", READ_ONLY)
        self.not_described(run_id)
        return run_dir

    def not_described(self, run_id: str) -> None:
        if self.live.executor.get(run_id) is not None:
            raise LiveError(409, "busy", DESCRIBING, headers={"Retry-After": "60"})

    def check_rate(self, request: Request) -> None:
        key = self.limiter.client_key(client_ip(request, self.settings.lull_proxy_hops))
        stop = self.limiter.retry_after(key)
        if stop is not None:
            limit, wait = stop
            message = (
                f"Too many changes in a minute. Try again in {wait} s."
                if limit == "client"
                else f"The Studio has had too many changes today. Try again in "
                f"{max(1, round(wait / 3600))} h."
            )
            raise LiveError(429, "rate_limited", message, headers={"Retry-After": str(wait)})
        self.limiter.hit(key)

    def check_budget(self, run_id: str) -> None:
        try:
            self.live.ledger.check(run_id, EDIT_ESTIMATE)
        except BudgetExceeded as e:
            raise LiveError(402, "budget", budget_message(e.scope), scope=e.scope) from None

    def doc(self, run_dir: Path) -> CueDoc:
        if not (run_dir / MANIFEST).is_file():
            raise LiveError(409, "not_ready", NOT_READY)
        try:
            doc = load_cues(run_dir)
        except NotEditable as e:
            raise not_editable(e) from None
        if doc is None:
            raise LiveError(409, "not_ready", NOT_READY)
        return doc

    async def context(self, run_dir: Path) -> RunContext:
        try:
            return await anyio.to_thread.run_sync(load_context, run_dir, self.settings.samples_dir)
        except NotEditable as e:
            raise not_editable(e) from None

    async def save(self, run_dir: Path, doc: CueDoc, changed: list[Cue]) -> None:
        try:
            await anyio.to_thread.run_sync(save_change, run_dir, doc, changed)
        finally:
            self.store.invalidate(run_dir)

    def scrub(self, run_dir: Path, text: str) -> str:
        return self.live.scrubber(run_dir)(text)[:300]


def not_editable(e: NotEditable) -> LiveError:
    return LiveError(409, "not_editable", f"This run cannot be edited: {e}.")


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
    run_dir = studio.writable(run_id)
    if not CUE_ID_RE.match(cue_id):
        raise no_cue()
    studio.check_rate(request)
    text = (await _body(request)).get("text")
    if not isinstance(text, str) or not text.strip():
        raise LiveError(422, "invalid", "Send the new description as text.")
    if len(text) > MAX_TEXT:
        raise LiveError(422, "invalid", f"A description takes at most {MAX_TEXT} characters.")

    async with studio.lock(run_id):
        studio.not_described(run_id)
        doc = studio.doc(run_dir)
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
        studio.check_budget(rc.run_id)

        work = run_dir / WORK / EDITS / f"{cue.id}-{secrets.token_hex(4)}"
        job = EditJob(rc, cue, text, ctx, work, studio.live.ledger, studio.clients)
        try:
            done = await anyio.to_thread.run_sync(studio.finalizer, job)
        except BudgetExceeded as e:
            await anyio.to_thread.run_sync(_tidy, run_dir, work, cue, None)
            raise LiveError(402, "budget", budget_message(e.scope), scope=e.scope) from None
        except Exception as e:
            log.exception("studio: the edit of %s/%s failed", run_id, cue.id)
            await anyio.to_thread.run_sync(_tidy, run_dir, work, cue, None)
            message = f"The edit could not be checked: {studio.scrub(run_dir, str(e))}"
            raise LiveError(502, "engine", message) from None
        if done.status != "held":
            done = done.model_copy(update={"status": "edited"})
        doc.replace(done)
        await studio.save(run_dir, doc, [done])
        await anyio.to_thread.run_sync(_tidy, run_dir, work, cue, done)
    return _view(run_dir, done)


@router.post("/v1/runs/{run_id}/cues/{cue_id}/approve")
async def approve_cue(run_id: str, cue_id: str, request: Request) -> dict:
    studio = _studio(request)
    run_dir = studio.writable(run_id)
    if not CUE_ID_RE.match(cue_id):
        raise no_cue()
    async with studio.lock(run_id):
        studio.not_described(run_id)
        doc = studio.doc(run_dir)
        cue = _cue(doc, cue_id)
        if cue.status == "approved":
            return _view(run_dir, cue)
        if cue.status not in APPROVABLE or cue.audio_path is None or not cue.audio_dur:
            raise LiveError(
                409,
                "invalid_transition",
                f"A {cue.status} cue cannot be approved. Edit it so it is checked again.",
                cue_status=cue.status,
            )
        cue = cue.model_copy(update={"status": "approved"})
        doc.replace(cue)
        await studio.save(run_dir, doc, [cue])
    return _view(run_dir, cue)


@router.post("/v1/runs/{run_id}/cues/{cue_id}/hold")
async def hold_cue(run_id: str, cue_id: str, request: Request) -> dict:
    studio = _studio(request)
    run_dir = studio.writable(run_id)
    if not CUE_ID_RE.match(cue_id):
        raise no_cue()
    reason = (await _body(request)).get("reason")
    if reason is not None and not isinstance(reason, str):
        raise LiveError(422, "invalid", "The reason must be text.")
    reason = " ".join((reason or "").split())
    if len(reason) > MAX_REASON:
        raise LiveError(422, "invalid", f"A reason takes at most {MAX_REASON} characters.")
    async with studio.lock(run_id):
        studio.not_described(run_id)
        doc = studio.doc(run_dir)
        cue = _cue(doc, cue_id)
        if reason:
            note = f"held by a reviewer: {reason}"
        elif cue.status == "held" and cue.note:
            note = cue.note  # already held: its reason stays
        else:
            note = "held by a reviewer"
        cue = cue.model_copy(update={"status": "held", "note": note})
        doc.replace(cue)
        await studio.save(run_dir, doc, [cue])
    return _view(run_dir, cue)


@router.post("/v1/runs/{run_id}/publish")
async def publish_run(run_id: str, request: Request) -> dict:
    studio = _studio(request)
    run_dir = studio.writable(run_id)
    studio.check_rate(request)
    async with studio.lock(run_id):
        studio.not_described(run_id)
        doc = studio.doc(run_dir)
        rc = await studio.context(run_dir)
        missing = unvoiced(doc)
        if missing:
            raise LiveError(
                409,
                "not_voiced",
                "Nothing was published: these cues have lost their audio. Edit them again.",
                cue_ids=missing,
            )
        try:
            await anyio.to_thread.run_sync(studio.publisher, rc, doc, studio.live.ledger)
        except OverlapError as e:
            raise LiveError(409, "overlap", OVERLAP, cue_ids=e.cue_ids) from None
        except Exception as e:
            log.exception("studio: publishing %s failed", run_id)
            message = f"Publishing failed: {studio.scrub(run_dir, str(e))}"
            raise LiveError(500, "publish_failed", message) from None
        finally:
            studio.store.invalidate(run_dir)
        await anyio.to_thread.run_sync(lambda: mark(run_dir, dirty=False))
        studio.store.invalidate(run_dir)
    detail = studio.store.detail(run_dir)
    if detail is None:  # pragma: no cover - publish just wrote the manifest
        raise LiveError(500, "publish_failed", "Publishing wrote no manifest.")
    return detail
