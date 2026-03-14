"""
AEGIS K-12: Micro-Price / Order Book Imbalance (OBI) Calculation.

Volume-weighted mid-price that accounts for order book asymmetry.
If 10,000 shares on Bid and 100 on Ask, the true equilibrium price
is near the Ask (supply is scarce on the Ask side, so price is more
likely to move up).

The micro-price is a more accurate estimate of fair value than the
simple mid-price (bid + ask) / 2, because it incorporates the
information embedded in the relative sizes of the best bid and ask.

Reference:
    Gatheral, J. & Oomen, R. (2010). "Zero-intelligence realized
    variance estimation." Finance & Stochastics 14(2), 249-283.

    Cont, R., Kukanov, A., & Stoikov, S. (2014). "The Price Impact
    of Order Book Events." Journal of Financial Econometrics 12(1),
    47-88.

Requires Level 2 (order book depth) data from IBKR or equivalent.

SKELETON IMPLEMENTATION — Phase K.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("nzt48.micro_price")


def compute_micro_price(
    bid_size: float,
    ask_size: float,
    bid_price: float,
    ask_price: float,
) -> float:
    """Compute the volume-weighted micro-price from L2 order book data.

    The micro-price weights each side by the OPPOSITE side's size,
    reflecting the intuition that if the ask is thin (small ask_size),
    the price is likely to move towards the ask.

    Formula:
        micro_price = (bid_price * ask_size + ask_price * bid_size)
                      / (bid_size + ask_size)

    Example:
        bid_size=10000, ask_size=100, bid=99.00, ask=101.00
        micro = (99.00 * 100 + 101.00 * 10000) / (10100)
              = (9900 + 1010000) / 10100
              = 1019900 / 10100
              = 100.98  (very close to ask — correct!)

    Args:
        bid_size: Total size (shares/contracts) on the best bid level.
        ask_size: Total size (shares/contracts) on the best ask level.
        bid_price: Best bid price.
        ask_price: Best ask price.

    Returns:
        The micro-price as a float. Returns simple mid-price as fallback
        if sizes are zero or prices are invalid.
    """
    # --- Input validation ---
    if bid_price <= 0 or ask_price <= 0:
        logger.warning(
            "K-12: Invalid prices bid=%.4f ask=%.4f — returning 0.0",
            bid_price, ask_price,
        )
        return 0.0

    if ask_price < bid_price:
        # Crossed book — anomalous, return mid
        logger.warning(
            "K-12: Crossed book bid=%.4f > ask=%.4f — returning mid",
            bid_price, ask_price,
        )
        return (bid_price + ask_price) / 2.0

    total_size = bid_size + ask_size
    if total_size <= 0:
        # No size data — fall back to simple mid-price
        logger.debug("K-12: Zero total size — returning simple mid-price")
        return (bid_price + ask_price) / 2.0

    micro = (bid_price * ask_size + ask_price * bid_size) / total_size
    return micro


def compute_order_book_imbalance(
    bid_size: float,
    ask_size: float,
) -> float:
    """Compute Order Book Imbalance (OBI) ratio.

    OBI = (bid_size - ask_size) / (bid_size + ask_size)

    Range: [-1.0, +1.0]
        +1.0 = all size on bid (strong buying pressure)
        -1.0 = all size on ask (strong selling pressure)
         0.0 = balanced book

    Args:
        bid_size: Total size on the best bid.
        ask_size: Total size on the best ask.

    Returns:
        OBI ratio in [-1.0, +1.0]. Returns 0.0 if both sizes are zero.
    """
    total = bid_size + ask_size
    if total <= 0:
        return 0.0

    return (bid_size - ask_size) / total


def compute_micro_price_from_depth(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    depth: int = 3,
) -> float:
    """Compute micro-price using multiple levels of order book depth.

    SKELETON — extends basic micro-price to top N levels.
    Uses volume-weighted aggregation across depth levels with
    exponential decay (closer levels weighted more heavily).

    Args:
        bids: List of (price, size) tuples, best bid first.
        asks: List of (price, size) tuples, best ask first.
        depth: Number of levels to consider (default 3).

    Returns:
        Depth-weighted micro-price. Falls back to L1 micro-price
        if insufficient depth.

    TODO (Phase Q2):
        - Implement exponential decay weighting across levels
        - Add persistence detection (sticky large orders)
        - Integrate with spoof_detector.py (K-14) to filter spoofed levels
        - Feed into predictive_scoring module for entry timing
    """
    if not bids or not asks:
        logger.warning("K-12: Empty bids/asks for depth micro-price")
        return 0.0

    # Use top level as minimum viable implementation
    best_bid_price, best_bid_size = bids[0]
    best_ask_price, best_ask_size = asks[0]

    # TODO (Phase Q2): Aggregate across `depth` levels with decay
    return compute_micro_price(
        bid_size=best_bid_size,
        ask_size=best_ask_size,
        bid_price=best_bid_price,
        ask_price=best_ask_price,
    )
