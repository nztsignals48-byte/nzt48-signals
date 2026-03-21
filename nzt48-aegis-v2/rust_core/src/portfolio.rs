//! PortfolioState — tracks positions, cash, PnL, sector/inverse metadata.
//! Maintained by the Executioner. Python receives clones (H40).

use std::collections::HashMap;

use crate::types::{PositionState, TickerId};

/// SC-10: VWAP cost-basis tracker for a single position.
/// Updated on each FillEvent. Cleared nightly + reqPositions resync.
#[derive(Clone, Debug)]
pub struct CostBasisEntry {
    /// Volume-weighted average price across all fills.
    pub vwap: f64,
    /// Total shares filled.
    pub total_qty: u32,
    /// Total cost (sum of price * qty per fill).
    pub total_cost: f64,
    /// Total commissions paid.
    pub total_commission: f64,
}

impl CostBasisEntry {
    pub fn new() -> Self {
        Self {
            vwap: 0.0,
            total_qty: 0,
            total_cost: 0.0,
            total_commission: 0.0,
        }
    }

    /// Record a fill, updating VWAP.
    pub fn record_fill(&mut self, price: f64, qty: u32, commission: f64) {
        self.total_cost += price * qty as f64;
        self.total_qty += qty;
        self.total_commission += commission;
        if self.total_qty > 0 {
            self.vwap = self.total_cost / self.total_qty as f64;
        }
    }

    /// Net cost basis including commissions.
    pub fn net_cost_basis(&self) -> f64 {
        self.total_cost + self.total_commission
    }
}

impl Default for CostBasisEntry {
    fn default() -> Self {
        Self::new()
    }
}

/// Live portfolio state. Source of truth for risk calculations.
#[derive(Clone, Debug)]
pub struct PortfolioState {
    positions: HashMap<TickerId, PositionState>,
    pending_count: u32,
    pub equity: f64,
    pub cash: f64,
    pub high_water_mark: f64,
    pub daily_pnl: f64,
    pub isa_year_invested: f64,
    pub consecutive_stop_losses: u32,
    sector_map: HashMap<TickerId, String>,
    inverse_map: HashMap<TickerId, TickerId>,
    /// SC-10: VWAP cost-basis tracker per ticker.
    cost_basis: HashMap<TickerId, CostBasisEntry>,
    /// WP-6: Dividend withholding tax factor (UK ISA: 0.85).
    pub dividend_withholding_factor: f64,
    /// N0a: Daily trade count for frequency management.
    /// Reset in maybe_daily_reset(). Incremented on each approved entry fill.
    pub daily_trade_count: u32,
}

impl PortfolioState {
    pub fn new(equity: f64) -> Self {
        Self {
            positions: HashMap::new(),
            pending_count: 0,
            equity,
            cash: equity,
            high_water_mark: equity,
            daily_pnl: 0.0,
            isa_year_invested: 0.0,
            consecutive_stop_losses: 0,
            sector_map: HashMap::new(),
            inverse_map: HashMap::new(),
            cost_basis: HashMap::new(),
            dividend_withholding_factor: 0.85,
            daily_trade_count: 0,
        }
    }

    pub fn add_position(&mut self, pos: PositionState) {
        let cost = pos.avg_entry * pos.qty as f64;
        self.cash -= cost;
        // P0-02 FIX: Track ISA annual investment for £20K limit enforcement.
        self.isa_year_invested += cost;
        self.positions.insert(pos.ticker_id, pos);
    }

    pub fn remove_position(&mut self, ticker_id: TickerId) -> Option<PositionState> {
        if let Some(pos) = self.positions.remove(&ticker_id) {
            let value = pos.avg_entry * pos.qty as f64 + pos.unrealized_pnl;
            self.cash += value;
            // P1-2.4: Credit sell proceeds back against ISA invested amount.
            // Sell proceeds stay inside the ISA wrapper — they reduce gross invested
            // so the £20K limit tracks NET new external deposits, not internal turnover.
            let entry_cost = pos.avg_entry * pos.qty as f64;
            self.isa_year_invested = (self.isa_year_invested - entry_cost).max(0.0);
            Some(pos)
        } else {
            None
        }
    }

    pub fn get_position(&self, ticker_id: &TickerId) -> Option<&PositionState> {
        self.positions.get(ticker_id)
    }

    pub fn positions(&self) -> &HashMap<TickerId, PositionState> {
        &self.positions
    }

