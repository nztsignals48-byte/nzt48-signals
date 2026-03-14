# PHASE 13 — ASIA-PACIFIC SESSION + DARK MODE

## Prerequisite
Phase 12 APPROVED. All tests green. European direct equities operational
in MODE B/B+. UniverseScanner, SubUniverseAllocator, ExchangeProfile, and
FxRateTable modules stable. Currency enum covers GBP, USD, EUR, CHF, SEK,
NOK, DKK, PLN. 100-line IBKR invariant holds across full European expansion.

---

## Core Architecture Change

**CURRENT (Phase 12):** The system operates across three active modes:
MODE B (Europe, 08:00-14:30 UTC), MODE B+ (Hybrid overlap, 14:30-16:30 UTC),
and MODE C (Americas, 16:30-21:00 UTC). The window from 21:00-08:00 UTC is
entirely idle. No trading occurs. Ouroboros fires at a fixed time (23:50 ET
= ~04:50 UTC) with no defined "DARK window" boundary. Asia-Pacific markets
— TSE, HKEX, ASX, SGX, KRX, NZX — are completely ignored despite being
ISA-eligible and actively traded during those idle hours.

**NEW:** Phase 13 completes the global 24/5 system by adding:

1. **MODE A (Asia-Pacific):** 23:00-08:00 UTC. All ISA-eligible Asian
   exchanges. 100 IBKR lines dedicated to Asian equities during this window.
   Six exchanges with distinct microstructure: TSE (Tokyo), HKEX (Hong Kong),
   ASX (Sydney), SGX (Singapore), KRX (Seoul), NZX (Auckland).

2. **DARK MODE (21:00-23:00 UTC):** A protected 2-hour window with NO
   trading whatsoever. Ouroboros runs its full nightly pipeline here:
   universe discovery for all 5 modes, component calibration, PDF report
   generation. DARK mode is a mandatory firewall between MODE C's close
   and MODE A's open.

**Full 5-mode system after Phase 13:**

```
MODE A  (Asia-Pac):   23:00 → 08:00 UTC  (9h)
MODE B  (Europe):     08:00 → 14:30 UTC  (6.5h)
MODE B+ (Hybrid):     14:30 → 16:30 UTC  (2h)
MODE C  (Americas):   16:30 → 21:00 UTC  (4.5h)
DARK:                 21:00 → 23:00 UTC  (2h)
                                          ─────
Total coverage:       24h continuous (5 days/week) ✓
```

**Impact on existing modes:**
- MODE B, MODE B+, MODE C: Unchanged except overnight carry handling
  for mega-runners that survive a MODE C → DARK → MODE A transition.
- DARK: Replaces the previously ad-hoc Ouroboros schedule. Ouroboros
  now fires at 21:00 UTC (MODE C close) and MUST complete within 2 hours.
- AllocationMode enum gains two new variants: ModeA and Dark.

---

## Section 1: MODE A Architecture Overview

MODE A is a full trading session with identical infrastructure to MODE B
and MODE C. The same HotScanner + RotationScanner strategies execute. The
same Chandelier trailing stop, RiskGate, WAL, and Executioner operate.
What changes is the exchange set, session boundaries, and exchange-specific
execution profiles.

### Session Boundaries

```
MODE C close:   21:00 UTC  → Flatten all Americas positions (unless mega-runner)
DARK open:      21:00 UTC  → Ouroboros fires immediately
DARK close:     23:00 UTC  → Ouroboros must have completed
MODE A open:    23:00 UTC  → Load Asian universe lists, subscribe lines
MODE A close:   08:00 UTC  → Flatten all Asian positions (unless mega-runner)
MODE B open:    08:00 UTC  → Load European universe lists, subscribe lines
```

### MODE A Transition Protocol

**MODE C → DARK (21:00 UTC):**
1. Flatten all MODE C positions at 20:55 UTC (T-5 relative to 21:00 close).
2. Mega-runners (defined below) exempted from flatten — carried forward.
3. Cancel all outstanding MODE C orders.
4. Unsubscribe all non-mega-runner MODE C lines.
5. Emit `ModeTransition { from: ModeC, to: Dark, ts_utc: ... }` WAL event.
6. Ouroboros pipeline begins immediately.

**DARK → MODE A (23:00 UTC):**
1. Assert Ouroboros completed (pipeline_complete flag in Redis). If not
   complete after 2h: log CRITICAL, proceed with last-known universe lists.
2. Load MODE A universe lists from Ouroboros output.
3. Subscribe IBKR lines for top-N Asian equities per line allocator.
4. Emit `ModeTransition { from: Dark, to: ModeA, ts_utc: ... }` WAL event.
5. T-5 adaptive timing initialises for each Asian exchange.

**MODE A → MODE B (08:00 UTC):**
1. Flatten all MODE A positions at T-5 relative to each exchange's close.
   - TSE closes 06:00 UTC → T-5 at 05:55 UTC (flatten TSE positions)
   - HKEX closes 08:00 UTC → T-5 at 07:55 UTC (flatten HKEX positions)
   - SGX closes 09:00 UTC → T-5 at 08:55 UTC (overlap with MODE B open)
   - ASX closes 06:00 UTC → T-5 at 05:55 UTC
2. Mega-runners exempted from flatten — see Section 4.
3. At 08:00 UTC: unsubscribe all Asian non-mega-runner lines.
4. Load MODE B universe lists, subscribe European + LSE ETP lines.
5. SGX and NZX positions still open at 08:00: retain safety-locked line
   until those exchanges close (SGX 09:00, NZX 05:45).

### Line Budget for MODE A

```
Total IBKR lines available:          100
Safety-locked carried positions:      variable (typically 0-3)
Available for Asian scanning:         100 - carried_count

HotScanner Asia:     top 30 (Hot tier, continuous ticks)
RotationScanner Asia: remaining 70 - carried_count (Rotation tier, 60s snapshots)
```

The 100-line invariant is identical to all previous modes. The
proptest from Phase 11 (extended in Phase 12) extends again to cover
MODE A. At no point in the MODE A session can active_lines() > 100.

---

## Section 2: Asian Exchange Coverage

All exchanges are ISA-eligible per HMRC Recognised Stock Exchanges
(confirmed Tables 1 and 2). Taiwan (TWSE/GTSM), China domestic (SSE/SZSE),
and India (BSE/NSE) are explicitly excluded — they are NOT in HMRC Tables.

| Exchange | Code | Key Constituents | Trading Hours (UTC) | Currency | ISA Table |
|----------|------|-----------------|---------------------|----------|-----------|
| Tokyo Stock Exchange | TSE | Toyota, Sony, SoftBank, Fast Retailing, Nintendo, Keyence | 00:00-02:30, 03:30-06:00 | JPY | Table 2 |
| HKEX | HKEX | HSBC, AIA, Meituan, Alibaba (9988.HK), Tencent, BYD | 01:30-04:00, 05:00-08:00 | HKD | Table 2 |
| Australian Securities Exchange | ASX | BHP, Commonwealth Bank, CSL, Rio Tinto, NAB, ANZ | 00:00-06:00 | AUD | Table 1 |
| Singapore Exchange | SGX | DBS, OCBC, UOB, Grab, Sea, CapitaLand | 01:00-09:00 | SGD | Table 1 |
| Korea Exchange | KRX | Samsung (GDR preferred), SK Hynix, LG Energy, Hyundai | 00:00-06:30 | KRW | Table 2 |
| New Zealand Exchange | NZX | Fisher & Paykel, Auckland Airport, Mainfreight, Spark | 21:00-05:45 (next UTC) | NZD | Table 1 |

**Total estimated additions:** ~1,500-2,500 ISA-eligible Asian tickers
across all exchanges after hard filters (liquidity, market cap, price
floor, recently traded, not suspended, FX drag acceptable).

**Key ISA exclusions (must be enforced by ISA eligibility gate):**
- Taiwan (TWSE): NOT in HMRC Tables → blocked
- China SSE/SZSE: NOT in HMRC Tables → blocked
- India BSE/NSE: NOT in HMRC Tables → blocked
- Samsung Electronics Co. (KRX direct): prefer SMSN.IL (GDR on LSE IOB)
  or KRX direct only if GDR unavailable

### TSE Session Structure

TSE has a mandatory lunch break producing a split session:

```
TSE Morning Session:  09:00-11:30 JST = 00:00-02:30 UTC
TSE Lunch Break:      11:30-12:30 JST = 02:30-03:30 UTC  ← NO TRADING
TSE Afternoon Session: 12:30-15:30 JST = 03:30-06:00 UTC

Total TSE active:     6 hours across two sessions
Lunch break:          1 hour (no new entries, maintain safety-locked stops)
```

HKEX has a similar lunch break:

```
HKEX Morning Session: 09:30-12:00 HKT = 01:30-04:00 UTC
HKEX Lunch Break:     12:00-13:00 HKT = 04:00-05:00 UTC  ← NO TRADING
HKEX Afternoon Session: 13:00-16:00 HKT = 05:00-08:00 UTC
```

### ETP Coverage for Asian Underlyings

The ETP-first routing principle from Phase 11/12 applies to Asia.
If a leveraged ETP exists on LSE for an Asian underlying, the ETP wins.

```
UNDERLYING (Exchange)       ETP EXISTS?         ROUTE
──────────────────────────────────────────────────────────────────────
TSMC (TWSE)                 TSM3.L (3x, LSE)    ETP wins ✓ (note: TSMC direct is TWSE → not ISA eligible; ETP on LSE IS eligible)
Samsung Electronics (KRX)   SMSN.IL (GDR, LSE)  GDR preferred over KRX direct
Alibaba Group (HKEX 9988)   BAB3.L or 9988.HK   9988.HK direct IS ISA-eligible; BAB3.L if available
BHP (ASX)                   No ETP               Trade direct ASX (AUD)
Toyota (TSE)                No ETP               Trade direct TSE (JPY)
Sony (TSE)                  No ETP               Trade direct TSE (JPY)
Nintendo (TSE)              No ETP               Trade direct TSE (JPY)
Tencent (HKEX 700.HK)       No ETP               Trade direct HKEX (HKD)
CSL (ASX)                   No ETP               Trade direct ASX (AUD)
DBS (SGX)                   No ETP               Trade direct SGX (SGD)
```

**Special note on TSMC:** TSMC itself trades on TWSE (Taiwan) which is NOT
ISA-eligible. However TSM3.L is a GBP-denominated leveraged ETP on LSE
tracking TSMC ADR — this IS ISA-eligible and is the correct route.
The direct Taiwan equity is BLOCKED by the ISA eligibility gate.

---

## Section 3: DARK Mode Architecture

DARK mode (21:00-23:00 UTC) is a protected execution window containing
NO live trading. It exists solely to run the Ouroboros nightly pipeline
without competing for CPU/memory with trading activity.

### DARK Mode Invariants

1. **Zero market data subscriptions during DARK.** All non-mega-runner
   lines cancelled at 21:00. The only IBKR connections active are:
   free snapshot polls (60s) for carried mega-runner positions.
2. **Zero order submission during DARK.** The Executioner's submit path
   checks `is_dark_mode()` and returns immediately with `DarkModeBlocked`
   if true. This is not a veto — it is a hard gate before veto evaluation.
