"""Suite B reference extraction: WebVTT parsing, the energy VAD, the transcript alignment and
the line anchors. No network, no media files."""

import numpy as np
import pytest

from suite_b import facts as facts_mod
from suite_b.refs import (
    ad_lines,
    align,
    anchor,
    best_offset,
    energy_vad,
    merge_spans,
    overlap_s,
    parse_ts,
    parse_vtt,
    usgs_reference,
    word_count,
)

VTT = """﻿WEBVTT
License: CC BY 4.0
Author: Someone

NOTE
Title: a test track
* comments span lines

STYLE
::cue(.fast) { voice-rate: fast; }

1
00:00:00.500 --> 00:00:05.000
Titles appear under the water
that floods a floor:

00:01:09.150 --> 00:01:16.030 align:start line:0
<v.fast>He runs</v> to <u>www.example.org</u> &amp; back.

3
10:47.500 --> 10:53.000
Produced By
Ton Roosendaal

NOTE This is the end
"""


def test_parse_vtt_cues_markup_and_blocks():
    cues = parse_vtt(VTT, "hunt")
    assert [c["id"] for c in cues] == ["hunt-01", "hunt-02", "hunt-03"]
    assert cues[0] == {"id": "hunt-01", "source": "hunt", "start": 0.5, "end": 5.0,
                       "text": "Titles appear under the water\nthat floods a floor:"}  # fmt: skip
    assert cues[1]["start"] == 69.15 and cues[1]["end"] == 76.03  # cue settings ignored
    assert cues[1]["text"] == "He runs to www.example.org & back."
    assert cues[2]["start"] == 647.5 and cues[2]["text"] == "Produced By\nTon Roosendaal"


def test_parse_vtt_refuses_bad_input():
    with pytest.raises(ValueError, match="WEBVTT"):
        parse_vtt("1\n00:00:01.000 --> 00:00:02.000\nx\n", "p")
    with pytest.raises(ValueError, match="timing"):
        parse_vtt("WEBVTT\n\nfirst line\nsecond line\nthird\n", "p")
    with pytest.raises(ValueError, match="ends before"):
        parse_vtt("WEBVTT\n\n00:00:05.000 --> 00:00:01.000\nx\n", "p")
    assert parse_ts("01:02:03.004") == pytest.approx(3723.004)
    with pytest.raises(ValueError):
        parse_ts("1:2.3")


def test_word_count_and_spans():
    assert word_count("Proog's walking-stick, 360 Woods Hole Road!") == 6
    assert word_count("— / —") == 0
    assert merge_spans([(3, 4), (0, 1), (1.1, 2)], join=0.2) == [(0.0, 2.0), (3.0, 4.0)]
    assert merge_spans([(0, 1), (1.3, 2)], join=0.2) == [(0.0, 1.0), (1.3, 2.0)]
    assert overlap_s([(0, 2), (1, 3)], [(2.5, 10)]) == 0.5
    assert overlap_s([(0, 1)], []) == 0.0


def _signal(rate, parts):
    """parts: (seconds, amplitude) runs of a 440 Hz tone (amplitude 0: digital silence)."""
    out = []
    for sec, amp in parts:
        t = np.arange(int(sec * rate)) / rate
        out.append((amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32))
    return np.concatenate(out)


def test_energy_vad_bridges_short_gaps_and_drops_blips():
    rate = 16000
    x = _signal(rate, [(1.0, 0), (1.0, 0.3), (0.2, 0), (0.5, 0.3), (1.0, 0), (0.04, 0.3),
                       (1.0, 0), (0.6, 0.001), (0.5, 0)])  # fmt: skip
    got = energy_vad(x, rate)
    # 1.0-2.0 and 2.2-2.7 join across the 0.2 s gap; the 40 ms blip is dropped; a tone at
    # -63 dBFS RMS is under the -45 dBFS threshold
    assert got == [(1.0, 2.7)]
    assert energy_vad(np.zeros(100, dtype=np.float32), rate) == []


# -- alignment on a synthetic transcript pair -----------------------------------------------------


def words(text, start, step=0.4, gap=0.0):
    """Words of `text` from `start`, each `step` long; returns (words, end time)."""
    out, t = [], start
    for w in text.split():
        out.append({"text": w, "start": round(t, 3), "end": round(t + step - 0.05, 3)})
        t += step
    return out, t + gap


