//! All enums and newtypes for AEGIS V2 data contracts (2 newtypes + 10 enums).
//! Matches docs/01_DATA_CONTRACTS.md exactly.

use pyo3::prelude::*;
use serde::{Deserialize, Serialize};

// ============================================================================
// NEWTYPES
// ============================================================================

/// Interned ticker identifier. Never use String for ticker comparisons (H01).
/// Map at Universe boundary: "QQQ3.L" -> TickerId(42)
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[pyclass(eq, hash, frozen)]
pub struct TickerId(pub u32);

#[pymethods]
impl TickerId {
    #[new]
    fn new(id: u32) -> Self {
        Self(id)
    }

    fn __repr__(&self) -> String {
        format!("TickerId({})", self.0)
    }

    #[getter]
    fn id(&self) -> u32 {
        self.0
    }
}

/// UUIDv7, time-ordered, sortable. Used for all event + order IDs.
/// Persisted in WAL. Injected into IBKR OrderRef field for crash recovery (H116).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct OrderId(pub uuid::Uuid);

impl OrderId {
    pub fn new() -> Self {
        Self(uuid::Uuid::now_v7())
    }

    pub fn to_string_repr(&self) -> String {
        self.0.to_string()
    }

    pub fn parse(s: &str) -> Result<Self, uuid::Error> {
        Ok(Self(uuid::Uuid::parse_str(s)?))
    }
}

impl Default for OrderId {
    fn default() -> Self {
        Self::new()
    }
}

impl std::str::FromStr for OrderId {
    type Err = uuid::Error;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        Self::parse(s)
    }
}

// ============================================================================
// ENUMS
// ============================================================================

/// Order direction. NEVER use strings ("BUY"/"SELL") across FFI (H04).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[pyclass(eq, frozen)]
pub enum Direction {
    Long,
    /// Short exists for type completeness but ISA safety invariant ALWAYS rejects it.
    Short,
}

/// Order side for broker submission. ISA only allows Buy (long entry) and Sell (exit).
/// Never use strings ("BUY"/"SELL") across FFI (H04).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[pyclass(eq, frozen)]
pub enum OrderSide {
    Buy,
    Sell,
}

impl std::fmt::Display for OrderSide {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            OrderSide::Buy => write!(f, "Buy"),
            OrderSide::Sell => write!(f, "Sell"),
        }
    }
}

/// Strategy identifier. Banned names (S3, S8, S15, S16) never appear.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[pyclass(eq, frozen)]
pub enum StrategyId {
    VanguardSniper,
    ApexScout,
    /// SC-15: Primary volatility signal scanner.
    HotScanner,
    /// SC-15: Sector rotation scanner.
    RotationScanner,
}

/// Why the RiskArbiter vetoed a trade. Logged with every rejection (H39).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum VetoReason {
    Approved,
    MaxPositionsReached,
    PortfolioHeatExceeded,
    SectorHeatExceeded { sector: String, pct: u32 },
    CashBufferInsufficient,
    DailyDrawdownBreached,
    StaleData { age_secs: u64 },
    BrokerDisconnected,
    WalUnavailable,
    IsaShortSellBlocked,
    InverseMutualExclusion { blocker: u32 },
    SpreadTooWide { spread_bps: u32 },
    TooLateInSession,
    VelocityCheckTriggered,
    AuctionPeriod,
    GapDetected { gap_bps: u32 },
    ConfidenceBelowFloor { confidence_x10: u32 },
    QueueDepthCritical { depth: u32 },
    ConsecutiveLossBreaker,
    RejectToHalt,
    BackpressureCritical,
    IsaAnnualLimitExceeded,
    IndicatorNotWarmedUp,
    /// SC-05: Calculated entry size below £1500 minimum.
    BelowMinimumEntrySize { size_gbp: u32 },
    /// Phase 15: CVaR heat exceeds portfolio-level threshold.
    CvarHeatExceeded { cvar_pct: u32 },
    /// Phase 15: Ticker is halted (reverse split, synthetic halt, etc).
    TickerHalted,
    /// Phase 15: Duplicate ticker already has an open position.
    DuplicatePosition,
    /// Phase 15: Correlation between new ticker and existing positions too high.
    CorrelationConcentration { corr_pct: u32 },
    /// Phase 15: GARCH forecast sigma too high for this ticker.
    GarchVolTooHigh { sigma_pct: u32 },
    /// Phase 15: Exchange not open during current trading mode.
    ExchangeClosed,
    /// Phase 15: FX rate stale for non-GBP ticker.
    FxRateStale,
    /// Phase 15: Scanner score below minimum threshold.
    ScannerScoreTooLow { score: u32 },
    /// Phase 15: Kelly fraction below floor (< 0.5%).
    KellyBelowFloor,
    /// Phase 9: Macro crisis detected (VIX extreme, credit spread wide).
    MacroCrisisDetected { vix: u32, credit_bps: u32 },
    /// Phase 9: Macro stress with stale tick data—risk assessment unreliable.
    MacroStressWithStaleTicks,
    /// Phase 9: DXY rapid appreciation >2% daily during macro stress → risk-off.
    DxyRiskOff { change_pct: u32 },
    /// Phase 9: Macro indicator feeds are stale (>5 min)—assume worst-case risk.
    MacroDataStale { age_secs: u64 },
    /// N0a: Daily trade limit reached — THE #1 cost control.
    DailyTradeLimitReached { count: u32, limit: u32 },
    /// N0d: Expected gross edge too low to cover spread + commission.
    GrossEdgeTooLow { edge_bps: u32, min_bps: u32 },
}

