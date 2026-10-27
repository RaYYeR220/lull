"""Mix: the programme ducked under every voiced description, normalised to -24 LKFS.

levels               ebur128 measures the programme and every cue first. Each cue is gained to
                     the programme's integrated loudness (+0 LU), kept within -27..-16 LUFS, so
                     a quiet lecture does not get a shouting narrator (or a whispering one).
described-audio.m4a  the programme dipped 10 dB around each voiced standard cue (linear 0.2 s
                     ramps on both sides), the levelled cue laid in at its start, then two
                     passes: ebur128 measures the mix, and one linear gain takes it to -24 LUFS
                     (loudnorm in linear mode). When the true peak or the loudness range rules
                     linear mode out, the same gain goes through a -2.5 dBFS limiter instead
                     (logged), so the dips always survive. AAC, 48 kHz stereo.
described.mp4        the original video stream (copied when MP4 can carry it), the described
                     audio as default track 1 "Described" and the original audio as track 2
                     "Original": a described version (G173) with a selectable track (G78).
extended.mp4         constant frame rate first. At each freeze point (a speech-free instant at
                     or before the extended cue's visual, after any standard cue playing there,
                     on a frame boundary) the frame is held for the cue audio rounded up to
                     whole frames, and the programme pauses in silence for exactly as long, so
                     picture and sound stay in step (G8).

Held cues are never mixed; extended cues only go into extended.mp4. Filter graphs are passed
with `-/filter_complex <file>` (ffmpeg 7 or newer, checked once) and cue audio by relative
path, so long videos stay under the Windows command-line limit.
"""

from __future__ import annotations

import functools
import logging
import math
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel

from lull.media import MediaError, Probe, _bin, _run, probe
from lull.timeline import merge_spans
from lull.types import Cue, Span

log = logging.getLogger(__name__)

AD_AUDIO = "described-audio.m4a"
AD_VIDEO = "described.mp4"
EXTENDED_VIDEO = "extended.mp4"
RATE = 48_000
TARGET_I, TARGET_TP, TARGET_LRA = -24.0, -2.0, 11.0
LOUDNORM = f"I={TARGET_I:g}:TP={TARGET_TP:g}:LRA={TARGET_LRA:g}"
LINEAR_MARGIN = 0.1  # dB of true-peak headroom kept so loudnorm stays linear
LIMITER_MARGIN = 0.5  # the fallback limiter sits this far under TARGET_TP (it reads sample peaks)
CUE_OFFSET_LU = 0.0  # cue loudness relative to the programme
CUE_MIN_LUFS, CUE_MAX_LUFS = -27.0, -16.0
MAX_CUE_GAIN_DB = 30.0
DUCK_DB = -10.0
RAMP = 0.2
FRAME_SAMPLES = 256  # the duck gain is evaluated per frame: 5.3 ms steps along a ramp
SILENT_LUFS = -70.0  # ebur128's floor: quieter than this is silence
AUDIO_BITRATE = "192k"
MIN_SEGMENT = 0.1  # extended pauses closer than this to each other or to an edge are merged
FALLBACK_FPS = Fraction(30)
FFMPEG_MIN_MAJOR = 7
STANDARD_VOICED = ("verified", "edited", "approved")
EXTENDED_VOICED = ("extended", "edited", "approved")
MP4_VIDEO = frozenset({"h264", "hevc", "av1", "mpeg4", "vp9"})
MP4_AUDIO = frozenset({"aac", "mp3", "ac3", "eac3", "alac"})
TTS_NAME = "NVIDIA Magpie TTS"
TTS_TAG = f"Audio description voiced with synthetic speech ({TTS_NAME})."
EVEN_SCALE = "scale=trunc(iw/2)*2:trunc(ih/2)*2"  # libx264 with yuv420p needs even sizes
LANG3 = {
    "en": "eng", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "pt": "por", "nl": "nld",
    "pl": "pol", "uk": "ukr", "ru": "rus", "ja": "jpn", "zh": "zho", "ko": "kor", "ar": "ara",
}  # fmt: skip
_FORMAT = "aformat=sample_fmts=fltp:channel_layouts=stereo"
_CUE_IN = f"aresample={RATE},{_FORMAT}"
_PROGRAMME_IN = f"aresample={RATE}:async=1:first_pts=0,{_FORMAT}"
_NUM = r"(-?(?:\d+(?:\.\d+)?|inf))"
_MEASURES = {  # ebur128's end-of-stream summary
    "input_i": re.compile(rf"Integrated loudness:\s*I:\s*{_NUM} LUFS"),
    "input_thresh": re.compile(rf"Integrated loudness:\s*I:[^\n]*\n\s*Threshold:\s*{_NUM} LUFS"),
    "input_lra": re.compile(rf"Loudness range:\s*LRA:\s*{_NUM} LU"),
    "input_tp": re.compile(rf"True peak:\s*Peak:\s*{_NUM} dBFS"),
}
_SUMMARY = re.compile(r"\[Parsed_ebur128_(\d+) @ [^\]]*\] Summary:")
_VERSION = re.compile(r"ffmpeg version n?(\d+)\.")


