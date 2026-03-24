# AEGIS V2 — Canonical Master Plan
**Date**: 2026-03-24 (updated 15:40 UTC — S1-S8 DONE, S5-S8 deployed)
**Status**: 21 sections. Source of truth for the entire system.
**Supersedes**: All prior plans (PLAN_1, PLAN_2, MERGED_MASTER_PLAN — archived in docs/archive/)
**Truth hierarchy**: Code > Runtime > This plan > Old docs

---

## 1. Executive Summary

AEGIS V2 is a paper-trading system on EC2. Rust engine (32,603 LOC) processes ticks and enforces risk. Python bridge (1,850 LOC) generates signals. IS_LIVE=false is hardcoded in Rust — the system cannot place real orders.

**Performance (2026-03-24)**: ~68 trades, 35.4% WR, -£6.79 cumulative P&L, ~0.77 PF. Every validation gate is failing. The system is not ready for live capital.

**What was fixed today (S4)**: The classification layer was blocking signals that passed all quality checks. Shadow gates on TypeC/E/F removed. TypeB loosened to actually fire. TypeE threshold aligned. Startup clock fixed. First TypeE trade observed (GOOG Long). IBS_MeanReversion now live.

**What was fixed today (S5-S8)**: Cost injection into Ouroboros (nightly now uses cost-adjusted P&L). LSEETF blocked (52 tickers via exchange blacklist). config.live.toml complete (all 8 overrides). Regime+session enforcement wired from strategy_registry.json into bridge.py.

**What remains broken**: 35.4% WR. Validation gates all failing. Ouroboros config mutation frozen at N=68 (nightly analysis RUNS, only parameter writes are frozen until N=300). LSEETF NOW BLOCKED (was dominant loss source). Cost injection NOW ACTIVE (was zero).

**Capital doctrine**: VanguardSniper is the capital core (33+ trades, only strategy with production history). All other strategies are either newly unblocked (S4) or dormant awaiting their market conditions.

---

## 2. System Identity

| Fact | Value | Source |
|------|-------|--------|
| Branch | `feat/tier-system-enhancements-full` | git log |
| GitHub | `nztsignals48-byte/nzt48-signals` (PRIVATE) | remote -v |
| Engine | Rust 2024 edition (1.94.0) | Cargo.toml |
| Rust LOC | 32,603 | tokei |
| Python LOC | 1,850 | tokei |
| IS_LIVE | false (hardcoded) | engine.rs |
| Starting equity | £10,000 | system_memory.json |
| Account type | UK ISA | — |
| Containers | 3: aegis-v2, aegis-ib-gateway, aegis-redis | docker ps |
| IB Gateway | port 4003, client_id=101 | docker-compose.yml |
| Redis password | nzt48redis | docker-compose.yml |

---

## 3. Source-of-Truth Hierarchy

| Rank | Source | Governs |
|------|--------|---------|
| 1 | Rust code (`rust_core/src/`) | Execution, risk, exits, WAL, broker |
| 2 | Python code (`python_brain/`) | Signals, sizing, classification, learning |
| 3 | Config files (`config/`) | Parameters, thresholds, universe |
| 4 | EC2 runtime state | What is actually deployed right now |
| 5 | WAL events (`/app/events/`) | Trade history, P&L, crash recovery |
| 6 | system_memory.json | Nightly-aggregated performance |
| 7 | This plan | Architecture decisions, roadmap |

If this plan contradicts code, the code is correct and this plan is wrong.

---

## 4. Current Performance

| Metric | Value | Gate Threshold | Status |
|--------|-------|----------------|--------|
| Lifetime trades | ~68 | — | Growing (system trading today) |
| Win rate | 35.4% | >= 40% | **FAILING** |
| Profit factor | ~0.77 | >= 1.3 | **FAILING** |
| Cumulative P&L | -£6.79 | — | Negative |
| Max consecutive losses | 14 | < 8 | **FAILING** |
| Strategy diversity | 1 with WR>35% | >= 2 | **FAILING** |

**Dominant loss source**: LSEETF leveraged ETPs — 0% WR, -£30 over 28 trades. **NOW BLOCKED** (Sprint S6: exchange-level blacklist, all 52 LSEETF tickers). WR and PF should improve once non-LSEETF trades accumulate.

**Cost-adjusted reality**: **NOW TRACKED** (Sprint S5). Nightly pipeline computes cost-adjusted P&L per trade (commission £3.40 RT + slippage 0.5% + FX 0.4% for non-GBP + spread). Persistent memory records cost-adjusted values. A £2,000 LSE trade costs ~£17; a £2,000 US trade costs ~£23.

**Ouroboros status**: FROZEN (observe_only=true in config.toml). Will remain frozen until N=300 trades. Cost injection is now active (S5) — Ouroboros will learn from realistic economics when it unfreezes.

### Today's Live Trading (2026-03-25, post-S4 deploy)

**11 trades entered | 0 exits | Unrealised P&L: -£7.46 | Equity: £9,997.72**

