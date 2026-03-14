//! P19: State Hash Checkpointing (H85).
//! Every hour, write a state hash (portfolio + positions + regime + FX rates) to WAL.
//! On startup, verify last checkpoint matches replayed state.

use std::collections::BTreeMap;

/// A snapshot of engine state for checkpointing.
#[derive(Clone, Debug)]
pub struct StateCheckpoint {
    /// Epoch timestamp in nanoseconds.
    pub timestamp_ns: u64,
    /// Current equity in GBP.
    pub equity_gbp: f64,
    /// Number of open positions.
    pub position_count: u32,
    /// Risk regime as string.
    pub regime: String,
    /// Per-position hashes: ticker_id → (qty, entry_price).
    pub positions: BTreeMap<u32, (i64, f64)>,
    /// Computed hash of the checkpoint.
    pub hash: u64,
}

impl StateCheckpoint {
    /// Create a new checkpoint and compute its hash.
    pub fn new(
        timestamp_ns: u64,
        equity_gbp: f64,
        position_count: u32,
        regime: &str,
        positions: BTreeMap<u32, (i64, f64)>,
    ) -> Self {
        let mut cp = Self {
            timestamp_ns,
            equity_gbp,
            position_count,
            regime: regime.to_string(),
            positions,
            hash: 0,
        };
        cp.hash = cp.compute_hash();
        cp
    }

    /// Compute a deterministic hash of the checkpoint state.
    /// Uses FNV-1a for simplicity (no external crate needed).
    fn compute_hash(&self) -> u64 {
        let mut hash: u64 = 0xcbf29ce484222325; // FNV offset basis
        let prime: u64 = 0x100000001b3;

        // Hash equity (as bits).
        for byte in self.equity_gbp.to_le_bytes() {
            hash ^= byte as u64;
            hash = hash.wrapping_mul(prime);
        }

        // Hash position count.
        for byte in self.position_count.to_le_bytes() {
            hash ^= byte as u64;
            hash = hash.wrapping_mul(prime);
        }

        // Hash regime.
        for byte in self.regime.as_bytes() {
            hash ^= *byte as u64;
            hash = hash.wrapping_mul(prime);
        }

        // Hash positions in deterministic order (BTreeMap is sorted).
        for (&tid, &(qty, price)) in &self.positions {
            for byte in tid.to_le_bytes() {
                hash ^= byte as u64;
                hash = hash.wrapping_mul(prime);
            }
            for byte in qty.to_le_bytes() {
                hash ^= byte as u64;
                hash = hash.wrapping_mul(prime);
            }
            for byte in price.to_le_bytes() {
                hash ^= byte as u64;
                hash = hash.wrapping_mul(prime);
            }
        }

        hash
    }
}

/// Manages periodic state checkpointing.
pub struct CheckpointManager {
    /// Interval between checkpoints in nanoseconds.
    interval_ns: u64,
    /// Timestamp of last checkpoint.
    last_checkpoint_ns: u64,
    /// The last checkpoint for startup verification.
    last_checkpoint: Option<StateCheckpoint>,
}

impl CheckpointManager {
    /// Create a new manager with the given interval (default 1 hour).
    pub fn new(interval_secs: u64) -> Self {
        Self {
            interval_ns: interval_secs * 1_000_000_000,
            last_checkpoint_ns: 0,
            last_checkpoint: None,
        }
    }

    /// Check if it's time for a new checkpoint.
    pub fn needs_checkpoint(&self, now_ns: u64) -> bool {
        now_ns >= self.last_checkpoint_ns + self.interval_ns
    }

    /// Record a checkpoint.
    pub fn record_checkpoint(&mut self, checkpoint: StateCheckpoint) {
        self.last_checkpoint_ns = checkpoint.timestamp_ns;
        self.last_checkpoint = Some(checkpoint);
    }

    /// Get the last checkpoint hash for WAL writing.
    pub fn last_hash(&self) -> Option<u64> {
        self.last_checkpoint.as_ref().map(|cp| cp.hash)
    }

    /// Verify a replayed state matches the stored checkpoint.
    /// Returns Ok if match or no stored checkpoint, Err with details if mismatch.
    pub fn verify_against_replay(
        &self,
        replayed: &StateCheckpoint,
    ) -> Result<(), CheckpointDivergence> {
        let stored = match &self.last_checkpoint {
            Some(cp) => cp,
            None => return Ok(()), // No checkpoint to verify against
        };

        if stored.hash != replayed.hash {
            return Err(CheckpointDivergence {
                stored_hash: stored.hash,
                replayed_hash: replayed.hash,
                stored_equity: stored.equity_gbp,
                replayed_equity: replayed.equity_gbp,
                stored_positions: stored.position_count,
                replayed_positions: replayed.position_count,
            });
        }

        Ok(())
    }

