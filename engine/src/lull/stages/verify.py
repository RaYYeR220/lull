"""Verify: the vision model re-checks every claim of a description against its own frames.

A blind listener takes a description as fact, so it may say only what the frames show. The
vision model sees a short clip around the cue's events (never the whole video), splits the
description into atomic visual claims and judges each one supported, not visible or
contradicted. Lightning then rewrites the description without the claims that failed.
"""

from __future__ import annotations

import logging
import math
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
CLIP_HEIGHT = 360
VERIFY_MAX_TOKENS = 1500
VERIFY_TEMPERATURE = 0.2
VERIFY_ESTIMATE = 0.003
EDIT_MAX_TOKENS = 800
EDIT_TEMPERATURE = 0.2
EDIT_ESTIMATE = 0.0005
VERDICTS = ("supported", "not_visible", "contradicted")
UNSPECIFIED = "(unspecified claim)"  # stands in for a failed claim the verifier gave no text

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
    cap: float = CLIP_CAP_S,
) -> tuple[float, float]:  # fmt: skip
    """[first start - pad, last end + pad] clamped to the video; when that is longer than `cap`,
    the `cap` seconds centred on the events, moved inside the video if they would leave it."""
    evs = list(events)
    if not evs:
        raise ValueError("verify: a cue needs at least one event to check against")
    first, last = min(e.t_start for e in evs), max(e.t_end for e in evs)
    lo, hi = max(0.0, first - pad), min(duration, last + pad)
    if hi - lo > cap:
        mid = (first + last) / 2
        lo, hi = mid - cap / 2, mid + cap / 2
        if lo < 0:
            lo, hi = 0.0, cap
        if hi > duration:
            lo, hi = max(0.0, duration - cap), duration
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
    return claims


def verify(
    text: str, clip: Path, tf: TokenFactory, *, run_id: str, names: Iterable[str] = (),
    offset: float = 0.0,
) -> list[Claim]:  # fmt: skip
    """Atomic visual claims of `text`, each judged against the frames of `clip` only.

    `names` are names the programme has established; they are context, not claims. `offset` is
    the clip's start in the video, added to every claim time.
    """
    known = sorted(set(names))
    user = "\n\n".join(
        [tagged("description", [text]), tagged("names", [", ".join(known)] if known else [])]
    )
    messages = [
        {"role": "system", "content": load_prompt("verify")},
        {"role": "user", "content": [video_part(clip), {"type": "text", "text": user}]},
    ]
    out = tf.json(
        VL, messages, VERIFY_SCHEMA,
        run_id=run_id, stage=STAGE, role="verify", thinking=False,
        max_tokens=VERIFY_MAX_TOKENS, temperature=VERIFY_TEMPERATURE, estimate=VERIFY_ESTIMATE,
    )  # fmt: skip
    return parse_claims(out, offset=offset)


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
