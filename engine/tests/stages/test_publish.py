import hashlib
import json
import math
import re
import shutil
import subprocess
import wave
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import lull
from locks import assert_locked
from lull import media
from lull.ledger import CallRecord, Ledger
from lull.media import MediaError, Probe, loudness_profile
from lull.stages import mix as mixer
from lull.stages.finalize import OverlapError
from lull.stages.mix import duck_filter
from lull.stages.publish import (
    coverage,
    f113_flags,
    publish,
    timecode,
    to_extended_vtt,
    to_vtt,
    tts_disclosure,
    wcag_status,
)
from lull.stages.research import GlossaryEntry, ResearchResult
from lull.types import Claim, Cue, Gap, RunOptions, Span, VisualEvent

SUPER = "nvidia/Nemotron-3.5-Super-VL"
LIGHT = "nvidia/Nemotron-3_5-Lightning"

# -- helpers ---------------------------------------------------------------------------------


def _cue(id_, start, dur, *, gap="g001", status="verified", text="A door opens.", ev=("e1",),
         audio="x.wav", claims=(), **kw) -> Cue:  # fmt: skip
    return Cue(
        id=id_, gap_id=gap, start=start, end=None if dur is None else start + dur, text=text,
        event_ids=list(ev), status=status, ahead=False, claims=list(claims),
        audio_path=None if dur is None else audio, audio_dur=dur, **kw,
    )  # fmt: skip


def _gap(id_, us, ue) -> Gap:
    return Gap(id=id_, start=us - 0.2, end=ue + 0.2, usable_start=us, usable_end=ue,
               capacity_words=int((ue - us) * 2.36))  # fmt: skip


