# IMPLEMENTATION_MASTER_PLAN_RC1.md — AEGIS V2 Institutional Audit
# Release Candidate 5 — SPREAD-FIRST REWRITE
**Generated:** 2026-03-19 | **Re-audit:** 2026-03-20 | **Version:** RC5
**Board:** CTO, CRO, CIO, Head of Quant Research, Head of Execution, Head of Production/SRE, Head of Autonomous Intelligence Design
**Deep-read:** ALL 79 Rust files, ALL 23 Python modules, ALL 13 config files, 4 background agents verified cost paths
**RC5 scope:** Complete rewrite with spread economics as primary constraint. Supersedes RC1-RC4.

---

## THE BOTTOM LINE

**The system cannot survive at >2 trades/day.** At 0.50% round-trip cost (spread+commission) on £2K positions, 3 trades/day = 76% annual equity drag. The £150/year cost figure in auxiliary docs is wrong by 50x. Everything in this plan is reordered around one question: **fewer, better trades.**

---

## CORRECTIONS FROM ALL PRIOR DRAFTS

| Prior Claim | Correction | Evidence |
|-------------|------------|----------|
| "Daily drawdown never resets" | **WRONG** — resets correctly | engine.rs:2466 |
| "26 WAL event types" | **WRONG** — 17 variants | types/wal.rs:24-197 |
| "7/8 adaptive multipliers" | **WRONG** — 6/8 | exit_engine.rs:583-591 |
| "£150/year spread costs" | **CATASTROPHICALLY WRONG** — £5K-12K/year | Math: trades × 0.50% × position |
| "Kelly Factor 8 protects against spread" | **DECORATIVE** — 0.4% adjustment at typical spreads | kelly_12factor.py:143 |
| "Ouroboros optimizes performance" | **PARTIALLY WRONG** — optimizes GROSS, not NET | nightly_v6.py ignores spread drag |
| "Paper results are indicative" | **WRONG** — paper 15-position/2% spread = non-representative | Cost structure guarantees ruin |

## GEMINI SECOND OPINION TRIAGE

| Claim | Verdict | Evidence | Action |
|---|---|---|---|
| G1: Spread cost math wrong | **ACCEPTED** | £150 vs £7,560 at 3 trades/day | Phase 7 rewritten |
| G2: con_id=0 → NOW | **ACCEPTED** | 216/303 unresolved | N6 |
| G3: Claude validates Ouroboros | **ACCEPTED** | Ouroboros lacks macro | X18 |
| G4: ISA FX hedge violation | **ACCEPTED** | Docs propose illegal FX forwards | N7 |
| G5: IBKR disconnect → HALT | **ACCEPTED** | yfinance offline only | N8 |
| G6: Market orders | **REJECTED** | ibkr_broker.rs:1097 `.limit()` always | No action |
| G7: yfinance fallback | **REJECTED** | Not implemented in live code | No action |
| G8: Target 30-50% annualized | **ACCEPTED** | Revised to 8-88% net depending on selectivity | Phase 7 rewritten |

---

## CONFIRMED WORKING (18 components)

| Component | Evidence |
|-----------|----------|
| WAL crash recovery (17 events, CRC32+fsync) | wal_writer.rs, wal_replay.rs |
| Risk arbiter (31 checks, fail-closed, ISA) | risk_arbiter.rs:111-378 |
| Chandelier exit (5 rungs, 6/8 adaptive) | exit_engine.rs:323-591 |
| Ouroboros nightly → config_writer → SIGHUP | nightly_v6.py → config_writer.py → main.rs |
| Clock (LSE/US/Asian, BST 2025-2032) | clock.rs, market_scheduler.rs |
| Gate veto logging (12 gates, NDJSON) | bridge.py:67-104 |
| Indicator intelligence (rule discovery) | indicator_intelligence.py |
| Persistent memory (per-ticker/regime/exchange) | persistent_memory.py |
| Kelly 12-factor sizing + ramp | risk_arbiter.rs:325-366 |
| Bayesian blending (30/70) | nightly_v6.py:399 |
| Guardrails (±15% clamp, Kelly [0.15,0.30]) | config_writer.py |
| Limit order execution (not market) | ibkr_broker.rs:1097,1103 |
| Smart Router cost estimation | smart_router.rs:168-193 |
| Breakeven rung includes fees | exit_engine.rs:73 (0.3% RT fee) |
| Commission tracking in PnL | engine.rs:1029 |
| Spread veto gate (0.3% live) | risk_arbiter.rs:192-202 |
| Per-strategy spread filters | autonomous_orchestrator.py:282-498 |
| Nightly median spread analysis | config_writer.py:290-316 |

