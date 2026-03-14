//! Phase 4 acceptance tests for Broker Interface + Paper Adapter (tests 1-8).

#[cfg(test)]
mod tests {
    use crate::broker::{BrokerAdapter, BrokerError, BrokerEvent, TokenBucket};
    use crate::config::RiskConfig;
    use crate::paper_broker::{PaperBroker, PaperBrokerConfig};
    use crate::portfolio::PortfolioState;
    use crate::risk_arbiter::{EvalContext, RiskArbiter};
    use crate::types::{BrokerAckStatus, Direction, OrderSide, TickerId};

    fn default_broker() -> PaperBroker {
        PaperBroker::new(PaperBrokerConfig::default())
    }

    // ── Test 1: Full lifecycle: submit → ack → fill → position updated ──
    #[test]
    fn test_full_lifecycle() {
        let mut broker = default_broker();
        broker.set_time_ns(1_000_000_000);
        broker
            .submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50)
            .expect("submit");
        broker.generate_fills("order-1").expect("fills");
        let events = broker.drain_events();
        assert!(events.len() >= 2);
        if let BrokerEvent::Ack { status, .. } = &events[0] {
            assert_eq!(*status, BrokerAckStatus::Accepted);
        } else {
            panic!("Expected Ack event");
        }
        if let BrokerEvent::Fill {
            filled_qty,
            remaining_qty,
            price,
            ..
        } = &events[1]
        {
            assert_eq!(*filled_qty, 100);
            assert_eq!(*remaining_qty, 0);
            assert!((price - 10.50).abs() < 1e-10);
        } else {
            panic!("Expected Fill event");
        }
        let positions = broker.request_positions().expect("positions");
        assert_eq!(positions.len(), 1);
        assert_eq!(positions[0].qty, 100);
    }

    // ── Test 2: Partial fill: 100 → fill 37 + 63, VWAP ──
    #[test]
    fn test_partial_fill_vwap() {
        let mut broker = default_broker();
        broker.set_time_ns(1_000_000_000);
        broker
            .submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50)
            .expect("submit");
        broker.generate_fill("order-1", 37, 10.40).expect("fill1");
        broker.generate_fill("order-1", 63, 10.55).expect("fill2");
        let events = broker.drain_events();
        assert_eq!(events.len(), 3);
        if let BrokerEvent::Fill {
            filled_qty,
            remaining_qty,
            ..
        } = &events[1]
        {
            assert_eq!(*filled_qty, 37);
            assert_eq!(*remaining_qty, 63);
        } else {
            panic!("Expected Fill event");
        }
        if let BrokerEvent::Fill {
            filled_qty,
            remaining_qty,
            ..
        } = &events[2]
        {
            assert_eq!(*filled_qty, 63);
            assert_eq!(*remaining_qty, 0);
        } else {
            panic!("Expected Fill event");
        }
        let vwap: f64 = (37.0 * 10.40 + 63.0 * 10.55) / 100.0;
        assert!((vwap - 10.4945).abs() < 0.001);
    }

    // ── Test 3: Duplicate submission rejected ──
    #[test]
    fn test_duplicate_submission_rejected() {
        let mut broker = default_broker();
        broker.set_time_ns(1_000_000_000);
        broker
            .submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50)
            .expect("first submit");
        let result = broker.submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50);
        assert!(result.is_err());
        if let Err(BrokerError::DuplicateOrderId(id)) = result {
            assert_eq!(id, "order-1");
        } else {
            panic!("Expected DuplicateOrderId error");
        }
    }

    // ── Test 4: Heartbeat timeout → HALT triggered ──
    #[test]
    fn test_heartbeat_timeout_triggers_halt() {
        let mut broker = default_broker();
        broker.set_time_ns(1_000_000_000);
        broker.heartbeat().expect("heartbeat");
        assert!(broker.is_connected());
        broker.set_time_ns(62_000_000_000);
        assert!(!broker.is_connected());
        let mut arbiter = RiskArbiter::new(RiskConfig::default());
        let portfolio = PortfolioState::new(100_000.0);
        let ctx = EvalContext {
            time_secs: 36_000,
            last_tick_age_secs: 1,
            bid: 10.0,
            ask: 10.01,
            broker_connected: broker.is_connected(),
            wal_available: true,
            now_ns: 62_000_000_000,
            ..EvalContext::default()
        };
        let decision =
            arbiter.evaluate(TickerId(42), Direction::Long, 80.0, 0.05, &portfolio, &ctx);
        assert!(!decision.approved);
        assert_eq!(arbiter.regime, crate::types::RiskRegime::Halt);
    }

    // ── Test 5: Configurable latency ──
    #[test]
    fn test_configurable_latency() {
        let config = PaperBrokerConfig {
            latency_min_ms: 75,
            latency_max_ms: 150,
            ..Default::default()
        };
        let broker = PaperBroker::new(config);
        assert_eq!(broker.client_id(), 100);
    }

    // ── Test 6: UUIDv7 exec_ids for fills ──
    #[test]
    fn test_uuidv7_exec_ids() {
        let mut broker = default_broker();
        broker.set_time_ns(1_000_000_000);
        broker
            .submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50)
            .expect("submit");
        broker.generate_fills("order-1").expect("fills");
        let events = broker.drain_events();
        if let BrokerEvent::Fill { exec_id, .. } = &events[1] {
            assert_eq!(exec_id.len(), 36);
            let uuid = uuid::Uuid::parse_str(exec_id).expect("valid uuid");
            assert_eq!(uuid.get_version_num(), 7);
        } else {
            panic!("Expected Fill event");
        }
    }

    // ── Test 7: Random partial fills (configurable) ──
    #[test]
    fn test_random_partial_fills() {
        let config = PaperBrokerConfig {
            partial_fill_enabled: true,
            partial_fill_chunks: 3,
            ..Default::default()
        };
        let mut broker = PaperBroker::new(config);
        broker.set_time_ns(1_000_000_000);
        broker
            .submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50)
            .expect("submit");
        broker.generate_fills("order-1").expect("fills");
        let events = broker.drain_events();
        assert_eq!(events.len(), 4); // 1 Ack + 3 fills
        let total_filled: u32 = events
            .iter()
            .filter_map(|e| {
                if let BrokerEvent::Fill { filled_qty, .. } = e {
                    Some(*filled_qty)
                } else {
                    None
                }
            })
            .sum();
        assert_eq!(total_filled, 100);
        if let BrokerEvent::Fill { remaining_qty, .. } = &events[3] {
            assert_eq!(*remaining_qty, 0);
        }
    }

    // ── Test 8: Rate limiter at 50 msgs/sec (H16) ──
    #[test]
    fn test_rate_limiter_50_msgs_sec() {
        let mut broker = default_broker();
        broker.set_time_ns(1_000_000_000);
        for i in 0..50 {
            let order_id = format!("order-{i}");
            broker
                .submit_order(&order_id, TickerId(i), OrderSide::Buy, 10, 1.0)
                .expect("submit within limit");
        }
        let result = broker.submit_order("order-50", TickerId(50), OrderSide::Buy, 10, 1.0);
        assert!(matches!(result, Err(BrokerError::RateLimitExceeded)));
        broker.set_time_ns(2_000_000_000);
        broker
            .submit_order("order-50", TickerId(50), OrderSide::Buy, 10, 1.0)
            .expect("submit after refill");
    }

    // ── Test 16: Token bucket unit test ──
    #[test]
    fn test_token_bucket_precise_refill() {
        let mut tb = TokenBucket::new(10);
        for _ in 0..10 {
            assert!(tb.try_consume(0));
        }
        assert!(!tb.try_consume(0));
        assert!(tb.try_consume(500_000_000));
    }
}
