# AEGIS V2 — Canonical Master Plan
**Date**: 2026-03-24 (updated 2026-03-24 session 3 — Microstructure Sprint)
**Status**: COMPLETE — all 22 sections written, S1+S2+S3(Microstructure) sprints executed
**Supersedes**: All prior MASTER_PLAN, IMPLEMENTATION_PLAN, PLAN_1, PLAN_2 docs (now in docs/archive/)
**Source of truth hierarchy**: Code > Runtime > This plan > Old docs

---

## 1. Executive Summary

AEGIS V2 is a paper-trading system running on EC2 (c7i-flex.large, 4GB RAM, 19GB disk). It processes live IBKR market data through a Rust execution engine (32,603 LOC) with a Python signal brain (1,850 LOC bridge). IS_LIVE=false is hardcoded — the system cannot place real orders.

**Current performance**: 48 nightly-tracked trades, 35.4% WR, -£6.79 cumulative P&L. LSEETF leveraged ETPs are the primary loss source (0% WR, -£30.34 over 28 trades). Asian equities showed 100% WR (+£16.91) but this likely reflects paper-mode mid-point fill artifacts.

**Session 2 (2026-03-23/24)**: 12 commits. Full codebase audit. 4 new strategies deployed. Ouroboros frozen. Gemini API key configured. IBS thresholds loosened. 108 stale docs archived. Brutal-truth 11-page PDF generated.

**Session 3 — Microstructure Sprint (2026-03-24)**: 4 commits, 3 Rust rebuilds, 3 EC2 deployments. Board lot sizing (TSE/HKEX/SGX=100-share lots). L1 data quality gate (bypassed in sim mode after discovering IBKR paper limits ~7 concurrent tick-by-tick streams). Unhalt grace period (active_trading_ticks reset on halt lift). Spoof detector calibrated (25x multiplier + 2% absolute floor — was killing 86% of signals as false positives). EC2 live config prepared (c7i.large non-burstable). System trading again: STAN.L and AI (Air Liquide) entries observed.

---

## 2. Current Verified System State

| Fact | Value | Evidence |
|------|-------|----------|
| Branch | `feat/tier-system-enhancements-full` @ `251b263` | `git log --oneline -1` |
| Containers | 3 healthy (aegis-v2, ib-gateway, redis) | `docker ps` on EC2 |
| Disk | 75% (4.7GB free / 19GB) | `df -h /` on EC2 |
| IB Gateway | Connected, 2FA approved | Subscriptions active |
| Subscriptions | 100 reqMktData + 100 L1 feeds | Engine logs |
| Python Bridge | Running, no errors | bridge_stderr.log |
| Ouroboros | FROZEN (observe_only=true) | config.toml + startup log |
| Gemini | API key SET (length 39) | `$GEMINI_API_KEY` check |
| Claude | CLI at /usr/bin/claude, shadow mode | entrypoint.sh |
| Strategies deployed | 6 sources + TypeA-F classification | bridge.py |
| Strategies observed | 2 (Momentum: 33 trades, TypeE: 3 trades) | WAL data |
| Equity | £10,000 | system_memory.json |
| Open positions | 0 | Heartbeat |
| Validation gate | ~64/100 trades (35.4% WR, ~0.77 PF) | system_memory.json + WAL |

---

## 3. Authoritative Source-of-Truth Hierarchy

| Rank | Source | What it governs |
|------|--------|----------------|
| 1 | Rust code (`rust_core/src/`) | Execution, risk, exits, WAL, broker |
| 2 | Python code (`python_brain/`) | Signals, sizing, classification, learning |
| 3 | Config files (`config/`) | Parameters, thresholds, universe |
| 4 | EC2 runtime state | What's actually deployed and running |
| 5 | WAL events (`/app/events/`) | Trade history, P&L |
| 6 | system_memory.json | Nightly-aggregated performance |
| 7 | This plan | Architecture decisions, roadmap |
| 8 | AEGIS_V2_CURRENT_STATE_OPERATING_MANUAL.md | Current-state documentation |
| 9 | Old plan docs (docs/archive/) | Historical context only, NOT authoritative |

---

## 4. Runtime Ownership Map

