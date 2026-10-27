"""Live runs: one description at a time, its progress streamed as Server-Sent Events.

POST /v1/runs takes an upload (or a sample id). Before anything streams, it checks that no
other run is going (409), the visitor's rate limit (429), the size (413), the format and the
duration (422) and the spend caps (402). Then it answers with text/event-stream: a `run` event,
every engine event as it happens (stage_start / stage_end / call / cue / warning), a final
`done` or `error`, and a `: ping` comment whenever the stream has been quiet for 15 s.

The engine runs in a worker thread. Its callback only hands each event to the event loop
(call_soon_threadsafe). On the loop the event gets its sequence number, is appended to
runs/<id>/events.jsonl and goes to every listener. GET /v1/runs/{id}/events replays that file
and follows the run if it is still going in this process. It resumes a live run nobody
finished (no manifest, no error.json: the process stopped); the engine's stage cache makes
that cheap.
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
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import FormData, UploadFile
from starlette.types import Message

from lull import media
from lull.api.ratelimit import RateLimiter
from lull.api.settings import ApiSettings
from lull.api.store import RunStore
from lull.config import get_settings
from lull.ledger import BudgetExceeded, Ledger
from lull.pipeline import Event, budget_estimate
from lull.pipeline import run as run_engine
from lull.speech import NemoSpeechHTTP
from lull.tavily import Tavily
from lull.tf import TokenFactory
from lull.types import RunOptions
from lull.voices import DEFAULT_WPS, voice_wps

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
FORM_SLACK = 64 * 1024  # multipart headers and the small fields around the file
MAX_SECONDS = 120.0
FORMATS = (".mp4", ".mov", ".webm", ".mkv")
HEARTBEAT_S = 15.0
BUSY_RETRY_S = 60

EVENTS, REQUEST, ERROR, MANIFEST, INPUT = (
    "events.jsonl",
    "request.json",
    "error.json",
    "manifest.json",
    "input",
)
SAMPLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
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


# -- keeping local paths out of what visitors see ----------------------------------------------

# an absolute path not inside a URL; it is replaced by its last part
_ABS_PATH = re.compile(
    r"(?<![\w:/.\\-])(?:[A-Za-z]:[\\/]|/)(?:[^\s'\"\\/:]+[\\/])+(?P<name>[^\s'\"\\/:,;)]*)"
)


class Scrubber:
    """Takes local paths and configured secrets out of text meant for visitors: the run
    folder and other known roots are cut off (input/clip.mp4 stays), any other absolute path
    shrinks to its file name."""

    def __init__(self, run_dir: Path, roots: Iterable[Path]) -> None:
        forms = set()
        for root in (run_dir, *roots):
            for form in (str(root), root.as_posix()):
                form = form.rstrip("/\\")
                if len(form) > 1:
                    forms.add(form)
        ordered = sorted(forms, key=len, reverse=True)
        self._roots = re.compile("|".join(re.escape(f) + r"[\\/]?" for f in ordered), re.IGNORECASE)
        s = get_settings()
        keys = (s.nebius_api_key, s.tavily_api_key, s.lull_speech_key)
        self._secrets = [k for k in keys if k and len(k) >= 8]

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        text = self._roots.sub("", text)
        return _ABS_PATH.sub(lambda m: m.group("name"), text)


# -- the engine --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Job:
    """One engine run: `runner(job, on_event)` does it in a worker thread."""

    run_id: str
    run_dir: Path
    video: Path
    opts: RunOptions
    duration: float
    ledger: Ledger
    resumed: bool = False


Runner = Callable[[Job, Callable[[Event], None]], object]


def _run_pipeline(job: Job, on_event: Callable[[Event], None]) -> None:
    """The API's one call into the engine, with real clients recording to the job's ledger."""
    tf = TokenFactory.from_settings(job.ledger)
    tavily = Tavily.from_settings(job.ledger)
    try:
        run_engine(
            job.video,
            job.run_dir,
            job.opts,
            tf=tf,
            speech=NemoSpeechHTTP.from_settings(),
            tavily=tavily,
            ledger=job.ledger,
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
        self.done = False
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
        elif ev.type == "done":
            self.done = True
            data = {k: v for k, v in data.items() if k not in ("output", "manifest")}
            data["run_id"] = self.run_id
        self.publish(ev.type, ev.stage, data, ev.ts)

    def fail(self, e: BaseException) -> None:
        """error.json, then the `error` event: {kind, message (no local paths), scope}."""
        stage = self.error_stage or self.stage
        info: dict[str, Any] = {"type": type(e).__name__}
        if isinstance(e, BudgetExceeded):
            info.update(message=budget_message(e.scope), stage=stage, scope=e.scope)
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
    refused (409), not queued."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.active: dict[str, LiveRun] = {}
        self._tasks: set[asyncio.Task] = set()

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

        try:
            await loop.run_in_executor(None, runner, job, on_event)
        except Exception as e:
            if isinstance(e, BudgetExceeded):
                log.warning("live run %s stopped by the %s cap", job.run_id, e.scope)
            else:
                log.exception("live run %s failed in %s", job.run_id, run.error_stage or run.stage)
            run.fail(e)
        else:
            if not run.done:
                run.publish("done", None, {"run_id": run.run_id})
        finally:  # free before the listeners hear the end, so they may start the next run
            self.lock.release()
            self.active.pop(run.run_id, None)
            run.close()


# -- what the routes share ---------------------------------------------------------------------


class Live:
    """The live routes' state. Tests swap `runner`, `probe`, `ledger`, `limiter`, `heartbeat`
    and the limits."""

    def __init__(self, settings: ApiSettings, store: RunStore) -> None:
        self.settings, self.store = settings, store
        self.runner: Runner = _run_pipeline
        self.probe: Callable[[Path], media.Probe] = media.probe
        self.limiter = RateLimiter()
        self.executor = Executor()
        self.heartbeat = HEARTBEAT_S
        self.max_bytes = MAX_UPLOAD_BYTES
        self.max_seconds = MAX_SECONDS
        self._ledger: Ledger | None = None

    @property
    def ledger(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger(get_settings().lull_home / "ledger.db")
        return self._ledger

    @ledger.setter
    def ledger(self, value: Ledger) -> None:
        self._ledger = value

    def scrubber(self, run_dir: Path) -> Scrubber:
        roots = (
            self.settings.lull_data.absolute(),
            get_settings().lull_home.absolute(),
            Path.home(),
            Path(tempfile.gettempdir()),
        )
        return Scrubber(run_dir.absolute(), roots)

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


def new_run_id() -> str:
    return f"live-{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


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


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:  # another filesystem, or one without links
        shutil.copyfile(src, dst)


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


def _sample(settings: ApiSettings, sample_id: str) -> Path | None:
    if not SAMPLE_RE.match(sample_id):
        return None
    path = settings.samples_dir / f"{sample_id}.mp4"
    return path if path.is_file() else None


async def _accept(live: Live, request: Request, run_id: str, run_dir: Path, client: str) -> Job:
    """Read and check the form, put the video in run_dir/input, probe it, check the spend
    caps and write request.json. Raises LiveError; the caller then removes run_dir."""
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
            video = run_dir / INPUT / name
            video.parent.mkdir(parents=True)
            size = await anyio.to_thread.run_sync(_save, upload.file, video, live.max_bytes)
            source: dict[str, Any] = {"kind": "upload", "name": name, "bytes": size}
        else:
            assert isinstance(sample, str)
            path = _sample(live.settings, sample)
            if path is None:
                raise LiveError(422, "sample", "There is no sample with that id.")
            video = run_dir / INPUT / path.name
            video.parent.mkdir(parents=True)
            await anyio.to_thread.run_sync(_link_or_copy, path, video)
            source = {"kind": "sample", "id": sample, "name": path.name}
    finally:
        await form.close()

    try:
        info = await anyio.to_thread.run_sync(live.probe, video)
    except Exception:
        log.info("live run %s: the video does not probe", run_id, exc_info=True)
        info = None
    if info is None or not info.duration > 0:
        raise LiveError(
            422, "unreadable", f"This file could not be read as a video. {FORMAT_MESSAGE}"
        )
    if info.duration > live.max_seconds:
        raise LiveError(
            422,
            "too_long",
            f"This video is {info.duration:.1f} s long. Live descriptions take videos up to "
            f"{live.max_seconds:.0f} s.",
        )
    live.check_budget(run_id, info.duration)
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
        },
    )
    return Job(run_id, run_dir, video, opts, info.duration, live.ledger)


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
) -> AsyncIterator[str]:
    try:
        for ev in backlog:
            yield _frame(ev)
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
) -> StreamingResponse:
    return StreamingResponse(
        _frames(backlog, run, q, heartbeat),
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
    paths = sorted(p for p in root.glob("*.mp4") if p.is_file() and SAMPLE_RE.match(p.stem))
    return [{"id": p.stem} for p in paths]


@router.post("/v1/runs")
async def create_run(request: Request) -> StreamingResponse:
    live = _live(request)
    key = live.limiter.client_key(client_ip(request, live.settings.lull_proxy_hops))
    if live.executor.busy:
        raise busy()
    live.check_rate(key)
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > live.max_bytes + FORM_SLACK:
        raise too_large(live.max_bytes)

    run_id = new_run_id()
    run_dir = live.settings.runs_dir / run_id
    try:
        job = await _accept(live, request, run_id, run_dir, key)
        # checked again with the claim, in one step: the upload took a while
        if live.executor.busy:
            raise busy()
        live.check_rate(key)
        first = {"run_id": run_id, "duration": job.duration, "options": _opts(job)}
        backlog, run, q = await live.start(job, first)
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    live.limiter.hit(key)
    return _sse(backlog, run, q, live.heartbeat)


@router.get("/v1/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int | None = None) -> StreamingResponse:
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
        return _sse(_since(events, after))

    job = _resume_job(live, run_id, run_dir)
    if live.executor.busy:
        raise busy()
    live.check_budget(run_id, job.duration)
    first = {"run_id": run_id, "duration": job.duration, "options": _opts(job), "resumed": True}
    backlog, run, q = await live.start(job, first)
    return _sse(_since(backlog, after), run, q, live.heartbeat)
