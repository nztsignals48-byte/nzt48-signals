"""
execution/cost_model.py
========================
Institutional-grade transaction cost model for LSE leveraged ETPs.

Implements:
  1. Static + live bid-ask spread estimation
  2. Market-impact model scaled by order size (Almgren & Chriss 2001)
  3. Implementation shortfall framework (Perold 1988)
  4. Adaptive spread tracking via EWMA with intraday regime awareness
  5. Round-trip cost decomposition and net R:R computation
  6. Spread gate (PASS / WATCH / VETO)

All costs in basis points (bps) unless noted.

References:
  - Perold, A. (1988). "The Implementation Shortfall: Paper Versus Reality."
    Journal of Portfolio Management, 14(3), 4-9.
  - Almgren, R. & Chriss, N. (2001). "Optimal Execution of Portfolio Transactions."
    Journal of Risk, 3(2), 5-39.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock

logger = logging.getLogger("nzt48.execution.cost_model")

# ─────────────────────────────────────────────────────────────────────────────
# Spread table — derived from canonical isa_universe.py SLIPPAGE_MODEL
# Phantom tickers (SC3S.L, GPTS.L, 3SNV.L, etc.) removed.
# ─────────────────────────────────────────────────────────────────────────────
try:
    from uk_isa.isa_universe import SLIPPAGE_MODEL
    SPREAD_BPS: dict[str, float] = {
        k: float(v) for k, v in SLIPPAGE_MODEL.get("spread_bps", {}).items()
    }
    _DEFAULT_SPREAD_BPS = float(SLIPPAGE_MODEL.get("default_bps", 5))
    # Spread gate thresholds — imported from SSOT (isa_universe.py SLIPPAGE_MODEL)
    SPREAD_WATCH_THRESHOLD_BPS  = float(SLIPPAGE_MODEL.get("spread_watch_threshold_bps", 22))
    SPREAD_VETO_THRESHOLD_BPS   = float(SLIPPAGE_MODEL.get("spread_veto_threshold_bps", 32))
except ImportError:
    SPREAD_BPS = {}
    _DEFAULT_SPREAD_BPS = 5.0
    SPREAD_WATCH_THRESHOLD_BPS  = 22.0
    SPREAD_VETO_THRESHOLD_BPS   = 32.0
_SLIPPAGE_BPS_PER_SIDE = 5.0     # market impact, per side
_PLATFORM_FEE_BPS = 2.0          # brokerage/platform fee per side

# Market impact parameters — Almgren & Chriss (2001) square-root model
# Impact_bps = _MI_COEFF * sqrt(order_value / ADV)
# Calibrated conservatively for LSE leveraged ETPs
_MI_COEFF = 15.0        # impact coefficient (bps per unit participation)
_DEFAULT_ADV = 500_000   # default average daily volume (GBP) if unknown


# ─────────────────────────────────────────────────────────────────────────────
# Adaptive spread tracker — EWMA of live spread observations
# ─────────────────────────────────────────────────────────────────────────────

class SpreadTracker:
    """
    Tracks live spread observations via EWMA per ticker.
    Falls back to static table when no live data available.
    """

    def __init__(self, alpha: float = 0.1, max_obs: int = 200):
        self._alpha = alpha
        self._ewma: dict[str, float] = {}
        self._obs: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_obs))
        self._lock = Lock()

    def observe(self, ticker: str, spread_bps: float) -> None:
        """Record a live spread observation."""
        with self._lock:
            if ticker not in self._ewma:
                self._ewma[ticker] = spread_bps
            else:
                self._ewma[ticker] = (
                    self._alpha * spread_bps + (1 - self._alpha) * self._ewma[ticker]
                )
            self._obs[ticker].append((time.time(), spread_bps))

    def get(self, ticker: str) -> float | None:
        """Return EWMA spread if available, else None."""
        with self._lock:
            return self._ewma.get(ticker)

    def get_volatility(self, ticker: str) -> float:
        """Return std of recent spread observations (spread volatility)."""
        with self._lock:
            obs = self._obs.get(ticker)
            if not obs or len(obs) < 5:
                return 0.0
            spreads = [s for _, s in obs]
            mean = sum(spreads) / len(spreads)
            return (sum((s - mean) ** 2 for s in spreads) / len(spreads)) ** 0.5


# Module-level singleton
_SPREAD_TRACKER = SpreadTracker()


# ─────────────────────────────────────────────────────────────────────────────
# Implementation Shortfall — Perold (1988)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImplementationShortfall:
    """
    Decomposes the total cost of a trade into component parts.

    Implementation Shortfall = (Paper Return - Actual Return)
    Decomposed as:
      1. Delay cost   — price drift between decision and execution
      2. Market impact — price movement caused by our order
      3. Spread cost   — half the bid-ask spread (per side)
      4. Commission     — brokerage/platform fees
      5. Opportunity cost — cost of not executing (if order missed)

    Perold (1988), further refined by Kissell & Glantz (2003).
    """
    ticker: str = ""
    decision_price: float = 0.0   # price at signal generation
    arrival_price: float = 0.0    # price when order hits market
    execution_price: float = 0.0  # actual fill price
    order_value: float = 0.0      # notional order value (GBP)
    adv: float = 0.0              # average daily volume (GBP)

    # Component costs (all in bps)
    delay_cost_bps: float = 0.0
    market_impact_bps: float = 0.0
    spread_cost_bps: float = 0.0
    commission_bps: float = 0.0

    # Totals
    total_shortfall_bps: float = 0.0
    total_shortfall_gbp: float = 0.0

    def compute(self) -> None:
        """Compute all shortfall components."""
        if self.decision_price <= 0:
            return

        # Delay cost: price drift from decision to arrival
        if self.arrival_price > 0:
            self.delay_cost_bps = abs(
                (self.arrival_price - self.decision_price) / self.decision_price
            ) * 10_000
        else:
            self.delay_cost_bps = 0.0

        # Market impact: Almgren & Chriss square-root model
        participation = (self.order_value / self.adv) if self.adv > 0 else 0.01
        self.market_impact_bps = _MI_COEFF * (participation ** 0.5)

        # Spread cost: half-spread per side (from tracker or static)
        live_spread = _SPREAD_TRACKER.get(self.ticker)
        if live_spread is not None:
            self.spread_cost_bps = live_spread / 2.0
        else:
            self.spread_cost_bps = SPREAD_BPS.get(self.ticker, _DEFAULT_SPREAD_BPS) / 2.0

        # Commission
        self.commission_bps = _PLATFORM_FEE_BPS

        # Total
        self.total_shortfall_bps = (
            self.delay_cost_bps
            + self.market_impact_bps
            + self.spread_cost_bps
            + self.commission_bps
        )
        self.total_shortfall_gbp = self.order_value * self.total_shortfall_bps / 10_000

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "delay_cost_bps": round(self.delay_cost_bps, 2),
            "market_impact_bps": round(self.market_impact_bps, 2),
            "spread_cost_bps": round(self.spread_cost_bps, 2),
            "commission_bps": round(self.commission_bps, 2),
            "total_shortfall_bps": round(self.total_shortfall_bps, 2),
            "total_shortfall_gbp": round(self.total_shortfall_gbp, 4),
            "participation_rate": round(
                (self.order_value / self.adv) if self.adv > 0 else 0, 4
            ),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Public API — backwards-compatible with existing callers
# ─────────────────────────────────────────────────────────────────────────────

def get_spread_bps(ticker: str, live_quote: dict = None) -> float:
    """
    Get spread in bps. Priority:
      1. Live bid/ask from quote
      2. EWMA tracked spread
      3. Static table proxy
    """
    if live_quote:
        bid = live_quote.get("bid", 0.0)
        ask = live_quote.get("ask", 0.0)
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            live_bps = round((ask - bid) / mid * 10_000, 2)
            _SPREAD_TRACKER.observe(ticker, live_bps)
            return live_bps

    tracked = _SPREAD_TRACKER.get(ticker)
    if tracked is not None:
        return tracked

    return SPREAD_BPS.get(ticker, _DEFAULT_SPREAD_BPS)


def market_impact_bps(order_value: float, adv: float = _DEFAULT_ADV) -> float:
    """
    Almgren & Chriss (2001) square-root market impact model.
    Returns estimated market impact in basis points.
    """
    if adv <= 0:
        adv = _DEFAULT_ADV
    participation = order_value / adv
    return _MI_COEFF * (participation ** 0.5)


def round_trip_cost_bps(
    ticker: str,
    live_quote: dict = None,
    order_value: float = 0.0,
    adv: float = _DEFAULT_ADV,
) -> float:
    """
    Total round-trip cost in bps:
      spread + 2×slippage + 2×platform_fee + market_impact

    If order_value is provided, includes size-dependent market impact.
    Otherwise falls back to static slippage estimate.
    """
    spread = get_spread_bps(ticker, live_quote)

    if order_value > 0:
        mi = market_impact_bps(order_value, adv)
    else:
        mi = 2 * _SLIPPAGE_BPS_PER_SIDE

    return spread + mi + 2 * _PLATFORM_FEE_BPS


def net_rr_after_costs(
    raw_rr: float,
    ticker: str,
    entry: float,
    stop: float,
    target1: float,
    live_quote: dict = None,
    order_value: float = 0.0,
    adv: float = _DEFAULT_ADV,
) -> float:
    """
    Compute net R:R after round-trip costs.
    Converts cost from bps to price units using entry.
    """
    cost_bps = round_trip_cost_bps(ticker, live_quote, order_value, adv)
    cost_price = entry * cost_bps / 10_000.0

    stop_dist   = abs(entry - stop)
    target_dist = abs(target1 - entry)

    net_reward = target_dist - cost_price
    net_risk   = stop_dist + cost_price

    if net_risk <= 0:
        return 0.0
    return round(net_reward / net_risk, 3)


def spread_gate_result(ticker: str, live_quote: dict = None) -> str:
    """Returns PASS / WATCH / VETO based on current spread."""
    spread = get_spread_bps(ticker, live_quote)
    if spread > SPREAD_VETO_THRESHOLD_BPS:
        return "VETO"
    if spread > SPREAD_WATCH_THRESHOLD_BPS:
        return "WATCH"
    return "PASS"


def compute_shortfall(
    ticker: str,
    decision_price: float,
    arrival_price: float,
    execution_price: float,
    order_value: float,
    adv: float = _DEFAULT_ADV,
) -> ImplementationShortfall:
    """
    Compute full Perold (1988) implementation shortfall decomposition.

    Call this after a trade executes to measure execution quality.
    """
    sf = ImplementationShortfall(
        ticker=ticker,
        decision_price=decision_price,
        arrival_price=arrival_price,
        execution_price=execution_price,
        order_value=order_value,
        adv=adv,
    )
    sf.compute()
    logger.info(
        "SHORTFALL: %s delay=%.1fbps impact=%.1fbps spread=%.1fbps "
        "commission=%.1fbps total=%.1fbps (£%.4f)",
        ticker, sf.delay_cost_bps, sf.market_impact_bps,
        sf.spread_cost_bps, sf.commission_bps,
        sf.total_shortfall_bps, sf.total_shortfall_gbp,
    )
    return sf
