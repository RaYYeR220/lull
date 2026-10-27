from lull.timeline import (
    capacity_words,
    complement,
    find_gaps,
    merge_spans,
    overlap_seconds,
    speech_spans,
)
from lull.types import Span, Turn, Word


def test_merge_spans():
    out = merge_spans([Span(start=0, end=1), Span(start=0.9, end=2), Span(start=3, end=4)])
    assert out == [Span(start=0, end=2), Span(start=3, end=4)]


def test_merge_join_within():
    spans = [Span(start=0, end=1), Span(start=1.2, end=2)]
    assert len(merge_spans(spans)) == 2
    assert merge_spans(spans, join_within=0.25) == [Span(start=0, end=2)]


def test_complement():
    assert complement([Span(start=1, end=2)], 0, 5) == [
        Span(start=0, end=1),
        Span(start=2, end=5),
    ]


def test_speech_spans_union():
    words = [Word(text="a", start=0, end=1), Word(text="b", start=1.1, end=2)]
    turns = [Turn(speaker=0, start=5, end=6)]
    assert speech_spans(words, turns) == [Span(start=0, end=2), Span(start=5, end=6)]


def test_capacity_words():
    assert capacity_words(2.6, 3.0) == 7
    assert capacity_words(3.6, 3.0) == 10


def test_two_gaps():
    words = [Word(text="a", start=0.0, end=2.0), Word(text="b", start=5.0, end=6.0)]
    gaps = find_gaps(speech_spans(words, []), 10)
    assert [g.id for g in gaps] == ["g001", "g002"]
    assert (gaps[0].usable_start, gaps[0].usable_end, gaps[0].capacity_words) == (2.2, 4.8, 7)
    assert (gaps[1].usable_start, gaps[1].usable_end, gaps[1].capacity_words) == (6.2, 9.8, 10)


def test_short_gap_dropped():
    assert find_gaps([Span(start=0, end=2), Span(start=3, end=4)], 4) == []


def test_no_speech_one_gap():
    gaps = find_gaps([], 30)
    assert len(gaps) == 1
    assert (gaps[0].usable_start, gaps[0].usable_end) == (0.2, 29.8)


def test_continuous_speech_no_gaps():
    assert find_gaps([Span(start=0, end=30)], 30) == []


def test_protected_splits_gap():
    prot = [Span(start=5, end=6)]
    gaps = find_gaps([Span(start=0, end=2), Span(start=10, end=12)], 12, protected=prot)
    assert [(g.usable_start, g.usable_end) for g in gaps] == [(2.2, 4.8), (6.2, 9.8)]
    assert gaps[0].id == "g001" and gaps[1].id == "g002"


def test_overlap_seconds():
    assert overlap_seconds([Span(start=0, end=2)], [Span(start=1.5, end=3)]) == 0.5