    /// Get the last checkpoint for inspection.
    pub fn last_checkpoint(&self) -> Option<&StateCheckpoint> {
        self.last_checkpoint.as_ref()
    }
}

impl Default for CheckpointManager {
    fn default() -> Self {
        Self::new(3600) // 1 hour default
    }
}

/// Details of a checkpoint divergence.
#[derive(Debug)]
pub struct CheckpointDivergence {
    pub stored_hash: u64,
    pub replayed_hash: u64,
    pub stored_equity: f64,
    pub replayed_equity: f64,
    pub stored_positions: u32,
    pub replayed_positions: u32,
}

impl std::fmt::Display for CheckpointDivergence {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "Checkpoint divergence: stored_hash={:#x} vs replayed_hash={:#x}, equity={:.2} vs {:.2}, positions={} vs {}",
            self.stored_hash, self.replayed_hash,
            self.stored_equity, self.replayed_equity,
            self.stored_positions, self.replayed_positions,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_checkpoint_hash_deterministic() {
        let positions = BTreeMap::from([(1, (100i64, 5.50)), (2, (200, 10.25))]);
        let cp1 = StateCheckpoint::new(1000, 10000.0, 2, "Normal", positions.clone());
        let cp2 = StateCheckpoint::new(1000, 10000.0, 2, "Normal", positions);
        assert_eq!(cp1.hash, cp2.hash);
    }

    #[test]
    fn test_checkpoint_hash_changes_with_equity() {
        let positions = BTreeMap::new();
        let cp1 = StateCheckpoint::new(1000, 10000.0, 0, "Normal", positions.clone());
        let cp2 = StateCheckpoint::new(1000, 10001.0, 0, "Normal", positions);
        assert_ne!(cp1.hash, cp2.hash);
    }

    #[test]
    fn test_checkpoint_hash_changes_with_regime() {
        let positions = BTreeMap::new();
        let cp1 = StateCheckpoint::new(1000, 10000.0, 0, "Normal", positions.clone());
        let cp2 = StateCheckpoint::new(1000, 10000.0, 0, "Halt", positions);
        assert_ne!(cp1.hash, cp2.hash);
    }

    #[test]
    fn test_needs_checkpoint_timing() {
        let mgr = CheckpointManager::new(3600); // 1 hour
        // Initial state: last_checkpoint_ns=0, needs checkpoint after interval
        assert!(mgr.needs_checkpoint(3601_000_000_000));
        assert!(!mgr.needs_checkpoint(100_000_000)); // 0.1s — too soon

        // After recording at t=1s, need to wait interval from that point
        let mut mgr2 = CheckpointManager::new(3600);
        let cp = StateCheckpoint::new(1_000_000_000, 10000.0, 0, "Normal", BTreeMap::new());
        mgr2.record_checkpoint(cp);
        assert!(!mgr2.needs_checkpoint(2_000_000_000)); // 2 seconds later — too soon
        assert!(mgr2.needs_checkpoint(3602_000_000_000)); // 1 hour + 1 second from checkpoint
    }

    #[test]
    fn test_verify_matching_checkpoint() {
        let mut mgr = CheckpointManager::new(3600);
        let positions = BTreeMap::from([(1, (100i64, 5.50))]);
        let cp = StateCheckpoint::new(1000, 10000.0, 1, "Normal", positions.clone());
        mgr.record_checkpoint(cp);

        let replayed = StateCheckpoint::new(1000, 10000.0, 1, "Normal", positions);
        assert!(mgr.verify_against_replay(&replayed).is_ok());
    }

    #[test]
    fn test_verify_divergent_checkpoint() {
        let mut mgr = CheckpointManager::new(3600);
        let cp = StateCheckpoint::new(1000, 10000.0, 1, "Normal", BTreeMap::from([(1, (100i64, 5.50))]));
        mgr.record_checkpoint(cp);

        let replayed = StateCheckpoint::new(1000, 9999.0, 1, "Normal", BTreeMap::from([(1, (100i64, 5.50))]));
        assert!(mgr.verify_against_replay(&replayed).is_err());
    }

    #[test]
    fn test_no_checkpoint_always_ok() {
        let mgr = CheckpointManager::new(3600);
        let replayed = StateCheckpoint::new(1000, 10000.0, 0, "Normal", BTreeMap::new());
        assert!(mgr.verify_against_replay(&replayed).is_ok());
    }

    #[test]
    fn test_last_hash() {
        let mut mgr = CheckpointManager::new(3600);
        assert!(mgr.last_hash().is_none());
        let cp = StateCheckpoint::new(1000, 10000.0, 0, "Normal", BTreeMap::new());
        let expected_hash = cp.hash;
        mgr.record_checkpoint(cp);
        assert_eq!(mgr.last_hash(), Some(expected_hash));
    }
}
