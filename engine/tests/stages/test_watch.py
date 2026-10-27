import subprocess
from pathlib import Path

import pytest

from lull.stages import watch as watch_mod
from lull.stages.watch import (
    EVENT_SCHEMA,
    confirm_said,
    load_prompt,
    merge_events,
    parse_events,
    plan_windows,
    transcript_slice,
    watch,
)
from lull.tf import VL
from lull.types import Span, VisualEvent, Word


class FakeTF:
    """Stands in for TokenFactory: returns canned JSON per call and records every call."""

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def json(self, model, messages, schema, **kw) -> dict:
        self.calls.append({"model": model, "messages": messages, "schema": schema, **kw})
        return self.responses.pop(0)


def _ev(t0, t1, kind="action", text="a person opens a door", *, sal=3, verb=None, said=False):
    return {
        "t_start": t0,
        "t_end": t1,
        "kind": kind,
        "text": text,
        "verbatim_text": verb,
        "salience": sal,
        "said_in_dialogue": said,
    }


def _ve(t0, t1, kind="action", text="a person opens a door", *, sal=3, verb=None, said=False):
    return VisualEvent(id="x", **_ev(t0, t1, kind, text, sal=sal, verb=verb, said=said))


def _span(a, b):
    return Span(start=a, end=b)


def _pairs(spans):
    return [(round(s.start, 3), round(s.end, 3)) for s in spans]


def _user_text(call) -> str:
    user = next(m for m in call["messages"] if m["role"] == "user")
    return "\n".join(p["text"] for p in user["content"] if p["type"] == "text")


# -- plan_windows -------------------------------------------------------------------


def test_plan_windows_short_video_is_one_window():
    assert plan_windows(30, []) == [Span(start=0, end=30)]


def test_plan_windows_snaps_to_cuts():
    assert _pairs(plan_windows(250, [100.0, 205.0])) == [(0, 100), (90, 205), (195, 250)]


def test_plan_windows_no_cuts_uses_max_len_and_overlap():
    assert _pairs(plan_windows(250, [])) == [(0, 110), (100, 210), (200, 250)]


def test_plan_windows_cut_beyond_hard_cap_ignored():
    # 119 s would make a clip longer than the model accepts safely
    assert _pairs(plan_windows(200, [119.0])) == [(0, 110), (100, 200)]


def test_plan_windows_picks_cut_nearest_nominal_end():
    assert _pairs(plan_windows(150, [96.0, 108.0, 113.0])) == [(0, 108), (98, 150)]


def test_plan_windows_end_of_video_counts_as_a_cut():
    # 115 s fits one clip; no 15 s sliver window for the tail
    assert _pairs(plan_windows(115, [])) == [(0, 115)]


@pytest.mark.parametrize(("duration", "cuts"), [(110.2, [110.0]), (112.0, [109.5])])
def test_plan_windows_video_end_in_snap_range_wins_over_cut(duration, cuts):
    assert _pairs(plan_windows(duration, cuts)) == [(0, duration)]


def test_plan_windows_always_progresses_with_small_max_len():
    wins = plan_windows(60, [7.0, 21.0, 33.0], max_len=20, overlap=10)
    assert wins[0].start == 0 and wins[-1].end == 60
    for a, b in zip(wins, wins[1:], strict=False):
        assert b.start > a.start and b.start < a.end  # overlapping, moving forward


@pytest.mark.parametrize("duration", [30.0, 111.0, 250.0, 601.7, 3600.0])
def test_plan_windows_covers_video_within_limits(duration):
    cuts = [c * 7.3 for c in range(1, int(duration / 7.3))]
    wins = plan_windows(duration, cuts)
    assert wins[0].start == 0 and wins[-1].end == pytest.approx(duration)
    for a, b in zip(wins, wins[1:], strict=False):
        assert b.start <= a.end  # no holes
        assert a.end - b.start == pytest.approx(10.0)
    assert all(w.dur <= watch_mod.MAX_CLIP_S + 1e-9 for w in wins)