3. **Ouroboros fires at exactly 21:00 UTC.** Triggered by the MODE C
   close event, not a cron. Clock-driven: `ModeTransition(ModeC → Dark)`.
4. **Ouroboros must complete by 22:55 UTC.** A 5-minute safety margin
   before MODE A open. If not complete: log CRITICAL, emit
   `OuroborosTimeout` WAL event, proceed with last-known lists.

### Ouroboros DARK Pipeline (21:00-23:00 UTC)

```
21:00  ModeTransition(ModeC → Dark) fires
       │
       ▼
21:01  STEP 1: Asia universe discovery
       │  reqContractDetails for TSE, HKEX, ASX, SGX, KRX, NZX
       │  Apply hard filters (liquidity, market cap, price floor, ISA check)
       │  ETP overlay: check Asian underlyings against LSE ETP catalogue
       │  Output: asia_universe_{date}.json
       │
       ▼
21:15  STEP 2: European universe refresh (existing, unchanged)
       │  Re-score European tickers with latest data
       │
       ▼
21:25  STEP 3: US/Americas universe refresh (existing, unchanged)
       │  Re-score US equities with latest data
       │
       ▼
21:35  STEP 4: Component calibration — ALL modes
       │  Bayesian win rate update (all exchanges)
       │  Kelly fraction recalculation (all exchanges)
       │  Alpha decay update (all exchanges)
       │  ASER re-score (all exchanges, including Asian tickers)
       │
       ▼
21:55  STEP 5: FX rate refresh
       │  Fetch JPY/GBP, HKD/GBP, AUD/GBP, SGD/GBP, KRW/GBP, NZD/GBP
       │  Refresh EUR/GBP, CHF/GBP, SEK/GBP (existing Phase 12 currencies)
       │  Write FX table to Redis
       │
       ▼
22:05  STEP 6: PDF reports
       │  PDF1 (21:00 post-mortem): today's trade summary, P&L, MODE C review
       │  PDF2 (07:00 morning primer): tomorrow's Asian/European opportunity
       │    scanner — built NOW at 22:05, held in Redis, delivered at 07:00
       │
       ▼
22:20  STEP 7: Asian holiday calendar update
       │  Refresh Japan, HK, AU, SG, KR, NZ public holiday lists
       │  Any exchange holiday tomorrow → zero lines allocated to that exchange
       │
       ▼
22:30  STEP 8: GDR/ETP routing table refresh (Asia)
       │  Re-scrape Leverage Shares, GraniteShares, WisdomTree
       │  Check for new Asian underlying ETPs
       │  Update routing_table.toml Asian entries
       │
       ▼
22:45  STEP 9: LSE registry update (existing)
       │  Scrape all LSE leveraged ETPs — unchanged from Phase 12
       │
       ▼
22:55  Ouroboros completion signal → Redis: pipeline_complete=1
       │
       ▼
23:00  ModeTransition(Dark → ModeA) fires
       Load MODE A universe lists
       Subscribe Asian IBKR lines
```

### DARK Mode PDF Reports

**PDF1 — Post-Mortem (generated at 21:05, available immediately):**
- Total trades today (all modes)
- Net P&L, max drawdown, gross win rate
- MODE C session summary: tickers traded, exits, mega-runners carried
- Top 3 wins, top 3 losses with post-mortem analysis
- Risk regime events (Reduce/Flatten/Halt triggers, if any)

**PDF2 — Morning Primer (generated at 22:05, delivered at 07:00):**
- Top 10 Asian opportunities ranked by ASER score
- Top 10 European opportunities (MODE B preview)
- FX environment: JPY/AUD/HKD rates and trend
- Overnight carries: mega-runner status with last-known stops
- Asian holiday calendar for next 5 trading days
- Cross-timezone intelligence: Asian moves vs. US futures correlation

---

## Section 4: Overnight Carry State Machine

Certain positions — "mega-runners" — are explicitly exempted from the
MODE C flatten and allowed to carry through DARK and into subsequent
sessions. The carry state machine governs their lifecycle.

### Mega-Runner Definition

A position qualifies as a mega-runner when ALL of the following hold:
- Unrealised gain >= +102% from entry price (adaptive: per-ticker 99th pct MFE if higher)
- Chandelier trailing stop is >= 2.0% below current price (room to run)
- Position is in a HotScanner (continuous tick) slot
- The security trades on an exchange that will resume within 16 hours

A mega-runner is promoted by the RiskGate during MODE C at any point
after the qualify conditions are met. Promotion is logged as a
`MegaRunnerPromoted` WAL event.

### Carry States

```
                    MODE C active, R >= 3.0
INACTIVE ─────────────────────────────────────────► LIVE
                                                      │
                                           21:00 UTC  │ MODE C close
                                     (qualify check)  │
                                                      ▼
                    21:00 UTC, no longer qualifies    CARRIED
INACTIVE ◄────────────────────────────────────────────│
         (flatten at close)                            │
                                                      │ 23:00 UTC, MODE A open
                                                      │ (Asian exchange opens for
                                                      │  a correlated session)
                                                      ▼
                                                  MONITORED
                                                      │
                                                      │ 14:30 UTC next day,
                                                      │ MODE B+ / original
                                                      │ exchange reopens
                                                      ▼
                                                  REACTIVATED
                                                      │
                    Chandelier stop hit, or           │
                    manual flatten, or T-5 EOD        │
                                                      ▼
                                                    CLOSED
```

### State Descriptions

**LIVE:** Position active during MODE C. HotScanner slot, continuous ticks.
Chandelier updating every tick. Normal RiskGate supervision.

**CARRIED:** MODE C closed. Exchange closed (NYSE/NASDAQ dark). No live
ticks available. IBKR free snapshots polled every 60 seconds via
`reqMktData` with `snapshot=true`. Chandelier uses last-known stop —
the stop level is FROZEN (not updated) until ticks resume. The safety-locked
line remains allocated throughout the DARK and MODE A windows.

**MONITORED:** MODE A active. The Asian exchanges are open and provide
correlated sentiment data. NVDA carried from MODE C: while NYSE is dark,
HKEX tech stocks and Nikkei futures provide directional intelligence.
The carried position's risk assessment is updated using cross-timezone
intelligence (Section 12). Stop remains frozen to last-known level.

**REACTIVATED:** The carried position's original exchange reopens
(14:30 UTC for NYSE/NASDAQ in MODE B+). Live ticks resume immediately.
Chandelier unfreezes and begins updating the trailing stop with fresh
ticks. The position re-enters the HotScanner Hot tier.

### Carry Line Accounting

During CARRIED/MONITORED states, the position holds exactly 1 safety-locked
IBKR line. This line is deducted from the 100-line budget for the current
mode. A MODE A session with 2 carried positions has 98 lines available for
Asian scanning.

```
available_lines = 100 - carried_positions.len()
```

This is enforced by the proptest (Section 14, test AT-43).

### NVDA Carry Example (MODE C → DARK → MODE A → MODE B+)

```
16:45 UTC: NVDA enters LIVE state. Unrealised gain = +34%. Not a mega-runner yet.
19:30 UTC: NVDA unrealised gain = +148%. Chandelier stop at $820 (current $850).
           +148% > +102% threshold: MegaRunnerPromoted WAL event emitted.
20:55 UTC: MODE C T-5. NVDA qualifies for carry. Flatten skipped.
21:00 UTC: MODE C close. NVDA → CARRIED state.
           Last known price: $853. Last known stop: $820. Frozen.
21:00-23:00: DARK. Ouroboros runs. NVDA polled every 60s (free snapshot).
           No ticks (NYSE dark). Stop remains $820.
23:00 UTC: MODE A open. NVDA → MONITORED state.
           HKEX opens. HKEX semiconductor stocks used as correlation proxy.
           If HKEX tech sector down 2%+: emit CarryRiskElevated event.
           Stop still frozen at $820 until NYSE reopens.
08:00-14:30: MODE B active. NVDA still MONITORED.
14:30 UTC: MODE B+ opens. NYSE pre-market opens.
           NVDA live ticks resume via reqMktData.
           NVDA → REACTIVATED state.
           Chandelier unfreezes. Stop begins tracking fresh ticks.
           Position re-enters HotScanner Hot tier.
16:30 UTC: If still open at MODE C boundary: standard EOD flatten applies.
```

---

## Section 5: Asian Exchange Profiles

File: `rust_core/src/asian_exchange.rs`

This new module extends the Exchange enum (from Phase 12's
`exchange_profile.rs`) with Asian exchanges and implements
exchange-specific logic: lunch break detection, board lot size lookup,
daily price limit detection, and local holiday calendar management.

### Exchange Enum Extension

```rust
// In rust_core/src/types/enums.rs — extend existing Exchange enum:
pub enum Exchange {
    // Existing (Phase 11/12)
    LSE, NYSE, NASDAQ,
    EuronextParis, EuronextAmsterdam, EuronextBrussels,
    EuronextDublin, EuronextLisbon, Xetra, SixSwiss,
    OmxStockholm, OmxHelsinki, OmxCopenhagen,
    BorsaItaliana, BmeMadrid, OsloBors, Warsaw, Athens,
    // New — Phase 13 Asian exchanges
    Tse,       // Tokyo Stock Exchange
    Hkex,      // Hong Kong Exchanges and Clearing
    Asx,       // Australian Securities Exchange
    Sgx,       // Singapore Exchange
    Krx,       // Korea Exchange
    Nzx,       // New Zealand Exchange
}
```

### Asian Exchange Profile Struct

```rust
/// Exchange-specific profile for an Asian exchange.
/// Extends ExchangeProfile with Asia-specific fields.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AsianExchangeProfile {
    /// Core exchange profile (open/close UTC, tick sizes, order types)
    pub base: ExchangeProfile,
    /// Whether this exchange has a lunch break
    pub has_lunch_break: bool,
    /// Lunch break start (UTC seconds from midnight), None if no break
    pub lunch_start_utc_secs: Option<u32>,
    /// Lunch break end (UTC seconds from midnight), None if no break
    pub lunch_end_utc_secs: Option<u32>,
    /// Standard board lot size (shares). Variable per ticker for TSE.
    /// Use board_lot_lookup() for TSE; this field is the default.
    pub default_board_lot: u32,
    /// Daily price limit percentage (e.g., 0.20 = 20%)
    /// None if exchange has no daily price limits.
    pub daily_price_limit_pct: Option<f64>,
    /// Upper circuit breaker: if price_today / prev_close > this → at limit up
    pub limit_up_ratio: Option<f64>,
    /// Lower circuit breaker: if price_today / prev_close < this → at limit down
    pub limit_down_ratio: Option<f64>,
    /// IBKR exchange code for this Asian exchange
    pub ibkr_code: String,
    /// Holiday dates for this exchange's jurisdiction (YYYY-MM-DD strings)
    pub holidays: Vec<String>,
    /// Whether this exchange has pre/post market sessions
    pub has_extended_hours: bool,
}

impl AsianExchangeProfile {
    /// Is this exchange in its lunch break right now?
    /// `utc_secs` = seconds from midnight UTC.
    pub fn is_lunch_break(&self, utc_secs: u32) -> bool {
        match (self.lunch_start_utc_secs, self.lunch_end_utc_secs) {
            (Some(start), Some(end)) => {
                (start..end).contains(&utc_secs)
            }
            _ => false,
        }
    }

    /// Is this exchange in continuous trading right now?
    /// Returns false during lunch break and outside open/close times.
    pub fn is_trading_now(&self, utc_secs: u32, date: &str) -> bool {
        if self.holidays.iter().any(|h| h == date) {
            return false;
        }
        let open = self.base.open_utc_secs;
        let close = self.base.close_utc_secs;
        let in_session = (open..close).contains(&utc_secs);
        in_session && !self.is_lunch_break(utc_secs)
    }

    /// Round a share quantity up to the nearest board lot.
    /// For TSE, callers should use board_lot_lookup() instead.
    pub fn round_to_board_lot(&self, qty: u32) -> u32 {
        let lot = self.default_board_lot;
        if lot <= 1 {
            return qty;
        }
        ((qty + lot - 1) / lot) * lot
    }

    /// Is the stock at its daily price limit (up or down)?
    /// `price_today` = current price. `prev_close` = previous session close.
    pub fn is_at_price_limit(&self, price_today: f64, prev_close: f64) -> bool {
        if prev_close <= 0.0 {
            return false;
        }
        let ratio = price_today / prev_close;
        if let Some(limit_up) = self.limit_up_ratio {
            if ratio >= limit_up {
                return true;
            }
        }
        if let Some(limit_down) = self.limit_down_ratio {
            if ratio <= limit_down {
                return true;
            }
        }
        false
    }
}
```

