# AEGIS V2 — Full System Fact File
## Date: 2026-03-25 00:15 UTC
## Source: Complete codebase audit (32,827 LOC Rust + ~15,000 LOC Python + 617 config/doc files)
## Authority: Derived from code, config, and runtime artifacts — NOT from plan documents.

---

## 1. Executive Summary

AEGIS V2 is a **paper-trading algorithmic trading system** running on EC2 (c7i-flex.large, 4GB RAM, 2 vCPUs) in Docker. It trades a UK ISA account (£10,000 starting equity) via IBKR paper trading, targeting leveraged/inverse ETPs on LSE plus equities across 6 global exchanges.

**Current performance**: ~64 trades, 35.4% win rate, -£6.79 cumulative P&L. Validation gates NOT passing (need WR≥40%, PF≥1.3).

**The system is architecturally sophisticated but economically unproven.** The Rust engine (32,827 LOC across 50+ source files) is well-structured with deterministic risk control. The Python brain generates signals via VanguardSniper. However, only ONE strategy has ever produced live trades. The system has massive infrastructure (30+ cron jobs, 80+ Python modules, 100+ doc files) relative to the actual trading edge being produced.

---

## 2. Brutal Verdict

| Dimension | Rating | Evidence |
|-----------|--------|----------|
| **Engine correctness** | ★★★★☆ | 33-CHECK risk arbiter, Chandelier exit, ISA compliance, WAL — all properly wired |
| **Strategy edge** | ★☆☆☆☆ | 35.4% WR, negative P&L, only VanguardSniper produces trades. 10 strategies registered, 8 have ZERO trades |
| **Operational robustness** | ★★★☆☆ | Good Docker/cron/healthcheck setup, but 2-vCPU box runs 30+ cron jobs competing for CPU |
| **Codebase hygiene** | ★★☆☆☆ | Massive doc sprawl (100+ .md files, 14 plan versions), duplicate ouroboros packages, dead shell scripts |
| **Model governance** | ★★★★☆ | Claude/Gemini bounded to cold-path only, no hot-path model authority, shadow mode enforced |
| **Capital efficiency** | ★☆☆☆☆ | £10k equity, £6.79 in the hole, validation gates failing, compounding blocked |
| **Live-readiness** | ★★☆☆☆ | Paper-mode relaxations everywhere (max_pos=999, heat=50%, consecutive_loss=8). Would need ~50 config reverts |

---

## 3. Current System Identity

**What AEGIS V2 actually IS (from code):**
- A Rust tick-processing engine (`aegis` binary) connected to IBKR Gateway via port 4003
- A Python bridge subprocess (`bridge.py`) providing signal generation via stdin/stdout JSON protocol
- An Ouroboros nightly learning loop (observe-only, frozen at N<300)
- A 30+ cron job orchestration layer for universe scanning, reporting, Claude/Gemini intelligence
- A WAL-based event journal for crash recovery and audit trail

**What AEGIS V2 is NOT (despite what docs claim):**
- NOT a multi-strategy portfolio system (only VanguardSniper trades)
- NOT a live trading system (paper mode with relaxed risk gates)
- NOT an AI-driven trading system (Claude/Gemini are cold-path only, advisory, shadow-mode)
- NOT validated (failing all validation gates: WR, PF, consecutive losses)

---

## 4. Verified Runtime Reality

### 4.1 Engine Architecture (from engine.rs, 3511 lines)
```
Tick Flow:
  IBKR Gateway → ibkr_broker.rs → engine.process_tick_with_signal()
    → FX conversion (GBP) → GBX/100 fix → Gap detection → GARCH update
    → EVT CVaR → Quote imbalance → Kalman filter → Hayashi-Yoshida
    → Bar history → Regime detection → EXIT EVALUATION (always runs)
    → Entry gates: Dark mode → Market scheduler → Exchange-open → ModeB
    → Auction → Economic calendar → Predictive scorer → Jump-diffusion
    → Sector concentration → Liquidation defense
    → Python signal required (no signal = no trade, phantom fallback REMOVED)
    → Risk arbiter (33 checks) → L1 gate → ISA gate → Kelly sizing
    → Board lot rounding → Order submission (simulated in paper mode)
```

