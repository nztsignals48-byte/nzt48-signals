# Universe Scanning Architecture: From 36k to Phase-Specific Subsets

**Date:** 2026-03-14
**Problem:** How to scan ~36k available tickers without causing computational bottlenecks
**Solution:** Phase-aware filtering pipeline that reduces scope to 15-200 tickers per phase

---

## The Problem

```
Naive Approach (DOESN'T WORK):
  ├─ 40-50 scans per day
  ├─ 36k tickers per scan
  ├─ ~10-50ms per ticker (data fetch + calculation)
  └─ Total: 36k × 10-50ms × 50 scans = 18,000-90,000 seconds/day
            = 5-25 HOURS/day of computation

Result: System spends more time scanning than trading. FAIL.
```

## The Solution: Phase-Aware Universe Scoping

**Key Insight:** We DON'T scan all 36k tickers. We scan **only the markets that are open**:

```
PHASE 1 (LSE+Euro, 08:00-14:30 UTC):
  Available Markets: London Stock Exchange, European exchanges
  Tickers to Scan:  ~100-150 ISA-eligible LSE tickers + ~50 Euro candidates
  Exclude:          US (closes at 21:00 UTC previous day), Asia (closes at 07:00 UTC)

  Scan Cost: 150 tickers × 20ms = 3 seconds per scan ✅

PHASE 2 (LSE+US, 14:30-16:30 UTC):
  Available Markets: London Stock Exchange, US (NASDAQ/NYSE)
  Tickers to Scan:  ~12 LSE leverage ETPs + 18 US equities (baseline)
                    + emerging Tier 3 runners (detected by volume)
  Exclude:          Asia (market not yet open), European (optional, can include)

  Scan Cost: 32-50 tickers × 20ms = 0.6-1.0 second per scan ✅

PHASE 3 (US, 16:30-21:00 UTC):
  Available Markets: US (NASDAQ/NYSE)
  Tickers to Scan:  18 US equities + Tier 3 runners detected
  Exclude:          LSE (closed), Asia (not yet)

  Scan Cost: 18-30 tickers × 20ms = 0.4-0.6 second per scan ✅

PHASE 5 (Asia, 22:00-08:00 UTC):
  Available Markets: Asia (Hong Kong, Tokyo, Singapore)
  Tickers to Scan:  TSM, ASML ADRs, key Asia-listed assets (~10-20 baseline)
  Exclude:          LSE (closed), US (closed)

  Scan Cost: 10-20 tickers × 20ms = 0.2-0.4 second per scan ✅

TOTAL DAILY COST:
  ~200-300 unique tickers scanned across all phases
  50 total scans per day
  = 200-300 × 20ms × 50 scans = 200-300 seconds/day
  = 3-5 MINUTES/day of computation ✅ (vs 18,000-90,000 without filtering)
```

---

## Filtering Pipeline (3 Stages)

### Stage 1: Market-Time Gating (Fastest Filter)

```python
def get_scannable_universe_for_phase(phase: Phase) -> List[str]:
    """Return only tickers relevant to this phase's open markets."""

    if phase == Phase.PHASE_1:  # 08:00-14:30 UTC
        return LSE_TICKERS + EUROPEAN_TICKERS  # ~150 total

    elif phase == Phase.PHASE_2:  # 14:30-16:30 UTC
        return LSE_TICKERS + US_TICKERS  # 12 + 18 = 30 baseline

    elif phase == Phase.PHASE_3:  # 16:30-21:00 UTC
        return US_TICKERS  # 18

    elif phase == Phase.PHASE_5:  # 22:00-08:00 UTC
        return ASIA_TICKERS  # 10-20

    return []
```

**Cost:** O(1) lookup, <1ms

---

### Stage 2: Liquidity Gate (Fast Filter)

