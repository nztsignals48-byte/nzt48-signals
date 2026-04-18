"""
Market-Impact-Aware Order Router

Decides between venue/order type based on:
1. Size vs book depth (large -> slice via Almgren-Chriss)
2. Urgency (immediate -> market order, patient -> passive limit)
3. Venue selection (dark pool vs lit venue)
4. Adverse selection estimate (VPIN / order flow imbalance)
5. Expected cost vs alpha (net edge calculation)

Reference:
- Citadel Securities execution research 2024
- Kissell (2014) "The Science of Algorithmic Trading"
- Johnson (2010) "Algorithmic Trading & DMA"
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OrderType(Enum):
    MARKET = "MKT"
    LIMIT = "LMT"
    MARKETABLE_LIMIT = "MARKETABLE_LIMIT"   # limit at aggressive side of book
    VWAP = "VWAP"                            # time-sliced, TWAP-like
    ICEBERG = "ICEBERG"                      # display only portion
    MIDPOINT = "MIDPOINT"                    # midpoint peg, dark-pool style


class Venue(Enum):
    SMART = "SMART"                          # IBKR smart routing
    DARK = "DARK"                            # midpoint/dark pool preference
    LIT_AGGRESSIVE = "LIT_AGG"
    LIT_PASSIVE = "LIT_PASS"


@dataclass
class RoutingDecision:
    order_type: OrderType
    venue: Venue
    num_slices: int
    slice_interval_s: float
    limit_offset_bps: float                 # bps from mid (for limit orders)
    expected_cost_bps: float
    rationale: str


@dataclass
class BookContext:
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    recent_volume: int                      # shares in last 5 min
    adv_shares: float                        # average daily volume
    vpin: float                              # volume-synchronized probability of informed trading
    volatility_bps: float                    # recent volatility
    urgency: float                           # 0 = patient, 1 = must fill now


def classify_order_size(shares: int, book: BookContext) -> str:
    """Classify order size relative to book depth + ADV."""
    top_of_book = max(book.bid_size, book.ask_size)

    if shares < top_of_book * 0.1:
        return "tiny"        # < 10% of top-of-book
    elif shares < top_of_book * 0.5:
        return "small"        # < 50% of top-of-book
    elif shares < book.adv_shares * 0.001:
        return "medium"       # < 0.1% of ADV
    elif shares < book.adv_shares * 0.01:
        return "large"        # < 1% of ADV
    else:
        return "huge"         # > 1% of ADV


def estimate_toxicity(book: BookContext) -> str:
    """Classify adverse selection risk from VPIN + OFI."""
    if book.vpin > 0.7:
        return "toxic"                      # HFTs detected — step aside
    elif book.vpin > 0.5:
        return "elevated"
    elif book.vpin > 0.3:
        return "normal"
    else:
        return "clean"


def route_order(
    shares: int,
    side: str,                              # "BUY" or "SELL"
    book: BookContext,
    alpha_decay_s: float = 300.0,           # seconds until signal decays
    target_cost_bps: float = 5.0,           # max acceptable cost
) -> RoutingDecision:
    """
    Decide how to execute this order based on size, urgency, book state.

    Returns RoutingDecision with order_type, venue, slicing params.
    """
    size_class = classify_order_size(shares, book)
    toxicity = estimate_toxicity(book)

    # Rule 1: Huge order + low urgency -> multi-slice VWAP
    if size_class == "huge" and book.urgency < 0.5:
        return RoutingDecision(
            order_type=OrderType.VWAP,
            venue=Venue.SMART,
            num_slices=20,
            slice_interval_s=alpha_decay_s / 20,
            limit_offset_bps=0,
            expected_cost_bps=15.0,
            rationale="Huge size, low urgency: 20-slice VWAP via SMART",
        )

    # Rule 2: Large + toxic book -> iceberg dark pool
    if size_class in ("large", "huge") and toxicity in ("elevated", "toxic"):
        return RoutingDecision(
            order_type=OrderType.ICEBERG,
            venue=Venue.DARK,
            num_slices=10,
            slice_interval_s=alpha_decay_s / 10,
            limit_offset_bps=0,
            expected_cost_bps=8.0,
            rationale=f"Large + {toxicity} book: iceberg to dark pool",
        )

    # Rule 3: Toxic book, any size -> pause or passive only
    if toxicity == "toxic":
        return RoutingDecision(
            order_type=OrderType.LIMIT,
            venue=Venue.LIT_PASSIVE,
            num_slices=1,
            slice_interval_s=0,
            limit_offset_bps=-2,  # 2 bps inside mid (passive)
            expected_cost_bps=-1.0,  # may earn spread
            rationale="Toxic VPIN: passive limit only, don't cross spread",
        )

    # Rule 4: Tiny/small + clean book -> marketable limit
    if size_class in ("tiny", "small") and toxicity == "clean":
        spread_bps = (book.ask - book.bid) / book.bid * 10000 if book.bid > 0 else 1.0
        return RoutingDecision(
            order_type=OrderType.MARKETABLE_LIMIT,
            venue=Venue.SMART,
            num_slices=1,
            slice_interval_s=0,
            limit_offset_bps=spread_bps / 2,  # cross half the spread
            expected_cost_bps=spread_bps / 2,
            rationale="Small + clean: marketable limit (half-spread)",
        )

    # Rule 5: Medium + high urgency -> 3-slice aggressive
    if size_class == "medium" and book.urgency > 0.7:
        return RoutingDecision(
            order_type=OrderType.MARKETABLE_LIMIT,
            venue=Venue.SMART,
            num_slices=3,
            slice_interval_s=alpha_decay_s / 3,
            limit_offset_bps=2.0,
            expected_cost_bps=6.0,
            rationale="Medium + urgent: 3-slice marketable limit",
        )

    # Rule 6: Default — limit at mid with SMART routing
    return RoutingDecision(
        order_type=OrderType.LIMIT,
        venue=Venue.SMART,
        num_slices=1,
        slice_interval_s=0,
        limit_offset_bps=0,
        expected_cost_bps=3.0,
        rationale="Default: limit at mid via SMART",
    )


def validate_routing_vs_alpha(
    decision: RoutingDecision,
    expected_alpha_bps: float,
    min_edge_bps: float = 2.0,
) -> tuple[bool, str]:
    """
    Check that net edge (alpha - cost) > minimum threshold.

    Returns (should_trade, reason).
    """
    net_edge = expected_alpha_bps - decision.expected_cost_bps

    if net_edge < min_edge_bps:
        return False, f"Net edge {net_edge:.2f}bps < min {min_edge_bps:.2f}bps"

    return True, f"Net edge {net_edge:.2f}bps OK"


def estimate_fill_probability(
    decision: RoutingDecision,
    book: BookContext,
) -> float:
    """Rough probability of full fill within slice interval."""
    if decision.order_type == OrderType.MARKET:
        return 0.99
    elif decision.order_type == OrderType.MARKETABLE_LIMIT:
        return 0.95
    elif decision.order_type == OrderType.MIDPOINT:
        return 0.40  # dark pool hit rate ~40%
    elif decision.order_type == OrderType.LIMIT:
        # Probability depends on offset from mid
        if decision.limit_offset_bps < 0:
            return 0.25  # passive, low fill rate
        elif decision.limit_offset_bps < 2:
            return 0.60
        else:
            return 0.85
    elif decision.order_type == OrderType.ICEBERG:
        return 0.70
    elif decision.order_type == OrderType.VWAP:
        return 0.90
    return 0.50


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        # Test 1: Small order, clean book
        book1 = BookContext(
            bid=100.0, ask=100.05,
            bid_size=500, ask_size=500,
            recent_volume=10000,
            adv_shares=1_000_000,
            vpin=0.2,
            volatility_bps=100,
            urgency=0.3,
        )
        d1 = route_order(100, "BUY", book1)
        print(f"Small/clean: {d1.order_type.value} @ {d1.venue.value} ({d1.expected_cost_bps:.1f} bps) - {d1.rationale}")

        # Test 2: Huge order, moderate urgency
        book2 = BookContext(
            bid=100.0, ask=100.05,
            bid_size=500, ask_size=500,
            recent_volume=10000,
            adv_shares=1_000_000,
            vpin=0.3,
            volatility_bps=100,
            urgency=0.4,
        )
        d2 = route_order(100000, "BUY", book2)
        print(f"Huge/patient: {d2.order_type.value} @ {d2.venue.value} slices={d2.num_slices} - {d2.rationale}")

        # Test 3: Large + toxic book
        book3 = BookContext(
            bid=100.0, ask=100.05,
            bid_size=500, ask_size=500,
            recent_volume=10000,
            adv_shares=1_000_000,
            vpin=0.75,
            volatility_bps=200,
            urgency=0.5,
        )
        d3 = route_order(5000, "BUY", book3)
        print(f"Large/toxic: {d3.order_type.value} @ {d3.venue.value} - {d3.rationale}")

        # Validate vs alpha
        should_trade, reason = validate_routing_vs_alpha(d1, expected_alpha_bps=12.0, min_edge_bps=5.0)
        print(f"Alpha validation: {should_trade} - {reason}")

        # Fill probability
        prob = estimate_fill_probability(d1, book1)
        print(f"Fill probability: {prob:.2%}")
        print("OK")