class MixResult(BaseModel):
    ad_audio: Path
    ad_video: Path


class Levels(BaseModel):
    programme: float  # integrated LUFS of the programme audio (SILENT_LUFS when there is none)
    cues: dict[str, float]  # cue audio path, as the cue gives it -> integrated LUFS


class Freeze(NamedTuple):
    frame: int  # the programme frame the picture holds on (at the constant frame rate)
    frames: int  # how long it holds, in whole frames
    cues: list[Cue]  # the extended cues voiced while it holds, in order


# -- ffmpeg version ------------------------------------------------------------------------------


def ffmpeg_major(version_text: str) -> int | None:
    """The major version from `ffmpeg -version` output; None for builds that do not say
    (git snapshots)."""
    m = _VERSION.search(version_text)
    return int(m.group(1)) if m else None


@functools.cache
def require_ffmpeg() -> None:
    """Raise MediaError unless ffmpeg is 7 or newer (checked once per process)."""
    text = _run([_bin("ffmpeg"), "-hide_banner", "-version"]).stdout.decode("utf-8", "replace")
    major = ffmpeg_major(text)
    first = text.splitlines()[0] if text else ""
    if major is None:
        log.warning("mix: cannot read the ffmpeg version from %r; assuming 7 or newer", first)
    elif major < FFMPEG_MIN_MAJOR:
        raise MediaError(
            f"ffmpeg {major} found ({first}); the mix needs ffmpeg {FFMPEG_MIN_MAJOR} or newer"
        )


# -- which cues are voiced -----------------------------------------------------------------------


def _with_audio(c: Cue) -> Cue:
    if c.audio_path is None or c.audio_dur is None or not c.audio_dur > 0:
        raise ValueError(f"cue {c.id} is {c.status} but has no voiced audio")
    return c


def voiced(cues: Iterable[Cue]) -> list[Cue]:
    """Standard cues that are voiced: verified, edited or approved, with a gap, in time order.
    Held cues are left out; a voiced status without measured audio is an error."""
    out = [_with_audio(c) for c in cues if c.gap_id is not None and c.status in STANDARD_VOICED]
    return sorted(out, key=lambda c: (c.start, c.id))


def voiced_extended(cues: Iterable[Cue]) -> list[Cue]:
    """Extended cues (no gap) that are voiced, in time order; held ones are left out."""
    out = [_with_audio(c) for c in cues if c.gap_id is None and c.status in EXTENDED_VOICED]
    return sorted(out, key=lambda c: (c.start, c.id))


# -- ducking -----------------------------------------------------------------------------------


def _windows(cues: Sequence[Cue], ramp: float) -> list[tuple[float, float]]:
    """[start - ramp, start + audio_dur + ramp] per cue; windows that meet are merged so the
    programme stays down between descriptions that follow each other closely."""
    merged: list[list[float]] = []
    for a, b in sorted((c.start - ramp, c.start + c.audio_dur + ramp) for c in cues):
        if merged and a <= merged[-1][1] + 1e-9:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _since(a: float) -> str:
    return f"t-{a:.3f}" if a >= 0 else f"t+{-a:.3f}"


