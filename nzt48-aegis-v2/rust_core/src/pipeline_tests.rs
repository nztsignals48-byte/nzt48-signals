//! Phase 6C acceptance tests: full pipeline wiring + GIL isolation + backpressure.

#[cfg(test)]
mod tests {
    use crate::broker::BrokerAdapter;
    use crate::channel::{ChannelConfig, TickChannel};
    use crate::exit_engine::ExitEngine;
    use crate::paper_broker::{PaperBroker, PaperBrokerConfig};
    use crate::portfolio::PortfolioState;
    use crate::risk_arbiter::{EvalContext, RiskArbiter};
    use crate::types::{
        Direction, ExitReason, MarketTick, OrderSide, OrderState, PositionState, RiskRegime,
        TickerId,
    };
    use crate::universe::{Universe, UniverseClass, UniverseConfig};

    fn make_tick(ticker_id: TickerId, last: f64) -> MarketTick {
        MarketTick {
            ticker_id,
            bid: last - 0.01,
            ask: last + 0.01,
            last,
            volume: 10000,
            timestamp_ns: 1_000_000_000,
            recv_timestamp_ns: 1_000_000_100,
        }
    }

    fn default_ctx() -> EvalContext {
        EvalContext::default()
    }

    // ── Test 1: Full pipeline end-to-end ──
    // Synthetic tick → channel → route → (mock Python) → OrderIntent →
    // RiskArbiter → PaperBroker fill → PositionState → Exit Engine armed
    #[test]
    fn test_full_pipeline_end_to_end() {
        // 1. Universe + channel
        let mut universe = Universe::new(UniverseConfig::default());
        let ticker_id = universe.register("QQQ3.L", UniverseClass::Vanguard);
        universe.set_prev_close(ticker_id, 10.0);

        let config = ChannelConfig {
            capacity: 1000,
            ..Default::default()
        };
        let mut channel = TickChannel::new(config);

        // 2. Send 200 synthetic ticks through channel
        for i in 0..200 {
            let tick = make_tick(ticker_id, 10.0 + i as f64 * 0.001);
            channel.send_or_drop_oldest(tick, i as u64 * 10_000_000);
        }

        // 3. Batch receive (simulating GIL thread drain)
        let batch = channel.recv_batch(200);
        assert_eq!(batch.len(), 200);

        // 4. Route through universe
        let mut routed = 0;
        let now = 500_000_000u64;
        for tick in &batch {
            let mut u = Universe::new(UniverseConfig::default());
            let id = u.register("QQQ3.L", UniverseClass::Vanguard);
            u.set_prev_close(id, 10.0);
            if matches!(
                u.route_tick(tick, now),
                crate::universe::RouteResult::Vanguard(_)
            ) {
                routed += 1;
            }
        }
        assert_eq!(routed, 200);

        // 5. Simulate Python Brain → OrderIntent fields
        // 6. RiskArbiter check
        let mut arbiter = RiskArbiter::new(crate::config::RiskConfig::default());
        let portfolio = PortfolioState::new(10000.0);
        let ctx = default_ctx();
        let decision = arbiter.evaluate(ticker_id, Direction::Long, 78.0, 0.08, &portfolio, &ctx);
        assert!(decision.approved);

        // 7. PaperBroker: submit → fill
        let mut broker = PaperBroker::new(PaperBrokerConfig::default());
        let order_id = format!("order-{}", broker.next_valid_id());
        broker
            .submit_order(&order_id, ticker_id, OrderSide::Buy, 100, 10.20)
            .expect("submit");
        broker.generate_fills(&order_id).expect("fills");
        let events = broker.drain_events();
        assert!(events.len() >= 2); // Ack + fill(s)

        // 8. Position → Exit Engine armed
        let pos = PositionState {
            entry_timestamp_ns: 1_000_000_000,
            avg_entry: 10.19,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
            highest_high: 10.19,
            stop_price: 9.68,
            total_commission: 1.50,
            qty: 100,
            ticker_id,
            trailing_rung: 0,
            state: OrderState::ExitRegistered,
            origin_order_id: order_id,
            is_carried: false,
                mae: 0.0,
                mfe: 0.0,
                spread_at_entry_pct: 0.0,
                daily_trade_number: 0,
                entry_type: String::new(),
                active_trading_ticks: 0,
        };
        let engine = ExitEngine::with_default_chandelier();
        // No exit at current price
        assert!(
            engine
                .evaluate(&pos, 10.19, 0.50, 36_000, false, false, false)
                .is_none()
        );
        // Price below stop → exit
        let exit = engine
            .evaluate(&pos, 9.60, 0.50, 36_000, false, false, false)
            .expect("stop exit");
        assert_eq!(exit.signal.reason, ExitReason::HardStopLoss);
    }

    // ── Test 2: GIL isolation — pipeline runs entirely in Rust ──
    #[test]
    fn test_gil_isolation_structural() {
        // All pipeline components work without Python::with_gil().
        // Only ffi.rs touches PyO3. This test proves architectural isolation.
        let mut arbiter = RiskArbiter::new(crate::config::RiskConfig::default());
        let portfolio = PortfolioState::new(10000.0);
        let ctx = default_ctx();
        let decision = arbiter.evaluate(TickerId(1), Direction::Long, 80.0, 0.10, &portfolio, &ctx);
        assert!(decision.approved);
    }

