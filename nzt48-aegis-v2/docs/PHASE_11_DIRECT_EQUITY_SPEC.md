# PHASE 11 — US DIRECT EQUITIES + CORE GLOBAL INFRASTRUCTURE
# Part 1 of 2 (Sections 1–8)

## Prerequisite
All 10 phases (0–9) APPROVED. All tests green. Crucible validated.
MODE B stable. 12 LSE leveraged ETPs trading live on paper.

---

## Document Map

| Part | Sections | File |
|------|----------|------|
| Part 1 (this file) | 1–8: Overview, 5-Mode Architecture, Smart Router, Allocator, UniverseScanner, HotScanner, RotationScanner, Executioner Upgrades | PHASE_11_DIRECT_EQUITY_SPEC.md |
| Part 2 | 9–17: Infinite Chandelier, T-5 Rule, RiskGate (31 vetoes), Ouroboros Upgrades, AUM Scaling, Clock Extensions, ISA Compliance Layer, Acceptance Tests, Phase Gate | (to be written) |

---

## Section 1: Overview and Scope

### What Phase 11 Adds

Phase 11 is the first global expansion phase. It introduces:

1. **MODE B+** — Hybrid overlap window (14:30–16:30 UTC). 80 lines of LSE ETPs
   plus 20 lines of US equities running simultaneously for the 2-hour window
   where both LSE and US pre-market overlap.

2. **MODE C** — Americas session (16:30–21:00 UTC). 100 lines dedicated to
   US and Canadian direct equities on NYSE, NASDAQ, and TSX. The system
   trades US equities at 1x leverage inside an ISA (no margin, no shorting).

3. **DARK mode** — Homework window (21:00–23:00 UTC). No new positions. No
   scanning. Ouroboros runs its nightly calibration and universe discovery
   pipeline. All carried positions hold under Infinite Chandelier protection.

4. **Smart Router** — Replaces the old two-column routing table. The Router
   checks in real time whether an LSE ETP exists for any underlying, evaluates
   ETP health, compares costs, and enforces the ISA hard gate.

5. **Component rebranding** — VanguardSniper → HotScanner, ApexScout →
   RotationScanner, RiskArbiter → RiskGate, Smart Router → Router, Line
   Allocator → Allocator. Executioner and Ouroboros names are kept.

6. **Underlying tracking** — Every ETP subscribed automatically triggers a
   parallel real-time subscription to its underlying equity. NVD3.L → track
   NVIDIA. QQQS.L (short QQQ) → track QQQ with inverted signal logic.

7. **ISA-only universe enforcement** — HMRC Table 1 + Table 2 recognised
   exchanges only. ADR trap logic. ETP-first principle hardened into Router.

8. **Infinite Chandelier** — Replaces the 5-rung fixed ladder from
   `exit_engine.rs::ChandelierStrategy`. ATR-based rungs with geometric decay,
   8 adaptive multipliers, no ceiling, stops ratchet up only. (Full spec in
   Part 2, Section 9.)

9. **Adaptive T-5 Rule** — Replaces fixed EOD_PHASE3_SECS. Position-count and
   volatility aware. MEGA RUNNER exception for positions >+102%. (Full spec
   in Part 2, Section 10.)

10. **RiskGate 31 vetoes** — Replaces RiskArbiter with 31 ordered, self-tuning
    veto checks. ISA-specific checks included. (Full spec in Part 2, Section 11.)

### What Phase 11 Does NOT Touch

- MODE B internals (LSE ETP scanning logic, tick ingestion, WAL format)
- Redis schema (extended, not replaced)
- IB Gateway connection (port 4004, client_id=101, unchanged)
- The existing `universe.rs::UniverseClass` enum — Phase 11 adds `Hot` and
  `Rotation` variants but the existing `Vanguard` and `Apex` variants are kept
  as aliases during the migration window
- The existing `clock.rs` constants for LSE — Phase 11 adds new clock
  functions alongside them
- Phases 12–13 scope: European equities (Phase 12) and Asia-Pac (Phase 13)
  are not scanned in Phase 11

### Relationship to Phases 12 and 13

```
Phase 11 → MODE B+ and MODE C operational
           Smart Router, Allocator, UniverseScanner, HotScanner,
           RotationScanner all generalised and parameterised.

Phase 12 → Extends MODE B (08:00–16:30 UTC) to add European direct
           equities on 15 ISA-eligible European exchanges. No new mode
           created. UniverseScanner nightly crawl extended only.

Phase 13 → Introduces MODE A (Asia-Pac, 23:00–08:00 UTC). Full 5-mode
           cycle complete. All 14+ exchanges active.
```

---

## Section 2: 5-Mode Architecture

### Mode Definitions

| Mode | Name | UTC Window | Primary Universe | Lines |
|------|------|-----------|-----------------|-------|
| MODE A | Asia-Pac | 23:00–08:00 | ASX, TSE, HKEX, SGX, KRX (Phase 13) | 100 |
| MODE B | Europe | 08:00–14:30 | LSE ETPs + European direct equities (Phase 12) | 100 |
| MODE B+ | Hybrid | 14:30–LSE_CLOSE¹ | LSE ETPs (80 lines) + US equities (20 lines) | 100 |
| MODE C | Americas | 16:30–21:00 | US/Canada direct equities | 100 |
| DARK | Homework | 21:00–23:00 | No new positions. Carried positions under Chandelier. Ouroboros runs. | 0 |

Phase 11 implements MODE B+ (extending existing MODE B transition), MODE C,
and DARK. MODE A is a Phase 13 stub only.

**¹ MODE B+ end boundary is DST-dependent.** LSE closes at 16:30 UTC in
GMT (winter) but at **15:30 UTC in BST** (summer, late March–late October).
The MODE B+ end boundary must be computed dynamically:

```python
# Correct approach — uses ZoneInfo, not hardcoded UTC
from zoneinfo import ZoneInfo
from datetime import datetime, time

def mode_b_plus_end_utc(date: datetime) -> time:
    """Returns the UTC time when MODE B+ ends (= LSE close)."""
    lse_close_local = datetime.combine(date, time(16, 30),
                                        tzinfo=ZoneInfo("Europe/London"))
    return lse_close_local.astimezone(ZoneInfo("UTC")).time()
# Returns 16:30 UTC in GMT, 15:30 UTC in BST
```

The ModeController's `from_utc_secs` ModeBPlus arm must use the runtime-
computed LSE close UTC seconds, NOT the hardcoded `16 * 3600 + 30 * 60`.
All APScheduler pre-LSE jobs (cross-asset macro update, PDF generation,
pre-market scoring) must use `timezone="Europe/London"` NOT
`timezone="UTC"` to maintain their relative offset from LSE open across
BST/GMT transitions.

### Mode Transition Logic

Transitions are hard clock boundaries evaluated every second by the mode
supervisor task. Transitions never interrupt an active order in flight —
the current order completes first, then mode flips.

```
UTC clock tick
    │
    ▼
ModeController::evaluate(utc_secs)
    │
    ├── 23:00 UTC → MODE A  (Phase 13 only; stub returns DARK until Phase 13)
    ├── 08:00 UTC → MODE B  (LSE open)
    ├── 14:30 UTC → MODE B+ (US pre-market overlap begins)
    ├── 16:30 UTC → MODE C  (LSE close; US regular session open)
    ├── 21:00 UTC → DARK    (NYSE close + 30 min wind-down)
    └── 23:00 UTC → MODE A  (loop)
```

```rust
// New: rust_core/src/mode_controller.rs

/// Trading session mode.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TradingMode {
    /// Asia-Pacific session. Phase 13 only. Stub in Phase 11.
    ModeA,
    /// European session. LSE ETPs + (Phase 12) European direct equities.
    ModeB,
    /// Hybrid overlap. LSE ETPs (80 lines) + US equities (20 lines).
    ModeBPlus,
    /// Americas session. US/Canada direct equities.
    ModeC,
    /// No new positions. Ouroboros runs.
    Dark,
}

impl TradingMode {
    /// Derive mode from UTC seconds-from-midnight.
    ///
    /// IMPORTANT: MODE A spans midnight (23:00–08:00 UTC). The arm must
    /// use `|| s < 28800` (wrapping condition), NOT `&& s < 28800`.
    /// Using `s >= 3600 && s < 28800` (01:00–08:00) is WRONG and will
    /// classify the 23:00–01:00 window as DARK, losing 2h of MODE A.
    pub fn from_utc_secs(utc_secs: u32) -> Self {
        match utc_secs {
            // MODE A: 23:00–08:00 UTC (spans midnight — use OR, not AND)
            s if s >= 23 * 3600 || s < 8 * 3600        => TradingMode::ModeA,
            s if s >= 8 * 3600 && s < 14 * 3600 + 30 * 60 => TradingMode::ModeB,
            // NOTE: ModeBPlus upper bound uses runtime LSE close (15:30 BST or 16:30 GMT)
            // The hardcoded 16*3600+30*60 is correct for GMT only — see note above.
            s if s >= 14 * 3600 + 30 * 60 && s < 16 * 3600 + 30 * 60 => TradingMode::ModeBPlus,
            s if s >= 16 * 3600 + 30 * 60 && s < 21 * 3600 => TradingMode::ModeC,
            _                                           => TradingMode::Dark,
        }
    }

    /// Human-readable label for logging and metrics.
    pub fn label(&self) -> &'static str {
        match self {
            TradingMode::ModeA     => "MODE_A",
            TradingMode::ModeB     => "MODE_B",
            TradingMode::ModeBPlus => "MODE_B_PLUS",
            TradingMode::ModeC     => "MODE_C",
            TradingMode::Dark      => "DARK",
        }
    }

    /// Whether new entries are permitted in this mode.
    pub fn entries_permitted(&self) -> bool {
        !matches!(self, TradingMode::Dark | TradingMode::ModeA)
        // ModeA returns false until Phase 13 implements it
    }

    /// Hot-scanner line allocation for this mode.
    pub fn hot_lines(&self) -> u8 {
        match self {
            TradingMode::ModeB     => 40,
            TradingMode::ModeBPlus => 40,  // 32 ETP + 8 US equity
            TradingMode::ModeC     => 40,
            _                      => 0,
        }
    }

    /// Rotation-scanner line allocation for this mode.
    pub fn rotation_lines(&self) -> u8 {
        match self {
            TradingMode::ModeB     => 60,
            TradingMode::ModeBPlus => 60,  // 48 ETP + 12 US equity
            TradingMode::ModeC     => 60,
            _                      => 0,
        }
    }
}
```

### MODE B+ Detail

**DST NOTE:** During BST (late March–late October), LSE closes at **15:30 UTC**
not 16:30 UTC. MODE B+ effectively becomes a US-only session from 15:30–16:30
UTC during BST. The LSE ETP subscriptions should be released at LSE close (dynamic),
not held open until 16:30 UTC. The runtime `mode_b_plus_end_utc()` function above
governs this boundary. During BST, the 80-line LSE allocation should gracefully
reduce to carry-position-only after 15:30 UTC.

During MODE B+ (14:30–LSE_CLOSE UTC), the 100 IBKR market data lines are split:

- **80 lines** — LSE ETPs and their underlyings (same as MODE B)
- **20 lines** — US equity pre-market top candidates from the prior night's
  Ouroboros MODE C ranked list. These are the 20 highest-scoring US names
  that show pre-market momentum.

The Allocator decides the exact 80/20 split dynamically based on open
positions. If 15 positions are open in LSE ETPs and each requires 2 lines
(ETP + underlying), that uses 30 lines, leaving 70 for new ETP candidates
plus 20 for US. If ETP positions expand, US allocation shrinks to a floor
of 10 lines. Never below 10.

### MODE C Detail

During MODE C (16:30–21:00 UTC), LSE is closed. All 100 lines are allocated
to US and Canadian equities. The ETP-first principle still applies within
MODE C — if the Router detects that a US equity has a health-passing LSE ETP,
that ETP is noted but NOT traded (LSE is closed). The direct equity is traded.

MODE C entry cutoff: 20:30 UTC (30 minutes before DARK). No new entries after
20:30. Existing positions run under Chandelier until DARK or T-5 triggers.

### DARK Detail

From 21:00–23:00 UTC no new subscriptions are opened, no new positions
entered, no scanning runs. Carried positions (if any) maintain their
Chandelier stops via the Redis-persisted stop levels. Ouroboros begins
calibration at 21:05 UTC. Ouroboros must complete by 22:55 UTC (hard
deadline). MODE A opens at 23:00 UTC regardless of Ouroboros status —
if Ouroboros fails, RULING A2 applies (ORANGE halt, no new entries).

The system does NOT flatten all positions at DARK transition. Any position
held through DARK is a cross-session carry — permitted only when Chandelier
stop is at or above breakeven (i.e., the position cannot lose more than
commissions on the carried portion).

---

## Section 3: Smart Router

The Router is the single authority that decides execution vehicle for any
signal. It replaces the static routing table from the old Phase 11 spec.

### Routing Principle

```
ETP ALWAYS WINS — unless:
    (a) no ETP exists for this underlying, OR
    (b) ETP fails health check AND direct equity is ISA-eligible AND
        direct equity cost < ETP cost * 1.10 (10% cost tolerance)

ISA HARD GATE — if no ISA-eligible execution vehicle exists, skip entirely.
ADR TRAP — ADRs whose underlying trades only on a non-HMRC-recognised
           exchange are ineligible. Example: TSMC ADR on NASDAQ is NOT
           eligible because TSMC's primary exchange (TWSE) is not on HMRC
           Table 1 or Table 2. TSM3.L (LSE ETP for TSMC) IS eligible.
```

### Data Structures

