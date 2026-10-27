"""Research stage: find proper nouns / technical terms and confirm canonical spelling via Tavily."""

from __future__ import annotations

import re
from importlib.resources import files

from pydantic import BaseModel, Field

from lull.ledger import BudgetExceeded
from lull.tavily import Tavily, TavilyError
from lull.tf import LIGHTNING, TokenFactory
from lull.types import RunOptions, VisualEvent, Word

TRANSCRIPT_CHARS = 6000
CONTEXT_CHARS = 1500
NOTE_CHARS = 200
MAX_SOURCES = 3
LOOKUP_FAILED = "lookup failed"

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


class ResearchResult(BaseModel):
    glossary: list[GlossaryEntry]
    skipped: bool  # True when no Tavily client was available: nothing was looked up
    source_context: str | None = None
    errors: list[str] = Field(default_factory=list)


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
) -> list[str]:  # fmt: skip
    user = f"Return at most {max_terms} terms.\n\n{_material(events, words, context)}"
    out = tf.json(
        LIGHTNING,
        [{"role": "system", "content": _system_prompt()}, {"role": "user", "content": user}],
        TERMS_SCHEMA,
        run_id=run_id, stage="research", role="research",
        thinking=False, max_tokens=1500, temperature=0.2, estimate=0.002,
    )  # fmt: skip
    seen: set[str] = set()
    terms: list[str] = []
    for item in out.get("terms") or []:
        term = str(item.get("term", "")).strip()
        if term and term.lower() not in seen:
            seen.add(term.lower())
            terms.append(term)
    return terms[:max_terms]


def _canonical(term: str, results: list[dict]) -> str:
    pat = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
    for r in results[:MAX_SOURCES]:
        for text in (r.get("title") or "", r.get("content") or ""):
            m = pat.search(text)
            if m:
                return m.group(0)
    return term


def _entry(term: str, results: list[dict]) -> GlossaryEntry:
    note = ((results[0].get("content") or "")[:NOTE_CHARS]) if results else ""
    sources = [r["url"] for r in results if r.get("url")][:MAX_SOURCES]
    return GlossaryEntry(term=term, canonical=_canonical(term, results), note=note, sources=sources)


def research(
    events: list[VisualEvent],
    words: list[Word],
    opts: RunOptions,
    tf: TokenFactory,
    tavily: Tavily | None,
    *,
    run_id: str,
    max_terms: int = 8,
) -> ResearchResult:
    errors: list[str] = []
    context: str | None = None
    if tavily is not None and opts.source_url:
        try:
            pages = tavily.extract([opts.source_url], run_id=run_id)
        except TavilyError as e:
            errors.append(f"source extract failed: {e}")
            pages = []
        raw = ((pages[0].get("raw_content") or "") if pages else "").strip()
        if raw:
            context = raw[:CONTEXT_CHARS]

    if not words and not events and not context:
        return ResearchResult(glossary=[], skipped=tavily is None, errors=errors)

    terms = _extract_terms(events, words, context, tf, run_id, max_terms)
    if tavily is None:
        entries = [GlossaryEntry(term=t, canonical=t, note="", sources=[]) for t in terms]
        return ResearchResult(glossary=entries, skipped=True)

    entries = []
    for t in terms:
        try:
            entries.append(_entry(t, tavily.search(t, run_id=run_id)))
        except BudgetExceeded:
            raise
        except TavilyError as e:
            errors.append(f"search failed for {t!r}: {e}")
            entries.append(GlossaryEntry(term=t, canonical=t, note=LOOKUP_FAILED, sources=[]))
    return ResearchResult(glossary=entries, skipped=False, source_context=context, errors=errors)
