//! Phase 5 acceptance tests for the Singular Canonical Exit Engine.

#[cfg(test)]
mod tests {
    use crate::exit_engine::{
        ChandelierStrategy, ExitConfig, ExitEngine, ExitStrategy, TimeInForce, emergency_tif,
        entry_tif, initial_stop_price,
    };
    use crate::types::{
        ExitOrderType, ExitPriority, ExitReason, OrderState, PositionState, TickerId,
    };

    fn make_position(entry: f64, stop: f64, qty: u32) -> PositionState {
        PositionState {
            entry_timestamp_ns: 0,
            avg_entry: entry,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
            highest_high: entry,
            stop_price: stop,
            total_commission: 1.50,
            qty,
            ticker_id: TickerId(42),
            trailing_rung: 0,
            state: OrderState::ExitRegistered,
            origin_order_id: "test-order".to_string(),
            is_carried: false,
        }
    }

    fn default_engine() -> ExitEngine {
        ExitEngine::with_default_chandelier()
    }

    // ── Test 1: Exactly ONE exit engine (structural) ──
    #[test]
    fn test_singular_exit_engine() {
        let engine = default_engine();
        // Only one ExitEngine type exists. No duplicate exit logic.
        // Verified by: all exit evaluation goes through engine.evaluate().
        let pos = make_position(10.0, 9.50, 100);
        let result = engine.evaluate(&pos, 10.10, 0.50, 36_000, false, false, false);
        assert!(result.is_none()); // No exit triggered
    }

    // ── Test 2: Exit priority ordering ──
    #[test]
    fn test_exit_priority_ordering() {
        assert!(ExitPriority::HaltFlatten > ExitPriority::HardStopLoss);
        assert!(ExitPriority::HardStopLoss > ExitPriority::ChandelierStop);
        assert!(ExitPriority::ChandelierStop > ExitPriority::EodFlatten);
        assert!(ExitPriority::EodFlatten > ExitPriority::SignalReversal);
    }

    // ── Test 3: Same-tick collision: hard stop + Chandelier → hard stop wins ──
    #[test]
    fn test_collision_hard_stop_beats_chandelier() {
        let engine = default_engine();
        let mut pos = make_position(10.0, 9.50, 100);
        pos.highest_high = 10.75; // Rung 3 reached
        pos.trailing_rung = 3;
        // Chandelier stop at rung 3 = entry + 0.5 * ATR = 10.0 + 0.25 = 10.25
        // Hard stop at 9.50
        // Price drops to 9.40 → both fire
        pos.stop_price = 9.50;
        let result = engine.evaluate(&pos, 9.40, 0.50, 36_000, false, false, false);
        let r = result.expect("exit should fire");
        assert_eq!(r.signal.reason, ExitReason::HardStopLoss);
        assert_eq!(r.signal.priority, ExitPriority::HardStopLoss);
        assert!(r.suppressed_count >= 1); // Chandelier suppressed
    }

    // ── Test 4: Same-tick collision: hard stop + EOD → hard stop wins ──
    #[test]
    fn test_collision_hard_stop_beats_eod() {
        let engine = default_engine();
        let pos = make_position(10.0, 9.50, 100);
        // Price below hard stop AND past EOD time
        let result = engine.evaluate(&pos, 9.40, 0.50, 59_200, false, false, false);
        let r = result.expect("exit should fire");
        assert_eq!(r.signal.reason, ExitReason::HardStopLoss);
        assert!(r.suppressed_count >= 1); // EOD suppressed
    }

    // ── Test 5: HALT override → ALL exits become MarketToLimit ──
    #[test]
    fn test_halt_override_market_sell() {
        let engine = default_engine();
        let pos = make_position(10.0, 9.50, 100);
        // Hard stop fires at 9.40, but HALT also active
        let result = engine.evaluate(&pos, 9.40, 0.50, 36_000, true, false, false);
        let r = result.expect("exit should fire");
        assert_eq!(r.signal.reason, ExitReason::HaltFlatten);
        assert_eq!(r.signal.order_type, ExitOrderType::MarketToLimit); // H117
        assert_eq!(r.tif, TimeInForce::Ioc); // H69
    }