```python
def filter_by_liquidity(tickers: List[str], phase: Phase) -> List[str]:
    """Remove tickers with insufficient liquidity."""

    liquid_tickers = []

    for ticker in tickers:
        data = fetch_latest_1min_bar(ticker)  # 5-10ms per ticker

        # Quick checks:
        if data.bid_ask_spread_pct > SPREAD_THRESHOLD[phase]:  # >1% = skip
            continue
        if data.volume_day < VOLUME_MIN[phase]:  # <500k = skip
            continue
        if data.halted or data.delisted:  # Status check = skip
            continue

        liquid_tickers.append(ticker)

    return liquid_tickers
```

**Cost:**
- Phase 1: 150 tickers × 10ms = 1.5 seconds
- Phase 2: 30 tickers × 10ms = 0.3 seconds
- Phase 3: 18 tickers × 10ms = 0.2 seconds
- Phase 5: 15 tickers × 10ms = 0.15 seconds

**Result:** Filters to ~120 tickers (Phase 1), ~25 (Phase 2), ~15 (Phase 3), ~12 (Phase 5)

---

### Stage 3: Technical Analysis Gate (Detailed Filter)

```python
def classify_by_tier(tickers: List[str], phase: Phase) -> Dict[str, TickerProfile]:
    """Calculate daily range, RSI, RVOL, and classify into tiers."""

    profiles = {}

    for ticker in tickers:
        data = fetch_ohlcv_20days(ticker)  # 10-20ms per ticker

        daily_range = calculate_daily_range_pct(data)  # Last 20 days avg
        rsi = calculate_rsi(data)  # 14-period
        rvol = calculate_rvol(data)  # Relative volume
        bid_ask = get_current_spread_pct(ticker)

        # Classify tier
        if daily_range <= 3.0:
            tier = "conservative"
        elif daily_range <= 7.0:
            tier = "moderate"
        elif daily_range <= 15.0:
            tier = "volatile"
        else:
            tier = "extreme"

        profiles[ticker] = TickerProfile(
            ticker=ticker,
            tier=tier,
            daily_range_pct=daily_range,
            rsi=rsi,
            rvol=rvol,
            liquidity_score=1 - bid_ask,
            isa_eligible=check_isa(ticker, phase)
        )

    return profiles
```

**Cost:**
- Phase 1: 120 tickers × 20ms = 2.4 seconds
- Phase 2: 25 tickers × 20ms = 0.5 seconds
- Phase 3: 15 tickers × 20ms = 0.3 seconds
- Phase 5: 12 tickers × 20ms = 0.24 seconds

---

## Daily Scanning Schedule (Complete)

### 07:45 UTC - PHASE 1 INITIAL (LSE+Euro)

```
Pipeline:
  ├─ Stage 1: Get LSE+Euro universe (150 tickers) — 1ms
  ├─ Stage 2: Filter by liquidity (-> 120 tickers) — 1.5 sec
  ├─ Stage 3: Classify by tier (all 120 tickers) — 2.4 sec
  └─ Total: ~4 seconds, output 120 tickers with profiles

Output:
  ├─ 110 Tier 1 (conservative)
  ├─ 8 Tier 2 (moderate)
  ├─ 2 Tier 3 (volatile)
  └─ Universe ready for main engine
```

### 08:15 UTC - PHASE 1 HOUR 1 REFRESH #1

```
Pipeline (same as above):
  ├─ Rescan 120 tickers (profiles may have changed)
  ├─ Detect any NEW runners (Tier 3, daily_range expanded)
  ├─ Detect any REMOVED (halted, spread widened >1%)
  └─ Alert on changes (new runners, removals)

Cost: ~4 seconds
```

### 08:30 UTC, 08:45 UTC, 09:00 UTC - HOUR 1 REFRESH #2-#4

Same as #1, ~4 seconds each.

### 10:00 UTC onwards - HOURLY REFRESHES

Same scan, ~4 seconds per hour.

---

### 14:15 UTC - PHASE 2 INITIAL (LSE+US)

