"""Ouroboros Persistent Memory — cumulative system state across all sessions.

Single source of truth for everything the system has learned. Updated nightly
by nightly_v6.py after trade analysis. Read by config_writer.py to inform
parameter tuning. Read by the Rust engine (via dynamic_weights.toml) at boot.

File: data/system_memory.json

Design:
  - Atomic writes (write .tmp, rename)
  - Backward compatible (new fields get defaults via .get())
  - Never deleted, only appended to
  - Human-readable JSON for debugging

What it tracks:
  - Cumulative trade stats (total trades, all-time PnL, win rate)
  - Per-ticker performance (wins, losses, avg PnL, best/worst trade)
  - Per-exchange performance
  - Per-regime performance (which market states are profitable?)
  - Parameter history (Kelly/Chandelier drift over time)
  - Session history (daily summaries for trend analysis)
  - Lessons learned (auto-generated rules from trade outcomes)
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("ouroboros.memory")

_PROJECT_ROOT = Path(os.environ.get("AEGIS_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("AEGIS_DATA_DIR", _PROJECT_ROOT / "data"))
MEMORY_FILE = DATA_DIR / "system_memory.json"


@dataclass
class TickerStats:
    """Per-ticker cumulative performance."""
    symbol: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    best_pnl: float = 0.0
    worst_pnl: float = 0.0
    avg_rung: float = 0.0
    # Rolling stats
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    # Sizing memory
    last_kelly: float = 0.0
    recommended_kelly: float = 0.0

    def update(self, pnl: float, rung: int, kelly: float):
        """Record a trade outcome."""
        self.total_trades += 1
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.best_pnl = max(self.best_pnl, pnl)
        self.worst_pnl = min(self.worst_pnl, pnl)
        if self.total_trades > 0:
            self.win_rate = self.wins / self.total_trades
            self.avg_pnl = self.total_pnl / self.total_trades
        # Running average rung
        self.avg_rung = ((self.avg_rung * (self.total_trades - 1)) + rung) / self.total_trades
        self.last_kelly = kelly


@dataclass
class RegimeStats:
    """Per-regime cumulative performance."""
    regime: str
    total_trades: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0

    def update(self, pnl: float):
        self.total_trades += 1
        if pnl > 0:
            self.wins += 1
        self.total_pnl += pnl
        if self.total_trades > 0:
            self.win_rate = self.wins / self.total_trades


@dataclass
class ExchangeStats:
    """Per-exchange cumulative performance."""
    exchange: str
    total_trades: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0

    def update(self, pnl: float):
        self.total_trades += 1
        if pnl > 0:
            self.wins += 1
        self.total_pnl += pnl
        if self.total_trades > 0:
            self.win_rate = self.wins / self.total_trades


@dataclass
class SessionSummary:
    """One day's summary for trend analysis."""
    date: str
    trades: int = 0
    exits: int = 0
    pnl: float = 0.0
    win_rate: float = 0.0
    avg_rung: float = 0.0
    kelly_used: float = 0.0
    chandelier_mult: float = 0.0


