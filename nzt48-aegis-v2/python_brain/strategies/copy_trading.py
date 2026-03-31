"""Copy Trading & Signal Following Architecture — Book 203.

Internal copy trading: identifies best-performing strategy configurations
and proportionally replicates their signals. Capital flows toward demonstrated
edge and away from underperformance — a Darwinian allocation mechanism.

Key concepts:
  - Leader selection via multi-criteria ranking (Sharpe, drawdown, consistency)
  - Proportional signal replication with equity-adjusted sizing
  - Lag penalty: confidence decays with signal age
  - Anti-herding: limits how many followers can track one leader

Data paths:
  - /app/data/copy_trading_state.json — leader registry + performance
  - /app/data/copy_trading_signals.ndjson — replicated signal log

Bridge.py integration:
    try:
        from python_brain.strategies.copy_trading import (
            CopyTradingManager, SignalReplicator, LeaderSelector, LeaderMetrics,
        )
    except ImportError:
        pass

Usage:
    mgr = CopyTradingManager(max_leaders=3)
    mgr.add_leader("trend_surfer_A", LeaderMetrics(
        leader_id="trend_surfer_A", sharpe=1.8, max_dd=0.06,
        win_rate=0.58, n_trades=120, consistency_score=0.85,
    ))
    result = mgr.process_leader_signal("trend_surfer_A", signal_dict)
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

try:
    import numpy as np
except ImportError:
    pass

log = logging.getLogger("copy_trading")

__all__ = [
    "LeaderMetrics",
    "LeaderSelector",
    "SignalReplicator",
    "CopyTradingManager",
]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DATA_DIR = Path("/app/data")
STATE_PATH = DATA_DIR / "copy_trading_state.json"
SIGNALS_PATH = DATA_DIR / "copy_trading_signals.ndjson"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class LeaderMetrics:
    """Performance metrics for a leader strategy configuration.

    Attributes:
        leader_id: Unique identifier for the leader (e.g., strategy name + param set).
        sharpe: Annualised Sharpe ratio (or rolling window Sharpe).
        max_dd: Maximum drawdown as fraction (e.g., 0.06 = 6%).
        win_rate: Fraction of winning trades (0.0 to 1.0).
        n_trades: Number of trades in evaluation period.
        consistency_score: Stability of rolling Sharpe (0.0 to 1.0). Higher = more consistent.
    """
    leader_id: str = ""
    sharpe: float = 0.0
    max_dd: float = 0.0
    win_rate: float = 0.0
    n_trades: int = 0
    consistency_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to dict."""
        return {
            "leader_id": self.leader_id,
            "sharpe": self.sharpe,
            "max_dd": self.max_dd,
            "win_rate": self.win_rate,
            "n_trades": self.n_trades,
            "consistency_score": self.consistency_score,
        }


