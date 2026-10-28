"""Verify: the vision model re-checks every claim of a description against its own frames.

A blind listener takes a description as fact, so it may say only what the frames show. The
vision model sees a short clip around the cue's events (never the whole video), splits the
description into atomic visual claims and judges each one supported, not visible or
contradicted. Lightning then rewrites the description without the claims that failed.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from functools import cache
from importlib.resources import files
from pathlib import Path

from lull.tf import LIGHTNING, VL, TokenFactory, video_part
from lull.types import Claim, VisualEvent

log = logging.getLogger(__name__)

STAGE = "finalize"
CLIP_PAD_S = 1.0  # context on each side of the cue's events
CLIP_CAP_S = 12.0  # longest clip sent to the verifier (cost control)
CLIP_MIN_S = 4.0  # shortest clip: a 1 s event needs context to be seen
CLIP_SEE_S = 2.0  # a capped clip runs at least this far past the last visual's start
CLIP_MAX_S = 20.0  # longest clip, when the visuals of one line start far apart
CLIP_HEIGHT = 360
VERIFY_MAX_TOKENS = 1500
VERIFY_TEMPERATURE = 0.2
VERIFY_ESTIMATE = 0.003
EDIT_MAX_TOKENS = 800
EDIT_TEMPERATURE = 0.2
EDIT_ESTIMATE = 0.0005
VERDICTS = ("supported", "not_visible", "contradicted")
UNSPECIFIED = "(unspecified claim)"  # stands in for a failed claim the verifier gave no text
SAID_SHARE = 0.5  # a claim is the text's when at least this share of its words is in it
_WORD = re.compile(r"[a-z0-9]+")
_POSSESSIVE = re.compile(r"['\u2019]s\b")
_NUMBER_WORDS = {
    w: str(n)
    for n, w in enumerate(
        "zero _ two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
    if w != "_"  # "one" is as often "a" as a count
}
# words that frame a claim rather than state it ("the slide is titled ...", "there is ...")
_FRAMING = frozenset(
    """a an the and or but of to in on at by for from with into onto over under as is are was
    were be been being am there here this that these those it its they them their he she his her
    has have had do does did can could will would now then still also just some any each one
    moment visible seen see shown show shows showing displays display displayed list lists
    listed reads read reading says say said titled title labelled labeled label caption
    captioned text words word written appears appear appearing screen slide frame image picture
    video clip""".split()
)

_CLAIM_PROPS: dict = {
    "text": {"type": "string", "minLength": 1},
    "verdict": {"type": "string", "enum": list(VERDICTS)},
    "at": {"anyOf": [{"type": "number"}, {"type": "null"}]},
}
VERIFY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _CLAIM_PROPS,
                "required": list(_CLAIM_PROPS),
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}
TEXT_SCHEMA: dict = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


@cache
def load_prompt(name: str) -> str:
    """A system prompt from prompts/<name>.md."""
    return (files("lull") / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def clip_window(
    events: Iterable[VisualEvent], duration: float, *, pad: float = CLIP_PAD_S,
    cap: float = CLIP_CAP_S, least: float = CLIP_MIN_S,
) -> tuple[float, float]:  # fmt: skip
    """[first start - pad, last end + pad] clamped to the video. When that is longer than `cap`
    it starts at first start - pad (a line describes a visual from its start, not its
    middle) and runs `cap` seconds, or as far as CLIP_SEE_S past the last visual's start
    when that is later (at most CLIP_MAX_S); when it is shorter than `least` it is the
    `least` seconds centred on the visuals. It is moved inside the video if it would leave it
    (a video shorter than the window gives all it has)."""
    evs = list(events)
    if not evs:
        raise ValueError("verify: a cue needs at least one event to check against")
    first, last = min(e.t_start for e in evs), max(e.t_end for e in evs)
    lo, hi = max(0.0, first - pad), min(duration, last + pad)
    if hi - lo > cap:
        size = min(CLIP_MAX_S, max(cap, max(e.t_start for e in evs) + CLIP_SEE_S - lo))
        lo, hi = lo, lo + size
        if hi > duration:
            lo, hi = max(0.0, duration - size), duration
    elif hi - lo < least:
        mid = (first + last) / 2
        lo, hi = mid - least / 2, mid + least / 2
        if lo < 0:
            lo, hi = 0.0, min(duration, least)
        if hi > duration:
            lo, hi = max(0.0, duration - least), duration
    if hi <= lo:
        raise ValueError(f"verify: events {first}..{last} lie outside the video (0..{duration})")
    return round(lo, 3), round(hi, 3)


def tagged(tag: str, lines: Iterable[str]) -> str:
    """`lines` inside <tag>...</tag>, "(none)" when there are none: model input as data."""
    body = "\n".join(lines) or "(none)"
    return f"<{tag}>\n{body}\n</{tag}>"


def parse_claims(out: object, *, offset: float = 0.0) -> list[Claim]:
    """Claims from one verifier answer, `at` moved from clip time to video time.

    An unknown verdict counts as not visible (never as supported). A claim without text is
    dropped only when it is supported; otherwise it stays as UNSPECIFIED, so a failed check is
    never lost. A missing or non-numeric time becomes None. An answer without a `claims` list,
    or whose claims all end up dropped, raises ValueError: a broken answer never passes a cue.
    """
    raw = out.get("claims") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        raise ValueError("verify: model answer has no 'claims' list")
    claims: list[Claim] = []
    for n, item in enumerate(raw):
        item = item if isinstance(item, dict) else {}
        verdict = item.get("verdict")
        if verdict not in VERDICTS:
            log.warning("verify: claim %d has verdict %r, counted as not visible", n, verdict)
            verdict = "not_visible"
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            if verdict == "supported":
                log.warning("verify: dropped supported claim %d without text", n)
                continue
            text = UNSPECIFIED
        at = item.get("at")
        ok = isinstance(at, int | float) and not isinstance(at, bool) and math.isfinite(at)
        claims.append(
            Claim(
                text=" ".join(text.split()),
                verdict=verdict,
                at=round(max(0.0, float(at)) + offset, 3) if ok else None,
            )
        )
    if raw and not claims:
        raise ValueError(f"verify: all {len(raw)} claims were supported claims without text")
    return _distinct(claims)


_WORST = {"supported": 0, "not_visible": 1, "contradicted": 2}


def _distinct(claims: list[Claim]) -> list[Claim]:
    """Each claim once, compared without case or spacing, in first-seen order; a
    repeat with a worse verdict makes the kept claim worse, never better. Claims without text
    (UNSPECIFIED) all stay."""
    kept: dict[object, Claim] = {}
    for n, c in enumerate(claims):
        # a claim the verifier gave no text is its own claim: never folded into another
        key = n if c.text == UNSPECIFIED else " ".join(c.text.lower().split())
        first = kept.get(key)
        if first is None:
            kept[key] = c
        elif _WORST[c.verdict] > _WORST[first.verdict]:
            kept[key] = first.model_copy(update={"verdict": c.verdict})
    if len(kept) < len(claims):
        log.info("verify: %d repeated claims judged once", len(claims) - len(kept))
    return list(kept.values())


def _stem(word: str) -> str:
    """A crude English stem, enough to match "waves", "waved" and "waving"."""
    for suffix in ("ing", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


def _said_words(s: str) -> set[str]:
    plain = _POSSESSIVE.sub("", s.lower())
    return {_stem(w) for w in _WORD.findall(plain) if w not in _FRAMING}


def _numbers(s: str) -> set[str]:
    words = _WORD.findall(s.lower())
    return {_NUMBER_WORDS.get(w, w) for w in words if w in _NUMBER_WORDS or w.isdigit()}


def said_in(claim: str, text: str) -> bool:
    """Whether `claim` is something `text` says: every number it gives is in the text ("2" and
    "two" alike), and at least SAID_SHARE of its words (inflection and framing words such as
    "the slide is titled" aside) are. A claim of framing words only is the text's. The
    verifier also lists what the frames show beyond the line (the other bullets of a slide);
    those are not claims of the line."""
    if not _numbers(claim) <= _numbers(text):
        return False
    words = _said_words(claim)
    if not words:
        return True
    return len(words & _said_words(text)) >= SAID_SHARE * len(words)


def claims_of(text: str, claims: Iterable[Claim]) -> list[Claim]:
    """The claims of `text`: a supported claim stays only when the line says it (said_in), so
    the extras the verifier lists from the frames are not published as the line's. Every
    contradicted or not-visible claim stays, however it is worded: a failed check is never
    filtered away, and it decides the cue's fate."""
    kept, extra = [], []
    for c in claims:
        ok = c.verdict != "supported" or said_in(c.text, text)
        (kept if ok else extra).append(c)
    if extra:
        log.info("verify: %d supported claims the line does not make left out: %s", len(extra),
                 "; ".join(c.text for c in extra))  # fmt: skip
    return kept


