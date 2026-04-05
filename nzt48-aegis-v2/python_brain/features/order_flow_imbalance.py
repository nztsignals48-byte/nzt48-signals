"""Order Flow Imbalance (OFI) signal — LOB-inspired microstructure signal.

Computes tick-by-tick order flow imbalance from bid/ask changes.
OFI predicts short-term price moves better than volume alone.
Reference: Cont, Kukanov & Stoikov (2014) "The Price Impact of Order Book Events"

License dependency: None (pure implementation from academic paper).
"""

import json
import os
import time
from collections import defaultdict
from typing import Dict, Optional, Tuple

# Exponential moving average decay for OFI smoothing
_OFI_DECAY = 0.94  # ~17 tick half-life


class TickState:
    """Per-ticker OFI tracking state."""
    __slots__ = ('prev_bid', 'prev_ask', 'prev_bid_sz', 'prev_ask_sz',
                 'ofi_ema', 'ofi_raw_sum', 'tick_count', 'last_update_ns',
                 'depth_imbalance_ema', 'book_pressure_ema', 'has_depth')

    def __init__(self):
        self.prev_bid = 0.0
        self.prev_ask = 0.0
        self.prev_bid_sz = 0
        self.prev_ask_sz = 0
        self.ofi_ema = 0.0
        self.ofi_raw_sum = 0.0
        self.tick_count = 0
        self.last_update_ns = 0
        # L2 depth tracking
        self.depth_imbalance_ema = 0.0  # EMA of depth_imbalance from order book
        self.book_pressure_ema = 0.0    # EMA of book_pressure from order book
        self.has_depth = False           # Whether this ticker has L2 data


