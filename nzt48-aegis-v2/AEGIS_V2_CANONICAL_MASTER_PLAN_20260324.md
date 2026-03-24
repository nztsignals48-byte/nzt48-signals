# AEGIS V2 — Canonical Master Plan + System Audit
**Date**: 2026-03-24 (updated session 3 — Microstructure Sprint + Code Audit)
**Status**: 22 sections + audit findings merged. S1+S2+S3 DONE. S4 next.
**Supersedes**: All prior plans + standalone AEGIS_V2_SYSTEM_AUDIT_20260324.md
**Source of truth hierarchy**: Code > Runtime > This plan > Old docs

---

## 1. Executive Summary

AEGIS V2 is a paper-trading system on EC2 (c7i-flex.large, 4GB RAM). Rust engine (32,603 LOC) + Python bridge (1,850 LOC). IS_LIVE=false — cannot place real orders.

**Performance**: ~66 trades, 35.4% WR, -£6.79 P&L, ~0.77 PF. Below all validation gates.

**Audit finding (session 3)**: The system has **2 independent signal generators** (VanguardSniper + Orchestrator S17-S20) and a **classification layer (TypeA-F) that blocks more than it helps**. The "6 strategies" narrative is inaccurate — 4 inline generators (IBS_MR, VolExp, ORB, GapFade) generate signals that get immediately blocked by the TypeC/E/F shadow gate. TypeB classification is unreachable (3-bar rising RVOL never fires on 5s bars).

**Capital doctrine**: VanguardSniper is the capital core. TypeB needs loosened classification to actually fire. TypeC/E/F shadow gate should be removed — risk arbiter (32 checks) provides real protection.

**Session 3 delivered**: Board lot sizing, L1 gate, unhalt grace, spoof calibration, EC2 live config. System trading: STAN.L and AI entries observed.

---

## 2. Current Verified System State

| Fact | Value | Evidence |
|------|-------|----------|
| Branch | `feat/tier-system-enhancements-full` | `git log` |
| Containers | 3 healthy (aegis-v2, ib-gateway, redis) | `docker ps` |
| Subscriptions | 100 reqMktData | Engine logs |
| Python Bridge | Running | bridge_stderr.log |
| Ouroboros | FROZEN (observe_only=true) | config.toml |
| Signal generators | 2 real (Vanguard + Orchestrator) | bridge.py audit |
| Signal producers | 1 (VanguardSniper: 33 trades) | WAL data |
| Classification layer | TypeA-F (3 disabled, 2 shadow-blocked, 1 unreachable) | bridge.py:1582-1592 |
| Equity | £10,000 | system_memory.json |
| Validation gate | FAILING (35.4% WR, ~0.77 PF, 14 consec losses) | system_memory.json |

---

## 3. Source-of-Truth Hierarchy

| Rank | Source | What it governs |
|------|--------|----------------|
| 1 | Rust code (`rust_core/src/`) | Execution, risk, exits, WAL, broker |
| 2 | Python code (`python_brain/`) | Signals, sizing, classification, learning |
| 3 | Config files (`config/`) | Parameters, thresholds, universe |
| 4 | EC2 runtime state | What's actually deployed |
| 5 | WAL events (`/app/events/`) | Trade history, P&L |
| 6 | system_memory.json | Nightly-aggregated performance |
| 7 | This plan | Architecture decisions, roadmap |

---

## 4. Runtime Ownership Map

| Concern | Owner | Files |
|---------|-------|-------|
| Tick processing | Rust engine.rs | engine.rs:891 |
| Entry gating (32 checks) | Rust risk_arbiter.rs | risk_arbiter.rs |
| Exit decisions | Rust exit_engine.rs | exit_engine.rs (Chandelier 5-rung) |
| Signal generation | Python bridge.py | 2 generators + TypeA-F classification |
| Position sizing | Python kelly_12factor.py | 12-factor + sim costs |
| Nightly learning | Python nightly_v6.py | FROZEN (observe_only) |
| Universe curation | Python ticker_selector.py + Gemini | Rotation priority |
| Signal challenge | Claude curator | Shadow only, non-blocking |

---

## 5. Strategy Inventory (Audit-Corrected)

**AUDIT FINDING**: The real signal topology is 2 generators + 1 classification layer, not "11 strategies."

### Signal Generators (independent, produce signals)