| Concern | Owner | Authority | Files |
|---------|-------|-----------|-------|
| Tick processing | Rust engine.rs | Final | engine.rs:891 |
| Entry gating (32 checks) | Rust risk_arbiter.rs | Final, deterministic | risk_arbiter.rs |
| Exit decisions | Rust exit_engine.rs | Final, Chandelier | exit_engine.rs |
| Signal generation | Python bridge.py | 6 sources, best-by-confidence | bridge.py:1257 |
| Entry type classification | Python bridge.py Stage 4 | Runtime authority | bridge.py:601 |
| Entry type thresholds | config.toml [entry_types] | Single source | config.toml:418 |
| Position sizing | Python kelly_12factor.py | 12-factor + sim costs | kelly_12factor.py |
| Nightly learning | Python nightly_v6.py | Analysis only (FROZEN) | nightly_v6.py |
| Config generation | Python config_writer.py | FROZEN (observe_only) | config_writer.py |
| Universe curation | Python ticker_selector.py + Gemini | Rotation priority | ticker_selector.py |
| Signal challenge | Claude curator | Shadow only, non-blocking | claude_curator.py |

---

## 5. Strategy Inventory

| Strategy | Status | Python path | Rust path | Config | Observed | Trades | Verdict |
|----------|--------|-------------|-----------|--------|----------|--------|---------|
| VanguardSniper | **LIVE** | bridge.py:1302 | N/A | brain/config.py | YES | 33 | Core. Only proven producer. |
| Orchestrator | **LIVE** | bridge.py:1318 | N/A | strategies.toml | NO | 0 | Needs strategies.toml (exists, 16KB) |
| IBS_MeanReversion | **LIVE** | bridge.py:1342 | N/A | Inline thresholds | NO | 0 | Loosened 2026-03-24. Monitor. |
| VolExpansion | **LIVE** | bridge.py:1370 | N/A | Inline thresholds | NO | 0 | Needs RVOL > 2.0 + ADX > 20. |
| ORB_Breakout | **LIVE** | bridge.py:1401 | N/A | Inline thresholds | NO | 0 | US session only (14:45-15:30 UTC). |
| GapFade | **LIVE** | bridge.py:1440 | N/A | Inline thresholds | NO | 0 | Needs gap-down > 1% + RVOL < 2. |
| TypeA (DipRecovery) | **LIVE (monitored)** | bridge.py:638 | entry_engine.rs (ref) | config.toml:425 | NO | 0 | 29.5% backtest WR. Collecting data. |
| TypeB (EarlyRunner) | **LIVE** | bridge.py:624 | entry_engine.rs (ref) | config.toml:429 | NO | 0 | 52.4% backtest WR. Best theoretical. |
| TypeC (OverboughtFade) | **LIVE** | bridge.py:633 | entry_engine.rs (ref) | config.toml:433 | NO | 0 | Needs RSI > 80 (rare). |
| TypeD (SupportBounce) | **LIVE (monitored)** | bridge.py:643 | entry_engine.rs (ref) | config.toml:435 | NO | 0 | 24.1% backtest WR. Collecting data. |
| TypeE (IBS) | **LIVE** | bridge.py:621 | entry_engine.rs (ref) | config.toml:441 | YES | 3 | Observed. Classifier fires. |
| TypeF (OBVDivergence) | **LIVE** | bridge.py:617 | entry_engine.rs (ref) | config.toml:445 | NO | 0 | Needs vol_div < -0.5. |

---

## 6. Contradiction Register

| ID | Subsystem | What docs say | What code/runtime says | Severity | Fix |
|----|-----------|--------------|----------------------|----------|-----|
| C01 | Gemini | Manual still has "BROKEN" in one table row | API key is SET, verified on EC2 | MEDIUM | Fix remaining table row in manual |
| C02 | Strategy mix | Backtest says TypeB is best | Production: 0 TypeB signals observed | HIGH | Investigate threshold alignment |
| C03 | Rust entry_engine | Looks active (786 LOC, compiles) | NOT used at runtime (Python classifies) | MEDIUM | Add "NOT RUNTIME" comment block |
| C04 | dynamic_weights | Local file: WR 79.2%, 20 trades | EC2 deployed: WR 36.5%, 48 trades | LOW | config.toml observe_only=true fixes drift |
| C05 | Paper fills | Asian 100% WR suggests mid-point fills | **RESOLVED**: Fills use ask/bid (realistic). Asian WR is small-sample noise. | ~~HIGH~~ CLOSED | S1 audit complete |
| C06 | Profit Factor | system_memory shows PF=0.0 | **RESOLVED**: persistent_memory.py never computed PF. Fixed 2026-03-24. | ~~MEDIUM~~ CLOSED | S2 fix deployed |