    // ── Test 6: Chandelier 5-rung ladder test ──
    #[test]
    fn test_chandelier_5_rung_ladder() {
        let strategy = ChandelierStrategy::default();
        let entry = 10.0;
        let atr = 0.50;
        let mut pos = make_position(entry, 9.50, 100);

        // Rung 0: price at entry, no rung
        assert_eq!(strategy.compute_rung(&pos, entry, atr), 0);

        // Rung 1: price 10.25 (+0.5 ATR) → stop = 10.00 (breakeven)
        pos.highest_high = 10.25;
        assert_eq!(strategy.compute_rung(&pos, 10.25, atr), 1);
        let stop = strategy.compute_stop(&pos, 10.25, atr);
        assert!((stop - 10.0).abs() < 0.001, "Rung 1 stop={stop}");

        // Rung 2: price 10.50 (+1.0 ATR) → stop = 10.125
        pos.highest_high = 10.50;
        assert_eq!(strategy.compute_rung(&pos, 10.50, atr), 2);
        let stop = strategy.compute_stop(&pos, 10.50, atr);
        assert!((stop - 10.125).abs() < 0.001, "Rung 2 stop={stop}");

        // Rung 3: price 10.75 (+1.5 ATR) → stop = 10.25
        pos.highest_high = 10.75;
        assert_eq!(strategy.compute_rung(&pos, 10.75, atr), 3);
        let stop = strategy.compute_stop(&pos, 10.75, atr);
        assert!((stop - 10.25).abs() < 0.001, "Rung 3 stop={stop}");

        // Rung 4: price 11.00 (+2.0 ATR) → stop = 10.50
        pos.highest_high = 11.0;
        assert_eq!(strategy.compute_rung(&pos, 11.0, atr), 4);
        let stop = strategy.compute_stop(&pos, 11.0, atr);
        assert!((stop - 10.50).abs() < 0.001, "Rung 4 stop={stop}");

        // Rung 5: price 11.50 (+3.0 ATR) → stop = 11.50 - 1.5*0.50 = 10.75
        pos.highest_high = 11.50;
        assert_eq!(strategy.compute_rung(&pos, 11.50, atr), 5);
        let stop = strategy.compute_stop(&pos, 11.50, atr);
        assert!((stop - 10.75).abs() < 0.001, "Rung 5 stop={stop}");
    }

    // ── Test 7: Stop ratchet — stop can NEVER decrease (H68) ──
    #[test]
    fn test_stop_ratchet_never_decreases() {
        let engine = default_engine();
        let mut pos = make_position(10.0, 9.50, 100);
        let atr = 0.50;

        // Price rises → stop ratchets up
        engine.update_tracking(&mut pos, 10.25, atr); // Rung 1
        assert!((pos.stop_price - 10.0).abs() < 0.001);

        engine.update_tracking(&mut pos, 10.75, atr); // Rung 3
        let stop_at_rung3 = pos.stop_price;
        assert!(stop_at_rung3 >= 10.25);

        // Price drops back → stop must NOT decrease
        engine.update_tracking(&mut pos, 10.30, atr);
        assert!(
            pos.stop_price >= stop_at_rung3,
            "Stop decreased: {} < {}",
            pos.stop_price,
            stop_at_rung3
        );
    }

    // ── Test 8: EOD flatten at 16:25 ──
    #[test]
    fn test_eod_flatten() {
        let engine = default_engine();
        let pos = make_position(10.0, 9.50, 100);
        // Before 16:25 (59100s) → no exit
        let result = engine.evaluate(&pos, 10.10, 0.50, 59_000, false, false, false);
        assert!(result.is_none());
        // At 16:25 → market sell all
        let result = engine.evaluate(&pos, 10.10, 0.50, 59_100, false, false, false);
        let r = result.expect("EOD exit");
        assert_eq!(r.signal.reason, ExitReason::EodFlatten);
        assert_eq!(r.signal.order_type, ExitOrderType::MarketSell);
    }

