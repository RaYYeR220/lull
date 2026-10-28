"""Suite A lecture specs: Nemotron 3.5 Lightning writes twelve short lectures (slides, charts,
decoys, then narration) whose narration leaves visual facts unsaid; fixed rules
then derive the answer key's facts from what is on each slide and what the narration says.

    uv run python -m suite_a.specs            # generate every missing spec
    uv run python -m suite_a.specs --only a03 # one subject (an existing spec is kept)
    uv run python -m suite_a.specs --check    # re-validate and re-derive the committed specs

Two calls per lecture, both thinking off, temperature 0.7, strict JSON schema, fixed seeds:

1. slides: title, bullets, the two charts (type and slide fixed per subject in SUBJECTS) and
   four absent decoys; seed = subject seed + 1000 * (attempt - 1), up to SLIDE_ATTEMPTS.
2. narration: per slide 2-4 sentences, told which bullet to say (bullet ((k - 1) mod n) on
   slide k) and which words and numbers never to say; seed = the slides seed + 100 *
   attempt, up to NARRATION_ATTEMPTS per accepted slide set.

The pause before each sentence (1.5-4.0 s, to 0.1 s) is not the model's: `plan_pauses` draws
it from random.Random(subject seed), so the room a describer gets is spread evenly over the
range instead of following one model's habit.

A rejected answer is logged with the validator's reasons and the next attempt is told them.
The provider does not promise the same answer for the same seed (two Lightning calls with one
seed differed in our test), so the committed specs, not a regeneration, are the reproducible
artefact; `generation_log.json` lists every attempt, accepted or not.

The facts are not the model's opinion of what it wrote: `derive_facts` reads them off the
slides by rule (see its docstring), using word overlap and the numbers the narration states
in digits or in words, so anyone can re-derive them from the spec (`--check` does).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from suite_a.style import y_ticks
from suite_a.text import STOP, contains_phrase, content_words, fmt_value, numbers_in, overlap

HERE = Path(__file__).resolve().parent
SPECS_DIR = HERE / "specs"
LOG_PATH = SPECS_DIR / "generation_log.json"
RUN_ID = "suite-a-specs"  # ledger run id for generation

TEMPERATURE = 0.7
SLIDE_ATTEMPTS = 6
NARRATION_ATTEMPTS = 4
MAX_TOKENS = 4000


@dataclass(frozen=True)
class Subject:
    id: str
    subject: str
    seed: int
    slides: int
    charts: dict[int, str]  # slide number (1-based) -> "bar" | "line"


SUBJECTS: tuple[Subject, ...] = (
    Subject("a01", "biology", 1101, 4, {2: "bar", 4: "line"}),
    Subject("a02", "economics", 1102, 3, {1: "line", 3: "bar"}),
    Subject("a03", "physics", 1103, 4, {2: "line", 3: "bar"}),
    Subject("a04", "history", 1104, 3, {2: "bar", 3: "line"}),
    Subject("a05", "statistics", 1105, 4, {1: "bar", 3: "line"}),
    Subject("a06", "chemistry", 1106, 3, {1: "bar", 2: "line"}),
    Subject("a07", "geography", 1107, 4, {3: "bar", 4: "line"}),
    Subject("a08", "computer science", 1108, 3, {2: "line", 3: "bar"}),
    Subject("a09", "art history", 1109, 4, {1: "line", 4: "bar"}),
    Subject("a10", "medicine", 1110, 3, {1: "bar", 3: "line"}),
    Subject("a11", "climate", 1111, 4, {2: "line", 4: "bar"}),
    Subject("a12", "engineering", 1112, 3, {2: "bar", 3: "line"}),
)

KINDS = ("title", "bullet", "chart_value", "chart_trend", "label")

# limits the validator enforces (and the prompts state)
BULLETS = (2, 4)
SENTENCES = (2, 4)
POINTS = (3, 6)
PAUSE_S = (1.5, 4.0)
DECOY_KINDS = ("slide_title", "chart_value", "image", "bullet")
PROMPT_EXAMPLES = ("Fermentation", "mitochondrion")  # the decoy examples in SLIDES_PROMPT
MIN_VISUAL_PER_SLIDE = 2
MAX_TITLE, MAX_BULLET, MAX_CHART_TEXT, MAX_POINT_LABEL = 48, 60, 40, 20
SENTENCE_WORDS = (4, 30)
UNSAID = 0.5  # on-slide text sharing less than this of its content words with the narration
SAID = 0.6  # ... at least this much: the narration says it (in between: neither)
DECOY_NEW = 0.6  # a decoy shares less than this with any slide's text and with the narration

# direction words: a word starting with one of the prefixes, or equal to one of the words
RISE = (("rise", "rising", "rose", "risen", "increas", "grow", "grew", "climb", "higher",
         "doubl", "tripl", "upward", "surg", "soar", "gain"), ("up",))  # fmt: skip
FALL = (("fall", "fell", "declin", "decreas", "drop", "shrink", "shrank", "lower", "halv",
         "downward", "plung", "slump", "reduc"), ("down",))  # fmt: skip
DIRECTION_WORDS = ("rise, rising, rose, increase, grow, grew, growth, climb, higher, double, "
                   "triple, upward, surge, soar, gain, up, fall, fell, decline, decrease, drop, "
                   "shrink, lower, halve, downward, plunge, slump, reduce, down")  # fmt: skip

# -- the schemas --------------------------------------------------------------------------------

_STR = {"type": "string"}


def _obj(props: dict) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def _arr(items: dict, lo: int, hi: int) -> dict:
    return {"type": "array", "items": items, "minItems": lo, "maxItems": hi}


def _text(limit: int) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": limit}


def slides_schema(subject: Subject) -> dict:
    """The slides answer for `subject`, its structure enforced by the schema: one key per
    slide, a chart of the fixed type exactly on the chart slides, the item counts."""
    point = _obj({
        "label": _text(MAX_POINT_LABEL),
        "value": {"type": "number", "minimum": 0.1, "maximum": 100_000},
    })  # fmt: skip
    slides = {}
    for k in range(1, subject.slides + 1):
        props = {"title": _text(MAX_TITLE), "bullets": _arr(_text(MAX_BULLET), *BULLETS)}
        if k in subject.charts:
            props["chart"] = _obj({
                "type": {"type": "string", "enum": [subject.charts[k]]},
                "title": _text(MAX_CHART_TEXT), "x_label": _text(MAX_CHART_TEXT),
                "y_label": _text(MAX_CHART_TEXT), "points": _arr(point, *POINTS),
            })  # fmt: skip
        slides[f"slide_{k}"] = _obj(props)
    return _obj({
        "title": _text(80),
        "slides": _obj(slides),
        "absent_decoys": _arr(
            _obj({"kind": {"type": "string", "enum": list(DECOY_KINDS)}, "text": _text(160)}),
            len(DECOY_KINDS), len(DECOY_KINDS),
        ),
    })  # fmt: skip


def narration_schema(n_slides: int) -> dict:
    """The narration answer: per slide 2-4 sentences."""
    return _obj({
        "slides": _obj({f"slide_{k}": _obj({"sentences": _arr(_text(220), *SENTENCES)})
                        for k in range(1, n_slides + 1)}),
    })  # fmt: skip


def slides_from_answer(answer: dict, subject: Subject) -> dict:
    """The slides answer in the spec's shape: a list of slides, `chart` None when absent."""
    return {
        "title": answer["title"],
        "slides": [
            {**{k: v for k, v in s.items() if k != "chart"}, "chart": s.get("chart")}
            for s in (answer["slides"][f"slide_{k}"] for k in range(1, subject.slides + 1))
        ],
        "absent_decoys": answer["absent_decoys"],
    }


