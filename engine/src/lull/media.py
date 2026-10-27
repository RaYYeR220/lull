"""Thin ffmpeg/ffprobe wrappers used by the audio-description pipeline."""

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


def _ffmpeg(*args: str) -> subprocess.CompletedProcess[bytes]:
    return _run([_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error", *args])


def _fps(rate: str) -> float:
    num, _, den = rate.partition("/")
    d = float(den) if den else 1.0
    return float(num) / d if d else 0.0


def probe(path: Path) -> Probe:
    out = _run(
        [_bin("ffprobe"), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)]
    )  # fmt: skip
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


def audio_duration(path: Path) -> float:
    out = _run(
        [_bin("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)]
    )  # fmt: skip
    try:
        return float(out.stdout.decode().strip())
    except ValueError as e:
        raise MediaError(f"cannot determine duration of {path}") from e


def extract_wav(src: Path, dst: Path, rate: int = 16000) -> Path:
    _ffmpeg("-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dst))
    return dst


def make_proxy(src: Path, dst: Path, *, max_bytes: int = 10 * 2**20, height: int = 720) -> Path:
    attempts = [(height, 28), (height, 34), (480, 34), (360, 38)]
    last = 0
    for h, crf in attempts:
        _ffmpeg(
            "-i", str(src), "-an", "-vf", f"scale=-2:{h}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-crf", str(crf), "-movflags", "+faststart", str(dst),
        )  # fmt: skip
        last = dst.stat().st_size
        if last < max_bytes:
            return dst
    raise MediaError(f"proxy still {last} bytes after all attempts (limit {max_bytes})")


def cut_clip(src: Path, dst: Path, start: float, end: float, *, height: int = 480) -> Path:
    if end <= start:
        raise MediaError(f"invalid clip range {start}..{end}")
    _ffmpeg(
        "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
        "-vf", f"scale=-2:{height}", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-crf", "28", "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", str(dst),
    )  # fmt: skip
    return dst


def frame_at(src: Path, t: float, dst: Path) -> Path:
    _ffmpeg("-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(dst))
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
            args += ["-ss", f"{t:.3f}", "-i", str(src)]
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
        [_bin("ffmpeg"), "-hide_banner", "-nostats", "-i", str(src), "-an",
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
