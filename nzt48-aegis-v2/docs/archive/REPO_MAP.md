# REPO_MAP.md — AEGIS V2 Institutional Audit
# SPREAD-ECONOMICS RE-AUDIT
**Generated:** 2026-03-19 | **Re-audit:** 2026-03-20 | **Version:** 2.0 (Spread-first rewrite)
**Board:** CTO, CRO, CIO, Head of Quant Research, Head of Execution, Head of Production/SRE, Head of Autonomous Intelligence Design

---

## Project Identity
- **Name:** AEGIS V2 — Autonomous Equity & Global Intelligence System
- **Path:** `/Users/rr/nzt48-signals/nzt48-aegis-v2/`
- **Stack:** Rust engine (Tokio async) + Python brain (Ouroboros) + IB Gateway + Redis
- **Target:** UK ISA leveraged/inverse ETPs, global multi-session rotation
- **Equity:** GBP 10,000 (paper), ISA annual limit GBP 20,000
- **Status:** Paper validation phase (20 trades, 79% WR — **cost-blind, non-representative**)
- **#1 Viability Threat:** Spread + commission drag at 0.50% round-trip per trade. 3 trades/day = 76% annual equity drag.

---

## Directory Structure (Spread-Annotated)

```
nzt48-aegis-v2/
├── rust_core/src/                # 79 Rust files, ~30K LOC
│   │
│   ├── ── COST-CRITICAL PATH ──────────────────────────────────────
│   ├── smart_router.rs           # ⚠ CostBreakdown struct (spread+FX+FTT+commission)
│   │                             #   ibkr_commission_gbp: 1.70 (line 79)
│   │                             #   direct_cost() pre-trade estimate (lines 168-193)
│   │                             #   NOT FED TO OUROBOROS — wiring gap
│   ├── engine.rs                 # Core orchestrator (2,890 LOC)
│   │                             #   final_pnl = unrealized - commission (line 1029)
│   │                             #   Paper spread_veto override → 2.0% (line 485) ⚠ TOO LOOSE
│   │                             #   PositionClosed emitted but LACKS spread_at_entry/exit
│   ├── risk_arbiter.rs           # 31-check fail-closed risk gate (461 LOC)
│   │                             #   Spread veto: spread_pct > spread_veto_pct (lines 192-202)
│   │                             #   NO min-gross-edge gate (expected_edge vs 2×spread)
│   │                             #   NO daily trade count limit
│   ├── exit_engine.rs            # Chandelier 5-rung profit ladder (749 LOC)
│   │                             #   round_trip_fee_pct = 0.003 for Rung 2 only (line 73)
│   │                             #   6/8 adaptive multipliers (lines 583-591)
│   ├── entry_engine.rs           # Kelly sizing, leverage caps, position limits
│   │                             #   Uses gross edge, NOT net-of-spread edge
│   ├── portfolio.rs              # Aggregate position state, heat calc
│   │                             #   CostBasisEntry: VWAP + total_commission (lines 11-46)
│   │                             #   NO spread tracking per position
│   │
│   ├── ── EXECUTION PATH ──────────────────────────────────────────
│   ├── main.rs                   # Binary entry, 8-step startup, 100ms event loop
│   │                             #   Live spread calc: (ask-bid)/bid*100 (lines 602-606)
│   ├── ibkr_broker.rs            # IB Gateway API client (orders, fills, data)
│   │                             #   ALWAYS .limit() — PROVEN (lines 1097, 1103)
│   ├── paper_broker.rs           # Paper execution simulator
│   │                             #   Commission = £1.50 (line 141) vs live £1.70
│   │                             #   NO spread simulation in fill price
│   │
│   ├── ── CORE INFRASTRUCTURE ─────────────────────────────────────
│   ├── crucible.rs               # Signal validation, confidence scoring
│   ├── config_loader.rs          # TOML parsing, hot-reload via SIGHUP
│   ├── clock.rs                  # LSE/US/Asian session windows, UK holidays
│   ├── wal_writer.rs             # Append-only WAL (CRC32, fsync)
│   ├── wal_replay.rs             # Crash recovery, position reconstruction
│   ├── garch_evt.rs              # GARCH(1,1) + EVT tail risk
│   ├── cross_asset_macro.rs      # VIX/DXY/Credit regime detection
│   ├── isa_gate.rs               # ISA whitelist enforcement
│   ├── hardening.rs              # 16 runtime invariants
│   ├── types/                    # Structs, enums, WAL event schemas
│   │   ├── wal.rs                # 17 WAL event types — LACKS cost fields in FillEvent
│   │   └── ...                   # config.rs, enums.rs, execution.rs
│   └── [55+ more modules]        # Sessions, broker, ML, telemetry, tests
│
├── python_brain/                  # Python subprocess modules
│   │
│   ├── ── COST-CRITICAL PYTHON ────────────────────────────────────
│   ├── bridge.py                  # 1,040 LOC: stdin/stdout signal generation
│   │                              #   12 gate vetoes logged (NDJSON)
│   │                              #   NO cost-aware gate
│   └── ouroboros/
│       ├── nightly_v6.py          # 1,010 LOC: Nightly learning loop
│       │                          #   analyze_trades() uses RAW PnL (lines 202-217) ⚠
│       │                          #   NO cost-adjusted WR/PF/Sharpe
│       │                          #   Bayesian blending 30/70 (line 399)
│       │                          #   OPTIMIZES GROSS, NOT NET — cost-blind
│       ├── config_writer.py       # 793 LOC: Generate dynamic_weights.toml
│       │                          #   Nightly median spread uses commission as proxy ⚠
│       │                          #   Guardrails: ±15% clamp, Kelly [0.15, 0.30]
│       ├── kelly_12factor.py      # Kelly sizing model
│       │                          #   Factor 8 (spread): max(1-spread*2, 0.1) ⚠ DECORATIVE
│       │                          #   0.20% spread → 0.996x reduction (0.4%)
│       │                          #   Base Kelly uses gross edge, NOT net
│       ├── autonomous_orchestrator.py # Strategy orchestration
│       │                          #   Per-strategy spread filters: 15-20 bps (lines 282-498)
│       │                          #   Only protection beyond risk_arbiter spread veto
│       ├── ticker_selector.py     # 1,419 LOC: 4-tier universe ranking
│       ├── persistent_memory.py   # 381 LOC: Cumulative system state
│       ├── indicator_intelligence.py # 1,016 LOC: Rule discovery
│       ├── session_pdf.py         # 447 LOC: Pre-market briefings
│       ├── sheets_sync.py         # Google Sheets drain
│       ├── telegram_notify.py     # Alerts + heartbeats
│       └── [10+ more modules]     # Calibration, scraping, simulation
│
├── config/
│   ├── config.toml                # 176 parameters
│   │                              #   max_simultaneous_positions = 15 (paper) ⚠ vs 3 live
│   │                              #   spread_veto_pct = 0.3 (live) / 2.0 (paper override)
│   │                              #   portfolio_heat_limit = 50% (paper) ⚠ vs 10% live
│   │                              #   NO max_daily_trades parameter
│   │                              #   NO min_gross_edge parameter
│   ├── contracts.toml             # 303 contracts (49 LSE + 70 US + 60 TSE + ...)
│   │                              #   216/303 have con_id=0 (unresolved)
│   ├── strategies.toml            # 5 strategies (S17-S21), 2 active
│   │                              #   confidence_floor = 55.0 (needs 65+ for paper)
│   ├── dynamic_weights.toml       # Auto-generated nightly (Kelly, regime, gates)
│   ├── initial_universe.toml      # Active watchlist (50 Vanguard tickers)
│   ├── spread_cache.toml          # Nightly spread data (commission proxy)
│   ├── uk_holidays.toml           # LSE closure dates 2026-2029
│   ├── fx_rates.toml              # GBP/USD/JPY/SGD (6-hourly refresh)
│   └── [5 more files]             # Universe classification, GARCH params, etc.
│
├── data/
│   ├── ouroboros_recommendations.json  # Latest optimizer output (COST-BLIND)
│   ├── system_memory.json         # Cumulative learnings (persistent)
│   ├── indicator_intelligence.json # Discovered gates
│   ├── gate_vetoes.ndjson         # Real-time veto logging
│   └── ouroboros_reports/         # Daily PDFs, metrics, battle plans
│
├── events/                        # WAL volume (NDJSON, append-only)
│   ├── current.ndjson             # Today's events (LACKS cost fields)
│   └── archive/                   # Rotated WALs (7-day retention)
│
├── Dockerfile                     # Multi-stage: Python 3.12 + Rust + Supercronic
├── docker-compose.yml             # 3 containers: engine + IB Gateway + Redis
├── entrypoint.sh                  # Startup: supercronic → config_writer → engine
├── crontab                        # 22 scheduled jobs (nightly, 15-min, session)
├── .env.production                # IBKR creds, API keys, Telegram tokens
└── deploy/                        # EC2 deployment scripts
```

