"""Suite B references: what human describers said about real videos, as timed lines.

    uv run --directory eval python -m suite_b.refs               # refs/<video>.json + .md
    uv run --directory eval python -m suite_b.refs --transcribe  # ASR again (speech server)
    uv run --directory eval python -m suite_b.refs --spot-check  # 5 random USGS lines in context

Sources are read from the lull-data folder (next to the repository, or --data; MANIFEST.md
there lists every URL and licence). Nothing here calls a language model.

Elephants Dream has two text references, Silvia Pfeiffer's and JJ Hunt's WebVTT description
tracks: each cue becomes one reference line timed by its own start and end. Hunt also voiced
his track; `energy_vad` finds the voiced intervals of that recording (thresholds below), and
the intervals are checked against his cue times and against the film's dialogue.

The USGS videos have no text track: each comes as the original and as a version with the
description voiced into it, the video paused where the describer needed more room. Both
soundtracks are transcribed by the local speech server (Parakeet, word timestamps, as the
engine's speech client asks for them; answers kept in refs/asr/). `align` matches the AD
version's words to the original's narration (difflib sequence alignment on normalised
tokens, then the repairs its docstring lists); the AD version's words left over are the
describer's, cut into sentences. Matched narration words map AD-version time to original
time; a line's anchor is the original time at which the AD version played or paused it.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import re
import subprocess
import sys
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from suite_a.text import STOP, norm, tokens

HERE = Path(__file__).resolve().parent
EVAL_ROOT = HERE.parent
REPO_ROOT = EVAL_ROOT.parent
REFS = HERE / "refs"
ASR_DIR = REFS / "asr"
CORRECTIONS_PATH = REFS / "asr_corrections.json"
OUT = HERE / "out"
DATA_DEFAULT = REPO_ROOT.parent / "lull-data"

SPOT_SEED = 20261028

# -- alignment parameters (USGS) -----------------------------------------------------------------
SIMILAR = 0.75  # difflib ratio at which two tokens (or two joined token runs) are one word
SHORT_EQUAL = 2  # an exact match this short inside the describer's words can be chance
MAX_SHIFT = 8  # tokens a describer's run may slide along a repeated phrase to a sentence edge
MIN_LINE_TOKENS = 2  # a left-over run shorter than this is recogniser noise (listed, not kept)
PAUSE_S = 1.0  # an AD gap this much longer than the original's means the video was paused
SPAN_JOIN_S = 0.25  # word spans closer than this are one stretch of speech (as the engine)
SENT_END = (".", "?", "!")
# a word ending in a full stop that does not end a sentence: "U.S.", "Dr."
ABBREV = re.compile(r"^(?:(?:[A-Za-z]\.){2,}|(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|vs)\.)$")

# -- voice activity on Hunt's described-audio track ----------------------------------------------
# The track holds only the describer's voice and is digital silence (-120 dBFS) in between, so a
# plain energy threshold is enough: 20 ms frames, voiced above -45 dBFS RMS, gaps under 0.3 s
# (between words) bridged, runs under 0.1 s dropped.
VAD = {"rate": 16000, "frame_s": 0.02, "threshold_dbfs": -45.0, "bridge_s": 0.3, "min_len_s": 0.1}

VIDEOS: dict[str, dict] = {
    "elephants_dream": {
        "kind": "vtt",
        "title": "Elephants Dream (2006)",
        "about": "a 10-minute animated short film: two men, Proog and Emo, in a vast "
        "surreal machine",
        "media": {
            "file": "elephants/ed_480p.webm",
            "licence": "CC BY 2.5",
            "credit": "(c) Orange Open Movie Team / Blender Foundation",
            "url": "https://commons.wikimedia.org/wiki/File:Elephants_Dream_(2006).webm",
        },
        "references": {
            "pfeiffer": {
                "file": "elephants/pfeiffer_elephant.audiodescriptions.vtt",
                "author": "Silvia Pfeiffer",
                "licence": "CC BY 4.0",
                "url": "http://html5videoguide.net/presentations/LCA_MM_Miniconf2013/"
                "elephant.audiodescriptions.vtt",
            },
            "hunt": {
                "file": "elephants/ed-description-jjhunt.vtt",
                "author": "JJ Hunt",
                "licence": "CC BY-SA 4.0",
                "url": "https://github.com/OwenEdwards/ElephantsDreamAudioDescription",
            },
        },
        "hunt_audio": {
            "file": "elephants/ed_audio_description_jjhunt.wav",
            "author": "JJ Hunt",
            "licence": "CC BY-SA 4.0",
            "url": "https://github.com/OwenEdwards/ElephantsDreamAudioDescription",
        },
    },
    "woodshole": {
        "kind": "usgs",
        "title": "USGS Woods Hole Coastal and Marine Science Center",
        "about": "a 3-minute USGS documentary about a coastal and marine science center",
        "media": {
            "file": "usgs/woodshole.mp4",
            "licence": "Public domain (USGS); music used with permission",
            "credit": "U.S. Geological Survey",
            "url": "https://www.usgs.gov/media/videos/"
            "usgs-woods-hole-coastal-and-marine-science-center",
        },
        "ad_media": {
            "file": "usgs/woodshole_AD.mp4",
            "url": "https://www.usgs.gov/media/videos/"
            "usgs-woods-hole-coastal-and-marine-science-center-ad",
        },
    },
    "aquatic_connectivity": {
        "kind": "usgs",
        "title": "The importance of aquatic connectivity for maintaining cold water species",
        "about": "a 4-minute USGS explainer with drawings and photos of a stream network",
        "media": {
            "file": "usgs/aquatic_connectivity.mp4",
            "licence": "Public domain (USGS); some materials used with permission",
            "credit": "U.S. Geological Survey",
            "url": "https://www.usgs.gov/media/videos/importance-aquatic-connectivity-"
            "maintaining-cold-water-species-face-climate-change",
        },
        "ad_media": {
            "file": "usgs/aquatic_connectivity_AD.mp4",
            "url": "https://www.usgs.gov/media/videos/importance-aquatic-connectivity-"
            "maintaining-cold-water-species-face-climate-change-ad",
        },
    },
    "csc_intro": {
        "kind": "usgs",
        "title": "USGS Climate Science Champions: introduction",
        "about": "a 45-second USGS introduction showing scientists at work in many settings",
        "media": {
            "file": "usgs/csc_intro.mp4",
            "licence": "Public domain (USGS); music used with permission",
            "credit": "U.S. Geological Survey",
            "url": "https://www.usgs.gov/media/videos/climate-science-champions-introduction",
        },
        "ad_media": {
            "file": "usgs/csc_intro_AD.mp4",
            "url": "https://www.usgs.gov/media/videos/climate-science-champions-introduction-ad",
        },
    },
}
USGS_REFERENCE = {
    "author": "USGS (professionally voiced extended audio description)",
    "licence": "Public domain (USGS)",
}


# -- small helpers --------------------------------------------------------------------------------


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def word_count(text: str) -> int:
    """Words a listener hears: runs of letters or digits (an apostrophe or hyphen inside one
    keeps it one word)."""
    return len(re.findall(r"[0-9A-Za-zÀ-ɏ]+(?:['’\-][0-9A-Za-z]+)*", text))


def merge_spans(spans, join: float = 0.0) -> list[tuple[float, float]]:
    """Sorted, merged (start, end) spans; spans closer than `join` become one."""
    out: list[list[float]] = []
    for a, b in sorted((float(a), float(b)) for a, b in spans):
        if out and a <= out[-1][1] + join:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out]


def overlap_s(a, b) -> float:
    """Seconds covered by both span lists."""
    total = 0.0
    for x0, x1 in merge_spans(a):
        for y0, y1 in merge_spans(b):
            lo, hi = max(x0, y0), min(x1, y1)
            if hi > lo:
                total += hi - lo
    return round(total, 3)


# -- WebVTT ---------------------------------------------------------------------------------------

_TS = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})$")


def parse_ts(s: str) -> float:
    m = _TS.match(s.strip())
    if not m:
        raise ValueError(f"not a WebVTT timestamp: {s!r}")
    h, mnt, sec, ms = m.groups()
    return int(h or 0) * 3600 + int(mnt) * 60 + int(sec) + int(ms) / 1000


def strip_markup(line: str) -> str:
    """A cue line without its tags (voice, class, underline ...) and with entities decoded."""
    return html.unescape(re.sub(r"<[^>]*>", "", line))


def parse_vtt(text: str, source: str) -> list[dict]:
    """The cues of a WebVTT file: id `<source>-NN` (position, from 01), start, end, text (the
    payload lines without markup, joined by newlines). NOTE, STYLE and REGION blocks and the
    header are skipped; a block without a timing line raises ValueError."""
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or not lines[0].startswith("WEBVTT"):
        raise ValueError("not a WebVTT file (no WEBVTT header)")
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in lines:
        if line.strip():
            cur.append(line)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    cues = []
    for block in blocks[1:]:
        if block[0].strip().split(maxsplit=1)[0] in ("NOTE", "STYLE", "REGION"):
            continue
        timing = next((k for k, x in enumerate(block[:2]) if "-->" in x), None)
        if timing is None:
            raise ValueError(f"cue without a timing line: {block[0]!r}")
        left, right = block[timing].split("-->", 1)
        start, end = parse_ts(left), parse_ts(right.strip().split()[0])
        if end < start:
            raise ValueError(f"cue ends before it starts: {block[timing]!r}")
        payload = [strip_markup(x).strip() for x in block[timing + 1 :]]
        cues.append({
            "id": f"{source}-{len(cues) + 1:02d}",
            "source": source,
            "start": round(start, 3),
            "end": round(end, 3),
            "text": "\n".join(x for x in payload if x),
        })  # fmt: skip
    return cues


# -- audio ----------------------------------------------------------------------------------------


def decode_audio(path: Path, rate: int = 16000, stream: str = "0:a:0") -> np.ndarray:
    """Mono float32 samples of one audio stream (an attached cover-art stream is ignored)."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", stream, "-vn",
           "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]  # fmt: skip
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def energy_vad(
    x: np.ndarray,
    rate: int,
    frame_s: float = VAD["frame_s"],
    threshold_dbfs: float = VAD["threshold_dbfs"],
    bridge_s: float = VAD["bridge_s"],
    min_len_s: float = VAD["min_len_s"],
) -> list[tuple[float, float]]:
    """Voiced intervals: frames whose RMS level is above `threshold_dbfs`, runs closer than
    `bridge_s` joined, runs shorter than `min_len_s` dropped."""
    n = int(round(frame_s * rate))
    frames = len(x) // n
    if frames == 0:
        return []
    rms = np.sqrt(np.mean(np.square(x[: frames * n].reshape(frames, n), dtype=np.float64), axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-10))
    on = db > threshold_dbfs
    runs, start = [], None
    for i, v in enumerate(on):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start * frame_s, i * frame_s))
            start = None
    if start is not None:
        runs.append((start * frame_s, frames * frame_s))
    merged = merge_spans(runs, join=bridge_s - 1e-9)
    return [(a, b) for a, b in merged if b - a >= min_len_s - 1e-9]


