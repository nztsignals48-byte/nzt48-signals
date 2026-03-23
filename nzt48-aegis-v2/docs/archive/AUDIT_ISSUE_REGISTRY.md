# AEGIS V2 — Master Audit Issue Registry
# Generated: 2026-03-19 01:20 UTC
# Audit Mode: Full system audit + remediation

---

## ISSUE CLASSIFICATION KEY
- **FIX IMMEDIATELY** — Blocking correct operation, safe to fix now
- **FIX THIS SESSION** — Important, must be done before deployment
- **TEST FIRST** — Needs investigation before fix
- **DEFER** — Not blocking, lower priority
- **REJECT** — Not a real issue or not worth fixing

---

## P0 — FIX IMMEDIATELY

### ISS-001: IB Gateway Internal Process Dead
- **Subsystem**: Infrastructure / IB Gateway
- **Runtime Evidence**: `socat E connect(5, AF=2 127.0.0.1:4001, 16): Connection refused` (continuous every 30s)
- **Root Cause**: IB Gateway Java process crashed or 2FA expired (Wednesday, likely weekly expiry)
- **Impact**: Engine in HALT, ZERO market data, ZERO signals, ZERO trades
- **Remediation**: Restart IB Gateway container + re-auth 2FA on IBKR mobile
- **Difficulty**: LOW (manual intervention)

### ISS-002: SIGNAL_DROUGHT — 6288 Ticks, 0 Signals
- **Subsystem**: Trade Plane / Python Brain
- **Runtime Evidence**: `WARNING: SIGNAL_DROUGHT — 6288 ticks received but 0 signals generated`
- **Root Cause**: INVESTIGATING — likely combination of: (a) no valid IBKR data (broker disconnected), (b) bar history warmup never completed, (c) VanguardSniper thresholds too restrictive
- **Impact**: System cannot trade even when broker reconnects
- **Remediation**: Trace full tick→signal pipeline, fix warmup/threshold issues
- **Difficulty**: MEDIUM

### ISS-003: Ouroboros Learning Loop Not Closed
- **Subsystem**: Ouroboros / Adaptive
- **Runtime Evidence**: `ouroboros_recommendations.json` has `"adjustments": []`, all values are defaults
- **Root Cause**: (a) 0 trades → nothing to learn from, (b) persistent_memory.py written but never read back, (c) regime_scales always defaults
- **Impact**: No adaptation occurring, system stuck at initial parameters
- **Remediation**: Close feedback loop — read persistent memory in nightly, accumulate across days
- **Difficulty**: MEDIUM

---

## P1 — FIX THIS SESSION

### ISS-004: Deploy Script Has Wrong EC2 IP
- **Subsystem**: Infrastructure / Deployment
- **Runtime Evidence**: deploy_to_ec2.sh line 35: `EC2_IP="100.51.83.159"` vs actual 3.230.44.22
- **Root Cause**: Stale IP not updated after Elastic IP assignment
- **Impact**: Deployment script deploys to wrong host
- **Remediation**: Fix IP in deploy script
- **Difficulty**: LOW

### ISS-005: Port Confusion (4002 vs 4003 vs 4004)
- **Subsystem**: Infrastructure / Config
- **Runtime Evidence**: .env says 4004, docker-compose says 4003, deploy scripts check 4002/4004
- **Root Cause**: Multiple config sources with conflicting values
- **Impact**: Deployment validation will fail, debugging confusion
- **Remediation**: Standardize on 4003 (gnzsnz convention), fix all references
- **Difficulty**: LOW

### ISS-006: Credentials Hardcoded in Git
- **Subsystem**: Infrastructure / Security
- **Runtime Evidence**: .env and .env.production contain TWS_USERID, TWS_PASSWORD, API keys
- **Root Cause**: No secrets management implemented
- **Impact**: Security risk if repository shared
- **Remediation**: Template .env files, use AWS Secrets Manager or env injection
- **Difficulty**: MEDIUM

