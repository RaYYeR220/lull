"""GET /v1/health: the speech server and the spend so far, cached for a few seconds.

The ledger is only read: a read-only SQLite connection opened and closed per check, never the
engine's Ledger (which creates the folder, the file and its tables). A missing ledger is no
spend yet; one that cannot be read reports its spend as null.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from lull.config import get_settings
from lull.ledger import TAVILY_CREDIT_USD

log = logging.getLogger(__name__)

HEALTH_TTL = 10.0


def midnight_utc(now: datetime | None = None) -> float:
    """The epoch seconds of the last UTC midnight (at or before `now`)."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


SPEND_KEYS = ("spent", "models", "tavily_credits")


def ledger_budget(path: Path, *, cap_day: float, cap_total: float) -> dict:
    """Spend today and in total from the ledger at `path`, read-only: all of it (what the caps
    count), the model calls' share and the Tavily credits apart."""
    out: dict = {"cap_day": cap_day, "cap_total": cap_total}
    for key in SPEND_KEYS:
        out[f"{key}_today"] = out[f"{key}_total"] = 0.0
    path = Path(path)
    if not path.is_file():
        return out
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            sql = (
                "SELECT provider = 'tavily', COALESCE(SUM(cost_usd), 0) FROM calls{} "
                "GROUP BY provider = 'tavily'"
            )
            today = con.execute(sql.format(" WHERE ts >= ?"), (midnight_utc(),)).fetchall()
            total = con.execute(sql.format("")).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        log.warning("health: the ledger could not be read", exc_info=True)
        for key in SPEND_KEYS:
            out[f"{key}_today"] = out[f"{key}_total"] = None
        return out
    for when, rows in (("today", today), ("total", total)):
        by = {bool(tavily): float(cost) for tavily, cost in rows}
        out[f"spent_{when}"] = by.get(False, 0.0) + by.get(True, 0.0)
        out[f"models_{when}"] = by.get(False, 0.0)
        out[f"tavily_credits_{when}"] = round(by.get(True, 0.0) / TAVILY_CREDIT_USD, 2)
    return out


def speech_ok() -> bool:
    try:
        from lull.speech import NemoSpeechHTTP

        return bool(NemoSpeechHTTP.from_settings().health())
    except Exception:
        log.warning("speech health check failed", exc_info=True)
        return False


class Health:
    """The health answer, worked out at most once per `ttl` seconds."""

    def __init__(self, ttl: float = HEALTH_TTL, clock: Callable[[], float] = time.monotonic):
        self.ttl, self.clock = ttl, clock
        self._lock = threading.Lock()
        self._cached: tuple[float, dict] | None = None

    def get(self) -> dict:
        """The cached answer, or a fresh one. The lock guards the cache only: a slow speech
        probe never makes other callers wait (two may probe at once when the cache is cold)."""
        with self._lock:
            if self._cached is not None and self.clock() < self._cached[0]:
                return self._cached[1]
        s = get_settings()
        body = {
            "ok": True,
            "speech": speech_ok(),
            "budget": ledger_budget(
                s.lull_home / "ledger.db", cap_day=s.lull_cap_day, cap_total=s.lull_cap_total
            ),
        }
        with self._lock:
            self._cached = (self.clock() + self.ttl, body)
        return body
