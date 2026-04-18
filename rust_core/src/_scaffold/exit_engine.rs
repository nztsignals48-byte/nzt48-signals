// Exit engine — 5 methods: Chandelier, FixedDay, EventWindow, NextOpen, ProfitTarget.
// Chandelier is the default alpha exit; rungs from defaults.toml.

#[derive(Debug, Clone, Copy)]
pub enum ExitMethod { Chandelier, FixedDay, EventWindow, NextOpen, ProfitTarget }

#[derive(Debug, Clone)]
pub struct ExitDecision { pub flatten: bool, pub reason: &'static str }

pub fn evaluate(_method: ExitMethod) -> ExitDecision {
    // Phase 3 fills.
    ExitDecision { flatten: false, reason: "scaffold" }
}
