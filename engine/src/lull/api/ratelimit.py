"""In-memory sliding windows: a few per visitor in a short window (an hour for live runs, a
minute for Studio changes), optionally a day's worth per visitor, and a cap a day for everyone.

Visitors are known only by sha256(daily salt + ip): the salt is random, lives in memory and
changes at UTC midnight, so the key cannot be turned back into an address or linked across
days. An IPv6 address counts as its /64 (one subscriber's prefix), an IPv4-mapped one as the
IPv4 address. The windows reset when the process restarts; the ledger's day cap still holds then.
"""

from __future__ import annotations

import hashlib
import ipaddress
import math
import secrets
import time
from collections import deque
from collections.abc import Callable
from typing import Literal

DAY = 86_400.0
HOUR = 3_600.0

Limit = Literal["client", "client_day", "global"]


def subject(ip: str) -> str:
    """What a visitor is counted as: the address, or for IPv6 its /64."""
    try:
        addr = ipaddress.ip_address(ip.strip().strip("[]"))
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.IPv6Network((int(addr) >> 64 << 64, 64)))
    return str(addr)


class RateLimiter:
    def __init__(
        self,
        *,
        per_client: int = 3,
        client_window: float = HOUR,
        per_day: int = 20,
        day_window: float = DAY,
        per_client_day: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.per_client, self.client_window = per_client, client_window
        self.per_day, self.day_window = per_day, day_window
        self.per_client_day = per_client_day  # one visitor's share of a day (None: no limit)
        self._clock = clock
        self._clients: dict[str, deque[float]] = {}
        self._client_days: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()
        self._salt: tuple[int, bytes] = (-1, b"")

    def client_key(self, ip: str) -> str:
        """sha256 of today's salt and the visitor's address (hex)."""
        day = int(self._clock() // DAY)
        if self._salt[0] != day:
            self._salt = (day, secrets.token_bytes(16))
        return hashlib.sha256(self._salt[1] + subject(ip).encode("utf-8", "replace")).hexdigest()

    def _prune(self, now: float) -> None:
        while self._all and self._all[0] <= now - self.day_window:
            self._all.popleft()
        for windows, span in ((self._clients, self.client_window), (self._client_days, DAY)):
            for key in list(windows):
                hits = windows[key]
                while hits and hits[0] <= now - span:
                    hits.popleft()
                if not hits:
                    del windows[key]

    def retry_after(self, key: str) -> tuple[Limit, int] | None:
        """None when `key` may start a run now, else which limit stops it and the whole
        seconds until a slot frees."""
        now = self._clock()
        self._prune(now)
        hits = self._clients.get(key, ())
        if len(hits) >= self.per_client:
            return "client", max(1, math.ceil(hits[0] + self.client_window - now))
        days = self._client_days.get(key, ())
        if self.per_client_day is not None and len(days) >= self.per_client_day:
            return "client_day", max(1, math.ceil(days[0] + DAY - now))
        if len(self._all) >= self.per_day:
            return "global", max(1, math.ceil(self._all[0] + self.day_window - now))
        return None

    def hit(self, key: str) -> None:
        now = self._clock()
        self._clients.setdefault(key, deque()).append(now)
        if self.per_client_day is not None:
            self._client_days.setdefault(key, deque()).append(now)
        self._all.append(now)
