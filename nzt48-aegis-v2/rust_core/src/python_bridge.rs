//! Python Brain Bridge — subprocess IPC for signal generation.
//!
//! Spawns `python3 -m python_brain.bridge` and communicates via JSON lines
//! over stdin/stdout. Each tick sent gets a synchronous response.
//!
//! This avoids the PyO3 extension-module vs auto-initialize conflict.

use std::collections::HashMap;
use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::time::Duration;

use pyo3::prelude::*;

use crate::types::{MarketTick, RiskRegime, TickerId};

/// Signal returned by the Python Brain.
#[derive(Clone, Debug)]
#[pyclass]
pub struct BrainSignal {
    #[pyo3(get)]
    pub direction: String,
    #[pyo3(get)]
    pub confidence: f64,
    #[pyo3(get)]
    pub kelly_fraction: f64,
    #[pyo3(get)]
    pub shares: u32,
    #[pyo3(get)]
    pub strategy: String,
    // AUDIT-FIX: Indicator context for Ouroboros learning.
    // Captured at signal generation time, written to WAL for win/loss analysis.
    #[pyo3(get)]
    pub rvol: f64,
    #[pyo3(get)]
    pub hurst: f64,
    #[pyo3(get)]
    pub adx: f64,
    /// N2b: Volume slope at signal time (from 5-min bar regression).
    #[pyo3(get)]
    pub vol_slope: f64,
    /// N2b: VWAP distance % at signal time. Positive = above VWAP.
    #[pyo3(get)]
    pub vwap_dist_pct: f64,
    /// N3a: Structural Tradability Score (0-100) computed by bridge.py.
    #[pyo3(get)]
    pub structural_score: f64,
    /// TypeA-F entry classification from bridge.py classify_entry_type().
    #[pyo3(get)]
    pub entry_type: String,
    /// RSI(14) at signal time for WAL enrichment.
    #[pyo3(get)]
    pub rsi: f64,
    /// IBS (Internal Bar Strength) at signal time.
    #[pyo3(get)]
    pub ibs: f64,
    // ── EXIT HINT FIELDS (consumed by exit_engine.rs) ──
    /// Per-strategy initial Chandelier stop ATR multiplier (Book 39).
    #[pyo3(get)]
    pub suggested_initial_stop_atr_mult: Option<f64>,
    /// Per-strategy Rung 3 trailing ATR multiplier (regime-adaptive).
    #[pyo3(get)]
    pub suggested_rung3_atr: Option<f64>,
    /// Qualitative trail bias: "wide", "tight", or "neutral".
    #[pyo3(get)]
    pub exit_trail_bias: Option<String>,
    /// Strategy-specific max holding period in hours.
    #[pyo3(get)]
    pub max_hold_hours: Option<f64>,
    /// Strategy-suggested max holding period (overrides leverage-based).
    #[pyo3(get)]
    pub suggested_max_hold_hours: Option<f64>,
    /// Hours after which to start tightening stops (urgency ramp).
    #[pyo3(get)]
    pub exit_urgency_ramp_hours: Option<f64>,
    /// Minimum profit target % to justify breakeven stop (spread-adjusted).
    #[pyo3(get)]
    pub min_profit_target_pct: Option<f64>,
    /// Execution algorithm hint: "TWAP" for high-impact orders.
    /// KEPT (not dead code): forward-looking infrastructure for IS_LIVE mode.
    /// Used when live trading routes through TWAP/VWAP execution algos.
    #[pyo3(get)]
    pub execution_algo: Option<String>,
}

#[pymethods]
impl BrainSignal {
    #[new]
    #[pyo3(signature = (direction, confidence, kelly_fraction, shares, strategy, rvol=0.0, hurst=0.0, adx=0.0, vol_slope=0.0, vwap_dist_pct=0.0, structural_score=0.0))]
    fn new(
        direction: String,
        confidence: f64,
        kelly_fraction: f64,
        shares: u32,
        strategy: String,
        rvol: f64,
        hurst: f64,
        adx: f64,
        vol_slope: f64,
        vwap_dist_pct: f64,
        structural_score: f64,
    ) -> Self {
        Self {
            direction,
            confidence,
            kelly_fraction,
            shares,
            strategy,
            rvol,
            hurst,
            adx,
            vol_slope,
            vwap_dist_pct,
            structural_score,
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
        }
    }
}