FRESH_NOTE = (
    "A previous check of this description listed a claim it does not make. List only the "
    "claims this description makes, one checkable fact each, and judge them again."
)


def verify(
    text: str, clip: Path, tf: TokenFactory, *, run_id: str, names: Iterable[str] = (),
    offset: float = 0.0, note: str | None = None,
) -> list[Claim]:  # fmt: skip
    """Atomic visual claims of `text`, each judged against the frames of `clip` only.

    `names` are names the programme has established; they are context, not claims. `offset` is
    the clip's start in the video, added to every claim time. Supported claims the line does
    not make are left out; failed ones never are (claims_of). `note` is added after the data
    (FRESH_NOTE asks for a fresh extraction).
    """
    known = sorted(set(names))
    parts = [tagged("description", [text]), tagged("names", [", ".join(known)] if known else [])]
    user = "\n\n".join([*parts, note] if note else parts)
    messages = [
        {"role": "system", "content": load_prompt("verify")},
        {"role": "user", "content": [video_part(clip), {"type": "text", "text": user}]},
    ]
    out = tf.json(
        VL, messages, VERIFY_SCHEMA,
        run_id=run_id, stage=STAGE, role="verify", thinking=False,
        max_tokens=VERIFY_MAX_TOKENS, temperature=VERIFY_TEMPERATURE, estimate=VERIFY_ESTIMATE,
    )  # fmt: skip
    return claims_of(text, parse_claims(out, offset=offset))


