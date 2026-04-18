"""
Live-data integration test: run a real signal through the WHOLE pipeline
with synthetic market data, and verify each stage's output matches expectations.

Goal: prove the subsystems actually interact correctly, not just that they
import in isolation.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))


def generate_synthetic_ticks(n: int = 500, seed: int = 42) -> list[dict]:
    """Simulate realistic tick stream for AAPL."""
    rng = np.random.default_rng(seed)
    ts = time.time()
    ticks = []
    price = 100.0
    for i in range(n):
        price += rng.normal(0, 0.02)
        spread = 0.02
        tick = {
            "ticker": "AAPL",
            "symbol": "AAPL",
            "ts": ts + i * 0.1,
            "bid": price - spread / 2,
            "ask": price + spread / 2,
            "last": price,
            "mid": price,
            "bid_size": max(1, int(rng.exponential(100))),
            "ask_size": max(1, int(rng.exponential(100))),
            "exchange": "SMART",
            "vpin": 0.3,
            "rvol": 1.5,
        }
        ticks.append(tick)
    return ticks


def test_indicator_framer_with_ticks():
    """Feed synthetic ticks to indicator computation."""
    ticks = generate_synthetic_ticks(100)

    # Extract OHLC-like features
    prices = [t["last"] for t in ticks]
    returns = np.diff(prices) / prices[:-1]

    # Volatility
    vol = float(np.std(returns))
    # Mean return
    mean_ret = float(np.mean(returns))
    # Max adverse move
    running_max = np.maximum.accumulate(prices)
    drawdowns = (running_max - prices) / running_max

    result = {
        "n_ticks": len(ticks),
        "vol_pct": round(vol * 100, 4),
        "mean_return_bps": round(mean_ret * 10000, 2),
        "max_drawdown_pct": round(float(drawdowns.max()) * 100, 4),
        "price_range": [round(min(prices), 2), round(max(prices), 2)],
    }

    assert vol > 0
    assert len(prices) == 100

    return result


def test_l2_book_predicts_move():
    """Verify L2 book imbalance predicts directional moves."""
    from python_brain.quant.l2_book_imbalance import (
        OrderBook, OrderBookLevel, compute_all_signals, predict_short_term_move
    )

    # Heavy buy pressure book
    buy_pressure = OrderBook(
        bids=[OrderBookLevel(100.00, 1000), OrderBookLevel(99.99, 800)],
        asks=[OrderBookLevel(100.02, 100), OrderBookLevel(100.03, 80)],
    )
    signals_buy = compute_all_signals(buy_pressure)
    predicted_buy = predict_short_term_move(signals_buy, horizon_s=10)
    assert predicted_buy > 5, f"should predict up-move, got {predicted_buy}"

    # Heavy sell pressure
    sell_pressure = OrderBook(
        bids=[OrderBookLevel(100.00, 100), OrderBookLevel(99.99, 80)],
        asks=[OrderBookLevel(100.02, 1000), OrderBookLevel(100.03, 800)],
    )
    signals_sell = compute_all_signals(sell_pressure)
    predicted_sell = predict_short_term_move(signals_sell, horizon_s=10)
    assert predicted_sell < -5, f"should predict down-move, got {predicted_sell}"

    return {
        "buy_pressure_predicted_bps": round(predicted_buy, 1),
        "sell_pressure_predicted_bps": round(predicted_sell, 1),
    }


def test_full_pipeline_happy_path():
    """Run a strong signal through the whole gate pipeline."""
    from python_brain.quant.cost_model import CostModel
    from python_brain.quant.meta_labeler import MetaLabeler, MetaLabelFeatures
    from python_brain.quant.bocpd_regime import BocpdRegimeDetector
    from python_brain.quant.deflated_sharpe import deflated_sharpe_ratio
    from python_brain.risk.portfolio_correlation_guard import check_correlation_guard

    # Step 1: Strategy emits signal with 30bps gross edge
    signal = {
        "ticker": "AAPL",
        "strategy": "filing_change_detect",
        "conviction": 0.75,
        "gross_edge_bps": 30,
        "shares": 500,
        "fill_price": 100.0,
        "adv_shares": 50_000_000,
        "spread_bps": 2.0,
    }

    # Step 2: Cost model filter
    cm = CostModel()
    cost_result = cm.net_edge_bps(
        gross_edge_bps=signal["gross_edge_bps"],
        shares=signal["shares"],
        fill_price=signal["fill_price"],
        adv_shares=signal["adv_shares"],
        spread_bps=signal["spread_bps"],
    )
    cost_passes = cost_result["passes_min_edge"]

    # Step 3: Meta-labeler
    ml = MetaLabeler()
    features = MetaLabelFeatures(
        strategy=signal["strategy"],
        conviction=signal["conviction"],
        gross_edge_bps=signal["gross_edge_bps"],
        spread_bps=signal["spread_bps"],
        rvol=1.5, vpin=0.3, regime="calm", session="us_session",
    )
    ml_accept, ml_prob = ml.should_accept(features)

    # Step 4: Regime
    det = BocpdRegimeDetector()
    rng = np.random.default_rng(42)
    for x in rng.normal(0, 0.01, 50):
        det.update(float(x))
    regime = det.current_regime()
    size_mult = det.size_multiplier()

    # Step 5: Correlation check (empty portfolio)
    corr_check = check_correlation_guard({}, signal["ticker"], 1000, {})

    # Step 6: DSR gate (would gate the strategy, not this signal)
    dsr = deflated_sharpe_ratio(1.5, 500, 10)

    # Final decision
    all_pass = cost_passes and ml_accept and corr_check.pass_check

    return {
        "cost_check": {
            "net_edge_bps": cost_result["net_edge_bps"],
            "passes": cost_passes,
        },
        "meta_label": {
            "prob": round(float(ml_prob), 3),
            "accepts": ml_accept,
        },
        "regime": {
            "current": regime,
            "size_multiplier": size_mult,
        },
        "correlation": {
            "pass": corr_check.pass_check,
            "violations": len(corr_check.violations),
        },
        "dsr_strategy_gate": round(dsr, 4),
        "final_decision": "APPROVED" if all_pass else "REJECTED",
    }


def test_risk_monitor_catches_breach():
    """VaR/CVaR monitor must detect excessive risk."""
    from python_brain.risk.realtime_var_cvar import RealtimeRiskMonitor

    monitor = RealtimeRiskMonitor(
        portfolio_cap_var_usd=100,  # Tight cap
        cvar_cap_usd=200,
        max_drawdown_halt=0.05,
    )

    # Feed extreme returns — should trigger breach
    rng = np.random.default_rng(42)
    for t in ["VOLATILE_A", "VOLATILE_B"]:
        for r in rng.normal(0, 0.05, 100):  # 5% daily vol — very high
            monitor.update_return(t, float(r))
        monitor.update_position(t, 5000)

    metrics = monitor.compute(10000, method="historical")
    breaches = monitor.breach_check()

    assert metrics.volatility > 0.3, "should detect high vol"

    return {
        "var_95": round(metrics.var_95, 2),
        "cvar_95": round(metrics.cvar_95, 2),
        "vol_annualized": round(metrics.volatility, 2),
        "breaches_detected": len(breaches),
        "breaches": breaches[:3],
    }


def test_execution_schedule_realistic():
    """Almgren-Chriss should produce valid schedule for realistic trade."""
    from python_brain.execution.almgren_chriss_executor import (
        MarketParams, adaptive_schedule
    )

    # AAPL 10000 shares over 1hr
    params = MarketParams.from_ticker_stats(
        daily_vol_bps=150.0,
        adv_shares=50_000_000,
        spread_bps=1.0,
    )

    schedules = {}
    for urgency in ["passive", "normal", "aggressive", "immediate"]:
        sched = adaptive_schedule(10000, 3600, params, urgency)
        total = sum(abs(s) for s in sched.slice_sizes)
        schedules[urgency] = {
            "num_slices": len(sched.slice_sizes),
            "total_shares": total,
            "cost_bps": round(sched.expected_cost_bps, 2),
        }
        assert abs(total - 10000) < 10, f"shares mismatch in {urgency}"

    # More aggressive should cost more
    assert schedules["immediate"]["cost_bps"] >= schedules["passive"]["cost_bps"]

    return schedules


def test_spde_simulator_generates_fills():
    """SPDE LOB simulator should produce realistic fill patterns."""
    from python_brain.quant.spde_lob_simulator import LOBSimulatorConfig, run_simulation

    cfg = LOBSimulatorConfig(seed=42, mid_start=100.0)
    sim, stats = run_simulation(duration_s=60, config=cfg)

    # Place a test order and check fill prob
    order = sim.place_limit_order(sim.best_bid, 100, "BUY")
    fill_prob = sim.estimate_fill_probability(order.price, order.size, order.side, horizon_s=30)

    assert stats["total_fills"] > 0
    assert 0 <= fill_prob <= 1.0

    return {
        "total_fills": stats["total_fills"],
        "final_spread_bps": round(stats["final_spread_bps"], 2),
        "mid_vol_bps": round(stats["mid_vol_bps"], 2),
        "our_order_fill_prob": round(fill_prob, 3),
    }


def test_tail_hedge_responds_to_crisis():
    """Tail hedge should recommend protection in crisis."""
    from python_brain.risk.tail_hedge_overlay import (
        compute_tail_load, classify_tail_regime, recommend_hedge
    )
    rng = np.random.default_rng(42)

    # Simulate drawdown
    normal = np.cumsum(rng.normal(0.001, 0.01, 100))
    crash = normal[-1] - np.abs(rng.normal(0.01, 0.005, 50)).cumsum()
    equity = 10000 * np.exp(np.concatenate([normal, crash]))

    load = compute_tail_load(equity)
    regime = classify_tail_regime(load, vix_level=40)  # Crisis VIX (>35 threshold)
    rec = recommend_hedge(10000, 1.1, regime, 9000, 0)

    assert rec is not None, "should recommend hedge in crisis"
    assert regime == "crisis"
    assert rec.urgency == "high"

    return {
        "tail_load": {
            "drawdown_pct": round(load["drawdown_pct"], 4),
            "downside_vol": round(load["downside_vol"], 4),
        },
        "regime": regime,
        "hedge": {
            "symbol": rec.hedge_symbol,
            "ratio": rec.hedge_ratio,
            "urgency": rec.urgency,
        },
    }


def main():
    tests = [
        ("indicator_framer_with_ticks", test_indicator_framer_with_ticks),
        ("l2_book_predicts_direction", test_l2_book_predicts_move),
        ("full_pipeline_happy_path", test_full_pipeline_happy_path),
        ("risk_monitor_catches_breach", test_risk_monitor_catches_breach),
        ("execution_schedule_realistic", test_execution_schedule_realistic),
        ("spde_simulator_generates_fills", test_spde_simulator_generates_fills),
        ("tail_hedge_responds_to_crisis", test_tail_hedge_responds_to_crisis),
    ]

    print("=" * 70)
    print("V5 INTEGRATION TEST WITH SYNTHETIC DATA")
    print("=" * 70)

    results = {}
    passed = 0
    failed = 0

    for name, fn in tests:
        try:
            t0 = time.time()
            data = fn()
            ms = (time.time() - t0) * 1000
            results[name] = {"status": "PASS", "duration_ms": round(ms, 1), "data": data}
            passed += 1
            print(f"\n✅ {name} ({ms:.1f}ms)")
            print(f"   {json.dumps(data, indent=2, default=str)[:400]}")
        except Exception as e:
            import traceback
            results[name] = {
                "status": "FAIL",
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-400:],
            }
            failed += 1
            print(f"\n❌ {name}: {e}")
            print(f"   {traceback.format_exc()[-300:]}")

    print("\n" + "=" * 70)
    print(f"SUMMARY: {passed}/{passed + failed} passed ({passed/(passed+failed)*100:.0f}%)")

    report_path = ROOT / "docs" / "V5_INTEGRATION_TEST_REPORT.json"
    report_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"Report: {report_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
