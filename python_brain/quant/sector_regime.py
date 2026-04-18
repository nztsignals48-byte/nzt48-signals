"""Sector regime detection — per-sector volatility + trend regime.

A "calm" SPY can hide a "crisis" semiconductor sector. Tracks sector ETF
returns (XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLRE, XLC) and classifies
each into {calm, trending, choppy, crisis}. Strategies trading tech stocks
receive XLK regime multiplier instead of broad regime.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np


SECTOR_ETFS = {
    "XLK": "tech",
    "XLF": "financials",
    "XLE": "energy",
    "XLV": "healthcare",
    "XLI": "industrials",
    "XLY": "consumer_discretionary",
    "XLP": "consumer_staples",
    "XLU": "utilities",
    "XLB": "materials",
    "XLRE": "real_estate",
    "XLC": "communication_services",
}

# Rough mapping from major tickers to their sector ETF
TICKER_SECTOR = {
    # Tech
    **{t: "XLK" for t in ["AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "META", "AMD", "INTC", "CRM",
                          "ORCL", "ADBE", "NFLX", "AVGO", "TXN", "QCOM", "IBM", "NOW", "INTU"]},
    # Financials
    **{t: "XLF" for t in ["JPM", "BAC", "WFC", "C", "GS", "MS", "BLK", "AXP", "SCHW", "USB", "PNC"]},
    # Energy
    **{t: "XLE" for t in ["XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "VLO", "OXY", "PXD"]},
    # Healthcare
    **{t: "XLV" for t in ["UNH", "JNJ", "LLY", "PFE", "ABBV", "MRK", "TMO", "DHR", "ABT", "AMGN"]},
    # Industrials
    **{t: "XLI" for t in ["BA", "CAT", "UNP", "GE", "HON", "UPS", "DE", "LMT", "RTX", "MMM"]},
    # Consumer discretionary
    **{t: "XLY" for t in ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "TGT"]},
    # Consumer staples
    **{t: "XLP" for t in ["WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "MDLZ", "CL"]},
    # Communication
    **{t: "XLC" for t in ["TMUS", "VZ", "T", "CMCSA", "DIS", "EA", "ATVI", "CHTR"]},
}


@dataclass
class SectorRegimeState:
    sector: str
    regime: str
    vol_annualized: float
    trend_strength: float
    size_multiplier: float


class SectorRegimeDetector:
    def __init__(self, window_days: int = 30):
        self.window = window_days
        self.returns: dict[str, list[float]] = defaultdict(list)

    def add_return(self, etf_symbol: str, daily_return: float) -> None:
        if etf_symbol not in SECTOR_ETFS:
            return
        self.returns[etf_symbol].append(daily_return)
        if len(self.returns[etf_symbol]) > self.window:
            self.returns[etf_symbol].pop(0)

    def classify(self, etf_symbol: str) -> SectorRegimeState:
        rets = self.returns.get(etf_symbol, [])
        if len(rets) < 10:
            return SectorRegimeState(
                sector=SECTOR_ETFS.get(etf_symbol, "unknown"),
                regime="uninitialized",
                vol_annualized=0.0,
                trend_strength=0.0,
                size_multiplier=1.0,
            )
        arr = np.array(rets[-self.window:])
        vol = float(arr.std() * np.sqrt(252))
        mean = float(arr.mean())
        trend = mean / max(arr.std(), 1e-6)

        # Regime logic
        if vol > 0.40:
            regime = "crisis"
            size_mult = 0.2
        elif vol > 0.25:
            regime = "stressed"
            size_mult = 0.45
        elif vol > 0.15:
            if abs(trend) > 0.3:
                regime = "trending"
                size_mult = 1.1
            else:
                regime = "choppy"
                size_mult = 0.6
        else:
            if abs(trend) > 0.2:
                regime = "trending"
                size_mult = 1.2
            else:
                regime = "calm"
                size_mult = 1.0

        return SectorRegimeState(
            sector=SECTOR_ETFS.get(etf_symbol, "unknown"),
            regime=regime,
            vol_annualized=vol,
            trend_strength=trend,
            size_multiplier=size_mult,
        )

    def classify_ticker(self, ticker: str) -> SectorRegimeState | None:
        """For a given ticker, return its sector regime."""
        etf = TICKER_SECTOR.get(ticker.upper())
        if not etf:
            return None
        return self.classify(etf)

    def snapshot_all(self) -> dict:
        return {
            etf: self.classify(etf).__dict__
            for etf in self.returns
            if self.returns[etf]
        }


if __name__ == "__main__":
    import sys
    if "--smoke" in sys.argv:
        det = SectorRegimeDetector()
        rng = np.random.default_rng(42)
        # XLK in crisis
        for _ in range(30):
            det.add_return("XLK", rng.normal(-0.01, 0.04))
        # XLP calm
        for _ in range(30):
            det.add_return("XLP", rng.normal(0.0005, 0.008))

        print(f"XLK: {det.classify('XLK')}")
        print(f"XLP: {det.classify('XLP')}")
        print(f"NVDA (via sector): {det.classify_ticker('NVDA')}")
        print("OK")