### TSE Board Lot Size Lookup

TSE board lots are not uniform — they vary by company. IBKR's
`reqContractDetails` returns the `minTick` and `multiplier` fields which,
combined, give the effective board lot. The lookup table is populated
at boot from IBKR contract data and refreshed nightly.

```rust
/// TSE-specific board lot registry.
/// Populated from IBKR reqContractDetails at boot and nightly refresh.
pub struct TseBoardLotRegistry {
    /// conid (IBKR contract ID) → board lot size
    lots: HashMap<u32, u32>,
}

impl TseBoardLotRegistry {
    /// Look up board lot for a given IBKR contract ID.
    /// Returns default of 100 if not found.
    pub fn lot_size(&self, conid: u32) -> u32 {
        *self.lots.get(&conid).unwrap_or(&100)
    }

    /// Round a quantity up to the nearest TSE board lot.
    pub fn round_to_lot(&self, conid: u32, qty: u32) -> u32 {
        let lot = self.lot_size(conid);
        if lot <= 1 {
            return qty;
        }
        ((qty + lot - 1) / lot) * lot
    }
}

// Known TSE board lot sizes (representative sample):
// Toyota Motor (7203):    100 shares
// Sony Group (6758):      100 shares
// SoftBank Group (9984):  100 shares
// Nintendo (7974):        100 shares
// Fast Retailing (9983):  100 shares
// Keyence (6861):         100 shares
// NTT (9432):             100 shares
// (All major TSE stocks standardised to 100 since 2018 lot standardisation)
```

### Asian Exchange Profile Constants

```toml
# config/asian_exchange_profiles.toml

[tse]
ibkr_code = "TSEJ"
open_utc_secs = 0        # 00:00 UTC (09:00 JST)
close_utc_secs = 21600   # 06:00 UTC (15:30 JST)
has_lunch_break = true
lunch_start_utc_secs = 9000    # 02:30 UTC (11:30 JST)
lunch_end_utc_secs = 12600     # 03:30 UTC (12:30 JST)
default_board_lot = 100
daily_price_limit_pct = 0.20   # ±20% for most stocks
limit_up_ratio = 1.20
limit_down_ratio = 0.80
currency = "JPY"
auction_open_buffer_mins = 5
auction_close_buffer_mins = 10

[hkex]
ibkr_code = "SEHK"
open_utc_secs = 5400     # 01:30 UTC (09:30 HKT)
close_utc_secs = 28800   # 08:00 UTC (16:00 HKT)
has_lunch_break = true
lunch_start_utc_secs = 14400   # 04:00 UTC (12:00 HKT)
lunch_end_utc_secs = 18000     # 05:00 UTC (13:00 HKT)
default_board_lot = 100
daily_price_limit_pct = null   # HKEX: no hard daily limit
limit_up_ratio = null
limit_down_ratio = null
currency = "HKD"
auction_open_buffer_mins = 5
auction_close_buffer_mins = 10

[asx]
ibkr_code = "ASX"
# ⚠️ DST NOTE: ASX open/close shifts seasonally by 1 hour:
#   AEDT (Oct–Apr, UTC+11): open=00:00 UTC, close=06:00 UTC (values below)
#   AEST (Apr–Oct, UTC+10): open=23:00 UTC prev day, close=05:00 UTC
# In AEST, ASX opens at 23:00 UTC — coinciding with MODE A open boundary.
# The open_utc_secs value below (0 = 00:00 UTC) is correct for AEDT.
# In AEST, use open_utc_secs = 82800 (23:00 UTC). This is currently not
# dynamically computed — DEFERRED: implement ZoneInfo("Australia/Sydney")
# offset check before subscribing ASX lines. Until then, AEST-period ASX
# scans will miss the first hour of the session.
open_utc_secs = 0        # 00:00 UTC (10:00 AEDT) | 23:00 UTC prev day (AEST)
close_utc_secs = 21600   # 06:00 UTC (16:00 AEDT) | 05:00 UTC (15:00 AEST)
has_lunch_break = false
default_board_lot = 1    # ASX: no board lot restriction (any whole shares)
daily_price_limit_pct = null
limit_up_ratio = null
limit_down_ratio = null
currency = "AUD"
auction_open_buffer_mins = 10  # ASX SYCOM pre-open runs 07:00-10:00 AEDT
auction_close_buffer_mins = 10

[sgx]
ibkr_code = "SGX"
open_utc_secs = 3600     # 01:00 UTC (09:00 SGT)
close_utc_secs = 32400   # 09:00 UTC (17:00 SGT)
has_lunch_break = false
default_board_lot = 100
daily_price_limit_pct = null
limit_up_ratio = null
limit_down_ratio = null
currency = "SGD"
auction_open_buffer_mins = 5
auction_close_buffer_mins = 5

[krx]
ibkr_code = "KSE"
open_utc_secs = 0        # 00:00 UTC (09:00 KST)
close_utc_secs = 23400   # 06:30 UTC (15:30 KST)
has_lunch_break = false
default_board_lot = 1    # KRX: no board lot (any whole shares)
daily_price_limit_pct = 0.30   # ±30% daily limit
limit_up_ratio = 1.30
limit_down_ratio = 0.70
currency = "KRW"
auction_open_buffer_mins = 5
auction_close_buffer_mins = 10

[nzx]
ibkr_code = "NZX"
# ⚠️ DST NOTE: NZX open/close shifts seasonally by 1 hour:
#   NZDT (Sep–Apr, UTC+13): open=22:00 UTC prev day (→ 82800 secs = 23:00 UTC,
#     CORRECTION: 82800/3600 = 23.0h = 23:00 UTC; 22:00 UTC would be 79200)
#   NZST (Apr–Sep, UTC+12): open=21:00 UTC (10:00 NZST) → falls in DARK mode!
#
# ⚠️ CRITICAL CONFLICT — NZX in NZST (21:00 UTC open):
#   NZST open at 21:00 UTC = MODE C/DARK boundary. During NZST, the first 2h
#   of NZX trading (21:00–23:00 UTC) falls inside DARK mode ("no trading").
#   Any NZX carry position entering DARK at 21:00 UTC has no active monitoring.
#   DEFERRED: NZX activation requires carry state machine extension to handle
#   the 20:00–21:00 UTC (NZDT) or 21:00–23:00 UTC (NZST) pre-DARK window.
#   Until resolved, NZX subscriptions are DISABLED in Phase 13 initial rollout.
#
# NOTE: open_utc_secs = 82800 is 23:00 UTC (NZDT, 10:00 local time).
# Comment "22:00 UTC prior day" was incorrect — 82800s = 23:00 UTC.
open_utc_secs = 82800    # 23:00 UTC (10:00 NZDT, Season: Sep-Apr)
# NZX wraps midnight: handled as open=82800 (23:00 UTC), close=20700 next day
close_utc_secs = 20700   # 05:45 UTC (17:45 NZDT)
has_lunch_break = false
default_board_lot = 1
daily_price_limit_pct = null
limit_up_ratio = null
limit_down_ratio = null
currency = "NZD"
auction_open_buffer_mins = 5
auction_close_buffer_mins = 5
```

---

## Section 6: Clock Extensions

File: `rust_core/src/clock.rs` — MODIFIED

Phase 13 adds MODE A boundary constants, lunch break detection functions,
and a generic `is_asian_exchange_open()` helper. All times are UTC seconds
from midnight.

```rust
// ── New MODE A boundary constants ───────────────────────────────────────
/// MODE A open: 23:00 UTC (wraps midnight — expressed as 23 * 3600)
pub const MODE_A_OPEN_UTC_SECS: u32 = 23 * 3600;
/// MODE A close: 08:00 UTC
pub const MODE_A_CLOSE_UTC_SECS: u32 = 8 * 3600;
/// DARK mode open: 21:00 UTC
pub const DARK_OPEN_UTC_SECS: u32 = 21 * 3600;
/// DARK mode close: 23:00 UTC
pub const DARK_CLOSE_UTC_SECS: u32 = 23 * 3600;

// ── TSE lunch break constants ─────────────────────────────────────────
/// TSE lunch break start: 02:30 UTC (11:30 JST)
pub const TSE_LUNCH_START_UTC: u32 = 2 * 3600 + 30 * 60;
/// TSE lunch break end: 03:30 UTC (12:30 JST)
pub const TSE_LUNCH_END_UTC: u32 = 3 * 3600 + 30 * 60;

// ── HKEX lunch break constants ────────────────────────────────────────
/// HKEX lunch break start: 04:00 UTC (12:00 HKT)
pub const HKEX_LUNCH_START_UTC: u32 = 4 * 3600;
/// HKEX lunch break end: 05:00 UTC (13:00 HKT)
pub const HKEX_LUNCH_END_UTC: u32 = 5 * 3600;

// ── MODE A flatten time: T-5 relative to MODE A close ────────────────
/// MODE A flatten time: 07:55 UTC (T-5 before 08:00 close)
pub const MODE_A_FLATTEN_UTC_SECS: u32 = 7 * 3600 + 55 * 60;

impl Clock {
    // ── New static methods ───────────────────────────────────────────

    /// Is the current UTC time within the DARK mode window (21:00-23:00)?
    pub fn is_dark_mode(utc_secs: u32) -> bool {
        (DARK_OPEN_UTC_SECS..DARK_CLOSE_UTC_SECS).contains(&utc_secs)
    }

    /// Is the current UTC time within MODE A (23:00-08:00)?
    /// MODE A wraps midnight, so this check handles the wrap.
    pub fn is_mode_a(utc_secs: u32) -> bool {
        utc_secs >= MODE_A_OPEN_UTC_SECS || utc_secs < MODE_A_CLOSE_UTC_SECS
    }

    /// Is the TSE currently in its lunch break? (02:30-03:30 UTC)
    pub fn is_tse_lunch(utc_secs: u32) -> bool {
        (TSE_LUNCH_START_UTC..TSE_LUNCH_END_UTC).contains(&utc_secs)
    }

    /// Is the HKEX currently in its lunch break? (04:00-05:00 UTC)
    pub fn is_hkex_lunch(utc_secs: u32) -> bool {
        (HKEX_LUNCH_START_UTC..HKEX_LUNCH_END_UTC).contains(&utc_secs)
    }

    /// Is a given Asian exchange currently in continuous trading?
    /// Delegates to AsianExchangeProfile.is_trading_now().
    /// `exchange`: must be one of Tse, Hkex, Asx, Sgx, Krx, Nzx.
    /// `utc_secs`: current UTC seconds from midnight.
    /// `date`: current date string "YYYY-MM-DD" for holiday check.
    pub fn is_asian_exchange_open(
        exchange: Exchange,
        utc_secs: u32,
        date: &str,
        registry: &AsianExchangeProfileRegistry,
    ) -> bool {
        registry
            .get(exchange)
            .map(|p| p.is_trading_now(utc_secs, date))
            .unwrap_or(false)
    }

    /// T-5 flatten time relative to an Asian exchange's close.
    /// Returns the UTC seconds at which T-5 flatten phase begins.
    pub fn get_mode_a_flatten_time(
        exchange: Exchange,
        registry: &AsianExchangeProfileRegistry,
    ) -> Option<u32> {
        registry.get(exchange).map(|p| {
            // T-5: 5 minutes before exchange close
            p.base.close_utc_secs.saturating_sub(5 * 60)
        })
    }

    /// Current system allocation mode based on UTC seconds.
    /// Priority order for boundary conflicts: DARK > MODE A > MODE B > MODE B+ > MODE C
    pub fn current_mode(utc_secs: u32) -> AllocationMode {
        if Self::is_dark_mode(utc_secs) {
            AllocationMode::Dark
        } else if Self::is_mode_a(utc_secs) {
            AllocationMode::ModeA
        } else if utc_secs >= 14 * 3600 + 30 * 60 && utc_secs < 16 * 3600 + 30 * 60 {
            AllocationMode::ModeBPlus
        } else if utc_secs >= LSE_OPEN_SECS && utc_secs < 14 * 3600 + 30 * 60 {
            AllocationMode::ModeB
        } else {
            AllocationMode::ModeC
        }
    }
}
```