def narration_from_answer(answer: dict, subject: Subject) -> dict:
    """The narration answer in the spec's shape, with the planned pauses."""
    said = [answer["slides"][f"slide_{k}"]["sentences"] for k in range(1, subject.slides + 1)]
    pauses = plan_pauses(subject, [len(x) for x in said])
    return {"slides": [{"narration": n, "pauses": p} for n, p in zip(said, pauses, strict=True)]}


def plan_pauses(subject: Subject, sentences: list[int]) -> list[list[float]]:
    """The silence before each sentence, per slide: uniform in PAUSE_S, to 0.1 s, drawn from
    random.Random(subject.seed) in slide and sentence order."""
    rng = random.Random(subject.seed)
    return [[round(rng.uniform(*PAUSE_S), 1) for _ in range(n)] for n in sentences]


# -- the prompts --------------------------------------------------------------------------------

SLIDES_PROMPT = """You write the slides of a short lecture for a synthetic test video.

Subject: {subject}. Write exactly {n} slides.
{chart_lines}

Return JSON with:
- `title`: the lecture title, at most 8 words.
- `slides`: one entry per slide, `slide_1` to `slide_{n}`. Each slide has:
  - `title`: at most 5 words and at most {max_title} characters.
  - `bullets`: 2 to 4 bullet points, each at most 8 words and at most {max_bullet} \
characters. Each bullet states a different idea, in its own words.
  - `chart`: on a chart slide, an object with `type` (exactly as listed above), `title` (at \
most 5 words), `x_label` and `y_label` (each at most 3 words), and `points`: 3 to 6 points in \
display order, each with a `label` (one or two words, or a year; at most {max_point} \
characters) and a `value` greater than zero with at most one decimal place. Use realistic, \
roughly correct values. Other slides have no chart.
- `absent_decoys`: 4 plausible things someone describing this video might wrongly claim to \
see, none of which is on any of your slides, one of each `kind`: `slide_title` (a slide \
title on a related topic that none of your slides has), `chart_value` (a value for a \
category that is in no chart, with a value that is not a round number, for example "The \
bar chart shows Fermentation at 4.3."), \
`image` (a picture, photo or diagram; your slides have none, for example "A diagram of a \
mitochondrion is shown."), `bullet` (a bullet point that is on no slide). Each `text` is one \
short sentence stating it as if it were on screen. Never reuse a number from your slides.

Content must be well-established, textbook-level and factually sound. No people's names, no \
brands, no quotations."""

