"""Watch stage: the vision model inventories visual events window by window, merged to one list.

Each window is cut from the video, sent with its speaker-tagged transcript slice, and answered
in window-relative seconds; `merge_events` moves everything to absolute time and folds the
duplicates that overlapping windows produce.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import get_args

from lull.media import cut_clip, make_proxy, probe, scene_cuts
from lull.tf import MAX_VIDEO_BYTES, VL, TokenFactory, video_part
from lull.types import EventKind, Span, VisualEvent, Word

log = logging.getLogger(__name__)

SNAP_S = 15.0  # a window end moves to the nearest scene cut within this distance
MAX_CLIP_S = 118.0  # hard window cap: the model takes clips up to 120 s; margin for encoding
CLIP_HEIGHT = 480
CLIP_MAX_BYTES = MAX_VIDEO_BYTES
PAUSE_S = 1.0  # a transcript line ends at a pause longer than this
MERGE_DT = 2.0
MERGE_JACCARD = 0.5
FOLD_GAP_S = 1.5  # within one window, same-kind look-alikes this close are one event
CUT_SLACK_S = 0.5  # a scene cut this soon after the later event starts still separates them
SAID_AROUND_S = 5.0  # dialogue this close to an event can be what conveys it
SAID_SHARE = 0.3  # ... when it says at least this share of the event's content words
MAX_TOKENS = 6000
TEMPERATURE = 0.6
ESTIMATE = 0.01
PROFILES = ("education", "entertainment")
NO_SPEECH = "(no speech in this window)"
_EPS = 1e-9
_SENTENCE_END = (".", "?", "!", "…")
_STOP = frozenset(
    "a an the and or of to in on at by for from with into onto is are its their".split()
)
_WORD = re.compile(r"\w+")
# words that say nothing about what is on screen: they never show the dialogue conveys it
_SAID_STOP = _STOP | frozenset(
    "he she him her his hers it its they them their we us our you your i me my "
    "himself herself itself themselves myself yourself ourselves yourselves "
    "can could will would shall should may might must "
    "s t ll re ve d m "
    "is are was were be been being am has have had do does did "
    "this that these those".split()
)

_EVENT_PROPS: dict = {  # VisualEvent minus id
    "t_start": {"type": "number"},
    "t_end": {"type": "number"},
    "kind": {"type": "string", "enum": list(get_args(EventKind))},
    "text": {"type": "string"},
    "verbatim_text": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "salience": {"type": "integer", "minimum": 1, "maximum": 5},
    "said_in_dialogue": {"type": "boolean"},
}
EVENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": _EVENT_PROPS,
                "required": list(_EVENT_PROPS),
                "additionalProperties": False,
            },
        }
    },
    "required": ["events"],
    "additionalProperties": False,
}


@cache
def load_prompt() -> str:
    """System prompt (prompts/watch.md). It encodes R12 visible only, R13 no censoring,
    R14 no assumed identity, R15 naming, R16-R18 priorities and wording, R19 verbatim text,
    R20 slides and talking heads, R21 said_in_dialogue, R22 charts, R7/R9/R10 style."""
    return (files("lull") / "prompts" / "watch.md").read_text(encoding="utf-8")


# -- windows ----------------------------------------------------------------------------


def plan_windows(
    duration: float, cuts: list[float], *, max_len: float = 110.0, overlap: float = 10.0
) -> list[Span]:
    """Overlapping windows covering [0, duration], each at most MAX_CLIP_S long.

    A window nominally ends max_len after its start. If the end of the video is no further
    than SNAP_S past that (and within MAX_CLIP_S), the window takes the rest of the video, so
    there is no sliver tail window. Otherwise, when a scene cut lies within SNAP_S of the
    nominal end, the window ends at the nearest cut (ties go to the earlier), so window edges
    fall between shots. The next window starts `overlap` before the previous end.
    """
    if not (0 <= overlap < max_len <= MAX_CLIP_S):
        raise ValueError(f"need 0 <= overlap < max_len <= {MAX_CLIP_S} (got {overlap}, {max_len})")
    if duration <= 0:
        return []
    inner = sorted(c for c in cuts if 0 < c < duration)
    out: list[Span] = []
    start = 0.0
    while True:
        nominal = start + max_len
        hi = min(nominal + SNAP_S, start + MAX_CLIP_S)  # >= nominal since max_len <= cap
        if duration <= hi:
            out.append(Span(start=start, end=duration))
            return out
        near = [c for c in inner if nominal - SNAP_S <= c <= hi and c > start + overlap]
        end = min(near, key=lambda c: (abs(c - nominal), c)) if near else nominal
        out.append(Span(start=start, end=end))
        start = end - overlap


# -- transcript ------------------------------------------------------------------------


def transcript_slice(words: list[Word], span: Span) -> str:
    """Speaker-tagged lines `[a–b] S1: text` in window-relative seconds.

    A line ends at a speaker change, a pause longer than PAUSE_S, or a sentence end.
    """
    inside = sorted(
        (w for w in words if w.end > span.start and w.start < span.end and w.text.strip()),
        key=lambda w: (w.start, w.end),
    )
    lines: list[str] = []
    run: list[Word] = []
    for w in inside:
        if run and (
            w.speaker != run[-1].speaker
            or w.start - run[-1].end > PAUSE_S
            or run[-1].text.rstrip().endswith(_SENTENCE_END)
        ):
            lines.append(_line(run, span))
            run = []
        run.append(w)
    if run:
        lines.append(_line(run, span))
    return "\n".join(lines) if lines else NO_SPEECH


def _line(run: list[Word], span: Span) -> str:
    a = max(0.0, run[0].start - span.start)
    b = min(span.dur, max(w.end for w in run) - span.start)
    who = "?" if run[0].speaker is None else str(run[0].speaker)
    return f"[{a:.1f}–{b:.1f}] S{who}: " + " ".join(w.text.strip() for w in run)


def _user_text(span: Span, words: list[Word], profile: str) -> str:
    length = f"{span.dur:.1f}"
    return (
        f"Clip length: {length} s. Give every time in seconds from the start of this clip, "
        f"0 to {length}.\n"
        f"Profile: {profile}\n\n"
        f"Transcript (clip seconds):\n{transcript_slice(words, span)}"
    )


# -- model output ----------------------------------------------------------------------


def parse_events(out: dict, length: float, *, window: int = 0) -> list[VisualEvent]:
    """Window-relative events from one model answer, clamped to [0, length].

    Reversed spans are swapped and events with empty text dropped. A malformed event is
    dropped with a warning. An answer without an `events` list, or whose events are all
    malformed, raises ValueError: an empty inventory must never stand in for a broken one.
    """
    raw = out.get("events") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        raise ValueError(f"watch window {window}: model answer has no 'events' list")
    events: list[VisualEvent] = []
    errors: list[str] = []
    for n, item in enumerate(raw):
        try:
            ev = _event(item, length, f"w{window:03d}.{n:03d}")
        except (TypeError, ValueError, KeyError, AttributeError) as e:
            log.warning("watch window %d: dropped malformed event %d: %s", window, n, e)
            errors.append(f"{n}: {e!r}")
            continue
        if ev is not None:
            events.append(ev)
    if raw and len(errors) == len(raw):
        raise ValueError(
            f"watch window {window}: all {len(raw)} events malformed ({'; '.join(errors)[:300]})"
        )
    return events


def _event(item: dict, length: float, id_: str) -> VisualEvent | None:
    text = item["text"]
    if not isinstance(text, str):
        raise TypeError(f"text is {type(text).__name__}, not str")
    if not text.strip():
        return None
    t0, t1 = float(item["t_start"]), float(item["t_end"])
    if not (math.isfinite(t0) and math.isfinite(t1)):
        raise ValueError(f"non-finite time {t0}..{t1}")
    t0, t1 = (min(max(t, 0.0), length) for t in (t0, t1))
    if t1 < t0:
        t0, t1 = t1, t0
    verb = item.get("verbatim_text")
    verb = verb.strip() if isinstance(verb, str) else None
    return VisualEvent(  # pydantic rejects an unknown kind (ValidationError is a ValueError)
        id=id_,
        t_start=t0,
        t_end=t1,
        kind=item["kind"],
        text=text.strip(),
        verbatim_text=verb or None,
        salience=min(5, max(1, round(float(item["salience"])))),
        said_in_dialogue=item.get("said_in_dialogue") is True,
    )


# -- merge -----------------------------------------------------------------------------


@dataclass
class _Group:
    ev: VisualEvent
    windows: set[int]


def _cut_between(a: VisualEvent, b: VisualEvent, cuts: list[float]) -> bool:
    """A scene cut after `a` starts and no later than just after `b` starts: another shot."""
    return any(a.t_start + _EPS < c <= b.t_start + CUT_SLACK_S for c in cuts)


def fold_window(events: list[VisualEvent], cuts: list[float] = ()) -> list[VisualEvent]:
    """One window's events with near-duplicates folded (ruling, Task 16): an event joins the
    last event of its kind when the token Jaccard of their text (or on-screen text) is at least
    MERGE_JACCARD, it starts at most FOLD_GAP_S after that one ends and no scene cut lies
    between them (ruling R3; `cuts` in the events' time). The model sometimes reports one shot
    as per-second slices; folded, they span the union and keep the higher salience (see
    _combine)."""
    cuts = sorted(cuts)
    out: list[VisualEvent] = []
    last: dict[str, int] = {}  # kind -> index in out of the latest event of that kind
    for e in sorted(events, key=lambda e: (e.t_start, e.t_end)):
        i = last.get(e.kind)
        if (
            i is not None
            and e.t_start - out[i].t_end <= FOLD_GAP_S + _EPS
            and not _cut_between(out[i], e, cuts)
        ):
            if _similarity(out[i], e) >= MERGE_JACCARD - _EPS:
                out[i] = _combine(out[i], e)
                continue
        last[e.kind] = len(out)
        out.append(e)
    return out


def merge_events(
    per_window: list[tuple[Span, list[VisualEvent]]], cuts: list[float] = ()
) -> list[VisualEvent]:
    """Window-relative events -> one absolute-time list with ids e001… by start.

    Each window's near-duplicates are folded first (`fold_window`, never across one of the
    scene `cuts`, given in video time).
    An event merges into one from an overlapping window when the kinds match, the token
    Jaccard of the text (or of the on-screen text) is at least MERGE_JACCARD, and either
    - the starts differ by at most MERGE_DT (both windows saw it begin in the overlap), or
    - it is carried over the boundary: the earlier event is still on screen when the new
      window starts and the new event starts with that window (both within MERGE_DT), as a
      slide or setting that persists across the cut between windows is reported.
    Matching is one-to-one, best similarity first. The merged event spans both, keeps the
    higher salience and the longer verbatim text, and counts as said in dialogue only when
    both say so (an undescribed visual is worse than a repeated one).
    """
    spans = [span for span, _ in per_window]
    groups: list[_Group] = []
    for wi, (span, events) in enumerate(per_window):
        shifted = [
            e.model_copy(update={"t_start": e.t_start + span.start, "t_end": e.t_end + span.start})
            for e in fold_window(events, [c - span.start for c in cuts])
        ]
        pool = [
            g
            for g in groups
            if wi not in g.windows and any(_overlap(spans[x], span) for x in g.windows)
        ]
        pairs = sorted(
            (-sim, abs(g.ev.t_start - e.t_start), gi, ei)
            for gi, g in enumerate(pool)
            for ei, e in enumerate(shifted)
            if (sim := _match(g.ev, e, span)) is not None
        )
        used_g: set[int] = set()
        used_e: set[int] = set()
        for _, _, gi, ei in pairs:
            if gi in used_g or ei in used_e:
                continue
            used_g.add(gi)
            used_e.add(ei)
            pool[gi].ev = _combine(pool[gi].ev, shifted[ei])
            pool[gi].windows.add(wi)
        groups += [_Group(e, {wi}) for ei, e in enumerate(shifted) if ei not in used_e]
    ordered = sorted((g.ev for g in groups), key=lambda e: (e.t_start, e.t_end))
    return [e.model_copy(update={"id": f"e{i:03d}"}) for i, e in enumerate(ordered, 1)]


def _overlap(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


def _tokens(s: str | None) -> set[str]:
    return {t for t in _WORD.findall((s or "").lower()) if t not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _match(a: VisualEvent, b: VisualEvent, span: Span) -> float | None:
    """Similarity when `b` (from window `span`) is the same thing as earlier `a`, else None."""
    if a.kind != b.kind:
        return None
    near = abs(a.t_start - b.t_start) <= MERGE_DT + _EPS
    carried = a.t_end >= span.start - MERGE_DT - _EPS and b.t_start <= span.start + MERGE_DT + _EPS
    if not (near or carried):
        return None
    sim = _similarity(a, b)
    return sim if sim >= MERGE_JACCARD - _EPS else None


def _similarity(a: VisualEvent, b: VisualEvent) -> float:
    """Token Jaccard of the descriptions, or of the on-screen text when both have some."""
    sim = _jaccard(_tokens(a.text), _tokens(b.text))
    if a.verbatim_text and b.verbatim_text:
        sim = max(sim, _jaccard(_tokens(a.verbatim_text), _tokens(b.verbatim_text)))
    return sim


def _combine(a: VisualEvent, b: VisualEvent) -> VisualEvent:
    primary = a if (a.salience, len(a.text)) >= (b.salience, len(b.text)) else b
    verbs = [v for v in (a.verbatim_text, b.verbatim_text) if v]
    return primary.model_copy(
        update={
            "t_start": min(a.t_start, b.t_start),
            "t_end": max(a.t_end, b.t_end),
            "salience": max(a.salience, b.salience),
            "verbatim_text": max(verbs, key=len) if verbs else None,
            "said_in_dialogue": a.said_in_dialogue and b.said_in_dialogue,
        }
    )


def confirm_said(events: list[VisualEvent], words: list[Word]) -> list[VisualEvent]:
    """Ruling R1: an event keeps the model's said_in_dialogue only when at least SAID_SHARE of
    its content words are said within SAID_AROUND_S of it; otherwise the flag is cleared (an
    undescribed visual is worse than a repeated one). Content words leave out pronouns
    (reflexive ones too), the fragments of contractions and possessives, be/have/do forms,
    modal verbs and this/that/these/those as well as the usual stopwords. The flag is never
    set here."""
    out = []
    for e in events:
        if e.said_in_dialogue:
            mine = _tokens(e.text) - _SAID_STOP
            near = set().union(*(
                _tokens(w.text) for w in words
                if w.end > e.t_start - SAID_AROUND_S and w.start < e.t_end + SAID_AROUND_S
            ))  # fmt: skip
            if not mine or len(mine & near) < SAID_SHARE * len(mine) - _EPS:
                log.info("watch: %s is not said nearby; said_in_dialogue cleared", e.id)
                e = e.model_copy(update={"said_in_dialogue": False})
        out.append(e)
    return out


# -- stage -----------------------------------------------------------------------------


def watch(
    video: Path, work: Path, words: list[Word], tf: TokenFactory, *, run_id: str, profile: str
) -> list[VisualEvent]:
    """Inventory the visual events of `video` in absolute seconds, windows sent one by one."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r} (expected one of {PROFILES})")
    info = probe(video)
    cuts = scene_cuts(video)
    windows = plan_windows(info.duration, cuts)
    clips = work / "watch"
    clips.mkdir(parents=True, exist_ok=True)
    per_window: list[tuple[Span, list[VisualEvent]]] = []
    for i, span in enumerate(windows):
        clip = cut_clip(video, clips / f"w{i:03d}.mp4", span.start, span.end, height=CLIP_HEIGHT)
        if clip.stat().st_size > CLIP_MAX_BYTES:
            clip = make_proxy(
                clip, clips / f"w{i:03d}_proxy.mp4", max_bytes=CLIP_MAX_BYTES, height=CLIP_HEIGHT
            )
        messages = [
            {"role": "system", "content": load_prompt()},
            {
                "role": "user",
                "content": [
                    video_part(clip),
                    {"type": "text", "text": _user_text(span, words, profile)},
                ],
            },
        ]
        out = tf.json(
            VL, messages, EVENT_SCHEMA,
            run_id=run_id, stage="watch", role="watch", thinking=False,
            max_tokens=MAX_TOKENS, temperature=TEMPERATURE, estimate=ESTIMATE,
        )  # fmt: skip
        per_window.append((span, parse_events(out, span.dur, window=i)))
    return confirm_said(merge_events(per_window, cuts), words)
