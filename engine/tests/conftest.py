import subprocess
from pathlib import Path

import pytest


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True, capture_output=True)


@pytest.fixture(scope="session")
def tone_slides(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """12 s, 640x360 30 fps; red 0-6 s, blue 6-12 s; 440 Hz sine 0-4 s then silence."""
    out = tmp_path_factory.mktemp("media") / "tone_slides.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=red:s=640x360:r=30:d=6",
        "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4,apad=whole_dur=12",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "12", str(out),
    )  # fmt: skip
    return out


@pytest.fixture(scope="session")
def noaudio(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """4 s portrait 720x1280 with no audio stream."""
    out = tmp_path_factory.mktemp("media") / "noaudio.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=720x1280:r=25:d=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", str(out),
    )  # fmt: skip
    return out


@pytest.fixture(scope="session")
def loud_burst(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """6 s, quiet 440 Hz hum (about -32 dBFS) with a 24 dB louder burst at 2.0-3.0 s."""
    out = tmp_path_factory.mktemp("media") / "loud_burst.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=gray:s=320x180:r=25:d=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-af", "volume=0.04,volume=16:enable='between(t,2,3)'",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "6", str(out),
    )  # fmt: skip
    return out
