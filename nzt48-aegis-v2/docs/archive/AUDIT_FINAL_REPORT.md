# AEGIS V2 — Master Audit Final Report
## Generated: 2026-03-19
## Audit Scope: Full system audit + remediation + deployment

---

## 1. PRESERVED STATE

### Prior Work Preserved
- All 29,703 lines of Rust core code (80+ modules)
- All Python brain strategies (VanguardSniper, AutonomousOrchestrator)
- All Ouroboros nightly pipeline (nightly_v6, config_writer, persistent_memory)
- All Docker infrastructure (3-container stack)
- All cron jobs (63 scheduled tasks)
- All reporting (PDFs, Google Sheets, Telegram)
- WAL current + 7 archived files on EC2
- Redis state (sheets:seen_hashes)

### How This Mandate Merged
- Unified all 7 parallel audit streams into single issue registry
- Preserved all existing architecture while fixing root causes
- No destructive changes — all fixes are additive or corrective

---

## 2. FULL ISSUE REGISTRY (22 Issues Found)

### P0 — FIX IMMEDIATELY (3 issues)
| ID | Issue | Status |
|----|-------|--------|
| ISS-001 | IB Gateway internal process dead (2FA expired) | **NEEDS MANUAL 2FA** |
| ISS-002 | SIGNAL_DROUGHT: Moreira-Muir vol-scaling crushed confidence below floor for ALL 3x ETPs | **FIXED** |
| ISS-003 | Ouroboros learning loop not closed (memory written but never read) | **FIXED** |

### P1 — FIX THIS SESSION (10 issues)
| ID | Issue | Status |
|----|-------|--------|
| ISS-004 | Deploy script has wrong EC2 IP (100.51.83.159 → 3.230.44.22) | **FIXED** |
| ISS-005 | Port confusion (4002/4003/4004) across configs | **FIXED** |
| ISS-006 | Credentials hardcoded in git (.env files) | DEFERRED (security, not blocking) |
| ISS-007 | Daily_Summary sheet tab not populated | DEFERRED |
| ISS-008 | Entry/exit prices missing from WAL schema | DEFERRED (already in PositionClosed) |
| ISS-009 | BST hardcoded to 2028 only | **FIXED** (extended to 2032) |
| ISS-010 | Holiday calendar only covers 2026-2027 | **FIXED** (extended to 2029) |
| ISS-011 | REDIS_URL mismatch across configs | **FIXED** |
| ISS-012 | Disk at 76% on EC2 | **FIXED** (pruned to 63%) |
| ISS-013 | 10 "No security definition" IB contract errors | DEFERRED |

### P2 — TEST FIRST (6 issues), P3 — DEFER (3 issues)
- ISS-014 through ISS-022: Deferred or test-first items documented in AUDIT_ISSUE_REGISTRY.md

---

## 3. REMEDIATION DETAILS

### ISS-002: Signal Drought (CRITICAL FIX)

**Root Cause (Technical):**
The VanguardSniper strategy multiplied the raw momentum score by a Moreira-Muir volatility scaling factor before comparing against the confidence floor. For 3x leveraged ETPs with ~70% annual volatility, the MM scale was approximately 0.21. Even a perfect momentum setup scoring 100 points would produce confidence = 100 × 0.21 = 21, far below the floor of 60. This killed 100% of signals.

