//! Replay Harness — deterministic replay of synthetic data through the full pipeline.
//! Proves: signal paths connected, state consistent, filters working, deterministic output.

use std::collections::HashMap;
use std::path::Path;

use crate::broker::{BrokerAdapter, BrokerEvent};
use crate::config::RiskConfig;
use crate::exit_engine::{ExitEngine, initial_stop_price};
use crate::paper_broker::{PaperBroker, PaperBrokerConfig};
use crate::portfolio::PortfolioState;
use crate::risk_arbiter::{EvalContext, RiskArbiter};
use crate::types::{Direction, MarketTick, OrderSide, OrderState, PositionState, TickerId, WalPayload};
use crate::universe::{FilterReason, RouteResult, Universe};
use crate::wal_writer::{WalWriter, make_wal_event};

/// Counters tracked during replay for verification.
#[derive(Clone, Debug, Default)]
pub struct ReplayCounters {
    pub ticks_processed: u64,
    pub ticks_filtered: u64,
    pub signals_generated: u64,
    pub orders_approved: u64,
    pub orders_rejected: u64,
    pub fills_received: u64,
    pub exits_triggered: u64,
    pub positions_closed: u64,
    pub wal_events_written: u64,
    pub gap_cooldowns: u64,
    pub erroneous_ticks: u64,
    pub synthetic_halts: u64,
    pub price_spikes_blocked: u64,
}

/// T2T latency record (H118).
#[derive(Clone, Debug)]
pub struct LatencyRecord {
    pub recv_ns: u64,
    pub process_ns: u64,
}

/// The replay engine: ties all pipeline components together deterministically.
pub struct ReplayEngine {
    pub universe: Universe,
    pub arbiter: RiskArbiter,
    pub broker: PaperBroker,
    pub exit_engine: ExitEngine,
    pub portfolio: PortfolioState,
    pub wal: Option<WalWriter>,
    pub counters: ReplayCounters,
    pub latency_log: Vec<LatencyRecord>,
    pub positions: HashMap<TickerId, PositionState>,
    pub last_prices: HashMap<TickerId, f64>,
    gap_cooldowns: HashMap<TickerId, u64>,
    pub now_ns: u64,
    pub time_secs: u32,
    pub order_counter: u64,
}

impl ReplayEngine {
    pub fn new(universe: Universe, wal_path: Option<&Path>) -> Self {
        let arbiter = RiskArbiter::new(RiskConfig::default());
        let broker = PaperBroker::new(PaperBrokerConfig::default());
        let exit_engine = ExitEngine::with_default_chandelier();
        let portfolio = PortfolioState::new(10_000.0);
        let wal = wal_path.and_then(|p| {
            let dead_letter = p.parent().unwrap_or(Path::new(".")).join("dead_letter");
            WalWriter::open_file(p, &dead_letter).ok()
        });
        Self {
            universe,
            arbiter,
            broker,
            exit_engine,
            portfolio,
            wal,
            counters: ReplayCounters::default(),
            latency_log: Vec::new(),
            positions: HashMap::new(),
            last_prices: HashMap::new(),
            gap_cooldowns: HashMap::new(),
            now_ns: 0,
            time_secs: 36_000, // default 10:00 AM
            order_counter: 0,
        }
    }

