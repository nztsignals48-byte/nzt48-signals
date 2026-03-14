"""
H-14: Weekly Performance Report Generator.
Every Sunday 20:00 UK: compute metrics, send via Telegram, store locally.
"""
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)


@dataclass
class WeeklyMetrics:
    week_start: str
    week_end: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_pct: float
    total_pnl: float
    trades_by_ticker: Dict[str, int]
    trades_by_regime: Dict[str, int]
    gate_rejections: Dict[str, int]


class WeeklyReportGenerator:
    """Generate weekly performance reports from trade data."""

    def __init__(self, data_dir: str = "data", report_dir: str = "data/weekly_reports"):
        self._data_dir = Path(data_dir)
        self._report_dir = Path(report_dir)
        self._report_dir.mkdir(parents=True, exist_ok=True)

    def compute_metrics(self, trades: List[dict], week_start: datetime,
                       week_end: datetime) -> WeeklyMetrics:
        if not trades:
            return WeeklyMetrics(
                week_start=week_start.isoformat(), week_end=week_end.isoformat(),
                total_trades=0, wins=0, losses=0, win_rate=0.0,
                profit_factor=0.0, sharpe_ratio=0.0, max_drawdown_pct=0.0,
                total_pnl=0.0, trades_by_ticker={}, trades_by_regime={},
                gate_rejections={},
            )

        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) <= 0]
        gross_profit = sum(t.get("pnl", 0) for t in wins) if wins else 0
        gross_loss = abs(sum(t.get("pnl", 0) for t in losses)) if losses else 0
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        pnls = [t.get("pnl", 0) for t in trades]
        import numpy as np
        pnl_arr = np.array(pnls) if pnls else np.array([0])
        sharpe = (pnl_arr.mean() / pnl_arr.std() * np.sqrt(252)) if pnl_arr.std() > 0 else 0

        # Max drawdown
        cumulative = np.cumsum(pnl_arr)
        peak = np.maximum.accumulate(cumulative)
        dd = (cumulative - peak)
        max_dd = float(dd.min()) if len(dd) > 0 else 0

        by_ticker: Dict[str, int] = {}
        by_regime: Dict[str, int] = {}
        for t in trades:
            tk = t.get("ticker", "unknown")
            by_ticker[tk] = by_ticker.get(tk, 0) + 1
            rg = t.get("regime", "unknown")
            by_regime[rg] = by_regime.get(rg, 0) + 1

        return WeeklyMetrics(
            week_start=week_start.isoformat(), week_end=week_end.isoformat(),
            total_trades=len(trades), wins=len(wins), losses=len(losses),
            win_rate=len(wins) / len(trades) if trades else 0,
            profit_factor=pf, sharpe_ratio=float(sharpe),
            max_drawdown_pct=max_dd, total_pnl=sum(pnls),
            trades_by_ticker=by_ticker, trades_by_regime=by_regime,
            gate_rejections={},
        )

    def generate_report_text(self, m: WeeklyMetrics) -> str:
        lines = [
            f"\U0001f4ca NZT-48 Weekly Report",
            f"Period: {m.week_start[:10]} \u2192 {m.week_end[:10]}",
            f"",
            f"Trades: {m.total_trades} (W:{m.wins} L:{m.losses})",
            f"Win Rate: {m.win_rate:.1%}",
            f"Profit Factor: {m.profit_factor:.2f}",
            f"Sharpe: {m.sharpe_ratio:.2f}",
            f"Max DD: {m.max_drawdown_pct:.2%}",
            f"Total P&L: \u00a3{m.total_pnl:.2f}",
            f"",
            f"By Ticker: {json.dumps(m.trades_by_ticker)}",
            f"By Regime: {json.dumps(m.trades_by_regime)}",
        ]
        return "\n".join(lines)

    def save_report(self, metrics: WeeklyMetrics) -> str:
        filename = f"weekly_{metrics.week_start[:10]}.json"
        path = self._report_dir / filename
        with open(path, "w") as f:
            json.dump(metrics.__dict__, f, indent=2, default=str)
        logger.info("Weekly report saved: %s", path)
        return str(path)
