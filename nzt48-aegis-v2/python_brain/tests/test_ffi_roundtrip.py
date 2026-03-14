"""Phase 1 FFI Round-Trip Tests.
Verifies all #[pyclass] types can be created and read from Python.
"""
import pytest
import rust_core


class TestTickerId:
    def test_create(self):
        t = rust_core.TickerId(42)
        assert t.id == 42

    def test_repr(self):
        t = rust_core.TickerId(99)
        assert "99" in repr(t)

    def test_equality(self):
        a = rust_core.TickerId(42)
        b = rust_core.TickerId(42)
        c = rust_core.TickerId(99)
        assert a == b
        assert a != c

    def test_hash(self):
        a = rust_core.TickerId(42)
        b = rust_core.TickerId(42)
        assert hash(a) == hash(b)
        s = {a, b}
        assert len(s) == 1


class TestEnums:
    def test_direction_variants(self):
        assert rust_core.Direction.Long != rust_core.Direction.Short

    def test_strategy_id_variants(self):
        assert rust_core.StrategyId.VanguardSniper != rust_core.StrategyId.ApexScout

    def test_risk_regime_ordering(self):
        assert rust_core.RiskRegime.Halt > rust_core.RiskRegime.Flatten
        assert rust_core.RiskRegime.Flatten > rust_core.RiskRegime.Reduce
        assert rust_core.RiskRegime.Reduce > rust_core.RiskRegime.Normal

    def test_exit_priority_ordering(self):
        assert rust_core.ExitPriority.HaltFlatten > rust_core.ExitPriority.HardStopLoss
        assert rust_core.ExitPriority.HardStopLoss > rust_core.ExitPriority.ChandelierStop
        assert rust_core.ExitPriority.ChandelierStop > rust_core.ExitPriority.EodFlatten
        assert rust_core.ExitPriority.EodFlatten > rust_core.ExitPriority.SignalReversal

    def test_order_state_15_variants(self):
        states = [
            rust_core.OrderState.IntentGenerated,
            rust_core.OrderState.RiskChecked,
            rust_core.OrderState.Rejected,
            rust_core.OrderState.WalWritten,
            rust_core.OrderState.Submitted,
            rust_core.OrderState.BrokerRejected,
            rust_core.OrderState.Acknowledged,
            rust_core.OrderState.Orphaned,
            rust_core.OrderState.PartiallyFilled,
            rust_core.OrderState.Filled,
            rust_core.OrderState.ExitRegistered,
            rust_core.OrderState.ExitTriggered,
            rust_core.OrderState.ExitOrderSubmitted,
            rust_core.OrderState.ExitFilled,
            rust_core.OrderState.PositionClosed,
        ]
        assert len(states) == 15
        # All distinct
        assert len(set(states)) == 15

    def test_broker_ack_status(self):
        assert rust_core.BrokerAckStatus.Accepted != rust_core.BrokerAckStatus.Rejected

    def test_exit_reason(self):
        assert rust_core.ExitReason.HaltFlatten != rust_core.ExitReason.HardStopLoss

    def test_exit_order_type(self):
        assert rust_core.ExitOrderType.MarketSell != rust_core.ExitOrderType.LimitAtStop


class TestMarketTick:
    def test_roundtrip(self):
        """Create MarketTick in Python -> read all fields -> verify."""
        tick = rust_core.MarketTick(
            ticker_id=rust_core.TickerId(42),
            bid=10.50,
            ask=10.52,
            last=10.51,
            volume=50000,
            timestamp_ns=1_000_000_000,
            recv_timestamp_ns=1_000_000_100,
        )
        assert tick.ticker_id == rust_core.TickerId(42)
        assert tick.bid == 10.50
        assert tick.ask == 10.52
        assert tick.last == 10.51
        assert tick.volume == 50000
        assert tick.timestamp_ns == 1_000_000_000
        assert tick.recv_timestamp_ns == 1_000_000_100

    def test_repr(self):
        tick = rust_core.MarketTick(
            ticker_id=rust_core.TickerId(42),
            bid=10.50, ask=10.52, last=10.51,
            volume=50000, timestamp_ns=0, recv_timestamp_ns=0,
        )
        r = repr(tick)
        assert "42" in r
        assert "10.5" in r