def build_pair():
    """The original narration and an AD version: the describer speaks in two inserted pauses
    and once before the program; the AD version hears 'Woods Hole' as 'woods hall'."""
    o, t = words("At the Woods Hole Science Center we study the coast.", 0.0)
    o2, t = words("Our center is in Woodshole, Massachusetts, near the sea.", t + 1.0)
    o3, t = words("We map the seafloor.", t + 1.0)
    original = o + o2 + o3
    a1, t = words("A title card glows.", 0.0)  # before the program (the video waits)
    a2, t = words("At the Woods Hole Science Center.", t + 0.5)
    a3, t = words("Video shows the Woods Hole Science Center main building.", t + 0.3)
    a4, t = words("We study the coast.", t + 0.3)
    a5, t = words("Our center is in woods hall, Massachusetts, near the sea.", t + 1.0)
    a6, t = words("Map of the bay with an arrow.", t + 0.3)  # inserted pause
    a7, t = words("We map the seafloor.", t + 1.0)
    ad = a1 + a2 + a3 + a4 + a5 + a6 + a7
    return original, ad


def test_align_finds_the_describer_lines_and_maps_time():
    original, ad = build_pair()
    al = align(original, ad)
    keep, drop = ad_lines(ad, al["ad"])
    lines = [" ".join(ad[i]["text"] for i in ws) for ws in keep]
    assert lines == [
        "A title card glows.",
        "Video shows the Woods Hole Science Center main building.",  # slid to its sentence
        "Map of the bay with an arrow.",
    ]
    assert drop == []
    assert al["missing"] == []  # "woods hall" is the narration heard differently
    doc = usgs_reference("t", {"words": original}, {"words": ad},
                         o_dur=original[-1]["end"] + 1, a_dur=ad[-1]["end"] + 1)  # fmt: skip
    cues = doc["cues"]
    assert [c["placement"] for c in cues] == ["before_program", "timed", "timed"]
    assert cues[0]["anchor_time"] == 0.0 and cues[0]["paused"]
    # the building line sits after "Center" (original 2.35 s) and before "We" (2.4 s)
    assert 2.3 <= cues[1]["anchor_time"] <= 2.4 and cues[1]["paused"]
    # the map line sits after "sea." and before "We map"
    sea = next(w for w in original if w["text"] == "sea.")
    we_map = original[-4]
    assert sea["end"] <= cues[2]["anchor_time"] <= we_map["start"]
    assert doc["stats"]["original_tokens_matched_share"] == 1.0
    assert all(c["anchor_end"] == c["anchor_time"] for c in cues)


def test_chance_match_inside_a_description_is_not_narration():
    original, _ = words("Tools to collect observations of what can be seen recorded.", 0)
    ad, _ = words("Tools. Scientists deploying equipment view of lobster on the seafloor. "
                  "Recorded.", 0)  # fmt: skip
    al = align(original, ad)
    keep, _ = ad_lines(ad, al["ad"])
    assert [" ".join(ad[i]["text"] for i in ws) for ws in keep] == [
        "Scientists deploying equipment view of lobster on the seafloor."
    ]
    missing = " ".join(original[i]["text"] for i in al["missing"])
    assert missing == "to collect observations of what can be seen"


def test_split_compound_slides_to_the_narration_copy():
    """The narration says 'road stream crossings.' in the AD version ('roadstream' in the
    original); the describer's own 'roadstream crossings' must not take its place."""
    original, _ = words("Problems found at roadstream crossings. It is not unusual.", 0)
    ad, _ = words("Problems found at road stream crossings. A drawing with culverts at the "
                  "roadstream crossings. It is not unusual.", 0)  # fmt: skip
    al = align(original, ad)
    keep, _ = ad_lines(ad, al["ad"])
    assert [" ".join(ad[i]["text"] for i in ws) for ws in keep] == [
        "A drawing with culverts at the roadstream crossings."
    ]


