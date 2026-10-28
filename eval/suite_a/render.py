"""Suite A rendering: each spec becomes slides (matplotlib, 1280x720), a narration track (NVIDIA
Magpie TTS, voice "Aria", through the local speech server) and an H.264/AAC video; then the
answer key is built from the specs and the timelines.

    uv run python -m suite_a.render            # render every lecture, then write the key
    uv run python -m suite_a.render --only a03 # render one lecture (the key is not written)
    uv run python -m suite_a.render --key      # write key.json + key.sha256 from the renders

Outputs (gitignored) go to suite_a/out/<id>/: slide_<k>.png, narration.wav, video.mp4 and
timeline.json (slide start/end, every sentence's start/end, the speech spans, file hashes).

Nothing here is random. The slides use one fixed style (suite_a.style) and the bundled DejaVu
Sans font; PNGs carry no timestamp. TTS answers are cached by (voice, text) and the server
returned identical bytes for identical requests in our checks. Each sentence's leading and
trailing silence below TRIM_DB is cut, so a sentence's span in the timeline is where the voice
is. The timeline is laid out in samples (22050 Hz): slide k starts at the first video frame
(1/25 s) at or after the previous slide's last sentence ends; before each sentence comes its
planned pause; after the last sentence TAIL_S of silence. ffmpeg is told to write bit-exact
output; its encoder version is recorded in the timeline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import textwrap
import wave
from pathlib import Path

import numpy as np

from suite_a import style
from suite_a.specs import (
    DECOY_KINDS,
    MIN_VISUAL_PER_SLIDE,
    SAID,
    SUBJECTS,
    UNSAID,
    check_committed,
    load_spec,
    spec_path,
    write_json,
)
from suite_a.text import fmt_value, numbers_in

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
TTS_CACHE = OUT / "tts"
KEY_PATH = HERE / "key.json"
KEY_SHA_PATH = HERE / "key.sha256"
SAMPLE_RATE = 22050
RENDER_VERSION = 1


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# -- slides -------------------------------------------------------------------------------------


def _tick_label(t: float) -> str:
    return f"{t:g}" if abs(t) < 1e6 else f"{t:.0f}"


def render_slide(slide: dict, k: int, n: int, dst: Path) -> dict:
    """Draw slide k of n to `dst` (PNG, WIDTH x HEIGHT) and return what is on it beyond the
    spec: the vertical-axis ticks. Raises ValueError when text would leave its area."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.lines import Line2D

    w, h, dpi = style.WIDTH, style.HEIGHT, style.DPI
    fig = Figure(figsize=(w / dpi, h / dpi), dpi=dpi, facecolor=style.BACKGROUND)
    canvas = FigureCanvasAgg(fig)
    font = {"family": style.FONT}
    fig.text(0.05, 0.885, slide["title"], fontsize=style.TITLE_PT, fontweight="bold",
             color=style.TITLE_COLOR, va="center", ha="left", fontdict=font)  # fmt: skip
    fig.add_artist(Line2D([0.05, 0.95], [0.815, 0.815], color=style.ACCENT, linewidth=3,
                          transform=fig.transFigure))  # fmt: skip
    chart = slide.get("chart")
    wrap = 30 if chart else 64
    y = 0.73
    bullet_texts = []
    for b in slide["bullets"]:
        lines = textwrap.wrap(b, wrap, break_long_words=False) or [b]
        fig.text(0.055, y, "•", fontsize=style.BULLET_PT, color=style.ACCENT,
                 va="top", ha="left", fontdict=font)  # fmt: skip
        bullet_texts.append(
            fig.text(
                0.08,
                y,
                "\n".join(lines),
                fontsize=style.BULLET_PT,
                color=style.TEXT_COLOR,
                va="top",
                ha="left",
                linespacing=1.25,
                fontdict=font,
            )  # fmt: skip
        )
        y -= 0.058 * len(lines) + 0.04
    ticks: list[float] = []
    ax = None
    if chart:
        ax = fig.add_axes((0.56, 0.17, 0.39, 0.53))
        labels = [p["label"] for p in chart["points"]]
        values = [float(p["value"]) for p in chart["points"]]
        xs = list(range(len(values)))
        top, ticks = style.y_ticks(values)
        if chart["type"] == "bar":
            ax.bar(xs, values, color=style.ACCENT, width=0.6, zorder=3)
        else:
            ax.plot(xs, values, color=style.ACCENT, linewidth=2.5, marker="o", markersize=7,
                    zorder=3)  # fmt: skip
        for x, v in zip(xs, values, strict=True):
            ax.annotate(fmt_value(v), (x, v), xytext=(0, 5), textcoords="offset points",
                        ha="center", va="bottom", fontsize=style.VALUE_PT,
                        color=style.TEXT_COLOR, family=style.FONT)  # fmt: skip
        ax.set_ylim(0, top)
        ax.set_yticks(ticks)
        ax.set_yticklabels([_tick_label(t) for t in ticks], fontsize=style.TICK_PT,
                           color=style.TEXT_COLOR, family=style.FONT)  # fmt: skip
        ax.set_xticks(xs)
        wrapped = ["\n".join(textwrap.wrap(lb, 12, break_long_words=False)) or lb for lb in labels]
        ax.set_xticklabels(wrapped, fontsize=style.TICK_PT, color=style.TEXT_COLOR,
                           family=style.FONT)  # fmt: skip
        ax.set_xlim(-0.6, len(values) - 0.4)
        ax.set_xlabel(chart["x_label"], fontsize=style.AXIS_PT, color=style.TEXT_COLOR,
                      family=style.FONT, labelpad=8)  # fmt: skip
        ax.set_ylabel(chart["y_label"], fontsize=style.AXIS_PT, color=style.TEXT_COLOR,
                      family=style.FONT, labelpad=8)  # fmt: skip
        ax.set_title(chart["title"], fontsize=style.CHART_TITLE_PT, color=style.TITLE_COLOR,
                     family=style.FONT, pad=14)  # fmt: skip
        ax.yaxis.grid(True, color=style.GRID, linewidth=1, zorder=0)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(axis="x", length=0)
    fig.text(0.95, 0.035, f"{k} / {n}", fontsize=style.NUMBER_PT, color=style.MUTED,
             ha="right", va="bottom", fontdict=font)  # fmt: skip
    canvas.draw()
    if ax is not None and _crowded(ax.get_xticklabels(), canvas.get_renderer()):
        # category labels that touch are set at 30 degrees, unwrapped
        labels = [p["label"] for p in chart["points"]]
        ax.set_xticklabels(labels, fontsize=style.TICK_PT, color=style.TEXT_COLOR,
                           family=style.FONT, rotation=30, ha="right",
                           rotation_mode="anchor")  # fmt: skip
        canvas.draw()
    _check_layout(fig, canvas, bullet_texts, ax)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dst, format="png", dpi=dpi, facecolor=style.BACKGROUND,
                metadata={"Software": None})  # fmt: skip
    return {"y_ticks": ticks}


