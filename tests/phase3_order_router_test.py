"""Phase 3 acceptance: order router picks the right tier per strategy + urgency."""
from python_brain.engine.order_router import AlgoTier, pick_tier


def test_stop_always_market():
    d = pick_tier("sentiment_long_short", is_stop=True, adv_pct=0.0001)
    assert d.tier == AlgoTier.MARKET


def test_high_adv_uses_arrival_price():
    d = pick_tier("sentiment_long_short", is_stop=False, adv_pct=0.05)
    assert d.tier == AlgoTier.ARRIVAL_PRICE


def test_sentiment_is_urgent():
    d = pick_tier("sentiment_long_short", is_stop=False, adv_pct=0.0001)
    assert d.tier == AlgoTier.URGENT


def test_overnight_is_peg_mid():
    d = pick_tier("overnight_return", is_stop=False, adv_pct=0.0001)
    assert d.tier == AlgoTier.PEG_MID


def test_unknown_strategy_defaults_patient():
    d = pick_tier("unknown_xyz", is_stop=False, adv_pct=0.0001)
    assert d.tier == AlgoTier.PATIENT