    /// Mutable access to positions (for WAL replay rung restoration).
    pub fn positions_mut(&mut self) -> &mut HashMap<TickerId, PositionState> {
        &mut self.positions
    }

    /// Filled + pending combined (H34).
    pub fn total_position_count(&self) -> u32 {
        self.positions.len() as u32 + self.pending_count
    }

    pub fn filled_count(&self) -> u32 {
        self.positions.len() as u32
    }

    pub fn set_pending_count(&mut self, count: u32) {
        self.pending_count = count;
    }

    /// Register a ticker's sector for heat cap checks (H30).
    pub fn register_sector(&mut self, ticker_id: TickerId, sector: String) {
        self.sector_map.insert(ticker_id, sector);
    }

    /// Register an inverse pair: if A is open, B is blocked and vice versa (H32).
    pub fn register_inverse_pair(&mut self, a: TickerId, b: TickerId) {
        self.inverse_map.insert(a, b);
        self.inverse_map.insert(b, a);
    }

    /// Check if an inverse of `ticker_id` is currently held.
    pub fn inverse_blocker(&self, ticker_id: TickerId) -> Option<TickerId> {
        if let Some(&inverse) = self.inverse_map.get(&ticker_id)
            && self.positions.contains_key(&inverse)
        {
            return Some(inverse);
        }
        None
    }

    /// Portfolio heat = sum((entry - stop) * qty / equity) * 100 (%).
    pub fn portfolio_heat_pct(&self) -> f64 {
        if self.equity <= 0.0 {
            return 0.0;
        }
        let risk: f64 = self
            .positions
            .values()
            .map(|p| (p.avg_entry - p.stop_price).max(0.0) * p.qty as f64)
            .sum();
        risk / self.equity * 100.0
    }

    /// Sector heat for the sector containing `ticker_id` (% of equity).
    pub fn sector_heat_pct(&self, ticker_id: TickerId) -> f64 {
        if self.equity <= 0.0 {
            return 0.0;
        }
        let Some(sector) = self.sector_map.get(&ticker_id) else {
            return 0.0;
        };
        let exposure: f64 = self
            .positions
            .iter()
            .filter(|(tid, _)| self.sector_map.get(tid) == Some(sector))
            .map(|(_, p)| p.avg_entry * p.qty as f64)
            .sum();
        exposure / self.equity * 100.0
    }

    /// Daily drawdown from high-water mark (%).
    pub fn daily_drawdown_pct(&self) -> f64 {
        if self.high_water_mark <= 0.0 {
            return 0.0;
        }
        (self.high_water_mark - self.equity) / self.high_water_mark * 100.0
    }

    /// Cash as percentage of equity.
    pub fn cash_buffer_pct(&self) -> f64 {
        if self.equity <= 0.0 {
            return 0.0;
        }
        self.cash / self.equity * 100.0
    }

    /// Phase 15: CVaR (Conditional Value at Risk) heat as percentage of equity.
    /// Estimates expected tail loss (95th percentile) across all positions.
    /// Uses parametric approach: CVaR ≈ Σ(position_value × σ × 2.063) / equity × 100.
    /// 2.063 = E[Z | Z > 1.645] for normal distribution (95% CVaR factor).
    pub fn cvar_heat_pct(&self, volatilities: &std::collections::HashMap<TickerId, f64>) -> f64 {
        if self.equity <= 0.0 {
            return 0.0;
        }
        const CVAR_95_FACTOR: f64 = 2.063;
        let cvar_sum: f64 = self
            .positions
            .values()
            .map(|p| {
                let pos_value = p.avg_entry * p.qty as f64;
                let vol = volatilities.get(&p.ticker_id).copied().unwrap_or(0.30);
                // Daily CVaR: annualized vol → daily vol (/√252) × CVaR factor
                pos_value * vol / 252.0_f64.sqrt() * CVAR_95_FACTOR
            })
            .sum();
        cvar_sum / self.equity * 100.0
    }

    /// Phase 15: Check if a ticker already has an open position.
    pub fn has_position(&self, ticker_id: &TickerId) -> bool {
        self.positions.contains_key(ticker_id)
    }

    /// Count how many open positions exist for a specific ticker.
    /// Used by momentum re-entry logic to allow winners to have multiple positions.
    pub fn position_count_for(&self, ticker_id: &TickerId) -> u32 {
        if self.positions.contains_key(ticker_id) { 1 } else { 0 }
    }

