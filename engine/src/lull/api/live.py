"""Live runs: one description at a time, its progress streamed as Server-Sent Events.

POST /v1/runs takes an upload (or a sample id). Before anything streams, it checks that no
other run is going (409) and at most MAX_UPLOADS uploads are being received (503), the
visitor's rate limits (429: runs started, and uploads refused), the size (413), the format, the
limits on what decoding it costs (422 unsupported: lull.api.clip.over_limits) and the duration
(422: uploads up to MAX_SECONDS, samples MAX_SAMPLE_SECONDS) and the spend caps (402). The
run gets its own copy, one bounded transcode cut at that limit and probed again
(lull.api.clip.transcode: at most 720p, 30 frames a second, H.264/AAC), so what counts is the
copy, not what the container claims. Then it answers with
text/event-stream: a `run` event, every engine event as it happens (stage_start / stage_end /
call / cue / warning), a final `done`, `error` or `stopped`, and a `: ping` comment whenever
the stream has been quiet for 15 s.

The engine runs in a worker thread. Its callback only hands each event to the event loop
(call_soon_threadsafe). On the loop the event gets its sequence number, is appended to
runs/<id>/events.jsonl and goes to every listener. GET /v1/runs/{id}/events replays that file
and follows the run if it is still going in this process. Replaying a published run is open to
anyone; following a run still going and replaying a failed one is for its owner only (the
edit token). At most MAX_FOLLOWERS
streams follow one run and MAX_STREAMS are open in all (429 busy past that): a stream holds a
request slot for as long as the run lasts. A stopped run (no
manifest, no error.json) is restarted for its owner inside that request: the engine's stage
cache skips the stages already done, and the run's spend cap counts every attempt.

A run nobody is listening to any more (the page that followed it was closed) is stopped: a
`stopped` event, no error.json, so it reads `stopped`. Its thread is refused any further model
call or search and ends at the next one or at its next stage start (a stage it finished is
cached; the one it was in is not). Until it ends, up to the run's wall clock, other runs get
409 busy; a restart of the same run waits for it.

A run still going after LULL_RUN_WALL_CLOCK_S (WALL_CLOCK_S by default) is given up: error
event, error.json, the executor freed; its thread cannot be killed, but its next model call or
search is refused and anything it reports after that is dropped.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import secrets
import shutil
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
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
LENGTH_SLACK = 0.2  # a copy more than 20 % off the length the file states is refused
FORMATS = (".mp4", ".mov", ".webm", ".mkv")
HEARTBEAT_S = 15.0
BUSY_RETRY_S = 60
MAX_UPLOADS = 2  # uploads received and checked at once
UPLOADS_RETRY_S = 15
UPLOAD_DEADLINE_S = 60.0  # to receive the whole body (the visitor's part)
STORE_DEADLINE_S = 60.0  # to store it (ours: a slow disk is never counted against them)
FORM_TYPES = ("multipart/form-data", "application/x-www-form-urlencoded")
REFUSALS_PER_HOUR = 10  # uploads refused after they were read, per visitor
WALL_CLOCK_S = 900.0  # the default of LULL_RUN_WALL_CLOCK_S
SEARCHES_PER_RUN = 3  # web searches a live run may make (the day cap is LULL_TAVILY_DAY)
NOT_COUNTED = (402, 409, 503)  # refusals that are not the visitor's doing
OPEN_CHECK_ID, OPEN_CHECK_USD = "live-open-check", 1e-6  # "is any spend left at all"
MAX_FOLLOWERS = 3  # GET .../events streams following one run
MAX_STREAMS = 8  # GET .../events streams in all (follows and replays)
FOLLOW_RETRY_S = 30

EVENTS, REQUEST, ERROR, MANIFEST, INPUT = (
    "events.jsonl",
    "request.json",
    "error.json",
    "manifest.json",
    "input",
)
SAMPLE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")  # called with fullmatch
BUSY_MESSAGE = "Another description is running. Try again in a minute, or open a recorded run."
CLIENT_HEADER = "X-Lull-Client"  # any value; a cross-site form cannot send it without a preflight
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


def check_client_header(request: Request) -> None:
    """What starts work (a run, a fork) carries X-Lull-Client (400 client_header otherwise): a
    custom header makes a browser ask first (CORS preflight), which LULL_WEB_ORIGINS answers,
    so another site cannot spend its visitors' limits here with a plain form post."""
    if not request.headers.get(CLIENT_HEADER, "").strip():
        raise LiveError(
            400,
            "client_header",
            f"Send the {CLIENT_HEADER} header (any value, such as web) with this request.",
        )


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