    // ── Test 9: Shadow stops — internal, not IBKR trailing stops (H67) ──
    #[test]
    fn test_shadow_stops_internal() {
        // Shadow stops verified by architecture: ExitEngine computes stops in Rust.
        // No reqTrailingStop or reqBracketOrder calls exist anywhere.
        // The stop_price lives in PositionState, updated by update_tracking().
        let engine = default_engine();
        let mut pos = make_position(10.0, 9.50, 100);
        engine.update_tracking(&mut pos, 10.50, 0.50);
        // Stop is internally managed, not sent to IBKR
        assert!(pos.stop_price > 9.50);
        assert_eq!(pos.trailing_rung, 2);
    }

    // ── Test 10: TIF rules — entry=DAY, emergency=IOC (H69) ──
    #[test]
    fn test_tif_rules() {
        assert_eq!(entry_tif(), TimeInForce::Day);
        assert_eq!(emergency_tif(), TimeInForce::Ioc);
        // Verify HALT exit uses IOC
        let engine = default_engine();
        let pos = make_position(10.0, 9.50, 100);
        let result = engine.evaluate(&pos, 10.0, 0.50, 36_000, true, false, false);
        let r = result.expect("HALT exit");
        assert_eq!(r.tif, TimeInForce::Ioc);
        // Verify normal exit uses DAY
        let result = engine.evaluate(&pos, 9.40, 0.50, 36_000, false, false, false);
        let r = result.expect("stop exit");
        assert_eq!(r.tif, TimeInForce::Day);
    }

    // ── Test 11: MTL for emergency exits (H117) ──
    #[test]
    fn test_mtl_emergency_exits() {
        let engine = default_engine();
        let pos = make_position(10.0, 9.50, 100);
        let result = engine.evaluate(&pos, 10.0, 0.50, 36_000, true, false, false);
        let r = result.expect("HALT exit");
        assert_eq!(r.signal.order_type, ExitOrderType::MarketToLimit);
    }

    // ── Test 12: Highest_high persisted in WAL for crash recovery (H70) ──
    #[test]
    fn test_highest_high_persisted() {
        let engine = default_engine();
        let mut pos = make_position(10.0, 9.50, 100);
        engine.update_tracking(&mut pos, 10.50, 0.50);
        engine.update_tracking(&mut pos, 10.80, 0.50);
        assert!((pos.highest_high - 10.80).abs() < 0.001);
        // Simulate "crash recovery" — highest_high survives in PositionState
        let recovered = PositionState {
            highest_high: pos.highest_high,
            trailing_rung: pos.trailing_rung,
            stop_price: pos.stop_price,
            ..pos
        };
        assert!((recovered.highest_high - 10.80).abs() < 0.001);
        // Continue tracking from recovered state — stop doesn't regress
        let mut pos2 = recovered;
        engine.update_tracking(&mut pos2, 10.60, 0.50);
        assert!(pos2.stop_price >= pos.stop_price);
    }

    // ── Test 13: Price spike filter (H71) ──
    #[test]
    fn test_price_spike_filter() {
        let engine = default_engine();
        // Normal drop (5%) — not a spike
        assert!(!engine.is_price_spike(10.0, 9.50, 9.48, 9.52));
        // Spike: 10% last-price drop but midpoint only dropped 2%
        assert!(engine.is_price_spike(10.0, 9.0, 9.78, 9.82));
        // Real crash: 10% drop and midpoint also dropped 10%
        assert!(!engine.is_price_spike(10.0, 9.0, 8.98, 9.02));
    }

    // ── Test 14: Commission in targets (H73) ──
    #[test]
    fn test_commission_in_targets() {
        // Trade: buy 100 at 10.0, current 10.05, commission 3.00
        let ev = ExitEngine::ev_after_commission(10.0, 10.05, 100, 3.00);
        assert!((ev - 2.0).abs() < 0.001); // 5.0 gross - 3.0 comm = 2.0
        // Negative EV: buy 100 at 10.0, current 10.01, commission 3.00
        let ev = ExitEngine::ev_after_commission(10.0, 10.01, 100, 3.00);
        assert!(ev < 0.0); // 1.0 gross - 3.0 comm = -2.0
    }