| # | Symbol | Exchange | Qty | Entry (GBP) | Value | Strategy | Conf |
|---|--------|----------|-----|-------------|-------|----------|------|
| 1 | 5USL.L | XLON | 11 | £22.20 | £244 | Momentum | 77 |
| 2 | GLEN.L | XLON | 47 | £5.21 | £245 | Momentum | 77 |
| 3 | SAP | XETR | 1 | £127.24 | £127 | **TypeE** | 66 |
| 4 | MBG | XETR | 5 | £44.56 | £223 | **TypeE** | 77 |
| 5 | MC | XPAR | 1 | £398.59 | £399 | **TypeE** | 77 |
| 6 | AAPL | XNYS | 1 | £188.81 | £189 | Momentum | 77 |
| 7 | AI | XPAR | 1 | £145.82 | £146 | **TypeC** | 77 |
| 8 | MSFT | XNYS | 1 | £280.26 | £280 | Momentum | 77 |
| 9 | GOOG | XNYS | 1 | £219.61 | £220 | Momentum | 77 |
| 10 | SIE | XETR | 1 | £181.69 | £182 | Momentum | 77 |
| 11 | SU | XPAR | 1 | £209.33 | £209 | Momentum | 88 |

**Strategy diversity achieved (S4 working):**

| Strategy | Trades | Tickers |
|----------|--------|---------|
| Momentum (VanguardSniper) | 7 | 5USL.L, GLEN.L, AAPL, MSFT, GOOG, SIE, SU |
| TypeE (IBS MeanReversion) | 3 | SAP, MBG, MC |
| TypeC (OverboughtFade) | 1 | AI |

**Later entries (Orchestrator S17 VWAP Dip — first ever Orchestrator trades):**

| # | Symbol | Exchange | Qty | Entry (GBP) | Value | Strategy | Conf |
|---|--------|----------|-----|-------------|-------|----------|------|
| 12 | META | XNYS | 1 | £444.91 | £445 | **Orchestrator_vwap_dip_buy** | 54 |
| 13 | NVDA | XNYS | 1 | £130.72 | £131 | **Orchestrator_vwap_dip_buy** | 54 |
| 14 | BARC.L | XLON | 1 | £383.95 | £384 | **Orchestrator_vwap_dip_buy** | 54 |

**Updated strategy diversity (4 strategy types now producing):**

| Strategy | Trades | Tickers |
|----------|--------|---------|
| Momentum (VanguardSniper) | 7 | 5USL.L, GLEN.L, AAPL, MSFT, GOOG, SIE, SU |
| TypeE (IBS MeanReversion) | 3 | SAP, MBG, MC |
| TypeC (OverboughtFade) | 1 | AI |
| Orchestrator_vwap_dip_buy (S17) | 3 | META, NVDA, BARC.L |

First ever TypeC, TypeE, AND Orchestrator trades in production. 4 strategy types across 5 exchanges. 14 entries, 0 exits. Equity: £9,996.79, unrealised P&L: -£8.40.

**Exit logging**: Exits fire via Chandelier/time-stop/EOD-flatten. Each exit writes ExitSignal + PositionClosed to WAL with full P&L, spread, commission, session data, and Telegram alert. No exits yet — positions within stop ranges.

### Simulation-Truth Gaps (from ChatGPT simulation triage)

| Metric | Status | Resolved by |
|--------|--------|------------|
| Cost-adjusted PF | **NOW TRACKED** — nightly records cost-adjusted P&L to persistent memory | Sprint S5 ✅ |
| Cost-adjusted expectancy/trade | **NOW TRACKED** — estimate_trade_cost() per trade with per-exchange rates | Sprint S5 ✅ |
| Loss concentration by ticker family | **RESOLVED** — LSEETF blocked at exchange level (52 tickers) | Sprint S6 ✅ |
| % of wins invalidated by costs | **NOW TRACKED** — nightly logs cost-adjusted WR alongside sim WR | Sprint S5 ✅ |
| Per-strategy net expectancy | **PARTIALLY** — nightly shows cost-adjusted per-strategy data, not yet ranked | Sprint S9 |
| Instrument-class-aware friction | **RESOLVED** — LSEETF blocked, per-exchange cost rates in cost_model.py | Sprint S6 ✅ |
| Net-edge ranking when multiple signals compete | NOT IMPLEMENTED — uses raw confidence only | Sprint S9 |

S5 and S6 resolved. Remaining gap: S9 (friction-aware ranking when multiple signals compete).

---

## 5. Runtime Ownership Map

| Concern | Owner | Key Files |
|---------|-------|-----------|
| Tick processing | Rust engine.rs | engine.rs (~3200 lines) |
| Entry gating (32 checks) | Rust risk_arbiter.rs | risk_arbiter.rs |
| Exit decisions | Rust exit_engine.rs | Chandelier 5-rung, time-stop |
| Order routing | Rust ibkr_broker.rs | BrokerAdapter trait, L1 tracking |
| Board lot sizing | Rust engine.rs | min_lot_for_exchange() in broker.rs |
| Signal generation | Python bridge.py | VanguardSniper + Orchestrator + inline generators |
| Signal classification | Python bridge.py:606-659 | TypeA-F labels |
| Position sizing | Python kelly_12factor.py | 12-factor Kelly + sim costs |
| Nightly learning | Python nightly_v6.py | RUNNING (analysis active, config mutation frozen) |
| Persistent memory | Python persistent_memory.py | system_memory.json (cumulative stats) |
| Config generation | Python config_writer.py | dynamic_weights.toml (mutation FROZEN, analysis runs) |
| Universe curation | Python ticker_selector.py + Gemini | RUNNING. Produces core_universe_latest.json every 2h. |
| Signal challenge | Python claude_curator.py | RUNNING. Daily briefings, forensic reviews, gate calibration. |
| Cost model | Python cost_model.py | estimate_trade_cost() — per-exchange commission, slippage, FX |
| Exchange blocking | Python bridge.py | LSEETF blocked via [blacklist].exchanges in config.toml |
| Regime/session gating | Python bridge.py | strategy_registry.json regime/session metadata enforced at runtime |
| Hourly P&L Telegram | Python telegram_notify.py --hourly-pnl | RUNNING. Every hour 07:00-21:00 UTC Mon-Fri. |

