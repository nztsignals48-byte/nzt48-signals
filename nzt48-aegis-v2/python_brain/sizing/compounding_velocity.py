#!/usr/bin/env python3
"""
Book 26: Compounding Velocity Tracker
Tracks equity velocity, optimal trade frequency, and capital efficiency.
"""

import json
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
WAL_DIR = Path(os.environ.get("AEGIS_WAL_DIR", "/app/events"))


@dataclass
class TradeRecord:
    """Individual trade record for velocity calculation."""
    timestamp: datetime
    net_pnl: float  # GBP
    gross_pnl: float  # GBP
    cost: float  # GBP (commissions + slippage)
    deployed_capital: float  # GBP
    duration_seconds: float  # Time capital was deployed


@dataclass
class VelocityMetrics:
    """Velocity and frequency metrics."""
    velocity_5d: float = 0.0  # GBP/day
    velocity_20d: float = 0.0
    velocity_60d: float = 0.0
    negative_days_streak: int = 0
    actual_frequency: float = 0.0  # trades/day
    optimal_frequency: float = 0.0  # f* = E / (E + c)
    frequency_ratio: float = 0.0  # actual / optimal
    cash_efficiency: float = 0.0  # eta = deployed / equity
    recycling_events: int = 0  # Intraday compounding events
    last_updated: str = ""


