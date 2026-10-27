"""Mix: the programme ducked under every voiced description, normalised to -24 LKFS.

described-audio.m4a  the programme audio dipped 10 dB around each voiced standard cue (linear
                     0.2 s ramps on both sides), the cue audio laid in at its start, then
                     normalised in two passes: ebur128 measures the mix and loudnorm (I=-24,
                     TP=-2, LRA=11) applies one linear gain whenever the true peak allows
                     it, so the dips survive; AAC, 48 kHz stereo. (loudnorm's own first pass
                     under-reads the last seconds: 1 dB on a 6 s clip.)
described.mp4        the original video stream (copied when MP4 can carry it), the described
                     audio as default track 1 "Described" and the original audio as track 2
                     "Original": a described version (G173) with a selectable track (G78).
extended.mp4         the frame at each extended cue's start frozen for as long as its audio,
                     the programme silent meanwhile, every cue voiced (G8).

Held cues are never mixed; extended cues only go into extended.mp4. Filter graphs are passed
in a file and cue audio by relative path, so long videos stay under the Windows command-line
limit.
"""

from __future__ import annotations

import logging
import math
import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

from pydantic import BaseModel

from lull.media import MediaError, Probe, _bin, _run, probe
from lull.types import Cue

log = logging.getLogger(__name__)

AD_AUDIO = "described-audio.m4a"
AD_VIDEO = "described.mp4"
EXTENDED_VIDEO = "extended.mp4"
RATE = 48_000
LOUDNORM = "I=-24:TP=-2:LRA=11"
DUCK_DB = -10.0
RAMP = 0.2
FRAME_SAMPLES = 256  # the duck gain is evaluated per frame: 5.3 ms steps along a ramp
SILENT_LUFS = -70.0  # a mix quieter than this is silence: nothing to normalise
AUDIO_BITRATE = "192k"
MIN_SEGMENT = 0.1  # extended pauses closer than this to each other or to an edge are merged
STANDARD_VOICED = ("verified", "edited")
EXTENDED_VOICED = ("extended", "edited")
MP4_VIDEO = frozenset({"h264", "hevc", "av1", "mpeg4", "vp9"})
MP4_AUDIO = frozenset({"aac", "mp3", "ac3", "eac3", "alac"})
TTS_TAG = "Audio description voiced with synthetic speech (NVIDIA Magpie TTS)."
LANG3 = {
    "en": "eng", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "pt": "por", "nl": "nld",
    "pl": "pol", "uk": "ukr", "ru": "rus", "ja": "jpn", "zh": "zho", "ko": "kor", "ar": "ara",
}  # fmt: skip
_STEREO = f"aresample={RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
_NUM = r"(-?(?:\d+(?:\.\d+)?|inf))"
_MEASURES = {  # ebur128's end-of-stream summary
    "input_i": re.compile(rf"Integrated loudness:\s*I:\s*{_NUM} LUFS"),
    "input_thresh": re.compile(rf"Integrated loudness:\s*I:[^\n]*\n\s*Threshold:\s*{_NUM} LUFS"),
    "input_lra": re.compile(rf"Loudness range:\s*LRA:\s*{_NUM} LU"),
    "input_tp": re.compile(rf"True peak:\s*Peak:\s*{_NUM} dBFS"),
}


class MixResult(BaseModel):
    ad_audio: Path
    ad_video: Path


# -- which cues are voiced -----------------------------------------------------------------------


def _with_audio(c: Cue) -> Cue:
    if c.audio_path is None or c.audio_dur is None or not c.audio_dur > 0:
        raise ValueError(f"cue {c.id} is {c.status} but has no voiced audio")
    return c


def voiced(cues: Iterable[Cue]) -> list[Cue]:
    """Standard cues that are voiced: verified or edited, with a gap, in time order. Held cues
    are left out; a voiced status without measured audio is an error."""
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


# -- filter graphs -----------------------------------------------------------------------------


def _programme(info: Probe) -> str:
    """The programme audio as 48 kHz float stereo, exactly as long as the video (silence when
    the video has no audio stream)."""
    d = f"{info.duration:.3f}"
    if info.has_audio:
        return f"[0:a:0]{_STEREO},apad=whole_dur={d},atrim=end={d}"
    return f"anullsrc=r={RATE}:cl=stereo,aformat=sample_fmts=fltp,atrim=end={d}"


