# AEGIS V2 — EXECUTION BACKLOG v8.0
**Updated:** 2026-03-20 | **Status:** Active
**Total Items:** 37 | **Build Days:** 35 (~22 parallel)
**Session:** ULTRATHINK v8.0 Unified Implementation Run

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
| N7i | Macro event backfill layer (113 events/year) | 1 | ✅ BUILT | macro_event_layer.py (NEW) |
| N7j | Friction-adjusted expectancy tracking | 1 | ✅ BUILT | analytics_pack.py (NEW) |
| N7k | Session/exchange/leverage comparison tables | 0.5 | ✅ BUILT | analytics_pack.py (NEW) |
| N7l | Feature completeness scorecard | 0.5 | ✅ BUILT | analytics_pack.py (NEW) |
| N7m | Research context store for Claude | 1 | ✅ BUILT | research_store.py (NEW) |
| N7n | Anomaly baseline library (30-day rolling) | 0.5 | ✅ BUILT | research_store.py (NEW) |
| N7o | Operator incident review pack | 0.5 | ✅ BUILT | research_store.py (NEW) |
| N7p | Nightly wiring (Steps 5.8, 5.9, 5.10) | 0.5 | ✅ BUILT | nightly_v6.py |

## P0 — BUILT v7.1 SESSION (Pending Deploy)

| ID | Item | Days | Status | File |
|----|------|------|--------|------|
| N5c | Bridge SIGHUP hot-reload (kill/respawn) | 0.5 | ✅ DEPLOYED | main.rs:528-533 |
| N8a | config.live.toml overlay implementation | 1 | ✅ DEPLOYED | config_loader.rs (load_live + 6 overlay structs) |
| N8b | Live param startup assertions + 3 tests | 0.5 | ✅ DEPLOYED | main.rs:119-130, config_loader.rs (3 new tests) |

## P0 — BUILT v8.0 SESSION (QDR Fixes)

| ID | Item | Days | Status | File |
|----|------|------|--------|------|
| Q068 | Regime scale guard (MIN_REGIME_TRADES=50) | 0.5 | ✅ DEPLOYED | nightly_v6.py (regime_scales block) |
| Q073 | Dynamic floor guard (floor >= static 65) | 0.5 | ✅ DEPLOYED | config_writer.py (adaptive_floor block) |

## P0 — BUILT v9.0 SESSION (SRE + RED-TEAM)

| ID | Item | Days | Status | File |
|----|------|------|--------|------|
| N10c | Log rotation policy (7-day, daily 04:45 UTC) | 0.5 | ✅ DEPLOYED | log_rotate.py + crontab |
| RT2 | Bridge health monitor (15-min Telegram alerts) | 0.5 | ✅ DEPLOYED | bridge_health.py + crontab |

## P0 — QDR HIGH-RISK ITEMS (Scheduled)

| ID | Item | Days | Priority | Status |
|----|------|------|----------|--------|
| Q-095 | Cost drag evaluation (full spread+commission audit) | 1 | HIGH | DEFERRED — needs 50+ trades |
| Q-111 | 3-position validation period (verify N8a limits work) | 0.5 | HIGH | DEFERRED — activates with IS_LIVE=true |
| Q-051 | Cost model unification (config vs nightly vs bridge) | 1 | HIGH | SCHEDULED P1 |
| Q-045 | Bridge auto-restart alerting (RT2) | 0.5 | HIGH | ✅ DEPLOYED (c8c7a43) |

## P0 — DEFERRED (No ROI During Paper)

| ID | Item | Days | Depends | Reason |
|----|------|------|---------|--------|
| N5b | Bar history persistence (Redis) | 3+ | — | 16-min warmup is fine, no value during paper |
| N8c | GBP/USD FX tracking in PnL | 1 | — | All positions are LSE/GBP, activates when US routing enabled |

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

## P2 — RED-TEAM ITEMS

| ID | Item | Days | Depends | Owner | Status |
|----|------|------|---------|-------|--------|
| RT1 | config.live.toml + startup assertion | 0.5 | — | Engine | ✅ BUILT |
| RT2 | Python bridge health alerting | 0.5 | — | Engine | ✅ DEPLOYED (c8c7a43) |
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
- 16 items built (N7a-N7p)
- 4 new files created (backfill_foundation.py, macro_event_layer.py, analytics_pack.py, research_store.py)
- 6 existing files modified (engine.rs, python_bridge.rs, main.rs, bridge.py, config_writer.py, nightly_v6.py)
- Unified master plan v7.0, release candidate v7.0, updated backlog + proof register
- Verification: cargo check PASS, cargo test 675/676 PASS, all py_compile PASS
- 15/15 HIGH-ROI mandatory items now BUILT or PARTIALLY BUILT

**Built v7.1 session (2026-03-20):**
- 3 items built + deployed (N5c, N8a, N8b)
- 2 Rust files modified (main.rs, config_loader.rs) — +218 lines
- 3 new tests (live config parsing, overlay application, safety assertions)
- cargo test: 678 pass, 1 pre-existing failure
- N8a pre-flight log confirmed: `N8a LIVE OVERLAY: max_pos=3, heat=10.0%, sector=33.0%, buffer=25.0%`
- 2 items deferred (N5b, N8c) — no value during paper phase

**All v6.0 + v7.0 + v7.1 DEPLOYED to EC2 (commit 525592f)**

**Built v8.0 session (2026-03-20):**
- 2 QDR fixes deployed (Q-068 regime scale guard, Q-073 confidence floor guard)
- 2 existing files modified (nightly_v6.py, config_writer.py) — 16 insertions, 9 deletions
- 4 QDR HIGH items triaged (2 deferred pending data, 2 scheduled P1)
- cargo test: 678 pass, 1 pre-existing failure
- Python py_compile: all pass

**All v6.0 + v7.0 + v7.1 + v8.0 DEPLOYED to EC2 (commit ed6362a)**

**Built v9.0 session (2026-03-20):**
- 2 SRE/red-team items deployed (N10c log rotation, RT2 bridge health monitor)
- 2 new files created (log_rotate.py, bridge_health.py) — 231 lines
- crontab updated with 2 new cron jobs (daily log rotation, 15-min health check)
- Python py_compile: all pass
- Closes Q-045 (bridge alerting) and N10c (log rotation) from P1 backlog

**All v6.0 + v7.0 + v7.1 + v8.0 + v9.0 DEPLOYED to EC2 (commit c8c7a43)**
