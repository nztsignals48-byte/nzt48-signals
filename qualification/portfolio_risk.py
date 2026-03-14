"""
NZT-48 Trading System — Portfolio-Level Risk Decomposition Engine
Transforms a collection of trades into a properly managed portfolio.

9 Core Dimensions:
1. Portfolio Heat Map — risk deployed across sectors, strategies, directions, bots, regimes
2. Sector Concentration Limits — no single dimension dominates
3. Directional Exposure — net long/short as % of equity, regime-gated
4. Beta-Adjusted Exposure — position-weighted portfolio beta with ETP amplification
5. Correlation-Aware Risk — effective number of independent bets
6. Value-at-Risk (VaR) — parametric 95% and 99% confidence estimates
6b. Expected Shortfall (CVaR) — tail-risk measure beyond VaR
7. Max Drawdown Projection — trajectory-based worst-case projection
8. Risk Budget Remaining — daily risk budget tracking and utilization
"""

from __future__ import annotations

import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Signal, Direction, RegimeState

logger = logging.getLogger("nzt48.portfolio_risk")


# ---------------------------------------------------------------------------
# Sector mapping for the 12 Bot B tickers (mirrors portfolio_overseer.py)
# ---------------------------------------------------------------------------
TICKER_SECTOR = {
    # US equities (Bot B universe)
    "NVDA": "semiconductors",
    "AMD": "semiconductors",
    "MU": "memory",
    "SNDK": "memory",
    "AVGO": "semiconductors",
    "MRVL": "semiconductors",
    "ARM": "semiconductors",
    "TSM": "semiconductors",
    "ASML": "semiconductor_equip",
    "SMCI": "ai_infrastructure",
    "VRT": "ai_infrastructure",
    "TSLA": "ev_auto",
    # US ETPs / broad
    "QQQ": "broad_market",
    "SPY": "broad_market",
    "SMH": "semiconductors",
    "SOXX": "semiconductors",
    # ----------------------------------------------------------------
    # UK ISA leveraged ETP universe (.L tickers, T212 ISA)
    # ----------------------------------------------------------------
    # Nasdaq / broad market long
    "QQQ3.L": "nasdaq_beta_long",
    "3LUS.L": "nasdaq_beta_long",
    "QQQ5.L": "nasdaq_beta_long",
    "SP5L.L": "nasdaq_beta_long",
    # Nasdaq / broad market short
    "QQQS.L": "nasdaq_beta_short",
    "3USS.L": "nasdaq_beta_short",
    # Semiconductors
    "3SEM.L": "semiconductors_lev",
    "NVD3.L": "semiconductors_lev",
    "TSM3.L": "semiconductors_lev",
    "MU2.L":  "semiconductors_lev",
    "AMD3.L": "semiconductors_lev",
    "ARM3.L": "semiconductors_lev",
    # AI / GPT
    "GPT3.L": "ai_gpt_lev",
    # EV / auto
    "TSL3.L": "ev_tech_lev",
    # Single-stock short leveraged
    "NVDS.L": "single_short_lev",
    "TSLS.L": "single_short_lev",
    # EU broad
    "3LDE.L": "eu_broad_lev",
    "3LEU.L": "eu_broad_lev",
    # Commodities — leveraged
    "3GOL.L": "commodities_lev",
    "3SIL.L": "commodities_lev",
    "3OIL.L": "commodities_lev",
    "3HCL.L": "commodities_lev",
    # Commodities — physical 1x
    "PHAG.L": "commodities_phys",
    "PHAU.L": "commodities_phys",
}

# ISA factor groups — for concentration risk (max 3 from same group)
ISA_FACTOR_GROUPS = {
    "nasdaq_beta_long":   ["QQQ3.L", "3LUS.L", "QQQ5.L", "SP5L.L"],
    "nasdaq_beta_short":  ["QQQS.L", "3USS.L"],
    "semiconductors_lev": ["3SEM.L", "NVD3.L", "TSM3.L", "MU2.L", "AMD3.L", "ARM3.L"],
    "ev_tech_lev":        ["TSL3.L"],
    "ai_gpt_lev":         ["GPT3.L"],
    "single_short_lev":   ["NVDS.L", "TSLS.L"],
    "eu_broad_lev":       ["3LDE.L", "3LEU.L"],
    "commodities_lev":    ["3GOL.L", "3SIL.L", "3OIL.L", "3HCL.L"],
    "commodities_phys":   ["PHAG.L", "PHAU.L"],
}

# Default betas for common instruments (can be overridden at call site)
DEFAULT_BETAS = {
    # US equities
    "SPY": 1.0,
    "QQQ": 1.2,
    "SMH": 1.4,
    "SOXX": 1.4,
    "NVDA": 1.6,
    "AMD": 1.5,
    "TSM": 1.3,
    "ASML": 1.3,
    "AVGO": 1.2,
    "MRVL": 1.5,
    "ARM": 1.7,
    "MU": 1.4,
    "SNDK": 1.3,
    "SMCI": 2.0,
    "VRT": 1.8,
    "TSLA": 1.6,
    # UK ISA leveraged ETPs — beta = underlying_beta * leverage_factor
    # Underlying betas estimated vs SPX; multiply by leverage for portfolio beta
    "QQQ3.L": 3.6,   # QQQ beta ~1.2 * 3x
    "3LUS.L": 3.6,   # same as QQQ3.L
    "QQQ5.L": 6.0,   # QQQ beta ~1.2 * 5x
    "SP5L.L": 5.0,   # SPX beta 1.0 * 5x
    "QQQS.L": -3.6,  # inverse 3x QQQ
    "3USS.L": -3.6,  # inverse 3x US broad
    "3SEM.L": 4.2,   # SOX beta ~1.4 * 3x
    "GPT3.L": 3.9,   # AI/tech beta ~1.3 * 3x
    "NVD3.L": 5.1,   # NVDA beta ~1.7 * 3x
    "TSL3.L": 4.8,   # TSLA beta ~1.6 * 3x
    "TSM3.L": 3.9,   # TSM beta ~1.3 * 3x
    "MU2.L":  2.8,   # MU beta ~1.4 * 2x
    "AMD3.L": 4.5,   # AMD beta ~1.5 * 3x
    "ARM3.L": 5.1,   # ARM beta ~1.7 * 3x
    "NVDS.L": -5.1,  # inverse 3x NVDA
    "TSLS.L": -4.8,  # inverse 3x TSLA
    "3LDE.L": 2.7,   # DAX beta ~0.9 * 3x
    "3LEU.L": 2.7,   # EuroStoxx beta ~0.9 * 3x
    "3GOL.L": 1.5,   # Gold beta ~0.5 * 3x (low SPX correlation)
    "3SIL.L": 1.8,   # Silver beta ~0.6 * 3x
    "3OIL.L": 1.2,   # Oil beta ~0.4 * 3x
    "3HCL.L": 1.2,   # Brent Oil beta ~0.4 * 3x
    "PHAG.L": 0.3,   # Physical silver beta ~0.3 * 1x (defensive)
    "PHAU.L": 0.1,   # Physical gold beta ~0.1 * 1x (defensive/hedge)
    # Extended equity
    "LLY3.L": 3.6,   # Eli Lilly beta ~1.2 * 3x
    "SPXL.L": 3.0,   # S&P 500 3x
    "SEMI.L": 2.8,   # Semiconductor 2x
    "SOXL.L": 4.2,   # SOX 3x
    "3LTS.L": 4.8,   # Tesla 3x (WisdomTree)
    "3LNV.L": 5.1,   # Nvidia 3x (WisdomTree)
    "MAGS.L": 2.4,   # Magnificent 7 2x
    "AAPS.L": -3.6,  # Apple -3x (inverse)
}

