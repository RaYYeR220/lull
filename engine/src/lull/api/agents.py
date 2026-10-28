"""MCP: an AI agent (Claude Desktop, any MCP client) asks Lull to describe a video and fetches
the description tracks and the conformance report, with the website's budget, limits and
honesty.

Two servers, two trust levels:

- hosted_server(): `/mcp` on the API (streamable HTTP, stateless). Its sources are the API's
  samples and existing run ids only: it never fetches a URL, never reads a path and takes no
  upload (a fetch from the platform could reach its metadata server and internal addresses).
  Every call goes through the live executor, the spend caps, the per-visitor limits (keyed by
  the visitor's address, as the REST API keys them) and the REST API's run-id rules. An
  argument a tool does not declare is refused, not ignored (StrictArguments).
- local_server(): `lull mcp` over stdio, on the user's machine with their own keys and caps.
  describe_video also takes a local file path (an existing regular file of a video type);
  runs go under LULL_HOME/runs; review_queue lists the runs with lines held for a person.

describe_video sends MCP progress notifications per stage (the stage, its seconds, model calls
and spend so far) and returns the run id, status, cue counts, coverage, WCAG rows, model spend
and searches. A hosted run is owned by the edit token in that result (an agent can hand it to
a person for the Studio); the token is never logged. get_tracks and get_report give what the
run's manifest and cues.json say, for a finished run only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import stat
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field
from starlette.types import Receive, Scope, Send

from lull import media
from lull.api import facts
from lull.api import live as live_mod
from lull.api.live import FORMATS, Job, Live, LiveError, RunStopped, client_ip
from lull.api.settings import ApiSettings
from lull.api.spend import public_spend
from lull.api.store import RunStore
from lull.config import get_settings
from lull.ledger import Ledger
from lull.pipeline import Event, run_id_for
from lull.store import file_sha256
from lull.types import RunOptions

log = logging.getLogger(__name__)

PATH = "/mcp"
STAGES = ("listen", "watch", "research", "plan", "schedule", "finalize", "publish")
TRACK_FILES = (
    "descriptions.vtt", "extended.vtt", "described-audio.m4a", "described.mp4", "extended.mp4",
)  # fmt: skip
Mode = Literal["standard", "extended"]

HOSTED_INSTRUCTIONS = (
    "Lull writes audio description for video: short spoken descriptions fitted into the pauses "
    "of the soundtrack and checked against the frames. This hosted server describes the "
    "licensed sample clips only (list_samples), never a URL or an upload; for your own files, "
    "run `lull mcp` locally. A description takes a few minutes; progress comes per stage."
)
LOCAL_INSTRUCTIONS = (
    "Lull writes audio description for video: short spoken descriptions fitted into the pauses "
    "of the soundtrack and checked against the frames. This local server describes a video file "
    "on this machine (describe_video with a path) with the user's own keys and spend caps, and "
    "lists runs with lines held for a person to check (review_queue)."
)

# -- what the tools return ----------------------------------------------------------------------


class Sample(BaseModel):
    id: str
    title: str | None = None
    duration: float | None = None
    credit: str | None = None
    licence: str | None = None


class Samples(BaseModel):
    samples: list[Sample]


class WcagRow(BaseModel):
    criterion: str = Field(description="1.2.3, 1.2.5 or 1.2.7")
    level: str | None = None
    name: str | None = None
    status: str | None = Field(None, description="met, met (automated checks), needs review...")
    via: str | None = Field(None, description="the technique that meets it")
    blockers: list[str] = Field(default_factory=list, description="what keeps it from met")


class Spend(BaseModel):
    model_usd: float | None = Field(None, description="model spend in USD (all attempts)")
    searches: int | None = Field(None, description="web searches made (counted, not priced)")


class Described(BaseModel):
    run_id: str
    status: Literal["done", "failed", "stopped"]
    edit_token: str | None = Field(
        None,
        description="Owner token of a hosted run: send it as `Authorization: Bearer <token>` "
        "to change the run in the Studio. Give it only to the person who should edit it.",
    )
    cues: dict[str, int] | None = Field(None, description="cue counts by status")
    coverage: dict[str, Any] | None = None
    wcag: list[WcagRow] = Field(default_factory=list)
    spend: Spend | None = None
    error: str | None = Field(None, description="why a failed run failed")


class TrackCue(BaseModel):
    id: str
    timecode: str = Field(description="HH:MM:SS.mmm --> HH:MM:SS.mmm")
    start: float | None = None
    end: float | None = None
    status: str | None = Field(None, description="verified, held, extended, edited or approved")
    text: str | None = None
    extended: bool = Field(False, description="spoken while the picture holds (extended track)")
    note: str | None = Field(None, description="why a held line is held")


class Tracks(BaseModel):
    run_id: str
    cues: list[TrackCue]
    files: dict[str, str] = Field(description="file name -> URL (hosted) or path (local)")


class HeldLine(BaseModel):
    id: str
    text: str | None = None
    reason: str | None = None


class Report(BaseModel):
    run_id: str
    title: str | None = None
    duration: float | None = None
    cues: dict[str, int] | None = None
    coverage: dict[str, Any] | None = None
    wcag: list[WcagRow] = Field(default_factory=list)
    held: list[HeldLine] = Field(default_factory=list)
    spend: Spend | None = None
    tts_disclosure: str | None = None


class Queued(BaseModel):
    run_id: str
    title: str | None = None
    created: str | None = None
    held: list[HeldLine]


class Queue(BaseModel):
    runs: list[Queued]


# -- reading a run (both servers) ---------------------------------------------------------------


def _dict(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _clock(t: float) -> str:
    ms = round(max(t, 0.0) * 1000)
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _wcag(summary: dict) -> list[WcagRow]:
    rows = summary.get("wcag")
    return [
        WcagRow(
            criterion=str(r.get("criterion")),
            level=r.get("level"),
            name=r.get("name"),
            status=r.get("status"),
            via=r.get("via") or None,
            blockers=[str(x) for x in r.get("reasons") or []],
        )
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and r.get("criterion")
    ]


def _spend(summary: dict) -> Spend:
    s = public_spend(summary)
    usd = s["model_usd_all_runs"] if s["model_usd_all_runs"] is not None else s["model_usd"]
    searches = s["searches"]
    return Spend(model_usd=usd, searches=int(searches) if searches is not None else None)


def _counts(summary: dict) -> dict[str, int] | None:
    cues = summary.get("cues")
    return {k: int(v) for k, v in cues.items()} if isinstance(cues, dict) else None


def _finished(store: RunStore, run_id: str) -> Path:
    """The folder of a finished (published) run, or a tool error."""
    found = store.find(run_id)
    if found is None:
        raise ToolError("not_found: There is no run with that id.")
    kind, d = found
    status = facts.run_status(d, store.active(d.name))
    if status != "done":
        raise ToolError(f"not_ready: This run is {status}; its tracks and report come once done.")
    return d


def _summary(store: RunStore, d: Path) -> dict:
    manifest = store.manifest(d)
    return store.public_manifest(manifest)["summary"] if manifest is not None else {}


def _cues_doc(store: RunStore, d: Path) -> dict:
    return store.public_cues(d) or {}


def tracks(store: RunStore, run_id: str, where: Callable[[Path, str], str]) -> Tracks:
    d = _finished(store, run_id)
    doc = _cues_doc(store, d)
    cues = []
    for c in [*doc.get("cues", []), *doc.get("extended", [])]:
        start = c.get("start") if isinstance(c.get("start"), int | float) else None
        dur = c.get("audio_dur") if isinstance(c.get("audio_dur"), int | float) else 0.0
        end = None if start is None else round(start + dur, 3)
        cues.append(
            TrackCue(
                id=str(c.get("id")),
                timecode=f"{_clock(start or 0.0)} --> {_clock(end or 0.0)}",
                start=start,
                end=end,
                status=c.get("status"),
                text=c.get("text"),
                extended=bool(c.get("extended")),
                note=c.get("note"),
            )
        )
    cues.sort(key=lambda c: (c.start or 0.0, c.id))
    outputs = _dict(_dict(store.manifest(d)).get("outputs"))
    files = {name: where(d, name) for name in TRACK_FILES if name in outputs}
    return Tracks(run_id=d.name, cues=cues, files=files)


def _held(summary: dict, doc: dict) -> list[HeldLine]:
    text = {
        c.get("id"): c.get("text")
        for c in [*doc.get("cues", []), *doc.get("extended", [])]
        if isinstance(c, dict)
    }
    held = summary.get("held")
    return [
        HeldLine(id=str(h.get("id")), text=text.get(h.get("id")), reason=h.get("reason"))
        for h in (held if isinstance(held, list) else [])
        if isinstance(h, dict) and h.get("id") is not None
    ]


def report(store: RunStore, run_id: str) -> Report:
    d = _finished(store, run_id)
    manifest = store.public_manifest(store.manifest(d) or {})
    summary = manifest["summary"]
    video = _dict(manifest.get("video"))
    return Report(
        run_id=d.name,
        title=video.get("name"),
        duration=video.get("duration"),
        cues=_counts(summary),
        coverage=summary.get("coverage") if isinstance(summary.get("coverage"), dict) else None,
        wcag=_wcag(summary),
        held=_held(summary, _cues_doc(store, d)),
        spend=_spend(summary),
        tts_disclosure=summary.get("tts_disclosure"),
    )


def described(store: RunStore, run_dir: Path, final: dict, token: str | None) -> Described:
    """The result of a describe_video call from the run's last event and its manifest."""
    kind = final.get("type")
    if kind == "done":
        store.invalidate(run_dir)
        summary = _summary(store, run_dir)
        return Described(
            run_id=run_dir.name,
            status="done",
            edit_token=token,
            cues=_counts(summary),
            coverage=summary.get("coverage") if isinstance(summary.get("coverage"), dict) else None,
            wcag=_wcag(summary),
            spend=_spend(summary),
        )
    status = "stopped" if kind == "stopped" else "failed"
    return Described(
        run_id=run_dir.name,
        status=status,
        edit_token=token,
        error=str(final.get("message") or "the run did not finish"),
    )