# -- USGS: transcripts ----------------------------------------------------------------------------


def asr_models(url: str) -> list[str]:
    import httpx

    try:
        data = httpx.get(url.rstrip("/") + "/v1/models", timeout=10).json()
        return [m["id"] for m in data.get("data", []) if m.get("capability") == "transcription"]
    except (httpx.HTTPError, ValueError, KeyError):
        return []


def transcript(name: str, src: Path, data_root: Path, refresh: bool) -> dict:
    """The committed transcript refs/asr/<name>.json, or a fresh one from the speech server."""
    path = ASR_DIR / f"{name}.json"
    if path.exists() and not refresh:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc["sha256"] != sha256_file(src):
            raise SystemExit(f"{src} is not the file refs/asr/{name}.json was made from")
        return doc
    from lull.config import get_settings
    from lull.speech import DEFAULT_URL, NemoSpeechHTTP

    url = get_settings().lull_speech_url or DEFAULT_URL
    speech = NemoSpeechHTTP.from_settings()
    if not speech.health():
        raise SystemExit(f"the speech server at {url} is not answering")
    wav = OUT / "wav" / f"{name}.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), "-map", "0:a:0",
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                   check=True)  # fmt: skip
    words = speech.transcribe(wav)
    doc = {
        "file": src.relative_to(data_root).as_posix(),
        "sha256": sha256_file(src),
        "asr_models": asr_models(url),
        "request": "POST /v1/audio/transcriptions response_format=verbose_json, word timestamps "
        "(lull.speech.NemoSpeechHTTP.transcribe), 16 kHz mono PCM of audio stream 0:a:0",
        "words": [
            {"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3)} for w in words
        ],  # fmt: skip
    }
    write_json(path, doc)
    return doc


def clamped(words: list[dict]) -> list[dict]:
    """Words with the engine's clamp on implausibly long words (lull.stages.listen)."""
    from lull.stages.listen import clamp_words
    from lull.types import Word

    out, _ = clamp_words([Word(text=w["text"], start=w["start"], end=w["end"]) for w in words])
    return [{"text": w.text, "start": w.start, "end": w.end} for w in out]


# -- USGS: alignment ------------------------------------------------------------------------------


def _toks(words: list[dict]) -> tuple[list[str], list[int]]:
    toks, owner = [], []
    for i, w in enumerate(words):
        for t in tokens(w["text"]):
            toks.append(t)
            owner.append(i)
    return toks, owner


def _similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= SIMILAR


def _absorb_short_equals(ops: list[list], at: list[str]) -> list[list]:
    """A chance match inside the describer's words becomes part of one differing region: an
    exact match of at most SHORT_EQUAL tokens between two regions that both differ on both
    sides ("of" in "view of lobster" where the narration said "observations of what"), or
    one made of stop words only between two regions that both have AD-version words ("and"
    in "drone controller and women holding")."""
    ops = [list(o) for o in ops]
    changed = True
    while changed:
        changed = False
        for k in range(1, len(ops) - 1):
            tag, i1, i2, _, _ = ops[k]
            left, right = ops[k - 1][0], ops[k + 1][0]
            if tag != "equal" or i2 - i1 > SHORT_EQUAL:
                continue
            both_differ = left == "replace" and right == "replace"
            has_ad = ("replace", "delete")
            stop_inside = all(t in STOP for t in at[i1:i2]) and left in has_ad and right in has_ad
            if both_differ or stop_inside:
                j1, j2 = ops[k - 1][3], ops[k + 1][4]
                ops[k - 1 : k + 2] = [["replace" if j2 > j1 else "delete", ops[k - 1][1],
                                       ops[k + 1][2], j1, j2]]  # fmt: skip
                changed = True
                break
    return ops


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(flags):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(flags)))
    return out


