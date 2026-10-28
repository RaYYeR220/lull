import json

import numpy as np

from suite_a import style
from suite_a.render import (
    KEY_PATH,
    KEY_SHA_PATH,
    SAMPLE_RATE,
    inventory,
    layout,
    render_slide,
    sha256_file,
    trim,
)


def test_y_ticks_start_at_zero_and_cover_the_top():
    top, ticks = style.y_ticks([11.0, 17.5, 23.0])
    assert ticks[0] == 0 and ticks[-1] <= top and top >= 23.0 * 1.18 - 1e-9
    assert len(ticks) <= 6
    assert style.y_ticks([2.0, 36.0, 2.0])[1] == [0, 10, 20, 30, 40]


def test_trim_keeps_the_voiced_part_with_padding():
    rate = SAMPLE_RATE
    quiet = np.zeros(rate // 2, dtype=np.int16)
    loud = (np.sin(np.arange(rate) / 5) * 8000).astype(np.int16)
    out = trim(np.concatenate([quiet, loud, quiet]), rate)
    pad = int(rate * style.TRIM_PAD_S)
    assert abs(len(out) - (len(loud) + 2 * pad)) <= int(rate * 0.01) * 2


def test_layout_places_pauses_sentences_and_frame_aligned_slides():
    spec = {"slides": [
        {"narration": ["a", "b"], "pauses": [2.0, 1.5]},
        {"narration": ["c"], "pauses": [3.0]},
    ]}  # fmt: skip
    clip = np.zeros(SAMPLE_RATE, dtype=np.int16)  # 1.0 s each
    plan = layout(spec, [[clip, clip], [clip]])
    s = plan["sentences"]
    assert (s[0]["start"], s[0]["end"]) == (2.0, 3.0)
    assert (s[1]["start"], s[1]["end"]) == (4.5, 5.5)
    frame = 1 / style.FPS
    assert plan["slides"][1]["start"] >= 5.5
    assert (
        abs(plan["slides"][1]["start"] / frame - round(plan["slides"][1]["start"] / frame)) < 1e-9
    )
    assert s[2]["start"] == round(plan["slides"][1]["start"] + 3.0, 4)
    assert plan["duration"] >= s[2]["end"] + style.TAIL_S
    assert plan["slides"][0]["end"] == plan["slides"][1]["start"]


def test_slide_render_is_byte_identical(tmp_path):
    slide = {
        "title": "Output Over Time",
        "bullets": ["Oxygen raises the yield", "Enzymes speed reactions"],
        "chart": {"type": "bar", "title": "ATP Output", "x_label": "Hour", "y_label": "N",
                  "points": [{"label": "H1", "value": 11.0}, {"label": "H2", "value": 23.0}]},
    }  # fmt: skip
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    info = render_slide(slide, 2, 3, a)
    render_slide(slide, 2, 3, b)
    assert sha256_file(a) == sha256_file(b)
    assert info["y_ticks"][0] == 0
    text = inventory(slide, 2, 3, 1.0, 9.0, info["y_ticks"])
    assert '"H2" = 23' in text and 'Title: "Output Over Time"' in text


def test_committed_key_matches_its_hash():
    digest = KEY_SHA_PATH.read_text(encoding="utf-8").split()[0]
    assert sha256_file(KEY_PATH) == digest
    key = json.loads(KEY_PATH.read_text(encoding="utf-8"))
    assert key["videos_n"] == len(key["videos"]) == 12
    for v in key["videos"]:
        assert all(f["slide_start"] < f["slide_end"] for f in v["visual_facts"])
        assert v["narration"] == sorted(v["narration"], key=lambda s: s["start"])
