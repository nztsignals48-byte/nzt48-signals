//! Phase 23: Crucible — 7-suite verification harness.
//! Engineering-vs-Alpha boundary: validates signal edge + infrastructure resilience.
//! All suites must pass before live capital deployment.

use std::collections::HashMap;

/// Suite 1: Trade Gate — statistical validation of paper trade results.
#[derive(Clone, Debug)]
pub struct TradeGateResult {
    pub win_rate: f64,
    pub t_stat: f64,
    pub sharpe_ratio: f64,
    pub max_drawdown_pct: f64,
    pub total_trades: usize,
    pub bootstrap_wr_p5: f64,
    pub bootstrap_sharpe_p5: f64,
    pub halt_events: u64,
}

impl TradeGateResult {
    /// Suite 1 passes if all criteria met.
    pub fn passes(&self) -> bool {
        self.win_rate >= 0.40
            && self.t_stat >= 2.0
            && self.sharpe_ratio > 0.0
            && self.max_drawdown_pct < 8.0
            && self.total_trades >= 100
            && self.halt_events == 0
    }
}

/// Compute trade gate statistics from a vector of PnL values (in GBP).
pub fn compute_trade_gate(pnl_series: &[f64], halt_events: u64) -> TradeGateResult {
    let n = pnl_series.len();
    if n == 0 {
        return TradeGateResult {
            win_rate: 0.0,
            t_stat: 0.0,
            sharpe_ratio: 0.0,
            max_drawdown_pct: 0.0,
            total_trades: 0,
            bootstrap_wr_p5: 0.0,
            bootstrap_sharpe_p5: 0.0,
            halt_events,
        };
    }

    let wins = pnl_series.iter().filter(|&&p| p > 0.0).count();
    let win_rate = wins as f64 / n as f64;

    let mean_pnl = pnl_series.iter().sum::<f64>() / n as f64;
    let variance =
        pnl_series.iter().map(|p| (p - mean_pnl).powi(2)).sum::<f64>() / (n as f64 - 1.0).max(1.0);
    let std_pnl = variance.sqrt();

    let t_stat = if std_pnl > 0.0 {
        mean_pnl / (std_pnl / (n as f64).sqrt())
    } else {
        0.0
    };

    // Annualized Sharpe (assume 252 trading days)
    let sharpe_ratio = if std_pnl > 0.0 {
        (mean_pnl / std_pnl) * (252.0_f64).sqrt()
    } else {
        0.0
    };

    // Max drawdown from cumulative PnL
    let max_drawdown_pct = compute_max_drawdown(pnl_series);

    // Bootstrap: deterministic simple bootstrap (no RNG dependency)
    let (bootstrap_wr_p5, bootstrap_sharpe_p5) = deterministic_bootstrap(pnl_series);

    TradeGateResult {
        win_rate,
        t_stat,
        sharpe_ratio,
        max_drawdown_pct,
        total_trades: n,
        bootstrap_wr_p5,
        bootstrap_sharpe_p5,
        halt_events,
    }
}

/// Compute max drawdown as percentage of peak equity.
fn compute_max_drawdown(pnl_series: &[f64]) -> f64 {
    let starting_equity = 10_000.0; // £10k ISA starting
    let mut equity = starting_equity;
    let mut peak = starting_equity;
    let mut max_dd = 0.0;

    for &pnl in pnl_series {
        equity += pnl;
        if equity > peak {
            peak = equity;
        }
        let dd = (peak - equity) / peak * 100.0;
        if dd > max_dd {
            max_dd = dd;
        }
    }
    max_dd
}