def _edge_score(s: int, e: int, owner: list[int], words: list[dict]) -> int | None:
    """How well a run of tokens [s, e) sits on sentence edges (None: it splits a word)."""
    if (s > 0 and owner[s] == owner[s - 1]) or (e < len(owner) and owner[e] == owner[e - 1]):
        return None
    first, last = words[owner[s]]["text"].strip(), words[owner[e - 1]]["text"].strip()
    prev = words[owner[s - 1]]["text"].strip() if s > 0 else "."
    return (int(prev.endswith(SENT_END + (":", ","))) + int(last.endswith(SENT_END))
            + int(first[:1].isupper()))  # fmt: skip


def _matched_run(pair: list[int | None], start: int, step: int) -> int:
    """How many tokens from `start` (going right for step 1, left for -1) are matched to
    consecutive original tokens."""
    n, i = 0, start
    while 0 <= i < len(pair) and pair[i] is not None:
        if n and pair[i] != pair[i - step] + step:
            break
        n, i = n + 1, i + step
    return n


def _shift_runs(at, owner, words, pair, narr) -> None:
    """A describer's run next to a repeated phrase can be placed on either copy of it: "Center.
    Video shows the ... Center." against narration that says "Center" once, or "road stream
    crossings. A drawing ... at the roadstream crossings." where the narration's own copy
    was heard as two words. Slide the run along the repeat (its first k tokens, joined, equal
    to the first d matched tokens right after it; or its last k to the d right before it) to
    the place where it best starts and ends a sentence; ties keep it where it is."""
    for s, e in _runs([not x for x in narr]):
        score = _edge_score(s, e, owner, words)
        best = (-1 if score is None else score, None)
        for d in range(1, min(MAX_SHIFT, _matched_run(pair, e, 1)) + 1):  # to the right
            target = "".join(at[e : e + d])
            for k in range(1, min(MAX_SHIFT, e - s) + 1):
                if "".join(at[s : s + k]) == target:
                    sc = _edge_score(s + k, e + d, owner, words)
                    if sc is not None and sc > best[0]:
                        best = (sc, ("right", k, d))
        for d in range(1, min(MAX_SHIFT, _matched_run(pair, s - 1, -1)) + 1):  # to the left
            target = "".join(at[s - d : s])
            for k in range(1, min(MAX_SHIFT, e - s) + 1):
                if "".join(at[e - k : e]) == target:
                    sc = _edge_score(s - d, e - k, owner, words)
                    if sc is not None and sc > best[0]:
                        best = (sc, ("left", k, d))
        if best[1] is None:
            continue
        side, k, d = best[1]
        if side == "right":  # run tokens [s, s+k) take the place of narration [e, e+d)
            old = [pair[e + i] for i in range(d)]
            for i in range(d):
                pair[e + i], narr[e + i] = None, False
            for i in range(k):
                pair[s + i], narr[s + i] = (old[i] if i < d else None), True
        else:  # run tokens [e-k, e) take the place of narration [s-d, s)
            old = [pair[s - d + i] for i in range(d)]
            for i in range(d):
                pair[s - d + i], narr[s - d + i] = None, False
            for i in range(k):
                pair[e - k + i], narr[e - k + i] = (old[i] if i < d else None), True


