//! Sector Rotation Intelligence (Phase 11)
//!
//! Maps ISA tickers to sectors and tracks per-sector exposure
//! to prevent over-concentration in correlated instruments.

use std::collections::HashMap;

use crate::types::TickerId;

// ---------------------------------------------------------------------------
// Sector enum
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Sector {
    Technology,
    USBroad,
    Semiconductors,
    SingleStock,
    Unknown,
}

// ---------------------------------------------------------------------------
// Ticker -> Sector mapping (ISA universe)
// ---------------------------------------------------------------------------

/// Returns the sector classification for a given ticker.
pub fn sector_for_ticker(ticker_id: TickerId) -> Sector {
    match ticker_id.0 {
        1 => Sector::Technology,     // QQQ3.L
        2 => Sector::USBroad,        // 3LUS.L
        3 => Sector::Semiconductors, // 3SEM.L
        4 => Sector::Technology,     // GPT3.L
        5 => Sector::Semiconductors, // NVD3.L
        6 => Sector::SingleStock,    // TSL3.L
        7 => Sector::Semiconductors, // TSM3.L
        8 => Sector::Semiconductors, // MU2.L
        9 => Sector::Technology,     // QQQS.L
        10 => Sector::USBroad,       // 3USS.L
        11 => Sector::Technology,    // QQQ5.L
        12 => Sector::USBroad,       // SP5L.L
        _ => Sector::Unknown,
    }
}

// ---------------------------------------------------------------------------
// SectorHeatTracker
// ---------------------------------------------------------------------------

/// Tracks notional GBP exposure per sector and answers concentration queries.
pub struct SectorHeatTracker {
    exposure: HashMap<Sector, f64>,
}

impl SectorHeatTracker {
    pub fn new() -> Self {
        Self {
            exposure: HashMap::new(),
        }
    }

    /// Add notional exposure when a position is opened/increased.
    pub fn record_position(&mut self, ticker_id: TickerId, notional_gbp: f64) {
        let sector = sector_for_ticker(ticker_id);
        *self.exposure.entry(sector).or_insert(0.0) += notional_gbp;
    }

    /// Remove notional exposure when a position is closed/reduced.
    pub fn clear_position(&mut self, ticker_id: TickerId, notional_gbp: f64) {
        let sector = sector_for_ticker(ticker_id);
        let entry = self.exposure.entry(sector).or_insert(0.0);
        *entry -= notional_gbp;
        // Clamp to zero to avoid negative drift from float arithmetic.
        if *entry < 0.0 {
            *entry = 0.0;
        }
    }

    /// Returns the percentage of total equity allocated to `sector`.
    /// Returns 0.0 if `total_equity` is non-positive.
    pub fn sector_heat_pct(&self, sector: Sector, total_equity: f64) -> f64 {
        if total_equity <= 0.0 {
            return 0.0;
        }
        let exp = self.exposure.get(&sector).copied().unwrap_or(0.0);
        (exp / total_equity) * 100.0
    }

    /// Returns `true` when the sector's share of equity exceeds `cap_pct` (in %).
    pub fn is_over_concentrated(&self, sector: Sector, total_equity: f64, cap_pct: f64) -> bool {
        self.sector_heat_pct(sector, total_equity) > cap_pct
    }

    /// Returns the sector with the highest notional exposure, or `None` if empty.
    pub fn hottest_sector(&self) -> Option<(Sector, f64)> {
        self.exposure
            .iter()
            .filter(|(_, v)| **v > 0.0)
            .max_by(|a, b| a.1.partial_cmp(b.1).unwrap_or(std::cmp::Ordering::Equal))
            .map(|(&s, &v)| (s, v))
    }

    /// Returns a map of sector -> percentage-of-equity for all sectors with exposure.
    /// Returns an empty map if `total_equity` is non-positive.
    pub fn sector_allocation(&self, total_equity: f64) -> HashMap<Sector, f64> {
        if total_equity <= 0.0 {
            return HashMap::new();
        }
        self.exposure
            .iter()
            .filter(|(_, v)| **v > 0.0)
            .map(|(&s, &v)| (s, (v / total_equity) * 100.0))
            .collect()
    }
}

impl Default for SectorHeatTracker {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sector_mapping_known_tickers() {
        assert_eq!(sector_for_ticker(TickerId(1)), Sector::Technology);
        assert_eq!(sector_for_ticker(TickerId(3)), Sector::Semiconductors);
        assert_eq!(sector_for_ticker(TickerId(6)), Sector::SingleStock);
        assert_eq!(sector_for_ticker(TickerId(12)), Sector::USBroad);
    }

    #[test]
    fn test_sector_mapping_unknown() {
        assert_eq!(sector_for_ticker(TickerId(0)), Sector::Unknown);
        assert_eq!(sector_for_ticker(TickerId(99)), Sector::Unknown);
    }

    #[test]
    fn test_record_and_heat_pct() {
        let mut tracker = SectorHeatTracker::new();
        tracker.record_position(TickerId(1), 2000.0); // Tech
        tracker.record_position(TickerId(4), 1000.0); // Tech

        let pct = tracker.sector_heat_pct(Sector::Technology, 10_000.0);
        assert!((pct - 30.0).abs() < 1e-9, "expected 30%, got {pct}");
    }

    #[test]
    fn test_clear_position_clamps_to_zero() {
        let mut tracker = SectorHeatTracker::new();
        tracker.record_position(TickerId(2), 500.0);
        tracker.clear_position(TickerId(2), 600.0); // overshoot

        let pct = tracker.sector_heat_pct(Sector::USBroad, 10_000.0);
        assert!((pct - 0.0).abs() < 1e-9, "expected 0%, got {pct}");
    }

    #[test]
    fn test_is_over_concentrated() {
        let mut tracker = SectorHeatTracker::new();
        tracker.record_position(TickerId(3), 4000.0); // Semis
        tracker.record_position(TickerId(5), 2000.0); // Semis

        assert!(tracker.is_over_concentrated(Sector::Semiconductors, 10_000.0, 50.0));
        assert!(!tracker.is_over_concentrated(Sector::Semiconductors, 10_000.0, 70.0));
    }

    #[test]
    fn test_hottest_sector() {
        let mut tracker = SectorHeatTracker::new();
        tracker.record_position(TickerId(1), 1000.0); // Tech
        tracker.record_position(TickerId(3), 3000.0); // Semis

        let (sector, val) = tracker
            .hottest_sector()
            .expect("should have a hottest sector");
        assert_eq!(sector, Sector::Semiconductors);
        assert!((val - 3000.0).abs() < 1e-9);
    }

    #[test]
    fn test_hottest_sector_empty() {
        let tracker = SectorHeatTracker::new();
        assert!(tracker.hottest_sector().is_none());
    }

    #[test]
    fn test_sector_allocation() {
        let mut tracker = SectorHeatTracker::new();
        tracker.record_position(TickerId(1), 2000.0); // Tech
        tracker.record_position(TickerId(2), 3000.0); // USBroad

        let alloc = tracker.sector_allocation(10_000.0);
        assert!((alloc[&Sector::Technology] - 20.0).abs() < 1e-9);
        assert!((alloc[&Sector::USBroad] - 30.0).abs() < 1e-9);
    }

    #[test]
    fn test_sector_heat_pct_zero_equity() {
        let mut tracker = SectorHeatTracker::new();
        tracker.record_position(TickerId(1), 1000.0);
        assert!((tracker.sector_heat_pct(Sector::Technology, 0.0)).abs() < 1e-9);
    }
}
