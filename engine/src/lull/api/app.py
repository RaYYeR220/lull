"""The Lull API. Run it as one process (uvicorn --workers 1): the live executor, the upload
and Studio caps, the rate limits and the per-run edit locks are in memory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from lull.api.health import Health
from lull.api.live import SEARCHES_PER_RUN, Live, LiveError, live_error_handler
from lull.api.live import router as live_router
from lull.api.settings import ApiSettings
from lull.api.store import RunStore
from lull.api.studio import Studio
from lull.api.studio import router as studio_router

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".vtt": "text/vtt",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".html": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
    ".jpg": "image/jpeg",
    ".wav": "audio/wav",
}
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:"
CORS_METHODS = ["GET", "HEAD", "OPTIONS", "POST", "PATCH"]
CORS_HEADERS = ["Authorization", "Content-Type", "Last-Event-ID"]


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
        studio = app.state.studio
        swept = studio.sweep()  # before serving: no edit is in progress yet
        if swept:
            log.info("startup: removed %d edit folders nothing refers to", swept)
        studio.expire_forks()  # then every fork_sweep_every seconds while serving
        timer = asyncio.create_task(studio.expire_forks_forever(), name="fork expiry")
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
        # the cached answer, plus whether a live run is going now (never cached)
        return {**app.state.health.get(), "live_busy": app.state.live.executor.busy}

    @app.get("/v1/runs")
    def list_runs():
        return store.list()

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str):
        found = store.find(run_id)
        detail = store.detail(found[1]) if found else None
        if detail is None:
            raise HTTPException(404, "run not found")
        return detail

    @app.api_route("/v1/runs/{run_id}/files/{path:path}", methods=["GET", "HEAD"])
    def get_file(run_id: str, path: str):
        found = store.find(run_id)
        target = store.servable(found[1], path) if found else None
        if target is None:
            raise HTTPException(404, "not found")
        return file_response(target)

    @app.api_route("/v1/runs/{run_id}/cues/{cue_id}/audio", methods=["GET", "HEAD"])
    def get_cue_audio(run_id: str, cue_id: str):
        found = store.find(run_id)
        target = store.cue_audio(found[1], cue_id) if found else None
        if target is None:
            raise HTTPException(404, "not found")
        return file_response(target)

    return app


app = create_app()