def _ev(id_, t0, t1, kind="action", salience=4, text="Something happens.") -> VisualEvent:
    return VisualEvent(id=id_, t_start=t0, t_end=t1, kind=kind, text=text, verbatim_text=None,
                       salience=salience, said_in_dialogue=False)  # fmt: skip


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sine_wav(path: Path, seconds: float, freq: float, amp: float, rate: int = 22050) -> Path:
    n = round(seconds * rate)
    x = amp * np.sin(2 * np.pi * freq * np.arange(n) / rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    return path


def _ledger(tmp_path: Path, run_id: str = "r1") -> Ledger:
    led = Ledger(tmp_path / "ledger.db", cap_run=None, cap_day=None, cap_total=None)
    for role, model, cost in [("watch", SUPER, 0.004), ("verify", SUPER, 0.002),
                              ("verify", SUPER, 0.002), ("repair", LIGHT, 0.0001)]:  # fmt: skip
        led.record(CallRecord(run_id=run_id, stage="s", role=role, provider="tokenfactory",
                              model=model, tokens_in=1000, tokens_cached=0, tokens_out=100,
                              latency_s=1.5, cost_usd=cost, ts=1.0))  # fmt: skip
    return led


# -- WebVTT ----------------------------------------------------------------------------------


def test_to_vtt_exact_two_cues():
    cues = [
        _cue("c002", 65.5, 1.25, gap="g002", text="Words  appear: R&D <beta> -->\nnow."),
        _cue("c001", 1.2, 2.9, text="A red door opens."),
    ]
    assert to_vtt(cues) == (
        "WEBVTT\nKind: descriptions\nLanguage: en\n"
        "\n"
        "c001\n00:00:01.200 --> 00:00:04.100\nA red door opens.\n"
        "\n"
        "c002\n00:01:05.500 --> 00:01:06.750\nWords appear: R&amp;D &lt;beta&gt; --&gt; now.\n"
    )


def test_to_vtt_only_voiced_standard_cues():
    cues = [
        _cue("c001", 1.2, 2.9),
        _cue("c002", 5.0, None, status="held", note="not verified"),
        _cue("c003", 8.0, 1.0, status="edited", text="Edited line."),
        _cue("x001", 9.0, 2.0, gap=None, status="extended"),
    ]
    out = to_vtt(cues, language="de")
    assert out.startswith("WEBVTT\nKind: descriptions\nLanguage: de\n\n")
    assert "c001" in out and "c003" in out and "Edited line." in out
    assert "c002" not in out and "x001" not in out


def test_approved_cues_are_published_and_count_as_reviewed():
    cues = [
        _cue("c001", 1.2, 2.9, status="approved", text="Approved line."),
        _cue("x001", 9.0, 2.0, gap=None, status="approved", text="Approved extended line."),
    ]
    assert "Approved line." in to_vtt(cues)
    assert "Approved extended line." not in to_vtt(cues)
    assert [c.id for c in mixer.voiced(cues)] == ["c001"]
    assert [c.id for c in mixer.voiced_extended(cues)] == ["x001"]
    assert tts_disclosure(cues).endswith("every line was reviewed before publishing.")


def test_to_vtt_without_cues_is_header_only():
    assert to_vtt([]) == "WEBVTT\nKind: descriptions\nLanguage: en\n"
    held = [_cue("c001", 1.0, None, status="held")]
    assert to_vtt(held) == "WEBVTT\nKind: descriptions\nLanguage: en\n"


def test_extended_vtt_notes_each_pause():
    cues = [
        _cue("x002", 30.0, 2.5, gap=None, status="extended", text="A chart appears."),
        _cue("x001", 12.0, 3.25, gap=None, status="extended", text="Words appear: Lull."),
        _cue("x003", 40.0, None, gap=None, status="held"),
    ]
    assert to_extended_vtt(cues) == (
        "WEBVTT\nKind: descriptions\nLanguage: en\n"
        "\n"
        "NOTE extended pause=3.250\n\nx001\n00:00:12.000 --> 00:00:15.250\nWords appear: Lull.\n"
        "\n"
        "NOTE extended pause=2.500\n\nx002\n00:00:30.000 --> 00:00:32.500\nA chart appears.\n"
    )


def test_voiced_cue_without_audio_is_an_error():
    with pytest.raises(ValueError, match="c001"):
        to_vtt([_cue("c001", 1.0, None, status="verified")])


# -- ducking ---------------------------------------------------------------------------------


def test_duck_filter_two_cues():
    cues = [_cue("c002", 5.0, 1.5, gap="g002"), _cue("c001", 1.0, 2.0)]
    assert duck_filter(cues) == (
        "volume=volume='1-0.683772*min(1,"
        "clip((t-0.800)/0.200,0,1)*clip((3.200-t)/0.200,0,1)"
        "+clip((t-4.800)/0.200,0,1)*clip((6.700-t)/0.200,0,1))'"
        ":eval=frame:enable='between(t,0.800,3.200)+between(t,4.800,6.700)'"
    )


def test_duck_filter_merges_windows_that_touch_and_handles_time_zero():
    # cues 0.3 s apart: their ramps would overlap, so the programme stays ducked between them
    cues = [_cue("c001", 0.1, 1.0), _cue("c002", 1.4, 1.0)]
    assert duck_filter(cues, duck_db=-6.0, ramp=0.25) == (
        "volume=volume='1-0.498813*min(1,"
        "clip((t+0.150)/0.250,0,1)*clip((2.650-t)/0.250,0,1))'"
        ":eval=frame:enable='between(t,-0.150,2.650)'"
    )


def test_held_cues_are_never_mixed_even_with_audio():
    held = _cue("c002", 5.0, 1.0, status="held")  # voiced once, then held
    ext = _cue("x001", 9.0, 1.0, gap=None, status="extended")
    std = [_cue("c003", 8.0, 1.0, gap="g002"), held, _cue("c001", 1.0, 1.0), ext]
    assert [c.id for c in mixer.voiced(std)] == ["c001", "c003"]
    assert [
        c.id for c in mixer.voiced_extended([held.model_copy(update={"gap_id": None}), ext])
    ] == ["x001"]


INFO_25 = Probe(10.0, 640, 360, 25.0, True, "h264", "aac")


def _freezes(ext, **kw):
    fps, freezes = mixer.freeze_points(ext, INFO_25, **kw)
    assert fps == 25
    return [(f.frame, f.frames, [c.id for c in f.cues]) for f in freezes]


def test_extended_freezes_wait_for_a_standard_cue_and_merge_close_cues():
    std = [_cue("c001", 3.5, 1.0)]  # speaks 3.5-4.5
    ext = [
        _cue("x001", 0.02, 1.0, gap=None, status="extended"),  # silence: 0.3 s on (frame 8)
        _cue("x002", 4.0, 2.0, gap=None, status="extended"),  # inside c001 -> after it (4.52)
        _cue("x003", 4.25, 1.0, gap=None, status="extended"),  # 4.55: within 0.1 s of x002's
        _cue("x004", 30.0, 1.0, gap=None, status="extended"),  # past the end -> clamped
    ]
    assert _freezes(ext, standard=std) == [
        (8, 25, ["x001"]), (113, 75, ["x002", "x003"]), (247, 25, ["x004"])
    ]  # fmt: skip


def test_extended_freezes_snap_to_speech_free_frames_and_whole_frame_pauses():
    speech = [Span(start=0.0, end=0.83), Span(start=2.013, end=3.0), Span(start=6.0, end=6.5)]
    ext = [
        _cue("x001", 0.5, 0.53, gap=None, status="extended"),  # in speech -> its end
        _cue("x002", 2.5, 0.71, gap=None, status="extended"),  # in speech -> its end (3.0)
        _cue("x003", 5.0, 1.0, gap=None, status="extended"),  # speech-free: 0.3 s on
        _cue("x004", 6.5, 0.2, gap=None, status="extended"),  # at a span's end: 0.3 s on
    ]
    got = _freezes(ext, speech=speech)
    # without words each speech span is one sentence: a freeze waits for its end, never
    # holding before its visual. x001: 0.83 -> frame 21 (0.84, rounded up); x002: 3.0 ->
    # frame 75; x003: 5.3 -> frame 133; x004: 6.8 -> frame 170.
    # pauses: 0.53 s -> 14 frames, 0.71 s -> 18 frames (rounded up to whole frames)
    assert got == [(21, 14, ["x001"]), (75, 18, ["x002"]), (133, 25, ["x003"]),
                   (170, 5, ["x004"])]  # fmt: skip
    for (frame, _, _), c in zip(got, ext, strict=True):
        t = frame / 25
        assert not any(sp.start < t < sp.end for sp in speech), f"freeze at {t} is mid-speech"
        assert t >= c.start, f"freeze at {t} comes before its visual at {c.start}"


def test_cue_gain_levels_cues_to_the_programme_within_bounds():
    assert mixer.cue_target(-30.0) == -27.0  # a quiet programme: cues no quieter than -27
    assert mixer.cue_target(-20.0) == -20.0
    assert mixer.cue_target(-8.0) == -16.0  # a loud one: cues no louder than -16
    assert mixer.cue_gain(-20.0, -10.0) == pytest.approx(-10.0)
    assert mixer.cue_gain(-30.0, -12.0) == pytest.approx(-15.0)
    assert mixer.cue_gain(-20.0, -70.0) == 0.0  # silence is not amplified
    assert mixer.cue_gain(-20.0, -65.0) == 30.0  # gains are bounded


def test_final_gain_is_linear_or_limited_never_dynamic(caplog):
    stats = {"input_i": -30.0, "input_thresh": -40.0, "input_lra": 5.0, "input_tp": -15.0}
    linear = mixer._normalise(stats)
    assert linear.startswith("loudnorm=I=-24:TP=-2:LRA=11:measured_I=-30.00")
    assert linear.endswith(":linear=true")
    peaky = stats | {"input_tp": -6.0}  # +6 dB of gain would put the true peak at 0 dBFS
    with caplog.at_level("WARNING", logger="lull.stages.mix"):
        limited = mixer._normalise(peaky)
    assert limited == "volume=6.00dB,alimiter=limit=0.7499:level=0:latency=1"
    assert "cannot stay linear" in caplog.text
    wide = stats | {"input_lra": 15.0}  # loudnorm would turn dynamic and undo the ducking
    assert mixer._normalise(wide).startswith("volume=6.00dB,alimiter")
    assert mixer._normalise(stats | {"input_i": -70.0}) == "anull"


def test_ffmpeg_major_version():
    assert mixer.ffmpeg_major("ffmpeg version 8.1.1-full_build-www.gyan.dev Copyright") == 8
    assert mixer.ffmpeg_major("ffmpeg version n7.0.2 Copyright (c) 2000-2024") == 7
    assert mixer.ffmpeg_major("ffmpeg version 6.1.1-3ubuntu5 Copyright") == 6
    assert mixer.ffmpeg_major("ffmpeg version N-113456-gabcdef Copyright") is None


def test_duck_filter_without_cues_passes_audio_through():
    assert duck_filter([]) == "anull"
    with pytest.raises(ValueError):
        duck_filter([_cue("c001", 1.0, 1.0)], ramp=0.0)


# -- report logic ----------------------------------------------------------------------------


def test_f113_flags_crafted_case():
    gaps = [
        _gap("g001", 2.0, 5.0),  # empty, slide on screen, not described anywhere -> flag
        _gap("g002", 8.0, 9.5),  # empty but under 2 s
        _gap("g003", 12.0, 15.0),  # used by a voiced cue
        _gap("g004", 18.0, 21.0),  # empty, on-screen text of salience 3 only
        _gap("g005", 24.0, 27.0),  # empty, a salient action of any kind counts too -> flag
        _gap("g006", 30.0, 33.0),  # empty, chart already described by c003
        _gap("g007", 36.0, 39.0),  # its only cue is held -> empty, scene change -> flag
    ]
    events = [
        _ev("e1", 1.0, 3.0, kind="slide", salience=5),
        _ev("e2", 8.5, 9.0, kind="on_screen_text", salience=5),
        _ev("e3", 12.5, 14.0, kind="on_screen_text", salience=5),
        _ev("e4", 18.5, 20.0, kind="on_screen_text", salience=3),
        _ev("e5", 24.5, 26.0, kind="action", salience=5),
        _ev("e6", 28.0, 31.0, kind="chart", salience=4),
        _ev("e7", 36.5, 37.0, kind="scene_change", salience=4),
        _ev("e8", 37.5, 38.0, kind="on_screen_text", salience=4),
    ]
    cues = [
        _cue("c001", 12.0, 2.0, gap="g003", ev=["e3"]),
        _cue("c003", 28.4, 1.0, gap="g000", ev=["e6"]),
        _cue("c004", 36.0, None, gap="g007", status="held", ev=["e7"]),
    ]
    flags = f113_flags(gaps, cues, events)
    assert [f["gap_id"] for f in flags] == ["g001", "g005", "g007"]
    assert flags[0]["event_ids"] == ["e1"]
    assert flags[0]["start"] == 2.0 and flags[0]["end"] == 5.0 and flags[0]["seconds"] == 3.0
    assert flags[1]["event_ids"] == ["e5"]
    assert flags[2]["event_ids"] == ["e7", "e8"]


def test_coverage_counts_salient_events_in_voiced_cues():
    events = [
        _ev("e1", 0, 1, salience=5), _ev("e2", 1, 2, salience=4), _ev("e3", 2, 3, salience=4),
        _ev("e4", 3, 4, salience=3), _ev("e5", 4, 5, salience=5),
    ]  # fmt: skip
    cues = [
        _cue("c001", 0.0, 1.0, ev=["e1", "e4"]),
        _cue("c002", 2.0, None, status="held", ev=["e2"]),
    ]
    ext = [_cue("x001", 4.0, 1.0, gap=None, status="extended", ev=["e5"])]
    cov = coverage(cues, ext, events, extended_produced=True)
    assert cov == {
        "important_events": 4,
        "salient_events": 4,  # the old name, kept for readers of older summaries
        "described": 1,
        "ratio": 0.25,
        "described_with_extended": 2,
        "ratio_with_extended": 0.5,
        "undescribed": ["e2", "e3"],
        "repetitive": [],
        "watch_salient": 4,
    }
    assert coverage(cues, ext, events, extended_produced=False)["described_with_extended"] == 1
    assert coverage([], [], [_ev("e9", 0, 1, salience=2)], extended_produced=False)["ratio"] is None


def _wcag(**kw) -> dict[str, dict]:
    args = {"standard": 3, "extended_produced": False, "held": 0, "overlap_s": 0.0,
            "f113": 0, "reviewed": True} | kw  # fmt: skip
    return {row["criterion"]: row for row in wcag_status(**args)}


def test_wcag_all_met_when_every_check_passes():
    rows = _wcag(extended_produced=True)
    assert [rows[c]["status"] for c in ("1.2.3", "1.2.5", "1.2.7")] == ["met"] * 3
    assert "descriptions.vtt" in rows["1.2.3"]["via"] and "script.md" in rows["1.2.3"]["via"]
    assert "described.mp4" in rows["1.2.5"]["via"] and "G78" in rows["1.2.5"]["via"]
    assert "extended.mp4" in rows["1.2.7"]["via"] and "G8" in rows["1.2.7"]["via"]
    assert all(rows[c]["reasons"] == [] for c in rows)


def test_wcag_says_automated_checks_until_every_line_is_reviewed():
    rows = _wcag(extended_produced=True, reviewed=False)
    assert [rows[c]["status"] for c in ("1.2.3", "1.2.5", "1.2.7")] == [
        "met (automated checks)"
    ] * 3
    rows = _wcag(reviewed=False, held=1)  # a failing check still reads "needs review"
    assert rows["1.2.5"]["status"] == "needs review"


def test_wcag_states_why_the_extended_version_is_missing():
    rows = _wcag(extended_error="libx264 failed")
    assert rows["1.2.7"]["status"] == "not claimed"
    assert rows["1.2.7"]["reasons"] == ["extended version not produced: libx264 failed"]


def test_wcag_needs_review_on_overlap_f113_or_held():
    rows = _wcag(overlap_s=0.4, f113=1, held=2)
    assert rows["1.2.5"]["status"] == "needs review"
    assert rows["1.2.3"]["status"] == "needs review"
    reasons = " ".join(rows["1.2.5"]["reasons"])
    assert "0.400 s" in reasons and "F113" in reasons and "2 descriptions held" in reasons
    assert rows["1.2.7"]["status"] == "not claimed"
    assert rows["1.2.7"]["reasons"] == ["extended version not produced"]
    rows = _wcag(extended_produced=True, held=1)
    assert rows["1.2.7"]["status"] == "needs review"  # the held cue is missing from it too
    assert rows["1.2.7"]["reasons"] == ["1 description held for review"]
    assert "extended.mp4" in rows["1.2.7"]["via"]


def test_wcag_without_a_voiced_standard_track():
    rows = _wcag(standard=0)
    assert [rows[c]["status"] for c in ("1.2.3", "1.2.5", "1.2.7")] == [
        "needs review", "needs review", "not claimed"
    ]  # fmt: skip
    assert "no description is voiced" in rows["1.2.5"]["reasons"][0]
    # continuous speech: only the extended version carries descriptions (G8)
    rows = _wcag(standard=0, extended_produced=True)
    assert rows["1.2.5"]["status"] == "met" and "extended.mp4" in rows["1.2.5"]["via"]
    assert "described.mp4" not in rows["1.2.5"]["via"]


def _cov(salient: int, described: int, with_ext: int | None = None) -> dict:
    with_ext = described if with_ext is None else with_ext
    return {"important_events": salient, "described": described,
            "ratio": round(described / salient, 3) if salient else None,
            "described_with_extended": with_ext,
            "ratio_with_extended": round(with_ext / salient, 3) if salient else None,
            "undescribed": []}  # fmt: skip


def test_wcag_met_needs_four_in_five_salient_events_described():
    """Ruling (Task 16): 1.2.5 and 1.2.7 are met only with salient coverage >= 0.8; 1.2.7
    counts the extended descriptions too. 1.2.3 does not depend on coverage."""
    rows = _wcag(extended_produced=True, coverage=_cov(10, 7, 8))
    assert rows["1.2.3"]["status"] == "met"
    assert rows["1.2.5"]["status"] == "needs review"
    assert rows["1.2.5"]["reasons"] == ["3 important events undescribed (7 of 10 described)"]
    assert rows["1.2.7"]["status"] == "met" and rows["1.2.7"]["reasons"] == []
    rows = _wcag(extended_produced=True, coverage=_cov(10, 7, 7))
    assert rows["1.2.7"]["status"] == "needs review"
    assert rows["1.2.7"]["reasons"] == ["3 important events undescribed (7 of 10 described)"]
    rows = _wcag(coverage=_cov(5, 4), reviewed=False)
    assert rows["1.2.5"]["status"] == "met (automated checks)"
    rows = _wcag(coverage=_cov(4, 3))
    assert rows["1.2.5"]["reasons"] == ["1 important event undescribed (3 of 4 described)"]
    rows = _wcag(coverage=_cov(0, 0))  # nothing salient: nothing to miss
    assert rows["1.2.5"]["status"] == "met"
    # continuous speech: the extended version is the described version, so it counts
    rows = _wcag(standard=0, extended_produced=True, coverage=_cov(5, 0, 4))
    assert rows["1.2.5"]["status"] == "met"
    rows = _wcag(standard=0, extended_produced=True, coverage=_cov(5, 0, 3))
    assert rows["1.2.5"]["reasons"] == ["2 important events undescribed (3 of 5 described)"]


def test_events_the_dialogue_conveys_are_never_counted_undescribed():
    """Events left out of the plan because the dialogue already says them (R21: said in
    dialogue, salience under 5) are neither salient for coverage nor F113 candidates."""
    events = [
        _ev("e1", 1.0, 3.0, salience=4).model_copy(update={"said_in_dialogue": True}),
        _ev("e2", 1.0, 3.0, salience=5).model_copy(update={"said_in_dialogue": True}),
        _ev("e3", 7.0, 9.0, salience=4).model_copy(update={"said_in_dialogue": True}),
    ]
    cov = coverage([], [], events, extended_produced=False)
    assert cov["important_events"] == 1 and cov["undescribed"] == ["e2"]
    flags = f113_flags([_gap("g001", 0.5, 4.0), _gap("g002", 6.5, 9.5)], [], events)
    assert [f["event_ids"] for f in flags] == [["e2"]]


def test_a_duplicate_the_plan_dropped_counts_with_its_twin():
    events = [_ev("e1", 1.0, 3.0, salience=5), _ev("e2", 6.0, 8.0, salience=5),
              _ev("e3", 12.0, 13.0, salience=4)]  # fmt: skip
    cues = [_cue("c001", 1.0, 1.0, ev=["e1"])]
    dup = {"e2": "e1", "e3": "e9"}
    cov = coverage(cues, [], events, extended_produced=False, duplicates=dup)
    assert (cov["described"], cov["undescribed"]) == (2, ["e3"])
    gaps = [_gap("g001", 1.0, 4.0), _gap("g002", 5.5, 9.0), _gap("g003", 11.5, 14.0)]
    flags = f113_flags(gaps, cues, events, duplicates=dup)
    assert [f["gap_id"] for f in flags] == ["g003"]


def test_tts_disclosure_says_reviewed_only_when_every_cue_was_edited():
    edited = [_cue("c001", 1.0, 1.0, status="edited")]
    assert tts_disclosure(edited) == (
        "Descriptions are voiced with synthetic speech (NVIDIA Magpie TTS); "
        "every line was reviewed before publishing."
    )
    mixed = edited + [_cue("c002", 3.0, 1.0, status="verified")]
    assert tts_disclosure(mixed).endswith("lines are awaiting review.")
    assert "NVIDIA Magpie TTS" in tts_disclosure(mixed)
    held_too = edited + [_cue("c003", 5.0, None, status="held")]  # held lines are not published
    assert tts_disclosure(held_too).endswith("every line was reviewed before publishing.")
    approved = [_cue("c001", 1.0, 1.0, status="approved"),
                _cue("x001", 9.0, 1.0, gap=None, status="edited")]  # fmt: skip
    assert tts_disclosure(approved).endswith("every line was reviewed before publishing.")
    unreviewed_ext = approved + [_cue("x002", 12.0, 1.0, gap=None, status="extended")]
    assert tts_disclosure(unreviewed_ext).endswith("lines are awaiting review.")
    assert tts_disclosure([]) == "No description was voiced in this run."
    assert tts_disclosure([_cue("c004", 5.0, None, status="held")]) == (
        "No description was voiced in this run."
    )


# -- publish without media (fakes) -----------------------------------------------------------


@pytest.fixture
def fake_media(monkeypatch):
    """mix, extended video and keyframes write placeholder files (no ffmpeg)."""
    calls: dict[str, list] = {"mix": [], "extended": [], "frames": []}

    def fake_mix(video, cues, out_dir, **kw):
        calls["mix"].append([c.id for c in cues])
        audio, vid = out_dir / mixer.AD_AUDIO, out_dir / mixer.AD_VIDEO
        audio.write_bytes(b"m4a")
        vid.write_bytes(b"mp4")
        return mixer.MixResult(ad_audio=audio, ad_video=vid)

    def fake_extended(video, cues_extended, out_dir, **kw):
        calls["extended"].append([c.id for c in cues_extended])
        if calls.get("extended_fails"):
            (out_dir / mixer.EXTENDED_VIDEO).write_bytes(b"half")
            raise MediaError("ffmpeg failed (exit 1): libx264 exploded")
        return None

    def fake_frames(src, shots, **kw):
        calls["frames"].append([(round(t, 3), Path(d).name) for t, d in shots])
        for _, d in shots:
            Path(d).write_bytes(b"\xff\xd8jpg")
        return [Path(d) for _, d in shots]

    def fake_levels(video, info, cues):
        return mixer.Levels(programme=-23.0, cues={str(c.audio_path): -20.0 for c in cues})

    monkeypatch.setattr(media, "probe", lambda p: media.Probe(60.0, 640, 360, 25.0, True, "h264"))
    monkeypatch.setattr(mixer, "measure_levels", fake_levels)
    monkeypatch.setattr(mixer, "mix", fake_mix)
    monkeypatch.setattr(mixer, "extended_video", fake_extended)
    monkeypatch.setattr(media, "frames_at", fake_frames)
    return calls


def test_publish_refuses_on_overlap_and_writes_nothing(tmp_path, fake_media):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video")
    gaps = [_gap("g001", 2.0, 5.0), _gap("g002", 8.0, 10.0)]
    cues = [
        _cue("c001", 2.0, 2.5),
        _cue("c002", 8.0, 2.4, gap="g002"),  # runs 0.4 s past its pause into speech
    ]
    run_dir = tmp_path / "run"
    args = ([_ev("e1", 2, 4)], gaps, None, _ledger(tmp_path), "r1", RunOptions())
    with pytest.raises(OverlapError) as err:
        publish(run_dir, video, cues, [], *args, speech=[])
    assert err.value.cue_ids == ["c002"]
    # speech the gaps do not show (inside a usable window) is checked too
    with pytest.raises(OverlapError) as err:
        publish(run_dir, video, cues[:1], [], *args, speech=[Span(start=4.0, end=4.2)])
    assert err.value.cue_ids == ["c001"]
    assert not run_dir.exists()
    assert fake_media["mix"] == [] and fake_media["frames"] == []


def test_publish_with_an_empty_standard_track(tmp_path, fake_media):
    """Continuous speech: no standard cue at all, publish still succeeds (Review Focus 2)."""
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video bytes")
    events = [_ev("e1", 10.0, 14.0, kind="slide", salience=5, text="A slide titled Results.")]
    ext = [_cue("x001", 10.0, None, gap=None, status="held", ev=["e1"], note="not verified")]
    run = tmp_path / "run"
    (run / "keyframes").mkdir(parents=True)  # left by an earlier run of the same directory
    (run / "keyframes" / "c009.jpg").write_bytes(b"old")
    (run / "extended.mp4").write_bytes(b"old")
    (run / ".mix-old").mkdir()
    res = publish(run, video, [], ext, events, [], None, _ledger(tmp_path), "r1", RunOptions(),
                  speech=[Span(start=0.0, end=60.0)])  # fmt: skip
    assert not (run / "extended.mp4").exists() and not (run / ".mix-old").exists()
    assert sorted(p.name for p in (run / "keyframes").iterdir()) == ["x001.jpg"]
    assert (run / "descriptions.vtt").read_text(encoding="utf-8") == (
        "WEBVTT\nKind: descriptions\nLanguage: en\n"
    )
    assert (run / "extended.vtt").read_text(encoding="utf-8") == (
        "WEBVTT\nKind: descriptions\nLanguage: en\n"
    )
    assert fake_media["mix"] == [[]]
    assert fake_media["frames"] == [[(12.0, "x001.jpg")]]
    assert "extended.mp4" not in res.files
    manifest = json.loads(res.manifest.read_text(encoding="utf-8"))
    rows = {r["criterion"]: r["status"] for r in manifest["summary"]["wcag"]}
    assert rows == {"1.2.3": "needs review", "1.2.5": "needs review", "1.2.7": "not claimed"}
    assert manifest["summary"]["cues"] == {
        "verified": 0, "edited": 0, "approved": 0, "held": 1, "extended": 0,
    }  # fmt: skip
    assert manifest["summary"]["held"] == [{"id": "x001", "reason": "not verified"}]
    html = (run / "report.html").read_text(encoding="utf-8")
    assert "extended version not produced" in html
    assert "research skipped" in html
    assert "No description was voiced in this run." in html


def test_publish_report_and_manifest_contents(tmp_path, fake_media):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"lecture video")
    gaps = [_gap("g001", 2.0, 5.0), _gap("g002", 20.0, 23.0)]
    events = [
        _ev("e1", 2.0, 4.0, kind="slide", salience=5, text="A slide."),
        _ev("e2", 20.5, 22.0, kind="on_screen_text", salience=4, text="A caption."),
    ]
    finding = {"rule": "R14-gender", "message": "gendered word 'man'", "span": [2, 5],
               "severity": "warning"}  # fmt: skip
    audio = tmp_path / "run" / "work" / "audio" / "c001.wav"
    cues = [
        _cue("c001", 2.0, 2.5, text="A man & <a> slide.", ev=["e1"], findings=[finding],
             claims=[Claim(text="a\nslide", verdict="supported", at=2.5)],
             note="descriptor added\nfor Proog", audio=str(audio)),
        _cue("c002", 20.0, None, gap="g002", status="held", ev=["e2"],
             note="not verified after 4 rounds: a caption (not visible)"),
    ]  # fmt: skip
    research = ResearchResult(
        glossary=[GlossaryEntry(term="proog", canonical="Proog", note="a character",
                                sources=["https://example.org/proog", "javascript:alert(1)"])],
        skipped=False,
    )  # fmt: skip
    fake_media["extended_fails"] = True
    ext = [_cue("x001", 30.0, 1.0, gap=None, status="extended", ev=["e2"])]
    res = publish(tmp_path / "run", video, cues, ext, events, gaps, research, _ledger(tmp_path),
                  "r1", RunOptions(voice="Sofia"), speech=[Span(start=0.0, end=1.8)])  # fmt: skip
    run = tmp_path / "run"
    names = set(res.files)
    assert {"descriptions.vtt", "extended.vtt", "cues.json", "script.md", "report.html",
            "described-audio.m4a", "described.mp4", "keyframes/c001.jpg",
            "keyframes/c002.jpg", "keyframes/x001.jpg"} == names  # fmt: skip
    assert not (run / "extended.mp4").exists()  # the failed extended version is not shipped
    m = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert res.manifest == run / "manifest.json"
    assert m["run_id"] == "r1"
    assert m["video"] == {"name": "lecture.mp4", "sha256": _sha(video), "duration": 60.0}
    assert set(m["outputs"]) == names
    for name, meta in m["outputs"].items():
        assert meta == {"sha256": _sha(run / name), "bytes": (run / name).stat().st_size}
    s = m["summary"]
    assert s["cues"] == {"verified": 1, "edited": 0, "approved": 0, "held": 1, "extended": 1}
    assert s["coverage"]["described"] == 1 and s["coverage"]["important_events"] == 2
    assert s["overlap_seconds"] == 0.0
    assert s["overlap"] == {"speech_s": 0.0, "between_cues_s": 0.0, "outside_pauses_s": 0.0,
                            "total_s": 0.0}  # fmt: skip
    assert s["extended_produced"] is False and "libx264 exploded" in s["extended_error"]
    rows = {r["criterion"]: r for r in s["wcag"]}
    assert rows["1.2.7"]["status"] == "not claimed"
    assert "libx264 exploded" in rows["1.2.7"]["reasons"][0]
    assert s["reviewed"] is False
    assert s["levels"] == {"programme_lufs": -23.0, "cue_target_lufs": -23.0}
    assert [f["gap_id"] for f in s["f113"]] == ["g002"]
    assert s["cost_usd"] == pytest.approx(0.0081)
    assert s["cost_per_min"] == pytest.approx(0.0081)
    by = {(g["role"], g["model"]): g for g in s["calls_by_role_model"]}
    assert by[("verify", SUPER)]["calls"] == 2
    assert m["versions"]["engine"] == lull.__version__
    assert m["versions"]["models"] == sorted([SUPER, LIGHT])
    assert m["options"]["voice"] == "Sofia"

    html = (run / "report.html").read_text(encoding="utf-8")
    assert "A man &amp; &lt;a&gt; slide." in html and "<a> slide" not in html
    assert "R14-gender" in html and "gendered word" in html
    assert "not verified after 4 rounds" in html
    assert '<a href="https://example.org/proog">' in html and "Proog" in html
    assert "javascript:alert(1)" in html and 'href="javascript' not in html  # text, not a link
    assert "overlap with speech" in html and "0.000" in html
    assert "awaiting review" in html and "NVIDIA Magpie TTS" in html
    assert "needs review" in html and "not claimed" in html
    assert "http" not in html.replace("https://example.org/proog", "")  # self-contained
    assert "libx264 exploded" in html

    script = (run / "script.md").read_text(encoding="utf-8")
    assert "00:00:02.000" in script and "verified" in script and "held" in script
    assert "a slide — supported" in script and "R14-gender" in script
    assert "descriptor added for Proog" in script
    assert "|" not in script  # no tables

    data = json.loads((run / "cues.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in data["cues"]] == ["c001", "c002"]
    assert [c["id"] for c in data["extended"]] == ["x001"]
    assert data["cues"][0]["audio_path"] == "work/audio/c001.wav"  # relative to the run
    ext_vtt = (run / "extended.vtt").read_text(encoding="utf-8")
    assert "x001\n00:00:30.320 --> 00:00:31.320\n" in ext_vtt  # written regardless (0.3 s on)


def test_cues_json_carries_the_timeline_the_studio_and_the_site_draw(tmp_path, fake_media):
    """cues.json also holds the pauses (the windows the scheduler filled), the speech spans
    and, for each voiced extended cue, its freeze point."""
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"lecture video")
    gaps = [_gap("g001", 2.0, 5.0), _gap("g002", 20.0, 23.0)]
    events = [_ev("e1", 2.0, 4.0), _ev("e2", 11.0, 12.0)]
    cues = [_cue("c001", 2.0, 1.5, ev=["e1"], audio="c.wav")]
    ext = [_cue("x002", 11.0, 1.0, gap=None, status="extended", ev=["e2"], audio="x.wav"),
           _cue("x003", 13.0, None, gap=None, status="held", ev=["e2"])]  # fmt: skip
    speech = [Span(start=0.0, end=1.8), Span(start=10.0, end=12.5)]
    run = tmp_path / "run"
    publish(run, video, cues, ext, events, gaps, None, _ledger(tmp_path), "r1", RunOptions(),
            speech=speech)  # fmt: skip
    data = json.loads((run / "cues.json").read_text(encoding="utf-8"))
    assert data["gaps"] == [g.model_dump(mode="json") for g in gaps]
    assert data["speech"] == [{"start": 0.0, "end": 1.8}, {"start": 10.0, "end": 12.5}]
    assert [(c["id"], c.get("freeze_at")) for c in data["extended"]] == [
        ("x002", 12.52), ("x003", None)  # x002 waits for the speech under its visual to end
    ]  # fmt: skip
    assert "freeze_at" not in data["cues"][0]


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_a_failed_publish_leaves_the_previous_outputs_untouched(tmp_path, fake_media, monkeypatch):
    """Publish is atomic: everything is written into a staging folder first and moved into
    place only when every output and the manifest are done."""
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"lecture video")
    gaps = [_gap("g001", 2.0, 5.0)]
    events = [_ev("e1", 2.0, 4.0, kind="slide", salience=5)]
    cues = [_cue("c001", 2.0, 1.5, ev=["e1"], audio=str(tmp_path / "c001.wav"))]
    ext = [_cue("x001", 2.5, 1.0, gap=None, status="extended", ev=["e1"],
                audio=str(tmp_path / "x001.wav"))]  # fmt: skip
    run = tmp_path / "run"
    args = (events, gaps, None, _ledger(tmp_path), "r1", RunOptions())
    published = publish(run, video, cues, ext, *args, speech=[])
    before = _snapshot(run)
    assert "manifest.json" in before and "cues.json" in before and "keyframes/c001.jpg" in before

    def broken_mix(*a, **kw):
        raise MediaError("ffmpeg failed (exit 1): the mix broke")

    monkeypatch.setattr(mixer, "mix", broken_mix)
    other = [_cue("c001", 2.0, 1.2, ev=["e1"], text="Another line.", audio="y.wav")]
    with pytest.raises(MediaError, match="the mix broke"):
        publish(run, video, other, [], *args, speech=[])
    assert _snapshot(run) == before  # byte for byte, nothing added or removed
    assert published.manifest.read_bytes() == before["manifest.json"]