---

## 7. Pre-Live Blockers (MUST fix before any live capital)

| # | Blocker | Why | Sprint |
|---|---------|-----|--------|
| 1 | IS_LIVE=false hardcoded | Cannot trade live without Rust recompile | Rust rebuild |
| 2 | 8 paper overrides in config.toml | max_positions=999, heat=50%, etc. | Config revert |
| 3 | No time-stop | Capital locked in sideways positions | Rust exit_engine change |
| 4 | WR 35.4% (need 40%) | Below validation gate | More trades needed |
| 5 | PF ~0.77 (need 1.3) | Below validation gate | Strategy improvement |
| 6 | 14 consecutive losses (need <8) | Below validation gate | Better entry filtering |
| 7 | Paper fill realism unverified | May be trading against mid-point illusion | Audit paper_broker.rs |
| 8 | Only 1 of 6 strategies proven | Insufficient strategy diversification | Run 300+ trades |
| 9 | Ouroboros on N=48 data | ML loop frozen, needs N=300 | Wait for trades |
| 10 | EC2 4GB RAM | OOM risk under market stress | Upgrade to 8GB |

---

## 8. ROI-Ranked Action Backlog

| Priority | Action | ROI | Blocked? | Sprint |
|----------|--------|-----|----------|--------|
| CRITICAL | Verify paper fill realism (mid-point vs bid/ask) | Safety | No | S1 |
| CRITICAL | Fix nightly PF=0.0 calculation bug | Correctness | No | S2 |
| HIGH | Implement time-stop in Rust exit_engine | Capital efficiency | Disk (need 5GB for Rust rebuild) | S3 |
| HIGH | Investigate why TypeB never fires in production | Strategy coherence | No | S4 |
| HIGH | Upgrade EC2 to 8GB RAM (m7i-flex.large) | Operational safety | AWS action | S5 |
| MEDIUM | Add dead-code quarantine comments to entry_engine.rs | Future-proofing | Rust rebuild | S3 |
| MEDIUM | Create config.live.toml with reverted paper overrides | Deployment safety | No | S6 |
| MEDIUM | Add spread-at-fill tracking to paper trades | Fill quality audit | No | S7 |
| LOW | Verify Gemini scanner produces valid output | Gemini integration | No | S8 |
| LOW | Archive remaining untracked files | Repo hygiene | No | S9 |

---

## 9. Chunked Implementation Sprints

### Sprint S1: Paper Fill Audit — COMPLETED 2026-03-24
- **Goal**: Determine if fills are mid-point or realistic bid/ask
- **Finding**: Paper fills use ASK for entry, BID for exit (realistic side-crossing)
- **Critical detail**: `simulation_mode` bypasses `paper_broker.rs` entirely. Engine creates `SimulatedTrade` directly with `fill_price_gbp = tick.ask` (engine.rs:1887). Exits use `tick.bid` (engine.rs:1360).
- **Missing realism**: Zero slippage, zero commission (in sim path), no partial fills, instant execution
- **Asian 100% WR verdict**: NOT a mid-point artifact. The ask/bid pricing is correct. The 100% WR is likely small-sample noise (5 trades)
- **Recommendation**: P&L is optimistic by ~spread cost per trade. Acceptable for paper validation. Add slippage sim before live.
- **Pre-live**: COMPLETED (audit), remaining: add slippage simulation before live

### Sprint S2: Fix Profit Factor Calculation — COMPLETED 2026-03-24
- **Goal**: Fix PF=0.0 bug in system_memory.json
- **Root cause**: `persistent_memory.py:record_trade()` never updated `all_time_profit_factor`. It was initialized to 0.0 and never written. The daily PF in nightly_v6.py was CORRECT — the bug was only in the cumulative persistent memory.
- **Fix**: Added `cumulative_gross_wins` and `cumulative_gross_losses` fields to `SystemMemory` dataclass. `record_trade()` now tracks both and computes `all_time_profit_factor = gross_wins / gross_losses`.
- **Files changed**: `python_brain/ouroboros/persistent_memory.py`
- **Backward-compatible**: Old system_memory.json loads fine — new fields default to 0.0. PF tracks correctly from next trade onward. Historical PF will be slightly wrong until new trades dominate.
- **Verified**: Unit test — £10 win, £5 loss = PF 2.00 (correct)
- **Pre-live**: COMPLETED

