# AEGIS V2 — Canonical Master Plan v2.0
**Date**: 2026-03-24 03:00 UTC
**Status**: COMPLETE — 35 sections, evidence-bound, verified against live runtime
**Supersedes**: AEGIS_V2_CANONICAL_MASTER_PLAN_20260324.md and ALL prior plan docs
**Source of truth hierarchy**: Code > Runtime > Config > This plan > Old docs
**Verification method**: 4 parallel agents read all code + config + EC2 runtime + logs

---

## 1. Executive Summary

AEGIS V2 is a **paper-trading prototype** running on EC2 c7i-flex.large (4GB RAM, 19GB disk at 80% usage). It processes live IBKR market data through a Rust execution engine (32,603 LOC, 51 modules) with a Python signal brain (1,863 LOC bridge + 6,000+ LOC supporting files). `IS_LIVE=false` is hardcoded — the system cannot place real orders.

**Current performance**: 48 nightly-tracked trades (since 2026-03-18), 35.4% WR, -£6.79 cumulative P&L, PF=0.0 (bug — fixed this session, not yet run through nightly). Only VanguardSniper has produced trades (33 of the 48). All other strategies have 0 production signals.

**CRITICAL RUNTIME ISSUE DISCOVERED THIS SESSION**: The system is currently **generating signals but ALL are being vetoed** by RiskArbiter CHECK 10 (ConfidenceBelowFloor). Signals arrive at confidence=49.0 but the floor (from dynamic_weights.toml) is 65. The bridge's base confidence in non-LSE markets is too low to pass. Only 11 of 50 configured tickers are actively receiving data — the rest hit IBKR's tick-by-tick limit. The system is effectively **dead** outside LSE market hours.

**This session (2026-03-24 session 2)**: Sprint S1 (paper fill audit) and S2 (PF bug fix) completed. Plan expanded from 22 to 35 sections with full evidence-based detail.

---

## 2. Current Verified System State

| Fact | Value | Evidence | Verified |
|------|-------|----------|----------|
| Branch | `feat/tier-system-enhancements-full` @ `d17d9bd` | `git log --oneline -1` | Yes |
| Containers | 3 healthy (aegis-v2, ib-gateway, redis) | `docker ps` on EC2 | Yes |
| Disk | 80% (3.9GB free / 19GB) | `df -h /` on EC2 | Yes |
| IB Gateway | Connected, 2FA approved | Subscription active | Yes |
| Market data subscriptions | 200 mkt + 102 L1 attempted, but tick-by-tick limit hit for many | Engine logs | Yes |
| Active tickers receiving data | **11** (not 50) | `active_watchlist.json` on EC2 | Yes |
| Python Bridge | Running, producing signals | SIGNAL_ARRIVED logs | Yes |
| Signal outcome | **ALL vetoed** (ConfidenceBelowFloor, conf=49 < floor=55) | VETO logs | Yes |
| Ouroboros | FROZEN (observe_only=true) | config.toml | Yes |
| Gemini API key | **COMMENTED OUT in .env** — Gemini crons fail silently | `.env` inspection | Yes |
| Claude | CLI at /usr/bin/claude, shadow mode (non-blocking) | entrypoint.sh | Yes |
| Strategies deployed | 6 sources + TypeA-F classification in bridge.py | Code audit | Yes |
| Strategies producing signals | 0 right now (all vetoed) | Runtime logs | Yes |
| Strategies with historical trades | 1 (VanguardSniper: 33), 1 (TypeE: 3 classified) | WAL data | Yes |
| Equity | £10,000 | system_memory.json | Yes |
| Open positions | 0 | Heartbeat logs | Yes |
| dynamic_weights.toml | **STALE** — WR=79.2% from 20 trades, confidence_floor=65 | File content | Yes |
| Nightly pipeline | Runs 04:50 UTC via flock'd script | Crontab | Yes |
| system_memory PF | 0.0 (BUG — fix deployed, awaits next nightly) | system_memory.json | Yes |
| Risk CHECKs | **27 active** (not 33 as previously documented) | risk_arbiter.rs audit | Yes |
| Time-stop | **ABSENT** despite config.toml having `[exit_time_stop] enabled=true` | exit_engine.rs audit | Yes |
| Cron jobs | **35 scheduled jobs** in crontab | Crontab audit | Yes |
| Config sections | **30 sections** in config.toml | Full file read | Yes |

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
| 7 | This plan (v2.0) | Architecture decisions, roadmap |
| 8 | Old plan docs (docs/archive/) | Historical context only, NOT authoritative |

---

## 4. Actual System Architecture

