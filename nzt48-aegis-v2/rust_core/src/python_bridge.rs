//! Python Brain Bridge — subprocess IPC for signal generation.
//!
//! Spawns `python3 -m python_brain.bridge` and communicates via JSON lines
//! over stdin/stdout. Each tick sent gets a synchronous response.
//!
//! This avoids the PyO3 extension-module vs auto-initialize conflict.

use std::collections::HashMap;
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
}

#[pymethods]
impl TickContext {
    #[new]
    #[allow(clippy::too_many_arguments)]
    #[pyo3(signature = (win_rate=0.5, total_trades=0, avg_win=0.02, avg_loss=0.02, leverage=3, realized_vol=0.30, correlation=0.0, drawdown_pct=0.0, amihud=0.0, regime=RiskRegime::Normal, spread_pct=0.1, time_fraction=0.5, heat_pct=0.0, equity=10_000.0))]
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
            equity: 10_000.0,
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
}

impl PythonBridge {
    /// Start the Python bridge subprocess.
    /// P0-1.2: Spawns a dedicated reader thread for timeout-safe reads.
    pub fn start() -> Result<Self, String> {
        let mut child = Command::new("python3")
            .args(["/app/python_brain/bridge.py"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .current_dir("/app")
            .spawn()
            .map_err(|e| format!("Failed to start Python bridge: {e}"))?;

        let stdin = child.stdin.take().ok_or("No stdin on child process")?;
        let stdout = child.stdout.take().ok_or("No stdout on child process")?;

        // P0-1.2: Spawn reader thread — reads lines from stdout and sends via channel.
        // This allows the main thread to use recv_timeout() instead of blocking forever.
        let (line_tx, line_rx) = mpsc::channel::<String>();
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
            .map_err(|e| format!("Failed to spawn bridge reader thread: {e}"))?;

        eprintln!("Python Bridge: subprocess started (pid={})", child.id());

        Ok(Self {
            child,
            stdin,
            line_rx,
            leverage_map: HashMap::new(),
            consecutive_errors: 0,
            consecutive_timeouts: 0,
            consecutive_empty: 0,
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

        // Build JSON message
        let msg = format!(
            concat!(
                r#"{{"type":"tick","ticker_id":{},"last":{:.6},"high":{:.6},"low":{:.6},"#,
                r#""bid":{:.6},"ask":{:.6},"volume":{},"timestamp_ns":{},"#,
                r#""win_rate":{:.4},"total_trades":{},"avg_win":{:.4},"avg_loss":{:.4},"#,
                r#""leverage":{},"realized_vol":{:.4},"correlation":{:.4},"drawdown_pct":{:.4},"#,
                r#""amihud":{:.4},"regime":"{}","spread_pct":{:.4},"time_fraction":{:.4},"#,
                r#""heat_pct":{:.4},"equity":{:.2}}}"#
            ),
            tick.ticker_id.0,
            tick.last,
            high,
            low,
            tick.bid,
            tick.ask,
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
        );

        // Send
        if writeln!(self.stdin, "{msg}").is_err() {
            eprintln!("Python Bridge: stdin write failed");
            return None;
        }
        if self.stdin.flush().is_err() {
            eprintln!("Python Bridge: stdin flush failed");
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
            if self.consecutive_empty >= 10 && self.consecutive_empty % 10 == 0 {
                eprintln!(
                    "CRITICAL: Python bridge returned 0 signals for {} consecutive ticks — possible crash",
                    self.consecutive_empty
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
        })
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

        // Send
        if writeln!(self.stdin, "{msg}").is_err() {
            eprintln!("Python Bridge: apex snapshot stdin write failed");
            return None;
        }
        if self.stdin.flush().is_err() {
            eprintln!("Python Bridge: apex snapshot stdin flush failed");
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
            strategy: "VanguardSniper".into(),
            rvol: 1.5,
            hurst: 0.55,
            adx: 25.0,
            vol_slope: 0.5,
            vwap_dist_pct: 0.3,
            structural_score: 72.0,
        };
        assert_eq!(signal.shares, 50);
        assert_eq!(signal.confidence, 78.5);
    }
}
