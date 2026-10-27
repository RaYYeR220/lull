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
    led = mk(tmp_path, cap_run=0.50, cap_day=0, cap_total=0)
    led.record(rec(cost=0.45))
    with pytest.raises(BudgetExceeded) as e:
        led.check("r1", 0.10)
    assert e.value.scope == "run"
    led.check("r1", 0.05)  # exactly at cap is fine
    led.check("other-run", 0.10)


def test_check_day_counts_only_today(tmp_path):
    led = mk(tmp_path, cap_run=0, cap_day=1.0, cap_total=0)
    led.record(rec("old", 5.0, ts=midnight_utc() - 1))
    led.check("n", 0.9)  # yesterday's spend ignored
    led.record(rec("today", 0.95, ts=midnight_utc() + 1))
    with pytest.raises(BudgetExceeded) as e:
        led.check("n", 0.10)
    assert e.value.scope == "day"


def test_check_total_and_order(tmp_path):
    led = mk(tmp_path, cap_run=0, cap_day=0, cap_total=2.0)
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


def test_zero_cap_means_unlimited(tmp_path):
    led = mk(tmp_path, cap_run=0, cap_day=-1, cap_total=0)
    led.record(rec(cost=100))
    led.check("r1", 100)


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
