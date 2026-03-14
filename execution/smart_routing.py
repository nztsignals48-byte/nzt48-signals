"""
NZT-48 Trading System — Smart Order Routing & Liquidity Awareness
Institutional-grade execution intelligence: liquidity scoring, position size
capping, slippage prediction, optimal timing, market impact estimation,
and ETP-specific risk assessment.

Prevents getting destroyed by slippage and illiquidity — the silent killers
that turn a profitable strategy into a losing one.

Liquidity Score (0-100):
    ADV weight 40% | Spread weight 40% | Market cap weight 20%

Position Limits:
    Never exceed 1% of ADV (0.5% for illiquid, 2% for high-liquidity ETPs)

Slippage Model:
    Replaces the random model in virtual_trader with liquidity-aware prediction.
    Low liquidity = 3x slippage, low liquidity + high RVOL = 5x slippage.

Execution Timing:
    Best: first/last 30 min (09:30-10:00, 15:30-16:00 ET)
    Worst: 12:00-13:30 ET (lunch doldrums)

Market Impact:
    Kyle's Lambda: impact = sqrt(shares / ADV) * daily_volatility

ETP Risk:
    Leveraged ETP decay, NAV premium/discount, daily rebalance flows near close.
"""
from __future__ import annotations

import logging
import math
import sys
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
import config as cfg

logger = logging.getLogger("nzt48.smart_routing")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ADV scoring thresholds (shares per day)
_ADV_TIERS = [
    (10_000_000, 100),   # >10M = 100
    (2_000_000,   80),   # 2M-10M = 80
    (500_000,     60),   # 500K-2M = 60
    (100_000,     30),   # 100K-500K = 30
    (0,            0),   # <100K = 0
]

# Bid-ask spread scoring thresholds (% of mid)
_SPREAD_TIERS = [
    (0.0002, 100),  # <0.02% = 100
    (0.0005,  80),  # 0.02-0.05% = 80
    (0.001,   60),  # 0.05-0.1% = 60
    (0.005,   30),  # 0.1-0.5% = 30
]
_SPREAD_FLOOR = 0  # >0.5% = 0

# Market cap scoring thresholds (USD)
_MCAP_TIERS = [
    (10_000_000_000, 100),  # >10B = large cap = 100
    (2_000_000_000,   75),  # 2B-10B = mid cap = 75
    (300_000_000,     50),  # 300M-2B = small cap = 50
    (0,               20),  # <300M = micro cap = 20
]

# Component weights for combined liquidity score
_WEIGHT_ADV = 0.40
_WEIGHT_SPREAD = 0.40
_WEIGHT_MCAP = 0.20

# ADV participation limits
_DEFAULT_ADV_LIMIT_PCT = 0.01      # 1% of ADV — standard cap
_LOW_LIQ_ADV_LIMIT_PCT = 0.005    # 0.5% of ADV — illiquid names
_ETP_ADV_LIMIT_PCT = 0.02         # 2% of ADV — high-liquidity ETPs
_LOW_LIQUIDITY_THRESHOLD = 40     # Score below this = low liquidity

# Slippage model constants
_BASE_SLIPPAGE_BPS = 5.0          # 5 bps baseline
_LOW_LIQ_SLIPPAGE_MULT = 3.0     # 3x for low liquidity
_LOW_LIQ_HIGH_RVOL_MULT = 5.0    # 5x for low liquidity + high RVOL
_HIGH_RVOL_THRESHOLD = 2.5        # RVOL above this = fast market
_SIZE_IMPACT_SHARES = 500         # Additional slippage per 500 shares

# Execution timing (ET hours)
_MARKET_OPEN = time(9, 30)
_MORNING_OPTIMAL_END = time(10, 0)
_LUNCH_START = time(12, 0)
_LUNCH_END = time(13, 30)
_CLOSE_OPTIMAL_START = time(15, 30)
_MARKET_CLOSE = time(16, 0)

# Large order threshold for tranche splitting
_LARGE_ORDER_ADV_THRESHOLD = 0.01  # >1% of ADV triggers splitting

# ETP decay constants
_DAILY_DECAY_RATE_3X = 0.003      # ~30 bps/day for 3x products
_DAILY_DECAY_RATE_5X = 0.008      # ~80 bps/day for 5x products
_REBALANCE_RISK_WINDOW_START = time(15, 30)
_REBALANCE_RISK_WINDOW_END = time(16, 0)