def align(o_words: list[dict], a_words: list[dict]) -> dict:
    """Which words of the AD version are the original's narration, and how its times map.

    1. Both transcripts become lower-case tokens (suite_a.text.tokens) and difflib's
       SequenceMatcher (no junk heuristic) aligns them.
    2. Repairs: an exact match of at most SHORT_EQUAL tokens between two regions that differ
       on both sides is dropped (`_absorb_short_equals`); in a differing region, tokens at
       either end that are near-identical (ratio >= SIMILAR: "an"/"and", "gained"/"gain") are
       matched, and a region whose joined tokens are near-identical ("woods hall" /
       "woodshole") is the same narration heard differently; a describer's run beside a
       repeated phrase slides to its best sentence edges (`_shift_runs`).
    3. AD-version words left unmatched are the describer's; original words left unmatched
       are narration the AD version does not have (cut, or talked over).

    Returns `pairs` (AD word index -> original word index, in order), `ad` (per AD word:
    the describer's or not) and `missing` (original word indices without a counterpart)."""
    at, a_own = _toks(a_words)
    ot, o_own = _toks(o_words)
    ops = SequenceMatcher(None, at, ot, autojunk=False).get_opcodes()
    ops = _absorb_short_equals([list(o) for o in ops], at)
    pair: list[int | None] = [None] * len(at)
    narr = [False] * len(at)
    found = [False] * len(ot)
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                pair[i1 + k], narr[i1 + k], found[j1 + k] = j1 + k, True, True
            continue
        while i1 < i2 and j1 < j2 and _similar(at[i1], ot[j1]):
            pair[i1], narr[i1], found[j1] = j1, True, True
            i1, j1 = i1 + 1, j1 + 1
        while i1 < i2 and j1 < j2 and _similar(at[i2 - 1], ot[j2 - 1]):
            pair[i2 - 1], narr[i2 - 1], found[j2 - 1] = j2 - 1, True, True
            i2, j2 = i2 - 1, j2 - 1
        if i1 < i2 and j1 < j2 and _similar("".join(at[i1:i2]), "".join(ot[j1:j2])):
            for k in range(i1, i2):
                narr[k] = True
            for k in range(j1, j2):
                found[k] = True
    _shift_runs(at, a_own, a_words, pair, narr)
    word_narr = [False] * len(a_words)
    word_has_tok = [False] * len(a_words)
    pairs: list[tuple[int, int]] = []
    for i, w in enumerate(a_own):
        word_has_tok[w] = True
        word_narr[w] = word_narr[w] or narr[i]
        if pair[i] is not None:
            p = (w, o_own[pair[i]])
            if not pairs or (p[0] > pairs[-1][0] and p[1] >= pairs[-1][1]):
                pairs.append(p)
    ad = []
    for w in range(len(a_words)):
        if word_has_tok[w]:
            ad.append(not word_narr[w])
        else:  # punctuation only: it belongs to the word before it
            ad.append(bool(ad) and ad[-1])
    o_found = [False] * len(o_words)
    o_has_tok = [False] * len(o_words)
    for j, w in enumerate(o_own):
        o_has_tok[w] = True
        o_found[w] = o_found[w] or found[j]
    missing = [w for w in range(len(o_words)) if o_has_tok[w] and not o_found[w]]
    return {"pairs": pairs, "ad": ad, "missing": missing}


def ad_lines(a_words: list[dict], ad: list[bool]) -> tuple[list[list[int]], list[list[int]]]:
    """The describer's words cut into sentences (lists of word indices) and the runs too
    short to keep (fewer than MIN_LINE_TOKENS tokens)."""
    keep, drop = [], []
    for s, e in _runs(ad):
        cur: list[int] = []
        for w in range(s, e):
            cur.append(w)
            text = a_words[w]["text"].strip()
            if (text.endswith(SENT_END) and not ABBREV.match(text)) or w == e - 1:
                n = sum(len(tokens(a_words[i]["text"])) for i in cur)
                (keep if n >= MIN_LINE_TOKENS else drop).append(cur)
                cur = []
    return keep, drop