def _crowded(texts, renderer, gap: float = 6.0) -> bool:
    """True when two neighbouring texts come closer than `gap` pixels side by side."""
    boxes = sorted((t.get_window_extent(renderer) for t in texts), key=lambda b: b.x0)
    return any(b.x0 - a.x1 < gap for a, b in zip(boxes, boxes[1:], strict=False))


def _check_layout(fig, canvas, bullet_texts, ax) -> None:
    """Every text inside the slide; bullets clear of the chart and above the bottom edge;
    values printed above the chart's points clear of each other."""
    renderer = canvas.get_renderer()
    w, h = style.WIDTH, style.HEIGHT
    for t in fig.texts:
        bb = t.get_window_extent(renderer)
        if bb.x0 < 0 or bb.x1 > w or bb.y0 < 0 or bb.y1 > h:
            raise ValueError(f"text {t.get_text()!r} leaves the slide")
    limit_x = w
    if ax is not None:
        tb = ax.get_tightbbox(renderer)
        if tb.x0 < 0 or tb.x1 > w or tb.y0 < 0 or tb.y1 > h:
            raise ValueError("the chart leaves the slide")
        limit_x = tb.x0
        values = [t for t in ax.texts]
        for a, b in zip(values, values[1:], strict=False):
            if a.get_window_extent(renderer).overlaps(b.get_window_extent(renderer)):
                raise ValueError(f"chart values {a.get_text()} and {b.get_text()} overlap")
    for t in bullet_texts:
        bb = t.get_window_extent(renderer)
        if bb.x1 > limit_x - 8 or bb.y0 < 0.07 * h:
            raise ValueError(f"bullet {t.get_text()!r} runs into the chart or off the slide")


# -- narration ----------------------------------------------------------------------------------


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as f:
        if f.getsampwidth() != 2 or f.getnchannels() != 1:
            raise ValueError(f"{path}: expected 16-bit mono PCM")
        data = np.frombuffer(f.readframes(f.getnframes()), dtype="<i2")
        return data.copy(), f.getframerate()


