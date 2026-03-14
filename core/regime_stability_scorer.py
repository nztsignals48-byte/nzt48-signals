"""
Regime Stability Scorer -- NZT-48 Institutional Risk Metric
Guidolin & Timmermann (2007): Asset Allocation Under Multivariate Regime Switching.
Key: regime TRANSITIONS are the most dangerous periods. First 3 days after change
-> use 50% size. Stability score 0-1 determines full/half/quarter sizing.
"""

import json
import logging
import os
from datetime import datetime, date, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/regime_stability.json"

# Stability thresholds
STABILITY_FULL_SIZE = 0.70      # > 0.70: full size
STABILITY_THREE_QUARTER = 0.50  # 0.50-0.70: 0.75x size
STABILITY_HALF_SIZE = 0.30      # 0.30-0.50: 0.50x size (transition window)
# < 0.30: 0.25x size (fresh regime change -- pilot only)

TRANSITION_WINDOW_DAYS = 3   # First 3 days after regime change: dangerous


class RegimeStabilityScorer:
    """
    Scores regime stability to modulate position sizing.

    Guidolin & Timmermann (2007): optimal portfolios in regime-switching
    environments should reduce risky asset allocation 50-80% during transitions.

    Two components:
    1. Persistence score: how long has current regime lasted?
    2. Quality score: per-strategy win rate in this regime (from learning engine)
    Combined: stability = persistence x quality
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
        return {
            "current_regime": None,
            "regime_start": None,
            "regime_history": [],
            "regime_performance": {},
        }

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_file) if os.path.dirname(self.state_file) else ".", exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("RegimeStabilityScorer: save failed: %s", e)

    def record_regime(self, regime: str) -> bool:
        """
        Records current regime. Returns True if regime changed.
        Called on every scan cycle.
        """
        current = self.state.get("current_regime")
        if regime != current:
            # Regime change
            self.state["regime_history"].append({
                "regime": current,
                "ended_at": datetime.now(timezone.utc).isoformat(),
            })
            if len(self.state["regime_history"]) > 30:
                self.state["regime_history"] = self.state["regime_history"][-30:]
            self.state["current_regime"] = regime
            self.state["regime_start"] = datetime.now(timezone.utc).isoformat()
            self._save_state()
            logger.info("Regime transition: %s -> %s", current, regime)
            return True
        return False

    def get_days_in_current_regime(self) -> int:
        """Returns number of days in current regime."""
        start_str = self.state.get("regime_start")
        if not start_str:
            return 0
        try:
            start = datetime.fromisoformat(start_str)
            return (datetime.now(timezone.utc) - start).days
        except Exception:
            return 0

    def is_in_transition_window(self) -> bool:
        """True if regime changed within last TRANSITION_WINDOW_DAYS days."""
        return self.get_days_in_current_regime() < TRANSITION_WINDOW_DAYS

    def get_persistence_score(self) -> float:
        """
        0.0-1.0 persistence score based on days in current regime.
        < 3 days: 0.30 (dangerous transition)
        3-10 days: 0.60 (establishing)
        > 10 days: 0.90 (stable)
        """
        days = self.get_days_in_current_regime()
        if days < TRANSITION_WINDOW_DAYS:
            return 0.30
        elif days < 10:
            return 0.60
        else:
            return 0.90

    def get_quality_score(self, regime: str, strategy: str) -> float:
        """
        Per-strategy win rate in this regime (0.0-1.0).
        Falls back to 0.65 (optimistic default) if no data.
        """
        key = f"{regime}:{strategy}"
        perf = self.state.get("regime_performance", {}).get(key)
        if not perf:
            return 0.65  # Optimistic default
        win_rate = perf.get("win_rate", 0.65)
        return max(0.1, min(1.0, win_rate))

    def update_regime_performance(self, regime: str, strategy: str,
                                   win: bool) -> None:
        """Update win rate for regime/strategy pair."""
        key = f"{regime}:{strategy}"
        perf = self.state.setdefault("regime_performance", {}).setdefault(key, {
            "wins": 0, "total": 0, "win_rate": 0.65
        })
        perf["total"] += 1
        if win:
            perf["wins"] += 1
        perf["win_rate"] = perf["wins"] / perf["total"]
        self._save_state()

    def get_stability_score(self, current_regime: str, strategy: str = "S15") -> dict:
        """
        Combined stability score and size multiplier.

        Returns: {persistence_score, quality_score, combined, size_multiplier, days_in_regime}
        """
        persistence = self.get_persistence_score()
        quality = self.get_quality_score(current_regime, strategy)
        combined = round(persistence * quality, 3)
        days = self.get_days_in_current_regime()

        if combined > STABILITY_FULL_SIZE:
            size_mult = 1.0
        elif combined > STABILITY_THREE_QUARTER:
            size_mult = 0.75
        elif combined > STABILITY_HALF_SIZE:
            size_mult = 0.5
        else:
            size_mult = 0.25

        return {
            "regime": current_regime,
            "strategy": strategy,
            "persistence_score": persistence,
            "quality_score": quality,
            "combined": combined,
            "size_multiplier": size_mult,
            "days_in_regime": days,
            "in_transition_window": self.is_in_transition_window(),
        }

    def get_telegram_summary(self, current_regime: str) -> str:
        score = self.get_stability_score(current_regime)
        combined = score["combined"]
        days = score["days_in_regime"]
        size_mult = score["size_multiplier"]
        transition_warn = " TRANSITION WARNING" if score["in_transition_window"] else ""
        return (
            f"Regime Stability: {current_regime} day {days} | "
            f"stability={combined:.2f} | size x{size_mult:.2f}{transition_warn}"
        )
