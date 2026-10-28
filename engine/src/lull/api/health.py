"""GET /v1/health: the speech server, the model spend and the web searches so far, cached for
a few seconds.

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


def ledger_budget(
    path: Path, *, cap_day: float | None, cap_total: float | None, searches_cap_day: int | None
) -> dict:
    """From the ledger at `path`, read-only: model spend today and in total (what the caps
    count; a cap of None is none) and the web searches made today (counted, never priced)."""
    out: dict = {
        "spent_today": 0.0, "cap_day": cap_day, "spent_total": 0.0, "cap_total": cap_total,
        "searches_today": 0, "searches_cap_day": searches_cap_day,
    }  # fmt: skip
    path = Path(path)
    if not path.is_file():
        return out
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            spent = "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE provider != 'tavily'"
            today = con.execute(spent + " AND ts >= ?", (midnight_utc(),)).fetchone()[0]
            total = con.execute(spent).fetchone()[0]
            searches = con.execute(
                "SELECT COUNT(*) FROM calls WHERE provider = 'tavily' AND ts >= ?",
                (midnight_utc(),),
            ).fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        log.warning("health: the ledger could not be read", exc_info=True)
        out["spent_today"] = out["spent_total"] = out["searches_today"] = None
        return out
    out.update(spent_today=float(today), spent_total=float(total), searches_today=int(searches))
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

    def __init__(
        self,
        ttl: float = HEALTH_TTL,
        clock: Callable[[], float] = time.monotonic,
        *,
        searches_cap_run: int | None = None,
    ):
        self.ttl, self.clock = ttl, clock
        self.searches_cap_run = searches_cap_run  # what a live run may search
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
            "budget": {
                **ledger_budget(
                    s.lull_home / "ledger.db",
                    cap_day=s.lull_cap_day,
                    cap_total=s.lull_cap_total,
                    searches_cap_day=s.lull_tavily_day,
                ),
                "cap_run": s.lull_cap_run,
                "searches_cap_run": self.searches_cap_run,
            },
        }
        with self._lock:
            self._cached = (self.clock() + self.ttl, body)
        return body