/// Deterministic bootstrap using block resampling (no RNG).
/// Uses systematic sampling with different offsets for 100 iterations.
fn deterministic_bootstrap(pnl_series: &[f64]) -> (f64, f64) {
    let n = pnl_series.len();
    if n < 10 {
        return (0.0, 0.0);
    }

    let iterations = 100;
    let mut win_rates = Vec::with_capacity(iterations);
    let mut sharpes = Vec::with_capacity(iterations);

    for i in 0..iterations {
        // Systematic resampling: shift offset
        let offset = (i * 7 + 3) % n;
        let mut sample_wins = 0usize;
        let mut sample_sum = 0.0f64;
        let mut sample_sq_sum = 0.0f64;

        for j in 0..n {
            let idx = (offset + j * (i + 1)) % n;
            let val = pnl_series[idx];
            if val > 0.0 {
                sample_wins += 1;
            }
            sample_sum += val;
            sample_sq_sum += val * val;
        }

        let wr = sample_wins as f64 / n as f64;
        let mean = sample_sum / n as f64;
        let var = (sample_sq_sum / n as f64) - mean * mean;
        let std = var.max(0.0).sqrt();
        let sharpe = if std > 0.0 {
            (mean / std) * (252.0_f64).sqrt()
        } else {
            0.0
        };

        win_rates.push(wr);
        sharpes.push(sharpe);
    }

    win_rates.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    sharpes.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));

    let p5_idx = (iterations as f64 * 0.05) as usize;
    (win_rates[p5_idx], sharpes[p5_idx])
}

/// Suite 2: SIGTERM Flatten Drill result.
#[derive(Clone, Debug)]
pub struct FlattenDrillResult {
    pub repetitions: u32,
    pub all_flat_on_restart: bool,
    pub wal_consistent: bool,
    pub orphaned_positions: u32,
}

impl FlattenDrillResult {
    pub fn passes(&self) -> bool {
        self.repetitions >= 5
            && self.all_flat_on_restart
            && self.wal_consistent
            && self.orphaned_positions == 0
    }
}

/// Suite 3: Shadow Run result.
#[derive(Clone, Debug)]
pub struct ShadowRunResult {
    pub duration_hours: f64,
    pub max_divergence_gbp: f64,
    pub mode_transitions_logged: bool,
    pub max_transition_latency_ms: f64,
}

impl ShadowRunResult {
    pub fn passes(&self) -> bool {
        self.duration_hours >= 48.0
            && self.max_divergence_gbp < 50.0
            && self.mode_transitions_logged
            && self.max_transition_latency_ms < 50.0
    }
}

/// Suite 4: Chaos Engineering result.
#[derive(Clone, Debug)]
pub struct ChaosResult {
    pub python_bridge_recovered: bool,
    pub ibkr_recovered: bool,
    pub redis_recovered: bool,
    pub no_manual_intervention: bool,
}

impl ChaosResult {
    pub fn passes(&self) -> bool {
        self.python_bridge_recovered
            && self.ibkr_recovered
            && self.redis_recovered
            && self.no_manual_intervention
    }
}

/// Suite 5: ISA Compliance Audit.
#[derive(Clone, Debug)]
pub struct IsaAuditResult {
    pub total_intents: u32,
    pub short_orders: u32,
    pub blocked_exchange_orders: u32,
    pub over_limit_orders: u32,
    pub corporate_action_vetoes: u32,
    pub all_violations: Vec<String>,
}

impl IsaAuditResult {
    pub fn passes(&self) -> bool {
        self.total_intents >= 200
            && self.short_orders == 0
            && self.blocked_exchange_orders == 0
            && self.over_limit_orders == 0
            && self.all_violations.is_empty()
    }
}

/// Validate a batch of synthetic order intents against ISA rules.
pub fn run_isa_audit(intents: &[OrderIntent]) -> IsaAuditResult {
    let blocked_exchanges = ["TWSE", "XTAI", "XSHG", "XSHE", "XBOM", "XNSE"];
    let mut result = IsaAuditResult {
        total_intents: intents.len() as u32,
        short_orders: 0,
        blocked_exchange_orders: 0,
        over_limit_orders: 0,
        corporate_action_vetoes: 0,
        all_violations: Vec::new(),
    };

    let mut cumulative_deposit = 0.0f64;
    let annual_limit = 20_000.0;

    for intent in intents {
        // No short orders in ISA
        if intent.quantity < 0 {
            result.short_orders += 1;
            result.all_violations.push(format!(
                "SHORT order: {} qty={}",
                intent.symbol, intent.quantity
            ));
        }

        // No blocked exchanges
        if blocked_exchanges.contains(&intent.exchange_mic.as_str()) {
            result.blocked_exchange_orders += 1;
            result.all_violations.push(format!(
                "Blocked exchange: {} on {}",
                intent.symbol, intent.exchange_mic
            ));
        }

        // Deposit limit
        if intent.quantity > 0 {
            cumulative_deposit += intent.notional_gbp;
            if cumulative_deposit > annual_limit {
                result.over_limit_orders += 1;
                result.all_violations.push(format!(
                    "Over ISA limit: cumulative £{:.0} > £{:.0}",
                    cumulative_deposit, annual_limit
                ));
            }
        }

        // Corporate action blocklist
        if intent.corporate_action_blocked {
            result.corporate_action_vetoes += 1;
        }
    }

    result
}

