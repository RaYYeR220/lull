"""Client for a local NeMo-Speech.cpp server (ASR, diarization, TTS) plus speaker assignment."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx

from lull.config import get_settings
from lull.types import Turn, Word

DEFAULT_URL = "http://127.0.0.1:8089"
NEAR_S = 0.5


class SpeechError(RuntimeError):
    """Speech server returned an error or an unusable response."""


class SpeechClient(Protocol):
    def transcribe(self, wav: Path) -> list[Word]: ...

    def diarize(self, wav: Path) -> list[Turn]: ...

    def synthesize(self, text: str, voice: str, dst: Path) -> Path: ...


def _fail(what: str, resp: httpx.Response) -> SpeechError:
    return SpeechError(f"{what}: HTTP {resp.status_code}: {resp.text[:300]}")


class NemoSpeechHTTP:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 300.0,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, transport=transport, timeout=timeout
        )

    @classmethod
    def from_settings(cls) -> NemoSpeechHTTP:
        s = get_settings()
        return cls(s.lull_speech_url or DEFAULT_URL, s.lull_speech_key)

    def health(self) -> bool:
        try:
            r = self._http.get("/health")
            return r.status_code == 200 and r.json().get("status") == "ok"
        except (httpx.HTTPError, ValueError, AttributeError):
            return False

    def _post_file(self, path: str, wav: Path, data: dict[str, str]) -> dict:
        try:
            with open(wav, "rb") as f:
                files = {"file": (Path(wav).name, f, "audio/wav")}
                r = self._http.post(path, files=files, data=data)
        except httpx.HTTPError as e:
            raise SpeechError(f"{path}: {e}") from e
        if not r.is_success:
            raise _fail(path, r)
        try:
            return r.json()
        except ValueError as e:
            raise _fail(path, r) from e

    def transcribe(self, wav: Path) -> list[Word]:
        body = self._post_file(
            "/v1/audio/transcriptions",
            wav,
            {"response_format": "verbose_json", "timestamp_granularities[]": "word"},
        )
        words: list[Word] = []
        for w in body.get("words") or []:
            text = str(w.get("word", "")).strip()
            if not text:
                continue
            start = max(0.0, float(w["start"]))
            end = max(start, float(w["end"]))
            words.append(Word(text=text, start=start, end=end))
        words.sort(key=lambda w: w.start)  # stable: keeps server order on ties
        return words

    def diarize(self, wav: Path) -> list[Turn]:
        body = self._post_file("/v1/audio/diarizations", wav, {})
        turns: list[Turn] = []
        for s in body.get("segments") or []:
            start = max(0.0, float(s["start"]))
            turns.append(
                Turn(speaker=int(s["speaker"]), start=start, end=max(start, float(s["end"])))
            )
        return turns

    def synthesize(self, text: str, voice: str, dst: Path) -> Path:
        payload = {"model": "magpie", "input": text, "voice": voice, "response_format": "wav"}
        try:
            r = self._http.post("/v1/audio/speech", json=payload)
        except httpx.HTTPError as e:
            raise SpeechError(f"/v1/audio/speech: {e}") from e
        if not r.is_success:
            raise _fail("/v1/audio/speech", r)
        ctype = r.headers.get("content-type", "")
        if not ctype.startswith("audio/") or not r.content:
            raise SpeechError(
                f"/v1/audio/speech: HTTP {r.status_code}: unusable body "
                f"(content-type={ctype!r}, {len(r.content)} bytes): {r.text[:200]!r}"
            )
        dst = Path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(r.content)
        return dst


def assign_speakers(words: list[Word], turns: list[Turn]) -> list[Word]:
    """Give each word the speaker of the turn it overlaps most (ties: earlier turn).

    Zero overlap falls back to the nearest turn within NEAR_S seconds, else None.
    """
    ordered = sorted(turns, key=lambda t: (t.start, t.end))
    out: list[Word] = []
    for w in words:
        best, best_ov = None, 0.0
        for t in ordered:
            ov = min(w.end, t.end) - max(w.start, t.start)
            if ov > best_ov:
                best, best_ov = t, ov
        if best is None:
            best_d = NEAR_S
            for t in ordered:
                d = max(t.start - w.end, w.start - t.end, 0.0)
                if d <= NEAR_S and (best is None or d < best_d):
                    best, best_d = t, d
        out.append(w.model_copy(update={"speaker": best.speaker if best else None}))
    return out