def test_a_new_publish_replaces_the_outputs_and_drops_the_ones_it_no_longer_makes(
    tmp_path, fake_media, monkeypatch
):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"lecture video")
    gaps = [_gap("g001", 2.0, 5.0)]
    events = [_ev("e1", 2.0, 4.0, kind="slide", salience=5)]
    run = tmp_path / "run"
    args = (events, gaps, None, _ledger(tmp_path), "r1", RunOptions())
    (run / "notes.txt").parent.mkdir(parents=True)
    (run / "notes.txt").write_text("mine", encoding="utf-8")  # not an output: left alone

    def with_extended(video, cues_extended, out_dir, **kw):
        if not cues_extended:
            return None
        dst = out_dir / mixer.EXTENDED_VIDEO
        dst.write_bytes(b"extended")
        return dst

    monkeypatch.setattr(mixer, "extended_video", with_extended)
    ext = [_cue("x001", 2.5, 1.0, gap=None, status="extended", ev=["e1"], audio="x.wav")]
    cues = [_cue("c001", 2.0, 1.5, ev=["e1"], audio="c.wav")]
    first = publish(run, video, cues, ext, *args, speech=[])
    assert (run / "extended.mp4").exists() and "extended.mp4" in first.files
    assert sorted(p.name for p in (run / "keyframes").iterdir()) == ["c001.jpg", "x001.jpg"]
    second = publish(run, video, cues, [], *args, speech=[])
    assert not (run / "extended.mp4").exists() and "extended.mp4" not in second.files
    assert sorted(p.name for p in (run / "keyframes").iterdir()) == ["c001.jpg"]
    assert (run / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert [p.name for p in run.iterdir() if p.name.startswith(".")] == []
    m = json.loads(second.manifest.read_text(encoding="utf-8"))
    assert set(m["outputs"]) == set(second.files)
    for name, path in second.files.items():
        assert path == run / name and path.exists()


# -- real ffmpeg -----------------------------------------------------------------------------

PROGRAMME_HZ = 440.0
CUE_HZ = 3000.0
FPS = 25
FRAME = 1 / FPS
# A quiet programme (about -29 LUFS: the -27 floor for cues then sits 2 LU above it) and loud
# cues (about +2 LUFS before levelling).
PROGRAMME_VOLUME = 0.43
CUE_AMP = 0.9
SPEECH = [Span(start=0.61, end=1.3), Span(start=3.6, end=4.3)]


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    """One real publish on a 6 s, 25 fps fixture.

    c001 (verified, 1 s) at 2.0 s; c002 held; x001 (0.53 s) for a visual at 1.0 s and x002
    (0.71 s) for one at 4.0 s, both mid-speech: each freezes 0.3 s after its visual, where its
    speech has ended (1.32 s and 4.32 s, on frames), for 14 and 18 frames (0.56 s, 0.72 s).
    Extended timeline: x001 at 1.32, c001 at 2.56, x002 at 4.88,
    7.28 s in all.
    """
    root = tmp_path_factory.mktemp("publish")
    video = root / "fixture.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"color=c=gray:s=320x180:r={FPS}:d=6",
         "-f", "lavfi", "-i",
         f"sine=frequency={PROGRAMME_HZ}:duration=6,volume={PROGRAMME_VOLUME}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "6", str(video)],
        check=True, capture_output=True,
    )  # fmt: skip
    wavs = {
        name: _sine_wav(root / f"{name}.wav", dur, CUE_HZ, CUE_AMP)
        for name, dur in (("c001", 1.0), ("x001", 0.53), ("x002", 0.71))
    }
    gaps = [_gap("g001", 2.0, 3.5), _gap("g002", 4.6, 5.8)]
    events = [
        _ev("e1", 1.5, 3.5, kind="slide", salience=5),
        _ev("e2", 4.6, 5.5, kind="action", salience=4),
        _ev("e3", 1.0, 1.4, kind="on_screen_text", salience=5),
        _ev("e4", 4.0, 4.4, kind="on_screen_text", salience=5),
    ]
    cues = [
        _cue("c001", 2.0, 1.0, ev=["e1"], audio=str(wavs["c001"])),
        _cue("c002", 4.6, None, gap="g002", status="held", ev=["e2"], note="not verified"),
    ]
    ext = [
        _cue("x001", 1.0, 0.53, gap=None, status="extended", ev=["e3"], audio=str(wavs["x001"])),
        _cue("x002", 4.0, 0.71, gap=None, status="extended", ev=["e4"], audio=str(wavs["x002"])),
    ]
    run = root / "run"
    commands: list[list[str]] = []
    real = subprocess.run

    def spy(cmd, *args, **kwargs):
        commands.append([str(c) for c in cmd])
        return real(cmd, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(media.subprocess, "run", spy)
        res = publish(run, video, cues, ext, events, gaps, None, _ledger(root), "r1",
                      RunOptions(), speech=SPEECH)  # fmt: skip
    return {"video": video, "run": run, "res": res, "root": root, "commands": commands}


def test_publish_reads_every_input_as_a_local_file(published):
    """The probe, levels, mix, extended version and keyframes all open their inputs with
    -protocol_whitelist file,pipe (E-sec)."""
    ffmpeg = [c for c in published["commands"] if Path(c[0]).stem.lower() == "ffmpeg"]
    assert len(ffmpeg) >= 4  # levels, premix + mix, premix + extended, keyframes
    for cmd in published["commands"]:
        if "-version" in cmd:
            continue  # the version check reads no file
        assert_locked(cmd)


def _streams(path: Path) -> tuple[list[dict], float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,duration:stream_tags:stream_disposition=default"
         ":format=duration", "-of", "json", str(path)],
        check=True, capture_output=True,
    )  # fmt: skip
    info = json.loads(out.stdout)
    return info["streams"], float(info["format"]["duration"])


