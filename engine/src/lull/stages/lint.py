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


_DQUOTE = re.compile(r'"[^"]*"|“[^”]*”')
_LEAD_IN = re.compile(
    r"\b(?:reads?|words? appears?|text appears?|titles?|captions?|subtitles?"
    r"|text on screen|on-screen text)\s*:[^.!?]*[.!?]?",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_APOS = re.compile(r"['’]")
_ROMAN_PREV = {"war", "chapter", "part", "type", "act", "phase", "book", "volume", "stage", "level"}

_PERSON = {"we", "us", "our", "ours", "you", "your", "yours"}
_AUX = {"was", "were", "had"}
_PAST = {
    "walked",
    "looked",
    "opened",
    "closed",
    "turned",
    "entered",
    "picked",
    "pulled",
    "pushed",
    "smiled",
    "nodded",
    "stepped",
    "reached",
    "grabbed",
    "climbed",
    "jumped",
    "stood",
    "sat",
    "ran",
    "took",
    "went",
    "came",
    "saw",
    "watched",
    "waited",
    "stared",
    "held",
}
_EMOTION = {"sad", "happy", "angry", "afraid", "nervous", "excited", "upset", "confused"}
_GENDER = {
    "he",
    "she",
    "his",
    "her",
    "him",
    "hers",
    "himself",
    "herself",
    "man",
    "men",
    "woman",
    "women",
    "boy",
    "girl",
    "guy",
    "lady",
    "gentleman",
    "gentlemen",
    "mother",
    "father",
}
_HEDGE = re.compile(r"\b(?:appears?|seems?)\s+to\b|\blooks?\s+like\b", re.IGNORECASE)
_LABEL = re.compile(
    r"\bflashbacks?\b|\bcamera\s+pans?\b|\bclose-ups?\b"
    r"|\b(?:a|an|the)\s+close\s?ups?\b|\bclose\s?ups?\s+(?:of|on)\b",
    re.IGNORECASE,
)
# "we see ..." only at the start of the text / a sentence (optionally after "In a moment, ").
_SENT_WE_SEE = re.compile(
    r"(?P<pre>^|[.!?][\"”]?\s+)(?P<ahead>(?:In a moment,\s+)?)"
    r"(?:we|you)(?:\s+can)?\s+see\s+(?:that\s+)?(?P<nxt>[^\s.!?,;:])",
    re.IGNORECASE,
)


def quoted_spans(text: str) -> list[tuple[int, int]]:
    """Spans of verbatim on-screen/spoken text: double-quoted or after a lead-in ("reads:" ...)."""
    spans = [m.span() for pat in (_DQUOTE, _LEAD_IN) for m in pat.finditer(text)]
    return sorted(spans)


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)


# two or more tokens of capitals and digits in a row ("STEP 2: BAKE"): on-screen text read out
_CAPS_RUN = re.compile(
    r"\b[A-Z0-9][A-Z0-9'\u2019&\-]*(?:[ \t:,./+\-\u2013\u2014]+[A-Z0-9][A-Z0-9'\u2019&\-]*)+\b"
)


def screen_spans(text: str, verbatim: list[str]) -> list[tuple[int, int]]:
    """Where `text` reads the screen: runs of capitals ("STEP 2: BAKE", with at least one word
    of two capitals) and each occurrence of the on-screen text watch read (`verbatim`)."""
    spans = [m.span() for m in _CAPS_RUN.finditer(text)
             if re.search(r"[A-Z]{2}", m.group())]  # fmt: skip
    for v in verbatim:
        if v:
            spans += [(m.start(), m.end()) for m in re.finditer(re.escape(v), text)]
    return spans


def _is_numeral_i(text: str, m: re.Match[str]) -> bool:
    before = text[: m.start()].rstrip()
    prev = re.search(r"([A-Za-z]+)$", before)
    if prev and prev.group(1).lower() in _ROMAN_PREV:
        return True
    if text[: m.start()].endswith("-") or before[-1:].isdigit():
        return True
    return bool(re.match(r"-\d", text[m.end() :]))


