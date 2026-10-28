"""Thin ffmpeg/ffprobe wrappers used by the audio-description pipeline.

Every input file is opened with `-protocol_whitelist file,pipe` (`input_args`): ffmpeg sniffs a
file's format from its bytes, so an upload named .mp4 that is really an HLS playlist or a concat
list must not be able to make it fetch a URL or read another local file. The pipeline also
refuses any container but MP4/MOV, Matroska and WebM right after probing (`assert_safe_container`).
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_FLOOR_DB = -120.0
SAFE_PROTOCOLS = "file,pipe"
SAFE_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm"  # ffprobe's -format_whitelist
SAFE_CONTAINERS = frozenset(SAFE_FORMATS.split(","))
_NOT_LISTED = re.compile(r"\[(\w+) @ [^\]]*\] Format not on whitelist")
# demuxers that open other files or URLs, or are not media files at all: never accepted, even
# next to an allowed name
UNSAFE_CONTAINERS = frozenset(
    {"hls", "applehttp", "concat", "ffconcat", "image2", "image2pipe", "lavfi", "tee", "sdp",
     "rtp", "rtsp", "dash", "data", "subfile", "playlist"}
)  # fmt: skip


class MediaError(RuntimeError):
    """ffmpeg/ffprobe is missing or a media command failed."""


@dataclass(frozen=True)
class Probe:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    video_codec: str = ""
    audio_codec: str | None = None
    format_name: str = ""  # ffprobe's demuxer names, comma-separated ("mov,mp4,m4a,3gp,3g2,mj2")
    audio_streams: int = 0
    fps_rational: str = ""  # the nominal frame rate as ffprobe gives it ("30000/1001")


def _bin(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise MediaError(f"{name} not found on PATH")
    return path


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(cmd, check=True, capture_output=True, cwd=cwd)
    except subprocess.CalledProcessError as e:
        tail = e.stderr.decode("utf-8", "replace").strip()[-800:]
        raise MediaError(f"{Path(cmd[0]).stem} failed (exit {e.returncode}): {tail}") from e
    except OSError as e:
        raise MediaError(f"could not run {cmd[0]}: {e}") from e


def input_args(path: Path | str) -> list[str]:
    """`-i path` for ffmpeg, locked to local files: no URL, no other protocol."""
    return ["-protocol_whitelist", SAFE_PROTOCOLS, "-i", str(path)]


def wav_input_args(path: Path | str) -> list[str]:
    """input_args for a WAV the engine wrote (a voiced cue, a premix): read as WAV only, never
    handed to another demuxer whatever the file holds."""
    return ["-format_whitelist", "wav", *input_args(path)]


def assert_safe_container(info: Probe) -> None:
    """Raise MediaError unless the probed file is MP4/MOV, Matroska or WebM.

    ffprobe names a demuxer by all its aliases ("mov,mp4,m4a,3gp,3g2,mj2"); one allowed name is
    enough, but an HLS playlist, a concat list, an image sequence, lavfi and the like are
    refused whatever else they are called.
    """
    names = {n.strip().lower() for n in info.format_name.split(",") if n.strip()}
    if names & SAFE_CONTAINERS and not names & UNSAFE_CONTAINERS:
        return
    raise _unsupported(info.format_name)


def _unsupported(format_name: str) -> MediaError:
    return MediaError(
        f"unsupported container {format_name or 'unknown'!r}: only MP4/MOV, Matroska and "
        "WebM video files are accepted"
    )


def _ffmpeg(*args: str) -> subprocess.CompletedProcess[bytes]:
    return _run([_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args])


def _fps(rate: str) -> float:
    num, _, den = rate.partition("/")
    d = float(den) if den else 1.0
    return float(num) / d if d else 0.0


def _rotation(stream: dict) -> int:
    """The display rotation of a video stream in degrees (side data, or the older tag)."""
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            try:
                return int(round(float(side["rotation"])))
            except (TypeError, ValueError):
                return 0
    try:
        return int((stream.get("tags") or {}).get("rotate", 0))
    except (TypeError, ValueError):
        return 0


def probe(path: Path) -> Probe:
    """Duration, displayed size (a quarter-turned video reports its turned size), frame
    rate, audio and container of `path`.

    ffprobe may only pick an MP4/MOV, Matroska or WebM demuxer (-format_whitelist): a playlist
    or a concat list is refused before anything it names is opened, with the same error
    whatever it names, so the answer never tells which files exist."""
    try:
        out = _run(
            [_bin("ffprobe"), "-protocol_whitelist", SAFE_PROTOCOLS,
             "-format_whitelist", SAFE_FORMATS, "-v", "error",
             "-print_format", "json", "-show_format", "-show_streams", str(path)]
        )  # fmt: skip
    except MediaError as e:
        refused = _NOT_LISTED.search(str(e))
        if refused:
            raise _unsupported(refused.group(1)) from None
        raise
    info = json.loads(out.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError(f"no video stream in {path}")
    duration = info.get("format", {}).get("duration") or video.get("duration")
    if duration is None:
        raise MediaError(f"cannot determine duration of {path}")
    width, height = int(video["width"]), int(video["height"])
    if _rotation(video) % 180 == 90:
        width, height = height, width
    return Probe(
        duration=float(duration),
        width=width,
        height=height,
        fps=_fps(video.get("r_frame_rate", "0/1")),
        has_audio=audio is not None,
        video_codec=video.get("codec_name", ""),
        audio_codec=None if audio is None else audio.get("codec_name", ""),
        format_name=str(info.get("format", {}).get("format_name") or ""),
        audio_streams=sum(s.get("codec_type") == "audio" for s in streams),
        fps_rational=str(video.get("r_frame_rate") or ""),
    )


def audio_duration(path: Path) -> float:
    out = _run(
        [_bin("ffprobe"), "-protocol_whitelist", SAFE_PROTOCOLS, "-format_whitelist", "wav",
         "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
         str(path)]
    )  # fmt: skip
    try:
        return float(out.stdout.decode().strip())
    except ValueError as e:
        raise MediaError(f"cannot determine duration of {path}") from e


def extract_wav(src: Path, dst: Path, rate: int = 16000) -> Path:
    """The first audio stream as mono 16-bit PCM on the container's clock: an audio stream
    that starts after the video is padded from 0, exactly as the mix plays it, so transcript
    times, pauses and the mix agree."""
    _ffmpeg(
        *input_args(src), "-map", "0:a:0", "-af", "aresample=async=1:first_pts=0",
        "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dst),
    )  # fmt: skip
    return dst


def make_proxy(src: Path, dst: Path, *, max_bytes: int = 10 * 2**20, height: int = 720) -> Path:
    """`src` re-encoded at most `height` lines high, smaller each attempt until under
    `max_bytes`; an attempt that never gets there leaves no file behind."""
    attempts = list(dict.fromkeys((min(h, height), crf) for h, crf in
                                  [(height, 28), (height, 34), (480, 34), (360, 38)]))  # fmt: skip
    last = 0
    for h, crf in attempts:
        _ffmpeg(
            *input_args(src), "-an", "-vf", f"scale=-2:{h}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-crf", str(crf), "-movflags", "+faststart", str(dst),
        )  # fmt: skip
        last = dst.stat().st_size
        if last < max_bytes:
            return dst
    dst.unlink(missing_ok=True)
    raise MediaError(f"proxy still {last} bytes after all attempts (limit {max_bytes})")


def cut_clip(src: Path, dst: Path, start: float, end: float, *, height: int = 480) -> Path:
    if end <= start:
        raise MediaError(f"invalid clip range {start}..{end}")
    _ffmpeg(
        "-ss", f"{start:.3f}", *input_args(src), "-t", f"{end - start:.3f}",
        "-vf", f"scale=-2:{height}", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-crf", "28", "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", str(dst),
    )  # fmt: skip
    return dst


def atempo(src: Path, dst: Path, factor: float) -> Path:
    """Audio `src` played `factor` times faster, pitch kept (ffmpeg atempo, 0.5..100)."""
    if not 0.5 <= factor <= 100.0:
        raise MediaError(f"atempo factor {factor} outside 0.5..100")
    _ffmpeg(*wav_input_args(src), "-filter:a", f"atempo={factor:.3f}", str(dst))
    return dst


def frame_at(src: Path, t: float, dst: Path) -> Path:
    dst = Path(dst)
    dst.unlink(missing_ok=True)  # a failed extraction must not leave an old frame looking new
    _ffmpeg("-ss", f"{t:.3f}", *input_args(src), "-frames:v", "1", "-q:v", "2", str(dst))
    if not dst.exists():
        raise MediaError(f"no frame at t={t} in {src}")
    return dst


def frames_at(src: Path, shots: list[tuple[float, Path]], *, batch: int = 16) -> list[Path]:
    """One JPEG per (t, dst), `batch` frames per ffmpeg run (each input seeks to its own t):
    launching ffmpeg costs far more than decoding one frame."""
    out: list[Path] = []
    for i in range(0, len(shots), batch):
        chunk = [(t, Path(d)) for t, d in shots[i : i + batch]]
        args: list[str] = []
        for t, _ in chunk:
            args += ["-ss", f"{t:.3f}", *input_args(src)]
        for j, (_, dst) in enumerate(chunk):
            dst.unlink(missing_ok=True)
            args += ["-map", f"{j}:v:0", "-frames:v", "1", "-q:v", "2", str(dst)]
        _ffmpeg(*args)
        missing = [f"{t:.3f}" for t, dst in chunk if not dst.exists()]
        if missing:
            raise MediaError(f"no frame at t={', '.join(missing)} in {src}")
        out += [dst for _, dst in chunk]
    return out


_PTS = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def scene_cuts(src: Path, threshold: float = 0.3) -> list[float]:
    proc = _run(
        [_bin("ffmpeg"), "-hide_banner", "-nostats", *input_args(src), "-an",
         "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"]
    )  # fmt: skip
    return sorted(float(m) for m in _PTS.findall(proc.stderr.decode("utf-8", "replace")))


def loudness_profile(wav: Path, hop: float = 0.1) -> list[tuple[float, float]]:
    with wave.open(str(wav), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if width != 2:
            raise MediaError(f"expected 16-bit PCM wav, got {width * 8}-bit")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if ch > 1:
        data = data[: len(data) // ch * ch].reshape(-1, ch).mean(axis=1)
    x = data.astype(np.float64) / 32768.0
    n = max(1, int(round(hop * rate)))
    out: list[tuple[float, float]] = []
    for i in range(0, len(x), n):
        win = x[i : i + n]
        rms = math.sqrt(float(np.mean(win * win)))
        db = 20 * math.log10(rms) if rms > 0 else _FLOOR_DB
        out.append((i / rate, max(db, _FLOOR_DB)))
    return out
