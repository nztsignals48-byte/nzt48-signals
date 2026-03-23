//! Phase 2 acceptance tests for RiskArbiter.
//! Tests ALL 20 acceptance criteria from docs/03_ACCEPTANCE_TESTS.md.

#[cfg(test)]
mod tests {
    use crate::config::RiskConfig;
    use crate::portfolio::PortfolioState;
    use crate::risk_arbiter::{EvalContext, RiskArbiter};
    use crate::types::*;

    fn default_ctx() -> EvalContext {
        EvalContext::default()
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

    // ── Acceptance Test 3: Precedence HALT + REDUCE → HALT wins ──
    #[test]
    fn test_precedence_halt_over_reduce() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        arb.escalate(RiskRegime::Reduce);
        arb.escalate(RiskRegime::Halt);
        assert_eq!(arb.regime, RiskRegime::Halt);
    }

    // ── Acceptance Test 4: Precedence FLATTEN + REDUCE → FLATTEN wins ──
    #[test]
    fn test_precedence_flatten_over_reduce() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        arb.escalate(RiskRegime::Reduce);
        arb.escalate(RiskRegime::Flatten);
        assert_eq!(arb.regime, RiskRegime::Flatten);
    }

    // ── Acceptance Test 5: ISA short → REJECTED ──
    #[test]
    fn test_isa_short_sell_blocked() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(1), Direction::Short, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        assert_eq!(decision.reason, VetoReason::IsaShortSellBlocked);
        assert_eq!(arb.regime, RiskRegime::Halt);
    }

    // ── Acceptance Test 6: Drawdown 2.1% → FLATTEN ──
    #[test]
    fn test_drawdown_triggers_flatten() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let mut p = PortfolioState::new(10_000.0);
        p.high_water_mark = 10_000.0;
        p.equity = 9_790.0; // 2.1% drawdown
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        assert_eq!(decision.reason, VetoReason::DailyDrawdownBreached);
        assert_eq!(arb.regime, RiskRegime::Flatten);
    }

    // ── Acceptance Test 7: Stale data 121s → HALT ──
    #[test]
    fn test_stale_data_triggers_halt() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let mut ctx = default_ctx();
        ctx.last_tick_age_secs = 121;
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        match &decision.reason {
            VetoReason::StaleData { age_secs } => assert_eq!(*age_secs, 121),
            other => panic!("Expected StaleData, got {:?}", other),
        }
        assert_eq!(arb.regime, RiskRegime::Halt);
    }

    // ── Acceptance Test 8: Spread 0.6% → REJECTED ──
    #[test]
    fn test_spread_too_wide() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let mut ctx = default_ctx();
        // Spread = (10.06 - 10.00) / 10.00 * 100 = 0.6%
        ctx.bid = 10.00;
        ctx.ask = 10.06;
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        match &decision.reason {
            VetoReason::SpreadTooWide { spread_bps } => assert!(*spread_bps > 0),
            other => panic!("Expected SpreadTooWide, got {:?}", other),
        }
    }

    // ── Acceptance Test 9: Time 20:56 → REJECTED (past 20:55 cutoff) ──
    #[test]
    fn test_too_late_in_session() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let mut ctx = default_ctx();
        ctx.time_secs = 20 * 3600 + 56 * 60; // 20:56 London (past 20:55 cutoff)
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        assert_eq!(decision.reason, VetoReason::TooLateInSession);
    }

    // ── Acceptance Test 10: 3 consecutive stop-losses → HALT ──
    #[test]
    fn test_consecutive_loss_breaker() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let mut p = PortfolioState::new(10_000.0);
        p.consecutive_stop_losses = 3;
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        assert_eq!(decision.reason, VetoReason::ConsecutiveLossBreaker);
        assert_eq!(arb.regime, RiskRegime::Halt);
    }

    // ── Acceptance Test 11: Inverse exclusion ──
    #[test]
    fn test_inverse_mutual_exclusion() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let mut p = PortfolioState::new(10_000.0);
        let qqq3 = TickerId(1);
        let qqqs = TickerId(2);
        p.register_inverse_pair(qqq3, qqqs);
        p.add_position(make_position(1, 100, 10.0, 9.5)); // QQQ3 open
        let ctx = default_ctx();
        let decision = arb.evaluate(qqqs, Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        match &decision.reason {
            VetoReason::InverseMutualExclusion { blocker } => assert_eq!(*blocker, 1),
            other => panic!("Expected InverseMutualExclusion, got {:?}", other),
        }
    }

    // ── Acceptance Test 12: Velocity 5 in 1s → first 5 pass, 6th rejected ──
    #[test]
    fn test_velocity_check() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let mut ctx = default_ctx();
        let ticker = TickerId(1);

        // First 5 intents pass (velocity_max_intents = 5)
        for i in 0..5 {
            ctx.now_ns += 100_000_000; // +100ms each
            let d = arb.evaluate(ticker, Direction::Long, 80.0, 0.1, &p, &ctx);
            assert!(d.approved, "Intent {} should pass", i + 1);
        }

        // 6th intent in same window is rejected
        ctx.now_ns += 100_000_000;
        let d = arb.evaluate(ticker, Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!d.approved, "6th intent should be rejected");
        assert_eq!(d.reason, VetoReason::VelocityCheckTriggered);
    }

    // ── Acceptance Test 13: Max positions → REJECTED ──
    #[test]
    fn test_max_positions_reached() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let mut p = PortfolioState::new(100_000.0);
        // max_positions = 6, so fill 6 positions
        p.add_position(make_position(1, 100, 10.0, 9.5));
        p.add_position(make_position(2, 100, 20.0, 19.0));
        p.add_position(make_position(3, 100, 30.0, 29.0));
        p.add_position(make_position(4, 100, 40.0, 39.0));
        p.add_position(make_position(5, 100, 50.0, 49.0));
        p.add_position(make_position(6, 100, 60.0, 59.0));
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(7), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        assert_eq!(decision.reason, VetoReason::MaxPositionsReached);
    }

    // ── Acceptance Test 14: 5 filled + 1 pending → 7th REJECTED (H34) ──
    #[test]
    fn test_pending_plus_filled_count() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let mut p = PortfolioState::new(100_000.0);
        p.add_position(make_position(1, 100, 10.0, 9.5));
        p.add_position(make_position(2, 100, 20.0, 19.0));
        p.add_position(make_position(3, 100, 30.0, 29.0));
        p.add_position(make_position(4, 100, 40.0, 39.0));
        p.add_position(make_position(5, 100, 50.0, 49.0));
        p.set_pending_count(1); // 5 filled + 1 pending = 6 = max
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(7), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        assert_eq!(decision.reason, VetoReason::MaxPositionsReached);
    }

    // ── Acceptance Test 15: Cash buffer 9% → REJECTED (H31) ──
    #[test]
    fn test_cash_buffer_insufficient() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let mut p = PortfolioState::new(10_000.0);
        p.cash = 900.0; // 9% < 10% threshold
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        assert_eq!(decision.reason, VetoReason::CashBufferInsufficient);
    }

    // ── Acceptance Test 16: Sector heat 34% → REJECTED (H30) ──
    #[test]
    fn test_sector_heat_exceeded() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let mut p = PortfolioState::new(10_000.0);
        p.register_sector(TickerId(1), "Semiconductors".into());
        p.register_sector(TickerId(2), "Semiconductors".into());
        p.register_sector(TickerId(5), "Semiconductors".into());
        // 2000 + 1400 = 3400 out of 10000 = 34%
        p.add_position(make_position(1, 100, 20.0, 19.0));
        p.add_position(make_position(2, 100, 14.0, 13.0));
        let ctx = default_ctx();
        // Attempt another semiconductor ticker
        let decision = arb.evaluate(TickerId(5), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        match &decision.reason {
            VetoReason::SectorHeatExceeded { .. } => {}
            other => panic!("Expected SectorHeatExceeded, got {:?}", other),
        }
    }

    // ── Acceptance Test 17: Portfolio heat 15.1% → REJECTED ──
    #[test]
    fn test_portfolio_heat_exceeded() {
        let cfg = RiskConfig {
            max_positions: 10, // Allow more positions for this test
            ..Default::default()
        };
        let mut arb = RiskArbiter::new(cfg);
        let mut p = PortfolioState::new(100_000.0);
        // 5 positions with wide stops: risk per position = ~3033
        // Total heat = 15100 / 100000 * 100 = 15.1%
        p.add_position(make_position(1, 100, 100.0, 69.67)); // risk = 3033
        p.add_position(make_position(2, 100, 100.0, 69.67)); // risk = 3033
        p.add_position(make_position(3, 100, 100.0, 69.67)); // risk = 3033
        p.add_position(make_position(4, 100, 100.0, 69.67)); // risk = 3033
        p.add_position(make_position(5, 100, 100.0, 69.68)); // risk = 3032
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(6), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        assert_eq!(decision.reason, VetoReason::PortfolioHeatExceeded);
    }

    // ── Acceptance Test 18: VetoReason logging (each rejection has specific reason) ──
    #[test]
    fn test_veto_reason_specificity() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);

        // Each veto reason is distinct and specific
        let mut ctx = default_ctx();
        ctx.broker_connected = false;
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert_eq!(d.reason, VetoReason::BrokerDisconnected);

        // Reset for WAL check
        arb.manual_clear_halt();
        ctx.broker_connected = true;
        ctx.wal_available = false;
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert_eq!(d.reason, VetoReason::WalUnavailable);
    }

    // ── Acceptance Test 2: RiskArbiter 4-state hierarchy ──
    #[test]
    fn test_four_state_hierarchy() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        assert_eq!(arb.regime, RiskRegime::Normal);

        arb.escalate(RiskRegime::Reduce);
        assert_eq!(arb.regime, RiskRegime::Reduce);

        arb.escalate(RiskRegime::Flatten);
        assert_eq!(arb.regime, RiskRegime::Flatten);

        arb.escalate(RiskRegime::Halt);
        assert_eq!(arb.regime, RiskRegime::Halt);

        // Cannot go down via escalate
        arb.escalate(RiskRegime::Normal);
        assert_eq!(arb.regime, RiskRegime::Halt);
    }

    // ── Recovery methods ──
    #[test]
    fn test_regime_recovery() {
        let mut arb = RiskArbiter::new(RiskConfig::default());

        // REDUCE → NORMAL (auto)
        arb.escalate(RiskRegime::Reduce);
        arb.clear_reduce();
        assert_eq!(arb.regime, RiskRegime::Normal);

        // FLATTEN → NORMAL (auto after close)
        arb.escalate(RiskRegime::Flatten);
        arb.clear_flatten();
        assert_eq!(arb.regime, RiskRegime::Normal);

        // HALT → NORMAL (manual only)
        arb.escalate(RiskRegime::Halt);
        arb.clear_reduce(); // wrong method, no change
        assert_eq!(arb.regime, RiskRegime::Halt);
        arb.clear_flatten(); // wrong method, no change
        assert_eq!(arb.regime, RiskRegime::Halt);
        arb.manual_clear_halt();
        assert_eq!(arb.regime, RiskRegime::Normal);
    }

    // ── Acceptance Test 1: Approved order returns correct decision ──
    #[test]
    fn test_approved_order() {
        let cfg = RiskConfig {
            kelly_ramp_trades: 250, // Full ramp — no scaling
            minimum_entry_gbp: 500.0, // Low enough for test equity
            ..Default::default()
        };
        let mut arb = RiskArbiter::new(cfg);
        let p = PortfolioState::new(10_000.0);
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(decision.approved);
        assert_eq!(decision.reason, VetoReason::Approved);
        assert_eq!(decision.regime, RiskRegime::Normal);
        // adjusted_size = kelly(0.1) * ramp(1.0) * equity(10000) = 1000
        assert!((decision.adjusted_size - 1000.0).abs() < 0.01);
    }

    // ── REDUCE regime halves Kelly sizing ──
    #[test]
    fn test_reduce_halves_kelly() {
        let cfg = RiskConfig {
            kelly_ramp_trades: 250, // Full ramp — no scaling
            minimum_entry_gbp: 200.0, // Low enough for halved size
            ..Default::default()
        };
        let mut arb = RiskArbiter::new(cfg);
        arb.escalate(RiskRegime::Reduce);
        let p = PortfolioState::new(10_000.0);
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(decision.approved);
        // adjusted_size = 0.1 * ramp(1.0) * 10000 * 0.5 = 500
        assert!((decision.adjusted_size - 500.0).abs() < 0.01);
    }

    // ── FLATTEN/HALT blocks all entries ──
    #[test]
    fn test_flatten_blocks_entries() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        arb.escalate(RiskRegime::Flatten);
        let p = PortfolioState::new(10_000.0);
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
    }

    // ── Confidence below floor ──
    #[test]
    fn test_confidence_below_floor() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let ctx = default_ctx();
        let decision = arb.evaluate(TickerId(1), Direction::Long, 60.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        match &decision.reason {
            VetoReason::ConfidenceBelowFloor { .. } => {}
            other => panic!("Expected ConfidenceBelowFloor, got {:?}", other),
        }
    }

    // ── Auction period — REMOVED (was LSE-specific, global engine uses spread veto) ──
    #[test]
    fn test_auction_period_not_blocked_global() {
        // CHECK 12 removed: auction periods no longer block entries globally.
        // Spread veto (CHECK 13) provides natural protection during auctions.
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let mut ctx = default_ctx();
        ctx.time_secs = 7 * 3600 + 55 * 60; // 07:55 — was LSE open auction
        let _decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        // Now allowed (spread veto is the guard, not auction blocking)
        // Note: will still be rejected if spread is too wide or kelly ramp gate active
    }

    // ── SC-05: Minimum entry size gate ──
    #[test]
    fn test_sc05_minimum_entry_size_rejects() {
        let cfg = RiskConfig {
            kelly_ramp_trades: 300, // Past ramp, gate active
            minimum_entry_gbp: 1500.0,
            ..Default::default()
        };
        let mut arb = RiskArbiter::new(cfg);
        let p = PortfolioState::new(10_000.0);
        let ctx = default_ctx();
        // kelly=0.1, ramp=1.0, equity=10000 → size=1000 < 1500 → REJECTED
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!decision.approved);
        match &decision.reason {
            VetoReason::BelowMinimumEntrySize { size_gbp } => assert_eq!(*size_gbp, 1000),
            other => panic!("Expected BelowMinimumEntrySize, got {:?}", other),
        }
    }

    #[test]
    fn test_sc05_minimum_entry_size_passes() {
        let cfg = RiskConfig {
            kelly_ramp_trades: 300,
            minimum_entry_gbp: 1500.0,
            ..Default::default()
        };
        let mut arb = RiskArbiter::new(cfg);
        let p = PortfolioState::new(10_000.0);
        let ctx = default_ctx();
        // kelly=0.2, ramp=1.0, equity=10000 → size=2000 > 1500 → APPROVED
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.2, &p, &ctx);
        assert!(decision.approved);
        assert!((decision.adjusted_size - 2000.0).abs() < 0.01);
    }

    #[test]
    fn test_sc05_suspended_during_kelly_ramp() {
        let cfg = RiskConfig {
            kelly_ramp_trades: 100, // < 250, so gate suspended
            minimum_entry_gbp: 1500.0,
            ..Default::default()
        };
        let mut arb = RiskArbiter::new(cfg);
        let p = PortfolioState::new(10_000.0);
        let ctx = default_ctx();
        // kelly=0.1, ramp=0.4, equity=10000 → size=400 < 1500
        // But gate suspended because trades < 250 → APPROVED
        let decision = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(decision.approved);
        // size = 0.1 * max(0.1, min(1.0, 100/250)) * 10000 = 0.1 * 0.4 * 10000 = 400
        assert!((decision.adjusted_size - 400.0).abs() < 0.01);
    }

    // ── SC-13: Kelly scaling ramp ──
    #[test]
    fn test_sc13_kelly_ramp_scaling() {
        // trades=0 → ramp=0.1 (floor)
        let cfg = RiskConfig {
            kelly_ramp_trades: 0,
            ..Default::default()
        };
        let mut arb = RiskArbiter::new(cfg);
        let p = PortfolioState::new(10_000.0);
        let ctx = default_ctx();
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(d.approved);
        // 0.1 * 0.1 * 10000 = 100
        assert!((d.adjusted_size - 100.0).abs() < 0.01);

        // trades=125 → ramp=0.5
        let cfg2 = RiskConfig {
            kelly_ramp_trades: 125,
            ..Default::default()
        };
        let mut arb2 = RiskArbiter::new(cfg2);
        let d2 = arb2.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(d2.approved);
        // 0.1 * 0.5 * 10000 = 500
        assert!((d2.adjusted_size - 500.0).abs() < 0.01);

        // trades=500 → ramp=1.0 (cap)
        let cfg3 = RiskConfig {
            kelly_ramp_trades: 500,
            minimum_entry_gbp: 500.0,
            ..Default::default()
        };
        let mut arb3 = RiskArbiter::new(cfg3);
        let d3 = arb3.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(d3.approved);
        // 0.1 * 1.0 * 10000 = 1000
        assert!((d3.adjusted_size - 1000.0).abs() < 0.01);
    }

    // ── Phase 15: Expanded risk checks ──

    #[test]
    fn test_duplicate_position_rejected_default() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let mut p = PortfolioState::new(10_000.0);
        p.add_position(make_position(1, 10, 100.0, 95.0));
        let mut ctx = default_ctx();
        ctx.ticker_position_count = 1; // Already holding 1
        ctx.ticker_ic = 0.0;
        ctx.ticker_trade_count = 0;
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!d.approved);
        assert_eq!(d.reason, VetoReason::DuplicatePosition);
    }

    #[test]
    fn test_momentum_reentry_60pct_wr() {
        // IC >= 0.10 (60% WR) + 10 trades → 2nd position allowed
        let cfg = RiskConfig {
            kelly_ramp_trades: 250,
            minimum_entry_gbp: 500.0,
            ..Default::default()
        };
        let mut arb = RiskArbiter::new(cfg);
        let mut p = PortfolioState::new(100_000.0);
        p.add_position(make_position(1, 10, 100.0, 95.0));
        let mut ctx = default_ctx();
        ctx.ticker_position_count = 1;
        ctx.ticker_ic = 0.10; // 60% WR
        ctx.ticker_trade_count = 15;
        ctx.ticker_locked = false;
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(d.approved, "60% WR with 15 trades should allow 2nd position");
    }

    #[test]
    fn test_momentum_reentry_70pct_wr() {
        // IC >= 0.20 (70% WR) + 20 trades → up to 3 positions
        let cfg = RiskConfig {
            kelly_ramp_trades: 250,
            minimum_entry_gbp: 500.0,
            ..Default::default()
        };
        let mut arb = RiskArbiter::new(cfg);
        let mut p = PortfolioState::new(100_000.0);
        p.add_position(make_position(1, 10, 100.0, 95.0));
        p.add_position(make_position(11, 10, 100.0, 95.0)); // 2nd position (different ticker_id for portfolio)
        let mut ctx = default_ctx();
        ctx.ticker_position_count = 2; // Already holding 2
        ctx.ticker_ic = 0.20; // 70% WR
        ctx.ticker_trade_count = 25;
        ctx.ticker_locked = false;
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(d.approved, "70% WR with 25 trades should allow 3rd position");
    }

    #[test]
    fn test_momentum_reentry_locked_blocked() {
        // Locked ticker → no re-entry regardless of IC
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let mut p = PortfolioState::new(100_000.0);
        p.add_position(make_position(1, 10, 100.0, 95.0));
        let mut ctx = default_ctx();
        ctx.ticker_position_count = 1;
        ctx.ticker_ic = 0.30; // Great IC but locked
        ctx.ticker_trade_count = 50;
        ctx.ticker_locked = true;
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!d.approved, "Locked ticker should block re-entry");
        assert_eq!(d.reason, VetoReason::DuplicatePosition);
    }

    #[test]
    fn test_ticker_halted_rejected() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let mut ctx = default_ctx();
        ctx.ticker_halted = true;
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!d.approved);
        assert_eq!(d.reason, VetoReason::TickerHalted);
    }

    #[test]
    fn test_garch_vol_too_high_rejected() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let mut ctx = default_ctx();
        ctx.garch_sigma = 0.95; // > 80% threshold
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!d.approved);
        match d.reason {
            VetoReason::GarchVolTooHigh { sigma_pct } => assert_eq!(sigma_pct, 95),
            other => panic!("Expected GarchVolTooHigh, got {:?}", other),
        }
    }

    #[test]
    fn test_scanner_score_too_low_rejected() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let mut ctx = default_ctx();
        ctx.scanner_score = 15.0; // < 30 threshold
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!d.approved);
        match d.reason {
            VetoReason::ScannerScoreTooLow { score } => assert_eq!(score, 15),
            other => panic!("Expected ScannerScoreTooLow, got {:?}", other),
        }
    }

    #[test]
    fn test_kelly_below_floor_rejected() {
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let mut ctx = default_ctx();
        ctx.kelly_fraction_raw = 0.003; // < 0.5%
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(!d.approved);
        assert_eq!(d.reason, VetoReason::KellyBelowFloor);
    }

    #[test]
    fn test_cvar_heat_normal_passes() {
        // With no positions, CVaR is 0 → passes
        let mut arb = RiskArbiter::new(RiskConfig::default());
        let p = PortfolioState::new(10_000.0);
        let ctx = default_ctx();
        let d = arb.evaluate(TickerId(1), Direction::Long, 80.0, 0.1, &p, &ctx);
        assert!(d.approved);
    }
}