    /// Mark-to-market: recalculate equity from cash + sum of position market values.
    /// FIX: equity was set at construction and NEVER updated, always showing initial value.
    pub fn mark_to_market(&mut self, last_prices: &std::collections::HashMap<TickerId, f64>) {
        let position_value: f64 = self.positions.values().map(|p| {
            let price = last_prices.get(&p.ticker_id).copied().unwrap_or(p.avg_entry);
            price * p.qty as f64
        }).sum();
        self.equity = self.cash + position_value;
        // Update daily P&L
        self.daily_pnl = self.equity - self.high_water_mark;
    }

    pub fn update_high_water(&mut self) {
        if self.equity > self.high_water_mark {
            self.high_water_mark = self.equity;
        }
    }

    // ── SC-10: Cost-basis VWAP tracker ──

    /// Record a fill in the cost-basis tracker.
    pub fn record_fill(&mut self, ticker_id: TickerId, price: f64, qty: u32, commission: f64) {
        self.cost_basis
            .entry(ticker_id)
            .or_default()
            .record_fill(price, qty, commission);
    }

    /// Get cost-basis entry for a ticker.
    pub fn cost_basis(&self, ticker_id: &TickerId) -> Option<&CostBasisEntry> {
        self.cost_basis.get(ticker_id)
    }

    /// SC-20: Remove cost-basis entry on PositionClosed.
    pub fn clear_cost_basis(&mut self, ticker_id: &TickerId) {
        self.cost_basis.remove(ticker_id);
    }

    /// Nightly clear of all cost-basis entries (before reqPositions resync).
    pub fn clear_all_cost_basis(&mut self) {
        self.cost_basis.clear();
    }

    // ── WP-6: Dividend withholding tax ──

