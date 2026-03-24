# AEGIS V2 — Canonical Master Plan
**Date**: 2026-03-24 02:00 UTC
**Status**: PARTIAL — critical sections complete, detail expansion needed in next session
**Supersedes**: All prior MASTER_PLAN, IMPLEMENTATION_PLAN, PLAN_1, PLAN_2 docs (now in docs/archive/)
**Source of truth hierarchy**: Code > Runtime > This plan > Old docs

---

## 1. Executive Summary

AEGIS V2 is a paper-trading system running on EC2 (c7i-flex.large, 4GB RAM, 19GB disk). It processes live IBKR market data through a Rust execution engine (32,603 LOC) with a Python signal brain (1,850 LOC bridge). IS_LIVE=false is hardcoded — the system cannot place real orders.

**Current performance**: 48 nightly-tracked trades, 35.4% WR, -£6.79 cumulative P&L. LSEETF leveraged ETPs are the primary loss source (0% WR, -£30.34 over 28 trades). Asian equities showed 100% WR (+£16.91) but this likely reflects paper-mode mid-point fill artifacts.

**This session (2026-03-23/24)**: 12 commits. Full codebase audit. 4 new strategies deployed. Ouroboros frozen. Gemini API key configured. IBS thresholds loosened. 108 stale docs archived. Brutal-truth 11-page PDF generated.

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
| C05 | Paper fills | Asian 100% WR suggests mid-point fills | Paper broker likely fills at mid, not realistic | HIGH | Investigate paper_broker fill logic |
| C06 | Profit Factor | system_memory shows PF=0.0 | Gross winners exist (+£23.55 session) | MEDIUM | nightly_v6 PF calculation bug |

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

### Sprint S1: Paper Fill Audit (30 min, no Rust rebuild needed)
- **Goal**: Determine if paper_broker.rs fills at mid-point or realistic bid/ask
- **Files**: `rust_core/src/paper_broker.rs`
- **Test**: Compare WAL `entry_price` vs `bid`/`ask` at entry time
- **Success**: Document exactly how paper fills work
- **Pre-live**: MANDATORY

### Sprint S2: Fix Profit Factor Calculation (30 min)
- **Goal**: Fix PF=0.0 bug in nightly_v6.py
- **Files**: `python_brain/ouroboros/nightly_v6.py`
- **Test**: Manually compute PF from WAL and compare to nightly output
- **Success**: system_memory.json shows correct PF
- **Pre-live**: MANDATORY

### Sprint S3: Rust Rebuild (time-stop + dead-code quarantine) (2 hours)
- **Goal**: Add time-stop to exit_engine.rs, quarantine dead code
- **Blocker**: Need 5GB free disk — requires EC2 disk expansion or aggressive prune
- **Files**: `rust_core/src/exit_engine.rs`, `rust_core/src/entry_engine.rs`, `rust_core/src/lib.rs`
- **Pre-live**: MANDATORY (time-stop), MEDIUM (quarantine)

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

The system is a paper-trading prototype with institutional-grade risk infrastructure in Rust. The Python signal layer is functional but unproven — only 1 of 6 strategies has produced signals, and overall performance is below validation thresholds. The biggest immediate risks are: (1) paper fill realism unverified, (2) no time-stop for stale positions, (3) 8 paper overrides that would be catastrophic in live, (4) Ouroboros frozen on statistically invalid data.

The system needs 300+ spread-adjusted trades across multiple strategies before considering live capital. Estimated timeline to readiness: 4-8 weeks of paper trading at current signal rate.

---

## 13. Stop-State Handoff

**Sections completed**: 1-12 (executive summary through final verdict)
**Sections needing expansion in next session**:
- Detailed model-role matrix (Claude/Gemini per-role table)
- Full artifact flow map with paths
- Full nightly pipeline step-by-step
- Per-strategy runtime proof investigation
- Dead-code quarantine register with exact file list
- Stale-doc register
- Paper-vs-live override detailed impact analysis
- Validation gate rolling-window methodology
- Sprint detail expansion (S1-S9 need full implementation steps)

**Exact next action**: Start next session by reading this file, then expand sections 7-9 with sprint-level detail and run Sprint S1 (paper fill audit) and Sprint S2 (PF bug fix).

**Files to read on resume**:
- This plan: `AEGIS_V2_CANONICAL_MASTER_PLAN_20260324.md`
- Operating manual: `AEGIS_V2_CURRENT_STATE_OPERATING_MANUAL.md`
- Bridge: `python_brain/bridge.py`
- Paper broker: `rust_core/src/paper_broker.rs`
- Nightly: `python_brain/ouroboros/nightly_v6.py`