```
IBKR Gateway (aegis-ib-gateway:4003)
  │
  ├─ Market data ticks (200 mkt subs + 102 L1 attempted)
  │   └─ PROBLEM: tick-by-tick limit hit → only ~11 tickers get real data
  │
  └─ Rust engine.rs (process_tick_with_signal)
      │
      ├─ Pre-processing: FX, GBX→GBP, gap detection, GARCH, Kalman, regime
      │
      ├─ EXIT path (runs first, always — even in Dark mode)
      │   └─ exit_engine.rs: Chandelier 5-rung, EOD flatten, volume exhaustion
      │       └─ NO TIME-STOP (config says enabled but code doesn't implement)
      │
      ├─ ENTRY path (gated by session, mode, exchange hours, 15+ filters)
      │   ├─ Python bridge.py (stdin/stdout JSON IPC, 5-stage pipeline)
      │   │   ├─ Stage 0: Blackout/blacklist/warmup gates
      │   │   ├─ Stage 1: Indicator computation (RVOL, Hurst, ADX, VWAP, IBS)
      │   │   ├─ Stage 2: Quality gates (spread, VWAP extension, STS)
      │   │   ├─ Stage 3: 6 signal sources (VanguardSniper, Orchestrator, IBS, VolExp, ORB, GapFade)
      │   │   ├─ Stage 4: Adjustments + TypeA-F classification + Claude shadow
      │   │   └─ Stage 5: Best signal output
      │   │
      │   └─ RiskArbiter.evaluate() → 27 deterministic CHECKs
      │       └─ PROBLEM: CHECK 10 vetoing ALL signals (conf=49 < floor=55)
      │
      └─ WAL events → /app/events/current.ndjson
          └─ Nightly pipeline (04:50 UTC, flock'd)
              ├─ nightly_v6.py → daily metrics + recommendations
              ├─ config_writer.py → dynamic_weights.toml (FROZEN — observe_only)
              ├─ persistent_memory.py → system_memory.json (PF FIX DEPLOYED)
              ├─ Claude forensic review (04:53 UTC)
              └─ Fill quality + post-trade diagnostics (04:54-55 UTC)

Supporting infrastructure:
  ├─ Redis (aegis-redis:6379) — state journal, password-protected
  ├─ Telegram — kill switch, alerts, heartbeat, reports
  ├─ Google Sheets — trade sync every 5 min
  ├─ ticker_selector.py — universe rotation every 2h (15min during market hours)
  ├─ Gemini scanner — universe curation every 2h (BROKEN — API key not in .env)
  └─ 35 total cron jobs via supercronic
```

---

## 5. Runtime Ownership Map

| Concern | Owner | Authority | File(s) | Notes |
|---------|-------|-----------|---------|-------|
| Tick processing | Rust engine.rs | FINAL | engine.rs:891+ | 100ms main loop |
| Entry risk gating | Rust risk_arbiter.rs | FINAL, deterministic | risk_arbiter.rs | 27 CHECKs |
| Exit decisions | Rust exit_engine.rs | FINAL, Chandelier | exit_engine.rs | No time-stop |
| Signal generation | Python bridge.py | 6 sources, best-by-confidence | bridge.py | Hot path, every tick |
| Entry type classification | Python bridge.py Stage 4 | classify_entry_type() | bridge.py:601+ | Python only (Rust version unused) |
| Position sizing | Python kelly_12factor.py | 12-factor multiplicative | kelly_12factor.py | Called per signal |
| Nightly learning | Python nightly_v6.py | Analysis only (FROZEN) | nightly_v6.py | 04:50 UTC cron |
| Config generation | Python config_writer.py | FROZEN (observe_only) | config_writer.py | 04:51 UTC cron |
| Universe curation | Python ticker_selector.py | Writes active_watchlist.json | ticker_selector.py | Every 2h |
| Signal challenge | Claude curator | Shadow only, non-blocking, fail-open | claude_curator.py | >55 confidence only |
| Universe AI curation | Gemini | **BROKEN** (API key commented out) | gemini_scanner.py | Every 2h (silently failing) |

---

## 6. Rust / Python / Config / Runtime Authority Boundaries

| Decision | Rust | Python | Config | Runtime |
|----------|------|--------|--------|---------|
| "Should we enter?" | CHECK 1-27 veto | Signal generation + confidence | Thresholds in config.toml | dynamic_weights overrides |
| "What price?" | tick.ask (entry), tick.bid (exit) | N/A | marketable_limit_buffer_pct | N/A |
| "How many shares?" | qty = notional / ask | Kelly 12-factor sizing | kelly caps, sizing params | Half-Kelly for <250 trades |
| "When to exit?" | Chandelier rung + EOD | N/A | rung_pct, ATR mults | adaptive chandelier |
| "Which tickers?" | Universe from watchlist | ticker_selector scoring | contracts.toml | active_watchlist.json |
| "What confidence?" | Floor from config | Base from strategy + adjustments | confidence_floor | adaptive from dynamic_weights |

**Conflict: dynamic_weights.toml sets confidence_floor=65 but Python bridge generates signals at conf=49 for non-LSE tickers. This effectively blocks ALL non-LSE trading.**

---

## 7. Strategy Inventory and Classification

| Strategy | Canonical Name | Status | Python path | Config | Observed | Trades | Backtest WR | Verdict |
|----------|---------------|--------|-------------|--------|----------|--------|-------------|---------|
| VanguardSniper | Momentum | **LIVE** | bridge.py:1302 | brain/config.py | YES | 33 | N/A | Only proven producer |
| Orchestrator S17 | VWAP Dip Buy | **LIVE** | bridge.py:1318→autonomous_orchestrator | strategies.toml | NO | 0 | N/A | Needs mean-reverting regime + sigma conditions |
| Orchestrator S18 | Gap Fade | **LIVE** | bridge.py:1318→autonomous_orchestrator | strategies.toml | NO | 0 | N/A | Needs gap event |
| Orchestrator S19 | RSI(2)/IBS | **LIVE** | bridge.py:1318→autonomous_orchestrator | strategies.toml | NO | 0 | N/A | RSI(2)<5 very strict |
| Orchestrator S20 | Cross-Market Momentum | **LIVE** | bridge.py:1318→autonomous_orchestrator | strategies.toml | NO | 0 | N/A | Needs SPY return data |
| IBS Mean Reversion | IBS_MR | **LIVE** | bridge.py:1342 | Inline | NO | 0 | N/A | IBS<0.30 + RSI(2)<25 |
| VolExpansion | VolExp | **LIVE** | bridge.py:1370 | Inline | NO | 0 | N/A | RVOL>2.0 rare |
| ORB Breakout | ORB | **LIVE** | bridge.py:1401 | Inline | NO | 0 | N/A | US session only, 45min window |
| GapFade (inline) | GapFade | **LIVE** | bridge.py:1440 | Inline | NO | 0 | N/A | Needs gap-down > 1% |
| TypeA (DipRecovery) | TypeA | **CLASSIFICATION ONLY** | bridge.py:638 | config.toml:425 | NO | 0 | 29.5% | Net loser in backtest |
| TypeB (EarlyRunner) | TypeB | **CLASSIFICATION ONLY** | bridge.py:624 | config.toml:429 | NO | 0 | 52.4% | Best backtest but never fires live |
| TypeC (OverboughtFade) | TypeC | **CLASSIFICATION ONLY** | bridge.py:633 | config.toml:433 | NO | 0 | N/A | RSI>80 extremely rare |
| TypeD (SupportBounce) | TypeD | **CLASSIFICATION ONLY** | bridge.py:643 | config.toml:435 | NO | 0 | 24.1% | Net loser in backtest |
| TypeE (IBS) | TypeE | **CLASSIFICATION ONLY** | bridge.py:621 | config.toml:441 | YES | 3 | N/A | Only type observed |
| TypeF (OBVDivergence) | TypeF | **CLASSIFICATION ONLY** | bridge.py:617 | config.toml:445 | NO | 0 | N/A | vol_div<-0.5 |

