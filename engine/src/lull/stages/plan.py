"""Plan stage: Ultra reads the whole video as text and writes prioritised description variants.

One reasoning call sees the full transcript, the visual-event inventory, the gap table and the
glossary, and returns, per event worth describing, a priority and up to three variants (short,
medium, long) plus a cast registry (name -> descriptor). It never places anything in time: the
scheduler does that. Word counts are always computed here, never taken from the model.
Near-duplicate items are dropped afterwards (`drop_near_duplicates`).
"""

from __future__ import annotations

import json
import logging
import re
from functools import cache
from importlib.resources import files

from pydantic import BaseModel, Field

from lull.stages.research import LOOKUP_FAILED, ResearchResult
from lull.stages.watch import NO_SPEECH, transcript_slice
from lull.tf import ULTRA, TokenFactory
from lull.types import Gap, RunOptions, Span, Variant, VisualEvent, Word

log = logging.getLogger(__name__)

MAX_WORDS = 22  # hard ceiling for any variant; the prompt only gives soft targets
MIN_TOKENS, MAX_TOKENS = 12000, 32000
TOKENS_BASE, TOKENS_PER_EVENT = 4000, 150  # reasoning + three variants per event
ESTIMATE_BASE, ESTIMATE_PER_EVENT, ESTIMATE_CAP = 0.02, 0.0008, 0.25
TEMPERATURE = 0.6
LENGTHS = ("short", "medium", "long")
COVER_SALIENCE = 3  # a sent event this salient with no item is asked for once more
ASK_AGAIN = "Write an item for every event listed."
DUP_JACCARD = 0.6  # short lines this alike ...
DUP_WINDOW_S = 30.0  # ... this close in time say the same thing: one is kept
_EPS = 1e-9
_STOP = frozenset(
    "a an the and or of to in on at by for from with into onto is are its their".split()
)
_WORD = re.compile(r"\w+")


def budget(n_sent: int) -> tuple[int, float]:
    """(max_tokens, spend estimate in USD) for one plan call over `n_sent` events."""
    tokens = min(MAX_TOKENS, max(MIN_TOKENS, TOKENS_BASE + TOKENS_PER_EVENT * n_sent))
    return tokens, min(ESTIMATE_CAP, ESTIMATE_BASE + ESTIMATE_PER_EVENT * n_sent)


_NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_ITEM_PROPS: dict = {
    "event_id": {"type": "string"},
    "priority": {"type": "integer", "minimum": 1, "maximum": 5},
    "short": {"type": "string"},
    "medium": _NULLABLE_STR,
    "long": _NULLABLE_STR,
}
_CAST_PROPS: dict = {"descriptor": {"type": "string"}, "name": {"type": "string"}}
PLAN_SCHEMA: dict = {  # cast is a list of pairs: strict mode needs fixed object keys
    "type": "object",
    "properties": {
        "cast": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _CAST_PROPS,
                "required": list(_CAST_PROPS),
                "additionalProperties": False,
            },
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _ITEM_PROPS,
                "required": list(_ITEM_PROPS),
                "additionalProperties": False,
            },
        },
    },
    "required": ["cast", "items"],
    "additionalProperties": False,
}


class PlanResult(BaseModel):
    variants: list[Variant] = Field(default_factory=list)
    cast: dict[str, str] = Field(default_factory=dict)  # name -> descriptor used before it
    priority: dict[str, int] = Field(default_factory=dict)  # event_id -> 1..5
    unanswered: list[str] = Field(default_factory=list)  # salient sent events still without one
    duplicates: dict[str, str] = Field(default_factory=dict)  # dropped event -> the one kept


@cache
def load_prompt() -> str:
    """System prompt (prompts/plan.md). It encodes R7 tense/person, R8 sentences, R9 articles
    and pronouns, R10 verbs, R11 transitions, R12 expressions not emotions, R13 no softening,
    R14 no assumed identity, R15 naming and cast, R16 priorities, R17 general terms, R18 no
    jargon, R19 on-screen text verbatim, R21 no repeating dialogue, R22 charts."""
    return (files("lull") / "prompts" / "plan.md").read_text(encoding="utf-8")


def said_already(e: VisualEvent) -> bool:
    """R21: the dialogue already conveys the event (said in dialogue, salience under 5), so it
    is not sent to the plan and is never counted as undescribed."""
    return e.said_in_dialogue and e.salience < 5


def count_words(text: str) -> int:
    """Spoken words: whitespace tokens that hold a letter or digit ("2.1" is one word)."""
    return sum(1 for tok in text.split() if any(c.isalnum() for c in tok))


def _one_line(s: str) -> str:
    return " ".join(s.split())


# -- request -----------------------------------------------------------------------------


def _transcript(words: list[Word]) -> str:
    end = max((w.end for w in words), default=0.0)
    text = transcript_slice(words, Span(start=0.0, end=end)) if words else NO_SPEECH
    return "(no speech in this video)" if text == NO_SPEECH else text