### AllocationMode Enum Extension

```rust
// In rust_core/src/types/enums.rs — extend AllocationMode:
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[pyclass(eq, frozen)]
pub enum AllocationMode {
    ModeA,      // Phase 13: Asia-Pacific, 23:00-08:00 UTC
    ModeB,      // Phase 11/12: Europe, 08:00-14:30 UTC
    ModeBPlus,  // Phase 11: Hybrid overlap, 14:30-16:30 UTC
    ModeC,      // Phase 11: Americas, 16:30-21:00 UTC
    Dark,       // Phase 13: DARK window, 21:00-23:00 UTC
}
```

---

## Section 7: UniverseScanner Asia-Pac Pipeline

File: `python_brain/ouroboros/asia_universe.py` — NEW

This module is the Asia-Pacific equivalent of the existing European
universe scanner. It runs as STEP 1 of the Ouroboros DARK pipeline.

### Nightly Crawl Steps

```
STEP 1: Pull Asian exchange tickers via IBKR reqContractDetails
    │  Query per exchange:
    │    TSE:  secType=STK, exchange=TSEJ, currency=JPY
    │    HKEX: secType=STK, exchange=SEHK, currency=HKD
    │    ASX:  secType=STK, exchange=ASX,  currency=AUD
    │    SGX:  secType=STK, exchange=SGX,  currency=SGD
    │    KRX:  secType=STK, exchange=KSE,  currency=KRW
    │    NZX:  secType=STK, exchange=NZX,  currency=NZD
    │
    ▼
STEP 2: Apply hard filters
    │  • ISA-eligible: HMRC Table 1/2 check (Taiwan/China/India = BLOCKED)
    │  • Liquidity: avg daily volume > config threshold per exchange
    │      TSE: > 500,000 shares/day (JPY stocks are high-volume)
    │      HKEX: > 500,000 shares/day
    │      ASX: > 100,000 shares/day
    │      SGX: > 200,000 shares/day
    │      KRX: > 200,000 shares/day
    │      NZX: > 50,000 shares/day (smaller market)
    │  • Market cap: > configurable floor (USD equivalent)
    │      TSE/HKEX/ASX/SGX/KRX: > $100M USD equivalent
    │      NZX: > $50M USD equivalent
    │  • Price floor: > local currency minimum
    │      JPY: > ¥500 (to avoid penny stock noise)
    │      HKD: > HK$2.00
    │      AUD: > A$0.50
    │      SGD: > S$0.50
    │      KRW: > ₩1,000
    │      NZD: > NZ$0.50
    │  • Recently traded: last trade within 5 business days
    │  • Not suspended: IBKR tradingClass != "SUS"
    │  • FX drag acceptable: fx_drag <= 0.0010 (0.10%) — KRW highest
    │  • Not at daily price limit (today's data): skip if at limit up/down
    │
    ▼
STEP 3: ISA eligibility gate (CRITICAL — hard block)
    │  For each ticker, verify exchange is in HMRC Tables 1 or 2.
    │  Specifically BLOCK:
    │    Any TWSE (Taiwan) ticker — even if reached via IBKR
    │    Any SSE/SZSE (China domestic) ticker
    │    Any BSE/NSE (India) ticker
    │    Any ticker with primary_exchange = TWSE, SSE, SZSE, BSE, NSE
    │  ISA_ELIGIBILITY_GATE = strict allowlist:
    │    [TSEJ, SEHK, ASX, SGX, KSE, NZX] only
    │
    ▼
STEP 4: ETP overlay for Asian underlyings
    │  For each Asian survivor:
    │    Check LSE ETP catalogue (Leverage Shares, GraniteShares, WisdomTree)
    │    Check GDR catalogue (LSE IOB — International Order Book)
    │    TSMC → TSM3.L exists? YES → TSM3.L wins (TWSE direct blocked anyway)
    │    Samsung → SMSN.IL (GDR on LSE IOB) → preferred over KRX direct
    │    Alibaba 9988.HK → BAB3.L exists? If yes, ETP wins. If no, 9988.HK direct.
    │    BHP → no LSE leveraged ETP → trade ASX direct
    │
    ▼
STEP 5: Adaptive scoring pipeline (SAME as European tickers)
    │  ASER score calculation for Asian tickers
    │  Bayesian win rate (initialised from exchange-level priors if no history)
    │  Kelly fraction with FX drag penalty applied to Asian currencies
    │  Alpha decay scoring
    │
    ▼
STEP 6: Output MODE A universe list
    │  Sorted by ASER score, descending
    │  Top 30: HotScanner candidates (Hot tier)
    │  Remainder: RotationScanner candidates (Rotation tier)
    │  Written to: data/universe/asia_universe_{date}.json
    │  Loaded at 23:00 UTC by MODE A transition handler
```

### Asia Universe Config

```toml
# config/config.toml — new section

[universe.asia]
enabled = true
exchanges = [
    "TSEJ",   # Tokyo Stock Exchange
    "SEHK",   # Hong Kong Exchanges and Clearing
    "ASX",    # Australian Securities Exchange
    "SGX",    # Singapore Exchange
    "KSE",    # Korea Exchange
    "NZX",    # New Zealand Exchange
]

# ISA eligibility hard blocklist (no exceptions)
isa_blocked_exchanges = ["TWSE", "GTSM", "SSE", "SZSE", "BSE", "NSE"]

# Per-exchange minimums
[universe.asia.tse]
min_avg_daily_volume = 500_000
min_market_cap_usd = 100_000_000
min_price_jpy = 500

[universe.asia.hkex]
min_avg_daily_volume = 500_000
min_market_cap_usd = 100_000_000
min_price_hkd = 2.0

[universe.asia.asx]
min_avg_daily_volume = 100_000
min_market_cap_usd = 100_000_000
min_price_aud = 0.5

[universe.asia.sgx]
min_avg_daily_volume = 200_000
min_market_cap_usd = 100_000_000
min_price_sgd = 0.5

[universe.asia.krx]
min_avg_daily_volume = 200_000
min_market_cap_usd = 100_000_000
min_price_krw = 1000

[universe.asia.nzx]
min_avg_daily_volume = 50_000
min_market_cap_usd = 50_000_000
min_price_nzd = 0.5
```

---

## Section 8: HotScanner and RotationScanner for MODE A

MODE A uses the identical HotScanner / RotationScanner architecture from
Phase 11, applied to the Asian universe. No new scanner infrastructure is
created — the existing modules receive a new sub-universe feed.

### MODE A Sub-Universe Architecture

```
MODE A SUB-UNIVERSES (23:00-08:00 UTC):

    ┌────────────────────────────────────────────────────┐
    │  SUB-UNIVERSE 1: LSE ETPs (Asian underlyings)      │
    │    TSM3.L, BAB3.L, SMSN.IL (GDR), etc.            │
    │                                                    │
    │  SUB-UNIVERSE 2: Asian Direct Equities             │
    │    TSE, HKEX, ASX, SGX, KRX, NZX survivors        │
    │                                                    │
    │  ALLOCATOR: Thompson Sampling across 2             │
    │  Min 20% to each sub-universe                      │
    │  Rest allocated by signal quality                  │
    │                                                    │
    │  100-line constraint minus carried positions.      │
    └────────────────────────────────────────────────────┘
```

### Per-Exchange Hot Slot Management

Within the Asian direct equities sub-universe, lines are further
subdivided by exchange to ensure no single exchange dominates during
its peak hours. Exchange weights update dynamically based on which
exchanges are currently in active trading (not in lunch break, not closed).

```
00:00-02:30 UTC: TSE morning + ASX + KRX active
    → TSE gets proportional weight
    → ASX gets proportional weight
    → KRX gets proportional weight
    → HKEX not yet open (opens 01:30): HKEX weight = 0 until open

02:30-03:30 UTC: TSE LUNCH BREAK
    → TSE new entry weight = 0 (existing TSE positions maintained)
    → ASX, KRX continue normally
    → HKEX morning session active

03:30-04:00 UTC: TSE afternoon resumes
    → TSE weight restored
    → HKEX morning session continues

04:00-05:00 UTC: HKEX LUNCH BREAK
    → HKEX new entry weight = 0
    → TSE, ASX, KRX continue

05:00-06:00 UTC: HKEX afternoon session
    → Full complement of exchanges active

06:00-08:00 UTC: TSE closed, ASX closed, KRX closed
    → HKEX afternoon session + SGX
    → SGX open until 09:00 (carries into MODE B briefly)
```

### Worked Example: Tuesday, 01:00 UTC (MODE A)

```
Carried mega-runners: 0 (clean slate)
Available lines: 100

Thompson Sampling sample: ETP=0.25, Asia_direct=0.75
→ ETP (TSM3.L, BAB3.L, SMSN.IL): 25 lines
→ Asia direct: 75 lines

Within Asia direct (exchanges open at 01:00):
  TSE morning session: active → 35 lines
  ASX: active → 20 lines
  KRX: active → 12 lines
  HKEX: just opened → 8 lines (recently opened, warming up)
  SGX: open → 0 lines (warmed up weight not yet established)

HotScanner: top 30 (Hot tier, continuous ticks)
RotationScanner: 70 (Rotation tier, 60s snapshots)

TOTAL: 25 + 75 = 100 ✓
```