---

## 6. What Is Actually Working (Code-Verified)

Every item below has been verified against running code or runtime output.

| Component | Status | Evidence |
|-----------|--------|----------|
| Rust engine tick processing | **WORKING** | 570k+ ticks processed, <1ms/tick |
| Risk arbiter (32 checks) | **WORKING** | Deterministic, fail-closed, <1ms |
| Chandelier 5-rung exit | **WORKING** | Rung advancement tracked in WAL, stops ratchet correctly |
| Time-stop (45min, 0.3x ATR) | **WORKING** | Halt-safe — active_trading_ticks pauses during halts |
| Unhalt grace period | **WORKING** | active_trading_ticks reset to 0 on halt lift |
| Board lot sizing | **WORKING** | TSE/HKEX/SGX = 100-share lots, LOT_SKIP on sub-lot |
| Spoof detector | **WORKING** | 25x multiplier + 2% absolute floor, zero false positives |
| Python bridge IPC | **WORKING** | JSON over stdin/stdout, 5s timeout, reader thread |
| VanguardSniper | **WORKING** | 33+ trades, confidence scoring, Kelly sizing |
| Orchestrator S17-S20 | **WORKING** | 4 evaluators from strategies.toml, all enabled, functional |
| WAL logging | **WORKING** | Crash recovery source, archive rotation on restart |
| Nightly pipeline | **WORKING** | nightly_v6.py -> persistent_memory -> config_writer, all run correctly |
| PF tracking | **WORKING** | cumulative_gross_wins/losses in persistent_memory.py (fixed S2) |
| Startup clock | **WORKING** | Uses system UTC for initial trading mode (fixed S4D) |
| Strategy registry | **WORKING** | config/strategy_registry.json, perfect alignment with bridge.py |
| Docker deployment | **WORKING** | Preflight checks, graceful degradation |
| Cron scheduler | **WORKING** | supercronic, no zombies, proper serialization |
| Claude morning briefing | **WORKING** | briefing_2026-03-24_morning.txt generated at 07:45 |
| Claude evening briefing | **WORKING** | Runs at 21:30 daily, sends to Telegram |
| Claude approval gate | **WORKING** | approval_log.ndjson updated nightly |
| Gemini core scanner | **WORKING** | core_universe_latest.json updated at 11:05 today, valid ticker data |
| Gemini morning brief | **WORKING** | morning_brief_latest.json generated at 06:00 |
| Ouroboros nightly analysis | **WORKING** | nightly_v6.py runs at 04:50, analyzes trades, updates persistent_memory |
| Ouroboros config writer | **WORKING (mutation frozen)** | Runs but skips writes because observe_only=true. Will auto-enable at N=300. |
| Hourly P&L Telegram | **DEPLOYED** | --hourly-pnl mode, every hour 07:00-21:00 UTC Mon-Fri |
| Cost injection (S5) | **DEPLOYED** | estimate_trade_cost() in cost_model.py, nightly records cost-adjusted P&L |
| LSEETF exchange block (S6) | **DEPLOYED** | 52 tickers blocked via [blacklist].exchanges in config.toml |
| config.live.toml (S7) | **DEPLOYED** | All 8 paper overrides covered with production-safe values |
| Regime+session enforcement (S8) | **DEPLOYED** | strategy_registry.json gates enforced in bridge.py signal path |

---

## 7. Signal Generators (Code-Verified from bridge.py)

### Independent Signal Generators

| # | Generator | Location | Status | Trades | Entry Logic | Notes |
|---|-----------|----------|--------|--------|-------------|-------|
| 1 | VanguardSniper | bridge.py:1302 | **LIVE-PRODUCING** | 33+ | ADX>=25 + Price>EMA20 + RVOL>=1.5 | Capital core. Only strategy with trade history. |
| 2 | Orchestrator S17 VWAP Dip | autonomous_orchestrator.py + strategies.toml | **LIVE-DORMANT** | 0 | Price >2 sigma below VWAP, mean-reversion regime | Awaiting conditions |
| 3 | Orchestrator S18 Gap Fade | autonomous_orchestrator.py + strategies.toml | **LIVE-DORMANT** | 0 | Gap 1.5-6%, RVOL<2 (liquidity only) | Event-driven, infrequent |
| 4 | Orchestrator S19 RSI/IBS | autonomous_orchestrator.py + strategies.toml | **LIVE-DORMANT** | 0 | RSI(2)<5, IBS<0.20, above 200 SMA | Extreme oversold |
| 5 | Orchestrator S20 Cross-Market Momentum | autonomous_orchestrator.py + strategies.toml | **LIVE-DORMANT** | 0 | SPY 15min >0.3%, ADX>20, Hurst>0.5 | US only |
| 6 | IBS_MeanReversion | bridge.py:1354 | **NOW LIVE** (S4A+S4C) | 0->1 | IBS<0.30, RSI2<25 | Was blocked by TypeE shadow gate. First TypeE trade: GOOG Long. |
| 7 | VolExpansion | bridge.py:1383 | **LIVE-DORMANT** | 0 | RVOL>2.0, ADX>20, 3+ up bars | Awaiting conditions |
| 8 | ORB_Breakout | bridge.py:1413 | **LIVE-DORMANT** | 0 | US session only 14:45-15:30 UTC | Time-windowed |
| 9 | GapFade | bridge.py:1453 | **LIVE-DORMANT** | 0 | Gap down >1%, RVOL<2 | Event-driven |