    /// Apply withholding tax to a gross dividend amount.
    /// UK ISA: net = gross × 0.85.
    pub fn net_dividend(&self, gross_dividend: f64) -> f64 {
        gross_dividend * self.dividend_withholding_factor
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{OrderState, TickerId};

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
        }
    }

    #[test]
    fn test_new_portfolio() {
        let p = PortfolioState::new(10_000.0);
        assert_eq!(p.equity, 10_000.0);
        assert_eq!(p.cash, 10_000.0);
        assert_eq!(p.filled_count(), 0);
        assert_eq!(p.total_position_count(), 0);
    }

    #[test]
    fn test_add_remove_position() {
        let mut p = PortfolioState::new(10_000.0);
        p.add_position(make_position(1, 100, 10.0, 9.5));
        assert_eq!(p.filled_count(), 1);
        assert_eq!(p.cash, 9_000.0); // 10000 - 100*10

        let removed = p.remove_position(TickerId(1));
        assert!(removed.is_some());
        assert_eq!(p.filled_count(), 0);
    }

    #[test]
    fn test_position_count_includes_pending() {
        let mut p = PortfolioState::new(10_000.0);
        p.add_position(make_position(1, 100, 10.0, 9.5));
        p.set_pending_count(1);
        assert_eq!(p.total_position_count(), 2);
    }

    #[test]
    fn test_portfolio_heat() {
        let mut p = PortfolioState::new(10_000.0);
        // Position: 100 shares at 10.0, stop at 9.5 → risk = 50
        // Heat = 50 / 10000 * 100 = 0.5%
        p.add_position(make_position(1, 100, 10.0, 9.5));
        let heat = p.portfolio_heat_pct();
        assert!((heat - 0.5).abs() < 0.001);
    }

    #[test]
    fn test_sector_heat() {
        let mut p = PortfolioState::new(10_000.0);
        p.register_sector(TickerId(1), "Semiconductors".into());
        p.register_sector(TickerId(2), "Semiconductors".into());
        p.register_sector(TickerId(3), "Technology".into());
        // Two semiconductor positions worth 3400 total out of 10000
        p.add_position(make_position(1, 100, 20.0, 19.0)); // 2000
        p.add_position(make_position(2, 100, 14.0, 13.0)); // 1400
        // Sector heat for semiconductors = 3400 / 10000 * 100 = 34%
        let heat = p.sector_heat_pct(TickerId(1));
        assert!((heat - 34.0).abs() < 0.001);
    }

    #[test]
    fn test_inverse_blocker() {
        let mut p = PortfolioState::new(10_000.0);
        p.register_inverse_pair(TickerId(1), TickerId(2)); // QQQ3 ↔ QQQS
        p.add_position(make_position(1, 100, 10.0, 9.5));
        assert_eq!(p.inverse_blocker(TickerId(2)), Some(TickerId(1)));
        assert_eq!(p.inverse_blocker(TickerId(1)), None); // 2 not open
    }

    #[test]
    fn test_daily_drawdown() {
        let mut p = PortfolioState::new(10_000.0);
        p.high_water_mark = 10_000.0;
        p.equity = 9_790.0; // 2.1% drawdown
        let dd = p.daily_drawdown_pct();
        assert!((dd - 2.1).abs() < 0.001);
    }

    #[test]
    fn test_cash_buffer() {
        let mut p = PortfolioState::new(10_000.0);
        p.cash = 900.0; // 9% of equity
        let buf = p.cash_buffer_pct();
        assert!((buf - 9.0).abs() < 0.001);
    }

    // ── SC-10: Cost-basis VWAP tracker ──
    #[test]
    fn test_cost_basis_single_fill() {
        let mut p = PortfolioState::new(10_000.0);
        let t = TickerId(1);
        p.record_fill(t, 10.50, 100, 1.50);
        let cb = p.cost_basis(&t).expect("entry exists");
        assert_eq!(cb.total_qty, 100);
        assert!((cb.vwap - 10.50).abs() < 0.001);
        assert!((cb.total_commission - 1.50).abs() < 0.001);
    }

    #[test]
    fn test_cost_basis_multiple_fills_vwap() {
        let mut p = PortfolioState::new(10_000.0);
        let t = TickerId(1);
        // Fill 1: 60 shares at 10.00
        p.record_fill(t, 10.00, 60, 1.00);
        // Fill 2: 40 shares at 11.00
        p.record_fill(t, 11.00, 40, 1.00);
        let cb = p.cost_basis(&t).expect("entry exists");
        assert_eq!(cb.total_qty, 100);
        // VWAP = (600 + 440) / 100 = 10.40
        assert!((cb.vwap - 10.40).abs() < 0.001);
        // Net cost = 1040 + 2 = 1042
        assert!((cb.net_cost_basis() - 1042.0).abs() < 0.001);
    }

    #[test]
    fn test_cost_basis_clear_on_position_close() {
        let mut p = PortfolioState::new(10_000.0);
        let t = TickerId(1);
        p.record_fill(t, 10.50, 100, 1.50);
        assert!(p.cost_basis(&t).is_some());
        p.clear_cost_basis(&t);
        assert!(p.cost_basis(&t).is_none());
    }

    #[test]
    fn test_cost_basis_nightly_clear() {
        let mut p = PortfolioState::new(10_000.0);
        p.record_fill(TickerId(1), 10.0, 100, 1.0);
        p.record_fill(TickerId(2), 20.0, 50, 1.0);
        p.clear_all_cost_basis();
        assert!(p.cost_basis(&TickerId(1)).is_none());
        assert!(p.cost_basis(&TickerId(2)).is_none());
    }

    // ── P0-02: ISA annual counter ──
    #[test]
    fn test_isa_year_invested_tracks_position_cost() {
        let mut p = PortfolioState::new(25_000.0);
        assert!((p.isa_year_invested).abs() < 0.001);
        // Position 1: 100 shares at £150 = £15,000
        p.add_position(make_position(1, 100, 150.0, 140.0));
        assert!((p.isa_year_invested - 15_000.0).abs() < 0.001);
        // Position 2: 50 shares at £120 = £6,000 → total £21,000 > £20,000 limit
        p.add_position(make_position(2, 50, 120.0, 110.0));
        assert!((p.isa_year_invested - 21_000.0).abs() < 0.001);
    }

    // ── WP-6: Dividend withholding tax ──
    #[test]
    fn test_wp6_dividend_withholding_085() {
        let p = PortfolioState::new(10_000.0);
        assert!((p.dividend_withholding_factor - 0.85).abs() < 0.001);
        // £100 gross → £85 net
        let net = p.net_dividend(100.0);
        assert!((net - 85.0).abs() < 0.001);
    }

    #[test]
    fn test_wp6_zero_dividend() {
        let p = PortfolioState::new(10_000.0);
        assert!((p.net_dividend(0.0)).abs() < 0.001);
    }
}