# Regime-specific directional limits (fraction of equity)
DIRECTIONAL_LIMITS = {
    RegimeState.TRENDING_UP_STRONG: 0.80,
    RegimeState.TRENDING_UP_MOD: 0.70,
    RegimeState.TRENDING_DOWN_STRONG: 0.80,   # allows heavy short
    RegimeState.TRENDING_DOWN_MOD: 0.70,
    RegimeState.RANGE_BOUND: 0.40,
    RegimeState.HIGH_VOLATILITY: 0.40,
    RegimeState.RISK_OFF: 0.20,
    RegimeState.SHOCK: 0.00,
}

# Target portfolio betas by regime bucket
TARGET_BETAS = {
    "trending": 0.80,
    "choppy": 0.40,
    "risk_off": 0.00,
}


def _regime_bucket(regime: RegimeState) -> str:
    """Map a RegimeState to a simplified bucket for beta targets."""
    if regime in (RegimeState.TRENDING_UP_STRONG, RegimeState.TRENDING_UP_MOD,
                  RegimeState.TRENDING_DOWN_STRONG, RegimeState.TRENDING_DOWN_MOD):
        return "trending"
    if regime in (RegimeState.RANGE_BOUND, RegimeState.HIGH_VOLATILITY):
        return "choppy"
    return "risk_off"


def get_isa_factor_concentration(tickers: list) -> dict:
    """
    Check ISA factor concentration for a list of tickers.

    Returns a dict per factor group showing how many of the given tickers
    fall into each group, plus a WARN flag if any group has >= 3 picks.

    Used by PDF 3 tomorrow-setups page and position-sizing checks.

    Args:
        tickers: list of ticker strings to check

    Returns:
        {
          "group_counts": {"nasdaq_beta_long": 2, "semiconductors_lev": 3, ...},
          "concentrated_groups": ["semiconductors_lev"],   # groups with >= 3
          "max_group": "semiconductors_lev",
          "max_count": 3,
          "warn": True,
          "warn_msg": "HIGH FACTOR CONCENTRATION: 3 of N picks in semiconductors_lev",
        }
    """
    group_counts: dict = {}
    for group, members in ISA_FACTOR_GROUPS.items():
        overlap = [t for t in tickers if t in members]
        if overlap:
            group_counts[group] = len(overlap)

    concentrated = [g for g, c in group_counts.items() if c >= 3]
    max_group = max(group_counts, key=group_counts.get) if group_counts else ""
    max_count = group_counts.get(max_group, 0)
    warn = bool(concentrated)
    warn_msg = ""
    if warn:
        warn_msg = (
            f"HIGH FACTOR CONCENTRATION: {max_count} of {len(tickers)} picks "
            f"in {max_group} -- consider diversifying or sizing down"
        )

    return {
        "group_counts": group_counts,
        "concentrated_groups": concentrated,
        "max_group": max_group,
        "max_count": max_count,
        "warn": warn,
        "warn_msg": warn_msg,
    }


def get_isa_portfolio_beta(positions: list) -> float:
    """
    Compute weighted portfolio beta for ISA leveraged ETP positions.

    Args:
        positions: list of dicts with keys "ticker" and "weight" (fraction of portfolio)

    Returns:
        float: portfolio-level beta vs SPX (can be very high due to leverage)
    """
    total_beta = 0.0
    for pos in positions:
        ticker = pos.get("ticker", "")
        weight = abs(pos.get("weight", 0.0))
        beta = DEFAULT_BETAS.get(ticker, 1.5)  # default 1.5 for unknown .L tickers
        total_beta += weight * beta
    return round(total_beta, 2)


def _position_value(pos: dict) -> float:
    """Calculate the notional value of a position dict."""
    return abs(pos.get("shares", 0)) * pos.get("current_price", 0)


def _position_risk(pos: dict) -> float:
    """Calculate the risk dollars for a position (distance to stop * shares).

    Falls back to risk_dollars if stop is not available.
    """
    risk_d = pos.get("risk_dollars", 0)
    if risk_d > 0:
        return risk_d
    entry = pos.get("entry", 0)
    stop = pos.get("current_stop", pos.get("stop", 0))
    shares = abs(pos.get("shares", 0))
    if entry > 0 and stop > 0 and shares > 0:
        return abs(entry - stop) * shares
    return 0.0