class TestOrderIntent:
    def test_roundtrip(self):
        intent = rust_core.OrderIntent(
            ticker_id=rust_core.TickerId(42),
            side=rust_core.Direction.Long,
            confidence=72.5,
            strategy=rust_core.StrategyId.VanguardSniper,
            kelly_fraction=0.08,
            features={"adx": 28.5, "rvol": 2.3},
        )
        assert intent.ticker_id == rust_core.TickerId(42)
        assert intent.side == rust_core.Direction.Long
        assert abs(intent.confidence - 72.5) < 1e-10
        assert intent.strategy == rust_core.StrategyId.VanguardSniper
        assert abs(intent.kelly_fraction - 0.08) < 1e-10
        assert intent.features["adx"] == 28.5
        assert intent.features["rvol"] == 2.3

    def test_nan_rejection(self):
        """NaN sanitization (H09): NaN confidence should raise ValueError."""
        with pytest.raises(ValueError, match="NaN"):
            rust_core.OrderIntent(
                ticker_id=rust_core.TickerId(1),
                side=rust_core.Direction.Long,
                confidence=float("nan"),
                strategy=rust_core.StrategyId.VanguardSniper,
                kelly_fraction=0.05,
            )

    def test_infinity_rejection(self):
        """Infinity sanitization (H09)."""
        with pytest.raises(ValueError, match="Infinity"):
            rust_core.OrderIntent(
                ticker_id=rust_core.TickerId(1),
                side=rust_core.Direction.Long,
                confidence=70.0,
                strategy=rust_core.StrategyId.VanguardSniper,
                kelly_fraction=float("inf"),
            )

    def test_kelly_clamp(self):
        """Kelly fraction clamped to [0.0, 0.20] (H57)."""
        intent = rust_core.OrderIntent(
            ticker_id=rust_core.TickerId(1),
            side=rust_core.Direction.Long,
            confidence=90.0,
            strategy=rust_core.StrategyId.VanguardSniper,
            kelly_fraction=0.50,  # Should be clamped to 0.20
        )
        assert intent.kelly_fraction == 0.20

    def test_confidence_clamp(self):
        """Confidence clamped to [0.0, 100.0]."""
        intent = rust_core.OrderIntent(
            ticker_id=rust_core.TickerId(1),
            side=rust_core.Direction.Long,
            confidence=150.0,  # Should be clamped to 100.0
            strategy=rust_core.StrategyId.VanguardSniper,
            kelly_fraction=0.05,
        )
        assert intent.confidence == 100.0

    def test_default_features(self):
        """Features default to empty dict when not provided."""
        intent = rust_core.OrderIntent(
            ticker_id=rust_core.TickerId(1),
            side=rust_core.Direction.Long,
            confidence=70.0,
            strategy=rust_core.StrategyId.VanguardSniper,
            kelly_fraction=0.05,
        )
        assert intent.features == {}


class TestRiskDecision:
    def test_roundtrip(self):
        decision = rust_core.RiskDecision(
            approved=True,
            adjusted_size=1500.0,
            regime=rust_core.RiskRegime.Normal,
            decision_timestamp_ns=1_000_000_000,
        )
        assert decision.approved is True
        assert decision.adjusted_size == 1500.0
        assert decision.regime == rust_core.RiskRegime.Normal
        assert decision.decision_timestamp_ns == 1_000_000_000

    def test_veto_reason_accessible(self):
        decision = rust_core.RiskDecision(
            approved=False,
            adjusted_size=0.0,
            regime=rust_core.RiskRegime.Halt,
            decision_timestamp_ns=0,
        )
        assert decision.reason is not None
        assert hasattr(decision.reason, "name")