### 4.2 Python Bridge (from bridge.py, 2067 lines)
```
Signal Flow:
  Rust sends tick JSON → bridge.py receives via stdin
    → Accumulate 5-min OHLCV bars → Calculate indicators (RSI, RVOL, VWAP, IBS, Hurst, ADX)
    → Check exchange blackout → Check ticker blacklist → Check exchange blacklist
    → Check regime + session enforcement (strategy_registry.json)
    → VanguardSniper evaluate() → classify entry type (TypeA-F)
    → Autonomous Orchestrator (S17-S20 strategies) runs in parallel
    → Best signal wins (highest confidence)
    → Kelly 12-factor sizing → Structural tradability score
    → JSON response via stdout
```

### 4.3 Nightly Pipeline (04:50 UTC, from nightly_pipeline.sh)
```
Step 1: nightly_v6.py (CRITICAL — abort on failure)
Step 2: config_writer.py → dynamic_weights.toml (CRITICAL)
Step 3: win_loss_delta.py → Google Sheets (non-critical)
Step 4: claude_review.py → forensic review (non-critical)
Step 5: ouroboros_challenger.py → parameter challenge (non-critical)
Step 6: approval_gate.py → governed config changes (non-critical)
```

---

## 5. Source of Truth Hierarchy

| Priority | Source | Governs |
|----------|--------|---------|
| 1 | `rust_core/src/*.rs` (compiled binary) | Execution, risk, exits, orders, state |
| 2 | `config/config.toml` | All configurable parameters |
| 3 | `config/contracts.toml` | Universe (1251 contracts) |
| 4 | `config/strategy_registry.json` | Strategy status (live/shadow/disabled) |
| 5 | `config/dynamic_weights.toml` | Ouroboros-calibrated overlays (FROZEN) |
| 6 | `python_brain/bridge.py` | Signal generation logic |
| 7 | `config/active_watchlist.json` | Current 100-ticker streaming set |
| 8 | WAL (`events/current.ndjson`) | Runtime event journal |
| 9 | `data/system_memory.json` | Persistent cumulative stats |

---

## 6. Authority Map

| Domain | Authority | Model Role | Bounded? |
|--------|-----------|------------|----------|
| Order execution | Rust engine | NONE | ✅ |
| Risk regime | Rust RiskArbiter | NONE | ✅ |
| Exit management | Rust ExitEngine (Chandelier) | NONE | ✅ |
| Signal generation | Python bridge (VanguardSniper) | NONE | ✅ |
| Universe curation | Python ticker_selector + Gemini | Gemini: ranking advisory | ✅ Cold-path |
| Nightly learning | Python Ouroboros (FROZEN) | Claude: forensic review | ✅ Cold-path |
| Signal challenge | Claude (SHADOW mode) | Claude: advisory only | ✅ Shadow |
| Morning briefing | Claude | Claude: advisory only | ✅ Cold-path |
| Hot-path decisions | DETERMINISTIC RUST | NO MODEL | ✅ |

---

## 7. Strategy Reality Matrix

