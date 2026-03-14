"""
Phase 3: Pre-Trade Compliance Gates
Purpose: Final validation before order submission to IB Gateway (100ms window)

Checks:
1. Margin available for this trade
2. Bid-ask spread reasonable (<50 bps LSE, <75 bps EU/ASX, <100 bps Japan)
3. Symbol in ISA-eligible list
4. Order size ≤30% of recent volume (market impact)
5. Price within 5% of current quote (stale quote rejection)

If ANY check fails: REJECT order, log reason, do not submit to IB.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)


# Spread limits per market (in basis points)
SPREAD_LIMITS = {
    "LSE": 50,      # London Stock Exchange leveraged ETPs
    "NASDAQ": 40,   # US equities
    "EURONEXT": 75,  # European stocks
    "ASX": 80,      # Australian ETPs
    "JPX": 100,     # Japan (less liquid)
}

# Market to ISA-eligible symbol prefixes
MARKET_ISA_ELIGIBLE = {
    "LSE": ["QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L", "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L"],
    "NASDAQ": [],  # Will be populated in Phase 31
    "EURONEXT": [],  # Will be populated in Phase 30
    "ASX": [],  # Will be populated in Phase 31
    "JPX": [],  # Will be populated in Phase 33
}


@dataclass
class GateCheck:
    """Result of a single pre-trade check"""
    check_name: str
    passed: bool
    message: str = ""


@dataclass
class GateResult:
    """Result of pre-trade gating"""
    passed: bool
    checks: list
    rejection_reason: Optional[str] = None
    timestamp: datetime = None


class PreTradeGate:
    """
    Pre-trade compliance gating (100ms window before IB submission).

    Every order must pass all 5 checks before reaching IB Gateway.
    """

    def __init__(self):
        self.rejected_orders = []  # Log all rejections

    def validate_order(
        self,
        symbol: str,
        quantity: float,
        side: str,  # "BUY" or "SELL"
        current_price: float,
        bid_price: float,
        ask_price: float,
        recent_volume: float,
        margin_available: float,
        position_size_required: float,
        market: str = "LSE",
    ) -> GateResult:
        """
        Validate order against 5 pre-trade checks.

        Args:
            symbol: Trading symbol (e.g., "QQQ3.L")
            quantity: Order quantity
            side: "BUY" or "SELL"
            current_price: Current mid-price
            bid_price: Current bid
            ask_price: Current ask
            recent_volume: 20-day avg volume (£)
            margin_available: Available margin (£)
            position_size_required: Margin required for this trade (£)
            market: Market code (LSE, NASDAQ, EURONEXT, ASX, JPX)

        Returns:
            GateResult with pass/fail and check details
        """
        now = datetime.now()
        checks = []

        # Check 1: Margin available
        check1 = GateCheck(
            check_name="Margin Available",
            passed=margin_available >= position_size_required,
            message=f"Avail: £{margin_available:.2f}, Required: £{position_size_required:.2f}"
        )
        checks.append(check1)

        # Check 2: Spread reasonable
        spread_bps = abs(ask_price - bid_price) / current_price * 10000 if current_price > 0 else 999
        spread_limit = SPREAD_LIMITS.get(market, 100)
        check2 = GateCheck(
            check_name="Spread Limit",
            passed=spread_bps <= spread_limit,
            message=f"Spread: {spread_bps:.1f} bps (limit: {spread_limit} bps)"
        )
        checks.append(check2)

        # Check 3: Symbol ISA-eligible
        eligible_symbols = []
        for eligible_list in MARKET_ISA_ELIGIBLE.values():
            eligible_symbols.extend(eligible_list)
        check3 = GateCheck(
            check_name="ISA Eligible",
            passed=symbol in eligible_symbols,
            message=f"Symbol: {symbol}" + ("" if symbol in eligible_symbols else " (NOT ELIGIBLE)")
        )
        checks.append(check3)

        # Check 4: Order size ≤30% of volume
        max_size = recent_volume * 0.30
        order_value = quantity * current_price
        check4 = GateCheck(
            check_name="Market Impact",
            passed=order_value <= max_size,
            message=f"Order: £{order_value:.2f}, Max: £{max_size:.2f} (30% of volume)"
        )
        checks.append(check4)

        # Check 5: Price not stale (within 5% of current)
        price_variance = abs(current_price - ((bid_price + ask_price) / 2)) / current_price
        check5 = GateCheck(
            check_name="Price Freshness",
            passed=price_variance <= 0.05,
            message=f"Variance: {price_variance*100:.2f}% (limit: 5%)"
        )
        checks.append(check5)

        # Determine overall pass/fail
        overall_pass = all(check.passed for check in checks)
        rejection_reason = None

        if not overall_pass:
            failed = [check.check_name for check in checks if not check.passed]
            rejection_reason = f"Failed: {', '.join(failed)}"
            self.rejected_orders.append({
                "timestamp": now,
                "symbol": symbol,
                "quantity": quantity,
                "reason": rejection_reason
            })
            logger.warning(f"Order rejected: {rejection_reason}")

        return GateResult(
            passed=overall_pass,
            checks=checks,
            rejection_reason=rejection_reason,
            timestamp=now
        )


# Unit tests
def test_all_checks_pass():
    """Test order that passes all checks"""
    gate = PreTradeGate()

    result = gate.validate_order(
        symbol="QQQ3.L",
        quantity=10,
        side="BUY",
        current_price=100,
        bid_price=99.95,
        ask_price=100.05,
        recent_volume=100000,  # £100k avg daily volume
        margin_available=5000,
        position_size_required=1000,
        market="LSE"
    )

    assert result.passed, "Order should pass all checks"
    assert result.rejection_reason is None
    assert len(gate.rejected_orders) == 0

    print("✓ All checks pass test passed")


def test_insufficient_margin():
    """Test order rejection due to insufficient margin"""
    gate = PreTradeGate()

    result = gate.validate_order(
        symbol="QQQ3.L",
        quantity=100,
        side="BUY",
        current_price=100,
        bid_price=99.95,
        ask_price=100.05,
        recent_volume=100000,
        margin_available=500,  # FAIL: only £500 available
        position_size_required=5000,  # FAIL: need £5000
        market="LSE"
    )

    assert not result.passed
    assert "Margin Available" in result.rejection_reason
    assert len(gate.rejected_orders) == 1

    print("✓ Insufficient margin test passed")


def test_wide_spread():
    """Test order rejection due to wide bid-ask spread"""
    gate = PreTradeGate()

    # 100 bps spread (way too wide for LSE)
    result = gate.validate_order(
        symbol="QQQ3.L",
        quantity=10,
        side="BUY",
        current_price=100,
        bid_price=99,  # 100 bps spread
        ask_price=101,
        recent_volume=100000,
        margin_available=5000,
        position_size_required=1000,
        market="LSE"
    )

    assert not result.passed
    assert "Spread Limit" in result.rejection_reason

    print("✓ Wide spread rejection test passed")


def test_ineligible_symbol():
    """Test order rejection for ineligible symbol"""
    gate = PreTradeGate()

    result = gate.validate_order(
        symbol="BTC",  # Not ISA-eligible
        quantity=10,
        side="BUY",
        current_price=100,
        bid_price=99.95,
        ask_price=100.05,
        recent_volume=100000,
        margin_available=5000,
        position_size_required=1000,
        market="LSE"
    )

    assert not result.passed
    assert "ISA Eligible" in result.rejection_reason

    print("✓ Ineligible symbol rejection test passed")


def test_market_impact():
    """Test order rejection due to excessive size (>30% volume)"""
    gate = PreTradeGate()

    result = gate.validate_order(
        symbol="QQQ3.L",
        quantity=50,
        side="BUY",
        current_price=100,
        bid_price=99.95,
        ask_price=100.05,
        recent_volume=1000,  # Only £1000 avg daily volume
        margin_available=50000,  # plenty of margin
        position_size_required=5000,  # Want to buy £5000 (500% of volume!)
        market="LSE"
    )

    assert not result.passed
    assert "Market Impact" in result.rejection_reason

    print("✓ Market impact rejection test passed")


if __name__ == "__main__":
    test_all_checks_pass()
    test_insufficient_margin()
    test_wide_spread()
    test_ineligible_symbol()
    test_market_impact()

    print("\n" + "="*60)
    print("PHASE 3: PRE-TRADE COMPLIANCE GATES - EXAMPLE OUTPUT")
    print("="*60)

    gate = PreTradeGate()

    # Good order
    result_good = gate.validate_order(
        symbol="QQQ3.L",
        quantity=5,
        side="BUY",
        current_price=150,
        bid_price=149.80,
        ask_price=150.20,
        recent_volume=50000,
        margin_available=10000,
        position_size_required=750,
        market="LSE"
    )

    print("\n✅ Order 1 (PASS):")
    for check in result_good.checks:
        status = "✓" if check.passed else "✗"
        print(f"  {status} {check.check_name}: {check.message}")

    # Bad order
    result_bad = gate.validate_order(
        symbol="DOGE",  # ineligible
        quantity=100,
        side="BUY",
        current_price=100,
        bid_price=95,  # wide spread
        ask_price=105,
        recent_volume=1000,  # small volume
        margin_available=100,  # low margin
        position_size_required=10000,  # high requirement
        market="LSE"
    )

    print("\n❌ Order 2 (FAIL):")
    for check in result_bad.checks:
        status = "✓" if check.passed else "✗"
        print(f"  {status} {check.check_name}: {check.message}")
    print(f"\nRejection reason: {result_bad.rejection_reason}")

    print(f"\nTotal rejected orders: {len(gate.rejected_orders)}")
    print("\n✅ Phase 3 (Pre-Trade Compliance Gates) complete and tested")
