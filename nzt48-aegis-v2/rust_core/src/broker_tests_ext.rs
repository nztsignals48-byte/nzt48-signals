//! Phase 4 acceptance tests — extended (tests 9-17).

#[cfg(test)]
mod tests {
    use crate::broker::{BackoffState, BrokerAdapter, BrokerError, BrokerEvent};
    use crate::paper_broker::{PaperBroker, PaperBrokerConfig};
    use crate::portfolio::PortfolioState;
    use crate::types::{BrokerAckStatus, OrderSide, OrderState, PositionState, TickerId};

    fn default_broker() -> PaperBroker {
        PaperBroker::new(PaperBrokerConfig::default())
    }

    fn make_position(ticker_id: u32, qty: u32, entry: f64, stop: f64) -> PositionState {
        PositionState {
            entry_timestamp_ns: 0,
            avg_entry: entry,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
            highest_high: entry,
            stop_price: stop,
            total_commission: 0.0,
            qty,
            ticker_id: TickerId(ticker_id),
            trailing_rung: 0,
            state: OrderState::Filled,
            origin_order_id: "test".to_string(),
            is_carried: false,
                mae: 0.0,
                mfe: 0.0,
                spread_at_entry_pct: 0.0,
                daily_trade_number: 0,
                entry_type: String::new(),
        }
    }

    // ── Test 9: Exponential backoff: 1s, 2s, 4s, 8s (H17) ──
    #[test]
    fn test_exponential_backoff() {
        let mut backoff = BackoffState::new(1000, 60_000);
        assert_eq!(backoff.next_delay_ms(), 1000);
        assert_eq!(backoff.next_delay_ms(), 2000);
        assert_eq!(backoff.next_delay_ms(), 4000);
        assert_eq!(backoff.next_delay_ms(), 8000);
        backoff.reset();
        assert_eq!(backoff.attempt(), 0);
        assert_eq!(backoff.next_delay_ms(), 1000);
    }

    // ── Test 10: Client ID isolation (H41) ──
    #[test]
    fn test_client_id_isolation() {
        let executioner = PaperBroker::new(PaperBrokerConfig {
            client_id: 100,
            ..Default::default()
        });
        let ouroboros = PaperBroker::new(PaperBrokerConfig {
            client_id: 200,
            ..Default::default()
        });
        assert_eq!(executioner.client_id(), 100);
        assert_eq!(ouroboros.client_id(), 200);
        assert_ne!(executioner.client_id(), ouroboros.client_id());
    }