### Classification Layer (TypeA-F) — bridge.py:606-659

The classification layer labels signals after generation. It does NOT generate signals.

| Label | Status | Condition | WR History |
|-------|--------|-----------|------------|
| TypeA (DipRecovery) | **DISABLED** | RSI<30, RVOL>2.5 | 29.5% — proven loser, correctly blocked |
| TypeB (EarlyRunner) | **NOW REACHABLE** (S4B) | 2-bar rising RVOL + RSI [20,80] | Loosened from 3-bar/[30,70]. Can now fire. |
| TypeC (OverboughtFade) | **NOW LIVE** (S4A) | RSI>80 + price up + vol down | Shadow gate removed. Risk arbiter provides protection. |
| TypeD (SupportBounce) | **DISABLED** | RSI 25-35, near daily low | 24.1% — proven loser, correctly blocked |
| TypeE (IBS) | **NOW LIVE** (S4A+S4C) | IBS<0.30, RVOL>1.0 | Shadow gate removed. Threshold aligned to 0.30 (was 0.10). First trade: GOOG Long. |
| TypeF (OBVDivergence) | **NOW LIVE** (S4A) | vol_div<-0.5, RVOL>0.7 | Shadow gate removed. Risk arbiter provides protection. |

TypeA and TypeD remain correctly disabled (proven losers with <30% WR).

**Signal pipeline gating (S6+S8)**: Before any signal reaches the engine, bridge.py now enforces:
1. Ticker blacklist (4 individual tickers + all LSEETF via exchange block)
2. Regime gate: strategy must be allowed in current market regime (hurst + rvol + adx classification)
3. Session gate: strategy must be allowed in current UTC session window
4. Log lines: `EXCHANGE_VETO`, `REGIME_SESSION_VETO` for debugging

---

## 8. Completed Sprints

### Sprint S1: Paper Fill Audit — DONE 2026-03-24
- Verified fills use ASK for entry, BID for exit (realistic).
- Zero slippage/commission in simulation path (known, tracked for S7).

### Sprint S2: Profit Factor Tracking Fix — DONE 2026-03-24
- Added cumulative_gross_wins and cumulative_gross_losses to persistent_memory.py.
- PF now computes correctly across sessions.

### Sprint S3: Microstructure Sprint — DONE 2026-03-24
- Board lot sizing: TSE/HKEX/SGX = 100-share lots. engine.rs rounds before order. Sub-lot = LOT_SKIP.
- L1 gate: subscribe_l1() succeeds at API level, async errors (10190/10189) handled via l1_subscribed_set removal on poll.
- Unhalt grace period: active_trading_ticks reset to 0 on halt lift.
- Spoof detector calibration: 25x multiplier + 2% absolute floor.
- EC2 live config: terraform/variables.live.tfvars targets c7i.large (non-burstable).

### Sprint S4: Strategy Unblock + Clock Fix — DONE 2026-03-24
- **S4A**: Removed TypeC/E/F from shadow gate (bridge.py). All types now live. Risk arbiter (32 checks) provides real protection.
- **S4B**: Loosened TypeB classifier: 3-bar -> 2-bar rising RVOL, RSI [30,70] -> [20,80]. TypeB is now reachable.
- **S4C**: Fixed TypeE threshold mismatch: config.toml 0.10 -> 0.30, aligned with bridge.py:1359 IBS_MeanReversion generator.
- **S4D**: Fixed startup clock: engine now uses system UTC for initial trading mode instead of starting in Dark during market hours.
- First TypeE trade observed post-fix: GOOG Long.

---

## 9. Remaining Sprints — Money-First Priority Order

Ordered by economic impact, not engineering convenience. Fix the P&L lie first, kill the garbage tickers second, then build infrastructure.

### Sprint S5: Cost Injection into Ouroboros — DONE (2026-03-24 15:00 UTC)
- estimate_trade_cost() with per-exchange rates, commission, slippage, FX costs.
- Nightly persistent_memory records use cost-adjusted P&L.
- Log output shows sim vs cost-adjusted metrics.

### Sprint S6: LSEETF Disposal + Exchange Blocking — DONE (2026-03-24 15:15 UTC)
- Added `exchanges = ["LSEETF"]` to [blacklist] in config.toml.
- All 52 LSEETF leveraged ETPs blocked.
- Reversible: remove "LSEETF" from exchanges list.