def anchor(start: float, end: float, pairs_t: list[tuple[float, float, float, float]],
           a_dur: float, o_dur: float) -> dict:  # fmt: skip
    """Where an AD-version stretch [start, end] sits on the original timeline. `pairs_t` are
    matched narration words (AD start, AD end, original start, original end) in order. The
    stretch lies between the matched word before it (or the start) and the one after it (or
    the end); if that AD gap is longer than the original's by PAUSE_S or more, the video was
    paused there. The anchor is the original time reached when the stretch starts, assuming
    the video ran on until then: original gap start + (start - AD gap start), kept inside
    the original gap."""
    prev = [p for p in pairs_t if p[1] <= start + 1e-6]
    nxt = [p for p in pairs_t if p[0] >= end - 1e-6]
    ap, op = (prev[-1][1], prev[-1][3]) if prev else (0.0, 0.0)
    aq, oq = (nxt[0][0], nxt[0][2]) if nxt else (a_dur, o_dur)
    oq = max(oq, op)
    pause = (aq - ap) - (oq - op)
    t = min(max(op + (start - ap), op), oq)
    placement = "before_program" if not prev and pause >= PAUSE_S and oq - op < PAUSE_S else "timed"
    return {"anchor_time": round(t, 3), "anchor_range": [round(op, 3), round(oq, 3)],
            "pause_s": round(pause, 3), "paused": pause >= PAUSE_S,
            "placement": placement}  # fmt: skip


def usgs_reference(vid: str, o_doc: dict, a_doc: dict, o_dur: float, a_dur: float) -> dict:
    """The describer's lines of one USGS video with their anchors, and how the alignment went."""
    o_words, a_words = clamped(o_doc["words"]), clamped(a_doc["words"])
    al = align(o_words, a_words)
    pairs_t = [(a_words[i]["start"], a_words[i]["end"], o_words[j]["start"], o_words[j]["end"])
               for i, j in al["pairs"]]  # fmt: skip
    keep, drop = ad_lines(a_words, al["ad"])
    cues = []
    for k, ws in enumerate(keep, start=1):
        s, e = a_words[ws[0]]["start"], a_words[ws[-1]]["end"]
        cues.append({
            "id": f"usgs-{k:03d}", "source": "usgs_ad", "start": round(s, 3), "end": round(e, 3),
            "text": " ".join(a_words[i]["text"] for i in ws), "ad_words": [ws[0], ws[-1]],
            **anchor(s, e, pairs_t, a_dur, o_dur),
        })  # fmt: skip
    for c in cues:
        c["anchor_end"] = c["anchor_time"]
    narr = [(a_words[i]["start"], a_words[i]["end"]) for i, x in enumerate(al["ad"]) if not x]
    narration_a = merge_spans(narr, join=SPAN_JOIN_S)
    ad_spans = merge_spans(((c["start"], c["end"]) for c in cues), join=SPAN_JOIN_S)
    missing_runs = []
    for s, e in _runs([i in set(al["missing"]) for i in range(len(o_words))]):
        missing_runs.append({"original": [o_words[s]["start"], o_words[e - 1]["end"]],
                             "text": " ".join(w["text"] for w in o_words[s:e])})  # fmt: skip
    drift = _drift(al["pairs"], a_words, o_words)
    o_tok = sum(len(tokens(w["text"])) for w in o_words)
    o_found = o_tok - sum(len(tokens(o_words[i]["text"])) for i in al["missing"])
    return {
        "cues": cues,
        "dropped_runs": [{"start": a_words[ws[0]]["start"], "end": a_words[ws[-1]]["end"],
                          "text": " ".join(a_words[i]["text"] for i in ws)} for ws in drop],
        "narration_in_ad_version": narration_a,
        "description_in_ad_version": ad_spans,
        "narration_missing_from_ad_version": missing_runs,
        "stats": {
            "original_words": len(o_words), "ad_version_words": len(a_words),
            "original_tokens": o_tok, "original_tokens_matched": o_found,
            "original_tokens_matched_share": round(o_found / o_tok, 4) if o_tok else None,
            "matched_word_pairs": len(al["pairs"]),
            "describer_words": sum(al["ad"]), "lines": len(cues), "dropped_runs": len(drop),
            "paused_lines": sum(c["paused"] for c in cues),
            "before_program_lines": sum(c["placement"] == "before_program" for c in cues),
            "duration_original_s": o_dur, "duration_ad_version_s": a_dur,
            "offset_drift_within_matched_runs_s": drift,
        },
    }  # fmt: skip


def _drift(pairs, a_words, o_words) -> dict:
    """Consistency of the time map: inside a run of consecutive matched words the AD version
    plays the original's narration unchanged, so (AD start - original start) should stay put.
    Spread of that offset per run: median and maximum over runs of 3+ words."""
    runs, cur = [], []
    for p in pairs:
        if cur and p[0] == cur[-1][0] + 1 and p[1] == cur[-1][1] + 1:
            cur.append(p)
        else:
            if len(cur) >= 3:
                runs.append(cur)
            cur = [p]
    if len(cur) >= 3:
        runs.append(cur)
    spreads = []
    for r in runs:
        off = [a_words[i]["start"] - o_words[j]["start"] for i, j in r]
        spreads.append(max(off) - min(off))
    if not spreads:
        return {"runs": 0, "median_s": None, "max_s": None}
    return {"runs": len(spreads), "median_s": round(float(np.median(spreads)), 3),
            "max_s": round(float(max(spreads)), 3)}  # fmt: skip


