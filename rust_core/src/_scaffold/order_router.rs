// 4-tier IBKR algo routing: Urgent, Patient, PegMid, ArrivalPrice.
// KILL is always MARKET.

#[derive(Debug, Clone, Copy)]
pub enum AlgoTier { Urgent, Patient, PegMid, ArrivalPrice, Market }

pub fn pick_tier(_strategy: &str, _is_stop: bool, _adv_pct: f64) -> AlgoTier {
    // Phase 3 fills.
    AlgoTier::Patient
}