/// Synthetic order intent for ISA audit.
#[derive(Clone, Debug)]
pub struct OrderIntent {
    pub symbol: String,
    pub exchange_mic: String,
    pub quantity: i32,
    pub notional_gbp: f64,
    pub corporate_action_blocked: bool,
}

/// Suite 6: Line Budget Stress Test result.
#[derive(Clone, Debug)]
pub struct LineBudgetResult {
    pub sequences_tested: u32,
    pub max_active_lines: u32,
    pub line_limit: u32,
    pub violations: u32,
    pub scanner_conservation_held: bool,
}

impl LineBudgetResult {
    pub fn passes(&self) -> bool {
        self.sequences_tested >= 1000
            && self.violations == 0
            && self.max_active_lines <= self.line_limit
            && self.scanner_conservation_held
    }
}

/// Suite 7: Full Mode Cycle result.
#[derive(Clone, Debug)]
pub struct ModeCycleResult {
    pub modes_visited: Vec<String>,
    pub dst_boundary_handled: bool,
    pub ouroboros_completed: bool,
    pub spread_cache_fresh: bool,
    pub watchdog_running: bool,
}

impl ModeCycleResult {
    pub fn passes(&self) -> bool {
        let required = ["ModeA", "Dark", "ModeB", "ModeBPlus", "ModeC"];
        let all_visited = required.iter().all(|m| self.modes_visited.contains(&m.to_string()));
        all_visited
            && self.dst_boundary_handled
            && self.ouroboros_completed
            && self.spread_cache_fresh
            && self.watchdog_running
    }
}

/// Master Crucible: aggregates all 7 suites.
#[derive(Clone, Debug)]
pub struct CrucibleResult {
    pub suite1_trade_gate: Option<TradeGateResult>,
    pub suite2_flatten_drill: Option<FlattenDrillResult>,
    pub suite3_shadow_run: Option<ShadowRunResult>,
    pub suite4_chaos: Option<ChaosResult>,
    pub suite5_isa_audit: Option<IsaAuditResult>,
    pub suite6_line_budget: Option<LineBudgetResult>,
    pub suite7_mode_cycle: Option<ModeCycleResult>,
}

impl CrucibleResult {
    pub fn new() -> Self {
        Self {
            suite1_trade_gate: None,
            suite2_flatten_drill: None,
            suite3_shadow_run: None,
            suite4_chaos: None,
            suite5_isa_audit: None,
            suite6_line_budget: None,
            suite7_mode_cycle: None,
        }
    }

    /// Count how many suites have been run.
    pub fn suites_run(&self) -> u32 {
        let mut count = 0;
        if self.suite1_trade_gate.is_some() { count += 1; }
        if self.suite2_flatten_drill.is_some() { count += 1; }
        if self.suite3_shadow_run.is_some() { count += 1; }
        if self.suite4_chaos.is_some() { count += 1; }
        if self.suite5_isa_audit.is_some() { count += 1; }
        if self.suite6_line_budget.is_some() { count += 1; }
        if self.suite7_mode_cycle.is_some() { count += 1; }
        count
    }

    /// Count how many suites passed.
    pub fn suites_passed(&self) -> u32 {
        let mut count = 0;
        if self.suite1_trade_gate.as_ref().is_some_and(|s| s.passes()) { count += 1; }
        if self.suite2_flatten_drill.as_ref().is_some_and(|s| s.passes()) { count += 1; }
        if self.suite3_shadow_run.as_ref().is_some_and(|s| s.passes()) { count += 1; }
        if self.suite4_chaos.as_ref().is_some_and(|s| s.passes()) { count += 1; }
        if self.suite5_isa_audit.as_ref().is_some_and(|s| s.passes()) { count += 1; }
        if self.suite6_line_budget.as_ref().is_some_and(|s| s.passes()) { count += 1; }
        if self.suite7_mode_cycle.as_ref().is_some_and(|s| s.passes()) { count += 1; }
        count
    }

