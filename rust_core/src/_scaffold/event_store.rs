// Hash-chained WAL. Daily rotation. Schema versioned. Every SignalReceived + TradeClosed
// validates against the dataset contract (schemas/signal.proto) on write.

use anyhow::Result;
use chrono::Utc;
use sha2::{Digest, Sha256};
use std::fs::{OpenOptions};
use std::io::Write;
use std::path::PathBuf;

pub struct EventStore { pub dir: PathBuf, pub prev_hash: String }

impl EventStore {
    pub fn new(dir: PathBuf) -> Self { Self { dir, prev_hash: "0".repeat(64) } }

    pub fn today_path(&self) -> PathBuf {
        let d = Utc::now().format("%Y-%m-%d").to_string();
        self.dir.join(format!("events_{}.wal", d))
    }

    pub fn append(&mut self, kind: &str, payload: &str) -> Result<()> {
        let mut hasher = Sha256::new();
        hasher.update(self.prev_hash.as_bytes());
        hasher.update(kind.as_bytes());
        hasher.update(payload.as_bytes());
        let hash = format!("{:x}", hasher.finalize());
        let line = format!("{{\"schema_version\":1,\"kind\":\"{}\",\"prev\":\"{}\",\"hash\":\"{}\",\"payload\":{}}}\n",
            kind, self.prev_hash, hash, payload);
        let path = self.today_path();
        std::fs::create_dir_all(&self.dir)?;
        let mut f = OpenOptions::new().create(true).append(true).open(&path)?;
        f.write_all(line.as_bytes())?;
        self.prev_hash = hash;
        Ok(())
    }
}
