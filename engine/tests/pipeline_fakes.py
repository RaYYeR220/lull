"""Stand-ins for the services a whole pipeline run talks to, shaped for the `tone_slides` video
(12 s: red 0-6 s, blue 6-12 s; a 440 Hz tone 0-4 s, then silence).

The transcript says "Hi, I am Ada." at 0.3-1.6 s, the recogniser stretching "Ada." from 0.99 to
3.5 s (clamped back to 1.6 s), and "Bye." at 11.2-11.95 s. The silence between is 9.2 s usable,
which split_gaps
cuts into g001 [1.8, 6.25] and g002 [6.55, 11.0]. The watch answer has three events: red screen
e001 (0-6 s), a "Lull" logo e002 (0.3-1.5 s, during speech: no gap can hold it, so it becomes
the extended cue x002) and blue screen e003 (7-12 s). The schedule puts e001 in g001 (cue c001)
and e003 in g002 (cue c002).
"""

from __future__ import annotations

import copy
import time
import wave
from pathlib import Path

import numpy as np

from lull.ledger import CallRecord, Ledger, cost_of
from lull.stages.plan import count_words
from lull.types import Turn, Word

WORDS = [
    Word(text="Hi,", start=0.3, end=0.6),
    Word(text="I", start=0.65, end=0.75),
    Word(text="am", start=0.8, end=0.95),
    Word(text="Ada.", start=0.99, end=3.5),  # implausible: clamped to 1.6
    Word(text="Bye.", start=11.2, end=11.95),  # a drawn-out last word: kept
]
TURNS = [Turn(speaker=0, start=0.3, end=1.6), Turn(speaker=0, start=11.2, end=11.6)]


def _event(t0, t1, kind, text, verbatim=None, salience=4):
    return {
        "t_start": t0,
        "t_end": t1,
        "kind": kind,
        "text": text,
        "verbatim_text": verbatim,
        "salience": salience,
        "said_in_dialogue": False,
    }


WATCH = {
    "events": [
        _event(0.0, 6.0, "setting", "A plain red screen."),
        _event(0.3, 1.5, "on_screen_text", "A small logo reads Lull.", "Lull", salience=5),
        _event(7.0, 12.0, "setting", "The screen turns blue."),  # unlike e001: not folded
    ]
}
TERMS = {"terms": [{"term": "Lull", "kind": "work", "why": "the logo"}]}
PLAN = {
    "cast": [{"descriptor": "a presenter", "name": "Ada"}],
    "items": [
        {
            "event_id": "e001",
            "priority": 4,
            "short": "A red screen.",
            "medium": "The whole screen is plain red.",
            "long": None,
        },
        {
            "event_id": "e002",
            "priority": 5,
            "short": 'A logo reads "Lull".',
            "medium": None,
            "long": None,
        },
        {
            "event_id": "e003",
            "priority": 4,
            "short": "The screen is blue.",
            "medium": "Now the whole screen is plain blue.",
            "long": None,
        },
    ],
}
CUE_TEXT = {
    "c001": "The whole screen is plain red.",
    "c002": "Now the whole screen is plain blue.",
    "x002": 'A logo reads "Lull".',
}


def sine_wav(path: Path, seconds: float, *, rate: int = 22050, freq: float = 1000.0) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = round(seconds * rate)
    x = 0.3 * np.sin(2 * np.pi * freq * np.arange(n) / rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


class FakeSpeech:
    """The local speech server: canned words and turns; speech at `wps` words per second."""

    def __init__(self, words=WORDS, turns=TURNS, *, wps: float = 3.0):
        self.words, self.turns, self.wps = list(words), list(turns), wps
        self.calls: list[str] = []
        self.spoken: list[str] = []

    def transcribe(self, wav: Path) -> list[Word]:
        self.calls.append("transcribe")
        return list(self.words)

    def diarize(self, wav: Path) -> list[Turn]:
        self.calls.append("diarize")
        return list(self.turns)

    def synthesize(self, text: str, voice: str, dst: Path) -> Path:
        self.calls.append("synthesize")
        self.spoken.append(text)
        return sine_wav(Path(dst), max(0.3, count_words(text) / self.wps))


def description(messages: list[dict]) -> str:
    """The <description> a finalize call is about."""
    content = next(m for m in messages if m["role"] == "user")["content"]
    text = (
        content
        if isinstance(content, str)
        else "\n".join(p["text"] for p in content if p.get("type") == "text")
    )
    return text.split("<description>\n", 1)[1].split("\n</description>", 1)[0]


def verify_all(call: dict) -> dict:
    return {"claims": [{"text": description(call["messages"]), "verdict": "supported", "at": 0.5}]}


class FakeTF:
    """Token Factory: answers by role (a dict, or a function of the call) and records every call
    in the ledger as the real client does."""

    def __init__(self, ledger: Ledger, **answers):
        self.ledger = ledger
        self.answers = {
            "watch": WATCH,
            "research": TERMS,
            "plan": PLAN,
            "verify": verify_all,
            **answers,
        }
        self.calls: list[dict] = []

    def json(self, model, messages, schema, *, run_id, stage, role, **kw) -> dict:
        call = {
            "model": model,
            "messages": messages,
            "schema": schema,
            "run_id": run_id,
            "stage": stage,
            "role": role,
            **kw,
        }
        self.calls.append(call)
        answer = self.answers[role]
        out = answer(call) if callable(answer) else copy.deepcopy(answer)
        self.ledger.record(
            CallRecord(
                run_id=run_id,
                stage=stage,
                role=role,
                provider="tokenfactory",
                model=model,
                tokens_in=1000,
                tokens_cached=0,
                tokens_out=100,
                latency_s=0.5,
                cost_usd=cost_of(model, 1000, 0, 100),
                ts=time.time(),
            )
        )
        return out

    def roles(self) -> list[str]:
        return [c["role"] for c in self.calls]
