# AEGIS V2 Syndicate Triage — 2026-03-24
**Reviewers**: Gemini 2.5 Pro, ChatGPT o3
**Triaged by**: Claude Opus 4.6
**System state at triage**: 2 trades today (STAN.L, AI), ~66 lifetime trades, 35.4% WR, -6.79 P&L, 10k paper

---

## Ground Truth (what actually exists in code RIGHT NOW)

Before triaging, here is what is implemented and deployed. Any suggestion that assumes
these are missing is automatically ALREADY DONE.

| Feature | Status | Evidence |
|---------|--------|----------|
| Time-stop | DEPLOYED (45min, 0.3x ATR) | `exit_engine.rs:216-233`, `config_loader.rs:297` |
| Chandelier 5-rung ladder | DEPLOYED | `exit_engine.rs:46-88`, 5 rung thresholds + 3 ATR trail levels |
| Board lot sizing (TSE/HKEX/SGX=100) | DEPLOYED | `broker.rs:186`, `engine.rs:1889-1895` (LOT_SKIP) |
| L1 data quality gate | DEPLOYED (sim bypass) | `engine.rs:1775` (sim mode skip), `ibkr_broker.rs:l1_subscribed_set` |
| Unhalt grace period | DEPLOYED | `engine.rs:986-989` (active_trading_ticks reset) |
| EC2 live config (c7i.large) | DEPLOYED | `terraform/variables.live.tfvars` (294 bytes) |
| Spoof detector calibration | DEPLOYED | `quote_imbalance.rs` (25x multiplier + 2% floor) |
| PF cumulative tracking | DEPLOYED | `persistent_memory.py` (gross_wins/gross_losses) |
| Strategy registry | DEPLOYED | `config/strategy_registry.json` (230 lines, 11 strategies) |
| Kelly ramp (0->250 trades) | DEPLOYED | `engine.rs:1789` (smooth 10%-100% ramp) |
| Regime detector (Hurst + JumpDiffusion) | DEPLOYED | `regime_detector.rs` (170 LOC), `strategy_config.rs` |
| TypeA-F classification | DEPLOYED in Python | `bridge.py` Stage 4 classifier |
| TypeA/D disabled in registry | DEPLOYED | `strategy_registry.json` status=disabled |
| Ouroboros FROZEN | DEPLOYED | `config.toml` observe_only=true |
| 33 risk CHECKs | DEPLOYED | `risk_arbiter.rs` deterministic |

---

## GEMINI FEEDBACK TRIAGE

### G1: Dual-data pipeline (Polygon + IBKR) + Lambda cron jobs

**REJECT**

Reasoning:
- We have a 10k paper account with 2 trades today. We do not need 5000+ tickers via Polygon REST.
  The bottleneck is not discovery. The bottleneck is that our existing 6 strategies produce almost
  no signals (5 of 6 have zero trades).
- Polygon costs money. We are on a paper account. Adding a paid data vendor to a system that
  cannot yet prove it can trade profitably is burning cash to solve a problem we do not have.
- "Move 32 cron jobs to Lambda" is a massive infrastructure rewrite. We have 3 cron jobs
  (nightly, sim report, ticker rotation). There are not 32. This is hallucinated.
- The L1 gate we just built already separates discovery (MktData snapshot) from execution
  (L1 tick-by-tick). The architecture Gemini proposes is a more expensive version of what we
  already have.
- Correct later: Polygon as a discovery layer makes sense at scale (200+ trades, live capital,
  multiple strategies proven). Not now.

---

### G2: Strategy ruthlessness — delete TypeA/D/E/F, VanguardSniper to SHADOW, TypeB to LIVE CORE

**PARTIALLY ALREADY DONE / PARTIALLY REJECT**

What is already done:
- TypeA: DISABLED in strategy_registry.json (backtest WR 29.5%)
- TypeD: DISABLED in strategy_registry.json (backtest WR 24.1%)
- TypeB: STATUS=live in strategy_registry.json, Python classifier active
- Strategy registry exists with live/shadow/disabled states

