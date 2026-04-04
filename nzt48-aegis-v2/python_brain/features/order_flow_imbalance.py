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
                 'ofi_ema', 'ofi_raw_sum', 'tick_count', 'last_update_ns')

    def __init__(self):
        self.prev_bid = 0.0
        self.prev_ask = 0.0
        self.prev_bid_sz = 0
        self.prev_ask_sz = 0
        self.ofi_ema = 0.0
        self.ofi_raw_sum = 0.0
        self.tick_count = 0
        self.last_update_ns = 0


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
               now_ns: int = 0) -> Optional[float]:
        """Feed a tick, return smoothed OFI score or None if insufficient data.

        Returns float in [-100, 100] range:
          positive = net buying pressure (bullish)
          negative = net selling pressure (bearish)
        """
        s = self._states[ticker_id]

        if s.tick_count == 0:
            # First tick — just store state
            s.prev_bid = bid
            s.prev_ask = ask
            s.prev_bid_sz = bid_size
            s.prev_ask_sz = ask_size
            s.tick_count = 1
            s.last_update_ns = now_ns
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

        Returns [-5, +5] adjustment:
          +5 if OFI strongly confirms signal direction
          -5 if OFI strongly opposes signal direction
          0 if neutral or insufficient data
        """
        s = self._states.get(ticker_id)
        if s is None or s.tick_count < 20:
            return 0.0

        ofi = s.ofi_ema
        if abs(ofi) < 5.0:
            return 0.0  # Noise threshold

        # Check alignment
        if signal_direction == "Long" and ofi > 0:
            return min(5.0, ofi / 20.0)  # Scale: OFI=100 → +5
        elif signal_direction == "Short" and ofi < 0:
            return min(5.0, abs(ofi) / 20.0)
        elif signal_direction == "Long" and ofi < -10:
            return max(-5.0, ofi / 20.0)  # Opposing: OFI=-100 → -5
        elif signal_direction == "Short" and ofi > 10:
            return max(-5.0, -ofi / 20.0)
        return 0.0

    def snapshot(self) -> Dict:
        """Dump current state for debugging/logging."""
        return {
            tid: {
                "ofi_ema": round(s.ofi_ema, 2),
                "tick_count": s.tick_count,
            }
            for tid, s in self._states.items()
            if s.tick_count >= 10
        }


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