def _mean_db(profile, windows, hop=0.1) -> float:
    vals = [db for t, db in profile if any(a <= t and t + hop <= b for a, b in windows)]
    power = sum(10 ** (v / 10) for v in vals) / len(vals)
    return 10 * math.log10(power)


def _onset(profile, after: float, *, drop_db: float = 20.0) -> float:
    """The first window at or after `after` within `drop_db` of the loudest window."""
    peak = max(db for _, db in profile)
    return next(t for t, db in profile if t >= after and db >= peak - drop_db)


def _analyse(src: Path, root: Path, windows=()) -> tuple[Path, Path, dict, list[float]]:
    """The programme band (<1 kHz) and the cue band (>2 kHz) as 16-bit WAVs, the loudness of
    the whole signal and the integrated loudness of each (start, end) window, in one run."""
    lo, hi = root / f"{src.stem}.lo.wav", root / f"{src.stem}.hi.wav"
    n = 3 + len(windows)
    graph = [f"[0:a]asplit={n}[a][b][c]" + "".join(f"[w{k}]" for k in range(len(windows))),
             "[a]lowpass=f=1000,lowpass=f=1000[lo]", "[b]highpass=f=2000,highpass=f=2000[hi]",
             "[c]ebur128=peak=true:framelog=verbose[r]"]  # fmt: skip
    graph += [
        f"[w{k}]atrim=start={a}:end={b},ebur128=framelog=verbose[r{k}]"
        for k, (a, b) in enumerate(windows)
    ]
    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(src),
           "-filter_complex", ";".join(graph),
           "-map", "[lo]", "-ac", "1", "-c:a", "pcm_s16le", str(lo),
           "-map", "[hi]", "-ac", "1", "-c:a", "pcm_s16le", str(hi),
           "-map", "[r]", "-f", "null", "-"]  # fmt: skip
    for k in range(len(windows)):
        cmd += ["-map", f"[r{k}]", "-f", "null", "-"]
    proc = subprocess.run(cmd, check=True, capture_output=True)
    blocks = mixer._summaries(proc.stderr.decode("utf-8", "replace"))
    whole = mixer._parse(blocks[0])
    return lo, hi, whole, [mixer._parse(b, ["input_i"])["input_i"] for b in blocks[1:]]


