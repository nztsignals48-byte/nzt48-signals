"""Golden Signal test. Injects a known synthetic stream; every core strategy must emit one signal.

Phase 5 fills with tick-by-tick injection. Scaffold here proves ConvictionEngine wiring.
"""
from python_brain.conviction_engine import ConvictionEngine, StrategyView


def _view(name: str) -> StrategyView:
    return StrategyView(
        signal_id=f"sig-{name}",
        strategy=name,
        ticker="AAPL",
        default_conviction=0.7,
        edge_estimate_bps=30.0,
        risk_bps=10.0,
        features={"ibs": 0.1, "atr": 1.5},
    )


def test_conviction_engine_ranks_and_emits():
    views = [_view(n) for n in [
        "sentiment_long_short", "filing_change_detect", "index_recon",
        "earnings_pattern", "overnight_return", "ibs_mean_reversion",
    ]]
    ce = ConvictionEngine(max_per_batch=5, min_composite_score=1.0)
    ranked = ce.rank_signals(views)
    assert len(ranked) == 5
    assert all(r.rank >= 0 for r in ranked)
    assert ranked[0].score >= ranked[-1].score


def test_llm_clip_applies():
    ce = ConvictionEngine(min_composite_score=0.0)
    [r] = ce.rank_signals([_view("test")], llm_deltas={"sig-test": 999.0})
    assert r.llm_delta_pp == 15.0  # clipped at +15 pp
