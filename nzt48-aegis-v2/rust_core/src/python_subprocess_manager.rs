//! Python Subprocess Manager — exponential backoff + fork bomb prevention.
//! RM-5: Prevents infinite respawn loops when Python crashes with exit(255).
//!
//! Logic:
//!   - Track last N exit times in a sliding window
//!   - If >= 3 crashes in 60 seconds: trigger SystemHalt (engine stops)
//!   - Otherwise: exponential backoff (1s → 2s → 4s → 8s → ... → 60s cap)
//!   - On successful run (long-lived): reset backoff to 1s

use std::collections::VecDeque;
use std::time::{Duration, Instant};

/// Maximum backoff duration (cap).
const MAX_BACKOFF: Duration = Duration::from_secs(60);
/// Initial backoff duration.
const INITIAL_BACKOFF: Duration = Duration::from_secs(1);
/// Window for fork bomb detection.
const CRASH_WINDOW: Duration = Duration::from_secs(60);
/// Crash count that triggers SystemHalt.
const FORK_BOMB_THRESHOLD: usize = 3;
/// Exit code indicating Python requested clean restart.
const CLEAN_RESTART_EXIT_CODE: i32 = 255;
/// Minimum runtime before we consider a run "successful" (reset backoff).
const MIN_HEALTHY_RUNTIME: Duration = Duration::from_secs(30);

/// Result of evaluating a Python subprocess exit.
#[derive(Clone, Debug, PartialEq)]
pub enum RespawnDecision {
    /// Respawn after the given delay.
    RespawnAfter(Duration),
    /// Too many crashes — trigger SystemHalt.
    SystemHalt { crashes_in_window: usize },
    /// Fatal exit code (not 255) — do not respawn.
    Fatal { exit_code: Option<i32> },
}

/// Manages Python subprocess lifecycle with backoff + fork bomb detection.
pub struct PythonSubprocessManager {
    /// Recent exit timestamps for fork bomb detection.
    recent_exits: VecDeque<Instant>,
    /// Current backoff duration.
    backoff: Duration,
    /// When the current subprocess was started (for healthy runtime detection).
    started_at: Option<Instant>,
}

impl PythonSubprocessManager {
    pub fn new() -> Self {
        Self {
            recent_exits: VecDeque::with_capacity(FORK_BOMB_THRESHOLD + 1),
            backoff: INITIAL_BACKOFF,
            started_at: None,
        }
    }

    /// Record that the subprocess has started.
    pub fn mark_started(&mut self) {
        self.started_at = Some(Instant::now());
    }

    /// Evaluate a subprocess exit and decide what to do.
    pub fn evaluate_exit(&mut self, exit_code: Option<i32>) -> RespawnDecision {
        let now = Instant::now();

        match exit_code {
            Some(CLEAN_RESTART_EXIT_CODE) => {
                // Clean restart requested by Python
                self.record_exit(now);

                // Check for fork bomb pattern
                let crashes = self.count_recent_exits(now);
                if crashes >= FORK_BOMB_THRESHOLD {
                    return RespawnDecision::SystemHalt {
                        crashes_in_window: crashes,
                    };
                }

                // Check if the process ran long enough to be "healthy"
                if let Some(started) = self.started_at
                    && now.duration_since(started) >= MIN_HEALTHY_RUNTIME {
                        self.backoff = INITIAL_BACKOFF; // Reset backoff
                    }

                let delay = self.backoff;
                // Escalate backoff for next time
                self.backoff = (self.backoff * 2).min(MAX_BACKOFF);

                RespawnDecision::RespawnAfter(delay)
            }
            _ => {
                // Any other exit code is fatal — do not respawn
                RespawnDecision::Fatal { exit_code }
            }
        }
    }

    /// Record an exit timestamp.
    fn record_exit(&mut self, now: Instant) {
        self.recent_exits.push_back(now);
        // Keep only recent exits (trim old ones)
        while self.recent_exits.len() > FORK_BOMB_THRESHOLD + 2 {
            self.recent_exits.pop_front();
        }
    }

    /// Count exits within the crash detection window.
    fn count_recent_exits(&self, now: Instant) -> usize {
        self.recent_exits
            .iter()
            .filter(|&&t| now.duration_since(t) <= CRASH_WINDOW)
            .count()
    }

