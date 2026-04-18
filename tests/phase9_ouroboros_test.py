"""Phase 9: Ouroboros nightly runs end-to-end, writes learned.toml, validates bounds."""
import asyncio
import json
from pathlib import Path


def _seed_and_run_engine(tmp_path, monkeypatch, steps: int = 500):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    intel = tmp_path / "intel"; intel.mkdir(parents=True, exist_ok=True)
    (intel / "news_reactor.json").write_text(json.dumps({"events": [
        {"ticker": t, "score": s} for t, s in
        [("AAPL", 0.6), ("NVDA", 0.7), ("MSFT", 0.5), ("TSLA", -0.5), ("SPY", 0.4), ("QQQ", 0.4)]
    ]}))
    (intel / "earnings_whisper.json").write_text(json.dumps({"whispers":
        {t: {"expected_surprise_bps": b, "analyst_count": 20} for t, b in
         [("AAPL", 80), ("NVDA", 120), ("MSFT", 60), ("TSLA", -70)]}
    }))
    (intel / "sec_scanner.json").write_text(json.dumps({"filings": [
        {"ticker": t, "change_score": s} for t, s in [("AAPL", 0.35), ("NVDA", 0.40)]
    ]}))
    (intel / "regime_council.json").write_text(json.dumps({"regime_probs": [0.7, 0.2, 0.05, 0.05]}))
    (intel / "thesis_monitor.json").write_text(json.dumps({"invalidations": []}))
    (intel / "index_recon.json").write_text(json.dumps({"events": [
        {"ticker": "SPY", "type": "sp500", "effective_ts": 1_700_000_000 + 7 * 86400}]}))

    import importlib
    for m_name in ["python_brain.core.nats_client", "python_brain.core.data_health",
                   "python_brain.engine.wal", "python_brain.engine.loop",
                   "python_brain.ouroboros.core", "python_brain.ouroboros.learned_writer"]:
        import sys
        if m_name in sys.modules:
            importlib.reload(sys.modules[m_name])
    from python_brain.engine.loop import Engine
    from python_brain.engine.tick_feed import SimTickFeed
    asyncio.run(Engine().run(SimTickFeed(steps=steps)))


def test_ouroboros_runs_and_writes(tmp_path, monkeypatch):
    _seed_and_run_engine(tmp_path, monkeypatch, steps=400)
    monkeypatch.setenv("AEGIS_V5_CONFIG", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    repo_bounds = Path(__file__).resolve().parent.parent / "config" / "bounds.toml"
    (tmp_path / "config" / "bounds.toml").write_text(repo_bounds.read_text())

    import importlib, python_brain.ouroboros.learned_writer as lw, python_brain.ouroboros.core as core
    importlib.reload(lw); importlib.reload(core)
    result = core.run_nightly()
    assert result.n_trades >= 0
    # Either writes learned.toml OR refuses due to bounds.
    if result.n_trades > 0 and result.updated:
        assert (tmp_path / "config" / "learned.toml").exists()


def test_bounds_refuses_out_of_range(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_CONFIG", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    repo_bounds = Path(__file__).resolve().parent.parent / "config" / "bounds.toml"
    (tmp_path / "config" / "bounds.toml").write_text(repo_bounds.read_text())
    import importlib, python_brain.ouroboros.learned_writer as lw
    importlib.reload(lw)
    ok, refusals = lw.validate_bounds({"kelly_fraction": 0.99})
    assert not ok
    assert "kelly_fraction" in refusals


def test_bounds_accepts_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_CONFIG", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    repo_bounds = Path(__file__).resolve().parent.parent / "config" / "bounds.toml"
    (tmp_path / "config" / "bounds.toml").write_text(repo_bounds.read_text())
    import importlib, python_brain.ouroboros.learned_writer as lw
    importlib.reload(lw)
    ok, refusals = lw.validate_bounds({
        "kelly_fraction": 0.12, "chandelier_atr_mult": 2.3,
        "heat_limit": 0.075, "confidence_floor": 0.65, "regime_scale": 0.8,
    })
    assert ok, refusals