def test_mix_levels_and_ducks_the_programme_under_the_cue(published):
    inside, outside = (2.1, 2.9), (3.5, 5.7)
    lo, hi, loud, (cue_lufs, programme_lufs) = _analyse(
        published["run"] / "described-audio.m4a", published["root"], [inside, outside]
    )
    assert loud["input_i"] == pytest.approx(-24.0, abs=0.5)  # normalised to -24 LUFS
    assert loud["input_tp"] <= -2.0
    with wave.open(str(lo), "rb") as w:
        assert w.getframerate() == 48000
        assert w.getnframes() / w.getframerate() == pytest.approx(6.0, abs=0.1)
    programme = loudness_profile(lo)
    drop = _mean_db(programme, [(0.3, 1.5), outside]) - _mean_db(programme, [inside])
    print(f"programme {drop:.2f} dB quieter under the cue; cue window {cue_lufs:.1f} LUFS, "
          f"programme {programme_lufs:.1f} LUFS")  # fmt: skip
    assert 8.0 <= drop <= 12.0, f"programme {drop:.1f} dB quieter under the cue"
    voice = loudness_profile(hi)
    assert _mean_db(voice, [inside]) - _mean_db(voice, [outside]) >= 30.0  # the cue is placed
    # the loud cue was levelled to the quiet programme (R5)
    assert abs(cue_lufs - programme_lufs) <= 3.0
    m = json.loads(published["res"].manifest.read_text(encoding="utf-8"))
    assert m["summary"]["levels"]["programme_lufs"] == pytest.approx(-29.0, abs=1.0)
    assert m["summary"]["levels"]["cue_target_lufs"] == -27.0


def test_described_mp4_has_described_and_original_tracks(published):
    streams, duration = _streams(published["run"] / "described.mp4")
    assert duration == pytest.approx(6.0, abs=0.15)
    video = [s for s in streams if s["codec_type"] == "video"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    assert len(video) == 1 and video[0]["codec_name"] == "h264"  # copied
    assert [s["tags"]["handler_name"] for s in audio] == ["Described", "Original"]
    assert [s["tags"]["language"] for s in audio] == ["eng", "eng"]
    assert [s["disposition"]["default"] for s in audio] == [1, 0]


def test_extended_mp4_stays_in_sync_across_two_pauses(published):
    res = published["res"]
    assert "extended.mp4" in res.files
    streams, _ = _streams(res.files["extended.mp4"])
    assert [s["codec_type"] for s in streams] == ["video", "audio"]
    assert streams[1]["tags"]["handler_name"] == "Described (extended)"
    expected = 6.0 + 14 * FRAME + 18 * FRAME  # 7.28 s: pauses rounded up to whole frames
    v_dur, a_dur = (float(s["duration"]) for s in streams)
    assert v_dur == pytest.approx(expected, abs=FRAME)
    assert a_dur == pytest.approx(v_dur, abs=FRAME)
    print(f"extended: video {v_dur:.3f} s, audio {a_dur:.3f} s, expected {expected:.3f} s")

    lo, hi, loud, _ = _analyse(res.files["extended.mp4"], published["root"])
    assert loud["input_i"] == pytest.approx(-24.0, abs=0.5)
    voice = loudness_profile(hi, hop=0.005)
    for target in (1.32, 2.56, 4.88):  # x001, c001 (moved by x001's pause), x002
        onset = _onset(voice, target - 0.3)
        print(f"extended: cue wanted at {target:.2f} s, starts at {onset:.3f} s")
        assert onset == pytest.approx(target, abs=FRAME), f"cue at {onset}, wanted {target}"
    programme = loudness_profile(lo)
    playing = _mean_db(programme, [(0.1, 1.25), (5.7, 7.1)])
    for pause in ((1.37, 1.83), (4.93, 5.55)):  # the programme is silent while the picture holds
        assert playing - _mean_db(programme, [pause]) >= 30.0


def test_published_files_match_manifest(published):
    run, res = published["run"], published["res"]
    m = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert set(m["outputs"]) == set(res.files)
    for name in ("described-audio.m4a", "described.mp4", "extended.mp4", "keyframes/c001.jpg",
                 "keyframes/c002.jpg", "keyframes/x001.jpg", "keyframes/x002.jpg",
                 "descriptions.vtt"):  # fmt: skip
        path = run / name
        assert m["outputs"][name] == {"sha256": _sha(path), "bytes": path.stat().st_size}
    for name in ("keyframes/c001.jpg", "keyframes/x001.jpg"):
        assert (run / name).read_bytes()[:2] == b"\xff\xd8"
    rows = {r["criterion"]: r["status"] for r in m["summary"]["wcag"]}
    assert rows == {"1.2.3": "needs review", "1.2.5": "needs review", "1.2.7": "needs review"}
    assert m["summary"]["held"] == [{"id": "c002", "reason": "not verified"}]
    assert m["video"]["duration"] == pytest.approx(6.0, abs=0.1)
    vtt = (run / "descriptions.vtt").read_text(encoding="utf-8")
    assert "c001\n00:00:02.000 --> 00:00:03.000\n" in vtt
    ext_vtt = (run / "extended.vtt").read_text(encoding="utf-8")
    assert "NOTE extended pause=0.530\n\nx001\n00:00:01.320 --> 00:00:01.850\n" in ext_vtt
    assert "NOTE extended pause=0.710\n\nx002\n00:00:04.320 --> 00:00:05.030\n" in ext_vtt
    assert [p.name for p in run.iterdir() if p.name.startswith(".")] == []  # no temp dirs left


# -- Task 14 minors ------------------------------------------------------------------------------


def test_a_freeze_pulled_back_from_the_end_never_lands_in_speech():
    """The last freeze is kept MIN_SEGMENT inside the video; when that moves it into speech it
    goes back to where the speech starts."""
    speech = [Span(start=9.7, end=9.95)]
    ext = [_cue("x001", 9.98, 0.4, gap=None, status="extended")]
    [(frame, _, _)] = _freezes(ext, speech=speech)
    assert frame == 242  # 9.68 s, just before the speech; 9.88 s would be mid-word
    assert not any(sp.start < frame / 25 < sp.end for sp in speech)


def test_the_frame_rate_comes_from_the_probed_fraction():
    ntsc = Probe(10.0, 640, 360, 29.97, True, "h264", "aac", fps_rational="30000/1001")
    assert mixer.frame_rate(ntsc) == Fraction(30000, 1001)  # the float would give 2997/100
    assert mixer.frame_rate(Probe(10.0, 640, 360, 29.97, True, "h264")) == Fraction(2997, 100)
    odd = Probe(10.0, 640, 360, 25.0, True, "h264", fps_rational="0/0")
    assert mixer.frame_rate(odd) == 25


def test_probe_keeps_the_frame_rate_fraction(tone_slides):
    assert media.probe(tone_slides).fps_rational == "30/1"


def test_a_mix_without_a_voiced_line_is_titled_as_the_original(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=gray:s=160x90:r=25:d=2", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-t", "2", str(video)],
                   check=True, capture_output=True)  # fmt: skip
    held = [_cue("c001", 0.5, None, status="held")]
    res = mixer.mix(video, held, tmp_path / "out")
    for path in (res.ad_audio, res.ad_video):
        streams, _ = _streams(path)
        titles = [s["tags"]["handler_name"] for s in streams if s["codec_type"] == "audio"]
        assert titles[0] == "Original (no description)", (path.name, titles)
        assert "Described" not in titles


