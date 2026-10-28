"""Live runs: one description at a time, its progress streamed as Server-Sent Events.

POST /v1/runs takes an upload (or a sample id). Before anything streams, it checks that no
other run is going (409) and at most MAX_UPLOADS uploads are being received (503), the
visitor's rate limits (429: runs started, and uploads refused), the size (413), the format and
the duration (422: uploads up to MAX_SECONDS, samples MAX_SAMPLE_SECONDS) and the spend caps
(402). The run gets a remuxed copy cut at that limit and probed again (lull.api.clip), so the
duration that counts is the copy's, not what the container claims. Then it answers with
text/event-stream: a `run` event, every engine event as it happens (stage_start / stage_end /
call / cue / warning), a final `done` or `error`, and a `: ping` comment whenever the stream
has been quiet for 15 s.

The engine runs in a worker thread. Its callback only hands each event to the event loop
(call_soon_threadsafe). On the loop the event gets its sequence number, is appended to
runs/<id>/events.jsonl and goes to every listener. GET /v1/runs/{id}/events replays that file
and follows the run if it is still going in this process. It resumes a live run nobody
finished (no manifest, no error.json: the process stopped) for its owner (the edit token);
the engine's stage cache makes that cheap. Replaying a finished run is open to anyone.

A run still going after WALL_CLOCK_S is given up: error event, error.json, the executor
freed; its thread cannot be killed, but its next model call is refused and anything it reports
after that is dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import FormData, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import ClientDisconnect
from starlette.types import Message

from lull import media
from lull.api import clip, facts
from lull.api.clean import Scrubber, default_roots
from lull.api.owner import TOKEN_KEY, holds_token, new_token
from lull.api.ratelimit import HOUR, RateLimiter
from lull.api.settings import ApiSettings
from lull.api.spend import public_summary
from lull.api.store import PENDING, RunStore
from lull.config import get_settings
from lull.ledger import BudgetExceeded, Ledger
from lull.pipeline import Event, budget_estimate
from lull.pipeline import run as run_engine
from lull.speech import NemoSpeechHTTP
from lull.tavily import Tavily
from lull.tf import TokenFactory
from lull.types import RunOptions
from lull.voices import DEFAULT_WPS, FALLBACK_WPS, voice_wps

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 30 * 1024 * 1024  # Cloud Run refuses HTTP/1 bodies over 32 MiB
FORM_SLACK = 64 * 1024  # multipart headers and the small fields around the file
MAX_SECONDS = 60.0  # an upload (demo latency: the UI states it)
MAX_SAMPLE_SECONDS = 45.0
TRIM_SLACK = 1.0  # a stream copy cuts at a packet, a little past the limit
FORMATS = (".mp4", ".mov", ".webm", ".mkv")
HEARTBEAT_S = 15.0
BUSY_RETRY_S = 60
MAX_UPLOADS = 2  # uploads received and checked at once
UPLOADS_RETRY_S = 15
UPLOAD_DEADLINE_S = 60.0  # to receive and store the whole body
FORM_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")
REFUSALS_PER_HOUR = 10  # uploads refused after they were read, per visitor
WALL_CLOCK_S = 600.0
SEARCHES_PER_RUN = 3  # web searches a live run may make (the day cap is LULL_TAVILY_DAY)
NOT_COUNTED = (402, 409, 503)  # refusals that are not the visitor's doing

EVENTS, REQUEST, ERROR, MANIFEST, INPUT = (
    "events.jsonl",
    "request.json",
    "error.json",
    "manifest.json",
    "input",
)
SAMPLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")  # called with fullmatch
BUSY_MESSAGE = "Another description is running. Try again in a minute, or open a recorded run."
FORMAT_MESSAGE = "Send an mp4, mov, webm or mkv video."


# -- refusals ----------------------------------------------------------------------------------


class LiveError(Exception):
    """A refusal, answered as JSON {"error": code, ...extra, "message": plain words}."""

    def __init__(
        self,
        status: int,
        error: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(message)
        self.status, self.error, self.message = status, error, message
        self.headers, self.extra = headers, extra

    def response(self) -> JSONResponse:
        body = {"error": self.error, **self.extra, "message": self.message}
        return JSONResponse(body, status_code=self.status, headers=self.headers)


async def live_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, LiveError)
    return exc.response()


def busy() -> LiveError:
    return LiveError(409, "busy", BUSY_MESSAGE, headers={"Retry-After": str(BUSY_RETRY_S)})


def uploads_busy() -> LiveError:
    return LiveError(
        503,
        "uploads_busy",
        "Other videos are being uploaded right now. Try again in a few seconds.",
        headers={"Retry-After": str(UPLOADS_RETRY_S)},
    )


def one_upload() -> LiveError:
    return LiveError(
        429,
        "one_upload_at_a_time",
        "Your previous upload is still being received. Wait for it to finish.",
        headers={"Retry-After": str(UPLOADS_RETRY_S)},
    )


def upload_timeout(seconds: float) -> LiveError:
    return LiveError(
        408,
        "timeout",
        f"The upload did not arrive within {seconds:g} s. Try a smaller file or a faster "
        "connection.",
    )


def too_large(limit: int) -> LiveError:
    mb = f"{limit / 2**20:g}"
    return LiveError(
        413, "too_large", f"This file is over {mb} MB. Live descriptions take files up to {mb} MB."
    )


def budget_message(scope: str) -> str:
    if scope == "total":
        return "The live demo has used its whole model budget. Recorded runs still work."
    when = {"run": "this run", "day": "today", "provider": "now"}.get(scope, scope)
    return f"The live demo has used its model budget for {when}. Recorded runs still work."


def _wait_words(seconds: int) -> str:
    if seconds < 3600:
        n = max(1, math.ceil(seconds / 60))
        return f"{n} minute{'s' if n > 1 else ''}"
    n = math.ceil(seconds / 3600)
    return f"{n} hour{'s' if n > 1 else ''}"


# -- the engine --------------------------------------------------------------------------------


class RunTimeout(Exception):
    """The run went on past the wall clock and was given up."""


class StopLedger:
    """A run's ledger that refuses every further spend once `stop` is set: how the watchdog
    ends a run whose thread it cannot kill (every model and search call checks first)."""

    def __init__(self, inner: Ledger, stop: threading.Event) -> None:
        self.inner, self._stop = inner, stop

    def check(self, run_id: str, estimate: float) -> None:
        if self._stop.is_set():
            raise RunTimeout("the run was stopped by its wall clock")
        self.inner.check(run_id, estimate)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@dataclass(frozen=True)
class Job:
    """One engine run: `runner(job, on_event)` does it in a worker thread. `stop` is set when
    the watchdog gives the run up; a runner should end at its next chance."""

    run_id: str
    run_dir: Path
    video: Path
    opts: RunOptions
    duration: float
    ledger: Ledger
    resumed: bool = False
    stop: threading.Event = field(default_factory=threading.Event, compare=False)


Runner = Callable[[Job, Callable[[Event], None]], object]
AfterRun = Callable[[Job], "dict | None"]  # what it returns is added to the `done` event


def _run_pipeline(job: Job, on_event: Callable[[Event], None]) -> None:
    """The API's one call into the engine, with real clients recording to the job's ledger
    (made to refuse spend once the run is stopped)."""
    ledger: Any = StopLedger(job.ledger, job.stop)
    tf = TokenFactory.from_settings(ledger)
    tavily = Tavily.from_settings(ledger)
    try:
        run_engine(
            job.video,
            job.run_dir,
            job.opts,
            tf=tf,
            speech=NemoSpeechHTTP.from_settings(),
            tavily=tavily,
            ledger=ledger,
            on_event=on_event,
            run_id=job.run_id,
        )
    finally:
        for client in (tf, tavily):
            close = getattr(client, "close", None)
            if close is not None:
                close()


# -- one run's event log -----------------------------------------------------------------------


def read_events(run_dir: Path) -> list[dict]:
    """The events in run_dir/events.jsonl."""
    try:
        return _parse_events((run_dir / EVENTS).read_bytes())
    except FileNotFoundError:
        return []


def _parse_events(raw: bytes) -> list[dict]:
    """One JSON event a line; a line cut short by a stopped process is skipped."""
    out = []
    for line in raw.split(b"\n"):
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and isinstance(ev.get("type"), str):
            out.append(ev)
    return out


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class LiveRun:
    """The events of a run going in this process: numbered on from what events.jsonl already
    holds, appended to it, kept in memory and handed to every listener. Lives on the event
    loop."""

    def __init__(self, run_id: str, run_dir: Path, scrub: Callable[[str], str]) -> None:
        self.run_id, self.run_dir, self.scrub = run_id, run_dir, scrub
        path = run_dir / EVENTS
        raw = path.read_bytes() if path.exists() else b""
        self.events = _parse_events(raw)
        seqs = [e["seq"] for e in self.events if isinstance(e.get("seq"), int)]
        self._seq = max(seqs, default=-1) + 1
        self._listeners: set[asyncio.Queue[dict | None]] = set()
        self._log: BinaryIO | None = open(path, "ab")  # open for the whole run
        if raw and not raw.endswith(b"\n"):  # end the line a stopped process cut short
            self._log.write(b"\n")
        self.closed = False
        self.done: dict | None = None  # the engine's `done`, held until after_run has run
        self.stage: str | None = None  # the last stage started
        self.error_stage: str | None = None  # the stage the engine said it failed in

    def publish(self, type_: str, stage: str | None, data: dict, ts: float | None = None) -> dict:
        ev = {**data, "type": type_, "stage": stage, "ts": ts or time.time(), "seq": self._seq}
        self._seq += 1
        line = json.dumps(ev, ensure_ascii=False, default=str)
        ev = json.loads(line)  # what the file holds is what listeners get
        if self._log is not None:
            try:
                self._log.write(line.encode("utf-8") + b"\n")
                self._log.flush()
            except OSError:
                log.exception("live run %s: events.jsonl write failed", self.run_id)
        self.events.append(ev)
        for q in self._listeners:
            q.put_nowait(ev)
        return ev

    def on_engine_event(self, ev: Event) -> None:
        """An engine event, cleaned for visitors. The engine's own `error` event is held back:
        the failure is reported once, cleaned, when the runner raises."""
        if self.closed:
            return
        data = dict(ev.data)
        if ev.type == "error":
            self.error_stage = ev.stage
            return
        if ev.type == "stage_start":
            self.stage = ev.stage
        elif ev.type == "warning":
            data = {k: self.scrub(v) if isinstance(v, str) else v for k, v in data.items()}
        elif ev.type == "done":  # published by the executor once after_run has run
            data = {k: v for k, v in data.items() if k not in ("output", "manifest")}
            summary = data.get("summary")
            if isinstance(summary, dict):  # model spend and searches, nothing priced
                data["summary"] = public_summary(summary)
            self.done = {**data, "run_id": self.run_id}
            return
        self.publish(ev.type, ev.stage, data, ev.ts)

    def fail(self, e: BaseException) -> None:
        """error.json, then the `error` event: {kind, message (no local paths), scope}."""
        stage = self.error_stage or self.stage
        info: dict[str, Any] = {"type": type(e).__name__}
        if isinstance(e, BudgetExceeded):
            info.update(message=budget_message(e.scope), stage=stage, scope=e.scope)
        elif isinstance(e, RunTimeout):
            info.update(message=str(e), stage=stage)
        else:
            info.update(message=self.scrub(str(e))[:500] or type(e).__name__, stage=stage)
        try:
            _write_json(self.run_dir / ERROR, info)
        except OSError:
            log.exception("live run %s: error.json write failed", self.run_id)
        data = {"kind": info["type"], "message": info["message"]}
        if "scope" in info:
            data["scope"] = info["scope"]
        self.publish("error", stage, data)

    def subscribe(self) -> tuple[list[dict], asyncio.Queue[dict | None]]:
        """Everything so far and a queue of what follows (None: the run is over)."""
        q: asyncio.Queue[dict | None] = asyncio.Queue()
        if self.closed:
            q.put_nowait(None)
        else:
            self._listeners.add(q)
        return list(self.events), q

    def unsubscribe(self, q: asyncio.Queue[dict | None]) -> None:
        self._listeners.discard(q)

    def close(self) -> None:
        self.closed = True
        if self._log is not None:
            self._log.close()
            self._log = None
        for q in self._listeners:
            q.put_nowait(None)
        self._listeners.clear()


class Executor:
    """One live run at a time per process. The lock is never waited on: a second run is
    refused (409), not queued. A run still going after `wall_clock` seconds is given up.
    `changed(run_dir)` is told when a run starts and ends (the run list cache)."""

    def __init__(self, changed: Callable[[Path], None] | None = None) -> None:
        self.lock = asyncio.Lock()
        self.active: dict[str, LiveRun] = {}
        self.wall_clock = WALL_CLOCK_S
        self.changed = changed
        self.after_run: list[AfterRun] = []  # in the worker thread, after a run that succeeded
        self._tasks: set[asyncio.Task] = set()

    def _changed(self, run_dir: Path) -> None:
        if self.changed is not None:
            self.changed(run_dir)

    @property
    def busy(self) -> bool:
        return self.lock.locked()

    def get(self, run_id: str) -> LiveRun | None:
        return self.active.get(run_id)

    async def start(
        self, job: Job, runner: Runner, scrub: Callable[[str], str], first: dict
    ) -> LiveRun:
        """Claim the executor, publish the `run` event and start the engine thread. Does not
        suspend: whatever the caller checked just before still holds."""
        if self.lock.locked():
            raise busy()
        await self.lock.acquire()  # free and never waited on: returns at once
        try:
            run = LiveRun(job.run_id, job.run_dir, scrub)
            run.publish("run", None, first)
        except BaseException:
            self.lock.release()
            raise
        self.active[job.run_id] = run
        self._changed(job.run_dir)
        task = asyncio.create_task(self._drive(run, job, runner), name=f"live {job.run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run

    async def _drive(self, run: LiveRun, job: Job, runner: Runner) -> None:
        loop = asyncio.get_running_loop()

        def on_event(ev: Event) -> None:  # the worker thread: hand over, never block
            try:
                loop.call_soon_threadsafe(run.on_engine_event, ev)
            except RuntimeError:  # the loop is closed: shutting down
                pass

        work = loop.run_in_executor(None, self._work, runner, job, on_event)
        try:
            # the wall clock is the wait running out, never an exception the run raised (an
            # engine TimeoutError is the engine's error)
            await asyncio.wait({work}, timeout=self.wall_clock)
            if not work.done():
                job.stop.set()  # its next model call is refused; what it reports is dropped
                work.add_done_callback(_late(job.run_id))
                log.warning("live run %s given up after %g s", job.run_id, self.wall_clock)
                limit = _wait_words(math.ceil(self.wall_clock))
                raise RunTimeout(f"This description took longer than {limit} and was stopped.")
            extra = work.result()
        except RunTimeout as e:
            run.fail(e)
        except Exception as e:
            if isinstance(e, BudgetExceeded):
                log.warning("live run %s stopped by the %s cap", job.run_id, e.scope)
            else:
                log.exception("live run %s failed in %s", job.run_id, run.error_stage or run.stage)
            run.fail(e)
        else:
            run.publish("done", None, {**(run.done or {"run_id": run.run_id}), **extra})
        finally:  # free before the listeners hear the end, so they may start the next run
            self.lock.release()
            self.active.pop(run.run_id, None)
            run.close()
            self._changed(job.run_dir)

    def _work(self, runner: Runner, job: Job, on_event: Callable[[Event], None]) -> dict:
        """The run, then the after_run hooks (one failing is logged, never fails the run). A run
        the wall clock stopped skips them: nobody waits for it any more."""
        runner(job, on_event)
        extra: dict = {}
        if job.stop.is_set():
            return extra
        for hook in self.after_run:
            try:
                extra.update(hook(job) or {})
            except Exception:
                log.exception("live run %s: an after-run step failed", job.run_id)
        return extra


def _late(run_id: str) -> Callable[[asyncio.Future], None]:
    """What a given-up run's thread did in the end, for the log."""

    def done(f: asyncio.Future) -> None:
        e = None if f.cancelled() else f.exception()
        log.info("live run %s: its thread ended after the wall clock (%r)", run_id, e)

    return done