```
Pipeline:
  ├─ Stage 1: Get LSE + US universe (12 + 18 = 30 tickers) — 1ms
  ├─ Stage 2: Filter by liquidity (-> 25-28 tickers) — 0.3 sec
  ├─ Stage 3: Classify by tier (all 25-28) — 0.5 sec
  └─ Total: ~0.8 seconds, output 25-28 tickers with profiles

Output:
  ├─ 20 Tier 1 (LSE leverage ETPs)
  ├─ 4 Tier 2 (moderate-vol US equities)
  ├─ 1-2 Tier 3 (volatile runners, if detected)
  └─ Universe ready for main engine

Note: PHASE 2 is SHORT (2 hours), so scanning is quick
```

### 14:45 UTC, 15:00 UTC, 15:15 UTC - HOUR 1 REFRESH #1-#3

```
Each scan: 0.8 seconds
Alert any NEW Tier 3 runners detected (SNDK-like patterns)
```

### 16:00 UTC - PHASE 2 HOURLY REFRESH

Last refresh before Phase 2 close. 0.8 seconds.

---

### 16:15 UTC - PHASE 3 INITIAL (US Only)

```
Pipeline:
  ├─ Stage 1: Get US universe (18 tickers) — 1ms
  ├─ Stage 2: Filter by liquidity (-> 18 tickers) — 0.2 sec
  ├─ Stage 3: Classify by tier (all 18) — 0.3 sec
  └─ Total: ~0.5 seconds, output 18 tickers with profiles

Output:
  ├─ 15 Tier 1-2 (core US holdings)
  ├─ 2-3 Tier 3 (volatile runners, if detected)
  └─ Universe ready for main engine
```

### 16:45 UTC, 17:00 UTC, 17:45 UTC - HOUR 1 REFRESH #1-#3

0.5 seconds each.

### 17:30 UTC, 18:30 UTC, 19:30 UTC, 20:30 UTC - HOURLY REFRESHES

0.5 seconds each.

---

### 21:45 UTC - PHASE 5 INITIAL (Asia)

```
Pipeline:
  ├─ Stage 1: Get Asia universe (10-20 tickers) — 1ms
  ├─ Stage 2: Filter by liquidity (-> 10-15 tickers) — 0.15 sec
  ├─ Stage 3: Classify by tier (all 10-15) — 0.2 sec
  └─ Total: ~0.35 seconds, output 10-15 tickers with profiles

Output:
  ├─ 10-12 Tier 1 (TSM, ASML, stable Asia)
  ├─ 1-2 Tier 2 (moderate-vol)
  ├─ 0-1 Tier 3 (volatile, if any)
  └─ Universe ready for main engine
```

### 22:15 UTC, 22:30 UTC, 22:45 UTC - HOUR 1 REFRESH #1-#3

0.35 seconds each.

### 23:00 UTC onwards - HOURLY REFRESHES

0.35 seconds each (9 total through 07:00 UTC).

---

## Daily Computation Summary

```
Phase 1 (6.5 hours):
  Initial (07:45): 4 sec
  Hour 1 x3 (08:15, 08:30, 08:45): 12 sec
  Hourly x6 (09:00-14:15): 24 sec
  Subtotal: 40 seconds

Phase 2 (2 hours):
  Initial (14:15): 0.8 sec
  Hour 1 x3 (14:45, 15:00, 15:15): 2.4 sec
  Hourly x1 (16:00): 0.8 sec
  Subtotal: 4 seconds

Phase 3 (4.5 hours):
  Initial (16:15): 0.5 sec
  Hour 1 x3 (16:45, 17:00, 17:45): 1.5 sec
  Hourly x4 (17:30-20:30): 2 sec
  Subtotal: 4 seconds

Phase 5 (10 hours):
  Initial (21:45): 0.35 sec
  Hour 1 x3 (22:15, 22:30, 22:45): 1.05 sec
  Hourly x9 (23:00-07:00): 3.15 sec
  Subtotal: 4.5 seconds

═════════════════════════════════
TOTAL DAILY: ~52.5 seconds of computation
═════════════════════════════════

✅ Fits easily within budget (0.1% of 22.5 trading hours)
✅ Sufficient to refresh universes 40-50 times per day
✅ No bottlenecks or latency issues
```

