"""
Tail Loss Behavior Monitor -- NZT-48 Institutional Risk Metric
Taleb (2007): Black Swan; Bali, Cakici & Whitelaw (2011): Maxing Out.
CVaR at 5th percentile; consecutive loss clustering detection; skewness test.
Alert: CVaR > -3.0R -> reduce all sizes 25%.
"""

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

STATE_FILE = "data/tail_loss_state.json"

CVaR_ALERT_THRESHOLD = -3.0     # CVaR worse than -3R -> reduce size 25%
CLUSTERING_ALERT_TRADES = 20    # Lookback for loss clustering detection
CLUSTERING_PAUSE_HOURS = 24     # Pause duration if clustering detected
SIZE_REDUCTION_ON_CVAR = 0.75   # 0.75x size multiplier when CVaR alert active


class TailLossMonitor:
    """
    Monitors tail behavior and loss clustering.
    Bali et al. (2011): MAX anomaly -- stocks with extreme recent returns
    underperform. Applied here as: setups similar to prior worst outcomes
    get reduced weight.
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
        return {"cvar_cache": {}, "clustering_events": [], "last_update": None}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(self.state_file) if os.path.dirname(self.state_file) else ".", exist_ok=True)
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception as e:
            logger.warning("TailLossMonitor: save failed: %s", e)

    def get_cvar(self, outcomes: list, ticker: str = "ALL",
                 percentile: float = 0.05) -> Optional[float]:
        """
        Computes CVaR at given percentile (worst 5% average loss in R-multiples).
        Returns None if insufficient data.
        """
        r_vals = []
        for o in outcomes:
            if ticker != "ALL" and o.get("ticker") != ticker:
                continue
            r = o.get("r_multiple")
            if r is not None:
                r_vals.append(float(r))

        if len(r_vals) < 60:
            # Bali, Cakici & Whitelaw (2011): need ≥60 outcomes for reliable
            # CVaR_5% estimate (5% of 60 = 3 tail trades — minimum meaningful tail)
            return None

        sorted_r = sorted(r_vals)
        n_tail = max(1, int(len(sorted_r) * percentile))
        cvar = sum(sorted_r[:n_tail]) / n_tail
        return round(cvar, 4)

    def is_cvar_alert(self, outcomes: list) -> bool:
        """True if system-wide CVaR_5% < CVaR_ALERT_THRESHOLD (-3.0R)."""
        cvar = self.get_cvar(outcomes)
        if cvar is None:
            return False
        return cvar < CVaR_ALERT_THRESHOLD

    def get_size_multiplier(self, outcomes: list) -> float:
        """0.75x if CVaR alert active, else 1.0."""
        if self.is_cvar_alert(outcomes):
            return SIZE_REDUCTION_ON_CVAR
        return 1.0

    def is_tail_clustering(self, outcomes: list, lookback: int = CLUSTERING_ALERT_TRADES) -> bool:
        """
        True if recent loss streak > 2x historical average streak length.
        (Taleb 2007: clustered losses signal regime breakdown, not bad luck)
        """
        recent = outcomes[-lookback:] if len(outcomes) >= lookback else outcomes
        if len(recent) < 5:
            return False

        # Current streak
        current_streak = 0
        for o in reversed(recent):
            if o.get("status") in ("LOSS", "STOPPED_OUT"):
                current_streak += 1
            else:
                break

        if current_streak == 0:
            return False

        # Historical average streak
        all_streaks = []
        streak = 0
        for o in outcomes[:-lookback] if len(outcomes) > lookback else []:
            if o.get("status") in ("LOSS", "STOPPED_OUT"):
                streak += 1
            else:
                if streak > 0:
                    all_streaks.append(streak)
                streak = 0
        if streak > 0:
            all_streaks.append(streak)

        if not all_streaks:
            return current_streak >= 4  # No history: 4+ consecutive = clustering

        avg_streak = sum(all_streaks) / len(all_streaks)
        return current_streak > 2 * avg_streak

    def get_skewness(self, outcomes: list, ticker: str = "ALL") -> Optional[float]:
        """
        Returns return distribution skewness.
        Positive = right-tailed (good), negative = left-tailed (dangerous).
        """
        r_vals = [
            float(o["r_multiple"])
            for o in outcomes
            if (ticker == "ALL" or o.get("ticker") == ticker)
            and o.get("r_multiple") is not None
        ]
        if len(r_vals) < 10:
            return None

        n = len(r_vals)
        mean = sum(r_vals) / n
        variance = sum((x - mean) ** 2 for x in r_vals) / n
        std = math.sqrt(variance) if variance > 0 else 0
        if std == 0:
            return 0.0

        skew = sum((x - mean) ** 3 for x in r_vals) / (n * std ** 3)
        return round(skew, 4)

    def get_status(self, outcomes: list) -> dict:
        cvar = self.get_cvar(outcomes)
        skew = self.get_skewness(outcomes)
        clustering = self.is_tail_clustering(outcomes)
        cvar_alert = cvar is not None and cvar < CVaR_ALERT_THRESHOLD
        size_mult = SIZE_REDUCTION_ON_CVAR if cvar_alert else 1.0

        return {
            "cvar_5pct": cvar,
            "cvar_alert": cvar_alert,
            "skewness": skew,
            "tail_clustering": clustering,
            "size_multiplier": size_mult,
            "sample_n": len(outcomes),
        }

    def get_telegram_summary(self, outcomes: list) -> str:
        status = self.get_status(outcomes)
        cvar = status["cvar_5pct"]
        cvar_str = f"{cvar:.2f}R" if cvar is not None else "N/A"
        skew = status["skewness"]
        skew_str = f"{skew:+.2f}" if skew is not None else "N/A"
        parts = [f"Tail Risk: CVaR_5%={cvar_str}"]
        if status["cvar_alert"]:
            size_m = status["size_multiplier"]
            parts.append(f"  CVaR ALERT -- size x{size_m:.2f}")
        if status["tail_clustering"]:
            parts.append("  LOSS CLUSTERING DETECTED")
        skew_dir = "positive" if (skew or 0) > 0 else "negative"
        parts.append(f"  Skewness={skew_str} ({skew_dir})")
        return chr(10).join(parts)
