"""Book 49: Per-instrument liquidity scoring.

Score 0-100 computed nightly, consumed by bridge.py for Kelly scaling.
80-100=1.0x, 60-79=0.8x, 40-59=0.6x, 20-39=0.4x, 0-19=0.2x.

Consumed by: bridge.py _apply_adjustments() — scales Kelly by liquidity tier.
Written by: nightly_v6.py liquidity scoring step.
"""

import json
import os

_SCORES_PATH = "/app/data/liquidity_scores.json"
_cache = None
_cache_mtime = 0


def _load_scores():
    """Load liquidity scores from disk, with mtime-based caching."""
    global _cache, _cache_mtime
    if not os.path.exists(_SCORES_PATH):
        return {}
    try:
        mtime = os.path.getmtime(_SCORES_PATH)
        if _cache is not None and mtime == _cache_mtime:
            return _cache
        with open(_SCORES_PATH) as f:
            _cache = json.load(f)
        _cache_mtime = mtime
        return _cache
    except Exception:
        return {}


def get_liquidity_score(symbol):
    """Get liquidity score for a symbol (0-100). Returns 50 if unknown."""
    scores = _load_scores()
    return scores.get(symbol, 50)


def get_liquidity_scale(symbol):
    """Get Kelly scaling factor based on liquidity score."""
    score = get_liquidity_score(symbol)
    if score >= 80:
        return 1.0
    if score >= 60:
        return 0.8
    if score >= 40:
        return 0.6
    if score >= 20:
        return 0.4
    return 0.2


def compute_liquidity_score(adv_gbp, avg_spread_pct, depth_gbp=None, rvol_stability=None):
    """Compute a liquidity score from raw metrics. Used by nightly pipeline.

    Score components:
    - ADV (40 points): log-scaled, >£1M=40, <£10K=0
    - Spread (30 points): <10bp=30, >100bp=0
    - Depth (15 points): >£50K=15, <£5K=0
    - RVOL stability (15 points): low CV of RVOL = stable liquidity
    """
    import math

    # ADV score (0-40)
    if adv_gbp <= 0:
        adv_score = 0
    else:
        # Log scale: £10K=0, £100K=20, £1M=40
        adv_score = min(40, max(0, int(20 * math.log10(max(adv_gbp, 1) / 10000))))

    # Spread score (0-30)
    if avg_spread_pct <= 0:
        spread_score = 15  # Unknown → neutral
    else:
        # Linear: 0bp=30, 100bp=0
        spread_score = min(30, max(0, int(30 * (1 - avg_spread_pct / 1.0))))

    # Depth score (0-15)
    if depth_gbp is None or depth_gbp <= 0:
        depth_score = 7  # Unknown → neutral
    else:
        depth_score = min(15, max(0, int(15 * min(depth_gbp / 50000, 1.0))))

    # RVOL stability (0-15)
    if rvol_stability is None:
        stability_score = 7
    else:
        # Lower CV = more stable = higher score
        stability_score = min(15, max(0, int(15 * (1 - min(rvol_stability, 1.0)))))

    return adv_score + spread_score + depth_score + stability_score