---

## Section 9: Chandelier Carry Logic

File: `rust_core/src/overnight_carry.rs` — NEW

The overnight carry state machine governs position transitions across mode
boundaries. The Chandelier trailing stop interacts with carry state to
determine whether to update stops or freeze them.

### Chandelier Behavior by Carry State

```rust
/// Overnight carry state for a position.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum CarryState {
    /// Normal active trading. Chandelier updates on every tick.
    Live,
    /// Position carried through a mode boundary. Exchange is closed.
    /// Chandelier stop is FROZEN at last_known_stop.
    /// IBKR free snapshots polled every 60s.
    Carried,
    /// MODE A active. Correlated Asian ticks feed cross-timezone intelligence.
    /// Chandelier stop remains FROZEN. No direct update from correlated ticks.
    Monitored,
    /// Original exchange reopened. Live ticks resume. Chandelier unfreezes.
    Reactivated,
}

/// Overnight carry state for a single position.
#[derive(Clone, Debug)]
pub struct CarryPosition {
    pub ticker_id: TickerId,
    pub exchange: Exchange,
    pub carry_state: CarryState,
    /// Last known price from free snapshot or last live tick.
    pub last_known_price: f64,
    /// Frozen Chandelier stop level. Not updated until Reactivated.
    pub frozen_stop: f64,
    /// Timestamp of last free snapshot poll (ns).
    pub last_snapshot_ns: u64,
    /// Whether to use free snapshots during DARK/MODE A window.
    pub poll_snapshots: bool,
    /// WAL event emitted when state transitions to Carried.
    pub carry_promoted_ts: u64,
}

impl CarryPosition {
    /// Update from a free IBKR snapshot during CARRIED/MONITORED state.
    /// The frozen_stop is NOT updated. Only last_known_price changes.
    pub fn update_from_snapshot(&mut self, price: f64, now_ns: u64) {
        self.last_known_price = price;
        self.last_snapshot_ns = now_ns;
        // frozen_stop is intentionally NOT modified here.
        // Stop only updates when CarryState == Live or Reactivated.
    }

    /// Transition to Reactivated when the position's exchange reopens.
    /// After this, Chandelier will receive live ticks and begin updating
    /// the stop from the current price, not the frozen level.
    pub fn reactivate(&mut self) {
        self.carry_state = CarryState::Reactivated;
        // frozen_stop becomes the starting point for Chandelier re-engagement.
        // The Chandelier module will update from here as live ticks arrive.
    }

    /// Is the current snapshot price below the frozen stop? (stop triggered)
    /// This check runs on every snapshot during CARRIED/MONITORED state.
    pub fn is_stop_triggered(&self, current_price: f64) -> bool {
        current_price <= self.frozen_stop
    }
}
```

### Chandelier Stop Triggered During DARK/MODE A

If a free snapshot price drops below the frozen stop during DARK or
MODE A (while the original exchange is closed), the system cannot
immediately execute a sell order on a closed exchange. Protocol:

1. Emit `CarryStopBreached` WAL event with snapshot price and frozen stop.
2. At original exchange open (REACTIVATED transition):
   - Submit immediate market sell order.
   - ExitReason: `ChandelierTrailing` with note "stop breached during carry".
3. The position is NOT liquidated at a correlated Asian exchange even if
   a correlated instrument (e.g., QQQ future) is available. IBKR ISA
   accounts do not support cross-instrument hedging. Accept the gap risk.

```rust
/// Extended ExitReason for carry scenarios.
// Add to existing ExitReason enum in types/enums.rs:
pub enum ExitReason {
    // ... existing variants ...
    CarryStopBreached,     // Stop hit during carry window; exit on reopen
    CarryModeTransition,   // Carry cancelled at mode transition (not mega-runner)
}
```

---

## Section 10: RiskGate Asia-Specific Vetoes

File: `rust_core/src/risk_gate.rs` — MODIFIED

Phase 13 adds six new VetoReason variants and corresponding checks
in the RiskGate for Asia-specific risk scenarios.

### New VetoReason Variants

```rust
// Add to existing VetoReason enum in types/enums.rs:
pub enum VetoReason {
    // ... existing variants ...

    /// Entry blocked: exchange is in its lunch break (TSE or HKEX).
    LunchBreakActive { exchange: String },

    /// Entry blocked: stock is at its daily price limit (up or down).
    /// Applies to TSE (±20%) and KRX (±30%).
    DailyPriceLimitActive { limit_pct: u32, direction: String },

    /// Entry blocked: current mode is DARK (21:00-23:00 UTC).
    /// No trading whatsoever during DARK mode.
    DarkModeActive,

    /// Entry blocked: Asian exchange is not open (outside its session).
    AsianExchangeClosed { exchange: String },

    /// Entry blocked: ISA eligibility gate — exchange not in HMRC Tables 1/2.
    IsaExchangeBlocked { exchange: String },

    /// Entry blocked: FX drag for this currency exceeds the configured maximum.
    FxDragExceedsLimit { currency: String, drag_bps: u32 },
}
```

### RiskGate Evaluation Order for MODE A

The RiskGate checks Asia-specific vetoes in this order, BEFORE
applying the standard veto chain from Phase 11:

```
1. DarkModeActive           → check is_dark_mode(). Immediate reject if true.
2. AsianExchangeClosed      → is this ticker's exchange open right now?
3. LunchBreakActive         → is_tse_lunch() / is_hkex_lunch() for TSE/HKEX tickers
4. DailyPriceLimitActive    → is_at_price_limit() for TSE/KRX tickers
5. IsaExchangeBlocked       → is exchange in ISA_BLOCKED_EXCHANGES set?
6. FxDragExceedsLimit       → fx_drag_bps > config.max_fx_drag_bps?
   [Then standard Phase 11 veto chain: spread, heat, drawdown, ISA short, etc.]
```

### Asia-Specific Risk Configuration

```toml
# config/config.toml — new section

[risk.asia]
# Maximum FX drag in basis points before entry is blocked
max_fx_drag_bps = 10   # 0.10% round-trip

# During TSE/HKEX lunch break: maintain existing positions (no new entries)
block_entries_during_lunch = true

# If stock at daily limit: block entry (price discovery unreliable at limit)
block_entries_at_price_limit = true

# ISA eligibility: only allow these exchanges
isa_allowed_asian_exchanges = ["TSEJ", "SEHK", "ASX", "SGX", "KSE", "NZX"]

# ISA hard block: these exchanges are NEVER allowed
isa_blocked_exchanges = ["TWSE", "GTSM", "SSE", "SZSE", "BSE", "NSE"]
```

---

## Section 11: FX Handling for Asia

File: `rust_core/src/currency.rs` — MODIFIED

Phase 13 extends the Currency enum and FX rate infrastructure from
Phase 12 with six new Asian currencies.

### Currency Enum Extension

```rust
// Add to existing Currency enum in currency.rs:
pub enum Currency {
    // Existing Phase 11/12
    GBP, USD, EUR, CHF, SEK, NOK, DKK, PLN,
    // New Phase 13 — Asian currencies
    JPY,    // Japanese Yen — TSE
    HKD,    // Hong Kong Dollar — HKEX (pegged ~7.8 USD/HKD)
    AUD,    // Australian Dollar — ASX
    SGD,    // Singapore Dollar — SGX
    KRW,    // South Korean Won — KRX
    NZD,    // New Zealand Dollar — NZX
}
```

### FX Drag Configuration

FX drag is the round-trip cost of entering and exiting a position
denominated in a foreign currency. It is applied as a penalty in Kelly
sizing — the expected return is reduced by the round-trip FX cost before
computing optimal position size.

```toml
# config/config.toml

[kelly.fx_drag]
# Round-trip FX conversion cost in fractional terms
# Applied as: kelly_fraction *= (1 - fx_drag) before sizing
gbp = 0.0000   # Base currency, no drag
usd = 0.0003   # USD/GBP, tight spread
eur = 0.0004   # EUR/GBP
chf = 0.0006   # CHF/GBP
sek = 0.0010   # SEK/GBP, less liquid than EUR
nok = 0.0012   # NOK/GBP
dkk = 0.0007   # DKK/GBP
pln = 0.0020   # PLN/GBP, least liquid European currency
jpy = 0.0006   # JPY/GBP, liquid major pair
hkd = 0.0002   # HKD/GBP, HKD/USD peg makes this very low
aud = 0.0005   # AUD/GBP, liquid commodity currency
sgd = 0.0004   # SGD/GBP, well-managed currency
krw = 0.0008   # KRW/GBP, less liquid, higher drag
nzd = 0.0006   # NZD/GBP, smaller market than AUD
```

### Kelly Sizing with FX Drag

```rust
/// Apply FX drag penalty to Kelly fraction.
/// Called in the Kelly sizing path for every non-GBP position.
pub fn apply_fx_drag(kelly_fraction: f64, currency: Currency, fx_drag_table: &FxDragTable) -> f64 {
    let drag = fx_drag_table.get(currency);
    // Compound the drag: effective return = raw return - round_trip_drag
    // Kelly uses effective return → smaller fraction for higher drag currencies
    kelly_fraction * (1.0 - drag)
}
```

### HKD Special Handling

HKD is pegged to USD at approximately 7.75-7.85 HKD/USD. This peg has
held since 1983 and makes HKD effectively a USD proxy for risk purposes.
FX drag is minimal (0.0002 = 0.02% round-trip). HKD exposure is treated
as quasi-USD for currency concentration limits.

```toml
# config/config.toml
[risk.currency_concentration]
# HKD counts toward USD concentration limit (peg-linked)
hkd_counts_as_usd = true
# Max combined USD+HKD exposure as fraction of portfolio
max_usd_hkd_concentration = 0.40
```

---

## Section 12: Cross-Timezone Intelligence

File: `python_brain/ouroboros/cross_timezone.py` — NEW

Asian market moves during MODE A provide leading indicators for positions
carried from MODE C and for the upcoming MODE B session. This module
computes correlation signals that feed into: (1) carry position risk
assessment, (2) the morning primer PDF, and (3) MODE A signal confidence.

### Correlation Framework

```
CARRIED POSITION    MONITORING SIGNAL           CORRELATION
─────────────────────────────────────────────────────────────
NVDA (NYSE)         HKEX semiconductor stocks   High (0.65-0.75)
                    Nikkei 225 futures           Medium (0.55-0.65)
                    TSE tech index (TOPIX IT)    Medium (0.50-0.60)

AAPL (NASDAQ)       HKEX Tencent (700.HK)       Medium (0.40-0.55)
                    TSE consumer electronics     Medium (0.45-0.55)

SPY (NYSE)          Nikkei 225 (IBKR futures)   High (0.70-0.80)
                    HKEX Hang Seng               High (0.65-0.75)
                    ASX 200                      High (0.60-0.70)

Energy (NYSE)       ASX BHP, Rio Tinto           High (0.70-0.80)
                    SGX iron ore futures proxy   High (0.65-0.75)
```

### Cross-Timezone Signal Computation