What is wrong:
- "VanguardSniper to SHADOW" is the single worst suggestion in either review. VanguardSniper
  is our ONLY proven signal producer (33 trades, actual fills, actual P&L data). Moving it to
  shadow means we go from 2 trades/day to 0 trades/day. We would learn nothing. VanguardSniper
  IS the system right now.
- "Delete Orchestrator S17-S20" — these are already in shadow mode producing zero trades. Deleting
  dead code that costs nothing to keep is a premature cleanup that destroys future optionality.
- TypeE has 3 observed trades. Killing it when it is already shadow is pointless.

What is correct:
- TypeB SHOULD be the priority investigation target. This is already Sprint S4 in our plan.
  The suggestion to fix TypeB's trigger from "3-bar rising RVOL" to "1-min volume shock > 3x ADV
  AND OBI > 70%" is an interesting alternative worth testing, but it is a different strategy, not
  a fix. We need to first understand WHY TypeB never fires before rewriting its logic.

**ACCEPT LATER** (TypeB investigation is Sprint S4, already planned)

---

### G3: Extreme capital velocity — Time-stop 45min to 8min, replace Chandelier with Parabolic SAR

**REJECT**

Reasoning:
- Time-stop IS implemented at 45 minutes. Gemini's canonical plan draft was written before the
  last session added it. This is a stale observation.
- 8 minutes is absurdly short for an intraday momentum system. Our average trade duration on
  winning trades is much longer than 8 minutes. An 8-minute time-stop would kill every trade
  that needs time to develop. This is the opposite of letting winners run.
- Replacing Chandelier 5-rung with Parabolic SAR is a massive architectural change to the exit
  system (exit_engine.rs is 400+ LOC of battle-tested Chandelier logic). We have 66 trades.
  We do not know if Chandelier is the problem because we do not have enough data. Replacing
  the exit system before understanding exit performance is premature optimization.
- The Chandelier 5-rung ladder IS the exit system. It has configurable ATR multipliers per rung,
  it works, and it is deployed. The correct move is to collect 300 trades and measure rung
  attainment rates, not to replace the system wholesale.
- SAR is a lagging indicator that performs poorly in choppy markets. Our regime detector already
  blocks entry in high_vol_chop. Chandelier + regime gating is a better architecture than SAR.

---

### G4: Net-cost learning — Inject synthetic slippage/commission drag before Ouroboros evaluates

**ACCEPT LATER**

Reasoning:
- This is correct in principle. The canonical plan Section 20 already identifies this gap:
  "Entry commission: 0, Exit commission: 0, Slippage: 0" in sim mode.
- The plan already recommends adding `slippage_bps` config param (default 5bps) before live.
- However, Ouroboros is FROZEN at observe_only=true on N=48 data. Injecting synthetic costs
  into a frozen learning system has zero effect right now.
- The right time to do this is Sprint S7 (spread-at-fill tracking) and before enabling Ouroboros
  at N=300. Not this session.

**Backlog priority**: MEDIUM. Add to Sprint S7 or create S7b.

---

### G5: Dynamic Covariance Kelly + Intraday Capital Recycling

**REJECT**

Reasoning:
- We have a 10k paper account that has lost 6.79 cumulative. We are below all validation gates.
  Dynamic Covariance Kelly is a portfolio-level technique for allocating across correlated strategies.
  We have ONE strategy producing trades. There is nothing to covariance-weight.
- Kelly ramp is already implemented (smooth 10%-100% over 250 trades, currently at ~26% ramp
  with 66 trades). The sizing system is working and conservative. Adding covariance matrices
  to a single-strategy system is over-engineering.
- Intraday Capital Recycling (reusing freed capital within the same session) is an optimization
  for a system constrained by capital. We are paper trading with max_positions=999 and
  heat=50%. Capital is not the constraint. Signal quality is the constraint.
- Both of these are post-validation-gate features for a multi-strategy live portfolio.

---

## CHATGPT FEEDBACK TRIAGE

