import sqlite3
import threading
import time
from datetime import UTC, datetime

import pytest

from lull.ledger import (
    PRICES,
    TAVILY_CREDIT_USD,
    BudgetExceeded,
    CallRecord,
    Ledger,
    SearchCapReached,
    cost_of,
    cost_of_tavily,
)

ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
SUPER = "nvidia/Nemotron-3.5-Super-VL"
LIGHT = "nvidia/Nemotron-3_5-Lightning"


def rec(
    run_id="r1",
    cost=0.1,
    ts=None,
    role="describe",
    model=SUPER,
    provider="tokenfactory",
    tin=10,
    tcached=0,
    tout=5,
    lat=1.5,
):
    return CallRecord(
        run_id=run_id,
        stage="s",
        role=role,
        provider=provider,
        model=model,
        tokens_in=tin,
        tokens_cached=tcached,
        tokens_out=tout,
        latency_s=lat,
        cost_usd=cost,
        ts=time.time() if ts is None else ts,
    )


def midnight_utc() -> float:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def mk(tmp_path, **caps):
    return Ledger(tmp_path / "sub" / "ledger.db", **caps)


def test_prices_table():
    assert PRICES[ULTRA] == (1.00, 0.12, 3.00)
    assert PRICES[SUPER] == (0.30, 0.06, 0.90)
    assert PRICES[LIGHT] == (0.06, 0.06, 0.24)


def test_cost_of():
    assert cost_of(ULTRA, 1_000_000, 0, 1_000_000) == pytest.approx(4.0)
    assert cost_of(SUPER, 1_000_000, 1_000_000, 0) == pytest.approx(0.06)
    # tokens_in counts all prompt tokens; cached are a subset billed at cached price
    assert cost_of(SUPER, 1_000_000, 400_000, 0) == pytest.approx(0.6 * 0.30 + 0.4 * 0.06)


def test_cost_of_unknown_model():
    with pytest.raises(KeyError, match="gpt-nope"):
        cost_of("gpt-nope", 1, 0, 1)


def test_tavily():
    assert TAVILY_CREDIT_USD == 0.008
    assert cost_of_tavily(5) == pytest.approx(0.04)


def test_record_roundtrip_and_creates_dir(tmp_path):
    led = mk(tmp_path)
    r = rec()
    led.record(r)
    assert led.calls("r1") == [r]
    assert led.calls("other") == []


def test_spent_filters(tmp_path):
    led = mk(tmp_path)
    led.record(rec("a", 0.1, ts=100))
    led.record(rec("a", 0.2, ts=200))
    led.record(rec("b", 0.4, ts=300))
    assert led.spent() == pytest.approx(0.7)
    assert led.spent(run_id="a") == pytest.approx(0.3)
    assert led.spent(since=150) == pytest.approx(0.6)
    assert led.spent(run_id="a", since=150) == pytest.approx(0.2)
    assert mk(tmp_path / "x").spent() == 0.0


def test_check_run_cap(tmp_path):
    led = mk(tmp_path, cap_run=0.50, cap_day=None, cap_total=None)
    led.record(rec(cost=0.45))
    with pytest.raises(BudgetExceeded) as e:
        led.check("r1", 0.10)
    assert e.value.scope == "run"
    led.check("r1", 0.05)  # exactly at cap is fine
    led.check("other-run", 0.10)


def test_check_day_counts_only_today(tmp_path):
    led = mk(tmp_path, cap_run=None, cap_day=1.0, cap_total=None)
    led.record(rec("old", 5.0, ts=midnight_utc() - 1))
    led.check("n", 0.9)  # yesterday's spend ignored
    led.record(rec("today", 0.95, ts=midnight_utc() + 1))
    with pytest.raises(BudgetExceeded) as e:
        led.check("n", 0.10)
    assert e.value.scope == "day"


def test_check_total_and_order(tmp_path):
    led = mk(tmp_path, cap_run=None, cap_day=None, cap_total=2.0)
    led.record(rec("old", 1.95, ts=1000))
    with pytest.raises(BudgetExceeded) as e:
        led.check("n", 0.10)
    assert e.value.scope == "total"
    led2 = Ledger(tmp_path / "o.db", cap_run=0.1, cap_day=0.1, cap_total=0.1)
    led2.record(rec(cost=0.1))
    with pytest.raises(BudgetExceeded) as e2:
        led2.check("r1", 0.01)
    assert e2.value.scope == "run"


