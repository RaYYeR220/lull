"""Schedule stage: place the plan's description variants into dialogue gaps.

Language models write good descriptions but place them in time unreliably, so the plan stage
only writes (up to three variants per event) and this stage places, with a CP-SAT model: for
each visual event at most one variant in at most one gap, no gap holding more words than it
has room for, nothing described after its visual has ended (R2), maximising priority-weighted
information. A gap that ends before the event starts (within `lead` seconds) may carry it as a
look-ahead, voiced with "In a moment, " which costs `ahead_words` words of the gap.

R2 holds per sentence, not just per gap: a gap voices its sentences in visual start order (as
compose_cues writes them), so a sentence starts after the words before it, at `wps` words per
second; it must start before its visual ends. Long gaps should first be cut into windows with
`lull.timeline.split_gaps` so one cue cannot describe a visual long before it appears.

Same input, same answer: inputs are sorted by id, equal-value answers are broken towards the
gap closest to each visual, and the solver runs one worker with a fixed seed (parallel workers
return different co-optimal answers from run to run). An instance solved to optimality is
reproducible; one stopped by the time limit may depend on machine speed. A greedy pass stands
in when ortools is missing or the solver returns no solution in time, and wins over a
timed-out solver answer that is worse.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from lull.stages.plan import said_already
from lull.types import Cue, Gap, Variant, VisualEvent

try:
    from ortools.sat.python import cp_model
except ImportError:  # pragma: no cover - ortools is a dependency; greedy keeps the stage alive
    cp_model = None

log = logging.getLogger(__name__)

Length = Literal["short", "medium", "long"]
LENGTHS: tuple[Length, ...] = ("short", "medium", "long")
WEIGHTS: dict[str, int] = {"short": 60, "medium": 85, "long": 100}  # information per variant
AHEAD_PENALTY = 1  # objective cost of a look-ahead placement
AHEAD_PREFIX = "In a moment, "
AHEAD_WORDS = 3  # words in AHEAD_PREFIX
LEAD = 6.0  # seconds a look-ahead may come before its visual starts
SALIENT = 4  # unplaced events at or above this priority become extended candidates
MIN_PRIORITY = 2  # a standard placement needs at least this priority (ruling, Task 16)
WPS = 2.36  # voice rate used to time each sentence inside its gap
TIME_LIMIT_S = 5.0
NUM_WORKERS = 1  # parallel workers race to different co-optimal answers: not reproducible
TIE_BREAK = True  # prefer the closest gap among equal-value answers

_EPS = 1e-9
_TERMINAL = (".", "!", "?", "…")
_CLOSERS = "\"'”’)]»"
_FIRST_PERSON = {"I", "I'm", "I'll", "I've", "I'd"}


class Placement(BaseModel):
    event_id: str
    variant: Length
    gap_id: str
    ahead: bool


@dataclass(frozen=True)
class _Option:
    """One way to place an event: one of its variants in one eligible gap."""

    event: VisualEvent
    length: Length
    gap: Gap
    ahead: bool
    priority: int
    cost: int  # words the gap must hold, look-ahead prefix included
    value: int  # the brief's objective term: priority x weight, minus the look-ahead penalty
    rank: int  # 0 = the event's best gap (open during it, then closest); breaks ties only
    limit: int  # most words that may come before this sentence in its gap (R2 per sentence)


# -- options -----------------------------------------------------------------------------


def _variant_table(variants: Iterable[Variant]) -> dict[tuple[str, str], Variant]:
    table: dict[tuple[str, str], Variant] = {}
    for v in variants:
        table.setdefault((v.event_id, v.length), v)  # first one wins, as in the plan stage
    return table


def _eligible(e: VisualEvent, g: Gap, lead: float) -> bool:
    """R2: the gap starts before the visual ends and ends at most `lead` s before it starts."""
    return g.usable_start < e.t_end and g.usable_end > e.t_start - lead


def _is_ahead(e: VisualEvent, g: Gap) -> bool:
    return g.usable_end <= e.t_start


def _distance(e: VisualEvent, g: Gap) -> float:
    """Seconds between the visual's start and the gap (0 when the gap is open at that moment)."""
    if _is_ahead(e, g):
        return e.t_start - g.usable_end
    return max(0.0, g.usable_start - e.t_start)


