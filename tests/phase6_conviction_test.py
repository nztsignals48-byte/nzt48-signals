"""Phase 6 acceptance: ConvictionEngine.rank_signals() IS CALLED + LLM clip works + A/B harness functions."""
from python_brain.conviction_engine import ConvictionEngine, StrategyView
from python_brain.core.ab_harness import AgentABHarness


def _v(name: str, conv: float, edge: float = 30, risk: float = 10) -> StrategyView:
    return StrategyView(signal_id=f"id-{name}", strategy=name, ticker="AAPL",
                        default_conviction=conv, edge_estimate_bps=edge, risk_bps=risk,
                        features={"ibs": 0.2})


def test_rank_signals_sorts_by_score():
    ce = ConvictionEngine(max_per_batch=5, min_composite_score=0.0)
    out = ce.rank_signals([_v("a", 0.5, edge=10), _v("b", 0.9, edge=50), _v("c", 0.7, edge=20)])
    assert [r.strategy for r in out[:3]] == ["b", "c", "a"]
    assert all(r.rank == i for i, r in enumerate(out))


def test_llm_clip_range():
    ce = ConvictionEngine(min_composite_score=0.0)
    r_over = ce.rank_signals([_v("x", 0.5)], llm_deltas={"id-x": 200})[0]
    r_under = ce.rank_signals([_v("x", 0.5)], llm_deltas={"id-x": -300})[0]
    assert r_over.llm_delta_pp == 15.0
    assert r_under.llm_delta_pp == -30.0


def test_ab_harness_needs_200_samples():
    h = AgentABHarness("news_reactor", min_samples=200)
    for _ in range(50):
        h.record(0.5, 0.6, 5.0, "steady")
    assert not h.can_report_delta()
    for _ in range(150):
        h.record(0.5, 0.6, 5.0, "steady")
    assert h.can_report_delta()
    ci = h.delta_with_ci(bootstrap_n=200)
    assert ci is not None
    mean, lo, hi = ci
    assert lo <= mean <= hi


def test_ab_harness_alpha_positive_detection():
    h = AgentABHarness("news_reactor")
    for _ in range(400):
        h.record(0.5, 0.7, 5.0, "steady")   # consistent positive pnl
    assert h.is_alpha_positive() is True

    h2 = AgentABHarness("flat_agent")
    import random
    random.seed(1)
    for _ in range(400):
        h2.record(0.5, 0.7, random.gauss(0, 5), "steady")
    ci = h2.delta_with_ci(bootstrap_n=500)
    # For near-zero mean, CI should straddle zero (lo <= 0)
    _, lo, _ = ci
    assert lo <= 0.0
