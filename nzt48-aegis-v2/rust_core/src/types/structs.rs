//! Core #[pyclass] structs for AEGIS V2 data contracts.
//! MarketTick, OrderIntent, RiskDecision + validate_f64 helper.
//! Fields ordered largest-to-smallest for struct packing (H128).

use pyo3::prelude::*;
use std::collections::HashMap;

use super::enums::*;

// ============================================================================
// CORE STRUCTS
// ============================================================================

/// A single market data tick from IBKR.
/// Rust owns these. Python receives Vec<MarketTick> via PyO3.
/// Fields ordered largest-to-smallest for struct packing (H128).
#[derive(Clone, Debug)]
#[pyclass]
pub struct MarketTick {
    /// Unix epoch nanoseconds from IBKR server (H03). NOT wall clock.
    #[pyo3(get)]
    pub timestamp_ns: u64,
    /// Socket-level receive timestamp for T2T latency (H118)
    #[pyo3(get)]
    pub recv_timestamp_ns: u64,
    /// Cumulative volume (resets daily)
    #[pyo3(get)]
    pub volume: u64,
    /// Best bid price
    #[pyo3(get)]
    pub bid: f64,
    /// Best ask price
    #[pyo3(get)]
    pub ask: f64,
    /// Last traded price
    #[pyo3(get)]
    pub last: f64,
    /// Best bid size (lots) — from L1 field 0 or L2 depth. 0 if unavailable.
    #[pyo3(get)]
    pub bid_size: i32,
    /// Best ask size (lots) — from L1 field 3 or L2 depth. 0 if unavailable.
    #[pyo3(get)]
    pub ask_size: i32,
    /// Interned ticker ID (H01). NOT a String.
    #[pyo3(get)]
    pub ticker_id: TickerId,
    // ── Extended tick data (25 fields from reqMktData) ──
    /// Last trade size (LastSize / DelayedLastSize)
    #[pyo3(get)]
    pub last_size: i32,
    /// Daily open price (Open / DelayedOpen)
    #[pyo3(get)]
    pub open: f64,
    /// Previous day close (Close / DelayedClose)
    #[pyo3(get)]
    pub close: f64,
    /// Number of trades today (TradeCount generic tick 293)
    #[pyo3(get)]
    pub trade_count: i32,
    /// Trades per minute (TradeRate generic tick 294)
    #[pyo3(get)]
    pub trade_rate: f64,
    /// Volume per minute (VolumeRate generic tick 295)
    #[pyo3(get)]
    pub volume_rate: f64,
    /// Real-time 30-day historical volatility (generic tick 411)
    #[pyo3(get)]
    pub rt_hist_vol: f64,
    /// Shortable indicator: 0=not, >0.5=available, >2.5=easy to borrow (generic tick 236)
    #[pyo3(get)]
    pub shortable: f64,
    /// Trading halt status (Halted / DelayedHalted)
    #[pyo3(get)]
    pub halted: bool,
    /// Mark price for margining (generic tick 232)
    #[pyo3(get)]
    pub mark_price: f64,
    /// Indicative auction clearing price (generic tick 225)
    #[pyo3(get)]
    pub auction_price: f64,
    /// Shares/contracts offered during auction (generic tick 225)
    #[pyo3(get)]
    pub auction_volume: i32,
    /// Auction buy/sell imbalance (generic tick 225)
    #[pyo3(get)]
    pub auction_imbalance: f64,
    /// ETF NAV close (generic tick 578)
    #[pyo3(get)]
    pub etf_nav_close: f64,
    /// ETF NAV last (generic tick 577)
    #[pyo3(get)]
    pub etf_nav_last: f64,
    /// ETF NAV bid (generic tick 576)
    #[pyo3(get)]
    pub etf_nav_bid: f64,
    /// ETF NAV ask (generic tick 576)
    #[pyo3(get)]
    pub etf_nav_ask: f64,
    /// Call option open interest (generic tick 101)
    #[pyo3(get)]
    pub opt_call_oi: i32,
    /// Put option open interest (generic tick 101)
    #[pyo3(get)]
    pub opt_put_oi: i32,
    /// Call option volume (generic tick 100)
    #[pyo3(get)]
    pub opt_call_vol: i32,
    /// Put option volume (generic tick 100)
    #[pyo3(get)]
    pub opt_put_vol: i32,
    /// Option implied volatility (generic tick 106)
    #[pyo3(get)]
    pub opt_impl_vol: f64,
    /// Option 30-day historical volatility (generic tick 104)
    #[pyo3(get)]
    pub opt_hist_vol: f64,
    /// Average daily volume over 90 days (generic tick 165)
    #[pyo3(get)]
    pub avg_volume: i64,
    // ── AUDIT-FIX: 7 additional tick types ──
    /// 52-week high price (TickType 20)
    #[pyo3(get)]
    pub high_52wk: f64,
    /// 52-week low price (TickType 19)
    #[pyo3(get)]
    pub low_52wk: f64,
    /// Short-term volume 3-minute (TickType 63)
    #[pyo3(get)]
    pub short_term_vol_3min: i64,
    /// Short-term volume 5-minute (TickType 64)
    #[pyo3(get)]
    pub short_term_vol_5min: i64,
    /// Short-term volume 10-minute (TickType 65)
    #[pyo3(get)]
    pub short_term_vol_10min: i64,
    /// Regulatory imbalance (TickType 61)
    #[pyo3(get)]
    pub regulatory_imbalance: f64,
    /// Average option volume (TickType 87)
    #[pyo3(get)]
    pub avg_opt_volume: i64,
    // ── L2 Depth metrics (from reqMktDepth order book) ──
    /// Sum of all bid sizes across 5 depth levels
    #[pyo3(get)]
    pub total_bid_depth: f64,
    /// Sum of all ask sizes across 5 depth levels
    #[pyo3(get)]
    pub total_ask_depth: f64,
    /// (total_bid - total_ask) / (total_bid + total_ask), range [-1, 1]
    #[pyo3(get)]
    pub depth_imbalance: f64,
    /// Price level with the largest bid size (wall detection)
    #[pyo3(get)]
    pub bid_wall_price: f64,
    /// Price level with the largest ask size (wall detection)
    #[pyo3(get)]
    pub ask_wall_price: f64,
    /// Top-of-book spread from depth: asks[0] - bids[0]
    #[pyo3(get)]
    pub spread_depth_1: f64,
    /// Full book spread from depth: asks[4] - bids[4] (outermost levels)
    #[pyo3(get)]
    pub spread_depth_5: f64,
    /// Weighted book pressure: sum(size / distance_from_mid), positive = bullish
    #[pyo3(get)]
    pub book_pressure: f64,
}