def write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(samples.astype("<i2").tobytes())


def trim(samples: np.ndarray, rate: int) -> np.ndarray:
    """The voiced part: from the first to the last 10 ms frame louder than TRIM_DB, with
    TRIM_PAD_S kept on both sides."""
    frame = int(rate * 0.01)
    n = len(samples) // frame
    if n == 0:
        return samples
    frames = samples[: n * frame].astype(np.float64).reshape(n, frame)
    rms = np.sqrt((frames**2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9) / 32768.0)
    loud = np.nonzero(db > style.TRIM_DB)[0]
    if len(loud) == 0:
        raise ValueError("the TTS answer is silent")
    pad = int(rate * style.TRIM_PAD_S)
    start = max(0, loud[0] * frame - pad)
    end = min(len(samples), (loud[-1] + 1) * frame + pad)
    return samples[start:end]


def tts(text: str, speech) -> tuple[np.ndarray, str]:
    """The sentence spoken by the narrator voice (cached by voice and text), trimmed, and the
    sha256 of the server's untrimmed answer."""
    key = sha256_text(f"{style.NARRATOR_VOICE}\n{text}")[:24]
    path = TTS_CACHE / f"{key}.wav"
    if not path.exists():
        tmp = path.with_suffix(".part.wav")
        speech.synthesize(text, style.NARRATOR_VOICE, tmp)
        tmp.replace(path)
    samples, rate = read_wav(path)
    if rate != SAMPLE_RATE:
        raise ValueError(f"TTS sample rate {rate}, expected {SAMPLE_RATE}")
    return trim(samples, rate), sha256_file(path)


def layout(spec: dict, audio: list[list[np.ndarray]]) -> dict:
    """Where every slide and sentence goes, in samples and seconds (see the module docstring).
    `audio[k][i]` is the trimmed audio of sentence i of slide k (0-based)."""
    rate = SAMPLE_RATE
    frame = rate // style.FPS  # 882 samples: one video frame
    cursor = int(round(style.LEAD_IN_S * rate))
    slides, sentences = [], []
    for k, (slide, clips) in enumerate(zip(spec["slides"], audio, strict=True), 1):
        start = math.ceil(cursor / frame) * frame
        cursor = start
        for i, (text, pause, clip) in enumerate(
            zip(slide["narration"], slide["pauses"], clips, strict=True), 1
        ):
            cursor += int(round(pause * rate))
            sentences.append({
                "slide": k, "index": i, "text": text, "pause_before": pause,
                "start_sample": cursor, "end_sample": cursor + len(clip),
            })  # fmt: skip
            cursor += len(clip)
        slides.append({"index": k, "start_sample": start})
    total = math.ceil((cursor + int(round(style.TAIL_S * rate))) / frame) * frame
    for a, b in zip(slides, slides[1:] + [{"start_sample": total}], strict=True):
        a["end_sample"] = b["start_sample"]
    for s in slides:
        s["start"], s["end"] = s["start_sample"] / rate, s["end_sample"] / rate
    for s in sentences:
        s["start"], s["end"] = round(s["start_sample"] / rate, 4), round(s["end_sample"] / rate, 4)
    return {"total_samples": total, "duration": total / rate, "slides": slides,
            "sentences": sentences}  # fmt: skip


def ffmpeg_version() -> str:
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()[0]