```rust
// New: rust_core/src/router.rs

use crate::types::TickerId;

/// Possible execution vehicles for a signal.
#[derive(Clone, Debug)]
pub enum RouteTarget {
    /// Trade through an LSE leveraged ETP.
    Etp {
        ticker_id: TickerId,
        symbol: String,
        leverage: f32,
        underlying_symbol: String,
    },
    /// Trade the equity directly on its primary exchange.
    Direct {
        ticker_id: TickerId,
        symbol: String,
        exchange: String,        // "NYSE", "NASDAQ", "TSX", "XETRA", etc.
        currency: String,        // "USD", "CAD", "EUR", etc.
        is_isa_eligible: bool,
    },
    /// No valid ISA-eligible vehicle. Skip this signal entirely.
    Blocked {
        reason: BlockReason,
    },
}

#[derive(Clone, Debug)]
pub enum BlockReason {
    /// No ISA-eligible vehicle of any kind.
    NoIsaEligibleVehicle,
    /// ADR with underlying on non-HMRC-recognised exchange.
    AdrTrap { underlying_exchange: String },
    /// All vehicles failed health check.
    AllVehiclesUnhealthy,
    /// ISA annual limit headroom insufficient for position size.
    IsaLimitExceeded,
}

/// Outcome of a routing decision, including cost comparison.
#[derive(Clone, Debug)]
pub struct RouteDecision {
    pub target: RouteTarget,
    /// Estimated round-trip cost in basis points.
    pub est_cost_bps: f64,
    /// Whether the ETP was considered and rejected.
    pub etp_rejected: bool,
    /// Reason ETP was rejected, if applicable.
    pub etp_rejection_reason: Option<EtpRejectionReason>,
}

#[derive(Clone, Debug)]
pub enum EtpRejectionReason {
    /// No ETP exists for this underlying on LSE.
    NoEtpExists,
    /// Spread z-score above threshold (ETP illiquid vs 20d history).
    SpreadZscoreHigh { z_score: f64 },
    /// Volume below 20% of 20-day average.
    VolumeLow { ratio: f64 },
    /// Tracking error above 50 basis points vs underlying.
    TrackingErrorHigh { error_bps: f64 },
    /// ETP session closed (e.g., trying to use LSE ETP during MODE C).
    SessionClosed,
}

/// Static configuration for the Router. Loaded from config.toml.
#[derive(Clone, Debug)]
pub struct RouterConfig {
    /// Spread z-score above which ETP health check fails.
    pub etp_spread_z_threshold: f64,        // default: 2.0
    /// Volume ratio below which ETP health check fails.
    pub etp_volume_ratio_threshold: f64,    // default: 0.20
    /// Tracking error in bps above which ETP health check fails.
    pub etp_tracking_error_bps_threshold: f64, // default: 50.0
    /// Cost tolerance: use direct only if cost < ETP_cost * tolerance.
    pub direct_cost_tolerance_multiplier: f64, // default: 1.10
    /// HMRC Table 1 + Table 2 recognised exchange codes.
    pub recognised_exchanges: Vec<String>,
}

impl Default for RouterConfig {
    fn default() -> Self {
        Self {
            etp_spread_z_threshold: 2.0,
            etp_volume_ratio_threshold: 0.20,
            etp_tracking_error_bps_threshold: 50.0,
            direct_cost_tolerance_multiplier: 1.10,
            recognised_exchanges: vec![
                "NYSE".to_string(), "NASDAQ".to_string(), "TSX".to_string(),
                "LSE".to_string(),  "XETRA".to_string(), "SBF".to_string(),
                "AEB".to_string(),  "SIX".to_string(),   "OMX".to_string(),
                "BIT".to_string(),  "BME".to_string(),
                // Full list populated from config/recognised_exchanges.toml
            ],
        }
    }
}
```

### Routing Logic (Decision Tree)

```
Signal fires on underlying U (e.g., NVDA)
    │
    ├─► STEP 1: ISA gate on the underlying itself
    │       Is U's primary exchange HMRC-recognised? NO → BlockReason::NoIsaEligibleVehicle
    │       Is U an ADR with non-recognised primary exchange? YES → BlockReason::AdrTrap
    │
    ├─► STEP 2: ETP lookup (from nightly-cached etp_map.toml)
    │       Does an LSE ETP exist for U? NO → skip to STEP 5 (direct)
    │
    ├─► STEP 3: ETP session check
    │       Is LSE currently open (MODE B or MODE B+)? NO → EtpRejectionReason::SessionClosed
    │       YES → continue to health check
    │
    ├─► STEP 4: ETP health check (real-time, from last 60s tick data)
    │       spread_z = (current_spread - 20d_mean_spread) / 20d_std_spread
    │       If spread_z > 2.0   → EtpRejectionReason::SpreadZscoreHigh
    │       If vol_ratio < 0.20 → EtpRejectionReason::VolumeLow
    │       If tracking_err > 50bps → EtpRejectionReason::TrackingErrorHigh
    │       ALL pass → Route::Etp (ETP wins, done)
    │
    ├─► STEP 5: Direct equity fallback
    │       Is U ISA-eligible (exchange on HMRC list)? NO → BlockReason::NoIsaEligibleVehicle
    │       Compute direct_cost_bps (spread + IBKR commission estimate)
    │       If etp_rejected in STEP 4:
    │           compute etp_cost_bps (for reference)
    │           if direct_cost_bps <= etp_cost_bps * 1.10 → Route::Direct
    │           else → BlockReason::AllVehiclesUnhealthy
    │       Else (no ETP exists) → Route::Direct
    │
    └─► STEP 6: ISA limit headroom check
            If direct position size > remaining ISA annual allowance →
                BlockReason::IsaLimitExceeded
            Else → finalise RouteDecision
```

### ETP Cost Estimation

```python
# python_brain/router_cost.py

def estimate_etp_cost_bps(etp_spread_bps: float, leverage: float,
                           holding_minutes: float) -> float:
    """
    ETP round-trip cost:
      spread_cost   = etp_spread_bps (half-spread each way)
      decay_cost    = daily_decay_bps * (holding_minutes / 390)
      tracking_err  = measured from last 20 sessions

    Daily decay for 3x ETPs is approximately 5-15bps per day at normal vol.
    For intraday holds (<390 min) decay is prorated.
    """
    spread_cost = etp_spread_bps
    daily_decay_bps = 10.0 * (leverage - 1.0)  # rough: 2x=10bps, 3x=20bps
    decay_cost = daily_decay_bps * (holding_minutes / 390.0)
    return spread_cost + decay_cost

def estimate_direct_cost_bps(direct_spread_bps: float,
                              ibkr_commission_per_share: float,
                              price: float) -> float:
    """Direct equity round-trip: spread + 2x IBKR commission."""
    commission_bps = (2.0 * ibkr_commission_per_share / price) * 10000.0
    return direct_spread_bps + commission_bps
```

### ISA Hard Gate

Any `RouteDecision` with `target = RouteTarget::Blocked` is a hard stop.
The signal is discarded. No position is opened. The block reason is logged
to the WAL as a `SignalBlocked` event for Ouroboros audit.

The Router does NOT cache blocking decisions. Every signal re-evaluates
freshly. ETP health is evaluated on current tick data, not stale cache.

---

## Section 4: Line Allocator (Allocator)

The Allocator manages the 100 IBKR market data lines across HotScanner,
RotationScanner, carried positions, and underlying tracking subscriptions.

### Line Budget by Mode

```
MODE B (100 lines total):
    ├── Safety-locked (open positions + their underlyings): dynamic, 0–40
    ├── HotScanner slots:      40 (after safety-locked deducted)
    └── RotationScanner slots: 60 - safety_locked_overflow
    Floor: HotScanner never below 20 lines, RotationScanner never below 20.

MODE B+ (100 lines total):
    ├── Safety-locked (carried positions):     dynamic
    ├── HotScanner ETP slots:     32
    ├── HotScanner US equity slots: 8
    ├── RotationScanner ETP slots: 48
    └── RotationScanner US equity slots: 12
    Overflow rule: if safety-locked > 30, compress RotationScanner first.

MODE C (100 lines total):
    ├── Safety-locked (US equity positions):   dynamic
    ├── HotScanner US equity slots:    40
    └── RotationScanner US equity slots: 60
```

### Data Structures

```rust
// Extends existing line management in the dynamic rotation manager.

/// Allocation mode — drives how lines are distributed.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AllocationMode {
    /// Normal: enough lines for all components.
    Normal,
    /// Compressed: safety-locked positions consuming >30 lines.
    Compressed,
    /// Minimal: emergency — only safety-locked + HotScanner minimums.
    Minimal,
}

/// Current line budget snapshot.
#[derive(Clone, Debug)]
pub struct LineBudget {
    pub mode: AllocationMode,
    pub safety_locked: u8,      // open positions + underlyings
    pub hot_scanner: u8,        // HotScanner active subscriptions
    pub rotation_scanner: u8,   // RotationScanner active subscriptions
    pub total_used: u8,
    pub headroom: u8,
}
```

### Thompson Sampling Line Distribution

Within each component's allocation (HotScanner / RotationScanner), which
specific tickers occupy which lines is decided by Thompson Sampling.

Each ticker has a Beta(alpha, beta) distribution representing its expected
signal yield per subscribed hour. On each Ouroboros calibration cycle:

- `alpha` is incremented by the number of profitable signals generated while
  the ticker was in a Hot or Rotation slot
- `beta` is incremented by the number of unprofitable signals or null-yield
  hours
- Allocation samples from Beta distributions; highest samples get slots

This ensures tickers with genuine track records stay allocated and new
candidates get exploration time proportional to uncertainty.

```python
# python_brain/allocator.py

import numpy as np
from dataclasses import dataclass

@dataclass
class TickerBeta:
    symbol: str
    alpha: float = 1.0   # wins + 1 (uninformative prior)
    beta: float  = 1.0   # losses + 1

def thompson_sample(tickers: list[TickerBeta], n_slots: int) -> list[str]:
    """Draw n_slots tickers via Thompson Sampling without replacement."""
    scores = [(t.symbol, np.random.beta(t.alpha, t.beta)) for t in tickers]
    scores.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scores[:n_slots]]
```

### IBKR Snapshot for Carried Positions

At every mode transition, the Allocator calls `reqPositions` to get the
current IBKR position snapshot. This is the authoritative source for
`safety_locked` count. The Allocator does NOT rely on internal state alone —
IBKR is the ground truth. Discrepancies between internal state and IBKR
snapshot trigger a reconciliation event logged to WAL.

---

## Section 5: UniverseScanner

The UniverseScanner runs nightly inside Ouroboros (21:05–23:50 UTC) and
produces the ticker lists that HotScanner and RotationScanner use the next
day. It replaces the static `initial_universe.toml` approach from Phases 0–9.

### Daily Discovery Pipeline

```
STEP 1: Master Registry Pull
    │  Source: config/master_registry.toml — human-curated seed list
    │  Also pulls: IBKR reqContractDetails for each registered exchange
    │  Exchanges in Phase 11: NYSE, NASDAQ, TSX (+ LSE already present)
    │  Output: raw_candidates[] — all contracts with ConID, symbol, exchange
    │
    ▼
STEP 2: Hard Filters (applied in order, cheapest first)
    │  2a. ISA eligibility: exchange in recognised_exchanges list → KEEP
    │  2b. ADR trap: secType=STK, primaryExch NOT recognised → DISCARD
    │  2c. Price floor: last_close > $2.00 USD equivalent → KEEP
    │  2d. Market cap floor: > $500M USD equivalent → KEEP
    │  2e. Liquidity: 20d_avg_daily_volume > 500,000 shares → KEEP
    │  2f. Spread: avg_spread_bps < 50 → KEEP
    │  2g. Recently traded: last_trade within 3 business days → KEEP
    │  2h. Not suspended: IBKR trading status = ACTIVE → KEEP
    │  Output: filtered_candidates[]
    │
    ▼
STEP 3: ETP Overlay
    │  For each filtered_candidate C:
    │    Look up etp_map.toml: does an LSE ETP exist for C?
    │      YES → C is flagged as etp_available=true.
    │             Add ETP ticker to candidates if not already present.
    │             Mark direct C as etp_preferred (will be routed to ETP).
    │      NO  → C is flagged etp_available=false. Trades direct.
    │  Note: tickers that are themselves LSE ETPs are already in the list.
    │  Output: candidates[] with etp_available and etp_symbol fields
    │
    ▼
STEP 4: Composite Scoring (per ticker)
    │  Score = weighted sum of:
    │    momentum_score    (0.30) — 20d return / vol, signed
    │    liquidity_score   (0.20) — log(avg_daily_volume / 500k)
    │    volatility_score  (0.20) — ATR% in 1.5–4.0% sweet spot
    │    regime_score      (0.15) — HMM state alignment (bull=1.0, sideways=0.5, bear=0.0)
    │    recency_score     (0.15) — days since last profitable signal (inverse)
    │  Output: candidates[] sorted by composite_score descending
    │
    ▼
STEP 5: Slot Allocation
    │  Per mode, allocate tickers into ranked lists:
    │    MODE_B_HOT:        top 60  (Hot candidates for MODE B)
    │    MODE_B_ROTATION:   top 400 (Rotation pool for MODE B)
    │    MODE_BP_US_HOT:    top 25  (US equity Hot for MODE B+ 20-line slot)
    │    MODE_C_HOT:        top 60  (Hot candidates for MODE C)
    │    MODE_C_ROTATION:   top 400 (Rotation pool for MODE C)
    │  Each list stored with composite_score and Thompson alpha/beta params.
    │
    ▼
STEP 6: Pre-market Update (07:45 UTC for MODE B, 14:20 UTC for MODE B+,
    │  16:25 UTC for MODE C)
    │  Lightweight refresh: re-score top 200 candidates using overnight moves.
    │  If a ticker has gapped >5% overnight, re-evaluate rank.
    │  Promotes/demotes within top-60 Hot list only. Does not touch Rotation pool.
    │
    ▼
STEP 7: Handoff
    │  Write: config/universe_classification.toml (full ranked lists)
    │  Write: config/etp_map.toml (underlying → ETP mapping, all modes)
    │  Signal: HotScanner and RotationScanner reload on next mode transition.
    │  HotScanner reads its ranked Hot list.
    │  RotationScanner reads its Rotation pool.
```

### Exchange-Specific IBKR Contract Query