### Sprint S3: Microstructure Sprint — COMPLETED 2026-03-24
- **Goal**: Close gaps between paper simulation and physical market mechanics
- **Delivered** (4 commits, 3 Rust rebuilds, 3 EC2 deployments):
  1. Board lot sizing: `min_lot_for_exchange()` shared utility in broker.rs. Engine rounds qty to 100-share lots for TSE/HKEX/SGX before order. Sub-lot = LOT_SKIP.
  2. L1 data quality gate: `l1_subscribed_set` tracks tickers with true tick-by-tick data. Gate enforced in live mode only — bypassed in sim mode because IBKR paper supports only ~7 concurrent L1 streams (error 10190 kills the rest async).
  3. Unhalt grace period: `active_trading_ticks` reset to 0 when halt lifts — prevents aggressive 0.3x ATR time-stop from firing on post-unhalt auction noise.
  4. Spoof detector calibration: Raised multiplier 10x→25x, added 2% absolute spread floor. Was false-positive-ing 86% of signals (426/496 vetoes) on normal MktData bid-ask noise.
  5. EC2 live config: `terraform/variables.live.tfvars` with c7i.large (non-burstable).
- **Also delivered from prior session**: Time-stop (45min, 0.3x ATR), strategy registry, PF fix, validation counter reset.
- **Pre-live**: COMPLETED

### Sprint S4: TypeB Investigation (1 hour)
- **Goal**: Determine why TypeB (best backtest, 52.4% WR) has 0 production signals
- **Files**: `python_brain/bridge.py:624-630` (classify_entry_type)
- **Hypothesis**: 3-bar rising RVOL is too strict on 5-min bars with synthetic tick data
- **Test**: Log TypeB threshold checks to gate_vetoes.ndjson
- **Pre-live**: HIGH priority

### Sprint S5: EC2 Instance Upgrade (15 min)
- **Goal**: Upgrade to m7i-flex.large (8GB RAM, same 2 vCPU, free-tier compatible)
- **Method**: Stop instance → change type → start → 2FA
- **Risk**: Downtime during resize (~5 min)
- **Pre-live**: MANDATORY

### Sprint S6: Create config.live.toml (15 min)
- **Goal**: Create a live-mode config overlay that reverts all 8 paper overrides
- **Files**: `config/config.live.toml` (new)
- **Content**: max_positions=3, heat=10%, daily_trades=3, etc.
- **Pre-live**: MANDATORY

### Sprint S7: Spread-at-Fill Tracking + Synthetic Cost Injection (1 hour)
- **Goal**: Track actual spread at entry/exit in WAL. Inject synthetic slippage + commission into Ouroboros learning.
- **Why**: Ouroboros currently trains on zero-cost P&L. When enabled at N=300, it must learn from net-of-cost reality.
- **Files**: `persistent_memory.py`, `nightly_v6.py`, `engine.rs` (WAL spread field)
- **Pre-live**: HIGH (must be done before enabling Ouroboros learning)
- **Source**: Gemini G4 (ACCEPTED), ChatGPT C6 (ACCEPTED)

### Sprint S8: Friction-Aware Signal Ranking (1 hour)
- **Goal**: Rank simultaneous signals by net expected P&L (gross edge - spread - commission - slippage)
- **Why**: When TypeB starts firing alongside VanguardSniper, the engine needs to pick the best signal, not the first.
- **Files**: `bridge.py` (signal scoring), `engine.rs` (multi-signal arbitration)
- **Dependency**: Sprint S4 (TypeB fires) + Sprint S7 (cost tracking)
- **Source**: ChatGPT C6 (ACCEPTED)

### Sprint S9: Per-Strategy Asymmetric Exits (1 hour)
- **Goal**: Configure different Chandelier ATR multipliers and time-stop thresholds per strategy family.
- **Why**: Momentum continuation (TypeB) needs tighter trail than broader momentum (VanguardSniper).
- **Infrastructure exists**: `strategy_config.rs` already supports per-strategy exit params.
- **Dependency**: Sprint S4 (TypeB trades exist for comparison)
- **Source**: ChatGPT C8 (ACCEPTED)