---

## Key Metrics

| Metric | Value |
|--------|-------|
| Rust source files | 79 |
| Rust LOC | ~30,000 |
| Python modules | 23 |
| Python LOC | ~6,400 |
| Config parameters | 176 |
| Total contracts | 303 (216 unresolved con_id=0) |
| Active ISA funds | 12 |
| Strategies defined | 5 (2 active) |
| Risk checks | 31 |
| Runtime invariants | 16 |
| Cron jobs | 22 |
| Docker containers | 3 |

---

## Cost Model Reality Check

| What Exists | Where | Status |
|-------------|-------|--------|
| CostBreakdown struct (spread+FX+FTT+commission) | smart_router.rs:28-46 | ✅ EXISTS but not fed to learning |
| Pre-trade cost estimate | smart_router.rs:168-193 | ✅ EXISTS but not used as gate |
| IBKR commission (£1.70) | smart_router.rs:79 | ✅ HARDCODED |
| Paper commission (£1.50) | paper_broker.rs:141 | ✅ HARDCODED |
| Spread veto gate (0.3% live) | risk_arbiter.rs:192-202 | ✅ EXISTS |
| Commission in final PnL | engine.rs:1029 | ✅ TRACKED |
| Per-position commission tracking | portfolio.rs:11-46 | ✅ TRACKED |
| Rung 2 breakeven includes 0.3% RT fee | exit_engine.rs:73 | ✅ EXISTS |
| **Daily trade count limit** | **NOWHERE** | ❌ **MISSING** |
| **Min gross edge gate (edge > 2×spread)** | **NOWHERE** | ❌ **MISSING** |
| **Spread at fill in WAL events** | **types/wal.rs** | ❌ **MISSING** |
| **Cost-adjusted WR/PF in Ouroboros** | **nightly_v6.py** | ❌ **MISSING** |
| **Ouroboros NET optimization** | **nightly_v6.py** | ❌ **MISSING — uses GROSS** |
| **Paper spread simulation in fills** | **paper_broker.rs** | ❌ **MISSING** |
| **Kelly net-edge calculation** | **kelly_12factor.py** | ❌ **MISSING — uses GROSS** |