def duck_filter(cues: Sequence[Cue], *, duck_db: float = DUCK_DB, ramp: float = RAMP) -> str:
    """An ffmpeg `volume` filter that dips the programme by `duck_db` under every cue.

    Each cue's window is [start - ramp, start + audio_dur + ramp]: the gain falls linearly
    over the first `ramp` seconds, holds at 10^(duck_db/20) while the cue speaks and rises over
    the last `ramp` seconds. `enable` limits the work to the windows; no cue gives `anull`.
    """
    if ramp <= 0:
        raise ValueError(f"ramp must be positive, got {ramp}")
    cues = list(cues)
    if not cues:
        return "anull"
    for c in cues:
        if c.audio_dur is None:
            raise ValueError(f"cue {c.id} has no measured audio to duck under")
    depth = 1 - 10 ** (duck_db / 20)
    wins = _windows(cues, ramp)
    r = f"{ramp:.3f}"
    terms = "+".join(f"clip(({_since(a)})/{r},0,1)*clip(({b:.3f}-t)/{r},0,1)" for a, b in wins)
    enable = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in wins)
    return f"volume=volume='1-{depth:.6f}*min(1,{terms})':eval=frame:enable='{enable}'"


# -- loudness ------------------------------------------------------------------------------------


def _summaries(stderr: str) -> list[str]:
    """ebur128 summary blocks, in the order the filters appear in the graph."""
    marks = list(_SUMMARY.finditer(stderr))
    blocks = []
    for k, m in enumerate(marks):
        end = marks[k + 1].start() if k + 1 < len(marks) else len(stderr)
        blocks.append((int(m.group(1)), stderr[m.end() : end]))
    return [text for _, text in sorted(blocks)]


def _parse(summary: str, keys: Iterable[str] = tuple(_MEASURES)) -> dict[str, float]:
    out = {}
    for key in keys:
        m = _MEASURES[key].search(summary)
        if m is None:
            raise MediaError(f"ebur128 printed no {key}: {summary[-400:]!r}")
        out[key] = float(m.group(1))
    return out


def cue_target(programme_lufs: float) -> float:
    """The loudness a cue is brought to: the programme's, kept within -27..-16 LUFS."""
    return min(max(programme_lufs + CUE_OFFSET_LU, CUE_MIN_LUFS), CUE_MAX_LUFS)


def cue_gain(programme_lufs: float, cue_lufs: float) -> float:
    """dB that bring a cue measured at `cue_lufs` to cue_target (0 for a silent cue)."""
    if not cue_lufs > SILENT_LUFS:
        return 0.0
    gain = cue_target(programme_lufs) - cue_lufs
    return min(max(gain, -MAX_CUE_GAIN_DB), MAX_CUE_GAIN_DB)


def measure_levels(video: Path, info: Probe, cues: Sequence[Cue]) -> Levels:
    """Integrated loudness of the programme and of every voiced cue's audio, one ffmpeg run."""
    require_ffmpeg()
    paths = list(dict.fromkeys(str(_with_audio(c).audio_path) for c in cues))
    if not info.has_audio and not paths:
        return Levels(programme=SILENT_LUFS, cues={})
    cwd, inputs = _relative([Path(p) for p in paths])
    chains, labels = [], ""
    if info.has_audio:
        chains.append(f"[0:a:0]{_PROGRAMME_IN},ebur128=framelog=verbose[l0]")
        labels += "[l0]"
    cmd = [_bin("ffmpeg"), "-y", "-hide_banner", "-nostats", "-i", str(Path(video).resolve())]
    for i, p in enumerate(inputs, start=1):
        cmd += ["-i", p]
        chains.append(f"[{i}:a:0]{_CUE_IN},ebur128=framelog=verbose[l{i}]")
        labels += f"[l{i}]"
    graph = ";".join(chains) + f";{labels}concat=n={len(chains)}:v=0:a=1[all]"
    with tempfile.TemporaryDirectory(prefix="lull-levels-") as tmp:
        graph_file = Path(tmp) / "levels.graph"
        graph_file.write_text(graph, encoding="utf-8")
        cmd += ["-/filter_complex", str(graph_file), "-map", "[all]", "-f", "null", "-"]
        proc = _run(cmd, cwd=cwd)
    found = [
        _parse(s, ["input_i"])["input_i"]
        for s in _summaries(proc.stderr.decode("utf-8", "replace"))
    ]
    if len(found) != len(chains):
        raise MediaError(f"expected {len(chains)} loudness readings, ebur128 printed {len(found)}")
    programme = found.pop(0) if info.has_audio else SILENT_LUFS
    return Levels(programme=programme, cues=dict(zip(paths, found, strict=True)))


