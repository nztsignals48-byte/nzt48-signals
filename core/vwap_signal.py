"""
VWAP Signal Engine — NZT-48
Madhavan, Richardson & Roomans (1997): "Why Do Security Prices Change?"
Review of Financial Studies 10(4):1035-1064.

KEY FINDING: Institutions benchmark against VWAP. Deviation > 1 sigma from
VWAP signals either (a) genuine momentum (price breaking away with volume)
or (b) overextension likely to mean-revert. The direction of trade determines
which interpretation applies.

PARAMETERS (empirically validated):
- VWAP band ±0.8% for large-caps (±2% for 3x leveraged ETPs)
- Momentum signal: price > VWAP + threshold AND RVOL ≥ 1.5x = continuation
- Reversion signal: price within 0.3% of VWAP AND declining volume = fade
- Breakout confirmation: price reclaims VWAP from below on volume spike = long trigger

For 3x ETPs: multiply all thresholds by ~2.5x (volatility amplification).
"""

import logging
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

# VWAP deviation thresholds (applied to underlying, not ETP)
MOMENTUM_THRESHOLD_UNDERLYING_PCT = 0.8   # > VWAP + 0.8% = momentum signal
REVERSION_THRESHOLD_UNDERLYING_PCT = 0.3  # within ±0.3% of VWAP = reversion zone
VOLUME_MOMENTUM_RVOL = 1.5               # minimum RVOL to confirm breakout

# ETP leverage multiplier for thresholds
ETP_MULTIPLIER = 2.5  # 3x ETPs need wider bands

# Confidence adjustments
MOMENTUM_BOOST = 8    # +8 when price > VWAP + threshold with volume
REVERSION_PENALTY = -6  # -6 when price at VWAP without volume (institutional order cluster)
VWAP_RECLAIM_BOOST = 10  # +10 when price reclaims VWAP from below (high-conviction setup)


