"""Phase 1 gate: bus publish/subscribe, schema version enforcement, data_health evaluates."""
import asyncio
import json

import pytest


def _reload_with_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    import importlib
    import python_brain.core.nats_client as m1
    import python_brain.core.data_health as m2
    importlib.reload(m1)
    importlib.reload(m2)
    return m1, m2


def test_bus_publish_subscribe(tmp_path, monkeypatch):
    m1, _ = _reload_with_data_dir(tmp_path, monkeypatch)
    client = m1.NatsClient.from_env()
    received = []
    async def run():
        await client.connect()
        await client.subscribe("signals.core", lambda m: received.append(m))
        await client.publish("signals.core", {"strategy": "sentiment_long_short", "ticker": "AAPL"})
    asyncio.run(run())
    assert len(received) == 1
    assert received[0].payload["ticker"] == "AAPL"


def test_bus_schema_version_guard(tmp_path, monkeypatch):
    m1, _ = _reload_with_data_dir(tmp_path, monkeypatch)
    line = json.dumps({"subject": "signals.core", "schema_version": 999,
                       "payload": {}, "ts_ns": 0})
    with pytest.raises(m1.UnknownSchemaVersion):
        m1.BusMessage.from_line(line)


def test_data_health_missing_fed_starved(tmp_path, monkeypatch):
    _, m2 = _reload_with_data_dir(tmp_path, monkeypatch)
    m2.INTEL_DIR.mkdir(parents=True, exist_ok=True)
    (m2.INTEL_DIR / "news_reactor.json").write_text(
        json.dumps({"events": [{"ticker": "AAPL", "score": 0.5, "note": "x" * 200}]})
    )
    statuses = m2.DataHealthMonitor().check()
    assert statuses["news_reactor.json"].status == "FED"
    assert statuses["earnings_whisper.json"].status == "MISSING"
    starved = m2.DataHealthMonitor().starved_strategies()
    assert "earnings_pattern" in starved
    assert "filing_change_detect" in starved
    assert "sentiment_long_short" not in starved

    assert m2.DataHealthMonitor().is_startup_ok() is False