def _gain(levels: Levels, c: Cue) -> float:
    try:
        return cue_gain(levels.programme, levels.cues[str(c.audio_path)])
    except KeyError:
        raise ValueError(f"no loudness measured for cue {c.id} ({c.audio_path})") from None


def _clamp(stats: dict[str, float], key: str, lo: float, hi: float) -> float:
    v = stats[key]
    if not math.isfinite(v):
        raise MediaError(f"loudness measurement has no usable {key}: {stats}")
    return min(max(v, lo), hi)


def _normalise(stats: dict[str, float]) -> str:
    """One gain to -24 LUFS: loudnorm in linear mode when the true peak and loudness range
    allow it, otherwise the same gain through a limiter (logged). A silent mix is left be."""
    level = stats["input_i"]
    if not level > SILENT_LUFS:
        log.warning("mix: the mix is silent (%.1f LUFS); left unnormalised", level)
        return "anull"
    gain = TARGET_I - level
    tp, lra = stats["input_tp"], stats["input_lra"]
    if tp + gain <= TARGET_TP - LINEAR_MARGIN and lra <= TARGET_LRA:
        return (
            f"loudnorm={LOUDNORM}"
            f":measured_I={_clamp(stats, 'input_i', -99, 0):.2f}"
            f":measured_TP={_clamp(stats, 'input_tp', -99, 99):.2f}"
            f":measured_LRA={_clamp(stats, 'input_lra', 0, 99):.2f}"
            f":measured_thresh={_clamp(stats, 'input_thresh', -99, 0):.2f}:linear=true"
        )
    log.warning(
        "mix: loudnorm cannot stay linear (true peak %.1f dBFS + %.1f dB gain, LRA %.1f LU);"
        " applying the gain through a limiter",
        tp, gain, lra,
    )  # fmt: skip
    limit = 10 ** ((TARGET_TP - LIMITER_MARGIN) / 20)
    return f"volume={gain:.2f}dB,alimiter=limit={limit:.4f}:level=0:latency=1"


# -- filter graphs -----------------------------------------------------------------------------


def _programme(info: Probe) -> str:
    """The programme audio as 48 kHz float stereo starting at 0, exactly as long as the video
    (silence when the video has no audio stream)."""
    d = f"{info.duration:.3f}"
    if info.has_audio:
        return f"[0:a:0]{_PROGRAMME_IN},apad=whole_dur={d},atrim=end={d}"
    return f"anullsrc=r={RATE}:cl=stereo,{_FORMAT},atrim=end={d}"


def _premix_graph(programme: str, duck: str, placements: Sequence[tuple[float, float]]) -> str:
    """`programme` (a chain ending in an unlabelled output) ducked; cue inputs 1..n levelled
    (gain in dB), delayed to their starts (in samples) and summed without level scaling.
    [out] is the mix, [meas] its EBU R128 measurement."""
    parts = [f"{programme}[base]", f"[base]asetnsamples=n={FRAME_SAMPLES},{duck}[prog]"]
    labels = "[prog]"
    for i, (t, gain) in enumerate(placements, start=1):
        delay = max(0, round(t * RATE))
        parts.append(f"[{i}:a:0]{_CUE_IN},volume={gain:.2f}dB,adelay=delays={delay}S:all=1[c{i}]")
        labels += f"[c{i}]"
    if placements:
        parts.append(
            f"{labels}amix=inputs={len(placements) + 1}:normalize=0:duration=first"
            ":dropout_transition=0[mix]"
        )
    else:
        parts.append("[prog]anull[mix]")
    parts.append("[mix]asplit=2[out][m];[m]ebur128=peak=true:framelog=verbose[meas]")
    return ";".join(parts)


