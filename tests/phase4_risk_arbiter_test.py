"""Phase 4 acceptance: risk arbiter emits 16 continuous weighted deltas, NO hard gates except halt."""
from python_brain.engine.risk_arbiter import RiskArbiter
from python_brain.engine.portfolio_state import PortfolioState


def test_16_checks_emit_deltas():
    ra = RiskArbiter(confidence_floor=0.55)
    state = PortfolioState(equity_gbp=20000, hwm_gbp=20000)
    r = ra.evaluate(0.7, {"spread_bps": 5.0, "avg_volume": 500_000, "correlation_spy": 0.2,
                          "rt_hist_vol": 0.2, "edge_bps": 20.0, "est_cost_bps": 3.0,
                          "shortable": True, "halted": False}, state)
    assert len(r.deltas) == 16, f"expected 16 checks, got {sorted(r.deltas.keys())}"
    assert all(isinstance(v, float) for v in r.deltas.values())


def test_no_hard_gates_except_halt():
    ra = RiskArbiter()
    state = PortfolioState(equity_gbp=20000, hwm_gbp=20000, consecutive_losses=3)
    r = ra.evaluate(0.1, {"halted": True, "shortable": False, "spread_bps": 200}, state)
    # Even with worst-case features, we return a RiskEvaluation — never None.
    assert r is not None
    assert r.halt is False


def test_halt_on_8_consecutive_losses():
    ra = RiskArbiter()
    state = PortfolioState(consecutive_losses=8)
    r = ra.evaluate(0.7, {}, state)
    assert r.halt is True


def test_confidence_falls_with_drawdown():
    ra = RiskArbiter()
    low_dd = PortfolioState(equity_gbp=20000, hwm_gbp=20000)
    high_dd = PortfolioState(equity_gbp=18000, hwm_gbp=20000)
    r1 = ra.evaluate(0.7, {}, low_dd)
    r2 = ra.evaluate(0.7, {}, high_dd)
    assert r2.final_confidence < r1.final_confidence