# -- progress -------------------------------------------------------------------------------------


class Progress:
    """MCP progress from a run's events: one notification per stage start and end, with model
    calls and spend so far."""

    def __init__(self, ctx: Context) -> None:
        self.ctx, self.calls, self.usd = ctx, 0, 0.0

    async def __call__(self, ev: dict) -> None:
        kind, stage = ev.get("type"), ev.get("stage")
        if kind == "call":
            self.calls += 1
            cost = ev.get("cost_usd")
            self.usd += cost if isinstance(cost, int | float) else 0.0
            return
        if kind not in ("stage_start", "stage_end") or stage not in STAGES:
            return
        at = STAGES.index(stage)
        so_far = f"{self.calls} model calls, ${self.usd:.4f} so far"
        if kind == "stage_start":
            await self.ctx.report_progress(at, len(STAGES), f"{stage}: started ({so_far})")
            return
        secs = ev.get("seconds")
        took = f"{secs:.1f} s" if isinstance(secs, int | float) else "done"
        cached = ", from the cache" if ev.get("cached") else ""
        message = f"{stage}: {took}{cached} ({so_far})"
        await self.ctx.report_progress(at + 1, len(STAGES), message)


FINAL = ("done", "error", "stopped")


async def follow(backlog: list[dict], q: asyncio.Queue, ctx: Context) -> dict:
    """Follow a live run's events to its final one, reporting progress."""
    progress = Progress(ctx)
    for ev in backlog:
        if ev.get("type") in FINAL:
            return ev
        await progress(ev)
    while True:
        ev = await q.get()
        if ev is None:
            return {"type": "stopped", "message": "the run ended without a final event"}
        if ev.get("type") in FINAL:
            return ev
        await progress(ev)