class OrderFlowImbalanceTracker:
    """Track order flow imbalance across all tickers.

    OFI = change_in_bid_volume - change_in_ask_volume,
    adjusted for price level changes (Cont et al. 2014).
    """

    def __init__(self, decay: float = _OFI_DECAY):
        self._states: Dict[int, TickState] = defaultdict(TickState)
        self._decay = decay

    def update(self, ticker_id: int, bid: float, ask: float,
               bid_size: int = 0, ask_size: int = 0,
               now_ns: int = 0,
               depth_imbalance: float = 0.0,
               book_pressure: float = 0.0,
               total_bid_depth: float = 0.0,
               total_ask_depth: float = 0.0) -> Optional[float]:
        """Feed a tick, return smoothed OFI score or None if insufficient data.

        When L2 depth data is available (depth_imbalance != 0 or total_bid_depth > 0),
        the OFI signal is enriched with multi-level order book information,
        providing a stronger signal than top-of-book bid_size/ask_size alone.

        Returns float in [-100, 100] range:
          positive = net buying pressure (bullish)
          negative = net selling pressure (bearish)
        """
        s = self._states[ticker_id]

        # Track whether this ticker has L2 depth data
        has_l2 = total_bid_depth > 0.0 or total_ask_depth > 0.0
        if has_l2:
            s.has_depth = True

        if s.tick_count == 0:
            # First tick — just store state
            s.prev_bid = bid
            s.prev_ask = ask
            s.prev_bid_sz = bid_size
            s.prev_ask_sz = ask_size
            s.tick_count = 1
            s.last_update_ns = now_ns
            if has_l2:
                s.depth_imbalance_ema = depth_imbalance
                s.book_pressure_ema = book_pressure
            return None

        # Compute OFI components (Cont et al. 2014, Eq. 1)
        delta_bid_vol = 0
        delta_ask_vol = 0

        if bid > s.prev_bid:
            delta_bid_vol = bid_size  # New higher bid = fresh buying
        elif bid == s.prev_bid:
            delta_bid_vol = bid_size - s.prev_bid_sz  # Same level: net change
        else:
            delta_bid_vol = -s.prev_bid_sz  # Bid dropped = buying withdrawn

        if ask < s.prev_ask:
            delta_ask_vol = ask_size  # New lower ask = fresh selling
        elif ask == s.prev_ask:
            delta_ask_vol = ask_size - s.prev_ask_sz  # Same level: net change
        else:
            delta_ask_vol = -s.prev_ask_sz  # Ask rose = selling withdrawn

        # OFI = bid pressure - ask pressure
        ofi_raw = delta_bid_vol - delta_ask_vol

        # When L2 depth is available, blend depth_imbalance into OFI.
        # depth_imbalance is [-1, 1] from the full 5-level book.
        # Scale to OFI units (~[-100, 100]) and blend at 30% weight.
        if has_l2 and abs(depth_imbalance) > 0.001:
            depth_signal = depth_imbalance * 100.0  # Scale [-1,1] to [-100,100]
            ofi_raw = 0.7 * ofi_raw + 0.3 * depth_signal
            # Update depth EMAs
            s.depth_imbalance_ema = self._decay * s.depth_imbalance_ema + (1.0 - self._decay) * depth_imbalance
            s.book_pressure_ema = self._decay * s.book_pressure_ema + (1.0 - self._decay) * book_pressure

        # EMA smoothing
        s.ofi_ema = self._decay * s.ofi_ema + (1.0 - self._decay) * ofi_raw
        s.ofi_raw_sum += ofi_raw
        s.tick_count += 1

        # Store state
        s.prev_bid = bid
        s.prev_ask = ask
        s.prev_bid_sz = bid_size
        s.prev_ask_sz = ask_size
        s.last_update_ns = now_ns

        # Normalize to [-100, 100] range using adaptive scaling
        # After enough ticks, normalize by average absolute OFI
        if s.tick_count < 10:
            return None

        return max(-100.0, min(100.0, s.ofi_ema))

    def get_signal(self, ticker_id: int) -> Tuple[float, str]:
        """Get current OFI signal for a ticker.

        Returns (score, direction) where:
          score: absolute signal strength [0, 100]
          direction: "Long" or "Short"
        """
        s = self._states.get(ticker_id)
        if s is None or s.tick_count < 10:
            return (0.0, "Long")

        score = abs(s.ofi_ema)
        direction = "Long" if s.ofi_ema > 0 else "Short"
        return (min(score, 100.0), direction)

    def confidence_adjustment(self, ticker_id: int, signal_direction: str) -> float:
        """Return confidence adjustment based on OFI alignment with signal.

        Returns [-7, +7] adjustment (extended range when L2 depth confirms):
          +5..+7 if OFI strongly confirms signal direction (L2 boost)
          -5..-7 if OFI strongly opposes signal direction (L2 penalty)
          0 if neutral or insufficient data
        """
        s = self._states.get(ticker_id)
        if s is None or s.tick_count < 20:
            return 0.0

        ofi = s.ofi_ema
        if abs(ofi) < 5.0:
            return 0.0  # Noise threshold

        # Base OFI adjustment [-5, +5]
        base_adj = 0.0
        if signal_direction == "Long" and ofi > 0:
            base_adj = min(5.0, ofi / 20.0)  # Scale: OFI=100 -> +5
        elif signal_direction == "Short" and ofi < 0:
            base_adj = min(5.0, abs(ofi) / 20.0)
        elif signal_direction == "Long" and ofi < -10:
            base_adj = max(-5.0, ofi / 20.0)  # Opposing: OFI=-100 -> -5
        elif signal_direction == "Short" and ofi > 10:
            base_adj = max(-5.0, -ofi / 20.0)

        # L2 depth boost: when depth_imbalance confirms OFI direction,
        # add up to +/-2 extra confidence points.
        depth_boost = 0.0
        if s.has_depth and abs(s.depth_imbalance_ema) > 0.05:
            # depth_imbalance_ema is [-1, 1]
            di = s.depth_imbalance_ema
            if signal_direction == "Long" and di > 0.05 and base_adj > 0:
                depth_boost = min(2.0, di * 4.0)  # di=0.5 -> +2.0
            elif signal_direction == "Short" and di < -0.05 and base_adj > 0:
                depth_boost = min(2.0, abs(di) * 4.0)
            elif signal_direction == "Long" and di < -0.10 and base_adj < 0:
                depth_boost = max(-2.0, di * 4.0)  # di=-0.5 -> -2.0
            elif signal_direction == "Short" and di > 0.10 and base_adj < 0:
                depth_boost = max(-2.0, -di * 4.0)

        return max(-7.0, min(7.0, base_adj + depth_boost))

    def snapshot(self) -> Dict:
        """Dump current state for debugging/logging."""
        result = {}
        for tid, s in self._states.items():
            if s.tick_count >= 10:
                entry = {
                    "ofi_ema": round(s.ofi_ema, 2),
                    "tick_count": s.tick_count,
                }
                if s.has_depth:
                    entry["depth_imbalance_ema"] = round(s.depth_imbalance_ema, 4)
                    entry["book_pressure_ema"] = round(s.book_pressure_ema, 2)
                    entry["has_l2"] = True
                result[tid] = entry
        return result


# Module-level singleton
_tracker: Optional[OrderFlowImbalanceTracker] = None


def get_tracker() -> OrderFlowImbalanceTracker:
    global _tracker
    if _tracker is None:
        _tracker = OrderFlowImbalanceTracker()
    return _tracker


def save_snapshot(data_dir: str = "/app/data") -> None:
    """Save OFI snapshot to disk for nightly analysis."""
    tracker = get_tracker()
    snap = tracker.snapshot()
    if not snap:
        return
    path = os.path.join(data_dir, "ofi_snapshot.json")
    try:
        with open(path, "w") as f:
            json.dump({"timestamp": time.time(), "ofi": snap}, f)
    except Exception:
        pass