# ---------------------------------------------------------------------------
# Leader Selection
# ---------------------------------------------------------------------------
class LeaderSelector:
    """Multi-criteria ranking of leader strategy configurations.

    Ranks candidates by a composite score incorporating Sharpe ratio,
    drawdown, win rate, trade count, and consistency. Filters out
    candidates below minimum Sharpe threshold.
    """

    def __init__(
        self,
        min_sharpe: float = 0.5,
        min_trades: int = 30,
        max_drawdown: float = 0.20,
        weights: Optional[Dict[str, float]] = None,
    ) -> None:
        """Initialise leader selector.

        Args:
            min_sharpe: Minimum Sharpe ratio to be considered.
            min_trades: Minimum number of trades for statistical significance.
            max_drawdown: Maximum acceptable drawdown (rejects above this).
            weights: Optional custom weights for ranking components.
                     Keys: 'sharpe', 'drawdown', 'win_rate', 'consistency'.
        """
        self._min_sharpe = min_sharpe
        self._min_trades = min_trades
        self._max_drawdown = max_drawdown
        self._weights = weights or {
            "sharpe": 0.40,
            "drawdown": 0.20,
            "win_rate": 0.15,
            "consistency": 0.25,
        }
        log.info(
            "LeaderSelector: min_sharpe=%.2f min_trades=%d max_dd=%.2f",
            min_sharpe, min_trades, max_drawdown,
        )

    def rank(self, candidates: List[LeaderMetrics]) -> List[LeaderMetrics]:
        """Rank candidates by composite multi-criteria score.

        Pipeline:
          1. Filter by minimum trade count
          2. Filter by minimum Sharpe
          3. Filter by maximum drawdown
          4. Score remaining candidates
          5. Sort descending by composite score

        Args:
            candidates: List of LeaderMetrics to rank.

        Returns:
            Sorted list (best first) of qualifying candidates.
        """
        if not candidates:
            return []

        # Filter
        filtered = [c for c in candidates if c.n_trades >= self._min_trades]
        filtered = self._sharpe_filter(filtered, self._min_sharpe)
        filtered = [c for c in filtered if c.max_dd <= self._max_drawdown]

        if not filtered:
            log.info("No candidates passed all filters from %d initial", len(candidates))
            return []

        # Score
        scored = []
        for c in filtered:
            score = self._composite_score(c)
            scored.append((score, c))

        scored.sort(key=lambda x: x[0], reverse=True)
        ranked = [c for _, c in scored]

        log.info(
            "Ranked %d leaders from %d candidates. Top: %s (score=%.3f)",
            len(ranked), len(candidates),
            ranked[0].leader_id if ranked else "none",
            scored[0][0] if scored else 0.0,
        )
        return ranked

    def _sharpe_filter(
        self,
        candidates: List[LeaderMetrics],
        min_sharpe: float = 0.5,
    ) -> List[LeaderMetrics]:
        """Filter candidates below minimum Sharpe ratio.

        Args:
            candidates: List of LeaderMetrics.
            min_sharpe: Minimum Sharpe threshold.

        Returns:
            Filtered list.
        """
        return [c for c in candidates if c.sharpe >= min_sharpe]

    def _consistency_score(self, returns: List[float]) -> float:
        """Compute consistency as the inverse coefficient of variation of rolling Sharpe.

        A strategy with stable rolling Sharpe (low CV) scores high.
        Uses a 20-period rolling window.

        Args:
            returns: List of per-trade or per-period returns.

        Returns:
            Consistency score in [0.0, 1.0]. Higher = more consistent.
        """
        if len(returns) < 20:
            return 0.5  # insufficient data — neutral

        arr = np.array(returns, dtype=np.float64)
        window = 20
        rolling_sharpes = []

        for i in range(window, len(arr) + 1):
            chunk = arr[i - window:i]
            mu = float(np.mean(chunk))
            std = float(np.std(chunk))
            if std > 1e-10:
                rolling_sharpes.append(mu / std)

        if not rolling_sharpes:
            return 0.5

        rs_arr = np.array(rolling_sharpes, dtype=np.float64)
        mean_rs = float(np.mean(rs_arr))
        std_rs = float(np.std(rs_arr))

        if abs(mean_rs) < 1e-10:
            return 0.5

        cv = std_rs / abs(mean_rs)  # coefficient of variation
        # Map CV to [0, 1]: CV=0 → score=1, CV>=2 → score=0
        score = max(0.0, 1.0 - cv / 2.0)
        return score

    def _composite_score(self, m: LeaderMetrics) -> float:
        """Compute weighted composite score for a candidate.

        Components (all normalised to approximate [0, 1]):
          - Sharpe: capped at 3.0, divided by 3.0
          - Drawdown: inverted (lower DD = higher score)
          - Win rate: already in [0, 1]
          - Consistency: already in [0, 1]

        Args:
            m: LeaderMetrics for the candidate.

        Returns:
            Composite score (higher is better).
        """
        w = self._weights
        sharpe_norm = min(m.sharpe, 3.0) / 3.0
        dd_norm = 1.0 - min(m.max_dd, 1.0)  # lower DD = higher score
        wr_norm = m.win_rate
        cons_norm = m.consistency_score

        score = (
            w.get("sharpe", 0.4) * sharpe_norm
            + w.get("drawdown", 0.2) * dd_norm
            + w.get("win_rate", 0.15) * wr_norm
            + w.get("consistency", 0.25) * cons_norm
        )
        return score


