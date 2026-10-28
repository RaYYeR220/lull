"""Verify: every claim of a description is judged against its own frames.

A blind listener takes a description as fact, so it may say only what the frames show. The
engine numbers the description's tokens and Lightning splits it into atomic visual claims,
each naming the tokens it comes from (split_claims, checked by the engine: every content
token is said by a claim that holds it, and no claim says a word its tokens do not). The
vision model then sees a short clip around the cue's events (never the whole video) and
judges only those claims, by id: supported, not visible or contradicted (judge). It cannot
add a claim, and a claim it gives no verdict is not visible. The engine itself cuts the tokens
of the claims that failed, and Lightning only mends the grammar of what is left (mend), never
adding a word.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter
from collections.abc import Iterable
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel

from lull.tf import LIGHTNING, VL, TFUnusable, TokenFactory, video_part
from lull.types import Claim, VisualEvent

log = logging.getLogger(__name__)

STAGE = "finalize"
CLIP_PAD_S = 1.0  # context on each side of the cue's events
CLIP_CAP_S = 12.0  # longest clip sent to the verifier (cost control)
CLIP_MIN_S = 4.0  # shortest clip: a 1 s event needs context to be seen
CLIP_SEE_S = 2.0  # a capped clip runs at least this far past the last visual's start
CLIP_MAX_S = 20.0  # longest clip, when the visuals of one line start far apart
CLIP_HEIGHT = 360
VERIFY_MAX_TOKENS = 1500
VERIFY_TEMPERATURE = 0.0  # the same frames, the same verdict
VERIFY_ESTIMATE = 0.005  # a clip of up to CLIP_MAX_S (20 s), not just the 12 s cap
SPLIT_MAX_TOKENS = 1200
SPLIT_TEMPERATURE = 0.0
SPLIT_ESTIMATE = 0.0003
EDIT_MAX_TOKENS = 800
EDIT_TEMPERATURE = 0.2
EDIT_ESTIMATE = 0.0005
VERDICTS = ("supported", "not_visible", "contradicted")
UNSPECIFIED = "(unspecified claim)"  # stands in for a failed claim the verifier gave no text
SAID_SHARE = 0.5  # a claim is the text's when at least this share of its words is in it
_WORD = re.compile(r"[a-z0-9]+")
_POSSESSIVE = re.compile(r"['\u2019]s\b")
_NUMBER_WORDS = {
    w: str(n)
    for n, w in enumerate(
        "zero _ two three four five six seven eight nine ten eleven twelve thirteen fourteen "
        "fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
    if w != "_"  # "one" is as often "a" as a count
}
# words that frame a claim rather than state it ("the slide is titled ...", "there is ...")
_FRAMING = frozenset(
    """a an the and or but of to in on at by for from with into onto over under as is are was
    were be been being am there here this that these those it its they them their he she his her
    has have had do does did can could will would now then still also just some any each one
    moment visible seen see shown show shows showing displays display displayed list lists
    listed reads read reading says say said titled title labelled labeled label caption
    captioned text words word written appears appear appearing screen slide frame image picture
    video clip""".split()
)


def _list_of(key: str, props: dict) -> dict:
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                    "additionalProperties": False,
                },
            }
        },
        "required": [key],
        "additionalProperties": False,
    }


SPLIT_SCHEMA: dict = _list_of("claims", {
    "id": {"type": "string", "minLength": 1},
    "text": {"type": "string", "minLength": 1},
    "tokens": {"type": "array", "minItems": 1, "items": {
        "type": "array", "minItems": 2, "maxItems": 2,
        "items": {"type": "integer", "minimum": 0}}},
})  # fmt: skip
VERIFY_SCHEMA: dict = _list_of("verdicts", {
    "id": {"type": "string", "minLength": 1},
    "verdict": {"type": "string", "enum": list(VERDICTS)},
    "evidence": {"type": "string"},
    "at": {"anyOf": [{"type": "number"}, {"type": "null"}]},
})  # fmt: skip
# words no claim needs to cover: articles, copulas, pronouns, conjunctions and words that
# only frame time; negation, position and quantity words are facts and must be covered
_EXEMPT = frozenset(
    """a an the is are was were be been being am it its they them their theirs he him his she
    her hers this that these those there here who whom whose which what and or but nor so yet
    as while when then now meanwhile later soon""".split()
)
_TIME_WORDS = frozenset("now then meanwhile later soon".split())
_WORD_RE = re.compile(r"\w+(?:['\u2019]\w+)*")
_COUNT_WORDS = {**_NUMBER_WORDS, "one": "1"}  # a count in words is the same as in digits
TEXT_SCHEMA: dict = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}


@cache
def load_prompt(name: str) -> str:
    """A system prompt from prompts/<name>.md."""
    return (files("lull") / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def clip_window(
    events: Iterable[VisualEvent], duration: float, *, pad: float = CLIP_PAD_S,
    cap: float = CLIP_CAP_S, least: float = CLIP_MIN_S,
) -> tuple[float, float]:  # fmt: skip
    """[first start - pad, last end + pad] clamped to the video. When that is longer than `cap`
    it starts at first start - pad (a line describes a visual from its start, not its
    middle) and runs `cap` seconds, or as far as CLIP_SEE_S past the last visual's start
    when that is later (at most CLIP_MAX_S); when it is shorter than `least` it is the
    `least` seconds centred on the visuals. It is moved inside the video if it would leave it
    (a video shorter than the window gives all it has)."""
    evs = list(events)
    if not evs:
        raise ValueError("verify: a cue needs at least one event to check against")
    first, last = min(e.t_start for e in evs), max(e.t_end for e in evs)
    lo, hi = max(0.0, first - pad), min(duration, last + pad)
    if hi - lo > cap:
        size = min(CLIP_MAX_S, max(cap, max(e.t_start for e in evs) + CLIP_SEE_S - lo))
        lo, hi = lo, lo + size
        if hi > duration:
            lo, hi = max(0.0, duration - size), duration
    elif hi - lo < least:
        mid = (first + last) / 2
        lo, hi = mid - least / 2, mid + least / 2
        if lo < 0:
            lo, hi = 0.0, min(duration, least)
        if hi > duration:
            lo, hi = max(0.0, duration - least), duration
    if hi <= lo:
        raise ValueError(f"verify: events {first}..{last} lie outside the video (0..{duration})")
    return round(lo, 3), round(hi, 3)


def tagged(tag: str, lines: Iterable[str]) -> str:
    """`lines` inside <tag>...</tag>, "(none)" when there are none: model input as data."""
    body = "\n".join(lines) or "(none)"
    return f"<{tag}>\n{body}\n</{tag}>"


_WORST = {"supported": 0, "not_visible": 1, "contradicted": 2}


def _distinct(claims: list[Claim]) -> list[Claim]:
    """Each claim once, compared without case or spacing, in first-seen order; a
    repeat with a worse verdict makes the kept claim worse, never better. Claims without text
    (UNSPECIFIED) all stay."""
    kept: dict[object, Claim] = {}
    for n, c in enumerate(claims):
        # a claim the verifier gave no text is its own claim: never folded into another
        where = tuple(map(tuple, c.tokens)) if c.tokens else c.span
        key = n if c.text == UNSPECIFIED else (" ".join(c.text.lower().split()), where)
        first = kept.get(key)
        if first is None:
            kept[key] = c
        elif _WORST[c.verdict] > _WORST[first.verdict]:
            kept[key] = first.model_copy(update={"verdict": c.verdict})
    if len(kept) < len(claims):
        log.info("verify: %d repeated claims judged once", len(claims) - len(kept))
    return list(kept.values())


def _stem(word: str) -> str:
    """A crude English stem, enough to match "waves", "waved" and "waving"."""
    for suffix in ("ing", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


def _said_words(s: str) -> set[str]:
    plain = _POSSESSIVE.sub("", s.lower())
    return {_stem(w) for w in _WORD.findall(plain) if w not in _FRAMING}


def _numbers(s: str) -> set[str]:
    words = _WORD.findall(s.lower())
    return {_NUMBER_WORDS.get(w, w) for w in words if w in _NUMBER_WORDS or w.isdigit()}


def said_in(claim: str, text: str) -> bool:
    """Whether `claim` is something `text` says: every number it gives is in the text ("2" and
    "two" alike), and at least SAID_SHARE of its words (inflection and framing words such as
    "the slide is titled" aside) are. A claim of framing words only is the text's. The
    verifier also lists what the frames show beyond the line (the other bullets of a slide);
    those are not claims of the line."""
    if not _numbers(claim) <= _numbers(text):
        return False
    words = _said_words(claim)
    if not words:
        return True
    return len(words & _said_words(text)) >= SAID_SHARE * len(words)


def is_extra(claim: Claim, text: str) -> bool:
    """Whether `claim` is not something `text` says: it has no span in the text and its
    wording is not in it either (said_in). Deterministic: no model decides it."""
    if claim.tokens:  # split from the line itself
        return False
    return (claim.span is None or claim.span not in text) and not said_in(claim.text, text)


def claims_of(
    text: str, claims: Iterable[Claim], dropped: list[Claim] | None = None
) -> list[Claim]:
    """The claims of `text`: a supported claim stays only when the line says it (said_in), so
    the extras the verifier lists from the frames are not published as the line's. Every
    contradicted or not-visible claim stays, however it is worded: a failed check is never
    filtered away, and it decides the cue's fate."""
    kept, extra = [], []
    for c in claims:
        (extra if c.verdict == "supported" and is_extra(c, text) else kept).append(c)
    if dropped is not None:
        dropped.extend(extra)
    if extra:
        log.info("verify: %d supported claims the line does not make left out: %s", len(extra),
                 "; ".join(c.text for c in extra))  # fmt: skip
    return kept


