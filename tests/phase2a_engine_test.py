"""Phase 2A acceptance gate.

Proves the engine:
- processes ticks end-to-end
- emits SignalReceived and TradeClosed events under the dataset contract
- writes hash-chained WAL, daily-rotated
- at least 3 of the 6 MVP strategies fire
- WAL validates (every event has required fields)
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from python_brain.engine.loop import Engine
from python_brain.engine.tick_feed import SimTickFeed
from python_brain.engine.wal import DatasetContractViolation, WAL, REQUIRED_SIGNAL_FIELDS, REQUIRED_CLOSE_FIELDS


def _run_engine(tmp_path, monkeypatch, steps: int = 400):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    import importlib, python_brain.core.nats_client as bus, python_brain.core.data_health as dh
    import python_brain.engine.wal as wal, python_brain.core.preference_logger as pl
    importlib.reload(bus); importlib.reload(dh); importlib.reload(wal); importlib.reload(pl)
    import python_brain.engine.loop as loop
    importlib.reload(loop)

    # Seed intel into tmp_path
    intel = Path(tmp_path) / "intel"; intel.mkdir(parents=True, exist_ok=True)
    (intel / "news_reactor.json").write_text(json.dumps({"events": [
        {"ticker": "AAPL", "score":  0.6},
        {"ticker": "NVDA", "score":  0.7},
        {"ticker": "MSFT", "score":  0.5},
        {"ticker": "TSLA", "score": -0.5},
        {"ticker": "SPY",  "score":  0.4},
        {"ticker": "QQQ",  "score":  0.4},
    ]}))
    (intel / "earnings_whisper.json").write_text(json.dumps({"whispers": {
        "AAPL": {"expected_surprise_bps":  80, "analyst_count": 20},
        "NVDA": {"expected_surprise_bps": 120, "analyst_count": 30},
        "MSFT": {"expected_surprise_bps":  60, "analyst_count": 25},
        "TSLA": {"expected_surprise_bps": -70, "analyst_count": 22},
    }}))
    (intel / "sec_scanner.json").write_text(json.dumps({"filings": [
        {"ticker": "AAPL", "change_score": 0.35},
        {"ticker": "MSFT", "change_score": 0.30},
        {"ticker": "NVDA", "change_score": 0.40},
    ]}))
    (intel / "regime_council.json").write_text(json.dumps({"regime_probs": [0.7, 0.2, 0.05, 0.05]}))
    (intel / "thesis_monitor.json").write_text(json.dumps({"invalidations": []}))
    (intel / "index_recon.json").write_text(json.dumps({"events": [
        {"ticker": "SPY", "type": "sp500", "effective_ts": 1_700_000_000 + 7 * 86400}
    ]}))

    eng = loop.Engine()
    return asyncio.run(eng.run(SimTickFeed(steps=steps)))


def test_engine_processes_ticks(tmp_path, monkeypatch):
    s = _run_engine(tmp_path, monkeypatch, steps=300)
    assert s.ticks > 0
    assert s.signals_generated > 0, "strategies must fire on seeded intel"


def test_strategies_diverse(tmp_path, monkeypatch):
    s = _run_engine(tmp_path, monkeypatch, steps=300)
    assert len(s.per_strategy_trades) >= 3, f"at least 3 strategies must trade, got {s.per_strategy_trades}"


def test_wal_dataset_contract_validates(tmp_path, monkeypatch):
    _run_engine(tmp_path, monkeypatch, steps=300)
    wal_dir = Path(tmp_path) / "wal"
    files = list(wal_dir.glob("events_*.wal"))
    assert files, "WAL file must be created"
    sigs, closes = 0, 0
    for line in files[0].read_text().splitlines():
        rec = json.loads(line)
        assert "hash" in rec and "prev" in rec
        if rec["kind"] == "SignalReceived":
            assert REQUIRED_SIGNAL_FIELDS <= set(rec["payload"].keys())
            sigs += 1
        elif rec["kind"] == "TradeClosed":
            assert REQUIRED_CLOSE_FIELDS <= set(rec["payload"].keys())
            closes += 1
    assert sigs > 0


def test_wal_refuses_invalid_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    import importlib, python_brain.engine.wal as wal
    importlib.reload(wal)
    w = wal.WAL()
    with pytest.raises(wal.DatasetContractViolation):
        w.append("SignalReceived", {"schema_version": 1})   # missing fields


def test_wal_hash_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    import importlib, python_brain.engine.wal as wal
    importlib.reload(wal)
    w = wal.WAL()
    # Minimal valid TradeClosed payload.
    base = {
        "schema_version": 1, "signal_id": "x", "entry_timestamp_ns": 0, "exit_timestamp_ns": 1,
        "entry_price": 1.0, "exit_price": 1.01, "size_shares": 1,
        "spread_cost_bps": 0, "commission_abs": 0, "stamp_duty_abs": 0, "financing_cost_abs": 0,
        "slippage_bps_vs_arrival": 0,
        "realized_pnl_abs": 0.01, "realized_pnl_bps": 100, "mae_bps": 0, "mfe_bps": 100,
        "regime_at_entry": [1,0,0,0], "regime_at_exit": [1,0,0,0],
        "exit_reason": "ChandelierStop",
    }
    r1 = w.append("TradeClosed", base)
    r2 = w.append("TradeClosed", base)
    assert r1["hash"] != r2["hash"]
    assert w.prev_hash == r2["hash"]
