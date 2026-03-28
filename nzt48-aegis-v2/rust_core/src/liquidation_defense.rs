//! Phase 16: Liquidation Cascade Defense.
//! Prevents catastrophic drawdown spirals via ISA ceiling enforcement,
//! daily drawdown flattening, and consecutive stop-loss halting (Blood Oath H12).

/// Liquidation Cascade Defense: multi-layer circuit breaker for leveraged ETP portfolios.
pub struct LiquidationDefense {
    /// ISA annual deposit limit (GBP, default £20,000).
    pub isa_annual_limit_gbp: f64,
    /// Year-to-date ISA deposits (GBP).
    pub isa_deposits_ytd: f64,
    /// Current day's drawdown percentage (0.0 = no drawdown, positive = loss).
    pub daily_drawdown_pct: f64,
    /// Consecutive stop-loss hits without an intervening win.
    pub consecutive_stop_losses: u32,
    /// Equity snapshot at start of trading day (GBP).
    pub start_of_day_equity: f64,
    /// P2-#31: Daily drawdown flatten threshold (%, was hardcoded 2.0).
    pub flatten_drawdown_pct: f64,
    /// P2-#32: Consecutive stop losses before halt (was hardcoded 3).
    pub halt_consecutive_stops: u32,
    /// P2-#33: ISA ceiling % — block entries when remaining allowance < this % of equity.
    pub isa_ceiling_pct: f64,
}

impl LiquidationDefense {
    /// Create a new defense instance with the given ISA annual limit.
    pub fn new(isa_limit: f64) -> Self {
        Self {
            isa_annual_limit_gbp: isa_limit,
            isa_deposits_ytd: 0.0,
            daily_drawdown_pct: 0.0,
            consecutive_stop_losses: 0,
            start_of_day_equity: 0.0,
            // P2: These now match config.toml values (were divergent hardcodes).
            flatten_drawdown_pct: 4.0,
            halt_consecutive_stops: 8,
            isa_ceiling_pct: 3.0,
        }
    }

    /// Record a deposit into the ISA account.
    pub fn record_deposit(&mut self, amount: f64) {
        self.isa_deposits_ytd += amount;
    }

    /// Remaining ISA allowance in GBP.
    pub fn remaining_isa_allowance(&self) -> f64 {
        (self.isa_annual_limit_gbp - self.isa_deposits_ytd).max(0.0)
    }

    /// Remaining ISA allowance as a percentage of current equity.
    /// Returns 0.0 if equity is zero or negative.
    pub fn remaining_allowance_pct(&self, equity: f64) -> f64 {
        if equity <= 0.0 {
            return 0.0;
        }
        self.remaining_isa_allowance() / equity * 100.0
    }

    /// True if remaining ISA allowance is less than ceiling % of equity — block new entries.
    pub fn should_block_entries(&self, equity: f64) -> bool {
        self.remaining_allowance_pct(equity) < self.isa_ceiling_pct
    }

    /// Update daily drawdown from current equity vs start-of-day snapshot.
    /// Drawdown is expressed as a positive percentage (e.g., 2.5 means 2.5% loss).
    pub fn update_drawdown(&mut self, current_equity: f64) {
        if self.start_of_day_equity <= 0.0 {
            self.daily_drawdown_pct = 0.0;
            return;
        }
        let change = (self.start_of_day_equity - current_equity) / self.start_of_day_equity * 100.0;
        self.daily_drawdown_pct = change.max(0.0);
    }

    /// True if daily drawdown exceeds threshold — flatten all positions.
    pub fn should_flatten(&self) -> bool {
        self.daily_drawdown_pct > self.flatten_drawdown_pct
    }

    /// Record a stop-loss hit (increments consecutive counter).
    pub fn record_stop_loss(&mut self) {
        self.consecutive_stop_losses += 1;
    }

    /// Record a winning trade (resets consecutive stop-loss counter).
    pub fn record_win(&mut self) {
        self.consecutive_stop_losses = 0;
    }

    /// True if consecutive stop losses exceed threshold — halt trading (Blood Oath H12).
    pub fn should_halt(&self) -> bool {
        self.consecutive_stop_losses >= self.halt_consecutive_stops
    }