# -- the hosted server ----------------------------------------------------------------------------

HOSTED_ARGS = {
    "list_samples": set(),
    "describe_video": {"sample_id", "mode"},
    "get_tracks": {"run_id"},
    "get_report": {"run_id"},
}


class StrictArguments:
    """Server middleware: a tools/call with an argument its tool does not declare (a `url`, a
    `path`, a `file`...) is refused as an error result, not silently ignored."""

    def __init__(self, allowed: dict[str, set[str]]) -> None:
        self.allowed = allowed

    async def __call__(self, ctx: Any, call_next: Callable[[Any], Any]) -> Any:
        params = ctx.params if isinstance(getattr(ctx, "params", None), dict) else None
        if getattr(ctx, "method", None) == "tools/call" and params is not None:
            name, args = params.get("name"), params.get("arguments") or {}
            if name in self.allowed:
                extra = sorted(set(args) - self.allowed[name]) if isinstance(args, dict) else []
                if extra or not isinstance(args, dict):
                    message = (
                        f"unsupported: {name} takes {', '.join(sorted(self.allowed[name])) or 'no'}"
                        f" arguments, not {', '.join(extra) or 'these'}. This hosted server never "
                        "fetches a URL, reads a path or takes an upload: it describes its samples. "
                        "For your own files, run `lull mcp` locally."
                    )
                    return CallToolResult(
                        content=[TextContent(type="text", text=message)], is_error=True
                    )
        return await call_next(ctx)