def _event_line(e: VisualEvent) -> str:
    line = f"{e.id} [{e.t_start:.1f}–{e.t_end:.1f}] {e.kind}, salience {e.salience}: "
    line += _one_line(e.text)
    if e.verbatim_text:
        line += f" | on-screen text: {json.dumps(e.verbatim_text, ensure_ascii=False)}"
    if e.said_in_dialogue:
        line += " | said in dialogue"
    return line


def _gap_line(g: Gap) -> str:
    usable = g.usable_end - g.usable_start
    return (
        f"{g.id} [{g.usable_start:.1f}–{g.usable_end:.1f}] {usable:.1f} s usable, "
        f"room for about {g.capacity_words} words"
    )


def _glossary(research: ResearchResult) -> str:
    seen: set[str] = set()
    lines: list[str] = []
    for entry in research.glossary:
        name = _one_line(entry.canonical or entry.term)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        note = _one_line(entry.note)
        lines.append(f"{name}: {note}" if note and note != LOOKUP_FAILED else name)
    return "\n".join(lines) if lines else "(none)"


def _user_text(
    events: list[VisualEvent], words: list[Word], gaps: list[Gap],
    research: ResearchResult, opts: RunOptions,
) -> str:  # fmt: skip
    nl = "\n"
    gap_lines = nl.join(_gap_line(g) for g in gaps) or "(none: speech leaves no usable pause)"
    return (nl + nl).join(
        [
            f"Profile: {opts.profile} · Mode: {opts.mode} · Language: {opts.language}",
            f"<transcript>{nl}{_transcript(words)}{nl}</transcript>",
            f"<visual_events>{nl}{nl.join(_event_line(e) for e in events)}{nl}</visual_events>",
            f"<gaps>{nl}{gap_lines}{nl}</gaps>",
            f"<glossary>{nl}{_glossary(research)}{nl}</glossary>",
        ]
    )


# -- answer ------------------------------------------------------------------------------


def _priority(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise TypeError(f"priority is {type(raw).__name__}, not a number")
    return min(5, max(1, round(raw)))


def _variant(event_id: str, length: str, raw: object) -> Variant | None:
    if not isinstance(raw, str):
        return None
    text = _one_line(raw)
    n = count_words(text)
    if n == 0:
        return None
    if n > MAX_WORDS:
        log.info("plan: dropped %s %s variant of %d words", event_id, length, n)
        return None
    return Variant(event_id=event_id, length=length, text=text, words=n)


def _cast(raw: object) -> dict[str, str]:
    cast: dict[str, str] = {}
    for pair in raw if isinstance(raw, list) else []:
        if not isinstance(pair, dict):
            continue
        desc, name = pair.get("descriptor"), pair.get("name")
        if not (isinstance(desc, str) and isinstance(name, str)):
            continue
        desc, name = _one_line(desc), _one_line(name)
        if not (desc and name):
            continue
        if name not in cast:
            cast[name] = desc
        elif cast[name] != desc:
            log.warning("plan: cast %r: kept descriptor %r, not %r", name, cast[name], desc)
    return cast


def parse_plan(out: dict, events: list[VisualEvent]) -> PlanResult:
    """The model answer for `events` (the events that were sent) as a PlanResult.

    Items naming an event that was not sent are dropped; the first item per event wins;
    priority is clamped to 1..5; words are counted here and variants that are empty or longer
    than MAX_WORDS are dropped; an event left without a variant gets no priority. An answer
    without an `items` list, or whose items all name unknown events, raises ValueError: an
    empty plan must never stand in for a broken one.
    """
    raw = out.get("items") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        raise ValueError("plan: model answer has no 'items' list")
    order = {e.id: i for i, e in enumerate(events)}
    known = 0
    seen: set[str] = set()
    picked: list[tuple[int, int, Variant]] = []
    priority: dict[str, int] = {}
    for n, item in enumerate(raw):
        eid = item.get("event_id") if isinstance(item, dict) else None
        if not isinstance(eid, str) or eid not in order:
            log.warning("plan: dropped item %d for unknown event %r", n, eid)
            continue
        known += 1
        if eid in seen:
            log.warning("plan: dropped duplicate item %d for %s", n, eid)
            continue
        seen.add(eid)
        try:
            p = _priority(item.get("priority"))
        except (TypeError, ValueError) as e:
            log.warning("plan: dropped item %d for %s: %s", n, eid, e)
            continue
        vs = [
            (k, v)
            for k, length in enumerate(LENGTHS)
            if (v := _variant(eid, length, item.get(length))) is not None
        ]
        if not vs:
            continue
        priority[eid] = p
        picked += [(order[eid], k, v) for k, v in vs]
    if raw and not known:
        ids = sorted({str(i.get("event_id")) for i in raw if isinstance(i, dict)})
        raise ValueError(f"plan: no item names a known event id (got {', '.join(ids)[:200]})")
    log.info(
        "plan: %d events sent, %d got an item, %d kept a variant",
        len(events), len(seen), len(priority),
    )  # fmt: skip
    picked.sort(key=lambda t: (t[0], t[1]))
    return PlanResult(
        variants=[v for _, _, v in picked], cast=_cast(out.get("cast")), priority=priority
    )


# -- stage -------------------------------------------------------------------------------


def _ask(
    events: list[VisualEvent], words: list[Word], gaps: list[Gap], research: ResearchResult,
    opts: RunOptions, tf: TokenFactory, run_id: str, note: str | None = None,
) -> PlanResult:  # fmt: skip
    """One Ultra call about `events`, parsed."""
    user = _user_text(events, words, gaps, research, opts)
    if note:
        user = f"{user}\n\n{note}"
    messages = [
        {"role": "system", "content": load_prompt()},
        {"role": "user", "content": user},
    ]
    max_tokens, estimate = budget(len(events))
    out = tf.json(
        ULTRA, messages, PLAN_SCHEMA,
        run_id=run_id, stage="plan", role="plan", thinking=True,
        max_tokens=max_tokens, temperature=TEMPERATURE, estimate=estimate,
    )  # fmt: skip
    return parse_plan(out, events)


def _merge(first: PlanResult, second: PlanResult, events: list[VisualEvent]) -> PlanResult:
    """Both answers as one: variants in event order, the first answer's cast names kept."""
    order = {e.id: i for i, e in enumerate(events)}
    variants = sorted(
        first.variants + second.variants,
        key=lambda v: (order[v.event_id], LENGTHS.index(v.length)),
    )
    cast = dict(first.cast)
    for name, desc in second.cast.items():
        if name not in cast:
            cast[name] = desc
        elif cast[name] != desc:
            log.warning("plan: cast %r: kept descriptor %r, not %r", name, cast[name], desc)
    return PlanResult(variants=variants, cast=cast, priority={**second.priority, **first.priority})


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD.findall(text.lower()) if t not in _STOP}


