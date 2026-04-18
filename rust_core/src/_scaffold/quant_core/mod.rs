// Quant core — Phase 2B filling.
pub mod garch;
pub mod garch_evt;
pub mod kalman;
pub mod regime;
pub mod hayashi_yoshida;

#[derive(Debug, Clone, Default)]
pub struct QuantState {
    pub garch_vol_annualized: f64,
    pub evt_cvar_95: f64,
    pub kalman_residual: f64,
    pub regime_probs: [f64; 4],   // steady, trending, crisis, rotation
    pub hy_correlation_to_spy: f64,
}