#[pymethods]
impl MarketTick {
    #[new]
    #[pyo3(signature = (ticker_id, bid, ask, last, volume, timestamp_ns, recv_timestamp_ns, bid_size=0, ask_size=0,
        last_size=0, open=0.0, close=0.0, trade_count=0, trade_rate=0.0, volume_rate=0.0,
        rt_hist_vol=0.0, shortable=0.0, halted=false, mark_price=0.0, auction_price=0.0,
        auction_volume=0, auction_imbalance=0.0, etf_nav_close=0.0, etf_nav_last=0.0,
        etf_nav_bid=0.0, etf_nav_ask=0.0, opt_call_oi=0, opt_put_oi=0, opt_call_vol=0,
        opt_put_vol=0, opt_impl_vol=0.0, opt_hist_vol=0.0, avg_volume=0,
        high_52wk=0.0, low_52wk=0.0, short_term_vol_3min=0, short_term_vol_5min=0,
        short_term_vol_10min=0, regulatory_imbalance=0.0, avg_opt_volume=0,
        total_bid_depth=0.0, total_ask_depth=0.0, depth_imbalance=0.0,
        bid_wall_price=0.0, ask_wall_price=0.0, spread_depth_1=0.0,
        spread_depth_5=0.0, book_pressure=0.0))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        ticker_id: TickerId,
        bid: f64,
        ask: f64,
        last: f64,
        volume: u64,
        timestamp_ns: u64,
        recv_timestamp_ns: u64,
        bid_size: i32,
        ask_size: i32,
        last_size: i32,
        open: f64,
        close: f64,
        trade_count: i32,
        trade_rate: f64,
        volume_rate: f64,
        rt_hist_vol: f64,
        shortable: f64,
        halted: bool,
        mark_price: f64,
        auction_price: f64,
        auction_volume: i32,
        auction_imbalance: f64,
        etf_nav_close: f64,
        etf_nav_last: f64,
        etf_nav_bid: f64,
        etf_nav_ask: f64,
        opt_call_oi: i32,
        opt_put_oi: i32,
        opt_call_vol: i32,
        opt_put_vol: i32,
        opt_impl_vol: f64,
        opt_hist_vol: f64,
        avg_volume: i64,
        high_52wk: f64,
        low_52wk: f64,
        short_term_vol_3min: i64,
        short_term_vol_5min: i64,
        short_term_vol_10min: i64,
        regulatory_imbalance: f64,
        avg_opt_volume: i64,
        total_bid_depth: f64,
        total_ask_depth: f64,
        depth_imbalance: f64,
        bid_wall_price: f64,
        ask_wall_price: f64,
        spread_depth_1: f64,
        spread_depth_5: f64,
        book_pressure: f64,
    ) -> Self {
        Self {
            timestamp_ns,
            recv_timestamp_ns,
            volume,
            bid,
            ask,
            last,
            bid_size,
            ask_size,
            ticker_id,
            last_size,
            open,
            close,
            trade_count,
            trade_rate,
            volume_rate,
            rt_hist_vol,
            shortable,
            halted,
            mark_price,
            auction_price,
            auction_volume,
            auction_imbalance,
            etf_nav_close,
            etf_nav_last,
            etf_nav_bid,
            etf_nav_ask,
            opt_call_oi,
            opt_put_oi,
            opt_call_vol,
            opt_put_vol,
            opt_impl_vol,
            opt_hist_vol,
            avg_volume,
            high_52wk,
            low_52wk,
            short_term_vol_3min,
            short_term_vol_5min,
            short_term_vol_10min,
            regulatory_imbalance,
            avg_opt_volume,
            total_bid_depth,
            total_ask_depth,
            depth_imbalance,
            bid_wall_price,
            ask_wall_price,
            spread_depth_1,
            spread_depth_5,
            book_pressure,
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "MarketTick(ticker={}, bid={}, ask={}, last={}, vol={})",
            self.ticker_id.0, self.bid, self.ask, self.last, self.volume
        )
    }
}