### Sprint S7: config.live.toml — DONE (2026-03-24 15:25 UTC)
- All 8 paper overrides covered with production-safe values.
- Added spread_veto_pct and slippage_assumption_pct.

### Sprint S8-a: Regime + Session Enforcement — DONE (2026-03-24 15:30 UTC)
- strategy_registry.json regime_allowed/blocked and session_allowed/blocked enforced in bridge.py.
- _classify_market_regime() and _classify_current_session() map to registry names.
- REGIME_SESSION_VETO log for debugging. Fail-open for unknown strategies.

### Sprint S8-b: EC2 Instance Upgrade — BLOCKED (requires Terraform apply)
- Upgrade from c7i-flex.large (4GB) to c7i.large (8GB, non-burstable).
- Use terraform/variables.live.tfvars.

### Sprint S9: Friction-Aware Signal Ranking (~1 hour)
- When multiple strategies fire on the same tick, rank by net expected P&L after costs.
- Prevents commission-destruction from low-edge signals.

### Sprint S10: Per-Strategy Asymmetric Exits (~1 hour)
- Different Chandelier ATR multipliers per strategy family.
- Mean-reversion strategies (IBS, VWAP Dip) need tighter exits than momentum (VanguardSniper).

### Sprint S11: Cost-Honest Backtests (~1 hour)
- Add IBKR commissions + slippage model to fast_backtest_pipeline.py.
- A backtest that ignores costs is a lie.

---

## 10. Pre-Live Blockers

| # | Blocker | Status | Sprint |
|---|---------|--------|--------|
| 1 | IS_LIVE=false hardcoded in Rust | OPEN | Rust rebuild + careful review |
| 2 | 8 paper overrides in config.toml | **RESOLVED** — config.live.toml covers all 8 | S7 ✅ |
| 3 | WR 35.4% (gate requires >= 40%) | OPEN | Ongoing — LSEETF blocked, should improve |
| 4 | PF ~0.77 (gate requires >= 1.3) | OPEN | Ongoing — cost-adjusted tracking now active |
| 5 | 14 consecutive losses (gate requires < 8) | OPEN | Ongoing — LSEETF (primary cause) now blocked |
| 6 | Only 1 strategy producing trades (gate requires >= 2 with WR>35%) | OPEN | S4 unblocked strategies, S8 regime/session gates active |
| 7 | Ouroboros frozen at N=68 (need N=300) | OPEN | Cost injection now active (S5), will unfreeze at N=300 |
| 8 | EC2 only 4GB RAM | OPEN | S8-b (Terraform apply) |
| 9 | Zero cost injection in sim | **RESOLVED** — nightly uses cost-adjusted P&L | S5 ✅ |

**Closed blockers (resolved):**
- ~~Time-stop missing~~ — deployed (45min, 0.3x ATR, halt-safe)
- ~~Paper fill realism~~ — S1 confirmed ask/bid fills
- ~~PF tracking broken~~ — S2 fixed cumulative tracking
- ~~Shadow gate blocking strategies~~ — S4 removed shadow gates
- ~~Startup clock starting in Dark~~ — S4D fixed to use system UTC
- ~~8 paper overrides unreverted~~ — S7 config.live.toml covers all 8 with production-safe values
- ~~Zero cost injection~~ — S5 estimate_trade_cost() with per-exchange rates, nightly records cost-adjusted P&L
- ~~LSEETF not blocked~~ — S6 exchange-level blacklist, all 52 LSEETF tickers blocked
- ~~Regime/session metadata not enforced~~ — S8 strategy_registry.json gates wired into bridge.py

---

## 11. Paper-vs-Live Override Analysis

These 8 values in config.toml are set for paper experimentation and MUST be reverted before live trading. **All 8 are now covered in config.live.toml (Sprint S7).**

| Config key | Paper value | Live value (config.live.toml) | Risk if left | S7 |
|------------|-------------|-------------------------------|--------------|-----|
| max_positions | 999 | 3 | **CRITICAL** — unlimited position count | ✅ |
| max_heat_pct | 50% | 10% | **CRITICAL** — half of equity at risk | ✅ |
| daily_trade_limit | 999 | 3 | **HIGH** — commission death spiral | ✅ |
| spread_veto_pct | 0.3% | 0.3% | OK — already tight | ✅ |
| minimum_entry_gbp | 20 | 1500 | **HIGH** — dust positions | ✅ |
| confidence_floor | 50 | 65 | **MEDIUM** — accepting weak signals | ✅ |
| cash_buffer_pct | 5% | 25% | **MEDIUM** — no margin reserve | ✅ |
| is_simulation | true | false | N/A — controls fill path | ✅ |

---

## 12. Dead/Orphaned Code + Technical Debt Register

Code that exists but isn't exercised, plus known technical debt from syndicate stress test (triage #2).

### Dead/Orphaned Code

| Item | Location | LOC | Status | Notes |
|------|----------|-----|--------|-------|
| TypeA-F detectors | entry_engine.rs:88-500 | ~500 | Quarantined | Not called at runtime. Superseded by Python classification. |
| strategy_config.rs | strategy_config.rs | ~200 | Loaded, never queried | Struct is parsed from config but no runtime code reads the fields. |
| Hurst exponent | regime_detector.rs | ~50 | Computed, only has_jump used | Full Hurst value returned but discarded by callers. Future regime routing. |
| Risk arbiter CHECK 26 | risk_arbiter.rs:~451 | ~20 | Never triggers | Scanner score sentinel = -1.0, condition never met. Duplicates CHECK 10. |