    /// Process a single tick through the full pipeline.
    pub fn process_tick(&mut self, tick: MarketTick) {
        self.now_ns = tick.timestamp_ns;
        self.counters.ticks_processed += 1;

        // T2T latency logging (H118)
        self.latency_log.push(LatencyRecord {
            recv_ns: tick.recv_timestamp_ns,
            process_ns: self.now_ns,
        });

        // 1. Route through universe (applies all 5 filters)
        let routed_tick = match self.universe.route_tick(&tick, self.now_ns) {
            RouteResult::Vanguard(t) | RouteResult::Apex(t) => t,
            RouteResult::Filtered(reason) => {
                self.counters.ticks_filtered += 1;
                match reason {
                    FilterReason::ErroneousTick => self.counters.erroneous_ticks += 1,
                    FilterReason::SyntheticHalt => self.counters.synthetic_halts += 1,
                    _ => {}
                }
                return;
            }
        };

        let tid = routed_tick.ticker_id;

        // 2. Gap detection (H66): >2% gap → 15-min cooldown
        if let Some(&prev) = self.last_prices.get(&tid)
            && prev > 0.0
        {
            let gap_pct = ((routed_tick.last - prev) / prev).abs();
            if gap_pct > 0.02 {
                let cooldown_until = self.now_ns + 15 * 60 * 1_000_000_000;
                self.gap_cooldowns.insert(tid, cooldown_until);
                self.counters.gap_cooldowns += 1;
            }
        }
        self.last_prices.insert(tid, routed_tick.last);

        // ATR estimate (simplified: 2% of price)
        let atr = routed_tick.last * 0.02;

        // 3. Evaluate exits for existing positions
        if let Some(pos) = self.positions.get_mut(&tid) {
            // Price spike filter (H71)
            if self.exit_engine.is_price_spike(
                pos.highest_high,
                routed_tick.last,
                routed_tick.bid,
                routed_tick.ask,
            ) {
                self.counters.price_spikes_blocked += 1;
                return;
            }
            self.exit_engine.update_tracking(pos, routed_tick.last, atr);
            pos.unrealized_pnl = (routed_tick.last - pos.avg_entry) * pos.qty as f64;
            let is_halt = self.arbiter.regime >= crate::types::RiskRegime::Flatten;
            let exit_result = self.exit_engine.evaluate(
                pos,
                routed_tick.last,
                atr,
                self.time_secs,
                is_halt,
                false,
                false,
            );
            if let Some(ref result) = exit_result {
                // Extract values before borrowing self mutably
                let reason_str = format!("{:?}", result.signal.reason);
                let priority_str = format!("{:?}", result.signal.priority);
                let final_pnl = pos.unrealized_pnl - pos.total_commission;
                let entry_time = pos.entry_timestamp_ns;
                let exit_time = self.now_ns;
                let tid_u32 = tid.0;
                let close_qty = pos.qty;
                let close_sym = self.broker.symbol_for(tid).unwrap_or_else(|| format!("T{}", tid.0));
                self.counters.exits_triggered += 1;
                self.write_wal(WalPayload::ExitSignal {
                    ticker_id: tid_u32,
                    reason: reason_str,
                    priority: priority_str,
                });
                self.write_wal(WalPayload::PositionClosed {
                    ticker_id: tid_u32,
                    final_pnl,
                    entry_time_ns: entry_time,
                    exit_time_ns: exit_time,
                    gross_pnl: 0.0,
                    total_commission: 0.0,
                    spread_at_entry_pct: 0.0,
                    spread_at_exit_pct: 0.0,
                    daily_trade_number: 0,
                entry_type: String::new(),
                    symbol: close_sym,
                    qty: close_qty,
                    regime_at_entry: String::new(),
                    confidence: 0.0,
                    highest_rung: 0,
                    strategy: String::new(),
                    exchange: String::new(),
                    exit_price: 0.0,
                    entry_rvol: 0.0,
                    entry_hurst: 0.0,
                    entry_adx: 0.0,
                    entry_price: 0.0,
                    mae: 0.0,
                    mfe: 0.0,
                    // N2b: Enriched fields (defaults for replay mode).
                    hold_time_mins: 0,
                    entry_session_phase: String::new(),
                    vwap_dist_at_entry_pct: 0.0,
                    atr_pct_at_entry: 0.0,
                    vix_at_entry: 0.0,
                    vol_slope_at_entry: 0.0,
                    trade_class: String::new(),
                });
                self.portfolio.remove_position(tid);
                self.positions.remove(&tid);
                self.counters.positions_closed += 1;
                return;
            }
        }

        // 4. Mock brain: signal if price > prev_close + 0.5% and no position
        if self.positions.contains_key(&tid) {
            return;
        }
        if let Some(&cooldown) = self.gap_cooldowns.get(&tid)
            && self.now_ns < cooldown
        {
            return;
        }
        let prev_close = self
            .universe
            .tickers
            .get(&tid)
            .map(|s| s.prev_close)
            .unwrap_or(0.0);
        if prev_close <= 0.0 || routed_tick.last <= prev_close * 1.005 {
            return;
        }
        self.counters.signals_generated += 1;

        // 5. Risk arbiter
        let exchange_mic = self.broker.exchange_for_ticker(&tid).to_string();
        let ctx = EvalContext {
            time_secs: self.time_secs,
            last_tick_age_secs: 1,
            bid: routed_tick.bid,
            ask: routed_tick.ask,
            broker_connected: self.broker.is_connected(),
            wal_available: self.wal.is_some(),
            now_ns: self.now_ns,
            exchange_mic,
            ..EvalContext::default()
        };
        let decision =
            self.arbiter
                .evaluate(tid, Direction::Long, 78.0, 0.08, &self.portfolio, &ctx);
        if !decision.approved {
            self.counters.orders_rejected += 1;
            return;
        }
        self.counters.orders_approved += 1;

        // 6. WAL write — use monotonic counter for unique IDs
        self.order_counter += 1;
        let order_id = format!("order-{}", self.order_counter);
        let replay_symbol = self.broker.symbol_for(tid).unwrap_or_else(|| format!("T{}", tid.0));
        self.write_wal(WalPayload::RoutedOrder {
            order_id: order_id.clone(),
            ticker_id: tid.0,
            side: "Long".to_string(),
            confidence: 78.0,
            strategy: "MockBrain".to_string(),
            kelly_fraction: 0.08,
            approved_size: decision.adjusted_size,
            symbol: replay_symbol,
            qty: (decision.adjusted_size / routed_tick.ask).max(1.0) as u32,
            currency: "GBP".to_string(),
            entry_rvol: 0.0,
            entry_hurst: 0.0,
            entry_adx: 0.0,
            rsi: 0.0,
            vwap_dist_pct: 0.0,
            atr: 0.0,
            vol_slope: 0.0,
            spread_pct: 0.0,
            mtf_score: 0.0,
            entry_type: String::new(),
            ibs: 0.0,
        });

        // 7. Submit + fill
        let qty = (decision.adjusted_size / routed_tick.ask).max(1.0) as u32;
        let limit_price = routed_tick.ask * 1.001;
        if self
            .broker
            .submit_order(&order_id, tid, OrderSide::Buy, qty, limit_price)
            .is_err()
        {
            return;
        }
        let _ = self.broker.generate_fills(&order_id);

        // 8. Drain broker events → portfolio + WAL
        let events = self.broker.drain_events();
        for ev in &events {
            self.process_broker_event(ev);
        }
    }

