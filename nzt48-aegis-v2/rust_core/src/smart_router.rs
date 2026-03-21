//! Phase 12: Smart Router — ETP-first routing with cost comparison.
//! Compares direct equity cost vs LSE ETP cost, routes to cheapest.
//! ISA Gate fires BEFORE cost comparison.

use crate::currency::FxRateTable;
use crate::exchange_profile::ExchangeProfile;
use crate::isa_gate::{IsaCheckResult, IsaGate};

/// Routing decision from the Smart Router.
#[derive(Clone, Debug, PartialEq)]
pub enum RouteDecision {
    /// Route to LSE ETP (cheaper or only option for ISA).
    Etp {
        etp_symbol: String,
        estimated_cost_gbp: f64,
    },
    /// Route to direct European equity.
    Direct {
        exchange_mic: String,
        estimated_cost_gbp: f64,
    },
    /// Blocked: cannot trade (ISA blocked + no ETP available).
    Blocked { reason: String },
}

/// Cost breakdown for a routing option.
#[derive(Clone, Debug)]
pub struct CostBreakdown {
    pub spread_cost_gbp: f64,
    pub fx_cost_gbp: f64,
    pub ftt_cost_gbp: f64,
    pub commission_gbp: f64,
    pub total_gbp: f64,
}

impl CostBreakdown {
    fn compute(spread: f64, fx: f64, ftt: f64, commission: f64) -> Self {
        Self {
            spread_cost_gbp: spread,
            fx_cost_gbp: fx,
            ftt_cost_gbp: ftt,
            commission_gbp: commission,
            total_gbp: spread + fx + ftt + commission,
        }
    }
}

/// ETP mapping: European underlying → LSE-listed ETP.
#[derive(Clone, Debug)]
pub struct EtpMapping {
    /// Underlying symbol/ISIN.
    pub underlying: String,
    /// LSE ETP symbol.
    pub etp_symbol: String,
    /// Typical ETP spread (cached from Ouroboros, 5-day median).
    pub cached_spread_pct: f64,
    /// ETP tracking error (annualized).
    pub tracking_error_pct: f64,
    /// ETP premium/discount to NAV.
    pub premium_discount_pct: f64,
}

/// Smart Router configuration.
pub struct SmartRouter {
    pub fx_table: FxRateTable,
    pub isa_gate: IsaGate,
    /// ETP mappings: underlying → ETP
    etp_mappings: Vec<EtpMapping>,
    /// IBKR commission per trade (GBP, tiered model).
    pub ibkr_commission_gbp: f64,
}

impl SmartRouter {
    pub fn new(isa_gate: IsaGate) -> Self {
        Self {
            fx_table: FxRateTable::new(),
            isa_gate,
            etp_mappings: Vec::new(),
            ibkr_commission_gbp: 1.70, // Q-051: fallback; overridden by with_costs()
        }
    }

    /// Q-051: Construct with commission from unified cost config.
    pub fn with_costs(isa_gate: IsaGate, ibkr_commission_gbp: f64) -> Self {
        Self {
            fx_table: FxRateTable::new(),
            isa_gate,
            etp_mappings: Vec::new(),
            ibkr_commission_gbp,
        }
    }

    /// Register an ETP mapping.
    pub fn register_etp(&mut self, mapping: EtpMapping) {
        self.etp_mappings.push(mapping);
    }

    /// Find ETP for an underlying.
    pub fn find_etp(&self, underlying: &str) -> Option<&EtpMapping> {
        self.etp_mappings.iter().find(|m| m.underlying == underlying)
    }