| Strategy | ID | Status | Python Path | Rust Path | Config | Live Trades | Ruling |
|----------|----|--------|-------------|-----------|--------|-------------|--------|
| VanguardSniper | VanguardSniper | **LIVE** | bridge.py → vanguard_sniper.py | engine.rs (via signal) | brain/config.py | **33** | ONLY proven producer |
| TypeB EarlyRunner | TypeB | **LIVE** | bridge.py classify → TypeB | QUARANTINED | config.toml type_b_* | *subset of VS* | Classification of VS signals |
| TypeE IBS MR | TypeE | SHADOW | bridge.py IBS_MeanReversion | QUARANTINED | config.toml type_e_* | ~3 | Shadow-logged, needs validation |
| TypeA DipRecovery | TypeA | DISABLED | Classified but blocked | QUARANTINED | config.toml type_a_* | 0 | WR 29.5% — proven loser |
| TypeD SupportBounce | TypeD | DISABLED | Classified but blocked | QUARANTINED | config.toml type_d_* | 0 | WR 24.1% — proven loser |
| TypeC OverboughtFade | TypeC | SHADOW | Classified but shadow | QUARANTINED | config.toml type_c_* | 0 | Rare trigger, unproven |
| TypeF OBV Divergence | TypeF | SHADOW | Classified but shadow | QUARANTINED | config.toml type_f_* | 0 | Unproven |
| Orchestrator VWAPDip | S17 | SHADOW | bridge.py orchestrate() | N/A | strategies.toml | 0 | 0 production trades |
| VolExpansion | VolExp | SHADOW | bridge.py | N/A | inline | 0 | 0 production trades |
| ORB Breakout | ORB | SHADOW | bridge.py | N/A | inline | 0 | 0 production trades |
| GapFade | GapFade | SHADOW | bridge.py | N/A | inline/strategies.toml | 0 | 0 production trades |
| ApexScout | Apex | SHADOW | bridge.py → apex_scout.py | engine.rs Apex path | N/A | 0 | Mode A only, untested |

**Key finding**: VanguardSniper is the ONLY strategy generating real trades. TypeA-F are classifications applied to VanguardSniper output. The Autonomous Orchestrator (S17-S20) produces shadow signals only. ApexScout exists but Apex tickers don't get routed to it effectively.

---

## 8. Claude / Gemini / Ouroboros Matrix

| Role | Claude? | Gemini? | Ouroboros? | Reads | Writes | Authority | Correct? | Useful? |
|------|---------|---------|------------|-------|--------|-----------|----------|---------|
| Signal generation | No | No | No | N/A | N/A | N/A | ✅ | N/A |
| Signal challenge | SHADOW | No | No | WAL signals | claude_curator.ndjson | ADVISORY | ✅ | Unproven |
| Universe curation | SHADOW | YES | No | contracts.toml, market data | gemini/core_universe_latest.json | ADVISORY | ✅ | Partially |
| Nightly forensic review | YES | No | No | WAL, system_memory | claude/reviews/ | ADVISORY | ✅ | Yes (reporting) |
| Morning briefing | YES | YES | No | WAL, positions, market | claude/briefings/, Telegram | ADVISORY | ✅ | Yes (operator) |
| Parameter mutation | No | No | FROZEN | WAL, system_memory | dynamic_weights.toml | FROZEN | ✅ | Blocked (N<300) |
| Config writing | No | No | YES | ouroboros_recommendations.json | dynamic_weights.toml | WRITE (gated) | ✅ | Active but frozen |
| Ticker selection | No | No | YES | universe, contracts | active_watchlist.json | WRITE | ✅ | Active |
| Gate calibration | SHADOW | No | No | WAL rejected signals | Telegram report | ADVISORY | ✅ | Unproven |
| Psych audit | YES | No | No | Trade patterns | Telegram report | ADVISORY | ✅ | Novel |
| Filing scanner | SHADOW | No | No | SEC/RNS filings | claude/filings/ | ADVISORY | ✅ | Unproven |

---

## 9. Codebase Contradiction Register

