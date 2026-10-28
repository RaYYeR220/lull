"""Checks shared by the media and publish tests: every input ffmpeg opens is a local file."""

from __future__ import annotations

from pathlib import Path

LOCK = ["-protocol_whitelist", "file,pipe"]


def assert_locked(cmd: list[str]) -> None:
    """Every input of an ffmpeg command, and an ffprobe command, reads only local files."""
    name = Path(cmd[0]).stem.lower()
    if name == "ffprobe":
        i = cmd.index("-protocol_whitelist")
        assert cmd[i + 1] == "file,pipe", cmd
        return
    assert name == "ffmpeg", cmd
    for i, arg in enumerate(cmd):
        if arg == "-i":
            assert cmd[i - 2 : i] == LOCK, f"unlocked input {cmd[i + 1]!r} in {cmd}"
