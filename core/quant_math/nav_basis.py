"""
NAV Basis Arbitrage Gate -- front-running the Authorized Participants.
Requires Bloomberg IIV data (every 15 seconds).
"""
from __future__ import annotations
import logging

logger = logging.getLogger("nzt48.nav_basis")


def calculate_basis(current_price: float, iiv: float) -> float:
    """Basis = (Price - IIV) / IIV. Positive = premium, negative = discount."""
    if iiv <= 0:
        return 0.0
    return (current_price - iiv) / iiv


def nav_basis_gate(ticker: str, current_price: float, iiv: float,
                   direction: str) -> tuple[bool, str]:
    """
    NAV Basis Arbitrage Gate.

    Rules:
    - LONG: VETO when Basis > +0.002 (+0.2% premium). APs will SELL.
    - LONG: PASS when Basis < -0.001 (discount). APs will BUY.
    - SHORT: Reverse logic.
    """
    basis = calculate_basis(current_price, iiv)

    if direction.upper() == "LONG":
        if basis > 0.002:
            msg = f"NAV_PREMIUM_VETO: {ticker} basis=+{basis*100:.2f}% -- APs will dump supply"
            logger.warning(msg)
            return False, msg
        if basis < -0.001:
            msg = f"NAV_DISCOUNT_PASS: {ticker} basis={basis*100:.2f}% -- APs buying to close"
            return True, msg
        return True, f"NAV_NEUTRAL: {ticker} basis={basis*100:.2f}%"
    else:
        if basis < -0.002:
            msg = f"NAV_DISCOUNT_VETO: {ticker} basis={basis*100:.2f}% -- APs will buy"
            logger.warning(msg)
            return False, msg
        return True, f"NAV_PASS: {ticker} basis={basis*100:.2f}%"