def test_caps_default_from_settings(tmp_path, monkeypatch):
    from lull.config import get_settings

    monkeypatch.setenv("LULL_CAP_RUN", "0.2")
    get_settings.cache_clear()
    try:
        led = Ledger(tmp_path / "d.db")
        with pytest.raises(BudgetExceeded):
            led.check("r", 0.3)
    finally:
        get_settings.cache_clear()


def test_no_cap_is_none_and_a_zero_cap_refuses_every_call(tmp_path):
    """None (LULL_CAP_*=none) is the only way to switch a cap off; 0 means spend nothing."""
    led = mk(tmp_path, cap_run=None, cap_day=None, cap_total=None)
    led.record(rec(cost=100))
    led.check("r1", 100)
    for scope in ("run", "day", "total"):
        caps = {"cap_run": None, "cap_day": None, "cap_total": None, f"cap_{scope}": 0}
        zero = Ledger(tmp_path / f"{scope}.db", **caps)
        with pytest.raises(BudgetExceeded) as err:
            zero.check("r1", 0.0)
        assert err.value.scope == scope and "0" in str(err.value)


def test_persists_across_instances(tmp_path):
    p = tmp_path / "p.db"
    Ledger(p).record(rec(cost=0.3))
    assert Ledger(p).spent() == pytest.approx(0.3)


def test_summary(tmp_path):
    led = mk(tmp_path)
    led.record(rec(role="describe", model=SUPER, cost=0.1, tin=10, tcached=2, tout=5, lat=1.0))
    led.record(rec(role="describe", model=SUPER, cost=0.2, tin=20, tcached=0, tout=6, lat=2.0))
    led.record(rec(role="plan", model=ULTRA, cost=0.3, tin=1, tcached=0, tout=1, lat=4.0))
    led.record(rec("other", role="plan", model=ULTRA, cost=9))
    s = led.summary("r1")
    assert s["total_cost_usd"] == pytest.approx(0.6)
    assert s["calls"] == 3
    rows = {(r["role"], r["model"]): r for r in s["by_role_model"]}
    d = rows[("describe", SUPER)]
    assert (d["calls"], d["tokens_in"], d["tokens_cached"], d["tokens_out"]) == (2, 30, 2, 11)
    assert d["cost_usd"] == pytest.approx(0.3) and d["latency_s"] == pytest.approx(3.0)
    assert rows[("plan", ULTRA)]["calls"] == 1
    assert led.summary("none") == {
        "run_id": "none",
        "calls": 0,
        "total_cost_usd": 0.0,
        "by_role_model": [],
    }


def test_threaded_writes(tmp_path):
    led = mk(tmp_path)

    def work():
        for _ in range(25):
            led.record(rec(cost=0.01))

    ts = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(led.calls("r1")) == 100


def test_subscribers_see_every_record_after_it_is_stored(tmp_path):
    led = mk(tmp_path)
    seen: list[tuple[CallRecord, float]] = []
    unsubscribe = led.subscribe(lambda r: seen.append((r, led.spent(run_id=r.run_id))))
    led.record(rec("r1", cost=0.25))
    led.record(rec("r2", cost=0.5))
    # each subscriber runs after the row is committed: spent() already includes it
    assert [(r.run_id, spent) for r, spent in seen] == [("r1", 0.25), ("r2", 0.5)]
    unsubscribe()
    led.record(rec("r1", cost=1.0))
    assert len(seen) == 2
    unsubscribe()  # twice is harmless


def test_a_failing_subscriber_does_not_lose_the_record(tmp_path, caplog):
    led = mk(tmp_path)

    def boom(r):
        raise RuntimeError("ui went away")

    led.subscribe(boom)
    led.record(rec("r1", cost=0.1))
    assert led.spent(run_id="r1") == pytest.approx(0.1)
    assert "ui went away" in caplog.text