# Latency-aware execution timing constants
_HFT_CATALYST_DELAY_SEC = 30      # Delay after catalyst news (let HFTs settle)
_MACRO_ANNOUNCEMENT_DELAY_SEC = 300  # 5-minute delay around FOMC/CPI/NFP
_MACRO_WINDOW_MINUTES = 5          # Window before/after announcement to delay
_MARKETABLE_LIMIT_OFFSET = 0.01   # Offset from bid/ask for marketable limits


class SmartRouter:
    """Smart Order Routing & Liquidity Awareness engine.

    Scores liquidity, caps position sizes, predicts slippage, plans
    execution timing, estimates market impact, and assesses ETP-specific
    risks. Designed to slot into the NZT-48 execution pipeline between
    signal qualification and virtual/live order placement.

    Usage::

        router = SmartRouter()
        liq = router.assess_liquidity("NVDA", adv=45_000_000, spread_pct=0.01)
        capped = router.cap_shares_by_liquidity("NVDA", 500, 45_000_000, liq["score"])
        slip = router.predict_slippage("NVDA", capped, 130.0, liq["score"], rvol=1.8)
        plan = router.get_execution_plan("NVDA", capped, adv=45_000_000)
        impact = router.estimate_market_impact(capped, 45_000_000, 0.025)
    """

    def __init__(self) -> None:
        self._assessments: dict[str, dict] = {}  # Cache of last assessments by ticker
        self._total_assessments = 0
        self._total_caps_applied = 0
        self._total_splits_suggested = 0
        logger.info("SmartRouter initialised")

    # ------------------------------------------------------------------
    # 1. Liquidity Assessment
    # ------------------------------------------------------------------

    def assess_liquidity(
        self,
        ticker: str,
        adv: float,
        spread_pct: Optional[float] = None,
        market_cap: Optional[float] = None,
    ) -> dict:
        """Score a ticker's liquidity on a 0-100 scale.

        The combined score is a weighted average of three components:
        - Average Daily Volume (ADV): 40% weight
        - Bid-ask spread as % of mid price: 40% weight
        - Market capitalisation: 20% weight

        When spread or market cap data is unavailable, the available
        components are re-weighted proportionally so the score still
        uses the full 0-100 range.

        Args:
            ticker: Stock/ETP ticker symbol.
            adv: Average daily volume in shares.
            spread_pct: Bid-ask spread as a percentage (e.g. 0.03 for 0.03%).
                        If None, component is excluded and others re-weighted.
            market_cap: Market capitalisation in USD.
                        If None, component is excluded and others re-weighted.

        Returns:
            Dict with keys: ticker, score (0-100), adv_score, spread_score,
            mcap_score, grade (A/B/C/D/F), adv, spread_pct, market_cap.
        """
        adv_score = self._score_adv(adv)
        spread_score = self._score_spread(spread_pct) if spread_pct is not None else None
        mcap_score = self._score_mcap(market_cap) if market_cap is not None else None

        # Build weighted average with available components
        total_weight = 0.0
        weighted_sum = 0.0

        total_weight += _WEIGHT_ADV
        weighted_sum += _WEIGHT_ADV * adv_score

        if spread_score is not None:
            total_weight += _WEIGHT_SPREAD
            weighted_sum += _WEIGHT_SPREAD * spread_score

        if mcap_score is not None:
            total_weight += _WEIGHT_MCAP
            weighted_sum += _WEIGHT_MCAP * mcap_score

        # Normalise to 0-100 even if some components missing
        combined_score = round(weighted_sum / total_weight, 1) if total_weight > 0 else 0.0

        grade = self._score_to_grade(combined_score)

        result = {
            "ticker": ticker,
            "score": combined_score,
            "grade": grade,
            "adv_score": adv_score,
            "spread_score": spread_score,
            "mcap_score": mcap_score,
            "adv": adv,
            "spread_pct": spread_pct,
            "market_cap": market_cap,
        }

        self._assessments[ticker] = result
        self._total_assessments += 1

        logger.info(
            "Liquidity assessment %s: score=%.1f grade=%s "
            "(ADV=%d spread=%s mcap=%s)",
            ticker, combined_score, grade, adv,
            f"{spread_pct:.4f}%" if spread_pct is not None else "N/A",
            f"${market_cap:,.0f}" if market_cap is not None else "N/A",
        )

        return result

    # ------------------------------------------------------------------
    # 2. Position Size Capping
    # ------------------------------------------------------------------

    def cap_shares_by_liquidity(
        self,
        ticker: str,
        desired_shares: int,
        adv: float,
        liquidity_score: float,
        is_etp: bool = False,
    ) -> int:
        """Cap share count to avoid moving the market.

        Rules:
        - Standard: never exceed 1% of ADV
        - Low liquidity (score < 40): max 0.5% of ADV
        - ETPs (Bot A): relaxed to 2% of ADV (typically high liquidity)

        Args:
            ticker: Stock/ETP ticker symbol.
            desired_shares: Number of shares the signal wants to trade.
            adv: Average daily volume in shares.
            liquidity_score: Combined liquidity score (0-100).
            is_etp: Whether this is an ETP (Bot A product).

        Returns:
            The capped number of shares (always >= 1 if desired_shares > 0).
        """
        if desired_shares <= 0 or adv <= 0:
            return 0

        # Determine the ADV participation limit
        if is_etp:
            limit_pct = _ETP_ADV_LIMIT_PCT
            limit_label = "ETP (2%)"
        elif liquidity_score < _LOW_LIQUIDITY_THRESHOLD:
            limit_pct = _LOW_LIQ_ADV_LIMIT_PCT
            limit_label = "low-liq (0.5%)"
        else:
            limit_pct = _DEFAULT_ADV_LIMIT_PCT
            limit_label = "standard (1%)"

        max_shares = int(adv * limit_pct)
        # Ensure at least 1 share if desired > 0
        max_shares = max(max_shares, 1)

        capped = min(desired_shares, max_shares)

        if capped < desired_shares:
            self._total_caps_applied += 1
            logger.warning(
                "Size capped %s: %d -> %d shares (%s limit, ADV=%d, liq=%.1f)",
                ticker, desired_shares, capped, limit_label, adv, liquidity_score,
            )
        else:
            logger.debug(
                "Size OK %s: %d shares within %s limit (ADV=%d)",
                ticker, desired_shares, limit_label, adv,
            )

        return capped

    # ------------------------------------------------------------------
    # 3. Slippage Prediction
    # ------------------------------------------------------------------

    def predict_slippage(
        self,
        ticker: str,
        shares: int,
        price: float,
        liquidity_score: float,
        rvol: float = 1.0,
    ) -> float:
        """Predict execution slippage in dollar terms.

        Replaces the random slippage model in virtual_trader with a
        liquidity-aware deterministic estimate. The prediction accounts for:
        - Base slippage (5 bps)
        - Liquidity multiplier (3x for low liquidity)
        - RVOL multiplier (5x when low liquidity + fast market)
        - Size impact (additional cost per 500-share block)

        Args:
            ticker: Stock/ETP ticker symbol.
            shares: Number of shares being traded.
            price: Current price per share.
            liquidity_score: Combined liquidity score (0-100).
            rvol: Relative volume (1.0 = normal, >2.5 = fast market).

        Returns:
            Expected slippage in dollars (always >= 0).
        """
        if shares <= 0 or price <= 0:
            return 0.0

        # Base slippage in bps
        base_bps = _BASE_SLIPPAGE_BPS

        # Liquidity multiplier
        is_low_liq = liquidity_score < _LOW_LIQUIDITY_THRESHOLD
        is_high_rvol = rvol >= _HIGH_RVOL_THRESHOLD

        if is_low_liq and is_high_rvol:
            liq_mult = _LOW_LIQ_HIGH_RVOL_MULT
        elif is_low_liq:
            liq_mult = _LOW_LIQ_SLIPPAGE_MULT
        elif is_high_rvol:
            # High RVOL alone still doubles slippage (from virtual_trader convention)
            liq_mult = 2.0
        else:
            liq_mult = 1.0

        # Size impact: additional slippage for large orders
        size_blocks = max(0, (shares - _SIZE_IMPACT_SHARES)) / _SIZE_IMPACT_SHARES
        size_bps = size_blocks * 1.0  # +1 bps per additional 500-share block

        total_bps = (base_bps * liq_mult) + size_bps
        slippage_pct = total_bps / 10_000.0  # Convert bps to decimal
        slippage_dollars = slippage_pct * price * shares

        logger.debug(
            "Slippage prediction %s: %.2f bps (base=%.1f x liq=%.1fx + size=%.1f bps) "
            "= $%.2f on %d shares @ $%.2f  [liq_score=%.1f, rvol=%.1f]",
            ticker, total_bps, base_bps, liq_mult, size_bps,
            slippage_dollars, shares, price, liquidity_score, rvol,
        )

        return round(slippage_dollars, 2)

    # ------------------------------------------------------------------
    # 4. Optimal Execution Timing
    # ------------------------------------------------------------------

    def get_execution_plan(
        self,
        ticker: str,
        shares: int,
        current_time: Optional[datetime] = None,
        adv: Optional[float] = None,
    ) -> dict:
        """Generate an execution plan with timing and tranche recommendations.

        Evaluates the current time against known liquidity windows:
        - Best execution: 09:30-10:00 and 15:30-16:00 ET (highest liquidity)
        - Worst execution: 12:00-13:30 ET (lunch doldrums, wide spreads)

        For large orders (>1% of ADV), suggests splitting into 3 tranches
        spaced 5 minutes apart over 15 minutes total.

        Args:
            ticker: Stock/ETP ticker symbol.
            shares: Number of shares to execute.
            current_time: Current datetime (defaults to now UTC).
                          Converted to ET internally.
            adv: Average daily volume. If None, no tranche splitting evaluated.

        Returns:
            Dict with keys: ticker, shares, timing_quality (OPTIMAL / ACCEPTABLE /
            POOR), timing_reason, should_split, tranches (list of dicts if splitting),
            delay_recommended, recommended_windows.
        """
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        # Convert to ET-equivalent time for market hour comparison
        # We work with the time component relative to ET market hours
        et_time = self._to_et_time(current_time)

        # Evaluate timing quality
        timing_quality, timing_reason = self._evaluate_timing(et_time)

        # Determine whether to split into tranches
        should_split = False
        tranches = []
        if adv is not None and adv > 0:
            adv_pct = shares / adv
            if adv_pct > _LARGE_ORDER_ADV_THRESHOLD:
                should_split = True
                self._total_splits_suggested += 1
                tranches = self._build_tranches(shares, et_time)

        # Recommend better windows if timing is poor
        delay_recommended = timing_quality == "POOR"
        recommended_windows = []
        if delay_recommended:
            recommended_windows = [
                {"window": "MORNING_OPTIMAL", "start": "09:30", "end": "10:00",
                 "reason": "Highest opening liquidity"},
                {"window": "CLOSE_OPTIMAL", "start": "15:30", "end": "16:00",
                 "reason": "MOC/LOC flow provides deep liquidity"},
            ]

        plan = {
            "ticker": ticker,
            "shares": shares,
            "timing_quality": timing_quality,
            "timing_reason": timing_reason,
            "current_time_et": et_time.strftime("%H:%M:%S") if isinstance(et_time, time) else str(et_time),
            "should_split": should_split,
            "tranches": tranches,
            "delay_recommended": delay_recommended,
            "recommended_windows": recommended_windows,
        }

        logger.info(
            "Execution plan %s: %d shares, timing=%s%s%s",
            ticker, shares, timing_quality,
            f" ({len(tranches)} tranches)" if should_split else "",
            " [DELAY RECOMMENDED]" if delay_recommended else "",
        )

        return plan

    # ------------------------------------------------------------------
    # 5. Market Impact Estimator
    # ------------------------------------------------------------------

    def estimate_market_impact(
        self,
        shares: int,
        adv: float,
        daily_vol: float,
    ) -> float:
        """Estimate price impact of the order using Kyle's Lambda model.

        The square-root model is standard in institutional execution:
            impact_pct = sqrt(shares / ADV) * daily_volatility

        This gives the expected one-way price movement caused by the order
        itself, before any alpha from the signal.

        Args:
            shares: Number of shares to execute.
            adv: Average daily volume in shares.
            daily_vol: Daily price volatility as a decimal (e.g. 0.025 for 2.5%).

        Returns:
            Expected market impact as a percentage (e.g. 0.12 for 0.12%).
        """
        if shares <= 0 or adv <= 0 or daily_vol <= 0:
            return 0.0

        participation = shares / adv
        impact_pct = math.sqrt(participation) * daily_vol * 100  # Convert to %

        logger.debug(
            "Market impact estimate: %d shares / %d ADV (%.4f%% participation) "
            "* %.2f%% vol = %.4f%% impact",
            shares, adv, participation * 100, daily_vol * 100, impact_pct,
        )

        return round(impact_pct, 4)

    # ------------------------------------------------------------------
    # 6. ETP-Specific Routing
    # ------------------------------------------------------------------

    def assess_etp_risk(
        self,
        ticker: str,
        leverage: int = 3,
        holding_minutes: int = 60,
        direction: str = "LONG",
    ) -> dict:
        """Assess risks specific to leveraged Exchange-Traded Products.

        Leveraged ETPs (3x, 5x) carry risks beyond the underlying:
        - **Decay risk**: Volatility drag increases exponentially with holding
          period and leverage. Intraday is manageable; multi-day is dangerous.
        - **Premium/discount**: ETPs can trade away from NAV, especially
          in volatile conditions. Entering at a premium compounds losses.
        - **Rebalance risk**: Leveraged ETPs rebalance daily near close
          (15:30-16:00 ET), creating predictable flows that can be exploited
          or avoided.

        Args:
            ticker: ETP ticker symbol.
            leverage: Leverage multiple (1, 2, 3, or 5).
            holding_minutes: Expected holding duration in minutes.
            direction: "LONG" or "SHORT".

        Returns:
            Dict with keys: ticker, leverage, holding_minutes, direction,
            decay_risk (LOW/MEDIUM/HIGH/EXTREME), estimated_decay_bps,
            rebalance_risk, rebalance_note, nav_risk_note, overall_risk,
            recommendations (list of strings).
        """
        # --- Decay risk ---
        holding_hours = holding_minutes / 60.0
        holding_days = holding_hours / 6.5  # 6.5 trading hours per day

        if leverage >= 5:
            daily_decay_rate = _DAILY_DECAY_RATE_5X
        elif leverage >= 3:
            daily_decay_rate = _DAILY_DECAY_RATE_3X
        else:
            daily_decay_rate = _DAILY_DECAY_RATE_3X * 0.3  # Minimal for 1-2x

        # Decay is exponential with time and leverage
        estimated_decay_bps = daily_decay_rate * holding_days * leverage * 10_000
        estimated_decay_bps = round(estimated_decay_bps, 1)

        if estimated_decay_bps < 5:
            decay_risk = "LOW"
        elif estimated_decay_bps < 20:
            decay_risk = "MEDIUM"
        elif estimated_decay_bps < 50:
            decay_risk = "HIGH"
        else:
            decay_risk = "EXTREME"

        # --- Rebalance risk ---
        # 3x/5x ETPs rebalance daily near close, creating large directional flows
        rebalance_risk = leverage >= 3
        if rebalance_risk:
            rebalance_note = (
                f"{leverage}x ETPs rebalance daily 15:30-16:00 ET. "
                "Expect increased volatility and spread widening near close. "
                "Exiting before 15:25 avoids rebalance turbulence."
            )
        else:
            rebalance_note = "Low leverage — minimal rebalance impact."

        # --- NAV premium/discount risk ---
        nav_risk_note = (
            "Check real-time iNAV vs market price before entry. "
            f"Leveraged {leverage}x products can trade 0.1-0.5% from NAV "
            "in volatile conditions. Entering at a premium amplifies losses."
        )

        # --- Overall risk ---
        risk_factors = 0
        if decay_risk in ("HIGH", "EXTREME"):
            risk_factors += 2
        elif decay_risk == "MEDIUM":
            risk_factors += 1
        if rebalance_risk:
            risk_factors += 1
        if leverage >= 5:
            risk_factors += 1
        if holding_minutes > 390:  # Holding through close (full day)
            risk_factors += 2

        if risk_factors >= 4:
            overall_risk = "EXTREME"
        elif risk_factors >= 3:
            overall_risk = "HIGH"
        elif risk_factors >= 1:
            overall_risk = "MEDIUM"
        else:
            overall_risk = "LOW"

        # --- Recommendations ---
        recommendations = []
        if holding_minutes > 240:
            recommendations.append(
                f"Holding {leverage}x ETP for {holding_minutes} min is risky. "
                "Consider reducing to intraday only (< 240 min)."
            )
        if leverage >= 5:
            recommendations.append(
                "5x leverage amplifies decay and rebalance risk. "
                "Strict sub-2-hour holds recommended."
            )
        if direction == "SHORT" and leverage >= 3:
            recommendations.append(
                f"Shorting a {leverage}x ETP has unlimited upside risk. "
                "Ensure stop-loss is tight and position size is reduced."
            )
        if decay_risk == "LOW" and not rebalance_risk:
            recommendations.append("Risk profile acceptable for standard execution.")

        result = {
            "ticker": ticker,
            "leverage": leverage,
            "holding_minutes": holding_minutes,
            "direction": direction,
            "decay_risk": decay_risk,
            "estimated_decay_bps": estimated_decay_bps,
            "rebalance_risk": rebalance_risk,
            "rebalance_note": rebalance_note,
            "nav_risk_note": nav_risk_note,
            "overall_risk": overall_risk,
            "recommendations": recommendations,
        }

        logger.info(
            "ETP risk assessment %s: %dx %s hold=%dmin -> decay=%s (%.1f bps) "
            "overall=%s",
            ticker, leverage, direction, holding_minutes,
            decay_risk, estimated_decay_bps, overall_risk,
        )

        return result

    # ------------------------------------------------------------------
    # 7. Latency-Aware Execution Timing
    # ------------------------------------------------------------------

    def should_delay_execution(
        self,
        signal,
        market_ctx,
    ) -> tuple[bool, int, str]:
        """Determine whether execution should be delayed based on market events.

        HFT algorithms front-run retail flow in the first seconds after
        catalyst news and macro announcements.  Delaying execution lets
        the initial HFT spike settle, resulting in better fills.

        Rules:
            1. S4 (catalyst) strategy + news within 30s → delay 30s
               ("HFT latency window after catalyst")
            2. FOMC day + within 5 min of announcement → delay 300s
               ("FOMC announcement window")
            3. CPI/NFP day + within 5 min of release → delay 300s
               ("CPI/NFP announcement window")
            4. Otherwise → no delay

        Args:
            signal: Signal object. Must have ``strategy`` (str) and
                    optionally ``catalyst_detected_at`` (datetime).
            market_ctx: MarketContext object. Must have ``fomc_today`` (bool),
                        ``cpi_nfp_today`` (bool), and optionally
                        ``fomc_time`` / ``macro_release_time`` (datetime).

        Returns:
            Tuple of (should_delay, delay_seconds, reason).
            If no delay needed: (False, 0, "").
        """
        import time as time_mod

        # --- Rule 1: Catalyst strategy (S4) with recent news ---
        if getattr(signal, 'strategy', '') == "S4":
            catalyst_at = getattr(signal, 'catalyst_detected_at', None)
            if catalyst_at is not None:
                now = datetime.now(timezone.utc)
                elapsed = (now - catalyst_at).total_seconds()
                if elapsed < _HFT_CATALYST_DELAY_SEC:
                    remaining = _HFT_CATALYST_DELAY_SEC - int(elapsed)
                    reason = "HFT latency window after catalyst"
                    logger.info(
                        "Delay execution %s: %s — %ds remaining (elapsed=%.0fs)",
                        getattr(signal, 'ticker', '???'), reason, remaining, elapsed,
                    )
                    return (True, remaining, reason)

        # --- Rule 2: FOMC day ---
        if getattr(market_ctx, 'fomc_today', False):
            fomc_time = getattr(market_ctx, 'fomc_time', None)
            if fomc_time is not None:
                now = datetime.now(timezone.utc)
                diff_seconds = abs((now - fomc_time).total_seconds())
                if diff_seconds <= _MACRO_WINDOW_MINUTES * 60:
                    reason = "FOMC announcement window"
                    logger.info(
                        "Delay execution %s: %s — within %d min of FOMC time",
                        getattr(signal, 'ticker', '???'), reason, _MACRO_WINDOW_MINUTES,
                    )
                    return (True, _MACRO_ANNOUNCEMENT_DELAY_SEC, reason)

        # --- Rule 3: CPI/NFP day ---
        if getattr(market_ctx, 'cpi_nfp_today', False):
            macro_time = getattr(market_ctx, 'macro_release_time', None)
            if macro_time is not None:
                now = datetime.now(timezone.utc)
                diff_seconds = abs((now - macro_time).total_seconds())
                if diff_seconds <= _MACRO_WINDOW_MINUTES * 60:
                    reason = "CPI/NFP announcement window"
                    logger.info(
                        "Delay execution %s: %s — within %d min of release",
                        getattr(signal, 'ticker', '???'), reason, _MACRO_WINDOW_MINUTES,
                    )
                    return (True, _MACRO_ANNOUNCEMENT_DELAY_SEC, reason)

        # --- No delay needed ---
        return (False, 0, "")

    def get_routing_recommendation(
        self,
        signal,
        market_ctx,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
    ) -> dict:
        """Generate a full routing recommendation including order type and delay.

        Combines latency-aware delay logic with a recommendation to use
        marketable limit orders instead of market orders.  A marketable
        limit (bid + 0.01 for buys, ask - 0.01 for sells) guarantees
        immediate fill while capping slippage at 1 cent.

        Args:
            signal: Signal object with direction, strategy, ticker.
            market_ctx: MarketContext with fomc_today, cpi_nfp_today.
            bid: Current best bid price. If None, order_type defaults
                 to "LIMIT" without a specific price.
            ask: Current best ask price. If None, order_type defaults
                 to "LIMIT" without a specific price.

        Returns:
            Dict with keys:
                ticker           (str)
                direction        (str)
                order_type       (str)   — "MARKETABLE_LIMIT" or "LIMIT"
                limit_price      (float | None) — recommended limit price
                offset_cents     (float) — the offset from NBBO
                should_delay     (bool)
                delay_seconds    (int)
                delay_reason     (str)
                rationale        (str)   — human-readable explanation
        """
        should_delay, delay_sec, delay_reason = self.should_delay_execution(
            signal, market_ctx
        )

        direction = getattr(signal, 'direction', None)
        direction_str = direction.value if hasattr(direction, 'value') else str(direction)
        ticker = getattr(signal, 'ticker', '???')

        # Compute marketable limit price
        limit_price = None
        order_type = "LIMIT"

        if direction_str == "LONG" and ask is not None:
            limit_price = round(ask + _MARKETABLE_LIMIT_OFFSET, 2)
            order_type = "MARKETABLE_LIMIT"
        elif direction_str == "SHORT" and bid is not None:
            limit_price = round(bid - _MARKETABLE_LIMIT_OFFSET, 2)
            order_type = "MARKETABLE_LIMIT"

        rationale = (
            f"Use {order_type} at ${limit_price:.2f} instead of MARKET order. "
            f"Caps slippage at {_MARKETABLE_LIMIT_OFFSET * 100:.0f} cent(s) "
            f"beyond NBBO while ensuring immediate fill."
        ) if limit_price is not None else (
            "Use LIMIT order (bid/ask not available for marketable limit calculation). "
            "Avoid MARKET orders to prevent adverse selection."
        )

        if should_delay:
            rationale += f" DELAY {delay_sec}s: {delay_reason}."

        recommendation = {
            "ticker": ticker,
            "direction": direction_str,
            "order_type": order_type,
            "limit_price": limit_price,
            "offset_cents": _MARKETABLE_LIMIT_OFFSET * 100,
            "should_delay": should_delay,
            "delay_seconds": delay_sec,
            "delay_reason": delay_reason,
            "rationale": rationale,
        }

        logger.info(
            "Routing recommendation %s %s: %s @ %s, delay=%s%s",
            direction_str, ticker, order_type,
            f"${limit_price:.2f}" if limit_price else "N/A",
            f"{delay_sec}s ({delay_reason})" if should_delay else "none",
            "",
        )

        return recommendation

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return current router status and statistics.

        Returns:
            Dict with operational stats: total assessments, caps applied,
            splits suggested, and the most recent per-ticker assessments.
        """
        return {
            "module": "SmartRouter",
            "total_assessments": self._total_assessments,
            "total_caps_applied": self._total_caps_applied,
            "total_splits_suggested": self._total_splits_suggested,
            "cached_tickers": list(self._assessments.keys()),
            "assessments": dict(self._assessments),
        }

    # ==================================================================
    # Private helpers
    # ==================================================================

    @staticmethod
    def _score_adv(adv: float) -> float:
        """Score average daily volume on 0-100 scale."""
        for threshold, score in _ADV_TIERS:
            if adv >= threshold:
                return float(score)
        return 0.0

    @staticmethod
    def _score_spread(spread_pct: float) -> float:
        """Score bid-ask spread (as percentage, e.g. 0.03 for 0.03%) on 0-100 scale.

        The input is the spread as a percentage of mid price. Internally
        converted to a decimal fraction for tier comparison.
        """
        spread_decimal = spread_pct / 100.0  # 0.03% -> 0.0003
        for threshold, score in _SPREAD_TIERS:
            if spread_decimal < threshold:
                return float(score)
        # Above all thresholds = worst tier
        return float(_SPREAD_FLOOR)

    @staticmethod
    def _score_mcap(market_cap: float) -> float:
        """Score market capitalisation on 0-100 scale."""
        for threshold, score in _MCAP_TIERS:
            if market_cap >= threshold:
                return float(score)
        return 20.0  # Micro cap floor

    @staticmethod
    def _score_to_grade(score: float) -> str:
        """Convert numeric score to letter grade."""
        if score >= 80:
            return "A"
        elif score >= 60:
            return "B"
        elif score >= 40:
            return "C"
        elif score >= 20:
            return "D"
        else:
            return "F"

    @staticmethod
    def _to_et_time(dt: datetime) -> time:
        """Extract the ET-equivalent time from a datetime.

        If the datetime is timezone-aware and in UTC, adjusts by -5 hours
        (EST approximation). For naive datetimes, assumes already in ET.
        This is a simplified conversion; production should use pytz/zoneinfo.
        """
        if dt.tzinfo is not None:
            # Approximate ET as UTC-5 (EST). DST-aware conversion would
            # use zoneinfo but we keep it simple for the execution engine.
            utc_offset_hours = dt.utcoffset().total_seconds() / 3600 if dt.utcoffset() else 0
            et_offset = -5  # EST
            adjustment_hours = et_offset - utc_offset_hours
            adjusted = dt.hour + adjustment_hours
            # Wrap around midnight
            adjusted_hour = int(adjusted) % 24
            return time(adjusted_hour, dt.minute, dt.second)
        else:
            return dt.time()

    @staticmethod
    def _evaluate_timing(et_time: time) -> tuple[str, str]:
        """Evaluate execution timing quality based on ET market hours.

        Returns:
            Tuple of (quality, reason) where quality is OPTIMAL / ACCEPTABLE / POOR.
        """
        if et_time < _MARKET_OPEN or et_time >= _MARKET_CLOSE:
            return ("POOR", "Outside regular trading hours — pre/post-market only, thin liquidity")

        # Optimal: first 30 min (09:30-10:00) or last 30 min (15:30-16:00)
        if _MARKET_OPEN <= et_time < _MORNING_OPTIMAL_END:
            return ("OPTIMAL", "Morning momentum window — highest opening liquidity and volume")

        if _CLOSE_OPTIMAL_START <= et_time < _MARKET_CLOSE:
            return ("OPTIMAL", "Close mechanics window — MOC/LOC orders provide deep liquidity")

        # Poor: lunch doldrums (12:00-13:30)
        if _LUNCH_START <= et_time < _LUNCH_END:
            return ("POOR", "Lunch doldrums — lowest intraday volume, widest spreads, random chop")

        # Everything else is acceptable
        return ("ACCEPTABLE", "Standard trading hours — adequate liquidity")

    @staticmethod
    def _build_tranches(shares: int, et_time: time) -> list[dict]:
        """Split a large order into 3 tranches over 15 minutes.

        Tranche sizing: 40% / 30% / 30% (front-loaded to capture
        initial price level, reduced later tranches as impact builds).

        Args:
            shares: Total shares to split.
            et_time: Current ET time for tranche scheduling.

        Returns:
            List of 3 tranche dicts with keys: tranche_number, shares,
            pct_of_total, delay_minutes, scheduled_time_et.
        """
        splits = [
            (1, 0.40, 0),    # Tranche 1: 40%, immediate
            (2, 0.30, 5),    # Tranche 2: 30%, +5 minutes
            (3, 0.30, 10),   # Tranche 3: 30%, +10 minutes
        ]

        tranches = []
        allocated = 0
        for tranche_num, pct, delay_min in splits:
            if tranche_num < 3:
                tranche_shares = int(shares * pct)
            else:
                # Last tranche gets the remainder to avoid rounding loss
                tranche_shares = shares - allocated

            allocated += tranche_shares

            # Calculate scheduled time
            scheduled_minutes = et_time.hour * 60 + et_time.minute + delay_min
            sched_hour = (scheduled_minutes // 60) % 24
            sched_minute = scheduled_minutes % 60
            scheduled_time = time(sched_hour, sched_minute)

            tranches.append({
                "tranche_number": tranche_num,
                "shares": tranche_shares,
                "pct_of_total": round(pct * 100, 1),
                "delay_minutes": delay_min,
                "scheduled_time_et": scheduled_time.strftime("%H:%M"),
            })

        return tranches


# ---------------------------------------------------------------------------
# Module-level convenience (optional singleton for quick access)
# ---------------------------------------------------------------------------

_default_router: SmartRouter | None = None


def get_router() -> SmartRouter:
    """Get or create the default SmartRouter singleton."""
    global _default_router
    if _default_router is None:
        _default_router = SmartRouter()
    return _default_router