def test_plan_windows_empty_video():
    assert plan_windows(0, []) == []


def test_plan_windows_rejects_bad_params():
    with pytest.raises(ValueError):
        plan_windows(100, [], max_len=10, overlap=10)
    with pytest.raises(ValueError):
        plan_windows(100, [], max_len=130)


# -- merge_events -------------------------------------------------------------------


def test_merge_dedupes_duplicated_slide_across_overlap_and_offsets():
    w1, w2 = _span(0, 100), _span(90, 205)
    slide = "Slide titled Quarterly revenue with three bullet points"
    per_window = [
        (w1, [_ve(3, 6, "person", "a person in a grey suit at a lectern", sal=1),
              _ve(92.4, 100, "slide", slide, sal=4, verb="Quarterly revenue")]),
        (w2, [_ve(1.8, 30, "slide", "Slide titled Quarterly revenue, three bullets", sal=5,
                  verb="Quarterly revenue\n- Up 12%\n- Two new markets"),
              _ve(40, 44, "chart", "Bar chart of revenue by year", sal=5)]),
    ]  # fmt: skip
    out = merge_events(per_window)
    assert [e.id for e in out] == ["e001", "e002", "e003"]
    assert [e.kind for e in out] == ["person", "slide", "chart"]
    s = out[1]
    assert s.t_start == pytest.approx(91.8) and s.t_end == pytest.approx(120.0)
    assert s.salience == 5
    assert s.verbatim_text == "Quarterly revenue\n- Up 12%\n- Two new markets"
    assert out[2].t_start == pytest.approx(130.0) and out[2].t_end == pytest.approx(134.0)
    assert out[0].t_start == pytest.approx(3.0)


def test_merge_keeps_distinct_events():
    w1, w2 = _span(0, 100), _span(90, 205)
    per_window = [
        (w1, [_ve(95, 97, "slide", "Slide titled Methods")]),
        (w2, [
            _ve(5, 7, "on_screen_text", "Slide titled Methods"),     # other kind
            _ve(9, 12, "slide", "Slide titled Methods"),             # |dstart| > 2 s
            _ve(5.5, 8, "slide", "A diagram of the water cycle"),    # low overlap
        ]),
    ]  # fmt: skip
    assert len(merge_events(per_window)) == 4


def test_merge_folds_near_duplicates_within_one_window():
    """Ruling (Task 16): one shot reported as per-second slices becomes one event."""
    w = _span(30, 130)
    tag = "#CLIMATECHAMPIONS"
    per_window = [(w, [
        _ve(9.0, 10.0, "on_screen_text", "The tag #CLIMATECHAMPIONS on black", sal=4, verb=tag),
        _ve(10.0, 11.0, "on_screen_text", "Hashtag #CLIMATECHAMPIONS shown on black", sal=5,
            verb=tag),
        _ve(11.5, 12.5, "on_screen_text", "Hashtag #CLIMATECHAMPIONS on black", sal=4, verb=tag),
        _ve(12.5, 13.5, "on_screen_text", "USGS logo with the tagline science for a changing world",
            sal=5, verb="USGS science for a changing world"),
        _ve(13.5, 14.5, "on_screen_text", "USGS logo and tagline", sal=4,
            verb="USGS science for a changing world"),
        _ve(10, 12, text="a person waves"), _ve(11, 13, text="a person waves"),
    ])]  # fmt: skip
    out = merge_events(per_window)
    assert [(e.kind, e.t_start, e.t_end, e.salience) for e in out] == [
        ("on_screen_text", 39.0, 42.5, 5),
        ("action", 40.0, 43.0, 3),
        ("on_screen_text", 42.5, 44.5, 5),
    ]
    assert [e.id for e in out] == ["e001", "e002", "e003"]
    assert out[0].verbatim_text == tag
    assert out[2].text == "USGS logo with the tagline science for a changing world"