def _relative(paths: Sequence[Path]) -> tuple[Path | None, list[str]]:
    """A working directory and the paths relative to it (short command lines), or no
    directory and absolute paths when they share no root."""
    if not paths:
        return None, []
    absolute = [p.resolve() for p in paths]
    try:
        root = Path(os.path.commonpath([p.parent for p in absolute]))
    except ValueError:
        return None, [str(p) for p in absolute]
    return root, [os.path.relpath(p, root) for p in absolute]


def _premix(
    video: Path, programme: str, duck_cues: Sequence[Cue],
    placements: Sequence[tuple[float, Path, float]], wav: Path,
) -> dict[str, float]:  # fmt: skip
    """Render the un-normalised mix to a float WAV and measure it in the same run."""
    graph_file = wav.with_suffix(".graph")
    graph = _premix_graph(programme, duck_filter(duck_cues), [(t, g) for t, _, g in placements])
    graph_file.write_text(graph, encoding="utf-8")
    cwd, inputs = _relative([p for _, p, _ in placements])
    cmd = [_bin("ffmpeg"), "-y", "-hide_banner", "-nostats", "-i", str(video.resolve())]
    for p in inputs:
        cmd += ["-i", p]
    cmd += [
        "-/filter_complex", str(graph_file.resolve()),
        "-map", "[out]", "-c:a", "pcm_f32le", str(wav.resolve()),
        "-map", "[meas]", "-f", "null", "-",
    ]  # fmt: skip
    proc = _run(cmd, cwd=cwd)
    summaries = _summaries(proc.stderr.decode("utf-8", "replace"))
    if not summaries:
        raise MediaError("ebur128 printed no measurement of the mix")
    return _parse(summaries[-1])


def _aac(stream: str = "a") -> list[str]:
    s = f":{stream}"
    return [f"-c{s}", "aac", f"-b{s}", AUDIO_BITRATE, f"-ar{s}", str(RATE), f"-ac{s}", "2"]


def _video_codec(info: Probe) -> list[str]:
    if info.video_codec in MP4_VIDEO:
        return ["-c:v", "copy"]
    return ["-vf", EVEN_SCALE, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p"]  # fmt: skip


def _track(index: int, title: str, lang: str) -> list[str]:
    s = f"-metadata:s:a:{index}"
    return [f"{s}", f"handler_name={title}", f"{s}", f"title={title}", f"{s}", f"language={lang}"]


def _lang3(language: str) -> str:
    return LANG3.get(language.split("-")[0].lower(), "und")


def _disclose(voiced_any: bool) -> list[str]:
    return ["-metadata", f"comment={TTS_TAG}"] if voiced_any else []


# -- the standard mix --------------------------------------------------------------------------


def mix(
    video: Path, cues: list[Cue], out_dir: Path, *, info: Probe | None = None,
    language: str = "en", levels: Levels | None = None,
) -> MixResult:  # fmt: skip
    """described-audio.m4a and described.mp4 from the voiced standard cues (see module doc).
    `levels` (from measure_levels) is measured here when not given."""
    require_ffmpeg()
    video, out_dir = Path(video), Path(out_dir)
    info = info or probe(video)
    spoken = voiced(cues)
    levels = levels if levels is not None else measure_levels(video, info, spoken)
    lang = _lang3(language)
    out_dir.mkdir(parents=True, exist_ok=True)
    ad_audio, ad_video = out_dir / AD_AUDIO, out_dir / AD_VIDEO
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".mix-") as tmp:
        premix = Path(tmp) / "premix.wav"
        placements = [(c.start, Path(c.audio_path), _gain(levels, c)) for c in spoken]
        stats = _premix(video, _programme(info), spoken, placements, premix)
        graph = f"[1:a]{_normalise(stats)},aresample={RATE},asplit=2[a1][a2]"
        original = ["-map", "0:a:0"] if info.has_audio else []
        keep = ["-c:a:1", "copy"] if info.audio_codec in MP4_AUDIO else _aac("a:1")
        cmd = [
            _bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video.resolve()), "-i", str(premix),
            "-filter_complex", graph,
            "-map", "[a1]", *_aac(), *_track(0, "Described", lang), *_disclose(bool(spoken)),
            "-movflags", "+faststart", str(ad_audio),
            "-map", "0:v:0", "-map", "[a2]", *original, *_video_codec(info), *_aac("a:0"),
            *(keep if info.has_audio else []), *_track(0, "Described", lang),
            *(_track(1, "Original", lang) if info.has_audio else []),
            "-disposition:a:0", "default", *(["-disposition:a:1", "0"] if info.has_audio else []),
            *_disclose(bool(spoken)), "-movflags", "+faststart", str(ad_video),
        ]  # fmt: skip
        _run(cmd)
    log.info("mix: %d cues mixed into %s and %s", len(spoken), AD_AUDIO, AD_VIDEO)
    return MixResult(ad_audio=ad_audio, ad_video=ad_video)


