import os
import subprocess
from pathlib import Path

import pytest

from lull.ledger import Ledger
from lull.stages.watch import watch
from lull.tf import TokenFactory

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("LULL_LIVE") != "1", reason="set LULL_LIVE=1 to run"),
]


def _two_scenes(dst: Path) -> Path:
    """20 s, 640x360: solid red 0-10 s, solid blue 10-20 s, no audio."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=red:s=640x360:r=25:d=10",
         "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=25:d=10",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dst)],
        check=True, capture_output=True,
    )  # fmt: skip
    return dst


def test_watch_live_two_scenes(tmp_path: Path):
    video = _two_scenes(tmp_path / "two_scenes.mp4")
    ledger = Ledger(tmp_path / "live.db")
    client = TokenFactory.from_settings(ledger)
    try:
        events = watch(video, tmp_path / "work", [], client, run_id="live", profile="education")
    finally:
        client.close()
    assert len(events) >= 1
    assert all(0.0 <= e.t_start <= e.t_end <= 20.0 + 0.1 for e in events)
    assert [e.id for e in events] == [f"e{i:03d}" for i in range(1, len(events) + 1)]
    calls = ledger.calls("live")
    assert calls and all(c.stage == "watch" and c.model.endswith("Super-VL") for c in calls)
