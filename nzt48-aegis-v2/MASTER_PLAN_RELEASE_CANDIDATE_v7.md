# AEGIS V2 — Master Plan Release Candidate

**Version:** v7.0
**Date:** 2026-03-20
**Status:** CONDITIONAL PAPER — NOT READY FOR LIVE
**Classification:** CTO/CRO Go-Live Readiness Assessment

---

## 1. RELEASE SUMMARY

| Field | Value |
|---|---|
| System | AEGIS V2 Autonomous UK ISA Trading Engine |
| Mode | Paper trading (`IS_LIVE=false` hardcoded, `exit(1)` if true) |
| Architecture | Rust hot path + Python warm/cold path, Docker on EC2 |
| Instruments | 49 LSE leveraged/inverse ETPs + 254 global instruments (303 total) |
| Primary Strategy | VanguardSniper |
| Dormant Strategies | Orchestrator, ApexScout |
| Learning System | Ouroboros v6.0 nightly loop (11 analysis steps) |
| Infrastructure | c7i-flex.large (4GB RAM, 2 vCPU), Elastic IP 3.230.44.22 |
| Broker | IBKR via gnzsnz/ib-gateway on port 4003 |
| State | Redis (password-protected, internal Docker network only) |
| Persistence | WAL (NDJSON) with archive rotation + Redis AOF |

**Architecture overview:** The Rust engine handles order lifecycle, position tracking, chandelier exit management, WAL persistence, and real-time tick processing. Python handles signal generation (bridge.py), nightly learning (nightly_v6.py), config generation (config_writer.py), ticker selection, macro event screening, and session PDF reports. Communication is via TOML config files with SIGHUP hot-reload for the Rust engine and Supercronic-scheduled execution for Python.

---

## 2. WHAT'S BUILT

### 2.1 v6.0 Session — Survival Stack + Learning Foundation