```python
# python_brain/universe_scanner.py  (Ouroboros step)

EXCHANGE_QUERY_MAP = {
    "NYSE":    {"secType": "STK", "exchange": "NYSE",   "currency": "USD"},
    "NASDAQ":  {"secType": "STK", "exchange": "NASDAQ", "currency": "USD"},
    "TSX":     {"secType": "STK", "exchange": "TSX",    "currency": "CAD"},
    # Phase 12 adds: SBF, AEB, XETRA, SIX, etc.
    # Phase 13 adds: ASX, TSE, HKEX, etc.
}

async def pull_exchange_contracts(ib, exchange: str) -> list[dict]:
    """Pull all active STK contracts for an exchange via reqContractDetails."""
    contract = Contract()
    contract.secType = EXCHANGE_QUERY_MAP[exchange]["secType"]
    contract.exchange = exchange
    contract.currency = EXCHANGE_QUERY_MAP[exchange]["currency"]
    details = await ib.reqContractDetailsAsync(contract)
    return [
        {
            "con_id":   d.contract.conId,
            "symbol":   d.contract.symbol,
            "exchange": d.contract.exchange,
            "currency": d.contract.currency,
            "long_name": d.longName,
        }
        for d in details
        if d.contract.secType == "STK"
    ]
```

### Hard Filter Details — Phase 11 Thresholds

These values live in `config/config.toml` under `[universe_scanner]` and are
self-tuning via Ouroboros calibration (see Part 2, Section 13).

```toml
[universe_scanner]
price_floor_usd          = 2.00
market_cap_floor_usd_m   = 500.0
avg_daily_volume_floor   = 500_000
avg_spread_bps_ceiling   = 50.0
last_trade_days_max      = 3
regime_bear_exclude      = true      # exclude tickers in HMM bear regime
```

---

## Section 6: HotScanner (renamed from VanguardSniper)

HotScanner occupies the Hot-class slots (40 lines in most modes). Every
ticker in HotScanner receives continuous real-time tick delivery. This is the
highest-resolution scanner. It runs the full signal generation stack.

`universe.rs::UniverseClass::Vanguard` is renamed to `Hot` in Phase 11.
The old name is kept as a deprecated alias for one release cycle.

### Per-Mode Ticker Lists

HotScanner maintains completely separate ticker lists per mode:

```
hot_b:      top 60 MODE B candidates   (LSE ETPs + future European equities)
hot_bp_etp: top 32 MODE B+ ETP slots
hot_bp_us:  top 8  MODE B+ US equity slots
hot_c:      top 60 MODE C US/Canada equities
```

At mode transition, HotScanner atomically swaps its active list.
No overlap, no carryover (except safety-locked carried positions).

### Signal Generation Stack

For each tick on a Hot ticker, HotScanner runs the following pipeline:

```
Tick arrives (u64 nanosecond timestamp)
    │
    ├─► 1. OFI: Order Flow Imbalance
    │       OFI_t = (bid_size_t - ask_size_t) / (bid_size_t + ask_size_t)
    │       Normalise: z_OFI = (OFI_t - OFI_mean) / OFI_std  (rolling 200 ticks)
    │       Ref: Cont, Kukanov & Stoikov (2014) "The Price Impact of Order Book Events"
    │
    ├─► 2. CUSUM Filter with Adaptive Threshold
    │       S_t^+ = max(0, S_{t-1}^+ + (r_t - k·σ_t))
    │       S_t^- = max(0, S_{t-1}^- - (r_t + k·σ_t))
    │       Threshold h = k·σ_t  where σ_t = Kalman-filtered volatility estimate
    │       k = 0.5 (half the expected move). Adaptive: σ_t recalculated each tick.
    │       Signal fires when S_t^+ > h (long) or S_t^- > h (short, MODE C only)
    │
    ├─► 3. VPIN: Volume-Synchronised Probability of Informed Trading
    │       Bucket size V* = daily_volume / 50  (50 buckets per session)
    │       VPIN_t = |V_buy - V_sell| / V*  (rolling 50 buckets)
    │       Ref: Easley, de Prado & O'Hara (2012) "Flow Toxicity and Liquidity"
    │       VPIN > 0.4 flags elevated informed trading — reduces position sizing
    │
    ├─► 4. Kalman Filter for Trend Extraction
    │       State: [price, velocity] — 2-state linear Kalman
    │       Measurement noise R = empirical tick noise (Ouroboros calibrated)
    │       Process noise Q = Ouroboros calibrated per ticker per session
    │       Output: filtered_price, trend_velocity (pps = price per second)
    │       trend_velocity > 0 confirms long signal direction
    │
    ├─► 5. Tick Imbalance Bars (TIBs)
    │       Accumulate signed tick imbalance: Θ_T = Σ b_t  (b_t = sign(Δprice))
    │       New bar forms when |Θ_T| ≥ E[T] × |E[b_t]|  (adaptive threshold)
    │       TIB bar count increasing faster than expected → momentum signal
    │       Ref: de Prado (2018) "Advances in Financial Machine Learning", Ch. 2
    │
    └─► 6. 3-Layer Threshold Adaptation
            Layer 1 (session): thresholds start at Ouroboros-calibrated nightly values
            Layer 2 (intraday): thresholds tighten/loosen based on realised hit rate
                If last 10 signals: hit_rate > 0.65 → tighten by 5%
                If last 10 signals: hit_rate < 0.40 → loosen by 5%
            Layer 3 (tick-level): OFI threshold adapts to current bid/ask imbalance regime
```

### Meta-Labeling Gate

Before any signal from HotScanner is passed to the Executioner, it passes
through a meta-labeling gate. This is a secondary binary classifier that
predicts whether the primary signal will be profitable.

```python
# python_brain/meta_label_gate.py
# Ref: de Prado (2018) Ch. 3

class MetaLabelGate:
    """
    Primary signal: HotScanner fires (direction decided).
    Meta-label question: "Will this primary signal make money?"

    Features used by meta-labeler:
        - z_OFI (current)
        - VPIN (current)
        - trend_velocity (Kalman)
        - time_of_day_fraction (from clock.rs::time_of_day_fraction)
        - regime_state (HMM 3-state: 0=bear, 1=sideways, 2=bull)
        - intraday_vol_ratio (current ATR / 20d avg ATR)
        - spread_z_score (current spread vs 20d history)

    Model: Logistic Regression (fast, interpretable, avoids overfitting).
    Threshold: 0.55 (slightly above random — conservative).
    Retrained: nightly by Ouroboros with last 20 sessions of labelled outcomes.
    """
    def predict(self, features: dict) -> tuple[bool, float]:
        """Returns (pass_gate, confidence)."""
        ...
```

### Underlying Tracking (Safety-Locked)

Every ETP in HotScanner automatically triggers a corresponding underlying
subscription. This is not optional and cannot be disabled.

```
NVD3.L in HotScanner → subscribe NVDA real-time (uses 1 extra line)
TSL3.L in HotScanner → subscribe TSLA real-time (uses 1 extra line)
QQQS.L in HotScanner → subscribe QQQ real-time, INVERT signal direction
MU2.L  in HotScanner → subscribe MU real-time
```

The underlying tick is processed by a lightweight monitor (not full signal
stack). It checks for:
- Pre-market catalyst (underlying >2% move before LSE open)
- Earnings surprise flags via IBKR fundamental data
- Divergence alert (ETP vs underlying correlation breakdown)

Underlying subscriptions are counted in the `safety_locked` line budget.

---

## Section 7: RotationScanner (renamed from ApexScout)

RotationScanner covers the broader universe (200–400 tickers per mode) with
60-second OHLCV snapshots. Tickers rotate in and out of the 60 Rotation
lines based on Thompson Sampling priority scores updated each observation window.

`universe.rs::UniverseClass::Apex` is renamed to `Rotation` in Phase 11.
Old name kept as deprecated alias.

### Two-Phase Observation

Each ticker in the Rotation pool passes through two phases before it can
be promoted to HotScanner or generate a signal directly:

```
PHASE 1: TRIAGE (15–60 second adaptive window)
    │  Lightweight: only volume and price-change checked.
    │  Triage window duration is adaptive:
    │    - High-vol regime (VIX > 20): 15 seconds
    │    - Normal regime:              30 seconds
    │    - Low-vol regime (VIX < 12):  60 seconds
    │  Triage pass criteria (ALL must hold):
    │    • price_change_pct > triage_threshold (Ouroboros calibrated, ~0.3%)
    │    • volume_ratio > 0.8  (current 15s volume vs expected 15s volume)
    │    • Not in CUSUM dead zone (no directional accumulation in last bar)
    │  FAIL: ticker stays in Rotation pool, scored downward.
    │  PASS: enter Phase 2.
    │
    ▼
PHASE 2: ADAPTIVE OBSERVATION WINDOW (60–300 seconds)
    │  Full signal stack: OFI + CUSUM + VPIN + Kalman (same as HotScanner).
    │  Window duration adapts based on signal clarity:
    │    - Clear signal (score > 0.75):   extend to 300 seconds for confirmation
    │    - Moderate signal (0.50–0.75):   standard 120 seconds
    │    - Weak signal (< 0.50):          cut at 60 seconds, return to pool
    │  Promotion criteria: score > 0.70 AND meta-label gate passes.
    │  On promotion: ticker requests a Hot slot from the Allocator.
```

### Multiplicative Composite Score

```python
# python_brain/rotation_scanner.py

def composite_score(ticker_obs: TickerObservation) -> float:
    """
    Multiplicative scoring — if ANY factor is zero, score is zero.
    This prevents weak-on-one-axis tickers from sneaking through.
    """
    ofi_factor       = sigmoid(ticker_obs.z_ofi, k=3.0)        # 0→1
    momentum_factor  = sigmoid(ticker_obs.trend_velocity, k=2.0)
    volume_factor    = min(ticker_obs.volume_ratio, 2.0) / 2.0  # cap at 2x
    regime_factor    = ticker_obs.regime_alignment              # 0, 0.5, or 1.0
    spread_factor    = max(0.0, 1.0 - ticker_obs.spread_bps / 30.0)  # penalty for wide spread

    raw = ofi_factor * momentum_factor * volume_factor * regime_factor * spread_factor
    return float(np.clip(raw, 0.0, 1.0))

def sigmoid(x: float, k: float = 1.0) -> float:
    return 1.0 / (1.0 + np.exp(-k * x))
```

### Thompson Sampling Priority Queue

The Rotation pool is maintained as a Thompson Sampling priority queue.
On each Ouroboros calibration, alpha/beta parameters are updated per ticker.
During the trading session, the queue is sampled every 60 seconds (or
every completed observation window, whichever comes first) to decide which
ticker gets the next available Rotation slot.

```python
# Simplified: each ticker has Beta(alpha, beta)
# alpha = past promotions that resulted in profitable trades + 1
# beta  = past promotions that did not result in profitable trades + 1

def next_rotation_batch(pool: list[TickerBeta], n_open_slots: int) -> list[str]:
    """Select n_open_slots tickers for the next Rotation observation window."""
    return thompson_sample(pool, n_open_slots)
```

### Staggered Overlap Rotation

To avoid clustering all observation windows at the same time (which would
cause bursty IBKR reqMktData requests), observation windows are staggered:

```
Slot  0: starts at T+0s,   ends at T+60s
Slot  1: starts at T+1s,   ends at T+61s
Slot  2: starts at T+2s,   ends at T+62s
...
Slot 59: starts at T+59s,  ends at T+119s
```

This distributes ticker rotations across the full 60-second window and
respects the 10ms pacing rule from `universe.rs::UniverseConfig::mkt_data_pacing_ns`.

### Promotion and Demotion

**Promotion (Rotation → Hot):**
- Composite score > 0.70 for two consecutive observation windows
- Meta-label gate: confidence > 0.55
- Allocator confirms a Hot slot is available
- Once promoted, ticker moves to HotScanner and is removed from active
  Rotation observation. It stays in the Rotation pool for future cycles.

**Demotion (Hot → Rotation):**
- Ticker generates no signal for > 30 minutes in Hot slot
- Signal generated but meta-label confidence < 0.45 on 3 consecutive signals
- Explicitly evicted by Ouroboros nightly re-ranking (replaced by higher-scoring candidate)
- Demoted ticker re-enters Rotation pool with its current alpha/beta intact.

---

## Section 8: Executioner Upgrades

The Executioner from Phases 0–9 handles order lifecycle management. Phase 11
upgrades it with urgency-aware adaptive order types, microstructure-informed
sizing, and a slippage feedback loop.

### Urgency Scoring

Before placing any order, the Executioner computes an urgency score. This
determines which order type to use.

```rust
// rust_core/src/executioner.rs  (extended)

/// Urgency determines order aggressiveness.
/// urgency = signal_strength × alpha_decay / (spread_pct × liquidity_score)
///
/// High urgency (> 0.7): market order or aggressive limit
/// Medium urgency (0.3–0.7): passive limit at mid
/// Low urgency (< 0.3): passive limit at bid (long) or ask (short)
pub fn compute_urgency(
    signal_strength: f64,   // 0.0–1.0 from HotScanner composite score
    alpha_decay_rate: f64,  // estimated alpha half-life in seconds (Ouroboros calibrated)
    elapsed_secs: f64,      // seconds since signal fired
    spread_pct: f64,        // current bid/ask spread as pct of mid
    liquidity_score: f64,   // 0.0–1.0 (normalised ADV)
) -> f64 {
    let alpha_remaining = (-elapsed_secs / alpha_decay_rate).exp();
    let numerator = signal_strength * alpha_remaining;
    let denominator = (spread_pct * (1.0 / liquidity_score.max(0.01))).max(1e-6);
    (numerator / denominator).clamp(0.0, 1.0)
}
```

### Adaptive Order Types

```rust
/// Order type selected by urgency.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AdaptiveOrderType {
    /// urgency > 0.70 and spread < 10bps: market (inside NBBO)
    MarketInsideNbbo,
    /// urgency > 0.70 and spread ≥ 10bps: aggressive limit (cross spread by 1 tick)
    AggressiveLimit,
    /// urgency 0.30–0.70: passive limit at mid
    PassiveLimitAtMid,
    /// urgency < 0.30: passive limit at bid (for buys)
    PassiveLimitAtBid,
    /// HALT override (from exit_engine.rs::ExitOrderType::MarketToLimit)
    EmergencyMarketToLimit,
}

impl AdaptiveOrderType {
    pub fn from_urgency(urgency: f64, spread_bps: f64) -> Self {
        if urgency > 0.70 {
            if spread_bps < 10.0 {
                AdaptiveOrderType::MarketInsideNbbo
            } else {
                AdaptiveOrderType::AggressiveLimit
            }
        } else if urgency >= 0.30 {
            AdaptiveOrderType::PassiveLimitAtMid
        } else {
            AdaptiveOrderType::PassiveLimitAtBid
        }
    }
}
```