def encode(slides: list[tuple[Path, float]], narration: Path, dst: Path) -> None:
    """The slides held for their durations (whole frames) over the narration, H.264 + AAC."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for png, seconds in slides:
        args += ["-loop", "1", "-framerate", str(style.FPS), "-t", f"{seconds:.6f}",
                 "-i", str(png)]  # fmt: skip
    args += ["-i", str(narration)]
    chain = "".join(f"[{i}:v]" for i in range(len(slides)))
    args += [
        "-filter_complex", f"{chain}concat=n={len(slides)}:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-map", f"{len(slides)}:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-tune", "stillimage",
        "-r", str(style.FPS), "-g", str(2 * style.FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "1",
        "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
        "-flags:a", "+bitexact", "-movflags", "+faststart", str(dst),
    ]  # fmt: skip
    subprocess.run(args, check=True)


def render(spec: dict, speech) -> dict:
    """Render one lecture into OUT/<id>/ and return its timeline."""
    vid = spec["id"]
    folder = OUT / vid
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    n = len(spec["slides"])
    extras, pngs = [], []
    for k, slide in enumerate(spec["slides"], 1):
        png = folder / f"slide_{k}.png"
        extras.append(render_slide(slide, k, n, png))
        pngs.append(png)
    audio, tts_sha = [], []
    for slide in spec["slides"]:
        clips, shas = zip(*(tts(t, speech) for t in slide["narration"]), strict=True)
        audio.append(list(clips))
        tts_sha.append(list(shas))
    plan = layout(spec, audio)
    track = np.zeros(plan["total_samples"], dtype=np.int16)
    flat = [c for clips in audio for c in clips]
    for s, clip in zip(plan["sentences"], flat, strict=True):
        track[s["start_sample"] : s["end_sample"]] = clip
    narration = folder / "narration.wav"
    write_wav(narration, track, SAMPLE_RATE)
    video = folder / "video.mp4"
    encode([(p, (s["end_sample"] - s["start_sample"]) / SAMPLE_RATE)
            for p, s in zip(pngs, plan["slides"], strict=True)], narration, video)  # fmt: skip
    shas = [x for row in tts_sha for x in row]
    timeline = {
        "id": vid,
        "render_version": RENDER_VERSION,
        "spec_sha256": sha256_file(spec_path(vid)),
        "fps": style.FPS,
        "width": style.WIDTH,
        "height": style.HEIGHT,
        "narrator_voice": style.NARRATOR_VOICE,
        "sample_rate": SAMPLE_RATE,
        "duration": round(plan["duration"], 4),
        "slides": [
            {
                "index": s["index"],
                "start": round(s["start"], 4),
                "end": round(s["end"], 4),
                "y_ticks": e["y_ticks"],
                "png_sha256": sha256_file(p),
            }
            for s, e, p in zip(plan["slides"], extras, pngs, strict=True)
        ],  # fmt: skip
        "sentences": [
            {k: s[k] for k in ("slide", "index", "text", "pause_before", "start", "end")}
            | {"tts_sha256": sha}
            for s, sha in zip(plan["sentences"], shas, strict=True)
        ],
        "speech": [{"start": s["start"], "end": s["end"]} for s in plan["sentences"]],
        "files": {"narration.wav": sha256_file(narration), "video.mp4": sha256_file(video)},
        "encoder": ffmpeg_version(),
    }
    write_json(folder / "timeline.json", timeline)
    return timeline


# -- the answer key -----------------------------------------------------------------------------

RULES = {
    "engine_blind": (
        "The pipeline is given video.mp4 only (file name video.mp4, no source URL, research "
        "off). Nothing in this file, the specs or the timelines reaches it."
    ),
    "facts": (
        "Derived by fixed rules from each spec (suite_a.specs.derive_facts): a slide title, "
        "bullet, chart title or axis label is a visual fact when the slide's narration shares "
        f"less than {UNSAID} of its content words and the whole narration never says it as a "
        f"phrase; a spoken fact when the share is at least {SAID} or it is said as a phrase; "
        "neither in between (listed as partly_said, not scored). Every chart adds a "
        "chart_value fact for its highest value; a line chart also adds a chart_trend fact "
        "for the direction from its first to its last point unless the slide's narration "
        "uses a word of that direction (bar charts: categories, no trend). The narration "
        "never states a chart value (validated, digits and words). "
        f"Every slide has at least {MIN_VISUAL_PER_SLIDE} visual facts and one spoken bullet."
    ),
    "published": (
        "A published cue is a standard cue (with a gap) whose status is verified, edited or "
        "approved, or an extended cue (no gap) whose status is extended, edited or approved. "
        "Held cues are not published and are not scored."
    ),
    "overlap": (
        "Seconds during which a published standard cue's audio [start, start + audio_dur] "
        "overlaps a narration span (timeline.json speech), summed per video; must be 0. "
        "Extended cues pause the picture: counted instead is any extended cue whose freeze "
        "point lies strictly inside a narration span (must be 0)."
    ),
    "recall": (
        "Per visual fact, a judge (Nemotron 3 Ultra, temperature 0, JSON) names the "
        "published cues that convey it: the fact's specific content (the exact title or "
        "bullet wording up to paraphrase that keeps every idea; for a label, the label text; "
        "for a chart_value, the value with its category; for a chart_trend, the direction "
        "over the chart's span). A cue that only names the slide or the chart, without that "
        "content, does not convey it. Recall (standard) counts standard cues only; recall "
        "(with extended) counts standard and extended cues. Also reported: timely recall "
        "(a conveying cue starts while that slide is on screen) and recall by fact kind."
    ),
    "exact_match": (
        "Deterministic, no model: a title fact matches when one published cue contains the "
        "slide title as a whole-token phrase after normalisation (lower case, punctuation "
        "and spacing ignored); a chart_value fact matches when one published cue states the "
        "value (digits with optional thousands commas, or English number words) and contains "
        "its category label as a phrase. Reported separately for standard and with extended."
    ),
    "hallucination": (
        "Per claim of a published cue (the claims in the run's cues.json), the judge decides "
        "supported or unsupported by this key: the slide inventory (exact on-screen text, "
        "chart data, ticks and the fixed style), the slide time ranges and the narration. "
        "Text quoted or read from the screen must match exactly; a number must match the "
        "chart or the slide; anything not established by the key (an object, a colour, a "
        "person, an image, a value, a relation) is unsupported. Rate = unsupported claims / "
        "claims. Also reported per cue (a cue with any unsupported claim)."
    ),
    "decoys": (
        "Per absent decoy, the judge names the published cues that state it or something it "
        "asserts; expected 0. Deterministic check: a number written in digits in a decoy "
        "(and nowhere in the video) appearing in a published cue."
    ),
    "redundancy": (
        "Per published cue, the judge decides whether it restates a spoken fact or anything "
        "the narration says (adding nothing visual). Rate = such cues / published cues; also "
        "the share of spoken facts restated by any cue."
    ),
    "cost_and_speed": (
        "Ledger spend of each run's run id (model calls and Tavily separately) per minute of "
        "video; processing seconds (wall clock of lull.pipeline.run) per minute of video; "
        "verified, held and extended counts from the run's manifest."
    ),
    "aggregation": (
        "Pooled over the twelve videos (sum of numerators / sum of denominators). 95% "
        "intervals: percentile bootstrap over videos, 10000 resamples, seed 20261028."
    ),
    "judge": (
        "nvidia/Nemotron-3-Ultra-550b-a55b on Nebius Token Factory, temperature 0, reasoning "
        "on, strict JSON schema. Two calls per video: (1) coverage: for every visual fact, "
        "spoken fact and absent decoy, the ids of the published cues that convey it; (2) "
        "claims: every claim of every published cue supported or unsupported, and every "
        "published cue restates the narration or not. The judge sees this key's entry for "
        "the video and the published cues (id, track, start time, text, claim texts), never "
        "the engine's own verdicts. One run per video; no re-judging to change a result."
    ),
    "judge_probe": (
        "A check of the judge itself on videos a01, a05 and a09: synthetic cues made from "
        "the key (one per visual fact, its text; one per decoy, its text) are judged the "
        "same way. Reported: share of facts the judge credits to their own probe cue, share "
        "of decoys it credits to their probe cue, share of fact-cue claims judged supported "
        "and of decoy-cue claims judged unsupported."
    ),
}


def inventory(slide: dict, k: int, n: int, start: float, end: float, ticks: list[float]) -> str:
    """Everything on slide k, in words: the judge's ground truth for what can be seen."""
    side = "on the left half" if slide.get("chart") else "across the slide"
    bullets = "; ".join(f'"{b}"' for b in slide["bullets"])
    text = (
        f"Slide {k} of {n}, on screen from {start:.2f} s to {end:.2f} s. Title: "
        f'"{slide["title"]}". Bulleted list {side}, in order: {bullets}.'
    )
    chart = slide.get("chart")
    if chart:
        kind = "bar chart" if chart["type"] == "bar" else "line chart with round markers"
        pts = ", ".join(f'"{p["label"]}" = {fmt_value(p["value"])}' for p in chart["points"])
        text += (
            f' On the right half a {kind} titled "{chart["title"]}": categories along the '
            f"horizontal axis, left to right, with the value printed above each "
            f"{'bar' if chart['type'] == 'bar' else 'point'}: {pts}. Horizontal-axis label "
            f'"{chart["x_label"]}"; vertical-axis label "{chart["y_label"]}"; vertical-axis '
            f"ticks {', '.join(_tick_label(t) for t in ticks)}."
        )
    return text + f' Slide number "{k} / {n}" in the bottom-right corner.'