### C1: Make TypeB mandatory engineering priority

**ALREADY DONE (planned)**

This is Sprint S4 in the canonical plan. It is the #1 priority after the microstructure sprint
we just completed. The suggestion is correct but tells us what we already know.

---

### C2: VanguardSniper = capital core (explicit)

**ALREADY DONE**

VanguardSniper is status=live in strategy_registry.json. It is the only strategy with trades.
It is the default signal source in bridge.py. The canonical plan Section 5 calls it "Core. Only
proven producer." This is already explicit.

---

### C3: Add regime routing as first-class layer

**PARTIALLY ALREADY DONE / ACCEPT LATER for full integration**

What exists:
- `regime_detector.rs` (170 LOC) — JumpDiffusion detector + Hurst exponent estimation
- `strategy_registry.json` — every strategy has `regime_allowed`, `regime_blocked`, `regime_reduced`
- `strategy_config.rs` — per-strategy regime-aware configuration
- Risk arbiter blocks entry during flash crashes via regime detection

What is missing:
- The regime_allowed/blocked/reduced fields in strategy_registry.json are not yet enforced at
  runtime in the Python bridge or Rust engine. They are metadata only.
- Full regime routing (strategy X only fires in regime Y) requires wiring the registry into
  the bridge signal evaluation loop.

**ACCEPT LATER** — This is a real gap but it requires careful implementation. Add as Sprint S10
after TypeB investigation and EC2 upgrade. We need more trades (and thus more regime samples)
before this is meaningful anyway.

---

### C4: Add session templates as first-class layer

**PARTIALLY ALREADY DONE / ACCEPT LATER**

What exists:
- `strategy_registry.json` has `session_allowed` and `session_blocked` per strategy
- `session_definitions` map in registry (asia_main, lse_main, us_open, etc.)
- ORB_Breakout is already US-session-only (14:45-15:30 UTC)

What is missing:
- Same as regime routing: the session gating in the registry is metadata, not enforced in the
  hot path. Bridge.py should check current UTC time against strategy session_allowed before
  evaluating signals.

**ACCEPT LATER** — Wire alongside regime routing in Sprint S10.

---

### C5: Add symbol-quality memory

**ACCEPT LATER**

Reasoning:
- This is a good idea. Per-ticker Wilson-score memory already exists partially in the nightly
  pipeline (Step 4.5: `generate_ticker_scoreboard()`). Persistent memory tracks per-ticker
  stats.
- However, the ticker scoreboard does not yet feed back into signal gating. A ticker that has
  lost money 5 consecutive times should be deprioritized.
- This requires N=100+ trades across multiple tickers before the scores are meaningful. At 66
  trades mostly on a handful of tickers, the sample sizes are too small.

**Backlog priority**: LOW until N=200+.

---

### C6: Add friction-aware signal ranking

**ACCEPT LATER**

Reasoning:
- This overlaps with Gemini's G4 (net-cost learning). The idea is correct: rank signals by
  expected net P&L after spread + commission + slippage, not gross.
- bridge.py already computes `round_trip_fee_pct` and `ibkr_commission_gbp` for sizing. These
  could be used to rank signals.
- However, we currently have 1 strategy producing signals. There is nothing to rank. When
  TypeB starts firing (Sprint S4), ranking 2 simultaneous signals by net expectancy becomes
  useful.

**Backlog priority**: MEDIUM. After Sprint S4 (TypeB fires) and S7 (spread tracking).

---

### C7: Add portfolio-level allocation (heat cap, correlation cluster cap)

**REJECT (for now)**

Reasoning:
- Heat cap exists: max_heat_pct in config.toml (50% paper, 10% live in config.live.toml).
- Correlation cluster cap requires multiple simultaneous positions in correlated assets. With
  max_positions=999 in paper mode and only 2 trades today, we are not hitting cluster risk.
- This is a live-capital, multi-strategy feature. The canonical plan Section 18 already plans
  config.live.toml with max_positions=3.
- Adding correlation clustering to a system that rarely holds more than 1 position is dead code.