NARRATION_PROMPT = """These are the slides of a short lecture for a synthetic test video:

{slides}

Write what the lecturer says while each slide is on screen. A blind student will listen, and \
the test checks that the lecturer leaves most of each slide UNSAID, so follow the rules for \
each slide exactly.

{rules}

For every slide write 2 to 4 `sentences`, each 8 to 25 words, in plain spoken English, \
explaining the idea like a lecturer, in natural, grammatical sentences (inflect the words you \
are given as each sentence needs). You may say "on this slide" or "this chart", but never \
describe what is shown.

Never say any number that is on a slide (in any form, digits or words), never say whether a \
chart goes up or down, and never use these words: {direction}.

Return JSON with `slides`: one entry per slide, `slide_1` to `slide_{n}`, each with its \
`sentences`."""


def prompt_for_slides(s: Subject, errors: list[str] | None = None) -> str:
    lines = [f"Slide {k} has a {t} chart." for k, t in sorted(s.charts.items())]
    lines.append("The other slides have no chart.")
    text = SLIDES_PROMPT.format(
        subject=s.subject, n=s.slides, chart_lines="\n".join(lines), max_title=MAX_TITLE,
        max_bullet=MAX_BULLET, max_point=MAX_POINT_LABEL,
    )  # fmt: skip
    return text + _rejected(errors)


def sayable(bullet: str) -> bool:
    """A bullet the narration can say: no number (the narration never states one) and no
    word of direction (the narration never uses one)."""
    return (
        not numbers_in(bullet)
        and not says_direction(bullet, RISE)
        and not says_direction(bullet, FALL)
    )


def spoken_bullet(k: int, slide: dict) -> str | None:
    """The bullet the narration of slide k (1-based) is asked to say: the first sayable one
    from bullet ((k - 1) mod n) on, wrapping around; None when no bullet is sayable."""
    bullets = [b.strip() for b in slide["bullets"]]
    n = len(bullets)
    for i in range(n):
        b = bullets[(k - 1 + i) % n]
        if sayable(b):
            return b
    return None