/// Python-visible wrapper for VetoReason (simplified for FFI).
#[derive(Clone, Debug, PartialEq)]
#[pyclass(frozen)]
pub struct PyVetoReason {
    #[pyo3(get)]
    pub name: String,
    #[pyo3(get)]
    pub detail: String,
}

#[pymethods]
impl PyVetoReason {
    fn __repr__(&self) -> String {
        if self.detail.is_empty() {
            format!("VetoReason({})", self.name)
        } else {
            format!("VetoReason({}: {})", self.name, self.detail)
        }
    }
}

impl From<&VetoReason> for PyVetoReason {
    fn from(vr: &VetoReason) -> Self {
        match vr {
            VetoReason::Approved => Self {
                name: "Approved".into(),
                detail: String::new(),
            },
            VetoReason::MaxPositionsReached => Self {
                name: "MaxPositionsReached".into(),
                detail: String::new(),
            },
            VetoReason::PortfolioHeatExceeded => Self {
                name: "PortfolioHeatExceeded".into(),
                detail: String::new(),
            },
            VetoReason::SectorHeatExceeded { sector, pct } => Self {
                name: "SectorHeatExceeded".into(),
                detail: format!("{sector}: {pct}%"),
            },
            VetoReason::CashBufferInsufficient => Self {
                name: "CashBufferInsufficient".into(),
                detail: String::new(),
            },
            VetoReason::DailyDrawdownBreached => Self {
                name: "DailyDrawdownBreached".into(),
                detail: String::new(),
            },
            VetoReason::StaleData { age_secs } => Self {
                name: "StaleData".into(),
                detail: format!("{age_secs}s"),
            },
            VetoReason::BrokerDisconnected => Self {
                name: "BrokerDisconnected".into(),
                detail: String::new(),
            },
            VetoReason::WalUnavailable => Self {
                name: "WalUnavailable".into(),
                detail: String::new(),
            },
            VetoReason::IsaShortSellBlocked => Self {
                name: "IsaShortSellBlocked".into(),
                detail: String::new(),
            },
            VetoReason::InverseMutualExclusion { blocker } => Self {
                name: "InverseMutualExclusion".into(),
                detail: format!("blocker={blocker}"),
            },
            VetoReason::SpreadTooWide { spread_bps } => Self {
                name: "SpreadTooWide".into(),
                detail: format!("{spread_bps}bps"),
            },
            VetoReason::TooLateInSession => Self {
                name: "TooLateInSession".into(),
                detail: String::new(),
            },
            VetoReason::VelocityCheckTriggered => Self {
                name: "VelocityCheckTriggered".into(),
                detail: String::new(),
            },
            VetoReason::AuctionPeriod => Self {
                name: "AuctionPeriod".into(),
                detail: String::new(),
            },
            VetoReason::GapDetected { gap_bps } => Self {
                name: "GapDetected".into(),
                detail: format!("{gap_bps}bps"),
            },
            VetoReason::ConfidenceBelowFloor { confidence_x10 } => Self {
                name: "ConfidenceBelowFloor".into(),
                detail: format!("{confidence_x10}"),
            },
            VetoReason::QueueDepthCritical { depth } => Self {
                name: "QueueDepthCritical".into(),
                detail: format!("{depth}"),
            },
            VetoReason::ConsecutiveLossBreaker => Self {
                name: "ConsecutiveLossBreaker".into(),
                detail: String::new(),
            },
            VetoReason::RejectToHalt => Self {
                name: "RejectToHalt".into(),
                detail: String::new(),
            },
            VetoReason::BackpressureCritical => Self {
                name: "BackpressureCritical".into(),
                detail: String::new(),
            },
            VetoReason::IsaAnnualLimitExceeded => Self {
                name: "IsaAnnualLimitExceeded".into(),
                detail: String::new(),
            },
            VetoReason::IndicatorNotWarmedUp => Self {
                name: "IndicatorNotWarmedUp".into(),
                detail: String::new(),
            },
            VetoReason::BelowMinimumEntrySize { size_gbp } => Self {
                name: "BelowMinimumEntrySize".into(),
                detail: format!("£{size_gbp}"),
            },
            VetoReason::CvarHeatExceeded { cvar_pct } => Self {
                name: "CvarHeatExceeded".into(),
                detail: format!("{cvar_pct}%"),
            },
            VetoReason::TickerHalted => Self {
                name: "TickerHalted".into(),
                detail: String::new(),
            },
            VetoReason::DuplicatePosition => Self {
                name: "DuplicatePosition".into(),
                detail: String::new(),
            },
            VetoReason::CorrelationConcentration { corr_pct } => Self {
                name: "CorrelationConcentration".into(),
                detail: format!("{corr_pct}%"),
            },
            VetoReason::GarchVolTooHigh { sigma_pct } => Self {
                name: "GarchVolTooHigh".into(),
                detail: format!("{sigma_pct}%"),
            },
            VetoReason::ExchangeClosed => Self {
                name: "ExchangeClosed".into(),
                detail: String::new(),
            },
            VetoReason::FxRateStale => Self {
                name: "FxRateStale".into(),
                detail: String::new(),
            },
            VetoReason::ScannerScoreTooLow { score } => Self {
                name: "ScannerScoreTooLow".into(),
                detail: format!("{score}"),
            },
            VetoReason::KellyBelowFloor => Self {
                name: "KellyBelowFloor".into(),
                detail: String::new(),
            },
            VetoReason::MacroCrisisDetected { vix, credit_bps } => Self {
                name: "MacroCrisisDetected".into(),
                detail: format!("VIX={}, credit={}bps", vix, credit_bps),
            },
            VetoReason::MacroStressWithStaleTicks => Self {
                name: "MacroStressWithStaleTicks".into(),
                detail: String::new(),
            },
            VetoReason::DxyRiskOff { change_pct } => Self {
                name: "DxyRiskOff".into(),
                detail: format!("DXY change={}%", change_pct),
            },
            VetoReason::MacroDataStale { age_secs } => Self {
                name: "MacroDataStale".into(),
                detail: format!("age={} secs", age_secs),
            },
            VetoReason::DailyTradeLimitReached { count, limit } => Self {
                name: "DailyTradeLimitReached".into(),
                detail: format!("{count}/{limit}"),
            },
            VetoReason::GrossEdgeTooLow { edge_bps, min_bps } => Self {
                name: "GrossEdgeTooLow".into(),
                detail: format!("edge={edge_bps}bps, spread={min_bps}bps"),
            },
        }
    }
}