**Key insight**: TypeA-F are CLASSIFICATION labels applied to signals in Stage 4 of bridge.py. They are NOT signal generators themselves. The 6 signal sources (VanguardSniper, Orchestrator, IBS, VolExp, ORB, GapFade) generate signals, then classify_entry_type() labels them. The Rust entry_engine.rs TypeA-F detectors are LIBRARY CODE not called at runtime.

---

## 8. Strategy Sync Matrix

| Strategy | In bridge.py? | In strategies.toml? | In config.toml? | In Rust? | In backtest? | In nightly? | In reporting? | Synced? |
|----------|--------------|---------------------|-----------------|----------|-------------|-------------|---------------|---------|
| VanguardSniper | YES (source 1) | NO | brain/config.py | NO | YES (backtest pipeline) | YES (per-strategy) | YES | OK |
| Orchestrator | YES (source 2) | YES (4 strategies) | NO | NO | NO | NO | NO | **PARTIAL** — strategies.toml not in nightly |
| IBS_MR | YES (source 3) | YES (ibs_mean_reversion) | NO | Rust has TypeE detector (unused) | NO | NO | NO | **PARTIAL** |
| VolExpansion | YES (source 4) | NO | NO | NO | NO | NO | NO | **MISSING** — no config, no tracking |
| ORB | YES (source 5) | NO | NO | NO | NO | NO | NO | **MISSING** — no config, no tracking |
| GapFade | YES (source 6) | YES (gap_fade) | NO | NO | NO | NO | NO | **PARTIAL** |

---

## 9. Live / Shadow / Disabled Boundary Register

| Component | Boundary | Current state | Notes |
|-----------|----------|--------------|-------|
| Rust engine | LIVE (paper mode) | Running, processing ticks | IS_LIVE=false hardcoded |
| Python bridge | LIVE | Running, generating signals | All signals currently vetoed |
| RiskArbiter | LIVE | 27 CHECKs active | Many CHECKs skipped in sim mode |
| Chandelier exits | LIVE | Rung tracking active | No time-stop despite config |
| Claude curator | **SHADOW** | Evaluates signals, logs only, never blocks | Fail-open if Claude unavailable |
| Gemini scanner | **DISABLED** (silently) | API key commented out in .env | Cron runs but fails |
| Ouroboros learning | **FROZEN** | observe_only=true | Waits for N=300 |
| config_writer | **FROZEN** | observe_only=true, exits early | dynamic_weights.toml stale from Mar 19 |
| Nightly pipeline | LIVE | Runs 04:50 UTC daily | Analysis-only mode |
| Telegram alerts | LIVE | Kill switch, heartbeat, reports | Working |
| Google Sheets sync | LIVE | Every 5 min via cron | Working |
| IBKR scanner | **CONFIGURED** | config says enabled=true | Unclear if actually producing results |
| Backfill simulator | LIVE | 07:00 UTC daily | Pre-market learning |
| Claude forensic review | LIVE | 04:53 UTC daily | Post-nightly analysis |

---

## 10. Model Role Matrix (Claude / Gemini)

| Role | Model | Claude? | Gemini? | Reads from | Writes to | Classification | Wired? | Operational? |
|------|-------|---------|---------|------------|-----------|---------------|--------|-------------|
| Signal challenge | Claude | YES | NO | Signal dict + market context | claude_curator.ndjson | Shadow only | YES (bridge.py Stage 4) | YES but non-blocking |
| Forensic review | Claude | YES | NO | WAL trades + vetoes | claude reviews JSON | Cold path | YES (04:53 cron) | YES |
| Morning briefing | Claude | YES | NO | Nightly output + market data | Telegram message | Cold path | YES (07:45 cron) | YES |
| Evening briefing | Claude | YES | NO | Day's trades + P&L | Telegram message | Cold path | YES (21:30 cron) | YES |
| Gate calibration | Claude | YES | NO | Rejected trades WAL | Telegram + JSON | Cold path (weekly) | YES (Friday 22:00) | YES |
| Psych audit | Claude | YES | NO | Session performance | Telegram | Cold path (weekly) | YES (Sunday 23:00) | Questionable value |
| Filing scanner | Claude | YES | NO | SEC/RNS feeds | Filing alerts | Shadow | YES (06:00 daily) | Unproven |
| Universe curation (core) | Gemini | NO | YES | Universe data + indicators | core_universe_latest.json | Cold path | YES (every 2h cron) | **BROKEN** — API key not in .env |
| Universe curation (brief) | Gemini | NO | YES | Pre-market analysis | gemini_scanner.log | Cold path | YES (06:00 daily) | **BROKEN** — same reason |
| Ticker selection | Neither | NO | NO | yfinance + contracts.toml | active_watchlist.json | Cold path | YES (every 2h) | YES (deterministic) |

