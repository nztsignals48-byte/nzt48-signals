//! P21: Multi-Session Regime Transitions.
//! 5-mode TradingMode enum: DARK, MODE_A (Asian), MODE_B (European), AUCTION, CARRY.
//! Automatic regime escalation based on mode transitions.

/// Trading session mode — determines what the engine is doing at any moment.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SessionMode {
    /// No trading — outside all market hours.
    Dark,
    /// Asian session scanning (TSE, HKEX, ASX).
    ModeA,
    /// European session trading (LSE, XETRA, Euronext) 08:00-14:30.
    ModeB,
    /// US overlap mode (14:30-16:30 UTC) — 80 LSE + 20 US lines.
    ModeBPlus,
    /// Opening or closing auction period.
    Auction,
    /// Overnight carry management (positions held overnight).
    Carry,
}

impl std::fmt::Display for SessionMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SessionMode::Dark => write!(f, "DARK"),
            SessionMode::ModeA => write!(f, "MODE_A"),
            SessionMode::ModeB => write!(f, "MODE_B"),
            SessionMode::ModeBPlus => write!(f, "MODE_B_PLUS"),
            SessionMode::Auction => write!(f, "AUCTION"),
            SessionMode::Carry => write!(f, "CARRY"),
        }
    }
}

/// Transition event from one mode to another.
#[derive(Clone, Debug)]
pub struct ModeTransition {
    pub from: SessionMode,
    pub to: SessionMode,
    pub timestamp_ns: u64,
    /// Whether this transition should freeze new entries.
    pub freeze_entries: bool,
    /// Whether this transition should trigger carry checks.
    pub trigger_carry_check: bool,
}

/// Manages session mode transitions and their effects on the engine.
pub struct SessionManager {
    current_mode: SessionMode,
    last_transition_ns: u64,
    /// History of recent transitions (bounded to 50).
    history: Vec<ModeTransition>,
}

impl SessionManager {
    pub fn new() -> Self {
        Self {
            current_mode: SessionMode::Dark,
            last_transition_ns: 0,
            history: Vec::new(),
        }
    }

    /// Get the current session mode.
    pub fn mode(&self) -> SessionMode {
        self.current_mode
    }

    /// Determine the correct mode based on London time (seconds from midnight).
    pub fn compute_mode(london_time_secs: u32, has_open_positions: bool) -> SessionMode {
        const AUCTION_OPEN_START: u32 = 7 * 3600 + 50 * 60;  // 07:50
        const AUCTION_OPEN_END: u32 = 8 * 3600;      // 08:00
        const MODE_B_PLUS_START: u32 = 14 * 3600 + 30 * 60;  // 14:30 (US opens)
        const MODE_B_PLUS_END: u32 = 16 * 3600 + 30 * 60;    // 16:30 (LSE closes)
        const AUCTION_CLOSE_END: u32 = 16 * 3600 + 35 * 60;  // 16:35
        const OUROBOROS_START: u32 = 23 * 3600 + 45 * 60;    // 23:45

        // Ouroboros maintenance window: 23:45-00:00 is always Dark (highest priority).
        if london_time_secs >= OUROBOROS_START {
            return SessionMode::Dark;
        }

        // Asian session: 23:00-23:45 and 00:00-07:50 London (wraps midnight).
        // Condition: s >= 82800 (23:00) && s < 85740 (23:45) || s < 28200 (07:50)
        const ASIA_START: u32 = 23 * 3600;           // 23:00
        if london_time_secs >= ASIA_START || london_time_secs < AUCTION_OPEN_START {
            if has_open_positions {
                return SessionMode::Carry;
            }
            return SessionMode::ModeA;
        }

        // LSE opening auction: 07:50-08:00.
        if london_time_secs < AUCTION_OPEN_END {
            return SessionMode::Auction;
        }

        // European continuous trading: 08:00-14:30.
        if london_time_secs < MODE_B_PLUS_START {
            return SessionMode::ModeB;
        }

        // US overlap: 14:30-16:30 UTC (80 LSE + 20 US lines).
        if london_time_secs < MODE_B_PLUS_END {
            return SessionMode::ModeBPlus;
        }

        // LSE closing auction: 16:30-16:35.
        if london_time_secs < AUCTION_CLOSE_END {
            return SessionMode::Auction;
        }

        // Post-close carry: 16:35-23:45 (Ouroboros runs at 23:50).
        if has_open_positions {
            return SessionMode::Carry;
        }
        SessionMode::Dark
    }