# -- what the routes share ---------------------------------------------------------------------


class Live:
    """The live routes' state. Tests swap `runner`, `probe`, `ledger`, `limiter`, `heartbeat`
    and the limits."""

    def __init__(self, settings: ApiSettings, store: RunStore) -> None:
        self.settings, self.store = settings, store
        self.runner: Runner = _run_pipeline
        self.probe: Callable[[Path], media.Probe] = clip.probe
        self.trim: Callable[[Path, Path, float], Path] = clip.trim
        self.limiter = RateLimiter()
        self.refusals = RateLimiter(
            per_client=REFUSALS_PER_HOUR, client_window=HOUR, per_day=1_000_000
        )
        self.executor = Executor(changed=store.invalidate)
        store.active = lambda run_id: self.executor.get(run_id) is not None
        # the loudness envelope, worked out once a run has published (in its worker thread)
        self.envelope: Callable[[Path, Path, float | None], object] | None = facts.keep_loudness
        self.executor.after_run.append(self._keep_loudness)
        self.heartbeat = HEARTBEAT_S
        self.max_bytes = MAX_UPLOAD_BYTES
        self.max_seconds = MAX_SECONDS
        self.max_sample_seconds = MAX_SAMPLE_SECONDS
        self.max_uploads = MAX_UPLOADS
        self.upload_deadline = UPLOAD_DEADLINE_S
        self.uploading = 0  # uploads being received and checked now
        self.upload_keys: set[str] = set()  # whose
        self._ledger: Ledger | None = None

    @property
    def ledger(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger(
                get_settings().lull_home / "ledger.db", tavily_run=SEARCHES_PER_RUN
            )
        return self._ledger

    @ledger.setter
    def ledger(self, value: Ledger) -> None:
        self._ledger = value

    def scrubber(self, run_dir: Path) -> Scrubber:
        return Scrubber(run_dir.absolute(), default_roots(self.settings.lull_data))

    def _keep_loudness(self, job: Job) -> None:
        if self.envelope is not None and (job.run_dir / MANIFEST).is_file():
            self.envelope(job.run_dir, job.video, job.duration)

    def check_budget(self, run_id: str, duration: float) -> None:
        try:
            self.ledger.check(run_id, budget_estimate(duration))
        except BudgetExceeded as e:
            raise LiveError(402, "budget", budget_message(e.scope), scope=e.scope) from None

    def check_rate(self, key: str) -> None:
        stop = self.limiter.retry_after(key)
        if stop is None:
            return
        limit, wait = stop
        if limit == "client":
            message = (
                f"You have started {self.limiter.per_client} live descriptions in the last "
                f"hour. Try again in {_wait_words(wait)}, or open a recorded run."
            )
        else:
            message = (
                f"The live demo has run its {self.limiter.per_day} descriptions for today. "
                f"Try again in {_wait_words(wait)}, or open a recorded run."
            )
        raise LiveError(429, "rate_limited", message, headers={"Retry-After": str(wait)})

    def check_refusals(self, key: str) -> None:
        stop = self.refusals.retry_after(key)
        if stop is not None:
            wait = stop[1]
            message = (
                f"Too many of your uploads were refused in the last hour. Try again in "
                f"{_wait_words(wait)}, or open a recorded run."
            )
            raise LiveError(429, "rate_limited", message, headers={"Retry-After": str(wait)})

    async def start(self, job: Job, first: dict) -> tuple[list[dict], LiveRun, asyncio.Queue]:
        run = await self.executor.start(job, self.runner, self.scrubber(job.run_dir), first)
        backlog, q = run.subscribe()
        return backlog, run, q


def _live(request: Request) -> Live:
    return request.app.state.live


def client_ip(request: Request, hops: int = 0) -> str:
    """The visitor's address: the socket peer or, behind `hops` trusted proxies (Cloud Run:
    1), the entry the outermost of them appended to X-Forwarded-For."""
    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for", "")
        chain = [p.strip() for p in forwarded.split(",") if p.strip()]
        if len(chain) >= hops:
            return chain[-hops]
    return request.client.host if request.client else "unknown"


RUN_ID_BYTES = 12  # 96 random bits


def new_run_id() -> str:
    """A new live run id: `live-<UTC date>-<24 hex>`. The id is the read capability for a
    visitor's upload (a finished run replays without a token), so it must not be guessable.
    Ids of the old `live-<date>-<time>-<6 hex>` form still read: the store takes any safe name."""
    return f"live-{datetime.now(UTC):%Y%m%d}-{secrets.token_hex(RUN_ID_BYTES)}"


# -- the upload --------------------------------------------------------------------------------


class _TooLarge(Exception):
    pass


async def _read_form(request: Request, limit: int) -> FormData:
    """The form, with the body counted as it arrives: past the limit the upload is dropped
    (413) instead of spooled to its end."""
    seen = 0
    receive = request.receive

    async def counted() -> Message:
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit + FORM_SLACK:
                raise _TooLarge
        return message

    try:
        return await Request(request.scope, counted).form(max_files=1, max_fields=8)
    except _TooLarge:
        raise too_large(limit) from None
    except ClientDisconnect:
        raise LiveError(400, "disconnected", "The upload was cut off before it finished.") from None
    except StarletteHTTPException as e:  # a body the multipart parser refuses
        raise LiveError(400, "invalid", f"The upload could not be read: {e.detail}") from None


def _safe_name(filename: str | None) -> str | None:
    """The upload's file name made safe to store, or None when it is not a video we take."""
    base = re.split(r"[\\/]", filename or "")[-1]
    stem, dot, ext = base.rpartition(".")
    ext = f".{ext.lower()}"
    if not dot or ext not in FORMATS:
        return None
    stem = re.sub(r"[^\w. -]+", "_", stem).strip(" ._-")[:60] or "video"
    return stem + ext


def _save(src: BinaryIO, dst: Path, limit: int) -> int:
    src.seek(0)
    size = 0
    with open(dst, "wb") as out:
        while chunk := src.read(1 << 20):
            size += len(chunk)
            if size > limit:
                raise too_large(limit)
            out.write(chunk)
    return size


async def _store(fn: Callable[..., Any], *args: Any) -> Any:
    """Write the video into the run folder; a disk that fails (a timeout on a network mount,
    say) is our trouble, not the visitor's: 503, not counted against them."""
    try:
        return await anyio.to_thread.run_sync(fn, *args)
    except OSError:
        log.exception("live: storing an upload failed")
        raise LiveError(
            503,
            "storage",
            "The video could not be stored just now. Try again in a minute.",
            headers={"Retry-After": "60"},
        ) from None


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:  # another filesystem, or one without links
        shutil.copyfile(src, dst)


def _rate(voice: str) -> float:
    try:
        return voice_wps(voice)[0]
    except ValueError:  # voices.json does not load
        return FALLBACK_WPS


def _known_voice(name: str) -> bool:
    try:
        return voice_wps(name)[1] != "fallback"
    except ValueError:
        log.warning("voices.json does not load; only the built-in voices are offered")
        return name in DEFAULT_WPS


def _options(form: FormData) -> RunOptions:
    mode, voice = form.get("mode") or "standard", form.get("voice") or "Sofia"
    if mode not in ("standard", "extended"):
        raise LiveError(422, "invalid", "mode must be standard or extended.")
    if not isinstance(voice, str) or not _known_voice(voice):
        voices = ", ".join(sorted(DEFAULT_WPS))
        raise LiveError(422, "invalid", f"Unknown voice. Choose one of: {voices}.")
    return RunOptions(mode=mode, voice=voice)


def _new_run_dir(run_dir: Path) -> Path:
    """The run folder, marked pending (left out of the run list) until the run starts."""
    run_dir.mkdir(parents=True)
    (run_dir / PENDING).touch()
    (run_dir / INPUT).mkdir()
    return run_dir / INPUT


def unreadable() -> LiveError:
    return LiveError(422, "unreadable", f"This file could not be read as a video. {FORMAT_MESSAGE}")


def too_long(duration: float, limit: float) -> LiveError:
    return LiveError(
        422,
        "too_long",
        f"This video is {duration:.1f} s long. Live descriptions take videos up to {limit:.0f} s.",
    )


async def _probe(live: Live, run_id: str, video: Path) -> media.Probe:
    try:
        info = await anyio.to_thread.run_sync(live.probe, video)
        media.assert_safe_container(info)  # the engine's rule, right after probing
    except Exception:
        log.info("live run %s: %s does not probe", run_id, video.name, exc_info=True)
        raise unreadable() from None
    if not info.duration > 0:
        raise unreadable()
    return info


async def _cut(live: Live, run_id: str, video: Path, limit: float) -> media.Probe:
    """Check the video, then replace it by a remuxed copy cut at `limit` seconds, and return
    what probing that copy says: the container's own claim is not trusted."""
    info = await _probe(live, run_id, video)
    if info.duration > limit:
        raise too_long(info.duration, limit)
    cut = video.with_name(f".trim-{video.name}")
    try:
        await anyio.to_thread.run_sync(live.trim, video, cut, limit)
    except Exception:
        log.info("live run %s: %s does not remux", run_id, video.name, exc_info=True)
        raise unreadable() from None
    info = await _probe(live, run_id, cut)
    if info.duration > limit + TRIM_SLACK:
        raise too_long(info.duration, limit)
    os.replace(cut, video)
    return info


def _sample(settings: ApiSettings, sample_id: str) -> Path | None:
    if not SAMPLE_RE.fullmatch(sample_id):
        return None
    path = settings.samples_dir / f"{sample_id}.mp4"
    return path if path.is_file() else None


async def _accept(
    live: Live, request: Request, run_id: str, run_dir: Path, client: str
) -> tuple[Job, str]:
    """Receive the form (within the upload deadline: 408), put the video in run_dir/input,
    probe and cut it, check the spend caps and write request.json, which keeps the sha256 of
    the edit token returned with the job. Raises LiveError; the caller then removes run_dir."""
    received = None
    with anyio.move_on_after(live.upload_deadline):
        received = await _receive(live, request, run_dir)
    if received is None:  # the deadline, not a TimeoutError from the disk (that is ours)
        raise upload_timeout(live.upload_deadline)
    opts, video, source = received
    limit = live.max_sample_seconds if source["kind"] == "sample" else live.max_seconds
    info = await _cut(live, run_id, video, limit)
    live.check_budget(run_id, info.duration)
    token, token_hash = new_token()
    _write_json(
        run_dir / REQUEST,
        {
            "run_id": run_id,
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "source": source,
            "input": f"{INPUT}/{video.name}",
            "options": {"mode": opts.mode, "voice": opts.voice},
            "video": {
                "duration": info.duration,
                "width": info.width,
                "height": info.height,
                "has_audio": info.has_audio,
            },
            "client": client[:16],
            "wps": _rate(opts.voice),  # what the engine describes it at (lull.voices)
            TOKEN_KEY: token_hash,
        },
    )
    return Job(run_id, run_dir, video, opts, info.duration, live.ledger), token


async def _receive(
    live: Live, request: Request, run_dir: Path
) -> tuple[RunOptions, Path, dict[str, Any]]:
    """The form read and checked, its video (upload or sample) stored in run_dir/input."""
    form = await _read_form(request, live.max_bytes)
    try:
        opts = _options(form)
        upload, sample = form.get("file"), form.get("sample")
        has_file = isinstance(upload, UploadFile)
        has_sample = isinstance(sample, str) and sample != ""
        if has_file == has_sample or (upload is not None and not has_file):
            raise LiveError(422, "invalid", "Send one video file or one sample id.")
        if isinstance(upload, UploadFile):
            name = _safe_name(upload.filename)
            if name is None:
                raise LiveError(422, "format", FORMAT_MESSAGE)
            if upload.size is not None and upload.size > live.max_bytes:
                raise too_large(live.max_bytes)
            video = _new_run_dir(run_dir) / name
            size = await _store(_save, upload.file, video, live.max_bytes)
            source: dict[str, Any] = {"kind": "upload", "name": name, "bytes": size}
        else:
            assert isinstance(sample, str)
            path = _sample(live.settings, sample)
            if path is None:
                raise LiveError(422, "sample", "There is no sample with that id.")
            video = _new_run_dir(run_dir) / path.name
            await _store(_link_or_copy, path, video)
            source = {"kind": "sample", "id": sample, "name": path.name}
    finally:
        await form.close()
    return opts, video, source


def _resume_job(live: Live, run_id: str, run_dir: Path) -> Job:
    req = _read_json(run_dir / REQUEST)
    try:
        rel = Path(req["input"])
        duration = float(req["video"]["duration"])
        opts = RunOptions(**req["options"])
    except (TypeError, KeyError, ValueError, ValidationError):
        rel, duration, opts = None, 0.0, None
    video = run_dir / rel if rel is not None else None
    ok = rel is not None and not rel.is_absolute() and ".." not in rel.parts
    ok = ok and not (run_dir / PENDING).exists()  # an upload that never finished
    if not ok or opts is None or video is None or not video.is_file():
        raise LiveError(404, "not_found", "This run stopped before it finished and cannot resume.")
    return Job(run_id, run_dir, video, opts, duration, live.ledger, resumed=True)


# -- the stream --------------------------------------------------------------------------------


def _frame(ev: dict) -> str:
    head = f"id: {ev['seq']}\n" if isinstance(ev.get("seq"), int) else ""
    return f"{head}event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"


async def _frames(
    backlog: list[dict],
    run: LiveRun | None,
    q: asyncio.Queue[dict | None] | None,
    heartbeat: float,
    secret: dict | None = None,
) -> AsyncIterator[str]:
    try:
        for i, ev in enumerate(backlog):
            yield _frame({**ev, **secret} if secret and i == 0 and ev["type"] == "run" else ev)
        if q is None:
            return
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), heartbeat)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if ev is None:
                return
            yield _frame(ev)
    finally:
        if run is not None and q is not None:
            run.unsubscribe(q)


