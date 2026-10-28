"""Verify: every claim of a description is judged against its own frames.

A blind listener takes a description as fact, so it may say only what the frames show.
Lightning first splits the description into atomic visual claims, each with the exact words
of the description it comes from (split_claims, checked by the engine: every span is in the
text and every content word is covered). The vision model then sees a short clip around the
cue's events (never the whole video) and judges only those claims, by id: supported, not
visible or contradicted (judge). It cannot add a claim, and a claim it gives no verdict is not
visible. Lightning then rewrites the words of the claims that failed (remove_claims).
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections.abc import Iterable
from functools import cache
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel

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
SPLIT_MAX_TOKENS = 1200
SPLIT_TEMPERATURE = 0.0
SPLIT_ESTIMATE = 0.0003
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


def _list_of(key: str, props: dict) -> dict:
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                    "additionalProperties": False,
                },
            }
        },
        "required": [key],
        "additionalProperties": False,
    }


SPLIT_SCHEMA: dict = _list_of("claims", {
    "id": {"type": "string", "minLength": 1},
    "text": {"type": "string", "minLength": 1},
    "span": {"type": "string", "minLength": 1},
})  # fmt: skip
VERIFY_SCHEMA: dict = _list_of("verdicts", {
    "id": {"type": "string", "minLength": 1},
    "verdict": {"type": "string", "enum": list(VERDICTS)},
    "evidence": {"type": "string"},
    "at": {"anyOf": [{"type": "number"}, {"type": "null"}]},
})  # fmt: skip
# words a claim span need not cover: function words, connectives and words that only frame
# the time of a line ("In a moment, ", "then")
_UNCOVERED_OK = frozenset(
    """a an the and or but nor so yet of to in on at by for from with without into onto over
    under up down off out about around through across along between behind beside near as is
    are was were be been being am it its they them their theirs he him his she her hers this
    that these those there here then now while when where who whom whose which what also
    still just again too very both all each every some any moment meanwhile later soon""".split()
)
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


def is_extra(claim: Claim, text: str) -> bool:
    """Whether `claim` is not something `text` says: it has no span in the text and its
    wording is not in it either (said_in). Deterministic: no model decides it."""
    return (claim.span is None or claim.span not in text) and not said_in(claim.text, text)


def claims_of(
    text: str, claims: Iterable[Claim], dropped: list[Claim] | None = None
) -> list[Claim]:
    """The claims of `text`: a supported claim stays only when the line says it (said_in), so
    the extras the verifier lists from the frames are not published as the line's. Every
    contradicted or not-visible claim stays, however it is worded: a failed check is never
    filtered away, and it decides the cue's fate."""
    kept, extra = [], []
    for c in claims:
        (extra if c.verdict == "supported" and is_extra(c, text) else kept).append(c)
    if dropped is not None:
        dropped.extend(extra)
    if extra:
        log.info("verify: %d supported claims the line does not make left out: %s", len(extra),
                 "; ".join(c.text for c in extra))  # fmt: skip
    return kept


class SplitClaim(BaseModel):
    """One atomic visual claim of a description and the exact words it comes from."""

    id: str
    text: str
    span: str


def _tokens(s: str) -> list[str]:
    return _WORD.findall(_POSSESSIVE.sub("", s.lower()))


def _must_cover(text: str, names: Iterable[str]) -> set[str]:
    """The content words of `text` some claim must cover: not function words, connectives or
    words that frame time, and not the names the programme established (context)."""
    named = {t for n in names for t in _tokens(n)}
    return {t for t in _tokens(text) if t not in _UNCOVERED_OK and t not in named}


def check_split(text: str, claims: list[SplitClaim], names: Iterable[str] = ()) -> list[str]:
    """What is wrong with a split of `text`: an id used twice, a span that is not an exact
    substring of the text, or a content word no span covers. Empty when it is sound."""
    problems = []
    ids = [c.id for c in claims]
    if len(set(ids)) < len(ids):
        problems.append("two claims share an id")
    for c in claims:
        if c.span not in text:
            problems.append(f"the span {c.span!r} of claim {c.id} is not in the description")
    covered = {t for c in claims if c.span in text for t in _tokens(c.span)}
    missing = sorted(_must_cover(text, names) - covered)
    if missing:
        problems.append("no claim covers: " + ", ".join(missing))
    return problems


def _parse_split(out: object) -> list[SplitClaim]:
    raw = out.get("claims") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        raise ValueError("split: model answer has no 'claims' list")
    claims = []
    for item in raw:
        item = item if isinstance(item, dict) else {}
        cid, text, span = (str(item.get(k) or "").strip() for k in ("id", "text", "span"))
        if cid and text and span:
            claims.append(SplitClaim(id=cid, text=" ".join(text.split()), span=span))
    return claims


_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+")
_AHEAD_LEAD = re.compile(r"^in a moment,\s*", re.IGNORECASE)


def sentence_claims(text: str) -> list[SplitClaim]:
    """The fallback split: one claim per sentence, its look-ahead prefix aside."""
    out = []
    for n, piece in enumerate((p.strip() for p in _SENTENCE_BREAK.split(text)), start=1):
        body = _AHEAD_LEAD.sub("", piece)
        if any(ch.isalnum() for ch in body):
            out.append(SplitClaim(id=f"s{n}", text=body.rstrip("."), span=body))
    return out


