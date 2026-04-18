"""Phase 3: broker_sync flags discrepancies."""
from python_brain.engine.broker_sync import BrokerSync
from python_brain.engine.portfolio_state import PortfolioState, Position


def test_broker_sync_happy_path():
    state = PortfolioState()
    state.positions.append(Position(signal_id="s", ticker="AAPL", strategy="t",
                                    account="ISA", entry_price=100, entry_ts_ns=0, size_shares=10))
    r = BrokerSync().run(state, {"AAPL": 10})
    assert r.discrepancies == []


def test_broker_sync_catches_mismatch():
    state = PortfolioState()
    state.positions.append(Position(signal_id="s", ticker="AAPL", strategy="t",
                                    account="ISA", entry_price=100, entry_ts_ns=0, size_shares=10))
    r = BrokerSync().run(state, {"AAPL": 11})
    assert len(r.discrepancies) == 1
    assert "AAPL" in r.discrepancies[0]