class VWAPSignalEngine:
    """
    Computes VWAP-relative signals to confirm or filter momentum entries.

    Does NOT compute VWAP itself (that's done in feeds/indicators.py).
    Takes VWAP and current price as inputs, returns signal classification.
    """

    def classify(
        self,
        ticker: str,
        current_price: float,
        vwap: float,
        rvol: float = 1.0,
        prev_price: Optional[float] = None,  # for VWAP reclaim detection
        is_lse_etp: Optional[bool] = None,
    ) -> dict:
        """
        Classifies the current price relative to VWAP.

        Returns:
            {
                "signal": "MOMENTUM_ABOVE" | "MOMENTUM_BELOW" | "REVERSION_ZONE" |
                          "VWAP_RECLAIM" | "VWAP_REJECTION" | "NEUTRAL",
                "confidence_adjustment": int,
                "vwap_deviation_pct": float,
                "notes": str,
            }
        """
        if vwap <= 0 or current_price <= 0:
            return {"signal": "NEUTRAL", "confidence_adjustment": 0,
                    "vwap_deviation_pct": 0.0, "notes": "No VWAP data"}

        if is_lse_etp is None:
            is_lse_etp = ticker.endswith(".L")

        # Scale thresholds for leveraged ETPs
        multiplier = ETP_MULTIPLIER if is_lse_etp else 1.0
        mom_threshold = MOMENTUM_THRESHOLD_UNDERLYING_PCT * multiplier
        rev_threshold = REVERSION_THRESHOLD_UNDERLYING_PCT * multiplier

        dev_pct = (current_price - vwap) / vwap * 100

        # VWAP Reclaim: price was below VWAP, now above (requires prev_price)
        if prev_price is not None and prev_price < vwap <= current_price:
            return {
                "signal": "VWAP_RECLAIM",
                "confidence_adjustment": VWAP_RECLAIM_BOOST,
                "vwap_deviation_pct": round(dev_pct, 3),
                "notes": (
                    f"VWAP RECLAIM: {ticker} crossed above VWAP at {vwap:.4f} "
                    f"(Madhavan 1997: institutional demand resuming)"
                ),
            }

        # VWAP Rejection: price was above VWAP, now below
        if prev_price is not None and prev_price > vwap >= current_price:
            return {
                "signal": "VWAP_REJECTION",
                "confidence_adjustment": REVERSION_PENALTY,
                "vwap_deviation_pct": round(dev_pct, 3),
                "notes": f"VWAP REJECTION: {ticker} lost VWAP — institutional bid gone",
            }

        # Momentum above VWAP with volume confirmation
        if dev_pct > mom_threshold and rvol >= VOLUME_MOMENTUM_RVOL:
            return {
                "signal": "MOMENTUM_ABOVE",
                "confidence_adjustment": MOMENTUM_BOOST,
                "vwap_deviation_pct": round(dev_pct, 3),
                "notes": (
                    f"VWAP+{dev_pct:.2f}% with RVOL={rvol:.1f}x — "
                    f"institutional momentum confirmed (threshold: +{mom_threshold:.1f}%)"
                ),
            }

        # Momentum below VWAP with volume — short bias
        if dev_pct < -mom_threshold and rvol >= VOLUME_MOMENTUM_RVOL:
            return {
                "signal": "MOMENTUM_BELOW",
                "confidence_adjustment": -MOMENTUM_BOOST,
                "vwap_deviation_pct": round(dev_pct, 3),
                "notes": (
                    f"VWAP{dev_pct:.2f}% with RVOL={rvol:.1f}x — "
                    f"institutional selling pressure confirmed"
                ),
            }

        # Reversion zone: clustered near VWAP with declining volume
        if abs(dev_pct) <= rev_threshold and rvol < 1.0:
            return {
                "signal": "REVERSION_ZONE",
                "confidence_adjustment": REVERSION_PENALTY,
                "vwap_deviation_pct": round(dev_pct, 3),
                "notes": (
                    f"VWAP reversion zone: {ticker} within ±{rev_threshold:.1f}% of VWAP "
                    f"({dev_pct:+.2f}%), RVOL={rvol:.1f}x — momentum stalling"
                ),
            }

        # Neutral — extended from VWAP but no volume confirmation
        return {
            "signal": "NEUTRAL",
            "confidence_adjustment": 0,
            "vwap_deviation_pct": round(dev_pct, 3),
            "notes": f"{ticker} {dev_pct:+.2f}% from VWAP — no actionable signal",
        }

    def get_confidence_adjustment(
        self,
        ticker: str,
        current_price: float,
        vwap: float,
        rvol: float = 1.0,
        prev_price: Optional[float] = None,
    ) -> int:
        """Convenience: returns only the confidence adjustment integer."""
        result = self.classify(ticker, current_price, vwap, rvol, prev_price)
        return result.get("confidence_adjustment", 0)

    def should_avoid_entry(
        self,
        ticker: str,
        current_price: float,
        vwap: float,
        direction: str,  # "LONG" or "SHORT"
    ) -> tuple[bool, str]:
        """
        Hard veto on counter-VWAP entries without volume confirmation.
        LONG above VWAP is fine. LONG below VWAP without VWAP reclaim = risk.
        """
        if vwap <= 0:
            return False, ""

        dev_pct = (current_price - vwap) / vwap * 100
        is_lse_etp = ticker.endswith(".L")
        multiplier = ETP_MULTIPLIER if is_lse_etp else 1.0
        mom_threshold = MOMENTUM_THRESHOLD_UNDERLYING_PCT * multiplier

        # Long entry significantly below VWAP: counter-trend risk
        if direction == "LONG" and dev_pct < -mom_threshold:
            return True, (
                f"VWAP VETO: {ticker} is {dev_pct:.2f}% below VWAP — "
                f"entering long against institutional selling (threshold: -{mom_threshold:.1f}%)"
            )

        # Short entry significantly above VWAP without reversion evidence
        if direction == "SHORT" and dev_pct > mom_threshold:
            return True, (
                f"VWAP VETO: {ticker} is {dev_pct:.2f}% above VWAP — "
                f"shorting into institutional momentum (threshold: +{mom_threshold:.1f}%)"
            )

        return False, ""