def _order(e: VisualEvent) -> tuple[float, str]:
    """Order of sentences inside a gap: visual start, then id (as compose_cues voices them)."""
    return (e.t_start, e.id)


def _limit(e: VisualEvent, g: Gap, wps: float) -> int:
    """Most words before e's sentence in g that still let it start before e ends: preceding
    words / wps < t_end - usable_start, kept one word on the safe side."""
    return math.floor((e.t_end - g.usable_start) * wps + _EPS) - 1


def _in_time(group: Iterable[_Option]) -> bool:
    """True when every sentence of one gap starts before its visual ends."""
    before = 0
    for o in sorted(group, key=lambda o: _order(o.event)):
        if before > o.limit:
            return False
        before += o.cost
    return True


def _options(
    events: Sequence[VisualEvent], variants: Sequence[Variant], priority: dict[str, int],
    gaps: Sequence[Gap], lead: float, ahead_words: int, wps: float,
) -> list[_Option]:  # fmt: skip
    """Every (event, variant, gap) triple that is eligible, fits the gap on its own and would
    start in time if voiced first in it. Events under MIN_PRIORITY get none: a priority-1 line
    never fills spare room."""
    table = _variant_table(variants)
    gaps = sorted(gaps, key=lambda g: g.id)
    out: list[_Option] = []
    for e in sorted(events, key=lambda e: e.id):
        mine = [table[(e.id, n)] for n in LENGTHS if (e.id, n) in table]
        if not mine:
            continue
        p = max(1, int(priority.get(e.id, e.salience)))  # plan sets one per event it answers
        if p < MIN_PRIORITY:
            continue
        eligible = [g for g in gaps if _eligible(e, g, lead)]
        # closest first, gaps open during the visual before look-aheads (as the objective has it)
        near = sorted(eligible, key=lambda g: (_is_ahead(e, g), _distance(e, g), g.id))
        for rank, g in enumerate(near):
            ahead, limit = _is_ahead(e, g), _limit(e, g, wps)
            if limit < 0:
                continue
            for v in mine:
                cost = v.words + ahead_words * ahead
                if cost > g.capacity_words:
                    continue
                value = p * WEIGHTS[v.length] - AHEAD_PENALTY * ahead
                out.append(_Option(e, v.length, g, ahead, p, cost, value, rank, limit))
    return out


# -- placing -----------------------------------------------------------------------------


def _value(options: list[_Option], chosen: Iterable[int]) -> int:
    """The brief's objective for a choice of options."""
    return sum(options[i].value for i in chosen)


def _greedy(options: list[_Option]) -> list[int]:
    """Events by priority (desc) then start; each takes its longest variant that still fits,
    in the eligible gap closest to its visual that has room for it and keeps every sentence
    of that gap in time."""
    mine: dict[str, list[int]] = defaultdict(list)
    for i, o in enumerate(options):
        mine[o.event.id].append(i)
    left = {o.gap.id: o.gap.capacity_words for o in options}
    held: dict[str, list[_Option]] = defaultdict(list)  # gap id -> options placed there
    first = {eid: options[ix[0]] for eid, ix in mine.items()}
    order = sorted(mine, key=lambda eid: (-first[eid].priority, first[eid].event.t_start, eid))
    chosen: list[int] = []
    for eid in order:
        for length in reversed(LENGTHS):
            fits = (
                i
                for i in sorted(mine[eid], key=lambda i: options[i].rank)
                if options[i].length == length
                and options[i].cost <= left[options[i].gap.id]
                and _in_time([*held[options[i].gap.id], options[i]])
            )
            i = next(fits, None)
            if i is not None:
                left[options[i].gap.id] -= options[i].cost
                held[options[i].gap.id].append(options[i])
                chosen.append(i)
                break
    return chosen