def wait_words(seconds: int) -> str:
    if seconds < 3600:
        n = max(1, math.ceil(seconds / 60))
        return f"{n} minute{'s' if n > 1 else ''}"
    n = math.ceil(seconds / 3600)
    return f"{n} hour{'s' if n > 1 else ''}"


# -- the engine --------------------------------------------------------------------------------


class RunTimeout(Exception):
    """The run went on past the wall clock and was given up."""


class RunStopped(BaseException):  # noqa: N818 - a signal, like CancelledError
    """Raised in the engine thread of a run nobody listens to, at its next model call or web
    search (refused) or its next stage start, whichever comes first. A BaseException, so the
    engine's own `except Exception` neither catches it nor turns it into a held cue that the
    stage cache would keep."""


STOPPED_MESSAGE = (
    "The description stopped when the page following it closed. Open it again to carry on: "
    "the steps already done are kept."
)


class StopLedger:
    """A run's ledger that refuses every further spend once the run is let go: how a run whose
    thread cannot be killed is ended (the engine's model and search clients check the ledger
    before every call, retries included). `stop` (the wall clock) raises RunTimeout; `cancel`
    (nobody listens) raises RunStopped."""

    def __init__(
        self, inner: Ledger, stop: threading.Event, cancel: threading.Event | None = None
    ) -> None:
        self.inner, self._stop, self._cancel = inner, stop, cancel

    def _refuse(self) -> None:
        if self._cancel is not None and self._cancel.is_set():
            raise RunStopped("the run was stopped: nobody is listening")
        if self._stop.is_set():
            raise RunTimeout("the run was stopped by its wall clock")

    def check(self, run_id: str, estimate: float) -> None:
        self._refuse()
        self.inner.check(run_id, estimate)

    def check_searches(self, run_id: str) -> None:
        self._refuse()
        self.inner.check_searches(run_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@dataclass(frozen=True)
class Job:
    """One engine run: `runner(job, on_event)` does it in a worker thread. `stop` is set when
    the watchdog gives the run up (no more spend; a runner should end at its next chance);
    `cancel` when nobody listens any more (no more spend either, and it ends at its next model
    call or stage start: RunStopped)."""

    run_id: str
    run_dir: Path
    video: Path
    opts: RunOptions
    duration: float
    ledger: Ledger
    resumed: bool = False
    stop: threading.Event = field(default_factory=threading.Event, compare=False)
    cancel: threading.Event = field(default_factory=threading.Event, compare=False)


Runner = Callable[[Job, Callable[[Event], None]], object]
AfterRun = Callable[[Job], "dict | None"]  # what it returns is added to the `done` event


def _run_pipeline(job: Job, on_event: Callable[[Event], None]) -> None:
    """The API's one call into the engine, with real clients recording to the job's ledger
    (made to refuse spend once the run is stopped)."""
    ledger: Any = StopLedger(job.ledger, job.stop, job.cancel)
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
        self.unwatched = asyncio.Event()  # set when the last listener leaves
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
        if not self._listeners and not self.closed:
            self.unwatched.set()

    @property
    def watched(self) -> bool:
        return bool(self._listeners)

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
    refused (409), not queued. A run nobody listens to is stopped: it is let go at once (it
    reads stopped) but its thread, refused any further spend, lingers (`lingers`) until it ends,
    and until then the executor stays busy for any other run, up to the run's wall clock; past
    that the thread is abandoned (logged) and no longer counts. A run still going after
    `wall_clock` seconds is given up and frees the executor at once. A restart of a run whose
    own thread lingers waits for it. `changed(run_dir)` is told when a run starts and ends (the
    run list cache)."""

    def __init__(self, changed: Callable[[Path], None] | None = None) -> None:
        self.lock = asyncio.Lock()
        self.active: dict[str, LiveRun] = {}
        self.lingering: dict[str, asyncio.Future] = {}  # threads of runs already let go
        self._abandon_at: dict[str, float] = {}  # loop time: when each stops counting
        self.wall_clock = WALL_CLOCK_S
        self.changed = changed
        self.after_run: list[AfterRun] = []  # in the worker thread, after a run that succeeded
        self._tasks: set[asyncio.Task] = set()

    def _changed(self, run_dir: Path) -> None:
        if self.changed is not None:
            self.changed(run_dir)

    @property
    def busy(self) -> bool:
        """A run is going, or a stopped run's thread still is."""
        return self.lock.locked() or bool(self.lingering)

    def refuses(self, run_id: str) -> bool:
        """Whether `run_id` cannot start now: a run is going, or a thread of another run
        lingers (its own lingering thread is waited for)."""
        return self.lock.locked() or any(r != run_id for r in self.lingering)

    def get(self, run_id: str) -> LiveRun | None:
        return self.active.get(run_id)

    def lingers(self, run_id: str) -> bool:
        """Whether a thread of this run is still going after the run was let go."""
        return run_id in self.lingering

    def _linger(self, run_id: str, work: asyncio.Future, deadline: float) -> None:
        """Keep the thread of a run let go counted until it ends or `deadline` (loop time)."""
        loop = asyncio.get_running_loop()
        work.add_done_callback(_late(run_id))
        if loop.time() >= deadline:  # given up by its wall clock: it no longer counts
            return
        self.lingering[run_id], self._abandon_at[run_id] = work, deadline

        def forget(f: asyncio.Future, abandoned: bool = False) -> None:
            if self.lingering.get(run_id) is f:
                del self.lingering[run_id]
                self._abandon_at.pop(run_id, None)
                if abandoned:
                    log.warning("live run %s: its thread is abandoned at its wall clock", run_id)

        work.add_done_callback(forget)
        loop.call_at(deadline, forget, work, True)

    async def start(
        self, job: Job, runner: Runner, scrub: Callable[[str], str], first: dict
    ) -> LiveRun:
        """Claim the executor, publish the `run` event and start the engine thread. Does not
        suspend: whatever the caller checked just before still holds."""
        if self.refuses(job.run_id):
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

    async def _until(self, run: LiveRun, fut: asyncio.Future, deadline: float) -> str:
        """Wait for `fut` until `deadline` (loop time): "done", "unwatched" (nobody listens any
        more: checked again, a listener may have come back) or "late"."""
        loop = asyncio.get_running_loop()
        await asyncio.sleep(0)  # what the engine thread queued (its last events) runs first
        while True:
            left = deadline - loop.time()
            if fut.done():
                return "done"
            if left <= 0:
                return "late"
            unwatched = asyncio.ensure_future(run.unwatched.wait())
            try:
                await asyncio.wait({fut, unwatched}, timeout=left, return_when="FIRST_COMPLETED")
            finally:
                unwatched.cancel()
            if fut.done():
                return "done"
            if run.unwatched.is_set():
                if not run.watched:
                    return "unwatched"
                run.unwatched.clear()

    async def _drive(self, run: LiveRun, job: Job, runner: Runner) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.wall_clock

        def on_event(ev: Event) -> None:  # the worker thread: hand over, never block
            if ev.type == "stage_start" and job.cancel.is_set():
                raise RunStopped(ev.stage)  # past the engine's `except Exception`: it ends here
            try:
                loop.call_soon_threadsafe(run.on_engine_event, ev)
            except RuntimeError:  # the loop is closed: shutting down
                pass

        work: asyncio.Future | None = None
        try:
            # the wall clock is the wait running out, never an exception the run raised (an
            # engine TimeoutError is the engine's error)
            before = self.lingering.get(job.run_id)  # this run's own thread, still ending
            if before is not None:
                until = min(deadline, self._abandon_at.get(job.run_id, deadline))
                waited = await self._until(run, before, until)
            else:
                waited = "done"
            if waited == "done":
                work = loop.run_in_executor(None, self._work, runner, job, on_event)
                waited = await self._until(run, work, deadline)
            if waited == "unwatched":
                job.cancel.set()  # no more spend; it ends at its next call or stage start
                log.info("live run %s stopped: nobody is listening", job.run_id)
                run.publish("stopped", run.stage, {"message": STOPPED_MESSAGE})
                return
            if waited == "late":
                job.stop.set()  # its next model call is refused; what it reports is dropped
                log.warning("live run %s given up after %g s", job.run_id, self.wall_clock)
                limit = wait_words(math.ceil(self.wall_clock))
                raise RunTimeout(f"This description took longer than {limit} and was stopped.")
            assert work is not None
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
            if work is not None and not work.done():
                self._linger(job.run_id, work, deadline)
            self.lock.release()
            self.active.pop(run.run_id, None)
            run.close()
            self._changed(job.run_dir)

    def _work(self, runner: Runner, job: Job, on_event: Callable[[Event], None]) -> dict:
        """The run, then the after_run hooks (one failing is logged, never fails the run). A run
        the wall clock gave up skips them (it failed); a stopped one that still finished its
        last stage, publishing, runs them: it is done."""
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
    """What the thread of a run let go did in the end, for the log."""

    def done(f: asyncio.Future) -> None:
        e = None if f.cancelled() else f.exception()
        log.info("live run %s: its thread ended after the run was let go (%r)", run_id, e)

    return done


# -- what the routes share ---------------------------------------------------------------------


class Live:
    """The live routes' state. Tests swap `runner`, `probe`, `ledger`, `limiter`, `heartbeat`
    and the limits."""

    def __init__(self, settings: ApiSettings, store: RunStore) -> None:
        self.settings, self.store = settings, store
        self.runner: Runner = _run_pipeline
        self.probe: Callable[[Path], media.Probe] = clip.probe
        self.transcode: Callable[[Path, Path, float, media.Probe], Path] = clip.transcode
        self.limiter = RateLimiter()
        self.refusals = RateLimiter(
            per_client=REFUSALS_PER_HOUR, client_window=HOUR, per_day=1_000_000
        )
        self.executor = Executor(changed=store.invalidate)
        self.executor.wall_clock = settings.lull_run_wall_clock_s
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
        self.max_followers, self.max_streams = MAX_FOLLOWERS, MAX_STREAMS
        self.streams = 0  # GET .../events streams open now
        self.followers: dict[str, int] = {}  # of those, following each run
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

    def check_open_budget(self) -> None:
        """402 at once when the day or total model cap is already reached: before an upload's
        body is read, so nobody sends 30 MB to be refused."""
        try:
            self.ledger.check(OPEN_CHECK_ID, OPEN_CHECK_USD)
        except BudgetExceeded as e:
            raise LiveError(402, "budget", budget_message(e.scope), scope=e.scope) from None

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
                f"hour. Try again in {wait_words(wait)}, or open a recorded run."
            )
        else:
            message = (
                f"The live demo has run its {self.limiter.per_day} descriptions for today. "
                f"Try again in {wait_words(wait)}, or open a recorded run."
            )
        raise LiveError(429, "rate_limited", message, headers={"Retry-After": str(wait)})

    def check_refusals(self, key: str) -> None:
        stop = self.refusals.retry_after(key)
        if stop is not None:
            wait = stop[1]
            message = (
                f"Too many of your uploads were refused in the last hour. Try again in "
                f"{wait_words(wait)}, or open a recorded run."
            )
            raise LiveError(429, "rate_limited", message, headers={"Retry-After": str(wait)})

    async def start(self, job: Job, first: dict) -> tuple[list[dict], LiveRun, asyncio.Queue]:
        run = await self.executor.start(job, self.runner, self.scrubber(job.run_dir), first)
        backlog, q = run.subscribe()
        return backlog, run, q

    def watching(self, run_id: str | None) -> Callable[[], None]:
        """Count a GET .../events stream (`run_id`: one that follows that run), or 429 busy
        past the caps. Returns what to call when the stream ends."""
        follows = self.followers.get(run_id, 0) if run_id is not None else 0
        if run_id is not None and follows >= self.max_followers:
            message = (
                f"{self.max_followers} people are following this description already. Try "
                "again in a minute, or open it once it has finished."
            )
            raise LiveError(429, "busy", message, headers={"Retry-After": str(FOLLOW_RETRY_S)})
        if self.streams >= self.max_streams:
            message = "Too many runs are being watched right now. Try again in a minute."
            raise LiveError(429, "busy", message, headers={"Retry-After": str(FOLLOW_RETRY_S)})
        self.streams += 1
        if run_id is not None:
            self.followers[run_id] = follows + 1
        ended = False

        def release() -> None:
            nonlocal ended
            if ended:
                return
            ended = True
            self.streams -= 1
            if run_id is not None:
                left = self.followers.get(run_id, 1) - 1
                if left > 0:
                    self.followers[run_id] = left
                else:
                    self.followers.pop(run_id, None)

        return release


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