def test_merge_within_a_window_needs_same_kind_similar_text_and_at_most_1_5_s_apart():
    w = _span(0, 100)
    cases = [
        [_ve(10, 11, "action", "a person waves"), _ve(11, 12, "gesture", "a person waves")],
        [_ve(10, 11, text="a person waves"), _ve(11, 12, text="a dog runs across the yard")],
        [_ve(10, 11, text="a person waves"), _ve(12.6, 13, text="a person waves")],  # 1.6 s
        # adjacent means the last event of that kind: another action in between breaks the run
        [_ve(10, 11, text="a person waves"), _ve(11, 12, text="a dog runs across the yard"),
         _ve(12, 13, text="a person waves")],
    ]  # fmt: skip
    for events in cases:
        assert len(merge_events([(w, events)])) == len(events), events
    edge = [_ve(10, 11, text="a person waves"), _ve(12.5, 13, text="a person waves")]  # 1.5 s
    (one,) = merge_events([(w, edge)])
    assert (one.t_start, one.t_end) == (10, 13)


def test_merge_only_between_overlapping_windows():
    per_window = [
        (_span(0, 50), [_ve(49.5, 50, text="a person waves")]),
        (_span(51, 100), [_ve(0, 1, text="a person waves")]),  # abs 51: close, but disjoint
    ]
    assert len(merge_events(per_window)) == 2


def test_merge_is_one_to_one():
    w1, w2 = _span(0, 100), _span(90, 200)
    per_window = [  # the two in w2 are each like the one in w1, not like each other
        (w1, [_ve(95, 96, text="a person waves at the camera and smiles")]),
        (w2, [_ve(5, 6, text="a person waves at the camera, laughing"),
              _ve(6, 7, text="a person smiles and waves, nodding")]),
    ]  # fmt: skip
    out = merge_events(per_window)
    assert len(out) == 2
    assert out[0].t_start == pytest.approx(95.0) and out[0].t_end == pytest.approx(96.0)


def test_merge_said_in_dialogue_needs_both():
    w1, w2 = _span(0, 100), _span(90, 200)
    per_window = [
        (w1, [_ve(95, 99, "slide", "Slide titled Results", said=True)]),
        (w2, [_ve(5, 20, "slide", "Slide titled Results", said=False)]),
    ]
    (only,) = merge_events(per_window)
    assert only.said_in_dialogue is False


def test_merge_empty():
    assert merge_events([]) == []


def test_merge_carries_element_across_window_boundary():
    # on screen when B starts: A reports [50,100], B reports it from its own start
    a, b = _span(0, 110), _span(100, 210)
    per_window = [
        (a, [_ve(50, 100, "slide", "Slide titled Results", sal=3)]),
        (b, [_ve(0, 30, "slide", "Slide titled Results", sal=4)]),
    ]
    (only,) = merge_events(per_window)
    assert (only.t_start, only.t_end) == pytest.approx((50.0, 130.0))
    assert only.salience == 4 and only.id == "e001"


def test_merge_carry_over_chains_through_a_middle_window():
    per_window = [
        (_span(0, 110), [_ve(50, 110, "setting", "a lecture hall with tiered seats")]),
        (_span(100, 210), [_ve(0, 110, "setting", "a lecture hall with tiered seats")]),
        (_span(200, 260), [_ve(0, 20, "setting", "a lecture hall with tiered seats")]),
    ]
    (only,) = merge_events(per_window)
    assert (only.t_start, only.t_end) == pytest.approx((50.0, 220.0))


def test_merge_carry_over_needs_element_alive_at_the_boundary():
    a, b = _span(0, 110), _span(100, 210)
    gone_before_b = [
        (a, [_ve(50, 90, "slide", "Slide titled Results")]),
        (b, [_ve(0, 30, "slide", "Slide titled Results")]),
    ]
    assert len(merge_events(gone_before_b)) == 2
    appears_later_in_b = [
        (a, [_ve(50, 110, "slide", "Slide titled Results")]),
        (b, [_ve(5, 30, "slide", "Slide titled Results")]),  # abs 105: not from B's start
    ]
    assert len(merge_events(appears_later_in_b)) == 2
    other_kind = [
        (a, [_ve(50, 110, "slide", "Slide titled Results")]),
        (b, [_ve(0, 30, "on_screen_text", "Slide titled Results")]),
    ]
    assert len(merge_events(other_kind)) == 2