def split_claims(
    text: str, tf: TokenFactory, *, run_id: str, names: Iterable[str] = (),
    cache: dict[str, list[SplitClaim]] | None = None,
) -> list[SplitClaim]:  # fmt: skip
    """The atomic visual claims of `text` (Lightning, temperature 0, role "split"), checked by
    check_split; a split that fails the check is asked for once more with what was wrong,
    then one claim per sentence stands in (logged). `cache` keeps a split per text (and
    names), so a text checked again is not split again."""
    known = sorted({n for n in names if n})
    key = hashlib.sha256("\n".join([text, *known]).encode("utf-8")).hexdigest()
    if cache is not None and key in cache:
        return cache[key]
    base = [tagged("description", [text]), tagged("names", [", ".join(known)] if known else [])]
    note = None
    claims: list[SplitClaim] = []
    problems: list[str] = []
    for _attempt in range(2):
        messages = [
            {"role": "system", "content": load_prompt("split")},
            {"role": "user", "content": "\n\n".join([*base, note] if note else base)},
        ]
        out = tf.json(
            LIGHTNING, messages, SPLIT_SCHEMA,
            run_id=run_id, stage=STAGE, role="split", thinking=False,
            max_tokens=SPLIT_MAX_TOKENS, temperature=SPLIT_TEMPERATURE, estimate=SPLIT_ESTIMATE,
        )  # fmt: skip
        claims = _parse_split(out)
        problems = check_split(text, claims, known) if claims else ["no claim was returned"]
        if not problems:
            break
        note = "Your previous answer was not usable: " + "; ".join(problems) + ". Split again."
    else:
        log.warning("verify: the claim split failed twice (%s); one claim per sentence",
                    "; ".join(problems))  # fmt: skip
        claims = sentence_claims(text)
    if cache is not None:
        cache[key] = claims
    return claims


def judge(
    text: str, claims: list[SplitClaim], clip: Path, tf: TokenFactory, *, run_id: str,
    names: Iterable[str] = (), offset: float = 0.0,
) -> list[Claim]:  # fmt: skip
    """Each of `claims` judged against the frames of `clip` only (role "verify"). The vision
    model returns a verdict per claim id and adds none: an id it gives no verdict (or an
    unknown verdict) is not visible, an id it was not given is ignored, an id given twice
    keeps its worse verdict. `offset` (the clip's start in the video) is added to every
    time."""
    if not claims:
        return []
    known = sorted({n for n in names if n})
    user = "\n\n".join([
        tagged("description", [text]),
        tagged("names", [", ".join(known)] if known else []),
        tagged("claims", [f"{c.id}: {c.text}" for c in claims]),
    ])  # fmt: skip
    messages = [
        {"role": "system", "content": load_prompt("verify")},
        {"role": "user", "content": [video_part(clip), {"type": "text", "text": user}]},
    ]
    out = tf.json(
        VL, messages, VERIFY_SCHEMA,
        run_id=run_id, stage=STAGE, role="verify", thinking=False,
        max_tokens=VERIFY_MAX_TOKENS, temperature=VERIFY_TEMPERATURE, estimate=VERIFY_ESTIMATE,
    )  # fmt: skip
    raw = out.get("verdicts") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        raise ValueError("verify: model answer has no 'verdicts' list")
    given: dict[str, dict] = {}
    for item in raw:
        if not (isinstance(item, dict) and isinstance(item.get("id"), str)):
            continue
        prev = given.get(item["id"])
        if prev is None or _WORST.get(item.get("verdict"), 1) > _WORST.get(prev.get("verdict"), 1):
            given[item["id"]] = item
    result = []
    for c in claims:
        item = given.get(c.id) or {}
        verdict = item.get("verdict")
        if verdict not in VERDICTS:
            log.warning("verify: claim %s got %s, counted as not visible", c.id,
                        f"verdict {verdict!r}" if item else "no verdict")  # fmt: skip
            verdict = "not_visible"
        at = item.get("at")
        ok = isinstance(at, int | float) and not isinstance(at, bool) and math.isfinite(at)
        evidence = item.get("evidence") if isinstance(item.get("evidence"), str) else ""
        result.append(Claim(
            text=c.text, verdict=verdict,
            at=round(max(0.0, float(at)) + offset, 3) if ok and verdict != "not_visible" else None,
            span=c.span, evidence=" ".join(evidence.split()) or None,
        ))  # fmt: skip
    return _distinct(result)


def verify(
    text: str, clip: Path, tf: TokenFactory, *, run_id: str, names: Iterable[str] = (),
    offset: float = 0.0, cache: dict[str, list[SplitClaim]] | None = None,
    dropped: list[Claim] | None = None,
) -> list[Claim]:  # fmt: skip
    """The claims of `text`, each judged against the frames of `clip`: split_claims, then
    judge. `names` are names the programme has established; they are context, not claims.
    Failed claims are never filtered away; supported ones the line does not make are left out
    and added to `dropped` (claims_of)."""
    claims = split_claims(text, tf, run_id=run_id, names=names, cache=cache)
    judged = judge(text, claims, clip, tf, run_id=run_id, names=names, offset=offset)
    return claims_of(text, judged, dropped)


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
    listed = [
        f"- {c.text} ({c.verdict.replace('_', ' ')})"
        + (f' -- the words: "{c.span}"' if c.span else "")
        for c in bad
    ]
    parts = [tagged("description", [text]), tagged("remove", listed)]
    user = "\n\n".join([*parts, note] if note else parts)
    return rewrite("remove", user, tf, run_id=run_id, role="remove")