| Code | Item | Status |
|---|---|---|
| N0a | IS_LIVE=false kill switch with exit(1) guard | DEPLOYED |
| N0b | Chandelier exit rung persistence via WAL replay | DEPLOYED |
| N0c | MAE/MFE tracking per position (PositionClosed WAL event) | DEPLOYED |
| N0d | WAL archive scanning (current + archive/*.ndjson) | DEPLOYED |
| N0e | Dynamic weight generation via config_writer at boot + SIGHUP | DEPLOYED |
| N1a | Cost-aware learning — spread/commission in nightly P&L | BUILT |
| N1b | Trade taxonomy (momentum/mean-rev/breakout classification) | BUILT |
| N1c | Ticker blacklist from persistent_memory, enforced in engine | BUILT |
| N2a | SignalRejected WAL event type | BUILT |
| N2b | MissedWinnerCandidate WAL event type | BUILT |
| N2c | SignalRejected emission from bridge.py on gate veto | BUILT |
| N3a | Structural Tradability Score (liquidity, spread, volatility composite) | BUILT |
| N5a | WAL compressor (dedup, archive rotation) | BUILT |
| RT1 | Dynamic spread veto (live spread vs threshold, logged to gate_vetoes.ndjson) | BUILT |

**Documents delivered (v6.0):**
- AEGIS_V2_NIGHTLY_LOOP_SPEC.md
- AEGIS_V2_OUROBOROS_v6.md
- AEGIS_V2_MISSED_WINNER_ANALYSIS.md
- AEGIS_V2_SYSTEM_ARCHITECTURE.md
- AEGIS_V2_WAL_SCHEMA.md

### 2.2 v7.0 Session — Signal Intelligence + Observability

| Code | Item | Status |
|---|---|---|
| N7a | SignalRejected emission wired into bridge.py for all gate paths | BUILT |
| N7b | BrainSignal struct extended (vwap_distance, hurst, volume_slope fields) | BUILT |
| N7c | PositionClosed WAL wiring with MAE/MFE/rung/taxonomy | BUILT |
| N7d | Config ledger — every config_writer run logged with hash + diff | BUILT |
| N7e | Missed-winner analysis in nightly_v6.py Step 5.8 | BUILT |
| N7f | Ticker scoreboard in nightly_v6.py Step 5.9 | BUILT |
| N7g | Backfill foundation script (historical bar fetch) | BUILT |
| N7h | Indicator gate veto logging with full context (gate_vetoes.ndjson) | BUILT |

**New v7.0 modules:**

| Module | Purpose | Wired Into |
|---|---|---|
| `macro_event_layer.py` | Economic calendar screening, earnings blackout detection | nightly_v6.py Step 5.10 |
| `analytics_pack.py` | Drawdown curves, rung distribution, win-rate rolling windows | nightly_v6.py Step 5.9 |
| `research_store.py` | Persistent key-value store for cross-session research state | nightly_v6.py, config_writer |

### 2.3 Strategy Quality Gates (Build 6, prior)

- 5-minute bar aggregation from tick data
- 200-bar warmup period before signal emission
- Leverage-aware confidence floor (65% standard, 80% for 3x+ leverage)
- VWAP pullback check
- Hurst regime gate (trend vs mean-reversion filter)
- Volume slope gate
- Multi-timeframe confirmation
- Per-ticker 25-minute cooldown

### 2.4 Infrastructure

- Docker Compose: 3 containers (aegis-v2, aegis-ib-gateway, aegis-redis)
- Supercronic crontab with 4 scheduled tasks
- IBKR secdef 15s delay after connect before subscribe_all
- Docker image bakes Python dependencies (scp alone insufficient)
- .dockerignore optimised (726MB -> manageable context)
- entrypoint.sh runs config_writer at boot before engine start

---

## 3. VALIDATION GATES

All 10 gates must be GREEN before any live capital deployment. Current status: **0/10 MET**.

| # | Gate | Threshold | Status | Notes |
|---|---|---|---|---|
| G1 | Trade count | >= 100 with live-equivalent params | NOT MET | Insufficient paper trades accumulated |
| G2 | Net win rate | >= 50% | NOT MET | No validated sample |
| G3 | Net profit factor | >= 1.3 | NOT MET | No validated sample |
| G4 | Max drawdown | < 10% over 63 days | NOT MET | 63-day gauntlet not started |
| G5 | Spread victim rate | < 20% | NOT MET | Dynamic spread veto built but not validated |
| G6 | VanguardSniper backtest | Positive expectancy | NOT MET | No backtest infrastructure exists |
| G7 | Monte Carlo survival | > 95% at 1000 paths | NOT MET | Simulation not built |
| G8 | FX tracking | GBP/USD conversion active | NOT MET | N8c not implemented |
| G9 | Live config | config.live.toml validated | NOT MET | N8a not implemented |
| G10 | Remote kill switch | Operational + tested | NOT MET | API exists but not stress-tested |

**Gate progression plan:**
- G1-G5: Require 63-day paper gauntlet with live-equivalent parameters
- G6-G7: Require backtest infrastructure (N5b bar persistence + backtest harness)
- G8-G9: Require remaining P0 work (N8a, N8c)
- G10: Requires kill switch integration test under load

---

## 4. CRITICAL FINDINGS

Adversarial review identified 10 CRITICAL findings. 3 mitigated, 7 open.

### 4.1 MITIGATED (3/10)

| # | Finding | Mitigation |
|---|---|---|
| CF-1 | Spread slippage destroying edge on 3x ETPs | Dynamic spread veto (RT1) deployed. Gate vetoes logged. Threshold tuning pending validation. |
| CF-2 | No historical bar data for backtesting | Backfill foundation script (N7g) built. Fetches from IBKR historical data API. Not yet run at scale. |
| CF-3 | Config drift undetectable | Config ledger (N7d) deployed. Every config_writer run hashed and diffed. Audit trail in data/config_ledger.ndjson. |

### 4.2 OPEN (7/10)

| # | Finding | Severity | Impact |
|---|---|---|---|
| CF-4 | Paper trade data contaminated by non-live-equivalent params | CRITICAL | All historical paper trades invalid for validation. G1-G5 require fresh start with locked params. |
| CF-5 | VanguardSniper has zero backtest evidence | CRITICAL | Primary strategy has no statistical validation. G6 blocked. |
| CF-6 | No GBP/USD FX tracking | CRITICAL | P&L reported in mixed currencies. ISA NAV calculation incorrect. G8 blocked. |
| CF-7 | No config.live.toml overlay mechanism | HIGH | Paper and live configs share same file. Risk of paper params leaking to live. G9 blocked. |
| CF-8 | Polygon API key committed to git history | HIGH | Credential exposure. Requires key rotation + git history rewrite or acceptance of risk. |
| CF-9 | No crash recovery validation | HIGH | Engine restart behaviour under partial WAL write not tested. Data corruption possible. |
| CF-10 | Fill quality model absent | MEDIUM | Paper fills assume instant execution at mid. Live fills will be worse. Edge may evaporate. |

---

## 5. REMAINING P0 WORK

Items required before the 63-day paper gauntlet can begin with live-equivalent parameters.

| Code | Item | Est. Hours | Blocks |
|---|---|---|---|
| N5b | Bar history persistence (5-min bars to SQLite/Parquet) | 8h | G6 backtest |
| N5c | Bridge SIGHUP hot-reload (Python-side config reload without restart) | 4h | Operational stability |
| N8a | config.live.toml overlay (separate live config, merged at boot) | 6h | G9 |
| N8b | Paper param reduction (lock params to live-equivalent values) | 4h | G1-G5 (fresh start) |
| N8c | GBP/USD FX tracking (live rate fetch, P&L conversion) | 6h | G8 |

**Total estimated:** 28 hours

**Dependency chain:** N8b must be done first (locks params), then N8c (FX), then N8a (live overlay). N5b and N5c are independent and can be parallelised.

---

## 6. GO/NO-GO RECOMMENDATION

### Verdict: CONDITIONAL GO for paper trading. HARD NO for live trading.

**Paper trading (APPROVED):**
- System is stable, deployed, and collecting data
- Ouroboros nightly loop is running and producing analysis
- All safety guards operational (IS_LIVE kill switch, chandelier exits, circuit breakers)
- Continue paper trading to accumulate data for validation

**Live trading (BLOCKED):**

The following must ALL be completed before live capital deployment:

1. **Complete remaining P0 work** (N5b, N5c, N8a, N8b, N8c) — 28h estimated
2. **Lock paper params to live-equivalent** (N8b) and restart trade count from zero
3. **Run 63-day paper gauntlet** with locked params (minimum calendar requirement)
4. **Pass all 10 validation gates** (Section 3) — zero exceptions
5. **Resolve all 7 OPEN critical findings** (Section 4.2)
6. **Build and pass integration test suite** (Section 7 gap)
7. **Rotate Polygon API key** and audit all secrets in git history
8. **CTO sign-off** on config.live.toml with line-by-line review
9. **CRO sign-off** on risk parameters (max position size, daily loss limit, circuit breaker thresholds)

**Estimated timeline to live readiness:** 28h code + 63 calendar days minimum = earliest Q2 2026.

**Risk if deployed live today:** Near-certain capital loss. No statistical evidence of edge. Paper data contaminated. FX tracking absent. Fill quality unknown.

---

## 7. TEST SUITE INVENTORY

| Category | Count | Status |
|---|---|---|
| Rust unit tests | 676 | 675 pass, 1 pre-existing failure |
| Python py_compile | All files | Clean (no syntax errors) |
| Integration tests | 0 | GAP — no end-to-end tests exist |
| Backtest harness | 0 | GAP — blocked by N5b |
| Load/stress tests | 0 | GAP — kill switch not tested under load |
| WAL corruption tests | 0 | GAP — CF-9 unresolved |

**Pre-existing Rust test failure:** 1 test fails consistently across all builds. Tracked but not blocking paper trading. Must be resolved before live.

**Integration test plan (not yet built):**
- Signal -> Order -> Fill -> Position -> Chandelier -> Exit full lifecycle
- WAL write -> crash -> restart -> WAL replay -> state recovery
- Config change -> SIGHUP -> hot-reload -> param verification
- Circuit breaker trigger -> halt -> recovery -> resume
- Kill switch API -> engine shutdown -> state preservation

---

## 8. DEPLOYMENT

### Current State

| Component | Detail |
|---|---|
| Base commit | f37d202 + pending v7.0 commit |
| EC2 | c7i-flex.large, us-east-1c, Elastic IP 3.230.44.22 |
| Docker Compose | 3 containers: aegis-v2, aegis-ib-gateway, aegis-redis |
| IB Gateway | gnzsnz/ib-gateway, port 4003, TRADING_MODE=live (for real market data) |
| Redis | Password-protected, internal Docker network only |

### Supercronic Schedule

| Task | Schedule | UTC |
|---|---|---|
| nightly_v6.py | Daily | 04:50 |
| config_writer.py | Daily | 04:51 |
| ticker_selector.py | Every 15 min | */15 * * * * |
| Session PDFs | At session opens | Market-dependent |

### Deployment Procedure (Mandatory)

```
git add <files>
git commit -m "<message>"
git push origin <branch>
rsync -avz --exclude-from=.rsyncignore . ubuntu@3.230.44.22:/home/ubuntu/nzt48-aegis-v2/
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
  "cd /home/ubuntu/nzt48-aegis-v2 && docker system prune -f && docker compose build && docker compose up -d"
```

Local, GitHub, and EC2 must ALWAYS be in sync. Never deploy without committing first.

---

## 9. CHANGELOG

### v7.0 (2026-03-20)
- N7a-h: 8 code items (SignalRejected wiring, BrainSignal extension, PositionClosed wiring, config ledger, missed-winner analysis, ticker scoreboard, backfill foundation, gate veto logging)
- 3 new Python modules: macro_event_layer.py, analytics_pack.py, research_store.py
- Unified master plan release candidate (this document)
- All new modules wired into nightly_v6.py Steps 5.8, 5.9, 5.10

### v6.0 (2026-03-20)
- N0a-e: Survival Stack deployed to EC2
- N1a-c: Cost-aware learning, trade taxonomy, ticker blacklist
- N2a-c: SignalRejected + MissedWinnerCandidate WAL types
- N3a: Structural Tradability Score
- N5a: WAL compressor
- RT1: Dynamic spread veto
- 5 specification documents delivered
- Ouroboros v6.0 nightly loop operational

### Prior (Builds 1-7)
- Rust engine core: order lifecycle, position tracking, WAL persistence
- Chandelier exit with 5-rung profit ladder
- Strategy quality gates (Build 6): 200-bar warmup, leverage-aware confidence, VWAP pullback, Hurst gate, volume slope, multi-timeframe confirmation, per-ticker cooldown
- 49 LSE leveraged/inverse ETPs with verified conIds
- 303 total contracts across 8 exchanges
- IBKR integration via ib-gateway container
- Redis state management
- Supercronic scheduling
- Dynamic weight generation via config_writer
- 676 Rust unit tests

---

## APPENDIX A: INSTRUMENT UNIVERSE

| Exchange | Count | Status |
|---|---|---|
| LSEETF | 49 | Active (primary) |
| SMART (US) | 70 | Active |
| TSE (Toronto) | 60 | Active |
| HKEX | 40 | Active |
| KRX | 39 | Broken (account restriction) |
| XETRA | 20 | Active |
| EURONEXT | 12 | Active |
| SGX | 10 | Active |
| Other | 3 | Active |
| **Total** | **303** | **264 active, 39 broken** |

**LSE currency note:** Most LSE leveraged ETPs trade in USD on LSEETF. Only 3LUS.L and 5SPY.L are GBP. All others require `currency="USD"` in contracts.toml.

---

## APPENDIX B: RISK PARAMETERS (Current Paper)

| Parameter | Value |
|---|---|
| Max position size | Per config (not yet locked to live-equivalent) |
| Daily loss limit | Per config |
| Circuit breaker | Implemented, state not persisted to Redis (gap) |
| Chandelier exit | 5-rung ladder, rung persistence via WAL |
| Leverage confidence floor | 65% standard, 80% for 3x+ |
| Per-ticker cooldown | 25 minutes |
| Spread veto | Dynamic, threshold per instrument |

**Note:** All risk parameters are paper-mode values. Live-equivalent params (N8b) not yet locked. Current paper trade data is therefore invalid for validation purposes.

---

*End of document. Next action: complete P0 work (N5b, N5c, N8a, N8b, N8c), lock params, begin 63-day gauntlet.*