| ID | Subsystem | Files | Contradiction | Severity | Evidence | Ruling |
|----|-----------|-------|--------------|----------|----------|--------|
| C-01 | Config | config.toml vs dynamic_weights.toml | confidence_floor = 50 in config.toml but 45 in dynamic_weights.toml — which wins? | HIGH | config.toml line 10 vs dynamic_weights.toml line 38 | bridge.py loads both; dynamic_weights overrides. But Rust reads config.toml only. **SPLIT BRAIN** |
| C-02 | Risk | config.toml [position] | max_simultaneous_positions=999, portfolio_heat_limit=50%, sector_heat=80% — all paper-relaxed with "revert for live" comments | MEDIUM | Lines 21-25 | Intentional paper relaxation. ~50 values need revert for live. |
| C-03 | Ouroboros | ouroboros/ vs python_brain/ouroboros/ | TWO separate ouroboros packages at project root and in python_brain/ | HIGH | Dockerfile copies both. Crontab runs python_brain.ouroboros.* exclusively. Root ouroboros/ appears UNUSED. | Root ouroboros/ is likely dead code from earlier iteration |
| C-04 | LineBudget | engine.rs line 288 | LineBudget enforces carry+active+scan <= 100 but max_simultaneous_lines = 200 in config | MEDIUM | engine.rs line 288 vs config.toml line 235 | LineBudget is instantiated but NEVER READ by the engine. Dead abstraction. |
| C-05 | RotationScanner | engine.rs line 1473 | Comment says "RotationScanner — instantiated but NEVER CALLED (dead code)" | LOW | Self-documented dead code | Already identified, not yet removed |
| C-06 | Entry engine | entry_engine.rs | Contains EarlyRunnerDetector, DipRecoveryDetector, etc. — ALL QUARANTINED, never called | MEDIUM | strategy_registry.json confirms all Rust detectors QUARANTINED | entry_engine.rs is 811 lines of mostly dead code |
| C-07 | Exhaustion exit | engine.rs line 1173 | Volume exhaustion uses realized_vol() (annualized σ) where it should use RVOL (relative volume) | HIGH | Code says "current_rvol = realized_vol(6120.0)" but needs volume/avg_volume ratio | **BUG**: realized_vol returns annualized volatility ~0.30, NOT relative volume ~3.0. Exhaustion threshold of 10.0 will NEVER trigger. |
| C-08 | Dynamic weights | dynamic_weights.toml | bayesian.win_rate = 0.791667 (79%) but actual WR is 35.4% | HIGH | dynamic_weights.toml line 9 vs memory MEMORY.md | Stale value from 2026-03-19 when only 20 trades existed. Ouroboros is FROZEN so it hasn't updated. |
| C-09 | Chandelier | dynamic_weights.toml | chandelier_atr_mult = 3.05 vs config.toml initial_stop_atr_mult = 1.5 | MEDIUM | dynamic_weights.toml line 16 vs config.toml line 381 | Unclear if exit engine reads dynamic_weights overlay. Code inspection needed. |
| C-10 | ISA gate | engine.rs line 607 | LiquidationDefense::new(20_000.0) hardcodes £20,000 ISA limit instead of reading from config | LOW | config.toml isa_annual_limit_gbp = 20000 exists but LD hardcodes | Config exists but not wired to LiquidationDefense constructor |

---

## 10. Dead / Duplicate / Misleading Code Register

| Component | Status | Evidence | Impact | Recommendation |
|-----------|--------|----------|--------|----------------|
| `ouroboros/` (root) | DEAD | Crontab uses `python_brain.ouroboros.*` exclusively. Dockerfile copies but nothing imports root ouroboros/ | Disk waste, confusion | DELETE entire root ouroboros/ directory |
| `entry_engine.rs` (811 LOC) | MOSTLY DEAD | All detectors QUARANTINED per strategy_registry.json. Only imported but never called. | 811 lines of dead code | KEEP file but add clear QUARANTINED header |
| Shell scripts at root | DEAD | AEGIS_COMPLETE_EXECUTION.sh, AEGIS_INTERACTIVE.sh, THE_MASTER_COMMAND.sh, EXECUTE_FULL_PLAN.sh, PHASE_1_THROUGH_4_EXECUTOR.sh — from early development, not in crontab or Dockerfile | Confusion | MOVE to docs/archive/ |
| `docs/AEGIS_MASTER_PLAN_v17.md` through `v30.md` | STALE | 14 plan versions. Canonical is `AEGIS_V2_CANONICAL_MASTER_PLAN_20260324.md` | Massive confusion. New sessions waste time reading old plans. | MOVE v17-v30 to docs/archive/ |
| `docs/AEGIS_SELF_ANALYSIS_TRIAGE_v*.md` (12 files) | STALE | Superseded by canonical plan | Same | MOVE to docs/archive/ |
| LineBudget struct | DEAD | Defined in engine.rs, has tests, but NEVER read by any runtime code | 40 lines of dead code + tests | DELETE or wire |
| RotationScanner | DEAD | engine.rs line 1473 explicitly says "instantiated but NEVER CALLED" | CPU waste (instantiated), confusion | Either wire or remove |
| `dashboard/wal_dashboard.py` | UNKNOWN | Single file, not in crontab, unclear if used | Minor | CHECK if used |
| `CODEBASE_DUMP_FOR_GEMINI.txt` | STALE | Snapshot for Gemini review, outdated | Confusion | DELETE |
| `whole gemini chat + llm of chatgpt.txt` | STALE | Chat transcript, not code | Clutter | MOVE to docs/archive/ |
| Multiple PDF files at root | OUTPUT | 8 PDFs from generate_review_pdf.py | Output artifacts | Consider .gitignoring |
| `data/audit/` | STALE | Audit from 2026-03-23, superseded by this file | Minor | Keep for history |

