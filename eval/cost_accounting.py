"""Cost accounting, a draft for comparison with the provider's billing: the model ledger's
totals per model and per UTC day, the price table the engine costs calls with (and where each
price comes from), the provider's published prices, and the ledger's own costs recomputed from
the tokens. Reads the ledger only (no model calls).

    uv run --directory eval python cost_accounting.py

writes results/cost_ledger.json; RESULTS.md shows it as "Cost accounting (draft)"."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from lull.ledger import PRICES, TAVILY_CREDIT_USD

from suite_a.score import RESULTS, SECOND_JUDGE, SECOND_JUDGE_PRICE

LEDGER = Path.home() / ".lull" / "ledger.db"
OUT_JSON = "cost_ledger.json"

ENGINE_SOURCE = "engine/src/lull/ledger.py `PRICES` (unchanged since the ledger's first commit)"
JUDGE_SOURCE = ("eval/suite_a/score.py `SECOND_JUDGE_PRICE` (Token Factory "
                "`/v1/models?verbose=true`, read 2026-10-28)")  # fmt: skip
NOTES_SOURCE = (
    "the project's research notes on models (2026-10-27), condensed from the Token Factory "
    "model catalog (tokenfactory.nebius.com/api/public/models_info)"
)
# USD per 1M tokens (input, cached input, output) as the research notes record the provider's
# published prices; None where they record no price (Lightning lists no cached rate).
PUBLISHED: dict[str, tuple[float, float | None, float]] = {
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 0.12, 3.00),
    "nvidia/Nemotron-3.5-Super-VL": (0.30, 0.06, 0.90),
    "nvidia/Nemotron-3_5-Lightning": (0.06, None, 0.24),
}


def engine_prices() -> dict[str, tuple[float, float, float]]:
    return {**PRICES, SECOND_JUDGE: PRICES.get(SECOND_JUDGE, SECOND_JUDGE_PRICE)}


def price_table() -> list[dict]:
    rows = []
    for model, p in engine_prices().items():
        pub = PUBLISHED.get(model)
        rows.append({
            "model": model, "engine": list(p),
            "engine_source": JUDGE_SOURCE if model == SECOND_JUDGE else ENGINE_SOURCE,
            "published": list(pub) if pub else None,
            "published_source": NOTES_SOURCE if pub else None,
        })  # fmt: skip
    return rows


def _iso(ts: float | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def _cost(model: str, tin: int, cached: int, tout: int) -> float | None:
    p = engine_prices().get(model)
    if p is None:
        return None
    return ((tin - cached) * p[0] + cached * p[1] + tout * p[2]) / 1_000_000


def snapshot(db_path: Path, *, now: str) -> dict:
    """Totals of the ledger at `path`, opened read-only (FileNotFoundError when missing)."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        n, first, last = db.execute("SELECT count(*), min(ts), max(ts) FROM calls").fetchone()
        q = ("SELECT {key} provider, model, count(*), sum(tokens_in), sum(tokens_cached), "
             "sum(tokens_out), sum(cost_usd) FROM calls GROUP BY {group} provider, model "
             "ORDER BY {group} provider, model")  # fmt: skip
        per_model = db.execute(q.format(key="", group="")).fetchall()
        per_day = db.execute(q.format(key="date(ts, 'unixepoch'),",
                                      group="date(ts, 'unixepoch'),")).fetchall()  # fmt: skip
    finally:
        db.close()

    def row(provider, model, calls, tin, cached, tout, cost) -> dict:
        r = {"provider": provider, "model": model, "calls": calls, "tokens_in": tin or 0,
             "tokens_cached": cached or 0, "tokens_out": tout or 0,
             "cost_ledger": round(cost or 0.0, 6)}  # fmt: skip
        if provider == "tavily":
            r["credits"] = round((cost or 0.0) / TAVILY_CREDIT_USD, 3)
            r["cost_recomputed"] = round(r["credits"] * TAVILY_CREDIT_USD, 6)
        else:
            c = _cost(model, r["tokens_in"], r["tokens_cached"], r["tokens_out"])
            r["cost_recomputed"] = None if c is None else round(c, 6)
        return r

    models = [row(*x) for x in per_model]
    totals: dict[str, dict] = {}
    for m in models:
        t = totals.setdefault(m["provider"], {"calls": 0, "tokens_in": 0, "tokens_cached": 0,
                                              "tokens_out": 0, "cost_ledger": 0.0})  # fmt: skip
        for k in ("calls", "tokens_in", "tokens_cached", "tokens_out", "cost_ledger"):
            t[k] += m[k]
    for t in totals.values():
        t["cost_ledger"] = round(t["cost_ledger"], 6)
    return {
        "as_of": now, "ledger": "the engine's model ledger", "calls": n,
        "first_call": _iso(first), "last_call": _iso(last),
        "models": models, "totals": totals,
        "by_day": [{"day": d, **row(*x)} for d, *x in per_day],
        "prices": price_table(),
    }  # fmt: skip


