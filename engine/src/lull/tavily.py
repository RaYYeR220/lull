"""Tavily REST client (search + extract) with retries and ledger accounting."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

import httpx

from lull.config import get_settings
from lull.ledger import CallRecord, Ledger, cost_of_tavily

BASE_URL = "https://api.tavily.com"
_TRANSIENT = {429, 500, 502, 503, 504}
_BACKOFF = (1, 2, 4)


class TavilyError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body[:300]


class Tavily:
    def __init__(
        self,
        api_key: str,
        ledger: Ledger,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self.ledger = ledger
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=BASE_URL, timeout=httpx.Timeout(60.0, connect=15.0), transport=transport
        )

    @classmethod
    def from_settings(cls, ledger: Ledger) -> Tavily | None:
        key = get_settings().tavily_api_key
        return cls(key, ledger) if key else None

    def close(self) -> None:
        self._http.close()

    def search(self, query: str, *, run_id: str, max_results: int = 3) -> list[dict]:
        body = {"query": query, "max_results": max_results, "search_depth": "basic"}
        data = self._call("/search", body, "tavily-search", run_id, 1)
        return list(data.get("results") or [])

    def extract(self, urls: list[str], *, run_id: str) -> list[dict]:
        if not urls:
            return []
        credits = math.ceil(len(urls) / 5)
        data = self._call("/extract", {"urls": urls}, "tavily-extract", run_id, credits)
        return list(data.get("results") or [])

    def _call(self, path: str, body: dict, model: str, run_id: str, expected: float) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        for i in range(len(_BACKOFF) + 1):
            self.ledger.check_searches(run_id)  # searches are counted, not priced
            t0 = time.monotonic()
            try:
                r = self._http.post(path, json=body, headers=headers)
            except httpx.TransportError as e:
                if i < len(_BACKOFF):
                    self._sleep(_BACKOFF[i])
                    continue
                raise TavilyError(f"network error: {type(e).__name__}") from None
            latency = time.monotonic() - t0
            if r.status_code == 200:
                try:
                    data = r.json()
                except ValueError:
                    raise TavilyError("non-JSON 200 response", 200, r.text) from None
                self._record(data, model, run_id, latency, expected)
                return data
            if r.status_code in _TRANSIENT and i < len(_BACKOFF):
                self._sleep(_BACKOFF[i])
                continue
            raise TavilyError(f"Tavily HTTP {r.status_code}", r.status_code, r.text)
        raise AssertionError("unreachable")  # pragma: no cover

    def _record(self, data: dict, model: str, run_id: str, latency: float, expected: float) -> None:
        usage = data.get("usage")
        credits = expected
        if isinstance(usage, dict) and isinstance(usage.get("credits"), int | float):
            credits = float(usage["credits"])
        self.ledger.record(
            CallRecord(
                run_id=run_id,
                stage="research",
                role="research",
                provider="tavily",
                model=model,
                tokens_in=0,
                tokens_cached=0,
                tokens_out=0,
                latency_s=latency,
                cost_usd=cost_of_tavily(credits),
                ts=time.time(),
            )  # fmt: skip
        )