### Kyle's Lambda (Price Impact)

Before executing, the Executioner estimates price impact using Kyle's Lambda
to size positions so that impact cost does not exceed the signal's expected alpha.

```python
# python_brain/executioner_impact.py
# Ref: Kyle (1985) "Continuous Auctions and Insider Trading"

def kyle_lambda(price_changes: list[float],
                order_flow_imbalance: list[float]) -> float:
    """
    Estimate Kyle's lambda via OLS regression:
        Δp_t = λ × OFI_t + ε_t
    Returns λ in price-change-per-unit-OFI.
    Uses last 60 minutes of 1-minute bars. Recalculated every 15 minutes.
    """
    from numpy.linalg import lstsq
    import numpy as np
    X = np.array(order_flow_imbalance).reshape(-1, 1)
    y = np.array(price_changes)
    coeff, _, _, _ = lstsq(X, y, rcond=None)
    return float(coeff[0])

def max_shares_by_impact(
    expected_alpha_bps: float,
    kyle_lambda: float,
    bid_ask_spread: float,
    price: float
) -> int:
    """
    Max shares such that impact cost ≤ 50% of expected alpha.
    Impact ≈ λ × shares / (avg_daily_volume × price)
    Solve for shares: shares = 0.5 × alpha_bps/10000 × price / λ
    """
    alpha_price_units = (expected_alpha_bps / 10000.0) * price
    impact_budget = 0.5 * alpha_price_units
    if kyle_lambda <= 0:
        return 10_000  # unconstrained fallback
    max_s = int(impact_budget / (kyle_lambda * price))
    return max(1, max_s)
```

### Almgren-Chriss Optimal Execution

For larger positions (>0.1% of ADV), the Executioner uses Almgren-Chriss
optimal execution to schedule order slices.

```python
# python_brain/almgren_chriss.py
# Ref: Almgren & Chriss (2000) "Optimal Execution of Portfolio Transactions"

def optimal_schedule(
    total_shares: int,
    time_horizon_secs: int,     # e.g., 60s for urgent, 300s for passive
    volatility_per_sec: float,  # σ_s (from Kalman filter output)
    permanent_impact: float,    # γ (Kyle's lambda estimate)
    temporary_impact: float,    # η (bid/ask spread / 2)
    risk_aversion: float = 1e-6 # λ (trader risk aversion)
) -> list[int]:
    """
    Returns a list of share quantities to trade in each time slice.
    Number of slices = time_horizon_secs // 5  (5-second buckets).

    Exponential decay schedule (simplified closed-form):
    X_j = total_shares × sinh(κ(T-t_j)) / sinh(κT)
    where κ = sqrt(λσ² / η)
    """
    import numpy as np
    n_slices = max(1, time_horizon_secs // 5)
    if n_slices == 1:
        return [total_shares]
    kappa = np.sqrt(risk_aversion * volatility_per_sec**2 / max(temporary_impact, 1e-9))
    T = float(n_slices)
    schedule = []
    for j in range(n_slices):
        t_j = float(j)
        frac = np.sinh(kappa * (T - t_j)) / np.sinh(kappa * T)
        schedule.append(max(0, int(total_shares * frac)))
    # Correct rounding error: distribute remainder to last slice
    schedule[-1] += total_shares - sum(schedule)
    return schedule
```

### Partial Fill Handling

```rust
// rust_core/src/executioner.rs

/// State machine for partial fills.
#[derive(Clone, Debug)]
pub enum FillState {
    /// Order placed, no fills yet.
    Pending { order_id: String, target_qty: u32 },
    /// Partially filled.
    Partial {
        order_id: String,
        filled_qty: u32,
        remaining_qty: u32,
        avg_fill_price: f64,
        elapsed_secs: u32,
    },
    /// Fully filled.
    Complete { filled_qty: u32, avg_fill_price: f64 },
    /// Cancelled (partial or no fill).
    Cancelled { filled_qty: u32 },
}

impl FillState {
    /// Decide whether to chase, replace, or cancel a partial fill.
    /// Called every 5 seconds while in Partial state.
    pub fn should_chase(
        &self,
        current_urgency: f64,
        alpha_half_life_secs: f64,
        elapsed_secs: u32,
    ) -> FillAction {
        if let FillState::Partial { remaining_qty, elapsed_secs: fill_elapsed, .. } = self {
            let alpha_remaining = (-(elapsed_secs as f64) / alpha_half_life_secs).exp();
            if alpha_remaining < 0.25 {
                // Alpha mostly decayed — cancel remainder, keep partial fill
                return FillAction::CancelRemainder;
            }
            if current_urgency > 0.70 && *fill_elapsed > 15 {
                // High urgency, still partial after 15s — chase with aggressive limit
                return FillAction::Chase { order_type: AdaptiveOrderType::AggressiveLimit };
            }
        }
        FillAction::Wait
    }
}

pub enum FillAction {
    Wait,
    Chase { order_type: AdaptiveOrderType },
    CancelRemainder,
}
```

### ETP-Specific Execution Rules

ETPs on LSE have specific execution considerations that differ from direct
equity execution:

1. **Creation/redemption spread**: LSE ETPs have an indicative NAV (iNAV)
   published every 15 seconds. The Executioner checks if the current ask
   price deviates from iNAV by >30bps before placing a buy order. If
   premium > 30bps, order is held until premium compresses or T-5 cancels it.

2. **Auction participation**: ETPs cannot be entered during the LSE opening
   auction (07:50–08:00 UTC) or closing auction (16:30–16:35 UTC). The
   Executioner hard-blocks entries in these windows (same as existing
   `clock.rs::is_auction`).

3. **3x leverage volatility buffer**: For 3x ETPs, the Executioner widens
   its initial stop by 1 extra ATR to account for intraday compounding noise.
   This is applied at order placement, not by the Chandelier.

### Slippage Feedback Loop

After every fill, the Executioner records the slippage metric and feeds it
back to the HotScanner and RotationScanner threshold adaptation (Layer 2
of the 3-layer adaptation system in Section 6).

```python
# python_brain/slippage_feedback.py

@dataclass
class FillRecord:
    symbol: str
    signal_price: float      # mid-price at signal fire time
    fill_price: float        # actual fill
    direction: int           # +1 long, -1 short
    timestamp_ns: int

def slippage_bps(record: FillRecord) -> float:
    """Positive = adverse slippage (paid more / received less than expected)."""
    expected = record.signal_price
    actual   = record.fill_price
    move = (actual - expected) * record.direction
    return (move / expected) * 10000.0

class SlippageFeedback:
    """
    Maintains a rolling 20-fill window per ticker.
    If avg_slippage_bps > 5.0 for last 5 fills:
        → tighten urgency threshold by 10% (be less aggressive)
    If avg_slippage_bps < 1.0 for last 10 fills:
        → loosen urgency threshold by 5% (can be more passive)
    Feedback is applied to AdaptiveOrderType urgency cutoffs in RouterConfig.
    """
    def update(self, record: FillRecord) -> None: ...
    def get_urgency_adjustment(self, symbol: str) -> float: ...
```

---

*End of Part 1. Continue in Part 2 for Sections 9–17: Infinite Chandelier
Ladder, T-5 Rule, RiskGate (31 vetoes), Ouroboros Upgrades, AUM Scaling,
Clock Extensions, ISA Compliance Layer, Acceptance Tests, and Phase Gate.*

---

# PART 2 — ADAPTIVE INFRASTRUCTURE (SECTIONS 9-17)

---

## Section 9: Chandelier — Infinite Adaptive Ladder

### Overview

The old 5-rung fixed-percentage Chandelier is replaced by an infinite ATR-based
adaptive ladder. Rungs are not selling targets — they are stop-loss triggers only.
The ladder never stops growing. Stops only ratchet upward, never down.

### Rung Geometry

```
rung_1_distance = clamp(atr_period * base_multiplier, min=0.5 * atr, max=∞)
    where base_multiplier ∈ [1.5, 2.0]

rung_n_distance = rung_(n-1)_distance × decay_factor
    where decay_factor = 0.85

Floor: 0.5 × ATR (no rung can be tighter than half ATR)
Cap: none — rungs extend infinitely as price climbs

Rung n stop level = high_water_mark - rung_n_distance
```

The rung index is a `u32`, not `u8`. The system handles 4 billion rungs without
overflow. In practice a position on a strong runner may reach rung 30-50 before exit.

### 8 Adaptive Multipliers

Each multiplier adjusts the base stop distance. They are applied multiplicatively
to `rung_n_distance`:

```
final_distance = rung_n_distance
    × m1_atr_scale
    × m2_time_of_day
    × m3_regime
    × m4_ticker_specific
    × m5_profit_scale
    × m6_momentum_decay
    × m7_volume_exhaustion
    × m8_correlation_contagion
```

**M1 — ATR Scale** (`m1_atr_scale`):
Normalises for instrument volatility. Wider in high-vol environments.
```
m1 = current_atr / baseline_atr_60d
clamp(m1, 0.7, 2.0)
```

**M2 — Time of Day** (`m2_time_of_day`):
U-shaped: widest at open and close, tightest at midday.
```
minutes_from_open = (now - session_open_time).as_minutes()
session_length_minutes = (session_close_time - session_open_time).as_minutes()
midpoint = session_length_minutes / 2.0
distance_from_midpoint = abs(minutes_from_open - midpoint) / midpoint
m2 = 1.0 + 0.4 * distance_from_midpoint   // range [1.0, 1.4]
```

**M3 — Regime** (`m3_regime`):
Wider during trending regimes (let runners run), tighter during choppy/ranging.
```
match hmm_regime:
    Bull/Trending  → m3 = 1.20
    Sideways/Choppy → m3 = 0.85
    Bear/Risk-Off  → m3 = 0.70
```

**M4 — Ticker Specific** (`m4_ticker_specific`):
Per-ticker calibration from MAE/MFE analysis (Ouroboros nightly).
```
m4 = (ticker_mae_90th_percentile / universe_mae_median)
clamp(m4, 0.8, 1.5)
```
Default 1.0 for new tickers with insufficient trade history.

**M5 — Profit Scale** (`m5_profit_scale`):
As unrealised profit accumulates, tighten the stop proportionally to protect gains.
```
pnl_ratio = unrealised_pnl / initial_risk
m5 = 1.0 / (1.0 + 0.15 * pnl_ratio)
clamp(m5, 0.5, 1.0)
// At 2× initial risk returned: m5 ≈ 0.77 (23% tighter)
// At 5× initial risk returned: m5 ≈ 0.57 (43% tighter)
```

**M6 — Momentum Decay** (`m6_momentum_decay`):
If price momentum acceleration turns negative (second derivative), tighten.
```
acceleration = roc_5min_now - roc_5min_prev
if acceleration < -threshold:
    m6 = 1.0 + abs(acceleration) * 2.0
    clamp(m6, 1.0, 1.5)  // tighten by up to 50% when momentum fading
else:
    m6 = 1.0
```

**M7 — Volume Exhaustion** (`m7_volume_exhaustion`):
If trading volume relative to baseline dries up, the move may be ending — tighten.
```
vol_ratio = current_5min_volume / avg_5min_volume_60d
if vol_ratio < 0.3:
    m7 = 0.75  // volume collapsed — tighten 25%
elif vol_ratio < 0.6:
    m7 = 0.88  // volume fading — tighten 12%
else:
    m7 = 1.0
```

**M8 — Correlation Contagion** (`m8_correlation_contagion`):
When a correlated position is stopped out, contagion risk increases — tighten
remaining correlated positions' stops.
```
// Triggered by a stop-out event in portfolio
for each remaining_position:
    corr = dcc_garch_correlation(stopped_ticker, remaining_position.ticker)
    if corr > 0.7:
        m8 = 0.80  // tighten 20% on highly correlated positions
    elif corr > 0.5:
        m8 = 0.90  // tighten 10%
    else:
        m8 = 1.0
// m8 decays back to 1.0 linearly over 30 minutes after the stop-out event
```

### Exit Urgency Score

Computed in real-time alongside the Chandelier stop. Multiplicative combination
of five signals. Score in [0.0, 1.0].

```
urgency = (ofi_reversal       * 0.30)
        + (momentum_decay_signal  * 0.25)
        + (volume_exhaustion_signal * 0.20)
        + (time_pressure_signal  * 0.15)
        + (regime_deterioration_signal * 0.10)
```

| Signal Component | Computation |
|---|---|
| `ofi_reversal` | OFI sign flip + magnitude vs 95th percentile baseline |
| `momentum_decay_signal` | 5-min ROC 2nd derivative, normalised 0-1 |
| `volume_exhaustion_signal` | 1.0 − vol_ratio (clamped 0-1) |
| `time_pressure_signal` | Exponential ramp in final 30 min of session |
| `regime_deterioration_signal` | HMM posterior shift toward Bear regime |

**Urgency to action mapping:**

```
urgency < 0.30        → No change to stops
urgency 0.30-0.59     → Tighten ALL active stops by 30% (override multipliers)
urgency 0.60-0.79     → Tighten ALL active stops by 50%
urgency >= 0.80       → Immediate market exit (bypass rung ladder entirely)
```

### T-5 Rule (Adaptive Session-End)

The T-5 rule triggers between 3 and 15 minutes before session end, adapting
based on volatility and open position count.

```
// Dynamic timing
if atr_percentile_rank > 0.80 or open_positions > 2:
    t5_trigger_secs = 900  // 15 min before close
elif atr_percentile_rank > 0.50:
    t5_trigger_secs = 600  // 10 min before close
else:
    t5_trigger_secs = 180  // 3 min before close (low vol, 1 position)
```

**T-5 Decision Tree:**

```
IF secs_to_close <= t5_trigger_secs:
    FOR each open position:
        pnl_pct = (current_price - entry_price) / entry_price * 100
        threshold = mega_runner_threshold(ticker)  // adaptive 99th percentile

        IF pnl_pct <= threshold:
            T5Action::SellAll  // sell entire position at market
        ELSE:
            // MEGA RUNNER path
            harvest_pct = initial_cost_basis_pct + 0.50 * profit_pct
            T5Action::MegaRunnerCarry { harvest_pct }
            // harvest initial investment + 50% of profits
            // rest carries overnight with infinite ladder intact
```

**Mega Runner Threshold (Adaptive):**