---

### C8: Make exits asymmetric by strategy/session

**ACCEPT LATER**

Reasoning:
- This is architecturally sound. strategy_config.rs already supports per-strategy exit parameters
  (`exit_time_stop_minutes`, adaptive ranges per strategy type). The ChandelierStrategy can be
  instantiated with different rung thresholds per strategy.
- Currently, all strategies share the same Chandelier config. Once TypeB starts producing trades,
  it may need tighter exits (momentum continuation = faster trail) vs VanguardSniper (broader
  momentum = wider trail).
- Premature now. We need TypeB trades first, then measure if exit asymmetry improves outcomes.

**Backlog priority**: MEDIUM. After Sprint S4 produces TypeB trades and we have per-strategy
exit performance data.

---

### C9: Make backtests cost-honest and strategy-isolated

**ACCEPT LATER**

Reasoning:
- The canonical plan Section 12 already states: "Backtesting is not proof" and requires slippage,
  commission, and survivorship bias to be accounted for.
- fast_backtest_pipeline.py exists but we do not know if it includes costs.
- This is a backlog item but not urgent because we are not making live decisions based on
  backtest results. We are paper trading and measuring live performance.

**Backlog priority**: LOW until a strategy-change decision depends on backtest results.

---

### C10: Add brutal strategy kill framework

**ALREADY DONE**

The strategy registry already has live/shadow/disabled states. TypeA and TypeD are already
disabled based on backtest WR. The canonical plan Section 12 says strategies must pass
validation gates to stay live. The nightly pipeline evaluates per-strategy performance.

The registry is the kill framework. Changing status from "live" to "disabled" is one JSON edit.

---

### C11: Rewrite strategy inventory with runtime roles

**ALREADY DONE**

The strategy_registry.json IS the runtime inventory. It has:
- Status (live/shadow/disabled)
- Python source file + line number
- Rust detector status (quarantined or null)
- Config path
- Regime allowed/blocked/reduced
- Session allowed/blocked
- Backtest/nightly/reporting tags

This is exactly what ChatGPT is asking for. It already exists.

---

### C12: Expand Section 2 with net expectancy metrics

**ACCEPT LATER**

Reasoning:
- Section 2 of the canonical plan has WR and PF but not net expectancy per strategy, not
  average winner vs average loser, not expectancy per trade after costs.
- This is useful metadata but requires more trades to be meaningful. At 33 VanguardSniper
  trades and 3 TypeE trades, per-strategy net expectancy has wide confidence intervals.
- The nightly pipeline already computes daily metrics including per-strategy WR and PF.
  Adding cumulative net expectancy to persistent_memory.py is a small change.

**Backlog priority**: LOW. Useful for documentation, not for system behavior.

---

### C13: Add compounding-specific blocker prioritization

**REJECT**

Reasoning:
- This asks us to prioritize blockers by "which prevents compounding." The canonical plan
  Section 7 already has prioritized pre-live blockers. They are ranked by severity.
- "Compounding-specific" framing is a narrative overlay on the same blockers. The #1 blocker
  is WR 35.4% (need 40%). That blocks compounding AND everything else.
- Re-labeling existing priorities adds no value.

---

### C14: Various section rewrites (Section 4, 6, 7, 8, 9, etc.)

**REJECT**

Reasoning:
- ChatGPT wants to rewrite multiple sections of the canonical plan to incorporate its
  suggestions. The canonical plan was written TODAY and is 431 lines of evidence-bound content.
- The correct approach is to update the plan incrementally as we implement changes, not to
  rewrite sections based on theoretical improvements from an AI that has not seen our codebase.
- If a specific suggestion gets ACCEPT NOW or ACCEPT LATER, the relevant section update happens
  when we implement it. Not before.

---

## SUMMARY TABLE

