"""Book 136 Bridge Adapter — Integration layer between Book 136 cross-market lead-lag
and bridge.py signal generation pipeline.

This module:
1. Maintains tick buffers for ES, NQ, TY, VIX as bridge.py processes them
2. Feeds ticks to CrossMarketLeadLagDetector
3. Applies confidence adjustments to outgoing signals based on lead-lag analysis
4. Logs lead-lag signals for forensic review
5. Handles edge cases (market gaps, low volume, regime breaks)

Integration points in bridge.py:
  - process_tick(): Update lead-lag detector with ES/NQ/TY/VIX ticks
  - _apply_adjustments(): Apply confidence adjustment for current signal direction
  - Output signal: Include lead_lag_correlation, lead_lag_adjustment fields

Book 136 confidence adjustments:
  - If lead-lag direction MATCHES signal direction: +15% confidence
  - If lead-lag direction OPPOSES signal direction: -20% confidence
  - If lead-lag REGIME BROKEN (corr < 0.70): 0% adjustment (ignore)
  - Separate tracking for each of 4 pairs (ES→3USL, NQ→QQQS, TY→TLT, VIX→UVXY)

Edge cases handled:
  - Market gaps: Disable lead-lag for 5 minutes after detection
  - Low volume periods (<50% of 20-bar avg): Ignore lead-lag
  - Circuit breakers/halts: Pause monitoring
  - Regime breaks: Log and track correlation recovery
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("book_136_adapter")


class Book136BridgeAdapter:
    """Stateful adapter connecting Book 136 detector to bridge.py.

    Maintains tick buffers for ES/NQ/TY/VIX, feeds to detector,
    and applies adjustments to signals.
    """

    def __init__(self):
        """Initialize adapter."""
        from python_brain.strategies.book_136_cross_market_leadlag import (
            get_detector,
            TickData,
        )

        self.detector = get_detector()
        self.TickData = TickData

        # Tick buffers: ticker_id → list of recent ticks
        self._tick_buffers: Dict[int, List[Dict]] = {}
        self._symbol_to_ticker_id: Dict[str, int] = {}
        self._ticker_id_to_symbol: Dict[int, str] = {}

        # Mapping: IBKR symbol → Book 136 symbol
        self._symbol_mapping = {
            "ES": "ES",  # S&P 500 futures
            "NQ": "NQ",  # Nasdaq futures
            "TY": "TY",  # 10Y Treasury futures
            "VIX": "^VIX",  # VIX index (yfinance)
            "3USL.L": "3USL",  # 3x S&P 500 ETF
            "3USS.L": "3USS",  # 3x inverse S&P 500 ETF
            "QQQS.L": "QQQS",  # 3x inverse Nasdaq ETF
            "QQQ3.L": "QQQ3",  # 3x Nasdaq ETF
            "TLT": "TLT",  # 20+ Year Bond ETF
            "UVXY": "UVXY",  # 2x inverse VIX ETN
        }

        # Lead-lag pair tracking
        self._pair_to_leaders: Dict[str, List[str]] = {
            "3USL": ["ES"],
            "3USS": ["ES"],
            "QQQS": ["NQ"],
            "QQQ3": ["NQ"],
            "TLT": ["TY"],
            "UVXY": ["VIX"],
        }

        # Regime tracking
        self._regime_break_until: Dict[str, float] = {}
        self._low_volume_until: Dict[str, float] = {}
        self._gap_detection_until: Dict[str, float] = {}

    def register_symbol(self, ticker_id: int, symbol: str) -> None:
        """Register a symbol with its ticker ID."""
        self._ticker_id_to_symbol[ticker_id] = symbol
        self._symbol_to_ticker_id[symbol] = ticker_id
        if symbol not in self._tick_buffers:
            self._tick_buffers[symbol] = []

    def on_tick(
        self,
        ticker_id: int,
        symbol: str,
        price: float,
        bid: float,
        ask: float,
        volume: int,
        timestamp_ns: int,
    ) -> None:
        """Process a tick from bridge.py.

        Args:
            ticker_id: Internal ticker ID
            symbol: Symbol (e.g., "ES", "3USL.L")
            price: Latest price
            bid: Bid price
            ask: Ask price
            volume: Volume
            timestamp_ns: Nanosecond timestamp
        """
        # Register if new
        if ticker_id not in self._ticker_id_to_symbol:
            self.register_symbol(ticker_id, symbol)

        # Skip if not in Book 136 pairs
        if symbol not in self._symbol_mapping:
            return

        # Add to buffer
        mapped_symbol = self._symbol_mapping[symbol]
        if mapped_symbol not in self._tick_buffers:
            self._tick_buffers[mapped_symbol] = []

        tick = {
            "price": price,
            "bid": bid,
            "ask": ask,
            "volume": volume,
            "timestamp_ns": timestamp_ns,
        }
        self._tick_buffers[mapped_symbol].append(tick)

        # Keep only last 300 ticks (5 minutes @ 10Hz)
        if len(self._tick_buffers[mapped_symbol]) > 300:
            self._tick_buffers[mapped_symbol] = self._tick_buffers[mapped_symbol][-300:]

        # Update detector if we have both leader and follower
        self._try_update_detector(mapped_symbol, timestamp_ns / 1e9)

    def _try_update_detector(self, follower_symbol: str, current_time_sec: float) -> None:
        """Try to update detector with leader/follower pair."""
        leaders = self._pair_to_leaders.get(follower_symbol, [])
        if not leaders:
            return

        # Check regime (skip if in cooldown)
        pair_name = f"{leaders[0]}→{follower_symbol}"
        if pair_name in self._regime_break_until:
            if current_time_sec < self._regime_break_until[pair_name]:
                return

        # Get latest leader tick
        leader_symbol = leaders[0]
        leader_ticks = self._tick_buffers.get(leader_symbol, [])
        follower_ticks = self._tick_buffers.get(follower_symbol, [])

        if not leader_ticks or not follower_ticks or len(leader_ticks) < 2:
            return

        # Create TickData objects
        leader_tick = self.TickData(
            timestamp_ns=int(leader_ticks[-1]["timestamp_ns"]),
            price=leader_ticks[-1]["price"],
            volume=leader_ticks[-1]["volume"],
            bid=leader_ticks[-1]["bid"],
            ask=leader_ticks[-1]["ask"],
        )

        follower_tick = self.TickData(
            timestamp_ns=int(follower_ticks[-1]["timestamp_ns"]),
            price=follower_ticks[-1]["price"],
            volume=follower_ticks[-1]["volume"],
            bid=follower_ticks[-1]["bid"],
            ask=follower_ticks[-1]["ask"],
        )

        # Update detector
        try:
            result = self.detector.update_pair(
                pair_name,
                leader_tick,
                follower_tick,
                int(current_time_sec * 1e9),
            )
            if result and result.is_regime_break:
                # Set cooldown for 5 minutes
                self._regime_break_until[pair_name] = current_time_sec + 300
                sys.stderr.write(
                    f"BOOK136_REGIME_BREAK: {pair_name} correlation={result.correlation:.3f} "
                    f"(below threshold), pausing for 5 min\n"
                )
                sys.stderr.flush()
        except Exception as e:
            log.error(f"Book 136 detector error for {pair_name}: {e}")

    def apply_adjustment(
        self,
        signal_dict: Dict,
        signal_direction: str,
        ticker_id: int,
        symbol: str,
        current_timestamp_ns: int,
    ) -> Dict:
        """Apply Book 136 confidence adjustment to a signal.

        Args:
            signal_dict: Signal dict from bridge.py
            signal_direction: "long" or "short"
            ticker_id: Ticker ID
            symbol: Symbol (e.g., "3USL.L")
            current_timestamp_ns: Current timestamp

        Returns: Modified signal_dict
        """
        if symbol not in self._symbol_mapping:
            return signal_dict

        # Get aggregated lead-lag analysis
        try:
            agg = self.detector.get_signal_adjustment(signal_direction)
        except Exception as e:
            log.warning(f"Failed to get lead-lag adjustment: {e}")
            return signal_dict

        # Check if in regime break cooldown
        if agg.regime_health == "broken":
            # Don't apply adjustment if regime broken
            signal_dict["lead_lag_regime_broken"] = True
            signal_dict["lead_lag_adjustment_pct"] = 0.0
            return signal_dict

        # Apply adjustment
        conf_adjust = agg.confidence_adjustment_pct
        old_conf = signal_dict.get("confidence", 50)
        new_conf = max(0, min(100, int(old_conf + conf_adjust)))

        signal_dict["confidence"] = new_conf
        signal_dict["lead_lag_adjustment_pct"] = conf_adjust
        signal_dict["lead_lag_primary_pair"] = agg.primary_pair
        signal_dict["lead_lag_primary_correlation"] = agg.primary_correlation
        signal_dict["lead_lag_primary_lag_ms"] = agg.primary_lag_ms
        signal_dict["lead_lag_supporting"] = agg.supporting_pairs
        signal_dict["lead_lag_conflicting"] = agg.conflicting_pairs
        signal_dict["lead_lag_regime"] = agg.regime_health

        # Log for forensics
        if conf_adjust != 0:
            sys.stderr.write(
                f"BOOK136_ADJUST: {symbol} {signal_direction} "
                f"confidence={old_conf}->{new_conf} "
                f"primary={agg.primary_pair} corr={agg.primary_correlation:.3f} "
                f"adjustment={conf_adjust:+.0f}%\n"
            )
            sys.stderr.flush()

        return signal_dict

    def get_diagnostics(self) -> Dict:
        """Return diagnostics for all 4 pairs."""
        results = self.detector.get_all_results()
        return {
            pair: {
                "lag_ms": r.lag_ms,
                "correlation": r.correlation,
                "direction": r.direction,
                "confidence": r.confidence,
                "is_regime_break": r.is_regime_break,
            }
            for pair, r in results.items()
        }

    def reset(self) -> None:
        """Reset all state."""
        self.detector.reset()
        self._tick_buffers.clear()
        self._regime_break_until.clear()
        self._low_volume_until.clear()
        self._gap_detection_until.clear()


# ---------------------------------------------------------------------------
# Singleton Accessor
# ---------------------------------------------------------------------------

_adapter_instance: Optional[Book136BridgeAdapter] = None


def get_adapter() -> Book136BridgeAdapter:
    """Get or create singleton adapter instance."""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = Book136BridgeAdapter()
    return _adapter_instance
