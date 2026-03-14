"""
NZT-48 Learning Module 8: Strategy Tournament
Darwinian capital allocation — winning strategies get more capital, losers get benched.

Each strategy starts with 1000 tournament points.
After each trade:
  - Winner: +50 * R-multiple (capped at +200)
  - Loser:  -30 * abs(R-multiple) (capped at -150)

Position size multiplier = strategy_points / 1000, bounded [0.5x, 2.0x].

Auto-bench:  < 500 points after 20+ trades = BENCHED (no new trades).
Auto-reinstate: Monitor OOS for 10 signals. If avg predicted R > 0, reinstate at 750 points.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("nzt48.learning.tournament")

# ── Constants ──────────────────────────────────────────────────────────────────
STARTING_POINTS = 1000.0
WIN_MULTIPLIER = 50.0       # Points per 1R win
WIN_CAP = 200.0             # Max points awarded per winning trade
LOSS_MULTIPLIER = 30.0      # Points deducted per 1R loss
LOSS_CAP = 150.0            # Max points deducted per losing trade
SIZE_MIN = 0.5              # Floor multiplier (never less than half size)
SIZE_MAX = 2.0              # Ceiling multiplier (never more than double)
BENCH_THRESHOLD = 500.0     # Points below this → benched
BENCH_MIN_TRADES = 20       # Must have this many trades before benching kicks in
REINSTATE_OOS_COUNT = 10    # OOS signals needed to consider reinstatement
REINSTATE_POINTS = 750.0    # Points assigned on reinstatement


class StrategyTournament:
    """Darwinian capital allocation — winning strategies get more, losers get benched.

    Integrates with risk_sizer via get_size_multiplier() and with the
    pipeline's signal filter via is_benched().
    """

    def __init__(self) -> None:
        self.points: dict[str, float] = defaultdict(lambda: STARTING_POINTS)
        self.trade_counts: dict[str, int] = defaultdict(int)
        self.benched: set[str] = set()
        self.oos_buffer: dict[str, list[float]] = defaultdict(list)
        self._alerts: list[dict] = []

    # ── Core trade recording ───────────────────────────────────────────────

    def record_trade(self, strategy: str, r_multiple: float) -> Optional[dict]:
        """Record a completed trade and adjust tournament points.

        Args:
            strategy: Strategy identifier (e.g. "S1_breakout", "S3_mean_rev").
            r_multiple: Closed trade result in R-multiples (+1.5 = 1.5R win,
                        -1.0 = full stop-loss hit).

        Returns:
            Alert dict if strategy state changed (BENCHED or notable), else None.
        """
        # Ensure strategy exists in points table
        if strategy not in self.points:
            self.points[strategy] = STARTING_POINTS

        old_points = self.points[strategy]
        self.trade_counts[strategy] += 1

        # Calculate point delta
        if r_multiple > 0:
            delta = min(WIN_MULTIPLIER * r_multiple, WIN_CAP)
        else:
            delta = -min(LOSS_MULTIPLIER * abs(r_multiple), LOSS_CAP)

        # Apply — floor at zero (can't go negative)
        self.points[strategy] = max(0.0, old_points + delta)
        new_points = self.points[strategy]

        logger.info(
            "TOURNAMENT: %s trade R=%.2f | points %.0f -> %.0f (delta %+.0f) | trades=%d",
            strategy, r_multiple, old_points, new_points, delta,
            self.trade_counts[strategy],
        )

        # Check for auto-bench
        if (
            new_points < BENCH_THRESHOLD
            and self.trade_counts[strategy] >= BENCH_MIN_TRADES
            and strategy not in self.benched
        ):
            self.benched.add(strategy)
            alert = {
                "type": "STRATEGY_BENCHED",
                "strategy": strategy,
                "points": round(new_points, 1),
                "trades": self.trade_counts[strategy],
                "reason": (
                    f"Points {new_points:.0f} < {BENCH_THRESHOLD} "
                    f"after {self.trade_counts[strategy]} trades"
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._alerts.append(alert)
            logger.warning(
                "BENCHED: %s — points=%.0f trades=%d. No new trades until OOS recovery.",
                strategy, new_points, self.trade_counts[strategy],
            )
            return alert

        return None

    # ── Position sizing interface ──────────────────────────────────────────

    def get_size_multiplier(self, strategy: str) -> float:
        """Get position size multiplier for a strategy.

        Returns a float in [0.5, 2.0]:
          - 1000 points = 1.0x (baseline)
          - 2000 points = 2.0x (capped)
          -  500 points = 0.5x (floor, also bench threshold)

        Benched strategies return 0.0 — they should not trade.
        """
        if strategy in self.benched:
            return 0.0

        pts = self.points.get(strategy, STARTING_POINTS)
        raw = pts / STARTING_POINTS
        clamped = max(SIZE_MIN, min(SIZE_MAX, raw))
        return round(clamped, 3)

    # ── Bench status ───────────────────────────────────────────────────────

    def is_benched(self, strategy: str) -> bool:
        """Check if a strategy is currently benched (no live trading)."""
        return strategy in self.benched

    # ── OOS monitoring for benched strategies ──────────────────────────────

    def record_oos_signal(self, strategy: str, predicted_r: float) -> Optional[dict]:
        """Record an out-of-sample signal for a benched strategy.

        While benched, we still generate signals but don't trade them.
        After REINSTATE_OOS_COUNT signals, if avg predicted R > 0
        the strategy is reinstated at REINSTATE_POINTS.

        Args:
            strategy: Strategy identifier.
            predicted_r: The R-multiple the signal would have produced.

        Returns:
            Alert dict if strategy reinstated, else None.
        """
        if strategy not in self.benched:
            return None

        self.oos_buffer[strategy].append(predicted_r)
        logger.debug(
            "OOS signal for benched %s: predicted_R=%.2f (%d/%d)",
            strategy, predicted_r,
            len(self.oos_buffer[strategy]), REINSTATE_OOS_COUNT,
        )

        if len(self.oos_buffer[strategy]) >= REINSTATE_OOS_COUNT:
            avg_r = sum(self.oos_buffer[strategy]) / len(self.oos_buffer[strategy])

            if avg_r > 0:
                # Reinstate
                self.benched.discard(strategy)
                self.points[strategy] = REINSTATE_POINTS
                self.oos_buffer[strategy].clear()

                alert = {
                    "type": "STRATEGY_REINSTATED",
                    "strategy": strategy,
                    "oos_avg_r": round(avg_r, 3),
                    "oos_signals": REINSTATE_OOS_COUNT,
                    "new_points": REINSTATE_POINTS,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._alerts.append(alert)
                logger.info(
                    "REINSTATED: %s — OOS avg R=%.3f over %d signals. "
                    "Starting at %.0f points.",
                    strategy, avg_r, REINSTATE_OOS_COUNT, REINSTATE_POINTS,
                )
                return alert
            else:
                # Still underperforming — clear buffer, keep monitoring
                self.oos_buffer[strategy].clear()
                logger.info(
                    "OOS review for %s: avg_R=%.3f — still negative. "
                    "Remains benched, buffer reset.",
                    strategy, avg_r,
                )

        return None

    # ── Leaderboard ────────────────────────────────────────────────────────

    def get_leaderboard(self) -> list[dict]:
        """Get all strategies ranked by tournament points (descending).

        Returns list of dicts with: strategy, points, trades,
        size_multiplier, benched.
        """
        # Gather all known strategies (union of points and trade_counts keys)
        all_strategies = set(self.points.keys()) | set(self.trade_counts.keys())

        rows = []
        for strategy in all_strategies:
            pts = self.points.get(strategy, STARTING_POINTS)
            trades = self.trade_counts.get(strategy, 0)
            rows.append({
                "strategy": strategy,
                "points": round(pts, 1),
                "trades": trades,
                "size_multiplier": self.get_size_multiplier(strategy),
                "benched": strategy in self.benched,
            })

        return sorted(rows, key=lambda r: r["points"], reverse=True)

    # ── Telegram formatting ────────────────────────────────────────────────

    def to_telegram(self) -> str:
        """Format tournament standings for Telegram notification."""
        leaderboard = self.get_leaderboard()

        if not leaderboard:
            return "TOURNAMENT: No strategies tracked yet."

        lines = ["STRATEGY TOURNAMENT"]
        lines.append("-" * 30)

        for i, row in enumerate(leaderboard, 1):
            status = "BENCHED" if row["benched"] else f"{row['size_multiplier']:.1f}x"
            lines.append(
                f"{i}. {row['strategy']}  "
                f"{row['points']:.0f}pts  "
                f"({row['trades']} trades)  "
                f"[{status}]"
            )

        # Summary
        active = sum(1 for r in leaderboard if not r["benched"])
        benched = sum(1 for r in leaderboard if r["benched"])
        lines.append("-" * 30)
        lines.append(f"Active: {active} | Benched: {benched}")

        return "\n".join(lines)

    # ── Alerts ─────────────────────────────────────────────────────────────

    def get_alerts(self) -> list[dict]:
        """Get all bench/reinstate alerts."""
        return list(self._alerts)

    # ── Persistence ────────────────────────────────────────────────────────

    def save_state(self, conn: sqlite3.Connection) -> None:
        """Persist tournament state to the learning_state table as a JSON blob."""
        conn.execute(
            """CREATE TABLE IF NOT EXISTS learning_state (
                module TEXT PRIMARY KEY,
                state_json TEXT,
                updated_at TEXT
            )"""
        )

        state = {
            "points": dict(self.points),
            "trade_counts": dict(self.trade_counts),
            "benched": list(self.benched),
            "oos_buffer": {k: v for k, v in self.oos_buffer.items()},
            "alerts": self._alerts[-50:],  # Keep last 50 alerts
        }

        conn.execute(
            "INSERT OR REPLACE INTO learning_state (module, state_json, updated_at) "
            "VALUES (?, ?, ?)",
            ("strategy_tournament", json.dumps(state), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info("Tournament state saved: %d strategies", len(self.points))

    def load_state(self, conn: sqlite3.Connection) -> None:
        """Load tournament state from the learning_state table."""
        try:
            row = conn.execute(
                "SELECT state_json FROM learning_state WHERE module = ?",
                ("strategy_tournament",),
            ).fetchone()
        except Exception:
            return  # Table doesn't exist yet

        if not row:
            return

        raw = row["state_json"] if isinstance(row, sqlite3.Row) else row[0]
        state = json.loads(raw)

        # Restore points (using defaultdict so missing keys still work)
        for strategy, pts in state.get("points", {}).items():
            self.points[strategy] = pts

        for strategy, count in state.get("trade_counts", {}).items():
            self.trade_counts[strategy] = count

        self.benched = set(state.get("benched", []))

        for strategy, signals in state.get("oos_buffer", {}).items():
            self.oos_buffer[strategy] = signals

        self._alerts = state.get("alerts", [])

        logger.info(
            "Tournament state loaded: %d strategies, %d benched",
            len(self.points), len(self.benched),
        )


# ── Quick self-test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

    t = StrategyTournament()

    # Simulate some trades
    print("=== Strategy Tournament Self-Test ===\n")

    # S1 has a great run
    for _ in range(10):
        t.record_trade("S1_breakout", 1.5)
    print(f"S1 after 10 wins at 1.5R: {t.points['S1_breakout']:.0f} pts, "
          f"size={t.get_size_multiplier('S1_breakout'):.2f}x")

    # S2 has a rough patch
    for _ in range(25):
        t.record_trade("S2_mean_rev", -1.0)
    print(f"S2 after 25 losses at -1R: {t.points['S2_mean_rev']:.0f} pts, "
          f"benched={t.is_benched('S2_mean_rev')}")

    # S3 stays average
    for i in range(15):
        r = 0.5 if i % 2 == 0 else -0.3
        t.record_trade("S3_momentum", r)
    print(f"S3 mixed results: {t.points['S3_momentum']:.0f} pts, "
          f"size={t.get_size_multiplier('S3_momentum'):.2f}x")

    # Show leaderboard
    print(f"\n{t.to_telegram()}")

    # Test OOS reinstatement for S2
    print("\n--- OOS monitoring for benched S2 ---")
    for i in range(10):
        alert = t.record_oos_signal("S2_mean_rev", 0.3)
        if alert:
            print(f"  REINSTATED: {alert}")
    print(f"S2 after OOS: benched={t.is_benched('S2_mean_rev')}, "
          f"points={t.points['S2_mean_rev']:.0f}")

    # Test persistence
    print("\n--- Persistence test ---")
    conn = sqlite3.connect(":memory:")
    t.save_state(conn)
    t2 = StrategyTournament()
    t2.load_state(conn)
    print(f"Loaded {len(t2.points)} strategies from DB")
    assert t2.points["S1_breakout"] == t.points["S1_breakout"], "Points mismatch!"
    assert t2.trade_counts["S1_breakout"] == t.trade_counts["S1_breakout"], "Trades mismatch!"
    assert t2.is_benched("S2_mean_rev") == t.is_benched("S2_mean_rev"), "Bench mismatch!"
    print("All persistence assertions passed.")

    conn.close()
    print("\n=== Self-test complete ===")
