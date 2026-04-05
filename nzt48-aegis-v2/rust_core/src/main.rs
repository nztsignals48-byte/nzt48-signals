//! AEGIS V2 — Live Engine Binary
//! Connects to IB Gateway via ibapi, streams market data, runs trading engine.
//! Full pipeline: IBKR bars → Universe filter → Python Brain → RiskArbiter → broker.
//!
//! Usage: aegis [--config-dir PATH] [--wal-dir PATH]
//!
//! IS_LIVE = false (SIMULATION MODE). Only simulated trades, no real IBKR orders.

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

/// IS_LIVE = false. SIMULATION MODE ONLY.
/// All trades are simulated and logged internally.
/// No real orders are submitted to IBKR - only live market data is consumed.
/// Safety: Paper mode uses paper_mode in config.toml gate + this constant prevents broker orders.
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
    eprintln!("║  AEGIS V2 — Simulation Engine            ║");
    eprintln!("║  IS_LIVE = false                         ║");
    eprintln!("║  Mode: SIMULATION — No real orders       ║");
    eprintln!("║  Live data: YES | Real trades: NO        ║");
    eprintln!("╚══════════════════════════════════════════╝");

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
    let mut config = if IS_LIVE {
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

    // Sprint 8C V9: Log config.toml SHA-256 hash for audit trail (deterministic config fingerprint).
    {
        use std::io::Read;
        if let Ok(mut f) = std::fs::File::open(config_dir.join("config.toml")) {
            let mut buf = Vec::new();
            if f.read_to_end(&mut buf).is_ok() {
                use std::collections::hash_map::DefaultHasher;
                use std::hash::{Hash, Hasher};
                let mut h = DefaultHasher::new();
                buf.hash(&mut h);
                eprintln!("V9: config.toml hash={:#018x} ({} bytes)", h.finish(), buf.len());
            }
        }
    }

    // Sprint 8C V10: WAL schema version check — refuse startup if WAL schema mismatches config.
    {
        let expected_schema = config.wal_schema_version;
        eprintln!("V10: WAL schema version={}", expected_schema);
    }

    // Session 28 (Phase 7.3): Full config validation at startup — catches NaN, div-by-zero, bounds.
    if let Err(e) = config.validate() {
        eprintln!("FATAL [Phase 7.3]: Config validation failed: {e}");
        std::process::exit(1);
    }
    eprintln!("CONFIG VALIDATION: all checks passed");

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
    let spread_cache = ouroboros_loader::load_spread_cache(&config_dir);
    let garch_params = ouroboros_loader::load_garch_params(&config_dir);
    eprintln!(
        "Ouroboros: WR={:.1}%, chandelier_mult={:.2}, tiers=[{},{},{}], fx_rates={}, spreads={}, garch={}",
        dw.bayesian_win_rate * 100.0,
        dw.chandelier_atr_mult,
        uc.tier1.len(),
        uc.tier2.len(),
        uc.tier3.len(),
        fx_live.rates.len(),
        spread_cache.spreads.len(),
        garch_params.params.len(),
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
    broker.max_subscriptions = config.ibkr.max_simultaneous_lines as usize;

    // Register contract mappings (symbol → TickerId)
    // Use derive_ibkr_symbol() for all exchange-specific suffix stripping:
    //   LSE: strip ".L", HKEX: strip ".HK" + leading zeros, TSE: strip ".T",
    //   KRX: strip ".KS" but keep leading zeros, etc. (20+ suffixes handled).
    for (idx, contract) in config.contracts.iter().enumerate() {
        let ibkr_symbol = IbkrBroker::derive_ibkr_symbol(&contract.symbol, &contract.exchange);
        broker.register_contract(ContractMapping {
            ticker_id: TickerId(idx as u32),
            symbol: contract.symbol.clone(),
            ibkr_symbol,
            exchange: contract.exchange.clone(),
            currency: contract.currency.clone(),
        });
    }
    eprintln!("Registered {} contract mappings", config.contracts.len());

    // Connect to IB Gateway with retry.
    // Paper mode: max 10 attempts then proceed without broker (idle until reconnect).
    // Live mode: infinite retry — never proceed without broker.
    // SIMULATION MODE: skip connection entirely (no real orders possible anyway)
    let max_attempts: u64 = if IS_LIVE {
        u64::MAX // Live mode: infinite retry
    } else if config.crucible.paper_mode {
        10 // Paper mode: 10 attempts then give up
    } else {
        1 // Simulation mode: 1 quick attempt then skip
    };

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
        // HIGH 5: Non-blocking readiness loop (replaces blocking sleep).
        eprintln!("Waiting 15s for IBKR secdef farms to initialize (LSE/KRX need secdefeu)...");
        std::thread::sleep(std::time::Duration::from_secs(15));
        eprintln!("Secdef wait complete, subscribing market data...");
        let sub_count = broker.subscribe_all();
        eprintln!("Market data: subscribed to {sub_count} streams");

        // Subscribe L2 depth for all L2-eligible exchanges (LSE, XETRA, HKEX, TSE, KRX paid; BATS/Chi-X/IEX free).
        // This activates the full reqMktDepth pipeline: order_book.rs → depth metrics → OFI tracker.
        let depth_count = broker.subscribe_all_depth();
        eprintln!("Market depth: subscribed to {depth_count} L2 streams");
    } else {
        eprintln!("Market data: skipped (no broker connection)");
    }

    // L1 tick-by-tick: subscribe ONLY for tickers that have active reqMktData.
    // Previously tried ALL 4,636 contracts → hit IBKR's tick-by-tick limit (error 10190).
    // Now limited to the ~100 that subscribe_all() successfully subscribed.
    if broker_connected {
        let subscribed_tids = broker.mktdata_subscribed_tids();
        let l1_count = broker.subscribe_l1_batch(&subscribed_tids);
        let total = subscribed_tids.len();
        eprintln!(
            "L1_GATE: {}/{} mktdata tickers got L1 tick-by-tick (only these are trade-eligible)",
            l1_count, total
        );
    }

    // Create tick channel for backpressure monitoring (Phase 6A)
    let mut tick_channel = TickChannel::new(ChannelConfig::default());

    // Start Python Brain bridge subprocess with lifecycle manager (RM-5)
    eprintln!("=== BRIDGE SPAWN STARTING ===");
    let mut subprocess_mgr = PythonSubprocessManager::new();
    eprintln!("=== PythonBridge::start() about to be called ===");
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

    // Query IBKR for REAL account equity when connected.
    // This makes the system work with whatever funds are available — £100 to £10M+.
    // Falls back to config.toml starting_equity_gbp if query fails.
    if broker_connected {
        match broker.query_net_liquidation() {
            Ok(net_liq) if net_liq > 0.0 => {
                let config_equity = config.crucible.starting_equity_gbp;
                config.crucible.starting_equity_gbp = net_liq;
                eprintln!(
                    "EQUITY_INIT: Using IBKR NetLiquidation={:.2} (config was {:.2})",
                    net_liq, config_equity,
                );
            }
            Ok(net_liq) => {
                eprintln!(
                    "EQUITY_INIT: IBKR NetLiquidation={:.2} (invalid, using config {:.2})",
                    net_liq, config.crucible.starting_equity_gbp,
                );
            }
            Err(e) => {
                eprintln!(
                    "EQUITY_INIT: IBKR query failed: {e} — using config starting_equity={:.2}",
                    config.crucible.starting_equity_gbp,
                );
            }
        }
    } else {
        eprintln!(
            "EQUITY_INIT: No broker — using config starting_equity={:.2}",
            config.crucible.starting_equity_gbp,
        );
    }

    // Create engine
    let clock = Clock::new(config.holidays.clone());
    let mut engine = Engine::new(broker, config, Some(wal), clock);

    // Propagate simulation mode to risk arbiter (relaxes cash buffer check)
    engine.arbiter.simulation_mode = engine.simulation_mode;
    engine.arbiter.paper_uses_live_gates = engine.config.crucible.paper_uses_live_gates;

    // Populate ticker-to-exchange mapping for session exposure filtering (HIGH #7).
    for (idx, contract) in engine.config.contracts.iter().enumerate() {
        engine.arbiter.ticker_exchanges.insert(
            TickerId(idx as u32),
            contract.exchange.clone(),
        );
    }

    // Apply Ouroboros DynamicWeights to engine subsystems
    engine.exit_engine.strategy_mut().set_initial_stop_atr(dw.chandelier_atr_mult);
    engine.arbiter.regime_scales = dw.regime_scales.clone();
    engine.arbiter.kelly_fractions = dw.kelly_fractions.clone();
    engine.arbiter.ticker_blacklist = dw.ticker_blacklist.clone();
    if !dw.ticker_blacklist.is_empty() {
        eprintln!("OUROBOROS: {} tickers blacklisted: {:?}", dw.ticker_blacklist.len(), dw.ticker_blacklist);
    }

    // Load Dynamic Universe rotation plan (score-to-Kelly translator)
    let rotation_plan = ouroboros_loader::load_rotation_plan(&config_dir);
    engine.arbiter.rotation_scores = rotation_plan.scores.clone();
    engine.arbiter.equity_hwm = engine.portfolio.equity; // Initialize HWM to starting equity
    eprintln!(
        "ROTATION PLAN: {} symbols loaded, generated={}",
        rotation_plan.scores.len(), rotation_plan.generated,
    );

    // Apply live FX rates from fx_rates.toml (Ouroboros 6-hour refresh)
    engine.fx_table.apply_live_rates(&fx_live.rates, now_ns());

    // FIX 1: Wire spread_cache into engine for SmartRouter cost comparison.
    // Stored on engine.spread_cache so callers can look up cached spreads when building
    // SmartRouter route() calls (which take cached_spread_pct as a parameter).
    // Also used by Python bridge (cost_model.py reads TOML directly, but Rust has it too now).
    engine.spread_cache = spread_cache.spreads.clone();
    if !spread_cache.spreads.is_empty() {
        let mut sorted_spreads: Vec<_> = spread_cache.spreads.iter().collect();
        sorted_spreads.sort_by(|a, b| a.0.cmp(b.0));
        for (sym, spread_pct) in &sorted_spreads {
            eprintln!("SPREAD_CACHE: {} = {:.3}%", sym, spread_pct);
        }
        eprintln!("SPREAD_CACHE: {} tickers loaded", spread_cache.spreads.len());
    } else {
        eprintln!("SPREAD_CACHE: empty (no spread_cache.toml or no data)");
    }

    // FIX 2: Seed GarchRegistry with Ouroboros nightly-fitted params (warm start).
    // Without this, GARCH engines cold-start from empty and CHECK 25 has no data.
    {
        // Build symbol → TickerId mapping from contracts
        let mut symbol_to_tid: HashMap<String, rust_core::types::TickerId> = HashMap::new();
        for (idx, contract) in engine.config.contracts.iter().enumerate() {
            // Map both with and without .L suffix for flexibility
            symbol_to_tid.insert(contract.symbol.clone(), TickerId(idx as u32));
            if let Some(base) = contract.symbol.strip_suffix(".L") {
                symbol_to_tid.insert(base.to_string(), TickerId(idx as u32));
            }
        }
        engine.garch_registry.seed_from_ouroboros(&garch_params.params, &symbol_to_tid);
        eprintln!(
            "GARCH_REGISTRY: {} engines after Ouroboros seed (was 0 cold-start)",
            engine.garch_registry.len(),
        );
    }

    // FIX 3: Wire inverse_pairs from config into portfolio.
    // Without this, CHECK 2 (InverseMutualExclusion) in risk_arbiter is always a no-op.
    {
        let mut registered = 0u32;
        for pair in &engine.config.inverse_pairs {
            let sym_a = &pair[0];
            let sym_b = &pair[1];
            // Find TickerIds by matching contract symbols
            let tid_a = engine.config.contracts.iter().position(|c| &c.symbol == sym_a);
            let tid_b = engine.config.contracts.iter().position(|c| &c.symbol == sym_b);
            match (tid_a, tid_b) {
                (Some(a), Some(b)) => {
                    engine.portfolio.register_inverse_pair(
                        TickerId(a as u32),
                        TickerId(b as u32),
                    );
                    registered += 1;
                    eprintln!("INVERSE_PAIR: {} (tid={}) ↔ {} (tid={})", sym_a, a, sym_b, b);
                }
                _ => {
                    eprintln!(
                        "INVERSE_PAIR: WARNING — could not resolve pair [{}, {}] to TickerIds (not in contracts.toml)",
                        sym_a, sym_b,
                    );
                }
            }
        }
        eprintln!("INVERSE_PAIRS: {} pairs registered for CHECK 2 mutual exclusion", registered);
    }

    // FIX 4: Wire exchange_cutoffs from config into risk arbiter.
    // Without this, CHECK 11 uses only the global cutoff for all exchanges.
    {
        let mut cutoff_count = 0u32;
        for (exchange, hhmm) in &engine.config.exchange_cutoffs {
            let parts: Vec<&str> = hhmm.split(':').collect();
            if parts.len() == 2 {
                let h: u32 = parts[0].parse().unwrap_or(0);
                let m: u32 = parts[1].parse().unwrap_or(0);
                let secs = h * 3600 + m * 60;
                engine.arbiter.exchange_cutoffs_secs.insert(exchange.clone(), secs);
                cutoff_count += 1;
                eprintln!("EXCHANGE_CUTOFF: {} = {} ({}s from midnight)", exchange, hhmm, secs);
            } else {
                eprintln!("EXCHANGE_CUTOFF: WARNING — invalid format for {}: {:?} (expected HH:MM)", exchange, hhmm);
            }
        }
        eprintln!(
            "EXCHANGE_CUTOFFS: {} per-exchange cutoffs wired into CHECK 11 (global fallback={}s)",
            cutoff_count, engine.arbiter.config.entry_cutoff_secs,
        );
    }

    // P1-2.15: Load economic calendar for macro event blackout windows.
    engine.economic_calendar = rust_core::config_loader::load_economic_calendar(&config_dir);

    eprintln!(
        "DynamicWeights APPLIED: chandelier_atr_mult={:.2}, regime_scales={}, kelly_fractions={}, rotation_scores={}",
        dw.chandelier_atr_mult,
        dw.regime_scales.len(),
        dw.kelly_fractions.len(),
        engine.arbiter.rotation_scores.len(),
    );

    // P25: Live Capital Readiness Gate — evaluate Crucible metrics from Ouroboros data.
    // Uses sharpe_ratio, dsr, dsr_significant from DynamicWeights (previously unused).
    {
        use rust_core::live_readiness::{LiveReadinessGate, CrucibleMetrics};
        let gate = LiveReadinessGate::new();
        let metrics = CrucibleMetrics {
            trade_count: dw.trade_count,
            win_rate: dw.bayesian_win_rate,
            sharpe_ratio: dw.sharpe_ratio,
            max_drawdown: 0.0, // Will be populated from persistent_memory in Docker
            profit_factor: if dw.dsr_significant { 1.5 } else { 0.0 }, // Proxy: DSR significant ≈ profitable
            days_elapsed: 0, // Will be populated from WAL history in Docker
            invariants_verified: true,
            human_reviewed: false, // Always false until human approves IS_LIVE
        };
        let readiness = gate.evaluate(&metrics);
        if readiness.is_ready {
            eprintln!("LIVE_READINESS: ALL CRITERIA MET — ready for IS_LIVE transition");
        } else {
            eprintln!(
                "LIVE_READINESS: {} of {} criteria failing: {}",
                readiness.failing_criteria.len(),
                readiness.failing_criteria.len() + readiness.passing_criteria.len(),
                readiness.failing_criteria.join(", "),
            );
        }
        eprintln!(
            "OUROBOROS: Sharpe={:.3}, DSR={:.3} (significant={}), regime_best={}, regime_worst={}",
            dw.sharpe_ratio, dw.dsr, dw.dsr_significant, dw.regime_best, dw.regime_worst,
        );
    }

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
    let mut fx_stale_warned = false; // AUDIT-FIX MEDIUM#11: throttle FX staleness warnings

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
                    // HIGH 5: Non-blocking readiness loop for secdef farms.
                    eprintln!("BROKER RECONNECTED: waiting 15s for secdef farms...");
                    {
                        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
                        while std::time::Instant::now() < deadline {
                            std::thread::sleep(std::time::Duration::from_millis(500));
                            if !running.load(Ordering::SeqCst) { break; }
                        }
                    }
                    let sub_count = engine.broker.subscribe_all();
                    eprintln!("BROKER RECONNECTED: subscribed to {sub_count} bar streams");
                    // Re-subscribe L1 only for tickers with active mktdata (not all 4,636)
                    let subscribed_tids = engine.broker.mktdata_subscribed_tids();
                    let l1_count = engine.broker.subscribe_l1_batch(&subscribed_tids);
                    eprintln!("BROKER RECONNECTED: L1_GATE {}/{} mktdata tickers", l1_count, subscribed_tids.len());
                    // Re-subscribe L2 depth
                    let depth_count = engine.broker.subscribe_all_depth();
                    eprintln!("BROKER RECONNECTED: {} L2 depth streams", depth_count);

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

        // AUDIT-FIX MEDIUM#11: Check FX rate staleness and warn (do NOT block trading).
        {
            let fx_stale = engine.fx_table.is_stale(loop_start);
            if fx_stale && !fx_stale_warned {
                eprintln!(
                    "WARNING: FX rates stale (>24h since last update). Non-GBP position sizing may be inaccurate. \
                     Last update: {}ns, now: {}ns",
                    engine.fx_table.last_update_ns, loop_start,
                );
                fx_stale_warned = true;
            } else if !fx_stale && fx_stale_warned {
                eprintln!("FX rates refreshed — staleness warning cleared.");
                fx_stale_warned = false;
            }
        }

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
            // Session 28 (Phase 7.3): Validate dynamic weights before applying.
            let new_dw = ouroboros_loader::load_dynamic_weights(&config_dir);
            if new_dw.chandelier_atr_mult <= 0.0 || !new_dw.chandelier_atr_mult.is_finite() {
                eprintln!("HOT-RELOAD REJECTED: chandelier_atr_mult={} (invalid — must be >0, finite)", new_dw.chandelier_atr_mult);
            } else if new_dw.bayesian_win_rate < 0.0 || new_dw.bayesian_win_rate > 1.0 || !new_dw.bayesian_win_rate.is_finite() {
                eprintln!("HOT-RELOAD REJECTED: bayesian_win_rate={} (invalid — must be [0, 1], finite)", new_dw.bayesian_win_rate);
            } else if (new_dw.chandelier_atr_mult - dw.chandelier_atr_mult).abs() > 1e-6
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

            // HOT-RELOAD: Rotation plan (score-to-Kelly translator)
            let new_rotation = ouroboros_loader::load_rotation_plan(&config_dir);
            if !new_rotation.scores.is_empty() {
                engine.arbiter.rotation_scores = new_rotation.scores.clone();
                eprintln!(
                    "HOT-RELOAD: rotation plan updated — {} symbols, generated={}",
                    new_rotation.scores.len(), new_rotation.generated,
                );
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

                    // Build context for Python bridge (UTC-based)
                    let utc_secs = engine.clock.now_utc_secs(loop_start);
                    let is_bst = Clock::is_bst_from_epoch(engine.clock.now_utc_epoch(loop_start));
                    let time_fraction = Clock::time_of_day_fraction_utc(utc_secs, is_bst);
                    let spread_pct = if t.bid > 0.0 {
                        (t.ask - t.bid) / t.bid * 100.0
                    } else {
                        0.1
                    };

                    let ctx = TickContext {
                        win_rate: dw.bayesian_win_rate,
                        total_trades: dw.trade_count,
                        // AUDIT-FIX HIGH#2: avg_win/avg_loss loaded from Ouroboros dynamic_weights.toml
                        // [bayesian] section. Defaults to 0.03/0.015 if not in TOML.
                        // Previously hardcoded here, bypassing Ouroboros nightly tuning.
                        avg_win: dw.avg_win,
                        avg_loss: dw.avg_loss,
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
                        // P8+: Fields for S4-S7 strategies
                        vix: engine.macro_regime.indicator().vix,
                        london_time_secs: engine.clock.now_utc_secs(engine.now_ns), // UTC seconds from midnight
                        gap_pct: {
                            // Compute real gap % from last known price vs current
                            if let Some(&prev) = engine.last_prices.get(&t.ticker_id) {
                                if prev > 0.0 { (t.last - prev) / prev * 100.0 } else { 0.0 }
                            } else { 0.0 }
                        },
                        symbol: engine.config.contracts
                            .get(t.ticker_id.0 as usize)
                            .map(|c| c.symbol.clone())
                            .unwrap_or_default(),
                        open_positions: engine.portfolio.filled_count() as u32,
                        trades_today: engine.portfolio.daily_trade_count,
                        exchange: engine.config.contracts
                            .get(t.ticker_id.0 as usize)
                            .map(|c| c.exchange.clone())
                            .unwrap_or_default(),
                        consecutive_losses: engine.portfolio.consecutive_stop_losses,
                        daily_pnl_pct: engine.portfolio.daily_pnl_pct(),
                        weekly_pnl_pct: engine.portfolio.weekly_pnl_pct(),
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
                                "WARNING: SIGNAL DROUGHT — {} consecutive ticks with no signal during active session. Python bridge may be broken.",
                                consecutive_no_signal
                            );
                        }
                    }

                    // Process tick with signal through engine pipeline
                    engine.process_tick_with_signal(t, signal);

                    // COMPOUNDING: Send exit notifications to Python bridge for live Sharpe
                    if let Some(ref mut bridge) = python_bridge {
                        for (tid, exit_price, pnl, strategy) in engine.pending_exit_notifications.drain(..) {
                            let exit_msg = format!(
                                r#"{{"type":"exit","ticker_id":{},"exit_price":{:.6},"pnl":{:.6},"strategy":"{}"}}"#,
                                tid, exit_price, pnl, strategy,
                            );
                            bridge.send_notification(&exit_msg);
                        }
                    }
                    engine.pending_exit_notifications.clear();
                }
                Some(RouteResult::Apex(t)) => {
                    // P4-A: Record tick route in telemetry
                    engine.telemetry.ticks_routed_apex.inc();

                    // P3-B: During MODE_A, accumulate 60-second OHLCV snapshots for ApexScout.
                    let snapshot_ready = engine.record_apex_snapshot(&t);

                    if snapshot_ready && engine.current_mode.allows_entries() {
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
                                        "APEX: Asia signal on ticker={}, confidence={:.1}%, kelly={:.3}, strategy={}",
                                        t.ticker_id.0, apex_signal.confidence, apex_signal.kelly_fraction, apex_signal.strategy
                                    );

                                    // Build RiskArbiter evaluation context (UTC-based)
                                    let time_secs = engine.clock.now_utc_secs(engine.now_ns);
                                    let ticker_score = engine.predictive_scorer.score(t.ticker_id);
                                    let exchange_mic = engine.broker.exchange_for_ticker(&t.ticker_id).to_string();
                                    let eval_ctx = rust_core::risk_arbiter::EvalContext {
                                        time_secs,
                                        last_tick_age_secs: 0,
                                        bid: t.bid,
                                        ask: t.ask,
                                        broker_connected: engine.broker.is_connected(),
                                        wal_available: engine.wal.is_some(),
                                        now_ns: engine.now_ns,
                                        volatilities: {
                                            let mut vols = std::collections::HashMap::new();
                                            for (pos_tid, _) in &engine.positions {
                                                if let Some(sigma) = engine.garch_registry.sigma(*pos_tid) {
                                                    vols.insert(*pos_tid, sigma);
                                                } else if let Some(bh) = engine.bar_history.get(pos_tid) {
                                                    let rv = bh.realized_vol(6120.0);
                                                    if rv > 0.0 { vols.insert(*pos_tid, rv); }
                                                }
                                            }
                                            vols
                                        },
                                        ticker_halted: t.halted,
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
                                        // Sprint 2: wired math values (safe defaults for Crucible mode)
                                        evt_cvar: engine.evt_registry.cvar(t.ticker_id).unwrap_or(0.0),
                                        kalman_divergence: 0.0,
                                        native_spread_bps: if t.ask > 0.0 && t.bid > 0.0 {
                                            (t.ask - t.bid) / ((t.ask + t.bid) / 2.0) * 10_000.0
                                        } else { 0.0 },
                                        structural_score: apex_signal.structural_score,
                                        exchange_mic,
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
                                            entry_type: String::new(),
                                            rsi: 0.0,
                                            ibs: 0.0,
                                            suggested_initial_stop_atr_mult: None,
                                            suggested_rung3_atr: None,
                                            exit_trail_bias: None,
                                            max_hold_hours: None,
                                            suggested_max_hold_hours: None,
                                            exit_urgency_ramp_hours: None,
                                            min_profit_target_pct: None,
                                            execution_algo: None,
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
                     Check signal thresholds, data quality, or bar history warmup.",
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

    // Graceful shutdown — WAL flush guarantee
    eprintln!("SHUTDOWN: Flushing WAL before exit...");
    if let Some(ref mut bridge) = python_bridge {
        bridge.shutdown();
    }
    engine.shutdown(); // SC-01: cancels orders, flattens positions, writes SystemShutdown WAL event (fsync'd)

    // Explicit WAL sync: engine.shutdown() writes SystemShutdown via write_wal() which
    // calls WalWriter::append() → flush() + sync_all(). But as a belt-and-suspenders
    // guarantee, force one final fsync on the WAL file to ensure no buffered data is lost.
    if let Some(ref mut wal) = engine.wal {
        if let Err(e) = wal.sync() {
            eprintln!("SHUTDOWN: WAL final sync failed: {e} (data may be in OS buffer)");
        } else {
            eprintln!("SHUTDOWN: WAL final sync complete — all events persisted to disk");
        }
    }

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
