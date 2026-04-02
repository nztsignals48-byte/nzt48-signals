"""Book 14: Alpha Decay Detector.

Monitors per-strategy rolling Sharpe ratio to detect edge decay.
When a strategy's rolling 60-day Sharpe drops below 50% of its peak,
it's flagged as DECAYING. Persistent decline triggers KILL recommendation.

States:
  HEALTHY:  Rolling SR >= 50% of peak SR
  DECAYING: Rolling SR < 50% of peak for 14+ consecutive days
  KILL:     Rolling SR < 0 for 14+ consecutive days

Wired into nightly pipeline Step 18.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
DECAY_FILE = os.path.join(DATA_DIR, "alpha_decay_state.json")
PNL_FILE = os.path.join(DATA_DIR, "strategy_pnl_history.json")


@dataclass
class StrategyDecayState:
    peak_sharpe: float = 0.0
    rolling_sharpe: float = 0.0
    days_below_threshold: int = 0
    days_below_zero: int = 0
    status: str = "HEALTHY"  # HEALTHY, DECAYING, KILL
    allocation_scale: float = 1.0
    last_updated: str = ""


class AlphaDecayDetector:
    """Detects alpha decay in live strategies using rolling Sharpe."""

    DECAY_THRESHOLD = 0.50  # Flag when rolling SR < 50% of peak
    DECAY_DAYS = 14         # Consecutive days before flagging
    KILL_DAYS = 14          # Consecutive days with SR < 0
    ROLLING_WINDOW = 60     # 60 trading days

    def __init__(self):
        self._states: Dict[str, StrategyDecayState] = {}

    def update(self, strategy: str, daily_returns: List[float]):
        """Update decay state for a strategy with its recent daily returns."""
        if strategy not in self._states:
            self._states[strategy] = StrategyDecayState()
        state = self._states[strategy]

        # Compute rolling Sharpe
        n = min(self.ROLLING_WINDOW, len(daily_returns))
        if n < 10:
            state.status = "INSUFFICIENT_DATA"
            state.last_updated = time.strftime("%Y-%m-%d")
            return

        recent = daily_returns[-n:]
        sr = self._sharpe(recent)
        state.rolling_sharpe = round(sr, 4)

        # Track peak
        if sr > state.peak_sharpe:
            state.peak_sharpe = round(sr, 4)

        # Check decay
        threshold = state.peak_sharpe * self.DECAY_THRESHOLD
        if sr < threshold and state.peak_sharpe > 0.1:
            state.days_below_threshold += 1
        else:
            state.days_below_threshold = 0

        if sr < 0:
            state.days_below_zero += 1
        else:
            state.days_below_zero = 0

        # Determine status
        if state.days_below_zero >= self.KILL_DAYS:
            state.status = "KILL"
            state.allocation_scale = 0.25
        elif state.days_below_threshold >= self.DECAY_DAYS:
            state.status = "DECAYING"
            state.allocation_scale = 0.50
        else:
            state.status = "HEALTHY"
            state.allocation_scale = 1.0

        state.last_updated = time.strftime("%Y-%m-%d")

    def check_decay(self, strategy: str) -> StrategyDecayState:
        """Get current decay state for a strategy."""
        return self._states.get(strategy, StrategyDecayState())

    def status_report(self) -> Dict:
        """Generate a report of all strategy decay states."""
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "strategies": {},
            "alerts": [],
        }

        for name, state in self._states.items():
            report["strategies"][name] = {
                "status": state.status,
                "rolling_sr": state.rolling_sharpe,
                "peak_sr": state.peak_sharpe,
                "decay_ratio": round(state.rolling_sharpe / max(state.peak_sharpe, 0.01), 2),
                "days_decaying": state.days_below_threshold,
                "days_negative": state.days_below_zero,
                "allocation_scale": state.allocation_scale,
            }

            if state.status == "KILL":
                report["alerts"].append(
                    f"KILL: {name} — SR={state.rolling_sharpe:.2f} < 0 for "
                    f"{state.days_below_zero} days (peak={state.peak_sharpe:.2f})"
                )
            elif state.status == "DECAYING":
                report["alerts"].append(
                    f"DECAY: {name} — SR={state.rolling_sharpe:.2f} < "
                    f"{state.peak_sharpe * self.DECAY_THRESHOLD:.2f} threshold for "
                    f"{state.days_below_threshold} days"
                )

        report["summary"] = {
            "healthy": sum(1 for s in self._states.values() if s.status == "HEALTHY"),
            "decaying": sum(1 for s in self._states.values() if s.status == "DECAYING"),
            "kill": sum(1 for s in self._states.values() if s.status == "KILL"),
        }

        return report

    def save(self):
        try:
            os.makedirs(os.path.dirname(DECAY_FILE), exist_ok=True)
            data = {}
            for name, state in self._states.items():
                data[name] = {
                    "peak_sharpe": state.peak_sharpe,
                    "rolling_sharpe": state.rolling_sharpe,
                    "days_below_threshold": state.days_below_threshold,
                    "days_below_zero": state.days_below_zero,
                    "status": state.status,
                    "allocation_scale": state.allocation_scale,
                    "last_updated": state.last_updated,
                }
            with open(DECAY_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def load(self):
        if not os.path.exists(DECAY_FILE):
            return
        try:
            with open(DECAY_FILE) as f:
                data = json.load(f)
            for name, d in data.items():
                self._states[name] = StrategyDecayState(**d)
        except Exception:
            pass

    @staticmethod
    def _sharpe(returns: List[float]) -> float:
        if len(returns) < 2:
            return 0.0
        n = len(returns)
        mean_r = sum(returns) / n
        var_r = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
        if var_r <= 0:
            return 0.0
        return (mean_r / math.sqrt(var_r)) * math.sqrt(252)


# ─── Singleton ────────────────────────────────────────────────────────────────

_detector: Optional[AlphaDecayDetector] = None


def get_decay_detector() -> AlphaDecayDetector:
    global _detector
    if _detector is None:
        _detector = AlphaDecayDetector()
        _detector.load()
    return _detector


# ─── Nightly Runner ───────────────────────────────────────────────────────────

def run_decay_analysis() -> Dict:
    """Nightly: load P&L data, update decay states, save, return report."""
    detector = get_decay_detector()

    # Load strategy P&L history
    if os.path.exists(PNL_FILE):
        try:
            with open(PNL_FILE) as f:
                pnl_data = json.load(f)
            for strategy, returns in pnl_data.items():
                if isinstance(returns, list) and len(returns) >= 10:
                    detector.update(strategy, returns)
        except Exception:
            pass

    report = detector.status_report()
    detector.save()

    # Send Telegram alerts for DECAY/KILL
    if report.get("alerts"):
        try:
            from python_brain.ouroboros.claude_helper import send_telegram
            msg = "ALPHA DECAY REPORT\n\n" + "\n".join(report["alerts"])
            send_telegram(msg)
        except Exception:
            pass

    return report


def compute_signal_quality_scores(
    trades_file: str = "/app/data/trades_live.json",
    output_file: str = "/app/data/signal_quality_scores.json",
) -> Dict[str, Any]:
    """Score signal quality using IC, Sharpe, max DD, hit rate (Book 14).

    Runs nightly after trades_live.json written. Computes per-strategy
    quality metrics and adjusts confidence_floor for next day.

    Args:
        trades_file: Path to trades_live.json from Step 4b.
        output_file: Path to write signal_quality_scores.json.

    Returns:
        Dict with per-strategy quality scores.
    """
    if not os.path.exists(trades_file):
        return {"status": "no_trades", "strategies": {}}

    try:
        with open(trades_file) as f:
            trades = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {"status": "invalid_trades_file", "strategies": {}}

    # Group trades by strategy
    strategy_trades = defaultdict(list)
    for trade in trades:
        strat = trade.get("strategy", "unknown")
        strategy_trades[strat].append(trade)

    scores = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "strategies": {},
        "recommended_confidence_floor": 0.55,
    }

    for strategy, strat_trades in strategy_trades.items():
        if len(strat_trades) < 5:
            scores["strategies"][strategy] = {
                "status": "insufficient_trades",
                "n_trades": len(strat_trades),
            }
            continue

        # Extract metrics
        returns = []
        hit_count = 0
        max_dd = 0.0
        cumulative = 1.0

        for trade in strat_trades:
            pnl = trade.get("pnl", trade.get("realized_pnl", 0.0))
            if pnl is None:
                continue
            returns.append(pnl)
            if pnl > 0:
                hit_count += 1
            cumulative *= (1 + pnl)
            drawdown = 1.0 - cumulative / max(cumulative, 1.0)
            max_dd = max(max_dd, drawdown)

        if not returns:
            scores["strategies"][strategy] = {"status": "no_valid_pnl"}
            continue

        # Compute Sharpe, hit rate, IC (proxy: correlation to entry confidence)
        n = len(returns)
        mean_r = sum(returns) / n
        var_r = sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1)
        sharpe = (mean_r / math.sqrt(var_r)) * math.sqrt(252) if var_r > 0 else 0.0
        hit_rate = hit_count / n

        # IC (Information Coefficient): correlation of confidence to PnL sign
        confidences = [trade.get("confidence", 50) for trade in strat_trades]
        pnl_signs = [1.0 if pnl > 0 else 0.0 for pnl in returns]
        ic = _compute_correlation(confidences, pnl_signs)

        scores["strategies"][strategy] = {
            "n_trades": n,
            "sharpe": round(sharpe, 3),
            "hit_rate": round(hit_rate, 3),
            "max_drawdown": round(max_dd, 3),
            "ic": round(ic, 3),
            "quality_score": round((hit_rate * 0.3 + max(0, sharpe / 2.0) * 0.4 + max(ic, 0) * 0.3), 3),
        }

    # Recommend confidence floor adjustment
    quality_scores = [s.get("quality_score", 0.5) for s in scores["strategies"].values()]
    if quality_scores:
        avg_quality = sum(quality_scores) / len(quality_scores)
        if avg_quality > 0.7:
            scores["recommended_confidence_floor"] = 0.55
        elif avg_quality > 0.5:
            scores["recommended_confidence_floor"] = 0.60
        else:
            scores["recommended_confidence_floor"] = 0.70

    # Save
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(scores, f, indent=2)
        log.info(f"Signal quality scores saved to {output_file}")
    except Exception as e:
        log.error(f"Failed to save signal quality scores: {e}")

    return scores


def _compute_correlation(x: List[float], y: List[float]) -> float:
    """Compute Pearson correlation between x and y."""
    if len(x) < 2 or len(y) < 2 or len(x) != len(y):
        return 0.0
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    cov = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n)) / (n - 1)
    var_x = sum((xi - mean_x) ** 2 for xi in x) / (n - 1)
    var_y = sum((yi - mean_y) ** 2 for yi in y) / (n - 1)
    if var_x > 0 and var_y > 0:
        return cov / math.sqrt(var_x * var_y)
    return 0.0


if __name__ == "__main__":
    report = run_decay_analysis()
    print(f"Alpha Decay: {report.get('summary', {})}")
    for alert in report.get("alerts", []):
        print(f"  {alert}")