    // ── Test 11: nextValidId persistence (H47) ──
    #[test]
    fn test_next_valid_id_persistence() {
        let mut broker = PaperBroker::new(PaperBrokerConfig {
            initial_next_valid_id: 1000,
            ..Default::default()
        });
        broker.set_time_ns(1_000_000_000);
        assert_eq!(broker.next_valid_id(), 1000);
        broker
            .submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50)
            .expect("submit");
        assert_eq!(broker.next_valid_id(), 1001);
        broker
            .submit_order("order-2", TickerId(43), OrderSide::Buy, 50, 5.00)
            .expect("submit");
        assert_eq!(broker.next_valid_id(), 1002);
        broker.disconnect();
        broker.reconnect();
        let events = broker.drain_events();
        let connected = events
            .iter()
            .find(|e| matches!(e, BrokerEvent::Connected { .. }));
        if let Some(BrokerEvent::Connected { next_valid_id }) = connected {
            assert_eq!(*next_valid_id, 1002);
        } else {
            panic!("Expected Connected event");
        }
    }

    // ── Test 12: PendingCancel state (H54) ──
    #[test]
    fn test_pending_cancel_state() {
        let mut broker = default_broker();
        broker.set_time_ns(1_000_000_000);
        broker
            .submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50)
            .expect("submit");
        let _ = broker.drain_events();
        broker.cancel_order("order-1").expect("cancel");
        let events = broker.drain_events();
        assert_eq!(events.len(), 2);
        if let BrokerEvent::Ack { status, .. } = &events[0] {
            assert_eq!(*status, BrokerAckStatus::PendingCancel);
        } else {
            panic!("Expected PendingCancel Ack");
        }
        if let BrokerEvent::Ack { status, .. } = &events[1] {
            assert_eq!(*status, BrokerAckStatus::Cancelled);
        } else {
            panic!("Expected Cancelled Ack");
        }
        let open = broker.request_open_orders().expect("open orders");
        assert_eq!(open.len(), 0);
    }

    // ── Test 13: Phantom fill — cancel sent, fill arrives 50ms later (H55) ──
    #[test]
    fn test_phantom_fill_after_cancel() {
        let mut broker = default_broker();
        broker.set_time_ns(1_000_000_000);
        broker
            .submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50)
            .expect("submit");
        let _ = broker.drain_events();
        broker.cancel_order("order-1").expect("cancel");
        broker
            .inject_phantom_fill("order-1", 100, 10.50)
            .expect("phantom fill");
        let events = broker.drain_events();
        assert_eq!(events.len(), 3); // PendingCancel + Cancelled + Fill
        let has_fill = events.iter().any(|e| {
            matches!(
                e,
                BrokerEvent::Fill {
                    filled_qty: 100,
                    ..
                }
            )
        });
        assert!(has_fill, "Phantom fill must be accepted");
        let positions = broker.request_positions().expect("positions");
        assert_eq!(positions.len(), 1);
        assert_eq!(positions[0].qty, 100);
    }

    // ── Test 14: Disconnect rejects submissions ──
    #[test]
    fn test_disconnect_rejects_submissions() {
        let mut broker = default_broker();
        broker.set_time_ns(1_000_000_000);
        broker.disconnect();
        let result = broker.submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50);
        assert!(matches!(result, Err(BrokerError::NotConnected)));
    }

    // ── Test 15: Full lifecycle with PortfolioState update ──
    #[test]
    fn test_lifecycle_updates_portfolio() {
        let mut broker = default_broker();
        broker.set_time_ns(1_000_000_000);
        broker
            .submit_order("order-1", TickerId(42), OrderSide::Buy, 100, 10.50)
            .expect("submit");
        broker.generate_fills("order-1").expect("fills");
        let events = broker.drain_events();
        let mut portfolio = PortfolioState::new(100_000.0);
        for event in &events {
            if let BrokerEvent::Fill {
                ticker_id,
                filled_qty,
                remaining_qty,
                price,
                ..
            } = event
                && *remaining_qty == 0
            {
                let pos = make_position(ticker_id.0, *filled_qty, *price, price * 0.95);
                portfolio.add_position(pos);
            }
        }
        assert_eq!(portfolio.filled_count(), 1);
        let pos = portfolio.get_position(&TickerId(42));
        assert!(pos.is_some());
        assert_eq!(pos.expect("position exists").qty, 100);
    }

    // ── Test 17: WAL NextValidId event (H47) ──
    #[test]
    fn test_wal_next_valid_id_event() {
        use crate::types::WalPayload;
        use crate::wal_writer::make_wal_event;
        let event = make_wal_event(1_000_000, WalPayload::NextValidId { id: 42 });
        let json = serde_json::to_string(&event).expect("serialize");
        assert!(json.contains("NextValidId"));
        assert!(json.contains("42"));
    }

    // ── Test 18: Split quantity helper ──
    #[test]
    fn test_split_qty_via_partial_fills() {
        let config = PaperBrokerConfig {
            partial_fill_enabled: true,
            partial_fill_chunks: 4,
            ..Default::default()
        };
        let mut broker = PaperBroker::new(config);
        broker.set_time_ns(1_000_000_000);
        broker
            .submit_order("order-1", TickerId(1), OrderSide::Buy, 10, 1.0)
            .expect("submit");
        broker.generate_fills("order-1").expect("fills");
        let events = broker.drain_events();
        // 1 Ack + 4 partial fills (10 / 4 = chunks of 3,3,2,2)
        assert_eq!(events.len(), 5);
        let total: u32 = events
            .iter()
            .filter_map(|e| {
                if let BrokerEvent::Fill { filled_qty, .. } = e {
                    Some(*filled_qty)
                } else {
                    None
                }
            })
            .sum();
        assert_eq!(total, 10);
    }
}
