"""The cost-accounting draft: ledger totals per model next to the price tables."""

import sqlite3

import pytest

import cost_accounting as C

COLS = ("run_id, stage, role, provider, model, tokens_in, tokens_cached, tokens_out, "
        "latency_s, cost_usd, ts")  # fmt: skip
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
QWEN = "Qwen/Qwen3-235B-A22B-Instruct-2507"


def ledger(path, rows):
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE calls (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, stage TEXT, "
        "role TEXT, provider TEXT, model TEXT, tokens_in INTEGER, tokens_cached INTEGER, "
        "tokens_out INTEGER, latency_s REAL, cost_usd REAL, ts REAL)"
    )
    db.executemany(f"INSERT INTO calls ({COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()


def test_snapshot_totals_recomputes_and_splits_by_day(tmp_path):
    p = tmp_path / "ledger.db"
    day1, day2 = 1793132400.0, 1793190000.0  # 2026-10-27 and 2026-10-28 UTC
    ledger(p, [
        ("r1", "plan", "plan", "tokenfactory", ULTRA, 1_000_000, 100_000, 10_000, 1.0, 0.9312,
         day1),
        ("r2", "judge", "eval-judge", "tokenfactory", ULTRA, 0, 0, 1_000_000, 1.0, 3.0, day2),
        ("r2", "judge", "eval-judge", "tokenfactory", QWEN, 1_000_000, 0, 0, 1.0, 0.2, day2),
        ("r3", "research", "research", "tavily", "tavily-search", 0, 0, 0, 0.5, 0.016, day2),
    ])  # fmt: skip
    doc = C.snapshot(p, now="2026-10-28T13:00:00+00:00")
    assert doc["as_of"] == "2026-10-28T13:00:00+00:00" and doc["calls"] == 4
    assert doc["first_call"].startswith("2026-10-27") and doc["last_call"].startswith("2026-10-28")
    u = next(m for m in doc["models"] if m["model"] == ULTRA)
    assert (u["calls"], u["tokens_in"], u["tokens_cached"], u["tokens_out"]) == (
        2,
        1_000_000,
        100_000,
        1_010_000,
    )
    # 0.9 M uncached x 1.00 + 0.1 M cached x 0.12 + 1.01 M out x 3.00
    assert u["cost_recomputed"] == pytest.approx(0.9 + 0.012 + 3.03)
    assert u["cost_ledger"] == pytest.approx(3.9312)
    q = next(m for m in doc["models"] if m["model"] == QWEN)
    assert q["cost_recomputed"] == pytest.approx(0.2)
    t = next(m for m in doc["models"] if m["provider"] == "tavily")
    assert t["credits"] == pytest.approx(2.0) and t["cost_recomputed"] == pytest.approx(0.016)
    assert doc["totals"]["tokenfactory"]["cost_ledger"] == pytest.approx(3.9312 + 0.2)
    days = {(d["day"], d["model"]): d for d in doc["by_day"]}
    assert days[("2026-10-27", ULTRA)]["tokens_in"] == 1_000_000
    assert days[("2026-10-28", ULTRA)]["tokens_out"] == 1_000_000


def test_the_price_table_names_engine_published_and_sources():
    rows = {r["model"]: r for r in C.price_table()}
    assert rows[ULTRA]["engine"] == [1.00, 0.12, 3.00]
    assert rows[ULTRA]["published"] == [1.00, 0.12, 3.00]
    assert rows["nvidia/Nemotron-3_5-Lightning"]["published"][1] is None
    assert rows[QWEN]["engine"] == [0.20, 0.05, 0.60]
    assert "engine/src/lull/ledger.py" in rows[ULTRA]["engine_source"]
    assert "eval/suite_a/score.py" in rows[QWEN]["engine_source"]
    assert "research-models.md" in rows[ULTRA]["published_source"]


def test_markdown_is_a_draft_with_totals_and_both_price_columns(tmp_path):
    p = tmp_path / "ledger.db"
    ledger(p, [("r1", "plan", "plan", "tokenfactory", ULTRA, 2_000, 0, 1_000, 1.0, 0.005,
                1793190000.0)])  # fmt: skip
    md = "\n".join(C.markdown(C.snapshot(p, now="2026-10-28T13:00:00+00:00")))
    assert md.startswith("## Cost accounting (draft)")
    for want in (ULTRA, "2,000", "1,000", "$0.0050", "1.00 / 0.12 / 3.00", "not in this ledger",
                 "2026-10-28T13:00:00+00:00"):  # fmt: skip
        assert want in md, want


def test_the_ledger_is_opened_read_only(tmp_path):
    p = tmp_path / "ledger.db"
    ledger(p, [])
    before = p.read_bytes()
    C.snapshot(p, now="2026-10-28T13:00:00+00:00")
    assert p.read_bytes() == before
    with pytest.raises(FileNotFoundError):
        C.snapshot(tmp_path / "missing.db", now="x")