### ISS-007: Daily_Summary Sheet Tab Not Populated
- **Subsystem**: Reporting / Google Sheets
- **Runtime Evidence**: sheets_sync.py defines Daily_Summary tab but no WAL event routes to it
- **Root Cause**: Missing aggregation logic in sheets_sync
- **Impact**: Dashboard incomplete — no daily P&L overview
- **Remediation**: Add daily summary computation in nightly_v6 → push to sheets
- **Difficulty**: MEDIUM

### ISS-008: Entry/Exit Prices Missing from WAL Schema
- **Subsystem**: Trade Plane / WAL
- **Runtime Evidence**: RoutedOrder doesn't contain actual fill prices
- **Root Cause**: RoutedOrder is pre-fill; FillEvent has price but not linked to PositionClosed in reports
- **Impact**: Sheets and reports can't show entry/exit prices
- **Remediation**: Ensure PositionClosed contains entry_price and exit_price (ALREADY IN SCHEMA — verify Python readers use them)
- **Difficulty**: LOW

### ISS-009: BST Hardcoded to 2028
- **Subsystem**: Clock / Timezone
- **Runtime Evidence**: clock.rs BST_RANGES only covers 2025-2028, falls back to ±3 day approximation after
- **Root Cause**: Hardcoded Unix timestamps for BST transitions
- **Impact**: After 2028, session timing could be off by hours during transition weeks
- **Remediation**: Extend to 2032 or implement algorithmic last-Sunday-of-March/October detection
- **Difficulty**: LOW

### ISS-010: Holiday Calendar Only Covers 2026-2027
- **Subsystem**: Clock / Market Calendar
- **Runtime Evidence**: market_scheduler.rs HolidayCalendar only has 2026-2027 dates
- **Root Cause**: Hardcoded, no dynamic fetching
- **Impact**: After 2027, holidays not respected
- **Remediation**: Extend to 2028+, or implement dynamic holiday source
- **Difficulty**: LOW

### ISS-011: REDIS_URL Mismatch Across Configs
- **Subsystem**: Infrastructure / Config
- **Runtime Evidence**: .env=localhost:6379, .env.production=nzt48-redis:6379, compose=aegis-redis:6379
- **Root Cause**: Multiple config files with different hostnames
- **Impact**: Redis connection failures in some contexts
- **Remediation**: Standardize on aegis-redis (compose service name)
- **Difficulty**: LOW

### ISS-012: Disk at 76% on EC2
- **Subsystem**: Infrastructure / Operations
- **Runtime Evidence**: df -h shows 14GB/19GB used (4.6GB free)
- **Root Cause**: Docker images, data files, logs accumulating
- **Impact**: Could fill up, causing write failures
- **Remediation**: docker system prune, clean old reports/archives, add monitoring
- **Difficulty**: LOW

### ISS-013: 10 "No Security Definition" IB Contract Errors
- **Subsystem**: Trade Plane / Contract Resolution
- **Runtime Evidence**: Engine logs show 10 contracts not recognized by IBKR
- **Root Cause**: Incorrect exchange/currency in contracts.toml for some tickers
- **Impact**: 10 tickers can't be traded
- **Remediation**: Audit contracts.toml, fix or remove invalid entries
- **Difficulty**: LOW

---

## P2 — TEST FIRST

### ISS-014: Scanner Outputs Never Consumed
- **Subsystem**: Trade Plane / Indicators
- **Runtime Evidence**: HotScanner generates SignalCandidate but no visible consumption in engine.rs
- **Root Cause**: Feature incomplete or entry path uses Python brain exclusively
- **Impact**: Scanner sophistication is dead code
- **Remediation**: Verify if engine uses scanner output; if not, either wire in or remove
- **Difficulty**: MEDIUM