class TestFillEvent:
    def test_roundtrip(self):
        fill = rust_core.FillEvent(
            order_id="test-uuid",
            ticker_id=rust_core.TickerId(42),
            filled_qty=37,
            remaining_qty=63,
            price=10.5001,  # Sub-penny (H115)
            exec_id="exec-001",
            timestamp_ns=1_000_000_000,
            commission=1.50,
        )
        assert fill.order_id == "test-uuid"
        assert fill.ticker_id == rust_core.TickerId(42)
        assert fill.filled_qty == 37
        assert fill.remaining_qty == 63
        assert abs(fill.price - 10.5001) < 1e-10
        assert fill.exec_id == "exec-001"
        assert fill.timestamp_ns == 1_000_000_000
        assert fill.commission == 1.50


class TestPositionState:
    def test_roundtrip(self):
        pos = rust_core.PositionState(
            ticker_id=rust_core.TickerId(42),
            qty=100,
            avg_entry=10.50,
            stop_price=10.00,
            entry_timestamp_ns=1_000_000_000,
            origin_order_id="test-uuid",
        )
        assert pos.ticker_id == rust_core.TickerId(42)
        assert pos.qty == 100
        assert pos.avg_entry == 10.50
        assert pos.stop_price == 10.00
        assert pos.entry_timestamp_ns == 1_000_000_000
        assert pos.origin_order_id == "test-uuid"
        # Defaults
        assert pos.unrealized_pnl == 0.0
        assert pos.realized_pnl == 0.0
        assert pos.highest_high == 10.50  # Set to avg_entry
        assert pos.total_commission == 0.0
        assert pos.trailing_rung == 0
        assert pos.state == rust_core.OrderState.Filled


class TestBrokerAck:
    def test_roundtrip(self):
        ack = rust_core.BrokerAck(
            order_id="test-uuid",
            status=rust_core.BrokerAckStatus.Accepted,
            ibkr_order_id=12345,
            timestamp_ns=1_000_000_000,
            message=None,
        )
        assert ack.order_id == "test-uuid"
        assert ack.status == rust_core.BrokerAckStatus.Accepted
        assert ack.ibkr_order_id == 12345
        assert ack.timestamp_ns == 1_000_000_000
        assert ack.message is None

    def test_with_message(self):
        ack = rust_core.BrokerAck(
            order_id="test-uuid",
            status=rust_core.BrokerAckStatus.Rejected,
            ibkr_order_id=0,
            timestamp_ns=0,
            message="Insufficient funds",
        )
        assert ack.message == "Insufficient funds"


class TestExitSignal:
    def test_roundtrip(self):
        sig = rust_core.ExitSignal(
            ticker_id=rust_core.TickerId(42),
            reason=rust_core.ExitReason.ChandelierTrailing,
            priority=rust_core.ExitPriority.ChandelierStop,
            order_type=rust_core.ExitOrderType.LimitAtStop,
            position_order_id="test-uuid",
            limit_price=10.25,
        )
        assert sig.ticker_id == rust_core.TickerId(42)
        assert sig.reason == rust_core.ExitReason.ChandelierTrailing
        assert sig.priority == rust_core.ExitPriority.ChandelierStop
        assert sig.order_type == rust_core.ExitOrderType.LimitAtStop
        assert sig.position_order_id == "test-uuid"
        assert sig.limit_price == 10.25

    def test_market_sell_no_limit(self):
        sig = rust_core.ExitSignal(
            ticker_id=rust_core.TickerId(42),
            reason=rust_core.ExitReason.HaltFlatten,
            priority=rust_core.ExitPriority.HaltFlatten,
            order_type=rust_core.ExitOrderType.MarketSell,
            position_order_id="test-uuid",
        )
        assert sig.limit_price is None