# -- transcript slice ----------------------------------------------------------------


def test_transcript_slice_relative_speaker_runs():
    words = [
        Word(text="Before", start=98.0, end=98.5, speaker=1),  # ends before the window
        Word(text="Hello", start=99.8, end=100.1, speaker=1),  # straddles the start
        Word(text="everyone.", start=100.2, end=100.6, speaker=1),
        Word(text="Today", start=101.0, end=101.3, speaker=1),
        Word(text="we", start=101.35, end=101.5, speaker=1),
        Word(text="Thanks", start=102.0, end=102.4, speaker=2),
        Word(text="sure", start=105.0, end=105.4, speaker=None),
        Word(text="later", start=300.0, end=300.5, speaker=1),
    ]
    out = transcript_slice(words, _span(100.0, 210.0))
    assert out.splitlines() == [
        "[0.0–0.6] S1: Hello everyone.",
        "[1.0–1.5] S1: Today we",
        "[2.0–2.4] S2: Thanks",
        "[5.0–5.4] S?: sure",
    ]


def test_transcript_slice_breaks_on_long_pause():
    words = [
        Word(text="one", start=1.0, end=1.4, speaker=1),
        Word(text="two", start=4.0, end=4.4, speaker=1),
    ]
    assert transcript_slice(words, _span(0, 10)).splitlines() == [
        "[1.0–1.4] S1: one",
        "[4.0–4.4] S1: two",
    ]


def test_transcript_slice_no_speech():
    words = [Word(text="far", start=500.0, end=500.5, speaker=1)]
    assert transcript_slice(words, _span(0, 100)) == "(no speech in this window)"


# -- parse_events --------------------------------------------------------------------


def test_parse_events_clamps_swaps_and_drops():
    out = {
        "events": [
            _ev(-2.0, 5.0),
            _ev(30.0, 140.0, "slide", "Slide titled Results", verb="Results"),
            _ev(12.0, 8.0, "gesture", "a person points at the board"),
            _ev(4.0, 6.0, "object", "   "),
            _ev(7.0, 9.0, "on_screen_text", "Caption reads Day one", verb=""),
            _ev(1.0, 2.0, "setting", "a lab", sal=9),
        ]
    }
    evs = parse_events(out, 100.0)
    assert [(e.t_start, e.t_end) for e in evs] == [
        (0.0, 5.0), (30.0, 100.0), (8.0, 12.0), (7.0, 9.0), (1.0, 2.0),
    ]  # fmt: skip
    assert evs[3].verbatim_text is None
    assert evs[4].salience == 5


def test_parse_events_rejects_missing_events_list():
    with pytest.raises(ValueError):
        parse_events({"items": []}, 10.0)


def test_parse_events_raises_when_every_event_is_malformed():
    bad = {"events": [{"bogus": 1}, _ev(1.0, 2.0, "explosion", "a flash")]}
    with pytest.raises(ValueError, match="malformed"):
        parse_events(bad, 10.0)


def test_parse_events_keeps_good_events_beside_malformed_ones():
    mixed = {"events": [{"bogus": 1}, _ev(1.0, 2.0)]}
    assert len(parse_events(mixed, 10.0)) == 1
    assert parse_events({"events": []}, 10.0) == []


# -- schema and prompt ---------------------------------------------------------------