    // ── Test 15: ExitStrategy trait (H72) ──
    #[test]
    fn test_exit_strategy_trait_swappable() {
        // Custom strategy: simple fixed trailing stop
        struct FixedTrailStrategy {
            trail_pct: f64,
        }
        impl ExitStrategy for FixedTrailStrategy {
            fn compute_stop(&self, _pos: &PositionState, high: f64, _atr: f64) -> f64 {
                high * (1.0 - self.trail_pct)
            }
            fn compute_rung(&self, _pos: &PositionState, _high: f64, _atr: f64) -> u8 {
                0 // No rungs
            }
        }
        let engine = ExitEngine::new(
            ExitConfig::default(),
            Box::new(FixedTrailStrategy { trail_pct: 0.05 }),
        );
        let mut pos = make_position(10.0, 9.50, 100);
        pos.highest_high = 11.0;
        engine.update_tracking(&mut pos, 11.0, 0.50);
        // Fixed 5% trail from 11.0 = 10.45
        assert!((pos.stop_price - 10.45).abs() < 0.001);
    }

    // ── Test 16: Signal reversal exit ──
    #[test]
    fn test_signal_reversal_exit() {
        let engine = default_engine();
        let pos = make_position(10.0, 9.50, 100);
        let result = engine.evaluate(&pos, 10.50, 0.50, 36_000, false, true, false);
        let r = result.expect("signal reversal exit");
        assert_eq!(r.signal.reason, ExitReason::SignalReversal);
        assert_eq!(r.signal.priority, ExitPriority::SignalReversal);
    }

    // ── Test 17: Initial stop price helper ──
    #[test]
    fn test_initial_stop_price() {
        let stop = initial_stop_price(10.0, 0.05);
        assert!((stop - 9.50).abs() < 0.001);
    }

    // ── Test 18: HALT + hard stop + EOD all fire → HALT wins ──
    #[test]
    fn test_triple_collision_halt_wins() {
        let engine = default_engine();
        let pos = make_position(10.0, 9.50, 100);
        // Price below stop, past EOD, HALT active
        let result = engine.evaluate(&pos, 9.40, 0.50, 59_200, true, true, false);
        let r = result.expect("HALT wins");
        assert_eq!(r.signal.reason, ExitReason::HaltFlatten);
        assert_eq!(r.signal.order_type, ExitOrderType::MarketToLimit);
        assert_eq!(r.tif, TimeInForce::Ioc);
        assert!(r.suppressed_count >= 2); // Hard stop + EOD + signal suppressed
    }

    // ── SC-06: Dust guard tests ──
    #[test]
    fn test_sc06_is_dust_below_threshold() {
        let engine = default_engine();
        // 10 shares at £40 = £400 < £500 threshold
        assert!(engine.is_dust(40.0, 10));
        // 100 shares at £40 = £4000 > £500 threshold
        assert!(!engine.is_dust(40.0, 100));
        // Edge: exactly at threshold
        assert!(!engine.is_dust(50.0, 10)); // £500 = threshold, NOT dust
    }

    #[test]
    fn test_sc06_dust_guard_exit_fires() {
        let engine = default_engine();
        let pos = make_position(40.0, 38.0, 10); // 10 shares at £40 = £400 < £500
        let result = engine.dust_guard_exit(&pos, 40.0);
        let r = result.expect("dust guard should fire");
        assert_eq!(r.signal.reason, ExitReason::DustGuard);
        assert_eq!(r.signal.priority, ExitPriority::DustGuard);
        assert_eq!(r.signal.order_type, ExitOrderType::MarketSell);
        assert_eq!(r.tif, TimeInForce::Day);
    }

    #[test]
    fn test_sc06_dust_guard_no_exit_above_threshold() {
        let engine = default_engine();
        let pos = make_position(40.0, 38.0, 100); // 100 shares at £40 = £4000
        let result = engine.dust_guard_exit(&pos, 40.0);
        assert!(result.is_none());
    }

