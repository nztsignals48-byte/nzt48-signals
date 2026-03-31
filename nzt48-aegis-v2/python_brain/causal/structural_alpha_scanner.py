"""Structural Alpha Scanner — Book 48.

Identifies and scores structural (causal) alpha opportunities across
10 mechanistic sources. Structural alpha persists 5-20+ years because
it requires regulatory or mechanical change to eliminate.

10 Sources:
  1. ETP daily rebalancing flows (legal leverage maintenance)
  2. Options expiry pinning (delta hedge requirement)
  3. Index reconstitution (fiduciary tracking obligation)
  4. Dividend ex-date effects (exchange price adjustment)
  5. Margin call cascades (regulatory margin req)
  6. ETF creation/redemption arbitrage (AP contractual mechanism)
  7. Central bank intervention (monetary policy mandate)
  8. Seasonal tax-loss selling (tax code optimisation)
  9. Settlement cycle effects (regulatory delivery req)
  10. Market maker inventory adjustment (risk limit constraint)

Architecture:
  CalendarEngine (event dates) + FlowEstimator (flow magnitudes)
  → SignalCombiner → StructuralSignal (ticker, source, conviction 0-100)

Nightly usage (step 5.45):
    from python_brain.causal.structural_alpha_scanner import (
        StructuralAlphaScanner, run_nightly_structural_scan,
    )
    results = run_nightly_structural_scan()
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

log = logging.getLogger("structural_alpha")


class AlphaSource(Enum):
    """The 10 structural alpha sources from Book 48."""
    ETP_REBALANCING = "etp_rebalancing"
    OPTIONS_EXPIRY = "options_expiry"
    INDEX_RECONSTITUTION = "index_reconstitution"
    DIVIDEND_EX_DATE = "dividend_ex_date"
    MARGIN_CASCADE = "margin_cascade"
    ETF_CREATION_REDEMPTION = "etf_creation_redemption"
    CENTRAL_BANK = "central_bank"
    TAX_LOSS_SELLING = "tax_loss_selling"
    SETTLEMENT_CYCLE = "settlement_cycle"
    MM_INVENTORY = "mm_inventory"


# Source → base conviction points (0-20 each)
SOURCE_BASE_CONVICTION: Dict[AlphaSource, int] = {
    AlphaSource.ETP_REBALANCING: 15,        # Daily, predictable flow
    AlphaSource.OPTIONS_EXPIRY: 12,          # Monthly/weekly, strong near expiry
    AlphaSource.INDEX_RECONSTITUTION: 18,    # Quarterly, very large flow
    AlphaSource.DIVIDEND_EX_DATE: 8,         # Per-event, smaller edge
    AlphaSource.MARGIN_CASCADE: 15,          # Episodic, very large when active
    AlphaSource.ETF_CREATION_REDEMPTION: 10, # Continuous, modest edge
    AlphaSource.CENTRAL_BANK: 14,            # 8x/year per bank, large drift
    AlphaSource.TAX_LOSS_SELLING: 12,        # Annual, concentrated window
    AlphaSource.SETTLEMENT_CYCLE: 5,         # Continuous, small edge
    AlphaSource.MM_INVENTORY: 10,            # Continuous, moderate edge
}

# Source → expected edge in basis points (mid-range)
SOURCE_EXPECTED_BPS: Dict[AlphaSource, int] = {
    AlphaSource.ETP_REBALANCING: 25,
    AlphaSource.OPTIONS_EXPIRY: 20,
    AlphaSource.INDEX_RECONSTITUTION: 200,
    AlphaSource.DIVIDEND_EX_DATE: 12,
    AlphaSource.MARGIN_CASCADE: 300,
    AlphaSource.ETF_CREATION_REDEMPTION: 50,
    AlphaSource.CENTRAL_BANK: 100,
    AlphaSource.TAX_LOSS_SELLING: 100,
    AlphaSource.SETTLEMENT_CYCLE: 10,
    AlphaSource.MM_INVENTORY: 25,
}

# Source → which strategy family benefits
SOURCE_STRATEGY: Dict[AlphaSource, str] = {
    AlphaSource.ETP_REBALANCING: "S2_Reversion",
    AlphaSource.OPTIONS_EXPIRY: "S6_Catalyst",
    AlphaSource.INDEX_RECONSTITUTION: "S3_MacroTrend",
    AlphaSource.DIVIDEND_EX_DATE: "S6_Catalyst",
    AlphaSource.MARGIN_CASCADE: "S7_TailHedge",
    AlphaSource.ETF_CREATION_REDEMPTION: "S2_Reversion",
    AlphaSource.CENTRAL_BANK: "S6_Catalyst",
    AlphaSource.TAX_LOSS_SELLING: "S3_MacroTrend",
    AlphaSource.SETTLEMENT_CYCLE: "S2_Reversion",
    AlphaSource.MM_INVENTORY: "S1_Microstructure",
}


@dataclass
class StructuralSignal:
    """A scored structural alpha opportunity."""
    ticker: str
    source: str              # AlphaSource.value
    direction: str           # "long" or "short"
    conviction: int          # 0-100
    expected_bps: int        # Expected edge in basis points
    causal_chain: str        # Human-readable causal mechanism
    strategy: str            # Target strategy (e.g., "S2_Reversion")
    event_date: Optional[str] = None  # When the structural event occurs
    expiry_date: Optional[str] = None  # When the opportunity expires


@dataclass
class StructuralScanResult:
    """Result of a full structural alpha scan."""
    date: str
    signals: List[StructuralSignal] = field(default_factory=list)
    active_sources: int = 0
    total_conviction: int = 0
    top_opportunity: Optional[str] = None


# ---------------------------------------------------------------------------
# Calendar Engine — event date tracking
# ---------------------------------------------------------------------------

# Monthly US options expiry: 3rd Friday of each month
def _third_friday(year: int, month: int) -> date:
    """Compute 3rd Friday of a given month (US options expiry)."""
    d = date(year, month, 15)
    while d.weekday() != 4:  # Friday
        d += timedelta(days=1)
    return d


# FTSE 100 reconstitution: typically 3rd Friday of March, June, Sep, Dec
FTSE_RECON_MONTHS = {3, 6, 9, 12}

# Central bank meeting months (approximate — 8 meetings/year)
FED_MONTHS = {1, 3, 5, 6, 7, 9, 11, 12}
BOE_MONTHS = {2, 3, 5, 6, 8, 9, 11, 12}

# UK tax year end: April 5
UK_TAX_YEAR_END = (4, 5)

# Tax-loss selling window: March 15 - April 5 (UK), Dec 15 - Dec 31 (US)
TAX_LOSS_UK_START = (3, 15)
TAX_LOSS_UK_END = (4, 5)
TAX_LOSS_US_START = (12, 15)
TAX_LOSS_US_END = (12, 31)


class CalendarEngine:
    """Identify upcoming structural events."""

    def __init__(self, today: Optional[date] = None):
        self._today = today or datetime.now(timezone.utc).date()

    def upcoming_events(self, lookforward_days: int = 7) -> List[Dict]:
        """Return structural events within the lookforward window."""
        events: List[Dict] = []
        window_end = self._today + timedelta(days=lookforward_days)

        # Options expiry (monthly)
        for month_offset in range(3):
            m = self._today.month + month_offset
            y = self._today.year + (m - 1) // 12
            m = ((m - 1) % 12) + 1
            exp = _third_friday(y, m)
            if self._today <= exp <= window_end:
                events.append({
                    "source": AlphaSource.OPTIONS_EXPIRY.value,
                    "date": exp.isoformat(),
                    "description": f"Monthly options expiry {exp}",
                    "days_until": (exp - self._today).days,
                })

        # FTSE reconstitution
        for m in FTSE_RECON_MONTHS:
            recon = _third_friday(self._today.year, m)
            if self._today <= recon <= window_end:
                events.append({
                    "source": AlphaSource.INDEX_RECONSTITUTION.value,
                    "date": recon.isoformat(),
                    "description": f"FTSE 100 reconstitution {recon}",
                    "days_until": (recon - self._today).days,
                })

        # Central bank meetings (approximate — check within window)
        for bank, months in [("Fed", FED_MONTHS), ("BoE", BOE_MONTHS)]:
            if self._today.month in months:
                # Approximate meeting in the 3rd week
                approx = date(self._today.year, self._today.month, 20)
                if self._today <= approx <= window_end:
                    events.append({
                        "source": AlphaSource.CENTRAL_BANK.value,
                        "date": approx.isoformat(),
                        "description": f"{bank} policy meeting ~{approx}",
                        "days_until": (approx - self._today).days,
                    })

        # Tax-loss selling windows
        for label, start, end in [
            ("UK", TAX_LOSS_UK_START, TAX_LOSS_UK_END),
            ("US", TAX_LOSS_US_START, TAX_LOSS_US_END),
        ]:
            s = date(self._today.year, start[0], start[1])
            e = date(self._today.year, end[0], end[1])
            if s <= self._today <= e:
                events.append({
                    "source": AlphaSource.TAX_LOSS_SELLING.value,
                    "date": e.isoformat(),
                    "description": f"{label} tax-loss selling window active until {e}",
                    "days_until": (e - self._today).days,
                })

        return sorted(events, key=lambda x: x.get("days_until", 99))


# ---------------------------------------------------------------------------
# Flow Estimator — estimate structural flow magnitude
# ---------------------------------------------------------------------------

class FlowEstimator:
    """Estimate structural flow magnitudes from available data."""

    # ETP AUM estimates (GBP millions) — rough, updated quarterly
    ETP_AUM: Dict[str, float] = {
        "3USL.L": 500.0, "QQQ3.L": 300.0, "3UKL.L": 150.0,
        "NVD3.L": 200.0, "3TSL.L": 150.0, "AAP3.L": 80.0,
        "AMD3.L": 60.0, "3MSF.L": 100.0, "3AMZ.L": 80.0,
        "3GOO.L": 70.0, "3FB.L": 60.0, "3EUL.L": 100.0,
    }

    ETP_LEVERAGE: Dict[str, int] = {
        "3USL.L": 3, "QQQ3.L": 3, "3UKL.L": 3,
        "NVD3.L": 3, "3TSL.L": 3, "AAP3.L": 3,
        "AMD3.L": 3, "3MSF.L": 3, "3AMZ.L": 3,
        "3GOO.L": 3, "3FB.L": 3, "3EUL.L": 3,
        "3USS.L": -3, "QQQS.L": -3, "NV3S.L": -3,
        "TS3S.L": -3, "3UKS.L": -3,
    }

    def estimate_rebalancing_flow(
        self, ticker: str, underlying_return: float,
    ) -> Dict:
        """Estimate ETP rebalancing flow from underlying daily return.

        Flow ≈ AUM × L × r × (L-1) / (1 + L×r)
        Simplified: Flow ≈ AUM × L × (L-1) × r  for small r
        Direction: same as day's return (pro-cyclical)
        """
        aum = self.ETP_AUM.get(ticker, 50.0)
        L = abs(self.ETP_LEVERAGE.get(ticker, 3))
        r = underlying_return

        if abs(r) < 0.001:
            return {"flow_gbp_m": 0.0, "direction": "neutral", "impact_bps": 0}

        flow = aum * L * (L - 1) * r
        # Impact ≈ flow / daily_volume (rough: ~10% of AUM trades daily)
        daily_vol_est = aum * 0.10
        impact_bps = abs(flow / max(daily_vol_est, 1.0)) * 10000

        return {
            "flow_gbp_m": round(flow, 2),
            "direction": "buy" if flow > 0 else "sell",
            "impact_bps": round(impact_bps, 1),
            "aum_gbp_m": aum,
        }

    def estimate_nav_premium(
        self, etp_price: float, nav_estimate: float,
    ) -> Dict:
        """Estimate NAV premium/discount.

        AP arbitrage converges price to NAV. Premium > 100bps or
        discount > 100bps signals convergence opportunity.
        """
        if nav_estimate <= 0 or etp_price <= 0:
            return {"premium_bps": 0, "opportunity": False}

        premium = (etp_price / nav_estimate - 1.0) * 10000
        opportunity = abs(premium) > 100  # >1% premium/discount

        return {
            "premium_bps": round(premium, 1),
            "opportunity": opportunity,
            "direction": "short" if premium > 100 else "long" if premium < -100 else "neutral",
        }


# ---------------------------------------------------------------------------
# Signal Combiner — score and combine structural sources
# ---------------------------------------------------------------------------

class StructuralAlphaScanner:
    """Scan for structural alpha opportunities."""

    def __init__(self, today: Optional[date] = None):
        self._calendar = CalendarEngine(today)
        self._flow = FlowEstimator()
        self._today = today or datetime.now(timezone.utc).date()

    def scan(
        self,
        tickers: Optional[List[str]] = None,
        underlying_returns: Optional[Dict[str, float]] = None,
        vix: float = 20.0,
    ) -> StructuralScanResult:
        """Run full structural alpha scan.

        Args:
            tickers: List of ETP tickers to scan (default: all known)
            underlying_returns: {ticker: daily_return} for rebalancing flow
            vix: Current VIX level for regime adjustments

        Returns: StructuralScanResult with scored signals
        """
        if tickers is None:
            tickers = list(FlowEstimator.ETP_AUM.keys())
        if underlying_returns is None:
            underlying_returns = {}

        signals: List[StructuralSignal] = []
        active_sources: Set[str] = set()

        # 1. Calendar-based events
        events = self._calendar.upcoming_events(lookforward_days=7)
        for event in events:
            src = event["source"]
            active_sources.add(src)
            base_conv = SOURCE_BASE_CONVICTION.get(
                AlphaSource(src), 10
            )

            # Proximity bonus: closer events get higher conviction
            days_until = event.get("days_until", 7)
            proximity_mult = max(0.5, 1.0 - days_until * 0.1)
            conviction = int(base_conv * proximity_mult)

            for ticker in tickers:
                strategy = SOURCE_STRATEGY.get(AlphaSource(src), "S2_Reversion")
                signals.append(StructuralSignal(
                    ticker=ticker,
                    source=src,
                    direction=self._event_direction(src, ticker, vix),
                    conviction=conviction,
                    expected_bps=SOURCE_EXPECTED_BPS.get(AlphaSource(src), 10),
                    causal_chain=event["description"],
                    strategy=strategy,
                    event_date=event.get("date"),
                ))

        # 2. ETP rebalancing flows (daily, always active)
        for ticker in tickers:
            ret = underlying_returns.get(ticker, 0.0)
            if abs(ret) < 0.005:
                continue  # <0.5% move — negligible flow

            flow = self._flow.estimate_rebalancing_flow(ticker, ret)
            if flow["impact_bps"] < 5:
                continue

            active_sources.add(AlphaSource.ETP_REBALANCING.value)

            # Conviction scales with impact magnitude
            impact_conv = min(20, int(flow["impact_bps"] / 2))
            base = SOURCE_BASE_CONVICTION[AlphaSource.ETP_REBALANCING]
            conviction = min(100, base + impact_conv)

            # Rebalancing is pro-cyclical: after large up move, ETP buys more
            # Reversion signal: fade the rebalancing (opposite direction)
            rebal_dir = "short" if flow["direction"] == "buy" else "long"

            signals.append(StructuralSignal(
                ticker=ticker,
                source=AlphaSource.ETP_REBALANCING.value,
                direction=rebal_dir,
                conviction=conviction,
                expected_bps=int(flow["impact_bps"]),
                causal_chain=f"Rebalancing flow {flow['flow_gbp_m']:.1f}M GBP ({flow['direction']}), "
                             f"impact ~{flow['impact_bps']:.0f}bps",
                strategy="S2_Reversion",
            ))

        # 3. Margin cascade detection (episodic)
        if vix > 30:
            active_sources.add(AlphaSource.MARGIN_CASCADE.value)
            for ticker in tickers:
                signals.append(StructuralSignal(
                    ticker=ticker,
                    source=AlphaSource.MARGIN_CASCADE.value,
                    direction="long",  # Buy after cascade exhaustion
                    conviction=min(100, int(15 + (vix - 30) * 2)),
                    expected_bps=SOURCE_EXPECTED_BPS[AlphaSource.MARGIN_CASCADE],
                    causal_chain=f"VIX={vix:.0f} > 30: margin cascade conditions, "
                                 f"post-exhaustion reversion expected",
                    strategy="S7_TailHedge",
                ))

        # Apply multi-source bonus
        for sig in signals:
            ticker_sources = sum(
                1 for s in signals
                if s.ticker == sig.ticker and s.source != sig.source
            )
            if ticker_sources >= 2:
                sig.conviction = min(100, int(sig.conviction * 1.5))
            elif ticker_sources >= 1:
                sig.conviction = min(100, int(sig.conviction * 1.3))

        # Sort by conviction descending
        signals.sort(key=lambda s: s.conviction, reverse=True)

        result = StructuralScanResult(
            date=self._today.isoformat(),
            signals=signals[:50],  # Top 50 max
            active_sources=len(active_sources),
            total_conviction=sum(s.conviction for s in signals[:50]),
            top_opportunity=(
                f"{signals[0].ticker} {signals[0].source} ({signals[0].conviction})"
                if signals else None
            ),
        )

        return result

    def _event_direction(
        self, source: str, ticker: str, vix: float,
    ) -> str:
        """Determine trade direction for a structural event."""
        # Tax-loss selling → buy (prices depressed)
        if source == AlphaSource.TAX_LOSS_SELLING.value:
            return "long"
        # Central bank → depends on regime (trend follow in low vol)
        if source == AlphaSource.CENTRAL_BANK.value:
            return "long" if vix < 20 else "short"
        # Options expiry → mean reversion (pinning)
        if source == AlphaSource.OPTIONS_EXPIRY.value:
            return "long"  # Default; refined with OI data
        # Index reconstitution → long additions
        if source == AlphaSource.INDEX_RECONSTITUTION.value:
            return "long"
        # Default
        return "long"


# ---------------------------------------------------------------------------
# Nightly entry point
# ---------------------------------------------------------------------------

def run_nightly_structural_scan(
    underlying_returns: Optional[Dict[str, float]] = None,
    vix: float = 20.0,
) -> Dict:
    """Nightly structural alpha scan.

    Runs all 10 structural source checks and writes report.

    Returns: Summary dict for nightly_v6 recommendations.
    """
    import os

    data_dir = Path(os.environ.get("AEGIS_DATA_DIR", "/app/data"))
    reports_dir = data_dir / "claude" / "reviews"
    reports_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    scanner = StructuralAlphaScanner()
    result = scanner.scan(
        underlying_returns=underlying_returns or {},
        vix=vix,
    )

    # Write detailed report
    report = {
        "date": today,
        "active_sources": result.active_sources,
        "total_signals": len(result.signals),
        "total_conviction": result.total_conviction,
        "top_opportunity": result.top_opportunity,
        "signals": [asdict(sig) for sig in result.signals],
    }

    report_path = reports_dir / f"structural_alpha_{today}.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    log.info(
        "Structural scan: %d active sources, %d signals, top=%s → %s",
        result.active_sources, len(result.signals),
        result.top_opportunity, report_path,
    )

    return {
        "status": "complete",
        "active_sources": result.active_sources,
        "total_signals": len(result.signals),
        "top_opportunity": result.top_opportunity,
        "report_path": str(report_path),
        "signals_by_strategy": _group_by_strategy(result.signals),
    }


def _group_by_strategy(signals: List[StructuralSignal]) -> Dict[str, int]:
    """Count signals per target strategy."""
    counts: Dict[str, int] = {}
    for s in signals:
        counts[s.strategy] = counts.get(s.strategy, 0) + 1
    return counts


def detect_structural_break(closes: "np.ndarray") -> Dict:
    """Detect a structural break in a price series using CUSUM test.

    Called by bridge.py as a pre-gate. If a recent structural break is
    detected, the confidence floor is raised to prevent entries during
    regime transitions.

    Args:
        closes: numpy array of close prices (newest last).

    Returns:
        dict with:
          - significant (bool): whether a structural break was detected
          - recency_bars (int): how many bars ago the break occurred
    """
    try:
        import numpy as _np
    except ImportError:
        return {"significant": False, "recency_bars": 999}

    if len(closes) < 10:
        return {"significant": False, "recency_bars": 999}

    arr = _np.asarray(closes, dtype=float)
    mean_val = float(_np.mean(arr))
    std_val = float(_np.std(arr, ddof=1))

    if std_val < 1e-10:
        return {"significant": False, "recency_bars": 999}

    # CUSUM: cumulative sum of deviations from the mean
    deviations = arr - mean_val
    cusum = _np.cumsum(deviations)

    # Max absolute deviation in the CUSUM series
    abs_cusum = _np.abs(cusum)
    max_dev = float(_np.max(abs_cusum))
    max_idx = int(_np.argmax(abs_cusum))

    # Threshold: 3 * std * sqrt(n) is the CUSUM critical value approximation
    threshold = 3.0 * std_val
    significant = max_dev > threshold

    # Recency: how many bars from the end is the break point
    recency_bars = len(arr) - 1 - max_idx

    return {
        "significant": significant,
        "recency_bars": recency_bars,
    }