    /// All 7 suites run and passed → APPROVED FOR LIVE CAPITAL.
    pub fn approved_for_live(&self) -> bool {
        self.suites_run() == 7 && self.suites_passed() == 7
    }

    /// Summary of pass/fail per suite.
    pub fn summary(&self) -> HashMap<&'static str, bool> {
        let mut map = HashMap::new();
        map.insert("Suite1_TradeGate", self.suite1_trade_gate.as_ref().is_some_and(|s| s.passes()));
        map.insert("Suite2_FlattenDrill", self.suite2_flatten_drill.as_ref().is_some_and(|s| s.passes()));
        map.insert("Suite3_ShadowRun", self.suite3_shadow_run.as_ref().is_some_and(|s| s.passes()));
        map.insert("Suite4_Chaos", self.suite4_chaos.as_ref().is_some_and(|s| s.passes()));
        map.insert("Suite5_IsaAudit", self.suite5_isa_audit.as_ref().is_some_and(|s| s.passes()));
        map.insert("Suite6_LineBudget", self.suite6_line_budget.as_ref().is_some_and(|s| s.passes()));
        map.insert("Suite7_ModeCycle", self.suite7_mode_cycle.as_ref().is_some_and(|s| s.passes()));
        map
    }
}

impl Default for CrucibleResult {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── Suite 1: Trade Gate ──

    #[test]
    fn test_trade_gate_winning_series() {
        // 100 trades: 55 wins (+£50), 45 losses (-£40), interleaved
        let mut pnl = Vec::new();
        for i in 0..100 {
            if i % 2 == 0 || i > 89 {
                pnl.push(50.0); // wins: 50 even + 5 tail = 55
            } else {
                pnl.push(-40.0); // losses: 45 odd
            }
        }
        let result = compute_trade_gate(&pnl, 0);
        assert_eq!(result.total_trades, 100);
        assert!((result.win_rate - 0.55).abs() < 0.01);
        assert!(result.t_stat >= 2.0, "t_stat={}", result.t_stat);
        assert!(result.sharpe_ratio > 0.0, "sharpe={}", result.sharpe_ratio);
        assert!(result.passes(), "WR={} t={} sharpe={} dd={} trades={} halts={}",
            result.win_rate, result.t_stat, result.sharpe_ratio, result.max_drawdown_pct, result.total_trades, result.halt_events);
    }

    #[test]
    fn test_trade_gate_losing_series() {
        // 100 trades: 30 wins, 70 losses (interleaved)
        let mut pnl = Vec::with_capacity(100);
        for i in 0..100 {
            if i % 10 < 3 { pnl.push(20.0); } // 30% wins
            else { pnl.push(-30.0); }
        }
        let result = compute_trade_gate(&pnl, 0);
        assert!(result.win_rate < 0.40);
        assert!(!result.passes(), "Should fail WR < 40%");
    }

    #[test]
    fn test_trade_gate_insufficient_trades() {
        let pnl: Vec<f64> = (0..50).map(|_| 10.0).collect();
        let result = compute_trade_gate(&pnl, 0);
        assert_eq!(result.total_trades, 50);
        assert!(!result.passes(), "Should fail: < 100 trades");
    }

    #[test]
    fn test_trade_gate_halt_events_fail() {
        let pnl: Vec<f64> = (0..100).map(|_| 10.0).collect();
        let result = compute_trade_gate(&pnl, 1);
        assert!(!result.passes(), "Should fail: halt events > 0");
    }

    #[test]
    fn test_max_drawdown_calculation() {
        // Equity: 10000 → 10100 → 10200 → 9800 → 10000
        let pnl = vec![100.0, 100.0, -400.0, 200.0];
        let dd = compute_max_drawdown(&pnl);
        // Peak = 10200, trough = 9800, DD = 400/10200 = 3.92%
        assert!((dd - 3.92).abs() < 0.1, "dd={}", dd);
    }

    #[test]
    fn test_trade_gate_empty() {
        let result = compute_trade_gate(&[], 0);
        assert_eq!(result.total_trades, 0);
        assert!(!result.passes());
    }

    // ── Suite 5: ISA Compliance Audit ──