### Technical Debt (from syndicate triage #2 — `tasks/syndicate_triage_2_20260324.md`)

47 points evaluated → 6 genuinely new, 15 repeats, 26 premature.

| ID | Issue | Severity | Action | Status |
|----|-------|----------|--------|--------|
| V1.6 | Crossed market (bid≥ask) causes divide-by-zero in spread calc | MEDIUM | Add guard `if bid >= ask { skip }` in engine.rs | TODO |
| V1.8 | Slippage injection must be bps-based, not flat ticks | HIGH | Note for Sprint S5 — use bps not ticks | NOTED |
| V6.2 | WAL corruption on kernel panic — malformed last JSON line crashes parser | MEDIUM | Add try-parse with skip-malformed in wal_replay.rs | TODO |
| V6.3 | Orphaned orders after TCP drop between send and ACK | HIGH (live) | Already handled — reconciliation on reconnect (engine.rs:2954) | DOCUMENTED |
| V6.4 | Config hierarchy ambiguity (config.toml vs dynamic_weights.toml) | MEDIUM | config.toml is primary, dynamic_weights is overlay. Verify on next session. | DOCUMENTED |
| V4.6 | Stock splits look like 50% crash in paper mode | LOW | Paper only — IBKR adjusts prices in live mode | DOCUMENTED |

---

## 13. Nightly Pipeline (04:50 UTC Mon-Fri)

Runs via supercronic cron scheduler. No zombies, proper serialization.

| Step | Script | Output | Notes |
|------|--------|--------|-------|
| 1 | nightly_v6.py | nightly_output.json | Analyzes trades: WR, PF, per-ticker, per-session, regime metrics |
| 2 | config_writer.py | dynamic_weights.toml | Generates recommendations. **Mutation frozen** (observe_only=true). Analysis runs, writes skipped. |
| 3 | persistent_memory.py | system_memory.json | Update cumulative stats with **cost-adjusted P&L** (S5) |
| 4 | Claude review | /app/data/claude/reviews/ | Forensic analysis of day's trades. Shadow mode, advisory. |
| 5 | Telegram report | — | Performance summary to Telegram |

**Ouroboros nightly runs every night at 04:50 UTC.** It analyzes trades, computes metrics, and updates persistent_memory.json **with cost-adjusted P&L** (Sprint S5). The ONLY frozen part is config_writer mutation (dynamic_weights.toml writes). observe_only=true prevents parameter changes until N=300 trades. Current N=~68. Cost injection is active — when Ouroboros unfreezes at N=300, it will learn from realistic economics.

---

## 14. AI Model Roles + Intelligence Layer Status

**Neither AI model has trading authority.** All trade entries flow through the deterministic RiskArbiter (32 checks, fail-closed). AI models observe, advise, and curate. The system trades on math, not AI opinions.

### Claude (via `claude -p` CLI on EC2, $0/month Max subscription)

| Role | Schedule | Output | Status |
|------|----------|--------|--------|
| Morning briefing | 07:45 UTC daily | /app/data/claude/briefings/ | **RUNNING** — briefing_2026-03-24_morning.txt verified |
| Evening briefing | 21:30 UTC daily | /app/data/claude/briefings/ | **RUNNING** |
| Forensic review | 04:53 UTC (nightly pipeline) | /app/data/claude/reviews/ | **RUNNING** — empty until trades close in WAL |
| Gate calibration | Friday 22:00 UTC | rejected_reviews/ | **RUNNING** — weekly schedule |
| Universe curation | Every 2h (shadow) | /app/data/claude/curation/ | **RUNNING (shadow)** — logs only, not yet promoted |
| Filing/news scanner | 06:00 UTC daily | macro/ | **RUNNING** |

Claude is fully operational in cold-path advisory mode. It produces daily briefings, nightly forensic reviews, and weekly gate calibration analysis. None of these affect execution — they inform the operator.

### Gemini 2.5 Flash (API key: AIzaSyBMyC..., 39 chars)

| Role | Schedule | Output | Status |
|------|----------|--------|--------|
| Core universe curation | Every 2h (cron) | /app/data/gemini/core_universe_latest.json | **RUNNING** — last updated 11:05 today, valid ticker lists |
| Morning brief | 06:00 UTC daily | /app/data/gemini/morning_brief_latest.json | **RUNNING** — generated today |

Gemini is fully operational. API key is deployed and producing valid output. It curates the ticker universe for the session-aware watchlist rotation.

### Ouroboros (deterministic nightly learning loop)

| Component | Schedule | Output | Status |
|-----------|----------|--------|--------|
| nightly_v6.py (analysis) | 04:50 UTC Mon-Fri | nightly_output.json | **RUNNING** — analyzes trades, computes WR/PF/per-ticker metrics |
| persistent_memory.py | After nightly | system_memory.json | **RUNNING** — updates cumulative stats |
| config_writer.py (mutation) | 04:51 UTC Mon-Fri | dynamic_weights.toml | **RUNNING but MUTATION FROZEN** — observe_only=true |

