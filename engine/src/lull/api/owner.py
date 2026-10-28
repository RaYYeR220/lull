"""Who may change a live run: whoever holds the edit token that POST /v1/runs (or a fork)
handed out, sent as "Authorization: Bearer <token>". Only the token's sha256 is stored, in
request.json under TOKEN_KEY, and it is compared in constant time."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from starlette.requests import Request

TOKEN_KEY = "edit_token_sha256"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> tuple[str, str]:
    """A fresh token (32 random bytes, URL-safe) and its sha256."""
    token = secrets.token_urlsafe(32)
    return token, digest(token)


def bearer(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    return token if scheme.lower() == "bearer" and token else None


def holds_token(run_dir: Path, request: Request) -> bool:
    """True when the request carries the token whose sha256 the run's request.json keeps."""
    try:
        stored = json.loads((run_dir / "request.json").read_text(encoding="utf-8")).get(TOKEN_KEY)
    except (OSError, ValueError, AttributeError):
        return False
    given = bearer(request)
    if not isinstance(stored, str) or given is None:
        return False
    return hmac.compare_digest(digest(given), stored)