@dataclass
class SystemMemory:
    """The complete persistent memory of the trading system."""

    # Identity
    version: int = 2
    last_updated: str = ""

    # Cumulative stats
    total_trades: int = 0
    total_exits: int = 0
    total_wins: int = 0
    total_losses: int = 0
    cumulative_pnl: float = 0.0
    cumulative_gross_wins: float = 0.0
    cumulative_gross_losses: float = 0.0
    all_time_win_rate: float = 0.0
    all_time_profit_factor: float = 0.0
    peak_equity: float = 10000.0
    max_drawdown_pct: float = 0.0

    # Session tracking
    total_sessions: int = 0
    first_session_date: str = ""
    sessions: List[Dict[str, Any]] = field(default_factory=list)

    # Per-ticker memory
    ticker_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Per-regime memory
    regime_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Per-exchange memory
    exchange_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # Parameter history (track drift over time)
    param_history: List[Dict[str, Any]] = field(default_factory=list)

    # Lessons learned (auto-generated rules)
    lessons: List[Dict[str, Any]] = field(default_factory=list)

    # Active alerts
    alerts: List[str] = field(default_factory=list)

    def record_trade(self, symbol: str, pnl: float, rung: int, kelly: float,
                     regime: str, exchange: str, confidence: float, strategy: str):
        """Record a single trade outcome into persistent memory."""
        self.total_trades += 1
        self.total_exits += 1
        if pnl > 0:
            self.total_wins += 1
        else:
            self.total_losses += 1
        self.cumulative_pnl += pnl
        if pnl > 0:
            self.cumulative_gross_wins += pnl
        elif pnl < 0:
            self.cumulative_gross_losses += abs(pnl)
        if self.total_exits > 0:
            self.all_time_win_rate = self.total_wins / self.total_exits
        if self.cumulative_gross_losses > 0:
            self.all_time_profit_factor = self.cumulative_gross_wins / self.cumulative_gross_losses
        elif self.cumulative_gross_wins > 0:
            self.all_time_profit_factor = 99.0  # All winners, cap at 99

        # Per-ticker
        if symbol not in self.ticker_stats:
            self.ticker_stats[symbol] = asdict(TickerStats(symbol=symbol))
        ts = TickerStats(**self.ticker_stats[symbol])
        ts.update(pnl, rung, kelly)
        self.ticker_stats[symbol] = asdict(ts)

        # Per-regime
        if regime and regime != "unknown":
            if regime not in self.regime_stats:
                self.regime_stats[regime] = asdict(RegimeStats(regime=regime))
            rs = RegimeStats(**self.regime_stats[regime])
            rs.update(pnl)
            self.regime_stats[regime] = asdict(rs)

        # Per-exchange
        if exchange:
            if exchange not in self.exchange_stats:
                self.exchange_stats[exchange] = asdict(ExchangeStats(exchange=exchange))
            es = ExchangeStats(**self.exchange_stats[exchange])
            es.update(pnl)
            self.exchange_stats[exchange] = asdict(es)

        # Auto-generate lessons
        self._check_lessons(symbol)

    def record_session(self, date: str, trades: int, exits: int, pnl: float,
                       win_rate: float, avg_rung: float, kelly: float, chandelier: float):
        """Record a daily session summary."""
        self.total_sessions += 1
        if not self.first_session_date:
            self.first_session_date = date
        self.sessions.append(asdict(SessionSummary(
            date=date, trades=trades, exits=exits, pnl=pnl,
            win_rate=win_rate, avg_rung=avg_rung,
            kelly_used=kelly, chandelier_mult=chandelier,
        )))
        # Keep last 90 days of sessions
        if len(self.sessions) > 90:
            self.sessions = self.sessions[-90:]

        # Record parameter snapshot
        self.param_history.append({
            "date": date,
            "kelly": kelly,
            "chandelier": chandelier,
            "win_rate": win_rate,
            "pnl": pnl,
        })
        if len(self.param_history) > 90:
            self.param_history = self.param_history[-90:]

        self.last_updated = datetime.now(timezone.utc).isoformat()

    def _check_lessons(self, symbol: str):
        """Auto-generate lessons from trade outcomes."""
        ts = self.ticker_stats.get(symbol)
        if not ts:
            return
        trades = ts.get("total_trades", 0)
        wr = ts.get("win_rate", 0.0)

        # Lesson: ticker with 10+ trades and <30% WR should be avoided
        if trades >= 10 and wr < 0.30:
            lesson_key = f"avoid_{symbol}"
            existing = [l for l in self.lessons if l.get("key") == lesson_key]
            if not existing:
                self.lessons.append({
                    "key": lesson_key,
                    "type": "avoid_ticker",
                    "symbol": symbol,
                    "reason": f"{symbol} has {wr:.0%} WR across {trades} trades — consider locking",
                    "created": datetime.now(timezone.utc).isoformat(),
                    "trades": trades,
                    "win_rate": wr,
                })

        # Lesson: ticker with 10+ trades and >70% WR is a strong performer
        if trades >= 10 and wr > 0.70:
            lesson_key = f"strong_{symbol}"
            existing = [l for l in self.lessons if l.get("key") == lesson_key]
            if not existing:
                self.lessons.append({
                    "key": lesson_key,
                    "type": "strong_ticker",
                    "symbol": symbol,
                    "reason": f"{symbol} has {wr:.0%} WR across {trades} trades — consider higher Kelly",
                    "created": datetime.now(timezone.utc).isoformat(),
                    "trades": trades,
                    "win_rate": wr,
                })

        # Keep lessons manageable
        if len(self.lessons) > 100:
            self.lessons = self.lessons[-100:]

    def get_ticker_kelly_recommendation(self, symbol: str) -> Optional[float]:
        """Get Kelly recommendation for a ticker based on cumulative performance."""
        ts = self.ticker_stats.get(symbol)
        if not ts or ts.get("total_trades", 0) < 5:
            return None
        wr = ts.get("win_rate", 0.5)
        avg_pnl = ts.get("avg_pnl", 0.0)
        # Kelly criterion: f* = (bp - q) / b
        # Simplified: if WR > 50%, scale up; if < 50%, scale down
        if wr > 0.60:
            return 0.20 * (1.0 + (wr - 0.60) / 0.40)  # up to 0.30
        elif wr < 0.40:
            return max(0.05, 0.20 * (wr / 0.40))  # down to 0.05
        return 0.20  # Default

    def get_regime_scale(self, regime: str) -> float:
        """Get confidence scaling for a regime based on cumulative performance."""
        rs = self.regime_stats.get(regime)
        if not rs or rs.get("total_trades", 0) < 10:
            return 1.0  # Default: no adjustment
        wr = rs.get("win_rate", 0.5)
        if wr > 0.55:
            return 1.0 + (wr - 0.55) * 2.0  # Scale up in good regimes
        elif wr < 0.40:
            return max(0.3, wr / 0.40)  # Scale down in bad regimes
        return 1.0

    def summary_text(self) -> str:
        """Human-readable summary for logs/reports."""
        lines = [
            "=== SYSTEM MEMORY ===",
            f"Sessions: {self.total_sessions} | First: {self.first_session_date or 'N/A'}",
            f"Trades: {self.total_trades} entries, {self.total_exits} exits",
            f"All-time: WR={self.all_time_win_rate:.1%} | PnL=£{self.cumulative_pnl:,.2f}",
            f"Tickers tracked: {len(self.ticker_stats)}",
            f"Regimes tracked: {len(self.regime_stats)}",
            f"Lessons learned: {len(self.lessons)}",
        ]
        # Top 5 tickers by trade count
        if self.ticker_stats:
            sorted_tickers = sorted(
                self.ticker_stats.items(),
                key=lambda x: x[1].get("total_trades", 0),
                reverse=True,
            )[:5]
            lines.append("Top tickers:")
            for sym, stats in sorted_tickers:
                t = stats.get("total_trades", 0)
                wr = stats.get("win_rate", 0.0)
                pnl = stats.get("total_pnl", 0.0)
                lines.append(f"  {sym}: {t} trades, WR={wr:.0%}, PnL=£{pnl:,.2f}")
        if self.lessons:
            lines.append(f"Active lessons: {len(self.lessons)}")
            for l in self.lessons[-3:]:
                lines.append(f"  - {l.get('reason', '')}")
        return "\n".join(lines)


