"""Phase 8: watchlist published on NATS, held positions preserved, Thompson slots used."""
import asyncio
import json


def _prep(tmp_path, monkeypatch):
    monkeypatch.setenv("AEGIS_V5_DATA", str(tmp_path))
    (tmp_path / "scan_scores.json").write_text(json.dumps(
        {f"TICK{i}": 1.0 - i * 0.01 for i in range(200)}
    ))
    import importlib
    import python_brain.core.nats_client as m1
    import python_brain.scanner.scanner as m2
    importlib.reload(m1); importlib.reload(m2)
    return m1, m2


def test_watchlist_preserves_held(tmp_path, monkeypatch):
    _, scanner = _prep(tmp_path, monkeypatch)
    wl = asyncio.run(scanner.scan_once(held_positions={"HELD1", "HELD2"}, slots=50))
    assert "HELD1" in wl and "HELD2" in wl


def test_watchlist_ranks_by_score(tmp_path, monkeypatch):
    _, scanner = _prep(tmp_path, monkeypatch)
    wl = asyncio.run(scanner.scan_once(held_positions=set(), slots=30))
    assert "TICK0" in wl


def test_watchlist_published_to_nats(tmp_path, monkeypatch):
    bus_mod, scanner = _prep(tmp_path, monkeypatch)
    client = bus_mod.NatsClient.from_env()
    async def go():
        await client.connect()
        wl = await scanner.scan_once(held_positions={"AAPL"}, slots=20)
        await scanner.publish_watchlist(client, wl)
        last = await client.last("watchlist.current")
        return wl, last
    wl, last = asyncio.run(go())
    assert last is not None
    assert last.payload["count"] == len(wl)
    assert "AAPL" in last.payload["tickers"]