**Failure behaviors**:
- Claude unavailable: fail-open with 10% conservative haircut on confidence
- Gemini unavailable: silently fails, ticker_selector still works (deterministic fallback)

---

## 11. Current Deployment State

| Component | Version/Commit | Location | Status |
|-----------|---------------|----------|--------|
| Rust binary | `d17d9bd` | /usr/local/bin/aegis | Running (PID 1 via tini) |
| Python bridge | `d17d9bd` | /app/python_brain/bridge.py | Running as subprocess |
| IB Gateway | gnzsnz/ib-gateway:stable | Container aegis-ib-gateway | Healthy, 4003 exposed |
| Redis | redis:7-alpine | Container aegis-redis | Healthy, AOF persistence |
| Config | `d17d9bd` | /app/config/ | Baked into Docker image |
| WAL | Schema v1 | /app/events/ (Docker volume) | 7 archive files + current |
| system_memory | Last updated 2026-03-23 04:50 UTC | /app/data/system_memory.json | PF=0.0 (bug, fix deployed) |

---

## 12. Current Pipeline and Scheduling State (35 Cron Jobs)

### Nightly Window (04:30-05:35 UTC)
| Time | Job | Status |
|------|-----|--------|
| 04:30 | Docker prune | Best-effort (no Docker socket in container) |
| 04:40 | Parquet orphan cleanup | Active |
| 04:45 | Log rotation (7-day retention) | Active |
| 04:50 | **Nightly pipeline** (flock'd) | Active — nightly_v6 → config_writer → etc. |
| 04:52 | FTT registry (Monday only) | Active |
| 04:53 | Claude forensic review (flock'd behind nightly) | Active |
| 04:54 | Fill quality report (flock'd) | Active |
| 04:55 | Post-trade diagnostics (flock'd) | Active |
| 05:00 | Bridge stderr log rotation | Active |
| 05:10 | Ouroboros monitor (TOML health check) | Active |
| 05:30 | Universe updater (Wikipedia scrape) | Active |
| 05:35 | Universe sync (universe.json → contracts.toml) | Active |

### Market Hours
| Time | Job | Status |
|------|-----|--------|
| Every 5 min | External monitor (deep health check) | Active |
| Every 5 min | Google Sheets sync | Active |
| Every 15 min (08-20) | Bridge health monitor | Active |
| Every 2h | ticker_selector (universe rotation) | Active |
| Every 2h (+5min) | Gemini core universe scan | **BROKEN** — no API key |
| Every 4h | Telegram heartbeat | Active |
| Every 6h | FX rate refresh (yfinance → fx_rates.toml) | Active |
| Every 6h | Contract expander | Active |

### Session Briefings (DST-aware dual-cron)
| Time (London) | Session | Status |
|---------------|---------|--------|
| 00:55 | Asian | Active |
| 06:00 | Gemini morning brief | **BROKEN** |
| 06:00 | ISA universe refresh | Active |
| 06:00 | Claude filing scanner | Active |
| 07:00 | Backfill simulator | Active |
| 07:45 | Claude morning briefing | Active |
| 07:55 | European | Active |
| 08:00 | Daily health report | Active |
| 14:25 | American | Active |
| 16:30 | US-only | Active |
| 21:15 | Daily sim trade report | Active |
| 21:20 | Cost drag report | Active |
| 21:30 | Claude evening briefing | Active |

### Weekly
| Time | Job | Status |
|------|-----|--------|
| Sunday 22:00 | IBKR full universe scanner | Active |
| Friday 22:00 | Claude gate calibration | Active |
| Sunday 23:00 | Claude psych audit | Active |

---

## 13. Artifact Flow Map

```
INBOUND DATA:
  IBKR Gateway → Rust engine (ticks)
  yfinance → ticker_selector (prices)
  Gemini API → gemini_scanner (curation) [BROKEN]
  Claude CLI → claude_curator (shadow verdicts)

INTERNAL FLOW:
  Rust engine → Python bridge (JSON stdin) → signal → Rust engine (JSON stdout)
  Rust engine → WAL (events/current.ndjson)
  WAL → nightly_v6 (analysis) → ouroboros_recommendations.json
  recommendations → config_writer → dynamic_weights.toml [FROZEN]
  ticker_selector → active_watchlist.json → Rust engine (file watch + resub)

OUTBOUND:
  Rust engine → Telegram (via WAL watcher + kill switch)
  Nightly → Telegram (reports, briefings)
  Nightly → Google Sheets (trade data)
  WAL → system_memory.json (cumulative stats)
```

---

## 14. Config Truth Map

| Config file | Location | Generated by | Read by | Hot-reload? |
|------------|----------|-------------|---------|-------------|
| config.toml | config/ | Manual | Rust config_loader | At startup only |
| config.live.toml | config/ | Manual | Rust config_loader (N8a overlay) | At startup only |
| contracts.toml | config/ | contract_expander + manual | Rust + Python | SIGHUP |
| dynamic_weights.toml | config/ | config_writer (FROZEN) | Rust + bridge.py | SIGHUP |
| strategies.toml | config/ | Manual | bridge.py → orchestrator | On bridge restart |
| active_watchlist.json | config/ | ticker_selector | Rust (file mtime watch) | Yes (file watch) |
| initial_universe.toml | config/ | ticker_selector | Rust config_loader | At startup only |
| fx_rates.toml | config/ | fx_refresh cron | Rust | SIGHUP |
| spread_cache.toml | config/ | config_writer | Rust | SIGHUP |

**STALE CONFIG**: dynamic_weights.toml generated 2026-03-19 with WR=79.2% from 20 trades. Reality: WR=35.4% from 48 trades. The confidence_floor=65 set in this file is vetoing all current signals.

---

## 15. Parameter Truth Map

| Parameter | config.toml value | dynamic_weights value | Runtime effective | Correct? |
|-----------|-------------------|----------------------|-------------------|----------|
| confidence_floor | 55 | 65 | **65** (dynamic overrides) | **WRONG** — too high for current strategies |
| max_positions | 999 (simulation section) | N/A | 999 | OK for paper |
| chandelier_atr_mult | 3.0 (default) | 3.05 | 3.05 | OK |
| spread_veto_pct | 0.3% | N/A | 0.3% | OK |
| kelly_fraction_cap | 0.5 | N/A | 0.5 | OK |
| daily_trade_limit | 999999 (simulation) | N/A | 999999 | OK for paper |
| system_velocity_max | 10 | N/A | 10 | OK |
| observe_only | true | N/A | true | Intentional (needs N=300) |
| exit_time_stop.enabled | true | N/A | **NOT IMPLEMENTED** | **BUG** |
| exit_time_stop.max_minutes_to_rung2 | 45 | N/A | **NOT IMPLEMENTED** | **BUG** |

---

## 16. Contradiction Register

| ID | Subsystem | Files | Docs say | Code says | Runtime says | Severity | Fix |
|----|-----------|-------|----------|-----------|-------------|----------|-----|
| C01 | Risk CHECKs | risk_arbiter.rs, MEMORY.md | "33 CHECKs" | **27 active CHECKs** (3,4 don't exist, 12 removed) | 27 | LOW | Update docs |
| C02 | TypeB signals | bridge.py, backtest | "52.4% WR, best strategy" | classify_entry_type needs 3-bar rising RVOL | 0 TypeB trades ever | HIGH | Sprint S4: investigate threshold |
| C03 | entry_engine.rs | entry_engine.rs, tasks/todo.md | "Dead code" / "Wire TypeA-F" | 786 LOC compiles, NOT called at runtime | Never executes | MEDIUM | Add quarantine comment |
| C04 | Time-stop | config.toml | `exit_time_stop.enabled = true` | exit_engine.rs has NO time-stop code | Positions held until EOD or Chandelier stop | **CRITICAL** | Sprint S3: implement |
| C05 | Confidence floor | config.toml, dynamic_weights | config=55, dynamic=65 | dynamic overrides config | ALL non-LSE signals vetoed at conf=49 | **CRITICAL** | Immediate: lower dynamic floor |
| C06 | Gemini | .env, entrypoint.sh | "API key SET" (manual says) | **COMMENTED OUT in .env** | Gemini crons fail silently | HIGH | Uncomment API key |
| C07 | Active tickers | config, logs | "50 tickers loaded" | 50 attempted, tick-by-tick limit hit | Only 11 receiving data | HIGH | Reduce subscription count |
| C08 | PF calculation | persistent_memory.py | "PF should be computed" | Never computed (was 0.0) | Fixed this session | CLOSED | Deployed |
| C09 | Paper fills | master plan v1 | "Possible mid-point fills" | Fills at ask/bid (realistic) | Confirmed ask entry, bid exit | CLOSED | Documented |
| C10 | dynamic_weights | dynamic_weights.toml | "Generated nightly" | observe_only=true → exits early | **STALE since Mar 19** | HIGH | Unfreeze after collecting more data |

---

## 17. Dead Code / Dead Path Register

| Item | Location | LOC | Status | Why dead | Action |
|------|----------|-----|--------|----------|--------|
| entry_engine.rs TypeA-F detectors | rust_core/src/entry_engine.rs | 787 | Compiles, NEVER called | Python bridge handles all classification | Add quarantine comment |
| rotation_scanner.rs | rust_core/src/rotation_scanner.rs | ~200 | Instantiated, never called | HotScanner does apex ticks; RotationScanner idle | Leave (may wire later) |
| PaperBroker for sim fills | rust_core/src/paper_broker.rs | 453 | Used for tests only | simulation_mode bypasses broker entirely | Keep for tests |
| Docker prune cron | crontab line 30 | 1 | Runs but no-op | No Docker socket inside container | Remove or fix |
| Gemini scanner crons | crontab | 3 | Run but fail silently | GEMINI_API_KEY commented out | Fix .env or disable crons |

---

## 18. Stale Docs / Stale Plan Register

| Document | Location | Status | Why stale |
|----------|----------|--------|-----------|
| MERGED_MASTER_PLAN_v1.0 | docs/archive/ | SUPERSEDED | Replaced by this plan |
| PLAN_1_ENGINE_FIX_AND_MULTI_EXCHANGE.md | project root | SUPERSEDED | Sprints 0-10 completed, new sprints here |
| PLAN_2_CLAUDE_INTEGRATION.md | project root | PARTIALLY VALID | Claude integration done, some items remain |
| AEGIS_V2_CANONICAL_MASTER_PLAN_20260324.md | project root | SUPERSEDED | This v2 plan replaces it |
| 108 docs in docs/archive/ | docs/archive/ | ARCHIVED | Historical context only |
| tasks/todo.md | project root | STALE | References old contradictions, mostly resolved |
| dynamic_weights.toml | config/ | STALE | Generated Mar 19, WR=79.2% vs reality 35.4% |

---

## 19. Risk / Safety Control Map

| Control | Implementation | Active? | Bypassed in sim? |
|---------|---------------|---------|-------------------|
| IS_LIVE=false hardcoded | main.rs | YES | N/A |
| 27-CHECK risk arbiter | risk_arbiter.rs | YES | 8 CHECKs skipped in sim (6,14,15,16,17,18,30,31,32) |
| Chandelier 5-rung stop | exit_engine.rs | YES | No |
| EOD flatten (16:25 London) | exit_engine.rs | YES | No |
| Kill switch (KILL/PAUSE files) | main.rs + Telegram | YES | No |
| Consecutive loss breaker | risk_arbiter.rs CHECK 21 | YES | No |
| Daily drawdown halt | risk_arbiter.rs CHECK 18 | YES | Skipped in sim |
| Zombie halt recovery | risk_arbiter.rs | YES (30-min timeout) | No |
| Bridge watchdog | entrypoint.sh + bridge_watchdog.py | YES | No |
| IBKR circuit breaker | broker_circuit_breaker in engine | YES | No |
| Ouroboros freeze | config.toml observe_only | YES | N/A |
| Claude shadow-only | claude_curator.py | YES | N/A |

---

## 20. Pre-Live Blockers (MUST fix before real capital)

| # | Blocker | Why | Sprint | Effort |
|---|---------|-----|--------|--------|
| 1 | IS_LIVE=false hardcoded | Cannot trade live | Rust change | 5 min |
| 2 | 8 paper overrides in simulation section | max_positions=999, heat=100%, etc. | Config revert via config.live.toml | 15 min |
| 3 | **No time-stop** despite config saying enabled | Capital locked in sideways positions | Sprint S3 | 2h |
| 4 | WR 35.4% (need 40%) | Below validation gate | More trades + strategy improvement | Weeks |
| 5 | PF ~0.77 (need 1.3) | Below validation gate | Strategy improvement | Weeks |
| 6 | 14 consecutive losses (need <8) | Below validation gate | Better entry filtering | Weeks |
| 7 | Only 1 of 6 strategies proven | Insufficient diversification | Run 300+ trades | Weeks |
| 8 | Ouroboros frozen on N=48 | ML loop needs N=300 | Wait for trades | Weeks |
| 9 | EC2 4GB RAM | OOM risk under stress | Sprint S5: upgrade | 15 min |
| 10 | Disk at 80% | Cannot rebuild Rust | Prune + expand | 15 min |
| 11 | Zero slippage simulation | P&L optimistic | Add slippage_bps config | 1h |

---

## 21. Post-Live Non-Blockers (Safe to do after live)

| Item | Why post-live | Priority |
|------|-------------|----------|
| Claude curator → veto authority | Needs validation data first | Medium |
| Gemini → signal-level integration | Currently universe-only | Low |
| Advanced regime detection (VPIN hot-path) | Marginal improvement | Low |
| Rust entry_engine.rs wire-up | Python classification works fine | Low |
| Multi-account ISA/GIA split | Single account first | Low |
| Advanced order types (iceberg, TWAP) | Simple limit orders fine initially | Low |

---

## 22. Daily Operating Workflow

| Time (UTC) | Action | Command/Check |
|------------|--------|--------------|
| 07:00 | Check containers healthy | `ssh EC2 'docker ps'` |
| 07:00 | Check overnight errors | `docker logs aegis-v2 --tail 30` |
| 07:00 | 2FA if Monday | IBKR mobile app |
| 07:45 | Read Claude morning briefing | Telegram |
| 08:00 | LSE open: verify tick flow | `grep SIGNAL_ARRIVED` in logs |
| 08:00 | Read daily health report | Telegram |
| 14:30 | US open: check for ORB/momentum | Logs |
| 16:25 | LSE close: verify EOD flatten | Check WAL for PositionClosed |
| 21:00 | Read daily sim trade report | Telegram |
| 21:15 | Read cost drag report | Telegram |
| 21:30 | Read Claude evening briefing | Telegram |

---

## 23. Intraday Operating Workflow

| Event | Action | Automated? |
|-------|--------|-----------|
| Signal arrives | Bridge generates, RiskArbiter evaluates | YES |
| Signal vetoed | Logged to WAL + telemetry | YES |
| Signal approved | SimulatedTrade created, position tracked | YES |
| Rung advance | Chandelier stop ratcheted up | YES |
| EOD flatten | All positions closed at 16:25 London | YES |
| Bridge crash | Watchdog detects (30s), restarts | YES |
| IBKR disconnect | Engine reconnects (10 attempts, backoff) | YES |
| Kill switch triggered | Positions flattened, engine halts | YES (via Telegram /kill) |

---

## 24. End-of-Day / LSE Close Workflow

| Time (London) | Event | Owner |
|---------------|-------|-------|
| 16:25 | EOD flatten trigger fires | Rust exit_engine |
| 16:25 | All open positions get MarketSell exit | Rust engine |
| 16:30 | LSE closing auction — no new entries | Rust blackout window |
| 16:30 | US-only session briefing sent | Telegram cron |
| 17:00 | Dark mode begins (no new entries) | Rust engine |
| 20:00 | ModeC (US-session) may allow entries | Rust engine |
| 21:00 | US close — final exits | Rust engine |
| 21:15 | Daily sim trade report | Python cron |

---

## 25. Nightly Workflow

See Section 12 for complete schedule. Key sequence:
1. **04:50** — nightly_v6.py: load WAL trades → compute metrics → recommendations → update persistent_memory
2. **04:51** — config_writer: reads recommendations → generates dynamic_weights.toml (FROZEN — observe_only)
3. **04:53** — Claude forensic review: classifies trades + vetoes
4. **04:54** — Fill quality report: paper→live slippage estimate
5. **04:55** — Post-trade diagnostics: consolidated analysis
6. **05:10** — Ouroboros monitor: TOML health + staleness check

---

## 26. Recovery / Restart / Rollback Workflow

### Kill Switch
```bash
ssh EC2 'docker exec aegis-v2 touch /app/KILL'      # Halt trading
ssh EC2 'docker exec aegis-v2 touch /app/PAUSE'     # Flatten + halt
ssh EC2 'cd ~/nzt48-aegis-v2 && docker compose down' # Full stop
```

### Restart
```bash
ssh EC2 'docker exec aegis-v2 rm -f /app/KILL /app/PAUSE'
ssh EC2 'cd ~/nzt48-aegis-v2 && docker compose up -d'
```

### Rollback to Previous Commit
```bash
git revert HEAD && git push
rsync ... && ssh EC2 'docker compose build aegis-v2 && docker compose up -d && docker system prune -f'
```

### Full Rebuild (Python changes)
```bash
git push && rsync ... && ssh EC2 'docker system prune -f && docker compose build aegis-v2 && docker compose up -d'
```

### Full Rebuild (Rust changes — needs >5GB free)
```bash
ssh EC2 'docker system prune -af'  # Aggressive prune
rsync ... && ssh EC2 'docker compose build --no-cache aegis-v2 && docker compose up -d'
```

---

## 27. Validation and Readiness Gates

### 100-Trade Rolling-Window Gates

| Gate | Threshold | Current (N=48) | Status |
|------|-----------|----------------|--------|
| Win Rate | >= 40% | 35.4% | **FAILING** |
| Profit Factor | >= 1.3 | ~0.77 | **FAILING** |
| Max Consecutive Losses | < 8 | 14 | **FAILING** |
| Chandelier Rung >= 2 | >= 50% of exits | Unknown | **UNMEASURED** |
| Max Daily Drawdown | < 3% | OK | PASSING |
| Strategy Diversity | >= 2 strategies with WR > 35% | 1 only | **FAILING** |

### Pre-Validation Immediate Fixes Needed
1. **CRITICAL**: Lower confidence_floor in dynamic_weights to allow signals through
2. **CRITICAL**: Fix IBKR tick-by-tick subscription limit (reduce from 102 L1 + 200 mkt)
3. **HIGH**: Uncomment GEMINI_API_KEY in .env for universe curation
4. **HIGH**: Implement time-stop (config says enabled but code doesn't have it)

---

## 28. ROI-Ranked Action Backlog

| Priority | Action | ROI | Sprint |
|----------|--------|-----|--------|
| **CRITICAL** | Lower confidence_floor to unblock signals | System is dead without this | S-HOTFIX |
| **CRITICAL** | Fix IBKR subscription limit (only 11 of 50 tickers get data) | System starved of data | S-HOTFIX |
| **CRITICAL** | Implement time-stop in exit_engine | Capital efficiency | S3 |
| HIGH | Uncomment GEMINI_API_KEY in .env | Universe curation broken | S-HOTFIX |
| HIGH | Investigate TypeB threshold mismatch | Best backtest strategy never fires | S4 |
| HIGH | Upgrade EC2 to 8GB RAM | OOM protection | S5 |
| MEDIUM | Create config.live.toml with safe production values | Deployment safety | S6 |
| MEDIUM | Add slippage simulation to paper fills | P&L realism | S7 |
| LOW | Quarantine entry_engine.rs dead code | Code clarity | S3 (bundle) |
| LOW | Verify Gemini scanner produces valid output | Gemini integration | S8 |

---

## 29. Chunked Implementation Sprints

### Sprint S-HOTFIX: Unblock Signal Flow (30 min, CRITICAL)
- **Priority**: CRITICAL
- **Classification**: Fix Now
- **Why**: System is generating signals but ALL are vetoed. Zero trades being made. The paper trading validation gate cannot advance.
- **Files**: `config/dynamic_weights.toml`, `.env`
- **Changes**:
  1. Set `confidence_floor = 45` in dynamic_weights.toml (was 65, killing everything)
  2. Uncomment `GEMINI_API_KEY` in `.env`
  3. Reduce L1 subscription attempts (or accept graceful degradation)
- **Deploy**: rsync → docker compose build → up -d
- **Success criteria**: Signals no longer all vetoed. At least 1 trade per day.
- **Rollback**: Revert dynamic_weights.toml to previous values

### Sprint S3: Time-Stop Implementation (2 hours)
- **Priority**: Critical
- **Classification**: Build Now
- **Why**: Config says `exit_time_stop.enabled=true, max_minutes_to_rung2=45` but exit_engine.rs has no time-stop code. Capital locks in sideways positions until EOD.
- **Files**: `rust_core/src/exit_engine.rs`, `rust_core/src/config_loader.rs`
- **Changes**: Add time-stop logic: if position held > max_minutes and not at rung 2+, tighten trail to aggressive_trail_atr
- **Blocker**: Needs >5GB free disk for Rust rebuild. EC2 at 80% — prune first.
- **Deploy**: docker system prune -af → build --no-cache → up -d
- **Success criteria**: Positions that don't reach rung 2 within 45 min get aggressive trailing stop
- **Rollback**: Set exit_time_stop.enabled=false in config.toml
- **Pre-live**: MANDATORY

### Sprint S4: TypeB Investigation (1 hour)
- **Priority**: High
- **Classification**: Build Now
- **Why**: TypeB has 52.4% WR in backtest but 0 production signals. Best theoretical strategy is silent.
- **Files**: `python_brain/bridge.py:624-630` (classify_entry_type)
- **Hypothesis**: 3-bar rising RVOL is too strict on 5-min bars
- **Test**: Add gate veto logging for TypeB threshold checks
- **Deploy**: rsync → build → up -d
- **Success criteria**: TypeB either fires or has documented reason why conditions never met
- **Rollback**: N/A (logging only)

### Sprint S5: EC2 Instance Upgrade (15 min)
- **Priority**: High
- **Classification**: Build Now
- **Why**: 4GB RAM insufficient under market stress. m7i-flex.large (8GB) is free-tier.
- **Method**: AWS console → Stop instance → Change type → Start → 2FA re-auth
- **Risk**: 5 min downtime
- **Success criteria**: `free -m` shows 8GB
- **Rollback**: Change back to c7i-flex.large

### Sprint S6: config.live.toml (15 min)
- **Priority**: Medium
- **Classification**: Build Now
- **Why**: 8 paper overrides would be catastrophic in live mode
- **Files**: `config/config.live.toml` (already exists with N8a overlay)
- **Success criteria**: Live overlay verified with safe values
- **Pre-live**: MANDATORY

---

## 30. Deployment Sequence

For any change:
1. `git add` specific files → `git commit -m "..."` → `git push`
2. `rsync -avz --exclude='.git' --exclude='target' --exclude='data' -e "ssh -i key" local/ EC2:/path/`
3. If Python-only: `docker compose build aegis-v2 && docker compose up -d`
4. If Rust change: `docker system prune -f` first, then build
5. Verify: `docker ps`, `docker logs aegis-v2 --tail 30`, check for errors

---

## 31. Verification Sequence

After every deploy:
1. `docker ps` — all 3 containers healthy
2. `docker logs aegis-v2 --tail 30` — no errors
3. Check for SIGNAL_ARRIVED in logs — bridge generating signals
4. Check for VETO vs SIM_TRADE — signals getting through
5. `df -h /` — disk not >90%
6. Check Telegram heartbeat arrives on schedule

---

## 32. Success Criteria

| Milestone | Metric | Target | Current |
|-----------|--------|--------|---------|
| Signal flow unblocked | Signals not all vetoed | >0 trades/day | 0 (all vetoed) |
| 100 trades reached | Cumulative trade count | 100 | 48 |
| Validation gate WR | Win rate | >= 40% | 35.4% |
| Validation gate PF | Profit factor | >= 1.3 | ~0.77 |
| Validation gate losses | Max consecutive | < 8 | 14 |
| Strategy diversity | Strategies with trades | >= 2 | 1 |
| Live readiness | All pre-live blockers resolved | 0 blockers | 11 blockers |

---

## 33. Failure / Escalation Rules

| Condition | Action | Authority |
|-----------|--------|-----------|
| Daily drawdown > 3% | Automatic FLATTEN | Rust risk_arbiter |
| 3 consecutive stop losses | Automatic HALT | Rust risk_arbiter |
| Bridge crash (no heartbeat 30s) | Auto-restart | bridge_watchdog |
| IBKR disconnect > 60s | Auto-reconnect with backoff | Rust engine |
| IBKR disconnect > 10min | HALT mode | Rust engine |
| Disk > 90% | ALERT operator (Telegram) | external_monitor |
| Kill switch triggered | Flatten all, halt | Telegram → kill_switch.py |
| Equity < 70% of initial | Automatic HALT (CHECK 32) | Rust risk_arbiter |

---

## 34. Final Canonical Plan Verdict

**The system is a paper-trading prototype with institutional-grade risk infrastructure in Rust, but it is currently INOPERABLE due to a misconfigured confidence floor that vetoes all signals.** The immediate priority is Sprint S-HOTFIX to lower the confidence_floor in dynamic_weights.toml from 65 to 45 and allow the system to actually trade.

Beyond the hotfix, the system needs:
1. Time-stop implementation (config says enabled, code doesn't have it)
2. TypeB investigation (best theoretical strategy never fires)
3. EC2 upgrade (4GB → 8GB, disk expansion)
4. 300+ trades for Ouroboros unfreeze and statistical validity

The system is 4-8 weeks from live readiness at current signal rates — but signal rates will increase dramatically once the confidence floor is fixed.

---

## 35. Stop-State Handoff (Session 2, Plan v2)

**This session delivered**:
1. Sprint S1 COMPLETED: Paper fills confirmed realistic (ask/bid, not mid-point)
2. Sprint S2 COMPLETED: PF=0.0 bug fixed in persistent_memory.py
3. Plan v2 written: 35 sections, evidence-bound, brutal-truth
4. CRITICAL DISCOVERY: All signals being vetoed by stale dynamic_weights.toml confidence_floor=65
5. CRITICAL DISCOVERY: Only 11 of 50 tickers receiving data (IBKR tick-by-tick limit)
6. CRITICAL DISCOVERY: No time-stop despite config saying enabled
7. CRITICAL DISCOVERY: Gemini API key commented out in .env

**Exact next actions (in order)**:
1. **Sprint S-HOTFIX** — Lower confidence_floor, uncomment GEMINI_API_KEY, deploy
2. **Sprint S3** — Implement time-stop in exit_engine.rs (needs disk prune first)
3. **Sprint S4** — Investigate TypeB threshold mismatch
4. **Sprint S5** — Upgrade EC2 to 8GB
5. Continue paper trading → 100 trades → evaluate validation gates

**Files changed this session**:
- `python_brain/ouroboros/persistent_memory.py` — PF fix
- `AEGIS_V2_CANONICAL_MASTER_PLAN_20260324.md` — expanded (now superseded by this v2)
- `AEGIS_V2_CANONICAL_MASTER_PLAN_v2_20260324.md` — THIS FILE (new canonical plan)

**Files to read on next resume**:
- This plan: `AEGIS_V2_CANONICAL_MASTER_PLAN_v2_20260324.md`
- Exit engine: `rust_core/src/exit_engine.rs` (for S3)
- Bridge: `python_brain/bridge.py:624-630` (for S4 TypeB)
- Dynamic weights: `config/dynamic_weights.toml` (for S-HOTFIX)
- .env: `.env` (for GEMINI_API_KEY fix)