def rewrite(prompt: str, user: str, tf: TokenFactory, *, run_id: str, role: str) -> str:
    """One Lightning rewrite (prompts/<prompt>.md) answered as {"text": ...}; whitespace
    collapsed, and "" when nothing with a letter or digit is left."""
    messages = [
        {"role": "system", "content": load_prompt(prompt)},
        {"role": "user", "content": user},
    ]
    out = tf.json(
        LIGHTNING, messages, TEXT_SCHEMA,
        run_id=run_id, stage=STAGE, role=role, thinking=False,
        max_tokens=EDIT_MAX_TOKENS, temperature=EDIT_TEMPERATURE, estimate=EDIT_ESTIMATE,
    )  # fmt: skip
    text = out.get("text") if isinstance(out, dict) else None
    if not isinstance(text, str):
        raise ValueError(f"{role}: model answer has no 'text' string")
    text = " ".join(text.split())
    return text if any(c.isalnum() for c in text) else ""


def remove_claims(
    text: str, bad: list[Claim], tf: TokenFactory, *, run_id: str, note: str | None = None
) -> str:
    """`text` rewritten without the `bad` claims, or "" when nothing else is left. `note` says
    what was wrong with a previous answer."""
    if not bad:
        return text
    listed = [f"- {c.text} ({c.verdict.replace('_', ' ')})" for c in bad]
    parts = [tagged("description", [text]), tagged("remove", listed)]
    user = "\n\n".join([*parts, note] if note else parts)
    return rewrite("remove", user, tf, run_id=run_id, role="remove")
