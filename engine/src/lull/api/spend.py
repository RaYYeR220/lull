"""What a run cost, as visitors see it: model spend in USD and web searches as a count.

The engine caps model spend only and meters Tavily in searches; it still prices a search at
its list rate for its own report (`tavily_notional_usd_this_run`, and a `cost_usd` on the
Tavily rows of `calls_by_role_model`). Tavily runs on free credits, so no such dollar figure
is shown here: those are dropped and each Tavily row carries its `searches` instead.
"""

from __future__ import annotations

from typing import Any


def is_search(model: Any) -> bool:
    return isinstance(model, str) and model.startswith("tavily")


def _num(x: Any) -> float | int | None:
    return x if isinstance(x, int | float) and not isinstance(x, bool) else None


def public_calls(groups: Any) -> list[dict] | None:
    """calls_by_role_model with the searches counted, not priced."""
    if not isinstance(groups, list):
        return None
    out = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        if is_search(g.get("model")):
            g = {k: v for k, v in g.items() if k != "cost_usd"}
            g["searches"] = _num(g.get("calls"))
        out.append(g)
    return out


def public_spend(summary: dict) -> dict[str, Any]:
    """{model_usd, model_usd_all_runs, searches} from a run summary: the engine's `spend`
    when it has one, else worked out from its calls (a summary from before searches were
    counted), else its cost_usd alone."""
    spend = summary.get("spend") if isinstance(summary.get("spend"), dict) else None
    groups = summary.get("calls_by_role_model")
    groups = [g for g in groups if isinstance(g, dict)] if isinstance(groups, list) else None
    if spend is not None:
        return {
            "model_usd": _num(spend.get("model_usd_this_run")),
            "model_usd_all_runs": _num(spend.get("model_usd_all_runs")),
            "searches": _num(spend.get("tavily_searches_this_run")),
        }
    if groups is not None:
        models = sum(
            _num(g.get("cost_usd")) or 0.0 for g in groups if not is_search(g.get("model"))
        )
        searches = sum(_num(g.get("calls")) or 0 for g in groups if is_search(g.get("model")))
        return {"model_usd": round(models, 6), "model_usd_all_runs": None, "searches": searches}
    return {
        "model_usd": _num(summary.get("cost_usd")),
        "model_usd_all_runs": None,
        "searches": None,
    }


def public_summary(summary: dict) -> dict:
    """`summary` with spend as public_spend gives it, `cost_usd` the model spend, and the
    Tavily rows of calls_by_role_model counted in searches; nothing priced at list rate."""
    out = dict(summary)
    out["spend"] = public_spend(summary)
    if out["spend"]["model_usd"] is not None:
        out["cost_usd"] = out["spend"]["model_usd"]
    calls = public_calls(summary.get("calls_by_role_model"))
    if calls is not None:
        out["calls_by_role_model"] = calls
    return out