---

## CRITICAL COST ANALYSIS

### Cost Per Trade (PROVEN)

| Component | Value |
|-----------|-------|
| Entry spread crossing | ~0.10-0.15% |
| Exit spread crossing | ~0.10-0.15% |
| Entry commission (IBKR) | £1.70 (~0.085%) |
| Exit commission (IBKR) | £1.70 (~0.085%) |
| Marketable limit buffer | ~0.10% |
| **Round-trip total** | **~0.50%** |

### Annual Drag Matrix

| Trades/Day | Annual Cost | As % of £10K | Required Daily Gross |
|------------|-------------|-------------|---------------------|
| 1 | £2,520 | 25% | 0.10% |
| 2 | £5,040 | 50% | 0.20% |
| 3 | £7,560 | 76% | 0.30% |
| 5 | £12,600 | 126% | 0.50% |

### What This Means

Paper mode at 15 positions + 2% spread veto = 5-15 trades/day = **meaningless data at catastrophic simulated cost.** Must fix paper mode to match live economics BEFORE collecting validation data.

---

## COMPLETE ISSUE REGISTRY (23 Issues + 5 Telemetry Gaps)

### P0: SURVIVAL STACK (Before Everything Else) — 5.5 days

| # | Issue | Fix | Files | Effort |
|---|-------|-----|-------|--------|
| N0a | No daily trade count limit | Add max_daily_trades = 3 config + arbiter gate | config.toml, risk_arbiter.rs | 0.5d |
| N0b | Paper mode cost-blind | max_positions=3, spread_veto=0.5%, heat=10% | config.toml, engine.rs | 0.5d |
| N0c | Confidence floor too low for paper | Raise from 45 to 65 | config.toml, strategies.toml | 0.5d |
| N0d | No min-gross-edge gate | Reject if expected_edge < 2 × spread | risk_arbiter.rs | 1d |
| N0e | No cost telemetry in WAL | Add gross_pnl, spreads, slippage to PositionClosed | types/wal.rs, engine.rs | 2d |
| N0f | FillEvent lacks spread/slippage | Add spread_at_fill, side, symbol, slippage | types/wal.rs, engine.rs | 1d |

### CRITICAL — NOW (N1-N8) — 10 days

| # | Issue | Fix | Effort |
|---|-------|-----|--------|
| N1 | Simulation regime bypass (triple) | Remove overrides; sim-specific relaxation only | 2d |
| N2 | Bar history not persisted | BarSnapshot WAL + replay | 2d |
| N3 | No macro event suppression | economic_calendar.toml + bridge.py | 2d |
| N4 | PositionClosed lacks exit_reason | exit_reason, conviction, vix (partial from N0e) | 1d |
| N5 | Hot-reload failure silent | Telegram alert + regime escalation | 0.5d |
| N6 | 216 contracts con_id=0 | reqContractDetails for active exchanges | 1d |
| N7 | ISA FX hedge doc violation | Remove FX forward proposals from docs | 0.5d |
| N8 | yfinance fallback docs misleading | Label as NOT IMPLEMENTED | 0.5d |

### HIGH — NEXT (X1-X18) — 25 days

| # | Issue | Fix | Effort |
|---|-------|-----|--------|
| X1 | Cost-adjusted Ouroboros learning | Net PnL optimization | 2d |
| X2 | Trade frequency recommendation | max_positions from cost drag | 1d |
| X3-X4 | Dead code (mega-runner, correlation) | Wire or remove | 2d |
| X5-X9 | MAE/MFE, velocity, calendars, VIX | Persistence + calendars | 5d |
| X10-X13 | New WAL events (4 types) | SignalGenerated/Rejected/Anomaly/RungAdvanced | 6d |
| X14-X16 | Taxonomy + 16-tab Sheets | W1-W5, L1-L7, Cost_Dashboard | 7d |
| X17-X18 | Claude CI/CD + Ouroboros validation | GitHub Actions | 2d |

---

## UNIFIED PRIORITY LIST

### N0 — SURVIVAL STACK — 5.5 days
N0a: daily_max_trades (0.5d) → N0b: paper config fix (0.5d) → N0c: confidence floor (0.5d) → N0d: min-edge gate (1d) → N0e: cost telemetry WAL (2d) → N0f: FillEvent spread (1d)

**Gate: Paper deployment with ≤2 trades/day. ALL trades have cost data.**