def _forbidden(slide: dict, keep: str) -> list[str]:
    """Words of the slide the narration must not use: every non-stop word of its title, other
    bullets, chart title, axis labels and point labels that the spoken bullet does not use."""
    allowed = {w.lower() for w in re.findall(r"[A-Za-z]+", keep)}
    texts = [slide["title"], *[b for b in slide["bullets"] if b.strip() != keep]]
    chart = slide.get("chart")
    if chart:
        texts += [chart["title"], chart["x_label"], chart["y_label"]]
        texts += [p["label"] for p in chart["points"]]
    out: list[str] = []
    for t in texts:
        for w in re.findall(r"[A-Za-z]+", t):
            lw = w.lower()
            if lw not in STOP and lw not in allowed and len(lw) > 2 and lw not in out:
                out.append(lw)
    return out


def prompt_for_narration(content: dict, errors: list[str] | None = None) -> str:
    shown = {k: s for k, s in enumerate(content["slides"], 1)}
    rules = []
    for k, s in shown.items():
        keep = spoken_bullet(k, s) or ""
        words = list(dict.fromkeys(
            w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", keep) if w.lower() not in STOP
        ))  # fmt: skip
        numbers = ""
        if s.get("chart"):
            numbers = (
                " Never say these numbers: "
                + ", ".join(fmt_value(p["value"]) for p in s["chart"]["points"])
                + "."
            )
        rules.append(
            f'Slide {k}: say the idea of this bullet: "{keep}", using these words from it: '
            f"{', '.join(words)}. Do not read or paraphrase anything else on the slide. "
            f"Never use these words: "
            f"{', '.join(_forbidden(s, keep)) or '(none)'}.{numbers}"
        )
    text = NARRATION_PROMPT.format(
        slides=json.dumps({"title": content["title"], "slides": content["slides"]}, indent=1,
                          ensure_ascii=False),
        rules="\n".join(rules), direction=DIRECTION_WORDS, n=len(content["slides"]),
    )  # fmt: skip
    return text + _rejected(errors)


def _rejected(errors: list[str] | None) -> str:
    if not errors:
        return ""
    return "\n\nA previous attempt was rejected for these reasons; avoid them:\n" + "\n".join(
        f"- {e}" for e in errors[:12]
    )


# -- facts, by rule -----------------------------------------------------------------------------


def says_direction(text: str, family: tuple[tuple[str, ...], tuple[str, ...]]) -> bool:
    prefixes, exact = family
    return any(w.startswith(prefixes) or w in exact for w in re.findall(r"[a-z]+", text.lower()))


def slide_text(slide: dict) -> str:
    """Every piece of text on the slide except tick numbers and the slide number."""
    parts = [slide["title"], *slide["bullets"]]
    chart = slide.get("chart")
    if chart:
        parts += [chart["title"], chart["x_label"], chart["y_label"]]
        parts += [p["label"] for p in chart["points"]]
        parts += [fmt_value(p["value"]) for p in chart["points"]]
    return " ".join(parts)


def slide_numbers(slide: dict) -> set[float]:
    found = numbers_in(slide_text(slide))
    chart = slide.get("chart")
    if chart:
        values = [float(p["value"]) for p in chart["points"]]
        found |= set(values) | set(y_ticks(values)[1])
    return found


def _said(text: str, narration: str, everywhere: str) -> str:
    """'said', 'unsaid' or 'partly' for on-slide text, against the slide's narration (word
    overlap) and the whole narration (the text as a phrase)."""
    share = overlap(text, narration)
    if share >= SAID or contains_phrase(everywhere, text):
        return "said"
    return "unsaid" if share < UNSAID else "partly"


