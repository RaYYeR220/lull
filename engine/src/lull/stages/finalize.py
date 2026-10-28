"""Finalise one cue: style, frame verification, voice and fit; then the overlap gate.

This is where a description is refused. Each cue goes through at most MAX_ROUNDS rounds:

1. autofix + lint. Errors get one Lightning repair; errors that survive it hold the cue.
   Warnings (R14-gender and the like) stay on the cue for a reviewer and do not block.
2. verify. The vision model judges every claim against a clip of the cue's own events. Claims
   that are not supported are cut by Lightning and what is left is verified again next round;
   nothing left holds the cue. The cue is held at once when the verifier rejects a claim that
   was already cut, or when the cut changes nothing; on the last round it is held as well.
   A held cue still loses its contradicted claims (never kept); not-visible ones stay for the
   reviewer to judge.
3. voice. The line is spoken and its duration measured. It fits when it fits the gap's
   usable window; up to 10 % over, it is sped up with atempo; beyond that Lightning compresses
   it towards floor(words x usable / duration x 0.95) words and the next round starts at
   step 1. A line that fits starts LEAD_IN_S before its first visual, or at the window's start
   if that is later. When it could not then finish before the window ends it starts as late as
   the window allows; if that is before its visual it is voiced again with "In a moment, "
   (and fitted again), so a listener never hears a description early without being told. It
   is also timed sentence by sentence from the measured audio: a sentence that would start at
   or after its visual ends sends the line back for compression too.

A look-ahead ("In a moment, ") is never lost silently: a repair or compression that drops one
gets one repair to restore it, else the cue is held; a claim cut may drop one only with the
sentence it introduced. Before round 1, a cast name the programme has not introduced yet gets
its descriptor at its first mention (ruling I4). Extended cues (no gap) are verified and voiced
but never fitted or timed.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from lull.media import audio_duration, cut_clip, probe
from lull.speech import SpeechClient
from lull.stages.lint import Finding, autofix, lint, quoted_spans
from lull.stages.plan import count_words
from lull.stages.schedule import AHEAD_PREFIX, lower_first
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
from lull.timeline import merge_spans
from lull.types import Check, Claim, Cue, Gap, RunOptions, Span, VisualEvent

log = logging.getLogger(__name__)

MAX_ROUNDS = 4
LEAD_IN_S = 0.3  # a line starts this long before its first visual when its pause allows
COMPRESS_MARGIN = 0.95
GATED = ("verified", "edited", "approved")  # statuses the overlap gate checks
OVERLAP_EPS = 1e-6
_FIT_EPS = 1e-6
_ARTICLES = frozenset({"a", "an", "the"})
_SENTENCE_END = re.compile(r"[.!?…][\"”’)]?\s+$")
_POSSESSIVE = re.compile(r"['’]s\b")
_PUNCT_NEXT = re.compile(r"\s*[,.;:!?…]")
_TOKEN = re.compile(r"[a-z0-9]+")
_TERMINAL = ".!?…"
_CLOSERS = "'’)]»"  # may follow a sentence's last mark (double quotes are handled apart)
_AHEAD = re.compile(r"\bin a moment\b", re.IGNORECASE)
_AHEAD_START = re.compile(r"\s*in a moment\b", re.IGNORECASE)
_STOP = frozenset(
    "a an the and or of to in on at by for from with into onto is are its their moment".split()
)


class CueContext(BaseModel):
    """What finalising one cue needs to know about the rest of the programme."""

    wps: float  # calibrated voice rate in words per second (fitting uses measured audio)
    cast: dict[str, str] = Field(default_factory=dict)  # name -> descriptor used before it
    named: set[str] = Field(default_factory=set)  # names lint checks for R15
    introduced: set[str] = Field(default_factory=set)  # introduced before this cue
    verbatim: list[str] = Field(default_factory=list)  # on-screen text of the cue's events
    proper_nouns: set[str] = Field(default_factory=set)  # spelled exactly; never visual claims
    video_duration: float | None = None  # probed once by the caller; probed here when None


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
    text: str, target_words: int, keep: list[str], tf: TokenFactory, *, run_id: str,
    note: str | None = None,
) -> str:  # fmt: skip
    """`text` shortened towards `target_words` words, every `keep` string intact (Lightning).
    The word target is guidance; the caller voices the result and measures it. `note` says
    what was wrong with a previous answer."""
    ask = f"Shorten it to about {target_words} words or fewer."
    user = "\n\n".join(
        [tagged("description", [text]), f"{ask} {note}" if note else ask, tagged("keep", keep)]
    )
    return rewrite("compress", user, tf, run_id=run_id, role="compress")


# -- sentences and look-aheads ------------------------------------------------------------------


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans of `text`. A mark ends a sentence when whitespace or the end follows it
    ("2.1" does not split); inside double quotes it does so only when the quote closes right
    after it ('A sign reads: "Exit." A door opens.')."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    quoted = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if start is None:
            if ch.isspace():
                i += 1
                continue
            start = i
        if ch == '"':
            quoted = not quoted
        elif ch == "“":
            quoted = True
        elif ch == "”":
            quoted = False
        elif ch in _TERMINAL:
            j = i + 1
            while j < n and text[j] in _TERMINAL:
                j += 1
            if quoted and j < n and text[j] in '"”':
                quoted = False  # the quote closes right after the mark
                j += 1
            elif quoted:
                i = j
                continue
            while j < n and text[j] in _CLOSERS:
                j += 1
            if j >= n or text[j].isspace():
                spans.append((start, j))
                start = None
            i = j
            continue
        i += 1
    if start is not None and text[start:].strip():
        spans.append((start, len(text.rstrip())))
    return spans


def _masked(text: str) -> str:
    """`text` with quoted on-screen text blanked out, same length."""
    chars = list(text)
    for s, e in quoted_spans(text):
        chars[s:e] = " " * (e - s)
    return "".join(chars)


def count_ahead(text: str) -> int:
    """Look-ahead prefixes ("In a moment") in `text`, quoted on-screen text left out."""
    return len(_AHEAD.findall(_masked(text)))


def _ahead_sentences(text: str) -> list[str]:
    masked = _masked(text)
    return [text[s:e] for s, e in split_sentences(text) if _AHEAD_START.match(masked[s:e])]


def _content(s: str) -> set[str]:
    return {t for t in _TOKEN.findall(s.lower()) if t not in _STOP}


def dropped_sentences(before: str, after: str, claims: Iterable[Claim]) -> list[str]:
    """Sentences of `before` that `after` lost (fewer than half their content words survive)
    although the `claims` cut were not about them (they cover less than half of its words)."""
    kept = _content(after)
    cut = set().union(*(_content(c.text) for c in claims))
    out = []
    for s, e in split_sentences(before):
        words = _content(before[s:e])
        if words and len(words & kept) * 2 < len(words) and len(words & cut) * 2 < len(words):
            out.append(before[s:e])
    return out


def _lost_ahead(before: str, after: str) -> bool:
    """True when a look-ahead sentence of `before` is still in `after` without its prefix: an
    unprefixed sentence of `after` keeps at least half of that sentence's own content words
    (those no other sentence of `before` has; all of them when it has none of its own), even
    when the rewrite merged it into a longer sentence."""
    spans = split_sentences(before)
    masked_before = _masked(before)
    words = [_content(before[s:e]) for s, e in spans]
    bodies = []
    for i, (s, e) in enumerate(spans):
        if not _AHEAD_START.match(masked_before[s:e]):
            continue
        others = set().union(*(w for j, w in enumerate(words) if j != i))
        own = (words[i] - others) or words[i]
        if own:
            bodies.append(own)
    masked = _masked(after)
    for s, e in split_sentences(after):
        if _AHEAD_START.match(masked[s:e]):
            continue
        kept = _content(after[s:e])
        if kept and any(len(kept & b) * 2 >= len(b) for b in bodies):
            return True
    return False


def described_events(parts: dict[str, str], text: str) -> list[str] | None:
    """The events (keys of `parts`, in order) whose own words mostly survive in `text`: at
    least half of the content words their composed sentence has and no other one has (all of
    its words when it has none of its own). A one-event line describes its event while it
    says anything. None without parts (nothing to judge: every event counts)."""
    if not parts:
        return None
    if not text.strip():
        return []
    if len(parts) == 1:
        return list(parts)
    words = {eid: _content(t) for eid, t in parts.items()}
    kept = _content(text)
    out = []
    for eid, mine in words.items():
        others = set().union(*(w for k, w in words.items() if k != eid))
        own = (mine - others) or mine
        if not own or len(own & kept) * 2 >= len(own):
            out.append(eid)
    return out


def refresh_described(cue: Cue) -> Cue:
    """`cue` with `described` recomputed from its parts and its current text (described_events;
    None when it lacks the parts of any of its events). Required after any change to a cue's
    text, such as an edit in the Studio: coverage and F113 read `described`, and a stale value
    would count an event the new text no longer mentions."""
    parts = {i: cue.parts[i] for i in cue.event_ids if i in cue.parts}
    described = described_events(parts, cue.text) if len(parts) == len(cue.event_ids) else None
    return cue.model_copy(update={"described": described})


# -- one cue -------------------------------------------------------------------------------------


def _lint(text: str, keep: list[str], ctx: CueContext) -> list[Finding]:
    """lint, counting as introduced the names introduced before this cue and those the text
    introduces itself (a rewrite may add or drop a descriptor)."""
    introduced = set(ctx.introduced) | introduced_in(text, ctx.cast)
    named = set(ctx.named) | set(ctx.cast)
    return lint(text, verbatim=keep, named=named, introduced=introduced)


def _claims_note(claims: list[Claim]) -> str:
    return "; ".join(f"{c.text} ({c.verdict.replace('_', ' ')})" for c in claims)


def _key(claim: Claim) -> str:
    return " ".join(claim.text.lower().split())


def _same(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


@dataclass
class _Finalise:
    """The state of one cue while it goes round; `run` returns the finished cue."""

    cue: Cue
    gap: Gap | None
    events: list[VisualEvent]  # in voicing order: start, then id
    video: Path
    work: Path
    tf: TokenFactory
    speech: SpeechClient
    opts: RunOptions
    ctx: CueContext
    run_id: str
    text: str = ""
    keep: list[str] = field(default_factory=list)  # on-screen text that must survive (R19)
    names: set[str] = field(default_factory=set)
    inserted: list[str] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    removed: list[Claim] = field(default_factory=list)
    words_before: int | None = None  # word count before the first compression
    checks: list[Check] = field(default_factory=list)  # every verification round
    clip: tuple[Path, float] | None = None
    at: float | None = None  # where in its window the voiced line starts
    prefixed: bool = False  # "In a moment, " added because the line starts before its visual

    @property
    def start(self) -> float:
        if self.gap is None:
            return self.cue.start
        return self.at if self.at is not None else self.gap.usable_start

    def place(self, dur: float) -> tuple[float, bool]:
        """Where a line of `dur` seconds starts in its window, and whether that is before its
        first visual: LEAD_IN_S before the visual (not before the window opens) when it can
        still finish in the window, else as late as the window allows."""
        us, ue = self.gap.usable_start, self.gap.usable_end
        first = min(e.t_start for e in self.events)
        want = max(us, math.ceil(round((first - LEAD_IN_S) * 1000, 6)) / 1000)
        if want + dur <= ue + _FIT_EPS:
            return want, False
        start = max(us, math.floor(round((ue - dur) * 1000, 6)) / 1000)
        return start, start < first - LEAD_IN_S - _FIT_EPS

    def run(self) -> Cue:
        ctx = self.ctx
        self.text, _, self.inserted = introduce_names(self.cue.text, ctx.cast, ctx.introduced)
        self.names = set(ctx.named) | set(ctx.cast) | set(ctx.introduced) | set(ctx.proper_nouns)
        self.keep = _verbatim_in(self.text, ctx.verbatim)
        for rnd in range(1, MAX_ROUNDS + 1):
            last = rnd == MAX_ROUNDS
            reason = self.style()
            if reason:
                return self.held(reason)
            self.claims = self.check_frames()
            if not self.claims:
                return self.held("the verifier found no visual claim to check")
            bad = [c for c in self.claims if c.verdict != "supported"]
            out = self.cut(bad, last) if bad else self.speak(last)
            if out is not None:
                return out
        raise AssertionError("unreachable")  # pragma: no cover - every last round returns

    # -- results -------------------------------------------------------------------------

    def _note(self, reason: str | None, extra: list[str]) -> str | None:
        parts = [reason] if reason else []
        if self.inserted:
            parts.append("descriptor added for " + ", ".join(self.inserted))
        if self.removed:
            n = len(self.removed)
            gone = "; ".join(c.text for c in self.removed)
            parts.append(f"{n} claim{'' if n == 1 else 's'} removed: {gone}")
        if self.words_before is not None:
            parts.append(f"compressed {self.words_before}→{count_words(self.text)} words")
        return "; ".join(parts + extra) or None

    def _result(
        self, status: str, reason: str | None, audio: Path | None, dur: float | None,
        tempo: float, ahead: bool, extra: list[str],
    ) -> Cue:  # fmt: skip
        log.info("finalize: %s %s%s", self.cue.id, status, f" ({reason})" if reason else "")
        return self.cue.model_copy(
            update={
                "text": self.text,
                "status": status,
                "start": self.start,
                "end": None if dur is None else self.start + dur,
                "ahead": ahead,
                "claims": self.claims,
                "audio_path": None if audio is None else str(audio),
                "audio_dur": dur,
                "tempo": tempo,
                "findings": [f.model_dump(mode="json") for f in self.findings],
                "note": self._note(reason, extra),
                "checks": list(self.checks),
                "described": self._described(),
            }
        )

    def _described(self) -> list[str] | None:
        return refresh_described(self.cue.model_copy(update={"text": self.text})).described

    def held(self, reason: str) -> Cue:
        """Held for a person; it keeps its placement, look-ahead included."""
        return self._result("held", reason, None, None, 1.0, self.cue.ahead, [])

    def done(self, audio: Path, dur: float, tempo: float) -> Cue:
        ahead = (self.cue.ahead or self.prefixed) and count_ahead(self.text) > 0
        extra = ["look-ahead sentence removed"] if self.cue.ahead and not ahead else []
        if self.prefixed and ahead:
            extra.append("look-ahead added: the line starts before its visual")
        status = "verified" if self.gap is not None else self.cue.status
        return self._result(status, None, audio, dur, tempo, ahead, extra)

    # -- style ---------------------------------------------------------------------------

    def _repair(self, text: str, errors: list[Finding]) -> str:
        names = names_in(text, self.names)
        out = repair(
            text, errors, self.tf, run_id=self.run_id, keep=self.keep, cast=self.ctx.cast,
            names=names,
        )  # fmt: skip
        return autofix(out)

    def keep_ahead(self, before: str, new: str) -> tuple[str, str | None]:
        """`new` when it keeps every look-ahead of `before`; otherwise one repair asks for them
        back, and the hold reason when it does not restore them."""
        want = count_ahead(before)
        if count_ahead(new) >= want:
            return new, None
        missing = Finding(
            rule="R2-ahead-format",
            message='a rewrite dropped the look-ahead "In a moment, "; begin the sentence(s) '
            "about what is coming with it again, as they were: "
            + " ".join(_ahead_sentences(before)),
        )
        fixed = self._repair(new, [missing])
        if count_ahead(fixed) >= want:
            return fixed, None
        return (
            fixed,
            'a rewrite dropped the look-ahead "In a moment, " and repair did not restore it',
        )

    def style(self) -> str | None:
        """autofix + lint; errors get one repair and a second lint. The hold reason, if any."""
        self.text = autofix(self.text)
        self.findings = _lint(self.text, self.keep, self.ctx)
        errors = [f for f in self.findings if f.severity == "error"]
        lost = None
        if errors and self.text:
            before = self.text
            self.text, lost = self.keep_ahead(before, self._repair(before, errors))
            self.findings = _lint(self.text, self.keep, self.ctx)
        if lost:
            return lost
        if not self.text:
            return "nothing left to say"
        rules = sorted({f.rule for f in self.findings if f.severity == "error"})
        return "lint errors after repair: " + ", ".join(rules) if rules else None

    # -- verify --------------------------------------------------------------------------

    def check_frames(self) -> list[Claim]:
        if self.clip is None:
            duration = self.ctx.video_duration
            if duration is None:
                duration = probe(self.video).duration
            lo, hi = clip_window(self.events, duration)
            dst = self.work / "verify" / f"{self.cue.id}.mp4"
            dst.parent.mkdir(parents=True, exist_ok=True)
            self.clip = (cut_clip(self.video, dst, lo, hi, height=CLIP_HEIGHT), lo)
        clip, offset = self.clip
        names = names_in(self.text, self.names)
        claims = verify(self.text, clip, self.tf, run_id=self.run_id, names=names, offset=offset)
        self.checks.append(Check(text=self.text, claims=claims))
        return claims

    def _removed_add(self, bad: list[Claim]) -> None:
        seen = {_key(c) for c in self.removed}
        self.removed.extend(c for c in bad if _key(c) not in seen)

    def cut(self, bad: list[Claim], last: bool) -> Cue | None:
        """Cut the claims the frames do not support; the held cue, or None to go round. A cut
        that drops a sentence the claims were not about is asked for once more with a note;
        doing it again holds the cue with the line as it was."""
        gone = {_key(c) for c in self.removed}
        again = [c for c in bad if _key(c) in gone]
        if again:
            reason = "the verifier rejected a removed claim again: " + _claims_note(again)
            return self.hold_unverified(reason, bad)
        if last:
            reason = f"not verified after {MAX_ROUNDS} rounds: " + _claims_note(bad)
            return self.hold_unverified(reason, bad)
        before = self.text
        new = remove_claims(before, bad, self.tf, run_id=self.run_id)
        lost = dropped_sentences(before, new, bad)
        if lost and not _same(new, before):
            note = (
                f'Your previous answer "{new}" dropped "{lost[0]}", which none of the listed '
                "claims is about. Remove only the listed claims and keep every other sentence."
            )
            new = remove_claims(before, bad, self.tf, run_id=self.run_id, note=note)
            lost = dropped_sentences(before, new, bad)
            if lost and not _same(new, before):
                reason = f'the claim cut dropped "{lost[0]}" twice: ' + _claims_note(bad)
                return self.hold_unverified(reason, bad, cut_tried=True)
        if _same(new, before):
            reason = "the rewrite removed nothing: " + _claims_note(bad)
            return self.hold_unverified(reason, bad, cut_tried=True)
        self._removed_add(bad)
        self.claims = []  # they judged the old text; the next round verifies the new one
        self.text = new
        if not new:
            return self.held("nothing left once the unverified claims were removed")
        if count_ahead(new) < count_ahead(before) and _lost_ahead(before, new):
            return self.held('a claim cut dropped "In a moment, " from a sentence it kept')
        said = " ".join(c.text.lower() for c in bad)
        self.keep = [k for k in self.keep if not _occurs(said, k.lower())]  # cut quotes only
        return None

    def hold_unverified(self, reason: str, bad: list[Claim], *, cut_tried: bool = False) -> Cue:
        """Held for review. Contradicted claims are cut from the text even so (never kept), and
        the text is withheld when they cannot be cut. The claims left keep their verdicts for
        the reviewer, and the findings are those of the text kept."""
        wrong = [c for c in bad if c.verdict == "contradicted"]
        if wrong:
            cut = (
                self.text
                if cut_tried
                else remove_claims(self.text, wrong, self.tf, run_id=self.run_id)
            )
            if _same(cut, self.text):
                self.text = ""
                reason += "; text withheld: a contradicted claim could not be cut"
            else:
                self.text = autofix(cut)
                self._removed_add(wrong)
            self.claims = [c for c in self.claims if c.verdict != "contradicted"]
            keep = [k for k in self.keep if _occurs(self.text, k)]
            self.findings = _lint(self.text, keep, self.ctx)
        return self.held(reason)

    # -- voice and fit -------------------------------------------------------------------

    def late(self, dur: float) -> list[tuple[int, float, float]]:
        """Sentences that would start at or after their visual ends, voiced from `start` for
        `dur` seconds: (index, estimated start, visual end). A sentence starts at
        start + dur x words before it / all words. Sentence k belongs to the k-th event in start
        order; when a rewrite left more or fewer sentences than events, k takes the earliest
        end among the events it may belong to (both keep their order)."""
        spans = split_sentences(self.text)
        total = count_words(self.text)
        if not spans or not total:
            return []
        n, m = len(spans), len(self.events)
        out = []
        for k, (s, _) in enumerate(spans):
            lo, hi = max(0, k - max(0, n - m)), min(m - 1, k + max(0, m - n))
            end = min(e.t_end for e in self.events[lo : hi + 1])
            at = self.start + dur * count_words(self.text[:s]) / total
            if at >= end - _FIT_EPS:
                out.append((k, at, end))
        return out

    def overcut(self, before: str, new: str, target: int) -> str | None:
        """What a compression of `before` cut that it should not have: more than half of the
        word target, or a visual's sentence (fewer sentences than visuals, as far as `before`
        had one per visual). None when it is fine, or empty (held by the caller)."""
        if not new:
            return None
        words = count_words(new)
        if words * 2 < target:
            return f"kept {words} of about {target} words"
        need = min(len(self.events), len(split_sentences(before)))
        have = len(split_sentences(new))
        if have < need:
            return f"left {have} sentence{'' if have == 1 else 's'} for {need} visuals"
        return None

    def shorten(self, target: int, reason: str) -> Cue | None:
        """Compress towards `target` words; the held cue, or None to go round. A compression
        that cuts too much (see `overcut`) is asked for once more with a note; cutting too much
        again holds the cue with the line as it was before compression."""
        if target < 1:
            return self.held(reason)
        before = self.text
        keep = [k for k in self.keep if _occurs(before, k)]
        new = compress(before, target, keep, self.tf, run_id=self.run_id)
        problem = self.overcut(before, new, target)
        if problem:
            n = len(self.events)
            each = f"keep a sentence for each of the {n} visuals" if n > 1 else "keep what it shows"
            note = (
                f'Your previous answer "{new}" {problem}. Shorten every sentence instead of '
                f"dropping one; {each}."
            )
            new = compress(before, target, keep, self.tf, run_id=self.run_id, note=note)
            problem = self.overcut(before, new, target)
            if problem:
                return self.held(f"compression {problem}, twice")
        if self.words_before is None:
            self.words_before = count_words(before)
        self.claims = []
        if not new:
            self.text = ""
            return self.held("nothing left after compression")
        self.text, lost = self.keep_ahead(before, autofix(new))
        return self.held(lost) if lost else None

    def speak(self, last: bool) -> Cue | None:
        """Voice, fit and time the line; the finished cue, or None to go round."""
        audio_dir = self.work / "audio"
        audio, dur = voice(
            self.text, self.opts.voice, self.speech, audio_dir / f"{self.cue.id}.wav"
        )
        fast_path = audio_dir / f"{self.cue.id}.t.wav"
        if self.gap is None:
            return self.done(audio, dur, 1.0)
        self.at = None
        usable = self.gap.usable_end - self.gap.usable_start
        tempo = 1.0
        if usable + _FIT_EPS < dur <= usable * MAX_TEMPO + _FIT_EPS:
            factor = min(MAX_TEMPO, math.ceil(dur / usable * 1000 - 1e-6) / 1000)
            fast = atempo(audio, fast_path, factor)
            fast_dur = audio_duration(fast)
            if fast_dur <= usable + _FIT_EPS:
                audio, dur, tempo = fast, fast_dur, factor
        if dur > usable + _FIT_EPS:
            fit = f"{dur:.2f} s voiced for a {usable:.2f} s pause"
            if last:
                return self.held(f"does not fit after {MAX_ROUNDS} rounds: {fit}")
            target = math.floor(count_words(self.text) * usable / dur * COMPRESS_MARGIN)
            return self.shorten(target, f"does not fit: {fit}")
        if tempo == 1.0:
            fast_path.unlink(missing_ok=True)
        start, early = self.place(dur)
        if early and count_ahead(self.text) == 0:
            names = set(self.ctx.proper_nouns) | set(self.ctx.cast)
            self.text = AHEAD_PREFIX + lower_first(self.text, names)
            self.prefixed = True
            log.info("finalize: %s starts before its visual; voiced again as a look-ahead",
                     self.cue.id)  # fmt: skip
            return self.speak(last)  # the longer line is fitted and placed again
        self.at = start
        late = self.late(dur)
        if not late:
            return self.done(audio, dur, tempo)
        k, at, end = late[0]
        reason = f"sentence {k + 1} would start at {at:.2f} s, after its visual ends at {end:.2f} s"
        if last:
            return self.held(f"{reason} (after {MAX_ROUNDS} rounds)")
        room = min(
            (t_end - self.start) / (t_at - self.start) if t_at > self.start else 0.0
            for _, t_at, t_end in late
        )
        return self.shorten(math.floor(count_words(self.text) * room * COMPRESS_MARGIN), reason)


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
    evs = sorted((events[i] for i in cue.event_ids), key=lambda e: (e.t_start, e.id))
    state = _Finalise(cue, gap, evs, Path(video), Path(work), tf, speech, opts, ctx, run_id)
    return state.run()


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


def _overlap(a: Span, spans: Iterable[Span]) -> float:
    """Seconds of `a` covered by `spans` (which do not overlap each other), unrounded."""
    return sum(max(0.0, min(a.end, s.end) - max(a.start, s.start)) for s in spans)


def overlap_report(cues: list[Cue], speech: list[Span]) -> dict:
    """How far voiced standard cues (verified, edited or approved, with a gap) overlap speech
    and each other, each cue taken as [start, start + audio_dur].

    `speech_s` sums the cues' overlap with speech, `between_cues_s` counts each overlapping
    pair once (`per_cue` charges it to both cues). Seconds are rounded to 3 dp; `offending`
    (cue ids in time order) is decided on the unrounded overlap, more than OVERLAP_EPS.
    """
    gated = _gated(cues)
    talk = merge_spans(speech)
    span = {c.id: Span(start=c.start, end=c.start + c.audio_dur) for c in gated}
    raw = {c.id: [_overlap(span[c.id], talk), 0.0] for c in gated}
    between = 0.0
    for i, a in enumerate(gated):
        for b in gated[i + 1 :]:
            if b.start >= span[a.id].end:
                break
            ov = _overlap(span[a.id], [span[b.id]])
            raw[a.id][1] += ov
            raw[b.id][1] += ov
            between += ov
    speech_s = sum(s for s, _ in raw.values())
    return {
        "checked": len(gated),
        "speech_s": round(speech_s, 3),
        "between_cues_s": round(between, 3),
        "total_s": round(speech_s + between, 3),
        "per_cue": {
            cid: {"speech_s": round(s, 3), "cues_s": round(c, 3)} for cid, (s, c) in raw.items()
        },
        "offending": [cid for cid, (s, c) in raw.items() if s > OVERLAP_EPS or c > OVERLAP_EPS],
    }


def assert_no_overlap(cues: list[Cue], speech: list[Span]) -> None:
    """Raise OverlapError naming every voiced standard cue that overlaps speech or another cue.
    Held and extended cues, and cues without a gap, are not checked."""
    offending = overlap_report(cues, speech)["offending"]
    if offending:
        raise OverlapError(offending)
