"""Trade Taxonomy Classifier — Classifies closed trades into diagnostic categories.

Used by:
  - nightly_v6.py (trade analysis enrichment)
  - sheets_sync.py (Closed_Trades tab classification column)
  - indicator_intelligence.py (per-class indicator analysis)
  - Claude nightly review (context for forensics)

Each trade is classified into exactly ONE primary class. Classes enable:
  1. Targeted Ouroboros learning (optimize differently for spread victims vs thesis failures)
  2. Setup promotion/demotion (kill classes with < 30% WR over 100+ trades)
  3. Claude forensic analysis (cluster loser archetypes)
  4. Operator insight (what's actually killing performance)

BUILD NOW item N1b from IMPLEMENTATION_MASTER_PLAN v6.0.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional


# ============================================================================
# TRADE OUTCOME CLASSES
# ============================================================================

class TradeClass:
    """Primary trade outcome classification."""

    # --- Winners ---
    CLEAN_TREND = "clean_trend"          # Rung 3+, MAE < 20% of MFE, held > 10 bars
    GRIND_WINNER = "grind_winner"        # Rung 2-3, MAE > 50% of MFE (survived drawdown)
    SPIKE_WINNER = "spike_winner"        # Rung 4+, held < 25 min (fast momentum capture)
    LUCKY_WINNER = "lucky_winner"        # Won but indicators were against (regime wrong, etc.)
    BREAKEVEN_WIN = "breakeven_win"      # PnL > 0 but < 2x spread (barely won)

    # --- Losers ---
    SPREAD_VICTIM = "spread_victim"      # Loss < 2x total spread cost (friction killed it)
    NOISE_EXIT = "noise_exit"            # Stopped out < Rung 2, held < 50 min
    STOP_HUNT = "stop_hunt"             # MAE > 2x ATR then price reversed within 30 bars
    THESIS_FAILURE = "thesis_failure"    # Regime changed or trend reversed (legitimate loss)
    LATE_ENTRY = "late_entry"            # Entered > 70% of session elapsed
    OVEREXTENSION = "overextension"      # Entry > 1% above VWAP (chased)
    GAP_AGAINST = "gap_against"          # Overnight gap against position

    # --- Anomalies ---
    FLASH_CRASH = "flash_crash"          # > 3% drop in < 1 minute
    CORRELATION_BREAK = "corr_break"     # Ticker decorrelated from benchmark mid-trade

    ALL_CLASSES = [
        CLEAN_TREND, GRIND_WINNER, SPIKE_WINNER, LUCKY_WINNER, BREAKEVEN_WIN,
        SPREAD_VICTIM, NOISE_EXIT, STOP_HUNT, THESIS_FAILURE, LATE_ENTRY,
        OVEREXTENSION, GAP_AGAINST, FLASH_CRASH, CORRELATION_BREAK,
    ]

    WINNER_CLASSES = [CLEAN_TREND, GRIND_WINNER, SPIKE_WINNER, LUCKY_WINNER, BREAKEVEN_WIN]
    LOSER_CLASSES = [SPREAD_VICTIM, NOISE_EXIT, STOP_HUNT, THESIS_FAILURE, LATE_ENTRY, OVEREXTENSION, GAP_AGAINST]
    ANOMALY_CLASSES = [FLASH_CRASH, CORRELATION_BREAK]


def classify_trade(trade: Dict) -> str:
    """Classify a closed trade into exactly one TradeClass.

    Args:
        trade: Dict with WAL PositionClosed fields:
            - final_pnl: float (net P&L after commission)
            - gross_pnl: float (P&L before commission)
            - total_commission: float
            - spread_at_entry_pct: float
            - spread_at_exit_pct: float
            - highest_rung: int (0-5)
            - mae: float (max adverse excursion, negative)
            - mfe: float (max favorable excursion, positive)
            - entry_price: float
            - exit_price: float
            - qty: int
            - hold_time_mins: int (if available)
            - atr_pct_at_entry: float (if available)
            - entry_session_phase: str (if available)
            - vwap_dist_at_entry_pct: float (if available)

    Returns:
        One of TradeClass.* string constants.
    """
    pnl = trade.get("final_pnl", 0.0)
    gross_pnl = trade.get("gross_pnl", 0.0)
    commission = trade.get("total_commission", 0.0)
    spread_entry = trade.get("spread_at_entry_pct", 0.0)
    spread_exit = trade.get("spread_at_exit_pct", 0.0)
    rung = trade.get("highest_rung", 0)
    mae = abs(trade.get("mae", 0.0))
    mfe = abs(trade.get("mfe", 0.001))  # Avoid division by zero
    entry_price = trade.get("entry_price", 1.0)
    qty = trade.get("qty", 1)
    hold_mins = trade.get("hold_time_mins", 60)
    atr_pct = trade.get("atr_pct_at_entry", 0.01)
    session_phase = trade.get("entry_session_phase", "")

    position_value = max(entry_price * qty, 1.0)
    total_spread_cost = (spread_entry + spread_exit) / 100.0 * position_value
    loss_abs = abs(pnl)

    # --- Anomaly detection first (takes precedence) ---
    # Flash crash: MFE was fine but MAE was extreme (> 3% in short hold)
    if mae > 0.03 * entry_price * qty and hold_mins < 5:
        return TradeClass.FLASH_CRASH

    # --- Winner classification ---
    if pnl > 0:
        # Breakeven win: barely made money
        if pnl < 2 * total_spread_cost and pnl > 0:
            return TradeClass.BREAKEVEN_WIN

        # Spike winner: fast high-rung capture
        if rung >= 4 and hold_mins < 25:
            return TradeClass.SPIKE_WINNER

        # Clean trend: high rung, low MAE relative to MFE
        if rung >= 3 and mfe > 0 and (mae / mfe) < 0.20:
            return TradeClass.CLEAN_TREND

        # Grind winner: survived significant drawdown
        if rung >= 2 and mfe > 0 and (mae / mfe) > 0.50:
            return TradeClass.GRIND_WINNER

        # Lucky winner: low rung, probably noise
        return TradeClass.LUCKY_WINNER

    # --- Loser classification ---
    # Spread victim: loss is less than 2x the spread cost
    if loss_abs < 2 * total_spread_cost:
        return TradeClass.SPREAD_VICTIM

    # Late entry: entered in close session phase
    if session_phase in ("close", "afternoon") and hold_mins < 30:
        return TradeClass.LATE_ENTRY

    # Stop hunt: MAE > 2x ATR then price came back (but we were already stopped)
    if atr_pct > 0 and mae > 2 * atr_pct * entry_price * qty:
        if mfe > 0.5 * mae:  # Price came back more than half
            return TradeClass.STOP_HUNT

    # Noise exit: stopped out quickly at low rung
    if rung < 2 and hold_mins < 50:
        return TradeClass.NOISE_EXIT

    # Default: thesis failure (legitimate loss — the trade idea was wrong)
    return TradeClass.THESIS_FAILURE


@dataclass
class TradeClassStats:
    """Aggregated statistics for a trade class."""
    trade_class: str
    count: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    avg_hold_mins: float = 0.0
    avg_rung: float = 0.0
    win_rate: float = 0.0

    def update(self, trade: Dict):
        pnl = trade.get("final_pnl", 0.0)
        self.count += 1
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.total_pnl += pnl
        self.avg_pnl = self.total_pnl / self.count
        self.avg_hold_mins = (
            (self.avg_hold_mins * (self.count - 1) + trade.get("hold_time_mins", 0))
            / self.count
        )
        self.avg_rung = (
            (self.avg_rung * (self.count - 1) + trade.get("highest_rung", 0))
            / self.count
        )
        self.win_rate = self.wins / self.count if self.count > 0 else 0.0


def build_class_report(trades: list) -> Dict[str, TradeClassStats]:
    """Build per-class statistics from a list of closed trades.

    Returns dict: trade_class -> TradeClassStats
    """
    stats: Dict[str, TradeClassStats] = {}
    for trade in trades:
        tc = classify_trade(trade)
        if tc not in stats:
            stats[tc] = TradeClassStats(trade_class=tc)
        stats[tc].update(trade)
    return stats