### Sprint S10: Regime + Session Enforcement (2 hours)
- **Goal**: Wire `strategy_registry.json` regime_allowed/blocked/reduced and session_allowed/blocked into runtime signal evaluation.
- **Why**: Registry has the metadata but it's not enforced. Strategy X should only fire in regime Y during session Z.
- **Files**: `bridge.py` (signal evaluation gate), `engine.rs` (regime forwarding to bridge)
- **Dependency**: More trades (regime samples)
- **Source**: ChatGPT C3+C4 (ACCEPTED)

### Sprint S11: Symbol-Quality Memory + Net Expectancy Metrics (1 hour)
- **Goal**: Track per-ticker quality score (spread stability, win rate, slippage). Add net expectancy per strategy to Section 2.
- **Why**: Tickers that consistently lose should be deprioritized. Strategies need net expectancy, not just WR.
- **Files**: `persistent_memory.py`, `nightly_v6.py`, this plan Section 2
- **Dependency**: N=200+ trades for meaningful per-ticker samples
- **Source**: ChatGPT C5+C12 (ACCEPTED)

### Sprint S12: Cost-Honest Backtests (1 hour)
- **Goal**: Ensure fast_backtest_pipeline.py includes IBKR tiered commissions + configurable slippage.
- **Why**: Backtest results that exclude costs are misleading. Any strategy promotion decision must be cost-honest.
- **Files**: `python_brain/ouroboros/fast_backtest_pipeline.py`
- **Source**: ChatGPT C9 (ACCEPTED)

---

## 10. Daily Operating Workflow

| Time (UTC) | Action | Command |
|------------|--------|---------|
| 07:00 | Pre-market: check containers | `ssh EC2 'docker ps'` |
| 07:00 | Check overnight errors | `docker logs aegis-v2 --tail 20` |
| 07:00 | 2FA if Monday | IBKR mobile app |
| 08:00 | LSE open: watch for signals | `grep SIGNAL_ARRIVED` in logs |
| 14:30 | US open: watch for ORB signals | `grep ORB_Breakout` in bridge_stderr |
| 16:25 | LSE close: EodFlatten fires | Check WAL for PositionClosed events |
| 21:00 | Post-market: daily P&L | `grep HEARTBEAT` in logs |
| 21:15 | Daily sim report (cron) | Check Telegram |
| 04:50 | Nightly pipeline (cron) | Check next morning |

---

## 11. Recovery Procedures

### Kill Switch
```bash
ssh EC2 'docker exec aegis-v2 touch /app/KILL'    # Halt trading
ssh EC2 'docker exec aegis-v2 touch /app/PAUSE'   # Flatten + halt
ssh EC2 'cd ~/nzt48-aegis-v2 && docker compose down'  # Full stop
```

### Restart
```bash
ssh EC2 'docker exec aegis-v2 rm -f /app/KILL /app/PAUSE'
ssh EC2 'cd ~/nzt48-aegis-v2 && docker compose up -d'
```

### Full Rebuild (Python-only changes)
```bash
git push && rsync ... && ssh EC2 'docker compose build aegis-v2 && docker compose up -d && docker system prune -f'
```

### Full Rebuild (Rust changes — needs disk space)
```bash
# Ensure > 5GB free: docker system prune -af, remove old data
ssh EC2 'docker compose build --no-cache aegis-v2 && docker compose up -d'
```

---

## 12. Final Verdict

The system is a paper-trading prototype with institutional-grade risk infrastructure in Rust. The Python signal layer is functional but unproven — only 1 of 6 strategies has produced signals, and overall performance is below validation thresholds.

**Session 3 progress**: Time-stop IS now deployed. Paper fill audit IS complete. Board lot sizing, L1 gate, unhalt grace, spoof calibration are all deployed. The microstructure gap between paper and physical markets is narrowing. System is actively trading (2 entries today: STAN.L, AI).

**Remaining immediate risks**: (1) 8 paper overrides that would be catastrophic in live (Sprint S6), (2) Ouroboros frozen on statistically invalid data (need N=300), (3) WR 35.4% below 40% validation gate, (4) only 1 of 6 strategies proven.