| ID | Suggestion | Source | Verdict | Sprint |
|----|-----------|--------|---------|--------|
| G1 | Dual-data pipeline (Polygon + Lambda) | Gemini | **REJECT** | - |
| G2 | Delete TypeA/D, VanguardSniper to shadow | Gemini | **REJECT** (VanguardSniper shadow is suicidal), TypeA/D **ALREADY DONE** | - |
| G2b | TypeB to LIVE CORE | Gemini | **ALREADY DONE** (status=live) + **ACCEPT LATER** (investigation) | S4 |
| G2c | Fix TypeB trigger logic | Gemini | **ACCEPT LATER** (investigate first, then potentially rewrite) | S4 |
| G3 | Time-stop 8min + replace Chandelier with SAR | Gemini | **REJECT** | - |
| G4 | Inject synthetic slippage into Ouroboros | Gemini | **ACCEPT LATER** | S7b |
| G5 | Dynamic Covariance Kelly + Capital Recycling | Gemini | **REJECT** | - |
| C1 | TypeB engineering priority | ChatGPT | **ALREADY DONE** (Sprint S4 planned) | S4 |
| C2 | VanguardSniper = capital core | ChatGPT | **ALREADY DONE** | - |
| C3 | Regime routing as first-class layer | ChatGPT | **PARTIALLY DONE**, wire enforcement **ACCEPT LATER** | S10 |
| C4 | Session templates as first-class layer | ChatGPT | **PARTIALLY DONE**, wire enforcement **ACCEPT LATER** | S10 |
| C5 | Symbol-quality memory | ChatGPT | **ACCEPT LATER** | S11 (N=200+) |
| C6 | Friction-aware signal ranking | ChatGPT | **ACCEPT LATER** | After S4+S7 |
| C7 | Portfolio-level allocation (heat, correlation) | ChatGPT | **REJECT** (heat cap exists, correlation premature) | - |
| C8 | Asymmetric exits by strategy | ChatGPT | **ACCEPT LATER** (infra exists in strategy_config.rs) | After S4 |
| C9 | Cost-honest backtests | ChatGPT | **ACCEPT LATER** | S12 |
| C10 | Brutal strategy kill framework | ChatGPT | **ALREADY DONE** (strategy_registry.json) | - |
| C11 | Rewrite strategy inventory with runtime roles | ChatGPT | **ALREADY DONE** (strategy_registry.json) | - |
| C12 | Net expectancy metrics in Section 2 | ChatGPT | **ACCEPT LATER** | S11 |
| C13 | Compounding-specific blocker prioritization | ChatGPT | **REJECT** (relabeling existing priorities) | - |
| C14 | Section rewrites (4, 6, 7, 8, 9...) | ChatGPT | **REJECT** (plan updates happen at implementation) | - |

---

## FINAL SCORECARD

| Verdict | Count | % |
|---------|-------|---|
| ACCEPT NOW | 0 | 0% |
| ACCEPT LATER | 9 | 45% |
| REJECT | 6 | 30% |
| ALREADY DONE | 5 | 25% |

**Zero items are actionable this session.** Both AIs are optimizing for a future system
state (multi-strategy, live capital, proven edge) that does not exist yet. The correct
action right now is:

1. Let the microstructure fixes from today's session soak (L1 gate, spoof calibration, board lots)
2. Sprint S4: Investigate why TypeB never fires (THIS is the highest-leverage work)
3. Continue paper trading toward N=100 validation gate
4. Do not add complexity to a system that cannot yet prove its simplest strategy is profitable

---

## META-OBSERVATION

Gemini's suggestions are aggressive architectural rewrites (Polygon, SAR, 8-min time-stop,
Covariance Kelly) that assume a mature system needing optimization. We are a prototype that
needs validation.

ChatGPT's suggestions are more conservative but suffer from not seeing the codebase. Half of
what it recommends already exists (strategy registry, regime metadata, VanguardSniper as core,
TypeB priority). The other half is correctly identified future work.

Neither AI asked the most important question: **Why does VanguardSniper have a 35.4% win rate
and negative cumulative P&L after 66 trades?** Until we answer that, no amount of architectural
sophistication matters. A fancy exit system on a losing signal is still a losing system.
