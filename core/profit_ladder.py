"""
NZT-48 Self-Teaching Learning Engine
Sections 49-52: The system learns from every trade.

Three learning mechanisms:
1. Regime Performance Matrix — tracks win rate per regime/strategy/direction
2. Ticker Profiles — builds per-ticker optimal parameters
3. MAE/MFE Recalibration — adjusts stops and targets based on actual execution

Every 50 trades: recalibrate MAE/MFE
Every 20 trades: update regime matrix
Weekly: update ticker profiles and priority scores
"""
from __future__ import annotations
import logging
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import (
    Signal, Direction, RegimeState, Trade, TickerProfile,
    RegimeMemoryCell, Strategy,
)
import config as cfg

# Learning modules
from learning.indicator_tracker import IndicatorEffectivenessTracker
from learning.strategy_tracker import StrategyContextMatrix
from learning.move_attribution import MoveAttribution
from learning.pattern_tracker import PatternTracker
from learning.failure_analysis import FailureAnalysis
from learning.correlation_tracker import CorrelationTracker
from learning.decay_detector import DecayDetector
from learning.weight_optimizer import WeightOptimizer
from learning.param_optimizer import ParameterOptimizer
from learning.system_iq import SystemIQ


class RegimePerformanceMatrix:
    """Tracks performance by regime x strategy x direction.

    Matrix structure:
    {regime: {strategy: {direction: RegimeMemoryCell}}}

    Used to:
    1. Boost confidence for regime/strategy combos that historically work
    2. Reduce confidence for underperforming combos
    3. Disable strategies in regimes where they consistently lose
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.learning.regime_matrix")
        # 3D matrix: regime -> strategy -> direction -> cell
        self.matrix: dict[str, dict[str, dict[str, RegimeMemoryCell]]] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(RegimeMemoryCell))
        )
        self._trades_since_update = 0
        self._update_interval = 10  # Reduced from 20 to 10 for faster adaptation

    def record_trade(self, trade: Trade) -> None:
        """Record a completed trade into the matrix."""
        regime = trade.regime_state
        strategy = trade.strategy
        direction = trade.direction.value

        cell = self.matrix[regime][strategy][direction]
        cell.regime = regime
        cell.strategy = strategy
        cell.direction = direction
        cell.trades += 1

        # Update running win rate
        if trade.pnl_r_multiple > 0:
            old_wins = cell.win_rate * (cell.trades - 1)
            cell.win_rate = (old_wins + 1) / cell.trades
        else:
            old_wins = cell.win_rate * (cell.trades - 1)
            cell.win_rate = old_wins / cell.trades

        # Update average R
        cell.avg_r = (
            (cell.avg_r * (cell.trades - 1) + trade.pnl_r_multiple) / cell.trades
        )

        # Expectancy = (WR x avg_win) - ((1-WR) x avg_loss)
        cell.expectancy = cell.win_rate * max(cell.avg_r, 0) - (1 - cell.win_rate) * abs(min(cell.avg_r, 0))

        self._trades_since_update += 1

    def get_confidence_adjustment(self, regime: str, strategy: str, direction: str) -> int:
        """Get confidence adjustment based on historical performance.

        Returns:
            -15 to +15 confidence adjustment
        """
        cell = self.matrix.get(regime, {}).get(strategy, {}).get(direction)
        if not cell or cell.trades < 5:
            return 0  # Not enough data

        # Strong performers: +5 to +15
        if cell.win_rate >= 0.65 and cell.avg_r >= 1.5:
            return 15
        elif cell.win_rate >= 0.55 and cell.avg_r >= 1.0:
            return 10
        elif cell.win_rate >= 0.50 and cell.avg_r >= 0.5:
            return 5

        # Underperformers: -5 to -15
        elif cell.win_rate < 0.35 and cell.avg_r < -0.5:
            return -15
        elif cell.win_rate < 0.40:
            return -10
        elif cell.win_rate < 0.45:
            return -5

        return 0

    def should_disable_strategy(self, regime: str, strategy: str, direction: str) -> bool:
        """Check if a strategy should be disabled in this regime.

        Disable if: 10+ trades AND win rate < 30% AND avg R < -0.5
        """
        cell = self.matrix.get(regime, {}).get(strategy, {}).get(direction)
        if not cell or cell.trades < 10:
            return False
        return cell.win_rate < 0.30 and cell.avg_r < -0.5

    def get_best_strategy(self, regime: str, direction: str) -> Optional[str]:
        """Get the best-performing strategy for this regime/direction."""
        best = None
        best_expectancy = -float("inf")

        for strategy, directions in self.matrix.get(regime, {}).items():
            cell = directions.get(direction)
            if cell and cell.trades >= 5 and cell.expectancy > best_expectancy:
                best = strategy
                best_expectancy = cell.expectancy

        return best

    def export_matrix(self) -> list[dict]:
        """Export the full matrix for display."""
        rows = []
        for regime, strategies in self.matrix.items():
            for strategy, directions in strategies.items():
                for direction, cell in directions.items():
                    if cell.trades > 0:
                        rows.append({
                            "regime": regime,
                            "strategy": strategy,
                            "direction": direction,
                            "trades": cell.trades,
                            "win_rate": round(cell.win_rate * 100, 1),
                            "avg_r": round(cell.avg_r, 2),
                            "expectancy": round(cell.expectancy, 3),
                        })
        return sorted(rows, key=lambda r: r["expectancy"], reverse=True)


class TickerProfileManager:
    """Manages per-ticker performance profiles.

    Tracks:
    - Rolling 60-day win rate per ticker
    - Best strategy per ticker
    - False breakout rate
    - Optimal RVOL threshold
    - Optimal stop multiplier
    - Priority score (used for ticker ranking)
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.learning.ticker_profiles")
        self.profiles: dict[str, TickerProfile] = {}
        self._trade_history: dict[str, list[Trade]] = defaultdict(list)

    def record_trade(self, trade: Trade) -> None:
        """Record a trade for ticker profile building."""
        self._trade_history[trade.ticker].append(trade)

    def rebuild_profiles(self) -> None:
        """Rebuild all ticker profiles from trade history.
        Called weekly or after 50 trades."""
        for ticker, trades in self._trade_history.items():
            if len(trades) < 5:
                continue

            # Filter to last 60 days
            cutoff = datetime.now(timezone.utc) - timedelta(days=60)
            recent = [t for t in trades if t.time_entered >= cutoff]
            if not recent:
                continue

            profile = TickerProfile(ticker=ticker)

            # Win rate
            wins = sum(1 for t in recent if t.pnl_r_multiple > 0)
            profile.rolling_60d_wr = wins / len(recent)

            # Best strategy
            strat_wins: dict[str, int] = defaultdict(int)
            strat_count: dict[str, int] = defaultdict(int)
            for t in recent:
                strat_count[t.strategy] += 1
                if t.pnl_r_multiple > 0:
                    strat_wins[t.strategy] += 1

            best_strat = ""
            best_wr = 0.0
            for strat, count in strat_count.items():
                if count >= 3:
                    wr = strat_wins[strat] / count
                    if wr > best_wr:
                        best_wr = wr
                        best_strat = strat
            profile.best_strategy = best_strat

            # Best direction
            long_wins = sum(1 for t in recent if t.direction == Direction.LONG and t.pnl_r_multiple > 0)
            long_total = sum(1 for t in recent if t.direction == Direction.LONG)
            short_wins = sum(1 for t in recent if t.direction == Direction.SHORT and t.pnl_r_multiple > 0)
            short_total = sum(1 for t in recent if t.direction == Direction.SHORT)

            long_wr = long_wins / long_total if long_total > 0 else 0
            short_wr = short_wins / short_total if short_total > 0 else 0
            profile.best_direction = Direction.LONG if long_wr >= short_wr else Direction.SHORT

            # False breakout rate (trades that reversed immediately)
            false_breakouts = sum(
                1 for t in recent
                if t.pnl_r_multiple <= -0.5 and t.duration_minutes < 30
            )
            profile.false_breakout_rate = false_breakouts / len(recent)

            # Optimal RVOL: average RVOL of winning trades
            # (stored in trade context, approximated from what we have)
            winning_trades = [t for t in recent if t.pnl_r_multiple > 0]
            if winning_trades:
                profile.optimal_rvol = max(1.5, sum(
                    t.confidence_score / 100 * 3.0 for t in winning_trades
                ) / len(winning_trades))

            # Priority score: expectancy x frequency
            avg_r = sum(t.pnl_r_multiple for t in recent) / len(recent)
            profile.priority_score = avg_r * len(recent) * profile.rolling_60d_wr

            self.profiles[ticker] = profile
            self.logger.info(
                "PROFILE %s: WR=%.0f%% Best=%s FBR=%.0f%% Priority=%.2f",
                ticker, profile.rolling_60d_wr * 100,
                profile.best_strategy, profile.false_breakout_rate * 100,
                profile.priority_score,
            )

    def get_profile(self, ticker: str) -> Optional[TickerProfile]:
        """Get a ticker's profile, if sufficient data exists."""
        return self.profiles.get(ticker)

    def get_priority_ranking(self) -> list[tuple[str, float]]:
        """Get tickers ranked by priority score."""
        ranked = sorted(
            self.profiles.items(),
            key=lambda x: x[1].priority_score,
            reverse=True,
        )
        return [(ticker, p.priority_score) for ticker, p in ranked]