/// Context sent alongside each tick for Kelly sizing.
/// RM-3: #[pyclass] enables zero-copy Rust → Python transfer (no JSON).
#[derive(Clone, Debug)]
#[pyclass]
pub struct TickContext {
    #[pyo3(get, set)]
    pub win_rate: f64,
    #[pyo3(get, set)]
    pub total_trades: u32,
    #[pyo3(get, set)]
    pub avg_win: f64,
    #[pyo3(get, set)]
    pub avg_loss: f64,
    #[pyo3(get, set)]
    pub leverage: u32,
    #[pyo3(get, set)]
    pub realized_vol: f64,
    #[pyo3(get, set)]
    pub correlation: f64,
    #[pyo3(get, set)]
    pub drawdown_pct: f64,
    #[pyo3(get, set)]
    pub amihud: f64,
    #[pyo3(get)]
    pub regime: RiskRegime,
    #[pyo3(get, set)]
    pub spread_pct: f64,
    #[pyo3(get, set)]
    pub time_fraction: f64,
    #[pyo3(get, set)]
    pub heat_pct: f64,
    #[pyo3(get, set)]
    pub equity: f64,
    /// P8+: VIX value for S4/S7 volatility strategies.
    #[pyo3(get, set)]
    pub vix: f64,
    /// P8+: London local time in seconds from midnight for S5 overnight carry.
    #[pyo3(get, set)]
    pub london_time_secs: u32,
    /// P9: Gap percentage for S6 catalyst rotation.
    #[pyo3(get, set)]
    pub gap_pct: f64,
    /// P8+: Ticker symbol name for inverse detection (S4/S7).
    #[pyo3(get, set)]
    pub symbol: String,
    /// P9: Number of open positions for Claude context.
    #[pyo3(get, set)]
    pub open_positions: u32,
    /// P9: Trades executed today for Claude context.
    #[pyo3(get, set)]
    pub trades_today: u32,
    /// Exchange code from contracts.toml (e.g. "LSE", "SMART", "HKEX").
    /// Used by CrossVenueArb to identify quote venue.
    #[pyo3(get, set)]
    pub exchange: String,
    /// Consecutive stop losses from portfolio. Used by Python tilt guard.
    #[pyo3(get, set)]
    pub consecutive_losses: u32,
}

#[pymethods]
impl TickContext {
    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (win_rate=0.5, total_trades=0, avg_win=0.02, avg_loss=0.02, leverage=3, realized_vol=0.30, correlation=0.0, drawdown_pct=0.0, amihud=0.0, regime=RiskRegime::Normal, spread_pct=0.1, time_fraction=0.5, heat_pct=0.0, equity=100_000.0))]
    fn new(
        win_rate: f64,
        total_trades: u32,
        avg_win: f64,
        avg_loss: f64,
        leverage: u32,
        realized_vol: f64,
        correlation: f64,
        drawdown_pct: f64,
        amihud: f64,
        regime: RiskRegime,
        spread_pct: f64,
        time_fraction: f64,
        heat_pct: f64,
        equity: f64,
    ) -> Self {
        Self {
            win_rate,
            total_trades,
            avg_win,
            avg_loss,
            leverage,
            realized_vol,
            correlation,
            drawdown_pct,
            amihud,
            regime,
            spread_pct,
            time_fraction,
            heat_pct,
            equity,
            vix: 0.0,
            london_time_secs: 0,
            gap_pct: 0.0,
            symbol: String::new(),
            open_positions: 0,
            trades_today: 0,
            exchange: String::new(),
            consecutive_losses: 0,
        }
    }
}

impl Default for TickContext {
    fn default() -> Self {
        Self {
            win_rate: 0.5,
            total_trades: 0,
            avg_win: 0.02,
            avg_loss: 0.02,
            leverage: 3,
            realized_vol: 0.30,
            correlation: 0.0,
            drawdown_pct: 0.0,
            amihud: 0.0,
            regime: RiskRegime::Normal,
            spread_pct: 0.1,
            time_fraction: 0.5,
            heat_pct: 0.0,
            equity: 100_000.0,
            vix: 0.0,
            london_time_secs: 0,
            gap_pct: 0.0,
            symbol: String::new(),
            open_positions: 0,
            trades_today: 0,
            exchange: String::new(),
            consecutive_losses: 0,
        }
    }
}

/// P0-1.2: Read timeout in seconds for Python bridge responses.
/// 5 seconds allows for yfinance I/O + GIL contention in Python.
const BRIDGE_READ_TIMEOUT_SECS: u64 = 5;

/// Subprocess-based Python Bridge.
pub struct PythonBridge {
    #[allow(dead_code)]
    child: Child,
    stdin: std::process::ChildStdin,
    /// P0-1.2: Reader thread sends lines via channel for timeout support.
    line_rx: mpsc::Receiver<String>,
    /// Leverage factor per ticker (from contracts config).
    pub leverage_map: HashMap<TickerId, u32>,
    /// Consecutive error responses from Python (strategy crash detection).
    pub consecutive_errors: u64,
    /// P0-1.2: Consecutive timeout counter for bridge restart decision.
    pub consecutive_timeouts: u32,
    /// P2-3.5: Consecutive ticks where Python returned 0 signals (crash gap detection).
    pub consecutive_empty: u32,
    /// Set on stdin write/flush failure — caller should respawn the bridge.
    pub needs_respawn: bool,
}

