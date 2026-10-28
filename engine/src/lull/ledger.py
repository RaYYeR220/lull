"""Call ledger and spend caps: every call is recorded; caps are checked before spending."""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from types import EllipsisType
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


class SearchCapReached(Exception):
    """A run or a day has made as many web searches as its cap allows."""

    def __init__(self, scope: Literal["run", "day"], done: int, cap: int) -> None:
        self.scope, self.done, self.cap = scope, done, cap
        super().__init__(f"{scope} search cap reached: {done} of {cap} searches")


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
    """Every model and search call, in SQLite, and the caps checked before each one.

    Spend caps (run, day, total) count model calls only; a cap of None is no cap and a cap of
    0 refuses every call. Web searches are counted instead of priced (their notional cost is
    kept for reporting): `tavily_run` and `tavily_day` searches at most. A cap left out comes
    from the settings (LULL_CAP_*, LULL_TAVILY_*).

    check() and record() are separate steps: two calls checked at once may both pass and
    overshoot a cap by one estimate. The pipeline makes its calls one at a time per run."""

    def __init__(
        self,
        path: Path,
        *,
        cap_run: float | None | EllipsisType = ...,
        cap_day: float | None | EllipsisType = ...,
        cap_total: float | None | EllipsisType = ...,
        tavily_run: int | None | EllipsisType = ...,
        tavily_day: int | None | EllipsisType = ...,
    ) -> None:
        s = get_settings()
        self.cap_run = s.lull_cap_run if cap_run is ... else cap_run
        self.cap_day = s.lull_cap_day if cap_day is ... else cap_day
        self.cap_total = s.lull_cap_total if cap_total is ... else cap_total
        self.tavily_run = s.lull_tavily_run if tavily_run is ... else tavily_run
        self.tavily_day = s.lull_tavily_day if tavily_day is ... else tavily_day
        for scope, given, cap in (("run", cap_run, self.cap_run), ("day", cap_day, self.cap_day),
                                  ("total", cap_total, self.cap_total)):  # fmt: skip
            if given is not ...:
                continue
            if cap is None:
                log.warning("ledger: LULL_CAP_%s is none: no %s cap, spending is not limited",
                            scope.upper(), scope)  # fmt: skip
            elif cap <= 0:
                log.warning("ledger: LULL_CAP_%s is %s: the %s cap refuses every call",
                            scope.upper(), cap, scope)  # fmt: skip
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[CallRecord], None]] = []
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        """Close the database (a closed ledger raises on use; closing twice is fine)."""
        with self._lock:
            self._db.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

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

    def spent(
        self, *, run_id: str | None = None, since: float | None = None, tavily: bool = False
    ) -> float:
        """USD recorded for model calls (with `tavily`, the notional search cost too)."""
        where, args = ([], []) if tavily else (["provider != 'tavily'"], [])
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

    def searches(self, *, run_id: str | None = None, since: float | None = None) -> int:
        """Web searches recorded (Tavily calls)."""
        where, args = ["provider = 'tavily'"], []
        if run_id is not None:
            where.append("run_id = ?")
            args.append(run_id)
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        sql = "SELECT COUNT(*) FROM calls WHERE " + " AND ".join(where)
        with self._lock:
            return int(self._db.execute(sql, args).fetchone()[0])

    def check_searches(self, run_id: str) -> None:
        """Raise SearchCapReached if one more web search would cross a search cap."""
        for scope, cap, done in (
            ("run", self.tavily_run, self.searches(run_id=run_id)),
            ("day", self.tavily_day, self.searches(since=_midnight_utc())),
        ):
            if cap is not None and done + 1 > cap:
                raise SearchCapReached(scope, done, cap)  # type: ignore[arg-type]

    def check(self, run_id: str, estimate: float) -> None:
        """Raise BudgetExceeded if spending `estimate` more on model calls would cross a cap
        (None = no cap, 0 = refuse every call). An estimate that is not a finite,
        non-negative amount raises ValueError."""
        if not (isinstance(estimate, int | float) and math.isfinite(estimate) and estimate >= 0):
            raise ValueError(f"ledger: the spend estimate must be a cost in USD, got {estimate!r}")
        checks = (
            ("run", self.cap_run, self.spent(run_id=run_id)),
            ("day", self.cap_day, self.spent(since=_midnight_utc())),
            ("total", self.cap_total, self.spent()),
        )
        for scope, cap, spent in checks:
            if cap is None:
                continue
            if cap <= 0:
                raise BudgetExceeded(scope, f"{scope} cap is $0: no model call is allowed")  # type: ignore[arg-type]
            if spent + estimate > cap:
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
        """Every run in the ledger, most recent activity first: calls, model spend (`cost_usd`;
        web searches are counted in `searches`, never charged), first/last time."""
        with self._lock:
            rows = self._db.execute(
                "SELECT run_id, COUNT(*),"
                " SUM(CASE WHEN provider = 'tavily' THEN 0 ELSE cost_usd END),"
                " SUM(CASE WHEN provider = 'tavily' THEN 1 ELSE 0 END), MIN(ts), MAX(ts)"
                " FROM calls GROUP BY run_id ORDER BY MAX(ts) DESC, run_id"
            ).fetchall()
        return [
            {"run_id": r[0], "calls": r[1], "cost_usd": r[2], "searches": r[3],
             "first_ts": r[4], "last_ts": r[5]}
            for r in rows
        ]  # fmt: skip

    def summary(self, run_id: str, *, since: float | None = None) -> dict:
        """Calls of `run_id` by role and model (only those at or after `since` when given)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT role, model, COUNT(*), SUM(tokens_in), SUM(tokens_cached),"
                " SUM(tokens_out), SUM(cost_usd), SUM(latency_s) FROM calls"
                " WHERE run_id = ? AND ts >= ? GROUP BY role, model ORDER BY MIN(id)",
                (run_id, since if since is not None else float("-inf")),
            ).fetchall()
        groups = [
            {"role": r[0], "model": r[1], "calls": r[2], "tokens_in": r[3],
             "tokens_cached": r[4], "tokens_out": r[5], "cost_usd": r[6], "latency_s": r[7]}
            for r in rows
        ]  # fmt: skip
        searches = self.searches(run_id=run_id, since=since)
        return {
            "run_id": run_id,
            "calls": sum(g["calls"] for g in groups),
            "total_cost_usd": sum(g["cost_usd"] for g in groups),  # list price of searches too
            "model_cost_usd": self.spent(run_id=run_id, since=since) if groups else 0.0,
            "searches": searches,
            "by_role_model": groups,
        }