def load_memory() -> SystemMemory:
    """Load system memory from disk. Returns fresh memory if file doesn't exist."""
    if not MEMORY_FILE.exists():
        log.info("No system memory found at %s — starting fresh", MEMORY_FILE)
        return SystemMemory(last_updated=datetime.now(timezone.utc).isoformat())
    try:
        with open(MEMORY_FILE) as f:
            data = json.load(f)
        mem = SystemMemory()
        # Populate from JSON with safe defaults for missing fields
        for k, v in data.items():
            if hasattr(mem, k):
                setattr(mem, k, v)
        log.info("Loaded system memory: %d sessions, %d trades, £%.2f cumulative PnL",
                 mem.total_sessions, mem.total_trades, mem.cumulative_pnl)
        return mem
    except Exception as e:
        log.warning("Failed to load system memory from %s: %s — starting fresh", MEMORY_FILE, e)
        return SystemMemory(last_updated=datetime.now(timezone.utc).isoformat())


def save_memory(mem: SystemMemory):
    """Atomically save system memory to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    mem.last_updated = datetime.now(timezone.utc).isoformat()
    tmp_path = MEMORY_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(asdict(mem), f, indent=2, default=str)
        os.rename(str(tmp_path), str(MEMORY_FILE))
        log.info("Saved system memory: %d sessions, %d trades, £%.2f PnL",
                 mem.total_sessions, mem.total_trades, mem.cumulative_pnl)
    except Exception as e:
        log.error("Failed to save system memory: %s", e)
        if tmp_path.exists():
            tmp_path.unlink()


# ---------------------------------------------------------------------------
# CLI: show current memory state
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    mem = load_memory()
    print(mem.summary_text())
