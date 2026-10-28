"""An upload checked before a run, with the engine's own rules (lull.media: local files only,
the MP4/MOV and Matroska/WebM demuxers only, assert_safe_container) and what the engine does
not do for it: a time limit on each ffprobe/ffmpeg call, limits on what the file says it is,
and the run's own copy of it, made to be bounded whatever the file says.

Limits (over_limits), checked before any work on the two streams the run's copy keeps, the
first video and the first audio stream: at most 1920x1080 (2.1 megapixels, either way up), 60
frames a second, sound of up to 8 channels at 192 kHz, and a file bit rate of at most 100 Mb/s.
Anything unknown or zero on those is refused, and so is a file without a video stream. Other
streams (a phone's data or timecode track, subtitles, attachments, a second picture or sound)
are never read: the copy leaves them out.

The frame rate is the higher of the nominal and average rates; a nominal rate over 240 (a
browser recording's 1000/1) is not believed, and the rate is measured instead from the packets
themselves: the video packets of the first MEASURE_S seconds (the whole window a run may
take), (packets - 1) over the span of their times, never the duration the header states
(ffprobe, under the same time limit). A rate
that cannot be measured (fewer than 2 packets, no span) is refused. The live side also refuses
a file whose copy's length is more than 20 % off the length the file states.

These checks keep honest files cheap to refuse early; what bounds a run's work is the
normalising transcode below (its frame-rate cap, frame cap and time limit), whatever a header
or a measurement says.

The run's copy (transcode): one normalising transcode, and every engine stage works on it only.
The first video stream, scaled to fit 1280x720 (720x1280 upright; never upscaled), at its
stated frame rate capped at 30, at most `seconds` long and 30 frames a second of it; the first
audio stream at 48 kHz, at most 2 channels; H.264/AAC in MP4, no metadata. Decoding refuses
frames over 2.1 megapixels, so a stream that grows past its header stops the copy. A copy
that fails or runs out of time refuses the upload.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from lull.media import SAFE_FORMATS, SAFE_PROTOCOLS, MediaError, Probe

PROBE_TIMEOUT = 20.0
TRANSCODE_TIMEOUT = 180.0

MAX_LONG_SIDE, MAX_SHORT_SIDE, MAX_PIXELS = 1920, 1080, 2_100_000
MAX_FPS = 60.0
FPS_SLACK = 0.5  # a nominal 60 that a phone reports as 60.02
PLAUSIBLE_FPS = 240.0  # a stated rate above this is measured, not believed
MEASURE_S = 60  # seconds of packets the measured rate is taken over: the longest a run takes
FRAME_SLACK = 2  # frames the copy may have past OUT_FPS times its length
MAX_CHANNELS, MAX_SAMPLE_RATE = 8, 192_000
MAX_BIT_RATE = 100_000_000  # bits a second, the whole file
OUT_LONG_SIDE, OUT_SHORT_SIDE = 1280, 720  # the run's copy
OUT_FPS, OUT_CHANNELS, OUT_SAMPLE_RATE = 30, 2, 48_000


class OverLimits(MediaError):
    """The file is outside the limits, in plain words for the visitor (lull.api.live: 422)."""


_OVER_PIXELS = re.compile(r"Picture size (\d+)x(\d+) exceeds specified max pixel count")


@dataclass(frozen=True)
class Media(Probe):
    """The engine's Probe (its first video stream) and what the limits look at: every video
    stream's size and frame rate, every audio stream's channels and sample rate, the other
    streams' kinds and the file's bit rate."""

    videos: tuple[tuple[int, int, float], ...] = ()
    audios: tuple[tuple[int, int], ...] = ()
    others: tuple[str, ...] = ()
    bit_rate: float | None = None
    rate: str = ""  # the first video stream's frame rate as ffprobe states it ("30000/1001")
    measured: bool = False  # the stated rate was not believed and `fps` was measured


def _bin(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise MediaError(f"{name} not found on PATH")
    return path


def _run(
    cmd: list[str], timeout: float, *, pixels: bool = False
) -> subprocess.CompletedProcess[bytes]:
    """Run an ffmpeg tool under `timeout`. With `pixels`, a picture over the -max_pixels cap
    (ffprobe decodes a few frames of every stream to describe them; the cap keeps that cheap)
    is OverLimits, naming its size."""
    name = Path(cmd[0]).stem
    try:
        return subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise MediaError(f"{name} timed out after {timeout:g} s") from None
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", "replace")
        over = _OVER_PIXELS.search(err) if pixels else None
        if over is not None:
            w, h = over.group(1), over.group(2)
            raise OverLimits(
                f"This file holds a {w}x{h} picture. Live descriptions take videos up to "
                f"{MAX_LONG_SIDE}x{MAX_SHORT_SIDE}: export it again at 1080p or less, with "
                "one picture."
            ) from None
        raise MediaError(f"{name} failed (exit {e.returncode}): {err.strip()[-800:]}") from None
    except OSError as e:
        raise MediaError(f"could not run {name}: {e}") from None


def _fps(rate: str) -> float:
    num, _, den = rate.partition("/")
    try:
        d = float(den) if den else 1.0
        return float(num) / d if d else 0.0
    except ValueError:
        return 0.0


def _rate(stream: dict) -> tuple[float, str]:
    """The stream's frame rate, and as stated: the higher of its nominal and average rates
    (either can understate what the packets carry); (0.0, "") when neither is known."""
    rates = [(_fps(str(stream.get(k) or "")), str(stream.get(k) or ""))
             for k in ("r_frame_rate", "avg_frame_rate")]  # fmt: skip
    known = [r for r in rates if r[0] > 0]
    return max(known) if known else (0.0, "")


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
    limits. Unknown or zero is refused (fail closed). A plain Probe (no stream list: the test
    doubles) is checked on its first video stream alone."""
    full = isinstance(info, Media)
    # the streams the run's copy keeps: the first video stream, the first audio stream
    videos = info.videos[:1] if full else ((info.width, info.height, info.fps),)
    audios = info.audios[:1] if full else ()
    for w, h, fps in videos:
        if w <= 0 or h <= 0:
            return "This video does not state its picture size. Export it again as an mp4."
        if not fps > 0 and getattr(info, "measured", False):
            return "This video's frame rate could not be measured. Export it again as an mp4."
        if not fps > 0:
            return "This video does not state its frame rate. Export it again as an mp4."
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
        if channels <= 0 or rate <= 0:
            return (
                "This video's sound does not state its channels or sample rate. Export it "
                "again as an mp4."
            )
        if channels > MAX_CHANNELS or rate > MAX_SAMPLE_RATE:
            return (
                f"This video's sound has {channels} channels at {rate} Hz. Live descriptions "
                f"take up to {MAX_CHANNELS} channels at {MAX_SAMPLE_RATE // 1000} kHz."
            )
    bit_rate = info.bit_rate if full else 1.0
    if bit_rate is None or not bit_rate > 0:
        return "This video does not state its bit rate. Export it again as an mp4."
    if bit_rate > MAX_BIT_RATE:
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
         "-format_whitelist", SAFE_FORMATS, "-max_pixels", str(MAX_PIXELS),
         "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout,
        pixels=True,
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
        fps, rate = _rate(video)
        measured = fps > PLAUSIBLE_FPS  # not believed: measured from the packets
        if measured:
            fps = _measured_rate(path, timeout)
            rate = f"{fps:.6g}" if fps > 0 else ""
        first = next(i for i, s in enumerate(streams) if s.get("codec_type") == "video")
        return Media(
            duration=float(duration),
            width=int(video["width"]),
            height=int(video["height"]),
            fps=fps,
            has_audio=audio is not None,
            video_codec=video.get("codec_name", ""),
            audio_codec=None if audio is None else audio.get("codec_name", ""),
            format_name=str(fmt.get("format_name") or ""),
            audio_streams=kinds.count("audio"),
            fps_rational=str(video.get("r_frame_rate") or ""),
            videos=tuple(
                (_int(s.get("width")), _int(s.get("height")), fps if i == first else _rate(s)[0])
                for i, s in enumerate(streams)
                if s.get("codec_type") == "video"
            ),
            audios=tuple(
                (_int(s.get("channels")), _int(s.get("sample_rate")))
                for s in streams
                if s.get("codec_type") == "audio"
            ),
            others=tuple(k for k in kinds if k not in ("video", "audio")),
            bit_rate=_number(fmt.get("bit_rate")),
            rate=rate,
            measured=measured,
        )
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise MediaError(f"ffprobe output for {path} does not read: {e}") from None