def _exact(fact: dict) -> dict | None:
    if fact["kind"] == "title":
        return {"type": "title", "text": fact["anchor"]}
    if fact["kind"] == "chart_value":
        return {"type": "number", "value": fact["value"], "label": fact["anchor"]}
    return None


def _decoy_numbers(decoy: dict, video_numbers: set[float]) -> list[float]:
    return sorted(n for n in numbers_in(decoy["text"], words=False) if n not in video_numbers)


def key_entry(spec: dict, timeline: dict) -> dict:
    n = len(spec["slides"])
    times = {s["index"]: s for s in timeline["slides"]}
    slides = []
    for s in spec["slides"]:
        t = times[s["index"]]
        chart = None if s["chart"] is None else {**s["chart"], "y_ticks": t["y_ticks"]}
        slides.append({
            "index": s["index"], "start": t["start"], "end": t["end"], "title": s["title"],
            "bullets": s["bullets"], "chart": chart,
            "inventory": inventory(s, s["index"], n, t["start"], t["end"], t["y_ticks"]),
        })  # fmt: skip
    from suite_a.specs import slide_numbers

    video_numbers = set().union(*(slide_numbers(s) for s in spec["slides"]))
    video_numbers |= {float(k) for k in range(1, n + 1)}  # slide numbers
    for s in timeline["sentences"]:
        video_numbers |= numbers_in(s["text"])

    def timed(f: dict) -> dict:
        t = times[f["slide"]]
        return {**f, "slide_start": t["start"], "slide_end": t["end"]}

    return {
        "id": spec["id"],
        "subject": spec["subject"],
        "title": spec["title"],
        "duration": timeline["duration"],
        "spec_sha256": timeline["spec_sha256"],
        "video_sha256": timeline["files"]["video.mp4"],
        "slides": slides,
        "narration": [
            {k: s[k] for k in ("slide", "index", "text", "start", "end")}
            for s in timeline["sentences"]
        ],
        "visual_facts": [{**timed(f), "exact": _exact(f)} for f in spec["visual_facts"]],
        "spoken_facts": [timed(f) for f in spec["spoken_facts"]],
        "partly_said": [timed(f) for f in spec["partly_said"]],
        "absent_decoys": [
            {**d, "numbers": _decoy_numbers(d, video_numbers)} for d in spec["absent_decoys"]
        ],
    }