    fn process_broker_event(&mut self, ev: &BrokerEvent) {
        match ev {
            BrokerEvent::Fill {
                order_id,
                ticker_id,
                filled_qty,
                remaining_qty,
                price,
                exec_id,
                commission,
            } => {
                self.counters.fills_received += 1;
                self.write_wal(WalPayload::FillEvent {
                    order_id: order_id.clone(),
                    ticker_id: ticker_id.0,
                    filled_qty: *filled_qty,
                    remaining_qty: *remaining_qty,
                    price: *price,
                    exec_id: exec_id.clone(),
                    commission: *commission,
                    spread_at_fill_pct: 0.0,
                    side: "Buy".to_string(),
                });
                if *remaining_qty == 0 {
                    let stop = initial_stop_price(*price, 0.05);
                    let pos = PositionState {
                        entry_timestamp_ns: self.now_ns,
                        avg_entry: *price,
                        unrealized_pnl: 0.0,
                        realized_pnl: 0.0,
                        highest_high: *price,
                        stop_price: stop,
                        total_commission: *commission,
                        qty: *filled_qty,
                        ticker_id: *ticker_id,
                        trailing_rung: 0,
                        state: OrderState::ExitRegistered,
                        origin_order_id: order_id.clone(),
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
                exit_trail_bias: None,
                partial_exits_done: 0,
                    };
                    self.portfolio.add_position(pos.clone());
                    self.positions.insert(*ticker_id, pos);
                }
            }
            BrokerEvent::Ack {
                order_id,
                ibkr_order_id,
                status,
                ..
            } => {
                self.write_wal(WalPayload::BrokerAck {
                    order_id: order_id.clone(),
                    status: format!("{:?}", status),
                    ibkr_order_id: *ibkr_order_id,
                });
            }
            _ => {}
        }
    }