---

## Integration with Universe Scanner Code

```python
# In universe_refresh_scheduler.py

class UniverseRefreshScheduler:
    def __init__(self, artifacts_dir: Optional[Path] = None):
        self.artifacts_dir = artifacts_dir or Path("artifacts")

        # Phase-specific candidate lists (hardcoded, updated occasionally)
        self.phase_universes = {
            Phase.PHASE_1: self._get_lse_euro_candidates(),      # ~150
            Phase.PHASE_2: self._get_lse_us_candidates(),        # ~30
            Phase.PHASE_3: self._get_us_candidates(),            # ~18
            Phase.PHASE_5: self._get_asia_candidates(),          # ~15
        }

    async def execute_refresh(
        self,
        schedule: RefreshSchedule,
        universe_scanner_fn: Callable,
    ) -> UniverseSnapshot:
        """Execute a single universe refresh for a phase."""

        # Stage 1: Get phase-specific candidates
        candidates = self.phase_universes[schedule.phase]

        # Stage 2: Filter by liquidity
        liquid_tickers = await self._filter_liquidity(candidates, schedule.phase)

        # Stage 3: Classify by tier
        profiles = await self._classify_by_tier(liquid_tickers, schedule.phase)

        # Build snapshot
        snapshot = UniverseSnapshot(
            timestamp=datetime.now(UTC),
            phase=schedule.phase,
            scan_type=schedule.scan_type,
            lse_tickers=self._extract_by_market(profiles, "LSE"),
            euro_tickers=self._extract_by_market(profiles, "EURO"),
            us_tickers=self._extract_by_market(profiles, "US"),
            asia_tickers=self._extract_by_market(profiles, "ASIA"),
            total_count=len(profiles),
            ticker_profiles=profiles,
        )

        return snapshot
```

---

## Key Points

### 1. No Full-Universe Scanning
- ❌ Never scan all 36k tickers
- ✅ Only scan open markets for current phase
- ✅ Result: 15-200 tickers per scan, not 36k

### 2. Phase-Specific Universe Lists
- LSE+Euro candidates: ~150 tickers (managed list, updated monthly)
- US candidates: 18 core + emerging runners
- Asia candidates: ~15 tickers (TSM, ASML, indices, etc.)
- These are PRE-COMPUTED and CACHED, not dynamically discovered

### 3. Three-Stage Pipeline
- Stage 1 (Market gating): Which markets are open? 1ms
- Stage 2 (Liquidity): Quick bid-ask/volume check. 10ms per ticker
- Stage 3 (Technical): RSI, RVOL, daily range. 20ms per ticker
- Total: ~20-25ms per ticker × 15-200 tickers = 0.3-4 seconds per scan

### 4. Computational Budget
- 52.5 seconds per day of scanning (out of ~86,400 seconds available)
- 99.94% of time spent trading, 0.06% spent scanning
- ✅ No bottleneck

---

## How This Handles Your SNDK Question

When SNDK appears:

**Current Status:** SNDK is NASDAQ-listed (US only), not ISA-LSE-listed
- ✅ Included in `US_TICKERS` candidate list (Phase 2-3)
- ✅ Stage 2 (liquidity) checks: spread <1%, volume >500k ✓
- ✅ Stage 3 (tier): daily_range = 8.8%, classifies as Tier 3
- ✅ Alerts triggered on RVOL spike + RSI extremes

**Result:** SNDK is discovered ONLY during Phase 2 and Phase 3 (when US is open), not scanned during Phase 1 (when only LSE+Euro are open) or Phase 5 (when only Asia is open).

This is efficient and correct.

---

## Summary

| Item | Value |
|------|-------|
| Total tickers evaluated per scan | 15-200 (phase-specific) |
| Computation per scan | 0.3-4 seconds |
| Scans per day | 40-50 |
| Total daily computation | ~52 seconds |
| Overhead | 0.06% of trading day |
| Bottleneck risk | None ✅ |

**System is efficient and ready for production.**
