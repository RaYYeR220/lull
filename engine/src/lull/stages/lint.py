"""Deterministic audio-description lint rules (DCMP, Netflix, ACB by R-id) and a tiny autofix."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel


class Finding(BaseModel):
    rule: str
    message: str
    span: tuple[int, int] | None = None
    severity: Literal["error", "warning"] = "error"


_DQUOTE = re.compile(r'"[^"]*(?:"|$)|\u201c[^\u201d]*(?:\u201d|$)')
_LEAD_IN = re.compile(
    r"\b(?:reads?|words? appears?|text appears?|titles?|captions?|subtitles?"
    r"|text on screen|on-screen text)\s*:[^.!?]*[.!?]?",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

_PERSON = {"we", "us", "our", "ours", "you", "your", "yours"}
_AUX = {"was", "were", "had"}
_PAST = {
    "walked", "looked", "opened", "closed", "turned", "entered", "picked", "pulled", "pushed",
    "smiled", "nodded", "stepped", "reached", "grabbed", "climbed", "jumped", "stood", "sat",
    "ran", "took", "went", "came", "saw", "watched", "waited", "stared", "held",
}  # fmt: skip
_EMOTION = {"sad", "happy", "angry", "afraid", "nervous", "excited", "upset", "confused"}
_GENDER = {
    "he", "she", "his", "her", "him", "hers", "himself", "herself", "man", "men", "woman",
    "women", "boy", "girl", "guy", "lady", "gentleman", "gentlemen", "mother", "father",
}  # fmt: skip
_HEDGE = re.compile(r"\b(?:appears?|seems?)\s+to\b|\blooks?\s+like\b", re.IGNORECASE)
_LABEL = re.compile(r"\bflashbacks?\b|\bcamera\s+pans?\b|\bclose[- ]?ups?\b", re.IGNORECASE)
_WE_SEE = re.compile(r"\b(?:we|you)(?:\s+can)?\s+see(?:\s+that)?\b\s*", re.IGNORECASE)


def quoted_spans(text: str) -> list[tuple[int, int]]:
    """Spans of verbatim on-screen/spoken text: double-quoted or after a lead-in ("reads:" ...)."""
    spans = [m.span() for pat in (_DQUOTE, _LEAD_IN) for m in pat.finditer(text)]
    return sorted(spans)


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)


def lint(
    text: str,
    *,
    verbatim: list[str] = [],  # noqa: B006
    named: set[str] = frozenset(),  # type: ignore[assignment]
    introduced: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[Finding]:
    out: list[Finding] = []
    quoted = quoted_spans(text)

    def add(rule, msg, span, severity="error", *, exempt=True):
        if exempt and span and _inside(span[0], quoted):
            return
        out.append(Finding(rule=rule, message=msg, span=span, severity=severity))

    for m in _WORD.finditer(text):
        raw, span = m.group(), m.span()
        w = raw.lower()
        base = w.split("'")[0]
        if base in _PERSON or raw == "I" or raw.startswith("I'"):
            add("R7-person", f"first/second person '{raw}'; describe in third person", span)
        if w in _AUX or w in _PAST:
            add("R7-tense", f"past tense '{raw}'; use present tense", span, "warning")
        if base in _EMOTION:
            add("R12-emotion", f"'{raw}' is an interpretation; describe the visible expression",
                span, "warning")  # fmt: skip
        if base in _GENDER:
            add("R14-gender", "gendered word; confirm the programme establishes it", span,
                "warning")  # fmt: skip
    for m in _HEDGE.finditer(text):
        add("R12-hedge", f"hedge '{m.group()}'; state what is visible", m.span())
    for m in _LABEL.finditer(text):
        add("R18-label", f"film-language label '{m.group()}'", m.span(), "warning")

    known = {n.lower() for n in introduced}
    for name in sorted(named):
        if name.lower() in known:
            continue
        for m in re.finditer(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            add("R15-name", f"'{name}' used before being introduced in dialogue or on screen",
                m.span(), exempt=False)  # fmt: skip

    for v in verbatim:
        if v not in text:
            add("R19-verbatim", f"on-screen text {v!r} must appear exactly", None, exempt=False)

    m = re.search(r"\bin a moment\b", text, re.IGNORECASE)
    if m and not text.startswith("In a moment, "):
        add("R2-ahead-format", 'ahead-of-time cue must start with "In a moment, "', m.span(),
            exempt=False)  # fmt: skip
    return out


def _strip_we_see(text: str) -> str:
    quoted = quoted_spans(text)
    parts, last = [], 0
    for m in _WE_SEE.finditer(text):
        if _inside(m.start(), quoted):
            continue
        parts.append(text[last : m.start()])
        last = m.end()
    parts.append(text[last:])
    return "".join(parts)


def autofix(text: str) -> str:
    """Deterministic, meaning-preserving fixes only. Idempotent."""
    prev = None
    while prev != text:
        prev = text
        text = _strip_we_see(text)
        text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if text[0].isalpha():
        text = text[0].upper() + text[1:]
    if not re.search(r'[.!?]["\u201d]?$', text):
        text += "."
    return text