impl PythonBridge {
    /// Start the Python bridge subprocess.
    /// P0-1.2: Spawns a dedicated reader thread for timeout-safe reads.
    pub fn start() -> Result<Self, String> {
        eprintln!("[DEBUG] PythonBridge::start() called");

        // Pipe stderr to a log file instead of inheriting (avoids lost diagnostics).
        eprintln!("[DEBUG] Opening /app/data/bridge_stderr.log");
        let stderr_file = OpenOptions::new()
            .create(true)
            .append(true)
            .open("/app/data/bridge_stderr.log")
            .map_err(|e| {
                let msg = format!("Failed to open bridge stderr log: {e}");
                eprintln!("[ERROR] {}", msg);
                msg
            })?;
        eprintln!("[DEBUG] Stderr file opened successfully");

        eprintln!("[DEBUG] Spawning python3 /app/python_brain/bridge.py");
        let mut child = Command::new("python3")
            .args(["/app/python_brain/bridge.py"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::from(stderr_file))
            .current_dir("/app")
            .spawn()
            .map_err(|e| {
                let msg = format!("Failed to start Python bridge: {e}");
                eprintln!("[ERROR] {}", msg);
                msg
            })?;
        eprintln!("[DEBUG] Child process spawned successfully");

        eprintln!("[DEBUG] Taking stdin and stdout from child process");
        let stdin = child.stdin.take().ok_or("No stdin on child process")?;
        let stdout = child.stdout.take().ok_or("No stdout on child process")?;
        eprintln!("[DEBUG] Stdin and stdout acquired");

        // P0-1.2: Spawn reader thread — reads lines from stdout and sends via channel.
        // This allows the main thread to use recv_timeout() instead of blocking forever.
        let (line_tx, line_rx) = mpsc::channel::<String>();
        eprintln!("[DEBUG] Spawning bridge reader thread");
        std::thread::Builder::new()
            .name("aegis-bridge-reader".to_string())
            .spawn(move || {
                let mut reader = BufReader::new(stdout);
                let mut buf = String::with_capacity(4096);
                loop {
                    buf.clear();
                    match reader.read_line(&mut buf) {
                        Ok(0) => break, // EOF — Python process exited
                        Ok(_) => {
                            if line_tx.send(buf.clone()).is_err() {
                                break; // Receiver dropped — PythonBridge was dropped
                            }
                        }
                        Err(_) => break, // Read error — pipe broken
                    }
                }
            })
            .map_err(|e| {
                let msg = format!("Failed to spawn bridge reader thread: {e}");
                eprintln!("[ERROR] {}", msg);
                msg
            })?;
        eprintln!("[DEBUG] Bridge reader thread spawned successfully");

        eprintln!("Python Bridge: subprocess started (pid={})", child.id());

        Ok(Self {
            child,
            stdin,
            line_rx,
            leverage_map: HashMap::new(),
            consecutive_errors: 0,
            consecutive_timeouts: 0,
            consecutive_empty: 0,
            needs_respawn: false,
        })
    }

    /// P0-1.2: Read a line from Python bridge with timeout.
    /// Returns None on timeout (after BRIDGE_READ_TIMEOUT_SECS).
    fn read_line_timeout(&mut self) -> Option<String> {
        match self.line_rx.recv_timeout(Duration::from_secs(BRIDGE_READ_TIMEOUT_SECS)) {
            Ok(line) => {
                self.consecutive_timeouts = 0;
                Some(line)
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.consecutive_timeouts += 1;
                if self.consecutive_timeouts == 1 || self.consecutive_timeouts % 10 == 0 {
                    eprintln!(
                        "CRITICAL: Python Bridge read TIMEOUT ({}s, #{} consecutive) — stop-loss processing may be delayed!",
                        BRIDGE_READ_TIMEOUT_SECS, self.consecutive_timeouts,
                    );
                }
                None
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                eprintln!("CRITICAL: Python Bridge reader thread died — bridge subprocess likely crashed");
                None
            }
        }
    }

    /// COMPOUNDING: Send a raw JSON message to the bridge (fire-and-forget, no response expected).
    /// Used for exit notifications, config updates, etc.
    pub fn send_notification(&mut self, json_line: &str) {
        if writeln!(self.stdin, "{json_line}").is_err() {
            eprintln!("BRIDGE_NOTIFY: stdin write failed (non-fatal)");
        }
        let _ = self.stdin.flush();
    }

    /// Send a tick to the Python bridge and get a signal back.
    /// Returns None if no signal or if communication fails.
    pub fn evaluate_tick(
        &mut self,
        tick: &MarketTick,
        high: f64,
        low: f64,
        ctx: &TickContext,
    ) -> Option<BrainSignal> {
        let regime_str = match ctx.regime {
            RiskRegime::Normal => "normal",
            RiskRegime::Reduce => "reduce",
            RiskRegime::Flatten => "flatten",
            RiskRegime::Halt => "halt",
        };

        let leverage = self
            .leverage_map
            .get(&tick.ticker_id)
            .copied()
            .unwrap_or(ctx.leverage);

        // Build JSON message — includes S4-S7 fields + extended tick data (25 new fields)
        let msg = format!(
            concat!(
                r#"{{"type":"tick","ticker_id":{},"last":{:.6},"high":{:.6},"low":{:.6},"#,
                r#""bid":{:.6},"ask":{:.6},"bid_size":{},"ask_size":{},"volume":{},"timestamp_ns":{},"#,
                r#""win_rate":{:.4},"total_trades":{},"avg_win":{:.4},"avg_loss":{:.4},"#,
                r#""leverage":{},"realized_vol":{:.4},"correlation":{:.4},"drawdown_pct":{:.4},"#,
                r#""amihud":{:.4},"regime":"{}","spread_pct":{:.4},"time_fraction":{:.4},"#,
                r#""heat_pct":{:.4},"equity":{:.2},"#,
                r#""vix":{:.2},"london_time_secs":{},"gap_pct":{:.4},"symbol":"{}","#,
                r#""open_positions":{},"trades_today":{},"exchange":"{}","consecutive_losses":{},"#,
                // Extended tick data
                r#""last_size":{},"open":{:.6},"close":{:.6},"trade_count":{},"#,
                r#""trade_rate":{:.2},"volume_rate":{:.2},"rt_hist_vol":{:.4},"shortable":{:.1},"#,
                r#""halted":{},"mark_price":{:.6},"auction_price":{:.6},"auction_volume":{},"#,
                r#""auction_imbalance":{:.2},"etf_nav_close":{:.6},"etf_nav_last":{:.6},"#,
                r#""etf_nav_bid":{:.6},"etf_nav_ask":{:.6},"opt_call_oi":{},"opt_put_oi":{},"#,
                r#""opt_call_vol":{},"opt_put_vol":{},"opt_impl_vol":{:.4},"opt_hist_vol":{:.4},"#,
                r#""avg_volume":{},"#,
                // L2 depth metrics
                r#""total_bid_depth":{:.2},"total_ask_depth":{:.2},"depth_imbalance":{:.6},"#,
                r#""bid_wall_price":{:.6},"ask_wall_price":{:.6},"spread_depth_1":{:.6},"#,
                r#""spread_depth_5":{:.6},"book_pressure":{:.4}}}"#
            ),
            tick.ticker_id.0,
            tick.last,
            high,
            low,
            tick.bid,
            tick.ask,
            tick.bid_size,
            tick.ask_size,
            tick.volume,
            tick.timestamp_ns,
            ctx.win_rate,
            ctx.total_trades,
            ctx.avg_win,
            ctx.avg_loss,
            leverage,
            ctx.realized_vol,
            ctx.correlation,
            ctx.drawdown_pct,
            ctx.amihud,
            regime_str,
            ctx.spread_pct,
            ctx.time_fraction,
            ctx.heat_pct,
            ctx.equity,
            ctx.vix,
            ctx.london_time_secs,
            ctx.gap_pct,
            ctx.symbol,
            ctx.open_positions,
            ctx.trades_today,
            ctx.exchange,
            ctx.consecutive_losses,
            // Extended tick data
            tick.last_size,
            tick.open,
            tick.close,
            tick.trade_count,
            tick.trade_rate,
            tick.volume_rate,
            tick.rt_hist_vol,
            tick.shortable,
            tick.halted,
            tick.mark_price,
            tick.auction_price,
            tick.auction_volume,
            tick.auction_imbalance,
            tick.etf_nav_close,
            tick.etf_nav_last,
            tick.etf_nav_bid,
            tick.etf_nav_ask,
            tick.opt_call_oi,
            tick.opt_put_oi,
            tick.opt_call_vol,
            tick.opt_put_vol,
            tick.opt_impl_vol,
            tick.opt_hist_vol,
            tick.avg_volume,
            // L2 depth metrics
            tick.total_bid_depth,
            tick.total_ask_depth,
            tick.depth_imbalance,
            tick.bid_wall_price,
            tick.ask_wall_price,
            tick.spread_depth_1,
            tick.spread_depth_5,
            tick.book_pressure,
        );

        // Send — broken pipe means Python process is dead, flag for respawn.
        if writeln!(self.stdin, "{msg}").is_err() {
            eprintln!("CRITICAL: Python Bridge stdin write failed — marking for respawn");
            self.needs_respawn = true;
            return None;
        }
        if self.stdin.flush().is_err() {
            eprintln!("CRITICAL: Python Bridge stdin flush failed (broken pipe) — marking for respawn");
            self.needs_respawn = true;
            return None;
        }

        // P0-1.2: Read response with timeout (prevents engine freeze if Python hangs)
        let response_line = match self.read_line_timeout() {
            Some(line) => line,
            None => return None, // Timeout or disconnected — no signal
        };

        // Parse response
        let resp: serde_json::Value = serde_json::from_str(response_line.trim()).ok()?;

        let resp_type = resp.get("type")?.as_str()?;

        // FIX 2026-03-11: Detect strategy crash errors from Python bridge.
        // "error" means the strategy threw an exception — this is NOT "no setup".
        if resp_type == "error" {
            let err_msg = resp
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            self.consecutive_errors += 1;
            if self.consecutive_errors == 1 || self.consecutive_errors.is_multiple_of(100) {
                eprintln!(
                    "PYTHON BRIDGE ERROR (#{}) ticker={}: {}",
                    self.consecutive_errors,
                    resp.get("ticker_id").and_then(|v| v.as_i64()).unwrap_or(-1),
                    err_msg
                );
            }
            return None;
        }

        if resp_type == "signal" {
            self.consecutive_errors = 0;
            self.consecutive_empty = 0; // P2-3.5: Reset on signal
        }

        if resp_type != "signal" {
            // P2-3.5: Track consecutive empty (no-signal) responses for crash gap detection.
            self.consecutive_empty += 1;
            // Only warn at escalating intervals: 1000, 5000, 10000, then every 10000.
            // During off-market hours 0 signals is normal — avoid log pollution.
            // At 5s/tick with 100 tickers, 1000 empties ≈ 50 seconds of market data.
            let n = self.consecutive_empty;
            if n == 1000 || n == 5000 || (n >= 10000 && n % 10000 == 0) {
                eprintln!(
                    "WARN: Python bridge returned 0 signals for {} consecutive ticks (may be off-market)",
                    n
                );
            }
            return None;
        }

        Some(BrainSignal {
            direction: resp.get("direction")?.as_str()?.to_string(),
            confidence: resp.get("confidence")?.as_f64()?,
            kelly_fraction: resp.get("kelly_fraction")?.as_f64()?,
            shares: resp.get("shares")?.as_u64()? as u32,
            strategy: resp.get("strategy")?.as_str()?.to_string(),
            rvol: resp.get("rvol").and_then(|v| v.as_f64()).unwrap_or(0.0),
            hurst: resp.get("hurst").and_then(|v| v.as_f64()).unwrap_or(0.0),
            adx: resp.get("adx").and_then(|v| v.as_f64()).unwrap_or(0.0),
            vol_slope: resp.get("vol_slope").and_then(|v| v.as_f64()).unwrap_or(0.0),
            vwap_dist_pct: resp.get("vwap_dist_pct").and_then(|v| v.as_f64()).unwrap_or(0.0),
            structural_score: resp.get("structural_score").and_then(|v| v.as_f64()).unwrap_or(0.0),
            entry_type: resp.get("entry_type").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            rsi: resp.get("rsi").and_then(|v| v.as_f64()).unwrap_or(0.0),
            ibs: resp.get("ibs").and_then(|v| v.as_f64()).unwrap_or(0.0),
            // Exit hint fields — consumed by exit_engine for per-signal Chandelier config
            suggested_initial_stop_atr_mult: resp.get("suggested_initial_stop_atr_mult").and_then(|v| v.as_f64()),
            suggested_rung3_atr: resp.get("suggested_rung3_atr").and_then(|v| v.as_f64()),
            exit_trail_bias: resp.get("exit_trail_bias").and_then(|v| v.as_str()).map(|s| s.to_string()),
            max_hold_hours: resp.get("max_hold_hours").and_then(|v| v.as_f64()),
            suggested_max_hold_hours: resp.get("suggested_max_hold_hours").and_then(|v| v.as_f64()),
            exit_urgency_ramp_hours: resp.get("exit_urgency_ramp_hours").and_then(|v| v.as_f64()),
            min_profit_target_pct: resp.get("min_profit_target_pct").and_then(|v| v.as_f64()),
            execution_algo: resp.get("execution_algo").and_then(|v| v.as_str()).map(|s| s.to_string()),
        })
    }

    /// Arrow IPC version of evaluate_tick — writes tick as Arrow IPC to /dev/shm,
    /// signals Python via stdin, reads response from /dev/shm Arrow file.
    /// Feature-gated behind `arrow_ipc`. Falls back to JSON on any error.
    #[cfg(feature = "arrow_ipc")]
    pub fn evaluate_tick_arrow(
        &mut self,
        tick: &MarketTick,
        high: f64,
        low: f64,
        ctx: &TickContext,
    ) -> Option<BrainSignal> {
        use arrow::array::*;
        use arrow::datatypes::{DataType, Field, Schema};
        use arrow::ipc::writer::StreamWriter;
        use arrow::ipc::reader::StreamReader;
        use arrow::record_batch::RecordBatch;
        use std::sync::Arc;

        let regime_str = match ctx.regime {
            RiskRegime::Normal => "normal",
            RiskRegime::Reduce => "reduce",
            RiskRegime::Flatten => "flatten",
            RiskRegime::Halt => "halt",
        };
        let leverage = self.leverage_map.get(&tick.ticker_id).copied().unwrap_or(ctx.leverage);

        // Build Arrow record batch matching TICK_SCHEMA from arrow_codec.py
        let schema = Arc::new(Schema::new(vec![
            Field::new("ticker_id", DataType::UInt32, false),
            Field::new("last", DataType::Float64, false),
            Field::new("high", DataType::Float64, false),
            Field::new("low", DataType::Float64, false),
            Field::new("bid", DataType::Float64, false),
            Field::new("ask", DataType::Float64, false),
            Field::new("volume", DataType::UInt64, false),
            Field::new("timestamp_ns", DataType::UInt64, false),
            Field::new("win_rate", DataType::Float64, false),
            Field::new("total_trades", DataType::UInt32, false),
            Field::new("avg_win", DataType::Float64, false),
            Field::new("avg_loss", DataType::Float64, false),
            Field::new("leverage", DataType::UInt32, false),
            Field::new("realized_vol", DataType::Float64, false),
            Field::new("correlation", DataType::Float64, false),
            Field::new("drawdown_pct", DataType::Float64, false),
            Field::new("amihud", DataType::Float64, false),
            Field::new("regime", DataType::Utf8, false),
            Field::new("spread_pct", DataType::Float64, false),
            Field::new("time_fraction", DataType::Float64, false),
            Field::new("heat_pct", DataType::Float64, false),
            Field::new("equity", DataType::Float64, false),
            Field::new("vix", DataType::Float64, false),
            Field::new("london_time_secs", DataType::UInt32, false),
            Field::new("gap_pct", DataType::Float64, false),
            Field::new("symbol", DataType::Utf8, false),
            Field::new("open_positions", DataType::UInt32, false),
            Field::new("trades_today", DataType::UInt32, false),
        ]));

        let batch = match RecordBatch::try_new(schema.clone(), vec![
            Arc::new(UInt32Array::from(vec![tick.ticker_id.0])),
            Arc::new(Float64Array::from(vec![tick.last])),
            Arc::new(Float64Array::from(vec![high])),
            Arc::new(Float64Array::from(vec![low])),
            Arc::new(Float64Array::from(vec![tick.bid])),
            Arc::new(Float64Array::from(vec![tick.ask])),
            Arc::new(UInt64Array::from(vec![tick.volume])),
            Arc::new(UInt64Array::from(vec![tick.timestamp_ns])),
            Arc::new(Float64Array::from(vec![ctx.win_rate])),
            Arc::new(UInt32Array::from(vec![ctx.total_trades])),
            Arc::new(Float64Array::from(vec![ctx.avg_win])),
            Arc::new(Float64Array::from(vec![ctx.avg_loss])),
            Arc::new(UInt32Array::from(vec![leverage])),
            Arc::new(Float64Array::from(vec![ctx.realized_vol])),
            Arc::new(Float64Array::from(vec![ctx.correlation])),
            Arc::new(Float64Array::from(vec![ctx.drawdown_pct])),
            Arc::new(Float64Array::from(vec![ctx.amihud])),
            Arc::new(StringArray::from(vec![regime_str])),
            Arc::new(Float64Array::from(vec![ctx.spread_pct])),
            Arc::new(Float64Array::from(vec![ctx.time_fraction])),
            Arc::new(Float64Array::from(vec![ctx.heat_pct])),
            Arc::new(Float64Array::from(vec![ctx.equity])),
            Arc::new(Float64Array::from(vec![ctx.vix])),
            Arc::new(UInt32Array::from(vec![ctx.london_time_secs])),
            Arc::new(Float64Array::from(vec![ctx.gap_pct])),
            Arc::new(StringArray::from(vec![ctx.symbol.as_str()])),
            Arc::new(UInt32Array::from(vec![ctx.open_positions])),
            Arc::new(UInt32Array::from(vec![ctx.trades_today])),
        ]) {
            Ok(b) => b,
            Err(e) => {
                eprintln!("Arrow IPC: batch creation failed: {e}, falling back to JSON");
                return self.evaluate_tick(tick, high, low, ctx);
            }
        };

        // Write to /dev/shm
        let tick_path = "/dev/shm/aegis_tick.arrow";
        let mut file = match std::fs::File::create(tick_path) {
            Ok(f) => f,
            Err(e) => {
                eprintln!("Arrow IPC: cannot create {tick_path}: {e}, falling back to JSON");
                return self.evaluate_tick(tick, high, low, ctx);
            }
        };

        let mut writer = match StreamWriter::try_new(&mut file, &schema) {
            Ok(w) => w,
            Err(e) => {
                eprintln!("Arrow IPC: stream writer failed: {e}, falling back to JSON");
                return self.evaluate_tick(tick, high, low, ctx);
            }
        };
        if writer.write(&batch).is_err() || writer.finish().is_err() {
            eprintln!("Arrow IPC: write failed, falling back to JSON");
            return self.evaluate_tick(tick, high, low, ctx);
        }
        drop(file);

        // Signal Python to read the Arrow file
        if writeln!(self.stdin, r#"{{"type":"arrow_tick"}}"#).is_err() {
            self.needs_respawn = true;
            return None;
        }
        let _ = self.stdin.flush();

        // Read ack from Python (lightweight JSON with resp_type)
        let ack_line = match self.read_line_timeout() {
            Some(line) => line,
            None => return None,
        };

        let ack: serde_json::Value = match serde_json::from_str(ack_line.trim()) {
            Ok(v) => v,
            Err(_) => return None,
        };

        let resp_type = ack.get("resp_type").and_then(|v| v.as_str()).unwrap_or("error");
        if resp_type != "signal" {
            return None;
        }

        // Read full signal response from Arrow file
        let signal_path = "/dev/shm/aegis_signal.arrow";
        let signal_file = match std::fs::File::open(signal_path) {
            Ok(f) => f,
            Err(_) => return None,
        };
        let reader = match StreamReader::try_new(signal_file, None) {
            Ok(r) => r,
            Err(_) => return None,
        };

        for batch_result in reader {
            let batch = match batch_result {
                Ok(b) => b,
                Err(_) => return None,
            };
            if batch.num_rows() == 0 { return None; }

            // Extract fields from Arrow batch
            let get_f64 = |name: &str| -> f64 {
                batch.column_by_name(name)
                    .and_then(|c| c.as_any().downcast_ref::<Float64Array>())
                    .map(|a| a.value(0))
                    .unwrap_or(0.0)
            };
            let get_i32 = |name: &str| -> i32 {
                batch.column_by_name(name)
                    .and_then(|c| c.as_any().downcast_ref::<Int32Array>())
                    .map(|a| a.value(0))
                    .unwrap_or(0)
            };
            let get_str = |name: &str| -> String {
                batch.column_by_name(name)
                    .and_then(|c| c.as_any().downcast_ref::<StringArray>())
                    .and_then(|a| if a.is_null(0) { None } else { Some(a.value(0).to_string()) })
                    .unwrap_or_default()
            };
            let get_opt_f64 = |name: &str| -> Option<f64> {
                batch.column_by_name(name)
                    .and_then(|c| c.as_any().downcast_ref::<Float64Array>())
                    .and_then(|a| if a.is_null(0) || a.value(0) == 0.0 { None } else { Some(a.value(0)) })
            };
            let get_opt_str = |name: &str| -> Option<String> {
                batch.column_by_name(name)
                    .and_then(|c| c.as_any().downcast_ref::<StringArray>())
                    .and_then(|a| if a.is_null(0) || a.value(0).is_empty() { None } else { Some(a.value(0).to_string()) })
            };

            self.consecutive_errors = 0;
            self.consecutive_empty = 0;

            return Some(BrainSignal {
                direction: get_str("direction"),
                confidence: get_f64("confidence"),
                kelly_fraction: get_f64("kelly_fraction"),
                shares: get_i32("shares") as u32,
                strategy: get_str("strategy"),
                rvol: get_f64("rvol"),
                hurst: get_f64("hurst"),
                adx: get_f64("adx"),
                vol_slope: get_f64("vol_slope"),
                vwap_dist_pct: get_f64("vwap_dist_pct"),
                structural_score: get_f64("structural_score"),
                entry_type: get_str("entry_type"),
                rsi: get_f64("rsi"),
                ibs: get_f64("ibs"),
                suggested_initial_stop_atr_mult: get_opt_f64("suggested_initial_stop_atr_mult"),
                suggested_rung3_atr: None,
                exit_trail_bias: get_opt_str("exit_trail_bias"),
                max_hold_hours: get_opt_f64("max_hold_hours"),
                suggested_max_hold_hours: None,
                exit_urgency_ramp_hours: None,
                min_profit_target_pct: None,
                execution_algo: get_opt_str("execution_algo"),
            });
        }
        None
    }

    /// Send 60-second OHLCV snapshots to ApexScout for evaluation.
    /// Returns None if no signal or if communication fails.
    pub fn evaluate_apex_snapshot(
        &mut self,
        ticker_id: TickerId,
        snapshots: Vec<serde_json::Value>,
    ) -> Option<BrainSignal> {
        // Build JSON message with snapshot array
        let snapshots_json = serde_json::to_string(&snapshots)
            .map_err(|e| {
                eprintln!("Apex snapshot JSON serialization failed: {e}");
                e
            })
            .ok()?;

        let msg = format!(
            r#"{{"type":"apex_snapshot","ticker_id":{},"snapshots":{}}}"#,
            ticker_id.0, snapshots_json
        );

        // Send — broken pipe means Python process is dead, flag for respawn.
        if writeln!(self.stdin, "{msg}").is_err() {
            eprintln!("CRITICAL: Python Bridge apex snapshot stdin write failed — marking for respawn");
            self.needs_respawn = true;
            return None;
        }
        if self.stdin.flush().is_err() {
            eprintln!("CRITICAL: Python Bridge apex snapshot stdin flush failed (broken pipe) — marking for respawn");
            self.needs_respawn = true;
            return None;
        }

        // P0-1.2: Read response with timeout (prevents engine freeze if Python hangs)
        let response_line = match self.read_line_timeout() {
            Some(line) => line,
            None => return None, // Timeout or disconnected
        };

        // Parse response
        let resp: serde_json::Value = serde_json::from_str(response_line.trim()).ok()?;

        let resp_type = resp.get("type")?.as_str()?;

        // Detect strategy errors
        if resp_type == "error" {
            let err_msg = resp
                .get("error")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            self.consecutive_errors += 1;
            if self.consecutive_errors == 1 || self.consecutive_errors.is_multiple_of(100) {
                eprintln!(
                    "PYTHON BRIDGE APEX ERROR (#{}) ticker={}: {}",
                    self.consecutive_errors,
                    resp.get("ticker_id").and_then(|v| v.as_i64()).unwrap_or(-1),
                    err_msg
                );
            }
            return None;
        }

        if resp_type == "signal" {
            self.consecutive_errors = 0;
        }

        if resp_type != "signal" {
            return None;
        }

        Some(BrainSignal {
            direction: resp.get("direction")?.as_str()?.to_string(),
            confidence: resp.get("confidence")?.as_f64()?,
            kelly_fraction: resp.get("kelly_fraction")?.as_f64()?,
            shares: resp.get("shares")?.as_u64()? as u32,
            strategy: resp.get("strategy")?.as_str()?.to_string(),
            rvol: resp.get("rvol").and_then(|v| v.as_f64()).unwrap_or(0.0),
            hurst: resp.get("hurst").and_then(|v| v.as_f64()).unwrap_or(0.0),
            adx: resp.get("adx").and_then(|v| v.as_f64()).unwrap_or(0.0),
            vol_slope: resp.get("vol_slope").and_then(|v| v.as_f64()).unwrap_or(0.0),
            vwap_dist_pct: resp.get("vwap_dist_pct").and_then(|v| v.as_f64()).unwrap_or(0.0),
            structural_score: resp.get("structural_score").and_then(|v| v.as_f64()).unwrap_or(0.0),
            entry_type: resp.get("entry_type").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            rsi: resp.get("rsi").and_then(|v| v.as_f64()).unwrap_or(0.0),
            ibs: resp.get("ibs").and_then(|v| v.as_f64()).unwrap_or(0.0),
            // Apex signals don't carry exit hints — use defaults
            suggested_initial_stop_atr_mult: None,
            suggested_rung3_atr: None,
            exit_trail_bias: None,
            max_hold_hours: None,
            suggested_max_hold_hours: None,
            exit_urgency_ramp_hours: None,
            min_profit_target_pct: None,
            execution_algo: None,
        })
    }

    /// Gracefully shut down the Python bridge.
    pub fn shutdown(&mut self) {
        let _ = writeln!(self.stdin, r#"{{"type":"shutdown"}}"#);
        let _ = self.stdin.flush();
        eprintln!("Python Bridge: shutdown sent");
    }
}

impl Drop for PythonBridge {
    fn drop(&mut self) {
        self.shutdown();
        // Reap the child process to prevent zombies.
        // Give it 2s to exit gracefully after shutdown message, then kill.
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            match self.child.try_wait() {
                Ok(Some(_status)) => break, // Reaped
                Ok(None) => {
                    if std::time::Instant::now() >= deadline {
                        let _ = self.child.kill();
                        let _ = self.child.wait(); // Reap after kill
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(50));
                }
                Err(_) => break,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    #[test]
    fn test_tick_context_default() {
        let ctx = TickContext::default();
        assert_eq!(ctx.win_rate, 0.5);
        assert_eq!(ctx.leverage, 3);
        assert_eq!(ctx.equity, 10_000.0);
        assert_eq!(ctx.regime, RiskRegime::Normal);
    }

    #[test]
    fn test_pyo3_tick_extraction_latency() {
        // AT-RM3: Verify <0.5ms for TickContext construction (native struct, no JSON)
        let iterations = 100_000;
        let start = Instant::now();

        for i in 0..iterations {
            let ctx = TickContext {
                win_rate: 0.55,
                total_trades: i as u32,
                avg_win: 0.025,
                avg_loss: 0.018,
                leverage: 3,
                realized_vol: 0.32,
                correlation: 0.15,
                drawdown_pct: 0.8,
                amihud: 0.001,
                regime: RiskRegime::Normal,
                spread_pct: 0.12,
                time_fraction: 0.6,
                heat_pct: 3.2,
                equity: 10_500.0,
                vix: 0.0,
                london_time_secs: 0,
                gap_pct: 0.0,
                symbol: String::new(),
                open_positions: 0,
                trades_today: 0,
                exchange: String::new(),
                consecutive_losses: 0,
            };
            // Prevent optimization elision
            std::hint::black_box(&ctx);
        }

        let elapsed = start.elapsed();
        let avg_ns = elapsed.as_nanos() / iterations as u128;

        // Native struct construction must be <500μs (0.5ms) — typically <100ns
        assert!(
            avg_ns < 500_000,
            "Average TickContext construction {}ns exceeds 500μs",
            avg_ns
        );
        eprintln!(
            "RM-3: {}k TickContext constructions, avg={}ns (vs 5-10ms for JSON)",
            iterations / 1000,
            avg_ns
        );
    }

    #[test]
    fn test_brain_signal_construction() {
        let signal = BrainSignal {
            direction: "Long".into(),
            confidence: 78.5,
            kelly_fraction: 0.12,
            shares: 50,
            strategy: "TypeB".into(),
            rvol: 1.5,
            hurst: 0.55,
            adx: 25.0,
            vol_slope: 0.5,
            vwap_dist_pct: 0.3,
            structural_score: 72.0,
            entry_type: "TypeB".into(),
            rsi: 45.0,
            ibs: 0.3,
            suggested_initial_stop_atr_mult: Some(1.5),
            suggested_rung3_atr: Some(1.0),
            exit_trail_bias: Some("neutral".into()),
            max_hold_hours: Some(8.0),
            suggested_max_hold_hours: Some(8.0),
            exit_urgency_ramp_hours: Some(6.0),
            min_profit_target_pct: Some(0.3),
            execution_algo: None,
        };
        assert_eq!(signal.shares, 50);
        assert_eq!(signal.confidence, 78.5);
        assert_eq!(signal.entry_type, "TypeB");
    }
}
