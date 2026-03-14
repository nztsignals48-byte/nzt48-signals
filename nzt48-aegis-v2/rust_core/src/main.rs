//! AEGIS V2 — Paper Engine Binary
//! Connects to IB Gateway via ibapi, streams market data, runs trading engine.
//! Full pipeline: IBKR bars → Universe filter → Python Brain → RiskArbiter → broker.
//!
//! Usage: aegis [--config-dir PATH] [--wal-dir PATH]
//!
//! IS_LIVE = false (H20). This binary is for paper trading only.

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use rust_core::broker::BrokerAdapter;
use rust_core::channel::{ChannelConfig, TickChannel};
use rust_core::clock::Clock;
use rust_core::config_loader::EngineConfig;
use rust_core::engine::Engine;
use rust_core::ibkr_broker::{ContractMapping, IbkrBroker, IbkrBrokerConfig};
use rust_core::ouroboros_loader;
use rust_core::python_bridge::{PythonBridge, TickContext};
use rust_core::python_subprocess_manager::{PythonSubprocessManager, RespawnDecision};
use rust_core::types::{MarketTick, RiskRegime, TickerId, WalPayload};
use rust_core::universe::{RouteResult, UniverseClass};
use rust_core::wal_writer::WalWriter;

/// IS_LIVE = false (H20). Hardcoded for safety.
const IS_LIVE: bool = false;

/// Reconciliation interval in nanoseconds (5 minutes).
const RECONCILE_INTERVAL_NS: u64 = 300_000_000_000;

/// Main event loop tick interval in milliseconds.
const LOOP_INTERVAL_MS: u64 = 100;

/// State hash interval in nanoseconds (1 hour).
const STATE_HASH_INTERVAL_NS: u64 = 3_600_000_000_000;

