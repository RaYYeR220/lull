import os
import subprocess
from pathlib import Path

import pytest

from lull.ledger import Ledger
from lull.speech import NemoSpeechHTTP
from lull.stages.finalize import CueContext, finalize_cue
from lull.tf import TokenFactory
from lull.types import Cue, Gap, RunOptions, VisualEvent

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("LULL_LIVE") != "1", reason="set LULL_LIVE=1 to run"),
]

FONTS = (
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


def _blue_title(dst: Path) -> Path:
    """10 s, 640x360: a static blue screen with the white word WELCOME, no audio."""
    font = next((f for f in FONTS if Path(f).exists()), None)
    if font is None:
        pytest.skip("no TrueType font for ffmpeg drawtext")
    fontfile = font.replace(":", r"\:")
    text = (
        f"drawtext=fontfile='{fontfile}':text='WELCOME':fontcolor=white:fontsize=64"
        ":x=(w-text_w)/2:y=(h-text_h)/2"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=25:d=10", "-vf", text,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)],
        check=True, capture_output=True,
    )  # fmt: skip
    return dst


def test_finalize_live_refuses_a_false_claim(tmp_path: Path):
    video = _blue_title(tmp_path / "blue.mp4")
    speech = NemoSpeechHTTP(
        os.environ.get("LULL_SPEECH_URL") or "http://127.0.0.1:8089",
        os.environ.get("LULL_SPEECH_KEY") or None,
    )
    assert speech.health()
    event = VisualEvent(
        id="e001", t_start=0.0, t_end=10.0, kind="on_screen_text",
        text="White text on a blue screen reads WELCOME.", verbatim_text="WELCOME",
        salience=5, said_in_dialogue=False,
    )  # fmt: skip
    gap = Gap(id="g001", start=0.0, end=10.0, usable_start=0.2, usable_end=9.8, capacity_words=22)
    cue = Cue(
        id="c001", gap_id="g001", start=0.2, end=None,
        text='A red car drives past. Words appear: "WELCOME".', event_ids=["e001"],
        status="verified", ahead=False, claims=[], audio_path=None, audio_dur=None,
    )  # fmt: skip
    ledger = Ledger(tmp_path / "live.db")
    tf = TokenFactory.from_settings(ledger)
    try:
        out = finalize_cue(
            cue, gap, {"e001": event}, video, tmp_path / "work", tf, speech, RunOptions(),
            CueContext(wps=2.36, verbatim=["WELCOME"]), run_id="live",
        )  # fmt: skip
    finally:
        tf.close()
    assert out.status == "held" or "car" not in out.text.lower()
    if out.status == "verified":
        assert out.claims and all(c.verdict == "supported" for c in out.claims)
        assert Path(out.audio_path).exists() and out.end <= gap.usable_end + 1e-6
    calls = ledger.calls("live")
    assert any(c.role == "verify" and c.model.endswith("Super-VL") for c in calls)
    assert all(c.stage == "finalize" for c in calls)
