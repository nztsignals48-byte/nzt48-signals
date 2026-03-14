"""
Lead-Lag Latency Arbitrage -- NQ -> QQQ3.L.
Requires Bloomberg or CME NQ tick stream.
Thomas & Zhang (2008).
"""
from __future__ import annotations
import logging

logger = logging.getLogger("nzt48.lead_lag")


def detect_lead_signal(nq_tick_change_bps: float, etp_price: float,
                       etp_last_change_bps: float, leverage: float) -> dict:
    """If NQ prints a massive move but QQQ3.L hasn't reflected it yet,
    you are looking into the future.
    """
    expected_etp_change = nq_tick_change_bps * leverage
    actual_etp_change = etp_last_change_bps
    gap = expected_etp_change - actual_etp_change

    if abs(gap) > 10:  # > 10 bps gap
        signal = "LONG" if gap > 0 else "SHORT"
        confidence = min(0.9, abs(gap) / 50)
        logger.info("LEAD_LAG: gap=%.1f bps -> %s (conf=%.2f)", gap, signal, confidence)
        return {
            "signal": signal,
            "gap_bps": gap,
            "confidence": confidence,
            "source": "LEAD_LAG_NQ",
        }
    return {"signal": "NONE"}