def test_the_reason_the_extended_version_failed_names_no_local_path(
    tmp_path, fake_media, monkeypatch
):
    def broken(video, cues_extended, out_dir, **kw):
        raise MediaError(
            r"ffmpeg failed (exit 1): C:\Users\someone\runs\talk\.extended-x1\premix.wav:"
            " Invalid data; /home/someone/runs/talk/work/x001.wav missing; \\\\host\\share\\a.mp4"
        )

    monkeypatch.setattr(mixer, "extended_video", broken)
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video")
    ext = [_cue("x001", 2.5, 1.0, gap=None, status="extended", audio="x.wav")]
    res = publish(tmp_path / "run", video, [], ext, [_ev("e1", 2.0, 4.0)], [], None,
                  _ledger(tmp_path), "r1", RunOptions(), speech=[])  # fmt: skip
    reason = json.loads(res.manifest.read_text(encoding="utf-8"))["summary"]["extended_error"]
    assert reason == ("ffmpeg failed (exit 1): premix.wav: Invalid data; x001.wav missing; a.mp4")


# -- fix round 1: rollback during the move, one publisher at a time ---------------------------


def _tree(root: Path) -> tuple[dict[str, bytes], list[str]]:
    """Every file's bytes and every entry's name (empty folders included)."""
    return _snapshot(root), sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def _publish_args(tmp_path: Path):
    video = tmp_path / "lecture.mp4"
    video.write_bytes(b"lecture video")
    gaps = [_gap("g001", 2.0, 5.0)]
    events = [_ev("e1", 2.0, 4.0, kind="slide", salience=5)]
    return video, (events, gaps, None, _ledger(tmp_path), "r1", RunOptions())


def test_a_failure_while_moving_the_outputs_in_rolls_everything_back(
    tmp_path, fake_media, monkeypatch
):
    """I2: the old outputs move to a backup folder, the new ones move in, the backup goes only
    after success. A move that fails at any point puts every old file back in place."""
    from lull.stages import publish as pub

    video, args = _publish_args(tmp_path)
    run = tmp_path / "run"
    cues = [_cue("c001", 2.0, 1.5, ev=["e1"], audio="c.wav")]
    other = [_cue("c001", 2.0, 1.2, ev=["e1"], text="Another line.", audio="y.wav")]
    real = pub._move
    failed_at = []
    for n in range(1, 40):
        publish(run, video, cues, [], *args, speech=[])  # a good publish to fall back to
        before = _tree(run)
        calls = {"n": 0}

        def flaky(src, dst, _n=n, _calls=calls):
            _calls["n"] += 1
            if _calls["n"] == _n:
                raise OSError(f"disk said no on move {_n}")
            return real(src, dst)

        monkeypatch.setattr(pub, "_move", flaky)
        try:
            publish(run, video, other, [], *args, speech=[])
        except OSError:
            failed_at.append(n)
            assert _tree(run) == before, f"not rolled back after a failure on move {n}"
        else:
            break  # n is past the last move: the publish went through
        finally:
            monkeypatch.setattr(pub, "_move", real)
    assert len(failed_at) >= 10  # the backup moves and the moves in were all tried
    assert "Another line." in (run / "descriptions.vtt").read_text(encoding="utf-8")


def test_publishes_of_one_run_take_turns(tmp_path, fake_media, monkeypatch):
    """M6: a publish holds run_dir/.publish.lock (created exclusively); another one waits for
    it instead of clearing the first one's staging folder."""
    import threading
    import time

    from lull.stages import publish as pub

    monkeypatch.setattr(pub, "LOCK_POLL_S", 0.05)
    video, args = _publish_args(tmp_path)
    run = tmp_path / "run"
    run.mkdir()
    lock = run / ".publish.lock"
    lock.write_text("someone else", encoding="utf-8")
    busy = run / ".publish-busy"  # the other publish's staging folder
    busy.mkdir()

    def finish() -> None:
        time.sleep(0.4)
        shutil.rmtree(busy)
        lock.unlink()

    t = threading.Thread(target=finish)
    t0 = time.monotonic()
    t.start()
    publish(run, video, [_cue("c001", 2.0, 1.5, ev=["e1"], audio="c.wav")], [], *args, speech=[])
    t.join()
    assert time.monotonic() - t0 >= 0.35  # it waited its turn
    assert (run / "manifest.json").exists() and not lock.exists()


def test_a_stale_publish_lock_is_broken(tmp_path, fake_media, caplog):
    import os
    import time

    video, args = _publish_args(tmp_path)
    run = tmp_path / "run"
    run.mkdir()
    lock = run / ".publish.lock"
    lock.write_text("crashed", encoding="utf-8")
    old = time.time() - 11 * 60
    os.utime(lock, (old, old))
    (run / ".publish-crashed").mkdir()  # its staging folder
    with caplog.at_level("WARNING", logger="lull.stages.publish"):
        publish(run, video, [], [], *args, speech=[])
    assert "stale" in caplog.text
    assert not lock.exists() and not (run / ".publish-crashed").exists()


def test_a_backup_left_by_a_crash_is_put_back_before_the_next_publish(tmp_path):
    from lull.stages import publish as pub

    run = tmp_path / "run"
    (run / ".publish-old-x1").mkdir(parents=True)
    (run / ".publish-old-x1" / "manifest.json").write_text("old manifest", encoding="utf-8")
    (run / ".publish-old-x1" / "report.html").write_text("old report", encoding="utf-8")
    (run / "report.html").write_text("new report", encoding="utf-8")  # already moved in
    pub._recover(run)
    assert (run / "manifest.json").read_text(encoding="utf-8") == "old manifest"
    # no manifest was in place: the old outputs all come back (Task 17)
    assert (run / "report.html").read_text(encoding="utf-8") == "old report"
    assert not (run / ".publish-old-x1").exists()


def test_coverage_and_f113_count_only_the_events_a_line_still_describes():
    """I3: a cue that lost e2's sentence describes e1 only; a free pause during e2 is F113."""
    events = [_ev("e1", 1.0, 3.0, salience=5), _ev("e2", 1.5, 9.0, salience=4)]
    cue = _cue("c001", 1.0, 1.5, ev=["e1", "e2"], described=["e1"])
    cov = coverage([cue], [], events, extended_produced=False)
    assert (cov["described"], cov["undescribed"]) == (1, ["e2"])
    gaps = [_gap("g001", 1.0, 3.0), _gap("g002", 5.0, 8.0)]
    assert [(f["gap_id"], f["event_ids"]) for f in f113_flags(gaps, [cue], events)] == [
        ("g002", ["e2"])
    ]
    legacy = cue.model_copy(update={"described": None})  # a cue finalised before: all events
    assert coverage([legacy], [], events, extended_produced=False)["described"] == 2


# -- Task 17: recovery and the lock -------------------------------------------------------------


def _files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in root.rglob("*") if p.is_file()}  # fmt: skip


def test_a_crash_while_moving_in_restores_every_backed_up_output(tmp_path):
    """No manifest in place: the commit stopped in its second phase, so every backed-up item
    goes back over whatever the new publish had already moved in."""
    from lull.stages import publish as pub

    run = tmp_path / "run"
    backup = run / ".publish-old-x1"
    (backup / "keyframes").mkdir(parents=True)
    for name, text in [
        ("manifest.json", "old manifest"),
        ("report.html", "old report"),
        ("cues.json", "old cues"),
        ("keyframes/c001.jpg", "old frame"),
    ]:
        (backup / name).write_text(text, encoding="utf-8")  # fmt: skip
    (run / "keyframes").mkdir()
    (run / "keyframes" / "c009.jpg").write_text("new frame", encoding="utf-8")
    (run / "report.html").write_text("new report", encoding="utf-8")
    pub._recover(run)
    assert _files(run) == {
        "manifest.json": "old manifest", "report.html": "old report", "cues.json": "old cues",
        "keyframes/c001.jpg": "old frame",
    }  # fmt: skip


def test_a_backup_left_after_a_finished_commit_is_just_removed(tmp_path):
    """The new manifest is in place and the backup holds the old one: the commit finished
    and only removing the backup failed."""
    from lull.stages import publish as pub

    run = tmp_path / "run"
    backup = run / ".publish-old-x2"
    backup.mkdir(parents=True)
    (backup / "manifest.json").write_text("old manifest", encoding="utf-8")
    (backup / "report.html").write_text("old report", encoding="utf-8")
    (run / "manifest.json").write_text("new manifest", encoding="utf-8")
    (run / "report.html").write_text("new report", encoding="utf-8")
    pub._recover(run)
    assert _files(run) == {"manifest.json": "new manifest", "report.html": "new report"}