class Token(NamedTuple):
    """One token of a line: a word (letters, digits, an inner apostrophe) or one punctuation
    mark, with its place in the line."""

    text: str
    start: int
    end: int

    @property
    def word(self) -> bool:
        return self.text[:1].isalnum() or self.text[:1] == "_"


_TOKEN_RE = re.compile(r"\w+(?:['’]\w+)*|[^\w\s]")


def tokenize(text: str) -> list[Token]:
    """The tokens of `text`, in order: deterministic, punctuation as tokens of its own."""
    return [Token(m.group(), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


def fold(word: str) -> str:
    """A word for comparison only: case and the curly apostrophe folded."""
    return word.replace("’", "'").lower()


_IRREGULAR = {
    "ran": "run", "sat": "sit", "stood": "stand", "went": "go", "came": "come", "saw": "see",
    "took": "take", "held": "hold", "fell": "fall", "threw": "throw", "wore": "wear",
    "ate": "eat", "flew": "fly", "drove": "drive", "rode": "ride", "spoke": "speak",
    "broke": "break", "began": "begin", "swam": "swim", "knelt": "kneel", "led": "lead",
    "met": "meet", "found": "find", "gave": "give", "got": "get", "made": "make",
    "said": "say", "told": "tell", "thought": "think", "brought": "bring", "caught": "catch",
    "left": "leave", "lit": "light", "hung": "hang", "shot": "shoot", "grew": "grow",
    "drew": "draw", "ridden": "ride", "taken": "take", "fallen": "fall", "worn": "wear",
    "men": "man", "women": "woman", "children": "child", "people": "person", "feet": "foot",
    "teeth": "tooth", "mice": "mouse",
}  # fmt: skip


def norm(word: str) -> str:
    """A word as the split check compares it: folded, a possessive dropped, a count as
    digits ("twelve" is "12"), an irregular form as its base ("ran" is "run"), then stemmed."""
    w = fold(word)
    if w.endswith("'s"):
        w = w[:-2]
    if w.isdigit():
        return w
    if w in _COUNT_WORDS:
        return _COUNT_WORDS[w]
    return _stem(_IRREGULAR.get(w, w))


# words a claim's text may hold without its tokens holding them
_PRONOUNS = frozenset(
    """he him his himself she her hers herself they them their theirs themselves it its itself
    i me my mine we us our ours you your yours""".split()
)  # a claim may say one only where its own tokens do: never another person's
_ARTICLES = frozenset({"a", "an", "the"})
_LIGHT = frozenset(
    """a an the is are was were be been being am has have had there it its they them their
    theirs he him his she her hers this that these those who whom whose which what of to in on
    at by for from with into onto over under up down off out about around through across
    along between behind beside near past against toward towards inside outside""".split()
)
FUNCTION_WORDS = _LIGHT | _EXEMPT  # words that tie the others together, saying nothing seen
PREPOSITIONS = frozenset(
    """of to in on at by for from with into onto over under up down off out about around
    through across along between behind beside near past against toward towards inside
    outside""".split()
)


def time_framing(tokens: list[Token]) -> set[int]:
    """Indices of tokens that only frame time: each "In a moment" (and its comma), and the
    words now, then, meanwhile, later and soon."""
    out: set[int] = set()
    words = [fold(t.text) for t in tokens]
    for i in range(len(tokens) - 2):
        if words[i : i + 3] == ["in", "a", "moment"]:
            out |= {i, i + 1, i + 2}
            if i + 3 < len(tokens) and tokens[i + 3].text == ",":
                out.add(i + 3)
    out |= {i for i, w in enumerate(words) if w in _TIME_WORDS}
    return out


def _named(tokens: list[Token], names: Iterable[str]) -> set[int]:
    """Indices of tokens that spell one of `names` (case and apostrophes aside): context."""
    out: set[int] = set()
    words = [fold(t.text) for t in tokens]
    for name in {n for n in names if n}:
        parts = [fold(t.text) for t in tokenize(name)]
        k = len(parts)
        for i in range(len(tokens) - k + 1):
            if k and words[i : i + k] == parts:
                out |= set(range(i, i + k))
    return out


def must_cover(tokens: list[Token], names: Iterable[str] = ()) -> list[int]:
    """The tokens some claim must cover: every word except articles, copulas, pronouns,
    conjunctions, the time framing and the programme's names (where they occur). Negation,
    position and quantity words are facts and must be covered."""
    skip = time_framing(tokens) | _named(tokens, names)
    return [i for i, t in enumerate(tokens)
            if t.word and i not in skip and fold(t.text) not in _EXEMPT]  # fmt: skip


def content_words(text: str) -> Counter:
    """The words of `text` that say something, normalised (see norm), with their counts:
    light words (articles, copulas, pronouns, prepositions, conjunctions) and the time
    framing aside. A mend of a cut line may drop such words but never add one."""
    return Counter(_content_seq(text))


def _content_seq(text: str) -> list[str]:
    tokens = tokenize(text)
    framing = time_framing(tokens)
    return [norm(t.text) for i, t in enumerate(tokens)
            if t.word and i not in framing
            and fold(t.text) not in _LIGHT and fold(t.text) not in _EXEMPT]  # fmt: skip


def _ends_sentence(tokens: list[Token], i: int) -> bool:
    """Whether token `i` ends a sentence: a full stop, question or exclamation mark or an
    ellipsis not glued to a word after it ("004.77" and "e.g" go on)."""
    if tokens[i].word or tokens[i].text not in ".!?\u2026":
        return False
    nxt = tokens[i + 1] if i + 1 < len(tokens) else None
    return not (nxt is not None and nxt.word and nxt.start == tokens[i].end)


def _sentences(text: str) -> list[tuple[str, list[str]]]:
    """Each sentence of `text` (see _ends_sentence) and its words, normalised (see norm),
    articles aside; a sentence with no such word is left out."""
    tokens = tokenize(text)
    out: list[tuple[str, list[str]]] = []
    first, cur = None, []
    for i, t in enumerate(tokens):
        first = i if first is None else first
        if t.word and fold(t.text) not in _ARTICLES:
            cur.append(norm(t.text))
        if _ends_sentence(tokens, i) or i == len(tokens) - 1:
            if cur:
                out.append((text[tokens[first].start : t.end], cur))
            first, cur = None, []
    return out


def mend_dropped(raw: str, mended: str) -> list[str] | None:
    """The sentences of the cut line `raw` a mend left out, or None when the mend does more
    than tidy it. Every word but the articles is compared (normalised: case, apostrophes,
    stems, numbers; see norm): the mend says the words of `raw` in their order, and may leave
    out only whole sentences of it. So it never adds, doubles or moves a word ("a red door"
    never becomes "a person in red"), puts back a preposition or a pronoun, or drops a word
    from a sentence it keeps ("does not")."""
    have = _sentences(raw)
    want = [w for _, s in _sentences(mended) for w in s]

    @cache
    def fit(i: int, j: int) -> tuple[int, ...] | None:  # sentences of have[i:] left out
        if j == len(want):
            return tuple(range(i, len(have)))
        if i == len(have):
            return None
        words = have[i][1]
        if want[j : j + len(words)] == words:
            kept = fit(i + 1, j + len(words))
            if kept is not None:
                return kept
        rest = fit(i + 1, j)
        return None if rest is None else (i, *rest)

    out = fit(0, 0)
    return None if out is None else [have[i][0] for i in out]


def mend_keeps(raw: str, mended: str) -> bool:
    """Whether a mend of the cut line `raw` only tidies it (see mend_dropped)."""
    return mend_dropped(raw, mended) is not None


def mend_ok(before: str, raw: str, mended: str) -> bool:
    """Whether `mended` may stand for the cut line `raw` of `before`: it only tidies `raw`
    (mend_keeps) and is not `before` again, articles and spelling aside."""
    flat = [w for _, s in _sentences(mended) for w in s]
    return (
        bool(flat)
        and flat != [w for _, s in _sentences(before) for w in s]
        and mend_keeps(raw, mended)
    )


class SplitClaim(BaseModel):
    """One atomic visual claim of a line and the tokens of the line it comes from (ranges,
    inclusive)."""

    id: str
    text: str
    tokens: list[tuple[int, int]]
    whole: bool = False  # a whole sentence the judge reads as written (sentence_gate)

    def indices(self) -> set[int]:
        return {i for a, b in self.tokens for i in range(a, b + 1)}

    def span(self, text: str) -> str:
        """The line's words the claim comes from: each range as it stands in the line, ranges
        apart joined by an ellipsis."""
        toks = tokenize(text)
        parts = [text[toks[a].start : toks[b].end] for a, b in sorted(self.tokens)
                 if 0 <= a <= b < len(toks)]  # fmt: skip
        return " … ".join(parts)


def check_split(text: str, claims: list[SplitClaim], names: Iterable[str] = ()) -> list[str]:
    """What is wrong with a split of `text`, empty when it is sound:
    - an id used twice, or a token range outside the line;
    - a token no claim covers: a token is covered by a claim only when it is in the claim's
      ranges and its word (normalised: see norm) is in the claim's own text, so a claim can
      neither leave a fact of its tokens unjudged nor be judged for it under another word;
    - a claim text that holds a word its own tokens do not (light words such as articles,
      copulas and prepositions aside), so a claim cannot add or swap a fact (a "blue hat"
      built from the tokens of "red hat ... blue car"); a pronoun or a name counts as such a
      word, so a claim never moves an action onto another person ("he smiles" from the tokens
      of "She smiles")."""
    tokens = tokenize(text)
    n = len(tokens)
    problems = []
    ids = [c.id for c in claims]
    if len(set(ids)) < len(ids):
        problems.append("two claims share an id")
    odd = [i for i in ids if not re.fullmatch(r"k\d+", i)]
    if odd:
        problems.append("claim ids must be k1, k2, ...: " + ", ".join(odd))
    covered: set[int] = set()
    for c in claims:
        if not c.tokens or any(not (0 <= a <= b < n) for a, b in c.tokens):
            problems.append(f"claim {c.id} has tokens outside 0..{n - 1}")
            continue
        own = c.indices()
        said = [fold(w) for w in _WORD_RE.findall(c.text)]
        said_norm = {norm(w) for w in said}
        covered |= {i for i in own if tokens[i].word and norm(tokens[i].text) in said_norm}
        theirs = {norm(tokens[i].text) for i in own if tokens[i].word}
        added = sorted({w for w in said
                        if (w in _PRONOUNS or (w not in _LIGHT and w not in _EXEMPT))
                        and norm(w) not in theirs})  # fmt: skip
        if added:
            problems.append(f"claim {c.id} says {', '.join(added)}, which its tokens do not")
    missing = [i for i in must_cover(tokens, names) if i not in covered]
    if missing:
        problems.append("tokens no claim says: "
                        + ", ".join(f"{i} {tokens[i].text!r}" for i in missing))  # fmt: skip
    return problems


def _parse_split(out: object) -> list[SplitClaim]:
    raw = out.get("claims") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        raise ValueError("split: model answer has no 'claims' list")
    claims = []
    for item in raw:
        item = item if isinstance(item, dict) else {}
        cid, text = (str(item.get(k) or "").strip() for k in ("id", "text"))
        ranges = []
        for r in item.get("tokens") or []:
            ints = (
                isinstance(r, list | tuple)
                and len(r) == 2
                and all(isinstance(x, int) and not isinstance(x, bool) for x in r)
            )
            if ints:
                ranges.append((int(r[0]), int(r[1])))
        if cid and text and ranges:
            claims.append(SplitClaim(id=cid, text=" ".join(text.split()), tokens=ranges))
    return claims


def sentence_claims(text: str) -> list[SplitClaim]:
    """The fallback split: one claim per sentence, its tokens the sentence's (the time framing
    aside), its text the sentence's words."""
    tokens = tokenize(text)
    framing = time_framing(tokens)
    out, start = [], 0
    for i, _t in enumerate(tokens + [Token(".", len(text), len(text))]):
        if i < len(tokens) and not _ends_sentence(tokens, i):
            continue  # "004.77" is one number, not two sentences
        idx = [k for k in range(start, min(i + 1, len(tokens))) if k not in framing]
        words = [k for k in idx if tokens[k].word]
        if words:
            a, b = words[0], idx[-1]
            body = text[tokens[a].start : tokens[b].end]
            out.append(SplitClaim(id=f"s{len(out) + 1}", text=body.rstrip(".!?… "),
                                  tokens=[(a, b)]))  # fmt: skip
        start = i + 1
    return out


CUT_OFF = (
    "the answer was too long and was cut off: give each claim one or a few ranges "
    "[first, last], not a range per token"
)
FALLBACK_NOTE = "claims: one per sentence (split failed)"


def split_claims(
    text: str, tf: TokenFactory, *, run_id: str, names: Iterable[str] = (),
    cache: dict[str, list[SplitClaim]] | None = None, notes: list[str] | None = None,
) -> list[SplitClaim]:  # fmt: skip
    """The atomic visual claims of `text` (Lightning, temperature 0, role "split"): the engine
    numbers the line's tokens and each claim names its tokens by range. The split is checked
    (check_split); one that fails is asked for once more, naming the exact problems (the
    tokens left out), then one claim per sentence stands in (logged, and noted in `notes`).
    `cache` keeps a split per text and names."""
    known = sorted({n for n in names if n})
    key = hashlib.sha256("\n".join([text, *known]).encode("utf-8")).hexdigest()
    if cache is not None and key in cache:
        return cache[key]
    numbered = [f"{i}: {t.text}" for i, t in enumerate(tokenize(text))]
    base = [tagged("description", [text]), tagged("tokens", numbered),
            tagged("names", [", ".join(known)] if known else [])]  # fmt: skip
    note = None
    claims: list[SplitClaim] = []
    problems: list[str] = []
    for _attempt in range(2):
        messages = [
            {"role": "system", "content": load_prompt("split")},
            {"role": "user", "content": "\n\n".join([*base, note] if note else base)},
        ]
        try:
            out = tf.json(
                LIGHTNING, messages, SPLIT_SCHEMA,
                run_id=run_id, stage=STAGE, role="split", thinking=False,
                max_tokens=SPLIT_MAX_TOKENS, temperature=SPLIT_TEMPERATURE,
                estimate=SPLIT_ESTIMATE,
            )  # fmt: skip
        except TFUnusable:  # cut off by the token limit: a failed attempt, not a failed run
            claims, problems = [], [CUT_OFF]
        else:
            claims = _parse_split(out)
            problems = check_split(text, claims, known) if claims else ["no claim was returned"]
        if not problems:
            break
        note = "Your previous answer was not usable: " + "; ".join(problems) + ". Split again."
    else:
        log.warning("verify: the claim split failed twice (%s); one claim per sentence",
                    "; ".join(problems))  # fmt: skip
        claims = sentence_claims(text)
        if notes is not None:
            notes.append(FALLBACK_NOTE)
    if cache is not None:
        cache[key] = claims
    return claims


def judge(
    text: str, claims: list[SplitClaim], clip: Path, tf: TokenFactory, *, run_id: str,
    names: Iterable[str] = (), offset: float = 0.0,
) -> list[Claim]:  # fmt: skip
    """Each of `claims` judged against the frames of `clip` only (role "verify"). The vision
    model returns a verdict per claim id and adds none: an id it gives no verdict (or an
    unknown verdict) is not visible, an id it was not given is ignored, an id given twice
    keeps its worse verdict. `offset` (the clip's start in the video) is added to every
    time."""
    if not claims:
        return []
    known = sorted({n for n in names if n})
    user = "\n\n".join([
        tagged("description", [text]),
        tagged("names", [", ".join(known)] if known else []),
        tagged("claims", [f"{c.id}: {c.text}" for c in claims]),
    ])  # fmt: skip
    messages = [
        {"role": "system", "content": load_prompt("verify")},
        {"role": "user", "content": [video_part(clip), {"type": "text", "text": user}]},
    ]
    out = tf.json(
        VL,
        messages,
        VERIFY_SCHEMA,
        run_id=run_id,
        stage=STAGE,
        role="verify",
        thinking=False,
        max_tokens=VERIFY_MAX_TOKENS,
        temperature=VERIFY_TEMPERATURE,
        estimate=VERIFY_ESTIMATE,
    )  # fmt: skip  (an answer cut off raises TFUnusable: the caller holds the line)
    raw = out.get("verdicts") if isinstance(out, dict) else None
    if not isinstance(raw, list):
        raise ValueError("verify: model answer has no 'verdicts' list")
    given: dict[str, dict] = {}
    for item in raw:
        if not (isinstance(item, dict) and isinstance(item.get("id"), str)):
            continue
        prev = given.get(item["id"])
        if prev is None or _WORST.get(item.get("verdict"), 1) > _WORST.get(prev.get("verdict"), 1):
            given[item["id"]] = item
    result = []
    for c in claims:
        item = given.get(c.id) or {}
        verdict = item.get("verdict")
        if verdict not in VERDICTS:
            log.warning("verify: claim %s got %s, counted as not visible", c.id,
                        f"verdict {verdict!r}" if item else "no verdict")  # fmt: skip
            verdict = "not_visible"
        at = item.get("at")
        ok = isinstance(at, int | float) and not isinstance(at, bool) and math.isfinite(at)
        evidence = item.get("evidence") if isinstance(item.get("evidence"), str) else ""
        result.append(Claim(
            text=c.text, verdict=verdict,
            at=round(max(0.0, float(at)) + offset, 3) if ok and verdict != "not_visible" else None,
            span=c.span(text), evidence=" ".join(evidence.split()) or None,
            tokens=[list(r) for r in c.tokens], kind="sentence" if c.whole else "atomic",
        ))  # fmt: skip
    return _distinct(result)


def verify(
    text: str, clip: Path, tf: TokenFactory, *, run_id: str, names: Iterable[str] = (),
    offset: float = 0.0, cache: dict[str, list[SplitClaim]] | None = None,
    dropped: list[Claim] | None = None, notes: list[str] | None = None,
) -> list[Claim]:  # fmt: skip
    """The claims of `text`, each judged against the frames of `clip`: split_claims, then
    judge. `names` are names the programme has established; they are context, not claims.
    Failed claims are never filtered away; supported ones the line does not make are left out
    and added to `dropped` (claims_of)."""
    claims = split_claims(text, tf, run_id=run_id, names=names, cache=cache, notes=notes)
    asked = claims + sentence_gate(text, claims)
    judged = judge(text, asked, clip, tf, run_id=run_id, names=names, offset=offset)
    return claims_of(text, judged, dropped)


def sentence_gate(text: str, claims: list[SplitClaim]) -> list[SplitClaim]:
    """Every whole sentence of `text` (ids s1, s2, ...; see sentence_claims), for the judge to
    read as written beside the atomic `claims`: what the atomic claims cannot hold (which
    detail belongs to whom, a negation, the sense of the whole) is judged there. A sentence
    that one of `claims` already says, on the same tokens, is not asked twice (the fallback
    split's claims are the sentences themselves)."""
    toks = tokenize(text)

    def says(c: SplitClaim) -> tuple:  # its words, and the line's words it comes from
        return _words(c.text), frozenset(i for i in c.indices() if i < len(toks) and toks[i].word)

    said = {says(c) for c in claims}
    # a split's ids are k1, k2, ... (check_split), or, fallen back, these very sentences
    return [s.model_copy(update={"whole": True}) for s in sentence_claims(text)
            if says(s) not in said]  # fmt: skip


def _words(text: str) -> tuple[str, ...]:
    return tuple(fold(w) for w in _WORD_RE.findall(text))


def rewrite(prompt: str, user: str, tf: TokenFactory, *, run_id: str, role: str) -> str:
    """One Lightning rewrite (prompts/<prompt>.md) answered as {"text": ...}; whitespace
    collapsed, and "" when nothing with a letter or digit is left."""
    messages = [
        {"role": "system", "content": load_prompt(prompt)},
        {"role": "user", "content": user},
    ]
    out = tf.json(
        LIGHTNING, messages, TEXT_SCHEMA,
        run_id=run_id, stage=STAGE, role=role, thinking=False,
        max_tokens=EDIT_MAX_TOKENS, temperature=EDIT_TEMPERATURE, estimate=EDIT_ESTIMATE,
    )  # fmt: skip
    text = out.get("text") if isinstance(out, dict) else None
    if not isinstance(text, str):
        raise ValueError(f"{role}: model answer has no 'text' string")
    text = " ".join(text.split())
    return text if any(c.isalnum() for c in text) else ""


def mend(text: str, cut: list[str], tf: TokenFactory, *, run_id: str) -> str:
    """The grammar of `text` (a line the engine cut words out of) mended by Lightning (role
    "mend"); `cut` lists the words that were cut, which must not come back. The caller checks
    the answer (it may only keep or drop words, never add one); "" when there is none."""
    parts = [tagged("description", [text]), tagged("cut", [", ".join(cut)] if cut else [])]
    try:
        return rewrite("mend", "\n\n".join(parts), tf, run_id=run_id, role="mend")
    except (TFUnusable, ValueError) as e:  # no usable mend: the caller keeps the raw cut
        log.warning("verify: the mend gave no usable answer (%s)", e)
        return ""
