"""The Lull API. Run it as one process (uvicorn --workers 1): the live executor, the upload
and Studio caps, the rate limits and the per-run edit locks are in memory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from lull.api.health import Health
from lull.api.live import SEARCHES_PER_RUN, Live, LiveError, live_error_handler
from lull.api.live import router as live_router
from lull.api.owner import holds_token
from lull.api.settings import ApiSettings
from lull.api.store import RunStore
from lull.api.studio import Studio
from lull.api.studio import router as studio_router

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".vtt": "text/vtt",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".html": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".wav": "audio/wav",
}
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:"
CORS_METHODS = ["GET", "HEAD", "OPTIONS", "POST", "PATCH"]
CORS_HEADERS = ["Authorization", "Content-Type", "Last-Event-ID"]


NO_VERIFY = "LULL_NO_VERIFY"  # the engine's evaluation-only switch: frames go unchecked
FALSE_WORDS = ("", "0", "false", "no", "off")


def refuse_unverified() -> None:
    """The API never describes without frame verification: with LULL_NO_VERIFY set to
    anything true it does not start at all (so /v1/health is never answered)."""
    value = os.environ.get(NO_VERIFY)
    if value is not None and value.strip().lower() not in FALSE_WORDS:
        message = (
            f"{NO_VERIFY}={value!r} turns frame verification off (evaluation only): "
            "the API does not start with it; unset it"
        )
        log.critical(message)
        raise RuntimeError(message)


def no_run() -> LiveError:
    return LiveError(404, "not_found", "There is no run with that id.")


def no_file() -> LiveError:
    return LiveError(404, "not_found", "There is no such file in this run.")


def file_response(target) -> FileResponse:
    ext = target.suffix.lower()
    ctype = CONTENT_TYPES.get(ext) or mimetypes.guess_type(target.name)[0]
    headers = {"X-Content-Type-Options": "nosniff"}
    if ext == ".html":
        headers["Content-Security-Policy"] = REPORT_CSP
    return FileResponse(target, media_type=ctype, headers=headers)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()
    store = RunStore(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        refuse_unverified()
        studio = app.state.studio
        swept = studio.sweep()  # before serving: no edit is in progress yet
        if swept:
            log.info("startup: removed %d edit folders nothing refers to", swept)
        studio.expire_runs()  # then every fork_sweep_every seconds while serving
        timer = asyncio.create_task(studio.expire_runs_forever(), name="run expiry")
        try:
            yield
        finally:
            timer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await timer

    app = FastAPI(title="Lull API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.health = Health(searches_cap_run=SEARCHES_PER_RUN)
    app.state.live = Live(settings, store)
    app.state.studio = Studio(settings, store, app.state.live)
    app.add_exception_handler(LiveError, live_error_handler)
    app.include_router(live_router)
    app.include_router(studio_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_methods=CORS_METHODS,
        allow_headers=CORS_HEADERS,
        expose_headers=["Retry-After"],
    )

    @app.get("/v1/health")
    def health():
        # the cached answer, plus whether a live run is going now (never cached) and how
        # long uploads and forks are kept (what the site's notice says)
        studio = app.state.studio
        return {
            **app.state.health.get(),
            "live_busy": app.state.live.executor.busy,
            "retention_days": round(studio.upload_ttl / 86400, 3),
            "fork_retention_hours": round(studio.fork_ttl / 3600, 3),
        }

    @app.get("/v1/runs")
    def list_runs():
        return store.list()

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str, request: Request):
        found = store.find(run_id)
        if found is None:
            raise no_run()
        return store.detail(found[1], owner=holds_token(found[1], request))

    @app.api_route("/v1/runs/{run_id}/original", methods=["GET", "HEAD"])
    def get_original(run_id: str):
        found = store.find(run_id)
        target = store.original(found[1]) if found else None
        if target is None:
            raise no_file()
        return file_response(target)

    @app.api_route("/v1/runs/{run_id}/files/{path:path}", methods=["GET", "HEAD"])
    def get_file(run_id: str, path: str):
        found = store.find(run_id)
        target = store.servable(found[1], path) if found else None
        if target is None:
            raise no_file()
        return file_response(target)

    @app.api_route("/v1/runs/{run_id}/cues/{cue_id}/audio", methods=["GET", "HEAD"])
    def get_cue_audio(run_id: str, cue_id: str):
        found = store.find(run_id)
        target = store.cue_audio(found[1], cue_id) if found else None
        if target is None:
            raise no_file()
        return file_response(target)

    return app


app = create_app()