def derive_facts(slides: list[dict]) -> dict:
    """The key's facts, read off the slides by rule:

    - title, each bullet, the chart title and each axis label is a visual fact (kind title,
      bullet, label) when the slide's narration shares less than UNSAID of its content words
      and the whole narration never says it as a phrase; a spoken fact when the narration
      shares at least SAID or says it as a phrase; neither in between ("partly said").
    - every chart gives a chart_value fact for its highest value (the first, on a tie); a
      line chart (its points are in order) also gives a chart_trend fact for the direction
      from the first point to the last when they differ and the slide's narration uses no
      word of that direction. A bar chart's categories need not be in any order, so it has
      no trend. (The validator has already refused any narration that states a number from
      a chart.)
    """
    visual: list[dict] = []
    spoken: list[dict] = []
    partly: list[dict] = []
    everywhere = " ".join(" ".join(s["narration"]) for s in slides)
    for k, s in enumerate(slides, 1):
        said = " ".join(s["narration"])
        items = [("title", s["title"], f'The slide title reads "{s["title"]}".')]
        items += [("bullet", b, f'A bullet reads "{b}".') for b in s["bullets"]]
        chart = s.get("chart")
        if chart:
            kind = f"{chart['type']} chart"
            items += [
                ("label", chart["title"], f'A {kind} is titled "{chart["title"]}".'),
                ("label", chart["x_label"],
                 f'The {kind}\'s horizontal axis is labelled "{chart["x_label"]}".'),
                ("label", chart["y_label"],
                 f'The {kind}\'s vertical axis is labelled "{chart["y_label"]}".'),
            ]  # fmt: skip
        for kind, anchor, text in items:
            verdict = _said(anchor, said, everywhere)
            entry = {"slide": k, "kind": kind, "text": text, "anchor": anchor}
            if verdict == "unsaid":
                visual.append({**entry, "value": None})
            elif verdict == "said":
                spoken.append(entry)
            else:
                partly.append(entry)
        if not chart:
            continue
        pts = chart["points"]
        top = max(pts, key=lambda p: p["value"])
        visual.append({
            "slide": k, "kind": "chart_value", "anchor": top["label"], "value": top["value"],
            "text": f'The highest value in the {chart["type"]} chart "{chart["title"]}" is '
                    f'{fmt_value(top["value"])}, for "{top["label"]}".',
        })  # fmt: skip
        first, last = pts[0], pts[-1]
        if chart["type"] != "line" or last["value"] == first["value"]:
            continue
        up = last["value"] > first["value"]
        if says_direction(said, RISE if up else FALL):
            continue
        steady = all(
            (b["value"] > a["value"]) if up else (b["value"] < a["value"])
            for a, b in zip(pts, pts[1:], strict=False)
        )
        if steady:
            how = "rise at every step" if up else "fall at every step"
        else:
            how = "end higher than they start" if up else "end lower than they start"
        visual.append({
            "slide": k, "kind": "chart_trend", "anchor": "", "value": None,
            "text": f'In the {chart["type"]} chart "{chart["title"]}" the values {how}, '
                    f'from "{first["label"]}" to "{last["label"]}".',
        })  # fmt: skip
    return {"visual_facts": visual, "spoken_facts": spoken, "partly_said": partly}


# -- the validators -----------------------------------------------------------------------------


def validate_slides(content: dict, subject: Subject) -> list[str]:
    """Every reason the slides answer is unusable for `subject` (empty when usable)."""
    errs: list[str] = []
    slides = content.get("slides") or []
    if len(slides) != subject.slides:
        return [f"needs exactly {subject.slides} slides, got {len(slides)}"]
    if not str(content.get("title", "")).strip():
        errs.append("the lecture has no title")
    for k, s in enumerate(slides, 1):
        where = f"slide {k}"
        if spoken_bullet(k, s) is None:
            errs.append(f"{where}: needs a bullet with no number and no word like rise or fall")
        title = s["title"].strip()
        if not title or len(title) > MAX_TITLE:
            errs.append(f"{where}: the title must be 1-{MAX_TITLE} characters")
        if not BULLETS[0] <= len(s["bullets"]) <= BULLETS[1]:
            errs.append(f"{where}: needs {BULLETS[0]}-{BULLETS[1]} bullets")
        for b in s["bullets"]:
            if not b.strip() or len(b) > MAX_BULLET:
                errs.append(f"{where}: a bullet is longer than {MAX_BULLET} characters: {b!r}")
        if len({b.strip() for b in s["bullets"]}) != len(s["bullets"]):
            errs.append(f"{where}: a bullet is repeated")
        want, chart = subject.charts.get(k), s.get("chart")
        if want is None:
            if chart is not None:
                errs.append(f"{where}: must have no chart")
            continue
        if chart is None:
            errs.append(f"{where}: must have a {want} chart")
            continue
        if chart["type"] != want:
            errs.append(f"{where}: the chart must be a {want} chart")
        for field in ("title", "x_label", "y_label"):
            if not chart[field].strip() or len(chart[field]) > MAX_CHART_TEXT:
                errs.append(f"{where}: chart {field} must be 1-{MAX_CHART_TEXT} characters")
        pts = chart["points"]
        if not POINTS[0] <= len(pts) <= POINTS[1]:
            errs.append(f"{where}: the chart needs {POINTS[0]}-{POINTS[1]} points")
        labels = [p["label"].strip() for p in pts]
        if len(set(labels)) != len(labels):
            errs.append(f"{where}: a chart label is repeated")
        for p in pts:
            v = p["value"]
            if not p["label"].strip() or len(p["label"]) > MAX_POINT_LABEL:
                errs.append(f"{where}: chart label {p['label']!r} is over {MAX_POINT_LABEL} chars")
            if not (math.isfinite(v) and 0 < v <= 100_000 and round(v, 1) == v):
                errs.append(f"{where}: chart value {v} must be > 0 with one decimal at most")
    if errs:
        return errs
    decoys = content["absent_decoys"]
    if sorted(d["kind"] for d in decoys) != sorted(DECOY_KINDS):
        errs.append(f"needs one absent decoy of each kind: {', '.join(DECOY_KINDS)}")
    shown = set().union(*(slide_numbers(s) for s in slides))
    for d in decoys:
        where = f"decoy {d['text']!r}"
        if not content_words(d["text"]):
            errs.append(f"{where}: is empty")
            continue
        found = numbers_in(d["text"], words=False)
        hit = sorted(n for n in found if n in shown)
        if hit:
            errs.append(f"{where}: the number {hit[0]:g} is on a slide")
        if d["kind"] == "chart_value" and not found:
            errs.append(f"{where}: a chart_value decoy states a value in digits")
        if any(contains_phrase(d["text"], x) for x in PROMPT_EXAMPLES):
            errs.append(f"{where}: copies the example from the instructions")
        if any(overlap(d["text"], slide_text(s)) >= DECOY_NEW for s in slides):
            errs.append(f"{where}: is too close to what a slide shows")
    return errs