**Sprint roadmap**: S3 DONE. S4 next (TypeB investigation). S5-S6 pre-live config. S7-S12 accepted from syndicate triage (cost injection, friction ranking, asymmetric exits, regime enforcement, symbol quality, cost-honest backtests).

The system needs 300+ spread-adjusted trades across multiple strategies before considering live capital. Estimated timeline to readiness: 4-8 weeks of paper trading at current signal rate.

---

## 13. AI Model-Role Matrix

| Role | Model | How invoked | Current status | Authority |
|------|-------|-------------|---------------|-----------|
| Signal challenge | Claude (via claude_curator.py) | `claude -p` on EC2 | Shadow mode — logs only, non-blocking | Advisory |
| Universe curation | Gemini 2.5 Flash | `$GEMINI_API_KEY` via ticker_selector.py | API key SET, cron every 15min | Advisory |
| Nightly learning | None (deterministic) | nightly_v6.py cron at 04:50 UTC | FROZEN (observe_only=true) | Analysis only |
| Config generation | None (deterministic) | config_writer.py cron at 04:51 UTC | FROZEN (observe_only=true) | Analysis only |
| Backtest analysis | None (offline) | fast_backtest_pipeline.py manual | Ad-hoc | Offline |

**Key constraints**:
- Claude: $0/month via Max subscription CLI auth. Runs `claude -p` with system prompt.
- Gemini: API key in env, rate-limited to 60 RPM. Used for ticker filtering, NOT signal generation.
- Neither AI model has trading authority. All entries go through deterministic RiskArbiter (33 CHECKs).

---

## 14. Artifact Flow Map

```
IBKR Gateway (4003)
  └─ Market data ticks
      └─ Rust engine.rs (process_tick)
          ├─ Phase 1-9: Exit management (Chandelier, 5-rung ladder)
          ├─ Phase 10+: Entry signal evaluation
          │   ├─ Python bridge.py (via stdin/stdout IPC)
          │   │   ├─ VanguardSniper momentum scoring
          │   │   ├─ Orchestrator strategies.toml matching
          │   │   ├─ IBS/VolExpansion/ORB/GapFade inline strategies
          │   │   └─ TypeA-F classification (Stage 4)
          │   └─ RiskArbiter.evaluate() → 33 CHECKs
          └─ WAL events → /app/events/current.ndjson
              └─ Nightly pipeline (04:50 UTC)
                  ├─ nightly_v6.py → analysis + metrics
                  ├─ config_writer.py → dynamic_weights.toml (FROZEN)
                  └─ persistent_memory.py → system_memory.json

Config files:
  config/config.toml ──────────→ Rust engine (config_loader.rs)
  config/contracts.toml ───────→ Python contract_loader.py → bridge.py
  config/dynamic_weights.toml ─→ Rust engine (SIGHUP hot-reload)
  config/strategies.toml ──────→ Python Orchestrator (bridge.py)

Universe rotation:
  ticker_selector.py (every 15min) → /app/config/active_watchlist.json
  → Rust engine watches file mtime → re-subscribes IBKR feeds
```

---

## 15. Nightly Pipeline Step-by-Step

Cron schedule: `50 4 * * *` (04:50 UTC, London dark window)

| Step | Function | What it does | Output |
|------|----------|-------------|--------|
| 1 | `load_todays_trades()` | Scan all WAL files for today's PositionClosed events | `List[TradeRecord]` |
| 1.5 | `classify_trade_cost_aware()` | N1a: Tag each trade with cost taxonomy (victim/survivor/winner) | `Dict` |
| 2 | `analyze_trades()` | Compute daily metrics: WR, PF, avg rung, per-ticker/type/session | `DailyMetrics` |
| 2.5 | Load persistent memory | Cumulative stats across all sessions | `SystemMemory` |
| 3 | `optimize_parameters()` | Auto-tune with guardrails (confidence, kelly, chandelier) | `Dict` recommendations |
| 3.5 | `missed_winner_analysis()` | Compare SignalRejected vs actual price moves | `List[MissedWinner]` |
| 3.6 | `evaluate_curation_pipeline()` | Score IBKR/Gemini/Claude effectiveness | `Dict` |
| 3.7 | `detect_alpha_decay()` | Compare last 7d vs 30d performance (WR, PF, rung, spread) | `List[AlphaDecaySignal]` |
| 3.8 | `check_regime_accuracy()` | Verify regime predictions against trade outcomes | `RegimeAccuracy` |
| 4 | `record_trade()` × N | Update persistent memory (cumulative stats) | `system_memory.json` |
| 4.5 | `generate_ticker_scoreboard()` | Promote/demote/kill tickers based on Wilson score + decay | `Dict` |
| 5 | `generate_daily_report()` | PDF + text report with all metrics | `/app/reports/YYYY-MM-DD.txt` |
| 6 | config_writer.py (04:51 UTC) | Generate dynamic_weights.toml from recommendations (FROZEN) | `dynamic_weights.toml` |