| Generator | Status | Trades | Logic | Verdict |
|-----------|--------|--------|-------|---------|
| VanguardSniper | **LIVE-PRODUCING** | 33 | ADX≥25 + Price>EMA20 + RVOL≥1.5 | **Capital core** |
| Orchestrator S17 (VWAP Dip) | **LIVE-DORMANT** | 0 | Price >2σ below VWAP, ADX<25, Hurst<0.5 | Mean-reversion, regime-filtered |
| Orchestrator S18 (Gap Fade) | **LIVE-DORMANT** | 0 | Gap 1.5-6%, RVOL<2 (liquidity only) | Event-driven, infrequent |
| Orchestrator S19 (RSI/IBS) | **LIVE-DORMANT** | 0 | RSI(2)<5, IBS<0.20, above 200 SMA | Extreme oversold |
| Orchestrator S20 (Cross-Mkt) | **LIVE-DORMANT** | 0 | SPY 15min move >0.3%, ADX>20, Hurst>0.5 | US macro momentum |
| IBS_MeanReversion | **BLOCKED** (TypeE shadow) | 0 | IBS<0.30, RSI2<25. **Generates but shadow gate kills** | **FIX: remove shadow gate** |
| VolExpansion | **LIVE-DORMANT** | 0 | RVOL>2.0, ADX>20, 3+ up bars | Escapes type gate (Unclassified) |
| ORB_Breakout | **LIVE-DORMANT** | 0 | US session 14:45-15:30, price>ORB high, RVOL>1.5 | US-only window |
| GapFade | **LIVE-DORMANT** | 0 | Gap down >1%, RVOL<2 (liquidity) | Event-driven |

### Classification Layer (labels signals, does not generate)

| Label | Status | Condition | Issue |
|-------|--------|-----------|-------|
| TypeA (DipRecovery) | **DISABLED** | RSI<30, RVOL>2.5 | 29.5% WR — correct to block |
| TypeB (EarlyRunner) | **UNREACHABLE** | 3-bar rising RVOL + RSI [30,70] | **FIX: loosen to 2-bar, RSI [20,80]** |
| TypeC (OverboughtFade) | **SHADOW-BLOCKED** | RSI>80 + price up + vol down | **FIX: remove shadow gate** |
| TypeD (SupportBounce) | **DISABLED** | RSI 25-35, near daily low | 24.1% WR — correct to block |
| TypeE (IBS) | **SHADOW-BLOCKED** | IBS<0.10, RVOL>1.0 | **FIX: align threshold to 0.30 (matches inline generator)** |
| TypeF (OBVDivergence) | **SHADOW-BLOCKED** | vol_div<-0.5, RVOL>0.7 | **FIX: remove shadow gate** |

### The Classification Bottleneck (Root Cause)

```python
# bridge.py:1582-1592 — THIS IS THE BOTTLENECK
_DISABLED_TYPES = {"TypeA", "TypeD"}   # Correct — proven losers
_SHADOW_TYPES = {"TypeC", "TypeE", "TypeF"}  # WRONG — kills signals that passed all quality checks
```

**The fix**: Remove TypeC/E/F from shadow gate. Risk arbiter (32 checks) provides real protection.

**TypeE threshold bug**: Classifier uses `ibs < 0.10` (config.toml:445) but inline IBS_MR generator uses `ibs < 0.30` (bridge.py:1359). Mismatch means IBS signals fire but get caught as TypeE and blocked.

---

## 6. Contradiction Register (Audit-Merged)

| ID | Issue | Severity | Status |
|----|-------|----------|--------|
| C01 | Gemini table row still says BROKEN | LOW | Fix in manual |
| C02 | TypeB "best but never fires" — it's an unreachable label, not a latent strategy | HIGH | **FIX: loosen TypeB classification** |
| C03 | entry_engine.rs looks active but not used at runtime | MEDIUM | Quarantined, documented |
| ~~C05~~ | Paper fills — CLOSED (S1 audit: ask/bid realistic) | CLOSED | S1 DONE |
| ~~C06~~ | PF=0.0 — CLOSED (S2 fix) | CLOSED | S2 DONE |
| X01 | "6 strategies" narrative is wrong — 2 generators + classification | HIGH | **FIX: plan corrected above** |
| X03 | IBS_MR/VolExp/ORB/GapFade "LIVE" but shadow-blocked | MEDIUM | **FIX: remove shadow gate** |
| X04 | Risk arbiter CHECK 26 dead (scanner score sentinel=-1, never triggers) | LOW | **FIX: delete CHECK 26** |
| X07 | 8 paper overrides not reverted | CRITICAL (pre-live) | Sprint S6 |
| X08 | Zero slippage/commission in sim | HIGH | Sprint S7 |

---

## 7. Pre-Live Blockers

