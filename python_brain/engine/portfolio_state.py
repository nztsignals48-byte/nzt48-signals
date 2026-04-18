"""Portfolio state: equity, HWM, drawdown, per-strategy/ticker/sector/account books."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Position:
    signal_id: str
    ticker: str
    strategy: str
    account: str
    entry_price: float
    entry_ts_ns: int
    size_shares: int
    mae_bps: float = 0.0
    mfe_bps: float = 0.0
    peak_price: float = 0.0


@dataclass
class PortfolioState:
    equity_gbp: float = 20000.0
    hwm_gbp: float = 20000.0
    consecutive_losses: int = 0
    positions: List[Position] = field(default_factory=list)
    per_strategy_gbp: Dict[str, float] = field(default_factory=dict)
    per_ticker_gbp: Dict[str, float] = field(default_factory=dict)
    per_sector_gbp: Dict[str, float] = field(default_factory=dict)
    per_account_gbp: Dict[str, float] = field(default_factory=dict)

    @property
    def drawdown_pct(self) -> float:
        if self.hwm_gbp <= 0:
            return 0.0
        return max(0.0, (self.hwm_gbp - self.equity_gbp) / self.hwm_gbp)

    def on_open(self, p: Position, size_gbp: float) -> None:
        self.positions.append(p)
        self.per_strategy_gbp[p.strategy] = self.per_strategy_gbp.get(p.strategy, 0.0) + size_gbp
        self.per_ticker_gbp[p.ticker]     = self.per_ticker_gbp.get(p.ticker, 0.0) + size_gbp
        self.per_account_gbp[p.account]   = self.per_account_gbp.get(p.account, 0.0) + size_gbp

    def on_close(self, p: Position, pnl_gbp: float, size_gbp: float) -> None:
        if p in self.positions:
            self.positions.remove(p)
        self.per_strategy_gbp[p.strategy] = max(0.0, self.per_strategy_gbp.get(p.strategy, 0.0) - size_gbp)
        self.per_ticker_gbp[p.ticker]     = max(0.0, self.per_ticker_gbp.get(p.ticker, 0.0) - size_gbp)
        self.per_account_gbp[p.account]   = max(0.0, self.per_account_gbp.get(p.account, 0.0) - size_gbp)
        self.equity_gbp += pnl_gbp
        self.hwm_gbp = max(self.hwm_gbp, self.equity_gbp)
        if pnl_gbp < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