Ouroboros runs every night. It analyzes trades, updates persistent memory, and generates config recommendations. The only frozen part is the final config write — `observe_only=true` prevents dynamic_weights.toml from being updated until N=300 trades with cost injection (Sprint S5).

### Telegram Notifications

| Type | Schedule | Status |
|------|----------|--------|
| Trade entry alerts | On every SIM_TRADE | **RUNNING** |
| Trade exit alerts | On every PositionClosed | **RUNNING** |
| System heartbeat | Every 4h | **RUNNING** |
| Hourly P&L update | Every hour 07:00-21:00 UTC | **DEPLOYED** (new, added 2026-03-24) |
| Daily sim report | 21:15 UTC | **RUNNING** |
| Session summaries | Session boundaries | **RUNNING** |

---

## 15. Artifact Flow Map

```
IBKR Gateway (port 4003)
  |
  v
Market Ticks --> Rust engine.rs (tick processing, <1ms)
  |
  +-- Exit evaluation
  |     Chandelier 5-rung (rung advancement in WAL)
  |     Time-stop (45min, 0.3x ATR, halt-safe)
  |     Exhaustion detection
  |
  +-- Entry gate pre-checks (engine.rs)
  |     Board lot sizing (TSE/HKEX/SGX = 100 shares)
  |     L1 subscription gate
  |     Spoof detection (25x + 2% floor)
  |
  +-- Python bridge (JSON stdin/stdout, 5s timeout)
  |     Exchange block (S6): LSEETF blocked (52 tickers)
  |     VanguardSniper (ADX + EMA + RVOL)
  |     Orchestrator S17-S20 (4 evaluators from strategies.toml)
  |     IBS_MeanReversion, VolExpansion, ORB_Breakout, GapFade
  |     TypeA-F classification (TypeA/D disabled, rest live)
  |     Regime+session gate (S8): strategy_registry.json enforcement
  |     Kelly 12-factor sizing
  |
  +-- Risk arbiter (32 deterministic checks, <1ms)
  |     Fail-closed. If any check fails, trade is vetoed.
  |
  +-- Paper broker (simulation_mode: fills at ask/bid, no slippage)
  |
  +-- WAL events --> /app/events/current.ndjson
        |
        +-- Nightly pipeline (04:50 UTC)
              nightly_v6.py -> persistent_memory.py -> config_writer.py (mutation frozen)
```

---

## 16. Validation Gate Methodology

| Gate | Threshold | Current Value | Status | Notes |
|------|-----------|---------------|--------|-------|
| Win Rate | >= 40% | 35.4% | **FAILING** | LSEETF leveraged ETPs drag WR hard |
| Profit Factor | >= 1.3 | ~0.77 | **FAILING** | More money lost than made |
| Max Consecutive Losses | < 8 | 14 | **FAILING** | Streak driven by LSEETF run |
| Strategy Diversity | >= 2 strategies with WR>35% | 1 | **FAILING** | Only VanguardSniper has trade history |
| Trade Count | >= 200 | ~68 | **FAILING** | Need 3x more trades for statistical validity |

**No gate is passing.** The system must not go live until all five gates pass simultaneously over a rolling 200-trade window.

---

## 17. Commission and Slippage Model

| Dimension | Sim fills | Real cost (S5 estimate) | Status |
|-----------|-----------|-------------------------|--------|
| Commission | £0.00 in sim | £1.70/trade (£3.40 round trip) | **TRACKED** — estimate_trade_cost() |
| Slippage | 0 bps | 0.5% of position (~£10 on £2k) | **TRACKED** — from config.toml risk.slippage_assumption_pct |
| Spread | Exact ask/bid | Per-exchange: LSE 0.35%, US 0.15%, TSE 0.25% | **TRACKED** — per_exchange_rt in cost_model.py |
| FX conversion | £0.00 | 0.2% × 2 for non-GBP exchanges (~£8 on £2k) | **TRACKED** — US/TSE/HKEX/SGX/XETRA/EURONEXT |
| Fill model | simulation_mode: bypasses paper_broker.rs | paper_broker.rs with real IB fills | — |

**Cost injection is now active (Sprint S5).** The nightly pipeline computes cost-adjusted P&L per trade and records it to persistent memory. Example costs: LSE £2k trade = ~£17 round-trip, US £2k trade = ~£23 round-trip. Ouroboros will learn from realistic economics when it unfreezes at N=300.

---

## 18. EC2 Infrastructure

| Resource | Current | Live Target | Notes |
|----------|---------|-------------|-------|
| Instance | c7i-flex.large (4GB RAM, 2 vCPU) | c7i.large (8GB, non-burstable) | terraform/variables.live.tfvars |
| Disk | 19GB (76% used) | 30GB+ | Docker images ~5GB each. Prune before every build. |
| Elastic IP | 3.230.44.22 | Keep | — |
| Containers | aegis-v2 + aegis-ib-gateway + aegis-redis | Same | All healthy |
| IB Gateway | port 4003, client_id=101 | Same | 2FA re-auth required every Monday |
| SSH | ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 | Same | — |

**Deployment sequence:**
```bash
git commit && git push
rsync -avz --exclude target --exclude .git . ubuntu@3.230.44.22:~/nzt48-aegis-v2/
ssh EC2 'cd ~/nzt48-aegis-v2 && docker image prune -f && docker compose build aegis-v2 && docker compose up -d && docker image prune -f'
```

