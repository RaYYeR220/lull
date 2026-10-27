"""An upload checked before a run: ffprobe and ffmpeg may only read local files through the
mp4/mov and Matroska/WebM demuxers, each under a time limit, and the run gets a copy remuxed
and cut at the length limit, so a container that misstates its duration cannot make a longer
run.

The demuxer whitelist matters: ffmpeg recognises scripts by their content, so an ffconcat
playlist named clip.mp4 would otherwise read whatever local files it lists.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from lull.media import MediaError, Probe

PROTOCOLS = "file,pipe"
DEMUXERS = "mov,matroska"  # the "mov,mp4,m4a,3gp,3g2,mj2" and "matroska,webm" demuxers
PROBE_TIMEOUT = 20.0
TRIM_TIMEOUT = 120.0


def _bin(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise MediaError(f"{name} not found on PATH")
    return path


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    name = Path(cmd[0]).stem
    try:
        return subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MediaError(f"{name} timed out after {timeout:g} s") from None
    except subprocess.CalledProcessError as e:
        tail = e.stderr.decode("utf-8", "replace").strip()[-800:]
        raise MediaError(f"{name} failed (exit {e.returncode}): {tail}") from None
    except OSError as e:
        raise MediaError(f"could not run {name}: {e}") from None


def _fps(rate: str) -> float:
    num, _, den = rate.partition("/")
    try:
        d = float(den) if den else 1.0
        return float(num) / d if d else 0.0
    except ValueError:
        return 0.0


def probe(path: Path, *, timeout: float = PROBE_TIMEOUT) -> Probe:
    """lull.media.probe, restricted and time-limited."""
    out = _run(
        [_bin("ffprobe"), "-v", "error", "-protocol_whitelist", PROTOCOLS,
         "-format_whitelist", DEMUXERS, "-print_format", "json", "-show_format",
         "-show_streams", str(path)],
        timeout,
    )  # fmt: skip
    try:
        info = json.loads(out.stdout)
        streams = info.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if video is None:
            raise MediaError(f"no video stream in {path}")
        duration = info.get("format", {}).get("duration") or video.get("duration")
        if duration is None:
            raise MediaError(f"cannot determine duration of {path}")
        return Probe(
            duration=float(duration),
            width=int(video["width"]),
            height=int(video["height"]),
            fps=_fps(video.get("r_frame_rate", "0/1")),
            has_audio=audio is not None,
            video_codec=video.get("codec_name", ""),
            audio_codec=None if audio is None else audio.get("codec_name", ""),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise MediaError(f"ffprobe output for {path} does not read: {e}") from None


def trim(src: Path, dst: Path, seconds: float, *, timeout: float = TRIM_TIMEOUT) -> Path:
    """`src` remuxed (no re-encoding) into `dst`, cut at `seconds`. Data streams are left out:
    they are not needed and some (a phone's metadata track) cannot be copied."""
    _run(
        [_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
         "-protocol_whitelist", PROTOCOLS, "-format_whitelist", DEMUXERS, "-i", str(src),
         "-map", "0", "-map", "-0:d", "-c", "copy", "-t", f"{seconds:g}", str(dst)],
        timeout,
    )  # fmt: skip
    return dst
