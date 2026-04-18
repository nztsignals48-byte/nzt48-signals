// 10 indicators: RSI, ATR, VWAP, IBS, ADX, Hurst, EMA, BB, Keltner, MACD.

#[derive(Debug, Clone, Default)]
pub struct Indicators {
    pub rsi: f64,
    pub atr: f64,
    pub vwap_distance_bps: f64,
    pub ibs: f64,
    pub adx: f64,
    pub hurst: f64,
    pub ema_fast: f64,
    pub ema_slow: f64,
    pub bb_width: f64,
    pub keltner_width: f64,
    pub macd_hist: f64,
    pub rvol: f64,
    pub session_high: f64,
    pub session_low: f64,
}

pub struct IndicatorStore { pub current: Indicators }

impl IndicatorStore {
    pub fn new() -> Self { Self { current: Indicators::default() } }
    pub fn update(&mut self) { /* Phase 2A fills */ }
}
