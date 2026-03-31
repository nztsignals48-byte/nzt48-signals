//! Proptest: random chaotic state transitions → no panics (H91).

#[cfg(test)]
mod tests {
    use proptest::prelude::*;

    use crate::config::RiskConfig;
    use crate::portfolio::PortfolioState;
    use crate::risk_arbiter::{EvalContext, RiskArbiter};
    use crate::types::*;

    fn arb_direction() -> impl Strategy<Value = Direction> {
        prop_oneof![Just(Direction::Long), Just(Direction::Short),]
    }

    fn arb_regime() -> impl Strategy<Value = RiskRegime> {
        prop_oneof![
            Just(RiskRegime::Normal),
            Just(RiskRegime::Reduce),
            Just(RiskRegime::Flatten),
            Just(RiskRegime::Halt),
        ]
    }

    fn arb_eval_context() -> impl Strategy<Value = EvalContext> {
        (
            0u32..86400,            // time_secs
            0u64..300,              // last_tick_age_secs
            0.01f64..1000.0,        // bid
            0.01f64..1000.0,        // ask
            any::<bool>(),          // broker_connected
            any::<bool>(),          // wal_available
            1_000_000u64..u64::MAX, // now_ns
        )
            .prop_map(|(time_secs, age, bid, ask, broker, wal, now)| EvalContext {
                time_secs,
                last_tick_age_secs: age,
                bid,
                ask: ask.max(bid), // ask >= bid
                broker_connected: broker,
                wal_available: wal,
                now_ns: now,
                ..EvalContext::default()
            })
    }

    proptest! {
        /// Random evaluate calls MUST NEVER panic.
        #[test]
        fn proptest_no_panics(
            ticker_id in 0u32..100,
            direction in arb_direction(),
            confidence in -10.0f64..200.0,
            kelly in -0.5f64..1.0,
            regime in arb_regime(),
            equity in 0.0f64..1_000_000.0,
            ctx in arb_eval_context(),
            num_positions in 0u32..5,
        ) {
            let mut arb = RiskArbiter::new(RiskConfig::default());
            arb.escalate(regime);

            let mut portfolio = PortfolioState::new(equity);
            for i in 0..num_positions {
                let pos = PositionState {
                    entry_timestamp_ns: 0,
                    avg_entry: 10.0 + i as f64,
                    unrealized_pnl: 0.0,
                    realized_pnl: 0.0,
                    highest_high: 10.0 + i as f64,
                    stop_price: 9.0 + i as f64,
                    total_commission: 0.0,
                    qty: 100,
                    ticker_id: TickerId(100 + i),
                    trailing_rung: 0,
                    state: OrderState::Filled,
                    origin_order_id: format!("order-{i}"),
                    is_carried: false,
                mae: 0.0,
                mfe: 0.0,
                spread_at_entry_pct: 0.0,
                daily_trade_number: 0,
                entry_type: String::new(),
                active_trading_ticks: 0,
                max_hold_hours: None,
                exit_urgency_ramp_hours: None,
                suggested_initial_stop_atr_mult: None,
                suggested_rung3_atr: None,
                min_profit_target_pct: None,
                partial_exits_done: 0,
                };
                portfolio.add_position(pos);
            }

            // This must not panic regardless of inputs
            let decision = arb.evaluate(
                TickerId(ticker_id),
                direction,
                confidence,
                kelly,
                &portfolio,
                &ctx,
            );

            // Basic sanity: rejected → size 0, approved → finite size
            if !decision.approved {
                prop_assert!((decision.adjusted_size - 0.0).abs() < f64::EPSILON);
            } else {
                prop_assert!(decision.adjusted_size.is_finite());
            }
        }

        /// Random state transitions never panic.
        #[test]
        fn proptest_state_transitions(
            regimes in proptest::collection::vec(arb_regime(), 1..20),
        ) {
            let mut arb = RiskArbiter::new(RiskConfig::default());
            for r in &regimes {
                arb.escalate(*r);
            }
            // Regime should be the maximum of all escalated values
            let max_regime = regimes.iter().copied().max().unwrap_or(RiskRegime::Normal);
            prop_assert_eq!(arb.regime, max_regime);

            // Recovery
            arb.manual_clear_halt();
            arb.clear_flatten();
            arb.clear_reduce();
        }
    }
}
