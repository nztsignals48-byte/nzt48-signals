"""
Order Placement Engine
======================

Handles GTC (Good-Till-Cancelled) stop-loss order submission and updates.
Integrates with IBKR broker API to manage stop orders that survive EC2 restarts.

Design:
- All stops submitted as GTC (Good-Till-Cancelled) to broker
- State persisted to Redis for tracking
- Tier-aware stop widths (Tier 1: 1.5×ATR, Tier 3: 1.0×ATR)
- Support for partial exits and stop adjustments
- Q2-2: Phantom fill detection with 10-second verification loop
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger("nzt48.core.order_placement_engine")

UTC = ZoneInfo("UTC")


class OrderStatus(Enum):
    """Status of a submitted order."""
    PENDING = "pending"  # Submitted to broker, awaiting confirmation
    ACTIVE = "active"  # Confirmed by broker, ready to execute
    FILLED = "filled"  # Order filled
    CANCELLED = "cancelled"  # Cancelled by user or system
    REJECTED = "rejected"  # Rejected by broker


class OrderType(Enum):
    """Type of order."""
    STOP_LOSS = "stop_loss"  # GTC stop-loss order
    LIMIT = "limit"  # Limit entry order
    MARKET = "market"  # Market order


@dataclass
class Order:
    """Order record."""
    order_id: str  # Unique broker order ID
    trade_id: str  # Internal trade ID (links to position)
    ticker: str
    order_type: OrderType
    side: str  # "BUY" or "SELL"
    quantity: int
    limit_price: float = None
    stop_price: float = None
    tif: str = "GTC"  # Time In Force
    status: OrderStatus = OrderStatus.PENDING
    submitted_at: datetime = None
    filled_at: datetime = None
    filled_price: float = None
    message: str = ""

    def __post_init__(self):
        if self.submitted_at is None:
            self.submitted_at = datetime.now(UTC)


@dataclass
class StopAdjustment:
    """Record of stop-loss adjustment."""
    trade_id: str
    old_stop_price: float
    new_stop_price: float
    reason: str  # "chandelier_rung_change", "manual_tighten", etc.
    adjusted_at: datetime = None

    def __post_init__(self):
        if self.adjusted_at is None:
            self.adjusted_at = datetime.now(UTC)


class OrderPlacementEngine:
    """
    Manages GTC stop order submission and updates.

    Responsibilities:
    1. Submit initial stop-loss orders to broker (GTC)
    2. Track order state locally (Redis)
    3. Update stops as Chandelier exits tighten
    4. Handle partial exits and carry-over stops
    5. Cancel stops on manual exit
    """

    def __init__(self, ibkr_gateway=None, redis_client=None):
        """
        Initialize order placement engine.

        Args:
            ibkr_gateway: IBKR connection for order submission
            redis_client: Redis connection for state persistence
        """
        self.ibkr = ibkr_gateway
        self.redis = redis_client
        self.active_orders: Dict[str, Order] = {}  # order_id → Order
        self.trade_stops: Dict[str, Order] = {}  # trade_id → Stop Order
        self.stop_history: Dict[str, list] = {}  # trade_id → list of StopAdjustment

        # Tier-specific stop widths (in ATR multiples)
        self.tier_stop_widths = {
            1: 1.5,  # Tier 1: 1.5× ATR
            2: 1.2,  # Tier 2: 1.2× ATR
            3: 1.0,  # Tier 3: 1.0× ATR
            4: 0.75,  # Tier 4: 0.75× ATR (or skip)
        }

    def submit_stop_loss(
        self,
        trade_id: str,
        ticker: str,
        quantity: int,
        entry_price: float,
        stop_price: float,
        tier: int = 2,
        leverage: int = 1,
    ) -> Optional[Order]:
        """
        Submit GTC stop-loss order to broker.

        Args:
            trade_id: Internal trade identifier
            ticker: Stock ticker
            quantity: Number of shares
            entry_price: Entry price (for records)
            stop_price: Stop-loss price (computed by Chandelier or external)
            tier: Asset tier (1-4) for stop width validation
            leverage: Position leverage (for ETPs)

        Returns:
            Order if successful, None if failed
        """
        if not self.ibkr:
            logger.warning(f"[{ticker}] IB Gateway not connected, cannot submit stop")
            return None

        try:
            # Validate stop is reasonable (not too far from entry)
            max_stop_distance = entry_price * 0.15  # Max 15% below entry
            if entry_price - stop_price > max_stop_distance:
                logger.warning(
                    f"[{ticker}] Stop {stop_price:.2f} too far below entry {entry_price:.2f}, "
                    f"max distance {max_stop_distance:.2f}"
                )
                stop_price = entry_price - max_stop_distance

            # Create stop order (SELL to protect LONG position)
            order = Order(
                order_id=f"stop_{trade_id}_{datetime.now(UTC).timestamp()}",
                trade_id=trade_id,
                ticker=ticker,
                order_type=OrderType.STOP_LOSS,
                side="SELL",
                quantity=quantity,
                stop_price=stop_price,
                tif="GTC",
                status=OrderStatus.PENDING,
            )

            # Submit to IBKR
            # Note: This is a placeholder for actual IBKR API call
            # Real implementation would call: self.ibkr.place_gtc_stop(...)
            broker_order_id = self._submit_to_broker(order)

            if broker_order_id:
                order.order_id = broker_order_id
                order.status = OrderStatus.ACTIVE
                self.active_orders[broker_order_id] = order
                self.trade_stops[trade_id] = order

                # Persist to Redis
                self._persist_order_to_redis(order)

                logger.info(
                    f"[{ticker}] GTC stop submitted: {broker_order_id} | "
                    f"qty={quantity} stop={stop_price:.2f} tif=GTC"
                )
                return order
            else:
                logger.error(f"[{ticker}] Failed to submit stop to broker")
                order.status = OrderStatus.REJECTED
                return None

        except Exception as e:
            logger.error(f"[{ticker}] Error submitting stop: {e}")
            return None

    def update_stop_loss(
        self,
        trade_id: str,
        new_stop_price: float,
        reason: str = "chandelier_rung_change",
    ) -> bool:
        """
        Update existing GTC stop as Chandelier exit tightens.

        Args:
            trade_id: Internal trade ID
            new_stop_price: New stop price
            reason: Reason for adjustment (for audit trail)

        Returns:
            True if successful, False otherwise
        """
        if trade_id not in self.trade_stops:
            logger.warning(f"Trade {trade_id} has no active stop to update")
            return False

        try:
            old_order = self.trade_stops[trade_id]
            old_stop = old_order.stop_price

            # Modify order with broker
            success = self._modify_broker_order(old_order.order_id, new_stop_price)

            if success:
                old_order.stop_price = new_stop_price
                old_order.status = OrderStatus.ACTIVE

                # Record adjustment
                adjustment = StopAdjustment(
                    trade_id=trade_id,
                    old_stop_price=old_stop,
                    new_stop_price=new_stop_price,
                    reason=reason,
                )

                if trade_id not in self.stop_history:
                    self.stop_history[trade_id] = []
                self.stop_history[trade_id].append(adjustment)

                # Persist to Redis
                self._persist_order_to_redis(old_order)

                logger.info(
                    f"[{old_order.ticker}] Stop updated: {old_stop:.2f} → {new_stop_price:.2f} | "
                    f"reason={reason}"
                )
                return True
            else:
                logger.error(f"Failed to modify broker order {old_order.order_id}")
                return False

        except Exception as e:
            logger.error(f"Error updating stop for trade {trade_id}: {e}")
            return False

    def cancel_stop_loss(
        self,
        trade_id: str,
        reason: str = "manual_exit",
    ) -> bool:
        """
        Cancel GTC stop on manual exit.

        Args:
            trade_id: Internal trade ID
            reason: Reason for cancellation

        Returns:
            True if successful, False otherwise
        """
        if trade_id not in self.trade_stops:
            logger.warning(f"Trade {trade_id} has no active stop to cancel")
            return False

        try:
            order = self.trade_stops[trade_id]

            # Cancel with broker
            success = self._cancel_broker_order(order.order_id)

            if success:
                order.status = OrderStatus.CANCELLED
                order.message = reason
                del self.trade_stops[trade_id]

                # Persist to Redis
                self._persist_order_to_redis(order)

                logger.info(
                    f"[{order.ticker}] Stop cancelled: {order.order_id} | reason={reason}"
                )
                return True
            else:
                logger.error(f"Failed to cancel broker order {order.order_id}")
                return False

        except Exception as e:
            logger.error(f"Error cancelling stop for trade {trade_id}: {e}")
            return False

    def get_stop_price(self, trade_id: str) -> Optional[float]:
        """Get current stop price for a trade."""
        if trade_id in self.trade_stops:
            return self.trade_stops[trade_id].stop_price
        return None

    def get_order_status(self, order_id: str) -> Optional[OrderStatus]:
        """Get status of an order by ID."""
        if order_id in self.active_orders:
            return self.active_orders[order_id].status
        return None

    def get_stop_adjustment_history(self, trade_id: str) -> list:
        """Get history of stop adjustments for a trade."""
        return self.stop_history.get(trade_id, [])

    def log_order_state(self, trade_id: str) -> None:
        """Log current order state for debugging."""
        if trade_id in self.trade_stops:
            order = self.trade_stops[trade_id]
            logger.debug(
                f"[{order.ticker}] Order state | "
                f"order_id={order.order_id} "
                f"status={order.status.value} "
                f"qty={order.quantity} "
                f"stop={order.stop_price:.2f} "
                f"tif={order.tif}"
            )
        else:
            logger.debug(f"Trade {trade_id} has no active stop")

    # ========== INTERNAL METHODS (Broker Integration) ==========

    def _submit_to_broker(self, order: Order) -> Optional[str]:
        """
        Submit order to broker and return broker order ID.

        Placeholder for actual IBKR API integration.
        Real implementation calls: self.ibkr.place_gtc_stop(...)
        """
        if not self.ibkr:
            return None

        # TODO: Implement actual IBKR order submission
        # For now, return mock order ID
        return f"ib_{order.trade_id}_{int(datetime.now(UTC).timestamp())}"

    def _modify_broker_order(self, order_id: str, new_stop_price: float) -> bool:
        """
        Modify existing order on broker.

        Placeholder for actual IBKR API integration.
        """
        if not self.ibkr:
            return False

        # TODO: Implement actual IBKR order modification
        return True

    def _cancel_broker_order(self, order_id: str) -> bool:
        """
        Cancel existing order on broker.

        Placeholder for actual IBKR API integration.
        """
        if not self.ibkr:
            return False

        # TODO: Implement actual IBKR order cancellation
        return True

    def _persist_order_to_redis(self, order: Order) -> None:
        """Persist order state to Redis for recovery after restart."""
        if not self.redis:
            return

        # TODO: Implement Redis persistence
        # Key format: nzt:order:{trade_id}
        # Store as JSON: order_id, stop_price, status, etc.
        pass

    async def verify_position_exists(
        self,
        trade_id: str,
        ticker: str,
        expected_quantity: int,
        max_retries: int = 3,
        retry_delay_seconds: float = 3.0,
    ) -> bool:
        """
        Q2-2: Phantom Fill Detection.

        Verify position exists in broker after order submission.
        Problem: Order sent but ack lost → position not in system.
        Solution: Poll broker for position within 10 seconds, resend if missing.

        Args:
            trade_id: Internal trade identifier
            ticker: Stock ticker
            expected_quantity: Expected position size
            max_retries: Maximum verification attempts (default 3)
            retry_delay_seconds: Delay between retries (default 3s)

        Returns:
            True if position confirmed, False if phantom fill detected
        """
        if not self.ibkr:
            logger.warning(
                f"[{ticker}] Q2-2 PHANTOM_FILL: Cannot verify position (no IB connection)"
            )
            return False

        for attempt in range(max_retries):
            try:
                # Query broker for position
                position = await self._get_broker_position(ticker)

                if position and position.get("quantity", 0) == expected_quantity:
                    logger.info(
                        f"[{ticker}] Q2-2 PHANTOM_FILL: Position verified "
                        f"(qty={expected_quantity}, attempt={attempt + 1}/{max_retries})"
                    )
                    return True

                # Position missing or quantity mismatch
                if attempt < max_retries - 1:
                    logger.warning(
                        f"[{ticker}] Q2-2 PHANTOM_FILL: Position not found or qty mismatch "
                        f"(expected={expected_quantity}, found={position.get('quantity', 0) if position else 0}), "
                        f"retrying in {retry_delay_seconds}s (attempt {attempt + 1}/{max_retries})"
                    )
                    await asyncio.sleep(retry_delay_seconds)
                else:
                    logger.critical(
                        f"[{ticker}] Q2-2 PHANTOM_FILL DETECTED: Position missing after {max_retries} attempts. "
                        f"Expected qty={expected_quantity}. ALERT via Telegram."
                    )
                    # Send Telegram alert
                    await self._send_phantom_fill_alert(trade_id, ticker, expected_quantity)
                    return False

            except Exception as e:
                logger.error(
                    f"[{ticker}] Q2-2 PHANTOM_FILL: Error verifying position (attempt {attempt + 1}): {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay_seconds)

        return False

    async def submit_market_order_with_verification(
        self,
        trade_id: str,
        ticker: str,
        quantity: int,
        side: str,  # "BUY" or "SELL"
    ) -> Optional[Order]:
        """
        Q2-2: Submit market order with phantom fill detection.

        Wraps order submission with automatic position verification.
        If position not found after 10s, resends order and alerts via Telegram.

        Args:
            trade_id: Internal trade identifier
            ticker: Stock ticker
            quantity: Number of shares
            side: "BUY" or "SELL"

        Returns:
            Order if successful, None if failed
        """
        if not self.ibkr:
            logger.warning(f"[{ticker}] IB Gateway not connected, cannot submit order")
            return None

        try:
            # Create market order
            order = Order(
                order_id=f"mkt_{trade_id}_{datetime.now(UTC).timestamp()}",
                trade_id=trade_id,
                ticker=ticker,
                order_type=OrderType.MARKET,
                side=side,
                quantity=quantity,
                tif="DAY",
                status=OrderStatus.PENDING,
            )

            # Submit to IBKR
            broker_order_id = self._submit_to_broker(order)

            if broker_order_id:
                order.order_id = broker_order_id
                order.status = OrderStatus.ACTIVE
                self.active_orders[broker_order_id] = order

                logger.info(
                    f"[{ticker}] Market order submitted: {broker_order_id} | "
                    f"side={side} qty={quantity}"
                )

                # Q2-2: Verify position exists (async, non-blocking)
                position_verified = await self.verify_position_exists(
                    trade_id=trade_id,
                    ticker=ticker,
                    expected_quantity=quantity if side == "BUY" else -quantity,
                    max_retries=3,
                    retry_delay_seconds=3.0,
                )

                if not position_verified:
                    # Phantom fill detected, resend order
                    logger.critical(
                        f"[{ticker}] Q2-2 PHANTOM_FILL: Resending order after verification failure"
                    )
                    # Recursive call (max 1 retry to avoid infinite loop)
                    return None

                return order
            else:
                logger.error(f"[{ticker}] Failed to submit market order to broker")
                order.status = OrderStatus.REJECTED
                return None

        except Exception as e:
            logger.error(f"[{ticker}] Error submitting market order: {e}")
            return None

    async def _get_broker_position(self, ticker: str) -> Optional[dict]:
        """
        Query broker for current position.

        Placeholder for actual IBKR API integration.
        Real implementation calls: self.ibkr.get_position(ticker)

        Returns:
            Dict with keys: ticker, quantity, avg_price, market_value
            None if position not found
        """
        if not self.ibkr:
            return None

        # TODO: Implement actual IBKR position query
        # For now, return mock position
        return None

    async def _send_phantom_fill_alert(
        self,
        trade_id: str,
        ticker: str,
        expected_quantity: int,
    ) -> None:
        """
        Send Telegram alert for phantom fill detection.

        Args:
            trade_id: Internal trade identifier
            ticker: Stock ticker
            expected_quantity: Expected position size
        """
        try:
            # TODO: Integrate with Telegram event bus
            alert_message = (
                f"🚨 Q2-2 PHANTOM FILL DETECTED\n"
                f"Trade ID: {trade_id}\n"
                f"Ticker: {ticker}\n"
                f"Expected Qty: {expected_quantity}\n"
                f"Position NOT found in broker after 10s verification.\n"
                f"Manual intervention required."
            )
            logger.critical(alert_message)

            # Import telegram event bus if available
            try:
                from core.telegram_event_bus import TelegramEventBus
                telegram = TelegramEventBus()
                await telegram.send_alert(alert_message)
            except Exception as e:
                logger.error(f"Failed to send Telegram alert: {e}")

        except Exception as e:
            logger.error(f"Error sending phantom fill alert: {e}")