    /// Current backoff duration.
    pub fn current_backoff(&self) -> Duration {
        self.backoff
    }

    /// Reset the manager to initial state.
    pub fn reset(&mut self) {
        self.recent_exits.clear();
        self.backoff = INITIAL_BACKOFF;
        self.started_at = None;
    }
}

impl Default for PythonSubprocessManager {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_subprocess_fork_bomb_prevention() {
        // AT-RM5: Backoff escalates, SystemHalt after 3 crashes in 60s
        let mut mgr = PythonSubprocessManager::new();

        // First crash: respawn with 1s backoff
        mgr.mark_started();
        let decision = mgr.evaluate_exit(Some(255));
        assert_eq!(decision, RespawnDecision::RespawnAfter(Duration::from_secs(1)));

        // Second crash: respawn with 2s backoff
        mgr.mark_started();
        let decision = mgr.evaluate_exit(Some(255));
        assert_eq!(decision, RespawnDecision::RespawnAfter(Duration::from_secs(2)));

        // Third crash: FORK BOMB DETECTED → SystemHalt
        mgr.mark_started();
        let decision = mgr.evaluate_exit(Some(255));
        assert_eq!(
            decision,
            RespawnDecision::SystemHalt {
                crashes_in_window: 3
            }
        );
    }

    #[test]
    fn test_backoff_escalation() {
        let mut mgr = PythonSubprocessManager::new();

        let expected = [1, 2, 4]; // Would be 8, 16, 32, 60 but fork bomb triggers first
        for &secs in &expected[..2] {
            mgr.mark_started();
            let decision = mgr.evaluate_exit(Some(255));
            assert_eq!(
                decision,
                RespawnDecision::RespawnAfter(Duration::from_secs(secs))
            );
        }
    }

    #[test]
    fn test_backoff_cap_at_60s() {
        let mut mgr = PythonSubprocessManager::new();
        // Override recent_exits to avoid fork bomb (simulate old exits)
        mgr.backoff = Duration::from_secs(32);

        mgr.mark_started();
        let decision = mgr.evaluate_exit(Some(255));
        assert_eq!(decision, RespawnDecision::RespawnAfter(Duration::from_secs(32)));

        // Next would be 64 but capped at 60
        assert_eq!(mgr.current_backoff(), Duration::from_secs(60));
    }

    #[test]
    fn test_fatal_exit_code() {
        let mut mgr = PythonSubprocessManager::new();

        // Exit code 1 (non-255) → fatal, do not respawn
        let decision = mgr.evaluate_exit(Some(1));
        assert_eq!(decision, RespawnDecision::Fatal { exit_code: Some(1) });

        // No exit code (killed by signal) → fatal
        let decision = mgr.evaluate_exit(None);
        assert_eq!(decision, RespawnDecision::Fatal { exit_code: None });
    }

    #[test]
    fn test_healthy_run_resets_backoff() {
        let mut mgr = PythonSubprocessManager::new();

        // First crash — backoff goes to 2s
        mgr.mark_started();
        mgr.evaluate_exit(Some(255));
        assert_eq!(mgr.current_backoff(), Duration::from_secs(2));

        // Simulate a healthy run (started > 30s ago)
        mgr.started_at = Some(Instant::now() - Duration::from_secs(60));
        mgr.recent_exits.clear(); // Clear to avoid fork bomb

        let decision = mgr.evaluate_exit(Some(255));
        // Should have reset backoff to 1s (healthy run), then escalate to 2s
        assert_eq!(decision, RespawnDecision::RespawnAfter(INITIAL_BACKOFF));
    }

    #[test]
    fn test_reset() {
        let mut mgr = PythonSubprocessManager::new();

        mgr.mark_started();
        mgr.evaluate_exit(Some(255));
        mgr.evaluate_exit(Some(255));

        mgr.reset();
        assert_eq!(mgr.current_backoff(), INITIAL_BACKOFF);
    }

    #[test]
    fn test_default_impl() {
        let mgr = PythonSubprocessManager::default();
        assert_eq!(mgr.current_backoff(), INITIAL_BACKOFF);
    }
}
