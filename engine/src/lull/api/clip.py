"""An upload checked before a run, with the engine's own rules (lull.media: local files only,
the MP4/MOV and Matroska/WebM demuxers only, assert_safe_container) and two things the engine
does not do for it: a time limit on each ffprobe/ffmpeg call, and the run's copy remuxed and
cut at the length limit, so a container that misstates its duration cannot make a longer run.

Limits (over_limits): every engine stage decodes the video in full, so a small file that asks
for a lot of work (a huge frame, a high frame rate, extra pictures) is refused before any work:
at most 1920x1080 (2.1 megapixels, either way up), 60 frames a second, one video stream, two
audio streams of up to 8 channels at 192 kHz, no subtitle, data or attachment streams, and a
bit rate of at most 100 Mb/s. The run's copy keeps the one video stream and the audio only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lull.media import SAFE_FORMATS, SAFE_PROTOCOLS, MediaError, Probe

PROBE_TIMEOUT = 20.0
TRIM_TIMEOUT = 120.0

MAX_LONG_SIDE, MAX_SHORT_SIDE, MAX_PIXELS = 1920, 1080, 2_100_000
MAX_FPS = 60.0
FPS_SLACK = 0.5  # a nominal 60 that a phone reports as 60.02
MAX_VIDEO_STREAMS, MAX_AUDIO_STREAMS = 1, 2
MAX_CHANNELS, MAX_SAMPLE_RATE = 8, 192_000
MAX_BIT_RATE = 100_000_000  # bits a second, the whole file


@dataclass(frozen=True)
class Media(Probe):
    """The engine's Probe (its first video stream) and what the limits look at: every video
    stream's size and frame rate, every audio stream's channels and sample rate, the other
    streams' kinds and the file's bit rate."""

    videos: tuple[tuple[int, int, float], ...] = ()
    audios: tuple[tuple[int, int], ...] = ()
    others: tuple[str, ...] = ()
    bit_rate: float | None = None


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


def _rate(stream: dict) -> float:
    """The stream's frame rate: the lower of its nominal and average rates that is known (a
    variable-rate file can state a nominal rate far above what it plays at)."""
    rates = [_fps(str(stream.get(k) or "")) for k in ("r_frame_rate", "avg_frame_rate")]
    known = [r for r in rates if r > 0]
    return min(known) if known else 0.0


def _int(x: object) -> int:
    try:
        return int(x)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _number(x: object) -> float | None:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def over_limits(info: Probe) -> str | None:
    """Why a live run does not take this video, in plain words; None when it is within the
    limits. A plain Probe (no stream list) is checked on its first video stream alone."""
    videos = getattr(info, "videos", ()) or ((info.width, info.height, info.fps),)
    audios = getattr(info, "audios", ())
    for kind in getattr(info, "others", ()):
        return (
            f"This file carries a {kind} track besides its picture and sound. Live "
            f"descriptions take a plain video: export it again without the {kind} track."
        )
    if len(videos) > MAX_VIDEO_STREAMS:
        return (
            f"This file holds {len(videos)} pictures (video streams). Live descriptions take a "
            "video with one."
        )
    count = max(len(audios), info.audio_streams)
    if count > MAX_AUDIO_STREAMS:
        return (
            f"This file holds {count} sound tracks. Live descriptions take up to "
            f"{MAX_AUDIO_STREAMS}."
        )
    for w, h, fps in videos:
        if max(w, h) > MAX_LONG_SIDE or min(w, h) > MAX_SHORT_SIDE or w * h > MAX_PIXELS:
            return (
                f"This video is {w}x{h}. Live descriptions take videos up to "
                f"{MAX_LONG_SIDE}x{MAX_SHORT_SIDE}: export it again at 1080p or less."
            )
        if fps > MAX_FPS + FPS_SLACK:
            return (
                f"This video has {fps:.4g} frames a second. Live descriptions take up to "
                f"{MAX_FPS:g} frames a second."
            )
    for channels, rate in audios:
        if channels > MAX_CHANNELS or rate > MAX_SAMPLE_RATE:
            return (
                f"This video's sound has {channels} channels at {rate} Hz. Live descriptions "
                f"take up to {MAX_CHANNELS} channels at {MAX_SAMPLE_RATE // 1000} kHz."
            )
    bit_rate = getattr(info, "bit_rate", None)
    if bit_rate is not None and bit_rate > MAX_BIT_RATE:
        return (
            f"This video's bit rate is {bit_rate / 1e6:.0f} Mb/s. Live descriptions take up "
            f"to {MAX_BIT_RATE // 1_000_000} Mb/s: export it again at a lower quality."
        )
    return None


def probe(path: Path, *, timeout: float = PROBE_TIMEOUT) -> Media:
    """What lull.media.probe answers (the engine's Probe, the container in format_name) and
    every stream for the limits, with the same ffprobe restrictions, under a time limit. The
    engine probes the run's copy again itself; this one decides whether there is a run."""
    out = _run(
        [_bin("ffprobe"), "-v", "error", "-protocol_whitelist", SAFE_PROTOCOLS,
         "-format_whitelist", SAFE_FORMATS, "-print_format", "json", "-show_format",
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
        fmt = info.get("format", {})
        duration = fmt.get("duration") or video.get("duration")
        if duration is None:
            raise MediaError(f"cannot determine duration of {path}")
        kinds = [str(s.get("codec_type") or "unknown") for s in streams]
        return Media(
            duration=float(duration),
            width=int(video["width"]),
            height=int(video["height"]),
            fps=_fps(video.get("r_frame_rate", "0/1")),
            has_audio=audio is not None,
            video_codec=video.get("codec_name", ""),
            audio_codec=None if audio is None else audio.get("codec_name", ""),
            format_name=str(fmt.get("format_name") or ""),
            audio_streams=kinds.count("audio"),
            fps_rational=str(video.get("r_frame_rate") or ""),
            videos=tuple(
                (_int(s.get("width")), _int(s.get("height")), _rate(s))
                for s in streams
                if s.get("codec_type") == "video"
            ),
            audios=tuple(
                (_int(s.get("channels")), _int(s.get("sample_rate")))
                for s in streams
                if s.get("codec_type") == "audio"
            ),
            others=tuple(k for k in kinds if k not in ("video", "audio")),
            bit_rate=_number(fmt.get("bit_rate")),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise MediaError(f"ffprobe output for {path} does not read: {e}") from None


def trim(src: Path, dst: Path, seconds: float, *, timeout: float = TRIM_TIMEOUT) -> Path:
    """`src` remuxed (no re-encoding) into `dst`, cut at `seconds`: its first video stream (the
    one probed and checked) and its audio, nothing else."""
    _run(
        [_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
         "-protocol_whitelist", SAFE_PROTOCOLS, "-format_whitelist", SAFE_FORMATS,
         "-i", str(src), "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-t", f"{seconds:g}",
         str(dst)],
        timeout,
    )  # fmt: skip
    return dst