```python
# python_brain/ouroboros/cross_timezone.py

class CrossTimezoneIntelligence:
    """
    During MODE A, monitors Asian market moves and produces signals
    that inform carry risk assessment and the morning primer PDF.
    """

    def compute_us_sentiment(
        self,
        hkex_tech_return: float,   # HKEX tech sector return today (%)
        nikkei_return: float,       # Nikkei 225 return today (%)
        asx_return: float,          # ASX 200 return today (%)
    ) -> float:
        """
        Produce a blended US market sentiment score from Asian moves.
        Returns: score in [-1.0, 1.0] where +1.0 = strongly bullish US.
        Used to adjust carry position risk assessment during MODE A.
        """
        # Weighted blend: HKEX tech most correlated with US tech
        score = (
            0.45 * hkex_tech_return +
            0.35 * nikkei_return +
            0.20 * asx_return
        ) / 100.0  # normalise from % to fraction

        return max(-1.0, min(1.0, score * 5.0))  # amplify, clamp

    def assess_carry_risk(
        self,
        position: CarryPosition,
        asian_sentiment: float,
    ) -> CarryRiskLevel:
        """
        If Asian sentiment is strongly negative while a US position
        is being carried, elevate risk level.
        """
        if asian_sentiment < -0.4:
            return CarryRiskLevel.Elevated
        elif asian_sentiment < -0.2:
            return CarryRiskLevel.Moderate
        else:
            return CarryRiskLevel.Normal
```

### Morning Primer PDF — Cross-Timezone Section

The PDF2 morning primer (generated at 22:05 UTC in DARK, delivered at
07:00 UTC) includes a cross-timezone summary:

```
CROSS-TIMEZONE INTELLIGENCE (generated 22:05 UTC)
──────────────────────────────────────────────────
Nikkei 225:     +1.2% (bullish for US open)
HKEX Hang Seng: +0.8% (neutral-positive)
ASX 200:        +0.5% (neutral)
HKEX Tech:      +2.1% (NVDA carry: risk NORMAL)

Carry positions:
  NVDA (MODE C carry): last price $853, frozen stop $820
  Asian sentiment score: +0.34 (NORMAL risk)
  Recommendation: Allow carry to REACTIVATE at 14:30 UTC

US futures (if available via IBKR):
  NQ futures: +0.6% (supportive for tech carry)
```

---

## Section 13: New Rust and Python Modules

### New Rust Modules

| Module | Purpose | LOC (est.) |
|--------|---------|------------|
| `rust_core/src/asian_exchange.rs` | AsianExchangeProfile, TseBoardLotRegistry, AsianExchangeProfileRegistry, board lot rounding, lunch break detection, daily price limit detection | ~280 |
| `rust_core/src/overnight_carry.rs` | CarryPosition, CarryState state machine, snapshot update, stop freeze/unfreeze, stop triggered check, CarryStopBreached event | ~180 |
| `rust_core/src/phase13_tests.rs` | 45 acceptance tests for Phase 13 | ~450 |

### Modified Rust Modules

| Module | Change | LOC delta |
|--------|--------|-----------|
| `rust_core/src/clock.rs` | Add MODE A / DARK constants, is_mode_a(), is_dark_mode(), is_tse_lunch(), is_hkex_lunch(), is_asian_exchange_open(), get_mode_a_flatten_time(), current_mode() | +80 |
| `rust_core/src/types/enums.rs` | Extend Exchange enum (6 Asian exchanges), AllocationMode enum (ModeA + Dark variants), VetoReason (6 new Asia variants), ExitReason (CarryStopBreached, CarryModeTransition) | +60 |
| `rust_core/src/currency.rs` | Extend Currency enum (JPY, HKD, AUD, SGD, KRW, NZD), add FxDragTable, apply_fx_drag() | +60 |
| `rust_core/src/exchange_profile.rs` | Extend Exchange enum in ExchangeProfile struct (forward-compat, Asian enums added) | +20 |
| `rust_core/src/risk_gate.rs` | Add Asia veto chain: DarkModeActive, AsianExchangeClosed, LunchBreakActive, DailyPriceLimitActive, IsaExchangeBlocked, FxDragExceedsLimit | +80 |
| `rust_core/src/universe.rs` | MODE A sub-universe registration, TickerState gains exchange field for mode-aware routing | +40 |
| `rust_core/src/allocator.rs` | Extend SubUniverseAllocator for MODE A (ETP + Asia_direct, 2-way Thompson Sampling). Carried position deduction from budget. | +60 |
| `rust_core/src/exit_engine.rs` | Carry state-aware exit: CarryStopBreached → queue deferred exit at exchange reopen | +40 |

### New Python Modules

| Module | Purpose | LOC (est.) |
|--------|---------|------------|
| `python_brain/ouroboros/asia_universe.py` | Asia-Pac daily discovery pipeline: IBKR pull, hard filters, ISA gate, ETP overlay, ASER scoring, output asia_universe_{date}.json | ~220 |
| `python_brain/ouroboros/cross_timezone.py` | CrossTimezoneIntelligence: Asian sentiment score, carry risk assessment, morning primer cross-timezone section | ~120 |

### Modified Python Modules

| Module | Change | LOC delta |
|--------|--------|-----------|
| `python_brain/ouroboros/universe.py` | Import and run asia_universe.py as STEP 1 of DARK pipeline. Reorganise existing steps to steps 2-9. Assert pipeline completes by 22:55 UTC. | +40 |
| `python_brain/ouroboros/fx_rates.py` | Add JPY, HKD, AUD, SGD, KRW, NZD fetching alongside existing Phase 12 currencies | +40 |
| `python_brain/reports/pdf_generator.py` | Add PDF1 post-mortem (MODE C summary) and PDF2 morning primer (Asia opportunities, cross-timezone section) | +150 |

### New Config Files

| File | Purpose | LOC (est.) |
|------|---------|------------|
| `config/asian_exchange_profiles.toml` | 6 Asian exchange profiles with all timing, lot size, price limit, holiday parameters | ~150 |
| `config/asian_routing_table.toml` | Asian ETP/GDR routes (TSM3.L, SMSN.IL, etc.) + direct equity routes | ~100 |

---

## Section 14: Acceptance Tests

File: `rust_core/src/phase13_tests.rs`

### Clock and Mode Boundaries (AT-01 to AT-06)

| # | Test | Expected |
|---|------|----------|
| AT-01 | `Clock::is_mode_a(23 * 3600)` — exactly 23:00 UTC | `true` (MODE A boundary inclusive) |
| AT-02 | `Clock::is_mode_a(4 * 3600)` — 04:00 UTC (within MODE A) | `true` |
| AT-03 | `Clock::is_mode_a(8 * 3600)` — 08:00 UTC (MODE A close, exclusive) | `false` |
| AT-04 | `Clock::is_dark_mode(21 * 3600)` — exactly 21:00 UTC | `true` (DARK boundary inclusive) |
| AT-05 | `Clock::is_dark_mode(22 * 3600 + 30 * 60)` — 22:30 UTC | `true` |
| AT-06 | `Clock::is_dark_mode(23 * 3600)` — 23:00 UTC (DARK close) | `false` (exclusive) |

### TSE-Specific (AT-07 to AT-12)

| # | Test | Expected |
|---|------|----------|
| AT-07 | `Clock::is_tse_lunch(2 * 3600 + 30 * 60)` — 02:30 UTC (lunch start, inclusive) | `true` |
| AT-08 | `Clock::is_tse_lunch(3 * 3600)` — 03:00 UTC (mid-lunch) | `true` |
| AT-09 | `Clock::is_tse_lunch(3 * 3600 + 30 * 60)` — 03:30 UTC (lunch end, exclusive) | `false` |
| AT-10 | New entry attempt for TSE ticker during lunch break (02:45 UTC) | `VetoReason::LunchBreakActive { exchange: "TSE" }` |
| AT-11 | Existing TSE position during lunch break: Chandelier continues monitoring | Stop level maintained, no new entries blocked. Existing position unaffected. |
| AT-12 | TSE ticker at daily price limit (price = prev_close * 1.20) | `is_at_price_limit()` returns `true`. Entry blocked: `VetoReason::DailyPriceLimitActive { limit_pct: 20, direction: "up" }` |

### HKEX-Specific (AT-13 to AT-17)

| # | Test | Expected |
|---|------|----------|
| AT-13 | `Clock::is_hkex_lunch(4 * 3600)` — 04:00 UTC (lunch start) | `true` |
| AT-14 | `Clock::is_hkex_lunch(4 * 3600 + 30 * 60)` — 04:30 UTC (mid-lunch) | `true` |
| AT-15 | `Clock::is_hkex_lunch(5 * 3600)` — 05:00 UTC (lunch end) | `false` |
| AT-16 | HKEX entry during lunch (04:15 UTC) | `VetoReason::LunchBreakActive { exchange: "HKEX" }` |
| AT-17 | HKEX board lot rounding: qty=150, lot=200 | Rounded to 200 shares. `round_to_board_lot(150) == 200` |

### KRX-Specific (AT-18 to AT-20)

| # | Test | Expected |
|---|------|----------|
| AT-18 | KRX ticker at +30% limit (price = prev_close * 1.30) | `is_at_price_limit()` returns `true`. Entry blocked. |
| AT-19 | KRX ticker at -30% limit (price = prev_close * 0.70) | `is_at_price_limit()` returns `true`. Entry blocked. |
| AT-20 | Samsung GDR (SMSN.IL on LSE) vs KRX direct routing | Router selects SMSN.IL (GDR/ETP on LSE wins over KRX direct). Log: "GDR wins: SMSN.IL for Samsung" |

### ASX-Specific (AT-21 to AT-22)

| # | Test | Expected |
|---|------|----------|
| AT-21 | ASX ticker at 00:00 UTC (exchange open, no lunch) | `AsianExchangeProfile::is_trading_now()` returns `true` |
| AT-22 | ASX ticker at 06:01 UTC (after close) | `is_trading_now()` returns `false`. New entry blocked: `VetoReason::AsianExchangeClosed { exchange: "ASX" }` |

### ISA Eligibility Gate (AT-23 to AT-27)

| # | Test | Expected |
|---|------|----------|
| AT-23 | Taiwan TSMC direct (TWSE ticker) added to universe | BLOCKED by ISA eligibility gate. Never reaches MODE A list. |
| AT-24 | TSMC via TSM3.L (LSE ETP) | ALLOWED. TSM3.L is ISA-eligible (LSE). Routes through TSM3.L. |
| AT-25 | China Alibaba via SSE A-share (601688.SS) | BLOCKED. SSE not in HMRC Tables. ISA gate rejects. |
| AT-26 | Alibaba via HKEX (9988.HK) | ALLOWED. HKEX is in HMRC Table 2. ISA gate passes. |
| AT-27 | India Infosys via BSE/NSE direct | BLOCKED. BSE/NSE not in HMRC Tables. ISA gate rejects. |

### Overnight Carry State Machine (AT-28 to AT-36)

