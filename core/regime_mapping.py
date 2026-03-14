"""
NZT-48 Core -- Canonical Regime Mapping (W5)
Maps 5-state volatility regime to 8-state settings taxonomy.
Decision D1: Hybrid -- 5-state internal, 8-state for output.
Feature flag: regime_unification in settings.yaml
"""

import logging

logger = logging.getLogger("nzt48.core.regime_mapping")

# 5-state -> 8-state canonical mapping
# Each 5-state maps to ONE 8-state (no ambiguity)
REGIME_MAP: dict[str, str] = {
    "COMPRESSION": "RANGE_BOUND",
    "EXPANSION": "TRENDING_UP_STRONG",
    "BLOW_OFF": "HIGH_VOLATILITY",
    "EXHAUSTION": "TRENDING_DOWN_MOD",
    "BREAKDOWN": "RISK_OFF",
    # Fallbacks for edge cases
    "NEUTRAL": "RANGE_BOUND",
    "UNKNOWN": "RANGE_BOUND",
}

# Valid 8-state values (authoritative list)
VALID_8_STATE = {
    "TRENDING_UP_STRONG", "TRENDING_UP_MOD",
    "TRENDING_DOWN_STRONG", "TRENDING_DOWN_MOD",
    "RANGE_BOUND", "HIGH_VOLATILITY", "RISK_OFF", "SHOCK",
}

# Valid 5-state values
VALID_5_STATE = {"COMPRESSION", "EXPANSION", "BLOW_OFF", "EXHAUSTION", "BREAKDOWN"}


def map_regime(vol_regime: str, confidence: float = 0.5) -> str:
    """Map 5-state volatility regime to canonical 8-state output regime.

    Args:
        vol_regime: 5-state regime from volatility_regime.py
        confidence: Regime confidence [0.0-1.0] -- used for strong vs moderate distinction

    Returns:
        Canonical 8-state regime string
    """
    vol_regime_upper = (vol_regime or "UNKNOWN").upper().strip()

    # If already an 8-state value, pass through
    if vol_regime_upper in VALID_8_STATE:
        return vol_regime_upper

    base = REGIME_MAP.get(vol_regime_upper, "RANGE_BOUND")

    # Refine EXPANSION by confidence: high conf = STRONG, low = MOD
    if vol_regime_upper == "EXPANSION":
        if confidence >= 0.7:
            base = "TRENDING_UP_STRONG"
        else:
            base = "TRENDING_UP_MOD"

    # Refine EXHAUSTION by confidence
    if vol_regime_upper == "EXHAUSTION":
        if confidence >= 0.7:
            base = "TRENDING_DOWN_STRONG"
        else:
            base = "TRENDING_DOWN_MOD"

    # BLOW_OFF with very high confidence = SHOCK
    if vol_regime_upper == "BLOW_OFF" and confidence >= 0.85:
        base = "SHOCK"

    if vol_regime_upper not in REGIME_MAP and vol_regime_upper not in VALID_8_STATE:
        logger.warning("REGIME_MAP: unknown regime '%s', defaulting to RANGE_BOUND", vol_regime)

    return base


def detect_regime_contradiction(regime: str, drought: bool) -> tuple[bool, str]:
    """Detect contradictions between regime and drought state.

    EXPANSION + DROUGHT is a contradiction: strong trends should produce signals.

    Returns:
        (is_contradiction, explanation)
    """
    if drought and regime in ("TRENDING_UP_STRONG", "TRENDING_UP_MOD", "EXPANSION"):
        msg = f"REGIME_DROUGHT_CONTRADICTION: regime={regime} but drought=True -- strong trend should produce signals"
        logger.warning(msg)
        return True, msg
    return False, ""