```
threshold_pct = 99th_percentile(ticker_60session_intraday_returns)
// Computed nightly by Ouroboros
// Default: 102% until sufficient trade history (60 sessions)
// Updated per-ticker, per-mode (direct equity thresholds differ from ETP thresholds)
```

The threshold exists per ticker per mode. A 3x ETP like NVD3.L has a much higher
threshold (because 5% intraday on NVDA = 15% on NVD3.L) than a direct equity
like AVGO (1x). Ouroboros tracks both distributions separately.

### Rust ExitStrategy Trait Extension

File: `rust_core/src/chandelier_v2.rs`

```rust
pub trait ExitStrategy: Send + Sync {
    /// Compute the current stop price for this position.
    fn compute_stop(
        &self,
        pos: &Position,
        high_water_mark: f64,
        atr: f64,
        regime: RegimeState,
        multipliers: &ChandelierMultipliers,
    ) -> f64;

    /// Return the current rung index (u32 — infinite ladder, no u8 cap).
    fn compute_rung(&self, pos: &Position, high_water_mark: f64, atr: f64) -> u32;

    /// Compute the exit urgency score [0.0, 1.0].
    fn exit_urgency(
        &self,
        pos: &Position,
        ofi: f64,
        volume_ratio: f64,
        regime_delta: f64,
        secs_to_close: u64,
    ) -> f64;

    /// Check if position qualifies for mega-runner carry.
    fn mega_runner_check(
        &self,
        pos: &Position,
        threshold_pct: f64,
    ) -> MegaRunnerDecision;

    /// Decide action at T-5 trigger.
    fn t5_action(
        &self,
        pos: &Position,
        secs_to_close: u64,
        t5_trigger_secs: u64,
    ) -> T5Action;
}

pub struct ChandelierMultipliers {
    pub m1_atr_scale: f64,
    pub m2_time_of_day: f64,
    pub m3_regime: f64,
    pub m4_ticker_specific: f64,
    pub m5_profit_scale: f64,
    pub m6_momentum_decay: f64,
    pub m7_volume_exhaustion: f64,
    pub m8_correlation_contagion: f64,
}

pub enum T5Action {
    SellAll,
    MegaRunnerCarry { harvest_pct: f64 },
    Hold,  // secs_to_close > t5_trigger_secs — not yet triggered
}

pub enum MegaRunnerDecision {
    NotYet,  // pnl_pct below threshold
    Carry {
        harvest_shares: u64,   // shares to sell at T-5
        remaining_shares: u64, // shares carried overnight with infinite ladder
    },
}

/// Immutable rule: stops ONLY move up. Ratchet enforced here, not at call sites.
pub fn ratchet_stop(old_stop: f64, new_stop: f64) -> f64 {
    old_stop.max(new_stop)  // strictly monotone increasing
}
```

---

## Section 10: RiskGate — 31 Adaptive Veto Checks

### Rename

`risk_arbiter.rs` is replaced by `risk_gate.rs`. The `RiskArbiter` struct becomes
`RiskGate`. All call sites updated. Old `VetoReason` enum extended in-place (no
breaking changes to variants that already exist).

### Design Principles

1. **Cheapest first**: computational cost orders the checks. Instant lookups gate
   before expensive market-impact calculations.
2. **Fail fast**: first veto exits immediately. No wasted computation.
3. **Self-tuning**: every veto tracks its own precision and net value.
4. **Six ordered groups**: 31 checks total.

### Veto Groups

**Group 1 — Instant, No Computation (6 checks)**

Evaluated from in-memory state. Zero I/O, zero arithmetic.

| # | Check | Veto Condition |
|---|-------|----------------|
| G1-1 | ISA contribution limit hard stop | `isa_used_this_year + order_value > 20_000 GBP` |
| G1-2 | Fully funded check | `account_balance < min_order_value` |
| G1-3 | No short selling | `direction == Short` (ISA restriction — always) |
| G1-4 | EOD lockout | `secs_to_close < eod_lockout_secs` (config default: 60s) |
| G1-5 | Cooldown period | `now - last_trade_time[ticker] < cooldown_secs` (per ticker) |
| G1-6 | Max daily trade count | `daily_trades >= max_daily_trades` (config) |

**Group 2 — Regime / Macro, Cached (4 checks)**

Values cached from cross-asset macro module. Refresh every 5 minutes.

| # | Check | Veto Condition |
|---|-------|----------------|
| G2-1 | VIX circuit breaker | `vix > vix_halt_threshold` (config default: 40.0) |
| G2-2 | Credit spread breaker | `hy_spread_bps > hy_spread_threshold` (config default: 600) |
| G2-3 | Market breadth breaker | `advance_decline_ratio < breadth_threshold` (config default: 0.25) |
| G2-4 | Regime risk budget | `regime_risk_score > regime_budget_limit` (HMM state × ATR rank) |

**Group 3 — Microstructure, Real-Time (5 checks)**

Computed from the live order book and tick feed at decision time.

| # | Check | Veto Condition |
|---|-------|----------------|
| G3-1 | Spread veto (adaptive U-shaped) | `spread_pct > spread_limit(time_of_day)` |
| G3-2 | Volume participation veto | `order_size > 0.02 × rolling_5min_volume` (2% cap) |
| G3-3 | Market impact (Almgren-Chriss) | `impact_cost_bps > max_impact_bps` (config) |
| G3-4 | Price staleness | `now - last_tick_time > staleness_threshold_ms` (default: 5000ms) |
| G3-5 | Order book imbalance | `bid_volume / ask_volume < imbalance_threshold` (buying into thin ask) |

Spread limit for G3-1 is U-shaped by time-of-day:
```
spread_limit(t) = base_limit * (1.0 + 0.5 * time_of_day_factor(t))
// Widest in first 30 min and last 30 min of session (M2 mirrors this logic)
```

**Group 4 — Position-Level (5 checks)**

| # | Check | Veto Condition |
|---|-------|----------------|
| G4-1 | Meta-label conviction | `meta_label_proba < 0.55` |
| G4-2 | Expected profit floor | `expected_pnl_bps < min_expected_pnl_bps` (config) |
| G4-3 | Risk-reward minimum | `reward / risk < 1.5` |
| G4-4 | Stop distance sanity | `stop_distance_pct < min_stop_pct OR > max_stop_pct` |
| G4-5 | Position concentration | `new_position_notional / portfolio_nav > max_single_position_pct` |

**Group 5 — Portfolio-Level (8 checks)**

| # | Check | Veto Condition |
|---|-------|----------------|
| G5-1 | Max simultaneous positions (N_eff) | `N_eff > max_positions_eff` where `N_eff = N / (1 + (N-1) × avg_corr)` |
| G5-2 | Daily drawdown YELLOW | `daily_dd <= -3.0%`: 50% Kelly reduction, HotScanner entries only |
| G5-3 | Daily drawdown ORANGE | `daily_dd <= -5.0%`: 25% Kelly reduction, no new entries, manage existing only |
| G5-4 | Daily drawdown RED | `daily_dd <= -8.0%`: full halt, safety-locked positions only, manual restart required |
| G5-5 | Portfolio VaR | `95_var > var_limit_pct × nav` (parametric, 1-day horizon) |
| G5-6 | CVaR (Expected Shortfall) | `95_cvar > cvar_limit_pct × nav` |
| G5-7 | DCC-GARCH correlation spike | `avg_dynamic_correlation > corr_spike_threshold` |
| G5-8 | Sector concentration with ETP look-through | `sector_exposure_pct > sector_limit` (ETPs decomposed to underlying sectors; HHI + PC1 sub-checks within) |

**Group 6 — Cross-Exposure and Final ISA (3 checks)**

| # | Check | Veto Condition |
|---|-------|----------------|
| G6-1 | ETP-underlying overlap | Same underlying held via ETP and direct (Smart Router mapping) |
| G6-2 | FX exposure limit | `total_non_gbp_notional > fx_limit_pct × nav` |
| G6-3 | ISA eligibility final gate | Ticker confirmed on HMRC recognised exchange list (cached, weekly refresh) |

### 4-Tier Drawdown System

Absolute daily drawdown thresholds (measured from session open NAV):

```
daily_dd_limit_yellow = -3.0%   // YELLOW trigger
daily_dd_limit_orange = -5.0%   // ORANGE trigger
daily_dd_limit_red    = -8.0%   // RED trigger
```

```
Tier 1 — NORMAL  (daily_dd > -3.0%):
    Full operation. No restrictions.

Tier 2 — YELLOW  (daily_dd <= -3.0% and > -5.0%):
    Reduce Kelly fraction by 50% on all new entries.
    HotScanner entries only (RotationScanner blocked).
    Telegram: SYSTEM SHIFT alert (YELLOW drawdown, -3%).

Tier 3 — ORANGE  (daily_dd <= -5.0% and > -8.0%):
    Reduce Kelly fraction to 25% on all new entries.
    No new entries permitted (manage existing positions only).
    Chandelier continues on open positions.
    Telegram: SYSTEM SHIFT alert (ORANGE drawdown, -5%).

Tier 4 — RED     (daily_dd <= -8.0%):
    Full system halt. No new entries. No Chandelier modifications.
    Safety-locked positions held until natural exit only.
    Manual restart required (operator sets Redis flag: risk_gate:resume).
    Telegram: SYSTEM SHIFT alert (RED — manual restart required, -8%).
```

**Hysteresis — prevents oscillation near tier boundaries:**
```
Downgrade requires crossing a buffer BELOW the threshold.

YELLOW → NORMAL:  daily_dd must recover above -2.7% (not -3.0%)
ORANGE → YELLOW:  daily_dd must recover above -4.5% (not -5.0%)
RED → ORANGE:     manual only (operator confirms recovery)

hysteresis_buffer = 10% of tier_threshold magnitude
downgrade_threshold = tier_threshold + hysteresis_buffer
```

### AUM Scaling

Position sizing reads actual ISA balance from `reqAccountSummary()` at session
open and every 60 minutes thereafter.

```
Kelly fraction: f* = (edge / variance) × aum_scaling_factor(current_aum)

aum_scaling_factor(aum):
    <= £10k:   1.00   // 100% Kelly — aggressive growth mode
    £10k-£25k: interpolate 1.00 → 0.70 (logarithmic)
    £25k-£50k: interpolate 0.70 → 0.50 (logarithmic)
    £50k-£100k: interpolate 0.50 → 0.35 (logarithmic)
    > £100k:   0.35   // capital preservation priority
```

```rust
pub fn aum_scaling_factor(aum: f64) -> f64 {
    match aum {
        a if a <= 10_000.0 => 1.00,
        a if a < 25_000.0 => {
            let t = (a.ln() - 10_000f64.ln())
                  / (25_000f64.ln() - 10_000f64.ln());
            1.00 - t * 0.30
        }
        a if a < 50_000.0 => {
            let t = (a.ln() - 25_000f64.ln())
                  / (50_000f64.ln() - 25_000f64.ln());
            0.70 - t * 0.20
        }
        a if a < 100_000.0 => {
            let t = (a.ln() - 50_000f64.ln())
                  / (100_000f64.ln() - 50_000f64.ln());
            0.50 - t * 0.15
        }
        _ => 0.35,
    }
}
```

**ADV cap:** No single order may exceed 1% of 5-minute rolling volume for that ticker.
If the Kelly-sized order would breach the ADV cap, TWAP-slice it:

```
if order_shares > adv_cap_shares:
    n_slices = ceil(order_shares / adv_cap_shares)
    slice_interval_secs = max(30, available_secs_in_session / n_slices)
    // Submit slices at slice_interval_secs apart via Executioner TWAP mode
    // Never slice past T-5 trigger — reduce order size if time insufficient
```

### Self-Tuning Veto Tracking

Each of the 31 vetoes accumulates runtime statistics. Evaluated nightly by Ouroboros.

```rust
pub struct VetoStats {
    pub veto_id: VetoId,
    pub fire_count: u64,          // number of times this veto fired
    pub prevented_losses: f64,    // sum of losses prevented (counterfactual)
    pub missed_profits: f64,      // sum of profits missed by blocking good trades
    pub net_value: f64,           // prevented_losses - missed_profits
    pub precision: f64,           // fraction of fires that prevented actual losses
}
```

A veto with `net_value < 0 AND precision < 0.50` for 30 consecutive sessions
is flagged in the Ouroboros post-mortem PDF with a plain-English recommendation
to raise its threshold. Automatic adjustment is disabled — human review required.
Shadow book tracks every vetoed trade outcome (see Section 14).

---

## Section 11: Ouroboros — Component Calibration Pipeline

### Pipeline Execution Order

Ouroboros runs at 23:50 ET (04:50 London next day). Full execution target: under
8 minutes on EC2 c7i-flex.large.

