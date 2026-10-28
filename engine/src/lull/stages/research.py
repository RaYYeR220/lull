"""Research stage: find proper nouns / technical terms and confirm canonical spelling via Tavily.

Each step that worked can be kept by the caller (`memo`): the term list, the source page and
every successful lookup, so a run that stopped on a failed lookup tries only that one again.
"""

from __future__ import annotations

import re
from importlib.resources import files
from typing import Literal

from pydantic import BaseModel, Field

from lull.ledger import BudgetExceeded, SearchCapReached
from lull.store import Memo, fresh
from lull.tavily import Tavily, TavilyError
from lull.tf import LIGHTNING, TokenFactory
from lull.types import RunOptions, VisualEvent, Word

TRANSCRIPT_CHARS = 6000
CONTEXT_CHARS = 1500
NOTE_CHARS = 200
MAX_SOURCES = 3
SEARCHED_KINDS = {"person", "place", "organization", "work"}  # looked up when said or shown
ON_SCREEN_KINDS = {"technical"}  # looked up only when the screen shows them
LOOKUP_FAILED = "lookup failed"
LOOKUP_CAPPED = "lookup skipped: search cap"
Kind = Literal["person", "place", "organization", "work", "technical", "other"]


TERMS_SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["person", "place", "organization", "work", "technical", "other"],
                    },
                    "why": {"type": "string"},
                },
                "required": ["term", "kind", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["terms"],
    "additionalProperties": False,
}


class GlossaryEntry(BaseModel):
    term: str
    canonical: str
    note: str
    sources: list[str] = Field(default_factory=list)
    kind: Kind = "other"  # what the term extraction said it is


class ResearchResult(BaseModel):
    glossary: list[GlossaryEntry]
    skipped: bool  # True when no Tavily client was available: nothing was looked up
    source_context: str | None = None
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # what limited it, not errors ("capped")


def _system_prompt() -> str:
    return (files("lull") / "prompts" / "research.md").read_text(encoding="utf-8")


def _material(events: list[VisualEvent], words: list[Word], context: str | None) -> str:
    nl = "\n"
    transcript = " ".join(w.text for w in words)[:TRANSCRIPT_CHARS]
    verbatim = nl.join(e.verbatim_text for e in events if e.verbatim_text)
    described = nl.join(e.text for e in events)
    parts = [
        f"<transcript>{nl}{transcript}{nl}</transcript>",
        f"<on_screen_text>{nl}{verbatim}{nl}</on_screen_text>",
        f"<visual_events>{nl}{described}{nl}</visual_events>",
    ]
    if context:
        parts.append(f"<source_page>{nl}{context}{nl}</source_page>")
    return (nl + nl).join(parts)


def _extract_terms(
    events: list[VisualEvent], words: list[Word], context: str | None,
    tf: TokenFactory, run_id: str, max_terms: int,
) -> list[tuple[str, str]]:  # fmt: skip
    """(term, kind) pairs worth a lookup: a person, place, organization or work said or shown,
    or a technical term the screen shows (a spoken one is usually a common word)."""
    user = f"Return at most {max_terms} terms.\n\n{_material(events, words, context)}"
    out = tf.json(
        LIGHTNING,
        [{"role": "system", "content": _system_prompt()}, {"role": "user", "content": user}],
        TERMS_SCHEMA,
        run_id=run_id, stage="research", role="research",
        thinking=False, max_tokens=1500, temperature=0.2, estimate=0.002,
    )  # fmt: skip
    shown = [e.verbatim_text for e in events if e.verbatim_text]
    said_or_shown = [" ".join(w.text for w in words), *shown]
    seen: set[str] = set()
    terms: list[tuple[str, str]] = []
    for item in out.get("terms") or []:
        term, kind = str(item.get("term", "")).strip(), item.get("kind")
        if not term or term.lower() in seen:
            continue
        if kind in SEARCHED_KINDS:
            evidence = said_or_shown
        elif kind in ON_SCREEN_KINDS:
            evidence = shown
        else:
            continue
        pat = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
        if any(pat.search(t) for t in evidence):
            seen.add(term.lower())
            terms.append((term, kind))
    return terms[:max_terms]


def _canonical(term: str, results: list[dict]) -> str:
    pat = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
    for r in results[:MAX_SOURCES]:
        for text in (r.get("title") or "", r.get("content") or ""):
            m = pat.search(text)
            if m:
                return m.group(0)
    return term


def _entry(term: str, kind: str, results: list[dict]) -> GlossaryEntry:
    note = ((results[0].get("content") or "")[:NOTE_CHARS]) if results else ""
    sources = [r["url"] for r in results if r.get("url")][:MAX_SOURCES]
    return GlossaryEntry(
        term=term, canonical=_canonical(term, results), note=note, sources=sources, kind=kind
    )


def research(
    events: list[VisualEvent],
    words: list[Word],
    opts: RunOptions,
    tf: TokenFactory,
    tavily: Tavily | None,
    *,
    run_id: str,
    max_terms: int = 5,
    memo: Memo = fresh,
) -> ResearchResult:
    """The glossary: terms Lightning picks out (see _extract_terms), each looked up with
    Tavily when there is a client. A failed lookup or source extract is recorded in `errors`
    and degrades the entry; a spend cap still stops the stage. `memo` keeps the term list, the
    source page and each successful lookup (steps "terms", "source" and "lookup")."""
    errors: list[str] = []
    context: str | None = None
    if tavily is not None and opts.source_url:
        url = opts.source_url
        try:
            pages = memo("source", {"url": url}, lambda: tavily.extract([url], run_id=run_id))
        except SearchCapReached:
            pages = []
        except TavilyError as e:
            errors.append(f"source extract failed: {e}")
            pages = []
        raw = ((pages[0].get("raw_content") or "") if pages else "").strip()
        if raw:
            context = raw[:CONTEXT_CHARS]

    if not words and not events and not context:
        return ResearchResult(glossary=[], skipped=tavily is None, errors=errors)

    inputs = {"events": events, "words": words, "context": context, "max_terms": max_terms}

    def extract() -> list[list[str]]:
        return [list(p) for p in _extract_terms(events, words, context, tf, run_id, max_terms)]

    terms = [(str(t), str(k)) for t, k in memo("terms", inputs, extract)]
    if tavily is None:
        entries = [GlossaryEntry(term=t, canonical=t, note="", kind=k) for t, k in terms]
        return ResearchResult(glossary=entries, skipped=True)

    entries = []
    capped: SearchCapReached | None = None
    for t, k in terms:
        if capped is not None:
            entries.append(GlossaryEntry(term=t, canonical=t, note=LOOKUP_CAPPED, kind=k))
            continue
        try:
            found = memo("lookup", {"term": t}, lambda t=t: tavily.search(t, run_id=run_id))
            entries.append(_entry(t, k, found))
        except BudgetExceeded:
            raise
        except SearchCapReached as e:
            capped = e
            entries.append(GlossaryEntry(term=t, canonical=t, note=LOOKUP_CAPPED, kind=k))
        except TavilyError as e:
            errors.append(f"search failed for {t!r}: {e}")
            entries.append(GlossaryEntry(term=t, canonical=t, note=LOOKUP_FAILED, kind=k))
    notes = [f"research capped: {capped.done} searches"] if capped else []
    return ResearchResult(
        glossary=entries, skipped=False, source_context=context, errors=errors, notes=notes
    )