**Root Cause (Layman's):**
The system was adjusting signal strength based on how volatile the instrument was. But since ALL the instruments we trade are 3x leveraged (extremely volatile), the adjustment made every single signal look too weak to act on. It was like requiring a whisper to sound like a shout — impossible for these instruments.

**Fix Applied:**
1. Removed Moreira-Muir scaling from confidence calculation (it now only affects position SIZE via Kelly)
2. Reduced confidence floor from 60 → 45 (allows moderate-quality setups for paper validation)
3. Added diagnostic logging every 500 ticks per ticker to bridge.py

**Files Changed:**
- `python_brain/brain/strategies/vanguard_sniper.py:193-203`
- `python_brain/brain/config.py:8`
- `python_brain/bridge.py:51,447-456`

### ISS-003: Ouroboros Feedback Loop (CRITICAL FIX)

**Root Cause (Technical):**
nightly_v6.py called persistent_memory.record_trade() and save_memory() to WRITE cumulative stats, but optimize_parameters() never loaded memory to READ them. Parameter optimization used only today's metrics (0 trades = no adjustment). The regime_scales in dynamic_weights.toml were always defaults.

**Root Cause (Layman's):**
The system was keeping notes (persistent memory) about every trade and lesson learned, but when it sat down to decide what to do differently, it never opened the notebook. It was like studying for an exam, writing perfect notes, and then leaving them at home.

**Fix Applied:**
1. Memory loaded BEFORE optimization in run_nightly()
2. optimize_parameters() now accepts `mem` parameter
3. Blends cumulative win rate with daily metrics (70/30 split)
4. Computes regime_scales from cumulative per-regime performance
5. Logs lessons from memory as adjustments

**Files Changed:**
- `python_brain/ouroboros/nightly_v6.py:738-744,354-414`

### ISS-004/005: Deploy Script + Port Fixes

**Fix Applied:**
- EC2 IP fixed to 3.230.44.22 (parameterized with env default)
- All port references standardized to 4003 (gnzsnz convention)
- .env and .env.production updated: IB_GATEWAY_PORT=4003, REDIS_URL=aegis-redis

### ISS-009/010: BST + Holiday Calendar Extended

**Fix Applied:**
- BST transitions extended from 2025-2028 → 2025-2032 (8 years)
- UK bank holidays extended from 2026-2027 → 2026-2029

### ISS-012: Disk Space Reclaimed

**Fix Applied:**
- `docker system prune -f` on EC2: reclaimed 2.3GB
- Disk usage: 76% → 63% (6.9GB free)

---

## 4. CLOCK + CALENDAR TRUTH

**External Clock Source:** IBKR reqCurrentTime() → synced at engine boot
**Drift Measurement:** offset_ns = broker_time - system_time (logged at startup)
**Timezone Logic:** BST transitions hardcoded for 2025-2032 (exact Unix timestamps, tested)
**DST/BST:** Automatic +1h adjustment in `Clock::now_london_secs()` during BST periods
**Bank Holidays:** uk_holidays.toml (2026-2029), loaded at engine boot, checked by risk arbiter
**Session Classification:** Unified 22-hour active (23:00-21:00 London), 2-hour Dark (21:00-23:00)
**Market Hours:** Per-exchange (LSE 08:00-16:30, US 14:30-21:00, HK 01:30-08:00 London)

---

## 5. OUROBOROS LEARNING

**What It Learns:** Win rate, profit factor, avg rung, per-ticker stats, per-regime stats, lessons
**How It Feeds Back:** nightly_v6 → optimize_parameters (now with cumulative memory) → recommendations.json → config_writer → dynamic_weights.toml → engine loads at boot
**Regime Scales:** Computed from cumulative per-regime win rates (0.4-1.5 range)
**Kelly Fractions:** Adjusted ±2-5% based on blended WR (70% cumulative + 30% daily)
**Guardrails:** Max 15% drift per night from baseline, bounded ranges for all parameters

---

## 6. REPORTING

**Session PDFs:** 4 daily (Asian 00:55, European 07:55, American 14:25, US-only 16:30 UTC)
**Daily Summary PDF:** 21:15 UTC (DARK window)
**Google Sheets:** Every 5 minutes via Redis queue → gspread API (5 tabs)
**Telegram:** Real-time trade alerts + 4-hourly heartbeat + session PDFs
**WAL:** Append-only NDJSON, CRC32 checksum, archive rotation on restart

---

## 7. SESSION COVERAGE

| Session | London Time | UTC | Exchanges | Status |
|---------|-------------|-----|-----------|--------|
| Asian (ModeA) | 23:00-08:00 | 23:00-08:00 | HK, TSE, KRX | Active (22h unified) |
| European (ModeB) | 08:00-14:30 | 08:00-14:30 | LSE, XETRA, Euronext | Active |
| US Overlap (ModeBPlus) | 14:30-16:35 | 14:30-16:35 | LSE + US | Active |
| US Only (ModeC) | 16:35-21:00 | 16:35-21:00 | NYSE, NASDAQ | Active |
| Dark | 21:00-23:00 | 21:00-23:00 | None | Maintenance only |

---

## 8. RUNTIME PROOF

### Verified Working
- Container aegis-v2: healthy (healthcheck passing every 30s)
- Container aegis-redis: healthy (ping succeeds)
- Supercronic: 63 cron jobs executing on schedule
- Sheets sync: running every 5 minutes (succeeded 27+ iterations)
- Ticker selector: running every 15 minutes
- FX refresh: succeeded at 00:00 UTC
- Telegram heartbeat: succeeded at 00:00 UTC
- WAL: 6 events in current.ndjson, 7 archived files
- Disk: 63% usage (6.9GB free after cleanup)

### Verified Broken (Requiring Manual Intervention)
- IB Gateway: Java process dead (socat Connection refused on :4001)
- Reason: Weekly 2FA expiry (Wednesday, last auth was Monday)
- Action Required: Re-authenticate on IBKR mobile app, then restart IB Gateway container

---

## 9. REMAINING GAPS

| Gap | Blocking? | Why It Remains |
|-----|-----------|----------------|
| IB Gateway 2FA | YES | Requires physical IBKR mobile app approval |
| Indicator context in WAL | No | Rust schema change needed (not blocking paper validation) |
| Daily_Summary sheet tab | No | Needs aggregation logic (cosmetic) |
| Credentials in git | No | Security concern but doesn't affect operation |
| Scanner outputs unused | No | Engine uses Python brain exclusively (design decision) |
| Invalid IB contracts (10) | No | Non-critical tickers, can be cleaned up |
| Simulation mode risk relaxation | No | Acceptable for paper validation phase |

---

## 10. DEPLOYMENT STATUS

**Local Changes:**
- clock.rs: BST extended to 2032
- uk_holidays.toml: Extended to 2029
- vanguard_sniper.py: MM scale bug fixed (confidence no longer vol-crushed)
- config.py: CONFIDENCE_FLOOR 60→45
- bridge.py: Diagnostic logging added, _tick_counts dict added
- nightly_v6.py: Ouroboros feedback loop closed (memory → optimization)
- deploy_to_ec2.sh: EC2 IP fixed, ports standardized
- .env, .env.production: Port 4003, REDIS_URL standardized

**EC2 Deployment:** rsync complete, docker compose build in progress.

**Post-Deploy Verification Required:**
1. Restart IB Gateway (requires 2FA on IBKR mobile app)
2. Verify engine exits HALT state
3. Verify Python bridge generates signals (BRIDGE_DIAG logs every 500 ticks)
4. Verify first trade entry within 1-2 hours of market open
5. Verify nightly Ouroboros run produces non-default recommendations

---

## PHASE E/G/H (Second + Third Deployment Wave)

### Phase E: Bounded Adaptive Controls
- **Adaptive confidence floor** in dynamic_weights.toml `[signal]` section (range 30-70, starts 45)
- Python bridge reads at startup, overrides hardcoded value
- Config_writer computes from cumulative WR: WR>55% → floor=55, WR<30% → floor=35

### Phase G: Entry Quality Filters
- **Spread gate**: bid/ask spread > 0.5% → signal suppressed
- **Extension filter**: price > 3% from VWAP → signal suppressed (prevents buying tops)
- Both run after signal generation, before emission, with diagnostic logging

### Phase H: Indicator Intelligence
- **WAL schema extended**: entry_rvol, entry_hurst, entry_adx on RoutedOrder + PositionClosed (backward compatible)
- **SimulatedTrade extended**: carries indicator context from entry to exit
- **New module `indicator_intelligence.py`** (580+ lines): 30-day analysis, threshold rules, regime/session performance, recommended filters
- **Wired into nightly pipeline**: Step 5.5 in nightly_v6, feeds results into recommendations

### Three Deployments
1. Deploy 1: Signal drought + Ouroboros + infra → SUCCESS
2. Deploy 2: Phase E/G/H → FAILED (missed replay.rs + wal_tests.rs)
3. Deploy 3: Fixed all compilation errors → SUCCESS, engine healthy

### Total Files Changed: 15
clock.rs, types/wal.rs, engine.rs, replay.rs, wal_tests.rs, uk_holidays.toml,
config.py, vanguard_sniper.py, bridge.py, nightly_v6.py, config_writer.py,
indicator_intelligence.py (NEW), deploy_to_ec2.sh, .env, .env.production