---

## 16. Per-Strategy Runtime Proof

| Strategy | Expected trigger conditions | Why 0 trades? | Fix |
|----------|---------------------------|---------------|-----|
| VanguardSniper | Momentum + RVOL > 1.0 + ADX > 20 | **Works** — 33 trades observed | N/A |
| Orchestrator | strategies.toml pattern match | strategies.toml exists (16KB) but patterns may not match paper tickers | Add logging to Orchestrator match path |
| IBS_MeanReversion | IBS < 0.2 + RSI < 40 | Loosened 2026-03-24. Need time for signal | Monitor |
| VolExpansion | RVOL > 2.0 + ADX > 20 + Hurst > 0.5 | RVOL>2.0 is rare in paper mode | Lower to 1.5 if no signal after 1 week |
| ORB_Breakout | US session only (14:45-15:30 UTC) | Window is 45 minutes/day on US tickers only | Normal — wait for US session |
| GapFade | Gap-down > 1% + RVOL < 2 | Gap-downs are infrequent events | Normal — event-driven |
| TypeB (backtest 52.4% WR) | 3-bar rising RVOL + spread OK | **C02**: Likely threshold mismatch between backtest config and live config | Sprint S4 |
| TypeE (IBS) | IBS < 0.3 + vol_div < 0 | **Observed**: 3 trades. Working. | N/A |

---

## 17. Dead-Code Quarantine Register

| File | LOC | Status | Why quarantined | Action |
|------|-----|--------|----------------|--------|
| `rust_core/src/entry_engine.rs` | 786 | **Compiles, NOT called at runtime** | TypeA-F detection exists in Python bridge.py. Rust version unused. | Add `#[allow(dead_code)]` + `// QUARANTINED: Runtime TypeA-F detection is in bridge.py` header |
| `rust_core/src/rotation_scanner.rs` | ~200 | Instantiated, never called | HotScanner processes apex ticks; RotationScanner is wired but idle | Leave — may be wired in ModeB later |
| `python_brain/ouroboros/autonomous_orchestrator.py` | ~400 | ALIVE (verified Sprint 9) | Runs independently, generates universe recommendations | Keep |
| `python_brain/ouroboros/apex_scout.py` | ~300 | ALIVE (verified Sprint 9) | Apex ticker discovery for HotScanner | Keep |
| `python_brain/ouroboros/kelly_12factor.py` | ~250 | ALIVE (used by bridge.py) | 12-factor Kelly sizing | Keep |
| `python_brain/ouroboros/contract_expander.py` | ~200 | ALIVE (verified Sprint 9) | Expands contracts.toml from IBKR scanner data | Keep |

---

## 18. Paper-vs-Live Override Impact Analysis

| Config key | Paper value | Safe live value | Impact if left as paper | Risk |
|------------|------------|-----------------|------------------------|------|
| `max_positions` | 999 | 3 | Unlimited positions → could open 100+ → bankrupt | **CRITICAL** |
| `max_heat_pct` | 50% | 10% | Half equity at risk simultaneously | **CRITICAL** |
| `daily_trade_limit` | 999 | 5 | Unlimited trades per day → commission death | HIGH |
| `spread_veto_pct` | 4.5% | 1.5% | Accepts terrible fills on illiquid tickers | HIGH |
| `minimum_entry_gbp` | 20 | 1500 | Dust-size positions → commission dominates P&L | HIGH |
| `confidence_floor` | 55 | 65 | Accepts weak signals | MEDIUM |
| `cash_buffer_pct` | 5% | 15% | Insufficient cash reserve for margin calls | MEDIUM |
| `is_simulation` | true | false | No real orders placed (intentional for paper) | N/A |