def _usd(x: float | None) -> str:
    return "n/a" if x is None else f"${x:.4f}"


def _p(p: list | None) -> str:
    if not p:
        return "n/a"
    return " / ".join("n/a" if x is None else f"{x:.2f}" for x in p)


def markdown(doc: dict) -> list[str]:
    tf = (doc.get("totals") or {}).get("tokenfactory") or {}
    lines = [
        "## Cost accounting (draft)", "",
        f"A draft for comparison with the provider's billing, not yet checked against it. "
        f"{doc['ledger'][:1].upper() + doc['ledger'][1:]} as of {doc['as_of']}: {doc['calls']} "
        f"calls from {doc.get('first_call') or 'n/a'} to {doc.get('last_call') or 'n/a'} (UTC), "
        "every run on the evaluation machine (engine development, the evaluation and its "
        "judges). `tokens in` is the "
        "whole prompt as the provider reported it (`usage.prompt_tokens`), cached part "
        "included; `tokens out` is `usage.completion_tokens`. Token Factory total in the "
        f"ledger: {_usd(tf.get('cost_ledger'))}.", "",
        "| provider | model | calls | tokens in | of which cached | tokens out | cost (ledger) "
        "| cost (recomputed) |",
        "|---|---|---|---|---|---|---|---|",
    ]  # fmt: skip
    for m in doc.get("models", []):
        name = m["model"] + (f" ({m['credits']:g} credits)" if "credits" in m else "")
        lines.append(f"| {m['provider']} | {name} | {m['calls']:,} | {m['tokens_in']:,} | "
                     f"{m['tokens_cached']:,} | {m['tokens_out']:,} | {_usd(m['cost_ledger'])} "
                     f"| {_usd(m.get('cost_recomputed'))} |")  # fmt: skip
    lines += ["", "Per UTC day (billing periods are usually days):", "",
              "| day | provider | model | calls | tokens in | tokens out | cost (ledger) |",
              "|---|---|---|---|---|---|---|"]  # fmt: skip
    for d in doc.get("by_day", []):
        lines.append(
            f"| {d['day']} | {d['provider']} | {d['model']} | {d['calls']:,} | "
            f"{d['tokens_in']:,} | {d['tokens_out']:,} | {_usd(d['cost_ledger'])} |"
        )
    lines += ["", "Prices, USD per 1M tokens (input / cached input / output):", "",
              "| model | the engine costs calls at | source | published | source |",
              "|---|---|---|---|---|"]  # fmt: skip
    for p in doc.get("prices", []):
        lines.append(
            f"| {p['model']} | {_p(p['engine'])} | {p['engine_source']} | "
            f"{_p(p.get('published'))} | {p.get('published_source') or 'n/a'} |"
        )
    lines += [
        "", f"Web search (Tavily) is costed at ${TAVILY_CREDIT_USD} per credit for reporting "
        "only; Tavily bills it, not Token Factory. "
        "Lightning: the notes record no cached-input price; the engine charges cached tokens at "
        "the input price, and the ledger has no cached Lightning tokens.", "",
        "To check against the billing: the per-model token totals and the per-day costs above. "
        "What is not in this ledger and would show only in the billing: calls made with another "
        "`LULL_HOME` (for example inside the API container), requests sent outside the engine's "
        "client (manual probes), requests the provider billed but the client never saw answered "
        "(a timeout after the provider finished), and anything before the first call above. "
        "Whether the provider bills reasoning tokens inside `completion_tokens` (as the ledger "
        "assumes) is to be confirmed on the billing page.",
    ]  # fmt: skip
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    ap.add_argument("--results", type=Path, default=RESULTS)
    args = ap.parse_args(argv)
    doc = snapshot(args.ledger, now=datetime.now(UTC).isoformat(timespec="seconds"))
    out = args.results / OUT_JSON
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    tf = doc["totals"].get("tokenfactory", {})
    print(f"wrote {out}: {doc['calls']} calls; Token Factory {_usd(tf.get('cost_ledger'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
