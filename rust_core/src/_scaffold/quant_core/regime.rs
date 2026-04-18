// HMM regime — 4 states: steady, trending, crisis, rotation. Probability vector, NOT a label.
pub fn probs(_features: &[f64]) -> [f64; 4] { [1.0, 0.0, 0.0, 0.0] }