Docker bakes Python into the image. Any Python change requires a full docker compose build. PyO3 linker fails on macOS — use `cargo check` locally, Docker build on EC2 for full compile.

**Disk is tight at 19GB.** MUST run `docker image prune -f` before AND after builds.

---

## 19. Recovery Procedures

```bash
# Kill switch — stops all new trades immediately
ssh EC2 'docker exec aegis-v2 touch /app/KILL'

# Flatten + halt — closes positions and pauses
ssh EC2 'docker exec aegis-v2 touch /app/PAUSE'

# Full stop — brings down all containers
ssh EC2 'cd ~/nzt48-aegis-v2 && docker compose down'

# Restart — clear kill/pause files, bring up
ssh EC2 'docker exec aegis-v2 rm -f /app/KILL /app/PAUSE'
ssh EC2 'cd ~/nzt48-aegis-v2 && docker compose up -d'

# View logs
ssh EC2 'docker logs aegis-v2 --tail 50'

# Check container health
ssh EC2 'docker ps'
```

---

## 20. Operational Gotchas

| Gotcha | Detail |
|--------|--------|
| Monday 2FA | IB Gateway requires 2FA re-authentication every Monday morning |
| KRX contracts | Do not work. Skip Korean exchange tickers. |
| Redis password | nzt48redis (in docker-compose.yml) |
| WAL archives | Engine rotates current.ndjson on restart. Nightly scans archive/*.ndjson. |
| PyO3 macOS | Linker fails locally. Use `cargo check` on Mac, `docker compose build` on EC2. |
| L1 subscribe | subscribe_l1() succeeds at API level but errors arrive async (10190/10189). l1_subscribed_set removes on poll. |
| Board lot sub-lot | If requested shares < exchange lot size, engine emits LOT_SKIP and does not trade. |
| Rust edition | 2024 (1.94.0). NEVER downgrade to 2021. |
| Per-ticker cooldown | 5 minutes between entries on the same ticker |
| System velocity | Max 10 trades per 5-minute window |
| Disk usage | 19GB total, 76% used. Docker images ~5GB. Prune aggressively. |
| simulation_mode | Bypasses paper_broker.rs. Fills at exact ask/bid. No slippage. Not realistic for cost modeling. |

---

## 21. Brutal Final Verdict

**The system has institutional-grade engineering and losing economics — but the worst economics are now being fixed.**

The Rust engine is fast, deterministic, and correct. The risk arbiter is fail-closed with 32 checks. The exit engine ratchets properly. The deployment pipeline, cron, and nightly analysis all work. The microstructure handling (board lots, L1, unhalt grace, spoof detection) is production-ready. The intelligence layer is fully operational: Claude produces daily briefings and forensic reviews, Gemini curates the ticker universe every 2 hours, Ouroboros analyzes trades nightly with **cost-adjusted P&L** (mutation frozen until N=300), and hourly P&L updates go to Telegram.

**The core problem is 35.4% WR.** This is below breakeven for any reasonable commission structure. The dominant loss source WAS LSEETF leveraged ETPs (0% WR, -£30 over 28 trades) — **now blocked** (Sprint S6). With LSEETF removed, the remaining trades have a higher WR baseline. We need to accumulate non-LSEETF trades to see if the system is profitable without the garbage.

**The secondary problem — zero cost injection — is now resolved.** Sprint S5 added `estimate_trade_cost()` with per-exchange rates (commission, slippage, FX, spread). The nightly pipeline records cost-adjusted P&L to persistent memory. Ouroboros will learn from realistic economics when it unfreezes at N=300.

**What S5-S8 accomplished:**
- **S5**: Cost injection. Nightly records cost-adjusted P&L. The lie is over.
- **S6**: LSEETF blocked. 52 leveraged ETPs removed via exchange-level blacklist. Reversible.
- **S7**: config.live.toml complete. All 8 paper overrides covered with production-safe values.
- **S8**: Regime+session enforcement. Strategies now respect their registry regime/session constraints. Momentum blocked in choppy markets. Mean-reversion blocked in trending markets.

**What S4 accomplished**: Unblocked TypeC/E/F strategies, loosened TypeB, fixed the startup clock. The system now has 9 signal generators with regime/session-aware gating.

**Path to live:**
1. ~~S5: Inject costs into Ouroboros~~ ✅ DONE
2. ~~S6: LSEETF disposal~~ ✅ DONE
3. ~~S7: config.live.toml~~ ✅ DONE
4. ~~S8: Regime+session enforcement~~ ✅ DONE
5. S8-b: EC2 upgrade (4GB → 8GB, Terraform apply)
6. Accumulate 200+ cost-adjusted trades with LSEETF excluded
7. All 5 validation gates passing simultaneously over rolling 200-trade window
8. Remove IS_LIVE=false (Rust rebuild + human sign-off)

**Estimated timeline to live**: Not before 200 cost-adjusted trades pass all gates. With LSEETF removed and costs tracked, the signal-to-noise should improve. But the fundamental question remains: can VanguardSniper + TypeB/C/E/F produce a cost-adjusted WR above 40%? Only more trades will answer this. Do not rush. Capital is real.

---

*End of canonical plan. Last updated 2026-03-24 16:00 UTC. S1-S8 completed and deployed.*
