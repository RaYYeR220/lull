import pytest

from lull.timeline import (
    capacity_words,
    complement,
    find_gaps,
    merge_spans,
    overlap_seconds,
    speech_spans,
    split_gaps,
)
from lull.types import Gap, Span, Turn, Word


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


def _gap(id_, us, ue, cap, protected=()):
    return Gap(id=id_, start=us - 0.2, end=ue + 0.2, usable_start=us, usable_end=ue,
               capacity_words=cap, protected=list(protected))  # fmt: skip


def test_split_gaps_cuts_a_30_s_no_speech_gap_into_five_windows():
    [whole] = find_gaps([], 30, wps=2.36)
    out = split_gaps([whole], wps=2.36)
    width = (29.6 - 4 * 0.3) / 5  # 5.68 s each, 0.3 s breath between, no extra guard
    assert [g.id for g in out] == ["g001", "g002", "g003", "g004", "g005"]
    for i, g in enumerate(out):
        assert g.usable_start == pytest.approx(0.2 + i * (width + 0.3))
        assert g.usable_end == pytest.approx(0.2 + i * (width + 0.3) + width)
        assert g.capacity_words == capacity_words(width, 2.36) == 13
        assert g.protected == []
    assert out[0].start == 0.0 and out[-1].end == 30.0
    assert out[-1].usable_end == 29.8
    assert all(a.end <= b.start for a, b in zip(out, out[1:], strict=False))


def test_split_gaps_leaves_short_gaps_alone_and_renumbers_in_time_order():
    keep = _gap("g001", 1.0, 8.0, 21, [Span(start=0.5, end=0.8)])  # 7 s: untouched
    edge = _gap("g003", 32.0, 41.0, 27)  # exactly 1.5 x 6 s: untouched
    long_ = _gap("g002", 10.0, 30.0, 60)  # 20 s: ceil(20 / 6) = 4 windows
    out = split_gaps([edge, long_, keep], wps=3.0)
    assert [g.id for g in out] == [f"g{i:03d}" for i in range(1, 7)]
    assert out[0] == keep
    assert out[5] == edge.model_copy(update={"id": "g006"})
    width = (20.0 - 3 * 0.3) / 4
    starts = [10.0 + i * (width + 0.3) for i in range(4)]
    assert [g.usable_start for g in out[1:5]] == pytest.approx(starts)
    assert [g.usable_end for g in out[1:5]] == pytest.approx([s + width for s in starts])
    assert {g.capacity_words for g in out[1:5]} == {capacity_words(width, 3.0)}


def test_split_gaps_takes_the_window_and_breath():
    out = split_gaps([_gap("g001", 0.0, 10.0, 30)], max_window=2.0, breath=0.5, wps=3.0)
    assert len(out) == 5  # 10 s > 1.5 x 2 s, ceil(10 / 2) = 5
    assert out[1].usable_start - out[0].usable_end == pytest.approx(0.5)
    assert split_gaps([], wps=3.0) == []


# -- boundaries (Task 2 minors) ------------------------------------------------------------------


def test_usable_windows_round_inward_and_capacity_follows_the_rounded_window():
    # 1.00004 + 0.2 rounds up to 1.201, 5.10006 - 0.2 rounds down to 4.9: never into a guard
    [g] = find_gaps([Span(start=0.0, end=1.00004), Span(start=5.10006, end=9.0)], 9.0, wps=3.0)
    assert (g.usable_start, g.usable_end) == (1.201, 4.9)
    assert g.capacity_words == capacity_words(4.9 - 1.201, 3.0) == 11
    # float noise never pushes a whole-millisecond edge outward or inward
    [h] = find_gaps([Span(start=0.0, end=2.0), Span(start=5.0, end=6.0)], 6.0)
    assert (h.usable_start, h.usable_end) == (2.2, 4.8)


def test_a_silence_exactly_min_usable_long_after_guards_is_kept():
    [g] = find_gaps([Span(start=0.0, end=2.0), Span(start=3.6, end=5.0)], 5.0)
    assert (g.usable_start, g.usable_end) == (2.2, 3.4)
    assert find_gaps([Span(start=0.0, end=2.0), Span(start=3.599, end=5.0)], 5.0) == []


def test_find_gaps_takes_unsorted_touching_and_zero_length_speech():
    speech = [Span(start=6.0, end=7.0), Span(start=0.0, end=1.0), Span(start=1.0, end=2.0),
              Span(start=4.0, end=4.0)]  # fmt: skip
    gaps = find_gaps(speech, 10.0)
    assert [(g.usable_start, g.usable_end) for g in gaps] == [(2.2, 3.8), (4.2, 5.8), (7.2, 9.8)]


def test_protected_sound_over_speech_or_at_the_edge():
    speech = [Span(start=2.0, end=3.0)]
    prot = [Span(start=2.5, end=4.0), Span(start=0.0, end=0.3)]  # over speech; at the start
    gaps = find_gaps(speech, 10.0, protected=prot)
    assert [(g.usable_start, g.usable_end) for g in gaps] == [(0.5, 1.8), (4.2, 9.8)]
    assert gaps[0].protected == [Span(start=0.0, end=0.3)]
    assert find_gaps([], 10.0, protected=[Span(start=9.0, end=10.0)])[0].usable_end == 8.8


def test_guard_zero_and_an_empty_video():
    [g] = find_gaps([Span(start=1.0, end=2.0)], 4.0, guard=0.0)
    assert (g.start, g.usable_start, g.usable_end, g.end) == (2.0, 2.0, 4.0, 4.0)
    assert find_gaps([], 0.0) == [] and find_gaps([], -1.0) == []
    assert find_gaps([Span(start=0.0, end=1.0)], 0.5) == []


def test_split_gaps_needs_a_window_longer_than_its_breath():
    g = Gap(id="g001", start=0, end=30, usable_start=0.2, usable_end=29.8, capacity_words=80)
    for window, breath in ((0.3, 0.3), (0.2, 0.3), (0.0, 0.0)):
        with pytest.raises(ValueError, match="max_window"):
            split_gaps([g], max_window=window, breath=breath, wps=3.0)
