"""Nebius Token Factory client (OpenAI-compatible chat completions) with retries and ledger."""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from lull.config import get_settings
from lull.ledger import BudgetExceeded, CallRecord, Ledger, cost_of

log = logging.getLogger(__name__)

ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
VL = "nvidia/Nemotron-3.5-Super-VL"
LIGHTNING = "nvidia/Nemotron-3_5-Lightning"

BASE_URL = "https://api.tokenfactory.nebius.com/v1"
MAX_VIDEO_BYTES = 10 * 1024 * 1024
_TRANSIENT = {429, 500, 502, 503}
_BACKOFF = (1, 2, 4)
_MIME = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", re.DOTALL)
_THINK = re.compile(r"^<think>.*?</think>", re.DOTALL)
_UNSENT = (httpx.ConnectError, httpx.ConnectTimeout)  # the request never reached the server


class TFError(Exception):
    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body[:500]


class TFUnusable(TFError):
    """No usable answer: cut off by the token limit, empty or unparseable, even after the
    retry with twice the tokens. The call was made and paid for; the caller may fail closed."""


def _data_url(path: Path) -> tuple[str, str]:
    mime = _MIME.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"unsupported media extension: {path.suffix!r}")
    return mime, f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def video_part(path: Path) -> dict:
    path = Path(path)
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise ValueError(f"{path.name} is larger than 10 MiB")
    mime, url = _data_url(path)
    if not mime.startswith("video/"):
        raise ValueError(f"{path.name} is not a video")
    return {"type": "video_url", "video_url": {"url": url}}


def image_part(path: Path) -> dict:
    mime, url = _data_url(Path(path))
    if not mime.startswith("image/"):
        raise ValueError(f"{Path(path).name} is not an image")
    return {"type": "image_url", "image_url": {"url": url}}


def _parse_json(content: str) -> dict:
    """The JSON object in a model answer: a leading <think>...</think> block (reasoning that
    leaked into the content) and a code fence are dropped; anything but an object raises
    ValueError, so a stray list or an unclosed reasoning block counts as unparseable."""
    s = content.strip()
    s = _THINK.sub("", s, count=1).strip()
    if s.startswith("<think>"):
        raise ValueError("unclosed <think> block")
    m = _FENCE.match(s)
    if m:
        s = m.group(1).strip()
    out = json.loads(s)
    if not isinstance(out, dict):
        raise ValueError(f"expected a JSON object, got {type(out).__name__}")
    return out


class TokenFactory:
    def __init__(
        self,
        api_key: str,
        ledger: Ledger,
        *,
        base_url: str = BASE_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key
        self.ledger = ledger
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(300.0, connect=15.0),
            transport=transport,
        )

    @classmethod
    def from_settings(cls, ledger: Ledger) -> TokenFactory:
        key = get_settings().nebius_api_key
        if not key:
            raise TFError("NEBIUS_API_KEY is not set (put it in the environment or .env)")
        return cls(key, ledger)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> TokenFactory:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API -----------------------------------------------------------------

    def json(
        self,
        model: str,
        messages: list[dict],
        schema: dict,
        *,
        run_id: str,
        stage: str,
        role: str,
        thinking: bool = False,
        max_tokens: int = 4000,
        temperature: float = 0.6,
        estimate: float = 0.01,
    ) -> dict:
        extra = {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": stage, "schema": schema, "strict": True},
            }
        }
        return self._complete(  # type: ignore[return-value]
            model, messages, extra, _parse_json, run_id, stage, role,
            thinking, max_tokens, temperature, estimate,
        )  # fmt: skip

    def text(
        self,
        model: str,
        messages: list[dict],
        *,
        run_id: str,
        stage: str,
        role: str,
        thinking: bool = False,
        max_tokens: int = 4000,
        temperature: float = 0.6,
        estimate: float = 0.01,
    ) -> str:
        return self._complete(  # type: ignore[return-value]
            model, messages, {}, lambda c: c, run_id, stage, role,
            thinking, max_tokens, temperature, estimate,
        )  # fmt: skip

    # -- internals ------------------------------------------------------------------

    def _complete(
        self, model, messages, extra, parse, run_id, stage, role,
        thinking, max_tokens, temperature, estimate,
    ):  # fmt: skip
        last = ""
        for attempt_tokens in (max_tokens, max_tokens * 2):
            body = {
                "model": model,
                "messages": messages,
                "max_tokens": attempt_tokens,
                "temperature": temperature,
                "chat_template_kwargs": {"enable_thinking": thinking},
                **extra,
            }
            data, latency = self._post(body, run_id, estimate)
            self._record(data, model, run_id, stage, role, latency, estimate)
            if not data.get("usage"):
                log.warning("tf: %s answered without usage; charged at its estimate $%.4f",
                            model, estimate)  # fmt: skip
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            if choice.get("finish_reason") == "length" or not content.strip():
                last = "finish_reason=length or empty content"
                continue
            try:
                return parse(content)
            except ValueError as e:  # JSONDecodeError subclasses ValueError
                last = f"unparseable output ({e})"
        raise TFUnusable(f"truncated: {last} after retry with max_tokens={max_tokens * 2}")

    def _post(self, body: dict, run_id: str, estimate: float) -> tuple[dict, float]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for i in range(len(_BACKOFF) + 1):
            self.ledger.check(run_id, estimate)
            t0 = time.monotonic()
            try:
                r = self._http.post("/chat/completions", json=body, headers=headers)
            except _UNSENT as e:  # never reached the server: safe to send again
                if i < len(_BACKOFF):
                    self._sleep(_BACKOFF[i])
                    continue
                raise TFError(f"network error: {e}") from e
            except httpx.TransportError as e:  # may have run (and been billed): never resent
                raise TFError(f"network error after sending: {e}") from e
            latency = time.monotonic() - t0
            if r.status_code == 200:
                try:
                    return r.json(), latency
                except ValueError as e:
                    raise TFError("non-JSON 200 response", 200, r.text) from e
            if r.status_code == 402:
                raise BudgetExceeded("provider", f"Token Factory returned 402: {r.text[:200]}")
            if r.status_code in _TRANSIENT and i < len(_BACKOFF):
                self._sleep(_BACKOFF[i])
                continue
            raise TFError(f"Token Factory HTTP {r.status_code}", r.status_code, r.text)
        raise AssertionError("unreachable")  # pragma: no cover

    def _record(
        self, data: dict, model: str, run_id: str, stage: str, role: str, latency: float,
        estimate: float = 0.0,
    ) -> None:  # fmt: skip
        """One ledger row for a call. An answer that reports no usage is charged at the
        call's estimate, so the caps still see it."""
        usage = data.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens")
        if cached is None:
            cached = usage.get("prompt_cache_hit_tokens") or 0
        tin = int(usage.get("prompt_tokens") or 0)
        tout = int(usage.get("completion_tokens") or 0)
        cached = int(cached)
        self.ledger.record(
            CallRecord(
                run_id=run_id,
                stage=stage,
                role=role,
                provider="tokenfactory",
                model=model,
                tokens_in=tin,
                tokens_cached=cached,
                tokens_out=tout,
                latency_s=latency,
                cost_usd=cost_of(model, tin, cached, tout) if usage else estimate,
                ts=time.time(),
            )  # fmt: skip
        )