class PortfolioRiskManager:
    """Portfolio-level risk decomposition engine.

    Sits between individual signal qualification and the portfolio overseer.
    Every method accepts open_positions as a list of dicts with keys:
        ticker, direction, strategy, bot_instance, shares, entry,
        current_price, current_stop/stop, risk_dollars, sector (optional)
    """

    # Concentration limits (fractions)
    MAX_SECTOR_PCT = 0.30
    MAX_TICKER_PCT = 0.15
    MAX_STRATEGY_PCT = 0.40

    # Concentration warning thresholds (trigger warnings before violations)
    WARN_SECTOR_PCT = 0.25
    WARN_TICKER_PCT = 0.12
    WARN_STRATEGY_PCT = 0.35

    def __init__(
        self,
        equity: float = 10_000.0,
        daily_risk_budget_pct: float = 0.03,
    ) -> None:
        """Initialise the portfolio risk manager.

        Args:
            equity: Total account equity in dollars.
            daily_risk_budget_pct: Maximum daily risk as fraction of equity
                (default 3%, matching the immutable max-daily-loss rule).
        """
        self.equity = equity
        self.daily_risk_budget_pct = daily_risk_budget_pct
        self._created_at = datetime.now(timezone.utc)
        logger.info(
            "PortfolioRiskManager initialised: equity=$%.2f, daily_budget=%.1f%%",
            equity, daily_risk_budget_pct * 100,
        )

    # ------------------------------------------------------------------
    # 1. Portfolio Heat Map
    # ------------------------------------------------------------------

    def get_heat_map(self, open_positions: list[dict]) -> dict:
        """Build a multi-dimensional heat map of risk deployment.

        Breaks down total risk by: sector, strategy, direction, bot, and a
        combined summary.  Each bucket shows absolute risk dollars and its
        percentage of total deployed risk.

        Args:
            open_positions: List of position dicts.

        Returns:
            Dict with keys: total_risk, by_sector, by_strategy, by_direction,
            by_bot, alerts.  Each sub-dict maps dimension_value -> {risk, pct}.
        """
        by_sector: dict[str, float] = {}
        by_strategy: dict[str, float] = {}
        by_direction: dict[str, float] = {}
        by_bot: dict[str, float] = {}

        total_risk = 0.0

        for pos in open_positions:
            risk = _position_risk(pos)
            total_risk += risk

            sector = pos.get("sector", TICKER_SECTOR.get(pos.get("ticker", ""), "other"))
            strategy = pos.get("strategy", "unknown")
            direction = pos.get("direction", "LONG")
            bot = pos.get("bot_instance", "unknown")

            by_sector[sector] = by_sector.get(sector, 0) + risk
            by_strategy[strategy] = by_strategy.get(strategy, 0) + risk
            by_direction[direction] = by_direction.get(direction, 0) + risk
            by_bot[bot] = by_bot.get(bot, 0) + risk

        def _to_breakdown(bucket: dict[str, float]) -> dict:
            return {
                k: {
                    "risk": round(v, 2),
                    "pct": round(v / total_risk, 4) if total_risk > 0 else 0.0,
                }
                for k, v in sorted(bucket.items(), key=lambda x: -x[1])
            }

        # Generate alerts for any single dimension exceeding limits
        alerts: list[str] = []
        if total_risk > 0:
            for sector, risk_val in by_sector.items():
                pct = risk_val / total_risk
                if pct > self.MAX_SECTOR_PCT:
                    alerts.append(
                        f"SECTOR_CONCENTRATION: {sector} = {pct:.0%} of risk "
                        f"(limit {self.MAX_SECTOR_PCT:.0%})"
                    )
            for strategy, risk_val in by_strategy.items():
                pct = risk_val / total_risk
                if pct > self.MAX_STRATEGY_PCT:
                    alerts.append(
                        f"STRATEGY_CONCENTRATION: {strategy} = {pct:.0%} of risk "
                        f"(limit {self.MAX_STRATEGY_PCT:.0%})"
                    )

        if alerts:
            for alert in alerts:
                logger.warning("HEAT_MAP: %s", alert)

        return {
            "total_risk": round(total_risk, 2),
            "total_risk_pct_equity": round(total_risk / self.equity, 4) if self.equity > 0 else 0.0,
            "by_sector": _to_breakdown(by_sector),
            "by_strategy": _to_breakdown(by_strategy),
            "by_direction": _to_breakdown(by_direction),
            "by_bot": _to_breakdown(by_bot),
            "position_count": len(open_positions),
            "alerts": alerts,
        }

    # ------------------------------------------------------------------
    # 2. Sector Concentration Limits
    # ------------------------------------------------------------------

    def check_concentration(
        self,
        open_positions: list[dict],
        new_signal: Signal,
    ) -> dict:
        """Check whether a new signal would violate concentration limits.

        Simulates adding the new signal to the existing portfolio and tests:
        - No single sector > 30% of total risk
        - No single ticker > 15% of total risk
        - No single strategy > 40% of total risk

        Args:
            open_positions: Current open position dicts.
            new_signal: The proposed new Signal object.

        Returns:
            Dict with allowed (bool), violations (list), warnings (list).
        """
        violations: list[str] = []
        warnings: list[str] = []

        # Calculate existing risk breakdown
        risk_by_sector: dict[str, float] = {}
        risk_by_ticker: dict[str, float] = {}
        risk_by_strategy: dict[str, float] = {}
        total_risk = 0.0

        for pos in open_positions:
            risk = _position_risk(pos)
            total_risk += risk

            sector = pos.get("sector", TICKER_SECTOR.get(pos.get("ticker", ""), "other"))
            ticker = pos.get("ticker", "")
            strategy = pos.get("strategy", "unknown")

            risk_by_sector[sector] = risk_by_sector.get(sector, 0) + risk
            risk_by_ticker[ticker] = risk_by_ticker.get(ticker, 0) + risk
            risk_by_strategy[strategy] = risk_by_strategy.get(strategy, 0) + risk

        # Add the proposed signal's risk
        new_risk = new_signal.risk_dollars if new_signal.risk_dollars > 0 else (
            abs(new_signal.entry - new_signal.stop) * new_signal.shares
            if new_signal.entry > 0 and new_signal.stop > 0 and new_signal.shares > 0
            else 0.0
        )
        new_sector = TICKER_SECTOR.get(new_signal.ticker, "other")
        new_total = total_risk + new_risk

        if new_total <= 0:
            return {"allowed": True, "violations": [], "warnings": []}

        risk_by_sector[new_sector] = risk_by_sector.get(new_sector, 0) + new_risk
        risk_by_ticker[new_signal.ticker] = risk_by_ticker.get(new_signal.ticker, 0) + new_risk
        risk_by_strategy[new_signal.strategy] = risk_by_strategy.get(new_signal.strategy, 0) + new_risk

        # Check sector limits
        for sector, risk_val in risk_by_sector.items():
            pct = risk_val / new_total
            if pct > self.MAX_SECTOR_PCT:
                violations.append(
                    f"Sector {sector}: {pct:.1%} > {self.MAX_SECTOR_PCT:.0%} limit"
                )
            elif pct > self.WARN_SECTOR_PCT:
                warnings.append(
                    f"Sector {sector}: {pct:.1%} approaching {self.MAX_SECTOR_PCT:.0%} limit"
                )

        # Check ticker limits
        for ticker, risk_val in risk_by_ticker.items():
            pct = risk_val / new_total
            if pct > self.MAX_TICKER_PCT:
                violations.append(
                    f"Ticker {ticker}: {pct:.1%} > {self.MAX_TICKER_PCT:.0%} limit"
                )
            elif pct > self.WARN_TICKER_PCT:
                warnings.append(
                    f"Ticker {ticker}: {pct:.1%} approaching {self.MAX_TICKER_PCT:.0%} limit"
                )

        # Check strategy limits
        for strategy, risk_val in risk_by_strategy.items():
            pct = risk_val / new_total
            if pct > self.MAX_STRATEGY_PCT:
                violations.append(
                    f"Strategy {strategy}: {pct:.1%} > {self.MAX_STRATEGY_PCT:.0%} limit"
                )
            elif pct > self.WARN_STRATEGY_PCT:
                warnings.append(
                    f"Strategy {strategy}: {pct:.1%} approaching {self.MAX_STRATEGY_PCT:.0%} limit"
                )

        allowed = len(violations) == 0
        if not allowed:
            logger.warning(
                "CONCENTRATION BLOCK for %s %s: %s",
                new_signal.direction.value, new_signal.ticker,
                "; ".join(violations),
            )

        return {
            "allowed": allowed,
            "violations": violations,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # 3. Directional Exposure
    # ------------------------------------------------------------------

    def get_directional_exposure(
        self,
        open_positions: list[dict],
        equity: float,
    ) -> dict:
        """Calculate net directional exposure as percentage of equity.

        Args:
            open_positions: Current open position dicts.
            equity: Total account equity.

        Returns:
            Dict with gross_long, gross_short, net_exposure, net_pct,
            gross_pct, and regime-specific limit information.
        """
        gross_long = 0.0
        gross_short = 0.0

        for pos in open_positions:
            value = _position_value(pos)
            direction = pos.get("direction", "LONG")
            if direction == "LONG":
                gross_long += value
            else:
                gross_short += value

        net_exposure = gross_long - gross_short
        gross_exposure = gross_long + gross_short

        net_pct = net_exposure / equity if equity > 0 else 0.0
        gross_pct = gross_exposure / equity if equity > 0 else 0.0
        long_pct = gross_long / equity if equity > 0 else 0.0
        short_pct = gross_short / equity if equity > 0 else 0.0

        return {
            "gross_long": round(gross_long, 2),
            "gross_short": round(gross_short, 2),
            "net_exposure": round(net_exposure, 2),
            "gross_exposure": round(gross_exposure, 2),
            "net_pct": round(net_pct, 4),
            "gross_pct": round(gross_pct, 4),
            "long_pct": round(long_pct, 4),
            "short_pct": round(short_pct, 4),
            "direction_bias": "LONG" if net_pct > 0.05 else ("SHORT" if net_pct < -0.05 else "NEUTRAL"),
        }

    def check_directional_limits(
        self,
        open_positions: list[dict],
        equity: float,
        regime: RegimeState,
    ) -> dict:
        """Check whether current directional exposure is within regime limits.

        Args:
            open_positions: Current open position dicts.
            equity: Total account equity.
            regime: Current market regime state.

        Returns:
            Dict with within_limits (bool), current net_pct, limit, and alert.
        """
        exposure = self.get_directional_exposure(open_positions, equity)
        abs_net_pct = abs(exposure["net_pct"])

        limit = DIRECTIONAL_LIMITS.get(regime, 0.40)

        within_limits = abs_net_pct <= limit
        alert = ""
        if not within_limits:
            alert = (
                f"Directional exposure {abs_net_pct:.1%} exceeds "
                f"{regime.value} limit of {limit:.0%}"
            )
            logger.warning("DIRECTIONAL: %s", alert)

        return {
            "within_limits": within_limits,
            "net_pct": exposure["net_pct"],
            "abs_net_pct": round(abs_net_pct, 4),
            "limit": limit,
            "regime": regime.value,
            "alert": alert,
        }

    # ------------------------------------------------------------------
    # 4. Beta-Adjusted Exposure
    # ------------------------------------------------------------------

    def get_beta_exposure(
        self,
        open_positions: list[dict],
        betas: Optional[dict[str, float]] = None,
    ) -> dict:
        """Calculate portfolio beta as the position-weighted sum of individual betas.

        Each position contributes: beta * position_notional_value.
        ETPs with leverage have amplified beta (e.g. 3x ETP has beta ~3.0).

        Args:
            open_positions: Current open position dicts.
            betas: Dict mapping ticker -> beta. Falls back to DEFAULT_BETAS.

        Returns:
            Dict with portfolio_beta, target_beta_by_regime, contributions
            (per-position beta contribution), and total notional.
        """
        effective_betas = dict(DEFAULT_BETAS)
        if betas:
            effective_betas.update(betas)

        total_notional = 0.0
        weighted_beta = 0.0
        contributions: list[dict] = []

        for pos in open_positions:
            ticker = pos.get("ticker", "")
            value = _position_value(pos)
            direction = pos.get("direction", "LONG")

            # Leverage multiplier for ETPs (detected via ticker suffix or explicit field)
            leverage = pos.get("leverage", 1)
            if leverage == 1 and ticker.endswith(".L"):
                # Heuristic: London-listed ETPs — check for known 3x/5x
                if any(pfx in ticker for pfx in ("3", "5")):
                    leverage = 3 if "3" in ticker.split(".")[0][-2:] else 5

            raw_beta = effective_betas.get(ticker, 1.0)
            effective_beta = raw_beta * leverage

            # Short positions contribute negative beta
            sign = 1.0 if direction == "LONG" else -1.0
            contribution = sign * effective_beta * value

            total_notional += value
            weighted_beta += contribution

            contributions.append({
                "ticker": ticker,
                "direction": direction,
                "notional": round(value, 2),
                "raw_beta": raw_beta,
                "leverage": leverage,
                "effective_beta": round(effective_beta, 2),
                "beta_contribution": round(contribution, 2),
            })

        portfolio_beta = weighted_beta / total_notional if total_notional > 0 else 0.0

        return {
            "portfolio_beta": round(portfolio_beta, 4),
            "weighted_beta_dollars": round(weighted_beta, 2),
            "total_notional": round(total_notional, 2),
            "target_betas": TARGET_BETAS,
            "contributions": contributions,
        }

    # ------------------------------------------------------------------
    # 5. Correlation-Aware Risk
    # ------------------------------------------------------------------

    def get_effective_positions(
        self,
        open_positions: list[dict],
        correlation_matrix: Optional[dict[str, dict[str, float]]] = None,
    ) -> dict:
        """Calculate the effective number of independent bets.

        Two 100% correlated positions are NOT 2 independent bets.
        Effective N = N / (1 + (N-1) * avg_correlation)

        Args:
            open_positions: Current open position dicts.
            correlation_matrix: Optional nested dict of ticker -> ticker -> corr.
                If None, uses a default 0.5 avg correlation for same-sector
                tickers and 0.2 for cross-sector.

        Returns:
            Dict with actual_positions, effective_positions,
            avg_correlation, diversification_ratio.
        """
        n = len(open_positions)
        if n <= 1:
            return {
                "actual_positions": n,
                "effective_positions": n,
                "avg_correlation": 0.0,
                "diversification_ratio": 1.0,
            }

        # Compute average pairwise correlation
        total_corr = 0.0
        pair_count = 0

        tickers = [pos.get("ticker", "") for pos in open_positions]
        sectors = [
            pos.get("sector", TICKER_SECTOR.get(pos.get("ticker", ""), "other"))
            for pos in open_positions
        ]

        for i in range(n):
            for j in range(i + 1, n):
                if correlation_matrix:
                    corr = (
                        correlation_matrix
                        .get(tickers[i], {})
                        .get(tickers[j], None)
                    )
                    if corr is None:
                        # Try reverse lookup
                        corr = (
                            correlation_matrix
                            .get(tickers[j], {})
                            .get(tickers[i], None)
                        )
                    if corr is None:
                        # Default based on sector similarity
                        corr = 0.5 if sectors[i] == sectors[j] else 0.2
                else:
                    # Default: same sector = 0.5, cross-sector = 0.2
                    corr = 0.5 if sectors[i] == sectors[j] else 0.2

                total_corr += corr
                pair_count += 1

        avg_corr = total_corr / pair_count if pair_count > 0 else 0.0

        # Effective number of independent positions
        denominator = 1.0 + (n - 1) * avg_corr
        effective_n = n / denominator if denominator > 0 else float(n)

        diversification_ratio = effective_n / n if n > 0 else 1.0

        return {
            "actual_positions": n,
            "effective_positions": round(effective_n, 2),
            "avg_correlation": round(avg_corr, 4),
            "diversification_ratio": round(diversification_ratio, 4),
        }

    # ------------------------------------------------------------------
    # 6. Value-at-Risk (VaR)
    # ------------------------------------------------------------------

    def estimate_var(
        self,
        open_positions: list[dict],
        equity: float,
        lookback_vol: float,
    ) -> dict:
        """Estimate parametric Value-at-Risk at 95% and 99% confidence.

        Uses the simple formula: VaR = portfolio_value * portfolio_vol * z_score

        The lookback_vol is the annualised portfolio volatility (e.g. 0.25 for
        25% annual vol).  It is converted to a 1-day figure internally using
        sqrt(252).

        Args:
            open_positions: Current open position dicts.
            equity: Total account equity.
            lookback_vol: Annualised portfolio volatility as a decimal.

        Returns:
            Dict with var_95, var_99 (dollar amounts) and var_95_pct, var_99_pct
            (as fraction of equity).
        """
        # Z-scores for confidence levels
        Z_95 = 1.6449
        Z_99 = 2.3263

        total_notional = sum(_position_value(pos) for pos in open_positions)

        # Convert annual vol to 1-day vol
        daily_vol = lookback_vol / math.sqrt(252) if lookback_vol > 0 else 0.0

        var_95 = total_notional * daily_vol * Z_95
        var_99 = total_notional * daily_vol * Z_99

        var_95_pct = var_95 / equity if equity > 0 else 0.0
        var_99_pct = var_99 / equity if equity > 0 else 0.0

        logger.debug(
            "VaR: notional=$%.0f, daily_vol=%.4f, VaR95=$%.2f (%.2f%%), VaR99=$%.2f (%.2f%%)",
            total_notional, daily_vol, var_95, var_95_pct * 100, var_99, var_99_pct * 100,
        )

        return {
            "var_95": round(var_95, 2),
            "var_99": round(var_99, 2),
            "var_95_pct": round(var_95_pct, 4),
            "var_99_pct": round(var_99_pct, 4),
            "total_notional": round(total_notional, 2),
            "daily_vol": round(daily_vol, 6),
            "annual_vol": round(lookback_vol, 4),
        }

    # ------------------------------------------------------------------
    # 6b. Expected Shortfall (CVaR) — Historical Simulation
    # ------------------------------------------------------------------

    @staticmethod
    def compute_expected_shortfall(
        returns: list[float],
        confidence: float = 0.95,
    ) -> float:
        """Compute Expected Shortfall (CVaR) via historical simulation.

        Sorts returns ascending, identifies the worst (1 - confidence) %
        of observations, and returns the average of those tail losses
        as a positive number (i.e. the magnitude of the expected loss
        in the tail).

        Args:
            returns: Historical return series (e.g. daily P&L as
                fraction of equity: -0.02 means a 2% loss).
            confidence: Confidence level (default 0.95 = 95%).

        Returns:
            Expected Shortfall as a positive float.  Returns 0.0 if the
            input is empty or has no tail observations.
        """
        if not returns:
            return 0.0

        sorted_returns = sorted(returns)  # ascending (worst first)
        cutoff_index = max(1, int(len(sorted_returns) * (1.0 - confidence)))
        tail = sorted_returns[:cutoff_index]

        if not tail:
            return 0.0

        # Average of tail losses, returned as positive magnitude
        return abs(sum(tail) / len(tail))

    @staticmethod
    def compute_tail_index(var_value: float, es_value: float) -> float:
        """Compute the tail index as the ES / VaR ratio.

        A ratio > 1.5 indicates fat tails — the average loss in the
        tail is significantly worse than the threshold loss (VaR).

        For a normal distribution the ratio is approximately 1.3;
        anything above 1.5 signals heavy tails that parametric VaR
        understates.

        Args:
            var_value: Value-at-Risk (positive magnitude).
            es_value: Expected Shortfall / CVaR (positive magnitude).

        Returns:
            ES / VaR ratio.  Returns 0.0 if VaR is zero.
        """
        if var_value <= 0:
            return 0.0
        return es_value / var_value

    # ------------------------------------------------------------------
    # 7. Max Drawdown Projection
    # ------------------------------------------------------------------

    def project_drawdown(self, equity_history: list[float]) -> dict:
        """Project worst-case drawdown from the equity curve.

        Computes historical max drawdown, current drawdown from peak, and
        generates warning/critical alerts based on trajectory.

        Rules:
        - current_dd > 50% of historical max_dd -> WARNING
        - current_dd > 75% of historical max_dd -> CRITICAL

        Args:
            equity_history: Chronological list of equity values (e.g. daily
                closing equity).

        Returns:
            Dict with max_drawdown, max_drawdown_pct, current_drawdown,
            current_drawdown_pct, peak_equity, trough_equity, alert_level.
        """
        if not equity_history or len(equity_history) < 2:
            return {
                "max_drawdown": 0.0,
                "max_drawdown_pct": 0.0,
                "current_drawdown": 0.0,
                "current_drawdown_pct": 0.0,
                "peak_equity": equity_history[0] if equity_history else 0.0,
                "trough_equity": equity_history[0] if equity_history else 0.0,
                "alert_level": "NONE",
                "alert_message": "",
            }

        # Walk the equity curve to find max drawdown and current drawdown
        peak = equity_history[0]
        max_dd = 0.0
        max_dd_pct = 0.0
        peak_at_max_dd = peak
        trough_at_max_dd = peak

        for eq in equity_history:
            if eq > peak:
                peak = eq
            dd = peak - eq
            dd_pct = dd / peak if peak > 0 else 0.0
            if dd_pct > max_dd_pct:
                max_dd = dd
                max_dd_pct = dd_pct
                peak_at_max_dd = peak
                trough_at_max_dd = eq

        # Current drawdown = from most recent peak to last equity value
        recent_peak = max(equity_history)
        current_eq = equity_history[-1]
        current_dd = recent_peak - current_eq
        current_dd_pct = current_dd / recent_peak if recent_peak > 0 else 0.0

        # Alert classification
        alert_level = "NONE"
        alert_message = ""

        if max_dd_pct > 0:
            ratio = current_dd_pct / max_dd_pct
            if ratio > 0.75:
                alert_level = "CRITICAL"
                alert_message = (
                    f"Current drawdown {current_dd_pct:.1%} is {ratio:.0%} of "
                    f"historical max {max_dd_pct:.1%}. CRITICAL — consider halting."
                )
                logger.critical("DRAWDOWN PROJECTION: %s", alert_message)
            elif ratio > 0.50:
                alert_level = "WARNING"
                alert_message = (
                    f"Current drawdown {current_dd_pct:.1%} is {ratio:.0%} of "
                    f"historical max {max_dd_pct:.1%}. Reduce exposure."
                )
                logger.warning("DRAWDOWN PROJECTION: %s", alert_message)

        return {
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 4),
            "current_drawdown": round(current_dd, 2),
            "current_drawdown_pct": round(current_dd_pct, 4),
            "peak_equity": round(peak_at_max_dd, 2),
            "trough_equity": round(trough_at_max_dd, 2),
            "recent_peak": round(recent_peak, 2),
            "current_equity": round(current_eq, 2),
            "alert_level": alert_level,
            "alert_message": alert_message,
        }

    # ------------------------------------------------------------------
    # 8. Risk Budget Remaining
    # ------------------------------------------------------------------

    def get_risk_budget(
        self,
        daily_pnl: float,
        equity: float,
        open_risk: float,
    ) -> dict:
        """Calculate remaining daily risk budget.

        Budget = daily_risk_budget_pct * equity.
        Used  = |daily_pnl| (realised losses) + open_risk (unrealised at-risk).

        Args:
            daily_pnl: Today's realised P&L (negative = losses eating budget).
            equity: Current account equity.
            open_risk: Sum of risk_dollars for all open positions (from stops).

        Returns:
            Dict with budget_total, budget_used, budget_remaining,
            utilization_pct.
        """
        budget_total = self.daily_risk_budget_pct * equity
        # Realised losses consume budget; gains do NOT restore it (asymmetric)
        realised_consumed = abs(daily_pnl) if daily_pnl < 0 else 0.0
        budget_used = realised_consumed + open_risk
        budget_remaining = max(0.0, budget_total - budget_used)
        utilization_pct = (budget_used / budget_total * 100) if budget_total > 0 else 0.0

        if utilization_pct > 90:
            logger.warning(
                "RISK BUDGET: %.1f%% utilised ($%.2f of $%.2f). Near limit.",
                utilization_pct, budget_used, budget_total,
            )
        elif utilization_pct > 75:
            logger.info(
                "RISK BUDGET: %.1f%% utilised ($%.2f of $%.2f).",
                utilization_pct, budget_used, budget_total,
            )

        return {
            "budget_total": round(budget_total, 2),
            "budget_used": round(budget_used, 2),
            "budget_remaining": round(budget_remaining, 2),
            "utilization_pct": round(utilization_pct, 2),
            "realised_loss": round(realised_consumed, 2),
            "open_risk": round(open_risk, 2),
        }

    # ------------------------------------------------------------------
    # 9. Monte Carlo Stress Testing (Research Enhancement — Sprint 2)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_stressed_correlation_matrix(
        normal_corr: dict[str, dict[str, float]],
        stress_multiplier: float = 1.5,
    ) -> dict[str, dict[str, float]]:
        """Amplify a correlation matrix toward 1.0 for stress-testing.

        During crises, correlations spike ("go to 1").  This method
        produces a stressed version by blending the normal correlations
        toward 1.0.

        Formula per pair:
            stressed = normal + stress_multiplier * (1.0 - abs(normal)) * 0.3
            capped at 0.99, preserving sign.

        Args:
            normal_corr: Nested dict  ticker -> ticker -> correlation.
            stress_multiplier: Controls severity (1.0 = mild, 2.0 = severe).

        Returns:
            New nested dict with stressed correlations.
        """
        stressed: dict[str, dict[str, float]] = {}
        try:
            for t1, inner in normal_corr.items():
                stressed[t1] = {}
                for t2, corr in inner.items():
                    sign = 1.0 if corr >= 0 else -1.0
                    magnitude = abs(corr)
                    shift = stress_multiplier * (1.0 - magnitude) * 0.3
                    new_mag = min(0.99, magnitude + shift)
                    stressed[t1][t2] = round(sign * new_mag, 6)
        except Exception:
            logger.exception("compute_stressed_correlation_matrix failed")
            return normal_corr
        return stressed

    # Pre-defined stress scenarios
    STRESS_SCENARIOS: list[dict] = [
        {"name": "COVID_CRASH",      "spy_change": -0.34, "vix": 82, "correlation": 0.90},
        {"name": "2022_BEAR",        "spy_change": -0.27, "vix": 36, "correlation": 0.70},
        {"name": "FLASH_CRASH",      "spy_change": -0.10, "vix": 45, "correlation": 0.85},
        {"name": "SECTOR_ROTATION",  "spy_change": -0.05, "vix": 25, "correlation": 0.40},
        {"name": "BLACK_SWAN",       "spy_change": -0.50, "vix": 90, "correlation": 0.95},
    ]

    def run_stress_scenarios(
        self,
        open_positions: list[dict],
        scenarios: Optional[list[dict]] = None,
        max_drawdown_limit: float = 0.20,
    ) -> list[dict]:
        """Run defined stress scenarios against the current portfolio.

        For each scenario, estimate the portfolio loss based on position
        betas, the hypothetical SPY move, and a correlation adjustment.

        Args:
            open_positions: Current open position dicts.
            scenarios: Optional list of scenario dicts, each containing
                ``name``, ``spy_change``, ``vix``, ``correlation``.
                Falls back to ``STRESS_SCENARIOS``.
            max_drawdown_limit: The loss threshold that determines whether
                the portfolio "survives" a scenario (default 20%).

        Returns:
            List of result dicts, one per scenario, with keys:
            scenario_name, portfolio_loss_pct, portfolio_loss_dollars,
            worst_position, survives.
        """
        scenarios = scenarios or self.STRESS_SCENARIOS
        results: list[dict] = []

        try:
            equity = self.equity
            if equity <= 0:
                return results

            for scenario in scenarios:
                name = scenario.get("name", "UNKNOWN")
                spy_change = scenario.get("spy_change", -0.10)
                corr = scenario.get("correlation", 0.70)

                total_loss = 0.0
                worst_ticker = ""
                worst_loss = 0.0

                for pos in open_positions:
                    ticker = pos.get("ticker", "")
                    value = _position_value(pos)
                    direction = pos.get("direction", "LONG")
                    beta = DEFAULT_BETAS.get(ticker, 1.0)
                    leverage = pos.get("leverage", 1)
                    effective_beta = beta * leverage

                    # Direction sign: long loses on market decline
                    sign = 1.0 if direction == "LONG" else -1.0

                    # Position loss = value * beta * spy_change * (1 + corr_adj)
                    # corr_adj amplifies the move when correlation is high
                    corr_adj = (corr - 0.5) * 0.5  # 0.9 corr → +0.2 amplification
                    pos_loss = value * effective_beta * spy_change * (1.0 + corr_adj) * sign

                    total_loss += pos_loss

                    if abs(pos_loss) > abs(worst_loss):
                        worst_loss = pos_loss
                        worst_ticker = ticker

                loss_pct = total_loss / equity if equity > 0 else 0.0

                results.append({
                    "scenario_name": name,
                    "portfolio_loss_pct": round(loss_pct, 4),
                    "portfolio_loss_dollars": round(total_loss, 2),
                    "worst_position": worst_ticker,
                    "worst_position_loss": round(worst_loss, 2),
                    "survives": abs(loss_pct) < max_drawdown_limit,
                })

                if abs(loss_pct) >= max_drawdown_limit:
                    logger.warning(
                        "STRESS_TEST %s: portfolio loss %.1f%% exceeds "
                        "%.0f%% limit — DOES NOT SURVIVE",
                        name, loss_pct * 100, max_drawdown_limit * 100,
                    )

        except Exception:
            logger.exception("run_stress_scenarios failed")

        return results

    def reverse_stress_test(
        self,
        open_positions: list[dict],
        max_loss_pct: float = 0.20,
    ) -> dict:
        """Find the minimum market decline that causes a given drawdown.

        Uses binary search between 0% and 50% SPY decline with
        correlation = 0.85 (crisis-level).

        Args:
            open_positions: Current open position dicts.
            max_loss_pct: The loss threshold to trigger (default 20%).

        Returns:
            Dict with required_spy_decline, is_plausible (< 30%),
            risk_assessment.
        """
        try:
            equity = self.equity
            if equity <= 0 or not open_positions:
                return {
                    "required_spy_decline": 0.0,
                    "is_plausible": False,
                    "risk_assessment": "NO_POSITIONS",
                }

            lo, hi = 0.0, 0.50
            crisis_corr = 0.85

            for _ in range(30):  # binary search iterations
                mid = (lo + hi) / 2.0
                scenario = [{
                    "name": "REVERSE_TEST",
                    "spy_change": -mid,
                    "vix": 40,
                    "correlation": crisis_corr,
                }]
                result = self.run_stress_scenarios(
                    open_positions, scenario, max_loss_pct
                )
                if result and abs(result[0]["portfolio_loss_pct"]) >= max_loss_pct:
                    hi = mid
                else:
                    lo = mid

            required_decline = round((lo + hi) / 2.0, 4)
            is_plausible = required_decline < 0.30

            if is_plausible:
                assessment = (
                    f"A {required_decline:.1%} SPY decline (plausible) would cause "
                    f"a {max_loss_pct:.0%} portfolio loss. Consider reducing exposure."
                )
                logger.warning("REVERSE STRESS: %s", assessment)
            else:
                assessment = (
                    f"A {required_decline:.1%} SPY decline (unlikely) required for "
                    f"{max_loss_pct:.0%} portfolio loss. Portfolio is resilient."
                )

            return {
                "required_spy_decline": required_decline,
                "is_plausible": is_plausible,
                "risk_assessment": assessment,
            }

        except Exception:
            logger.exception("reverse_stress_test failed")
            return {
                "required_spy_decline": 0.0,
                "is_plausible": False,
                "risk_assessment": "ERROR",
            }

    # ------------------------------------------------------------------
    # Composite Methods
    # ------------------------------------------------------------------

    def get_full_risk_report(
        self,
        open_positions: list[dict],
        equity: float,
        regime: RegimeState,
        daily_pnl: float,
        correlation_matrix: Optional[dict[str, dict[str, float]]] = None,
        betas: Optional[dict[str, float]] = None,
        equity_history: Optional[list[float]] = None,
        lookback_vol: float = 0.20,
        return_history: Optional[list[float]] = None,
    ) -> dict:
        """Generate a comprehensive portfolio risk report combining all 9 dimensions.

        Args:
            open_positions: Current open position dicts.
            equity: Total account equity.
            regime: Current market regime state.
            daily_pnl: Today's realised P&L.
            correlation_matrix: Optional ticker-pair correlation data.
            betas: Optional ticker -> beta overrides.
            equity_history: Historical equity curve for drawdown projection.
            lookback_vol: Annualised portfolio volatility (default 20%).
            return_history: Historical daily returns as fractions of equity
                (e.g. -0.02 = 2% loss day). Used for Expected Shortfall.

        Returns:
            Comprehensive dict with all risk dimensions and an overall
            risk_score (0-100, higher = more risk deployed).
        """
        self.equity = equity

        # Calculate open risk for budget
        open_risk = sum(_position_risk(pos) for pos in open_positions)

        # 1. Heat map
        heat_map = self.get_heat_map(open_positions)

        # 2. Directional exposure
        directional = self.get_directional_exposure(open_positions, equity)
        directional_check = self.check_directional_limits(open_positions, equity, regime)

        # 3. Beta exposure
        beta_exposure = self.get_beta_exposure(open_positions, betas)

        # 4. Correlation / effective positions
        effective_pos = self.get_effective_positions(
            open_positions, correlation_matrix
        )

        # 5. VaR
        var_estimate = self.estimate_var(open_positions, equity, lookback_vol)

        # 5b. Expected Shortfall (CVaR) — historical simulation
        es_95 = self.compute_expected_shortfall(return_history or [], 0.95)
        es_99 = self.compute_expected_shortfall(return_history or [], 0.99)
        tail_index_95 = self.compute_tail_index(
            var_estimate["var_95_pct"], es_95,
        )
        # Flag: if ES at 95% > 3% of portfolio, recommend 30-50% size reduction
        es_reduce_position_sizes = es_95 > 0.03
        es_report = {
            "es_95": round(es_95, 6),
            "es_99": round(es_99, 6),
            "es_95_dollars": round(es_95 * equity, 2),
            "es_99_dollars": round(es_99 * equity, 2),
            "tail_index_95": round(tail_index_95, 4),
            "fat_tails_detected": tail_index_95 > 1.5,
            "reduce_position_sizes": es_reduce_position_sizes,
            "reduction_range": "30-50%" if es_reduce_position_sizes else "none",
            "return_history_length": len(return_history or []),
        }
        if es_reduce_position_sizes:
            logger.warning(
                "ES_ALERT: ES(95%%)=%.4f > 3%% threshold — "
                "recommend reducing position sizes by 30-50%%",
                es_95,
            )

        # 6. Drawdown projection
        drawdown = self.project_drawdown(equity_history or [])

        # 7. Risk budget
        risk_budget = self.get_risk_budget(daily_pnl, equity, open_risk)

        # --- Composite risk score (0-100) ---
        # Higher = more risk currently deployed.  Used for quick assessment.
        risk_score = self._compute_risk_score(
            heat_map=heat_map,
            directional_check=directional_check,
            beta_exposure=beta_exposure,
            effective_pos=effective_pos,
            var_estimate=var_estimate,
            drawdown=drawdown,
            risk_budget=risk_budget,
        )

        # Collect all alerts
        all_alerts: list[str] = list(heat_map.get("alerts", []))
        if directional_check.get("alert"):
            all_alerts.append(directional_check["alert"])
        if drawdown.get("alert_message"):
            all_alerts.append(drawdown["alert_message"])
        if risk_budget["utilization_pct"] > 90:
            all_alerts.append(
                f"Risk budget {risk_budget['utilization_pct']:.0f}% utilised"
            )
        if es_reduce_position_sizes:
            all_alerts.append(
                f"ES(95%)={es_95:.4f} exceeds 3% threshold — "
                f"reduce position sizes by 30-50%"
            )
        if es_report["fat_tails_detected"]:
            all_alerts.append(
                f"Fat tails detected: tail_index={tail_index_95:.2f} (>1.5)"
            )

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "equity": equity,
            "regime": regime.value,
            "risk_score": risk_score,
            "heat_map": heat_map,
            "directional": directional,
            "directional_limits": directional_check,
            "beta_exposure": beta_exposure,
            "effective_positions": effective_pos,
            "var": var_estimate,
            "expected_shortfall": es_report,
            "drawdown": drawdown,
            "risk_budget": risk_budget,
            "alerts": all_alerts,
            "alert_count": len(all_alerts),
        }

        logger.info(
            "FULL RISK REPORT: score=%d, positions=%d (effective=%.1f), "
            "VaR95=$%.0f, budget=%.0f%% used, alerts=%d",
            risk_score,
            len(open_positions),
            effective_pos["effective_positions"],
            var_estimate["var_95"],
            risk_budget["utilization_pct"],
            len(all_alerts),
        )

        return report

    def should_allow_new_trade(
        self,
        open_positions: list[dict],
        new_signal: Signal,
        equity: float,
        regime: RegimeState,
    ) -> dict:
        """Determine whether a new trade should be allowed given portfolio state.

        Runs concentration checks, directional limits, and risk budget analysis.
        Returns a clear allow/deny with reasons and an overall risk score.

        Args:
            open_positions: Current open position dicts.
            new_signal: The proposed Signal object.
            equity: Total account equity.
            regime: Current market regime state.

        Returns:
            Dict with allowed (bool), reasons (list of strings explaining
            deny/allow decisions), risk_score (0-100).
        """
        self.equity = equity
        reasons: list[str] = []
        blocked = False

        # 1. Concentration check
        conc = self.check_concentration(open_positions, new_signal)
        if not conc["allowed"]:
            blocked = True
            reasons.extend([f"CONCENTRATION: {v}" for v in conc["violations"]])
        reasons.extend([f"WARN: {w}" for w in conc.get("warnings", [])])

        # 2. Directional limit check — simulate adding the new position
        simulated_positions = list(open_positions)
        sim_pos = {
            "ticker": new_signal.ticker,
            "direction": new_signal.direction.value,
            "shares": new_signal.shares,
            "entry": new_signal.entry,
            "current_price": new_signal.entry,
            "current_stop": new_signal.stop,
            "stop": new_signal.stop,
            "risk_dollars": new_signal.risk_dollars,
            "strategy": new_signal.strategy,
            "bot_instance": new_signal.bot_instance.value if hasattr(new_signal.bot_instance, 'value') else str(new_signal.bot_instance),
        }
        simulated_positions.append(sim_pos)

        dir_check = self.check_directional_limits(simulated_positions, equity, regime)
        if not dir_check["within_limits"]:
            blocked = True
            reasons.append(f"DIRECTIONAL: {dir_check['alert']}")

        # 3. Risk budget check
        open_risk = sum(_position_risk(pos) for pos in open_positions)
        new_risk = new_signal.risk_dollars if new_signal.risk_dollars > 0 else (
            abs(new_signal.entry - new_signal.stop) * new_signal.shares
            if new_signal.entry > 0 and new_signal.stop > 0 and new_signal.shares > 0
            else 0.0
        )
        budget_total = self.daily_risk_budget_pct * equity
        projected_used = open_risk + new_risk
        if projected_used > budget_total:
            blocked = True
            reasons.append(
                f"RISK_BUDGET: Adding ${new_risk:.0f} would use "
                f"${projected_used:.0f}/${budget_total:.0f} "
                f"({projected_used / budget_total * 100:.0f}%)"
            )

        # 4. SHOCK regime = no new trades
        if regime == RegimeState.SHOCK:
            blocked = True
            reasons.append("REGIME: SHOCK — all new entries blocked")

        # Compute risk score for the projected portfolio
        heat_map = self.get_heat_map(simulated_positions)
        risk_score = min(100, int(
            heat_map["total_risk_pct_equity"] * 100
            + abs(dir_check.get("net_pct", 0)) * 50
            + len(conc.get("violations", [])) * 20
            + len(conc.get("warnings", [])) * 5
        ))

        if not blocked:
            reasons.append("All portfolio risk checks passed")

        logger.info(
            "TRADE GATE: %s %s %s — allowed=%s, score=%d, reasons=%d",
            new_signal.direction.value,
            new_signal.ticker,
            new_signal.strategy,
            not blocked,
            risk_score,
            len(reasons),
        )

        return {
            "allowed": not blocked,
            "reasons": reasons,
            "risk_score": risk_score,
        }

    def get_status(self) -> dict:
        """Return current configuration and status of the risk manager.

        Returns:
            Dict with equity, budget_pct, concentration limits, and
            creation timestamp.
        """
        return {
            "equity": self.equity,
            "daily_risk_budget_pct": self.daily_risk_budget_pct,
            "daily_risk_budget_dollars": round(self.daily_risk_budget_pct * self.equity, 2),
            "max_sector_pct": self.MAX_SECTOR_PCT,
            "max_ticker_pct": self.MAX_TICKER_PCT,
            "max_strategy_pct": self.MAX_STRATEGY_PCT,
            "directional_limits": {k.value: v for k, v in DIRECTIONAL_LIMITS.items()},
            "target_betas": TARGET_BETAS,
            "created_at": self._created_at.isoformat(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_risk_score(
        self,
        heat_map: dict,
        directional_check: dict,
        beta_exposure: dict,
        effective_pos: dict,
        var_estimate: dict,
        drawdown: dict,
        risk_budget: dict,
    ) -> int:
        """Compute a composite risk score from 0 (no risk) to 100 (max risk).

        Weighting:
        - Risk budget utilization: 30% weight
        - VaR as % of equity:     20% weight
        - Directional tilt:       15% weight
        - Beta deviation:         10% weight
        - Diversification:        10% weight (inverted — low diversification = high risk)
        - Drawdown trajectory:    15% weight
        """
        score = 0.0

        # Budget utilization (0-100 already)
        budget_component = min(100, risk_budget.get("utilization_pct", 0))
        score += budget_component * 0.30

        # VaR95 as pct of equity (0-10% mapped to 0-100)
        var_pct = var_estimate.get("var_95_pct", 0)
        var_component = min(100, var_pct * 1000)  # 10% VaR = score 100
        score += var_component * 0.20

        # Directional tilt (0-100% mapped to 0-100)
        abs_net = abs(directional_check.get("net_pct", 0))
        dir_component = min(100, abs_net * 100)
        score += dir_component * 0.15

        # Beta deviation from target (0-2.0 deviation mapped to 0-100)
        portfolio_beta = abs(beta_exposure.get("portfolio_beta", 0))
        beta_component = min(100, portfolio_beta * 50)
        score += beta_component * 0.10

        # Diversification (inverted: 1.0 ratio = 0 risk, 0.0 ratio = 100 risk)
        div_ratio = effective_pos.get("diversification_ratio", 1.0)
        div_component = (1.0 - div_ratio) * 100
        score += div_component * 0.10

        # Drawdown trajectory
        dd_pct = drawdown.get("current_drawdown_pct", 0)
        dd_component = min(100, dd_pct * 1000)  # 10% DD = score 100
        score += dd_component * 0.15

        return min(100, max(0, int(round(score))))