def test_abbreviations_do_not_end_a_line_and_short_runs_are_dropped():
    original, _ = words("We study the coast. We map it.", 0)
    ad, _ = words("We study the coast. Text reads the U.S. Geological Survey. We map it. Yes.", 0)
    al = align(original, ad)
    keep, drop = ad_lines(ad, al["ad"])
    assert [" ".join(ad[i]["text"] for i in ws) for ws in keep] == [
        "Text reads the U.S. Geological Survey."
    ]
    assert [" ".join(ad[i]["text"] for i in ws) for ws in drop] == ["Yes."]


def test_anchor_without_a_pause_maps_linearly():
    pairs = [(10.0, 10.5, 5.0, 5.5), (14.0, 14.5, 9.0, 9.5)]  # AD gap 3.5 s = original gap
    a = anchor(11.0, 12.0, pairs, a_dur=20, o_dur=15)
    assert a["anchor_time"] == 6.0 and not a["paused"] and a["placement"] == "timed"
    assert a["anchor_range"] == [5.5, 9.0]
    b = anchor(11.0, 12.0, [(10.0, 10.5, 5.0, 5.5), (24.0, 24.5, 9.0, 9.5)], 30, 15)
    assert b["paused"] and b["pause_s"] == 10.0 and b["anchor_time"] == 6.0
    c = anchor(18.0, 20.0, [(10.0, 10.5, 5.0, 5.5), (24.0, 24.5, 9.0, 9.5)], 30, 15)
    assert c["anchor_time"] == 9.0  # kept inside the original gap


def test_best_offset_finds_the_shift_with_least_dialogue_overlap():
    speech = [(0, 2), (5, 7), (10, 12)]
    desc = [(2.5, 4.5), (7.5, 9.5)]  # sits between the dialogue at shift 0
    b = best_offset(desc, speech)
    assert b["best_shift_s"] == 0.0 and b["overlap_at_best_s"] == 0.0
    late = [(4.0, 5.5), (9.0, 10.5)]  # 0.5 s into the next line of dialogue
    b = best_offset(late, speech)
    assert b["overlap_at_0_s"] == 1.0
    # every shift from -3.5 to -0.5 clears the dialogue; the smallest one is reported
    assert b["best_shift_s"] == -0.5 and b["overlap_at_best_s"] == 0.0


# -- fact extraction plumbing (no model) ----------------------------------------------------------


def test_chunk_request_schema_forces_one_answer_per_line():
    doc = {"title": "T", "about": "a test"}
    chunk = [{"id": "p-01", "text": "A man sits.\nHe stands."}, {"id": "p-02", "text": "Rain."}]
    prompt, schema = facts_mod.chunk_request(doc, "p", chunk, [{"id": "p-00", "text": "Dusk."}])
    lines = schema["properties"]["lines"]
    assert lines["minItems"] == lines["maxItems"] == 2
    assert lines["items"]["properties"]["id"]["enum"] == ["p-01", "p-02"]
    kinds = lines["items"]["properties"]["facts"]["items"]["properties"]["kind"]["enum"]
    assert kinds == list(facts_mod.KINDS)
    assert "p-01: A man sits. / He stands." in prompt and "p-00: Dusk." in prompt


def test_facts_inherit_their_line_and_problems_are_listed():
    cues = [{"id": "p-01", "text": "A man sits.", "anchor_time": 3.0, "anchor_end": 5.0,
             "placement": "timed"},
            {"id": "p-02", "text": "Rain.", "anchor_time": 9.0, "anchor_end": 9.0}]  # fmt: skip
    answers = [{"lines": [{"id": "p-01", "facts": [{"text": " A man sits. ", "kind": "visual"}]},
                          {"id": "p-01", "facts": []}]}]  # fmt: skip
    out, problems = facts_mod.facts_of({}, "p", cues, answers)
    assert out == [{"id": "p-01.1", "ref_cue_id": "p-01", "source": "p", "text": "A man sits.",
                    "kind": "visual", "anchor_time": 3.0, "anchor_end": 5.0,
                    "placement": "timed"}]  # fmt: skip
    assert problems == ["p-01: answered twice (first kept)", "p-02: no answer", "p-02: no facts"]


def test_reference_digest_pins_the_list():
    rows = ["aa  refs/x.json", "bb  facts/x.json"]
    d = facts_mod.reference_digest(rows)
    assert d == facts_mod.reference_digest(list(rows))
    assert d != facts_mod.reference_digest(rows[::-1])
