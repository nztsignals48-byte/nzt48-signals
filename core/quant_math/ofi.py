"""
Order Flow Imbalance -- Cont, Kukanov & Stoikov (2014).
Predicts next 1-50 ticks from limit order additions/cancellations.
"""
from __future__ import annotations
import numpy as np


def calculate_ofi(bid_t: float, bid_size_t: float, ask_t: float, ask_size_t: float,
                  bid_prev: float, bid_size_prev: float, ask_prev: float, ask_size_prev: float) -> float:
    """
    Tick-by-tick Order Flow Imbalance.
    Positive = upward pressure. Negative = downward pressure.
    """
    if bid_t > bid_prev:
        e_bid = bid_size_t
    elif bid_t == bid_prev:
        e_bid = bid_size_t - bid_size_prev
    else:
        e_bid = -bid_size_prev

    if ask_t > ask_prev:
        e_ask = -ask_size_prev
    elif ask_t == ask_prev:
        e_ask = ask_size_t - ask_size_prev
    else:
        e_ask = ask_size_t

    return float(e_bid - e_ask)


def aggregate_ofi(ofi_array: np.ndarray) -> float:
    """Rolling sum of OFI over last N ticks."""
    return float(np.sum(ofi_array))