```
STEP 1: Universe Discovery (per mode)
    For each active mode (A / B+ / C / DARK):
        - Query IBKR reqContractDetails for ISA-eligible tickers
        - Run yfinance bulk pull for adjusted closes (60-session window)
        - Check GraniteShares / Leverage Shares site for new ETP listings
        - Score every ticker via ASER (Adaptive Signal-to-Execution Ratio)
        - Filter: remove tickers with ADV < 1M USD equivalent
        - Output: ranked universe list per mode → universe_YYYYMMDD.toml

STEP 2: Performance Analysis (trade review, factor updates)
    - Pull all today's closed trades from SQLite WAL
    - Update Bayesian win-rate posterior per ticker (Beta distribution)
    - Update factor weights via Bayesian Ridge regression (60-day rolling)
    - Update HMM regime posteriors (3-state: bull / bear / sideways)
    - Run PELT changepoint detection on equity curve
        If changepoint detected:
            → Flag all weight updates as HIGH CONFIDENCE
            → Trigger full recalibration (skip incremental updates)
            → Telegram alert: "STRUCTURAL BREAK DETECTED — weights reset"
    - Output: updated per-ticker priors → Redis + SQLite

STEP 3: Component Calibration (NEW — one sub-step per component)

    3a. HotScanner Calibration:
        - Recompute OFI baselines (mean, sigma per ticker per time-of-day slot)
        - Recalibrate CUSUM h threshold (target: false positive rate < 2%)
        - Refit VPIN bucket size (tau) using today's tick volume
        - Update Kalman filter Q/R matrices from residual analysis
        - Retrain meta-label model (LogisticRegression on 60-day feature matrix)
        - Output: hot_scanner_params_YYYYMMDD.toml

    3b. RotationScanner Calibration:
        - Update triage thresholds (RVOL floor, OFI floor) from percentile shifts
        - Update Thompson Sampling Beta priors (alpha/beta) from today's outcomes
        - Adjust promotion threshold (minimum ASER score to enter hot tier)
        - Output: rotation_scanner_params_YYYYMMDD.toml

    3c. Executioner Calibration:
        - Update per-ticker execution profiles (alpha decay half-lives)
        - Refit Kyle's Lambda per ticker from today's bid-ask + OFI data
        - Update slippage model coefficients from today's fill analysis
        - Flag tickers where average slippage exceeded 3-sigma for manual review
        - Output: executioner_params_YYYYMMDD.toml

    3d. Chandelier Calibration:
        - Recalculate ATR per ticker (14-period EWM)
        - Run MAE/MFE analysis on all closed positions:
            MAE 90th percentile  → m4_ticker_specific update
            MFE distribution     → mega-runner threshold update (99th pct)
        - Update rung spacing per ticker (decay factor, floor)
        - Recompute t5_trigger_secs per ticker (volatility + position count model)
        - Output: chandelier_params_YYYYMMDD.toml

    3e. RiskGate Calibration:
        - Update VetoStats for all 31 vetoes (fire count, net value, precision)
        - Refit DCC-GARCH correlation matrices (60-session window)
        - Update regime-conditional position limits
        - Flag underperforming vetoes (net_value < 0, precision < 0.50 for 30 sessions)
        - Update sector exposure limits based on regime (trending = looser, choppy = tighter)
        - Output: risk_gate_params_YYYYMMDD.toml

    3f. Router Calibration:
        - Check for new ETP listings (scrape GraniteShares + Leverage Shares)
        - Validate existing ETPs via IBKR reqContractDetails (detect delistings)
        - Update routing_table.toml with any changes
        - Calculate tracking error: ETP vs 3x underlying (flag if > 5% deviation)
        - Output: routing_table_YYYYMMDD.toml

STEP 4: Scoring and Slot Allocation (per mode, ranked lists)
    - Apply Bayesian Ridge + HMM + EWA blended weights
    - Score every ticker in universe (ASER score)
    - Produce ranked lists: top-N for hot tier per mode
    - Allocate Thompson Sampling exploration budget
    - Output: slot_allocation_YYYYMMDD.toml

STEP 5: Pre-Market Update (runs again at 07:45 London)
    - Pull overnight price data (yfinance pre-market)
    - Re-score top-50 tickers with fresh data
    - Promote / demote from hot tier based on overnight moves
    - Evaluate pending intent queue — remove stale intents
    - Output: morning_primer_YYYYMMDD.toml (feeds 07:00 PDF)

STEP 6: PDF Reports
    - 21:00 UTC: Post-Mortem PDF (Section 14)
    - 07:00 UTC: Morning Primer PDF (Section 14)
```

### Adaptive Weight System

Four weight systems run in parallel. Final ASER score is their blended output.

**Bayesian Ridge (primary, 45% weight):**
- Feature matrix: 60-day rolling window, nightly update
- Features: OFI momentum, RVOL rank, regime state, time-of-day, sector, ATR rank
- Prior: N(0, alpha) with hyperparameter alpha updated via evidence maximisation

**HMM Regime Conditioning (25% weight):**
- 3-state HMM (bull / bear / sideways) fit on daily returns
- Regime-conditional mean returns used to gate score
- A strong signal in a bear regime is discounted by 40%

**Fixed-Share EWA (online learning, 20% weight):**
- Exponential Weighted Average with fixed-share mixing parameter eta = 0.05
- Updates every trade: profitable trade increases weight of contributing features
- Handles concept drift without full model retraining

**Optional LambdaMART (Learning-to-Rank, 10% weight when active):**
- Activated only if >= 500 closed trades in history (sufficient training data)
- Trained on features → relative ranking (not absolute score)
- Objective: NDCG@10 on held-out 20% validation set
- When inactive, the 10% is absorbed by EWA

**PELT Changepoint Detection:**
- Algorithm: PELT (Pruned Exact Linear Time) on rolling equity curve, penalty = BIC
- If changepoint detected in last 5 trading days:
  - All Bayesian Ridge weights reset to prior (start fresh)
  - HMM re-fit with shorter lookback (20 days instead of 60)
  - Telegram SYSTEM SHIFT alert fired

### Data Sources

| Source | Data Used | Refresh Cadence |
|---|---|---|
| IBKR API | Real-time bars, ticks, contract details, account summary | Continuous / nightly |
| SQLite WAL | All trade history, outcomes, fill data | Real-time write, nightly read |
| Redis | Current positions, regime state, pending intents | Continuous |
| yfinance | Bulk adjusted closes, pre-market data | Nightly + 07:45 |
| GraniteShares scrape | New / delisted ETP listings | Weekly (Monday) |
| Leverage Shares scrape | New / delisted ETP listings | Weekly (Monday) |
| VIX, DXY, HY spreads | Macro indicators | Every 5 minutes |
| Advance/decline ratio | Market breadth | Every 5 minutes |
| Earnings calendar | Blackout window per ticker | Nightly |
| Short interest | SI% float | Weekly |
| FinBERT NLP | Overnight news sentiment per ticker | Nightly |
| HMRC exchange list | ISA eligibility gate | Monthly |

---

## Section 12: AUM Scaling

### Reading Live Balance

At system boot and every 60 minutes during a session, the engine calls
`reqAccountSummary()` and reads `NetLiquidationByCurrency` for GBP.
This becomes `current_aum` used in all position sizing calculations.

```rust
pub struct AumState {
    pub current_aum: f64,
    pub scaling_factor: f64,
    pub last_updated: DateTime<Utc>,
    pub mode: AumMode,
}

pub enum AumMode {
    AggressiveGrowth,   // <= £10k
    Balanced,           // £10k-£50k
    Preservation,       // £50k-£100k
    CapitalProtection,  // > £100k
}
```

### Kelly Position Sizing with AUM Adaptation

```
Full Kelly:     f* = edge / variance
Adjusted Kelly: f_adj = f* × aum_scaling_factor(current_aum)
Notional:       notional = f_adj × current_aum
Shares:         shares = floor(notional / entry_price)

// Hard caps applied AFTER Kelly (in order):
shares = min(shares, adv_cap_shares)                         // ADV cap
shares = min(shares, isa_remaining / entry_price)            // ISA annual limit
notional = min(notional, max_single_position_pct × current_aum)  // concentration cap
```

### Growth Trajectory

| Balance | Mode | Kelly Fraction | Practical Daily Target |
|---|---|---|---|
| £10k | Aggressive Growth | 100% | 0.5% net |
| £25k | Balanced | 77% | 0.4% net |
| £50k | Balanced | 70% | 0.35% net |
| £100k | Preservation | 50% | 0.3% net |
| £200k+ | Capital Protection | 35% | 0.2% net |

Lower percentage targets at higher AUM reflect risk preservation. Compounding
still produces superior absolute PnL because the base is larger.

### TWAP Slicing

When Kelly sizing produces an order exceeding 1% of 5-minute rolling volume:

```python
def twap_slice(order_shares: int, adv_cap_shares: int, secs_remaining: int):
    n_slices = math.ceil(order_shares / adv_cap_shares)
    slice_interval = max(30, secs_remaining // (n_slices * 2))
    slices, remaining = [], order_shares
    for i in range(n_slices):
        sz = min(adv_cap_shares, remaining)
        slices.append(TwapSlice(shares=sz, delay_secs=i * slice_interval))
        remaining -= sz
    return slices
```

Maximum slicing window: never extends past T-5 trigger. If insufficient time
remains to place all slices, reduce order size to fit within available time.

---

## Section 13: New Rust and Python Modules

### New Rust Modules

| Module | File | Purpose |
|---|---|---|
| SmartRouter v2 | `rust_core/src/router.rs` | Full routing table with RouteTarget, RouteDecision, ETP health checks, hot-reload from TOML |
| Thompson Allocator | `rust_core/src/allocator.rs` | Thompson Sampling line allocator, AllocationMode enum, exploration budget management |
| HotScanner | `rust_core/src/hot_scanner.rs` | OFI computation, CUSUM adaptive h, VPIN bucket, Kalman filter, meta-label gate (extends VanguardSniper logic) |
| RotationScanner | `rust_core/src/rotation_scanner.rs` | Two-phase observe-then-promote logic, triage thresholds, adaptive window, promotion queue |
| Executioner V2 | `rust_core/src/executioner_v2.rs` | Urgency scoring, adaptive order types, Almgren-Chriss market impact, TWAP slicer, partial fill state machine |
| Chandelier V2 | `rust_core/src/chandelier_v2.rs` | Infinite ATR-based ladder, 8 multipliers, ratchet enforcer, exit urgency, T-5 logic, mega-runner |
| RiskGate | `rust_core/src/risk_gate.rs` | 31 veto checks (replaces risk_arbiter.rs), 4-tier drawdown, AUM scaling, VetoStats self-tuning |
| ModeController | `rust_core/src/mode_controller.rs` | Mode state machine (A → B+ → C → DARK), transition events, mode-conditional parameter sets |
| UniverseScanner | `rust_core/src/universe_scanner.rs` | Daily ISA universe discovery, ASER scoring, routing table application per mode |
| Clock Extension | `rust_core/src/clock.rs` | Add MODE B+ / C / DARK timing functions, adaptive T-5 trigger, extended session detection |

#### Key New Rust Types

```rust
// rust_core/src/mode_controller.rs
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SystemMode {
    ModeA,      // ETP Blitz (LSE open 08:00-16:30 London)
    ModeBPlus,  // Triple overlap (14:30-16:30 London, ETP + US equity)
    ModeC,      // Equity Sweep (LSE closed 16:30-08:00)
    ModeDark,   // Weekend / holiday — reduced scanning
}

pub struct ModeTransitionEvent {
    pub from: SystemMode,
    pub to: SystemMode,
    pub triggered_at: DateTime<Utc>,
    pub reason: TransitionReason,
}

pub enum TransitionReason {
    LseOpen, LseClosed, UsOpen, UsClosed,
    RiskHalt, ManualOverride,
}

// rust_core/src/hot_scanner.rs
pub struct HotScannerParams {
    pub ofi_baseline_mu: f64,
    pub ofi_baseline_sigma: f64,
    pub cusum_h: f64,
    pub vpin_bucket_size: f64,
    pub kalman_q: f64,
    pub kalman_r: f64,
    pub meta_label_threshold: f64,  // default 0.55
}

// rust_core/src/executioner_v2.rs
pub struct ExecutionProfile {
    pub ticker_id: TickerId,
    pub alpha_decay_half_life_secs: f64,
    pub kyle_lambda: f64,
    pub avg_slippage_bps: f64,
    pub preferred_order_type: AdaptiveOrderType,
}
```

### New Python Modules

| Module | File | Purpose |
|---|---|---|
| Universe Scanner | `ouroboros/universe_scanner.py` | Daily universe discovery pipeline (yfinance + IBKR + ETP scraping) |
| Component Calibrator | `ouroboros/component_calibrator.py` | Per-component nightly calibration (Steps 3a-3f) |
| Adaptive Scorer | `ouroboros/adaptive_scorer.py` | Bayesian Ridge + HMM + EWA + optional LambdaMART weight blending |
| PDF Generator | `ouroboros/pdf_generator.py` | 21:00 post-mortem + 07:00 morning primer via PyMuPDF fitz.Story (zero system deps) |
| Telegram Reporter | `ouroboros/telegram_reporter.py` | 4-type alert system via python-telegram-bot async |

```python
# ouroboros/pdf_generator.py — implementation pattern
# PyMuPDF only — NOT weasyprint, NOT wkhtmltopdf, NOT pdfkit (all need system deps)
import fitz  # PyMuPDF

def generate_postmortem(date: str, data: PostmortemData) -> bytes:
    html = render_postmortem_html(data)  # jinja2 template → HTML string
    story = fitz.Story(html)
    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    mediabox = fitz.paper_rect("a4")
    while True:
        device = writer.begin_page(mediabox)
        more, _ = story.place(mediabox + (-36, -36, -36, -36))  # 36pt margins
        story.draw(device)
        writer.end_page()
        if not more:
            break
    writer.close()
    return buf.getvalue()
```

---

## Section 14: Telegram Alerts and PDF Reports

### Telegram Alert System

Library: `python-telegram-bot` v20+ (async).
Bot token: environment variable `TELEGRAM_BOT_TOKEN`.
Chat ID: config key `telegram.chat_id`.

**Exactly 4 alert types. No risk veto spam. No debug noise.**

---

**Alert 1 — TARGET ACQUIRED (new position entered)**

```
🟢 TARGET ACQUIRED
Mode:       MODE A (ETP Blitz)
Ticker:     NVD3.L
Source:     HotScanner (OFI spike, CUSUM threshold crossed)
Vehicle:    ETP (3x leverage, underlying: NVDA)
Fill:       £42.18 (slippage: +0.4 bps vs mid)
Size:       £1,847 (12.3% of portfolio)
ATR mult:   1.72x
Port heat:  34% VaR utilisation
```

---

**Alert 2 — CHANDELIER SEVERED (position exited via stop)**

```
🔵 CHANDELIER SEVERED
Mode:    MODE A
Ticker:  QQQ3.L
Exit:    £187.43
Net PnL: +2.8% (+£52)
Time:    1h 23min
Reason:  Rung #7 breached (M7 volume exhaustion tightened by 25%)
```

---

**Alert 3 — MEGA-RUNNER CARRY (T-5 carry decision)**

```
🌟 MEGA-RUNNER CARRY
Ticker:    NVD3.L
Current:   +148% intraday (threshold: +102%)
Harvest:   £2,100 (initial £1,847 + 50% of profit)
Carrying:  £312 remaining (infinite ladder continues overnight)
Mode:      Entering MODE C carry position
```

---

**Alert 4 — SYSTEM SHIFT (mode or regime change)**

```
🔄 SYSTEM SHIFT
Event:     LSE closed → MODE C activated
HMM:       Bull → Sideways (posterior: 0.67)
Drawdown:  NORMAL (daily -0.4%, limit 2.0%)
Action:    16 ETP rotating lines freed, 60 equity lines allocated
```