# ---------------------------------------------------------------------------
# Signal Replication
# ---------------------------------------------------------------------------
class SignalReplicator:
    """Proportionally replicates leader signals adjusted for own equity.

    Handles:
      - Equity-proportional sizing (leader's size scaled to our capital)
      - Lag penalty: older signals get reduced confidence
      - Signal validation before replication
    """

    def __init__(
        self,
        scaling_factor: float = 1.0,
        max_lag_seconds: float = 5.0,
        min_confidence: float = 30.0,
    ) -> None:
        """Initialise signal replicator.

        Args:
            scaling_factor: Global scaling multiplier (1.0 = full replication).
            max_lag_seconds: Maximum acceptable lag before signal is discarded.
            min_confidence: Minimum confidence after lag penalty to still replicate.
        """
        self._scaling_factor = scaling_factor
        self._max_lag_seconds = max_lag_seconds
        self._min_confidence = min_confidence
        self._replicated_count: int = 0
        self._discarded_count: int = 0
        log.info(
            "SignalReplicator: scale=%.2f max_lag=%.1fs min_conf=%.1f",
            scaling_factor, max_lag_seconds, min_confidence,
        )

    def replicate(
        self,
        leader_signal: Dict[str, Any],
        own_equity: float,
        leader_equity: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Replicate a leader signal proportionally to our equity.

        Args:
            leader_signal: Signal dict from leader. Expected keys:
                - direction: 'BUY' or 'SELL'
                - confidence: 0-100
                - kelly: Kelly fraction
                - ticker: Instrument ticker
                - shares: Number of shares leader would trade
                - price: Current price
                - ts: Signal timestamp (epoch seconds)
            own_equity: Our current portfolio equity.
            leader_equity: Leader's equity (for proportional sizing).
                           If None, uses shares directly with scaling_factor.

        Returns:
            Replicated signal dict, or None if discarded (lag/confidence).
        """
        signal_ts = leader_signal.get("ts", time.time())
        signal_age = time.time() - signal_ts

        # Check lag
        if signal_age > self._max_lag_seconds:
            self._discarded_count += 1
            log.debug(
                "Signal discarded: age=%.2fs > max=%.1fs (%s)",
                signal_age, self._max_lag_seconds,
                leader_signal.get("ticker", "?"),
            )
            return None

        # Compute lag penalty
        lag_penalty = self._lag_penalty(signal_age)
        original_confidence = leader_signal.get("confidence", 50.0)
        adjusted_confidence = original_confidence * lag_penalty

        if adjusted_confidence < self._min_confidence:
            self._discarded_count += 1
            log.debug(
                "Signal discarded: conf=%.1f * lag_pen=%.3f = %.1f < min=%.1f",
                original_confidence, lag_penalty, adjusted_confidence,
                self._min_confidence,
            )
            return None

        # Size adjustment
        leader_shares = leader_signal.get("shares", 0)
        leader_eq = leader_equity or own_equity  # fallback: assume same equity
        adjusted_shares = self._size_adjustment(
            leader_shares, leader_eq, own_equity,
        )
        adjusted_shares = max(1, int(round(adjusted_shares * self._scaling_factor)))

        replicated = {
            "ticker": leader_signal.get("ticker", ""),
            "direction": leader_signal.get("direction", ""),
            "confidence": round(adjusted_confidence, 1),
            "original_confidence": original_confidence,
            "kelly": leader_signal.get("kelly", 0.0),
            "shares": adjusted_shares,
            "price": leader_signal.get("price", 0.0),
            "source": f"copy:{leader_signal.get('source', 'unknown')}",
            "leader_id": leader_signal.get("leader_id", ""),
            "lag_seconds": round(signal_age, 3),
            "lag_penalty": round(lag_penalty, 4),
            "ts": time.time(),
        }

        self._replicated_count += 1
        log.info(
            "Replicated: %s %s %d shares @ %.4f (conf=%.1f lag=%.3fs)",
            replicated["direction"], replicated["ticker"],
            adjusted_shares, replicated["price"],
            adjusted_confidence, signal_age,
        )
        return replicated

    def _size_adjustment(
        self,
        leader_size: int,
        leader_equity: float,
        own_equity: float,
    ) -> float:
        """Compute proportionally adjusted position size.

        Scales leader's position size by equity ratio:
            own_size = leader_size * (own_equity / leader_equity)

        Args:
            leader_size: Number of shares the leader would trade.
            leader_equity: Leader's total equity.
            own_equity: Our total equity.

        Returns:
            Adjusted size as float (caller should round).
        """
        if leader_equity <= 0.0:
            log.warning("Leader equity <= 0, returning leader_size unchanged")
            return float(leader_size)

        ratio = own_equity / leader_equity
        adjusted = leader_size * ratio
        return adjusted

    def _lag_penalty(self, signal_age_seconds: float) -> float:
        """Compute confidence decay factor based on signal age.

        Uses exponential decay: penalty = exp(-lambda * age)
        Lambda is calibrated so that at max_lag, penalty = 0.5.

        Args:
            signal_age_seconds: Age of the signal in seconds.

        Returns:
            Decay factor in (0.0, 1.0]. Fresh signal = 1.0.
        """
        if signal_age_seconds <= 0.0:
            return 1.0
        if self._max_lag_seconds <= 0.0:
            return 1.0

        # Calibrate lambda: at max_lag, penalty = 0.5
        # 0.5 = exp(-lambda * max_lag) → lambda = ln(2) / max_lag
        lam = math.log(2.0) / self._max_lag_seconds
        penalty = math.exp(-lam * signal_age_seconds)
        return penalty

    @property
    def stats(self) -> Dict[str, int]:
        """Return replication statistics."""
        return {
            "replicated": self._replicated_count,
            "discarded": self._discarded_count,
        }


# ---------------------------------------------------------------------------
# Copy Trading Manager
# ---------------------------------------------------------------------------
class CopyTradingManager:
    """Manages multiple leader strategies and routes signals to replicators.

    Maintains a registry of leaders, tracks per-leader performance,
    and coordinates signal replication. Includes anti-herding protection
    to prevent all capital concentrating on a single leader.
    """

    def __init__(
        self,
        max_leaders: int = 3,
        scaling_factor: float = 1.0,
        max_lag_seconds: float = 5.0,
        own_equity: float = 10000.0,
    ) -> None:
        """Initialise copy trading manager.

        Args:
            max_leaders: Maximum number of leaders to follow simultaneously.
            scaling_factor: Global replication scaling factor.
            max_lag_seconds: Maximum signal lag before discard.
            own_equity: Our current portfolio equity (updated externally).
        """
        self._max_leaders = max_leaders
        self._own_equity = own_equity
        self._leaders: Dict[str, LeaderMetrics] = {}
        self._leader_equity: Dict[str, float] = {}
        self._replicator = SignalReplicator(
            scaling_factor=scaling_factor,
            max_lag_seconds=max_lag_seconds,
        )
        self._selector = LeaderSelector()
        self._signal_log: Deque[Dict[str, Any]] = deque(maxlen=5000)
        self._per_leader_signals: Dict[str, List[Dict[str, Any]]] = {}
        self._per_leader_pnl: Dict[str, float] = {}
        log.info(
            "CopyTradingManager: max_leaders=%d equity=%.2f",
            max_leaders, own_equity,
        )

    def add_leader(
        self,
        leader_id: str,
        metrics: LeaderMetrics,
        equity: Optional[float] = None,
    ) -> bool:
        """Register a new leader strategy.

        If at max capacity, rejects the addition unless this leader
        ranks higher than the worst current leader.

        Args:
            leader_id: Unique identifier for the leader.
            metrics: Performance metrics for the leader.
            equity: Leader's equity for proportional sizing.

        Returns:
            True if leader was added, False if rejected.
        """
        if leader_id in self._leaders:
            # Update existing
            self._leaders[leader_id] = metrics
            if equity is not None:
                self._leader_equity[leader_id] = equity
            log.info("Updated leader: %s", leader_id)
            return True

        if len(self._leaders) >= self._max_leaders:
            # Check if new leader is better than worst current
            all_candidates = list(self._leaders.values()) + [metrics]
            ranked = self._selector.rank(all_candidates)
            if ranked and ranked[-1].leader_id != leader_id:
                # New leader is not the worst — evict worst current
                worst_id = ranked[-1].leader_id
                self.remove_leader(worst_id)
                log.info("Evicted leader %s to make room for %s", worst_id, leader_id)
            else:
                log.info("Rejected leader %s — worse than all current", leader_id)
                return False

        self._leaders[leader_id] = metrics
        if equity is not None:
            self._leader_equity[leader_id] = equity
        self._per_leader_signals[leader_id] = []
        self._per_leader_pnl[leader_id] = 0.0
        log.info("Added leader: %s (sharpe=%.2f wr=%.2f)", leader_id, metrics.sharpe, metrics.win_rate)
        return True

    def remove_leader(self, leader_id: str) -> bool:
        """Remove a leader from the registry.

        Args:
            leader_id: ID of the leader to remove.

        Returns:
            True if removed, False if not found.
        """
        if leader_id not in self._leaders:
            log.warning("Leader %s not found for removal", leader_id)
            return False

        del self._leaders[leader_id]
        self._leader_equity.pop(leader_id, None)
        self._per_leader_signals.pop(leader_id, None)
        self._per_leader_pnl.pop(leader_id, None)
        log.info("Removed leader: %s", leader_id)
        return True

    def update_equity(self, equity: float) -> None:
        """Update our portfolio equity.

        Args:
            equity: Current portfolio equity.
        """
        self._own_equity = equity

    def process_leader_signal(
        self,
        leader_id: str,
        signal: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Process an incoming signal from a leader.

        Validates the leader is registered, replicates the signal
        proportionally, and logs the result.

        Args:
            leader_id: ID of the leader emitting the signal.
            signal: Signal dict (see SignalReplicator.replicate for format).

        Returns:
            Replicated signal dict, or None if discarded/rejected.
        """
        if leader_id not in self._leaders:
            log.warning("Signal from unregistered leader: %s", leader_id)
            return None

        signal["leader_id"] = leader_id
        leader_eq = self._leader_equity.get(leader_id, self._own_equity)

        replicated = self._replicator.replicate(
            signal, self._own_equity, leader_eq,
        )

        if replicated is None:
            return None

        # Log
        self._signal_log.append(replicated)
        if leader_id in self._per_leader_signals:
            self._per_leader_signals[leader_id].append(replicated)

        # Persist to NDJSON
        try:
            with open(SIGNALS_PATH, "a") as f:
                f.write(json.dumps(replicated, default=str) + "\n")
        except OSError as exc:
            log.warning("Failed to write signal log: %s", exc)

        return replicated

    def record_pnl(self, leader_id: str, pnl: float) -> None:
        """Record P&L from a replicated trade for attribution.

        Args:
            leader_id: Leader the trade was copied from.
            pnl: P&L in GBP from the trade.
        """
        if leader_id in self._per_leader_pnl:
            self._per_leader_pnl[leader_id] += pnl
        else:
            self._per_leader_pnl[leader_id] = pnl

    def performance_report(self) -> Dict[str, Any]:
        """Generate per-leader performance attribution report.

        Returns:
            Dict with per-leader stats (signal count, total P&L)
            and aggregate statistics.
        """
        leaders_report = {}
        total_pnl = 0.0
        total_signals = 0

        for lid, metrics in self._leaders.items():
            n_signals = len(self._per_leader_signals.get(lid, []))
            pnl = self._per_leader_pnl.get(lid, 0.0)
            total_pnl += pnl
            total_signals += n_signals

            leaders_report[lid] = {
                "metrics": metrics.to_dict(),
                "signals_replicated": n_signals,
                "total_pnl": round(pnl, 2),
                "equity_allocated": self._leader_equity.get(lid, 0.0),
            }

        return {
            "leaders": leaders_report,
            "leader_count": len(self._leaders),
            "max_leaders": self._max_leaders,
            "total_signals_replicated": total_signals,
            "total_pnl": round(total_pnl, 2),
            "own_equity": self._own_equity,
            "replicator_stats": self._replicator.stats,
        }

    def ranked_leaders(self) -> List[LeaderMetrics]:
        """Return current leaders ranked by composite score.

        Returns:
            List of LeaderMetrics, best first.
        """
        return self._selector.rank(list(self._leaders.values()))

    def save_state(self) -> None:
        """Persist manager state to disk."""
        state = {
            "leaders": {lid: m.to_dict() for lid, m in self._leaders.items()},
            "leader_equity": self._leader_equity,
            "per_leader_pnl": self._per_leader_pnl,
            "own_equity": self._own_equity,
            "replicator_stats": self._replicator.stats,
        }
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_PATH, "w") as f:
                json.dump(state, f, indent=2)
            log.info("State saved to %s", STATE_PATH)
        except OSError as exc:
            log.error("Failed to save state: %s", exc)
