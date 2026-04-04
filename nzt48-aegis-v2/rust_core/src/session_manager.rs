//! P21: Unified Session Manager.
//! Simplified 3-mode SessionMode: DARK, ACTIVE, CARRY.
//! ACTIVE runs 22 hours/day watching all 6 markets simultaneously.
//! Dark = 21:00-23:00 London (maintenance window).
//! Carry = overnight positions during Dark hours.
//!
//! Market closures are handled naturally — tickers from closed markets
//! score lower in Ouroboros and rotate out on the next 15-min refresh.

/// Trading session mode — unified 3-state architecture.
/// All 36+ strategies run in Active mode across 6 markets simultaneously.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SessionMode {
    /// No trading — maintenance window (21:00-23:00 London).
    /// Ouroboros nightly pipeline runs here.
    Dark,
    /// Unified active mode — all 6 markets monitored simultaneously.
    /// Runs 22 hours/day: 23:00-21:00 London.
    /// 100 tickers (IBKR max), refreshed every 15 min by Ouroboros.
    /// 36+ strategies: momentum, mean-reversion, macro, pairs, options.
    Active,
    /// Overnight carry management (positions held during Dark hours).
    Carry,
}

impl std::fmt::Display for SessionMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SessionMode::Dark => write!(f, "DARK"),
            SessionMode::Active => write!(f, "ACTIVE"),
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
    /// Unified architecture: ACTIVE from 23:00-21:00 London (22 hours).
    /// DARK from 21:00-23:00 London (2 hours maintenance + Ouroboros nightly).
    /// CARRY if positions are held during DARK hours.
    pub fn compute_mode(london_time_secs: u32, has_open_positions: bool) -> SessionMode {
        const ACTIVE_START: u32 = 23 * 3600;  // 23:00 London — markets begin (Asia)
        const ACTIVE_END: u32 = 21 * 3600;    // 21:00 London — US closes

        // Active hours: 23:00-23:59 and 00:00-21:00 London (wraps midnight)
        if london_time_secs >= ACTIVE_START || london_time_secs < ACTIVE_END {
            return SessionMode::Active;
        }

        // Dark hours: 21:00-23:00 London. Carry if positions, else Dark.
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
    fn should_freeze_entries(_from: SessionMode, to: SessionMode) -> bool {
        // Freeze entries when transitioning TO Dark or Carry
        matches!(to, SessionMode::Dark | SessionMode::Carry)
    }

    /// Whether carry checks should be triggered.
    fn should_trigger_carry(_from: SessionMode, to: SessionMode) -> bool {
        matches!(to, SessionMode::Carry)
    }

    /// Whether new entries are allowed in the current mode.
    pub fn entries_allowed(&self) -> bool {
        matches!(self.current_mode, SessionMode::Active)
    }

    /// Whether scanning is active in the current mode.
    pub fn scanning_active(&self) -> bool {
        matches!(self.current_mode, SessionMode::Active)
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
        // 21:00-23:00 London is Dark (no positions)
        assert_eq!(SessionManager::compute_mode(21 * 3600, false), SessionMode::Dark);
        assert_eq!(SessionManager::compute_mode(21 * 3600 + 30 * 60, false), SessionMode::Dark);
        assert_eq!(SessionManager::compute_mode(22 * 3600, false), SessionMode::Dark);
        assert_eq!(SessionManager::compute_mode(22 * 3600 + 59 * 60, false), SessionMode::Dark);
    }

    #[test]
    fn test_mode_computation_active_all_hours() {
        // Unified ACTIVE mode: 23:00-21:00 London (22 hours)
        // Asian hours
        assert_eq!(SessionManager::compute_mode(23 * 3600, false), SessionMode::Active);
        assert_eq!(SessionManager::compute_mode(23 * 3600 + 30 * 60, false), SessionMode::Active);
        assert_eq!(SessionManager::compute_mode(3 * 3600, false), SessionMode::Active);
        // European hours
        assert_eq!(SessionManager::compute_mode(8 * 3600, false), SessionMode::Active);
        assert_eq!(SessionManager::compute_mode(12 * 3600, false), SessionMode::Active);
        // US overlap
        assert_eq!(SessionManager::compute_mode(14 * 3600 + 30 * 60, false), SessionMode::Active);
        // US-only
        assert_eq!(SessionManager::compute_mode(18 * 3600, false), SessionMode::Active);
        assert_eq!(SessionManager::compute_mode(20 * 3600 + 59 * 60, false), SessionMode::Active);
        // Midnight
        assert_eq!(SessionManager::compute_mode(0, false), SessionMode::Active);
    }

    #[test]
    fn test_mode_computation_active_with_positions() {
        // Active mode stays Active even with positions (unlike old Carry during Asian)
        assert_eq!(SessionManager::compute_mode(3 * 3600, true), SessionMode::Active);
        assert_eq!(SessionManager::compute_mode(12 * 3600, true), SessionMode::Active);
    }

    #[test]
    fn test_mode_computation_carry_during_dark() {
        // 21:00-23:00 with positions → Carry
        assert_eq!(SessionManager::compute_mode(21 * 3600 + 30 * 60, true), SessionMode::Carry);
        assert_eq!(SessionManager::compute_mode(22 * 3600, true), SessionMode::Carry);
    }

    #[test]
    fn test_transition_generates_event() {
        let mut mgr = SessionManager::new();
        // Dark → Active at 23:00
        let trans = mgr.update(23 * 3600, false, 1000);
        assert!(trans.is_some());
        let t = trans.expect("transition exists");
        assert_eq!(t.from, SessionMode::Dark);
        assert_eq!(t.to, SessionMode::Active);
    }

    #[test]
    fn test_no_transition_same_mode() {
        let mut mgr = SessionManager::new();
        // Dark → Dark during maintenance window
        let trans = mgr.update(21 * 3600 + 30 * 60, false, 1000);
        assert!(trans.is_none()); // Already Dark
    }

    #[test]
    fn test_entries_allowed_active() {
        let mut mgr = SessionManager::new();
        mgr.update(12 * 3600, false, 1000); // Active
        assert!(mgr.entries_allowed());
    }

    #[test]
    fn test_entries_not_allowed_dark() {
        let mgr = SessionManager::new(); // Starts Dark
        assert!(!mgr.entries_allowed());
    }

    #[test]
    fn test_freeze_to_dark() {
        let freeze = SessionManager::should_freeze_entries(SessionMode::Active, SessionMode::Dark);
        assert!(freeze);
    }

    #[test]
    fn test_carry_trigger() {
        let carry = SessionManager::should_trigger_carry(SessionMode::Active, SessionMode::Carry);
        assert!(carry);
    }

    #[test]
    fn test_22_hours_coverage() {
        // Verify exactly 22 hours are Active and 2 hours are Dark
        let mut active_minutes = 0u32;
        let mut dark_minutes = 0u32;
        for minute in 0..(24 * 60) {
            let secs = minute * 60;
            match SessionManager::compute_mode(secs, false) {
                SessionMode::Active => active_minutes += 1,
                SessionMode::Dark => dark_minutes += 1,
                _ => {
                    // Unexpected mode — treat as Dark (exits allowed, entries blocked)
                    eprintln!(
                        "ERROR: Unexpected trading mode at minute {} — defaulting to Dark (exits allowed, entries blocked)",
                        minute
                    );
                    dark_minutes += 1;
                }
            }
        }
        assert_eq!(active_minutes, 22 * 60); // 22 hours active
        assert_eq!(dark_minutes, 2 * 60);    // 2 hours dark
    }
}