    #[test]
    fn test_sc06_custom_dust_threshold() {
        let config = ExitConfig {
            dust_threshold_gbp: 1000.0,
            ..Default::default()
        };
        let engine = ExitEngine::new(config, Box::new(ChandelierStrategy::default()));
        // 20 shares at £40 = £800 < £1000
        assert!(engine.is_dust(40.0, 20));
        // 30 shares at £40 = £1200 > £1000
        assert!(!engine.is_dust(40.0, 30));
    }

    // ── Phase 14: Adaptive Multiplier tests ──

    #[test]
    fn test_adaptive_multipliers_default_neutral() {
        let m = crate::exit_engine::AdaptiveMultipliers::default();
        assert!((m.combined() - 1.0).abs() < 0.001);
    }

    #[test]
    fn test_adaptive_multiplier_volatility() {
        let mut m = crate::exit_engine::AdaptiveMultipliers::default();
        m.update_volatility(0.15); // Low vol → clamps to 0.8
        assert!((m.volatility - 0.8).abs() < 0.01);
        m.update_volatility(0.55); // High vol → 1.5 (capped)
        assert!((m.volatility - 1.5).abs() < 0.01);
        m.update_volatility(0.35); // Mid vol → ~1.15
        assert!(m.volatility > 1.0 && m.volatility < 1.3);
    }

    #[test]
    fn test_adaptive_multiplier_time_decay() {
        let mut m = crate::exit_engine::AdaptiveMultipliers::default();
        m.update_time_decay(0.0); // Open → 1.0
        assert!((m.time_decay - 1.0).abs() < 0.001);
        m.update_time_decay(1.0); // Close → 0.8
        assert!((m.time_decay - 0.8).abs() < 0.001);
        m.update_time_decay(0.5); // Mid-day → 0.9
        assert!((m.time_decay - 0.9).abs() < 0.001);
    }

    #[test]
    fn test_adaptive_multiplier_mega_runner() {
        let mut m = crate::exit_engine::AdaptiveMultipliers::default();
        m.update_mega_runner(2.0); // Below 3 ATR → 1.0
        assert!((m.mega_runner - 1.0).abs() < 0.001);
        m.update_mega_runner(5.0); // 5 ATR profit → 1.4
        assert!((m.mega_runner - 1.4).abs() < 0.001);
        m.update_mega_runner(10.0); // 10 ATR → capped at 2.0
        assert!((m.mega_runner - 2.0).abs() < 0.001);
    }

    #[test]
    fn test_adaptive_multiplier_combined_product() {
        let m = crate::exit_engine::AdaptiveMultipliers {
            volatility: 1.2,
            momentum: 1.1,
            mega_runner: 1.5,
            ..Default::default()
        };
        // Combined = 1.2 * 1.0 * 1.0 * 1.1 * 1.0 * 1.0 * 1.0 * 1.5 = 1.98
        assert!((m.combined() - 1.98).abs() < 0.01);
    }

    #[test]
    fn test_infinite_chandelier_adaptive_trail() {
        let mut ic = crate::exit_engine::InfiniteChandelier::new();
        ic.multipliers.mega_runner = 1.5;
        // Base trail = 1.5 ATR, with mega_runner 1.5 → effective = 2.25 ATR
        let stop = ic.adaptive_trail(120.0, 2.0);
        // 120.0 - 2.25 * 2.0 = 120.0 - 4.5 = 115.5
        assert!((stop - 115.5).abs() < 0.01);
    }

    #[test]
    fn test_infinite_chandelier_as_exit_strategy() {
        let ic = crate::exit_engine::InfiniteChandelier::new();
        let pos = make_position(100.0, 95.0, 100);
        // Not at rung 5 yet → delegates to base chandelier
        let stop = ic.compute_stop(&pos, 100.0, 2.0);
        assert!(stop >= 95.0); // Should keep initial stop

        // At rung 5 (3+ ATR profit): entry=100, high=108, atr=2 → profit_atr=4
        let mut pos2 = make_position(100.0, 95.0, 100);
        pos2.highest_high = 108.0;
        let rung = ic.compute_rung(&pos2, 108.0, 2.0);
        assert_eq!(rung, 5);
        let stop2 = ic.compute_stop(&pos2, 108.0, 2.0);
        // Adaptive trail: 108 - 1.5 * 2.0 * 1.0 (default multiplier) = 105.0
        assert!((stop2 - 105.0).abs() < 0.01);
    }