def _refused(e: LiveError) -> ToolError:
    return ToolError(f"{e.error}: {e.message}")


def _request(ctx: Context) -> Any:
    try:
        return ctx.request_context.request
    except ValueError:
        return None


def _base_url(ctx: Context, settings: ApiSettings) -> str:
    """Where this API answers: LULL_PUBLIC_URL, else the request's own scheme and host (the
    forwarded scheme behind trusted proxies); "" when there is no HTTP request (in-process)."""
    if settings.lull_public_url:
        return settings.lull_public_url.rstrip("/")
    request = _request(ctx)
    headers = getattr(request, "headers", None)
    if request is None or headers is None or not headers.get("host"):
        return ""
    scheme = request.url.scheme
    if settings.lull_proxy_hops > 0:
        scheme = headers.get("x-forwarded-proto", scheme).split(",")[0].strip() or scheme
    return f"{scheme}://{headers['host']}"


READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def hosted_server(live: Live, store: RunStore, settings: ApiSettings) -> MCPServer:
    """The MCP server mounted on the API at /mcp (see the module notes)."""
    server = MCPServer(
        "lull",
        title="Lull audio description",
        instructions=HOSTED_INSTRUCTIONS,
        middleware=[StrictArguments(HOSTED_ARGS)],
    )

    def visitor(ctx: Context) -> str:
        request = _request(ctx)
        ip = client_ip(request, settings.lull_proxy_hops) if request is not None else "in-process"
        return live.limiter.client_key(ip)

    @server.tool(annotations=READ_ONLY)
    def list_samples() -> Samples:
        """The video clips this server can describe (licensed excerpts, at most 45 s each): id,
        title, duration in seconds, credit and licence. Pass an id to describe_video."""
        return _samples(settings)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, open_world_hint=False))
    async def describe_video(
        sample_id: Annotated[str, Field(description="A sample id from list_samples.")],
        ctx: Context,
        mode: Annotated[
            Mode,
            Field(
                description="standard (descriptions fitted into pauses), or extended (also "
                "a version that holds the picture for longer descriptions)."
            ),  # fmt: skip
        ] = "standard",
    ) -> Described:
        """Describe one of the samples: listen, watch, research names, plan, voice and verify
        each description against the frames, then publish the tracks. Takes a few minutes;
        progress comes per stage. Returns the run id (for get_tracks and get_report), its
        status, cue counts, coverage, the WCAG 1.2.3/1.2.5/1.2.7 rows, model spend and
        searches, and an edit token that owns the run (hand it to a person for the Studio).
        Refused with the API's codes: busy (another description is running), rate_limited,
        budget (the demo's spend cap), sample (unknown id)."""
        try:
            started = await live_mod.start_sample(
                live, visitor(ctx), sample_id, RunOptions(mode=mode, voice="Sofia")
            )
        except LiveError as e:
            raise _refused(e) from None
        try:
            final = await follow(started.backlog, started.queue, ctx)
        finally:  # the caller gone (cancelled): the run stops at once, its owner may resume it
            started.run.unsubscribe(started.queue)
        return described(store, started.job.run_dir, final, started.token)

    @server.tool(annotations=READ_ONLY)
    def get_tracks(
        run_id: Annotated[str, Field(description="A run id from describe_video.")],
        ctx: Context,
    ) -> Tracks:
        """The description cues of a finished run as text with timecodes and status (verified,
        held, extended, edited, approved; held lines carry why), and the URLs of its
        description tracks: descriptions.vtt, extended.vtt, described-audio.m4a and
        described.mp4 (extended.mp4 when it was made)."""
        base = _base_url(ctx, settings)
        return tracks(store, run_id, lambda d, name: f"{base}/v1/runs/{d.name}/files/{name}")

    @server.tool(annotations=READ_ONLY)
    def get_report(
        run_id: Annotated[str, Field(description="A run id from describe_video.")],
    ) -> Report:
        """The conformance report of a finished run, exactly as its manifest says: cue counts,
        coverage, the WCAG 1.2.3 / 1.2.5 / 1.2.7 rows with what blocks each, the held lines
        with their reasons, model spend and searches, and the synthetic-speech disclosure."""
        return report(store, run_id)

    return server


