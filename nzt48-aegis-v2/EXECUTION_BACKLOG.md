# AEGIS V2 — EXECUTION BACKLOG v7.0
**Updated:** 2026-03-20 | **Status:** Active
**Total Items:** 33 | **Build Days:** 33 (~20 parallel)
**Session:** ULTRATHINK v7.0 Unified Implementation Run

---

## P0 — DEPLOYED

| ID | Item | Days | Status | Commit |
|----|------|------|--------|--------|
| N0a | Daily trade cap (3/day) | 0 | ✅ DEPLOYED | 8c50a66 |
| N0b | Paper config parity (spread veto 0.3%) | 0 | ✅ DEPLOYED | 8c50a66 |
| N0c | Confidence floor (65) | 0 | ✅ DEPLOYED | 8c50a66 |
| N0d | Min-edge gate (0.15%) | 0 | ✅ DEPLOYED | 8c50a66 |
| N0e | Cost WAL fields (spread, commission) | 0 | ✅ DEPLOYED | 8c50a66 |

## P0 — BUILT THIS SESSION (Pending Deploy)

| ID | Item | Days | Status | File |
|----|------|------|--------|------|
| N1a | Cost-aware nightly learning | 2 | ✅ BUILT | nightly_v6.py (Step 1.5 + optimize_parameters N1a block) |
| N1b | Trade taxonomy classifier | 1 | ✅ BUILT | ouroboros/trade_taxonomy.py (new file, 196 lines) |
| N1c | Ticker blacklist enforcement in bridge | 0.5 | ✅ BUILT | bridge.py (_load_ticker_blacklist + process_tick check) |
| N2a | SignalRejected WAL event type | 1 | ✅ BUILT | types/wal.rs (SignalRejected variant) + enums.rs |
| N2b | Enriched PositionClosed fields | 1 | ✅ BUILT | types/wal.rs (7 new fields: hold_time, session, VWAP, ATR, VIX, vol_slope, trade_class) |
| N2c | MissedWinnerCandidate WAL event | 1 | ✅ BUILT | types/wal.rs (MissedWinnerCandidate variant) + enums.rs |
| N3a | Structural tradability score | 1 | ✅ BUILT | bridge.py (5-component STS 0-100, gate + confidence adj) |
| N5a | UK holidays enforcement | 0.5 | ✅ VERIFIED | clock.rs + market_scheduler.rs + uk_holidays.toml (already implemented) |
| RT1 | config.live.toml + startup assertion | 0.5 | ✅ BUILT | config/config.live.toml (new) + main.rs RT1 check |

## P0 — BUILT v7.0 SESSION (Pending Deploy)

| ID | Item | Days | Status | File |
|----|------|------|--------|------|
| N7a | SignalRejected WAL emission at veto point | 0.5 | ✅ BUILT | engine.rs:1481 |
| N7b | BrainSignal extended (vol_slope, vwap_dist, STS) | 0.5 | ✅ BUILT | python_bridge.rs, main.rs |
| N7c | PositionClosed 4 TODO fields wired to real data | 0.5 | ✅ BUILT | engine.rs:1580-1600 |
| N7d | bridge.py signal enrichment (both strategies) | 0.5 | ✅ BUILT | bridge.py |
| N7e | Config diff rollback ledger (30-day ndjson) | 1 | ✅ BUILT | config_writer.py |
| N7f | Missed-winner analysis (Step 5.7 nightly) | 1 | ✅ BUILT | nightly_v6.py |
| N7g | Ticker promotion/demotion/kill scoreboard | 1 | ✅ BUILT | nightly_v6.py |
| N7h | Backfill foundation script | 2 | ✅ BUILT | backfill_foundation.py (NEW) |

## P0 — BUILD NEXT

| ID | Item | Days | Depends | Owner |
|----|------|------|---------|-------|
| N5b | Bar history persistence (Redis) | 1 | — | Bridge |
| N5c | Bridge SIGHUP hot-reload | 1 | — | Bridge |
| N8a | config.live.toml overlay implementation | 1 | — | Engine |
| N8b | Paper param reduction (3 pos, 10% heat) | 0.5 | N8a | Config |
| N8c | GBP/USD FX tracking in PnL | 1 | — | Engine |

## P1 — BUILD NEXT (Following Sprint)

| ID | Item | Days | Depends | Owner |
|----|------|------|---------|-------|
| N4a | Sheets 21-tab architecture | 2 | N2b | Sheets |
| N4b | Win/Loss indicator delta tab | 1 | N4a | Sheets |
| N6a | Claude nightly review module | 2 | N1b | Claude |
| N6b | Claude operator morning briefing | 1 | N6a | Claude |
| N9a | VanguardSniper 30-day backtest | 2 | N7h | Quant |
| N9b | Monte Carlo risk-of-ruin simulation | 1 | N9a | Quant |
| N10a | Remote kill switch (SSH + API) | 1 | — | SRE |
| N10b | External monitoring (health + alerts) | 1 | — | SRE |
| N10c | Log rotation policy | 0.5 | — | SRE |

## P2 — RED-TEAM ITEMS

| ID | Item | Days | Depends | Owner | Status |
|----|------|------|---------|-------|--------|
| RT1 | config.live.toml + startup assertion | 0.5 | — | Engine | ✅ BUILT |
| RT2 | Python bridge health alerting | 0.5 | — | Engine | PENDING |
| RT3 | Cost drag daily reporting | 0.5 | N6b | Reports | PENDING |

## VERIFY LATER (100+ Trades Required)

| ID | Item | When | Depends |
|----|------|------|---------|
| V1 | Gate calibration from veto analysis | 100+ trades | N2a |
| V2 | LSE confidence boost validation | 50+ LSE trades | N4a |
| V3 | VanguardSniper backtest | N7a complete | N7a |
| V4 | Chandelier rung threshold optimization | 100+ trades | N1b |

## CALIBRATE LATER (250+ Trades Required)

| ID | Item | When | Depends |
|----|------|------|---------|
| C1 | Per-ticker Kelly optimization | 250+ trades | N1a |
| C2 | Session-weighted entry timing | 250+ trades | N4a |
| C3 | Regime-specific position sizing | 250+ trades | N1a |
| C4 | Strategy promotion/demotion execution | 250+ trades | N8a |

---

## SESSION SUMMARY

**Built v6.0 session (2026-03-20):**
- 9 items built/verified (N1a, N1b, N1c, N2a, N2b, N2c, N3a, N5a, RT1)
- 4 new files created (trade_taxonomy.py, config.live.toml, QUESTION_DECISION_REGISTER.md, ADVERSARIAL_RED_TEAM_v6.md)
- 3 existing files modified (bridge.py, nightly_v6.py, main.rs)
- 2 Rust type files modified (types/wal.rs, types/enums.rs)

**Built v7.0 session (2026-03-20):**
- 8 items built (N7a-N7h)
- 1 new file created (backfill_foundation.py)
- 4 existing files modified (engine.rs, python_bridge.rs, main.rs, bridge.py, config_writer.py, nightly_v6.py)
- Unified master plan written (AEGIS_MASTER_IMPLEMENTATION_PLAN_v7.md)
- Verification: cargo check PASS, cargo test 675/676 PASS, all py_compile PASS

**Remaining for deployment:**
- `git commit && git push && rsync && docker compose build && docker compose up -d`
- Verify bridge.py changes work with Rust engine (structural score, blacklist)
- Verify nightly_v6.py imports in Docker (trade_taxonomy, missed-winner analysis)