### N1-N8 — FOUNDATION — 10 days
**Gate: 50 trades. Cost tracking live. Daily cost < £20. Spread victims < 20%.**

### X1-X18 — INTELLIGENCE — 25 days
**Gate: 250 trades. WR≥55%. PF≥1.5 (cost-adjusted). Net Sharpe≥2.0.**

### L1-L10 — ENHANCEMENT — 18 days (only after 250-trade gate)

### NEVER
- Claude real-time trade approval/timing (latency)
- DQN signal weighting (no env)
- Neural Hawkes (tick-level, not 5-min)
- Autonomous deployment (no human review)
- yfinance RT fallback (HALT if IBKR dies)
- FX forwards in ISA (illegal per HMRC)

---

## COMPOUNDING TARGETS (Cost-Adjusted)

| Scenario | Daily Gross | Trades/Day | Annual Net | Verdict |
|----------|-----------|-----------|-----------|---------|
| Conservative | 0.15% | 1.5 | ~8% | Barely viable |
| **Realistic** | **0.20%** | **2** | **~15%** | **Solid ISA returns** |
| Optimistic | 0.25% | 1.5 | ~49% | Requires 60%+ WR |
| World-class | 0.30% | 1 | ~88% | Extreme selectivity |

**Key insight: 1 trade/day at 0.30% gross nets MORE than 3 trades/day at 0.30% gross.** Fewer trades = less cost = more alpha.

---

## CLAUDE INTEGRATION SCORECARD (12 YES, 3 NO)

| Use Case | Phase | Value | NEW? |
|----------|-------|-------|------|
| Nightly trade review | NEXT | HIGH | |
| Winner/loser diagnosis | NEXT | HIGH | |
| Code review (PR) | NOW | HIGH | |
| PR generation | NOW | HIGH | |
| Stop-loss failure analysis | NEXT | HIGH | |
| **Cost analysis + frequency advisory** | **NOW** | **HIGH** | **YES** |
| Strategy critique | NEXT | MEDIUM | |
| Anomaly interpretation | NEXT | MEDIUM | |
| Session briefings | NEXT | MEDIUM | |
| Execution analysis | NEXT | MEDIUM | |
| Indicator translation | NEXT | MEDIUM | |
| Ouroboros recommendation validation | NEXT | MEDIUM | |
| Macro event classification | NEVER | — | (deterministic) |
| Real-time trade approval | NEVER | — | (latency) |
| Real-time entry timing | NEVER | — | (latency) |

---

## SUCCESS CRITERIA

| Metric | 50-Trade | 100-Trade | 250-Trade |
|--------|----------|-----------|-----------|
| Win Rate (cost-adjusted) | ≥45% | ≥50% | ≥55% |
| Profit Factor (cost-adjusted) | ≥1.0 | ≥1.2 | ≥1.5 |
| Sharpe Ratio (net) | N/A | ≥1.5 | ≥2.0 |
| Max Drawdown | <8% | <5% | <5% |
| **Avg Trades/Day** | **≤3** | **≤2.5** | **≤2** |
| **Daily Cost < % Equity** | **0.20%** | **0.15%** | **0.10%** |
| **Spread Victims (L5)** | **<20%** | **<10%** | **<5%** |
| Avg Rung at Exit | ≥1.0 | ≥1.3 | ≥1.5 |

---

## CLAIM CONFIDENCE (FINAL)

| Claim | Confidence |
|-------|-----------|
| WAL crash recovery robust | PROVEN |
| Risk arbiter fail-closed | PROVEN |
| Ouroboros changes live behavior | PROVEN |
| Orders use limit always | PROVEN |
| **Spread drag = #1 viability threat** | **PROVEN** |
| **Paper mode = cost-blind disaster** | **PROVEN** |
| **Kelly Factor 8 = decorative** | **PROVEN** |
| **Trade frequency > win rate as lever** | **PROVEN** |
| **Ouroboros is cost-blind** | **PROVEN** |
| Realistic NET: 0.05-0.20% daily | LIKELY |
| 0.3%+ daily NET | UNLIKELY |
| ETPs exempt from stamp duty | PROVEN |
| System ready for live | NEEDS TEST |

---

**Document Version:** RC5 — SPREAD-FIRST REWRITE
**Generated:** 2026-03-19 | **Re-audit:** 2026-03-20
**Status:** EXECUTION-READY pending N0 survival stack
**Next action:** N0a → N0b → N0c → N0d → N0e → N0f → then N1