def _solve(options: list[_Option], hint: list[int], time_limit_s: float) -> list[int] | None:
    """The optimal choice of options, or None when there is no solver or it found nothing."""
    if cp_model is None:
        log.warning("schedule: ortools is not available, placing greedily")
        return None
    model = cp_model.CpModel()
    x = [model.new_bool_var(f"x{i}") for i in range(len(options))]
    per_event: dict[str, list[int]] = defaultdict(list)
    per_gap: dict[str, list[int]] = defaultdict(list)
    for i, o in enumerate(options):
        per_event[o.event.id].append(i)
        per_gap[o.gap.id].append(i)
    for ix in per_event.values():
        model.add_at_most_one(x[i] for i in ix)
    for ix in per_gap.values():
        cap = options[ix[0]].gap.capacity_words
        model.add(_words(x, options, ix) <= cap)
        # R2 per sentence: when option o is chosen, the words voiced before it in this gap
        # (options of visuals that start earlier) must leave it starting before its visual ends
        for o_i in ix:
            o = options[o_i]
            before = [i for i in ix if _order(options[i].event) < _order(o.event)]
            most: dict[str, int] = defaultdict(int)  # largest cost per earlier event
            for i in before:
                most[options[i].event.id] = max(most[options[i].event.id], options[i].cost)
            if min(cap, sum(most.values())) > o.limit:  # otherwise it can never bind
                model.add(_words(x, options, before) <= o.limit).only_enforce_if(x[o_i])
    weights = [o.value for o in options]
    if TIE_BREAK:
        # Lexicographic: one unit of the brief's objective outweighs every tie-break unit
        # together (at most one option per event is chosen), so its optimum is kept.
        scale = 1 + sum(max(options[i].rank for i in ix) for ix in per_event.values())
        weights = [o.value * scale - o.rank for o in options]
    model.maximize(cp_model.LinearExpr.weighted_sum(x, weights))
    chosen = set(hint)
    for i, var in enumerate(x):
        model.add_hint(var, i in chosen)  # start from the greedy answer
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    # Reproducible stop when it binds first; measured at about 0.07 deterministic units per
    # wall second, so the wall limit above is usually what stops a long solve.
    solver.parameters.max_deterministic_time = time_limit_s
    solver.parameters.num_workers = NUM_WORKERS
    solver.parameters.random_seed = 0
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        log.warning("schedule: solver status %s, placing greedily", status)
        return None
    answer = [i for i, var in enumerate(x) if solver.boolean_value(var)]
    if status == cp_model.FEASIBLE and _value(options, answer) < _value(options, hint):
        log.warning("schedule: solver timed out below the greedy answer, placing greedily")
        return list(hint)
    return answer


def _words(x: list, options: list[_Option], ix: list[int]):
    """The words the chosen options among `ix` put in their gap, as a linear expression."""
    return cp_model.LinearExpr.weighted_sum([x[i] for i in ix], [options[i].cost for i in ix])


def extended_candidates(
    events: Iterable[VisualEvent], priority: dict[str, int], placed: set[str]
) -> list[str]:
    """Unplaced events the plan rates priority >= 4, by start: the ones worth stopping the
    video for. An event the plan gave no priority counts with its salience (it has no variant
    then, and the caller reports it undescribed), except one the dialogue already conveys
    (R21), which was never planned."""
    ordered = sorted(events, key=lambda e: (e.t_start, e.id))
    return [
        e.id
        for e in ordered
        if e.id not in placed and priority.get(e.id, e.salience) >= SALIENT and not said_already(e)
    ]


def _result(
    options: list[_Option], chosen: list[int], events: Sequence[VisualEvent],
    priority: dict[str, int],
) -> tuple[list[Placement], list[str]]:  # fmt: skip
    picked = sorted(
        (options[i] for i in chosen),
        key=lambda o: (o.gap.usable_start, o.gap.id, o.event.t_start, o.event.id),
    )
    placements = [
        Placement(event_id=o.event.id, variant=o.length, gap_id=o.gap.id, ahead=o.ahead)
        for o in picked
    ]
    return placements, extended_candidates(events, priority, {p.event_id for p in placements})