/// Risk Arbiter regime. Strict hierarchy: HALT > FLATTEN > REDUCE > NORMAL.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[pyclass(eq, ord, frozen)]
pub enum RiskRegime {
    Normal = 0,
    Reduce = 1,
    Flatten = 2,
    Halt = 3,
}

/// Broker acknowledgement status.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[pyclass(eq, frozen)]
pub enum BrokerAckStatus {
    Accepted,
    Rejected,
    PendingCancel,
    Cancelled,
}

/// Why an exit was triggered.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[pyclass(eq, frozen)]
pub enum ExitReason {
    HaltFlatten,
    HardStopLoss,
    ChandelierTrailing,
    EodFlatten,
    SignalReversal,
    SyntheticHalt,
    ReverseSplitSuspected,
    /// SC-06: Position remainder below dust threshold → market sell.
    DustGuard,
}

/// Exit priority. Higher number = higher priority. Enum ordering matches.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[pyclass(eq, ord, frozen)]
pub enum ExitPriority {
    SignalReversal = 1,
    /// SC-06: Dust guard cleanup (remainder < £500).
    DustGuard = 2,
    EodFlatten = 3,
    ChandelierStop = 4,
    HardStopLoss = 5,
    HaltFlatten = 6,
}

/// Order type for exit execution.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[pyclass(eq, frozen)]
pub enum ExitOrderType {
    MarketSell,
    MarketToLimit,
    LimitAtStop,
}