def _samples(settings: ApiSettings) -> Samples:
    return Samples(
        samples=[
            Sample(**{k: e.get(k) for k in ("id", "title", "duration", "credit", "licence")})
            for e in live_mod.sample_entries(settings.samples_dir)
        ]
    )


OPEN_HOSTS = TransportSecuritySettings(enable_dns_rebinding_protection=False)


class McpEndpoint:
    """/mcp on the API, as an ASGI app: the MCP server's streamable HTTP transport (stateless:
    one instance, any request), with a fresh session manager each time the app starts."""

    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self._app: Any = None

    @contextlib.asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        app = self.server.streamable_http_app(
            streamable_http_path=PATH, stateless_http=True, transport_security=OPEN_HOSTS
        )
        async with self.server.session_manager.run():
            self._app = app
            try:
                yield
            finally:
                self._app = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._app is None:
            body = b'{"error":"unavailable","message":"The MCP server is not running."}'
            await send({"type": "http.response.start", "status": 503,
                        "headers": [(b"content-type", b"application/json")]})  # fmt: skip
            await send({"type": "http.response.body", "body": body})
            return
        await self._app(scope, receive, send)


# -- the local server ------------------------------------------------------------------------------


class LocalLull:
    """The local server's state: runs under `home`/runs, the user's own ledger and caps, one
    description at a time. Tests swap `runner` and `probe`."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self.settings = ApiSettings(lull_data=home)
        self.store = RunStore(self.settings)
        self.runner: Callable[[Job, Callable[[Event], None]], object] = live_mod._run_pipeline
        self.probe: Callable[[Path], media.Probe] = media.probe
        self.busy = False
        self._ledger: Ledger | None = None

    @property
    def ledger(self) -> Ledger:
        if self._ledger is None:
            self._ledger = Ledger(get_settings().lull_home / "ledger.db")
        return self._ledger

    def video(self, sample_id: str | None, path: str | None) -> Path:
        if (sample_id is None) == (path is None):
            raise ToolError("invalid: Give either a sample_id or a path to a video file.")
        if sample_id is not None:
            found = live_mod._sample(self.settings, sample_id)
            if found is None:
                raise ToolError("sample: There is no sample with that id.")
            return found
        assert path is not None
        try:
            p = Path(path).expanduser().resolve(strict=True)
            mode = p.stat().st_mode
        except (OSError, RuntimeError):
            raise ToolError("not_found: There is no file at that path.") from None
        if not stat.S_ISREG(mode):
            raise ToolError("invalid: That path is not a regular file.")
        if p.suffix.lower() not in FORMATS:
            raise ToolError("format: Give an mp4, mov, webm or mkv video file.")
        return p

    async def describe(
        self, ctx: Context, sample_id: str | None, path: str | None, mode: Mode
    ) -> Described:
        video = self.video(sample_id, path)
        if self.busy:
            raise ToolError("busy: Another description is running here. Try again when it ends.")
        self.busy = True
        try:
            return await self._describe(ctx, video, mode)
        finally:
            self.busy = False

    async def _describe(self, ctx: Context, video: Path, mode: Mode) -> Described:
        try:
            info = await anyio.to_thread.run_sync(self.probe, video)
            media.assert_safe_container(info)
        except Exception as e:
            raise ToolError(f"unreadable: This file could not be read as a video ({e}).") from None
        sha = await anyio.to_thread.run_sync(file_sha256, video)
        run_id = run_id_for(video, sha)
        run_dir = self.settings.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        job = Job(run_id, run_dir, video, RunOptions(mode=mode), info.duration, self.ledger)
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[dict] = asyncio.Queue()

        def on_event(ev: Event) -> None:  # the worker thread
            if ev.type == "stage_start" and job.cancel.is_set():
                raise RunStopped(ev.stage)
            item = {**ev.data, "type": ev.type, "stage": ev.stage}
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(q.put_nowait, item)

        work = loop.run_in_executor(None, self.runner, job, on_event)
        progress = Progress(ctx)
        try:
            while not work.done():
                getter = asyncio.ensure_future(q.get())
                finished, _ = await asyncio.wait({getter, work}, return_when="FIRST_COMPLETED")
                if getter in finished:
                    await progress(getter.result())
                else:
                    getter.cancel()
            await asyncio.sleep(0)  # what the thread queued last
            while not q.empty():
                await progress(q.get_nowait())
        except asyncio.CancelledError:
            job.cancel.set()  # the caller gone: no more spend, it ends at its next stage
            raise
        error = work.exception()
        if error is not None:
            message = f"{type(error).__name__}: {' '.join(str(error).split())[:500]}"
            return Described(run_id=run_id, status="failed", error=message)
        return described(self.store, run_dir, {"type": "done"}, None)

    def queue(self) -> Queue:
        items = []
        root = self.settings.runs_dir
        for d in sorted(root.iterdir()) if root.is_dir() else []:
            manifest = self.store.manifest(d) if d.is_dir() else None
            if manifest is None:
                continue
            summary = _dict(manifest.get("summary"))
            held = _held(summary, _cues_doc(self.store, d))
            if held:
                video = _dict(manifest.get("video"))
                created = (
                    manifest.get("created") if isinstance(manifest.get("created"), str) else None
                )
                items.append(Queued(run_id=d.name, title=video.get("name"), created=created,
                                    held=held))  # fmt: skip
        items.sort(key=lambda q: q.created or "", reverse=True)
        return Queue(runs=items)


def local_server(home: Path | None = None) -> MCPServer:
    """The MCP server `lull mcp` runs over stdio (see the module notes)."""
    lull = LocalLull(home or get_settings().lull_home)
    server = MCPServer("lull", title="Lull audio description", instructions=LOCAL_INSTRUCTIONS)
    server.lull = lull  # type: ignore[attr-defined]  (tests reach the state through it)

    @server.tool(annotations=READ_ONLY)
    def list_samples() -> Samples:
        """The sample clips available here (LULL_SAMPLES or LULL_DATA/samples): id, title,
        duration in seconds, credit and licence. Pass an id to describe_video."""
        return _samples(lull.settings)

    @server.tool(annotations=ToolAnnotations(read_only_hint=False, open_world_hint=True))
    async def describe_video(
        ctx: Context,
        path: Annotated[
            str | None,
            Field(description="A video file on this machine (mp4, mov, webm or mkv)."),
        ] = None,
        sample_id: Annotated[
            str | None, Field(description="Or a sample id from list_samples.")
        ] = None,
        mode: Annotated[
            Mode,
            Field(
                description="standard (descriptions fitted into pauses), or extended (also "
                "a version that holds the picture for longer descriptions)."
            ),  # fmt: skip
        ] = "standard",
    ) -> Described:
        """Describe a video file on this machine (or a sample) with the user's own keys and
        spend caps: listen, watch, research names, plan, voice and verify each description
        against the frames, then publish the tracks under LULL_HOME/runs. Takes minutes;
        progress comes per stage; a run already made is reused from its cache. Returns the run
        id, status, cue counts, coverage, the WCAG 1.2.3/1.2.5/1.2.7 rows, model spend and
        searches."""
        return await lull.describe(ctx, sample_id, path, mode)

    @server.tool(annotations=READ_ONLY)
    def get_tracks(
        run_id: Annotated[str, Field(description="A run id from describe_video.")],
    ) -> Tracks:
        """The description cues of a finished run as text with timecodes and status (verified,
        held, extended, edited, approved; held lines carry why), and the paths of its
        description tracks: descriptions.vtt, extended.vtt, described-audio.m4a and
        described.mp4 (extended.mp4 when it was made)."""
        return tracks(lull.store, run_id, lambda d, name: str(d / name))

    @server.tool(annotations=READ_ONLY)
    def get_report(
        run_id: Annotated[str, Field(description="A run id from describe_video.")],
    ) -> Report:
        """The conformance report of a finished run, exactly as its manifest says: cue counts,
        coverage, the WCAG 1.2.3 / 1.2.5 / 1.2.7 rows with what blocks each, the held lines
        with their reasons, model spend and searches, and the synthetic-speech disclosure."""
        return report(lull.store, run_id)

    @server.tool(annotations=READ_ONLY)
    def review_queue() -> Queue:
        """The runs here with description lines held for a person to check (each with its text
        and why it was held), newest first. Open a run's report or tracks to review them."""
        return lull.queue()

    return server