def test_schema_is_strict_visual_event_minus_id():
    item = EVENT_SCHEMA["properties"]["events"]["items"]
    assert EVENT_SCHEMA["additionalProperties"] is False
    assert EVENT_SCHEMA["required"] == ["events"]
    assert item["additionalProperties"] is False
    fields = set(VisualEvent.model_fields) - {"id"}
    assert set(item["properties"]) == fields
    assert set(item["required"]) == fields
    assert set(item["properties"]["kind"]["enum"]) == {
        "setting", "person", "action", "on_screen_text", "slide",
        "chart", "object", "scene_change", "gesture",
    }  # fmt: skip


def test_prompt_loads_from_package():
    text = load_prompt()
    for needle in ("verbatim_text", "said_in_dialogue", "salience", "scene_change", "t_start"):
        assert needle in text


def test_prompt_rules_wording():
    text = load_prompt()
    assert "A name caption establishes only the name." in text  # R14
    assert "Use gendered words (he, she, man, woman) only when the transcript" in text
    assert "same level of detail" in text  # R14 equal treatment
    assert 'not "looks upset"' in text  # R12
    assert "increment (tick spacing)" in text  # R22
    assert "Watch the whole clip to the end; list events wherever they occur." in text
    assert "cover the whole clip" not in text


# -- watch (fake Token Factory) --------------------------------------------------------


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def two_window_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """125 s at 2 fps, red until 104 s then blue; no audio. Windows: [0,104], [94,125]."""
    out = tmp_path_factory.mktemp("media") / "two_window.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=red:s=320x180:r=2:d=104",
        "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=2:d=21",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    )  # fmt: skip
    return out


def test_watch_returns_absolute_times(two_window_video: Path, tmp_path: Path):
    words = [
        Word(text="Look", start=96.0, end=96.4, speaker=1),
        Word(text="here.", start=96.5, end=97.0, speaker=1),
    ]
    fake = FakeTF([
        {"events": [_ev(2.0, 5.0, "setting", "a plain red screen", sal=2),
                    _ev(103.5, 150.0, "scene_change", "the screen turns blue", sal=4)]},
        {"events": [_ev(10.0, 10.5, "scene_change", "the screen turns blue", sal=4),
                    _ev(20.0, 25.0, "object", "a blue field fills the frame", said=True)]},
    ])  # fmt: skip
    out = watch(two_window_video, tmp_path, words, fake, run_id="r1", profile="education")

    assert len(fake.calls) == 2
    for call in fake.calls:
        assert call["model"] == VL
        assert call["schema"] == EVENT_SCHEMA
        assert call["run_id"] == "r1" and call["stage"] == "watch" and call["role"] == "watch"
        assert call["thinking"] is False
        assert call["max_tokens"] == 6000
        assert call["temperature"] == 0.6
        assert call["estimate"] == 0.01
        user = next(m for m in call["messages"] if m["role"] == "user")
        video = [p for p in user["content"] if p["type"] == "video_url"]
        assert len(video) == 1
        assert video[0]["video_url"]["url"].startswith("data:video/mp4;base64,")
        system = next(m for m in call["messages"] if m["role"] == "system")
        assert system["content"] == load_prompt()

    t1, t2 = _user_text(fake.calls[0]), _user_text(fake.calls[1])
    assert "[96.0–97.0] S1: Look here." in t1
    assert "[2.0–3.0] S1: Look here." in t2  # window 2 starts at 94 s
    assert "education" in t1
    assert "104.0" in t1 and "31.0" in t2  # clip lengths

    # window 1: [2,5] and the scene change clamped to [103.5,104]; window 2 offset by 94:
    # its scene change at 104-104.5 merges with window 1's, the object lands at 114-119
    assert [e.id for e in out] == ["e001", "e002", "e003"]
    assert [e.kind for e in out] == ["setting", "scene_change", "object"]
    assert (out[0].t_start, out[0].t_end) == pytest.approx((2.0, 5.0), abs=0.05)
    assert (out[1].t_start, out[1].t_end) == pytest.approx((103.5, 104.5), abs=0.05)
    assert (out[2].t_start, out[2].t_end) == pytest.approx((114.0, 119.0), abs=0.05)
    assert all(0 <= e.t_start <= e.t_end <= 125.5 for e in out)
    assert out[2].said_in_dialogue is False  # R1: nobody says "blue field" near 114-119 s


