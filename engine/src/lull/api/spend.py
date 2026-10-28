"""Model spend and Tavily credits, kept apart: Tavily runs on free credits, so the money is
what the model calls cost. The engine records both in one ledger (Tavily at
TAVILY_CREDIT_USD a credit, model names "tavily-search" / "tavily-extract")."""

from __future__ import annotations

from typing import Any

from lull.ledger import TAVILY_CREDIT_USD


def is_tavily(model: Any) -> bool:
    return isinstance(model, str) and model.startswith("tavily")


def split(groups: Any) -> dict[str, float] | None:
    """{models_usd, tavily_credits} from a summary's calls_by_role_model, or None when there
    is no such list to split."""
    if not isinstance(groups, list):
        return None
    models = tavily = 0.0
    for g in groups:
        cost = g.get("cost_usd") if isinstance(g, dict) else None
        if not isinstance(cost, int | float) or isinstance(cost, bool):
            continue
        if is_tavily(g.get("model")):
            tavily += cost
        else:
            models += cost
    return {"models_usd": round(models, 6), "tavily_credits": round(tavily / TAVILY_CREDIT_USD, 2)}