    #[test]
    fn test_isa_audit_clean() {
        let intents: Vec<OrderIntent> = (0..200)
            .map(|i| OrderIntent {
                symbol: format!("ETP{}.L", i),
                exchange_mic: "XLON".to_string(),
                quantity: 10,
                notional_gbp: 50.0, // 200 * 50 = £10,000 < £20,000
                corporate_action_blocked: false,
            })
            .collect();

        let result = run_isa_audit(&intents);
        assert!(result.passes(), "Clean audit should pass");
        assert_eq!(result.short_orders, 0);
        assert_eq!(result.blocked_exchange_orders, 0);
    }

    #[test]
    fn test_isa_audit_catches_short() {
        let intents = vec![OrderIntent {
            symbol: "QQQ3.L".to_string(),
            exchange_mic: "XLON".to_string(),
            quantity: -5,
            notional_gbp: 500.0,
            corporate_action_blocked: false,
        }];
        let result = run_isa_audit(&intents);
        assert_eq!(result.short_orders, 1);
        assert!(!result.all_violations.is_empty());
    }

    #[test]
    fn test_isa_audit_catches_blocked_exchange() {
        let intents = vec![OrderIntent {
            symbol: "TSMC".to_string(),
            exchange_mic: "TWSE".to_string(),
            quantity: 10,
            notional_gbp: 1000.0,
            corporate_action_blocked: false,
        }];
        let result = run_isa_audit(&intents);
        assert_eq!(result.blocked_exchange_orders, 1);
    }

    #[test]
    fn test_isa_audit_catches_over_limit() {
        let intents: Vec<OrderIntent> = (0..25)
            .map(|i| OrderIntent {
                symbol: format!("ETP{}.L", i),
                exchange_mic: "XLON".to_string(),
                quantity: 10,
                notional_gbp: 1000.0, // 25 * 1000 = £25,000 > £20,000
                corporate_action_blocked: false,
            })
            .collect();

        let result = run_isa_audit(&intents);
        assert!(result.over_limit_orders > 0);
    }

    #[test]
    fn test_isa_audit_corporate_action_veto() {
        let intents = vec![OrderIntent {
            symbol: "BLOCKED.L".to_string(),
            exchange_mic: "XLON".to_string(),
            quantity: 10,
            notional_gbp: 100.0,
            corporate_action_blocked: true,
        }];
        let result = run_isa_audit(&intents);
        assert_eq!(result.corporate_action_vetoes, 1);
    }

    // ── Suite 6: Line Budget ──

    #[test]
    fn test_line_budget_passes() {
        let result = LineBudgetResult {
            sequences_tested: 1000,
            max_active_lines: 80,
            line_limit: 100,
            violations: 0,
            scanner_conservation_held: true,
        };
        assert!(result.passes());
    }

    #[test]
    fn test_line_budget_violation() {
        let result = LineBudgetResult {
            sequences_tested: 1000,
            max_active_lines: 105,
            line_limit: 100,
            violations: 5,
            scanner_conservation_held: true,
        };
        assert!(!result.passes());
    }

    // ── Suite 7: Mode Cycle ──

    #[test]
    fn test_mode_cycle_complete() {
        let result = ModeCycleResult {
            modes_visited: vec![
                "ModeA".to_string(),
                "Dark".to_string(),
                "ModeB".to_string(),
                "ModeBPlus".to_string(),
                "ModeC".to_string(),
            ],
            dst_boundary_handled: true,
            ouroboros_completed: true,
            spread_cache_fresh: true,
            watchdog_running: true,
        };
        assert!(result.passes());
    }

    #[test]
    fn test_mode_cycle_missing_mode() {
        let result = ModeCycleResult {
            modes_visited: vec![
                "ModeA".to_string(),
                "Dark".to_string(),
                "ModeB".to_string(),
                // Missing ModeBPlus and ModeC
            ],
            dst_boundary_handled: true,
            ouroboros_completed: true,
            spread_cache_fresh: true,
            watchdog_running: true,
        };
        assert!(!result.passes());
    }

    // ── Operational Suites (2-4): structural tests ──

    #[test]
    fn test_flatten_drill_passes() {
        let result = FlattenDrillResult {
            repetitions: 5,
            all_flat_on_restart: true,
            wal_consistent: true,
            orphaned_positions: 0,
        };
        assert!(result.passes());
    }

    #[test]
    fn test_flatten_drill_orphaned() {
        let result = FlattenDrillResult {
            repetitions: 5,
            all_flat_on_restart: true,
            wal_consistent: true,
            orphaned_positions: 1,
        };
        assert!(!result.passes());
    }