async def _store(fn: Callable[..., Any], *args: Any, deadline: float = STORE_DEADLINE_S) -> Any:
    """Write the video into the run folder, within `deadline` seconds; a disk that fails or
    hangs (a network mount, say) is our trouble, not the visitor's: 503, not counted against
    them. (A hung write is left to finish in its thread; the run folder is removed.)"""
    try:
        with anyio.fail_after(deadline):
            return await anyio.to_thread.run_sync(fn, *args, abandon_on_cancel=True)
    except OSError:  # TimeoutError included
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
    """The video probed, in a container we take (422 unreadable) and within the limits that
    bound a run's work (422 unsupported, the reason in plain words: lull.api.clip)."""
    try:
        info = await anyio.to_thread.run_sync(live.probe, video)
        media.assert_safe_container(info)  # the engine's rule, right after probing
    except clip.OverLimits as e:
        raise LiveError(422, "unsupported", str(e)) from None
    except Exception:
        log.info("live run %s: %s does not probe", run_id, video.name, exc_info=True)
        raise unreadable() from None
    if not info.duration > 0:
        raise unreadable()
    reason = clip.over_limits(info)
    if reason is not None:
        raise LiveError(422, "unsupported", reason)
    return info


def not_converted() -> LiveError:
    return LiveError(
        422,
        "unsupported",
        "This video could not be prepared for a live description. Export it again as an "
        "H.264 mp4 and try once more.",
    )