def _measured_rate(path: Path, timeout: float) -> float:
    """The first video stream's frame rate from its own packets: those of the first MEASURE_S
    seconds, (packets - 1) over the span of their times (sorted: B-frames come out of order);
    0.0 when there are fewer than 2 or no span. The header's duration plays no part."""
    out = _run(
        [_bin("ffprobe"), "-v", "error", "-protocol_whitelist", SAFE_PROTOCOLS,
         "-format_whitelist", SAFE_FORMATS, "-max_pixels", str(MAX_PIXELS),
         "-select_streams", "v:0",
         "-read_intervals", f"%+{MEASURE_S}", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", str(path)],
        timeout,
    )  # fmt: skip
    times = sorted(t for t in map(_number, out.stdout.decode().split()) if t is not None)
    if len(times) < 2 or not times[-1] > times[0]:
        return 0.0
    return (len(times) - 1) / (times[-1] - times[0])


def _out_rate(info: Probe) -> str:
    """The copy's frame rate: the stated one (exactly), capped at OUT_FPS."""
    rate = info.rate if isinstance(info, Media) else ""
    if 0 < info.fps <= OUT_FPS:
        return rate or f"{info.fps:.6g}"
    return str(OUT_FPS)


# fit OUT_LONG_SIDE x OUT_SHORT_SIDE the way the picture is turned; never larger than it is
_SCALE = (
    f"scale=w='if(gte(iw,ih),min({OUT_LONG_SIDE},iw),min({OUT_SHORT_SIDE},iw))'"
    f":h='if(gte(iw,ih),min({OUT_SHORT_SIDE},ih),min({OUT_LONG_SIDE},ih))'"
    ":force_original_aspect_ratio=decrease:force_divisible_by=2"
)


def transcode(
    src: Path, dst: Path, seconds: float, info: Probe, *, timeout: float = TRANSCODE_TIMEOUT
) -> Path:
    """The run's copy of `src` (what `info` probed) in `dst`: see the module notes. Raises
    MediaError when it fails or runs out of time."""
    audios = info.audios if isinstance(info, Media) else ()
    channels = min(OUT_CHANNELS, audios[0][0]) if audios and audios[0][0] > 0 else OUT_CHANNELS
    _run(
        [_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", "-nostdin",
         "-protocol_whitelist", SAFE_PROTOCOLS, "-format_whitelist", SAFE_FORMATS,
         "-max_pixels", str(MAX_PIXELS), "-i", str(src),
         "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1", "-map_metadata:s", "-1",
         "-map_chapters", "-1", "-write_tmcd", "0",
         "-t", f"{seconds:g}", "-frames:v", str(math.ceil(seconds * OUT_FPS)),
         "-vf", f"{_SCALE},fps=fps={_out_rate(info)},format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", "-b:a", "128k", "-ar", str(OUT_SAMPLE_RATE), "-ac", str(channels),
         "-movflags", "+faststart", "-f", "mp4", str(dst)],
        timeout,
    )  # fmt: skip
    _check_frames(dst, timeout)
    return dst


def _check_frames(copy: Path, timeout: float) -> None:
    """The copy holds at most OUT_FPS frames a second of its length (+ FRAME_SLACK), counted
    from its packets; MediaError otherwise. The copy is ours (constant rate H.264), so this
    only fails if the transcode's bounds did not hold."""
    out = _run(
        [_bin("ffprobe"), "-v", "error", "-protocol_whitelist", SAFE_PROTOCOLS,
         "-format_whitelist", "mov,mp4,m4a,3gp,3g2,mj2", "-max_pixels", str(MAX_PIXELS),
         "-select_streams", "v:0",
         "-count_packets", "-show_entries", "stream=nb_read_packets:format=duration",
         "-print_format", "json", str(copy)],
        timeout,
    )  # fmt: skip
    try:
        info = json.loads(out.stdout)
        frames = int(info["streams"][0]["nb_read_packets"])
        seconds = float(info["format"]["duration"])
    except (ValueError, KeyError, IndexError, TypeError):
        raise MediaError(f"the frames of {copy.name} could not be counted") from None
    if frames > OUT_FPS * seconds + FRAME_SLACK:
        raise MediaError(f"{copy.name} has {frames} frames over {seconds:g} s")
