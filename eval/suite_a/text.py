"""Text helpers shared by the spec validator, the key and the scorer: normalised text, content
words and the numbers a sentence states (in digits or in words).

Everything here is deterministic and model-free, so the checks built on it can be re-run and
audited by anyone.
"""

from __future__ import annotations

import math
import re

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                         "–": "-", "—": "-", "−": "-"})  # fmt: skip
# a number in digits: not glued to a letter before it ("CO2" is no number), thousands commas
_NUMBER = re.compile(r"(?<![A-Za-z0-9.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?![0-9])")

STOP = frozenset(
    """a an the and or but of to in on at by for from with into onto over under as is are was
    were be been being it its this that these those there here than then so such per via
    each every all some any most more less very also just only not no nor can could may might
    will would shall should do does did has have had their they them we our you your i he she
    his her which who whom whose what when where why how about between across after before
    during while up down out off one""".split()
)

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}


def clean(text: str) -> str:
    """Typographic quotes and dashes made plain."""
    return text.translate(_QUOTES)


def tokens(text: str) -> list[str]:
    """Lower-case word and number tokens (a decimal stays one token: "3.5")."""
    return _TOKEN.findall(clean(text).lower().replace(",", ""))


def norm(text: str) -> str:
    """The text as space-separated tokens: case, punctuation and spacing ignored."""
    return " ".join(tokens(text))


def contains_phrase(haystack: str, needle: str) -> bool:
    """True when `needle`'s tokens occur in `haystack` as a whole-token run."""
    n = norm(needle)
    return bool(n) and f" {n} " in f" {norm(haystack)} "


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def content_words(text: str) -> set[str]:
    """Tokens that carry meaning: stop words dropped, a plural 's' removed."""
    return {_stem(t) for t in tokens(text) if t not in STOP}


def overlap(part: str, whole: str) -> float:
    """Share of `part`'s content words that also occur in `whole` (0 when `part` has none)."""
    a = content_words(part)
    if not a:
        return 0.0
    return len(a & content_words(whole)) / len(a)


def _word_numbers(text: str) -> set[float]:
    """Numbers written in English words: "forty-five", "two hundred", "twelve thousand",
    "three point five". Good enough for the short numbers charts carry."""
    words = re.findall(r"[a-z]+", clean(text).lower().replace("-", " "))
    found: set[float] = set()
    i = 0
    while i < len(words):
        w = words[i]
        if w not in _UNITS and w not in _TENS:
            i += 1
            continue
        total, current = 0, 0
        while i < len(words):
            w = words[i]
            if w in _UNITS:
                current += _UNITS[w]
            elif w in _TENS:
                current += _TENS[w]
            elif w == "hundred":
                current = max(current, 1) * 100
            elif w in ("thousand", "million"):
                total += max(current, 1) * _SCALES[w]
                current = 0
            elif (
                w == "and"
                and i + 1 < len(words)
                and (words[i + 1] in _UNITS or words[i + 1] in _TENS)
            ):
                pass
            else:
                break
            i += 1
        value: float = total + current
        if i + 1 < len(words) and words[i] == "point" and words[i + 1] in _UNITS:
            value += _UNITS[words[i + 1]] / 10
            i += 2
        found.add(float(value))
    return found


def numbers_in(text: str, *, words: bool = True) -> set[float]:
    """Every number `text` states: digits ("1,200", "4.5", "45%") and, unless `words` is
    False, numbers in words. "CO2" or "x3" is not a number (a letter is glued to it)."""
    out: set[float] = set()
    for whole, frac in _NUMBER.findall(clean(text)):
        out.add(float(whole.replace(",", "") + (frac or "")))
    if words:
        out |= _word_numbers(text)
    return out


def same_number(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9)


def states_number(text: str, value: float) -> bool:
    return any(same_number(value, n) for n in numbers_in(text))


def fmt_value(v: float) -> str:
    """How a chart prints a value: an integer without a decimal point, else one decimal."""
    return str(int(round(v))) if float(v).is_integer() else f"{v:.1f}"