def _sse(
    backlog: list[dict],
    run: LiveRun | None = None,
    q: asyncio.Queue[dict | None] | None = None,
    heartbeat: float = HEARTBEAT_S,
    secret: dict | None = None,
) -> StreamingResponse:
    return StreamingResponse(
        _frames(backlog, run, q, heartbeat, secret),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def _after(request: Request, after: int | None) -> int | None:
    if after is not None:
        return after
    try:
        return int(request.headers.get("last-event-id", ""))
    except ValueError:
        return None


def _opts(job: Job) -> dict[str, str]:
    return {"mode": job.opts.mode, "voice": job.opts.voice}


def _since(events: list[dict], after: int | None) -> list[dict]:
    if after is None:
        return events
    return [e for e in events if not (isinstance(e.get("seq"), int) and e["seq"] <= after)]


# -- routes ------------------------------------------------------------------------------------

router = APIRouter()


@router.get("/v1/samples")
def list_samples(request: Request) -> list[dict[str, str]]:
    root = _live(request).settings.samples_dir
    if not root.is_dir():
        return []
    paths = sorted(p for p in root.glob("*.mp4") if p.is_file() and SAMPLE_RE.fullmatch(p.stem))
    return [{"id": p.stem} for p in paths]


def _check_headers(request: Request, limit: int) -> None:
    """A Content-Length, when sent, within the limit (413); a form body (422)."""
    length = request.headers.get("content-length")
    if length is not None:
        if not length.strip().isdigit():
            raise LiveError(400, "invalid", "The Content-Length header is not a number.")
        if int(length) > limit + FORM_SLACK:
            raise too_large(limit)
    ctype = request.headers.get("content-type", "").lower()
    if ctype and not ctype.startswith(FORM_TYPES):
        raise LiveError(422, "invalid", "Send the video as multipart/form-data.")


@router.post("/v1/runs")
async def create_run(request: Request) -> StreamingResponse:
    live = _live(request)
    key = live.limiter.client_key(client_ip(request, live.settings.lull_proxy_hops))
    if live.executor.busy:
        raise busy()
    live.check_rate(key)
    live.check_refusals(key)
    _check_headers(request, live.max_bytes)
    # the slots, only for a request whose headers hold up
    if key in live.upload_keys:
        raise one_upload()
    if live.uploading >= live.max_uploads:
        raise uploads_busy()

    run_id = new_run_id()
    run_dir = live.settings.runs_dir / run_id
    live.uploading += 1
    live.upload_keys.add(key)
    try:
        try:
            job, token = await _accept(live, request, run_id, run_dir, key)
        except BaseException as e:
            # an upload slot used without starting a run counts against the visitor, however
            # it ended (refused, cut off, crashed), unless it was the spend caps, a busy
            # executor or our storage
            if not (isinstance(e, LiveError) and e.status in NOT_COUNTED):
                live.refusals.hit(key)
            raise
        # checked again with the claim, in one step: the upload took a while
        if live.executor.busy:
            raise busy()
        live.check_rate(key)
        (run_dir / PENDING).unlink()
        first = {"run_id": run_id, "duration": job.duration, "options": _opts(job)}
        backlog, run, q = await live.start(job, first)
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    finally:
        live.uploading -= 1
        live.upload_keys.discard(key)
    live.limiter.hit(key)
    # the token goes to whoever started the run, in this stream only: never stored or replayed
    return _sse(backlog, run, q, live.heartbeat, secret={"edit_token": token})


@router.get("/v1/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int | None = None) -> Response:
    live = _live(request)
    after = _after(request, after)
    found = live.store.find(run_id)
    if found is None:
        raise LiveError(404, "not_found", "There is no run with that id.")
    kind, run_dir = found

    active = live.executor.get(run_id) if kind == "live" else None
    if active is not None:
        backlog, q = active.subscribe()
        return _sse(_since(backlog, after), active, q, live.heartbeat)

    finished = (run_dir / MANIFEST).exists() or (run_dir / ERROR).exists()
    if kind == "showcase" or finished:
        events = read_events(run_dir)
        if not events:
            raise LiveError(404, "not_found", "No progress was recorded for this run.")
        rest = _since(events, after)
        if not rest:  # seen to its end: 204 tells an EventSource to stop reconnecting
            return Response(status_code=204)
        return _sse(rest)

    # resuming spends: only the run's owner may (a finished run's replay stays open to all)
    if not holds_token(run_dir, request):
        raise LiveError(403, "forbidden", "Only the person who started this run can resume it.")
    job = _resume_job(live, run_id, run_dir)
    if live.executor.busy:
        raise busy()
    live.check_budget(run_id, job.duration)
    first = {"run_id": run_id, "duration": job.duration, "options": _opts(job), "resumed": True}
    backlog, run, q = await live.start(job, first)
    return _sse(_since(backlog, after), run, q, live.heartbeat)
