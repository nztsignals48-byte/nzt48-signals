"""Phase A-Q comprehensive test — validates every module built in this session."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path("/Users/rr/aegis-v5")
sys.path.insert(0, str(ROOT))


def test_phase_a_universe():
    """Phase A: Universe is 2500+."""
    import re
    text = (ROOT / "config/contracts.toml").read_text()
    n = len(re.findall(r"^\[\[contracts\]\]", text, re.MULTILINE))
    assert n >= 2500, f"expected >= 2500, got {n}"
    print(f"  ✅ Universe: {n} contracts")


def test_phase_b_measurement():
    """Phase B: MinTRL, paper_haircut, drift_sentry, bootstrap_ci."""
    from python_brain.quant.min_trl import promotion_ready
    r = promotion_ready(sharpe=1.5, n_observations=300, n_trials=5)
    assert "dsr" in r and "mintrl" in r
    print(f"  ✅ MinTRL: dsr={r['dsr']:.3f} mintrl={r['mintrl']:.0f}")

    from python_brain.execution.paper_haircut import apply_paper_haircut
    r = apply_paper_haircut(100.0, "BUY", "MKT", "SMART")
    assert r.fill_price_adjusted > 100.0
    print(f"  ✅ Paper haircut: {r.haircut_bps} bps")

    from python_brain.quant.drift_sentry import analyze
    rng = np.random.default_rng(42)
    r = analyze({"x": rng.normal(0, 1, 100).tolist()}, {"x": rng.normal(0, 1, 100).tolist()})
    assert hasattr(r, "alerts")
    print(f"  ✅ Drift sentry: {len(r.alerts)} alerts")

    from python_brain.quant.bootstrap_ci import bootstrap_sharpe
    r = bootstrap_sharpe(rng.normal(0.0005, 0.01, 200), n_bootstrap=100)
    assert r.ci_low <= r.estimate <= r.ci_high
    print(f"  ✅ Bootstrap CI: {r.estimate:.2f} [{r.ci_low:.2f}, {r.ci_high:.2f}]")


def test_phase_c_adaptive_allocators():
    from python_brain.quant.fdr_allocator import FDRAllocator
    a = FDRAllocator()
    for _ in range(50):
        a.update("good", 3.0)
        a.update("bad", -1.0)
    promotable = a.promotable()
    print(f"  ✅ FDR: promotable={promotable}")

    from python_brain.quant.covariance_adjusted_kelly import kelly_from_fills
    rng = np.random.default_rng(42)
    kellys = kelly_from_fills({
        "s1": rng.normal(0.001, 0.01, 60),
        "s2": rng.normal(-0.0005, 0.015, 60),
    })
    print(f"  ✅ Kelly: {kellys}")

    from python_brain.quant.gmm_regime import GMMRegimeClassifier
    clf = GMMRegimeClassifier(n_regimes=4)
    X = rng.normal(size=(100, 6))
    clf.fit(X)
    r = clf.classify([0.001, 15, 0, 0, 0.001, 0.01])
    print(f"  ✅ GMM regime: {r.regime_label}")


def test_phase_e_microstructure():
    from python_brain.quant.walk_forward_oos import walk_forward, summarize
    rng = np.random.default_rng(42)
    rets = rng.normal(0.0005, 0.012, 200)
    windows = walk_forward(rets)
    s = summarize(windows)
    assert s["n_windows"] > 0
    print(f"  ✅ Walk-forward: {s['n_windows']} windows, consistency {s['consistency_pct']:.0f}%")

    from python_brain.quant.ensemble_entry import EnsembleEntry
    ee = EnsembleEntry()
    ee.add_signal("AAPL", "BUY", "SentimentLongShort", 0.7)
    ee.add_signal("AAPL", "BUY", "FilingChangeDetect", 0.8)
    r = ee.ensemble("AAPL", "BUY")
    assert r.n_contributing == 2
    print(f"  ✅ Ensemble: conv={r.ensemble_conviction:.3f}")


def test_phase_f_llm_upgrades():
    from python_brain.intelligence.feature_schema_lock import validate_features
    ok, errs = validate_features({
        "conviction": 0.7, "gross_edge_bps": 15, "spread_bps": 3,
        "rvol": 1.2, "vpin": 0.3, "regime": "calm", "session": "us_session",
    })
    assert ok
    print(f"  ✅ Feature schema: valid")

    ok, errs = validate_features({"conviction": 1.5, "regime": "invalid"})
    assert not ok
    print(f"  ✅ Feature schema: caught {len(errs)} errors on bad input")


def test_phase_k_execution_upgrades():
    from python_brain.execution.ioc_fok_support import translate, is_aggressive
    s = translate("IOC")
    assert s.tif == "IOC"
    assert is_aggressive("FOK")
    print(f"  ✅ IOC/FOK support")

    from python_brain.execution.account_size_aware_ac import scale_for_account
    r = scale_for_account(10000, 100.0, 10_000)
    assert r.effective_shares < 10000
    print(f"  ✅ AC account scaling: requested 10000 -> {r.effective_shares}")

    from python_brain.infra.ibkr_connection_pool import snapshot
    snap = snapshot()
    assert "can_spawn" in snap
    print(f"  ✅ IBKR connection pool: can_spawn={snap['can_spawn']}")


def test_phase_l_risk():
    from python_brain.risk.stress_replay_weekly import run_scenario
    r = run_scenario({"AAPL": 3000, "MSFT": 2000, "SH": 500}, 10000, "2008_oct")
    assert r.max_drawdown_pct >= 0
    print(f"  ✅ Stress replay 2008: dd={r.max_drawdown_pct:.2%}")

    from python_brain.risk.cross_portfolio_halt import HaltMonitor
    mon = HaltMonitor(kill_dd=0.08)
    assert not mon.update(10000)
    assert mon.update(9100)  # 9% DD → kill
    print(f"  ✅ Cross-portfolio halt fires at 9% DD")


def test_phase_m_regime_hierarchy():
    from python_brain.quant.sector_regime import SectorRegimeDetector
    det = SectorRegimeDetector()
    rng = np.random.default_rng(42)
    for _ in range(30):
        det.add_return("XLK", rng.normal(0, 0.02))
    r = det.classify("XLK")
    assert r.regime in ("calm", "trending", "choppy", "stressed", "crisis")
    print(f"  ✅ Sector regime: XLK = {r.regime} (vol {r.vol_annualized:.2%})")


def test_phase_q_eleven_out_of_ten():
    from python_brain.quant.strategy_synthesizer import identify_conditional_patterns
    rng = np.random.default_rng(42)
    fills = []
    for _ in range(300):
        fills.append({
            "strategy_name": "test_strat",
            "regime": rng.choice(["calm", "crisis"]),
            "session": rng.choice(["us_session", "overnight"]),
            "realized_pnl_bps": float(rng.normal(2, 10)),
        })
    proposals = identify_conditional_patterns(fills)
    assert isinstance(proposals, list)
    print(f"  ✅ Strategy synthesizer: {len(proposals)} proposals from 300 synthetic fills")

    from python_brain.quant.cross_exchange_arbitrage import detect_opportunities
    prices = {
        ("BHP", "SMART"): (60.00, "USD"),
        ("BHP", "ASX"): (80.00, "AUD"),  # A$80 = $52.80 USD → 12% arb
    }
    opps = detect_opportunities(prices)
    assert len(opps) > 0
    print(f"  ✅ Cross-exchange arb: {len(opps)} opportunities detected")

    from python_brain.quant.adversarial_self_test import synthesize_signals
    sigs = synthesize_signals(5)
    assert len(sigs) == 25  # 5 categories × 5
    print(f"  ✅ Adversarial synthesis: {len(sigs)} pathological signals across 5 categories")


def test_integration_pipeline():
    """End-to-end adaptive gate chain logic."""
    from python_brain.execution.paper_haircut import haircut_bps_for
    from python_brain.intelligence.feature_schema_lock import validate_features

    signal = {
        "signal_id": "test_1",
        "ticker": "AAPL",
        "side": "BUY",
        "conviction_score": 0.75,
        "strategy_name": "filing_change_detect",
        "expected_fill_price": 100.0,
        "exchange": "SMART",
        "feature_vector": {
            "gross_edge_bps": 25,
            "vpin": 0.3,
            "regime": "calm",
        },
    }

    # Simulate gate chain
    haircut = haircut_bps_for("MKT", signal["exchange"])
    gross = signal["feature_vector"]["gross_edge_bps"]
    net = gross - haircut
    assert net > 2.0
    print(f"  ✅ End-to-end: gross={gross} haircut={haircut} net={net} PASSES")


def run_all():
    print("=" * 70)
    print("V5 PHASE A-Q COMPREHENSIVE TEST")
    print("=" * 70)

    tests = [
        ("Phase A: Universe 2500+", test_phase_a_universe),
        ("Phase B: Measurement", test_phase_b_measurement),
        ("Phase C: Adaptive allocators", test_phase_c_adaptive_allocators),
        ("Phase E: Microstructure", test_phase_e_microstructure),
        ("Phase F: LLM upgrades", test_phase_f_llm_upgrades),
        ("Phase K: Execution upgrades", test_phase_k_execution_upgrades),
        ("Phase L: Risk", test_phase_l_risk),
        ("Phase M: Regime hierarchy", test_phase_m_regime_hierarchy),
        ("Phase Q: 11/10 layer", test_phase_q_eleven_out_of_ten),
        ("Integration: end-to-end pipeline", test_integration_pipeline),
    ]

    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"\n[{name}]")
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  ❌ ASSERT: {e}")
        except Exception as e:
            failed += 1
            print(f"  ❌ {type(e).__name__}: {e}")

    print(f"\n{'=' * 70}")
    print(f"RESULT: {passed}/{passed + failed} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