| # | Test | Expected |
|---|------|----------|
| AT-28 | Position with +85% unrealised gain at MODE C close | Does NOT qualify for carry (below +102% threshold). Flattened at T-5. |
| AT-29 | Position with +148% unrealised gain, stop >= 2% below price | Qualifies for carry. `MegaRunnerPromoted` WAL event emitted. |
| AT-30 | Carry position during DARK: free snapshot update | `CarryPosition::update_from_snapshot()` updates `last_known_price` only. `frozen_stop` unchanged. |
| AT-31 | Carry stop breached during DARK (snapshot price < frozen_stop) | `is_stop_triggered()` returns `true`. `CarryStopBreached` WAL event emitted. Deferred market sell queued for exchange reopen. |
| AT-32 | Carry position at MODE A open: state = MONITORED | State transitions from CARRIED to MONITORED. Stop remains frozen. No change to `frozen_stop`. |
| AT-33 | Carry position at MODE B+ open (exchange reopens 14:30 UTC) | `reactivate()` called. State = REACTIVATED. Live ticks resume. Chandelier unfreezes. |
| AT-34 | Chandelier first tick after REACTIVATED | Chandelier resumes updating trailing stop from `frozen_stop` as base level. |
| AT-35 | Two carry positions across mode boundary | Both hold 1 safety-locked line each. `available_lines = 100 - 2 = 98` for active scanning. |
| AT-36 | Carry position: IBKR submit blocked during DARK | `is_dark_mode()` check fires. `DarkModeActive` veto. No order submission. |

### DARK Mode (AT-37 to AT-40)

| # | Test | Expected |
|---|------|----------|
| AT-37 | New entry attempt at 21:30 UTC (DARK window) | Immediate rejection: `VetoReason::DarkModeActive`. No veto chain evaluation. |
| AT-38 | Ouroboros pipeline: fires at 21:00 UTC on MODE C close event | `ModeTransition(ModeC → Dark)` triggers `ouroboros.run()`. Confirmed by Redis `pipeline_start_ts` key. |
| AT-39 | Ouroboros timeout: pipeline not complete by 22:55 UTC | `OuroborosTimeout` WAL event. System proceeds with last-known universe lists. MODE A opens at 23:00 regardless. |
| AT-40 | DARK mode: zero IBKR market data subscriptions (non-carry) | `active_lines() == carry_positions.len()`. All non-carry lines cancelled at 21:00. |

### Full Mode Cycle (AT-41 to AT-44)

| # | Test | Expected |
|---|------|----------|
| AT-41 | Full mode sequence: MODE A → MODE B transition at 08:00 UTC | Asian positions flattened (except mega-runners). Asian lines unsubscribed. European universe loaded. 100-line invariant holds. |
| AT-42 | Full mode sequence: MODE C → DARK → MODE A → MODE B | Clean transitions at each boundary. WAL events emitted at each. No orphaned lines. |
| AT-43 | Proptest: random carry count [0,10] + random mode transition | `active_lines() <= 100` holds for all inputs. `active_lines() == carry_count + scanning_lines`. Sum invariant never violated. |
| AT-44 | SGX and NZX positions at MODE A → MODE B boundary (08:00 UTC) | SGX (closes 09:00) and NZX (closes 05:45) handled independently. NZX closed by 08:00 → flattened. SGX still open → safety-locked through MODE B open. |

### FX and Kelly Sizing (AT-45 to AT-48)

| # | Test | Expected |
|---|------|----------|
| AT-45 | Kelly sizing for JPY-denominated TSE position | `apply_fx_drag(kelly_fraction, JPY, ...)` applies 0.0006 drag. Effective kelly < raw kelly. |
| AT-46 | Kelly sizing for HKD-denominated HKEX position | FX drag = 0.0002 (lowest Asian currency). HKD exposure counted toward USD+HKD concentration limit. |
| AT-47 | KRW FX drag (0.0008) vs AUD drag (0.0005): same underlying return | KRW position gets smaller Kelly fraction due to higher drag. ASER scores reflect this. |
| AT-48 | FX rates stale at MODE A open (not refreshed during DARK) | `FxRateTable::has_stale_rates()` returns `true`. System halts subscription until IBKR FX fetch completes. |

---

## Section 15: Files Summary

| File | Change | LOC (est.) |
|------|--------|------------|
| `rust_core/src/asian_exchange.rs` | NEW — AsianExchangeProfile, TseBoardLotRegistry, AsianExchangeProfileRegistry, board lot rounding, lunch break detection, daily price limit detection | ~280 |
| `rust_core/src/overnight_carry.rs` | NEW — CarryPosition, CarryState (Live/Carried/Monitored/Reactivated), CarryRiskLevel, snapshot update, stop freeze, deferred exit queue | ~180 |
| `rust_core/src/phase13_tests.rs` | NEW — 48 acceptance tests (AT-01 through AT-48) | ~450 |
| `rust_core/src/clock.rs` | MODIFIED — MODE A/DARK constants, is_mode_a(), is_dark_mode(), is_tse_lunch(), is_hkex_lunch(), is_asian_exchange_open(), get_mode_a_flatten_time(), current_mode() | +80 |
| `rust_core/src/types/enums.rs` | MODIFIED — Exchange (6 Asian variants), AllocationMode (ModeA + Dark), VetoReason (6 Asia variants), ExitReason (CarryStopBreached, CarryModeTransition) | +60 |
| `rust_core/src/currency.rs` | MODIFIED — Currency (JPY, HKD, AUD, SGD, KRW, NZD), FxDragTable, apply_fx_drag() | +60 |
| `rust_core/src/risk_gate.rs` | MODIFIED — DarkModeActive gate (pre-veto), AsianExchangeClosed, LunchBreakActive, DailyPriceLimitActive, IsaExchangeBlocked, FxDragExceedsLimit veto checks | +80 |
| `rust_core/src/universe.rs` | MODIFIED — MODE A sub-universe registration, TickerState gains exchange field | +40 |
| `rust_core/src/allocator.rs` | MODIFIED — MODE A 2-way Thompson Sampling (ETP + Asia_direct), carry position deduction from 100-line budget | +60 |
| `rust_core/src/exit_engine.rs` | MODIFIED — CarryStopBreached deferred exit, reactivation-triggered market sell | +40 |
| `rust_core/src/exchange_profile.rs` | MODIFIED — Exchange enum extended with 6 Asian variants (forward-compat) | +20 |
| `python_brain/ouroboros/asia_universe.py` | NEW — Asia-Pac nightly discovery: IBKR pull, hard filters, ISA gate, ETP/GDR overlay, ASER scoring, output JSON | ~220 |
| `python_brain/ouroboros/cross_timezone.py` | NEW — CrossTimezoneIntelligence: Asian sentiment score, carry risk assessment, morning primer cross-timezone section | ~120 |
| `python_brain/ouroboros/universe.py` | MODIFIED — Integrate asia_universe.py as STEP 1, reorganise existing steps 1-8 → steps 2-9, 22:55 completion assert | +40 |
| `python_brain/ouroboros/fx_rates.py` | MODIFIED — Add JPY, HKD, AUD, SGD, KRW, NZD alongside existing Phase 12 currencies | +40 |
| `python_brain/reports/pdf_generator.py` | MODIFIED — PDF1 post-mortem (21:05 UTC), PDF2 morning primer with Asia section and cross-timezone intelligence (22:05 UTC) | +150 |
| `config/asian_exchange_profiles.toml` | NEW — 6 Asian exchange profiles: timing, board lots, price limits, auction buffers, holidays | ~150 |
| `config/asian_routing_table.toml` | NEW — Asian ETP/GDR routes (TSM3.L, SMSN.IL, BAB3.L, etc.) + direct equity routes (BHP, Toyota, Sony, etc.) | ~100 |
| `config/config.toml` | MODIFIED — Add `[universe.asia]`, `[risk.asia]`, `[kelly.fx_drag]` (Asian currencies) sections | ~60 |
| `docs/checkpoints/PHASE_13_GATE.md` | NEW — Checkpoint gate for Phase 13 approval | ~50 |

---

## Section 16: Key Invariants

These invariants are unconditional. Any violation is a P0 build failure.

1. **Zero trading during DARK mode (21:00-23:00 UTC).**
   The DarkModeActive check in RiskGate fires BEFORE the standard veto
   chain. No order can be submitted during DARK under any circumstances.
   `is_dark_mode()` is checked at the Executioner's submit entry point,
   not just in the RiskGate. Double-gated.

2. **ISA eligibility gate blocks Taiwan, China, India — no exceptions.**
   TWSE, GTSM, SSE, SZSE, BSE, and NSE are in the hard blocklist. These
   exchanges are never added to any universe list. The check runs at
   UniverseScanner time (nightly, in asia_universe.py), at RiskGate time
   (VetoReason::IsaExchangeBlocked), and at routing table write time.
   Three layers.

3. **100-line IBKR invariant holds across all 5 modes.**
   The proptest (AT-43) verifies that `active_lines() <= 100` for all
   combinations of carry count and mode. The proptest runs on every CI
   build. Carry positions consume lines from the budget; scanning uses
   the remainder. The sum `carried_lines + scanning_lines <= 100` is an
   algebraic identity enforced by the Allocator.

4. **Chandelier stop FROZEN during CARRIED and MONITORED states.**
   The `CarryPosition::update_from_snapshot()` method updates only
   `last_known_price`. The `frozen_stop` field is never modified by
   snapshot updates. Only the Chandelier module in LIVE or REACTIVATED
   state may update the stop. This prevents cross-timezone correlation
   noise from leaking into stop levels.

5. **Ouroboros owns DARK (21:00-23:00 UTC). Nothing else runs.**
   No strategy evaluation, no signal generation, no order submission
   occurs during DARK. The only IBKR activity is free snapshot polls
   for carry positions (passive, read-only). Ouroboros has the full
   2-hour window to complete its pipeline.

6. **TSE lunch break and HKEX lunch break are mode-independent hard stops.**
   New entry attempts during TSE lunch (02:30-03:30 UTC) or HKEX lunch
   (04:00-05:00 UTC) are blocked regardless of signal quality, heat level,
   or any other factor. Existing positions in these exchanges continue to
   be monitored (Chandelier holds last-known stop); only new entries are
   blocked.

7. **ETP/GDR always preferred over direct Asian equity when available.**
   Identical to the Phase 11/12 routing principle. SMSN.IL preferred
   over KRX Samsung direct. TSM3.L used instead of TWSE TSMC (which is
   also ISA-ineligible). BAB3.L preferred over 9988.HK Alibaba direct
   if available. The SmartRouter ETP-first check applies to Asian routes
   exactly as it does to European routes.

8. **Asian FX drag included in ALL Kelly sizing for non-GBP instruments.**
   `apply_fx_drag()` is called for every Asian position size calculation.
   KRW positions (highest drag, 0.0008) always receive a smaller Kelly
   fraction than equivalent AUD positions (0.0005) for the same signal
   strength. No Asian position bypasses FX drag accounting.

---

## Section 17: Triage Amendments (Post-Gemini Audit 2026-03-09)

The following amendments supersede or extend earlier sections. All are binding.

### Amendment A1: NZX / DARK Mode Contradiction — RESOLVED

**Supersedes:** Section 1, DARK mode description

Gemini finding: NZX opens at 21:00 UTC, which overlaps with DARK mode (21:00-23:00 UTC).
The spec states DARK mode is strictly empty (zero orders). This was a contradiction.

**Resolution:** NZX is treated as a MODE A exchange. It is included in the MODE A universe
(23:00-05:45 UTC). NZX is NOT subscribed during the 21:00-23:00 DARK window. Ouroboros
owns that window. NZX subscriptions begin at 23:00 UTC when MODE A opens.
NZX closes at 05:45 UTC — well within the MODE A window. No contradiction exists.

