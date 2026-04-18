"""Phase 7: each of the 5 agents produces valid structured output and records to A/B harness."""
import json


def test_news_reactor_positive_negative(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    import importlib, python_brain.intelligence.news_reactor as mod
    importlib.reload(mod)
    nr = mod.NewsReactor()
    pos = nr.classify_one("AAPL upgraded beats record", "AAPL")
    neg = nr.classify_one("TSLA miss downgrade recall", "TSLA")
    assert pos["score"] > 0
    assert neg["score"] < 0


def test_news_reactor_writes_structured_output(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    import importlib, python_brain.intelligence.news_reactor as mod
    importlib.reload(mod)
    mod.NewsReactor().run([{"headline": "AAPL upgraded", "ticker": "AAPL"}])
    data = json.loads(mod.OUT_PATH.read_text())
    assert "events" in data and isinstance(data["events"], list)
    assert set(data["events"][0]) == {"ticker", "headline", "score", "confidence"}


def test_earnings_whisper_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    import importlib, python_brain.intelligence.earnings_whisper as mod
    importlib.reload(mod)
    mod.EarningsWhisper().run(["AAPL", "MSFT"], {"AAPL": 0.5, "MSFT": 0.3})
    data = json.loads(mod.OUT_PATH.read_text())
    assert "AAPL" in data["whispers"]


def test_sec_scanner_jaccard(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    import importlib, python_brain.intelligence.sec_scanner as mod
    importlib.reload(mod)
    mod.SecScanner().run([{
        "ticker": "AAPL", "form": "10-Q",
        "prior_text": "We expect steady growth in services revenue",
        "current_text": "We foresee major disruption risk new competitor emerging",
    }])
    data = json.loads(mod.OUT_PATH.read_text())
    assert data["filings"][0]["change_score"] > 0.5


def test_regime_council_writes_probs(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    import importlib, python_brain.intelligence.regime_council as mod
    importlib.reload(mod)
    mod.RegimeCouncil().run([0.001] * 30, current_vol=0.2)
    data = json.loads(mod.OUT_PATH.read_text())
    assert len(data["regime_probs"]) == 4
    assert abs(sum(data["regime_probs"]) - 1.0) < 1e-6


def test_thesis_monitor_flags_sentiment_flip(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    import importlib, python_brain.intelligence.thesis_monitor as mod
    importlib.reload(mod)
    inv = mod.ThesisMonitor().run(
        [{"signal_id": "s1", "strategy": "sentiment_long_short", "ticker": "AAPL",
          "features": {"sentiment_score": 0.6}}],
        {"sentiment": {"AAPL": -0.5}},
    )
    assert len(inv) == 1 and inv[0]["reason"] == "sentiment_flip"