    fn write_wal(&mut self, payload: WalPayload) {
        if let Some(ref mut wal) = self.wal {
            let event = make_wal_event(self.now_ns, payload);
            let _ = wal.append(&event);
            self.counters.wal_events_written += 1;
        }
    }
}

// ── Synthetic data generation ──

/// Generate deterministic synthetic ticks for replay testing.
pub fn generate_synthetic_day(
    tickers: &[TickerId],
    base_prices: &[f64],
    ticks_per_ticker: u64,
    base_ns: u64,
) -> Vec<MarketTick> {
    let mut ticks = Vec::new();
    let interval_ns = 1_000_000_000u64;
    for idx in 0..ticks_per_ticker {
        for (i, &tid) in tickers.iter().enumerate() {
            let base = base_prices[i];
            let t = idx as f64 / ticks_per_ticker as f64;
            let wave = (t * std::f64::consts::PI * 4.0).sin() * base * 0.01;
            let trend = t * base * 0.005;
            let price = base + wave + trend;
            let ts = base_ns + idx * interval_ns;
            ticks.push(MarketTick {
                ticker_id: tid,
                bid: price - 0.01,
                ask: price + 0.01,
                last: price,
                volume: 10_000 + idx * 10,
                timestamp_ns: ts,
                recv_timestamp_ns: ts + 100,
                bid_size: 0,
                ask_size: 0,
            });
        }
    }
    ticks
}

/// Inject a tick with a >2% gap from previous price.
pub fn inject_gap_tick(tid: TickerId, prev: f64, gap_pct: f64, ts: u64) -> MarketTick {
    let price = prev * (1.0 + gap_pct);
    MarketTick {
        ticker_id: tid,
        bid: price - 0.01,
        ask: price + 0.01,
        last: price,
        volume: 50_000,
        timestamp_ns: ts,
        recv_timestamp_ns: ts + 100,
        bid_size: 0,
        ask_size: 0,
    }
}

/// Inject an erroneous spike tick (>5% deviation from EMA).
pub fn inject_spike_tick(tid: TickerId, normal: f64, spike_pct: f64, ts: u64) -> MarketTick {
    let price = normal * (1.0 + spike_pct);
    MarketTick {
        ticker_id: tid,
        bid: price - 0.01,
        ask: price + 0.01,
        last: price,
        volume: 1_000,
        timestamp_ns: ts,
        recv_timestamp_ns: ts + 100,
        bid_size: 0,
        ask_size: 0,
    }
}

/// Inject a flash crash: last drops heavily but bid/ask midpoint stays reasonable.
pub fn inject_flash_crash(tid: TickerId, normal: f64, ts: u64) -> MarketTick {
    MarketTick {
        ticker_id: tid,
        bid: normal * 0.95,
        ask: normal * 0.99,
        last: normal * 0.85,
        volume: 500,
        timestamp_ns: ts,
        recv_timestamp_ns: ts + 100,
        bid_size: 0,
        ask_size: 0,
    }
}