    /// Update mode and return any transition that occurred.
    pub fn update(
        &mut self,
        london_time_secs: u32,
        has_open_positions: bool,
        now_ns: u64,
    ) -> Option<ModeTransition> {
        let new_mode = Self::compute_mode(london_time_secs, has_open_positions);
        if new_mode == self.current_mode {
            return None;
        }

        let transition = ModeTransition {
            from: self.current_mode,
            to: new_mode,
            timestamp_ns: now_ns,
            freeze_entries: Self::should_freeze_entries(self.current_mode, new_mode),
            trigger_carry_check: Self::should_trigger_carry(self.current_mode, new_mode),
        };

        self.current_mode = new_mode;
        self.last_transition_ns = now_ns;
        self.history.push(transition.clone());
        if self.history.len() > 50 {
            self.history.remove(0);
        }

        Some(transition)
    }

    /// Whether entries should be frozen during this transition.
    fn should_freeze_entries(from: SessionMode, to: SessionMode) -> bool {
        matches!(
            (from, to),
            (SessionMode::ModeB, SessionMode::ModeBPlus)
                | (SessionMode::ModeB, SessionMode::Auction)
                | (SessionMode::ModeB, SessionMode::Carry)
                | (SessionMode::ModeB, SessionMode::Dark)
                | (SessionMode::ModeBPlus, SessionMode::Auction)
                | (SessionMode::ModeBPlus, SessionMode::Carry)
                | (SessionMode::ModeBPlus, SessionMode::Dark)
                | (SessionMode::Auction, SessionMode::Carry)
                | (SessionMode::Auction, SessionMode::Dark)
        )
    }

    /// Whether carry checks should be triggered.
    fn should_trigger_carry(from: SessionMode, to: SessionMode) -> bool {
        matches!(to, SessionMode::Carry)
            || matches!(
                (from, to),
                (SessionMode::Carry, SessionMode::ModeA)
                    | (SessionMode::Carry, SessionMode::ModeB)
                    | (SessionMode::Carry, SessionMode::ModeBPlus)
            )
    }

    /// Whether new entries are allowed in the current mode.
    pub fn entries_allowed(&self) -> bool {
        matches!(self.current_mode, SessionMode::ModeB | SessionMode::ModeBPlus)
    }

    /// Whether scanning is active in the current mode.
    pub fn scanning_active(&self) -> bool {
        matches!(
            self.current_mode,
            SessionMode::ModeA | SessionMode::ModeB
        )
    }

    /// Get transition history.
    pub fn history(&self) -> &[ModeTransition] {
        &self.history
    }
}

impl Default for SessionManager {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mode_computation_dark() {
        assert_eq!(SessionManager::compute_mode(23 * 3600 + 50 * 60, false), SessionMode::Dark);
    }

    #[test]
    fn test_mode_computation_mode_a() {
        assert_eq!(SessionManager::compute_mode(3 * 3600, false), SessionMode::ModeA);
    }

    #[test]
    fn test_mode_computation_carry_overnight() {
        assert_eq!(SessionManager::compute_mode(3 * 3600, true), SessionMode::Carry);
    }

    #[test]
    fn test_mode_computation_auction_open() {
        assert_eq!(SessionManager::compute_mode(7 * 3600 + 55 * 60, false), SessionMode::Auction);
    }

    #[test]
    fn test_mode_computation_mode_b() {
        assert_eq!(SessionManager::compute_mode(12 * 3600, false), SessionMode::ModeB);
    }

    #[test]
    fn test_mode_computation_auction_close() {
        assert_eq!(SessionManager::compute_mode(16 * 3600 + 32 * 60, false), SessionMode::Auction);
    }

    #[test]
    fn test_transition_generates_event() {
        let mut mgr = SessionManager::new();
        // Dark → ModeA
        let trans = mgr.update(3 * 3600, false, 1000);
        assert!(trans.is_some());
        let t = trans.expect("transition exists");
        assert_eq!(t.from, SessionMode::Dark);
        assert_eq!(t.to, SessionMode::ModeA);
    }

    #[test]
    fn test_no_transition_same_mode() {
        let mut mgr = SessionManager::new();
        // Dark → Dark (late night, no positions)
        let trans = mgr.update(23 * 3600 + 55 * 60, false, 1000);
        assert!(trans.is_none()); // Already Dark
    }

    #[test]
    fn test_entries_only_in_mode_b() {
        let mut mgr = SessionManager::new();
        mgr.update(12 * 3600, false, 1000); // ModeB
        assert!(mgr.entries_allowed());
        mgr.update(3 * 3600, false, 2000); // ModeA
        assert!(!mgr.entries_allowed());
    }

    #[test]
    fn test_freeze_on_close() {
        let freeze = SessionManager::should_freeze_entries(SessionMode::ModeB, SessionMode::Auction);
        assert!(freeze);
    }

    #[test]
    fn test_carry_trigger() {
        let carry = SessionManager::should_trigger_carry(SessionMode::ModeB, SessionMode::Carry);
        assert!(carry);
    }
}