def test_runs_lists_each_run_with_calls_cost_and_times(tmp_path):
    led = mk(tmp_path)
    led.record(rec("a", cost=0.1, ts=100.0))
    led.record(rec("b", cost=0.2, ts=200.0))
    led.record(rec("a", cost=0.3, ts=300.0))
    runs = led.runs()
    assert [r["run_id"] for r in runs] == ["a", "b"]  # most recent activity first
    a = runs[0]
    assert (a["calls"], a["first_ts"], a["last_ts"]) == (2, 100.0, 300.0)
    assert a["cost_usd"] == pytest.approx(0.4)
    assert mk(tmp_path / "empty").runs() == []


# -- guards (Task 4 minors) ----------------------------------------------------------------------


@pytest.mark.parametrize("estimate", [float("nan"), float("inf"), -0.01])
def test_check_refuses_an_estimate_that_is_not_a_cost(tmp_path, estimate):
    led = mk(tmp_path, cap_run=1.0)
    with pytest.raises(ValueError, match="estimate"):
        led.check("r1", estimate)
    led.check("r1", 0.0)  # nothing to spend is fine


def test_a_cap_switched_off_in_the_settings_is_logged(tmp_path, monkeypatch, caplog):
    from lull.config import get_settings

    monkeypatch.setenv("LULL_CAP_DAY", "none")
    monkeypatch.setenv("LULL_CAP_TOTAL", "0")
    get_settings.cache_clear()
    try:
        with caplog.at_level("WARNING", logger="lull.ledger"):
            led = Ledger(tmp_path / "d.db")
            Ledger(tmp_path / "e.db", cap_day=None, cap_total=0)  # given in code: no warning
    finally:
        get_settings.cache_clear()
    assert (led.cap_day, led.cap_total) == (None, 0.0)
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 2
    assert "LULL_CAP_DAY" in warnings[0] and "no day cap" in warnings[0]
    assert "LULL_CAP_TOTAL" in warnings[1] and "refuses every call" in warnings[1]


def test_ledger_closes_and_is_a_context_manager(tmp_path):
    path = tmp_path / "d.db"
    with Ledger(path, cap_run=None, cap_day=None, cap_total=None) as led:
        led.record(rec(cost=0.5))
        assert led.spent() == 0.5
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        led.spent()
    led.close()  # twice is fine
    for p in tmp_path.iterdir():
        p.unlink()  # nothing holds the database open any more (Windows)
    assert list(tmp_path.iterdir()) == []


# -- what the caps count -------------------------------------------------------------------------


def test_spend_caps_count_model_calls_only(tmp_path):
    """Tavily is metered in searches, not dollars: its notional cost never trips a spend cap
    (it is still reported)."""
    led = mk(tmp_path, cap_run=0.5, cap_day=None, cap_total=None)
    led.record(rec(cost=5.0, provider="tavily", model="tavily-search"))
    led.check("r1", 0.4)
    assert led.spent(run_id="r1") == 0.0 and led.spent(run_id="r1", tavily=True) == 5.0
    led.record(rec(cost=0.3))
    with pytest.raises(BudgetExceeded):
        led.check("r1", 0.3)


def test_searches_are_capped_per_run_and_per_day(tmp_path):
    led = Ledger(tmp_path / "s.db", cap_run=None, cap_day=None, cap_total=None, tavily_run=2,
                 tavily_day=3)  # fmt: skip
    for _ in range(2):
        led.check_searches("r1")
        led.record(rec(run_id="r1", provider="tavily", model="tavily-search", cost=0.008))
    with pytest.raises(SearchCapReached) as err:
        led.check_searches("r1")
    assert (err.value.scope, err.value.done, err.value.cap) == ("run", 2, 2)
    led.check_searches("r2")
    led.record(rec(run_id="r2", provider="tavily", model="tavily-search", cost=0.008))
    with pytest.raises(SearchCapReached) as err:
        led.check_searches("r3")
    assert err.value.scope == "day"
    assert led.searches(run_id="r1") == 2 and led.searches() == 3


def test_search_caps_default_from_the_settings(tmp_path, monkeypatch):
    from lull.config import get_settings

    get_settings.cache_clear()
    try:
        led = Ledger(tmp_path / "d.db")
        assert (led.tavily_run, led.tavily_day) == (5, 60)
        monkeypatch.setenv("LULL_TAVILY_RUN", "3")
        get_settings.cache_clear()
        assert Ledger(tmp_path / "e.db").tavily_run == 3
    finally:
        get_settings.cache_clear()