async def _cut(live: Live, run_id: str, video: Path, limit: float) -> tuple[Path, media.Probe]:
    """Check the video, then replace it by the run's copy (lull.api.clip.transcode: bounded,
    at most `limit` seconds, an mp4 named after it) and return that copy and what probing it
    says: the file's own claims are not trusted. Every engine stage works on the copy."""
    info = await _probe(live, run_id, video)
    if info.duration > limit:
        raise too_long(info.duration, limit)
    copy = video.with_name(f".copy-{video.stem}.mp4")
    try:
        await anyio.to_thread.run_sync(live.transcode, video, copy, limit, info)
    except Exception:
        log.info("live run %s: %s does not transcode", run_id, video.name, exc_info=True)
        raise not_converted() from None
    stated = info.duration
    info = await _probe(live, run_id, copy)
    if info.duration > limit + TRIM_SLACK:
        raise too_long(info.duration, limit)
    if abs(info.duration - stated) > LENGTH_SLACK * stated:
        raise LiveError(
            422,
            "unsupported",
            f"This video's stated length ({stated:.1f} s) does not match what it holds "
            f"({info.duration:.1f} s). Export it again as an mp4.",
        )
    final = video.with_name(f"{video.stem}.mp4")
    os.replace(copy, final)
    if final != video:
        video.unlink()
    return final, info


