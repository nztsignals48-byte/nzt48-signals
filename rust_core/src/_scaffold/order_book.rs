// Order book — 5-level bid/ask, order lifecycle: pending/partial/filled/cancelled/rejected.

#[derive(Debug, Clone, Default)]
pub struct OrderBook {
    pub bid_prices: [f64; 5],
    pub bid_sizes:  [u64; 5],
    pub ask_prices: [f64; 5],
    pub ask_sizes:  [u64; 5],
}

impl OrderBook {
    pub fn imbalance(&self) -> f64 {
        let b = self.bid_sizes.iter().sum::<u64>() as f64;
        let a = self.ask_sizes.iter().sum::<u64>() as f64;
        if a + b == 0.0 { 0.0 } else { (b - a) / (a + b) }
    }
}