def validate_narration(content: dict, narration: dict) -> list[str]:
    """Every reason the narration answer is unusable for the slides in `content`."""
    slides = content["slides"]
    got = narration.get("slides") or []
    if len(got) != len(slides):
        return [f"needs narration for exactly {len(slides)} slides, got {len(got)}"]
    errs: list[str] = []
    merged = [{**s, "narration": n["narration"], "pauses": n["pauses"]}
              for s, n in zip(slides, got, strict=True)]  # fmt: skip
    narration_all = " ".join(" ".join(s["narration"]) for s in merged)
    said_numbers = numbers_in(narration_all)
    for k, s in enumerate(merged, 1):
        where = f"slide {k}"
        if not SENTENCES[0] <= len(s["narration"]) <= SENTENCES[1]:
            errs.append(f"{where}: needs {SENTENCES[0]}-{SENTENCES[1]} narration sentences")
        for sent in s["narration"]:
            n = len(sent.split())
            if not SENTENCE_WORDS[0] <= n <= SENTENCE_WORDS[1]:
                errs.append(f"{where}: a narration sentence has {n} words: {sent!r}")
        if len(s["pauses"]) != len(s["narration"]):
            errs.append(f"{where}: needs one pause per narration sentence")
        for p in s["pauses"]:
            if not (math.isfinite(p) and PAUSE_S[0] <= p <= PAUSE_S[1]):
                errs.append(f"{where}: pause {p} is outside {PAUSE_S[0]}-{PAUSE_S[1]} s")
        for p in (s.get("chart") or {}).get("points", []):
            if float(p["value"]) in said_numbers:
                errs.append(f"{where}: the narration says the chart value {fmt_value(p['value'])}")
    if errs:
        return errs
    facts = derive_facts(merged)
    for k, s in enumerate(merged, 1):
        n_visual = sum(f["slide"] == k for f in facts["visual_facts"])
        if n_visual < MIN_VISUAL_PER_SLIDE:
            errs.append(f"slide {k}: the narration leaves fewer than {MIN_VISUAL_PER_SLIDE} "
                        "things on the slide unsaid")  # fmt: skip
        keep = spoken_bullet(k, s)
        if not any(f["slide"] == k and f["anchor"] == keep for f in facts["spoken_facts"]):
            errs.append(f'slide {k}: the narration must say "{keep}" using its key words')
    for d in content["absent_decoys"]:
        if overlap(d["text"], narration_all) >= DECOY_NEW:
            errs.append(f"the narration is too close to this fact, never say it: {d['text']!r}")
        hit = sorted(n for n in numbers_in(d["text"], words=False) if n in said_numbers)
        if hit:
            errs.append(f"the narration says the number {hit[0]:g}; never say it")
    return errs