### ISS-015: Regime Detection Modules Unused
- **Subsystem**: Trade Plane / Indicators
- **Runtime Evidence**: JumpDiffusionDetector, HurstEstimator available but no call site visible
- **Root Cause**: Entry engine may not call these
- **Impact**: Regime-based filtering not applied to entries
- **Remediation**: Verify call sites in full engine.rs read
- **Difficulty**: MEDIUM

### ISS-016: Simulation Mode Relaxes Risk Gates
- **Subsystem**: Trade Plane / Risk
- **Runtime Evidence**: risk_arbiter.rs simulation_mode flag relaxes cash buffer & portfolio heat
- **Root Cause**: Paper trading meant to be more permissive
- **Impact**: Paper results won't match live behavior
- **Remediation**: Consider running paper with live-equivalent risk gates
- **Difficulty**: LOW

### ISS-017: Persistent Memory Never Read Back
- **Subsystem**: Ouroboros / Learning
- **Runtime Evidence**: nightly_v6.py calls record_trade/record_session but never loads lessons
- **Root Cause**: Feedback loop not closed — memory written but not consumed
- **Impact**: No learning from historical patterns
- **Remediation**: Add lesson consumption to nightly_v6 parameter optimization
- **Difficulty**: MEDIUM

### ISS-018: Backfill Simulator Disconnected
- **Subsystem**: Ouroboros / Learning
- **Runtime Evidence**: backfill_simulator runs daily but results never fed back
- **Root Cause**: Report-only module, no A/B testing integration
- **Impact**: Historical validation doesn't influence live behavior
- **Remediation**: Wire backfill results into confidence scoring or parameter validation
- **Difficulty**: MEDIUM

### ISS-019: Indicator Context Not Stored in WAL
- **Subsystem**: Trade Plane / WAL
- **Runtime Evidence**: PositionClosed has entry/exit prices but no indicator snapshot (RSI, VWAP, ATR, etc.)
- **Root Cause**: WAL schema doesn't include indicator fields
- **Impact**: Can't analyze which indicator conditions predict wins vs losses
- **Remediation**: Add indicator_snapshot field to RoutedOrder/PositionClosed WAL events
- **Difficulty**: HIGH (Rust schema change + Python reader update)

---

## P3 — DEFER

### ISS-020: Crontab DST/BST Sensitivity
- **Subsystem**: Infrastructure / Scheduling
- **Runtime Evidence**: Crontab uses UTC times (correct), but some sessions assume London times
- **Root Cause**: Session PDF timing at 00:55/07:55/14:25/16:30 UTC assumes specific London offset
- **Impact**: During BST transitions, PDFs may arrive 1 hour early/late relative to actual session
- **Remediation**: Make cron times adaptive to BST (or accept ±1h drift)
- **Difficulty**: MEDIUM

### ISS-021: Dockerfile.ibc Conflicts with gnzsnz
- **Subsystem**: Infrastructure / Docker
- **Runtime Evidence**: Two different IB Gateway builds exist (Dockerfile.ibc vs gnzsnz image)
- **Root Cause**: Legacy custom build not removed
- **Impact**: Confusion about which to deploy
- **Remediation**: Remove Dockerfile.ibc if not used
- **Difficulty**: LOW

### ISS-022: validate_deployment.sh Checks for PostgreSQL
- **Subsystem**: Infrastructure / Deployment
- **Runtime Evidence**: Script checks nzt48-signals_postgres_1 which doesn't exist
- **Root Cause**: Stale script from earlier architecture
- **Impact**: Validation script fails on non-existent check
- **Remediation**: Remove PostgreSQL check
- **Difficulty**: LOW

---

## REJECT

### ISS-R01: Clock offset not persisted across restarts
- **Reason**: Clock syncs from IBKR reqCurrentTime() on every startup. No need to persist.

### ISS-R02: Python agents don't use chrono_tz for DST
- **Reason**: Python agents work in UTC. Only ticker_selector uses pytz for exchange hours, which is correct.

---
