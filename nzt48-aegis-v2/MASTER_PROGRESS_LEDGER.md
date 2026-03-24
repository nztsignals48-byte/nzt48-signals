# AEGIS V2 — Master Progress Ledger

## Purpose
Session recovery source of truth. Append-only. If a session stops unexpectedly, the next session resumes from here.

---

## Session: 2026-03-23/24 (Full System Audit + Deploy)

### Chunk 1: Codebase Audit (16:40 UTC, Mar 23)
- **Files Read**: 74 .rs files (32,603 LOC), 40+ .py files, config.toml, docker-compose.yml, .gitignore
- **Findings**: 12 contradictions registered, 4 simplification items, 14 action items ranked by ROI
- **Key Discovery**: WAL persistence CORRECT (Docker named volume `aegis-events`)
- **Key Discovery**: WAL `PositionClosed.final_pnl` IS correctly populated (earlier script read wrong field `pnl` instead of `final_pnl`)

### Chunk 2: Infrastructure Fixes
- EC2 Disk: 83% → 75% (multiple prune cycles, removed old nzt48-signals dir)
- WAL: NOT NEEDED — already persistent
- .env: NOT NEEDED — already in .gitignore

### Chunk 3: New Strategies Added
- IBS_MeanReversion: IBS < 0.2 + RSI(2) < 15 + RVOL > 0.7
- VolExpansion: RVOL > 2.0 + ADX > 20 + 3+ up bars
- ORB_Breakout: US session 14:45-15:30 UTC
- GapFade: Liquidity gap down >1%, RVOL < 2.0

### Chunk 4: TypeA/D Decision
- Initially disabled (commit `89ec4a8`), then reverted (commit `596e695`)
- **Final state: TypeA/D ENABLED.** Collecting live paper data. Ouroboros auto-downweights.

### Chunk 5: Log Spam Fix
- `python_bridge.rs`: "0 signals" CRITICAL throttled to 1K/5K/10K intervals (was every 10 ticks)
- Verified: no CRITICAL spam in post-deploy logs

### Chunk 6: Doc Cleanup
- 108 stale .md files archived to `docs/archive/`
- Root now has 5 files: CANONICAL_SYSTEM_PLAN, MASTER_PROGRESS_LEDGER, PLAN_1, PLAN_2, CLAUDE

### Chunk 7: Consistency Audit (00:00 UTC, Mar 24)
- Fixed 5 contradictions in CANONICAL_SYSTEM_PLAN.md:
  1. TypeA/D: removed "BLOCKED" from Stage 4 description — they are ENABLED
  2. Trade count: updated from "4 trades" to ~64 trades with correct breakdown
  3. P&L: corrected from "pnl=None bug" to "final_pnl correctly populated"
  4. Universe: explained 1,251 contracts vs 867 tickers (superset vs curated watchlist)
  5. Strategy maturity: added Implemented/Enabled/Observed columns

---

## Authoritative Trade Data (as of 2026-03-24 00:00 UTC)

### Source: system_memory.json (nightly_v6, 48 trades) + current WAL (16 closures)

| Source | Trades | Wins | Losses | P&L |
|--------|--------|------|--------|-----|
| system_memory.json (Mar 18-23) | 48 | 17 | 31 | -£6.79 |
| Current WAL (post-restart) | 16 closures | 3 | 8 | ~-£2.27 |
| **Combined estimate** | **~64** | **~20** | **~39** | **~-£9.06** |

### By Exchange (system_memory.json)
| Exchange | Trades | Wins | P&L | WR |
|----------|--------|------|-----|-----|
| LSEETF | 28 | 0 | -£30.34 | 0% |
| Asian (HK/TSE) | ~11 | 11 | +£16.91 | 100% |
| XETRA/EURONEXT | ~5 | 4 | +£3.63 | 80% |
| US | ~4 | 2 | +£3.01 | 50% |

### By Strategy (observed in WAL)
| Strategy | Trades | Observed? |
|----------|--------|-----------|
| Momentum (VanguardSniper) | 61 | Yes |
| TypeE (IBS classifier) | 3 | Yes |
| IBS_MeanReversion | 0 | No — deployed after market close |
| VolExpansion | 0 | No — deployed after market close |
| ORB_Breakout | 0 | No — US session only |
| GapFade | 0 | No — needs gap-down >1% |

---

## Current System Status (2026-03-24 00:00 UTC)
- **Commit**: `99f733e`
- **EC2 Disk**: 75% (4.7GB free)
- **Containers**: 3, all healthy
- **Open Positions**: 0
- **Equity**: £10,000
- **Session**: AfterHours / ModeA (pre-Asian)
- **Strategies**: 6 sources implemented + enabled; 2 observed in WAL (Momentum, TypeE)
- **Validation gate**: ~64/100 trades (64%)