# -- ASR corrections and blocks -------------------------------------------------------------------


class CorrectionError(ValueError):
    """An ASR correction that its evidence does not support."""


def load_corrections() -> dict:
    if not CORRECTIONS_PATH.exists():
        return {"frames": [], "corrections": [], "unresolved": []}
    return json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8"))


def apply_corrections(video: str, cues: list[dict], spec: dict, original_words: list[dict]):
    """The lines with the corrections of refs/asr_corrections.json applied (copies; a
    corrected line keeps its recogniser text in `asr_text` and lists its corrections).

    A correction replaces its `wrong` text, which must be in the line, by `right`, which must
    be in its evidence: the on-screen text of a frame of the ORIGINAL video as recorded in
    `frames` (the excerpt cited must be part of that frame's recorded transcription), or the
    original video's narration (the excerpt must be in its transcript). Text compared with
    suite_a.text.norm (case and punctuation ignored). Anything else raises CorrectionError."""
    frames = {(f["video"], float(f["time_s"])): f for f in spec.get("frames", [])}
    narration = f" {norm(' '.join(w['text'] for w in original_words))} "
    out = {c["id"]: dict(c) for c in cues}
    for k in spec.get("corrections", []):
        if k["video"] != video:
            continue
        where = f"{video} {k['ref_line']}"
        line = out.get(k["ref_line"])
        if line is None:
            raise CorrectionError(f"{where}: unknown line")
        ev = k["evidence"]
        excerpt = f" {norm(ev['text'])} "
        if ev.get("type") == "frame":
            fr = frames.get((video, float(ev["time_s"])))
            if fr is None:
                raise CorrectionError(f"{where}: no frame at {ev['time_s']} s in `frames`")
            if excerpt not in f" {norm(fr['text'])} ":
                raise CorrectionError(f"{where}: the excerpt is not in the transcription of the "
                                      f"frame at {ev['time_s']} s")  # fmt: skip
            label = f"frame {float(ev['time_s']):g} s"
        elif ev.get("type") == "narration":
            if excerpt not in narration:
                raise CorrectionError(f"{where}: the excerpt is not in the original narration")
            label = "original narration"
        else:
            raise CorrectionError(f"{where}: evidence type {ev.get('type')!r} (frame or narration)")
        if f" {norm(k['right'])} " not in excerpt:
            raise CorrectionError(f"{where}: the evidence does not say {k['right']!r}")
        if k["wrong"] not in line["text"]:
            raise CorrectionError(f"{where}: {k['wrong']!r} is not in {k['ref_line']}")
        line.setdefault("asr_text", line["text"])
        line["text"] = line["text"].replace(k["wrong"], k["right"], 1)
        line.setdefault("corrections", []).append(
            {"wrong": k["wrong"], "right": k["right"], "evidence": label}
        )
    return [out[c["id"]] for c in cues]


def mark_blocks(cues: list[dict], by_gap: bool) -> None:
    """Give every line its block and the block's length in seconds. USGS (by_gap): the
    consecutive lines voiced in one AD-version gap (same anchor_range), from the first one's
    start to the last one's end. WebVTT: each cue is its own block."""
    groups: list[list[dict]] = []
    for c in cues:
        if by_gap and groups and groups[-1][-1].get("anchor_range") == c.get("anchor_range"):
            groups[-1].append(c)
        else:
            groups.append([c])
    for k, g in enumerate(groups, start=1):
        length = round(float(g[-1]["end"]) - float(g[0]["start"]), 3)
        for c in g:
            c["block"], c["block_s"] = f"b{k:03d}", length


# -- Elephants Dream ------------------------------------------------------------------------------


def best_offset(intervals, speech, lo: float = -5.0, hi: float = 5.0, step: float = 0.1) -> dict:
    """The shift of `intervals` (description) against `speech` that overlaps it least: a
    description track timed to this cut of the film sits between its dialogue at shift 0."""
    rows = []
    n = int(round((hi - lo) / step))
    for k in range(n + 1):
        d = round(lo + k * step, 3)
        rows.append((overlap_s([(a + d, b + d) for a, b in intervals], speech), d))
    best = min(rows, key=lambda r: (r[0], abs(r[1])))
    at0 = next(o for o, d in rows if d == 0)
    curve = {f"{d:+.1f}": o for o, d in rows if d in (-4.0, -2.0, -1.0, 0.0, 1.0, 2.0, 4.0)}
    return {"best_shift_s": best[1], "overlap_at_best_s": best[0], "overlap_at_0_s": at0,
            "overlap_by_shift_s": curve, "range_s": [lo, hi], "step_s": step}  # fmt: skip