def schedule(
    events: list[VisualEvent],
    variants: list[Variant],
    priority: dict[str, int],
    gaps: list[Gap],
    *,
    lead: float = LEAD,
    ahead_words: int = AHEAD_WORDS,
    time_limit_s: float = TIME_LIMIT_S,
    wps: float = WPS,
) -> tuple[list[Placement], list[str]]:
    """Placements (in gap order) and the ids of the events left unplaced that the plan rates
    priority >= 4 (by start; see extended_candidates), which are the extended-mode candidates.

    An event may sit in gap g when g.usable_start < t_end and g.usable_end > t_start - lead; it
    is a look-ahead when g.usable_end <= t_start and then costs `ahead_words` more words. Inside
    a gap the words voiced before an event's sentence (earlier-starting visuals, ties by id) may
    be at most floor((t_end - usable_start) x wps) - 1. The model maximises
    sum(priority x weight) - 1 per look-ahead, weights short 60, medium 85, long 100. An event
    missing from `priority` counts with its salience; one under MIN_PRIORITY (2) is never
    placed.
    """
    options = _options(events, variants, priority, gaps, lead, ahead_words, wps)
    greedy = _greedy(options)
    chosen = _solve(options, greedy, time_limit_s) if options else []
    return _result(options, greedy if chosen is None else chosen, events, priority)


def schedule_greedy(
    events: list[VisualEvent],
    variants: list[Variant],
    priority: dict[str, int],
    gaps: list[Gap],
    *,
    lead: float = LEAD,
    ahead_words: int = AHEAD_WORDS,
    wps: float = WPS,
) -> tuple[list[Placement], list[str]]:
    """The fallback `schedule` uses without a solver answer: same rules, greedy choice."""
    options = _options(events, variants, priority, gaps, lead, ahead_words, wps)
    return _result(options, _greedy(options), events, priority)


# -- cue text ----------------------------------------------------------------------------


def _sentence(text: str) -> str:
    """Whitespace collapsed; ends in terminal punctuation (a closing quote may follow it)."""
    text = " ".join(text.split())
    if text and text[-1] in ",;:":
        text = text[:-1].rstrip()
    if not text or text.rstrip(_CLOSERS).endswith(_TERMINAL):
        return text
    return text + "."


def _lower_first(text: str, proper_nouns: set[str]) -> str:
    """First letter lower-cased to follow AHEAD_PREFIX, unless the first word is "I" or one of
    its contractions, an all-caps word of two or more letters ("NASA's", "U.S.", "TV") or a
    known proper noun. Text that starts with a quote is left alone."""
    if not text or not text[0].isupper() or (len(text) > 1 and text[1].isupper()):
        return text
    first = text.split(maxsplit=1)[0].replace("’", "'").rstrip(",;:!?")
    letters = re.sub(r"'s$", "", first).replace(".", "").replace("'", "")
    word = re.match(r"\w+", first)
    names = {n.replace("’", "'") for n in proper_nouns}  # either apostrophe matches
    if (
        first in _FIRST_PERSON
        or (len(letters) >= 2 and letters.isalpha() and letters.isupper())
        or first.rstrip(".") in names
        or re.sub(r"'s$", "", first.rstrip(".")) in names
        or (word and word.group() in names)
    ):
        return text
    return text[0].lower() + text[1:]


def _digits(id_: str) -> str:
    return re.sub(r"\D", "", id_) or id_


def _resolve(
    placements: Sequence[Placement], variants: Sequence[Variant],
    events: Sequence[VisualEvent], gaps: Sequence[Gap],
) -> list[tuple[Gap, VisualEvent, Placement, Variant]]:  # fmt: skip
    """Each placement with its gap, event and variant, in gap then event-start order."""
    ev = {e.id: e for e in events}
    gp = {g.id: g for g in gaps}
    table = _variant_table(variants)
    seen: set[str] = set()
    rows: list[tuple[Gap, VisualEvent, Placement, Variant]] = []
    for p in placements:
        if p.event_id in seen:
            raise ValueError(f"schedule: event {p.event_id} placed twice")
        seen.add(p.event_id)
        e, v = ev.get(p.event_id), table.get((p.event_id, p.variant))
        if e is None or v is None:
            raise ValueError(f"schedule: no {p.variant} variant for event {p.event_id}")
        g = gp.get(p.gap_id)
        if g is None:
            raise ValueError(f"schedule: unknown gap {p.gap_id} for event {p.event_id}")
        rows.append((g, e, p, v))
    rows.sort(key=lambda r: (r[0].usable_start, r[0].id, r[1].t_start, r[1].id))
    return rows