def drop_near_duplicates(result: PlanResult, events: list[VisualEvent]) -> PlanResult:
    """The plan without items that repeat another (ruling, Task 16).

    Two items are near-duplicates when their short lines (else their first variants) have a
    token Jaccard of at least DUP_JACCARD and their visuals start at most DUP_WINDOW_S apart.
    Items are kept highest priority first (the earlier visual on a tie); an item alike to one
    already kept is dropped and recorded in `duplicates` as dropped -> kept, so every twin is
    within DUP_WINDOW_S of what it stands for (no chains) and coverage can count it with it.
    A dropped item loses its variants and priority.
    """
    by_id = {e.id: e for e in events}
    words: dict[str, set[str]] = {}
    for v in result.variants:  # the short line, else the first variant the event has
        if v.event_id not in words or v.length == "short":
            words[v.event_id] = _tokens(v.text)
    order = sorted(
        (by_id[eid] for eid in result.priority if eid in by_id and eid in words),
        key=lambda e: (-result.priority[e.id], e.t_start, e.id),
    )
    kept: list[VisualEvent] = []
    dup: dict[str, str] = dict(result.duplicates)
    for e in order:
        alike = [
            k
            for k in kept
            if abs(e.t_start - k.t_start) <= DUP_WINDOW_S + _EPS
            and _jaccard(words[e.id], words[k.id]) >= DUP_JACCARD - _EPS
        ]
        if alike:  # the closest kept twin
            dup[e.id] = min(alike, key=lambda k: (abs(e.t_start - k.t_start), k.t_start)).id
        else:
            kept.append(e)
    if not dup:
        return result
    log.info("plan: dropped near-duplicate items %s", ", ".join(f"{d}~{k}" for d, k in dup.items()))
    return result.model_copy(
        update={
            "variants": [v for v in result.variants if v.event_id not in dup],
            "priority": {eid: p for eid, p in result.priority.items() if eid not in dup},
            "duplicates": dup,
        }
    )


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def plan(
    events: list[VisualEvent],
    words: list[Word],
    gaps: list[Gap],
    research: ResearchResult,
    opts: RunOptions,
    tf: TokenFactory,
    *,
    run_id: str,
) -> PlanResult:
    """Prioritised description variants for the events worth describing, from one Ultra call.

    Events the dialogue already conveys are left out before the call unless their salience is
    5 (R21); with nothing left there is no call and the plan is empty. The token budget and
    the spend estimate grow with the number of events sent (see `budget`). A sent event of
    salience 3 or more that got no item is asked for once more, in a second call about only
    those events; the ones still without an item are listed in `unanswered`. Near-duplicate
    items are dropped last (`drop_near_duplicates`).
    """
    send = [e for e in events if not said_already(e)]
    if not send:
        return PlanResult()
    result = _ask(send, words, gaps, research, opts, tf, run_id)
    missing = [e for e in send if e.salience >= COVER_SALIENCE and e.id not in result.priority]
    if missing:
        log.info("plan: asking again for %s", ", ".join(e.id for e in missing))
        try:
            again = _ask(missing, words, gaps, research, opts, tf, run_id, note=ASK_AGAIN)
        except ValueError as e:
            log.warning("plan: the second answer was unusable, keeping the first: %s", e)
            again = PlanResult()
        result = _merge(result, again, send)
    unanswered = [e.id for e in missing if e.id not in result.priority]
    if unanswered:
        log.warning("plan: no item for %s even when asked again", ", ".join(unanswered))
    return drop_near_duplicates(result.model_copy(update={"unanswered": unanswered}), send)
