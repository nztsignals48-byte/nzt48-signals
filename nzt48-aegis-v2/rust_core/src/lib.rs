//! AEGIS V2 — Rust Core
//! Institutional-grade leveraged ETP trading engine.
//! This crate exposes all data contract types to Python via PyO3.
#![deny(clippy::unwrap_used)]
#![deny(warnings)]

pub mod broker;
pub mod broker_resilience;
#[cfg(test)]
mod broker_tests;
#[cfg(test)]
mod broker_tests_ext;
pub mod channel;
pub mod clock;
pub mod config;
pub mod config_loader;
pub mod crucible;
pub mod cross_asset_macro;
pub mod cross_timezone;
pub mod currency;
pub mod dqn_signal_weighting;
pub mod engine;
pub mod asian_session;
pub mod european_session;
pub mod exchange_profile;
pub mod garch_evt;
pub mod garch_inference;
#[cfg(test)]
mod engine_tests;
pub mod exit_engine;
#[cfg(test)]
mod exit_engine_tests;
pub mod ffi;
pub mod hardening;
pub mod hayashi_yoshida;
pub mod ibkr_broker;
pub mod isa_gate;
pub mod liquidation_defense;
pub mod log_thompson_sampler;
pub mod market_config;
pub mod market_scheduler;
pub mod multiframe_vol;
pub mod neural_hawkes;
pub mod overnight_carry;
pub mod ouroboros_loader;
pub mod paper_broker;
#[cfg(test)]
mod pipeline_tests;
pub mod portfolio;
pub mod position_sizer;
pub mod quote_imbalance;
pub mod python_bridge;
pub mod python_subprocess_manager;
#[cfg(test)]
mod proptest_risk;
pub mod reconciler;
pub mod replay;
pub mod scanner;
pub mod sector_rotation;
pub mod smart_router;
pub mod split_handler;
pub mod subscription_manager;
#[cfg(test)]
mod replay_tests;
pub mod risk_arbiter;
#[cfg(test)]
mod risk_arbiter_tests;
pub mod student_t_kalman;
pub mod telemetry;
pub mod types;
pub mod universe;
#[cfg(test)]
mod universe_tests;
pub mod latency_profiler;
pub mod live_readiness;
pub mod predictive_scoring;
pub mod session_manager;
pub mod state_checkpoint;
// pub mod strategy_config; // REMOVED — unused module, nothing references it
pub mod wal_actor;
pub mod wal_compressor;
pub mod wal_replay;
#[cfg(test)]
mod wal_tests;
pub mod wal_writer;
#[cfg(feature = "quantum_apex")]
pub mod quantum_apex;
#[cfg(test)]
mod phase6_tests;
pub mod regime_detector;
pub mod entry_engine;