impl Default for MarketTick {
    fn default() -> Self {
        Self {
            timestamp_ns: 0,
            recv_timestamp_ns: 0,
            volume: 0,
            bid: 0.0,
            ask: 0.0,
            last: 0.0,
            bid_size: 0,
            ask_size: 0,
            ticker_id: TickerId(0),
            last_size: 0,
            open: 0.0,
            close: 0.0,
            trade_count: 0,
            trade_rate: 0.0,
            volume_rate: 0.0,
            rt_hist_vol: 0.0,
            shortable: 0.0,
            halted: false,
            mark_price: 0.0,
            auction_price: 0.0,
            auction_volume: 0,
            auction_imbalance: 0.0,
            etf_nav_close: 0.0,
            etf_nav_last: 0.0,
            etf_nav_bid: 0.0,
            etf_nav_ask: 0.0,
            opt_call_oi: 0,
            opt_put_oi: 0,
            opt_call_vol: 0,
            opt_put_vol: 0,
            opt_impl_vol: 0.0,
            opt_hist_vol: 0.0,
            avg_volume: 0,
            high_52wk: 0.0,
            low_52wk: 0.0,
            short_term_vol_3min: 0,
            short_term_vol_5min: 0,
            short_term_vol_10min: 0,
            regulatory_imbalance: 0.0,
            avg_opt_volume: 0,
            total_bid_depth: 0.0,
            total_ask_depth: 0.0,
            depth_imbalance: 0.0,
            bid_wall_price: 0.0,
            ask_wall_price: 0.0,
            spread_depth_1: 0.0,
            spread_depth_5: 0.0,
            book_pressure: 0.0,
        }
    }
}

impl MarketTick {
    /// Validate tick data is sane (not NaN/Inf, non-negative, bid < ask).
    #[inline(always)]
    pub fn is_valid(&self) -> bool {
        let f = |v: f64| v.is_finite() && v >= 0.0;
        f(self.bid) && f(self.ask) && f(self.last) && f(self.volume as f64)
            && (self.bid == 0.0 || self.ask == 0.0 || self.ask >= self.bid)
            && self.last >= 0.0
    }
}

/// Validate that an f64 is not NaN or Infinite (H09).
/// Returns Err with a descriptive message if invalid.
pub fn validate_f64(value: f64, field_name: &str) -> Result<f64, String> {
    if value.is_nan() {
        return Err(format!("NaN detected in field '{field_name}'"));
    }
    if value.is_infinite() {
        return Err(format!("Infinity detected in field '{field_name}'"));
    }
    Ok(value)
}