# -- the extended version ------------------------------------------------------------------------


def frame_rate(info: Probe) -> Fraction:
    """The constant frame rate the extended version is made at (the source's nominal rate)."""
    if not 1.0 <= info.fps <= 240.0:
        log.warning("mix: frame rate %.3f is implausible; using %s", info.fps, FALLBACK_FPS)
        return FALLBACK_FPS
    return Fraction(info.fps).limit_denominator(1001)


def _speech_free(t: float, talk: Sequence[Span]) -> float:
    """`t`, or when it falls inside speech the start of that speech (the end when the speech
    starts the programme): the latest instant at or before t where nobody is speaking."""
    for s in talk:
        if s.start < t < s.end:
            return s.start if s.start > 0 else s.end
    return t


def freeze_points(
    cues_extended: Sequence[Cue], info: Probe, *, standard: Sequence[Cue] = (),
    speech: Sequence[Span] = (),
) -> tuple[Fraction, list[Freeze]]:  # fmt: skip
    """Where the extended version holds the picture, at the constant frame rate it is made at.

    Each voiced extended cue's start (its visual) moves to a speech-free instant (_speech_free),
    then past any voiced standard cue still speaking there, then onto the neighbouring frame
    boundary that is still free (down, or up when it was moved later). It is kept MIN_SEGMENT
    inside the video; cues closer than that share one freeze. A freeze lasts the cues' audio
    rounded up to whole frames.
    """
    fps = frame_rate(info)
    talk = merge_spans(list(speech))
    std = voiced(standard)
    min_frames = max(1, math.ceil(MIN_SEGMENT * fps))
    last = max(0, math.floor(info.duration * fps) - min_frames)
    busy = [(s.start, s.end) for s in talk] + [(c.start, c.start + c.audio_dur) for c in std]

    def frame(t: float, later: bool) -> int:
        """The frame boundary next to t (after it when t was moved later) that is not inside
        speech or a standard cue; the other neighbour when that one is."""
        down, up = math.floor(t * fps + 1e-6), math.ceil(t * fps - 1e-6)
        order = (up, down) if later else (down, up)
        for n in order:
            if not any(a < n / fps < b for a, b in busy):
                return n
        return order[0]

    groups: list[list] = []
    for c in voiced_extended(cues_extended):
        t = _speech_free(c.start, talk)
        later = t > c.start
        for s in std:
            if s.start < t < s.start + s.audio_dur:
                t, later = s.start + s.audio_dur, True
        n = min(max(frame(t, later), 0), last)
        n = 0 if n < min_frames else n
        if groups and n - groups[-1][0] < min_frames:
            groups[-1][1].append(c)
        else:
            groups.append([n, [c]])
    freezes = [
        Freeze(frame=n, frames=math.ceil(sum(c.audio_dur for c in g) * fps - 1e-6), cues=g)
        for n, g in groups
    ]
    return fps, freezes


