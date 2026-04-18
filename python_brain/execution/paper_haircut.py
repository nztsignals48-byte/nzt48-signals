"""Paper-to-live fill-quality haircut.

IBKR paper trading simulates fills without routing to real exchanges.
Live fills are systematically worse. This module applies per-venue haircut
so cost model (and Ouroboros) don't overestimate edge.

Consumed by cost_model.py and signal_to_order_bridge.py before net_edge
calculation.
"""
from __future__ import annotations

from dataclasses import dataclass


# Per-venue + order-type haircut (bps). Starts at calibrated estimates;
# Ouroboros tunes these from any real live-fill samples accumulated.
HAIRCUT_BPS = {
    ("MKT", "SMART"): 2.0,
    ("LMT", "SMART"): 1.5,
    ("MKT", "LSE"): 3.5,
    ("LMT", "LSE"): 2.5,
    ("MKT", "LSEETF"): 5.0,
    ("LMT", "LSEETF"): 4.0,
    ("MKT", "IBIS"): 3.0,
    ("LMT", "IBIS"): 2.0,
    ("MKT", "ASX"): 4.0,
    ("LMT", "ASX"): 3.0,
    ("MKT", "TSEJ"): 4.0,
    ("LMT", "TSEJ"): 3.0,
    ("MKT", "SBF"): 3.5,
    ("LMT", "SBF"): 2.5,
    ("MKT", "SEHK"): 5.0,
    ("LMT", "SEHK"): 4.0,
    ("MKT", "SGX"): 4.0,
    ("LMT", "SGX"): 3.0,
    ("MKT", "BVME"): 4.5,
    ("LMT", "BVME"): 3.5,
    ("MKT", "DARK"): 5.0,
}

# Passive orders may not fill at all in live markets — probability adjustment
NON_FILL_PROB = {
    "MKT": 0.0,
    "LMT": 0.15,
    "MARKETABLE_LIMIT": 0.05,
}


@dataclass
class HaircutResult:
    fill_price_adjusted: float
    haircut_bps: float
    venue: str
    side: str
    order_type: str


def apply_paper_haircut(
    fill_price: float,
    side: str,
    order_type: str = "MKT",
    venue: str = "SMART",
) -> HaircutResult:
    """Apply per-venue haircut to paper fill price.

    For BUY: adjusted_price > paper_price (we pay more)
    For SELL: adjusted_price < paper_price (we receive less)
    """
    key = (order_type.upper(), venue.upper())
    bps = HAIRCUT_BPS.get(key, 3.0)  # default conservative

    if side.upper() == "BUY":
        adjusted = fill_price * (1 + bps / 10000)
    else:
        adjusted = fill_price * (1 - bps / 10000)

    return HaircutResult(
        fill_price_adjusted=adjusted,
        haircut_bps=bps,
        venue=venue,
        side=side.upper(),
        order_type=order_type.upper(),
    )


def haircut_bps_for(order_type: str, venue: str) -> float:
    return HAIRCUT_BPS.get((order_type.upper(), venue.upper()), 3.0)


def non_fill_probability(order_type: str) -> float:
    return NON_FILL_PROB.get(order_type.upper(), 0.0)


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        r = apply_paper_haircut(100.00, "BUY", "MKT", "SMART")
        print(f"BUY MKT SMART: {r.fill_price_adjusted:.4f} (haircut {r.haircut_bps} bps)")
        r = apply_paper_haircut(100.00, "SELL", "MKT", "LSE")
        print(f"SELL MKT LSE: {r.fill_price_adjusted:.4f} (haircut {r.haircut_bps} bps)")
        r = apply_paper_haircut(100.00, "BUY", "LMT", "LSEETF")
        print(f"BUY LMT LSEETF: {r.fill_price_adjusted:.4f} (haircut {r.haircut_bps} bps)")
        print("OK")