def build_key() -> str:
    """key.json from every spec and timeline, and key.sha256; returns the hash."""
    from lull.tf import LIGHTNING

    entries = []
    for s in SUBJECTS:
        spec = load_spec(s.id)
        errs = check_committed(spec)
        if errs:
            raise SystemExit(f"{s.id}: the spec does not validate: {'; '.join(errs)}")
        tl_path = OUT / s.id / "timeline.json"
        timeline = json.loads(tl_path.read_text(encoding="utf-8"))
        if timeline["spec_sha256"] != sha256_file(spec_path(s.id)):
            raise SystemExit(f"{s.id}: rendered from another spec; render it again")
        entries.append(key_entry(spec, timeline))
    key = {
        "suite": "A",
        "name": "planted facts: synthetic lectures with a hidden answer key",
        "version": 1,
        "videos_n": len(entries),
        "generator_model": LIGHTNING,
        "narrator": {"tts": "NVIDIA Magpie TTS", "voice": style.NARRATOR_VOICE},
        "style": style.STYLE_TEXT,
        "decoy_kinds": list(DECOY_KINDS),
        "rules": RULES,
        "totals": {
            "visual_facts": sum(len(e["visual_facts"]) for e in entries),
            "spoken_facts": sum(len(e["spoken_facts"]) for e in entries),
            "absent_decoys": sum(len(e["absent_decoys"]) for e in entries),
            "duration_s": round(sum(e["duration"] for e in entries), 3),
        },
        "videos": entries,
    }
    write_json(KEY_PATH, key)
    digest = sha256_file(KEY_PATH)
    KEY_SHA_PATH.write_text(f"{digest}  key.json\n", encoding="utf-8", newline="\n")
    return digest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma-separated lecture ids (the key is not written)")
    ap.add_argument("--key", action="store_true", help="only write the key from the renders")
    args = ap.parse_args(argv)
    if not args.key:
        from lull.speech import NemoSpeechHTTP

        speech = NemoSpeechHTTP.from_settings()
        if not speech.health():
            print("the speech server is not answering", file=sys.stderr)
            return 1
        for s in SUBJECTS:
            if args.only and s.id not in args.only.split(","):
                continue
            tl = render(load_spec(s.id), speech)
            video = tl["files"]["video.mp4"][:12]
            print(f"{s.id}: {tl['duration']:.2f} s, {len(tl['slides'])} slides, "
                  f"{len(tl['sentences'])} sentences, video {video}")  # fmt: skip
        if args.only:
            return 0
    digest = build_key()
    print(f"key.json sha256 {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