**Clock update — add to `clock.rs`:**
```rust
pub const NZX_OPEN_UTC_SECS: u32  = 23 * 3600;           // 23:00 UTC (MODE A open)
pub const NZX_CLOSE_UTC_SECS: u32 = 5 * 3600 + 45 * 60;  // 05:45 UTC
```

### Amendment A2: Carry Position Monitoring — reqPnL Subscription (not polling)

**Supersedes:** Section 9, CarryPosition snapshot polling

Gemini finding: Polling IBKR snapshots every 60s for carry positions will trigger
pacing violations. IBKR limits non-subscribed snapshot requests.

**Resolution:** Carry positions are monitored via `reqPnL` subscription (not polling):
```rust
// On carry position creation (MODE C → DARK transition):
ibkr.req_pnl_single(pnl_req_id, account_id, "", conid);
// IBKR pushes PnL updates in real-time → CarryPosition::on_pnl_update()
// No polling. No pacing violation.

// Cancel subscription when position closes:
ibkr.cancel_pnl_single(pnl_req_id);
```

Each `reqPnL` subscription consumes 0 extra market data lines (separate channel).
Carry positions consume ONLY their safety-locked data line (the ETP itself).

### Amendment A3: IBKR Daily Server Restart — MODE A Overlap

**Extends:** Section 3, DARK Mode Architecture

IBKR resets servers daily at approximately 23:45 ET (04:45 UTC). This falls WITHIN
MODE A (23:00-08:00 UTC), NOT during DARK mode.

**Scheduling:**
- DARK mode (21:00-23:00 UTC): Ouroboros owns fully — no IBKR restart risk
- IBKR restart: ~04:45 UTC during MODE A
- TSE and KRX are in lunch break at 04:45 UTC — minimal trading impact
- HKEX, SGX, ASX remain active: reconnect must complete within 3 minutes
- Phase 11 reconnect logic (P1-10) handles this automatically

**New acceptance tests:**
- AT-49: IBKR disconnect at 04:45 UTC → reconnect within 3 minutes → carry positions reconciled
- AT-50: Reconnect during TSE lunch → no spurious entries triggered on reconnect

### Amendment A4: ASX Pre-Market (SYCOM) — Explicitly Excluded

**Adds to:** Section 2, Asian Exchange Coverage

ASX pre-market (SYCOM) runs 17:00-07:00 AEST (07:00-21:00 UTC).
AEGIS does NOT trade ASX SYCOM. Only the official continuous session
(00:10-06:00 UTC, ASX local 10:10-16:00 AEST) is used.

**Add to `config/asian_exchange_profiles.toml`:**
```toml
[ASX]
official_open_utc_secs  = 600     # 00:10 UTC (after opening auction)
official_close_utc_secs = 21600   # 06:00 UTC
sycom_active = false               # SYCOM excluded — official session only
```

### Amendment A5: KRX Intraday Volatility Interruptions (VI)

**Extends:** Section 10, RiskGate Asia-Specific Vetoes

Gemini finding: KRX has intraday Volatility Interruption (VI) circuit breakers
that halt individual stocks when price moves >10% in 1 minute. These trigger
before the hard ±30% daily limit.

**Add to `AsianExchangeProfile`:**
```rust
pub struct AsianExchangeProfile {
    // ... existing fields ...
    pub vi_threshold_pct: Option<f64>,  // None = no VI (TSE uses own system)
    pub vi_halt_duration_secs: u32,     // Duration of pause (KRX = 120s)
}

// KRX profile values:
// vi_threshold_pct = Some(10.0)   // 10% in 1 minute triggers VI
// vi_halt_duration_secs = 120     // 2-minute pause
```

**New VetoReason variant:**
```rust
VolatilityInterruptionActive {
    exchange: &'static str,
    estimated_resume_utc_secs: u32,
}
```

Detection: if `|tick_price - 1min_open| / 1min_open > vi_threshold_pct`:
set VI halt state for ticker. Clear after `vi_halt_duration_secs`.

### Amendment A6: HKD Concentration Limit — 80% USD Equivalent

**Supersedes:** Section 11, HKD Special Handling

Gemini finding: The HKMA HKD/USD band (7.75-7.85) at 3x leverage means 1% FX
band move = 3% unhedged variance. Counting HKD 1:1 as USD is incorrect.

**New rule:** HKD positions count as 80% USD exposure for concentration limits.
```rust
impl Currency {
    pub fn usd_concentration_weight(&self) -> f64 {
        match self {
            Currency::USD => 1.0,
            Currency::HKD => 0.8,   // HKMA peg band risk
            _ => 0.0,
        }
    }
}

// In RiskGate concentration check:
// usd_exposure = Σ (position_gbp_value × currency.usd_concentration_weight())
// Limit: usd_exposure ≤ portfolio_gbp × 0.40
```

### Amendment A7: Cross-Timezone Correlation Weights — DCC-GARCH Derived

**Supersedes:** Section 12, CrossTimezoneIntelligence static weights (0.45/0.35/0.20)

Gemini finding: Hardcoded correlation weights violate the "Absolute Adaptivity" mandate.

**New approach:** Weights computed nightly by Ouroboros from DCC-GARCH:
```python
def compute_cross_tz_weights(corr_matrix: dict) -> dict:
    """Derive sentiment weights from DCC-GARCH rolling correlations."""
    raw = {
        "HKEX":   abs(corr_matrix["HKEX_SP500"]),   # 20-day rolling
        "Nikkei": abs(corr_matrix["NKY_SP500"]),
        "ASX":    abs(corr_matrix["ASX_SP500"]),
    }
    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}
```

Stored in `calibration/asia_cross_tz.json`. Loaded at MODE A open.
No hardcoded 0.45/0.35/0.20 values.

### Amendment A8: Asian Exchange Holiday Carry Handling

**Adds to:** Section 4, Overnight Carry State Machine

Gemini finding: If a mega-runner carry is heading into MODE A but the specific
Asian exchange has a public holiday, there was no routing logic.

**New rule:** On MODE C → DARK transition, Ouroboros checks IBKR trading calendar
for each carry position's primary exchange. If next MODE A day is a holiday:
- CarryState transitions to MONITORED (no active scanning, stop frozen)
- Re-check calendar each subsequent MODE C → DARK transition
- When exchange reopens: transition to REACTIVATED
- During holiday: monitored via reqPnL only (Amendment A2)

Holiday calendar source: `reqTradingHours` from IBKR `reqContractDetails`.

### Amendment A9: Maximum Carry Position Cap (RISK-08, SC-12)

**Problem:** With no carry position cap, a system running 24/5 could accumulate
mega-runners across all 5 modes. Each carry position consumes 2 lines (ETP +
underlying). At 10 carry positions = 20 lines permanently locked, leaving only
80 for active scanning across all modes. At MODE A → MODE B transition with 10
carries + 5 active Asian = 30 lines, leaving 70 for European scanning — tight.

**Ruling:** Maximum 6 concurrent carry positions at any time across all modes.
6 positions × 2 lines = 12 lines locked. Remaining 88 lines available for
scanning. This is sufficient for all three phases simultaneously.

**Implementation:**

```rust
// In CarryStateMachine
const MAX_CARRY_POSITIONS: usize = 6;

impl CarryStateMachine {
    /// Attempt to promote a position to CARRIED state.
    /// Returns Err if carry cap is reached.
    pub fn try_carry(&mut self, position: Position) -> Result<(), CarryError> {
        if self.carried_positions.len() >= MAX_CARRY_POSITIONS {
            return Err(CarryError::CapReached {
                current: self.carried_positions.len(),
                max: MAX_CARRY_POSITIONS,
            });
        }
        // ... promote to CARRIED state
        self.carried_positions.push(CarriedPosition::from(position));
        Ok(())
    }
}
```

When cap is reached: positions that would have been carried are instead
flattened at mode close (same as non-mega-runners). Log `CARRY_CAP_REACHED`
in WAL. Telegram alert: `⚠️ CARRY CAP: Position [TICKER] flattened (cap=6)`.

### Amendment A10: HALTED Carry State for Exchange Circuit Breakers

**Problem (FLAW-27, MISSING-04):** The carry state machine has no state for
positions frozen by exchange-imposed circuit breakers (TSE Dynamic Circuit
Breaker, KRX daily price limit hit, HKEX Volatility Control Mechanism). A
position hitting a ±30% KRX daily limit cannot be exited. The system will
show it as "pending close" indefinitely. No terminal state exists.

**New state:** Add `HALTED` to the carry state machine:

```
LIVE → CARRIED → MONITORED → REACTIVATED → CLOSED
                     │
                     ▼ (exchange circuit breaker fires)
                  HALTED
                     │ (exchange resumes OR next trading day)
                     ▼
                  MONITORED (reassessed)
```

**HALTED state rules:**
- No new orders submitted (cannot exit if book is frozen)
- reqPnL polling continues every 60s (Amendment A2)
- Chandelier stop is frozen at last computed level
- Maximum HALTED duration: 2 trading days. If not resolved by Day 3:
  submit market order at circuit breaker limit for partial exit.
- Telegram alert: `🚨 HALT: [TICKER] exchange circuit breaker active`

**Detection:** IBKR Error 201 (Order rejected, market halted) or
`reqContractDetails` returning `tradingHours` showing current time as
outside continuous trading.

### Amendment: Updated Phase 13 Gate Criteria

**Replaces** the gate criteria in Section 14.

Before Phase 14 begins, ALL of the following must be verified and signed off:

- [ ] All 48 original acceptance tests green
- [ ] AT-49: IBKR disconnect at 04:45 UTC handled, reconnect within 3 minutes
- [ ] AT-50: Reconnect during TSE lunch — no spurious entries triggered
- [ ] NZX: DISABLED in Phase 13 initial rollout (NZST/DARK conflict unresolved)
- [ ] ASX: DST note verified — AEST period uses 23:00 UTC open, AEDT uses 00:00 UTC
- [ ] reqPnL subscription: carry positions monitored without pacing violations
- [ ] ASX SYCOM: verified system does not trade before 00:10 UTC
- [ ] KRX VI detection: 10%/1min triggers VetoReason::VolatilityInterruptionActive
- [ ] HKD concentration: counted as 80% USD equivalent in limit calculation
- [ ] Cross-TZ weights: DCC-GARCH derived, no hardcoded 0.45/0.35/0.20
- [ ] Holiday carry: HKEX holiday tested, carry stays MONITORED until reopen
- [ ] Carry cap: 7th carry position triggers cap, position flattened, CARRY_CAP_REACHED logged
- [ ] HALTED state: KRX ±30% limit hit tested, position enters HALTED, no orders submitted
- [ ] 5 paper trading days: full MODE A coverage, no pacing violations, no 100-line breach

---

*Section 17 added 2026-03-09 — Gemini Adversarial Audit Integration*
*Amendments A9–A10 added 2026-03-09 — Claude Self-Analysis Triage Integration*
*ASX DST note, NZX NZDT conflict, carry cap, HALTED state, Gate Criteria updated.*
*See GEMINI_TRIAGE.md and AEGIS_SELF_ANALYSIS_TRIAGE.md for full rationale.*