class VelocityTracker:
    """Tracks compounding velocity and capital efficiency."""

    def __init__(self, state_file: str):
        self.state_file = Path(state_file)
        self.trades: deque = deque(maxlen=120)  # ~60 days @ 2 trades/day
        self.daily_velocities: deque = deque(maxlen=60)
        self.equity: float = 10000.0  # Default ISA size
        self.metrics = VelocityMetrics()
        self._load_state()

    def _load_state(self):
        """Load persistent state from disk."""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
                self.equity = data.get("equity", 10000.0)
                self.metrics.negative_days_streak = data.get("negative_days_streak", 0)
                self.metrics.recycling_events = data.get("recycling_events", 0)

                # Restore trade history
                for t in data.get("trades", []):
                    self.trades.append(TradeRecord(
                        timestamp=datetime.fromisoformat(t["timestamp"]),
                        net_pnl=t["net_pnl"],
                        gross_pnl=t["gross_pnl"],
                        cost=t["cost"],
                        deployed_capital=t["deployed_capital"],
                        duration_seconds=t["duration_seconds"]
                    ))

                # Restore daily velocities
                for dv in data.get("daily_velocities", []):
                    self.daily_velocities.append({
                        "date": datetime.fromisoformat(dv["date"]).date(),
                        "velocity": dv["velocity"]
                    })
        except Exception as e:
            print(f"[Book26] Failed to load state: {e}")

    def _save_state(self):
        """Save persistent state to disk."""
        try:
            data = {
                "equity": self.equity,
                "negative_days_streak": self.metrics.negative_days_streak,
                "recycling_events": self.metrics.recycling_events,
                "trades": [
                    {
                        "timestamp": t.timestamp.isoformat(),
                        "net_pnl": t.net_pnl,
                        "gross_pnl": t.gross_pnl,
                        "cost": t.cost,
                        "deployed_capital": t.deployed_capital,
                        "duration_seconds": t.duration_seconds
                    }
                    for t in self.trades
                ],
                "daily_velocities": [
                    {
                        "date": dv["date"].isoformat(),
                        "velocity": dv["velocity"]
                    }
                    for dv in self.daily_velocities
                ],
                "last_updated": datetime.utcnow().isoformat()
            }

            os.makedirs(self.state_file.parent, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[Book26] Failed to save state: {e}")

    def record_trade(self, trade: TradeRecord):
        """Record a completed trade."""
        self.trades.append(trade)
        self.equity += trade.net_pnl

        # Check for intraday recycling (profit from earlier trade funding later trade)
        same_day_trades = [t for t in self.trades if t.timestamp.date() == trade.timestamp.date()]
        if len(same_day_trades) > 1 and trade.net_pnl > 0:
            # Check if previous trade was profitable
            prev_trades = [t for t in same_day_trades if t.timestamp < trade.timestamp]
            if prev_trades and any(t.net_pnl > 0 for t in prev_trades):
                self.metrics.recycling_events += 1

    def compute_velocity(self) -> VelocityMetrics:
        """Compute velocity metrics across time windows."""
        if not self.trades:
            return self.metrics

        now = datetime.utcnow()

        # Calculate velocities over windows
        for window_days, attr in [(5, "velocity_5d"), (20, "velocity_20d"), (60, "velocity_60d")]:
            cutoff = now - timedelta(days=window_days)
            window_trades = [t for t in self.trades if t.timestamp >= cutoff]

            if window_trades:
                total_pnl = sum(t.net_pnl for t in window_trades)
                actual_days = (now - window_trades[0].timestamp).days + 1
                velocity = total_pnl / max(actual_days, 1)
                setattr(self.metrics, attr, velocity)

        # Track negative velocity streaks
        today = now.date()
        today_trades = [t for t in self.trades if t.timestamp.date() == today]
        if today_trades:
            today_velocity = sum(t.net_pnl for t in today_trades)
            if today_velocity < 0:
                self.metrics.negative_days_streak += 1
            else:
                self.metrics.negative_days_streak = 0

            # Update daily velocities
            if not self.daily_velocities or self.daily_velocities[-1]["date"] != today:
                self.daily_velocities.append({"date": today, "velocity": today_velocity})

        # Optimal frequency: f* = E / (E + c)
        if len(self.trades) >= 10:
            avg_gross_edge = sum(t.gross_pnl for t in self.trades) / len(self.trades)
            avg_cost = sum(t.cost for t in self.trades) / len(self.trades)

            if avg_gross_edge > 0 and avg_cost > 0:
                self.metrics.optimal_frequency = avg_gross_edge / (avg_gross_edge + avg_cost)
            else:
                self.metrics.optimal_frequency = 0.0

            # Actual frequency (trades/day over 20d)
            cutoff_20d = now - timedelta(days=20)
            recent_trades = [t for t in self.trades if t.timestamp >= cutoff_20d]
            if recent_trades:
                days_span = (now - recent_trades[0].timestamp).days + 1
                self.metrics.actual_frequency = len(recent_trades) / max(days_span, 1)

                if self.metrics.optimal_frequency > 0:
                    self.metrics.frequency_ratio = self.metrics.actual_frequency / self.metrics.optimal_frequency
                else:
                    self.metrics.frequency_ratio = 0.0

        # Cash efficiency: eta = time_weighted_deployed / equity
        if self.trades and self.equity > 0:
            cutoff_5d = now - timedelta(days=5)
            recent_trades = [t for t in self.trades if t.timestamp >= cutoff_5d]

            if recent_trades:
                total_capital_seconds = sum(t.deployed_capital * t.duration_seconds for t in recent_trades)
                window_seconds = 5 * 24 * 3600
                avg_deployed = total_capital_seconds / window_seconds if window_seconds > 0 else 0
                self.metrics.cash_efficiency = avg_deployed / self.equity if self.equity > 0 else 0.0

        self.metrics.last_updated = now.isoformat()
        self._save_state()
        return self.metrics

    def summary(self) -> Dict:
        """Generate summary dict for pipeline."""
        m = self.metrics
        warnings = []

        if m.negative_days_streak >= 5:
            warnings.append(f"Velocity negative for {m.negative_days_streak} consecutive days")

        if m.frequency_ratio > 1.5:
            warnings.append(f"Over-trading: {m.frequency_ratio:.2f}x optimal frequency")
        elif m.frequency_ratio < 0.5 and m.frequency_ratio > 0:
            warnings.append(f"Under-trading: {m.frequency_ratio:.2f}x optimal frequency")

        if m.cash_efficiency < 0.4:
            warnings.append(f"Low capital utilization: {m.cash_efficiency:.1%}")
        elif m.cash_efficiency > 0.6:
            warnings.append(f"High capital utilization: {m.cash_efficiency:.1%}")

        return {
            "velocity_5d_gbp_per_day": round(m.velocity_5d, 2),
            "velocity_20d_gbp_per_day": round(m.velocity_20d, 2),
            "velocity_60d_gbp_per_day": round(m.velocity_60d, 2),
            "negative_streak_days": m.negative_days_streak,
            "actual_frequency": round(m.actual_frequency, 2),
            "optimal_frequency": round(m.optimal_frequency, 2),
            "frequency_ratio": round(m.frequency_ratio, 2),
            "cash_efficiency": round(m.cash_efficiency, 3),
            "recycling_events": m.recycling_events,
            "total_trades": len(self.trades),
            "current_equity_gbp": round(self.equity, 2),
            "warnings": warnings,
            "last_updated": m.last_updated
        }


# Singleton instance
_velocity_tracker: Optional[VelocityTracker] = None


def get_velocity_tracker() -> VelocityTracker:
    """Get or create singleton VelocityTracker."""
    global _velocity_tracker
    if _velocity_tracker is None:
        state_file = Path(DATA_DIR) / "compounding_velocity.json"
        _velocity_tracker = VelocityTracker(str(state_file))
    return _velocity_tracker


def run_velocity_nightly() -> Dict:
    """Nightly pipeline step: compute velocity metrics."""
    tracker = get_velocity_tracker()

    # Load trades from WAL if available
    if WAL_DIR.exists():
        # Scan for trade execution events in WAL
        for wal_file in sorted(WAL_DIR.glob("*.jsonl"))[-7:]:  # Last 7 days
            try:
                with open(wal_file, "r") as f:
                    for line in f:
                        try:
                            event = json.loads(line.strip())
                            if event.get("event") == "trade_closed":
                                data = event.get("data", {})
                                tracker.record_trade(TradeRecord(
                                    timestamp=datetime.fromisoformat(event.get("timestamp", datetime.utcnow().isoformat())),
                                    net_pnl=data.get("net_pnl", 0.0),
                                    gross_pnl=data.get("gross_pnl", 0.0),
                                    cost=data.get("cost", 0.0),
                                    deployed_capital=data.get("deployed_capital", 0.0),
                                    duration_seconds=data.get("duration_seconds", 0.0)
                                ))
                        except (json.JSONDecodeError, KeyError):
                            continue
            except Exception as e:
                print(f"[Book26] Error reading WAL {wal_file}: {e}")

    # Compute metrics
    tracker.compute_velocity()
    summary = tracker.summary()

    print(f"[Book26] Velocity 5d: {summary['velocity_5d_gbp_per_day']} GBP/day")
    print(f"[Book26] Frequency ratio: {summary['frequency_ratio']:.2f}x optimal")
    print(f"[Book26] Cash efficiency: {summary['cash_efficiency']:.1%}")

    if summary["warnings"]:
        print(f"[Book26] Warnings: {', '.join(summary['warnings'])}")

    return summary


if __name__ == "__main__":
    print("[Book26] Compounding Velocity Tracker")
    result = run_velocity_nightly()
    print(json.dumps(result, indent=2))
