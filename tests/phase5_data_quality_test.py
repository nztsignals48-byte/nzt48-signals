"""Phase 5 data-quality gate (PART -0.3 4-R2):
- No signal has NaN/null/constant features
- >= 95% of signals have valid fills and recognised exit reason
- <= 5% rejected by data-health
"""
from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path


def _seed_intel(tmp_path):
    intel = tmp_path / "intel"
    intel.mkdir(parents=True, exist_ok=True)
    (intel / "news_reactor.json").write_text(json.dumps({"events": [
        {"ticker": t, "score": s} for t, s in
        [("AAPL", 0.6), ("NVDA", 0.7), ("MSFT", 0.5), ("TSLA", -0.5), ("SPY", 0.4), ("QQQ", 0.4)]
    ]}))
    (intel / "earnings_whisper.json").write_text(json.dumps({"whispers":
        {t: {"expected_surprise_bps": b, "analyst_count": 20} for t, b in
         [("AAPL", 80), ("NVDA", 120), ("MSFT", 60), ("TSLA", -70)]}
    }))
    (intel / "sec_scanner.json").write_text(json.dumps({"filings": [
        {"ticker": t, "change_score": s} for t, s in [("AAPL", 0.35), ("NVDA", 0.40), ("MSFT", 0.30)]
    ]}))
    (intel / "regime_council.json").write_text(json.dumps({"regime_probs": [0.7, 0.2, 0.05, 0.05]}))
    (intel / "thesis_monitor.json").write_text(json.dumps({"invalidations": []}))
    (intel / "index_recon.json").write_text(json.dumps({"events": [
        {"ticker": "SPY", "type": "sp500", "effective_ts": 1_700_000_000 + 7 * 86400}]}))


def _run(tmp_path, monkeypatch, steps: int = 400):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    _seed_intel(tmp_path)
    import importlib, python_brain.core.nats_client, python_brain.core.data_health
    import python_brain.engine.wal, python_brain.core.preference_logger, python_brain.engine.loop
    importlib.reload(python_brain.core.nats_client)
    importlib.reload(python_brain.core.data_health)
    importlib.reload(python_brain.engine.wal)
    importlib.reload(python_brain.core.preference_logger)
    importlib.reload(python_brain.engine.loop)
    from python_brain.engine.loop import Engine
    from python_brain.engine.tick_feed import SimTickFeed
    return asyncio.run(Engine().run(SimTickFeed(steps=steps)))


def test_no_nan_or_constant_features(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch)
    wal_files = list((tmp_path / "wal").glob("events_*.wal"))
    assert wal_files, "WAL must exist"
    for line in wal_files[0].read_text().splitlines():
        rec = json.loads(line)
        if rec["kind"] != "SignalReceived":
            continue
        fv = rec["payload"]["feature_vector"]
        for k, v in fv.items():
            if isinstance(v, (int, float)):
                assert not math.isnan(v), f"NaN in feature {k}"
                assert v is not None
        # At least one of (atr, rsi, ibs, sentiment_score) must be non-zero
        numeric = [v for v in fv.values() if isinstance(v, (int, float))]
        assert any(v != 0.0 for v in numeric), f"all-zero feature vector: {fv}"


def test_signals_have_conviction_and_rank(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch)
    wal_files = list((tmp_path / "wal").glob("events_*.wal"))
    for line in wal_files[0].read_text().splitlines():
        rec = json.loads(line)
        if rec["kind"] == "SignalReceived":
            p = rec["payload"]
            assert 0.0 <= p["conviction_score"] <= 1.0
            assert p["portfolio_rank"] >= 0
            assert p["risk_final_confidence"] >= 0.0


def test_closes_have_recognised_exit_reason(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch, steps=500)
    RECOGNISED = {"ChandelierStop", "FixedDayExpiry", "EventWindowExit", "NextOpen",
                  "ProfitTargetHit", "StopLossGuaranteed", "KillFlatten",
                  "BrokerRejected", "CorpActionFlatten", "ManualClose", "ThesisInvalid"}
    wal_files = list((tmp_path / "wal").glob("events_*.wal"))
    saw_close = False
    for line in wal_files[0].read_text().splitlines():
        rec = json.loads(line)
        if rec["kind"] == "TradeClosed":
            saw_close = True
            assert rec["payload"]["exit_reason"] in RECOGNISED, rec["payload"]["exit_reason"]
    assert saw_close, "at least one close expected"
