from __future__ import annotations

import logging
import mimetypes

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from lull.api.settings import ApiSettings
from lull.api.store import RunStore

log = logging.getLogger(__name__)

CONTENT_TYPES = {
    ".vtt": "text/vtt",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".html": "text/html; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
    ".jpg": "image/jpeg",
}
REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:"


def _speech_ok() -> bool:
    try:
        from lull.speech import NemoSpeechHTTP

        return bool(NemoSpeechHTTP.from_settings().health())
    except Exception:
        log.warning("speech health check failed", exc_info=True)
        return False


def _budget() -> dict[str, float]:
    from lull.config import get_settings
    from lull.ledger import Ledger, _midnight_utc

    s = get_settings()
    ledger = Ledger(path=s.lull_home / "ledger.db")
    return {
        "spent_today": ledger.spent(since=_midnight_utc()),
        "cap_day": ledger.cap_day,
        "spent_total": ledger.spent(),
        "cap_total": ledger.cap_total,
    }


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or ApiSettings()
    store = RunStore(settings)
    app = FastAPI(title="Lull API", version="0.1.0")
    app.state.settings = settings
    app.state.store = store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/v1/health")
    def health():
        return {"ok": True, "speech": _speech_ok(), "budget": _budget()}

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

    @app.get("/v1/runs/{run_id}/files/{path:path}")
    def get_file(run_id: str, path: str):
        found = store.find(run_id)
        target = store.servable(found[1], path) if found else None
        if target is None:
            raise HTTPException(404, "not found")
        ext = target.suffix.lower()
        ctype = CONTENT_TYPES.get(ext) or mimetypes.guess_type(target.name)[0]
        headers = {"X-Content-Type-Options": "nosniff"}
        if ext == ".html":
            headers["Content-Security-Policy"] = REPORT_CSP
        return FileResponse(target, media_type=ctype, headers=headers)

    return app


app = create_app()