    /// Reset daily state for a new trading day.
    /// Also resets consecutive stop loss counter — new day, clean slate.
    /// This enables auto-recovery from H12 Halt (3 consecutive losses).
    pub fn daily_reset(&mut self, equity: f64) {
        self.daily_drawdown_pct = 0.0;
        self.start_of_day_equity = equity;
        self.consecutive_stop_losses = 0;
    }

    /// Reset ISA deposits for a new tax year (April 6).
    pub fn isa_year_reset(&mut self) {
        self.isa_deposits_ytd = 0.0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_remaining_isa_allowance() {
        let mut ld = LiquidationDefense::new(20_000.0);
        assert!((ld.remaining_isa_allowance() - 20_000.0).abs() < f64::EPSILON);
        ld.record_deposit(15_000.0);
        assert!((ld.remaining_isa_allowance() - 5_000.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_isa_ceiling_blocks_entries() {
        let mut ld = LiquidationDefense::new(20_000.0);
        ld.record_deposit(19_800.0);
        // Remaining = 200, equity = 10_000 → 2% < 3% → block
        assert!(ld.should_block_entries(10_000.0));
    }

    #[test]
    fn test_isa_ceiling_allows_entries() {
        let mut ld = LiquidationDefense::new(20_000.0);
        ld.record_deposit(10_000.0);
        // Remaining = 10_000, equity = 10_000 → 100% > 3% → allow
        assert!(!ld.should_block_entries(10_000.0));
    }

    #[test]
    fn test_drawdown_flatten() {
        let mut ld = LiquidationDefense::new(20_000.0);
        ld.daily_reset(10_000.0);
        // P2: flatten_drawdown_pct is now 4.0 (was hardcoded 2.0). 4.5% loss triggers.
        ld.update_drawdown(9_550.0);
        assert!(ld.should_flatten());
    }

    #[test]
    fn test_drawdown_no_flatten() {
        let mut ld = LiquidationDefense::new(20_000.0);
        ld.daily_reset(10_000.0);
        // 3.5% loss — under 4.0% threshold
        ld.update_drawdown(9_650.0);
        assert!(!ld.should_flatten());
    }

    #[test]
    fn test_consecutive_stops_halt() {
        let mut ld = LiquidationDefense::new(20_000.0);
        // P2: halt_consecutive_stops is now 8 (was hardcoded 3).
        for _ in 0..7 {
            ld.record_stop_loss();
        }
        assert!(!ld.should_halt());
        ld.record_stop_loss(); // 8th → halt
        assert!(ld.should_halt());
    }

    #[test]
    fn test_win_resets_consecutive_stops() {
        let mut ld = LiquidationDefense::new(20_000.0);
        ld.record_stop_loss();
        ld.record_stop_loss();
        ld.record_win();
        assert_eq!(ld.consecutive_stop_losses, 0);
        assert!(!ld.should_halt());
    }

    #[test]
    fn test_daily_reset() {
        let mut ld = LiquidationDefense::new(20_000.0);
        ld.daily_drawdown_pct = 5.0;
        ld.daily_reset(12_000.0);
        assert!((ld.daily_drawdown_pct).abs() < f64::EPSILON);
        assert!((ld.start_of_day_equity - 12_000.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_isa_year_reset() {
        let mut ld = LiquidationDefense::new(20_000.0);
        ld.record_deposit(18_000.0);
        ld.isa_year_reset();
        assert!((ld.isa_deposits_ytd).abs() < f64::EPSILON);
        assert!((ld.remaining_isa_allowance() - 20_000.0).abs() < f64::EPSILON);
    }

    #[test]
    fn test_zero_equity_handling() {
        let ld = LiquidationDefense::new(20_000.0);
        assert!((ld.remaining_allowance_pct(0.0)).abs() < f64::EPSILON);
        assert!(ld.should_block_entries(0.0));
    }

    #[test]
    fn test_drawdown_equity_gain_no_negative_dd() {
        let mut ld = LiquidationDefense::new(20_000.0);
        ld.daily_reset(10_000.0);
        // Equity went UP — drawdown should stay at 0, not go negative
        ld.update_drawdown(10_500.0);
        assert!((ld.daily_drawdown_pct).abs() < f64::EPSILON);
    }
}