---

## 11. Config Truth Register

| Config File | Live? | Who Reads It? | Key Facts |
|-------------|-------|---------------|-----------|
| `config/config.toml` | YES | Rust engine + Python bridge | 617 lines, ALL parameters. Paper-relaxed values marked with "revert for live" |
| `config/contracts.toml` | YES | Rust engine (startup), Python (various) | 1251 contracts across 7+ exchanges |
| `config/dynamic_weights.toml` | YES | Python bridge (confidence floor), Ouroboros | FROZEN (observe_only=true). Stale WR=79% (actual=35%) |
| `config/strategy_registry.json` | YES | Python bridge (regime+session gating) | 11 strategies registered. 2 LIVE, 6 SHADOW, 2 DISABLED |
| `config/strategies.toml` | PARTIAL | Python orchestrator strategies | Referenced but may be stale |
| `config/config.live.toml` | NO (future) | None currently | Prepared for live trading config overlay |
| `config/active_watchlist.json` | YES | Rust engine (subscription rotation) | Generated by ticker_selector every 2h |
| `config/universe.json` | YES | Universe pipeline | ISA-eligible universe |
| `config/fx_rates.toml` | YES | Rust FxRateTable | Refreshed every 6h via yfinance |
| `config/economic_calendar.toml` | YES | Rust engine (entry blackout) | FOMC/CPI/NFP/BOE events |
| `config/uk_holidays.toml` | YES | Various Python modules | UK bank holidays |

---

## 12. Compounding Blockers Register