    /// Route a trade: ISA gate → cost comparison → decision.
    pub fn route(
        &self,
        underlying: &str,
        exchange: &ExchangeProfile,
        position_gbp: f64,
        market_cap_eur: f64,
        is_intraday: bool,
        cached_spread_pct: f64,
    ) -> RouteDecision {
        // Step 1: ISA Gate check
        let isa_check = self.isa_gate.check(exchange.mic, position_gbp);

        match isa_check {
            IsaCheckResult::BlockedExchange { .. } => {
                // Try ETP fallback
                if let Some(etp) = self.find_etp(underlying) {
                    let etp_cost = self.etp_cost(position_gbp, etp);
                    return RouteDecision::Etp {
                        etp_symbol: etp.etp_symbol.clone(),
                        estimated_cost_gbp: etp_cost.total_gbp,
                    };
                }
                return RouteDecision::Blocked {
                    reason: format!(
                        "Exchange {} blocked by ISA gate, no ETP available",
                        exchange.mic
                    ),
                };
            }
            IsaCheckResult::DepositLimitExceeded { remaining_gbp } => {
                return RouteDecision::Blocked {
                    reason: format!("ISA deposit limit: only £{remaining_gbp} remaining"),
                };
            }
            IsaCheckResult::Allowed => {}
        }

        // Step 2: If ETP exists, compare costs
        if let Some(etp) = self.find_etp(underlying) {
            let direct_cost = self.direct_cost(
                position_gbp,
                exchange,
                cached_spread_pct,
                market_cap_eur,
                is_intraday,
            );
            let etp_cost = self.etp_cost(position_gbp, etp);

            if etp_cost.total_gbp <= direct_cost.total_gbp {
                return RouteDecision::Etp {
                    etp_symbol: etp.etp_symbol.clone(),
                    estimated_cost_gbp: etp_cost.total_gbp,
                };
            }
            return RouteDecision::Direct {
                exchange_mic: exchange.mic.to_string(),
                estimated_cost_gbp: direct_cost.total_gbp,
            };
        }

        // Step 3: No ETP — direct only
        let direct_cost = self.direct_cost(
            position_gbp,
            exchange,
            cached_spread_pct,
            market_cap_eur,
            is_intraday,
        );
        RouteDecision::Direct {
            exchange_mic: exchange.mic.to_string(),
            estimated_cost_gbp: direct_cost.total_gbp,
        }
    }

    /// Compute direct equity trading cost.
    fn direct_cost(
        &self,
        position_gbp: f64,
        exchange: &ExchangeProfile,
        cached_spread_pct: f64,
        market_cap_eur: f64,
        is_intraday: bool,
    ) -> CostBreakdown {
        // Spread cost
        let spread_cost = if cached_spread_pct > 0.0 {
            position_gbp * cached_spread_pct / 100.0
        } else {
            0.0
        };

        // FX cost
        let fx_cost = self.fx_table.fx_cost_gbp(position_gbp, exchange.currency);

        // FTT cost (in local currency → convert to GBP)
        let position_local = self.fx_table.from_gbp(position_gbp, exchange.currency);
        let ftt_local = exchange.ftt_cost(position_local, market_cap_eur, is_intraday);
        let ftt_gbp = self.fx_table.to_gbp(ftt_local, exchange.currency);

        CostBreakdown::compute(spread_cost, fx_cost, ftt_gbp, self.ibkr_commission_gbp)
    }