/// Generated by Python Brain. Crosses PyO3 back to Rust.
/// Python SUGGESTS. Rust DECIDES. Python has no gun (Non-Negotiable #2).
#[derive(Clone, Debug)]
#[pyclass]
pub struct OrderIntent {
    /// Signal confidence [0.0, 100.0]. Floor is 65.
    #[pyo3(get)]
    pub confidence: f64,
    /// Kelly fraction output from 12-factor sizing [0.0, 0.20]
    #[pyo3(get)]
    pub kelly_fraction: f64,
    /// Interned ticker ID
    #[pyo3(get)]
    pub ticker_id: TickerId,
    /// Long only in ISA (Short exists for type completeness but always rejected)
    #[pyo3(get)]
    pub side: Direction,
    /// Which strategy generated this intent
    #[pyo3(get)]
    pub strategy: StrategyId,
    /// Strategy-specific features for logging/Ouroboros analysis
    pub features: HashMap<String, f64>,
}

#[pymethods]
impl OrderIntent {
    #[new]
    #[pyo3(signature = (ticker_id, side, confidence, strategy, kelly_fraction, features=None))]
    fn new(
        ticker_id: TickerId,
        side: Direction,
        confidence: f64,
        strategy: StrategyId,
        kelly_fraction: f64,
        features: Option<HashMap<String, f64>>,
    ) -> PyResult<Self> {
        // NaN sanitization on every f64 from Python (H09)
        let confidence = validate_f64(confidence, "confidence")
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        let kelly_fraction = validate_f64(kelly_fraction, "kelly_fraction")
            .map_err(pyo3::exceptions::PyValueError::new_err)?;

        // Clamp confidence to [0.0, 100.0]
        let confidence = confidence.clamp(0.0, 100.0);
        // Clamp kelly_fraction to [0.0, 0.20] (H57)
        let kelly_fraction = kelly_fraction.clamp(0.0, 0.20);

        Ok(Self {
            confidence,
            kelly_fraction,
            ticker_id,
            side,
            strategy,
            features: features.unwrap_or_default(),
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "OrderIntent(ticker={}, side={:?}, conf={:.1}, kelly={:.4})",
            self.ticker_id.0, self.side, self.confidence, self.kelly_fraction
        )
    }

    /// Get features as a Python dict
    #[getter]
    fn features(&self) -> HashMap<String, f64> {
        self.features.clone()
    }
}

/// The RiskArbiter's verdict on an OrderIntent.
#[derive(Clone, Debug)]
#[pyclass]
pub struct RiskDecision {
    /// Timestamp of decision (IBKR-adjusted clock)
    #[pyo3(get)]
    pub decision_timestamp_ns: u64,
    /// Adjusted position size (may be reduced in REDUCE regime)
    #[pyo3(get)]
    pub adjusted_size: f64,
    /// true = proceed to WAL + broker; false = rejected
    #[pyo3(get)]
    pub approved: bool,
    /// Current Risk Arbiter regime at decision time
    #[pyo3(get)]
    pub regime: RiskRegime,
    /// Specific reason for veto (or VetoReason::Approved)
    pub reason: VetoReason,
}

#[pymethods]
impl RiskDecision {
    #[new]
    #[pyo3(signature = (approved, adjusted_size, regime, decision_timestamp_ns))]
    fn new(
        approved: bool,
        adjusted_size: f64,
        regime: RiskRegime,
        decision_timestamp_ns: u64,
    ) -> Self {
        let reason = if approved {
            VetoReason::Approved
        } else {
            VetoReason::MaxPositionsReached // Default; real code sets specific reason
        };
        Self {
            decision_timestamp_ns,
            adjusted_size,
            approved,
            regime,
            reason,
        }
    }

    /// Get the veto reason as a Python-visible object
    #[getter]
    fn reason(&self) -> PyVetoReason {
        PyVetoReason::from(&self.reason)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_market_tick_field_packing() {
        let tick = MarketTick {
            timestamp_ns: 1_000_000_000,
            recv_timestamp_ns: 1_000_000_100,
            volume: 50000,
            bid: 10.50,
            ask: 10.52,
            last: 10.51,
            ticker_id: TickerId(42),
            ..Default::default()
        };
        assert_eq!(tick.ticker_id, TickerId(42));
        assert_eq!(tick.bid, 10.50);
    }

    #[test]
    fn test_validate_f64_nan() {
        assert!(validate_f64(f64::NAN, "test").is_err());
    }

    #[test]
    fn test_validate_f64_infinity() {
        assert!(validate_f64(f64::INFINITY, "test").is_err());
        assert!(validate_f64(f64::NEG_INFINITY, "test").is_err());
    }

    #[test]
    fn test_validate_f64_valid() {
        assert!(validate_f64(42.0, "test").is_ok());
        assert!(validate_f64(0.0, "test").is_ok());
        assert!(validate_f64(-1.5, "test").is_ok());
    }
}