fn main() {
    eprintln!("╔══════════════════════════════════════════╗");
    eprintln!("║  AEGIS V2 — Paper Engine                 ║");
    eprintln!("║  IS_LIVE = false (H20)                   ║");
    eprintln!("║  Mode: Crucible (paper, max_positions=1) ║");
    eprintln!("╚══════════════════════════════════════════╝");

    if IS_LIVE {
        eprintln!("FATAL: IS_LIVE=true is not permitted. Aborting.");
        std::process::exit(1);
    }

    // Parse args
    let args: Vec<String> = std::env::args().collect();
    let config_dir = find_arg(&args, "--config-dir")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("config"));
    let wal_dir = find_arg(&args, "--wal-dir")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("events"));
    let ibkr_host = find_arg(&args, "--ibkr-host").unwrap_or_else(|| "127.0.0.1".to_string());
    let ibkr_port: u16 = find_arg(&args, "--ibkr-port")
        .and_then(|s| s.parse().ok())
        .unwrap_or(4004);

    // Load configuration
    eprintln!("Loading config from {:?}...", config_dir);
    let config = match EngineConfig::load(&config_dir) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("FATAL: Config load failed: {e}");
            std::process::exit(1);
        }
    };
    eprintln!(
        "Config: {} tickers, {} contracts, paper_mode={}",
        config.tickers.len(),
        config.contracts.len(),
        config.crucible.paper_mode,
    );

    // Load Ouroboros artifacts (safe fallback to defaults)
    let dw = ouroboros_loader::load_dynamic_weights(&config_dir);
    let uc = ouroboros_loader::load_universe_classification(&config_dir);
    eprintln!(
        "Ouroboros: WR={:.1}%, chandelier_mult={:.2}, tiers=[{},{},{}]",
        dw.bayesian_win_rate * 100.0,
        dw.chandelier_atr_mult,
        uc.tier1.len(),
        uc.tier2.len(),
        uc.tier3.len(),
    );

    // Build leverage map for Python bridge (ticker_id → leverage factor)
    let mut leverage_map: HashMap<TickerId, u32> = HashMap::new();
    for (idx, contract) in config.contracts.iter().enumerate() {
        leverage_map.insert(TickerId(idx as u32), contract.leverage as u32);
    }

    // Create WAL writer
    std::fs::create_dir_all(&wal_dir).unwrap_or_else(|e| {
        eprintln!("FATAL: Cannot create WAL dir {:?}: {e}", wal_dir);
        std::process::exit(1);
    });
    let wal_path = wal_dir.join("current.ndjson");
    let dl_path = wal_dir.join("dead_letter");
    let mut wal = match WalWriter::open_file(&wal_path, &dl_path) {
        Ok(w) => w,
        Err(e) => {
            eprintln!("FATAL: WAL open failed: {e}");
            std::process::exit(1);
        }
    };
    // Inject disk space checker (H25): parse `df` output for free percentage
    let wal_dir_clone = wal_dir.clone();
    wal.disk_check_fn = Some(Box::new(move || {
        let output = std::process::Command::new("df")
            .arg("--output=pcent")
            .arg(&wal_dir_clone)
            .output();
        match output {
            Ok(o) => {
                let stdout = String::from_utf8_lossy(&o.stdout);
                // Parse "Use%" line, e.g. " 42%"
                for line in stdout.lines().skip(1) {
                    let trimmed = line.trim().trim_end_matches('%');
                    if let Ok(used) = trimmed.parse::<f64>() {
                        return 100.0 - used;
                    }
                }
                100.0 // Assume OK if parsing fails
            }
            Err(_) => 100.0,
        }
    }));
    eprintln!("WAL: {:?} (disk check enabled)", wal_path);

    // Create IBKR broker
    let broker_config = IbkrBrokerConfig {
        host: ibkr_host,
        port: ibkr_port,
        client_id: config.ibkr.client_id_executioner,
        rate_limit_per_sec: config.ibkr.rate_limit_msgs_per_sec,
        heartbeat_timeout_ns: 60_000_000_000,
    };
    let mut broker = IbkrBroker::new(broker_config);

    // Register contract mappings (symbol → TickerId)
    for (idx, contract) in config.contracts.iter().enumerate() {
        let ibkr_symbol = contract.symbol.strip_suffix(".L").unwrap_or(&contract.symbol);
        broker.register_contract(ContractMapping {
            ticker_id: TickerId(idx as u32),
            symbol: contract.symbol.clone(),
            ibkr_symbol: ibkr_symbol.to_string(),
            exchange: contract.exchange.clone(),
            currency: contract.currency.clone(),
        });
    }
    eprintln!("Registered {} contract mappings", config.contracts.len());

    // Connect to IB Gateway (retry with exponential backoff + jitter, max 5 attempts)
    eprintln!("Connecting to IB Gateway...");
    let max_retries = 5;
    for attempt in 1..=max_retries {
        match broker.connect() {
            Ok(()) => break,
            Err(e) => {
                eprintln!(
                    "IB Gateway connection attempt {attempt}/{max_retries} failed: {e}"
                );
                if attempt == max_retries {
                    eprintln!("FATAL: All connection attempts exhausted.");
                    eprintln!("Is IB Gateway running on port {ibkr_port}?");
                    std::process::exit(1);
                }
                // P1-05: Exponential backoff with deterministic jitter.
                // Base: 5s * attempt. Jitter: hash(attempt) mod 3s to prevent
                // thundering herd if multiple instances retry simultaneously.
                let base_secs = 5 * attempt as u64;
                let jitter_secs = (attempt as u64 * 7 + 3) % 4; // 0-3s deterministic jitter
                let delay = std::time::Duration::from_secs(base_secs + jitter_secs);
                eprintln!("Retrying in {}s ({}s base + {}s jitter)...", delay.as_secs(), base_secs, jitter_secs);
                std::thread::sleep(delay);
            }
        }
    }

    // Subscribe to market data for all contracts
    let sub_count = broker.subscribe_all();
    eprintln!("Market data: subscribed to {sub_count} bar streams");

    // P0-01: Subscribe to L1 tick-by-tick bid/ask for real spread data.
    let l1_count = broker.subscribe_all_l1();
    eprintln!("Market data: subscribed to {l1_count} L1 bid/ask streams");

    // Create tick channel for backpressure monitoring (Phase 6A)
    let mut tick_channel = TickChannel::new(ChannelConfig::default());

    // Start Python Brain bridge subprocess with lifecycle manager (RM-5)
    let mut subprocess_mgr = PythonSubprocessManager::new();
    let mut python_bridge = match PythonBridge::start() {
        Ok(mut bridge) => {
            bridge.leverage_map = leverage_map.clone();
            subprocess_mgr.mark_started();
            eprintln!("Python Brain: bridge started");
            Some(bridge)
        }
        Err(e) => {
            eprintln!("WARNING: Python Brain bridge failed to start: {e}");
            eprintln!("Engine will run without signal generation (dry run mode).");
            None
        }
    };
    // Nanosecond timestamp of next allowed respawn attempt (0 = immediate).
    let mut next_respawn_ns: u64 = 0;

    // Create engine
    let clock = Clock::new(config.holidays.clone());
    let mut engine = Engine::new(broker, config, Some(wal), clock);

    // Apply Ouroboros DynamicWeights to engine subsystems
    engine.exit_engine.strategy_mut().set_trail_atr(dw.chandelier_atr_mult);
    engine.arbiter.regime_scales = dw.regime_scales.clone();
    engine.arbiter.kelly_fractions = dw.kelly_fractions.clone();
    eprintln!(
        "DynamicWeights APPLIED: chandelier_atr_mult={:.2}, regime_scales={}, kelly_fractions={}",
        dw.chandelier_atr_mult,
        dw.regime_scales.len(),
        dw.kelly_fractions.len(),
    );

    // Register tickers in engine's universe
    // All 12 ISA contracts are Vanguard (continuous) in Crucible phase.
    // Ouroboros tier classification uses ticker IDs (i64), not symbols.
    for (idx, contract) in engine.config.contracts.iter().enumerate() {
        let tid_i64 = idx as i64;
        let class = if uc.tier3.contains(&tid_i64) || uc.locked.contains(&tid_i64) {
            UniverseClass::Apex // Tier 3 / locked → reduced monitoring
        } else {
            UniverseClass::Vanguard // Tier 1, Tier 2, or unclassified → continuous
        };
        engine
            .universe
            .register(&contract.symbol, class);
        engine
            .bar_history
            .insert(TickerId(idx as u32), rust_core::engine::BarHistory::new(500));
    }

    // Load WAL events for replay
    let wal_events = rust_core::wal_replay::read_wal_file(&wal_path).unwrap_or_default();

    // Get broker time for clock sync
    let system_ns = now_ns();
    let broker_secs = system_ns / 1_000_000_000;

    // Run startup sequence
    eprintln!("Running 8-step startup sequence...");
    match engine.startup(&wal_events, broker_secs, system_ns) {
        Ok(result) => {
            eprintln!("STARTUP COMPLETE:");
            eprintln!("  WAL events replayed: {}", result.wal_events_replayed);
            eprintln!("  Positions reconciled: {}", result.positions_reconciled);
            eprintln!("  Orphans found: {}", result.orphans_found);
            eprintln!("  Clock offset: {:.3}s", result.clock_offset_secs);
            eprintln!("  Tickers registered: {}", result.tickers_registered);
        }
        Err(e) => {
            eprintln!("FATAL: Startup failed: {e}");
            std::process::exit(1);
        }
    }

    // Install signal handler for graceful shutdown
    let running = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(true));
    let r = running.clone();
    if let Err(e) = ctrlc::set_handler(move || {
        eprintln!("\nSIGINT received, shutting down...");
        r.store(false, std::sync::atomic::Ordering::SeqCst);
    }) {
        eprintln!("WARNING: Could not set signal handler: {e}");
    }

    // Main event loop
    eprintln!("Engine running. Ctrl+C to stop.");
    let mut last_reconcile = now_ns();
    let mut last_state_hash = now_ns();
    let mut tick_count: u64 = 0;
    let mut total_ticks: u64 = 0;
    let mut signals_generated: u64 = 0;
    let mut ticks_filtered: u64 = 0;
    let mut consecutive_no_signal: u64 = 0;
    let mut last_regime = engine.arbiter.regime;

    while running.load(std::sync::atomic::Ordering::SeqCst) {
        let loop_start = now_ns();
        engine.now_ns = loop_start;
        engine.broker.set_time_ns(loop_start);

        // P2-C: Daily reset check (date-based, not time-based).
        let _utc_secs = (loop_start / 1_000_000_000) as u32 % 86400;
        let current_date = {
            let total_days = loop_start / 1_000_000_000 / 86400;
            // Simple days-since-epoch → YYYY-MM-DD (good enough for date comparison)
            format!("day-{total_days}")
        };
        engine.maybe_daily_reset(&current_date);

        // Poll market data ticks (non-blocking)
        engine.broker.poll_ticks();
        let ticks: Vec<MarketTick> = engine.broker.drain_ticks();

        // Drain bar high/low data for ATR computation
        let bar_data: HashMap<TickerId, Vec<(f64, f64)>> =
            std::mem::take(&mut engine.broker.bar_high_low);

        // Update bar history for ATR calculation
        for (tid, bars) in &bar_data {
            for &(high, low) in bars {
                let close = engine.last_prices.get(tid).copied().unwrap_or(high);
                engine.update_bar_data(*tid, high, low, close, 0);
            }
        }

        // Python bridge health check + respawn (RM-5)
        // If bridge is dead and cooldown has elapsed, attempt restart.
        if python_bridge.is_none() && loop_start >= next_respawn_ns {
            match PythonBridge::start() {
                Ok(mut bridge) => {
                    bridge.leverage_map = leverage_map.clone();
                    subprocess_mgr.mark_started();
                    python_bridge = Some(bridge);
                    consecutive_no_signal = 0;
                    eprintln!("Python Brain: bridge RESPAWNED successfully");
                }
                Err(e) => {
                    let decision = subprocess_mgr.evaluate_exit(Some(255));
                    match decision {
                        RespawnDecision::SystemHalt { crashes_in_window } => {
                            eprintln!(
                                "FATAL: Python bridge fork bomb detected ({crashes_in_window} crashes in 60s) → HALT"
                            );
                            engine.arbiter.regime = RiskRegime::Halt;
                            running.store(false, std::sync::atomic::Ordering::SeqCst);
                        }
                        RespawnDecision::RespawnAfter(delay) => {
                            next_respawn_ns = loop_start + delay.as_nanos() as u64;
                            eprintln!(
                                "Python Brain: respawn failed ({e}), retry in {}s",
                                delay.as_secs()
                            );
                        }
                        RespawnDecision::Fatal { exit_code } => {
                            eprintln!(
                                "Python Brain: fatal exit code {exit_code:?} — no more respawns"
                            );
                            // Set respawn far in the future to prevent retry
                            next_respawn_ns = u64::MAX;
                        }
                    }
                }
            }
        }

        // Check if bridge subprocess has died (stdin/stdout broken)
        // This is detected when evaluate_tick returns None AND we expected a signal.
        // Tracked via consecutive_no_signal below.

        for tick in ticks {
            total_ticks += 1;

            // Route through Universe filter (Phase 6A)
            let route = engine.route_tick(&tick);

            match route {
                Some(RouteResult::Vanguard(t)) => {
                    // P4-A: Record tick route in telemetry
                    engine.telemetry.ticks_routed_vanguard.inc();
                    // Send to tick channel for backpressure monitoring
                    tick_channel.send_or_drop_oldest(t.clone(), loop_start);

                    // Check channel health → escalate risk regime if needed
                    if let Some(escalation) = tick_channel.check_health()
                        && escalation > engine.arbiter.regime {
                            eprintln!(
                                "CHANNEL: backpressure escalation → {:?} (depth={})",
                                escalation,
                                tick_channel.len()
                            );
                            engine.arbiter.regime = escalation;
                        }

                    // Build context for Python bridge
                    let time_secs = engine.clock.now_london_secs(loop_start);
                    let time_fraction = Clock::time_of_day_fraction(time_secs);
                    let spread_pct = if t.bid > 0.0 {
                        (t.ask - t.bid) / t.bid * 100.0
                    } else {
                        0.1
                    };

                    let ctx = TickContext {
                        win_rate: dw.bayesian_win_rate,
                        total_trades: dw.trade_count,
                        avg_win: 0.02, // Updated by Ouroboros nightly
                        avg_loss: 0.02,
                        leverage: engine
                            .config
                            .contracts
                            .get(t.ticker_id.0 as usize)
                            .map(|c| c.leverage as u32)
                            .unwrap_or(3),
                        realized_vol: engine
                            .bar_history
                            .get(&t.ticker_id)
                            .map(|h| h.realized_vol(6120.0)) // 5s bars, 8.5h LSE day
                            .unwrap_or(0.30),
                        // P5-D: Use Hayashi-Yoshida computed correlation (0.0 in Crucible cold start)
                        correlation: engine.hy_engine.avg_correlation(t.ticker_id),
                        drawdown_pct: engine.portfolio.daily_drawdown_pct(),
                        amihud: engine
                            .bar_history
                            .get(&t.ticker_id)
                            .map(|h| h.amihud())
                            .unwrap_or(0.0),
                        regime: engine.arbiter.regime,
                        spread_pct,
                        time_fraction,
                        heat_pct: engine.portfolio.portfolio_heat_pct(),
                        equity: engine.portfolio.equity,
                    };

                    // Evaluate via Python Brain (if available)
                    let signal = if let Some(ref mut bridge) = python_bridge {
                        let high = engine
                            .bar_history
                            .get(&t.ticker_id)
                            .map(|h| h.last_high())
                            .unwrap_or(t.last);
                        let low = engine
                            .bar_history
                            .get(&t.ticker_id)
                            .map(|h| h.last_low())
                            .unwrap_or(t.last);
                        bridge.evaluate_tick(&t, high, low, &ctx)
                    } else {
                        None
                    };

                    if signal.is_some() {
                        signals_generated += 1;
                        consecutive_no_signal = 0;
                    } else if python_bridge.is_some()
                        && engine.current_mode.allows_entries()
                    {
                        consecutive_no_signal += 1;
                        // Signal drought detection: >5000 ticks with no signal
                        // during entry-allowed hours means Python is likely broken
                        if consecutive_no_signal == 5000 {
                            eprintln!(
                                "WARNING: SIGNAL DROUGHT — {} consecutive ticks with no signal during ModeB. Python bridge may be broken.",
                                consecutive_no_signal
                            );
                        }
                    }

                    // Process tick with signal through engine pipeline
                    engine.process_tick_with_signal(t, signal);
                }
                Some(RouteResult::Apex(t)) => {
                    // P4-A: Record tick route in telemetry
                    engine.telemetry.ticks_routed_apex.inc();

                    // P3-B: During MODE_A, accumulate 60-second OHLCV snapshots for ApexScout.
                    let snapshot_ready = engine.record_apex_snapshot(&t);

                    if snapshot_ready && matches!(engine.current_mode, rust_core::clock::TradingMode::ModeA) {
                        // 60-second window completed — evaluate via ApexScout
                        let snapshots = engine.get_apex_snapshots(t.ticker_id);

                        if !snapshots.is_empty() {
                            let signal = if let Some(ref mut bridge) = python_bridge {
                                bridge.evaluate_apex_snapshot(t.ticker_id, snapshots)
                            } else {
                                None
                            };

                            if let Some(apex_signal) = signal
                                && apex_signal.confidence > 50.0 {
                                    // ApexScout generated a tradeable signal (>50% confidence)
                                    eprintln!(
                                        "APEX: ModeA signal on ticker={}, confidence={:.1}%, kelly={:.3}, strategy={}",
                                        t.ticker_id.0, apex_signal.confidence, apex_signal.kelly_fraction, apex_signal.strategy
                                    );

                                    // Build RiskArbiter evaluation context
                                    let time_secs = engine.clock.now_london_secs(engine.now_ns);
                                    let eval_ctx = rust_core::risk_arbiter::EvalContext {
                                        time_secs,
                                        last_tick_age_secs: 0,
                                        bid: t.bid,
                                        ask: t.ask,
                                        broker_connected: engine.broker.is_connected(),
                                        wal_available: engine.wal.is_some(),
                                        now_ns: engine.now_ns,
                                        volatilities: std::collections::HashMap::new(),
                                        ticker_halted: false,
                                        garch_sigma: engine.garch_registry.sigma(t.ticker_id).unwrap_or(0.30),
                                        leverage_factor: 3,
                                        scanner_score: apex_signal.confidence,
                                        kelly_fraction_raw: apex_signal.kelly_fraction,
                                        macro_indicator: *engine.macro_regime.indicator(),
                                        macro_stale_threshold_ns: 300_000_000_000,
                                    };

                                    // Check risk arbiter approval
                                    let direction = if apex_signal.direction == "Long" {
                                        rust_core::types::Direction::Long
                                    } else {
                                        rust_core::types::Direction::Short
                                    };

                                    let risk_decision = engine.arbiter.evaluate(
                                        t.ticker_id,
                                        direction,
                                        apex_signal.confidence,
                                        apex_signal.kelly_fraction,
                                        &engine.portfolio,
                                        &eval_ctx,
                                    );

                                    // Check if decision is approved
                                    if risk_decision.approved {
                                        signals_generated += 1;
                                        consecutive_no_signal = 0;

                                        // Use the risk-adjusted kelly fraction from the decision
                                        let approved_kelly = (risk_decision.adjusted_size / engine.portfolio.equity).clamp(0.0, 1.0);

                                        // Calculate shares from Apex Kelly fraction
                                        let shares = ((approved_kelly * engine.portfolio.equity) / t.last).max(1.0) as u32;

                                        eprintln!(
                                            "APEX: {} {} shares @{:.2} (kelly={:.3})",
                                            if approved_kelly > 0.0 { "BUY" } else { "SELL" },
                                            shares, t.last, approved_kelly
                                        );

                                        // Route to execution (reuses existing process_tick_with_signal path)
                                        let brain_signal = rust_core::python_bridge::BrainSignal {
                                            direction: apex_signal.direction,
                                            confidence: apex_signal.confidence,
                                            kelly_fraction: approved_kelly,
                                            shares,
                                            strategy: "ApexScout".to_string(),
                                        };
                                        let ticker_id = t.ticker_id;
                                        engine.process_tick_with_signal(t, Some(brain_signal));
                                        engine.clear_apex_snapshots(ticker_id);
                                    } else {
                                        // Risk arbiter rejected the trade
                                        let ticker_id = t.ticker_id;
                                        engine.clear_apex_snapshots(ticker_id);
                                    }
                            }
                        }
                    }
                }
                Some(RouteResult::Filtered(_reason)) => {
                    ticks_filtered += 1;
                    // P4-A: Record filtered tick in telemetry
                    engine.telemetry.ticks_filtered.inc();
                }
                None => {}
            }
        }

        // FIX 2026-03-11: Detect regime changes and persist to WAL.
        // This catches all regime transitions (drawdown, consecutive loss, IBKR errors,
        // backpressure, etc.) and writes them so they survive restarts.
        if engine.arbiter.regime != last_regime {
            let from_str = format!("{:?}", last_regime);
            let to_str = format!("{:?}", engine.arbiter.regime);
            eprintln!("REGIME CHANGE: {from_str} → {to_str}");
            engine.write_wal(WalPayload::RiskStateChange {
                from: from_str,
                to: to_str,
                trigger: "engine_loop_detected".to_string(),
            });
            last_regime = engine.arbiter.regime;
        }

        // Poll broker events (order updates, connection status)
        engine.broker.poll_events();
        let events = engine.broker.drain_events();
        for ev in &events {
            engine.process_broker_event(ev);
        }

        // Periodic reconciliation (every 5 minutes)
        if loop_start - last_reconcile > RECONCILE_INTERVAL_NS {
            if let Err(e) = engine.reconcile() {
                eprintln!("Reconciliation error: {e}");
            }
            // P3-C: Check for stale orders and prune completed ones
            engine.check_executioner();
            last_reconcile = loop_start;
            tick_count += 1;

            // P4-A: Dump telemetry snapshot every reconciliation cycle (5 min)
            let snap = engine.telemetry.snapshot();
            eprintln!(
                "TELEMETRY: ticks={} filtered={} signals={} approved={} vetoed={} orders={} fills={} T2T p50={:.1}ms p99={:.1}ms",
                snap.ticks_received, snap.ticks_filtered,
                snap.signals_generated, snap.signals_approved, snap.signals_vetoed,
                snap.orders_submitted, snap.orders_filled,
                snap.t2t_p50_ms, snap.t2t_p99_ms,
            );
            // P7-B: Write telemetry snapshot JSON for dashboard consumption.
            // P21: Include session mode. P22: Include latency profiler stats.
            let session_mode = format!("{:?}", engine.session_mgr.mode());
            let sub_lines = engine.subscription_manager.active_line_count();
            let snap_json = format!(
                "{{\"ticks_received\":{},\"ticks_filtered\":{},\"signals_generated\":{},\"signals_approved\":{},\"signals_vetoed\":{},\"orders_submitted\":{},\"orders_filled\":{},\"t2t_p50_ms\":{:.2},\"t2t_p95_ms\":{:.2},\"t2t_p99_ms\":{:.2},\"regime\":\"{:?}\",\"positions\":{},\"equity\":{:.2},\"session_mode\":\"{}\",\"sub_lines\":{}}}",
                snap.ticks_received, snap.ticks_filtered,
                snap.signals_generated, snap.signals_approved, snap.signals_vetoed,
                snap.orders_submitted, snap.orders_filled,
                snap.t2t_p50_ms, snap.t2t_p95_ms, snap.t2t_p99_ms,
                engine.arbiter.regime, engine.positions.len(), engine.portfolio.equity,
                session_mode, sub_lines,
            );
            let snap_path = wal_dir.join("telemetry_snapshot.json");
            let _ = std::fs::write(&snap_path, &snap_json);

            if tick_count.is_multiple_of(12) {
                eprintln!(
                    "HEARTBEAT: {} cycles, {} ticks, {} signals, {} filtered, regime={:?}",
                    tick_count, total_ticks, signals_generated, ticks_filtered,
                    engine.arbiter.regime,
                );
            }
        }

        // Hourly state hash (H85)
        if loop_start - last_state_hash > STATE_HASH_INTERVAL_NS {
            engine.maybe_write_state_hash();
            last_state_hash = loop_start;
        }

        // Heartbeat
        let _ = engine.broker.heartbeat();

        // Sleep until next loop iteration
        let elapsed_ms = (now_ns() - loop_start) / 1_000_000;
        if elapsed_ms < LOOP_INTERVAL_MS {
            std::thread::sleep(std::time::Duration::from_millis(
                LOOP_INTERVAL_MS - elapsed_ms,
            ));
        }
    }

    // Graceful shutdown
    if let Some(ref mut bridge) = python_bridge {
        bridge.shutdown();
    }
    engine.shutdown();
    eprintln!(
        "Engine stopped. {} ticks, {} signals, {} filtered. Goodbye.",
        total_ticks, signals_generated, ticks_filtered,
    );
}

fn now_ns() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos() as u64
}

fn find_arg(args: &[String], flag: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == flag).map(|w| w[1].clone())
}
