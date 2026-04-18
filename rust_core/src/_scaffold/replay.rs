// Deterministic replay harness — reads WAL, re-executes strategies with seeded RNG.

pub fn replay(_wal_path: &str, _strategy: &str) -> anyhow::Result<()> {
    // Phase 9 fills. Used by stress-window test (2020-03 / 2024-08) in Phase 12.
    Ok(())
}