def _premix_graph(programme: str, duck: str, starts: Sequence[float]) -> str:
    """`programme` (a chain ending in an unlabelled output) ducked, cue inputs 1..n delayed to
    their starts and summed without level scaling; [out] is the mix, [meas] its EBU R128
    measurement."""
    parts = [f"{programme}[base]", f"[base]asetnsamples=n={FRAME_SAMPLES},{duck}[prog]"]
    labels = "[prog]"
    for i, t in enumerate(starts, start=1):
        ms = max(0, round(t * 1000))
        parts.append(f"[{i}:a:0]{_STEREO},adelay=delays={ms}:all=1[c{i}]")
        labels += f"[c{i}]"
    if starts:
        parts.append(
            f"{labels}amix=inputs={len(starts) + 1}:normalize=0:duration=first"
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


def _loudness_stats(stderr: str) -> dict[str, float]:
    """I, its gate threshold, LRA and true peak from ebur128's summary."""
    summary = stderr[stderr.rfind("Summary:") :]
    out = {}
    for key, pattern in _MEASURES.items():
        m = pattern.search(summary)
        if m is None:
            raise MediaError(f"ebur128 printed no {key}: {summary[-400:]!r}")
        out[key] = float(m.group(1))
    return out


def _premix(
    video: Path, programme: str, duck_cues: Sequence[Cue],
    placements: Sequence[tuple[float, Path]], wav: Path,
) -> dict[str, float]:  # fmt: skip
    """Render the un-normalised mix to a float WAV and measure it in the same run."""
    graph_file = wav.with_suffix(".graph")
    graph = _premix_graph(programme, duck_filter(duck_cues), [t for t, _ in placements])
    graph_file.write_text(graph, encoding="utf-8")
    cwd, inputs = _relative([Path(p) for _, p in placements])
    cmd = [_bin("ffmpeg"), "-y", "-hide_banner", "-nostats", "-i", str(video.resolve())]
    for p in inputs:
        cmd += ["-i", p]
    cmd += [
        "-/filter_complex", str(graph_file.resolve()),
        "-map", "[out]", "-c:a", "pcm_f32le", str(wav.resolve()),
        "-map", "[meas]", "-f", "null", "-",
    ]  # fmt: skip
    proc = _run(cmd, cwd=cwd)
    return _loudness_stats(proc.stderr.decode("utf-8", "replace"))


def _clamp(stats: dict[str, float], key: str, lo: float, hi: float) -> float:
    v = stats[key]
    if not math.isfinite(v):
        raise MediaError(f"loudness measurement has no usable {key}: {stats}")
    return min(max(v, lo), hi)


def _normalise(stats: dict[str, float]) -> str:
    """loudnorm fed the measurement: one linear gain to -24 LUFS when the true peak allows it
    (loudnorm falls back to its dynamic mode otherwise). A silent mix is left as it is."""
    if not stats["input_i"] > SILENT_LUFS:
        log.warning("mix: the mix is silent (%.1f LUFS); left unnormalised", stats["input_i"])
        return "anull"
    return (
        f"loudnorm={LOUDNORM}"
        f":measured_I={_clamp(stats, 'input_i', -99, 0):.2f}"
        f":measured_TP={_clamp(stats, 'input_tp', -99, 99):.2f}"
        f":measured_LRA={_clamp(stats, 'input_lra', 0, 99):.2f}"
        f":measured_thresh={_clamp(stats, 'input_thresh', -99, 0):.2f}:linear=true"
    )


def _aac(stream: str = "a") -> list[str]:
    s = f":{stream}"
    return [f"-c{s}", "aac", f"-b{s}", AUDIO_BITRATE, f"-ar{s}", str(RATE), f"-ac{s}", "2"]


def _video_codec(info: Probe) -> list[str]:
    if info.video_codec in MP4_VIDEO:
        return ["-c:v", "copy"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def _track(index: int, title: str, lang: str) -> list[str]:
    s = f"-metadata:s:a:{index}"
    return [f"{s}", f"handler_name={title}", f"{s}", f"title={title}", f"{s}", f"language={lang}"]


def _lang3(language: str) -> str:
    return LANG3.get(language.split("-")[0].lower(), "und")


# -- the standard mix --------------------------------------------------------------------------


def mix(
    video: Path, cues: list[Cue], out_dir: Path, *, info: Probe | None = None,
    language: str = "en",
) -> MixResult:  # fmt: skip
    """described-audio.m4a and described.mp4 from the voiced standard cues (see module doc)."""
    video, out_dir = Path(video), Path(out_dir)
    info = info or probe(video)
    spoken = voiced(cues)
    lang = _lang3(language)
    out_dir.mkdir(parents=True, exist_ok=True)
    ad_audio, ad_video = out_dir / AD_AUDIO, out_dir / AD_VIDEO
    with tempfile.TemporaryDirectory(dir=out_dir, prefix=".mix-") as tmp:
        premix = Path(tmp) / "premix.wav"
        placements = [(c.start, Path(c.audio_path)) for c in spoken]
        stats = _premix(video, _programme(info), spoken, placements, premix)
        graph = f"[1:a]{_normalise(stats)},aresample={RATE},asplit=2[a1][a2]"
        original = ["-map", "0:a:0"] if info.has_audio else []
        keep = ["-c:a:1", "copy"] if info.audio_codec in MP4_AUDIO else _aac("a:1")
        cmd = [
            _bin("ffmpeg"), "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video.resolve()), "-i", str(premix),
            "-filter_complex", graph,
            "-map", "[a1]", *_aac(), *_track(0, "Described", lang),
            "-metadata", f"comment={TTS_TAG}", "-movflags", "+faststart", str(ad_audio),
            "-map", "0:v:0", "-map", "[a2]", *original, *_video_codec(info), *_aac("a:0"),
            *(keep if info.has_audio else []), *_track(0, "Described", lang),
            *(_track(1, "Original", lang) if info.has_audio else []),
            "-disposition:a:0", "default", *(["-disposition:a:1", "0"] if info.has_audio else []),
            "-metadata", f"comment={TTS_TAG}", "-movflags", "+faststart", str(ad_video),
        ]  # fmt: skip
        _run(cmd)
    log.info("mix: %d cues mixed into %s and %s", len(spoken), AD_AUDIO, AD_VIDEO)
    return MixResult(ad_audio=ad_audio, ad_video=ad_video)


# -- the extended version ------------------------------------------------------------------------


def _pauses(
    cues: Sequence[Cue], duration: float, standard: Sequence[Cue] = ()
) -> list[tuple[float, list[Cue]]]:
    """Where the picture stops, and which extended cues speak there (in order). A pause that
    would cut into a voiced standard cue waits for it to finish; times are clamped into the
    video; cues closer than MIN_SEGMENT share one pause."""
    hi = max(0.0, duration - MIN_SEGMENT)
    groups: list[tuple[float, list[Cue]]] = []
    for c in cues:
        t = c.start
        for s in standard:
            if s.start < t < s.start + s.audio_dur:
                t = s.start + s.audio_dur
        t = min(max(t, 0.0), hi)
        t = 0.0 if t < MIN_SEGMENT else round(t, 3)
        if groups and t - groups[-1][0] < MIN_SEGMENT:
            groups[-1][1].append(c)
        else:
            groups.append((t, [c]))
    return groups


def _segments(bounds: list[float], pauses: list[float]) -> list[tuple[float, float | None, float]]:
    """(start, end or None for the tail, pause before it) per stretch of programme; an empty
    first stretch (a pause at 0) is skipped."""
    edges = [0.0, *bounds]
    out = []
    for j, a in enumerate(edges):
        if j == 0 and bounds and bounds[0] == 0.0:
            continue  # nothing plays before a pause at 0
        b = edges[j + 1] if j + 1 < len(edges) else None
        out.append((a, b, pauses[j - 1] if j > 0 else 0.0))
    return out


def _trim(kind: str, a: float, b: float | None) -> str:
    end = "" if b is None else f":end={b:.3f}"
    pts = "setpts" if kind == "v" else "asetpts"
    prefix = "" if kind == "v" else "a"
    return f"{prefix}trim=start={a:.3f}{end},{pts}=PTS-STARTPTS"


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
    info: Probe | None = None, language: str = "en",
) -> Path | None:  # fmt: skip
    """extended.mp4: at each voiced extended cue the frame at its start is held (tpad clone)
    and the programme paused (silence) for the cue's audio duration; the standard cues are
    voiced and ducked at their shifted times. None when no extended cue is voiced."""
    ext = voiced_extended(cues_extended)
    if not ext:
        return None
    video, out_dir = Path(video), Path(out_dir)
    info = info or probe(video)
    std = voiced(standard)
    groups = _pauses(ext, info.duration, std)
    bounds = [t for t, _ in groups]
    pauses = [sum(c.audio_dur for c in g) for _, g in groups]

    def shift(t: float) -> float:
        return t + sum(p for b, p in zip(bounds, pauses, strict=True) if b <= t + 1e-9)

    moved = [c.model_copy(update={"start": shift(c.start)}) for c in std]
    placements = [(c.start, Path(c.audio_path)) for c in moved]
    before = 0.0
    for (b, group), p in zip(groups, pauses, strict=True):
        t = b + before
        for c in group:
            placements.append((t, Path(c.audio_path)))
            t += c.audio_dur
        before += p

    segs = _segments(bounds, pauses)
    audio_chains = [
        _trim("a", a, b) + (f",adelay=delays={round(p * 1000)}:all=1" if p > 0 else "")
        for a, b, p in segs
    ]
    video_chains = [
        _trim("v", a, b) + (f",tpad=start_mode=clone:start_duration={p:.3f}" if p > 0 else "")
        for a, b, p in segs
    ]
    programme = _join(_programme(info), audio_chains, "a")
    picture = _join("[0:v:0]null", video_chains, "v") + ",format=yuv420p[v]"

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
             *_aac(), *_track(0, "Described (extended)", _lang3(language)),
             "-metadata", f"comment={TTS_TAG}", "-movflags", "+faststart", str(dst)]
        )  # fmt: skip
    log.info("mix: %d extended pauses, %.2f s added, in %s", len(groups), sum(pauses), dst.name)
    return dst
