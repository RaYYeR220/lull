"""In-memory sliding windows for starting live runs: a few per visitor an hour, a cap a day.

Visitors are known only by sha256(daily salt + ip): the salt is random, lives in memory and
changes at UTC midnight, so the key cannot be turned back into an address or linked across
days. The windows reset when the process restarts; the ledger's day cap still holds then.
"""

from __future__ import annotations

import hashlib
import math
import secrets
import time
from collections import deque
from collections.abc import Callable
from typing import Literal

DAY = 86_400.0
HOUR = 3_600.0

Limit = Literal["client", "global"]


class RateLimiter:
    def __init__(
        self,
        *,
        per_client: int = 3,
        client_window: float = HOUR,
        per_day: int = 20,
        day_window: float = DAY,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.per_client, self.client_window = per_client, client_window
        self.per_day, self.day_window = per_day, day_window
        self._clock = clock
        self._clients: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._salt: tuple[int, bytes] = (-1, b"")

    def client_key(self, ip: str) -> str:
        """sha256 of today's salt and the address (hex)."""
        day = int(self._clock() // DAY)
        if self._salt[0] != day:
            self._salt = (day, secrets.token_bytes(16))
        return hashlib.sha256(self._salt[1] + ip.encode("utf-8", "replace")).hexdigest()

    def _prune(self, now: float) -> None:
        while self._all and self._all[0] <= now - self.day_window:
            self._all.popleft()
        for key in list(self._clients):
            hits = self._clients[key]
            while hits and hits[0] <= now - self.client_window:
                hits.popleft()
            if not hits:
                del self._clients[key]

    def retry_after(self, key: str) -> tuple[Limit, int] | None:
        """None when `key` may start a run now, else which limit stops it and the whole
        seconds until a slot frees."""
        now = self._clock()
        self._prune(now)
        hits = self._clients.get(key, ())
        if len(hits) >= self.per_client:
            return "client", max(1, math.ceil(hits[0] + self.client_window - now))
        if len(self._all) >= self.per_day:
            return "global", max(1, math.ceil(self._all[0] + self.day_window - now))
        return None

    def hit(self, key: str) -> None:
        now = self._clock()
        self._clients.setdefault(key, deque()).append(now)
        self._all.append(now)