def elephants(data: Path, refresh: bool) -> dict:
    spec = VIDEOS["elephants_dream"]
    media = data / spec["media"]["file"]
    refs = {}
    for src, r in spec["references"].items():
        path = data / r["file"]
        refs[src] = {**r, "kind": "vtt", "sha256": sha256_file(path),
                     "cues": parse_vtt(path.read_text(encoding="utf-8"), src)}  # fmt: skip
        for c in refs[src]["cues"]:
            c.update(anchor_time=c["start"], anchor_end=c["end"], placement="timed")
        mark_blocks(refs[src]["cues"], by_gap=False)
    wav = data / spec["hunt_audio"]["file"]
    voiced = energy_vad(decode_audio(wav, VAD["rate"]), VAD["rate"])
    hunt_cues = [(c["start"], c["end"]) for c in refs["hunt"]["cues"]]
    v_total = sum(b - a for a, b in voiced)
    inside = overlap_s(voiced, [(a - 0.5, b + 0.5) for a, b in hunt_cues])
    film = transcript("elephants_dream", media, data, refresh)
    speech = merge_spans(((w["start"], w["end"]) for w in clamped(film["words"])), SPAN_JOIN_S)
    hunt_audio = {
        **spec["hunt_audio"], "sha256": sha256_file(wav), "stream": "0:a:0",
        "duration_s": round(len(decode_audio(wav, 1000)) / 1000, 3),
        "vad": {**VAD, "intervals": [[a, b] for a, b in voiced],
                "voiced_s": round(v_total, 3)},
        "check_against_hunt_cues": {
            "voiced_s_within_0.5_s_of_a_cue": inside,
            "share": round(inside / v_total, 4) if v_total else None,
        },
    }  # fmt: skip
    check = {
        "what": "the film's dialogue (ASR words of this file, refs/asr/elephants_dream.json) "
        "against each description track shifted by -5..+5 s: a track timed to this cut of "
        "the film overlaps the dialogue least at shift 0",
        "hunt_voiced": best_offset(voiced, speech),
        **{f"{src}_cues": best_offset([(c["start"], c["end"]) for c in r["cues"]], speech)
           for src, r in refs.items()},
    }  # fmt: skip
    return {
        "video": "elephants_dream",
        "kind": "vtt",
        "title": spec["title"],
        "about": spec["about"],
        "media": {
            **spec["media"],
            "sha256": sha256_file(media),
            "duration_s": probe_duration(media),
        },  # fmt: skip
        "timeline": "the original film: every time below is a time in the film",
        "references": refs,
        "hunt_audio": hunt_audio,
        "alignment_check": check,
    }


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, check=True).stdout  # fmt: skip
    return round(float(out.strip()), 3)


def usgs(vid: str, data: Path, refresh: bool) -> dict:
    spec = VIDEOS[vid]
    o_path, a_path = data / spec["media"]["file"], data / spec["ad_media"]["file"]
    o_doc = transcript(vid, o_path, data, refresh)
    a_doc = transcript(f"{vid}_AD", a_path, data, refresh)
    o_dur, a_dur = probe_duration(o_path), probe_duration(a_path)
    ref = usgs_reference(vid, o_doc, a_doc, o_dur, a_dur)
    spec_c = load_corrections()
    cues = apply_corrections(vid, ref.pop("cues"), spec_c, o_doc["words"])
    mark_blocks(cues, by_gap=True)
    unresolved = [u for u in spec_c.get("unresolved", []) if u["video"] == vid]
    return {
        "video": vid,
        "kind": "usgs",
        "title": spec["title"],
        "about": spec["about"],
        "media": {**spec["media"], "sha256": sha256_file(o_path), "duration_s": o_dur},
        "ad_media": {**spec["ad_media"], "sha256": sha256_file(a_path), "duration_s": a_dur},
        "timeline": "anchor_time is a time in the original video; start/end are times in the "
        "AD version (whose video pauses for the description)",
        "asr": {
            "original": f"refs/asr/{vid}.json",
            "ad_version": f"refs/asr/{vid}_AD.json",
            "models": o_doc.get("asr_models"),
        },  # fmt: skip
        "references": {
            "usgs_ad": {
                **USGS_REFERENCE,
                "url": spec["ad_media"]["url"],
                "kind": "extended_ad",
                "cues": cues,
            }
        },  # fmt: skip
        "asr_corrections": {
            "applied": sum(len(c.get("corrections", [])) for c in cues),
            "lines": [c["id"] for c in cues if c.get("corrections")],
            "unresolved": unresolved,
        },
        "alignment": {
            "method": "difflib.SequenceMatcher on suite_a.text.tokens of both transcripts, "
            f"repairs: short chance matches <= {SHORT_EQUAL} tokens dropped, near-identical "
            f"tokens/runs (ratio >= {SIMILAR}) matched, describer runs slid along repeats "
            f"(<= {MAX_SHIFT} tokens) to sentence edges; describer runs under "
            f"{MIN_LINE_TOKENS} tokens dropped; paused = AD gap >= original gap + {PAUSE_S} s",
            **ref,
        },
    }


# -- the readable copy ----------------------------------------------------------------------------


def _mmss(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:06.3f}"


