//! AEGIS V2 — Paper Engine Binary
//! Connects to IB Gateway via ibapi, streams market data, runs trading engine.
//! Full pipeline: IBKR bars → Universe filter → Python Brain → RiskArbiter → broker.
//!
//! Usage: aegis [--config-dir PATH] [--wal-dir PATH]
//!
//! IS_LIVE = false (H20). This binary is for paper trading only.

#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
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

/// N10a: Kill switch check interval in nanoseconds (1 second).
/// Checks for /app/data/KILL and /app/data/PAUSE files.
const KILL_SWITCH_CHECK_NS: u64 = 1_000_000_000;

fn main() {
    eprintln!("╔══════════════════════════════════════════╗");
    eprintln!("║  AEGIS V2 — Paper Engine                 ║");
    eprintln!("║  IS_LIVE = false (H20)                   ║");
    eprintln!("║  Mode: Crucible (paper, simulation)      ║");
    eprintln!("╚══════════════════════════════════════════╝");

    if IS_LIVE {
        eprintln!("FATAL: IS_LIVE=true is not permitted. Aborting.");
        std::process::exit(1);
    }

    // RT1: Validate config.live.toml exists (pre-flight check for future live deployment).
    // Even in paper mode, assert the file exists so we catch config drift early.
    // When IS_LIVE is changed to true, this file provides production-safe overrides.
    {
        let live_config_path = std::path::Path::new("config/config.live.toml");
        if !live_config_path.exists() {
            eprintln!(
                "WARNING [RT1]: config/config.live.toml missing. \
                 Live deployment will be blocked until this file exists."
            );
        } else {
            eprintln!("RT1: config.live.toml present OK");
        }
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
        .unwrap_or(4003); // gnzsnz/ib-gateway paper API proxy port

    // Load configuration
    // N8a: In paper mode, load base config.toml only. In live mode, overlay config.live.toml.
    // Even in paper mode, validate that config.live.toml parses correctly (early error detection).
    eprintln!("Loading config from {:?}...", config_dir);
    let config = if IS_LIVE {
        match EngineConfig::load_live(&config_dir) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("FATAL: Live config load failed: {e}");
                std::process::exit(1);
            }
        }
    } else {
        // Paper mode: load base config only
        let cfg = match EngineConfig::load(&config_dir) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("FATAL: Config load failed: {e}");
                std::process::exit(1);
            }
        };
        // N8a pre-flight: validate config.live.toml parses (catch errors before live deployment)
        if let Err(e) = EngineConfig::load_live(&config_dir) {
            eprintln!("WARNING [N8a]: config.live.toml overlay failed to parse: {e} (paper mode, non-fatal)");
        }
        cfg
    };
    eprintln!(
        "Config: {} tickers, {} contracts, paper_mode={}",
        config.tickers.len(),
        config.contracts.len(),
        config.crucible.paper_mode,
    );

    // N8b: Live mode startup assertions — refuse to trade if params are unsafe
    if IS_LIVE {
        let r = &config.risk;
        if r.max_positions > 5 {
            eprintln!("FATAL [N8b]: max_positions={} exceeds live limit of 5", r.max_positions);
            std::process::exit(1);
        }
        if r.portfolio_heat_limit_pct > 20.0 {
            eprintln!("FATAL [N8b]: portfolio_heat={:.1}% exceeds live limit of 20%", r.portfolio_heat_limit_pct);
            std::process::exit(1);
        }
        if r.cash_buffer_pct < 15.0 {
            eprintln!("FATAL [N8b]: cash_buffer={:.1}% below live minimum of 15%", r.cash_buffer_pct);
            std::process::exit(1);
        }
        eprintln!(
            "N8b LIVE ASSERTIONS PASS: max_pos={}, heat={:.1}%, buffer={:.1}%",
            r.max_positions, r.portfolio_heat_limit_pct, r.cash_buffer_pct,
        );
    }

    // Load Ouroboros artifacts (safe fallback to defaults)
    let mut dw = ouroboros_loader::load_dynamic_weights(&config_dir);
    let uc = ouroboros_loader::load_universe_classification(&config_dir);
    let fx_live = ouroboros_loader::load_fx_rates(&config_dir);
    eprintln!(
        "Ouroboros: WR={:.1}%, chandelier_mult={:.2}, tiers=[{},{},{}], fx_rates={}",
        dw.bayesian_win_rate * 100.0,
        dw.chandelier_atr_mult,
        uc.tier1.len(),
        uc.tier2.len(),
        uc.tier3.len(),
        fx_live.rates.len(),
    );

    // Build leverage map for Python bridge (ticker_id → leverage factor)
    let mut leverage_map: HashMap<TickerId, u32> = HashMap::new();
    for (idx, contract) in config.contracts.iter().enumerate() {
        leverage_map.insert(TickerId(idx as u32), contract.leverage as u32);
    }

    // Create WAL writer — rotate stale WAL on startup
    std::fs::create_dir_all(&wal_dir).unwrap_or_else(|e| {
        eprintln!("FATAL: Cannot create WAL dir {:?}: {e}", wal_dir);
        std::process::exit(1);
    });
    let wal_path = wal_dir.join("current.ndjson");
    let dl_path = wal_dir.join("dead_letter");
    // Rotate: if current.ndjson exists and is non-empty, archive it so each engine
    // boot starts with a clean WAL. Prevents 14MB+ phantom data from old builds.
    if wal_path.exists() {
        if let Ok(meta) = std::fs::metadata(&wal_path) {
            if meta.len() > 0 {
                let archive_dir = wal_dir.join("archive");
                let _ = std::fs::create_dir_all(&archive_dir);
                let ts = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_secs())
                    .unwrap_or(0);
                let archive_name = format!("wal_{ts}.ndjson");
                let archive_path = archive_dir.join(&archive_name);
                match std::fs::rename(&wal_path, &archive_path) {
                    Ok(()) => eprintln!(
                        "WAL_ROTATE: archived {} ({} bytes) → {:?}",
                        wal_path.display(), meta.len(), archive_path,
                    ),
                    Err(e) => eprintln!("WAL_ROTATE: rename failed: {e} — continuing with append"),
                }
            }
        }
    }
    // Purge old WAL archives (keep last 7 days worth, ~7 files max).
    // Prevents unbounded disk fill from daily WAL rotation.
    {
        let archive_dir = wal_dir.join("archive");
        if archive_dir.exists() {
            let mut entries: Vec<_> = std::fs::read_dir(&archive_dir)
                .into_iter()
                .flatten()
                .filter_map(|e| e.ok())
                .filter(|e| {
                    e.path()
                        .extension()
                        .map_or(false, |ext| ext == "ndjson")
                })
                .collect();
            if entries.len() > 7 {
                // Sort by name (wal_TIMESTAMP.ndjson — oldest first)
                entries.sort_by_key(|e| e.file_name());
                let to_remove = entries.len() - 7;
                for entry in entries.into_iter().take(to_remove) {
                    let size = entry.metadata().map(|m| m.len()).unwrap_or(0);
                    match std::fs::remove_file(entry.path()) {
                        Ok(()) => eprintln!(
                            "WAL_PURGE: removed {:?} ({} bytes)",
                            entry.file_name(),
                            size,
                        ),
                        Err(e) => eprintln!("WAL_PURGE: failed to remove {:?}: {e}", entry.file_name()),
                    }
                }
            }
        }
    }
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
    // Derive IBKR-compatible symbol from our internal symbol:
    //   LSE: strip ".L" suffix (QQQ3.L → QQQ3)
    //   HKEX: strip leading zeros (0001 → 1, 0700 → 700)
    //   Others: use as-is
    for (idx, contract) in config.contracts.iter().enumerate() {
        let base_symbol = contract.symbol.strip_suffix(".L").unwrap_or(&contract.symbol);
        let ibkr_symbol = if contract.exchange == "HKEX" {
            base_symbol.trim_start_matches('0')
        } else {
            base_symbol
        };
        // Safety: if trimming zeros emptied the string (shouldn't happen), use "0"
        let ibkr_symbol = if ibkr_symbol.is_empty() { "0" } else { ibkr_symbol };
        broker.register_contract(ContractMapping {
            ticker_id: TickerId(idx as u32),
            symbol: contract.symbol.clone(),
            ibkr_symbol: ibkr_symbol.to_string(),
            exchange: contract.exchange.clone(),
            currency: contract.currency.clone(),
        });
    }
    eprintln!("Registered {} contract mappings", config.contracts.len());

    // Connect to IB Gateway with retry.
    // Paper mode: max 10 attempts then proceed without broker (idle until reconnect).
    // Live mode: infinite retry — never proceed without broker.
    let max_attempts: u64 = if config.crucible.paper_mode { 10 } else { u64::MAX };
    let mut broker_connected = false;
    eprintln!("Connecting to IB Gateway (max {} attempts)...", if max_attempts == u64::MAX { "∞".to_string() } else { max_attempts.to_string() });
    let mut attempt: u64 = 0;
    loop {
        attempt += 1;
        match broker.connect() {
            Ok(()) => {
                broker_connected = true;
                break;
            }
            Err(e) => {
                eprintln!(
                    "IB Gateway connection attempt {attempt} failed: {e}"
                );
                if attempt >= max_attempts {
                    eprintln!("WARNING: Max connection attempts reached. Proceeding without IB Gateway.");
                    eprintln!("Engine will idle until broker reconnects in main loop.");
                    break;
                }
                // Exponential backoff capped at 60s, with deterministic jitter 0-3s
                let base_secs = (5 * attempt).min(60);
                let jitter_secs = (attempt * 7 + 3) % 4; // 0-3s deterministic jitter
                let delay = std::time::Duration::from_secs(base_secs + jitter_secs);
                eprintln!("Retrying in {}s ({}s base + {}s jitter)...", delay.as_secs(), base_secs, jitter_secs);
                std::thread::sleep(delay);
            }
        }
    }

    // Subscribe to market data (only if broker connected)
    // CRITICAL FIX: Wait for IBKR secdef farms to initialize before subscribing.
    // Without this delay, reqMktData for LSE (LSEETF) and KRX (KSE) tickers fails
    // with code 200 "No security definition found" because secdefeu connects last.
    // TSE/HKEX work because jfarm connects first. 15s gives all farms time to ready.
    if broker_connected {
        eprintln!("Waiting 15s for IBKR secdef farms to initialize (LSE/KRX need secdefeu)...");
        std::thread::sleep(std::time::Duration::from_secs(15));
        eprintln!("Secdef wait complete, subscribing market data...");
        let sub_count = broker.subscribe_all();
        eprintln!("Market data: subscribed to {sub_count} streams");
    } else {
        eprintln!("Market data: skipped (no broker connection)");
    }

    // P0-01: Subscribe to L1 tick-by-tick bid/ask for top 2 LSE ETPs only.
    // IBKR paper limits tick-by-tick to ~2 concurrent (error 10190 at higher counts).
    // 2 core instruments: QQQ3.L, 3LUS.L (highest liquidity).
    // Mode rotation (P21) will subscribe/unsubscribe L1 as sessions change.
    if broker_connected {
        let l1_core: Vec<&str> = vec!["QQQ3.L", "3LUS.L"];
        let lse_tids: Vec<rust_core::types::TickerId> = l1_core.iter()
            .filter_map(|sym| broker.contract_map_keys().iter()
                .find(|&&tid| broker.symbol_for(tid).map_or(false, |s| s == *sym))
                .copied())
            .collect();
        let l1_count = broker.subscribe_l1_batch(&lse_tids);
        eprintln!("Market data: subscribed to {l1_count} L1 bid/ask streams (top 2 LSE ETPs)");
    }

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

    // Propagate simulation mode to risk arbiter (relaxes cash buffer check)
    engine.arbiter.simulation_mode = engine.simulation_mode;
    engine.arbiter.paper_uses_live_gates = engine.config.crucible.paper_uses_live_gates;

    // Apply Ouroboros DynamicWeights to engine subsystems
    engine.exit_engine.strategy_mut().set_trail_atr(dw.chandelier_atr_mult);
    engine.arbiter.regime_scales = dw.regime_scales.clone();
    engine.arbiter.kelly_fractions = dw.kelly_fractions.clone();
    engine.arbiter.ticker_blacklist = dw.ticker_blacklist.clone();
    if !dw.ticker_blacklist.is_empty() {
        eprintln!("OUROBOROS: {} tickers blacklisted: {:?}", dw.ticker_blacklist.len(), dw.ticker_blacklist);
    }
    // Apply live FX rates from fx_rates.toml (Ouroboros 6-hour refresh)
    engine.fx_table.apply_live_rates(&fx_live.rates, now_ns());

    // P1-2.15: Load economic calendar for macro event blackout windows.
    engine.economic_calendar = rust_core::config_loader::load_economic_calendar(&config_dir);

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

    // Load WAL events for replay (current + ALL archives — no trades lost across restarts)
    let wal_events = rust_core::wal_replay::read_all_wal_files(&wal_dir);

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
    let running = Arc::new(AtomicBool::new(true));
    let r = running.clone();
    if let Err(e) = ctrlc::set_handler(move || {
        eprintln!("\nSIGINT received, shutting down...");
        r.store(false, Ordering::SeqCst);
    }) {
        eprintln!("WARNING: Could not set signal handler: {e}");
    }

    // SIGHUP handler for hot-reloading contracts.toml (sent by contract_expander.py)
    let reload_flag = Arc::new(AtomicBool::new(false));
    if let Err(e) = signal_hook::flag::register(signal_hook::consts::SIGHUP, Arc::clone(&reload_flag)) {
        eprintln!("WARNING: Could not register SIGHUP handler: {e}");
    } else {
        eprintln!("SIGHUP handler registered (hot-reload contracts.toml)");
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
    let mut last_reconnect_ns: u64 = 0;
    let reconnect_interval_ns: u64 = 60_000_000_000; // 60s between reconnect attempts
    let mut last_kill_check_ns: u64 = 0;
    let mut paused = false;

    // N10a: Data directory for kill switch files
    let data_dir = std::path::PathBuf::from(
        std::env::var("AEGIS_DATA_DIR").unwrap_or_else(|_| "/app/data".to_string()),
    );
    let kill_file = data_dir.join("KILL");
    let pause_file = data_dir.join("PAUSE");

    while running.load(std::sync::atomic::Ordering::SeqCst) {
        let loop_start = now_ns();
        engine.now_ns = loop_start;
        engine.broker.set_time_ns(loop_start);

        // N10a: Kill switch — check for KILL/PAUSE files (1-second interval)
        if loop_start - last_kill_check_ns > KILL_SWITCH_CHECK_NS {
            last_kill_check_ns = loop_start;

            // KILL file: immediate graceful shutdown
            if kill_file.exists() {
                eprintln!("N10a KILL SWITCH: /app/data/KILL detected — initiating graceful shutdown");
                // Remove the file so next boot doesn't immediately kill again
                let _ = std::fs::remove_file(&kill_file);
                running.store(false, Ordering::SeqCst);
                continue;
            }

            // PAUSE file: freeze signal generation (keep market data flowing)
            let was_paused = paused;
            paused = pause_file.exists();
            if paused && !was_paused {
                eprintln!("N10a PAUSE: /app/data/PAUSE detected — signal generation frozen (market data continues)");
            } else if !paused && was_paused {
                eprintln!("N10a RESUME: /app/data/PAUSE removed — signal generation resumed");
            }
        }

        // N10a: When paused, skip signal processing but keep market data flowing
        if paused {
            engine.broker.poll_ticks();
            let _ticks: Vec<MarketTick> = engine.broker.drain_ticks();
            let _ = engine.broker.heartbeat();
            let elapsed_ms = (now_ns() - loop_start) / 1_000_000;
            if elapsed_ms < LOOP_INTERVAL_MS {
                std::thread::sleep(std::time::Duration::from_millis(
                    LOOP_INTERVAL_MS - elapsed_ms,
                ));
            }
            continue;
        }

        // P0-1.5: Broker reconnection with full state recovery.
        // Checks both initial connect failure AND mid-session disconnects.
        let actually_connected = engine.broker.is_connected();
        if !actually_connected && broker_connected {
            // Broker was connected but dropped — log critical + start reconnect cycle
            eprintln!(
                "CRITICAL: Broker connection LOST (had {} open positions) — entering reconnect cycle",
                engine.portfolio.filled_count(),
            );
            broker_connected = false;
        }
        if !broker_connected && loop_start - last_reconnect_ns > reconnect_interval_ns {
            last_reconnect_ns = loop_start;
            match engine.broker.connect() {
                Ok(()) => {
                    broker_connected = true;
                    // Wait for secdef farms before subscribing (same race condition as startup)
                    eprintln!("BROKER RECONNECTED: waiting 15s for secdef farms...");
                    std::thread::sleep(std::time::Duration::from_secs(15));
                    let sub_count = engine.broker.subscribe_all();
                    eprintln!("BROKER RECONNECTED: subscribed to {sub_count} bar streams");

                    // P0-1.5: Post-reconnect position reconciliation.
                    // If we have open positions, we MUST verify they still exist at the broker.
                    if engine.portfolio.filled_count() > 0 {
                        eprintln!("BROKER RECONNECT: Reconciling {} open positions...", engine.portfolio.filled_count());
                        match engine.broker.request_positions() {
                            Ok(broker_positions) => {
                                let recon = rust_core::reconciler::reconcile_positions(
                                    &engine.portfolio, &broker_positions
                                );
                                if !recon.is_clean {
                                    eprintln!(
                                        "CRITICAL: Post-reconnect reconciliation found {} mismatches! Escalating to FLATTEN.",
                                        recon.mismatches.len(),
                                    );
                                    for m in &recon.mismatches {
                                        eprintln!("  MISMATCH: {m:?}");
                                    }
                                    engine.arbiter.regime = RiskRegime::Flatten;
                                } else {
                                    eprintln!("BROKER RECONNECT: Reconciliation clean ({} positions verified)", recon.matches);
                                }
                            }
                            Err(e) => {
                                eprintln!("CRITICAL: Post-reconnect position request failed: {e} — positions may be stale");
                            }
                        }
                    }
                }
                Err(e) => {
                    // Log every 5th failure to avoid spam
                    static RECONNECT_FAILS: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
                    let count = RECONNECT_FAILS.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
                    if count <= 3 || count % 5 == 0 {
                        eprintln!("BROKER RECONNECT: attempt #{count} failed: {e}");
                    }
                }
            }
        }

        // P2-C: Daily reset check (date-based, not time-based).
        let _utc_secs = (loop_start / 1_000_000_000) as u32 % 86400;
        let current_date = {
            let total_days = loop_start / 1_000_000_000 / 86400;
            // Simple days-since-epoch → YYYY-MM-DD (good enough for date comparison)
            format!("day-{total_days}")
        };
        engine.maybe_daily_reset(&current_date);

        // HOT-RELOAD: Check if contract_expander.py signaled us via SIGHUP
        if reload_flag.compare_exchange(true, false, Ordering::SeqCst, Ordering::Relaxed).is_ok() {
            eprintln!("SIGHUP received — hot-reloading contracts.toml...");
            match EngineConfig::load_contracts_standalone(&config_dir.join("contracts.toml")) {
                Ok(new_contracts) => {
                    let existing_count = engine.broker.contract_map_keys().len();
                    let mut added = 0u32;
                    for (idx, contract) in new_contracts.iter().enumerate() {
                        let tid = TickerId(idx as u32);
                        // Skip already-registered contracts
                        if engine.broker.symbol_for(tid).is_some() {
                            continue;
                        }
                        // New contract — register it
                        let base_symbol = contract.symbol.strip_suffix(".L").unwrap_or(&contract.symbol);
                        let ibkr_symbol = if contract.exchange == "HKEX" {
                            base_symbol.trim_start_matches('0')
                        } else {
                            base_symbol
                        };
                        let ibkr_symbol = if ibkr_symbol.is_empty() { "0" } else { ibkr_symbol };
                        engine.broker.register_contract(ContractMapping {
                            ticker_id: tid,
                            symbol: contract.symbol.clone(),
                            ibkr_symbol: ibkr_symbol.to_string(),
                            exchange: contract.exchange.clone(),
                            currency: contract.currency.clone(),
                        });
                        // Register in engine universe + bar history
                        engine.universe.register(&contract.symbol, UniverseClass::Vanguard);
                        engine.bar_history.insert(tid, rust_core::engine::BarHistory::new(500));
                        // Update leverage map for Python bridge
                        leverage_map.insert(tid, contract.leverage as u32);
                        if let Some(ref mut bridge) = python_bridge {
                            bridge.leverage_map.insert(tid, contract.leverage as u32);
                        }
                        // Subscribe to IBKR market data (best effort — may hit cap)
                        if broker_connected {
                            let _ = engine.broker.subscribe_mktdata(tid);
                        }
                        added += 1;
                    }
                    eprintln!(
                        "HOT-RELOAD: {} new contracts registered (was {}, now {})",
                        added, existing_count, engine.broker.contract_map_keys().len(),
                    );
                }
                Err(e) => {
                    eprintln!("HOT-RELOAD: Failed to parse contracts.toml: {e}");
                }
            }

            // HOT-RELOAD: Also reload dynamic_weights.toml (config_writer sends SIGHUP after writing)
            let new_dw = ouroboros_loader::load_dynamic_weights(&config_dir);
            if (new_dw.chandelier_atr_mult - dw.chandelier_atr_mult).abs() > 1e-6
                || (new_dw.bayesian_win_rate - dw.bayesian_win_rate).abs() > 1e-6
                || new_dw.kelly_fractions != dw.kelly_fractions
            {
                engine.exit_engine.strategy_mut().set_trail_atr(new_dw.chandelier_atr_mult);
                engine.arbiter.regime_scales = new_dw.regime_scales.clone();
                engine.arbiter.kelly_fractions = new_dw.kelly_fractions.clone();
                engine.arbiter.ticker_blacklist = new_dw.ticker_blacklist.clone();
                eprintln!(
                    "HOT-RELOAD: dynamic_weights updated — WR={:.1}%, chandelier={:.2}, kelly_t1={:.4}",
                    new_dw.bayesian_win_rate * 100.0,
                    new_dw.chandelier_atr_mult,
                    new_dw.kelly_fractions.get("t1").copied().unwrap_or(0.0),
                );
                dw = new_dw;
            } else {
                eprintln!("HOT-RELOAD: dynamic_weights unchanged (no update needed)");
            }

            // P1-2.1: Hot-reload FX rates from fx_rates.toml (Python cron updates every 6h + sends SIGHUP)
            let new_fx = ouroboros_loader::load_fx_rates(&config_dir);
            if !new_fx.rates.is_empty() {
                engine.fx_table.apply_live_rates(&new_fx.rates, now_ns());
                eprintln!("HOT-RELOAD: FX rates updated ({} pairs)", new_fx.rates.len());
            }

            // N5c: Kill and respawn Python bridge so it picks up fresh config
            // (bridge caches dynamic_weights.toml at startup — no hot-reload in Python)
            if python_bridge.is_some() {
                eprintln!("HOT-RELOAD [N5c]: recycling Python bridge to pick up new config...");
                python_bridge = None; // Drop triggers shutdown() + process kill
                // RM-5 respawn logic (line ~548) will restart it on next loop iteration
            }
        }

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
                                    let ticker_score = engine.predictive_scorer.score(t.ticker_id);
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
                                        ticker_ic: ticker_score.map_or(0.0, |s| s.ic),
                                        ticker_trade_count: ticker_score.map_or(0, |s| s.trade_count),
                                        ticker_locked: ticker_score.map_or(false, |s| s.locked),
                                        ticker_position_count: engine.portfolio.position_count_for(&t.ticker_id),
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
                                            rvol: 0.0,
                                            hurst: 0.0,
                                            adx: 0.0,
                                            vol_slope: 0.0,
                                            vwap_dist_pct: 0.0,
                                            structural_score: 0.0,
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

        // Drain tick channel after processing batch to prevent unbounded growth.
        // The channel is used purely for health monitoring (depth/drop tracking),
        // not as a processing queue. Without draining, it fills to 50K and triggers
        // spurious Reduce/Halt regime escalations.
        let drained = tick_channel.recv_batch(tick_channel.len().min(10_000));
        drop(drained);

        // FIX 2026-03-11: Detect regime changes and persist to WAL.
        // This catches all regime transitions (drawdown, consecutive loss, IBKR errors,
        // backpressure, etc.) and writes them so they survive restarts.
        // Skip WAL persistence in simulation mode — regime is always force-reset to Normal.
        if engine.arbiter.regime != last_regime && !engine.simulation_mode {
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

        // P1-2.16: Drawdown velocity halt — check if equity dropped >2% in 1 hour.
        {
            let eq = engine.portfolio.equity;
            engine.arbiter.record_equity_snapshot(loop_start, eq);
            engine.arbiter.check_drawdown_velocity(loop_start, eq);
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
            // Dump veto breakdown so we can see WHY signals are being rejected.
            if snap.signals_vetoed > 0 {
                let mut veto_summary: Vec<String> = engine.telemetry.veto_counts
                    .iter()
                    .map(|(reason, count)| format!("{}={}", reason, count.get()))
                    .collect();
                veto_summary.sort();
                eprintln!("VETO_BREAKDOWN: {}", veto_summary.join(", "));
            }
            // FIX 4: Signal drought warning — alert when receiving ticks but zero signals.
            if snap.ticks_received > 5000 && snap.signals_generated == 0 && snap.signals_vetoed == 0 {
                eprintln!(
                    "WARNING: SIGNAL_DROUGHT — {} ticks received but 0 signals generated and 0 vetoed. \
                     Python bridge is returning no_signal for every tick. \
                     Check VanguardSniper thresholds, data quality, or bar history warmup.",
                    snap.ticks_received
                );
            }

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

        // Simulation mode: override any regime escalation at end of loop.
        // In simulation mode there's no real broker, no real VIX, no real reconciliation —
        // all regime escalation triggers are stale/false. Reset to Normal so trades flow.
        if engine.simulation_mode && engine.arbiter.regime > RiskRegime::Normal {
            engine.arbiter.regime = RiskRegime::Normal;
        }

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