Also fires for: VIX circuit breaker, drawdown tier escalation (YELLOW / ORANGE / RED),
structural break detection (PELT changepoint), manual halt or resume.

### PDF Post-Mortem (21:00 UTC, 3 pages)

Generated by `ouroboros/pdf_generator.py` using PyMuPDF. Delivered to configured
Telegram chat as document attachment.

**Page 1 — Global Scorecard + Shadow Book**

- Global Scorecard: PnL by mode, win rate, average hold time, slippage summary,
  ISA utilisation (GBP used / £20,000 limit)
- Shadow Book: up to 10 vetoed trades that would have been profitable.
  Columns: Ticker | Veto Group | Veto Reason | Projected PnL | Confidence Score.
  Purpose: quantify opportunity cost per veto type. Feeds VetoStats precision calc.

**Page 2 — Executioner Audit + Chandelier Audit**

- Executioner Audit: scatter plot of expected vs actual slippage per trade.
  Outliers (> 3-sigma) highlighted. Kyle's Lambda accuracy by ticker.
- Chandelier Audit: table of exits — did the stop trigger prematurely?
  MFE at exit vs maximum attainable MFE. Rung efficiency score per position.

**Page 3 — Ouroboros Prescriptions**

Plain-English parameter changes recommended for tomorrow. Format:

```
PRESCRIPTION #1  (HIGH CONFIDENCE — changepoint detected):
    Component: HotScanner
    Change: Raise CUSUM h for QQQ3.L from 3.2 to 4.1
    Reason: 6 false positives in last 5 sessions; precision 43%

PRESCRIPTION #2  (MEDIUM CONFIDENCE):
    Component: RiskGate — Veto G3-1 (spread)
    Change: Widen spread limit from 0.4% to 0.6% in first 15 min
    Reason: Net value -£84 over 10 sessions; 3 profitable entries blocked
```

### PDF Morning Primer (07:00 UTC, 3 pages)

**Page 1 — Macro Weather + New ISA Discoveries**

- HMM regime: current state, posterior probabilities, direction since yesterday
- VIX, DXY, HY spreads, advance/decline ratio — traffic-light dashboard
- New tickers added to ISA universe overnight (from Ouroboros Step 1)
- Tickers demoted (ADV dropped, ETP delisted, earnings blackout)

**Page 2 — Vanguard Draft (Top 50 + Promotions / Demotions)**

- Ranked list: top 50 tickers by ASER score post-calibration
- Promotions marked as [UP] (entered hot tier since yesterday)
- Demotions marked as [DOWN] (dropped from hot tier)
- Pending intents listed with age and decay status

**Page 3 — Smart Router Preferences**

- Today's routing table summary (any ETP changes from overnight scrape)
- Tracking error flags (ETPs diverging > 5% from 3x underlying)
- Spread warnings for tickers with historically wide pre-open spreads
- Mode A slot pre-allocation (expected hot / rotating split at 08:00)

---

## Section 15: Acceptance Tests

Target: 72 tests across 8 groups. Rust tests in `rust_core/src/phase11_tests.rs`,
Python tests in `tests/test_phase11_*.py`.

### Router Tests (12 tests)

| # | Test | Expected |
|---|------|----------|
| R-01 | ETP preference: NVDA signal, NVD3.L healthy | Route to NVD3.L, leverage=3 |
| R-02 | ETP health check fail: NVD3.L spread > 3%, stale tick | Fall back to NVDA direct |
| R-03 | Cost comparison: ETP cheaper after spread-adjusted basis | ETP preferred |
| R-04 | Cost comparison: direct cheaper after vol-drag adjustment | Direct equity preferred |
| R-05 | ISA gate: ticker not on HMRC list | REJECTED (G6-3) |
| R-06 | New ETP discovered overnight: AVGO3.L listed | Routing updated, direct subscription freed |
| R-07 | ETP delisted: NVD3.L removed | Route updates to NVDA direct, zero downtime |
| R-08 | Commodity ETP (3OIL.L): no underlying to scan | Route to 3OIL.L, no signal chaining |
| R-09 | Cross-exposure block: NVD3.L open, NVDA signal | REJECTED: CrossExposureBlocked |
| R-10 | Inverse ETP mutual exclusion: QQQ3.L open, QQQS.L signal | REJECTED: InverseEtpConflict |
| R-11 | Routing table hot-reload from TOML | New routes active within 100ms, no restart |
| R-12 | Proptest: 1,000 random (signal, portfolio) pairs | No panic, always returns valid RouteDecision |

### Allocator Tests (9 tests)

| # | Test | Expected |
|---|------|----------|
| A-01 | MODE A, 0 positions | locked=0, hot+rotating=100, total=100 |
| A-02 | MODE A, 3 positions | locked=3, hot+rotating=97, total=100 |
| A-03 | MODE B, 0 positions | total=100, all to direct equities |
| A-04 | Mode transition A→B at 16:30 | ETP lines unsubscribed, equity lines subscribed atomically |
| A-05 | Mode transition B→A at 08:00 | Equity lines unsubscribed, ETP lines subscribed, intents evaluated |
| A-06 | Thompson Sampling exploration | Tickers with low trial count appear in hot tier periodically |
| A-07 | Pending intent queue: 11th intent added | Oldest intent evicted (FIFO, max=10) |
| A-08 | Pending intent: age > 12h | Auto-expired before 08:00 evaluation |
| A-09 | Proptest: 10,000 random (positions, mode, regime) inputs | Total lines always <= 100, no panics |

### HotScanner Tests (9 tests)

| # | Test | Expected |
|---|------|----------|
| H-01 | OFI computation: synthetic tick stream | Matches hand-calculated reference ± 0.01% |
| H-02 | OFI time-of-day normalisation | Spike at 08:05 not flagged (high baseline); same magnitude at 11:00 is flagged |
| H-03 | CUSUM adaptive h: 30 days of false positives | h raised by Ouroboros nightly calibration |
| H-04 | VPIN bucket: high volume day | Buckets fill faster, detection latency decreases |
| H-05 | Kalman filter: position tracks true signal under noise | RMSE < 0.5x signal amplitude |
| H-06 | Meta-label gate: proba = 0.52 | Signal BLOCKED (below 0.55 threshold) |
| H-07 | Meta-label gate: proba = 0.71 | Signal PASSED |
| H-08 | Meta-label retrain: 60-day feature matrix | AUC > 0.58 on 20% holdout |
| H-09 | 50 tickers firing simultaneously | No race conditions, all processed under 10ms total |

### RotationScanner Tests (9 tests)

| # | Test | Expected |
|---|------|----------|
| RS-01 | Triage: RVOL below threshold | Observation only, no promotion |
| RS-02 | Triage: RVOL + OFI above threshold | Enters promotion queue |
| RS-03 | Promotion then deterioration | Demoted after 3 underperforming observation batches |
| RS-04 | Cold tier, 2 consecutive strong signals | Promoted to hot tier |
| RS-05 | Adaptive window: VIX > 20 | Observation window shrinks to 15s (faster triage) |
| RS-06 | Adaptive window: VIX < 12 | Observation window extends to 60s (fewer false entries) |
| RS-07 | Thompson Sampling: win | Alpha increment recorded correctly in Beta prior |
| RS-08 | Thompson Sampling: loss | Beta increment recorded correctly in Beta prior |
| RS-09 | 60-second batch cycle: 300 tickers | All scanned within line budget, no overrun |

### Chandelier Tests (12 tests)

| # | Test | Expected |
|---|------|----------|
| C-01 | Infinite ladder: compute rung 200 | u32 correct, geometric spacing, no overflow |
| C-02 | ATR-based rung 1: entry £10, ATR £0.50, mult 1.7 | First stop at £10 - (0.50 × 1.7) = £9.15 |
| C-03 | Rung geometry: rung 2 vs rung 1 distance | rung_2_distance = rung_1_distance × 0.85, floor at 0.5 ATR |
| C-04 | Ratchet enforcer: stop attempted to move down | Old stop returned unchanged (max semantics) |
| C-05 | M5 profit scale: PnL = 5x initial risk | m5 ≈ 0.57 applied (43% tighter) |
| C-06 | M7 volume exhaustion: vol_ratio = 0.2 | m7 = 0.75 applied (25% tighter) |
| C-07 | M8 correlation contagion: NVD3.L stopped, QQQ3.L corr=0.8 | QQQ3.L stop tightened 20% |
| C-08 | Exit urgency = 0.85 | Immediate market exit (ladder bypassed) |
| C-09 | T-5: 5 min to close, PnL = +45% (below 102% threshold) | T5Action::SellAll |
| C-10 | T-5: 5 min to close, PnL = +148% (above 102% threshold) | T5Action::MegaRunnerCarry { harvest_pct: initial + 50% profit } |
| C-11 | Adaptive threshold: ticker 99th pct = 87% | threshold = 87% not default 102% |
| C-12 | T-5 timing: ATR rank > 0.80, 3 positions open | t5_trigger_secs = 900 (15 min) |

### RiskGate Tests (12 tests)

| # | Test | Expected |
|---|------|----------|
| RG-01 | G1-1 ISA limit: £19,900 used, order £200 | REJECTED |
| RG-02 | G1-3 short sell attempt | REJECTED always (no ISA short selling) |
| RG-03 | G2-1 VIX = 45 | REJECTED (circuit breaker) |
| RG-04 | G3-1 spread U-shape: 08:02 vs 11:00 | 08:02 limit wider; 11:00 tighter — both computed correctly |
| RG-05 | G3-2 volume: order = 3% of 5-min volume | REJECTED (exceeds 2% cap) |
| RG-06 | G4-1 meta-label: proba = 0.53 | REJECTED |
| RG-07 | G4-3 risk-reward: reward / risk = 1.2 | REJECTED (below 1.5 minimum) |
| RG-08 | G5-1 N_eff: 4 positions, avg_corr=0.75, limit=3 | Correct N_eff computed, checked against limit |
| RG-09 | 4-tier drawdown: cross YELLOW (-3%) then ORANGE (-5%) | YELLOW: 50% Kelly, HotScanner only; ORANGE: 25% Kelly, no new entries |
| RG-10 | Hysteresis: ORANGE recovery | Recovery confirmed only above -4.5% (not -5.0%) |
| RG-11 | AUM scaling: balance £10k, f*=0.20 | f_adj = 0.20 × 1.00 = 0.20 |
| RG-12 | AUM scaling: balance £100k, f*=0.20 | f_adj = 0.20 × 0.35 = 0.07 |

### Ouroboros Tests (9 tests)

| # | Test | Expected |
|---|------|----------|
| O-01 | Universe discovery: ADV filter | Tickers below 1M USD ADV excluded from ranked list |
| O-02 | Chandelier calibration: MAE analysis updates m4 | m4_ticker_specific updated per ticker after analysis |
| O-03 | PELT changepoint: planted break in synthetic equity curve | Break detected within ±2 sessions of planted date |
| O-04 | PELT trigger: Bayesian Ridge weights reset | Prior confirmed reset, HMM lookback shortened to 20 days |
| O-05 | EWA online learning: profitable trade | Feature weight increment proportional to return size |
| O-06 | LambdaMART gate: < 500 trades | EWA used, LambdaMART not instantiated |
| O-07 | LambdaMART gate: >= 500 trades | Trained, NDCG@10 > 0.60 on holdout |
| O-08 | Per-mode rankings: MODE A vs MODE C | Scores diverge due to regime conditioning |
| O-09 | Full pipeline end-to-end: 30-day synthetic history | Completes under 8 minutes, no exceptions |

### Telemetry Tests (6 tests)

| # | Test | Expected |
|---|------|----------|
| T-01 | TARGET ACQUIRED alert format | All 8 fields present and correctly formatted |
| T-02 | CHANDELIER SEVERED: rung number + multiplier | Rung index and triggering multiplier name in message |
| T-03 | MEGA-RUNNER CARRY: harvest and carry amounts | Numbers match position arithmetic exactly |
| T-04 | SYSTEM SHIFT: mode name + HMM state | Correct mode string and HMM state label |
| T-05 | PDF post-mortem: shadow book populated | Up to 10 entries, projected PnL computed per vetoed trade |
| T-06 | PDF generation: both PDFs complete | PyMuPDF runs without system deps, file size > 0 bytes |

---

## Section 16: Files Summary

| File | Type | Est. Lines | Purpose |
|---|---|---|---|
| `rust_core/src/router.rs` | NEW | 280 | SmartRouter v2 — RouteTarget, RouteDecision, ETP health checks, hot-reload |
| `rust_core/src/allocator.rs` | NEW | 220 | Thompson Sampling line allocator, AllocationMode, exploration budget |
| `rust_core/src/hot_scanner.rs` | NEW | 350 | OFI, CUSUM adaptive h, VPIN, Kalman filter, meta-label gate |
| `rust_core/src/rotation_scanner.rs` | NEW | 240 | Two-phase observe/promote, triage thresholds, adaptive window, priority queue |
| `rust_core/src/executioner_v2.rs` | NEW | 380 | Urgency scoring, adaptive order types, Almgren-Chriss, TWAP slicer, partial fill FSM |
| `rust_core/src/chandelier_v2.rs` | NEW | 420 | Infinite ATR ladder, 8 multipliers, ratchet enforcer, urgency score, T-5, mega-runner |
| `rust_core/src/risk_gate.rs` | NEW | 500 | 31 veto checks, 4-tier drawdown, VetoStats, AUM scaling, self-tuning flags |
| `rust_core/src/mode_controller.rs` | NEW | 180 | Mode state machine, transition events, mode-conditional parameter sets |
| `rust_core/src/universe_scanner.rs` | NEW | 260 | ISA universe discovery, ASER scoring, routing table application per mode |
| `rust_core/src/clock.rs` | MODIFIED | +80 | MODE B+/C/DARK timing, adaptive T-5 trigger, extended session detection |
| `rust_core/src/risk_arbiter.rs` | DELETED | -480 | Replaced entirely by risk_gate.rs |
| `rust_core/src/smart_router.rs` | RENAMED | — | Superseded by router.rs (extended) |
| `rust_core/src/line_allocator.rs` | RENAMED | — | Superseded by allocator.rs (extended) |
| `rust_core/src/phase11_tests.rs` | NEW | 620 | All 72 acceptance tests (Rust groups) |
| `ouroboros/universe_scanner.py` | NEW | 340 | Universe discovery pipeline (yfinance + IBKR + scraping) |
| `ouroboros/component_calibrator.py` | NEW | 480 | Per-component nightly calibration (Steps 3a-3f) |
| `ouroboros/adaptive_scorer.py` | NEW | 360 | Bayesian Ridge + HMM + EWA + LambdaMART weight blending |
| `ouroboros/pdf_generator.py` | NEW | 290 | Post-mortem + morning primer via PyMuPDF fitz.Story |
| `ouroboros/telegram_reporter.py` | NEW | 160 | 4-type Telegram alert system (python-telegram-bot async) |
| `ouroboros/ouroboros.py` | MODIFIED | +200 | Integrate Steps 3 (component calibration) and 5 (pre-market update) |
| `config/config.toml` | MODIFIED | +60 | AUM scaling bands, veto thresholds, Chandelier params, ADV cap |
| `config/routing_table.toml` | MODIFIED | +30 | Generated nightly, ETP health check flags |
| `config/chandelier_params.toml` | NEW | 80 | Per-ticker ATR, MAE/MFE results, mega-runner thresholds |
| `config/risk_gate_params.toml` | NEW | 120 | 31 veto thresholds, DCC-GARCH matrices, VetoStats baselines |
| `tests/test_phase11_ouroboros.py` | NEW | 180 | Python-side Ouroboros tests (O-01 to O-09) |
| `tests/test_phase11_telemetry.py` | NEW | 120 | Telemetry tests (T-01 to T-06) |
| `PHASE_11_GATE.md` | NEW | 40 | Checkpoint gate: all criteria must be met before Phase 12 begins |