| # | Blocker | Status |
|---|---------|--------|
| 1 | IS_LIVE=false hardcoded | OPEN — Rust rebuild needed |
| 2 | 8 paper overrides in config.toml | OPEN — Sprint S6 |
| ~~3~~ | ~~No time-stop~~ | **CLOSED** — deployed (45min, 0.3x ATR) |
| 4 | WR 35.4% (need 40%) | OPEN — need more/better trades |
| 5 | PF ~0.77 (need 1.3) | OPEN — need strategy improvement |
| 6 | 14 consecutive losses (need <8) | OPEN — need better filtering |
| ~~7~~ | ~~Paper fill realism~~ | **CLOSED** — S1 confirmed ask/bid |
| 8 | Only 1 of 6 strategies producing | OPEN — **FIX: unblock shadow strategies** |
| 9 | Ouroboros frozen on N=48 | OPEN — need N=300 |
| 10 | EC2 4GB RAM | OPEN — Sprint S5 |

---

## 8. What Is Actually Working (Audit Section B)

| Component | Status | Evidence |
|-----------|--------|----------|
| Rust engine tick processing | **WORKING** | 570k+ ticks, <1ms/tick |
| Risk arbiter (32 checks) | **WORKING** | Deterministic, fail-closed |
| Chandelier 5-rung exit | **WORKING** | Rung advancement in WAL, stops ratchet |
| Time-stop (45min, 0.3x ATR) | **WORKING** | Halt-safe (active_trading_ticks) |
| Board lot sizing | **WORKING** | TSE/HKEX/SGX = 100-share lots |
| Spoof detector | **WORKING** | 25x + 2% floor, zero false positives |
| Python bridge IPC | **WORKING** | JSON stdin/stdout, 5s timeout |
| VanguardSniper | **WORKING** | 33 trades, Kelly sizing |
| Orchestrator S17-S20 | **WORKING** | 4 evaluators from strategies.toml |
| WAL logging | **WORKING** | Crash recovery source |
| Nightly pipeline | **WORKING** | All stages run correctly |
| Strategy registry | **WORKING** | Perfect alignment with bridge.py |
| Docker deployment | **WORKING** | Preflight, graceful degradation |
| Cron scheduler | **WORKING** | No zombies |

---

## 9. Complexity Register (Audit Section E)

| Item | Files | Impact | Action |
|------|-------|--------|--------|
| Shadow gate blocks IBS/TypeC/E/F | bridge.py:1583 | Kills valid signals | **REMOVE** shadow gate |
| TypeB unreachable condition | bridge.py:631 | "Best strategy" never fires | **LOOSEN** to 2-bar rising |
| TypeE threshold mismatch | config.toml:445 vs bridge.py:1359 | 0.10 vs 0.30 | **FIX** config to 0.30 |
| Dead CHECK 26 | risk_arbiter.rs:451 | Misleading, never triggers | **DELETE** |
| Dead Hurst returns | regime_detector.rs | Computed, never read | KEEP (future regime routing) |
| Orphaned strategy_config.rs | strategy_config.rs | Loaded, never queried | KEEP (documents intent) |
| Dead entry_engine.rs detectors | entry_engine.rs:88-500 | 500 LOC quarantined | KEEP (future option) |

---

## 10. Chunked Implementation Sprints

### Sprint S1: Paper Fill Audit — COMPLETED 2026-03-24
- Fills use ASK for entry, BID for exit (realistic). Zero slippage/commission in sim path.

### Sprint S2: Fix Profit Factor — COMPLETED 2026-03-24
- Added cumulative_gross_wins/losses to persistent_memory.py.

### Sprint S3: Microstructure Sprint — COMPLETED 2026-03-24
- Board lots, L1 gate, unhalt grace, spoof calibration, EC2 live config.

### Sprint S4: Unblock Strategies + Analyze Losses (NEXT)
- **Part A**: Remove TypeC/E/F shadow gate, loosen TypeB, fix TypeE threshold
- **Part B**: Analyze 66 trades — segment by ticker, session, rung attainment, P&L
- **Goal**: More strategies producing + understand WHY VanguardSniper loses
- **Files**: bridge.py:1583, bridge.py:631, config.toml:445,432-433

### Sprint S5: EC2 Instance Upgrade (15 min)
- Upgrade to 8GB RAM. Pre-live MANDATORY.

### Sprint S6: Create config.live.toml (15 min)
- Revert all 8 paper overrides. Pre-live MANDATORY.

### Sprint S7: Cost Injection into Ouroboros (1 hour)
- Add slippage + commission to persistent_memory before Ouroboros learns.

### Sprint S8: Friction-Aware Signal Ranking (1 hour)
- Rank signals by net expected P&L when multiple strategies fire simultaneously.

### Sprint S9: Per-Strategy Asymmetric Exits (1 hour)
- Different Chandelier ATR multipliers per strategy family.

### Sprint S10: Regime + Session Enforcement (2 hours)
- Wire strategy_registry.json regime/session metadata into runtime.

### Sprint S11: Symbol-Quality Memory (1 hour)
- Per-ticker quality scoring, net expectancy metrics.