def _sample(settings: ApiSettings, sample_id: str) -> Path | None:
    if not SAMPLE_RE.fullmatch(sample_id):
        return None
    path = settings.samples_dir / f"{sample_id}.mp4"
    return path if path.is_file() else None


async def _accept(
    live: Live, request: Request, run_id: str, run_dir: Path, client: str
) -> tuple[Job, str]:
    """Receive the form (within the upload deadline: 408), put the video in run_dir/input
    (on our own clock: 503 when the disk is slow), probe and cut it, check the spend caps and
    write request.json, which keeps the sha256 of the edit token returned with the job. Raises
    LiveError; the caller then removes run_dir."""
    form = None
    with anyio.move_on_after(live.upload_deadline):
        form = await _read_form(request, live.max_bytes)
    if form is None:  # the deadline ran out while the body was arriving
        raise upload_timeout(live.upload_deadline)
    try:
        opts, video, source = await _receive(live, form, run_dir)
    finally:
        await form.close()
    return await _prepare(live, run_id, run_dir, opts, video, source, client)


async def _prepare(
    live: Live,
    run_id: str,
    run_dir: Path,
    opts: RunOptions,
    video: Path,
    source: dict[str, Any],
    client: str,
) -> tuple[Job, str]:
    """The stored video (run_dir/input) probed and replaced by the run's copy, the spend caps
    checked and request.json written with the sha256 of the edit token returned with the job.
    Raises LiveError; the caller then removes run_dir."""
    limit = live.max_sample_seconds if source["kind"] == "sample" else live.max_seconds
    video, info = await _cut(live, run_id, video, limit)
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
    live: Live, form: FormData, run_dir: Path
) -> tuple[RunOptions, Path, dict[str, Any]]:
    """The form checked, its video (upload or sample) stored in run_dir/input."""
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
    return opts, video, source


