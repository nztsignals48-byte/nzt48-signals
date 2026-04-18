// Risk Arbiter — 16 weighted checks, NO hard gates (except 8-consec-losses halt).
// Each check emits a continuous confidence_delta in [-50, +5].

use std::collections::BTreeMap;

#[derive(Debug, Clone, Default)]
pub struct RiskEvaluation {
    pub deltas: BTreeMap<String, f64>,
    pub final_confidence: f64,
    pub halt: bool,
}

pub struct RiskArbiter { pub consec_losses: u32 }

impl RiskArbiter {
    pub fn new() -> Self { Self { consec_losses: 0 } }

    pub fn evaluate(&self) -> RiskEvaluation {
        // Phase 4 fills all 16 checks. Scaffold returns neutral.
        let mut deltas = BTreeMap::new();
        for name in [
            "spread", "liquidity", "correlation", "drawdown",
            "vol_regime", "heat", "concentration", "overnight",
            "shortable", "halted", "cost_alpha", "book_pressure",
            "vwap_chase", "imbalance", "vol_surge", "kalman_spike",
        ] {
            deltas.insert(name.to_string(), 0.0);
        }
        RiskEvaluation {
            deltas,
            final_confidence: 0.60,
            halt: self.consec_losses >= 8,
        }
    }
}