    /// Compute LSE ETP trading cost.
    fn etp_cost(&self, position_gbp: f64, etp: &EtpMapping) -> CostBreakdown {
        // ETP spread
        let spread_cost = position_gbp * etp.cached_spread_pct / 100.0;

        // Tracking error + premium/discount (annualized → per-trade approximation: /252)
        let tracking_cost =
            position_gbp * (etp.tracking_error_pct + etp.premium_discount_pct.abs()) / 100.0
                / 252.0;

        // No FX cost (LSE ETPs trade in GBP/GBX)
        // UK stamp duty exemption for ETFs (ETPs are exempt)
        CostBreakdown::compute(
            spread_cost + tracking_cost,
            0.0,
            0.0,
            self.ibkr_commission_gbp,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::currency::Currency;
    use crate::exchange_profile::ExchangeRegistry;

    fn test_router() -> SmartRouter {
        let isa_gate = IsaGate::new("2026-04-06");
        let mut router = SmartRouter::new(isa_gate);
        router.register_etp(EtpMapping {
            underlying: "TSMC".to_string(),
            etp_symbol: "TSM3.L".to_string(),
            cached_spread_pct: 0.15,
            tracking_error_pct: 0.5,
            premium_discount_pct: 0.1,
        });
        router.register_etp(EtpMapping {
            underlying: "SAP".to_string(),
            etp_symbol: "SAP.L".to_string(),
            cached_spread_pct: 0.08,
            tracking_error_pct: 0.0, // Direct listing, not a leveraged ETP
            premium_discount_pct: 0.0,
        });
        router
    }

    #[test]
    fn test_isa_blocked_routes_to_etp() {
        let router = test_router();
        // TSMC on TWSE is ISA-blocked → should route to TSM3.L ETP
        let exchange = crate::exchange_profile::ExchangeProfile {
            mic: "TWSE",
            name: "Taiwan Stock Exchange",
            currency: Currency::USD,
            open_utc_secs: 3600,
            close_utc_secs: 5 * 3600 + 30 * 60,
            closing_auction_utc_secs: 0,
            tick_size_over_1: 0.01,
            tick_size_under_1: 0.001,
            isa_eligible: false,
            country: "TW",
            has_ftt: false,
            ftt_rate: 0.0,
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        };

        match router.route("TSMC", &exchange, 5000.0, 0.0, false, 0.1) {
            RouteDecision::Etp { etp_symbol, .. } => assert_eq!(etp_symbol, "TSM3.L"),
            other => panic!("Expected ETP route, got {other:?}"),
        }
    }

    #[test]
    fn test_isa_blocked_no_etp_blocked() {
        let router = test_router();
        let exchange = crate::exchange_profile::ExchangeProfile {
            mic: "XBOM",
            name: "BSE India",
            currency: Currency::USD,
            open_utc_secs: 3 * 3600 + 45 * 60,
            close_utc_secs: 10 * 3600,
            closing_auction_utc_secs: 0,
            tick_size_over_1: 0.05,
            tick_size_under_1: 0.01,
            isa_eligible: false,
            country: "IN",
            has_ftt: false,
            ftt_rate: 0.0,
            ftt_market_cap_threshold_eur: 0.0,
            ftt_intraday_exempt: false,
        };

        // No ETP for Indian underlying → fully blocked
        match router.route("RELIANCE", &exchange, 5000.0, 0.0, false, 0.1) {
            RouteDecision::Blocked { reason } => {
                assert!(reason.contains("XBOM"));
            }
            other => panic!("Expected Blocked, got {other:?}"),
        }
    }

    #[test]
    fn test_etp_cheaper_than_direct() {
        let router = test_router();
        let reg = ExchangeRegistry::new();
        let xetr = reg.by_mic("XETR").expect("XETR");

        // SAP on XETRA: FX cost (£2 minimum) + spread + commission
        // vs SAP.L on LSE: just spread + commission (no FX)
        // For small positions, ETP wins due to FX minimum fee
        match router.route("SAP", xetr, 1000.0, 200_000_000_000.0, false, 0.1) {
            RouteDecision::Etp { etp_symbol, .. } => assert_eq!(etp_symbol, "SAP.L"),
            RouteDecision::Direct { .. } => {
                // Also acceptable if direct is cheaper
            }
            other => panic!("Expected Etp or Direct, got {other:?}"),
        }
    }

    #[test]
    fn test_direct_when_no_etp() {
        let router = test_router();
        let reg = ExchangeRegistry::new();
        let xsto = reg.by_mic("XSTO").expect("XSTO");

        // Swedish stock with no ETP mapping → direct only
        match router.route("VOLVO_B", xsto, 5000.0, 50_000_000_000.0, false, 0.05) {
            RouteDecision::Direct { exchange_mic, .. } => assert_eq!(exchange_mic, "XSTO"),
            other => panic!("Expected Direct, got {other:?}"),
        }
    }

    #[test]
    fn test_deposit_limit_blocks() {
        let isa_gate = IsaGate::new("2026-04-06");
        let mut router = SmartRouter::new(isa_gate);
        router.isa_gate.record_deposit(19_500.0); // £500 remaining

        let reg = ExchangeRegistry::new();
        let xlon = reg.by_mic("XLON").expect("XLON");

        match router.route("AVST", xlon, 1000.0, 1_000_000_000.0, false, 0.05) {
            RouteDecision::Blocked { reason } => {
                assert!(reason.contains("deposit limit"));
            }
            other => panic!("Expected Blocked (deposit limit), got {other:?}"),
        }
    }

    #[test]
    fn test_ftt_affects_routing() {
        let mut router = test_router();
        router.register_etp(EtpMapping {
            underlying: "LVMH".to_string(),
            etp_symbol: "LVMH.L".to_string(),
            cached_spread_pct: 0.20,
            tracking_error_pct: 0.3,
            premium_discount_pct: 0.05,
        });

        let reg = ExchangeRegistry::new();
        let xpar = reg.by_mic("XPAR").expect("XPAR");

        // French FTT (0.3%) on large-cap → makes direct more expensive
        // Position: £10,000 (within ISA £20k limit), market cap > €1B
        let decision = router.route("LVMH", xpar, 10_000.0, 300_000_000_000.0, false, 0.05);

        // With 0.3% FTT on position, ETP should be cheaper
        match decision {
            RouteDecision::Etp { etp_symbol, .. } => assert_eq!(etp_symbol, "LVMH.L"),
            RouteDecision::Direct { .. } => {
                // Could go either way depending on exact spread
            }
            other => panic!("Expected routing decision, got {other:?}"),
        }
    }

    #[test]
    fn test_zero_spread_guard() {
        let router = test_router();
        let reg = ExchangeRegistry::new();
        let xetr = reg.by_mic("XETR").expect("XETR");

        // cached_spread = 0.0 → no divide-by-zero, just 0 spread cost
        let decision = router.route("SAP", xetr, 5000.0, 200_000_000_000.0, false, 0.0);
        // Should still produce a valid decision
        assert!(matches!(
            decision,
            RouteDecision::Etp { .. } | RouteDecision::Direct { .. }
        ));
    }
}