    #[test]
    fn test_shadow_run_passes() {
        let result = ShadowRunResult {
            duration_hours: 48.0,
            max_divergence_gbp: 25.0,
            mode_transitions_logged: true,
            max_transition_latency_ms: 12.0,
        };
        assert!(result.passes());
    }

    #[test]
    fn test_shadow_run_high_divergence() {
        let result = ShadowRunResult {
            duration_hours: 48.0,
            max_divergence_gbp: 75.0,
            mode_transitions_logged: true,
            max_transition_latency_ms: 12.0,
        };
        assert!(!result.passes());
    }

    #[test]
    fn test_chaos_passes() {
        let result = ChaosResult {
            python_bridge_recovered: true,
            ibkr_recovered: true,
            redis_recovered: true,
            no_manual_intervention: true,
        };
        assert!(result.passes());
    }

    #[test]
    fn test_chaos_ibkr_not_recovered() {
        let result = ChaosResult {
            python_bridge_recovered: true,
            ibkr_recovered: false,
            redis_recovered: true,
            no_manual_intervention: true,
        };
        assert!(!result.passes());
    }

    // ── Master Crucible ──

    #[test]
    fn test_crucible_all_pass() {
        let mut crucible = CrucibleResult::new();

        // Suite 1: passing trade gate (interleaved wins/losses)
        let mut pnl = Vec::new();
        for i in 0..100 {
            if i % 5 < 3 { pnl.push(50.0); } // 60% wins
            else { pnl.push(-40.0); }
        }
        crucible.suite1_trade_gate = Some(compute_trade_gate(&pnl, 0));

        // Suite 2: passing flatten
        crucible.suite2_flatten_drill = Some(FlattenDrillResult {
            repetitions: 5, all_flat_on_restart: true,
            wal_consistent: true, orphaned_positions: 0,
        });

        // Suite 3: passing shadow
        crucible.suite3_shadow_run = Some(ShadowRunResult {
            duration_hours: 50.0, max_divergence_gbp: 10.0,
            mode_transitions_logged: true, max_transition_latency_ms: 8.0,
        });

        // Suite 4: passing chaos
        crucible.suite4_chaos = Some(ChaosResult {
            python_bridge_recovered: true, ibkr_recovered: true,
            redis_recovered: true, no_manual_intervention: true,
        });

        // Suite 5: passing ISA audit
        let intents: Vec<OrderIntent> = (0..200)
            .map(|i| OrderIntent {
                symbol: format!("ETP{}.L", i),
                exchange_mic: "XLON".to_string(),
                quantity: 10, notional_gbp: 50.0,
                corporate_action_blocked: false,
            })
            .collect();
        crucible.suite5_isa_audit = Some(run_isa_audit(&intents));

        // Suite 6: passing line budget
        crucible.suite6_line_budget = Some(LineBudgetResult {
            sequences_tested: 1000, max_active_lines: 75,
            line_limit: 100, violations: 0, scanner_conservation_held: true,
        });

        // Suite 7: passing mode cycle
        crucible.suite7_mode_cycle = Some(ModeCycleResult {
            modes_visited: vec![
                "ModeA".into(), "Dark".into(), "ModeB".into(),
                "ModeBPlus".into(), "ModeC".into(),
            ],
            dst_boundary_handled: true, ouroboros_completed: true,
            spread_cache_fresh: true, watchdog_running: true,
        });

        assert_eq!(crucible.suites_run(), 7);
        assert_eq!(crucible.suites_passed(), 7);
        assert!(crucible.approved_for_live());
    }

    #[test]
    fn test_crucible_partial_fail() {
        let mut crucible = CrucibleResult::new();

        // Only suite 1 with failing result
        let pnl: Vec<f64> = (0..50).map(|_| -10.0).collect();
        crucible.suite1_trade_gate = Some(compute_trade_gate(&pnl, 0));

        assert_eq!(crucible.suites_run(), 1);
        assert_eq!(crucible.suites_passed(), 0);
        assert!(!crucible.approved_for_live());
    }

    #[test]
    fn test_crucible_summary() {
        let crucible = CrucibleResult::new();
        let summary = crucible.summary();
        assert_eq!(summary.len(), 7);
        for passed in summary.values() {
            assert!(!passed);
        }
    }
}