def markdown(doc: dict) -> str:
    lines = [f"# {doc['title']}", "", f"`{doc['media']['file']}` ({doc['media']['licence']}; "
             f"{doc['media']['credit']}), {doc['media']['duration_s']} s, sha256 "
             f"`{doc['media']['sha256'][:16]}...`. {doc['timeline']}.", ""]  # fmt: skip
    if doc["kind"] == "usgs":
        st = doc["alignment"]["stats"]
        lines += [f"AD version `{doc['ad_media']['file']}`, {doc['ad_media']['duration_s']} s. "
                  f"Original narration tokens matched in the AD version: "
                  f"{st['original_tokens_matched']}/{st['original_tokens']}; describer lines "
                  f"{st['lines']} ({st['paused_lines']} where the video pauses, "
                  f"{st['before_program_lines']} before the program starts); short runs "
                  f"dropped {st['dropped_runs']}.", ""]  # fmt: skip
    for src, r in doc["references"].items():
        lines += [f"## {src}: {r['author']} ({r['licence']})", "", r.get("url", ""), "",
                  "| id | start-end | anchor | text |", "|---|---|---|---|"]  # fmt: skip
        for c in r["cues"]:
            extra = " (paused)" if c.get("paused") else ""
            if c.get("placement") == "before_program":
                extra = " (before the program)"
            text = c["text"].replace("\n", " / ").replace("|", "\\|")
            for k in c.get("corrections", []):
                text += f" *(heard: {k['wrong']}; {k['evidence']})*"
            lines.append(f"| {c['id']} | {_mmss(c['start'])}-{_mmss(c['end'])} | "
                         f"{_mmss(c['anchor_time'])}{extra} | {text} |")  # fmt: skip
        lines.append("")
    if doc["kind"] == "usgs":
        al = doc["alignment"]
        if al["narration_missing_from_ad_version"]:
            lines += ["## Narration the AD version's transcript does not have", "",
                      "(cut from the AD mix, talked over by the describer, or heard "
                      "differently by the recogniser)", ""]  # fmt: skip
            lines += [f"- {_mmss(m['original'][0])}: {m['text']}"
                      for m in al["narration_missing_from_ad_version"]]  # fmt: skip
            lines.append("")
        unresolved = doc.get("asr_corrections", {}).get("unresolved", [])
        if unresolved:
            lines += ["## Suspicious terms left as heard (no evidence to correct them)", ""]
            lines += [f"- {u['ref_line']}: \"{u['term']}\" (likely {u['likely']}): {u['why']}"
                      for u in unresolved]  # fmt: skip
            lines.append("")
        if al["dropped_runs"]:
            lines += ["## Left-over runs too short to be lines", ""]
            lines += [f"- {_mmss(d['start'])} (AD version): {d['text']}"
                      for d in al["dropped_runs"]]  # fmt: skip
            lines.append("")
    if doc["kind"] == "vtt":
        ha, ck = doc["hunt_audio"], doc["alignment_check"]
        lines += ["## Hunt's voiced track and the timeline check", "",
                  f"Voiced {ha['vad']['voiced_s']} s in {len(ha['vad']['intervals'])} intervals "
                  f"(energy VAD: {VAD['frame_s'] * 1000:.0f} ms frames, > "
                  f"{VAD['threshold_dbfs']} dBFS, gaps < {VAD['bridge_s']} s bridged, runs < "
                  f"{VAD['min_len_s']} s dropped); share within 0.5 s of one of Hunt's cues: "
                  f"{ha['check_against_hunt_cues']['share']}.", ""]  # fmt: skip
        for k in ("hunt_voiced", "pfeiffer_cues", "hunt_cues"):
            b = ck[k]
            lines.append(
                f"- {k}: least dialogue overlap at shift {b['best_shift_s']} s "
                f"({b['overlap_at_best_s']} s; at 0: {b['overlap_at_0_s']} s)"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# -- spot check -----------------------------------------------------------------------------------


def spot_check(doc: dict, n: int = 5, seed: int = SPOT_SEED, context: int = 6) -> str:
    """n random describer lines of a USGS video with the AD-version words around them and the
    original's words around the anchor: a reader can see the line is the describer's (the
    narration on both sides continues in the original) without listening."""
    a_words = json.loads((HERE / doc["asr"]["ad_version"]).read_text(encoding="utf-8"))["words"]
    o_words = json.loads((HERE / doc["asr"]["original"]).read_text(encoding="utf-8"))["words"]
    cues = doc["references"]["usgs_ad"]["cues"]
    pick = sorted(random.Random(seed).sample(range(len(cues)), min(n, len(cues))))
    out = []
    for k in pick:
        c = cues[k]
        i0, i1 = c["ad_words"]
        before = " ".join(w["text"] for w in a_words[max(0, i0 - context) : i0])
        after = " ".join(w["text"] for w in a_words[i1 + 1 : i1 + 1 + context])
        t = c["anchor_time"]
        o_before = " ".join(w["text"] for w in o_words if t - 6 <= w["end"] <= t + 0.05)
        o_after = " ".join(w["text"] for w in o_words if t - 0.05 <= w["start"] <= t + 6)
        out.append(
            f"{doc['video']} {c['id']} AD {c['start']:.2f}-{c['end']:.2f} s -> original "
            f"{t:.2f} s{' (paused)' if c['paused'] else ''}\n"
            f"  AD version:  ...{before} [[{c['text']}]] {after}...\n"
            f"  original:    ...{o_before} || {o_after}..."
        )
    return "\n".join(out)


# -- main -----------------------------------------------------------------------------------------


def build(data: Path, refresh: bool) -> list[dict]:
    docs = [elephants(data, refresh)]
    docs += [usgs(v, data, refresh) for v, s in VIDEOS.items() if s["kind"] == "usgs"]
    for doc in docs:
        write_json(REFS / f"{doc['video']}.json", doc)
        (REFS / f"{doc['video']}.md").write_text(markdown(doc), encoding="utf-8", newline="\n")
    return docs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", type=Path, default=DATA_DEFAULT, help="the lull-data folder")
    ap.add_argument("--transcribe", action="store_true", help="ask the speech server again")
    ap.add_argument("--spot-check", action="store_true", help="print 5 random lines per video")
    args = ap.parse_args(argv)
    if args.spot_check:
        for v, s in VIDEOS.items():
            if s["kind"] == "usgs":
                doc = json.loads((REFS / f"{v}.json").read_text(encoding="utf-8"))
                print(spot_check(doc) + "\n")
        return 0
    for doc in build(args.data, args.transcribe):
        n = sum(len(r["cues"]) for r in doc["references"].values())
        print(f"{doc['video']}: {n} reference lines", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