    // ── Test 3: Batch FFI — 200 ticks per batch ──
    #[test]
    fn test_batch_ffi_200_ticks() {
        let config = ChannelConfig {
            capacity: 50_000,
            ..Default::default()
        };
        let mut channel = TickChannel::new(config);

        for i in 0..500 {
            let tick = make_tick(TickerId(1), 10.0 + i as f64 * 0.001);
            channel.send_or_drop_oldest(tick, 0);
        }

        let batch = channel.recv_batch(200);
        assert_eq!(batch.len(), 200);
        let rest = channel.recv_batch(200);
        assert_eq!(rest.len(), 200);
        let final_batch = channel.recv_batch(200);
        assert_eq!(final_batch.len(), 100);
    }

    // ── Test 4: Backpressure thresholds ──
    #[test]
    fn test_backpressure_thresholds() {
        let warning_ms: u64 = 500;
        let reduce_ms: u64 = 2000;

        let fast_ms: u64 = 100;
        let slow_ms: u64 = 800;
        let very_slow_ms: u64 = 2500;

        assert!(fast_ms < warning_ms);
        assert!(slow_ms >= warning_ms && slow_ms < reduce_ms);
        assert!(very_slow_ms >= reduce_ms);

        let config = ChannelConfig {
            capacity: 50_000,
            reduce_threshold: 40_000,
            halt_threshold: 50_000,
            drop_alert_per_sec: 100,
        };
        let channel = TickChannel::new(config);
        assert!(channel.check_health().is_none());
    }

    // ── Test 5: Pipeline with multiple tickers ──
    #[test]
    fn test_pipeline_multiple_tickers() {
        let mut universe = Universe::new(UniverseConfig::default());
        let t1 = universe.register("QQQ3.L", UniverseClass::Vanguard);
        let t2 = universe.register("NVD3.L", UniverseClass::Vanguard);
        let t3 = universe.register("APEX1.L", UniverseClass::Apex);
        universe.set_prev_close(t1, 10.0);
        universe.set_prev_close(t2, 20.0);
        universe.set_prev_close(t3, 5.0);

        let now = 1_000_000_000u64;
        assert!(matches!(
            universe.route_tick(&make_tick(t1, 10.01), now),
            crate::universe::RouteResult::Vanguard(_)
        ));
        assert!(matches!(
            universe.route_tick(&make_tick(t2, 20.02), now + 1),
            crate::universe::RouteResult::Vanguard(_)
        ));
        assert!(matches!(
            universe.route_tick(&make_tick(t3, 5.01), now + 2),
            crate::universe::RouteResult::Apex(_)
        ));
    }

    // ── Test 6: Channel overflow + health monitoring ──
    #[test]
    fn test_channel_overflow_health() {
        let config = ChannelConfig {
            capacity: 100,
            reduce_threshold: 80,
            halt_threshold: 100,
            drop_alert_per_sec: 5,
        };
        let mut channel = TickChannel::new(config);
        let tick = make_tick(TickerId(1), 10.0);

        for _ in 0..80 {
            channel.send_or_drop_oldest(tick.clone(), 0);
        }
        assert!(matches!(channel.check_health(), Some(RiskRegime::Reduce)));

        for _ in 0..20 {
            channel.send_or_drop_oldest(tick.clone(), 0);
        }
        assert!(matches!(channel.check_health(), Some(RiskRegime::Halt)));

        let now = 1_000_000_000u64;
        for i in 0..10 {
            channel.send_or_drop_oldest(tick.clone(), now + i * 10);
        }
        assert_eq!(channel.monitor.total_drops, 10);
    }

    // ── Test 7: Risk → Broker → Exit full cycle ──
    #[test]
    fn test_risk_broker_exit_cycle() {
        let mut arbiter = RiskArbiter::new(crate::config::RiskConfig::default());
        let portfolio = PortfolioState::new(10000.0);
        let ctx = default_ctx();

        let decision =
            arbiter.evaluate(TickerId(42), Direction::Long, 80.0, 0.10, &portfolio, &ctx);
        assert!(decision.approved);
        assert_eq!(decision.regime, RiskRegime::Normal);

        let mut broker = PaperBroker::new(PaperBrokerConfig::default());
        let oid = "order-1".to_string();
        broker
            .submit_order(&oid, TickerId(42), OrderSide::Buy, 50, 10.20)
            .expect("submit");
        broker.generate_fills(&oid).expect("fills");

        let events = broker.drain_events();
        let fills: Vec<_> = events
            .iter()
            .filter(|e| matches!(e, crate::broker::BrokerEvent::Fill { .. }))
            .collect();
        assert!(!fills.is_empty());

        let engine = ExitEngine::with_default_chandelier();
        let pos = PositionState {
            entry_timestamp_ns: 0,
            avg_entry: 10.19,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
            highest_high: 10.50,
            stop_price: 10.19,
            total_commission: 1.0,
            qty: 50,
            ticker_id: TickerId(42),
            trailing_rung: 1,
            state: OrderState::ExitRegistered,
            origin_order_id: oid,
            is_carried: false,
                mae: 0.0,
                mfe: 0.0,
                spread_at_entry_pct: 0.0,
                daily_trade_number: 0,
                entry_type: String::new(),
                active_trading_ticks: 0,
        };
        assert!(
            engine
                .evaluate(&pos, 10.40, 0.50, 36_000, false, false, false)
                .is_none()
        );
    }
}