/// Order lifecycle state machine states (see 02_STATE_MACHINE.md).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[pyclass(eq, hash, frozen)]
pub enum OrderState {
    IntentGenerated,
    RiskChecked,
    Rejected,
    WalWritten,
    Submitted,
    BrokerRejected,
    Acknowledged,
    Orphaned,
    PartiallyFilled,
    Filled,
    ExitRegistered,
    ExitTriggered,
    ExitOrderSubmitted,
    ExitFilled,
    PositionClosed,
}

/// WAL event type discriminator.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum WalEventType {
    RoutedOrder,
    BrokerAck,
    FillEvent,
    ExitSignal,
    PositionClosed,
    RiskStateChange,
    OrphanResolved,
    StateSnapshot,
    SystemReady,
    /// N2a: Signal generated but rejected by a gate (for missed-winner analysis).
    SignalRejected,
    /// N2c: Post-hoc missed-winner candidate (written by nightly analysis).
    MissedWinnerCandidate,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_risk_regime_ordering() {
        assert!(RiskRegime::Halt > RiskRegime::Flatten);
        assert!(RiskRegime::Flatten > RiskRegime::Reduce);
        assert!(RiskRegime::Reduce > RiskRegime::Normal);
    }

    #[test]
    fn test_exit_priority_ordering() {
        assert!(ExitPriority::HaltFlatten > ExitPriority::HardStopLoss);
        assert!(ExitPriority::HardStopLoss > ExitPriority::ChandelierStop);
        assert!(ExitPriority::ChandelierStop > ExitPriority::EodFlatten);
        assert!(ExitPriority::EodFlatten > ExitPriority::SignalReversal);
    }

    #[test]
    fn test_ticker_id_equality() {
        let a = TickerId(42);
        let b = TickerId(42);
        let c = TickerId(99);
        assert_eq!(a, b);
        assert_ne!(a, c);
    }

    #[test]
    fn test_order_id_uniqueness() {
        let a = OrderId::new();
        let b = OrderId::new();
        assert_ne!(a, b);
    }

    #[test]
    fn test_order_id_roundtrip() {
        let id = OrderId::new();
        let s = id.to_string_repr();
        let parsed = OrderId::parse(&s).expect("valid uuid");
        assert_eq!(id, parsed);
    }

    #[test]
    fn test_direction_variants() {
        assert_ne!(Direction::Long, Direction::Short);
    }

    #[test]
    fn test_order_side_display() {
        assert_eq!(format!("{}", OrderSide::Buy), "Buy");
        assert_eq!(format!("{}", OrderSide::Sell), "Sell");
        assert_ne!(OrderSide::Buy, OrderSide::Sell);
    }

    #[test]
    fn test_order_state_all_15_variants() {
        let states = [
            OrderState::IntentGenerated,
            OrderState::RiskChecked,
            OrderState::Rejected,
            OrderState::WalWritten,
            OrderState::Submitted,
            OrderState::BrokerRejected,
            OrderState::Acknowledged,
            OrderState::Orphaned,
            OrderState::PartiallyFilled,
            OrderState::Filled,
            OrderState::ExitRegistered,
            OrderState::ExitTriggered,
            OrderState::ExitOrderSubmitted,
            OrderState::ExitFilled,
            OrderState::PositionClosed,
        ];
        assert_eq!(states.len(), 15);
    }

    #[test]
    fn test_veto_reason_to_py() {
        let vr = VetoReason::SpreadTooWide { spread_bps: 65 };
        let py = PyVetoReason::from(&vr);
        assert_eq!(py.name, "SpreadTooWide");
        assert!(py.detail.contains("65"));
    }
}