def compose_cues(
    placements: list[Placement],
    variants: list[Variant],
    events: list[VisualEvent],
    gaps: list[Gap],
    *,
    extended: list[str] | None = None,
    proper_nouns: set[str] | None = None,
    priority: dict[str, int] | None = None,
) -> list[Cue]:
    """One standard cue per gap that holds placements (gap order), then one extended cue per
    extended candidate that has a variant (start order).

    A standard cue `c<gap number>` starts at the gap's usable start and voices its variants in
    visual start order, look-aheads prefixed "In a moment, "; status "verified" is a placeholder
    until finalisation. An extended cue `x<event number>` starts at its visual and voices the
    longest variant. `extended` defaults to extended_candidates over the plan's `priority`
    (the events left unplaced that the plan rates 4 or more); first words in `proper_nouns`
    keep their capital after the look-ahead prefix.
    """
    names = proper_nouns or set()
    groups: dict[str, list[tuple[VisualEvent, str, bool]]] = defaultdict(list)
    gap_of: dict[str, Gap] = {}
    for g, e, p, v in _resolve(placements, variants, events, gaps):
        text = _sentence(v.text)
        if p.ahead:
            text = AHEAD_PREFIX + _lower_first(text, names)
        groups[g.id].append((e, text, p.ahead))
        gap_of[g.id] = g
    cues = [
        Cue(
            id="c" + _digits(gid),
            gap_id=gid,
            start=gap_of[gid].usable_start,
            end=None,
            text=" ".join(text for _, text, _ in items),
            event_ids=[e.id for e, _, _ in items],
            status="verified",
            ahead=any(ahead for _, _, ahead in items),
            claims=[],
            audio_path=None,
            audio_dur=None,
        )
        for gid, items in groups.items()  # insertion order is gap order
    ]
    ev = {e.id: e for e in events}
    if extended is None:
        extended = extended_candidates(events, priority or {}, {p.event_id for p in placements})
    unknown = [eid for eid in extended if eid not in ev]
    if unknown:
        raise ValueError(f"schedule: unknown extended event(s) {', '.join(unknown)}")
    table = _variant_table(variants)
    for e in sorted((ev[eid] for eid in extended), key=lambda e: (e.t_start, e.id)):
        v = next((table[(e.id, n)] for n in reversed(LENGTHS) if (e.id, n) in table), None)
        if v is None:
            log.info("schedule: extended candidate %s has no variant to voice", e.id)
            continue
        cues.append(
            Cue(
                id="x" + _digits(e.id),
                gap_id=None,
                start=e.t_start,
                end=None,
                text=_sentence(v.text),
                event_ids=[e.id],
                status="extended",
                ahead=False,
                claims=[],
                audio_path=None,
                audio_dur=None,
            )
        )
    return cues


# -- report ------------------------------------------------------------------------------


def placement_report(
    placements: list[Placement],
    variants: list[Variant],
    events: list[VisualEvent],
    gaps: list[Gap],
    *,
    ahead_words: int = AHEAD_WORDS,
    priority: dict[str, int] | None = None,
) -> dict:
    """What the schedule did with the gaps: placed events (gap order, spoken words each),
    unplaced salient events (extended_candidates over the plan's `priority`), words used
    against capacity per gap and overall utilisation."""
    rows = _resolve(placements, variants, events, gaps)
    used: dict[str, int] = defaultdict(int)
    members: dict[str, list[str]] = defaultdict(list)
    placed: list[dict] = []
    for g, e, p, v in rows:
        words = v.words + ahead_words * p.ahead
        used[g.id] += words
        members[g.id].append(e.id)
        placed.append({"event_id": e.id, "variant": p.variant, "gap_id": g.id,
                       "ahead": p.ahead, "words": words})  # fmt: skip
    per_gap = [
        {"gap_id": g.id, "capacity_words": g.capacity_words, "words_used": used[g.id],
         "event_ids": members[g.id]}
        for g in sorted(gaps, key=lambda g: (g.usable_start, g.id))
    ]  # fmt: skip
    words_used = sum(used.values())
    capacity = sum(max(0, g.capacity_words) for g in gaps)
    return {
        "placed": placed,
        "unplaced_salient": extended_candidates(
            events, priority or {}, {p.event_id for p in placements}
        ),
        "gaps": per_gap,
        "words_used": words_used,
        "capacity_words": capacity,
        "utilisation": round(words_used / capacity, 3) if capacity else 0.0,
    }
