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

log = logging.getLogger(__name__)

HEALTH_TTL = 10.0


def midnight_utc(now: datetime | None = None) -> float:
    """The epoch seconds of the last UTC midnight (at or before `now`)."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def ledger_budget(path: Path, *, cap_day: float, cap_total: float) -> dict:
    """Spend today and in total from the ledger at `path`, read-only."""
    out: dict = {"spent_today": 0.0, "cap_day": cap_day, "spent_total": 0.0, "cap_total": cap_total}
    path = Path(path)
    if not path.is_file():
        return out
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM calls"
            today = con.execute(sql + " WHERE ts >= ?", (midnight_utc(),)).fetchone()[0]
            total = con.execute(sql).fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        log.warning("health: the ledger could not be read", exc_info=True)
        out["spent_today"] = out["spent_total"] = None
        return out
    out["spent_today"], out["spent_total"] = float(today), float(total)
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
        with self._lock:
            now = self.clock()
            if self._cached is not None and now < self._cached[0]:
                return self._cached[1]
            s = get_settings()
            body = {
                "ok": True,
                "speech": speech_ok(),
                "budget": ledger_budget(
                    s.lull_home / "ledger.db", cap_day=s.lull_cap_day, cap_total=s.lull_cap_total
                ),
            }
            self._cached = (now + self.ttl, body)
            return body