@dataclass
class Started:
    """A run started for a caller that follows it: its job, edit token, events so far and
    queue (unsubscribe when done following)."""

    job: Job
    token: str
    run: LiveRun
    backlog: list[dict]
    queue: asyncio.Queue


def no_sample() -> LiveError:
    return LiveError(422, "sample", "There is no sample with that id.")


@contextlib.contextmanager
def _prepare_slot(live: Live, key: str) -> Iterator[None]:
    """One of the MAX_UPLOADS slots for receiving and preparing a video, one per visitor (429
    one_upload_at_a_time, 503 uploads_busy), held until the run starts or is refused. A
    refusal inside it counts against the visitor (REFUSALS_PER_HOUR) unless it was the spend
    caps, a busy executor or our storage."""
    if key in live.upload_keys:
        raise one_upload()
    if live.uploading >= live.max_uploads:
        raise uploads_busy()
    live.uploading += 1
    live.upload_keys.add(key)
    try:
        yield
    except BaseException as e:
        if not (isinstance(e, LiveError) and e.status in NOT_COUNTED):
            live.refusals.hit(key)
        raise
    finally:
        live.uploading -= 1
        live.upload_keys.discard(key)


async def start_sample(live: Live, key: str, sample_id: str, opts: RunOptions) -> Started:
    """Start a run of one of the samples for visitor `key`, through the gates POST /v1/runs
    has (busy 409, the visitor's starts and refusals 429, spend 402, a prepare slot 429/503,
    the sample 422): what the MCP server's describe_video calls. Nothing but a sample id names
    the video."""
    if live.executor.busy:
        raise busy()
    live.check_rate(key)
    live.check_refusals(key)
    live.check_open_budget()
    run_id = new_run_id()
    run_dir = live.settings.runs_dir / run_id
    try:
        with _prepare_slot(live, key):
            path = _sample(live.settings, sample_id)
            if path is None:
                raise no_sample()
            video = _new_run_dir(run_dir) / path.name
            await _store(_link_or_copy, path, video)
            source = {"kind": "sample", "id": sample_id, "name": path.name}
            job, token = await _prepare(live, run_id, run_dir, opts, video, source, key)
            # checked again with the claim, in one step: preparing took a while
            if live.executor.busy:
                raise busy()
            live.check_rate(key)
            (run_dir / PENDING).unlink()
            first = {"run_id": run_id, "duration": job.duration, "options": _opts(job)}
            backlog, run, q = await live.start(job, first)
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    live.limiter.hit(key)
    return Started(job, token, run, backlog, q)


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


