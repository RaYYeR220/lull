"""Finalise one cue: style, frame verification, voice and fit; then the overlap gate.

This is where a description is refused. Each cue goes through at most MAX_ROUNDS rounds:

1. autofix + lint. Errors get one Lightning repair; errors that survive it hold the cue.
   Warnings (R14-gender and the like) stay on the cue for a reviewer and do not block.
2. verify. The vision model judges every claim against a clip of the cue's own events. Claims
   that are not supported are cut by Lightning and what is left is verified again next round;
   nothing left holds the cue. On the last round the cue is held instead: contradicted claims
   are still cut from its text (never kept), not-visible ones stay for the reviewer to judge.
3. voice. The line is spoken and its duration measured. It fits when it ends by the gap's
   usable end; up to 10 % over, it is sped up with atempo; beyond that Lightning compresses it
   towards floor(words x usable / duration x 0.95) words and the next round starts at step 1.

Before round 1, a cast name the programme has not introduced yet gets its descriptor at its
first mention (ruling I4). Extended cues (no gap) are verified and voiced but never fitted.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, Field

from lull.media import audio_duration, cut_clip, probe
from lull.speech import SpeechClient
from lull.stages.lint import Finding, autofix, lint, quoted_spans
from lull.stages.plan import count_words
from lull.stages.verify import (
    CLIP_HEIGHT,
    clip_window,
    remove_claims,
    rewrite,
    tagged,
    verify,
)
from lull.stages.voice import MAX_TEMPO, atempo, voice
from lull.tf import TokenFactory
from lull.timeline import overlap_seconds
from lull.types import Claim, Cue, Gap, RunOptions, Span, VisualEvent

log = logging.getLogger(__name__)

MAX_ROUNDS = 4
COMPRESS_MARGIN = 0.95
GATED = ("verified", "edited")  # statuses the overlap gate checks
OVERLAP_EPS = 1e-6
_FIT_EPS = 1e-6
_ARTICLES = frozenset({"a", "an", "the"})
_SENTENCE_END = re.compile(r"[.!?…][\"”’)]?\s+$")
_POSSESSIVE = re.compile(r"['’]s\b")
_PUNCT_NEXT = re.compile(r"\s*[,.;:!?…]")
_TOKEN = re.compile(r"[a-z0-9]+")


class CueContext(BaseModel):
    """What finalising one cue needs to know about the rest of the programme."""

    wps: float  # calibrated voice rate in words per second (fitting uses measured audio)
    cast: dict[str, str] = Field(default_factory=dict)  # name -> descriptor used before it
    named: set[str] = Field(default_factory=set)  # names lint checks for R15
    introduced: set[str] = Field(default_factory=set)  # introduced before this cue
    verbatim: list[str] = Field(default_factory=list)  # on-screen text of the cue's events
    proper_nouns: set[str] = Field(default_factory=set)  # spelled exactly; never visual claims


class OverlapError(Exception):
    """Voiced descriptions overlap speech or each other."""

    def __init__(self, cue_ids: list[str]) -> None:
        self.cue_ids = list(cue_ids)
        super().__init__("descriptions overlap speech or each other: " + ", ".join(self.cue_ids))


# -- names ---------------------------------------------------------------------------------


def _name_re(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")


def names_in(text: str, names: Iterable[str]) -> set[str]:
    """The names that occur in `text` as whole words (case-sensitive): after a cue is voiced,
    the pipeline adds its cast names to the introduced set."""
    return {n for n in names if n and _name_re(n).search(text)}


def _tokens(s: str) -> list[str]:
    return [t for t in _TOKEN.findall(s.lower()) if t not in _ARTICLES]


def _has_descriptor(before: str, descriptor: str) -> bool:
    """True when the words right before a name already are its descriptor (articles aside)."""
    want = _tokens(descriptor)
    return bool(want) and _tokens(before)[-len(want) :] == want


def _descriptor_at(descriptor: str, sentence_start: bool) -> str:
    if sentence_start:
        return descriptor[0].upper() + descriptor[1:]
    if descriptor.split(maxsplit=1)[0].lower() in _ARTICLES:
        return descriptor[0].lower() + descriptor[1:]
    return descriptor


def introduce_names(
    text: str, cast: dict[str, str], introduced: set[str]
) -> tuple[str, set[str], list[str]]:
    """Give each cast name not yet introduced its descriptor at its first mention (R15).

    "Proog climbs." becomes "A bald person in a dark coat, Proog, climbs."; the descriptor is
    capitalised at a sentence start and its leading article lower-cased elsewhere. A name the
    text already introduces (inside quoted on-screen text, or right after its descriptor) is left
    as it is; a possessive first mention ("Proog's") is left to lint and repair. Returns the
    text, the names this cue introduces, and the names that got a descriptor, in text order.
    """
    already = introduced_in(text, cast)
    found = sorted(
        (m.start(), n)
        for n in cast
        if n and n not in introduced and n not in already and (m := _name_re(n).search(text))
    )
    inserted: list[str] = []
    for _, name in found:
        m = _name_re(name).search(text)
        before, after = text[: m.start()], text[m.end() :]
        descriptor = _descriptor(cast, name)
        if not descriptor or _POSSESSIVE.match(after):
            continue
        start = not before.strip() or _SENTENCE_END.search(before) is not None
        comma = "" if not after.strip() or _PUNCT_NEXT.match(after) else ","
        text = f"{before}{_descriptor_at(descriptor, start)}, {name}{comma}{after}"
        inserted.append(name)
    return text, (already - introduced) | set(inserted), inserted


def _descriptor(cast: dict[str, str], name: str) -> str:
    return " ".join(cast[name].split()).strip(" ,;:.")


def introduced_in(text: str, cast: dict[str, str]) -> set[str]:
    """Cast names `text` introduces itself: read inside quoted on-screen text, or given with
    their descriptor right before their first mention."""
    quoted = [text[s:e] for s, e in quoted_spans(text)]
    out: set[str] = set()
    for name in cast:
        pat = _name_re(name) if name else None
        m = pat.search(text) if pat else None
        if m is None:
            continue
        if any(pat.search(q) for q in quoted) or _has_descriptor(
            text[: m.start()], _descriptor(cast, name)
        ):
            out.add(name)
    return out


# -- on-screen text ----------------------------------------------------------------------------


def _occurs(text: str, piece: str) -> bool:
    return bool(piece) and re.search(rf"(?<!\w){re.escape(piece)}(?!\w)", text) is not None


def _verbatim_in(text: str, verbatim: Iterable[str]) -> list[str]:
    """On-screen text the cue reads exactly: each inventory string (whitespace collapsed), or
    else each of its lines, that occurs in `text` as whole words and holds a letter.

    These must survive every rewrite (R19). Text the cue never read is not demanded: the plan
    may read only a slide's title or its key line.
    """
    out: list[str] = []
    for v in verbatim:
        whole = " ".join(v.split())
        pieces = [whole] if _occurs(text, whole) else [" ".join(s.split()) for s in v.splitlines()]
        for p in pieces:
            if p not in out and any(c.isalpha() for c in p) and _occurs(text, p):
                out.append(p)
    return out


# -- Lightning rewrites ------------------------------------------------------------------------


def repair(
    text: str, errors: list[Finding], tf: TokenFactory, *, run_id: str,
    keep: Iterable[str] = (), cast: dict[str, str] | None = None, names: Iterable[str] = (),
) -> str:  # fmt: skip
    """`text` with the listed lint errors fixed and nothing else changed (Lightning)."""
    cast = cast or {}
    lines = []
    for f in errors:
        line = f"- {f.rule}: {f.message}"
        if f.span:
            words = text[f.span[0] : f.span[1]]
            line += f' ("{words}")'
            if f.rule == "R15-name":
                desc = cast.get(words)
                line += f'; descriptor: "{desc}"' if desc else "; no descriptor: describe them"
        lines.append(line)
    known = sorted(set(names))
    user = "\n\n".join(
        [
            tagged("description", [text]),
            tagged("findings", lines),
            tagged("keep", keep),
            tagged("names", [", ".join(known)] if known else []),
        ]
    )
    return rewrite("repair", user, tf, run_id=run_id, role="repair")


def compress(
    text: str, target_words: int, keep: list[str], tf: TokenFactory, *, run_id: str
) -> str:
    """`text` shortened towards `target_words` words, every `keep` string intact (Lightning).
    The word target is guidance; the caller voices the result and measures it."""
    user = "\n\n".join(
        [
            tagged("description", [text]),
            f"Shorten it to about {target_words} words or fewer.",
            tagged("keep", keep),
        ]
    )
    return rewrite("compress", user, tf, run_id=run_id, role="compress")


# -- one cue -------------------------------------------------------------------------------------


def _lint(text: str, keep: list[str], ctx: CueContext) -> list[Finding]:
    """lint, counting as introduced the names introduced before this cue and those the text
    introduces itself (a rewrite may add or drop a descriptor)."""
    introduced = set(ctx.introduced) | introduced_in(text, ctx.cast)
    named = set(ctx.named) | set(ctx.cast)
    return lint(text, verbatim=keep, named=named, introduced=introduced)


def _style(
    text: str, keep: list[str], ctx: CueContext, names: set[str], tf: TokenFactory, run_id: str
) -> tuple[str, list[Finding]]:
    """autofix + lint; errors get one repair and a second lint."""
    text = autofix(text)
    findings = _lint(text, keep, ctx)
    errors = [f for f in findings if f.severity == "error"]
    if errors and text:
        fixed = repair(
            text, errors, tf, run_id=run_id, keep=keep, cast=ctx.cast, names=names_in(text, names)
        )
        text = autofix(fixed)
        findings = _lint(text, keep, ctx)
    return text, findings


def _clip(video: Path, work: Path, cue_id: str, events: list[VisualEvent]) -> tuple[Path, float]:
    lo, hi = clip_window(events, probe(video).duration)
    dst = work / "verify" / f"{cue_id}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    return cut_clip(video, dst, lo, hi, height=CLIP_HEIGHT), lo


def _claims_note(claims: list[Claim]) -> str:
    return "; ".join(f"{c.text} ({c.verdict.replace('_', ' ')})" for c in claims)


def finalize_cue(
    cue: Cue,
    gap: Gap | None,
    events: dict[str, VisualEvent],
    video: Path,
    work: Path,
    tf: TokenFactory,
    speech: SpeechClient,
    opts: RunOptions,
    ctx: CueContext,
    *,
    run_id: str,
) -> Cue:
    """The cue verified and voiced, or held with the reason in `note`.

    A standard cue (with its gap) comes back "verified" with start = gap.usable_start and
    end = start + measured audio duration; an extended cue (gap None) keeps "extended" and its
    start. Model calls carry stage "finalize" and roles verify, repair, remove and compress.
    """
    if (gap is None) != (cue.gap_id is None) or (gap is not None and gap.id != cue.gap_id):
        got = gap.id if gap else None
        raise ValueError(f"finalize: cue {cue.id} belongs to gap {cue.gap_id!r}, got {got!r}")
    missing = [i for i in cue.event_ids if i not in events]
    if missing or not cue.event_ids:
        raise ValueError(f"finalize: cue {cue.id} has unknown events {missing or cue.event_ids}")
    evs = [events[i] for i in cue.event_ids]
    work = Path(work)
    audio_dir = work / "audio"
    start = gap.usable_start if gap is not None else cue.start

    text, _, inserted = introduce_names(cue.text, ctx.cast, ctx.introduced)
    names = set(ctx.named) | set(ctx.cast) | set(ctx.introduced) | set(ctx.proper_nouns)
    keep = _verbatim_in(text, ctx.verbatim)
    claims: list[Claim] = []
    findings: list[Finding] = []
    removed: list[Claim] = []
    words_before: int | None = None  # word count before the first compression
    clip: tuple[Path, float] | None = None

    def note(reason: str | None = None) -> str | None:
        parts = [reason] if reason else []
        if inserted:
            parts.append("descriptor added for " + ", ".join(inserted))
        if removed:
            n = len(removed)
            parts.append(
                f"{n} claim{'' if n == 1 else 's'} removed: " + "; ".join(c.text for c in removed)
            )
        if words_before is not None:
            parts.append(f"compressed {words_before}→{count_words(text)} words")
        return "; ".join(parts) or None

    def result(status: str, reason: str | None = None, audio: Path | None = None,
               dur: float | None = None, tempo: float = 1.0) -> Cue:  # fmt: skip
        log.info("finalize: %s %s%s", cue.id, status, f" ({reason})" if reason else "")
        return cue.model_copy(
            update={
                "text": text,
                "status": status,
                "start": start,
                "end": None if dur is None else start + dur,
                "ahead": cue.ahead and "in a moment" in text.lower(),
                "claims": claims,
                "audio_path": None if audio is None else str(audio),
                "audio_dur": dur,
                "tempo": tempo,
                "findings": [f.model_dump(mode="json") for f in findings],
                "note": note(reason),
            }
        )

    def removed_add(bad: list[Claim]) -> None:
        seen = {c.text for c in removed}
        removed.extend(c for c in bad if c.text not in seen)

    for rnd in range(1, MAX_ROUNDS + 1):
        last = rnd == MAX_ROUNDS
        text, findings = _style(text, keep, ctx, names, tf, run_id)
        if not text:
            return result("held", "nothing left to say")
        errors = sorted({f.rule for f in findings if f.severity == "error"})
        if errors:
            return result("held", "lint errors after repair: " + ", ".join(errors))

        if clip is None:
            clip = _clip(video, work, cue.id, evs)
        claims = verify(
            text, clip[0], tf, run_id=run_id, names=names_in(text, names), offset=clip[1]
        )
        if not claims:
            return result("held", "the verifier found no visual claim to check")
        bad = [c for c in claims if c.verdict != "supported"]
        if bad:
            if last:
                wrong = [c for c in bad if c.verdict == "contradicted"]
                if wrong:  # never kept, even for a reviewer
                    text = remove_claims(text, wrong, tf, run_id=run_id)
                    removed_add(wrong)
                return result(
                    "held", f"not verified after {MAX_ROUNDS} rounds: " + _claims_note(bad)
                )
            text = remove_claims(text, bad, tf, run_id=run_id)
            removed_add(bad)
            claims = []  # they judged the old text; the next round verifies the new one
            if not text:
                return result("held", "nothing left once the unverified claims were removed")
            keep = [k for k in keep if _occurs(text, k)]  # a removed quote is not demanded back
            continue

        audio, dur = voice(text, opts.voice, speech, audio_dir / f"{cue.id}.wav")
        fast_path = audio_dir / f"{cue.id}.t.wav"
        if gap is None:
            return result(cue.status, audio=audio, dur=dur)
        usable = gap.usable_end - cue.start
        if dur <= usable + _FIT_EPS:
            fast_path.unlink(missing_ok=True)
            return result("verified", audio=audio, dur=dur)
        if dur <= usable * MAX_TEMPO + _FIT_EPS:
            factor = min(MAX_TEMPO, math.ceil(dur / usable * 1000 - 1e-6) / 1000)
            fast = atempo(audio, fast_path, factor)
            fast_dur = audio_duration(fast)
            if fast_dur <= usable + _FIT_EPS:
                return result("verified", audio=fast, dur=fast_dur, tempo=factor)
        if last:
            return result(
                "held",
                f"does not fit after {MAX_ROUNDS} rounds: {dur:.2f} s voiced "
                f"for a {usable:.2f} s pause",
            )
        words = count_words(text)
        target = math.floor(words * usable / dur * COMPRESS_MARGIN)
        if target < 1:
            return result("held", f"does not fit: {dur:.2f} s voiced for a {usable:.2f} s pause")
        if words_before is None:
            words_before = words
        text = compress(text, target, [k for k in keep if _occurs(text, k)], tf, run_id=run_id)
        claims = []
        if not text:
            return result("held", "nothing left after compression")
    raise AssertionError("unreachable")  # pragma: no cover - every last round returns


# -- overlap gate --------------------------------------------------------------------------------


def _gated(cues: Iterable[Cue]) -> list[Cue]:
    out = []
    for c in cues:
        if c.status not in GATED or c.gap_id is None:
            continue
        if c.audio_dur is None:
            raise ValueError(f"overlap gate: cue {c.id} is {c.status} but has no measured audio")
        out.append(c)
    return sorted(out, key=lambda c: (c.start, c.id))


def overlap_report(cues: list[Cue], speech: list[Span]) -> dict:
    """How far voiced standard cues (verified or edited, with a gap) overlap speech and each
    other, each cue taken as [start, start + audio_dur]; seconds rounded to 3 dp.

    `speech_s` sums the cues' overlap with speech, `between_cues_s` counts each overlapping
    pair once (`per_cue` charges it to both cues), `offending` lists cue ids in time order.
    """
    gated = _gated(cues)
    span = {c.id: Span(start=c.start, end=c.start + c.audio_dur) for c in gated}
    per = {c.id: {"speech_s": overlap_seconds([span[c.id]], speech), "cues_s": 0.0} for c in gated}
    between = 0.0
    for i, a in enumerate(gated):
        for b in gated[i + 1 :]:
            if b.start >= span[a.id].end:
                break
            ov = overlap_seconds([span[a.id]], [span[b.id]])
            per[a.id]["cues_s"] += ov
            per[b.id]["cues_s"] += ov
            between += ov
    for v in per.values():
        v["cues_s"] = round(v["cues_s"], 3)
    speech_s = round(sum(v["speech_s"] for v in per.values()), 3)
    between = round(between, 3)
    return {
        "checked": len(gated),
        "speech_s": speech_s,
        "between_cues_s": between,
        "total_s": round(speech_s + between, 3),
        "per_cue": per,
        "offending": [
            cid
            for cid, v in per.items()
            if v["speech_s"] > OVERLAP_EPS or v["cues_s"] > OVERLAP_EPS
        ],
    }


def assert_no_overlap(cues: list[Cue], speech: list[Span]) -> None:
    """Raise OverlapError naming every voiced standard cue that overlaps speech or another cue.
    Held and extended cues, and cues without a gap, are not checked."""
    offending = overlap_report(cues, speech)["offending"]
    if offending:
        raise OverlapError(offending)
