"""
Position Sizing Engine with Margin Monitoring
==============================================

Q2-3: Real-time margin monitoring and dynamic position sizing.
Prevents overleveraging across multiple concurrent trades.

Design:
- Track available buying power in real-time
- Adjust position size dynamically if margin constrained
- Prevent excessive leverage across portfolio
- Integration with IBKR margin requirements

Risk Formula:
  max_position_value = available_margin × safety_factor
  safety_factor = 0.85 (leave 15% cushion)

Research basis:
  - Kelly Criterion (Kelly 1956): optimal position size = edge / odds
  - Half-Kelly: position_size = 0.5 × Kelly (reduces variance by 4x)
  - Margin safety: never use >85% of available margin (liquidity buffer)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.core.position_sizing_engine")

UTC = ZoneInfo("UTC")


@dataclass
class MarginStatus:
    """Current margin status from broker."""
    total_equity: float
    available_margin: float
    maintenance_margin: float
    margin_utilization_pct: float  # % of margin in use
    buying_power: float
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(UTC)


@dataclass
class PositionSizeResult:
    """Result of position sizing calculation."""
    ticker: str
    raw_size_pct: float  # Original size before margin adjustment
    adjusted_size_pct: float  # Final size after margin constraint
    position_value_usd: float
    margin_required: float
    margin_constrained: bool  # True if size was reduced due to margin
    reason: str  # Explanation of sizing decision


class PositionSizingEngine:
    """
    Q2-3: Margin-aware position sizing engine.

    Responsibilities:
    1. Query broker for real-time margin status
    2. Calculate maximum safe position size per trade
    3. Adjust sizing if margin constrained
    4. Prevent overleveraging across concurrent trades
    5. Apply tier-specific sizing rules
    """

    def __init__(self, ibkr_gateway=None, redis_client=None):
        """
        Initialize position sizing engine.

        Args:
            ibkr_gateway: IBKR connection for margin queries
            redis_client: Redis connection for state caching
        """
        self.ibkr = ibkr_gateway
        self.redis = redis_client

        # Safety parameters
        self.margin_safety_factor = 0.85  # Use max 85% of available margin
        self.max_portfolio_leverage = 2.0  # Max 2x portfolio leverage
        self.min_margin_cushion_pct = 0.15  # Keep 15% margin cushion

        # Cached margin status (updated every scan cycle)
        self._cached_margin_status: Optional[MarginStatus] = None
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl_seconds = 60  # Cache for 60 seconds

        # Active position tracking (ticker → size_pct)
        self._active_positions: Dict[str, float] = {}

    async def get_margin_status(self, force_refresh: bool = False) -> Optional[MarginStatus]:
        """
        Get current margin status from broker.

        Args:
            force_refresh: Force refresh from broker (skip cache)

        Returns:
            MarginStatus or None if unavailable
        """
        # Check cache
        if not force_refresh and self._cached_margin_status:
            now = datetime.now(UTC)
            if self._cache_timestamp:
                age_seconds = (now - self._cache_timestamp).total_seconds()
                if age_seconds < self._cache_ttl_seconds:
                    logger.debug(f"Q2-3 MARGIN: Using cached status (age={age_seconds:.1f}s)")
                    return self._cached_margin_status

        # Query broker
        if not self.ibkr:
            logger.warning("Q2-3 MARGIN: No IB connection, cannot query margin")
            return None

        try:
            margin_data = await self._query_broker_margin()

            if margin_data:
                status = MarginStatus(
                    total_equity=margin_data.get("total_equity", 0.0),
                    available_margin=margin_data.get("available_margin", 0.0),
                    maintenance_margin=margin_data.get("maintenance_margin", 0.0),
                    margin_utilization_pct=margin_data.get("margin_utilization_pct", 0.0),
                    buying_power=margin_data.get("buying_power", 0.0),
                )

                # Update cache
                self._cached_margin_status = status
                self._cache_timestamp = datetime.now(UTC)

                logger.info(
                    f"Q2-3 MARGIN: equity=${status.total_equity:,.0f} "
                    f"available=${status.available_margin:,.0f} "
                    f"utilization={status.margin_utilization_pct:.1f}% "
                    f"buying_power=${status.buying_power:,.0f}"
                )

                return status
            else:
                logger.warning("Q2-3 MARGIN: Broker returned no margin data")
                return None

        except Exception as e:
            logger.error(f"Q2-3 MARGIN: Error querying broker: {e}")
            return None

    async def calculate_position_size(
        self,
        ticker: str,
        tier_base_pct: float,
        current_price: float,
        account_equity: float,
        leverage: int = 3,
    ) -> PositionSizeResult:
        """
        Calculate margin-aware position size.

        Args:
            ticker: Stock ticker
            tier_base_pct: Base position size from tier (e.g., 0.04 = 4%)
            current_price: Current stock price
            account_equity: Total account equity
            leverage: Position leverage (e.g., 3 for 3x leveraged ETP)

        Returns:
            PositionSizeResult with final sizing
        """
        # Get margin status
        margin_status = await self.get_margin_status()

        if not margin_status:
            # Fallback: use tier base size (no margin adjustment)
            logger.warning(
                f"[{ticker}] Q2-3 MARGIN: No margin data, using tier base size {tier_base_pct * 100:.1f}%"
            )
            position_value = account_equity * tier_base_pct
            return PositionSizeResult(
                ticker=ticker,
                raw_size_pct=tier_base_pct,
                adjusted_size_pct=tier_base_pct,
                position_value_usd=position_value,
                margin_required=position_value / leverage,  # Rough estimate
                margin_constrained=False,
                reason="No margin data, using tier base size",
            )

        # Calculate raw position value
        raw_position_value = account_equity * tier_base_pct

        # Calculate margin required for this position
        # For leveraged ETPs: margin = position_value / leverage
        # For unleveraged: margin = position_value (no leverage benefit)
        margin_required = raw_position_value / max(leverage, 1)

        # Check if margin constrained
        available_margin = margin_status.available_margin * self.margin_safety_factor
        total_portfolio_value = sum(
            account_equity * pct for pct in self._active_positions.values()
        )
        total_portfolio_value += raw_position_value  # Add this position

        # Check constraints
        margin_constrained = False
        adjusted_position_value = raw_position_value
        reason = f"Tier base size {tier_base_pct * 100:.1f}%"

        # Constraint 1: Margin availability
        if margin_required > available_margin:
            # Scale down position to fit available margin
            max_position_value = available_margin * leverage
            adjusted_position_value = min(adjusted_position_value, max_position_value)
            margin_constrained = True
            reason = f"Margin constrained: scaled to ${adjusted_position_value:,.0f} (available=${available_margin:,.0f})"

        # Constraint 2: Portfolio leverage
        max_portfolio_value = account_equity * self.max_portfolio_leverage
        if total_portfolio_value > max_portfolio_value:
            # Scale down to stay within portfolio leverage limit
            excess = total_portfolio_value - max_portfolio_value
            adjusted_position_value = max(adjusted_position_value - excess, 0)
            margin_constrained = True
            reason = f"Portfolio leverage limit: max {self.max_portfolio_leverage}x"

        # Constraint 3: Minimum margin cushion
        margin_after_position = margin_status.available_margin - margin_required
        if margin_after_position < (margin_status.total_equity * self.min_margin_cushion_pct):
            # Leave minimum cushion
            max_safe_margin = margin_status.available_margin * (1 - self.min_margin_cushion_pct)
            max_position_value = max_safe_margin * leverage
            adjusted_position_value = min(adjusted_position_value, max_position_value)
            margin_constrained = True
            reason = f"Margin cushion: keeping {self.min_margin_cushion_pct * 100:.0f}% reserve"

        # Final sizing
        adjusted_size_pct = adjusted_position_value / account_equity
        adjusted_margin_required = adjusted_position_value / max(leverage, 1)

        logger.info(
            f"[{ticker}] Q2-3 POSITION_SIZE: "
            f"raw={tier_base_pct * 100:.1f}% (${raw_position_value:,.0f}) → "
            f"adjusted={adjusted_size_pct * 100:.1f}% (${adjusted_position_value:,.0f}) | "
            f"margin_req=${adjusted_margin_required:,.0f} | "
            f"constrained={margin_constrained} | {reason}"
        )

        return PositionSizeResult(
            ticker=ticker,
            raw_size_pct=tier_base_pct,
            adjusted_size_pct=adjusted_size_pct,
            position_value_usd=adjusted_position_value,
            margin_required=adjusted_margin_required,
            margin_constrained=margin_constrained,
            reason=reason,
        )

    def register_position(self, ticker: str, size_pct: float) -> None:
        """
        Register an active position for portfolio tracking.

        Args:
            ticker: Stock ticker
            size_pct: Position size as % of account equity
        """
        self._active_positions[ticker] = size_pct
        logger.debug(
            f"Q2-3 POSITION_REGISTRY: Registered {ticker} at {size_pct * 100:.1f}% "
            f"(total positions={len(self._active_positions)})"
        )

    def unregister_position(self, ticker: str) -> None:
        """
        Unregister position on exit.

        Args:
            ticker: Stock ticker
        """
        if ticker in self._active_positions:
            del self._active_positions[ticker]
            logger.debug(
                f"Q2-3 POSITION_REGISTRY: Unregistered {ticker} "
                f"(remaining positions={len(self._active_positions)})"
            )

    def get_portfolio_utilization(self) -> float:
        """
        Get total portfolio utilization as % of equity.

        Returns:
            Sum of all active position sizes (e.g., 0.15 = 15% total exposure)
        """
        return sum(self._active_positions.values())

    def get_active_positions(self) -> Dict[str, float]:
        """Get dict of active positions: ticker → size_pct."""
        return self._active_positions.copy()

    async def _query_broker_margin(self) -> Optional[dict]:
        """
        Query broker for margin data.

        Placeholder for actual IBKR API integration.
        Real implementation calls: self.ibkr.get_account_summary()

        Returns:
            Dict with keys: total_equity, available_margin, maintenance_margin,
            margin_utilization_pct, buying_power
        """
        if not self.ibkr:
            return None

        # TODO: Implement actual IBKR margin query
        # For now, return mock data
        return None
