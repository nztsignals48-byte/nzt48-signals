//! Session 15: broker_router scaffold.
//!
//! Centralizes routing decisions between IBKR (`Account::Isa` / `Account::Gia`)
//! and IG spread betting (`Account::Ig`). Engine used to call `ibkr_broker`
//! directly; routing through this module lets us add IG without touching the
//! engine's signal loop.
//!
//! This module is intentionally minimal — real `ig_broker.rs` (OAuth session,
//! Lightstreamer price feed, order endpoints) is not yet implemented. For now
//! IG signals are rejected at route time with a structured reason so the
//! engine + risk_arbiter can log them cleanly without crashing.
//!
//! When `ig_broker.rs` lands, add a branch in `BrokerRouter::route()` that
//! dispatches to it. Existing IBKR branches stay as-is.

use crate::types::{Account, BrainSignal};

/// Broker route decision.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Route {
    /// Send this signal to IBKR (ISA or GIA). The existing ibkr_broker path
    /// handles everything.
    Ibkr,
    /// Send this signal to IG spread betting. Not yet implemented — see
    /// `RouteBlock::IgNotImplemented`.
    Ig,
    /// Signal cannot be routed. Contains a reason string for logs.
    Block(RouteBlock),
}

/// Reasons a signal cannot be routed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RouteBlock {
    /// Account is Ig but the IG broker is not yet wired. Intentional no-op
    /// path — the engine should log and drop the signal.
    IgNotImplemented,
    /// IG-routed signal but `guaranteed_stop` missing — required by IG risk
    /// rules (guaranteed stops protect against weekend gaps).
    IgMissingGuaranteedStop,
    /// IG-routed signal but `per_point_gbp` missing — required to size the
    /// spread bet.
    IgMissingPerPoint,
    /// Session 17 P2: IG broker not attached to the engine (no credentials
    /// in env / not initialized). Distinct from `IgNotImplemented` which
    /// meant "no code yet" — this means "code exists but operator hasn't
    /// wired up IG credentials for this run".
    IgBrokerNotConfigured,
    /// Session 17 P2: ticker has no entry in `config/ig_epics.toml`.
    IgEpicNotFound,
}

impl RouteBlock {
    pub fn as_str(&self) -> &'static str {
        match self {
            RouteBlock::IgNotImplemented => "ig_not_implemented",
            RouteBlock::IgMissingGuaranteedStop => "ig_missing_guaranteed_stop",
            RouteBlock::IgMissingPerPoint => "ig_missing_per_point",
            RouteBlock::IgBrokerNotConfigured => "ig_broker_not_configured",
            RouteBlock::IgEpicNotFound => "ig_epic_not_found",
        }
    }
}

/// Route a single brain signal to a broker.
///
/// Pure function — no side effects. Signal shape is validated here; the
/// engine chooses whether to actually dispatch to an IG broker based on
/// whether credentials + epic table are attached.
pub fn route(signal: &BrainSignal) -> Route {
    match signal.account {
        Account::Isa | Account::Gia => Route::Ibkr,
        Account::Ig => {
            if signal.guaranteed_stop.is_none() {
                return Route::Block(RouteBlock::IgMissingGuaranteedStop);
            }
            if signal.per_point_gbp.is_none() {
                return Route::Block(RouteBlock::IgMissingPerPoint);
            }
            // Caller (engine) now decides between:
            //   - Route::Ig → dispatch via IgBroker
            //   - Block(IgBrokerNotConfigured) → IG broker not attached
            //   - Block(IgEpicNotFound) → ticker not in ig_epics.toml
            Route::Ig
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{Direction, ExecutionTier, ExitMethod};

    fn sample_signal(account: Account) -> BrainSignal {
        BrainSignal {
            signal_id: "s1".into(),
            view_id: "v1".into(),
            strategy: "test".into(),
            category: "test".into(),
            ticker: "AAPL".into(),
            exchange: "NASDAQ".into(),
            direction: Direction::Long,
            conviction: 70,
            magnitude_pct: 1.0,
            thesis: "".into(),
            risk_note: "".into(),
            target_size_gbp: 500.0,
            stop_distance_atr: 2.5,
            reference_price: 100.0,
            account,
            leverage_embedded: 1.0,
            exit_method: ExitMethod::Chandelier { atr_mult: 3.0, rungs: vec![] },
            execution_tier: ExecutionTier::Market,
            instrument_rationale: "".into(),
            same_catalyst_group: None,
            timestamp_us: 0,
            guaranteed_stop: None,
            per_point_gbp: None,
        }
    }

    #[test]
    fn isa_routes_to_ibkr() {
        let s = sample_signal(Account::Isa);
        assert_eq!(route(&s), Route::Ibkr);
    }

    #[test]
    fn gia_routes_to_ibkr() {
        let s = sample_signal(Account::Gia);
        assert_eq!(route(&s), Route::Ibkr);
    }

    #[test]
    fn ig_missing_guaranteed_stop_blocks() {
        let s = sample_signal(Account::Ig);
        assert_eq!(route(&s), Route::Block(RouteBlock::IgMissingGuaranteedStop));
    }

    #[test]
    fn ig_missing_per_point_blocks() {
        let mut s = sample_signal(Account::Ig);
        s.guaranteed_stop = Some(95.0);
        assert_eq!(route(&s), Route::Block(RouteBlock::IgMissingPerPoint));
    }

    #[test]
    fn ig_fully_specified_routes_to_ig() {
        // Session 17 P2: with guaranteed_stop + per_point_gbp set, router
        // returns Route::Ig. The engine layer decides whether an actual
        // IgBroker is attached (else Block::IgBrokerNotConfigured at call site).
        let mut s = sample_signal(Account::Ig);
        s.guaranteed_stop = Some(95.0);
        s.per_point_gbp = Some(2.0);
        assert_eq!(route(&s), Route::Ig);
    }

    #[test]
    fn block_variants_have_unique_as_str() {
        // Guard against anyone adding a variant and forgetting as_str().
        let strs = [
            RouteBlock::IgNotImplemented.as_str(),
            RouteBlock::IgMissingGuaranteedStop.as_str(),
            RouteBlock::IgMissingPerPoint.as_str(),
            RouteBlock::IgBrokerNotConfigured.as_str(),
            RouteBlock::IgEpicNotFound.as_str(),
        ];
        let mut sorted: Vec<&&str> = strs.iter().collect();
        sorted.sort();
        sorted.dedup();
        assert_eq!(sorted.len(), strs.len(), "duplicate RouteBlock as_str");
    }
}