# -- the spec on disk ---------------------------------------------------------------------------


def finish(content: dict, narration: dict, subject: Subject, made: dict) -> dict:
    """The accepted answers, tidied, with the derived facts (ids assigned in order)."""
    vid = subject.id
    slides = []
    for k, (s, n) in enumerate(zip(content["slides"], narration["slides"], strict=True), 1):
        chart = s["chart"]
        slides.append({
            "index": k,
            "title": s["title"].strip(),
            "bullets": [b.strip() for b in s["bullets"]],
            "chart": None if chart is None else {
                "type": chart["type"],
                "title": chart["title"].strip(),
                "x_label": chart["x_label"].strip(),
                "y_label": chart["y_label"].strip(),
                "points": [{"label": p["label"].strip(), "value": p["value"]}
                           for p in chart["points"]],
            },
            "narration": [t.strip() for t in n["narration"]],
            "pauses": [round(float(p), 2) for p in n["pauses"]],
        })  # fmt: skip
    return {
        "id": vid,
        "subject": subject.subject,
        "title": content["title"].strip(),
        "slides": slides,
        **with_ids(vid, derive_facts(slides)),
        "absent_decoys": [
            {"id": f"{vid}-d{i:02d}", "kind": d["kind"], "text": d["text"].strip()}
            for i, d in enumerate(content["absent_decoys"], 1)
        ],
        "generator": made,
    }


def with_ids(vid: str, facts: dict) -> dict:
    return {
        "visual_facts": [
            {"id": f"{vid}-v{i:02d}", **f} for i, f in enumerate(facts["visual_facts"], 1)
        ],
        "spoken_facts": [
            {"id": f"{vid}-s{i:02d}", **f} for i, f in enumerate(facts["spoken_facts"], 1)
        ],
        "partly_said": facts["partly_said"],
    }


def spec_path(vid: str) -> Path:
    return SPECS_DIR / f"{vid}.json"


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def load_spec(vid: str) -> dict:
    return json.loads(spec_path(vid).read_text(encoding="utf-8"))


def subject_of(vid: str) -> Subject:
    return next(s for s in SUBJECTS if s.id == vid)


def check_committed(spec: dict) -> list[str]:
    """A committed spec passes both validators again and its facts re-derive exactly."""
    subject = subject_of(spec["id"])
    content = {
        "title": spec["title"],
        "slides": [{k: s[k] for k in ("title", "bullets", "chart")} for s in spec["slides"]],
        "absent_decoys": spec["absent_decoys"],
    }
    narration = {"slides": [{k: s[k] for k in ("narration", "pauses")} for s in spec["slides"]]}
    errs = validate_slides(content, subject) or validate_narration(content, narration)
    planned = plan_pauses(subject, [len(s["narration"]) for s in spec["slides"]])
    if [s["pauses"] for s in spec["slides"]] != planned:
        errs.append("the pauses are not the planned ones")
    again = with_ids(spec["id"], derive_facts(spec["slides"]))
    for name in ("visual_facts", "spoken_facts", "partly_said"):
        if again[name] != spec[name]:
            errs.append(f"{name} do not re-derive from the slides")
    return errs


