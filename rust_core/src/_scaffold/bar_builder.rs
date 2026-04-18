// Multi-timeframe bar builder. 5 TFs: 100ms, 1m, 5m, 15m, 1h.
use crate::types::MarketTick;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Timeframe { Ms100, Min1, Min5, Min15, Hour1 }

#[derive(Debug, Clone, Default)]
pub struct Bar {
    pub tf: Option<Timeframe>,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: u64,
    pub vwap: f64,
    pub start_ns: u64,
    pub end_ns: u64,
}

#[derive(Default)]
pub struct BarStore { pub last: Vec<Bar> }

impl BarStore {
    pub fn on_tick(&mut self, _t: &MarketTick) {
        // Phase 2A fills TF aggregation.
    }
}