### Sprint S12: Cost-Honest Backtests (1 hour)
- Add IBKR commissions + slippage to fast_backtest_pipeline.py.

---

## 11. Daily Operating Workflow

| Time (UTC) | Action |
|------------|--------|
| 07:00 | Pre-market: check containers, overnight errors, 2FA if Monday |
| 08:00 | LSE open: watch for signals |
| 14:30 | US open: watch for ORB signals |
| 16:25 | LSE close: EodFlatten fires |
| 21:00 | Post-market: daily P&L check |
| 04:50 | Nightly pipeline (cron) |

---

## 12. Recovery Procedures

```bash
# Kill switch
ssh EC2 'docker exec aegis-v2 touch /app/KILL'
# Flatten + halt
ssh EC2 'docker exec aegis-v2 touch /app/PAUSE'
# Full stop
ssh EC2 'cd ~/nzt48-aegis-v2 && docker compose down'
# Restart
ssh EC2 'docker exec aegis-v2 rm -f /app/KILL /app/PAUSE'
ssh EC2 'cd ~/nzt48-aegis-v2 && docker compose up -d'
# Rebuild (Python changes)
git push && rsync ... && ssh EC2 'docker compose build aegis-v2 && docker compose up -d && docker image prune -f'
```

---

## 13. AI Model-Role Matrix

| Role | Model | Status | Authority |
|------|-------|--------|-----------|
| Signal challenge | Claude (claude_curator.py) | Shadow mode | Advisory |
| Universe curation | Gemini 2.5 Flash | API key SET, cron 15min | Advisory |
| Nightly learning | Deterministic (nightly_v6.py) | FROZEN | Analysis only |
| Config generation | Deterministic (config_writer.py) | FROZEN | Analysis only |
| Neither AI has trading authority. All entries go through deterministic RiskArbiter. |

---

## 14. Artifact Flow Map

```
IBKR Gateway → Market ticks → Rust engine.rs
  ├─ Exit evaluation (Chandelier, time-stop, exhaustion)
  ├─ Entry gates (27 pre-signal checks)
  ├─ Python bridge (VanguardSniper + Orchestrator + inline strategies)
  │   └─ TypeA-F classification → shadow/disabled gates
  ├─ Risk arbiter (32 checks)
  └─ WAL events → Nightly pipeline → persistent_memory
```

---

## 15. Paper-vs-Live Override Analysis

| Config key | Paper | Safe live | Risk if left |
|------------|-------|-----------|--------------|
| max_positions | 999 | 3 | **CRITICAL** — unlimited exposure |
| max_heat_pct | 50% | 10% | **CRITICAL** — half equity at risk |
| daily_trade_limit | 999 | 5 | HIGH — commission death |
| spread_veto_pct | 4.5% | 1.5% | HIGH — terrible fills |
| minimum_entry_gbp | 20 | 1500 | HIGH — dust positions |
| confidence_floor | 55 | 65 | MEDIUM — weak signals |
| cash_buffer_pct | 5% | 15% | MEDIUM — no reserve |

---

## 16. Validation Gate Methodology

| Gate | Threshold | Current | Status |
|------|-----------|---------|--------|
| Win Rate | ≥ 40% | 35.4% | FAILING |
| Profit Factor | ≥ 1.3 | ~0.77 | FAILING |
| Max Consecutive Losses | < 8 | 14 | FAILING |
| Strategy Diversity | ≥ 2 with WR>35% | 1 | FAILING |

---

## 17. Commission and Slippage Model

**Current (sim)**: Zero commission, zero slippage, fills at exact ask/bid.
**Live model**: IBKR £1.50/trade (£3.00 round trip), ~0.05-2% spread, ~5bps slippage.
**Fix (Sprint S7)**: Inject 5bps slippage + IBKR commission before Ouroboros learns.

---

## 18. EC2 Infrastructure

| Resource | Current | Target |
|----------|---------|--------|
| Instance | c7i-flex.large (4GB) | c7i.large (non-burstable) for live |
| Disk | 19GB (76% used) | Expand to 30GB+ |
| Docker images | ~5GB each | Prune before builds |
| Elastic IP | 3.230.44.22 | Keep |

---

## 19. Brutal Final Verdict (Audit Section I)

**The system has sound engineering and weak economics.**

The Rust engine is excellent — deterministic, fast, institutional-grade risk. The Python bridge works but the classification layer blocks signals unnecessarily. The deployment, cron, and pipeline are healthy.

**The core problem is 35.4% WR.** This must be investigated (is it ticker selection? exit calibration? entry timing?) before building more infrastructure.

**The secondary problem is the shadow gate** killing signals that passed all quality checks. Removing it lets more strategies contribute trades, which gives us the data to answer the WR question.

**Next action**: Sprint S4 — unblock strategies + analyze trade losses.