def _join(head: str, chains: list[str], kind: str) -> str:
    """`head` split into one copy per chain, each chain applied, the results concatenated."""
    if len(chains) == 1:
        return f"{head},{chains[0]}"
    n = len(chains)
    split = "split" if kind == "v" else "asplit"
    concat = f"concat=n={n}:v=1:a=0" if kind == "v" else f"concat=n={n}:v=0:a=1"
    outs = "".join(f"[{kind}{j}]" for j in range(n))
    body = ";".join(f"[{kind}{j}]{ch}[{kind}s{j}]" for j, ch in enumerate(chains))
    joined = "".join(f"[{kind}s{j}]" for j in range(n))
    return f"{head},{split}={n}{outs};{body};{joined}{concat}"


def extended_video(
    video: Path, cues_extended: list[Cue], out_dir: Path, *, standard: Sequence[Cue] = (),
    speech: Sequence[Span] = (), info: Probe | None = None, language: str = "en",
    levels: Levels | None = None,
) -> Path | None:  # fmt: skip
    """extended.mp4 (see module doc and freeze_points); None when no extended cue is voiced.
    Every time below is a whole number of frames, so picture and sound end together."""
    ext = voiced_extended(cues_extended)
    if not ext:
        return None
    require_ffmpeg()
    video, out_dir = Path(video), Path(out_dir)
    info = info or probe(video)
    std = voiced(standard)
    levels = levels if levels is not None else measure_levels(video, info, std + ext)
    fps, freezes = freeze_points(ext, info, standard=std, speech=speech)

    def sec(frames: int) -> float:
        return float(Fraction(frames) / fps)

    def samples(frames: int) -> int:
        return round(Fraction(frames) / fps * RATE)

    def shift(t: float) -> float:
        return t + sum(sec(f.frames) for f in freezes if sec(f.frame) <= t + 1e-9)

    moved = [c.model_copy(update={"start": shift(c.start)}) for c in std]
    placements = [(c.start, Path(c.audio_path), _gain(levels, c)) for c in moved]
    before = 0  # frames of pause inserted so far
    for f in freezes:
        t = sec(f.frame + before)
        for c in f.cues:
            placements.append((t, Path(c.audio_path), _gain(levels, c)))
            t += c.audio_dur
        before += f.frames

    edges = [0, *(f.frame for f in freezes)]
    audio_chains, video_chains = [], []
    for j, a in enumerate(edges):
        if j == 0 and freezes[0].frame == 0:
            continue  # nothing plays before a freeze at the first frame
        b = edges[j + 1] if j + 1 < len(edges) else None
        hold = freezes[j - 1].frames if j > 0 else 0
        v_end, a_end = ("", "") if b is None else (f":end_frame={b}", f":end_sample={samples(b)}")
        video_chains.append(
            f"trim=start_frame={a}{v_end},setpts=PTS-STARTPTS"
            + (f",tpad=start_mode=clone:start={hold}" if hold else "")
        )
        audio_chains.append(
            f"atrim=start_sample={samples(a)}{a_end},asetpts=PTS-STARTPTS"
            + (f",adelay=delays={samples(hold)}S:all=1" if hold else "")
        )
    programme = _join(_programme(info), audio_chains, "a")
    head = f"[0:v:0]fps={fps.numerator}/{fps.denominator},{EVEN_SCALE}"
    picture = _join(head, video_chains, "v") + ",format=yuv420p[v]"

    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / EXTENDED_VIDEO
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".extended-") as tmp:
        premix = Path(tmp) / "premix.wav"
        stats = _premix(video, programme, moved, placements, premix)
        graph_file = Path(tmp) / "final.graph"
        graph_file.write_text(
            f"{picture};[1:a]{_normalise(stats)},aresample={RATE}[a]", encoding="utf-8"
        )
        _run(
            [_bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
             "-i", str(video.resolve()), "-i", str(premix),
             "-/filter_complex", str(graph_file),
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             *_aac(), *_track(0, "Described (extended)", _lang3(language)), *_disclose(True),
             "-movflags", "+faststart", str(dst)]
        )  # fmt: skip
    added = sec(sum(f.frames for f in freezes))
    log.info("mix: %d extended pauses, %.2f s added, in %s", len(freezes), added, dst.name)
    return dst
