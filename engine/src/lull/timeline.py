"""Pure timeline math: speech intervals -> usable silences with word capacities."""

from __future__ import annotations

import math

from lull.types import Gap, Span, Turn, Word

EPS = 1e-9


def _r(x: float) -> float:
    return round(x, 3)


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


def speech_spans(
    words: list[Word], turns: list[Turn], join_within: float = 0.25
) -> list[Span]:
    """Union of word spans and diarized turns, merged across tiny pauses."""
    spans = [Span(start=w.start, end=w.end) for w in words]
    spans += [Span(start=t.start, end=t.end) for t in turns]
    return merge_spans(spans, join_within)


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
    wps: float = 3.0,
) -> list[Gap]:
    """Usable silences. Guard is trimmed from every side (speech, protected, video edge)."""
    prot = merge_spans(protected or [])
    pieces = complement(prot, 0.0, duration) if prot else [Span(start=0.0, end=duration)]
    silences: list[Span] = []
    for piece in pieces:
        silences += complement(speech, piece.start, piece.end)
    gaps: list[Gap] = []
    for s in silences:
        us, ue = s.start + guard, s.end - guard
        if ue - us < min_usable - EPS:
            continue
        inside = [p for p in prot if p.start < s.end + EPS and p.end > s.start - EPS]
        gaps.append(
            Gap(
                id=f"g{len(gaps) + 1:03d}",
                start=_r(s.start),
                end=_r(s.end),
                usable_start=_r(us),
                usable_end=_r(ue),
                capacity_words=capacity_words(ue - us, wps),
                protected=inside,
            )
        )
    return gaps