def _ask(tf, prompt: str, schema: dict, seed: int, stage: str) -> dict:
    from lull.tf import LIGHTNING

    return tf.json(
        LIGHTNING, [{"role": "user", "content": prompt}], schema,
        run_id=RUN_ID, stage=stage, role="eval-spec", thinking=False,
        max_tokens=MAX_TOKENS, temperature=TEMPERATURE, estimate=0.005, seed=seed,
    )  # fmt: skip


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate(subject: Subject, tf, log: list[dict]) -> dict | None:
    """Slides, then narration for them, each retried with the validator's reasons; the first
    pair that validates becomes the spec, or None."""
    from lull.tf import LIGHTNING, TFError

    slide_errors: list[str] | None = None
    for attempt in range(1, SLIDE_ATTEMPTS + 1):
        seed = subject.seed + 1000 * (attempt - 1)
        prompt = prompt_for_slides(subject, slide_errors)
        try:
            answer = _ask(tf, prompt, slides_schema(subject), seed, "suite_a_slides")
            content = slides_from_answer(answer, subject)
        except TFError as e:
            log.append({"step": "slides", "attempt": attempt, "seed": seed, "ok": False,
                        "errors": [str(e)]})  # fmt: skip
            slide_errors = None
            continue
        slide_errors = validate_slides(content, subject)
        log.append({"step": "slides", "attempt": attempt, "seed": seed,
                    "ok": not slide_errors, "errors": slide_errors})  # fmt: skip
        _say(subject.id, "slides", attempt, seed, slide_errors)
        if slide_errors:
            continue
        narration_errors: list[str] | None = None
        for n_attempt in range(1, NARRATION_ATTEMPTS + 1):
            n_seed = seed + 100 * n_attempt
            n_prompt = prompt_for_narration(content, narration_errors)
            try:
                answer = _ask(
                    tf, n_prompt, narration_schema(subject.slides), n_seed, "suite_a_narration"
                )
                narration = narration_from_answer(answer, subject)
            except TFError as e:
                log.append({"step": "narration", "attempt": n_attempt, "seed": n_seed,
                            "ok": False, "errors": [str(e)]})  # fmt: skip
                narration_errors = None
                continue
            narration_errors = validate_narration(content, narration)
            log.append({"step": "narration", "attempt": n_attempt, "seed": n_seed,
                        "ok": not narration_errors, "errors": narration_errors})  # fmt: skip
            _say(subject.id, "narration", n_attempt, n_seed, narration_errors)
            if not narration_errors:
                made = {
                    "model": LIGHTNING, "temperature": TEMPERATURE, "thinking": False,
                    "slides": {"seed": seed, "attempt": attempt, "prompt_sha256": _sha(prompt)},
                    "narration": {"seed": n_seed, "attempt": n_attempt,
                                  "prompt_sha256": _sha(n_prompt)},
                }  # fmt: skip
                return finish(content, narration, subject, made)
        slide_errors = None  # these slides never got a usable narration: new slides
    return None


def _say(vid: str, step: str, attempt: int, seed: int, errors: list[str]) -> None:
    status = "ok" if not errors else f"{len(errors)} problems"
    print(f"{vid} {step} attempt {attempt} seed {seed}: {status}", flush=True)
    for e in errors[:6]:
        print(f"    {e}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma-separated subject ids")
    ap.add_argument("--check", action="store_true", help="validate the committed specs only")
    ap.add_argument(
        "--rederive", action="store_true", help="rewrite the facts from the slides (no model call)"
    )
    args = ap.parse_args(argv)
    chosen = [s for s in SUBJECTS if not args.only or s.id in args.only.split(",")]
    if args.rederive:
        for s in chosen:
            spec = load_spec(s.id)
            write_json(spec_path(s.id), {**spec, **with_ids(s.id, derive_facts(spec["slides"]))})
        args.check = True
    if args.check:
        bad = 0
        for s in chosen:
            errs = check_committed(load_spec(s.id))
            print(f"{s.id}: {'ok' if not errs else '; '.join(errs)}")
            bad += bool(errs)
        return 1 if bad else 0

    from lull.config import get_settings
    from lull.ledger import Ledger
    from lull.tf import TokenFactory

    ledger = Ledger(get_settings().lull_home / "ledger.db")
    log = json.loads(LOG_PATH.read_text(encoding="utf-8")) if LOG_PATH.exists() else {}
    failed = []
    with TokenFactory.from_settings(ledger) as tf:
        for s in chosen:
            if spec_path(s.id).exists():
                print(f"{s.id}: kept (already generated)")
                continue
            log[s.id] = []
            spec = generate(s, tf, log[s.id])
            write_json(LOG_PATH, log)
            if spec is None:
                failed.append(s.id)
                continue
            write_json(spec_path(s.id), spec)
    print(f"generation spend so far: ${ledger.spent(run_id=RUN_ID):.4f}")
    if failed:
        print(f"no valid spec: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