class EventStream(StreamingResponse):
    """An SSE response that lets go of its run's queue however it ends: the client gone, the
    run over, or the body never started (a client that left before it did)."""

    def __init__(self, frames: AsyncIterator[str], on_close: Callable[[], None]) -> None:
        super().__init__(
            frames,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"},
        )
        self._on_close = on_close

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._on_close()


def _sse(
    backlog: list[dict],
    run: LiveRun | None = None,
    q: asyncio.Queue[dict | None] | None = None,
    heartbeat: float = HEARTBEAT_S,
    secret: dict | None = None,
    release: Callable[[], None] | None = None,
) -> StreamingResponse:
    def close() -> None:
        if run is not None and q is not None:
            run.unsubscribe(q)
        if release is not None:
            release()

    return EventStream(_frames(backlog, run, q, heartbeat, secret), close)


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


SAMPLE_FIELDS = (
    "id", "file", "title", "credit", "licence", "licence_url", "source_url", "excerpt",
    "changes", "duration",
)  # fmt: skip


@router.get("/v1/samples")
def list_samples(request: Request) -> list[dict[str, Any]]:
    return sample_entries(_live(request).settings.samples_dir)


def sample_entries(root: Path) -> list[dict[str, Any]]:
    """The samples a visitor may describe: samples.json's entries (title, credit, licence,
    source, duration...) whose <id>.mp4 is there; without samples.json, the ids alone."""
    if not root.is_dir():
        return []
    doc = _read_json(root / "samples.json")
    if isinstance(doc, dict) and isinstance(doc.get("samples"), list):
        out = []
        for e in doc["samples"]:
            if not isinstance(e, dict) or not isinstance(e.get("id"), str):
                continue
            ok = SAMPLE_RE.fullmatch(e["id"]) and e.get("file") == f"{e['id']}.mp4"
            if ok and (root / e["file"]).is_file():
                out.append({k: e[k] for k in SAMPLE_FIELDS if k in e})
        return out
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
    check_client_header(request)
    live = _live(request)
    key = live.limiter.client_key(client_ip(request, live.settings.lull_proxy_hops))
    if live.executor.busy:
        raise busy()
    live.check_rate(key)
    live.check_refusals(key)
    live.check_open_budget()  # before the body: a spent cap refuses at once
    _check_headers(request, live.max_bytes)

    run_id = new_run_id()
    run_dir = live.settings.runs_dir / run_id
    try:
        # a slot, only for a request whose headers hold up; an upload refused (or cut off, or
        # crashed) inside it counts against the visitor
        with _prepare_slot(live, key):
            job, token = await _accept(live, request, run_id, run_dir, key)
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
    status = "done" if kind == "showcase" else facts.run_status(run_dir, active is not None)
    if status != "done" and not holds_token(run_dir, request):
        # an unfinished run is its owner's: following it, its progress (cue texts and notes)
        # and resuming it; a published run's replay is open to anyone
        raise LiveError(403, "forbidden", "Only the person who started this run can open it.")
    if active is not None:
        release = live.watching(run_id)
        backlog, q = active.subscribe()
        return _sse(_since(backlog, after), active, q, live.heartbeat, release=release)
    if status in ("done", "failed"):
        events = read_events(run_dir)
        if not events:
            raise LiveError(404, "not_found", "No progress was recorded for this run.")
        rest = _since(events, after)
        if not rest:  # seen to its end: 204 tells an EventSource to stop reconnecting
            return Response(status_code=204)
        return _sse(rest, release=live.watching(None))

    # stopped: restarted here for its owner, from the stage cache
    job = _resume_job(live, run_id, run_dir)
    if live.executor.refuses(run_id):
        raise busy()
    key = live.limiter.client_key(client_ip(request, live.settings.lull_proxy_hops))
    live.check_rate(key)  # a resume is a start: it counts like one
    live.check_budget(run_id, job.duration)
    first = {"run_id": run_id, "duration": job.duration, "options": _opts(job), "resumed": True}
    backlog, run, q = await live.start(job, first)
    live.limiter.hit(key)
    return _sse(_since(backlog, after), run, q, live.heartbeat)