| Blocker | Severity | Evidence | Fix Path |
|---------|----------|----------|----------|
| **35.4% win rate** | CRITICAL | Only 64 trades, WR well below 40% gate | Need strategy improvement OR more diverse signal sources |
| **Only 1 active strategy** | CRITICAL | VanguardSniper = 33 trades. All others = 0 trades | Promote TypeE (IBS) or Orchestrator strategies from shadow |
| **Ouroboros FROZEN** | HIGH | observe_only=true, min_trades=300, current=64 | Must accumulate 236 more trades before learning unlocks |
| **Stale dynamic_weights** | HIGH | WR=79% in file vs 35% actual. chandelier_atr_mult=3.05 stale | Run config_writer manually to refresh (won't mutate due to frozen) |
| **Paper-relaxed risk gates** | MEDIUM | max_pos=999, heat=50%, daily_trades=999 | Data is being collected under unrealistic conditions |
| **Volume exhaustion bug (C-07)** | MEDIUM | Using realized_vol (σ~0.30) instead of RVOL for exhaustion check | Exhaustion exit never triggers. Profits given back unnecessarily. |
| **Confidence floor split-brain (C-01)** | HIGH | config.toml=50, dynamic_weights=45. Bridge reads dynamic. Rust reads config. | Signals may pass Python but get vetoed by Rust at different threshold |
| **2-vCPU resource contention** | MEDIUM | 30+ cron jobs + engine + bridge + supercronic on 4GB/2vCPU | Bridge latency spikes during Claude/Gemini API calls |

---

## 13. Highest-ROI Fix Order

| Priority | Fix | ROI Impact | Risk | Files |
|----------|-----|------------|------|-------|
| 1 | **Fix volume exhaustion bug (C-07)** | Prevents giving back profits on climactic reversals | LOW | engine.rs line 1173 — replace `realized_vol(6120.0)` with actual RVOL from Python signal |
| 2 | **Resolve confidence floor split-brain (C-01)** | Prevents signal loss from mismatch | LOW | Either: bridge reads config.toml OR Rust reads dynamic_weights |
| 3 | **Delete root ouroboros/ package** | Eliminates confusion, saves Docker build time | NONE | Delete entire ouroboros/ directory |
| 4 | **Archive stale docs** | Prevents new sessions wasting context on dead plans | NONE | Move 60+ files to docs/archive/ |
| 5 | **Promote TypeE IBS to LIVE** | Diversify signal sources beyond VanguardSniper | MEDIUM | strategy_registry.json: TypeE status → "live" |
| 6 | **Fix stale dynamic_weights WR** | Ouroboros downstream consumers get wrong statistics | LOW | Run config_writer refresh |
| 7 | **Wire LiquidationDefense to config** | Remove hardcoded £20,000 | LOW | engine.rs line 607 |
| 8 | **Remove dead shell scripts from root** | Clean project root | NONE | Move 5 .sh files to docs/archive/ |

---

## 14. Paper vs Live Truth Register

| Parameter | Paper Value | Live Value (from comments) | Risk |
|-----------|------------|---------------------------|------|
| max_simultaneous_positions | 999 | 3 | Paper data collected under impossible live conditions |
| portfolio_heat_limit_pct | 50.0 | 10.0 | Same |
| sector_heat_cap_pct | 80.0 | 33.0 | Same |
| cash_buffer_pct | 5.0 | 25.0 | Same |
| consecutive_loss_halt | 8 | 5 | Same |
| max_daily_trades | 999 | 3 | Same |
| min_gross_edge_pct | 0.10 | 0.15 | Same |
| spread_veto_pct | 0.3 | 0.3 | SAME — correctly aligned |
| slippage_assumption_pct | 0.5 | 0.5 | SAME — correctly aligned |
| kelly_ramp_target | 50 | 250? | Paper ramps faster, unclear live target |

---

## 15. Infrastructure Truth

| Component | Status | Notes |
|-----------|--------|-------|
| EC2 c7i-flex.large | RUNNING | 4GB RAM, 2 vCPUs. Burstable. Live target: c7i.large (non-burstable) |
| Docker containers | 3 | aegis-v2 (engine), aegis-ib-gateway (IBKR), aegis-redis |
| Redis | RUNNING | Password: nzt48redis. Used for state journal |
| IBKR Gateway | Paper mode | Port 4003, client_id=101. Monday 2FA re-auth required |
| Supercronic | RUNNING | 30+ cron entries, TZ=UTC |
| WAL | Active | events/current.ndjson, archived on rotation |
| Telegram | Active | Alerts, briefings, kill switch |
| Google Sheets | Active | Synced every 15 min via sheets_sync.py |
| Claude Code CLI | Installed | Node.js 22, npm @anthropic-ai/claude-code |
| Gemini Pro | Configured | 2-hourly universe scans |

---

## 16. Immediate Next Sprint

### S9: Fix Volume Exhaustion Bug (C-07) — 30 min
- **File**: `rust_core/src/engine.rs` line 1173
- **Bug**: `realized_vol(6120.0)` returns annualized σ (~0.30), not RVOL (current_volume/avg_volume)
- **Fix**: Use RVOL from Python signal metadata or compute from bar_history volume data
- **Test**: Verify exhaustion stop triggers when RVOL > 10.0
- **Deploy**: Docker rebuild required (Rust change)

### S10: Resolve Confidence Floor Split-Brain (C-01) — 15 min
- **Issue**: bridge.py reads dynamic_weights.toml (floor=45), Rust reads config.toml (floor=50)
- **Fix Option A**: Make Rust also load dynamic_weights confidence_floor overlay
- **Fix Option B**: Make bridge.py use config.toml value only
- **Recommendation**: Option B (simpler, one source of truth)

### S11: Cleanup Sprint — 1 hour
- Delete root `ouroboros/` directory
- Move stale shell scripts to `docs/archive/`
- Move plan versions v17-v30 to `docs/archive/`
- Move analysis/triage docs to `docs/archive/`

---

## 17. Stop-State / Resume Guidance

### What was completed in this session:
- Full codebase read: 50+ Rust files (32,827 LOC), 80+ Python files, all configs, all docs
- Architecture reverse-engineered from code (not docs)
- 10 contradictions identified with evidence
- 12+ dead/stale components catalogued
- ROI-ranked fix backlog produced
- Compounding blockers identified
- Strategy reality matrix (only VanguardSniper trades)
- Full fact file generated

### What remains:
1. **Implement C-07 fix** (volume exhaustion bug) — requires Rust code change + Docker rebuild
2. **Implement C-01 fix** (confidence floor split-brain) — Python or Rust change
3. **Cleanup sprint** (delete dead code/docs) — safe, no runtime impact
4. **Strategy promotion** (TypeE IBS to live) — requires monitoring plan
5. **Deep Python audit** — 80+ ouroboros modules, many may be stubs
6. **Backtest coherence audit** — 5 different backtest files, unclear which is canonical
7. **EC2 deployment verification** — SSH to EC2, check actual running state
8. **Live config preparation** — ~50 paper-relaxed values need live equivalents

### Recovery path for next session:
1. Read this fact file
2. Read `MASTER_PROGRESS_LEDGER.md`
3. Start with Fix #1 (C-07 volume exhaustion) — highest ROI
4. Then Fix #2 (C-01 confidence floor)
5. Then cleanup sprint

---

## APPENDIX A: File Counts by Category

| Category | Count | LOC |
|----------|-------|-----|
| Rust source (.rs) | 50 | 32,827 |
| Python source (.py) | ~90 | ~15,000 |
| Config files (.toml, .json) | 20 | ~3,000 |
| Documentation (.md) | 100+ | ~50,000+ |
| Shell scripts (.sh) | 15 | ~500 |
| PDFs | 10 | N/A |
| Test files | 15 | ~3,000 |
| Docker/infra | 5 | ~200 |

## APPENDIX B: Cron Job Inventory (30+ entries)

**Critical (pipeline):**
- 04:50 UTC: nightly_pipeline.sh (nightly_v6 → config_writer → win_loss_delta → claude_review)

**High-frequency (every 15 min or less):**
- */15: bridge_health, sheets_sync
- */10: external_monitor

**2-hourly:**
- ticker_selector (universe scanning)
- gemini_scanner (Gemini core universe)
- claude_curation (shadow mode)

**Daily:**
- 04:40: maintenance cleanup
- 04:45: log rotation
- 05:00: bridge log rotation
- 05:10: ouroboros_monitor
- 05:30: update_universe
- 05:35: sync_universe
- 06:00: universe_refresh, gemini_brief, claude_filing_scanner
- 07:00: backfill_simulator
- 07:45: claude_briefing (morning)
- 08:00: external_monitor daily report
- 21:15: daily_sim_report
- 21:20: cost_drag_report
- 21:30: claude_briefing (evening)

**Weekly:**
- Friday 22:00: claude_rejected_review
- Sunday 22:00: ibkr_scanner
- Sunday 23:00: claude_psych_audit
- Monday 04:52: config_fixes FTT registry

**Session briefings (DST-aware, dual-time):**
- Asian: 23:55 / 00:55 UTC
- European: 06:55 / 07:55 UTC
- American: 13:25 / 14:25 UTC
- US-only: 15:30 / 16:30 UTC

---

*This fact file was generated by full codebase audit on 2026-03-25. It reflects code truth, not plan aspirations.*