def test_a_lock_that_cannot_be_written_is_not_left_behind(tmp_path, monkeypatch):
    from lull.stages import publish as pub

    run = tmp_path / "run"
    run.mkdir()

    def broken(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(pub.os, "fdopen", broken)
    with pytest.raises(OSError, match="disk full"):
        with pub._lock(run):
            pass
    assert not (run / ".publish.lock").exists()


def test_a_held_lock_is_kept_fresh_by_a_heartbeat(tmp_path, monkeypatch):
    import os
    import time

    from lull.stages import publish as pub

    monkeypatch.setattr(pub, "LOCK_HEARTBEAT_S", 0.05)
    run = tmp_path / "run"
    run.mkdir()
    lock = run / ".publish.lock"
    with pub._lock(run):
        pid, host, _, token = lock.read_text(encoding="utf-8").split()
        assert len(token) == 32  # a random token of its own
        assert int(pid) == os.getpid() and host
        old = time.time() - 1000
        os.utime(lock, (old, old))
        deadline = time.monotonic() + 3
        while lock.stat().st_mtime < old + 500 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert lock.stat().st_mtime > old + 500  # touched again while held
    assert not lock.exists()


def test_a_lock_is_broken_only_when_quiet_and_its_holder_is_gone_or_very_old(tmp_path):
    """Stale (no heartbeat for LOCK_QUIET_S) is not enough: the holder must be a process that
    no longer runs on this host, or the lock must be older than LOCK_STALE_S (10 min)."""
    import os
    import socket
    import subprocess
    import sys
    import time

    from lull.stages import publish as pub

    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    here = socket.gethostname()
    lock = tmp_path / ".publish.lock"

    def breakable(content: str, age: float) -> bool:
        lock.write_text(content, encoding="utf-8")
        t = time.time() - age
        os.utime(lock, (t, t))
        return pub._breakable(lock)

    alive = f"{os.getpid()} {here} 0\n"
    dead = f"{gone.pid} {here} 0\n"
    elsewhere = f"{os.getpid()} another-host 0\n"
    assert not breakable(alive, 180)  # quiet, but its publisher still runs here
    assert breakable(alive, 11 * 60)  # quiet for longer than any publish
    assert breakable(dead, 180)  # its publisher is gone
    assert not breakable(dead, 30)  # recently touched: not stale
    assert not breakable(elsewhere, 180)  # cannot check a process on another host
    assert breakable(elsewhere, 11 * 60)
    assert not breakable("garbage", 180) and breakable("garbage", 11 * 60)


# -- Task 17: the events a description is owed ---------------------------------------------------


IMPORTANT = [
    _ev("e1", 1.0, 3.0, salience=5),  # plan 4: important, described
    _ev("e2", 5.5, 7.0, salience=4, text="The two keep walking."),  # plan 2: repetitive
    _ev("e3", 9.5, 11.0, salience=3),  # plan 3: important although watch said 3
    _ev("e4", 13.5, 15.0, salience=4),  # no plan priority: watch salience counts
    _ev("e5", 17.5, 19.0, salience=2),  # no plan priority, not salient
    _ev("e6", 21.5, 23.0, salience=4).model_copy(update={"said_in_dialogue": True}),  # R21
]
PRIORITY = {"e1": 4, "e2": 2, "e3": 3}


def test_coverage_and_f113_count_what_the_plan_rates_important():
    """Ruling (Task 17): important = plan priority >= 3; without a plan priority, watch
    salience >= 4. Watch-salient events the plan rated 1-2 are judged repetitive: listed, not
    counted as undescribed and never F113."""
    cues = [_cue("c001", 1.0, 1.5, ev=["e1"])]
    cov = coverage(cues, [], IMPORTANT, extended_produced=False, priority=PRIORITY)
    assert (cov["important_events"], cov["described"]) == (3, 1)
    assert cov["undescribed"] == ["e3", "e4"] and cov["repetitive"] == ["e2"]
    gaps = [_gap(f"g00{i}", 5.0 + 4 * (i - 1), 7.5 + 4 * (i - 1)) for i in range(1, 6)]
    flags = f113_flags(gaps, cues, IMPORTANT, priority=PRIORITY)
    assert [f["event_ids"] for f in flags] == [["e3"], ["e4"]]
    # without a plan the watch salience decides, as before
    assert coverage(cues, [], IMPORTANT, extended_produced=False)["important_events"] == 3


def test_the_report_lists_what_the_planner_judged_repetitive(tmp_path, fake_media):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video")
    cues = [_cue("c001", 1.0, 1.5, ev=["e1"])]
    gaps = [_gap("g001", 1.0, 3.0)]
    run = tmp_path / "run"
    res = publish(run, video, cues, [], IMPORTANT, gaps, None, _ledger(tmp_path), "r1",
                  RunOptions(), speech=[], priority=PRIORITY)  # fmt: skip
    summary = json.loads(res.manifest.read_text(encoding="utf-8"))["summary"]
    assert summary["repetitive"] == [{"id": "e2", "text": "The two keep walking."}]
    assert summary["coverage"]["important_events"] == 3
    reasons = {r["criterion"]: r["reasons"] for r in summary["wcag"]}
    assert "2 important events undescribed (1 of 3 described)" in reasons["1.2.5"]
    html = (run / "report.html").read_text(encoding="utf-8")
    assert "judged repetitive by the planner" in html and "The two keep walking." in html
    assert "1 of 3 important events described" in html


# -- Task 17 fix round: coverage the planner cannot game ----------------------------------------


def test_wcag_refuses_a_pass_when_the_planner_demotes_too_many_salient_events():
    """Ruling: more than 25 % of the watch-salient events judged repetitive blocks 1.2.5 and
    1.2.7 (rating everything 1-2 must not buy a pass); 25 % exactly is allowed."""
    many = _cov(5, 5) | {"watch_salient": 8, "repetitive": ["e1", "e2", "e3"]}
    rows = _wcag(extended_produced=True, coverage=many)
    reason = "3 of 8 salient events judged repetitive by the planner (limit 25 %)"
    assert rows["1.2.5"]["status"] == "needs review" and reason in rows["1.2.5"]["reasons"]
    assert rows["1.2.7"]["status"] == "needs review" and reason in rows["1.2.7"]["reasons"]
    assert rows["1.2.3"]["status"] == "met"
    few = _cov(6, 6) | {"watch_salient": 8, "repetitive": ["e1", "e2"]}
    assert _wcag(extended_produced=True, coverage=few)["1.2.5"]["status"] == "met"


def test_wcag_when_no_event_is_rated_important():
    """Nothing important although the watch saw salient events: review. Nothing important and
    nothing salient (a talking head, G203): met, with no description needed."""
    demoted = _cov(0, 0) | {"watch_salient": 3, "repetitive": ["e1", "e2", "e3"]}
    rows = _wcag(coverage=demoted)
    assert rows["1.2.5"]["status"] == "needs review"
    assert "no event was rated important" in rows["1.2.5"]["reasons"]
    talking_head = _cov(0, 0) | {"watch_salient": 0, "repetitive": []}
    rows = _wcag(standard=0, coverage=talking_head)
    g203 = "met (no visual information beyond the soundtrack)"
    assert [rows[c]["status"] for c in ("1.2.3", "1.2.5", "1.2.7")] == [g203] * 3
    assert all("G203" in rows[c]["via"] for c in rows)
    assert _wcag(standard=0, held=1, coverage=talking_head)["1.2.5"]["status"] == "needs review"


def test_coverage_reports_how_many_events_the_watch_found_salient():
    events = [_ev("e1", 0, 1, salience=5), _ev("e2", 1, 2, salience=4),
              _ev("e3", 2, 3, salience=3)]  # fmt: skip
    cov = coverage([], [], events, extended_produced=False, priority={"e1": 1, "e3": 4})
    assert (cov["watch_salient"], cov["repetitive"], cov["important_events"]) == (2, ["e1"], 2)


def test_the_report_escapes_text_from_the_models(tmp_path, fake_media):
    """The report environment autoescapes: an event or a held reason holding markup shows as
    text, never as markup."""
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video")
    evil = "<script>alert(1)</script>"
    events = [_ev("e1", 1.0, 3.0, salience=5, text=f"A sign reads {evil}")]
    cues = [_cue("c001", 1.0, None, status="held", ev=["e1"], note=f"held: {evil}")]
    run = tmp_path / "run"
    publish(run, video, cues, [], events, [_gap("g001", 1.0, 4.0)], None, _ledger(tmp_path),
            "r1", RunOptions(), speech=[])  # fmt: skip
    html = (run / "report.html").read_text(encoding="utf-8")
    assert "<script>alert" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_the_report_renders_a_summary_from_before_task_17(tmp_path, fake_media, monkeypatch):
    """Older summaries have no `repetitive` and no `important_events`: the template falls back
    instead of failing."""
    from lull.stages import publish as pub

    seen = {}
    real = pub._render_report

    def keep(context):
        seen["context"] = context
        return real(context)

    monkeypatch.setattr(pub, "_render_report", keep)
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video")
    publish(tmp_path / "run", video, [_cue("c001", 1.0, 1.0, ev=["e1"])], [],
            [_ev("e1", 1.0, 3.0)], [_gap("g001", 1.0, 3.0)], None, _ledger(tmp_path), "r1",
            RunOptions(), speech=[])  # fmt: skip
    old = dict(seen["context"])
    summary = {k: v for k, v in old["summary"].items() if k != "repetitive"}
    summary["coverage"] = {
        k: v
        for k, v in summary["coverage"].items()
        if k not in ("important_events", "repetitive", "watch_salient")
    }
    html = real({**old, "summary": summary})
    assert "1 of 1 important events described" in html


# -- Task 17 fix round: a lock is touched and released only by its owner ------------------------


def _other_lock(path: Path, age: float = 0.0, pid: int | None = None) -> str:
    import os
    import socket
    import time

    text = f"{pid or os.getpid()} {socket.gethostname()} {time.time():.0f} someone-elses-token\n"
    path.write_text(text, encoding="utf-8")
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return text


def test_a_lock_someone_else_took_over_is_neither_touched_nor_released(tmp_path, monkeypatch):
    import time

    from lull.stages import publish as pub

    monkeypatch.setattr(pub, "LOCK_HEARTBEAT_S", 0.05)
    run = tmp_path / "run"
    run.mkdir()
    lock = run / ".publish.lock"
    with pub._lock(run):
        theirs = _other_lock(lock, age=1000)  # broken and re-taken by another publisher
        stamp = lock.stat().st_mtime
        time.sleep(0.3)  # several heartbeats
        assert lock.stat().st_mtime == stamp  # not ours: never touched
    assert lock.read_text(encoding="utf-8") == theirs  # and not released either


def test_two_waiters_breaking_one_stale_lock_never_both_win(tmp_path):
    import subprocess
    import sys
    import threading

    from lull.stages import publish as pub

    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    lock = tmp_path / ".publish.lock"
    for _ in range(5):
        _other_lock(lock, age=300, pid=gone.pid)  # its publisher is gone: breakable
        start = threading.Barrier(2)
        won: list[bool] = []

        def breaker(start=start, won=won) -> None:
            start.wait()
            won.append(pub._break(lock))

        threads = [threading.Thread(target=breaker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(won) == [False, True]
        assert not lock.exists()
        assert [p.name for p in tmp_path.iterdir()] == []  # the aside copy is gone too


def test_breaking_a_lock_that_turns_out_fresh_puts_it_back(tmp_path):
    from lull.stages import publish as pub

    lock = tmp_path / ".publish.lock"
    fresh = _other_lock(lock)  # a live publisher, just written
    assert pub._break(lock) is False
    assert lock.read_text(encoding="utf-8") == fresh
    assert sorted(p.name for p in tmp_path.iterdir()) == [".publish.lock"]


def test_a_lock_whose_heartbeat_cannot_start_is_released(tmp_path, monkeypatch):
    from lull.stages import publish as pub

    class Broken:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise RuntimeError("no threads left")

    monkeypatch.setattr(pub.threading, "Thread", Broken)
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(RuntimeError, match="no threads"):
        with pub._lock(run):
            pass
    assert not (run / ".publish.lock").exists()


def test_a_lock_whose_content_cannot_be_written_is_not_left_behind(tmp_path, monkeypatch):
    from lull.stages import publish as pub

    real = pub.os.fdopen

    class Full:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

        def write(self, text):
            raise OSError("no space left")

    monkeypatch.setattr(pub.os, "fdopen", lambda fd, *a, **kw: Full(real(fd, *a, **kw)))
    run = tmp_path / "run"
    run.mkdir()
    with pytest.raises(OSError, match="no space"):
        with pub._lock(run):
            pass
    assert not (run / ".publish.lock").exists()


def test_alive_tells_a_running_process_from_a_finished_one():
    import os
    import subprocess
    import sys

    from lull.stages import publish as pub

    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    assert pub._alive(os.getpid()) is True
    assert pub._alive(gone.pid) is False
    assert pub._alive(0) is False and pub._alive(-5) is False


# -- Task 17 fix round 2 ------------------------------------------------------------------------


def test_waiters_never_disturb_a_live_lock(tmp_path, monkeypatch, caplog):
    """A holder with a running heartbeat and three waiters polling for about 3 s: the lock
    file is never missing or replaced while held, no waiter gets in, the heartbeat is still
    running at the end, and after the release the waiters take turns, one at a time."""
    import threading
    import time

    from lull.stages import publish as pub

    monkeypatch.setattr(pub, "LOCK_HEARTBEAT_S", 0.05)
    monkeypatch.setattr(pub, "LOCK_POLL_S", 0.01)
    run = tmp_path / "run"
    run.mkdir()
    lock = run / ".publish.lock"
    held, release = threading.Event(), threading.Event()
    problems: list[str] = []
    times: dict[str, float] = {}
    turns: list[tuple[float, float]] = []

    def holder() -> None:
        with pub._lock(run):
            held.set()
            release.wait(20)
            if not any(t.name == "publish-lock" and t.is_alive() for t in threading.enumerate()):
                problems.append("the heartbeat stopped")
            times["released"] = time.monotonic()

    def waiter() -> None:
        with pub._lock(run):
            t0 = time.monotonic()
            time.sleep(0.05)
            turns.append((t0, time.monotonic()))

    with caplog.at_level("WARNING", logger="lull.stages.publish"):
        h = threading.Thread(target=holder)
        h.start()
        assert held.wait(5)
        token = pub._owner(lock)
        waiters = [threading.Thread(target=waiter) for _ in range(3)]
        for w in waiters:
            w.start()
        end = time.monotonic() + 3.0
        while time.monotonic() < end and not problems:
            if pub._owner(lock) != token:
                problems.append("the lock went missing or changed while held")
        release.set()
        h.join(20)
        for w in waiters:
            w.join(20)
    assert problems == []
    assert "no longer ours" not in caplog.text and "broke" not in caplog.text
    assert len(turns) == 3 and all(t0 >= times["released"] for t0, _ in turns)
    turns.sort()
    assert all(b[0] >= a[1] for a, b in zip(turns, turns[1:], strict=False))  # one at a time
    assert not lock.exists()


def test_the_report_explains_a_pass_without_visual_information(tmp_path, fake_media):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video")
    run = tmp_path / "run"
    res = publish(run, video, [], [], [], [], None, _ledger(tmp_path), "r1", RunOptions(),
                  speech=[Span(start=0.0, end=60.0)])  # fmt: skip
    rows = json.loads(res.manifest.read_text(encoding="utf-8"))["summary"]["wcag"]
    assert {r["status"] for r in rows} == {"met (no visual information beyond the soundtrack)"}
    html = (run / "report.html").read_text(encoding="utf-8")
    assert "needs no description" in html


# -- what this attempt cost -------------------------------------------------------------------


def test_the_report_states_this_attempts_spend_and_all_runs_apart(tmp_path, fake_media):
    """Model spend of this attempt, its web searches (counted, notional $ for reference) and
    the model spend of every attempt under the run id, each labelled."""
    led = Ledger(tmp_path / "l.db", cap_run=None, cap_day=None, cap_total=None)

    def row(ts, cost, provider="tokenfactory", model=SUPER):
        led.record(CallRecord(run_id="r1", stage="s", role="watch", provider=provider,
                              model=model, tokens_in=10, tokens_cached=0, tokens_out=5,
                              latency_s=1.0, cost_usd=cost, ts=ts))  # fmt: skip

    row(100.0, 0.01)  # an earlier attempt under the same run id
    row(200.0, 0.02)
    row(201.0, 0.008, provider="tavily", model="tavily-search")
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"video")
    res = publish(tmp_path / "run", video, [], [], [], [], None, led, "r1", RunOptions(),
                  speech=[], since=150.0)  # fmt: skip
    s = json.loads(res.manifest.read_text(encoding="utf-8"))["summary"]
    assert s["spend"] == {
        "model_usd_this_run": 0.02, "tavily_searches_this_run": 1,
        "tavily_notional_usd_this_run": 0.008, "model_usd_all_runs": 0.03,
    }  # fmt: skip
    assert s["cost_usd"] == 0.02
    assert sum(g["calls"] for g in s["calls_by_role_model"]) == 2  # this attempt's calls
    html = " ".join((tmp_path / "run" / "report.html").read_text(encoding="utf-8").split())
    assert "Model spend, this run" in html and "all runs under run id r1" in html
    assert "1 web search" in html


# -- where the picture freezes for an extended description ------------------------------------


def _say(*sentences: tuple[float, str], step: float = 0.45, gap: float = 0.05) -> list:
    """Words of each sentence, from its start, `step` s per word with `gap` s between."""
    from lull.types import Word

    out = []
    for t, text in sentences:
        for w in text.split():
            out.append(Word(text=w, start=round(t, 3), end=round(t + step - gap, 3)))
            t += step
    return out


def test_a_freeze_never_comes_before_its_visual_and_waits_for_the_sentence():
    """A slide changes at 2.5 s, in the middle of a sentence: the picture holds at the end of
    that sentence (4.2 s), on the new slide, not where the speech began (on the old one)."""
    words = _say((0.0, "This is slide one."), (2.0, "Now the chart shows growth."))
    ext = [_cue("x001", 2.5, 1.0, gap=None, status="extended")]
    [(frame, _, _)] = _freezes(ext, words=words)
    assert frame / 25 == pytest.approx(4.2, abs=0.04)  # "growth." ends at 4.2 s
    assert frame / 25 >= 2.5 + 0.3


def test_a_freeze_in_silence_waits_a_moment_after_its_visual():
    words = _say((0.0, "Hello there."))  # ends at 0.85 s
    ext = [_cue("x001", 3.0, 1.0, gap=None, status="extended")]
    [(frame, _, _)] = _freezes(ext, words=words)
    assert frame / 25 == pytest.approx(3.3, abs=0.04)


def test_a_freeze_takes_a_gap_between_words_when_no_sentence_ends_soon():
    """No sentence boundary within FREEZE_WAIT_S: the first gap of 0.15 s or more between
    words after the visual will do."""
    from lull.types import Word

    words = [Word(text=f"w{i}", start=round(i * 0.4, 3), end=round(i * 0.4 + 0.35, 3))
             for i in range(30)]  # 0-12 s, 0.05 s gaps, no punctuation  # fmt: skip
    words[12] = words[12].model_copy(update={"end": 4.98})  # 4.98 to 5.2: a 0.22 s gap
    words[13] = words[13].model_copy(update={"start": 5.2})
    ext = [_cue("x001", 1.0, 1.0, gap=None, status="extended")]
    [(frame, _, _)] = _freezes(ext, words=words)
    assert frame / 25 == pytest.approx(5.0, abs=0.04)


def test_script_both_tracks_and_cues_json_place_every_cue_at_the_same_time(published):
    """One timeline: a voiced extended cue sits at its freeze point in script.md, in
    extended.vtt and in cues.json (freeze_at); a standard cue at its start in all three."""
    run = published["run"]
    script = (run / "script.md").read_text(encoding="utf-8")
    in_script = dict(
        (m.group(3), (m.group(1), m.group(2)))
        for m in re.finditer(r"^- (\S+) to (\S+) · (\w+) · ", script, re.M)
    )
    in_vtt: dict[str, tuple[str, str]] = {}
    for name in ("descriptions.vtt", "extended.vtt"):
        text = (run / name).read_text(encoding="utf-8")
        for m in re.finditer(r"^(\w+)\n(\S+) --> (\S+)$", text, re.M):
            in_vtt[m.group(1)] = (m.group(2), m.group(3))
    data = json.loads((run / "cues.json").read_text(encoding="utf-8"))
    in_json = {
        c["id"]: c.get("freeze_at", c["start"])
        for c in data["cues"] + data["extended"]
        if c["audio_dur"] is not None and c["status"] != "held"
    }
    assert set(in_script) == set(in_vtt) == set(in_json) == {"c001", "x001", "x002"}
    for cid, (a, b) in in_vtt.items():
        assert in_script[cid] == (a, b), cid
        assert timecode(in_json[cid]) == a, cid
    assert in_vtt["x002"][0] == "00:00:04.320"  # the freeze point, not the visual (4.0 s)