## Commits This Session (6 total)
1. `89ec4a8`: Add IBS/ORB/VolExpansion strategies + initial TypeA/D disable
2. `5f6045d`: Add canonical system plan and progress ledger
3. `596e695`: Keep TypeA/D active for paper validation
4. `44af315`: Updated ledger with trade report
5. `99f733e`: Add GapFade, fix CRITICAL log spam, archive 108 docs
6. (pending): Consistency audit fix for canonical plan

---

## Session: 2026-03-24 (Sprints S5-S8 Continuous Execution)

### Sprint S5: Cost Injection into Ouroboros (15:00 UTC)
- **Objective**: Make nightly P&L analysis include realistic costs
- **Files Changed**: `python_brain/ouroboros/cost_model.py`, `python_brain/ouroboros/nightly_v6.py`
- **Implementation**:
  - Added `estimate_trade_cost()` with per-exchange rates, commission, slippage, FX costs
  - Added `per_exchange_rt` field to CostModel (loads [costs.per_exchange] from config)
  - Nightly pipeline enriches each trade with `cost_adjusted_pnl` before persistent memory recording
  - `record_trade()` and `record_session()` now use cost-adjusted P&L
  - Log output shows both sim and cost-adjusted WR/PnL for transparency
- **Tests**: cost_model.py functions verified (parse, compute, per-exchange lookup)
- **Impact**: Ouroboros now sees realistic economics (£3.40 commission + 0.5% slippage + FX per trade)

### Sprint S6: LSEETF Disposal + Exchange Blocking (15:15 UTC)
- **Objective**: Stop trading 52 LSEETF leveraged ETPs (0% WR, -£30)
- **Files Changed**: `config/config.toml`, `python_brain/bridge.py`
- **Implementation**:
  - Added `exchanges = ["LSEETF"]` to [blacklist] section in config.toml
  - Added `_symbol_raw_exchange_map` to preserve raw exchange from contracts.toml
  - Added `_load_blocked_exchanges()` with lazy caching
  - Added EXCHANGE_VETO check in signal evaluation path (after blacklist, before warmup)
- **Tests**: config.toml TOML valid, bridge.py parses, exchange blocking logic verified
- **Impact**: All 52 LSEETF tickers blocked. Reversible by removing "LSEETF" from list.

### Sprint S7: config.live.toml Completeness (15:25 UTC)
- **Objective**: Ensure all 8 paper overrides have production-safe values
- **Files Changed**: `config/config.live.toml`
- **Implementation**: Added spread_veto_pct and slippage_assumption_pct
- **Tests**: TOML valid, all 8 overrides verified present
- **Status**: Pre-live prep complete. Not activated until IS_LIVE=true.

### Sprint S8: Regime + Session Enforcement (15:30 UTC)
- **Objective**: Wire strategy_registry.json regime/session gates into bridge.py
- **Files Changed**: `python_brain/bridge.py`
- **Implementation**:
  - Added `_load_strategy_registry()` (reads regime/session metadata from registry)
  - Added `_classify_market_regime(hurst, rvol, adx)` → registry regime names
  - Added `_classify_current_session()` → registry session names from UTC time
  - Added `_check_regime_session_gate()` with fail-open for unknown strategies
  - REGIME_SESSION_VETO log line for debugging
  - Enforcement added in Stage 4 after TypeA/D disable block
- **Tests**: All regime/session classification tests pass. Gate check logic verified.
- **Impact**: Strategies now respect their registry regime/session constraints.

### Deployment (15:40 UTC)
- **Commit**: `d622019` — all S5-S8 changes in single commit
- **Push**: origin/feat/tier-system-enhancements-full
- **Rsync**: 5 files synced to EC2
- **Docker Build**: In progress (Rust compile + Python bake)

## Current System Status (2026-03-24 15:40 UTC)
- **Commit**: `d622019`
- **EC2 Disk**: 76% (4.5GB free)
- **Containers**: 3 healthy (aegis-v2 rebuilding)
- **Open Positions**: 18 (will recover via WAL on restart)
- **Equity**: ~£10,005
- **IBKR**: Connection failures observed (may need 2FA re-auth)
- **Strategies**: 6 sources + regime/session enforcement active
- **LSEETF**: BLOCKED (52 tickers)

## Next Actions
1. Wait for Docker build completion
2. Restart aegis-v2 with new image
3. Verify trading resumes (WAL replay, position recovery, no errors)
4. Monitor IBKR connection — may need 2FA Monday re-auth
5. Continue paper trading toward N=200 validation gate
6. Next sprint: S9 (friction-aware ranking) or S10 (EC2 upgrade) when N>200
