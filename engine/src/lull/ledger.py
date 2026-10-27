"""Call ledger and spend caps: every call is recorded; caps are checked before spending."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from lull.config import get_settings

log = logging.getLogger(__name__)

# model -> (input, cached input, output) USD per 1M tokens
PRICES: dict[str, tuple[float, float, float]] = {
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 0.12, 3.00),
    "nvidia/Nemotron-3.5-Super-VL": (0.30, 0.06, 0.90),
    "nvidia/Nemotron-3_5-Lightning": (0.06, 0.06, 0.24),
}

TAVILY_CREDIT_USD = 0.008  # reporting only


def cost_of(model: str, tokens_in: int, tokens_cached: int, tokens_out: int) -> float:
    """USD cost of a Token Factory call. `tokens_in` is the full prompt size; the cached
    part of it is billed at the cached rate. Unknown models raise KeyError."""
    try:
        p_in, p_cached, p_out = PRICES[model]
    except KeyError:
        raise KeyError(f"no price for model {model!r}") from None
    uncached = max(tokens_in - tokens_cached, 0)
    return (uncached * p_in + tokens_cached * p_cached + tokens_out * p_out) / 1_000_000


def cost_of_tavily(credits: float) -> float:
    return credits * TAVILY_CREDIT_USD


class BudgetExceeded(Exception):
    def __init__(self, scope: Literal["run", "day", "total", "provider"], message: str = ""):
        self.scope = scope
        super().__init__(message or f"{scope} spend cap exceeded")


@dataclass(frozen=True)
class CallRecord:
    run_id: str
    stage: str
    role: str
    provider: Literal["tokenfactory", "tavily", "speech"]
    model: str
    tokens_in: int
    tokens_cached: int
    tokens_out: int
    latency_s: float
    cost_usd: float
    ts: float


_COLS = [f.name for f in fields(CallRecord)]
_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL, stage TEXT NOT NULL, role TEXT NOT NULL,
    provider TEXT NOT NULL, model TEXT NOT NULL,
    tokens_in INTEGER NOT NULL, tokens_cached INTEGER NOT NULL, tokens_out INTEGER NOT NULL,
    latency_s REAL NOT NULL, cost_usd REAL NOT NULL, ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_run ON calls(run_id);
CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts);
"""


def _midnight_utc() -> float:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class Ledger:
    def __init__(
        self,
        path: Path,
        *,
        cap_run: float | None = None,
        cap_day: float | None = None,
        cap_total: float | None = None,
    ) -> None:
        s = get_settings()
        self.cap_run = s.lull_cap_run if cap_run is None else cap_run
        self.cap_day = s.lull_cap_day if cap_day is None else cap_day
        self.cap_total = s.lull_cap_total if cap_total is None else cap_total
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[CallRecord], None]] = []
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def record(self, rec: CallRecord) -> None:
        d = asdict(rec)
        with self._lock:
            self._db.execute(
                f"INSERT INTO calls ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
                [d[c] for c in _COLS],
            )
            self._db.commit()
            subscribers = list(self._subscribers)
        for fn in subscribers:  # outside the lock: a subscriber may read the ledger
            try:
                fn(rec)
            except Exception:
                log.exception("ledger: a subscriber failed on a %s call", rec.role)

    def subscribe(self, fn: Callable[[CallRecord], None]) -> Callable[[], None]:
        """Call `fn` with every record once it is stored (on the recording thread); returns a
        function that unsubscribes. A subscriber that raises is logged, never fatal."""
        with self._lock:
            self._subscribers.append(fn)

        def unsubscribe() -> None:
            with self._lock:
                if fn in self._subscribers:
                    self._subscribers.remove(fn)

        return unsubscribe

    def spent(self, *, run_id: str | None = None, since: float | None = None) -> float:
        where, args = [], []
        if run_id is not None:
            where.append("run_id = ?")
            args.append(run_id)
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM calls"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._lock:
            return float(self._db.execute(sql, args).fetchone()[0])

    def check(self, run_id: str, estimate: float) -> None:
        """Raise BudgetExceeded if spending `estimate` more would cross a cap (<=0 = no cap)."""
        checks = (
            ("run", self.cap_run, self.spent(run_id=run_id)),
            ("day", self.cap_day, self.spent(since=_midnight_utc())),
            ("total", self.cap_total, self.spent()),
        )
        for scope, cap, spent in checks:
            if cap > 0 and spent + estimate > cap:
                raise BudgetExceeded(
                    scope,  # type: ignore[arg-type]
                    f"{scope} cap ${cap:.2f} would be exceeded "
                    f"(spent ${spent:.4f} + estimate ${estimate:.4f})",
                )

    def calls(self, run_id: str) -> list[CallRecord]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {','.join(_COLS)} FROM calls WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [CallRecord(*r) for r in rows]

    def runs(self) -> list[dict]:
        """Every run in the ledger, most recent activity first: calls, cost, first/last time."""
        with self._lock:
            rows = self._db.execute(
                "SELECT run_id, COUNT(*), SUM(cost_usd), MIN(ts), MAX(ts) FROM calls"
                " GROUP BY run_id ORDER BY MAX(ts) DESC, run_id"
            ).fetchall()
        return [
            {"run_id": r[0], "calls": r[1], "cost_usd": r[2], "first_ts": r[3], "last_ts": r[4]}
            for r in rows
        ]

    def summary(self, run_id: str) -> dict:
        with self._lock:
            rows = self._db.execute(
                "SELECT role, model, COUNT(*), SUM(tokens_in), SUM(tokens_cached),"
                " SUM(tokens_out), SUM(cost_usd), SUM(latency_s) FROM calls"
                " WHERE run_id = ? GROUP BY role, model ORDER BY MIN(id)",
                (run_id,),
            ).fetchall()
        groups = [
            {"role": r[0], "model": r[1], "calls": r[2], "tokens_in": r[3],
             "tokens_cached": r[4], "tokens_out": r[5], "cost_usd": r[6], "latency_s": r[7]}
            for r in rows
        ]  # fmt: skip
        return {
            "run_id": run_id,
            "calls": sum(g["calls"] for g in groups),
            "total_cost_usd": sum(g["cost_usd"] for g in groups),
            "by_role_model": groups,
        }
