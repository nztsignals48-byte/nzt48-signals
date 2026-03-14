"""
Gap Analytics — NZT-48 Microstructure Module
Nofsinger & Sias (1999): Institutional herding creates gap patterns.
Detects opening gaps, classifies them (gap-and-go vs gap-fade),
and tracks fill rates to adjust signal confidence.
"""

import json
import os
import logging
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/gap_analytics.json"

# Gap type constants
GAP_AND_GO = "GAP_AND_GO"         # Gap up/down continues in gap direction
GAP_FADE = "GAP_FADE"             # Gap reverses within first 30 min
GAP_FILL_IN_PROGRESS = "FILL_IP"  # Working back toward prior close
WATCHING = "WATCHING"             # Gap detected, not yet classified

# Thresholds
GAP_THRESHOLD_PCT = 1.0           # Minimum gap % to be worth tracking
GAP_AND_GO_THRESHOLD_PCT = 2.0   # Gap ≥ 2% with RVOL ≥ 1.5 = gap-and-go candidate
RVOL_THRESHOLD = 1.5              # Relative volume threshold for gap-and-go
HISTORICAL_FILL_RATE = 0.71       # 71% of gaps eventually fill (prior)


class GapAnalytics:
    """
    Tracks opening gaps and adjusts signal confidence:
    - Gap-and-go (gap ≥ 2%, RVOL ≥ 1.5): +8 confidence (institutional momentum)
    - Gap-fade (gap ≥ 3%, RVOL < 1.5, reverses by 30min): -8 confidence (trap)
    - Fill-in-progress: -5 confidence (reverting mean)
    - Small gap < 1%: 0 (noise)

    Also records gap outcomes to improve fill-rate estimates over time.
    """

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"gaps": {}, "outcomes": [], "fill_count": 0, "total_gaps": 0}

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning(f"GapAnalytics: save failed: {e}")

    def detect_gap(self, ticker: str, prior_close: float, current_price: float) -> Optional[float]:
        """
        Returns gap percentage (positive = gap up, negative = gap down).
        Returns None if gap < GAP_THRESHOLD_PCT.
        """
        if prior_close <= 0:
            return None
        gap_pct = (current_price - prior_close) / prior_close * 100
        if abs(gap_pct) < GAP_THRESHOLD_PCT:
            return None
        return round(gap_pct, 3)

    def classify_gap(
        self,
        ticker: str,
        gap_pct: float,
        rvol: float,
        minutes_since_open: int = 0,
        current_price: Optional[float] = None,
        prior_close: Optional[float] = None,
    ) -> str:
        """
        Classifies a detected gap.

        Rules:
        - |gap| ≥ 2% AND RVOL ≥ 1.5 → GAP_AND_GO
        - |gap| ≥ 3% AND RVOL < 1.5 AND (minutes_since_open ≥ 30 AND price reversing) → GAP_FADE
        - Price between gap and prior close → GAP_FILL_IN_PROGRESS
        - Otherwise → WATCHING
        """
        abs_gap = abs(gap_pct)

        # Check if fill-in-progress
        if current_price is not None and prior_close is not None and gap_pct != 0:
            gap_direction = 1 if gap_pct > 0 else -1
            open_price = prior_close * (1 + gap_pct / 100)
            # If price has retraced more than 50% of the gap
            retracement = (open_price - current_price) * gap_direction
            gap_size = abs(open_price - prior_close)
            if gap_size > 0 and retracement / gap_size > 0.5:
                return GAP_FILL_IN_PROGRESS

        if abs_gap >= GAP_AND_GO_THRESHOLD_PCT and rvol >= RVOL_THRESHOLD:
            return GAP_AND_GO

        if abs_gap >= 3.0 and rvol < RVOL_THRESHOLD and minutes_since_open >= 30:
            return GAP_FADE

        return WATCHING

    def get_gap_zone(self, prior_close: float, gap_pct: float) -> dict:
        """Returns the fill zone: price level where gap would be filled."""
        open_price = prior_close * (1 + gap_pct / 100)
        return {
            "open": round(open_price, 4),
            "fill_target": round(prior_close, 4),
            "midpoint": round((open_price + prior_close) / 2, 4),
        }

    def record_gap_outcome(self, ticker: str, gap_pct: float, classification: str, filled: bool):
        """Record outcome for fill-rate tracking."""
        self.state["total_gaps"] += 1
        if filled:
            self.state["fill_count"] += 1
        self.state["outcomes"].append({
            "ticker": ticker,
            "date": date.today().isoformat(),
            "gap_pct": gap_pct,
            "classification": classification,
            "filled": filled,
        })
        # Keep last 500 outcomes
        if len(self.state["outcomes"]) > 500:
            self.state["outcomes"] = self.state["outcomes"][-500:]
        self._save_state()

    def get_fill_rate(self) -> float:
        """Returns empirical fill rate from recorded outcomes, or historical prior."""
        total = self.state.get("total_gaps", 0)
        if total < 20:
            return HISTORICAL_FILL_RATE
        return self.state["fill_count"] / total

    def get_confidence_adjustment(
        self,
        ticker: str,
        gap_pct: Optional[float],
        classification: Optional[str] = None,
    ) -> int:
        """
        GAP_AND_GO:          +8 (institutional momentum, ride it)
        GAP_FADE:            -8 (trap — price returning, avoid long)
        GAP_FILL_IN_PROGRESS: -5 (mean-reversion in progress)
        WATCHING / None:      0
        """
        if gap_pct is None or classification is None:
            return 0
        if classification == GAP_AND_GO:
            return 8
        if classification == GAP_FADE:
            return -8
        if classification == GAP_FILL_IN_PROGRESS:
            return -5
        return 0

    def get_telegram_note(self, ticker: str, gap_pct: Optional[float], classification: Optional[str]) -> str:
        if gap_pct is None:
            return f"📊 {ticker}: No significant gap at open"
        direction = "⬆️ UP" if gap_pct > 0 else "⬇️ DOWN"
        lines = [f"📊 Gap Analytics — {ticker}"]
        lines.append(f"  Gap: {gap_pct:+.2f}% {direction}  |  Type: {classification or 'WATCHING'}")
        adj = self.get_confidence_adjustment(ticker, gap_pct, classification)
        if adj != 0:
            lines.append(f"  Confidence adjustment: {adj:+d}")
        lines.append(f"  Historical fill rate: {self.get_fill_rate()*100:.0f}%")
        return "\n".join(lines)