**Estimated total new code: ~5,700 lines (Rust + Python)**
**Net after deletions and renames: ~5,200 lines added**

---

## Section 17: Estimated Effort

Phase 11 is the largest single phase in the AEGIS V2 build. It delivers the full
adaptive infrastructure that all subsequent phases build upon.

| Work Stream | Est. Hours | Key Deliverables |
|---|---|---|
| Core infrastructure (router v2, allocator, mode controller, universe scanner) | 30h | Routing table, line allocation, mode state machine, universe discovery pipeline |
| Adaptive scoring system (Bayesian Ridge, HMM, EWA, LambdaMART, PELT) | 15h | Weight blending, changepoint detection, per-mode ranked lists |
| Chandelier V2 (infinite ladder, 8 multipliers, T-5, mega-runner) | 10h | ATR-based geometry, ratchet enforcer, exit urgency score |
| RiskGate — 31 vetoes (Groups 1-6, 4-tier drawdown, AUM scaling, self-tuning) | 12h | All 31 checks, VetoStats, hysteresis, TWAP ADV slicing |
| Ouroboros pipeline extension (Steps 3 and 5, component calibration) | 15h | Steps 3a-3f calibration logic, all data source integrations, nightly schedule |
| Telemetry (Telegram 4-type alerts, PDF post-mortem, PDF morning primer) | 10h | Alert templates, shadow book tracking, PDF charts via matplotlib + PyMuPDF |
| Acceptance tests (72 tests across 8 groups, Rust + Python) | 15h | All groups green, proptest invariants verified |
| Integration and wiring (engine.rs, main.py, Docker, config files) | 10h | End-to-end tick-to-trade with all new modules wired and tested |
| **Total** | **117h** | **Full adaptive infrastructure operational** |

### Phase Gate Criteria (PHASE_11_GATE.md)

Before Phase 12 begins, ALL of the following must be verified and signed off:

- [ ] All 72 acceptance tests green (zero failures, zero skipped)
- [ ] Chandelier V2 proptest: stops never move down across 100,000 random states
- [ ] RiskGate proptest: total market data lines never exceed 100 across 10,000 random states
- [ ] AUM scaling: position sizing verified correct at £10k, £25k, £50k, £100k balances
- [ ] Ouroboros full pipeline run completes under 8 minutes on EC2 c7i-flex.large
- [ ] Telegram: all 4 alert types fire and format correctly in test channel
- [ ] PDFs: post-mortem + morning primer generated, opened, visually spot-checked
- [ ] No regressions in Phase 0-10 test suite
- [ ] 5 consecutive paper trading days in MODE A + MODE C with no system halts
- [ ] Shadow book tracking operational (vetoed trade counterfactuals computing correctly)

---

## PHASE 11 SPEC COMPLETE

---

## Section 18: Triage Amendments (Post-Gemini Audit 2026-03-09)

The following amendments supersede or extend earlier sections based on the adversarial review.
All amendments are binding. Implementation must follow these rulings exactly.

### Amendment A1: Underlying Tracking — Open Positions Only (RULING A1)

**Supersedes:** Section 6, "Underlying Tracking (Safety-Locked)"

The original spec states: "Every ETP in HotScanner automatically triggers a corresponding
underlying subscription." This is REVOKED.

**New rule:** Underlying equity subscriptions are activated ONLY when an open position
exists in the corresponding ETP.

```
ETP in HotScanner, no position → NO underlying subscription (0 extra lines)
ETP with open position         → 1 underlying subscription (safety-locked)
```

If 3 ETP positions are open: 6 total lines consumed (3 ETP + 3 underlying).
Remaining 94 lines = pure scanning budget. This resolves the 100-line feasibility
issue across Phases 11 + 12 + 13 simultaneously.

`HotScanner::subscribe_underlying()` is called only from `Executioner::on_fill_confirmed()`.
`HotScanner::unsubscribe_underlying()` is called only from `ExitEngine::on_position_closed()`.

### Amendment A2: Ouroboros Failure Fallback (RULING A2)

**Supersedes:** Section 11, Ouroboros pipeline timeout behaviour

If `pipeline_complete` flag is not set in Redis by 22:55 UTC:
1. Engine reads the flag at 22:55 UTC (poll every 30s from 22:45)
2. If not set: `drawdown_tier = ORANGE` (no new entries)
3. Send Telegram 🔄 SYSTEM SHIFT: "OUROBOROS_TIMEOUT — entries halted until RESUME"
4. Morning primer PDF page 1 header: "⚠️ CALIBRATION FAILED — ENTRIES HALTED"
5. System resumes ONLY on manual `RESUME` command sent to Telegram bot
6. Carry positions continue to be monitored; Chandelier holds last-known stops

### Amendment A3: SubscriptionManager — Mutex ACK Protocol (RULING A3)

**Supersedes:** Section 4, Allocator line management

**New component:** `SubscriptionManager` singleton (Rust, `rust_core/src/subscription_manager.rs`)

```rust
pub struct SubscriptionManager {
    active_lines: Arc<Mutex<HashMap<TickerId, Symbol>>>,
    pending_cancel_ack: Arc<Mutex<HashSet<TickerId>>>,
    signal_pause: Arc<AtomicBool>,
}

impl SubscriptionManager {
    /// Cancel a subscription and wait for ACK before returning.
    /// ACK = tickSnapshotEnd callback OR 200ms timeout.
    pub async fn cancel_and_wait(&self, ticker_id: TickerId) -> Result<(), SubError>;

    /// Subscribe after cancel is confirmed.
    pub async fn subscribe(&self, contract: Contract) -> Result<TickerId, SubError>;

    /// Perform a full mode transition swap.
    /// Pauses signal generation for duration of swap (max 2,000ms).
    pub async fn mode_transition_swap(
        &self,
        remove: Vec<TickerId>,
        add: Vec<Contract>,
    ) -> Result<(), SubError>;
}
```

Rules:
- `signal_pause = true` for entire duration of `mode_transition_swap()`
- Each cancel: wait for `tickSnapshotEnd` OR 200ms timeout before next cancel
- Each subscribe: dispatched only after preceding cancel confirmed
- Total transition budget: 2,000ms; log WARNING if exceeded
- active_lines count NEVER exceeds 100 at any point; panic if invariant violated

### Amendment A4: Partial Fill Dust Guard (RULING A4)

**Supersedes:** Section 8, Partial Fill Handling

Add to `FillState` logic in `executioner_v2.rs`:

```rust
/// Minimum position size gate (applied BEFORE Kelly sizing submits an order).
///
/// RATIONALE: IBKR charges £1.00 minimum commission per trade. A £500 buy +
/// £500 sell = £2.00 round-trip = 0.40% of position. Target gross capture is
/// ~0.30–0.50% per trade — transaction costs consume the entire expected profit.
/// Minimum viable position at 0.13% commission = £1,500 (£2.00 / 1,500 = 0.13%).
///
/// Do NOT confuse with MINIMUM_VIABLE_GBP (post-partial-fill dust guard below).
/// This gate fires BEFORE order submission; dust guard fires AFTER partial fill.
const MINIMUM_ENTRY_GBP: f64 = 1500.0;  // configurable in config.toml

/// Gate applied in Kelly sizer before any order is submitted.
/// Returns None if position would be below minimum viable size.
pub fn pre_entry_size_gate(kelly_gbp: f64) -> Option<f64> {
    if kelly_gbp < MINIMUM_ENTRY_GBP {
        None  // skip_trade logged as BELOW_MIN_SIZE in WAL
    } else {
        Some(kelly_gbp)
    }
}

const MINIMUM_VIABLE_GBP: f64 = 500.0;  // post-partial-fill dust guard

/// After cancelling remainder, check filled quantity.
/// If filled value < MINIMUM_VIABLE_GBP: immediately issue market exit.
pub fn post_cancel_check(
    filled_qty: u32,
    mid_price: f64,
    currency: Currency,
    gbp_rate: f64,
) -> PostCancelAction {
    let value_gbp = (filled_qty as f64 * mid_price) / gbp_rate;
    if value_gbp < MINIMUM_VIABLE_GBP {
        PostCancelAction::ImmediateMarketExit  // log as DUST_LIQUIDATION in WAL
    } else {
        PostCancelAction::Hold
    }
}
```

### Amendment P0: P0 Critical Fixes Required Before Phase 11 Code

The following P0 items from GEMINI_TRIAGE.md must be implemented in Phase 11:
- P0-1: SubscriptionManager (Amendment A3 above) — 8h
- P0-2: Underlying tracking scope (Amendment A1 above) — 2h
- P0-3: Ouroboros failure fallback (Amendment A2 above) — 3h
- P0-4: Partial fill dust guard (Amendment A4 above) — 3h
- P0-5: IBKR rate limit chunking in UniverseScanner — 4h
- P0-6: NaN / divide-by-zero guard in Kelly + Chandelier — 2h
- P0-7: Redis noeviction policy in docker-compose.yml — 1h
- P0-8: Boot reconciliation (reqPositions vs WAL) — 4h

### Amendment P1: P1 High Fixes Required Before Phase 11 Gate

- P1-1: CUSUM secondary EWMA mean drift fix (α=0.02, ~50 ticks)
- P1-2: Kalman Q intraday scaling (Q_eff = Q_base × vol_ratio_tod)
- P1-3: VPIN adaptive bucket size (V* = 5d_median_ADV / 50)
- P1-4: OFI absolute depth context feature added to meta-labeler
- P1-5: Kyle's Lambda WLS (weighted by 1/σ²)
- P1-6: Chandelier rung floor = max(0.5 ATR, 1.5 × spread_ema)
- P1-7: Volume veto G3-2 suspended for first 10 minutes of session
- P1-8: Graceful SIGTERM shutdown handler
- P1-9: IBKR reconnect with 30s retry, 10-minute timeout
- P1-10: Order ID idempotency (hash-based, WAL-backed)
- P1-11: Heartbeat Telegram ping every 4 hours
- P1-12: DST timezone handling via chrono_tz (no hardcoded UTC offsets)
  - APScheduler pre-LSE jobs: `timezone="Europe/London"` (NOT `"UTC"`)
  - MODE B+ boundary: `mode_b_plus_end_utc()` using `ZoneInfo("Europe/London")`
  - `from_utc_secs()` ModeA arm: `s >= 23*3600 || s < 8*3600` (wrapping, NOT `&&`)
- P1-13: Meta-label F1-optimal threshold via PR curve in Ouroboros
- P1-14: Corporate actions RiskGate veto G1-CORP (48h ex-date check)
- P1-15: Stale tick guard (reject ticks > 5s old)

### Amendment: Updated Phase 11 Gate Criteria

**Replaces** the Phase Gate Criteria in Section 17.

Before Phase 12 begins, ALL of the following must be verified and signed off:

- [ ] All 72 original acceptance tests green
- [ ] P0-1: SubscriptionManager proptest — 10,000 transitions, lines ≤ 100 always
- [ ] P0-2: Underlying tracking scope — verified zero underlying subscriptions without open position
- [ ] P0-3: Ouroboros timeout test — timeout fires, ORANGE tier activates, Telegram sent
- [ ] P0-4a: Pre-entry size gate — Kelly output < £1,500 skips trade (BELOW_MIN_SIZE in WAL)
- [ ] P0-4b: Dust guard test — partial fill < £500 triggers immediate market exit
- [ ] P0-5: UniverseScanner rate limit — 50 req/batch, 10s sleep, no IBKR Error 162
- [ ] P0-6: NaN guard — zero-variance input returns 0 order size, no panic
- [ ] P0-7: Redis config verified — maxmemory-policy noeviction
- [ ] P0-8: Boot reconciliation — WAL vs IBKR mismatch handled correctly
- [ ] P1-1 through P1-15: all unit + integration tests pass
- [ ] SIGTERM handler: verified positions preserved on kill signal
- [ ] Heartbeat: 4-hour ping verified in Telegram
- [ ] DST test: verify mode boundaries are correct on DST transition days
- [ ] Corporate action veto: test ticker with 48h ex-date is blocked
- [ ] Chandelier floor: verify stop ≥ spread in low-volatility simulation
- [ ] 5 paper trading days: no system halts, no 100-line violations, no IBKR disconnects

---

*Section 18 added 2026-03-09 — Gemini Adversarial Audit Integration*
*All amendments binding. See GEMINI_TRIAGE.md for full rationale.*
*Updated 2026-03-09 — Claude Self-Analysis Triage: clock.rs ModeA boundary fix, MODE B+ LSE*
*summer close DST note, pre-entry size gate (£1,500), APScheduler Europe/London timezone note.*
*See AEGIS_SELF_ANALYSIS_TRIAGE.md for full rationale.*
