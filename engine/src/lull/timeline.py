"""Pure timeline math: speech intervals -> usable silences with word capacities."""

from __future__ import annotations

import math

from lull.types import Gap, Span, Turn, Word

# words per second of a voice not calibrated yet: the default of every function that takes a
# rate (the pipeline always passes the calibrated one)
UNCALIBRATED_WPS = 2.8

EPS = 1e-9


def _r(x: float) -> float:
    return round(x, 3)


def _ceil3(x: float) -> float:
    """`x` rounded up to the millisecond (float noise below a micro-millisecond ignored)."""
    return math.ceil(round(x * 1000, 6)) / 1000


def _floor3(x: float) -> float:
    return math.floor(round(x * 1000, 6)) / 1000


def merge_spans(spans: list[Span], join_within: float = 0.0) -> list[Span]:
    """Sort and merge overlapping spans, and spans separated by <= join_within."""
    out: list[Span] = []
    for s in sorted(spans, key=lambda s: (s.start, s.end)):
        if out and s.start <= out[-1].end + join_within + EPS:
            if s.end > out[-1].end:
                out[-1] = Span(start=out[-1].start, end=s.end)
        else:
            out.append(Span(start=s.start, end=s.end))
    return out


def speech_spans(words: list[Word], turns: list[Turn], join_within: float = 0.25) -> list[Span]:
    """Where someone speaks. The recogniser's words bound speech wherever there are words:
    their spans, merged across tiny pauses. A diarised turn counts only where it holds no word
    at all (a laugh, singing): one that runs past the words, or across the pause between two
    sentences, never widens the speech they bound."""
    spans = merge_spans([Span(start=w.start, end=w.end) for w in words], join_within)
    wordless = [
        Span(start=t.start, end=t.end)
        for t in turns
        if not any(w.start < t.end and w.end > t.start for w in words)
    ]
    return merge_spans(spans + wordless, join_within)


def complement(spans: list[Span], start: float, end: float) -> list[Span]:
    """Non-empty stretches of [start, end] not covered by spans."""
    out: list[Span] = []
    cursor = start
    for s in merge_spans(spans):
        if s.end <= cursor:
            continue
        if s.start >= end:
            break
        if s.start > cursor + EPS:
            out.append(Span(start=cursor, end=s.start))
        cursor = max(cursor, s.end)
    if end > cursor + EPS:
        out.append(Span(start=cursor, end=end))
    return out


def capacity_words(usable_seconds: float, wps: float) -> int:
    return max(0, math.floor(usable_seconds * wps + EPS))


def overlap_seconds(a: list[Span], b: list[Span]) -> float:
    """Total time covered by both span lists."""
    ma, mb = merge_spans(a), merge_spans(b)
    total = 0.0
    for x in ma:
        for y in mb:
            lo, hi = max(x.start, y.start), min(x.end, y.end)
            if hi > lo:
                total += hi - lo
    return _r(total)


def find_gaps(
    speech: list[Span],
    duration: float,
    *,
    guard: float = 0.20,
    min_usable: float = 1.2,
    protected: list[Span] | None = None,
    wps: float = UNCALIBRATED_WPS,
) -> list[Gap]:
    """Usable silences. Guard is trimmed from every side (speech, protected, video edge).

    The usable window is rounded inward to the millisecond (start up, end down), so rounding
    never reaches into a guard, and its capacity is computed from the rounded window. A window
    must be at least `min_usable` long; an empty or negative duration has none."""
    if duration <= 0:
        return []
    prot = merge_spans(protected or [])
    pieces = complement(prot, 0.0, duration) if prot else [Span(start=0.0, end=duration)]
    silences: list[Span] = []
    for piece in pieces:
        silences += complement(speech, piece.start, piece.end)
    gaps: list[Gap] = []
    for s in silences:
        us, ue = _ceil3(s.start + guard), _floor3(s.end - guard)
        if ue <= us or ue - us < min_usable - EPS:
            continue
        inside = [p for p in prot if p.start < s.end + EPS and p.end > s.start - EPS]
        gaps.append(
            Gap(
                id=f"g{len(gaps) + 1:03d}",
                start=_r(s.start),
                end=_r(s.end),
                usable_start=us,
                usable_end=ue,
                capacity_words=capacity_words(ue - us, wps),
                protected=inside,
            )
        )
    return gaps


def split_gaps(
    gaps: list[Gap], *, max_window: float = 6.0, breath: float = 0.3, wps: float
) -> list[Gap]:
    """Long gaps cut into short windows, so a cue can't describe a visual far ahead of time.

    A gap whose usable length is over 1.5 x max_window becomes n = ceil(usable / max_window)
    equal windows `breath` seconds apart (no extra guard: there is no speech between them),
    each with its capacity recomputed from its window rounded inward to the millisecond and no
    protected spans; the first keeps the gap's start and the last its end. Shorter gaps are
    kept as they are. All gaps are renumbered g001... in time order. `max_window` must be
    longer than `breath`.
    """
    if not max_window > breath >= 0:
        raise ValueError(f"split_gaps: need max_window > breath >= 0, got {max_window}, {breath}")
    out: list[Gap] = []
    for g in sorted(gaps, key=lambda g: (g.usable_start, g.id)):
        usable = g.usable_end - g.usable_start
        if usable <= 1.5 * max_window + EPS:
            out.append(g)
            continue
        n = math.ceil(usable / max_window - EPS)
        width = (usable - (n - 1) * breath) / n
        for i in range(n):
            raw = g.usable_start + i * (width + breath)
            us = g.usable_start if i == 0 else _ceil3(raw)
            ue = g.usable_end if i == n - 1 else _floor3(raw + width)
            out.append(
                Gap(
                    id=g.id,
                    start=g.start if i == 0 else us,
                    end=g.end if i == n - 1 else ue,
                    usable_start=us,
                    usable_end=ue,
                    capacity_words=capacity_words(ue - us, wps),
                    protected=[],
                )
            )
    return [g.model_copy(update={"id": f"g{i + 1:03d}"}) for i, g in enumerate(out)]