def test_watch_raises_when_a_window_answer_is_all_malformed(two_window_video: Path, tmp_path: Path):
    fake = FakeTF([{"events": [{"t_start": 1.0}]}])
    with pytest.raises(ValueError, match="malformed"):
        watch(two_window_video, tmp_path, [], fake, run_id="r", profile="education")
    assert len(fake.calls) == 1  # stops at the bad window, no further spend


def test_watch_no_speech_and_proxy_fallback(
    two_window_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    proxied: list[tuple[Path, int]] = []

    def fake_proxy(src: Path, dst: Path, *, max_bytes: int, height: int) -> Path:
        proxied.append((dst, max_bytes))
        dst.write_bytes(src.read_bytes())
        return dst

    monkeypatch.setattr(watch_mod, "CLIP_MAX_BYTES", 100)
    monkeypatch.setattr(watch_mod, "make_proxy", fake_proxy)
    fake = FakeTF([{"events": []}, {"events": []}])
    assert watch(two_window_video, tmp_path, [], fake, run_id="r", profile="entertainment") == []
    assert len(proxied) == 2 and all(mb == 100 for _, mb in proxied)
    assert "(no speech in this window)" in _user_text(fake.calls[0])
    assert "entertainment" in _user_text(fake.calls[1])


# -- fix round 1: rulings R1 and R3 ------------------------------------------------------------


def _w(text, start, end=None):
    return Word(text=text, start=start, end=start + 0.4 if end is None else end, speaker=1)


def test_said_in_dialogue_is_kept_only_when_the_words_are_said_near_it():
    """R1: the model's flag stands only when at least 30 % of the event's content words are
    said within 5 s of it; the code never sets the flag itself."""
    words = [_w("Safe", 0.2), _w("here.", 0.7), _w("Water", 30.0), _w("use", 30.5),
             _w("by", 31.0), _w("sector", 31.5), _w("is", 32.0), _w("high.", 32.5)]  # fmt: skip
    events = [
        _ve(1.0, 3.0, "action", "The person climbs out of the window onto a ledge", said=True),
        _ve(33.0, 36.0, "slide", "A slide titled Water use by sector", said=True),  # 2.5 s off
        _ve(60.0, 62.0, "slide", "A slide titled Water use by sector", said=True),  # too far
        _ve(31.0, 32.0, "slide", "Water use by sector", said=False),  # never set by code
        # 3 of 10 content words said: exactly 30 % is enough
        _ve(29.0, 33.0, "chart", "chart water use sector bars rise fall left right top",
            said=True),
    ]  # fmt: skip
    out = confirm_said(events, words)
    assert [e.said_in_dialogue for e in out] == [False, True, False, False, True]
    assert [e.text for e in out] == [e.text for e in events]


def test_the_fold_never_crosses_a_scene_cut():
    """R3: a cut between two look-alike events keeps them apart (the pipeline fixture's red
    and blue screens are a Jaccard-0.5 pair one second apart)."""
    w = _span(0, 12)
    pair = [_ve(0.0, 6.0, "setting", "A plain red screen."),
            _ve(7.0, 12.0, "setting", "A plain blue screen.")]  # fmt: skip
    assert len(merge_events([(w, pair)])) == 1  # no cut known: ruling B1 folds them
    assert len(merge_events([(w, pair)], cuts=[6.0])) == 2
    slices = [_ve(9.0, 10.0, "on_screen_text", "Tag #A on black", verb="#A"),
              _ve(10.0, 11.0, "on_screen_text", "Tag #A on black", verb="#A")]  # fmt: skip
    assert len(merge_events([(_span(30, 130), slices)], cuts=[20.0, 45.0])) == 1  # cuts elsewhere
    assert len(merge_events([(_span(30, 130), slices)], cuts=[40.0])) == 2  # absolute times