---

## Critical File Reference

### Must-Read for Any Audit (Spread-Priority Order)
1. `smart_router.rs` — CostBreakdown model (exists but disconnected from learning)
2. `risk_arbiter.rs` — 31 risk checks + spread veto (no frequency limit)
3. `engine.rs` — the beating heart (2,890 LOC) + paper spread override
4. `exit_engine.rs` — Chandelier exit + 0.3% RT fee in Rung 2 only
5. `kelly_12factor.py` — Factor 8 proven decorative (0.4% reduction)
6. `nightly_v6.py` — Ouroboros learning loop (COST-BLIND)
7. `config.toml` — 176 parameters (no max_daily_trades, no min_edge)
8. `bridge.py` — Python signal generation (1,040 LOC)
9. `strategies.toml` — 5 strategy definitions
10. `dynamic_weights.toml` — Ouroboros-generated config

### Hot-Path Execution Chain (Cost-Annotated)
```
IB Gateway tick → engine.rs:drain_ticks()
  → main.rs:602 spread calc (ask-bid)/bid
  → bridge.py:process_tick() → VanguardSniper/Orchestrator
  → 15+ gates (NO cost gate, NO frequency gate)
  → risk_arbiter.rs:evaluate() → 31 checks
    → spread_veto: spread > 0.3% (live) / 2.0% (paper) ⚠
    → NO: daily_trade_count check
    → NO: expected_edge > 2×spread check
  → entry_engine.rs:size() → Kelly from GROSS edge ⚠
  → smart_router.rs:direct_cost() → CostBreakdown computed but NOT gating
  → ibkr_broker.rs:submit_order() → ALWAYS .limit()
  → exit_engine.rs:evaluate() → Rung 2 includes 0.3% RT fee
  → wal_writer.rs:append() → PositionClosed (NO spread fields)
```

### Learning Loop Chain (Cost Gaps)
```
WAL events (NO cost fields on fills)
  → nightly_v6.py:analyze_trades() → uses RAW PnL ⚠ cost-blind
  → indicator_intelligence.py → rule discovery (gross-based)
  → persistent_memory.py:record_trade() → no cost categorization
  → config_writer.py:run() → spread from COMMISSION PROXY ⚠ not real spread
  → dynamic_weights.toml → SIGHUP → engine hot-reload

⚠ THE LOOP NEVER LEARNS FROM COSTS
  CostBreakdown in smart_router.rs is NEVER fed back
  Ouroboros cannot distinguish cost-killed vs market-killed trades
  "L5 Spread Victim" (gross>0, net≤0) is invisible
```

---

## Paper vs Live Configuration Divergence

| Parameter | Paper | Live | Risk |
|-----------|-------|------|------|
| max_simultaneous_positions | 15 | 3 | 5x overtrading in validation |
| spread_veto_pct | 2.0% (override) | 0.3% | Paper accepts 7x worse spreads |
| portfolio_heat_limit | 50% | 10% | Paper takes 5x more risk |
| commission | £1.50 | £1.70 | Minor (12% undercount) |
| **max_daily_trades** | **∞ (no limit)** | **∞ (no limit)** | **BOTH MISSING** |
| **min_gross_edge** | **none** | **none** | **BOTH MISSING** |
| confidence_floor | 55 | 55 | Should be 65+ for paper |

**Implication:** Paper mode at current settings generates 5-15 trades/day of non-representative data at catastrophic simulated cost. Paper validation is MEANINGLESS until config matches live economics.

---

**Document Version:** 2.0 — SPREAD-FIRST REWRITE
**Re-audit:** 2026-03-20
**Status:** Updated with cost-model reality check and spread annotations
**Companion docs:** IMPLEMENTATION_MASTER_PLAN.md (v3.0), IMPLEMENTATION_MASTER_PLAN_RC1.md (RC5), RUNTIME_ARTIFACT_MAP.md (v2.0)
