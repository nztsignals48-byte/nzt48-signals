//! Prometheus metrics export via `metrics` + `metrics-exporter-prometheus`.
//!
//! Exposes HTTP :9090/metrics for Grafana scraping.
//! Key metrics: tick_loop_duration, signals_received, fills, drawdown.

use metrics::{counter, gauge, histogram};
use metrics_exporter_prometheus::PrometheusBuilder;
use tracing::{error, info};

/// Initialize the Prometheus exporter.
/// Binds HTTP on 0.0.0.0:port for Grafana/Prometheus to scrape.
#[tracing::instrument]
pub fn init_metrics(port: u16) {
    match PrometheusBuilder::new()
        .with_http_listener(([0, 0, 0, 0], port))
        .install()
    {
        Ok(()) => info!(port, "prometheus metrics exporter started"),
        Err(e) => error!(error = %e, "failed to start prometheus exporter"),
    }
}

// ---------------------------------------------------------------------------
// Metric recording helpers
// ---------------------------------------------------------------------------

/// Record the duration of one tick loop iteration.
pub fn record_tick_loop_duration(duration_us: f64) {
    histogram!("aegis_tick_loop_duration_us").record(duration_us);
}

/// Increment the counter of ticks received.
pub fn inc_ticks_received(exchange: &str) {
    counter!("aegis_ticks_received_total", "exchange" => exchange.to_string()).increment(1);
}

/// Increment the counter of bars completed.
pub fn inc_bars_completed(timeframe: &str) {
    counter!("aegis_bars_completed_total", "timeframe" => timeframe.to_string()).increment(1);
}

/// Increment the counter of signals received from Python brain.
pub fn inc_signals_received() {
    counter!("aegis_signals_received_total").increment(1);
}

/// Increment the counter of orders filled.
pub fn inc_fills() {
    counter!("aegis_fills_total").increment(1);
}

/// Set the current drawdown percentage gauge.
pub fn set_drawdown(pct: f64) {
    gauge!("aegis_drawdown_pct").set(pct);
}

/// Set the current portfolio equity gauge.
pub fn set_equity(total: f64) {
    gauge!("aegis_equity_total_gbp").set(total);
}

/// Set the current number of open positions.
pub fn set_open_positions(count: f64) {
    gauge!("aegis_open_positions").set(count);
}

/// Set the current portfolio heat percentage.
pub fn set_heat(pct: f64) {
    gauge!("aegis_heat_pct").set(pct);
}

/// Record the number of active IBKR market data subscriptions.
pub fn set_mktdata_subscriptions(count: f64) {
    gauge!("aegis_mktdata_subscriptions").set(count);
}

/// Increment risk arbiter rejections.
pub fn inc_risk_rejections() {
    counter!("aegis_risk_rejections_total").increment(1);
}

/// Set the data drought counter (consecutive polls with zero ticks).
pub fn set_data_drought(polls: f64) {
    gauge!("aegis_data_drought_polls").set(polls);
}