def lint(
    text: str,
    *,
    verbatim: list[str] = [],  # noqa: B006
    named: set[str] = set(),  # noqa: B006
    introduced: set[str] = set(),  # noqa: B006
) -> list[Finding]:
    out: list[Finding] = []
    quoted = quoted_spans(text)
    screen = screen_spans(text, verbatim)  # text read off the screen: not the describer's

    def add(rule, msg, span, severity="error", *, exempt=True):
        if exempt and span and (_inside(span[0], quoted) or _inside(span[0], screen)):
            return
        out.append(Finding(rule=rule, message=msg, span=span, severity=severity))

    for m in _WORD.finditer(text):
        raw, span = m.group(), m.span()
        w = raw.lower()
        base = _APOS.split(w)[0]
        is_i = raw == "I" or (raw.startswith("I") and _APOS.match(raw[1:2]))
        if (base in _PERSON or is_i) and not (raw == "I" and _is_numeral_i(text, m)):
            add("R7-person", f"first/second person '{raw}'; describe in third person", span)
        if w in _AUX or w in _PAST:
            add("R7-tense", f"past tense '{raw}'; use present tense", span, "warning")
        if base in _EMOTION:
            msg = f"'{raw}' is an interpretation; describe the visible expression"
            add("R12-emotion", msg, span, "warning")
        if base in _GENDER:
            msg = "gendered word; confirm the programme establishes it"
            add("R14-gender", msg, span, "warning")
    for m in _HEDGE.finditer(text):
        add("R12-hedge", f"hedge '{m.group()}'; state what is visible", m.span())
    for m in _LABEL.finditer(text):
        add("R18-label", f"film-language label '{m.group()}'", m.span(), "warning")

    quoted_text = " ".join(text[s:e] for s, e in quoted)
    known = {n.lower() for n in introduced}
    for name in sorted(named):
        pat = rf"\b{re.escape(name)}\b"
        if name.lower() in known or re.search(pat, quoted_text):
            continue
        for m in re.finditer(pat, text):
            if _inside(m.start(), screen):
                continue  # the line reads the screen here; it names nobody
            msg = f"'{name}' used before being introduced in dialogue or on screen"
            add("R15-name", msg, m.span(), exempt=False)

    for v in verbatim:
        if v not in text:
            add("R19-verbatim", f"on-screen text {v!r} must appear exactly", None, exempt=False)

    mtext = _mask_quotes(text, quoted)
    for sent in re.finditer(r"[^.!?]+[.!?]*", mtext):
        body = sent.group()
        m = re.search(r"\bin a moment\b", body, re.IGNORECASE)
        if not m:
            continue
        start = sent.start() + len(body) - len(body.lstrip())
        if not mtext[start:].startswith("In a moment, "):
            msg = 'ahead-of-time cue must start with "In a moment, "'
            span = (sent.start() + m.start(), sent.start() + m.end())
            add("R2-ahead-format", msg, span, exempt=False)
    return out


def _mask_quotes(text: str, quoted: list[tuple[int, int]]) -> str:
    """`text` with quoted spans blanked, same length; a sentence mark that ends a quote
    ('"Hi."', 'reads: Day one.') stays, so the sentence still ends there."""
    masked = list(text)
    for s, e in quoted:
        end = e
        while end > s and text[end - 1] in "\"”’'":
            end -= 1
        keep = end - 1 if end > s and text[end - 1] in ".!?…" else None
        masked[s:e] = " " * (e - s)
        if keep is not None:
            masked[keep] = text[keep]
    return "".join(masked)


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(e, merged[-1][1]))
        else:
            merged.append((s, e))
    return merged


def _collapse_outside_quotes(text: str) -> str:
    parts, last = [], 0
    for s, e in _merge(quoted_spans(text)):
        parts.append(re.sub(r"\s+", " ", text[last:s]))
        parts.append(text[s:e])
        last = e
    parts.append(re.sub(r"\s+", " ", text[last:]))
    return "".join(parts).strip()


def _strip_we_see(text: str) -> str:
    quoted = quoted_spans(text)

    def repl(m: re.Match[str]) -> str:
        if _inside(m.end("ahead"), quoted):
            return m.group(0)
        nxt = m.group("nxt")
        if not m.group("ahead"):
            nxt = nxt.upper()
        return m.group("pre") + m.group("ahead") + nxt

    return _SENT_WE_SEE.sub(repl, text)


def autofix(text: str) -> str:
    """Deterministic, meaning-preserving fixes only. Idempotent."""
    prev = None
    while prev != text:
        prev = text
        text = _collapse_outside_quotes(_strip_we_see(text))
    if not text:
        return ""
    if text[0].isalpha():
        text = text[0].upper() + text[1:]
    if not re.search(r'[.!?:…]["”]?$', text):
        text += "."
    return text