class MAEMFETracker:
    """Maximum Adverse Excursion / Maximum Favorable Excursion tracker.

    Tracks the worst drawdown (MAE) and best profit (MFE) of every trade.
    Used to recalibrate:
    - Stop distances (if MAE consistently < stop, tighten; if > stop, widen)
    - Profit targets (if MFE consistently beyond target, extend; if below, tighten)

    Recalibrates every 50 trades.
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.learning.mae_mfe")
        self._mae_history: list[float] = []  # As fraction of risk (R), bounded
        self._mfe_history: list[float] = []  # As fraction of risk (R), bounded
        self._missed_gain_history: list[float] = []  # $ missed by early exit
        self._missed_loss_history: list[float] = []  # $ lost by late exit
        self._max_history = 200  # Prevent unbounded memory growth
        self._recalibrate_interval = 30  # Reduced from 50 for faster adaptation
        self._trades_since_recalibrate = 0
        self.optimal_stop_mult: float = 1.5  # ATR multiplier
        self.optimal_target_1r: float = 2.0  # R-multiple target
        self.optimal_target_2r: float = 3.0
        # Missed opportunity diagnostics
        self.avg_missed_gain: float = 0.0   # Running avg of missed gain by early exit
        self.avg_missed_loss: float = 0.0   # Running avg of missed loss by late exit
        self.exit_timing_bias: str = "NEUTRAL"  # EARLY, LATE, or NEUTRAL

    def record_trade(self, trade: Trade) -> None:
        """Record MAE/MFE from a completed trade.

        Uses entry_quality (MAE-based, 0-100) and exit_quality (MFE-based, 0-100)
        from the Trade model as proxies.
        """
        # MAE: how far against us (as R-multiple, negative)
        if trade.risk_dollars > 0:
            # entry_quality 100 = perfect (MAE = 0), 0 = terrible (MAE = full stop)
            mae_r = -(1.0 - trade.entry_quality / 100) if trade.entry_quality > 0 else -1.0
            mfe_r = trade.exit_quality / 100 * 3.0 if trade.exit_quality > 0 else trade.pnl_r_multiple
        else:
            mae_r = min(trade.pnl_r_multiple, 0)
            mfe_r = max(trade.pnl_r_multiple, 0)

        self._mae_history.append(mae_r)
        self._mfe_history.append(mfe_r)

        # Track missed gain/loss for exit timing analysis
        missed_gain = getattr(trade, "missed_gain_by_early_exit", 0) or 0
        missed_loss = getattr(trade, "missed_loss_by_late_exit", 0) or 0
        if missed_gain > 0:
            self._missed_gain_history.append(missed_gain)
        if missed_loss > 0:
            self._missed_loss_history.append(missed_loss)

        # Prevent memory leak — keep bounded
        if len(self._mae_history) > self._max_history:
            self._mae_history = self._mae_history[-self._max_history:]
        if len(self._mfe_history) > self._max_history:
            self._mfe_history = self._mfe_history[-self._max_history:]
        if len(self._missed_gain_history) > self._max_history:
            self._missed_gain_history = self._missed_gain_history[-self._max_history:]
        if len(self._missed_loss_history) > self._max_history:
            self._missed_loss_history = self._missed_loss_history[-self._max_history:]

        self._trades_since_recalibrate += 1

        if self._trades_since_recalibrate >= self._recalibrate_interval:
            self.recalibrate()
            self._trades_since_recalibrate = 0

    def recalibrate(self) -> None:
        """Recalibrate stop/target parameters from MAE/MFE data."""
        if len(self._mae_history) < 20:
            return

        # Use last 50 trades
        recent_mae = self._mae_history[-50:]
        recent_mfe = self._mfe_history[-50:]

        # Optimal stop: 90th percentile of MAE
        # (cover 90% of adverse moves, let 10% hit stop)
        sorted_mae = sorted(recent_mae)
        p90_idx = int(len(sorted_mae) * 0.10)  # 10th percentile (most negative)
        p90_mae = abs(sorted_mae[p90_idx]) if p90_idx < len(sorted_mae) else 1.0

        # Adjust stop multiplier (bound between 0.8 and 2.5 ATR)
        self.optimal_stop_mult = max(0.8, min(2.5, p90_mae * 1.5))

        # Optimal target: median MFE of winning trades
        winning_mfe = [m for m in recent_mfe if m > 0]
        if winning_mfe:
            winning_mfe.sort()
            median_idx = len(winning_mfe) // 2
            self.optimal_target_1r = max(1.0, winning_mfe[median_idx])
            # Target 2R: 75th percentile of MFE
            p75_idx = int(len(winning_mfe) * 0.75)
            self.optimal_target_2r = max(
                self.optimal_target_1r + 0.5,
                winning_mfe[min(p75_idx, len(winning_mfe) - 1)],
            )

        # Missed gain/loss exit timing analysis
        # If avg missed gain is high, system exits too early (could hold longer)
        # If avg missed loss is high, system exits too late (should take profits sooner)
        if self._missed_gain_history:
            recent_gains = self._missed_gain_history[-50:]
            self.avg_missed_gain = sum(recent_gains) / len(recent_gains)
        if self._missed_loss_history:
            recent_losses = self._missed_loss_history[-50:]
            self.avg_missed_loss = sum(recent_losses) / len(recent_losses)

        # Determine exit timing bias
        if self.avg_missed_gain > self.avg_missed_loss * 1.5 and self.avg_missed_gain > 10:
            self.exit_timing_bias = "EARLY"
            # System is exiting too early — extend targets slightly
            self.optimal_target_1r = min(self.optimal_target_1r * 1.05, 3.5)
            self.optimal_target_2r = min(self.optimal_target_2r * 1.05, 5.0)
        elif self.avg_missed_loss > self.avg_missed_gain * 1.5 and self.avg_missed_loss > 10:
            self.exit_timing_bias = "LATE"
            # System is holding too long — tighten targets slightly
            self.optimal_target_1r = max(self.optimal_target_1r * 0.95, 1.0)
            self.optimal_target_2r = max(self.optimal_target_2r * 0.95, 1.5)
        else:
            self.exit_timing_bias = "NEUTRAL"

        self.logger.info(
            "MAE/MFE RECALIBRATION: stop_mult=%.2f target1R=%.2f target2R=%.2f "
            "(n=%d, exit_bias=%s, avg_missed_gain=$%.2f, avg_missed_loss=$%.2f)",
            self.optimal_stop_mult, self.optimal_target_1r, self.optimal_target_2r,
            len(recent_mae), self.exit_timing_bias,
            self.avg_missed_gain, self.avg_missed_loss,
        )

    def get_adjustments(self) -> dict:
        """Get current recalibrated parameters."""
        return {
            "optimal_stop_mult": round(self.optimal_stop_mult, 2),
            "optimal_target_1r": round(self.optimal_target_1r, 2),
            "optimal_target_2r": round(self.optimal_target_2r, 2),
            "mae_samples": len(self._mae_history),
            "mfe_samples": len(self._mfe_history),
            "avg_missed_gain": round(self.avg_missed_gain, 2),
            "avg_missed_loss": round(self.avg_missed_loss, 2),
            "exit_timing_bias": self.exit_timing_bias,
            "missed_gain_samples": len(self._missed_gain_history),
            "missed_loss_samples": len(self._missed_loss_history),
        }


class LearningEngine:
    """Unified learning engine that coordinates all learning subsystems.

    Aggregates:
    1. Regime Performance Matrix
    2. Ticker Profile Manager
    3. MAE/MFE Tracker
    4. Kelly Criterion (in kelly_sizer.py, updated via this engine)

    Every trade flows through here and feeds all learning systems.
    """

    def __init__(self):
        self.logger = logging.getLogger("nzt48.learning")
        self.regime_matrix = RegimePerformanceMatrix()
        self.ticker_profiles = TickerProfileManager()
        self.mae_mfe = MAEMFETracker()

        # 10 learning modules
        self.indicator_tracker = IndicatorEffectivenessTracker()
        self.strategy_tracker = StrategyContextMatrix()
        self.move_attribution = MoveAttribution()
        self.pattern_tracker = PatternTracker()
        self.failure_analysis = FailureAnalysis()
        self.correlation_tracker = CorrelationTracker()
        self.decay_detector = DecayDetector()
        self.weight_optimizer = WeightOptimizer()
        self.param_optimizer = ParameterOptimizer()
        self.system_iq = SystemIQ()

        self._total_trades = 0
        self._kelly_callback = None  # Set by orchestrator to feed Kelly sizer

    def set_kelly_callback(self, callback) -> None:
        """Set callback to feed Kelly sizer with trade results."""
        self._kelly_callback = callback

    def record_trade(self, trade: Trade) -> None:
        """Record a completed trade across all learning systems."""
        import time as _time
        _learn_start = _time.monotonic()
        self._total_trades += 1

        # --- Original 3 subsystems ---
        self.regime_matrix.record_trade(trade)
        self.ticker_profiles.record_trade(trade)
        self.mae_mfe.record_trade(trade)

        # --- Feed Kelly sizer ---
        if self._kelly_callback:
            self._kelly_callback(trade.pnl_r_multiple)

        # --- Module 1: Indicator Effectiveness Tracker ---
        indicator_data = {
            "r_multiple": trade.pnl_r_multiple,
            "direction": trade.direction.value,
            "regime": trade.regime_state,
            "indicators": getattr(trade, "indicators", {}),
        }
        try:
            self.indicator_tracker.record_trade(indicator_data)
        except Exception as e:
            self.logger.warning(
                "indicator_tracker.record_trade failed for %s %s (R=%.2f): %s",
                trade.ticker, trade.strategy, trade.pnl_r_multiple, e,
            )

        # --- Module 2: Strategy-Context Matrix ---
        try:
            alert = self.strategy_tracker.record_trade(
                strategy=trade.strategy,
                regime=trade.regime_state,
                r_multiple=trade.pnl_r_multiple,
            )
            if alert:
                self.logger.info(
                    "STRATEGY ALERT: %s for %s/%s (R=%.2f)",
                    alert.get("type", ""), trade.strategy, trade.ticker, trade.pnl_r_multiple,
                )
        except Exception as e:
            self.logger.warning(
                "strategy_tracker.record_trade failed for %s/%s: %s",
                trade.strategy, trade.ticker, e,
            )

        # --- Module 4: Pattern Tracker ---
        try:
            patterns_detected = getattr(trade, "patterns", [])
            if patterns_detected:
                self.pattern_tracker.record_pattern_outcome(
                    patterns=patterns_detected,
                    regime=trade.regime_state,
                    ticker=trade.ticker,
                    was_win=trade.pnl_r_multiple > 0,
                )
        except Exception as e:
            self.logger.warning("pattern_tracker.record_pattern_outcome failed: %s", e)

        # --- Module 5: Failure Analysis (losers only) ---
        if trade.pnl_r_multiple < 0:
            try:
                failure_data = {
                    "ticker": trade.ticker,
                    "strategy": trade.strategy,
                    "direction": trade.direction.value,
                    "r_multiple": trade.pnl_r_multiple,
                    "exit_reason": getattr(trade, "exit_reason", ""),
                    "peak_r": getattr(trade, "peak_r", 0),
                    "trough_r": getattr(trade, "trough_r", 0),
                    "slippage": getattr(trade, "slippage", 0),
                    "regime_at_entry": trade.regime_state,
                    "regime_at_exit": getattr(trade, "regime_at_exit", trade.regime_state),
                }
                category = self.failure_analysis.record_failure(failure_data)
                self.logger.debug(
                    "FAILURE CATEGORY: %s for %s %s (R=%.2f, exit=%s)",
                    category, trade.ticker, trade.strategy, trade.pnl_r_multiple,
                    getattr(trade, "exit_reason", "N/A"),
                )
            except Exception as e:
                self.logger.warning(
                    "failure_analysis.record_failure failed for %s %s (R=%.2f): %s",
                    trade.ticker, trade.strategy, trade.pnl_r_multiple, e,
                )

        # --- Missed Gain/Loss Analysis ---
        # If the trade has missed_gain_by_early_exit or missed_loss_by_late_exit,
        # log it and factor into learning adjustments
        missed_gain = getattr(trade, "missed_gain_by_early_exit", 0) or 0
        missed_loss = getattr(trade, "missed_loss_by_late_exit", 0) or 0
        if missed_gain > 0 or missed_loss > 0:
            peak_r = getattr(trade, "peak_r", 0) or 0
            exit_reason = getattr(trade, "exit_reason", "N/A")
            if missed_gain > 50:
                # Significant missed gain — system exited too early
                self.logger.info(
                    "MISSED GAIN: %s %s exited too early via %s — left $%.2f on table "
                    "(peak_r=%.2fR, exit_r=%.2fR, strategy=%s)",
                    trade.ticker, trade.direction.value, exit_reason,
                    missed_gain, peak_r, trade.pnl_r_multiple, trade.strategy,
                )
            if missed_loss > 50:
                # Significant missed loss — system held too long
                self.logger.info(
                    "MISSED LOSS: %s %s held too long — gave back $%.2f "
                    "(peak_r=%.2fR, exit_r=%.2fR, exit_reason=%s, strategy=%s)",
                    trade.ticker, trade.direction.value,
                    missed_loss, peak_r, trade.pnl_r_multiple, exit_reason, trade.strategy,
                )

        # --- Module 7: Decay Detector ---
        try:
            decay_alerts = self.decay_detector.record_trade(
                strategy=trade.strategy,
                ticker=trade.ticker,
                r_multiple=trade.pnl_r_multiple,
            )
            for da in decay_alerts:
                self.logger.warning(
                    "DECAY ALERT: %s (strategy=%s, ticker=%s)",
                    da.get("message", ""), trade.strategy, trade.ticker,
                )
        except Exception as e:
            self.logger.warning(
                "decay_detector.record_trade failed for %s/%s: %s",
                trade.strategy, trade.ticker, e,
            )

        # --- Module 8: Weight Optimizer ---
        try:
            confidence_layers = getattr(trade, "confidence_layers", {})
            if confidence_layers:
                suggestion = self.weight_optimizer.record_trade(
                    confidence_layers=confidence_layers,
                    r_multiple=trade.pnl_r_multiple,
                )
                if suggestion:
                    self.logger.info("WEIGHT SUGGESTION: %s", suggestion.get("message", ""))
        except Exception as e:
            self.logger.warning("weight_optimizer.record_trade failed: %s", e)

        # --- Module 9: Parameter Optimizer ---
        try:
            param_data = {
                "r_multiple": trade.pnl_r_multiple,
                "atr_stop_mult": getattr(trade, "atr_stop_mult", 1.5),
                "rvol_at_entry": getattr(trade, "rvol_at_entry", 1.5),
                "confidence": trade.confidence_score,
                "orb_timeframe": getattr(trade, "orb_timeframe", "5min"),
            }
            self.param_optimizer.record_trade(param_data)
        except Exception as e:
            self.logger.warning("param_optimizer.record_trade failed: %s", e)

        # --- Module 10: System IQ (recalculate after each trade) ---
        try:
            self._update_system_iq()
        except Exception as e:
            self.logger.warning("system_iq.calculate failed: %s", e)

        _learn_elapsed = _time.monotonic() - _learn_start
        self.logger.info(
            "LEARN: Trade #%d %s %s %s R=%.2f (total: %d, elapsed=%.3fs)",
            self._total_trades, trade.ticker, trade.direction.value,
            trade.strategy, trade.pnl_r_multiple, self._total_trades,
            _learn_elapsed,
        )

        # Weekly profile rebuild trigger (every 50 trades or weekly)
        if self._total_trades % 50 == 0:
            self.ticker_profiles.rebuild_profiles()

    def _update_system_iq(self) -> None:
        """Gather metrics from all modules and recalculate System IQ."""
        # Win rate from regime matrix — approximate from all cells
        total_trades = 0
        total_wins = 0
        total_r = 0.0
        for regime_strats in self.regime_matrix.matrix.values():
            for dir_cells in regime_strats.values():
                for cell in dir_cells.values():
                    if cell.trades > 0:
                        total_trades += cell.trades
                        total_wins += int(cell.win_rate * cell.trades)
                        total_r += cell.avg_r * cell.trades

        win_rate = total_wins / total_trades if total_trades > 0 else 0.5
        avg_r = total_r / total_trades if total_trades > 0 else 0.0

        # Profit factor approximation
        gross_profit = max(avg_r * win_rate, 0.01)
        gross_loss = max(abs(avg_r) * (1 - win_rate), 0.01)
        profit_factor = gross_profit / gross_loss

        # Avg indicator accuracy from leaderboard
        ind_leaderboard = self.indicator_tracker.get_leaderboard()
        avg_ind_acc = (
            sum(row["accuracy"] for row in ind_leaderboard) / len(ind_leaderboard)
            if ind_leaderboard else 50.0
        )

        # Strategy-regime match rate: % of strategy cells with positive expectancy
        strat_lb = self.strategy_tracker.get_leaderboard()
        positive_strats = sum(1 for s in strat_lb if s["expectancy"] > 0)
        strat_match = (positive_strats / len(strat_lb) * 100) if strat_lb else 50.0

        # Pattern accuracy from leaderboard
        pat_lb = self.pattern_tracker.get_pattern_leaderboard()
        avg_pat_acc = (
            sum(row["accuracy"] for row in pat_lb) / len(pat_lb)
            if pat_lb else 50.0
        )

        # Entry and exit quality from MAE/MFE
        mae_adj = self.mae_mfe.get_adjustments()
        # Use target achievement as proxy for quality (0-100 scale)
        entry_quality = min(100, max(0, (1.0 - abs(mae_adj["optimal_stop_mult"] - 1.5) / 1.5) * 100))
        exit_quality = min(100, max(0, mae_adj["optimal_target_1r"] / 3.0 * 100))

        self.system_iq.calculate(
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_indicator_accuracy=avg_ind_acc,
            strategy_regime_match=strat_match,
            pattern_accuracy=avg_pat_acc,
            entry_quality_avg=entry_quality,
            exit_quality_avg=exit_quality,
        )

    def check_move_attribution(
        self,
        ticker: str,
        current_price: float,
        prev_close: float,
        earnings_today: bool = False,
        news_headlines: list[str] = None,
        sector_etf_move: float = 0.0,
        gex_squeeze: bool = False,
        macro_event: bool = False,
        indicators: dict = None,
    ) -> Optional[dict]:
        """Check for significant moves (>1.5%) and attribute cause.

        Call this for each ticker when price data is available,
        typically after market data updates.
        """
        return self.move_attribution.check_move(
            ticker=ticker,
            current_price=current_price,
            prev_close=prev_close,
            earnings_today=earnings_today,
            news_headlines=news_headlines,
            sector_etf_move=sector_etf_move,
            gex_squeeze=gex_squeeze,
            macro_event=macro_event,
            indicators=indicators,
        )

    def update_correlations(self, daily_returns: dict[str, float]) -> None:
        """Update correlation tracker with daily returns. Call once per day."""
        self.correlation_tracker.update_returns(daily_returns)

    def get_signal_adjustments(
        self, ticker: str, strategy: str, direction: str, regime: str,
    ) -> dict:
        """Get all learning-based adjustments for a signal.

        Returns dict with:
        - confidence_adj: int (-15 to +15)
        - stop_mult: float (recalibrated)
        - target_1r: float (recalibrated)
        - target_2r: float (recalibrated)
        - should_disable: bool
        - ticker_priority: float
        """
        adjustments = {}

        # Regime matrix adjustment
        adjustments["confidence_adj"] = self.regime_matrix.get_confidence_adjustment(
            regime, strategy, direction
        )
        adjustments["should_disable"] = self.regime_matrix.should_disable_strategy(
            regime, strategy, direction
        )

        # MAE/MFE recalibrated levels (includes missed gain/loss exit timing bias)
        mae_adj = self.mae_mfe.get_adjustments()
        adjustments["stop_mult"] = mae_adj["optimal_stop_mult"]
        adjustments["target_1r"] = mae_adj["optimal_target_1r"]
        adjustments["target_2r"] = mae_adj["optimal_target_2r"]
        adjustments["exit_timing_bias"] = mae_adj.get("exit_timing_bias", "NEUTRAL")
        adjustments["avg_missed_gain"] = mae_adj.get("avg_missed_gain", 0)
        adjustments["avg_missed_loss"] = mae_adj.get("avg_missed_loss", 0)

        # Ticker profile adjustments
        profile = self.ticker_profiles.get_profile(ticker)
        if profile:
            adjustments["ticker_priority"] = profile.priority_score
            adjustments["false_breakout_rate"] = profile.false_breakout_rate
            adjustments["best_strategy_for_ticker"] = profile.best_strategy
            # Penalize if false breakout rate is high
            if profile.false_breakout_rate > 0.3:
                adjustments["confidence_adj"] -= 5
        else:
            adjustments["ticker_priority"] = 0.0
            adjustments["false_breakout_rate"] = 0.0

        # === AUTOPSY FEEDBACK LOOP ===
        # Use autopsy grades from past trades to adjust confidence for this strategy+ticker.
        # If a strategy consistently scores poorly on setup/timing, penalize future signals.
        autopsy_adj = self._get_autopsy_adjustment(strategy, ticker)
        adjustments["confidence_adj"] += autopsy_adj
        adjustments["autopsy_adj"] = autopsy_adj

        return adjustments

    def _get_autopsy_adjustment(self, strategy: str, ticker: str) -> int:
        """Query trade_autopsies to derive a confidence adjustment.

        Logic:
        - Last 20 trades for this strategy (or strategy+ticker if enough data)
        - Average overall_grade < 40 → penalize -10
        - Average overall_grade < 50 → penalize -5
        - Average overall_grade > 75 → boost +5
        - Specific weak dimension: avg_setup < 40 → -3, avg_timing < 40 → -3
        """
        try:
            from delivery.database import get_connection
            conn = get_connection()
            try:
                # Strategy-level autopsy grades (last 20 trades)
                rows = conn.execute(
                    """SELECT overall_grade, setup_grade, timing_grade,
                              management_grade, market_context_grade
                       FROM trade_autopsies
                       WHERE strategy = ?
                       ORDER BY created_at DESC LIMIT 20""",
                    (strategy,),
                ).fetchall()

                if len(rows) < 5:
                    return 0  # Not enough data

                avg_overall = sum(r["overall_grade"] for r in rows) / len(rows)
                avg_setup = sum(r["setup_grade"] for r in rows) / len(rows)
                avg_timing = sum(r["timing_grade"] for r in rows) / len(rows)

                adj = 0

                # Overall grade penalties/boosts
                if avg_overall < 40:
                    adj -= 10
                elif avg_overall < 50:
                    adj -= 5
                elif avg_overall > 75:
                    adj += 5

                # Specific weakness penalties
                if avg_setup < 40:
                    adj -= 3
                if avg_timing < 40:
                    adj -= 3

                # Ticker-specific override: if this strategy on this ticker is particularly bad
                ticker_rows = conn.execute(
                    """SELECT overall_grade FROM trade_autopsies
                       WHERE strategy = ? AND ticker = ?
                       ORDER BY created_at DESC LIMIT 10""",
                    (strategy, ticker),
                ).fetchall()
                if len(ticker_rows) >= 3:
                    ticker_avg = sum(r["overall_grade"] for r in ticker_rows) / len(ticker_rows)
                    if ticker_avg < 35:
                        adj -= 5  # Extra penalty for bad strategy+ticker combo

                return max(-15, min(10, adj))  # Clamp
            finally:
                conn.close()
        except Exception:
            return 0  # Silent fail — autopsy data may not exist yet

    def get_status(self) -> dict:
        """Get learning engine status for dashboard."""
        return {
            "total_trades_learned": self._total_trades,
            "regime_matrix_entries": sum(
                1 for r in self.regime_matrix.matrix.values()
                for s in r.values()
                for d in s.values()
                if d.trades > 0
            ),
            "ticker_profiles": len(self.ticker_profiles.profiles),
            "mae_mfe_samples": len(self.mae_mfe._mae_history),
            "mae_mfe_adjustments": self.mae_mfe.get_adjustments(),
            "top_performers": self.regime_matrix.export_matrix()[:5],
            "worst_performers": self.regime_matrix.export_matrix()[-5:],
            "ticker_ranking": self.ticker_profiles.get_priority_ranking()[:5],
        }

    def get_all_learning_status(self) -> dict:
        """Get comprehensive status from ALL learning modules.

        Returns a dict with status/summary from every learning subsystem
        for the full-system dashboard.
        """
        status = {
            # Core 3 subsystems
            "total_trades_learned": self._total_trades,
            "regime_matrix": {
                "entries": sum(
                    1 for r in self.regime_matrix.matrix.values()
                    for s in r.values()
                    for d in s.values()
                    if d.trades > 0
                ),
                "top_performers": self.regime_matrix.export_matrix()[:5],
                "worst_performers": self.regime_matrix.export_matrix()[-5:],
            },
            "ticker_profiles": {
                "count": len(self.ticker_profiles.profiles),
                "ranking": self.ticker_profiles.get_priority_ranking()[:10],
            },
            "mae_mfe": self.mae_mfe.get_adjustments(),

            # Module 1: Indicator Tracker
            "indicator_tracker": {
                "leaderboard": self.indicator_tracker.get_leaderboard()[:10],
            },

            # Module 2: Strategy-Context Matrix
            "strategy_tracker": {
                "leaderboard": self.strategy_tracker.get_leaderboard()[:10],
                "alerts": self.strategy_tracker.get_alerts()[-5:],
            },

            # Module 3: Move Attribution
            "move_attribution": {
                "profiles": self.move_attribution.get_all_profiles(),
            },

            # Module 4: Pattern Tracker
            "pattern_tracker": {
                "leaderboard": self.pattern_tracker.get_pattern_leaderboard(),
            },

            # Module 5: Failure Analysis
            "failure_analysis": {
                "weekly_report": self.failure_analysis.get_weekly_report(),
                "recent_failures": self.failure_analysis.get_all_failures(limit=10),
            },

            # Module 6: Correlation Tracker
            "correlation_tracker": {
                "high_correlations": self.correlation_tracker.get_high_correlations(),
                "alerts": self.correlation_tracker.get_alerts(),
                "emerging_pairs": self.correlation_tracker.get_emerging_pairs(),
            },

            # Module 7: Decay Detector
            "decay_detector": self.decay_detector.get_status(),

            # Module 8: Weight Optimizer
            "weight_optimizer": {
                "current_weights": self.weight_optimizer.get_current_weights(),
                "pending_suggestion": self.weight_optimizer.has_pending_suggestion(),
                "suggestions_history": self.weight_optimizer.get_suggestions_history()[-5:],
            },

            # Module 9: Parameter Optimizer
            "param_optimizer": {
                "optimal_values": self.param_optimizer.get_optimal_values(),
                "band_report": self.param_optimizer.get_band_report(),
            },

            # Module 10: System IQ
            "system_iq": self.system_iq.get_current(),
        }

        return status

    def export_all(self) -> dict:
        """Export all learning data for persistence."""
        return {
            "regime_matrix": self.regime_matrix.export_matrix(),
            "ticker_profiles": {
                ticker: {
                    "rolling_60d_wr": p.rolling_60d_wr,
                    "best_strategy": p.best_strategy,
                    "best_direction": p.best_direction.value,
                    "false_breakout_rate": p.false_breakout_rate,
                    "priority_score": p.priority_score,
                }
                for ticker, p in self.ticker_profiles.profiles.items()
            },
            "mae_mfe": self.mae_mfe.get_adjustments(),
            "total_trades": self._total_trades,
            "indicator_leaderboard": self.indicator_tracker.get_leaderboard(),
            "strategy_leaderboard": self.strategy_tracker.get_leaderboard(),
            "move_attribution_profiles": self.move_attribution.get_all_profiles(),
            "pattern_leaderboard": self.pattern_tracker.get_pattern_leaderboard(),
            "failure_report": self.failure_analysis.get_weekly_report(),
            "correlation_matrix": self.correlation_tracker.get_correlation_matrix(),
            "decay_status": self.decay_detector.get_status(),
            "weight_optimizer": self.weight_optimizer.get_current_weights(),
            "param_optimizer": self.param_optimizer.get_optimal_values(),
            "system_iq": self.system_iq.get_current(),
        }
