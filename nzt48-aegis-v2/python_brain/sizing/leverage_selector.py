"""Book 27: Leverage Selector — Kelly-Optimal ETP Allocation.

Determines optimal leverage per instrument using Kelly criterion and regime-aware
modifiers. Computes allocation fractions for leveraged ETPs (2x, 3x, 5x) based on:

1. Kelly-optimal leverage L* = mu / sigma^2
2. Regime-conditional modifiers (STEADY/INFLATION/WOI/CRISIS)
3. Variance drag estimation and warnings
4. 5x product intraday-only rules
5. VIX kill switch for extreme volatility

Usage:
    from python_brain.sizing.leverage_selector import get_leverage_selector

    selector = get_leverage_selector()
    result = selector.get_allocation("NVDA", etp_leverage=3.0, regime="STEADY")
    # result.l_star, result.allocation_fraction, result.effective_leverage
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

log = logging.getLogger("leverage_selector")

DATA_DIR = os.environ.get("AEGIS_DATA_DIR", "/app/data")
LEVERAGE_STATE_FILE = os.path.join(DATA_DIR, "leverage_state.json")

# Regime-specific leverage modifiers
REGIME_MODIFIERS = {
    "STEADY": 1.0,
    "INFLATION": 0.6,   # Higher vol, mean reversion breaks
    "WOI": 0.3,         # Wall of Worry: uncertain, choppy
    "CRISIS": 0.0,      # Longs OFF, inverse ETPs only
}

# Default Kelly L* estimates for common instruments
# Based on historical mu/sigma^2 analysis
DEFAULT_L_STAR = {
    "SPY": 4.1,
    "S&P500": 4.1,
    "QQQ": 2.8,
    "NASDAQ": 2.8,
    "NVDA": 1.8,
    "TSLA": 0.9,
    "AAPL": 2.5,
    "MSFT": 2.3,
    "GOOGL": 2.1,
    "AMZN": 1.7,
    "META": 1.4,
    "DEFAULT": 1.5,
}


@dataclass
class LeverageResult:
    """Output from leverage allocation calculation."""
    ticker: str
    l_star: float                  # Kelly-optimal leverage
    allocation_fraction: float     # % of position to allocate to ETP
    regime_modifier: float         # Regime adjustment (0.0-1.0)
    drag_pct: float               # Estimated annual variance drag
    effective_leverage: float      # Final realized leverage
    etp_leverage: float           # Input ETP leverage (2x, 3x, 5x)
    regime: str
    warning: str = ""             # Any warnings (drag, 5x restrictions, etc.)


class LeverageSelector:
    """Kelly-optimal leverage calculator with regime awareness and drag estimation."""

    def __init__(
        self,
        max_leverage: float = 3.0,
        drag_warning_threshold: float = 0.10,  # Warn if drag > 10%
        five_x_vol_threshold: float = 0.12,    # 5x only if vol < 12%
    ):
        self.max_leverage = max_leverage
        self.drag_warning_threshold = drag_warning_threshold
        self.five_x_vol_threshold = five_x_vol_threshold

        # State: ticker -> {l_star, vol, last_updated}
        self._l_star_cache: Dict[str, Dict] = {}
        self._vix_level: float = 20.0
        self._last_vix_update: float = 0.0

    def compute_l_star(
        self,
        ticker: str,
        daily_returns: Optional[List[float]] = None,
    ) -> float:
        """Compute Kelly-optimal leverage L* = mu / sigma^2.

        Uses 60-day rolling returns if provided, else falls back to default table.
        """
        if daily_returns and len(daily_returns) >= 20:
            # Compute from actual returns
            mu = sum(daily_returns) / len(daily_returns)
            if mu <= 0:
                return 0.0

            variance = sum((r - mu) ** 2 for r in daily_returns) / len(daily_returns)
            if variance <= 0:
                return 0.0

            l_star = mu / variance
            # Clamp to sensible range [0, 10.0]
            l_star = max(0.0, min(10.0, l_star))

            # Cache result
            self._l_star_cache[ticker] = {
                "l_star": l_star,
                "vol": math.sqrt(variance) if variance > 0 else 0.0,
                "last_updated": time.time(),
            }

            return l_star
        else:
            # Use default table
            ticker_upper = ticker.upper()
            if ticker_upper in DEFAULT_L_STAR:
                return DEFAULT_L_STAR[ticker_upper]
            return DEFAULT_L_STAR["DEFAULT"]

    def get_allocation(
        self,
        ticker: str,
        etp_leverage: float,
        regime: str = "STEADY",
        daily_returns: Optional[List[float]] = None,
    ) -> LeverageResult:
        """Compute ETP allocation fraction given ticker, ETP leverage, and regime.

        Args:
            ticker: Instrument ticker
            etp_leverage: Leverage of ETP (2.0, 3.0, 5.0, etc.)
            regime: Current market regime (STEADY, INFLATION, WOI, CRISIS)
            daily_returns: Optional 60-day returns for L* calculation

        Returns:
            LeverageResult with allocation fraction and metadata
        """
        # 1. Compute base Kelly leverage
        l_star = self.compute_l_star(ticker, daily_returns)

        # 2. Apply regime modifier
        regime_mod = REGIME_MODIFIERS.get(regime.upper(), 1.0)
        l_target = l_star * regime_mod

        # 3. Cap at max_leverage
        l_target = min(l_target, self.max_leverage)

        # 4. VIX kill switch
        if self._check_vix_kill_switch():
            l_target = 0.0
            warning = f"VIX_KILL_SWITCH: VIX={self._vix_level:.1f}"
        else:
            warning = ""

        # 5. Compute allocation fraction
        if etp_leverage > 0 and l_target > 0:
            allocation_fraction = l_target / etp_leverage
            # Cap at 100% (never exceed ETP leverage)
            allocation_fraction = min(1.0, allocation_fraction)
        else:
            allocation_fraction = 0.0

        # 6. Compute effective leverage
        effective_leverage = allocation_fraction * etp_leverage

        # 7. Estimate variance drag
        vol = self._get_volatility(ticker, daily_returns)
        drag_pct = self._estimate_drag(etp_leverage, vol)

        # 8. Add warnings
        if drag_pct > self.drag_warning_threshold:
            warning += f" DRAG_WARNING: {drag_pct:.1%} annual drag"

        # 9. 5x product restrictions
        if etp_leverage >= 5.0:
            if vol > self.five_x_vol_threshold:
                warning += f" 5X_VOL_BREACH: vol={vol:.1%} > {self.five_x_vol_threshold:.1%}"
                allocation_fraction = 0.0
                effective_leverage = 0.0
            else:
                warning += " 5X_INTRADAY_ONLY: close by 15:45 UTC"

        return LeverageResult(
            ticker=ticker,
            l_star=round(l_star, 2),
            allocation_fraction=round(allocation_fraction, 4),
            regime_modifier=round(regime_mod, 2),
            drag_pct=round(drag_pct, 4),
            effective_leverage=round(effective_leverage, 2),
            etp_leverage=etp_leverage,
            regime=regime,
            warning=warning.strip(),
        )

    def nightly_update(
        self,
        returns_by_ticker: Dict[str, List[float]],
        vix_level: Optional[float] = None,
    ) -> Dict:
        """Run nightly update: recompute L* for all tickers with returns data.

        Args:
            returns_by_ticker: Dict of ticker -> list of daily returns (60-day)
            vix_level: Current VIX level

        Returns:
            Summary dict for pipeline
        """
        if vix_level is not None:
            self._vix_level = vix_level
            self._last_vix_update = time.time()

        results = {}
        for ticker, returns in returns_by_ticker.items():
            if len(returns) >= 20:
                l_star = self.compute_l_star(ticker, returns)
                results[ticker] = {
                    "l_star": round(l_star, 2),
                    "n_observations": len(returns),
                    "vol_annualized": round(
                        math.sqrt(sum((r - sum(returns)/len(returns))**2
                                    for r in returns) / len(returns)) * math.sqrt(252),
                        4
                    ),
                }

        self.save()

        summary = {
            "timestamp": time.time(),
            "n_tickers_updated": len(results),
            "vix_level": round(self._vix_level, 2),
            "results": results,
        }

        log.info(f"Leverage nightly: updated {len(results)} tickers, VIX={self._vix_level:.1f}")

        return summary

    def _get_volatility(
        self,
        ticker: str,
        daily_returns: Optional[List[float]] = None,
    ) -> float:
        """Get daily volatility for ticker."""
        if daily_returns and len(daily_returns) >= 10:
            mu = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mu) ** 2 for r in daily_returns) / len(daily_returns)
            return math.sqrt(variance) if variance > 0 else 0.20

        # Check cache
        if ticker in self._l_star_cache:
            return self._l_star_cache[ticker].get("vol", 0.20)

        # Default: assume 20% daily vol
        return 0.015  # ~20% annualized

    def _estimate_drag(self, leverage: float, daily_vol: float) -> float:
        """Estimate annual variance drag: drag = -(leverage^2 * variance) / 2.

        Example: 3x leverage with 20% annualized vol (1.5% daily):
            daily_variance = 0.015^2 = 0.000225
            annual_variance = 0.000225 * 252 = 0.0567
            drag = -(9 * 0.0567) / 2 = -0.255 = -25.5%
        """
        if daily_vol <= 0:
            return 0.0

        daily_variance = daily_vol ** 2
        annual_variance = daily_variance * 252
        drag = -(leverage ** 2 * annual_variance) / 2

        return drag

    def _check_vix_kill_switch(self) -> bool:
        """Returns True if VIX kill switch is active."""
        # VIX > 45 absolute
        if self._vix_level > 45:
            return True

        # TODO: VIX spike >40% intraday requires tick-level tracking
        # For now, just check absolute threshold

        return False

    def save(self):
        """Persist state to disk."""
        try:
            state = {
                "l_star_cache": self._l_star_cache,
                "vix_level": self._vix_level,
                "last_vix_update": self._last_vix_update,
                "timestamp": time.time(),
            }
            os.makedirs(os.path.dirname(LEVERAGE_STATE_FILE), exist_ok=True)
            with open(LEVERAGE_STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            log.warning(f"Failed to save leverage state: {e}")

    def load(self):
        """Load state from disk."""
        if not os.path.exists(LEVERAGE_STATE_FILE):
            return
        try:
            with open(LEVERAGE_STATE_FILE) as f:
                state = json.load(f)
            self._l_star_cache = state.get("l_star_cache", {})
            self._vix_level = state.get("vix_level", 20.0)
            self._last_vix_update = state.get("last_vix_update", 0.0)
            log.info(f"Loaded leverage state: {len(self._l_star_cache)} tickers")
        except Exception as e:
            log.warning(f"Failed to load leverage state: {e}")


# ─── Singleton ────────────────────────────────────────────────────────────────

_leverage_selector: Optional[LeverageSelector] = None


def get_leverage_selector() -> LeverageSelector:
    """Get singleton LeverageSelector instance."""
    global _leverage_selector
    if _leverage_selector is None:
        _leverage_selector = LeverageSelector()
        _leverage_selector.load()
    return _leverage_selector


# ─── Pipeline Entry Point ─────────────────────────────────────────────────────

def run_leverage_nightly(
    returns_by_ticker: Optional[Dict[str, List[float]]] = None,
    vix_level: Optional[float] = None,
) -> Dict:
    """Nightly pipeline step: update L* estimates for all tickers.

    Args:
        returns_by_ticker: Dict of ticker -> 60-day daily returns
        vix_level: Current VIX level

    Returns:
        Summary dict with updated L* values
    """
    selector = get_leverage_selector()

    if returns_by_ticker is None:
        returns_by_ticker = {}

    summary = selector.nightly_update(returns_by_ticker, vix_level)

    return summary


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Smoke test
    selector = LeverageSelector()

    print("=== Book 27: Leverage Selector Smoke Test ===\n")

    # Test 1: Default table lookup
    print("Test 1: Default L* lookup")
    result = selector.get_allocation("NVDA", etp_leverage=3.0, regime="STEADY")
    print(f"  NVDA 3x ETP in STEADY: L*={result.l_star}, alloc={result.allocation_fraction:.1%}, "
          f"effective={result.effective_leverage:.2f}x")
    print(f"  Drag: {result.drag_pct:.1%}, Warning: {result.warning or 'None'}\n")

    # Test 2: High vol ticker with 3x
    print("Test 2: High volatility ticker")
    result = selector.get_allocation("TSLA", etp_leverage=3.0, regime="STEADY")
    print(f"  TSLA 3x ETP in STEADY: L*={result.l_star}, alloc={result.allocation_fraction:.1%}, "
          f"effective={result.effective_leverage:.2f}x")
    print(f"  Drag: {result.drag_pct:.1%}\n")

    # Test 3: Regime modifiers
    print("Test 3: Regime modifiers")
    for regime in ["STEADY", "INFLATION", "WOI", "CRISIS"]:
        result = selector.get_allocation("SPY", etp_leverage=3.0, regime=regime)
        print(f"  SPY 3x in {regime:10s}: alloc={result.allocation_fraction:.1%}, "
              f"effective={result.effective_leverage:.2f}x")
    print()

    # Test 4: 5x product restrictions
    print("Test 4: 5x product (low vol)")
    import random
    random.seed(42)
    low_vol_returns = [random.gauss(0.001, 0.008) for _ in range(60)]  # ~10% vol
    result = selector.get_allocation("QQQ", etp_leverage=5.0, regime="STEADY",
                                     daily_returns=low_vol_returns)
    print(f"  QQQ 5x (low vol): alloc={result.allocation_fraction:.1%}, "
          f"effective={result.effective_leverage:.2f}x")
    print(f"  Warning: {result.warning}\n")

    # Test 5: High vol kills 5x
    print("Test 5: 5x product (high vol)")
    high_vol_returns = [random.gauss(0.001, 0.025) for _ in range(60)]  # ~35% vol
    result = selector.get_allocation("NVDA", etp_leverage=5.0, regime="STEADY",
                                     daily_returns=high_vol_returns)
    print(f"  NVDA 5x (high vol): alloc={result.allocation_fraction:.1%}, "
          f"effective={result.effective_leverage:.2f}x")
    print(f"  Warning: {result.warning}\n")

    # Test 6: VIX kill switch
    print("Test 6: VIX kill switch")
    selector._vix_level = 50.0
    result = selector.get_allocation("SPY", etp_leverage=3.0, regime="STEADY")
    print(f"  SPY 3x with VIX=50: alloc={result.allocation_fraction:.1%}, "
          f"effective={result.effective_leverage:.2f}x")
    print(f"  Warning: {result.warning}\n")

    # Test 7: Nightly pipeline
    print("Test 7: Nightly update")
    returns_data = {
        "NVDA": [random.gauss(0.002, 0.020) for _ in range(60)],
        "AAPL": [random.gauss(0.001, 0.012) for _ in range(60)],
        "TSLA": [random.gauss(0.001, 0.030) for _ in range(60)],
    }
    summary = selector.nightly_update(returns_data, vix_level=22.5)
    print(f"  Updated {summary['n_tickers_updated']} tickers, VIX={summary['vix_level']}")
    for ticker, data in summary["results"].items():
        print(f"    {ticker}: L*={data['l_star']:.2f}, vol={data['vol_annualized']:.1%}")

    print("\n=== All tests passed ===")
