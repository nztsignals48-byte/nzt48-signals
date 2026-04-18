"""Phase 2B: quant_core outputs are CONSUMED by the risk arbiter, not just logged."""
from python_brain.engine.quant_core import QuantCore, regime_probs, cvar_95


def test_garch_produces_nonzero_vol_on_returns():
    q = QuantCore()
    s = None
    for i in range(120):
        price = 100 + i * 0.01 + ((i % 7) - 3) * 0.05
        s = q.on_tick("AAPL", price, 1_700_000_000_000_000_000 + i * 1_000_000_000)
    assert s is not None
    assert s.garch_vol_annualized > 0.0


def test_regime_probs_sum_to_one():
    p = regime_probs([0.001] * 30, vol=0.2)
    assert abs(sum(p) - 1.0) < 1e-6


def test_regime_crisis_on_high_vol():
    p = regime_probs([0.02, -0.03, 0.04, -0.05] * 10, vol=1.5)
    assert p[2] > 0.3   # crisis prob elevated


def test_cvar_95_is_negative_for_loss_tail():
    residuals = [-3, -2.5, -2, -1, -0.5, 0, 0, 0.5, 1, 2, 3] * 10
    c = cvar_95(residuals)
    assert c < 0


def test_kalman_residual_z_populates():
    q = QuantCore()
    for i in range(60):
        q.on_tick("AAPL", 100 + (i % 5) * 0.5, i * 1_000_000_000)
    last = q.on_tick("AAPL", 100 + 50, 60 * 1_000_000_000)   # spike
    assert abs(last.kalman_z) > 0.0


def test_quant_feeds_risk_arbiter():
    """Risk arbiter must accept quant fields and emit non-zero deltas when they're extreme."""
    from python_brain.engine.risk_arbiter import RiskArbiter
    from python_brain.engine.portfolio_state import PortfolioState

    ra = RiskArbiter(confidence_floor=0.55)
    state = PortfolioState(equity_gbp=20000, hwm_gbp=20000)

    benign = ra.evaluate(0.7, {"rt_hist_vol": 0.15, "kalman_z": 0.5}, state)
    harsh  = ra.evaluate(0.7, {"rt_hist_vol": 0.80, "kalman_z": 5.0, "correlation_spy": 0.95,
                                "halted": True, "edge_bps": 20, "est_cost_bps": 20, "spread_bps": 40}, state)
    assert harsh.final_confidence < benign.final_confidence