**Sprint S6 deliverable**: Create `config/config.live.toml` with safe values. Deploy script must use `--config live` flag for live mode.

---

## 19. Validation Gate Methodology

**100-Trade Rolling-Window Gates** (all must pass simultaneously):

| Gate | Threshold | Current (N≈64) | How measured |
|------|-----------|----------------|-------------|
| Win Rate | ≥ 40% | 35.4% | `total_wins / total_exits` over last 100 trades |
| Profit Factor | ≥ 1.3 | ~0.77 | `gross_wins / gross_losses` over last 100 trades |
| Max Consecutive Losses | < 8 | 14 | Longest losing streak in last 100 trades |
| Chandelier Rung ≥ 2 | ≥ 50% of exits | Unknown | % of exits that reached rung 2+ |
| Max Daily Drawdown | < 3% | OK | Largest intraday equity decline |
| Strategy Diversity | ≥ 2 strategies with WR > 35% | 1 (VanguardSniper only) | Per-strategy WR over last 100 trades |

**Rolling window**: Recalculated after each trade. Not time-based — trade-count-based.
**Gate reset**: If any gate fails, counter resets. Must achieve 100 consecutive gate-passing trades.

---

## 20. Commission and Slippage Model

**Current (simulation mode)**:
- Entry commission: £0 (simulated trades bypass broker entirely)
- Exit commission: £0 (same reason)
- Slippage: £0 (fills at exact ask/bid)
- Simulated costs in bridge.py: round_trip_fee_pct (config) + ibkr_commission_gbp (config) — applied to P&L calculation but NOT to fill price

**Accurate model for live**:
- IBKR commission: £1.50 per trade (£3.00 round trip)
- Spread cost: varies, ~0.05-2% depending on ticker
- Slippage: ~0.02-0.10% typical for liquid ETPs
- FX conversion: 0.002% for non-GBP tickers

**Recommendation**: Before live, add `slippage_bps` config param (default 5bps) applied to fill price in sim path. This is the single most impactful realism improvement.

---

## 21. EC2 Infrastructure Status

| Resource | Current | Target | Action needed |
|----------|---------|--------|--------------|
| Instance type | c7i-flex.large (4GB) | m7i-flex.large (8GB) | Sprint S5: Stop → resize → start |
| Disk | 19GB (75% used) | 30GB+ | Expand EBS volume |
| Docker images | ~5GB each | Prune old | `docker system prune -f` before builds |
| Elastic IP | 3.230.44.22 | Keep | Free while attached |
| 2FA | Monday mornings | Same | IBKR mobile app |
| Cron | Supercronic in container | Same | nightly @ 04:50 UTC |
| Redis | Password-protected, internal only | Same | No changes needed |

---

## 22. Stop-State Handoff (Session 2)

**This session delivered**:
1. Sprint S1 COMPLETED: Paper fills use ask/bid (realistic). No mid-point artifact.
2. Sprint S2 COMPLETED: PF=0.0 bug fixed in persistent_memory.py. Tracks cumulative gross wins/losses.
3. Contradiction C05 and C06 CLOSED.
4. Master plan expanded from 13 to 22 sections (all written).

**Next priorities (in order)**:
1. **Commit + deploy** the PF fix to EC2
2. **Sprint S3**: Time-stop in exit_engine.rs (needs disk space — prune first)
3. **Sprint S4**: TypeB investigation (why 0 production signals)
4. **Sprint S5**: EC2 upgrade to 8GB RAM
5. **Sprint S6**: config.live.toml creation
6. Continue paper trading → reach 100 trades → evaluate validation gates

**Files changed this session**:
- `python_brain/ouroboros/persistent_memory.py` — PF fix (2 new fields + compute logic)
- `AEGIS_V2_CANONICAL_MASTER_PLAN_20260324.md` — full expansion (sections 13-22)

**Files to read on next resume**:
- This plan: `AEGIS_V2_CANONICAL_MASTER_PLAN_20260324.md`
- Exit engine: `rust_core/src/exit_engine.rs` (for Sprint S3 time-stop)
- Bridge TypeB classifier: `python_brain/bridge.py:624-630` (for Sprint S4)
- Config: `config/config.toml` (for Sprint S6 live overlay)