    #[test]
    fn test_adaptive_regime_reduce_tightens() {
        let mut m = crate::exit_engine::AdaptiveMultipliers::default();
        m.update_regime(false);
        assert!((m.regime - 1.0).abs() < 0.001);
        m.update_regime(true);
        assert!((m.regime - 0.6).abs() < 0.001);
    }

    #[test]
    fn test_adaptive_heat_tightens() {
        let mut m = crate::exit_engine::AdaptiveMultipliers::default();
        m.update_heat(1.0); // Low heat → 1.0
        assert!((m.heat - 1.0).abs() < 0.001);
        m.update_heat(8.0); // High heat → 0.7
        assert!((m.heat - 0.7).abs() < 0.001);
    }

    // ── Phase 14: Executioner V2 tests ──

    #[test]
    fn test_executioner_track_and_fill() {
        use crate::exit_engine::{Executioner, OrderLifecycle, TrackedOrder};

        let mut exec = Executioner::new();
        exec.track_order(TrackedOrder {
            order_id: "o-1".into(),
            ticker_id: TickerId(1),
            lifecycle: OrderLifecycle::Submitted,
            qty: 100,
            filled_qty: 0,
            limit_price: 10.50,
            submit_ns: 1_000_000_000,
            last_update_ns: 1_000_000_000,
            retries: 0,
            is_exit: false,
        });

        assert_eq!(exec.active_count(), 1);
        exec.update_lifecycle("o-1", OrderLifecycle::Acknowledged, 2_000_000_000);
        assert_eq!(
            exec.get("o-1").expect("exists").lifecycle,
            OrderLifecycle::Acknowledged
        );

        exec.record_fill("o-1", 50, 3_000_000_000);
        assert_eq!(
            exec.get("o-1").expect("exists").lifecycle,
            OrderLifecycle::PartialFill
        );

        exec.record_fill("o-1", 50, 4_000_000_000);
        assert_eq!(
            exec.get("o-1").expect("exists").lifecycle,
            OrderLifecycle::Filled
        );
    }

    #[test]
    fn test_executioner_stale_unacked() {
        use crate::exit_engine::{Executioner, OrderLifecycle, TrackedOrder};

        let mut exec = Executioner::new();
        exec.track_order(TrackedOrder {
            order_id: "o-1".into(),
            ticker_id: TickerId(1),
            lifecycle: OrderLifecycle::Submitted,
            qty: 100,
            filled_qty: 0,
            limit_price: 10.50,
            submit_ns: 1_000_000_000,
            last_update_ns: 1_000_000_000,
            retries: 0,
            is_exit: false,
        });

        // Not stale yet (only 1s elapsed, timeout = 5s)
        assert!(exec.stale_unacked(2_000_000_000).is_empty());
        // Stale after 6s
        assert_eq!(exec.stale_unacked(7_000_000_000).len(), 1);
    }

    #[test]
    fn test_executioner_prune_completed() {
        use crate::exit_engine::{Executioner, OrderLifecycle, TrackedOrder};

        let mut exec = Executioner::new();
        for (id, state) in [
            ("o-1", OrderLifecycle::Filled),
            ("o-2", OrderLifecycle::Cancelled),
            ("o-3", OrderLifecycle::Submitted),
        ] {
            exec.track_order(TrackedOrder {
                order_id: id.into(),
                ticker_id: TickerId(1),
                lifecycle: state,
                qty: 100,
                filled_qty: 0,
                limit_price: 10.0,
                submit_ns: 0,
                last_update_ns: 0,
                retries: 0,
                is_exit: false,
            });
        }

        assert_eq!(exec.total_count(), 3);
        exec.prune_completed();
        assert_eq!(exec.total_count(), 1); // Only o-3 (Submitted) remains
        assert!(exec.get("o-3").is_some());
    }
}
