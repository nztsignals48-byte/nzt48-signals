§# AEGIS V5 — MASTER PLAN (CONTINUOUS PHASES)
## Designed 2026-04-16 from the V4 Persona Adversarial Review + V3 11-Persona Audit

> **V5 Mandate (operator, 2026-04-16):**
> 1. Fix the real root causes that killed V4 — **data starvation, gauntlet blockers, dead-code LLM layer, multiple engines / multiple ports, LLMs and ML not impacting trades, IBKR data paid-for but not pulled / not used**.
> 2. **Scaffold every single piece on Day 1** as a placeholder file so the system's shape is complete and nothing is "missing later." Each placeholder is filled through the phases and each phase **proves the piece is actually consumed and actually changes trades** before moving on.
> 3. **Continuous, agent-paced phases, not calendar weeks.** The AI agent does the work; phases advance the moment their acceptance gate passes.
> 4. **Zero tolerance for dead code.** If we pay for an LLM call or an IBKR tick, it must alter a signal, a size, a risk check, or an exit. No "built but never consumed" — V4's defining failure.
> 5. **Zero tolerance for zero-trade days.** A day with 0 trades and no market-closed reason is a system bug that triggers an automatic root-cause run.
> 6. **Make it as likely as possible to actually work** — see **PART -1** for the five structural enforcement mechanisms that turn the plan from "intentions" into "automatically enforced." These are not optional and cannot be waived per deploy.

---

## PART -1 — MAKE-IT-WORK ENFORCEMENT LAYER

The honest assessment of the preceding plan: the architecture is right, but every prior AEGIS version had good architecture too. V4's failure was not its plan — it was **discipline collapse under session pressure**. The five mechanisms below are the difference between "likely to work" and "likely to fail the same way V4 did."

### M-1 — CI gates are branch-protected, not optional

Four CI jobs must pass before ANY merge to `main`:
1. `cargo test --all` (Rust unit + integration).
2. `pytest -q` (Python).
3. `scripts/dead_code_check.py --strict` (every module has a caller, every `@step` runs, every NATS subject has a subscriber).
4. `scripts/field_ledger_check.py --strict` (every row in `FIELD_CONSUMPTION_LEDGER.md` has a proof metric that fired in last 24 h OR is marked `scaffold_phase=N` where N is the current phase or later).

GitHub branch protection rule: `main` requires all four jobs green. **No admin bypass.** If the gate blocks a merge, the gate wins. The only way to ship a dead-code module is to add a ledger entry and a proof metric — which is the same as not shipping dead code.

**Why this matters:** V4 had a `scripts/deploy.sh` that pushed straight to EC2 with no CI. Every "we'll fix it after paper trading" started there. V5 cannot have that escape hatch.

### M-2 — The Golden Signal Test (every phase, every day)

A single integration test, `tests/golden_signal.py`, runs in CI on every commit AND in production every 15 minutes during market hours. It injects a known synthetic tick stream that is designed to produce exactly **one signal per core strategy**. If the signal does not appear at the expected NATS subject with the expected conviction within the expected latency budget, the test fails.

This is the single most important test in V5. It answers in 60 seconds: *"is the pipeline still plumbed end-to-end?"* — the exact question V4 couldn't answer without a human audit.

**Coverage:**
- Tick → bar builder → indicators → quant → risk arbiter → exit engine
- Strategy evaluation → conviction engine → portfolio constructor
- Order router → broker sync → event store
- For each of the 9 core strategies: one golden tick sequence, one expected signal

**Production deployment:** the test runs as a cron every 15 min against the live paper engine using a reserved "canary" ticker. If the canary stops firing, Telegram CRITICAL alert, same mechanism as the zero-trade-day autodiag.

**Why this matters:** V4's `_apply_adjustments` had 50+ `return None` gates that killed every signal, and nobody noticed for weeks because there was no end-to-end test. The golden signal test makes that failure mode impossible to hide.

### M-3 — Measurement honesty: N ≥ 200 is enforced by code, not by convention

The A/B delta requirement for every LLM agent and every ML model is implemented as a **bounded counter**, not a spec. The relevant file `python_brain/core/ab_harness.py` exposes:

```
AgentABHarness(agent_name, min_samples=200)
  .record(strategy_default, llm_output, realized_pnl)
  .can_report_delta() -> bool        # False until 200 samples
  .delta_with_ci() -> (mean, lo, hi) # 95% CI via bootstrap
  .is_alpha_positive() -> bool       # lo > 0
```

No agent reports a delta below 200 samples — the method literally returns `None`. No agent stays enabled if `is_alpha_positive()` returns False on the latest 200 rolling samples. The cost governor auto-disables any agent failing this test.

**Regime stratification:** deltas are computed separately for `regime={steady, trending, crisis}` so an agent that helps in steady but hurts in crisis is not averaged into a false positive.

**Why this matters:** V4 deployed LLM conviction on 20 signals and called it validated. The bounded counter makes that impossible.

### M-4 — The Kill-Switch Hierarchy (three independent authorities)

Any one of three authorities can halt the system, and all three are tested weekly:

1. **Engine self-kill** — `risk_arbiter.rs` watches 8-consecutive-losses, drawdown > 10 %, equity < 70 % HWM. Writes `kill.engine` to NATS.
2. **External watchdog** — Hetzner CX22 reads equity from NATS mirror, has its own drawdown threshold, its own Telegram bot, and authority to write `kill.watchdog` to NATS.
3. **Operator manual** — `scripts/kill.sh` on any host writes `kill.operator` to NATS. Works even if the engine is wedged, because it writes directly to NATS.

The engine flattens on ANY of the three subjects. If NATS itself is down, the engine treats that as `kill.engine` automatically (fail-closed).

**Weekly drill:** every Sunday, `scripts/kill_drill.sh` fires each of the three kill paths against the paper engine and verifies flatten within 30 s. Failure → blocks Monday session.

**Why this matters:** V4's KILL file was hardcoded to `/app/data/KILL` which didn't exist on the Mac. If it had been triggered, nothing would have happened. V5's three independent paths + weekly drill means at least one path is always live.

### M-5 — The Live Readiness Checklist (blocks Phase 13)

Before the first pound of real capital, a single file `docs/LIVE_READINESS_CHECKLIST.md` must have every item green. It is a literal checklist, not a design doc:

- [ ] 60 consecutive paper days with zero `zero-trade-day` incidents
- [ ] 3 strategies with ≥ 500 trades, Sharpe > 0.5, PF > 1.05, MDD < 2× backtest, DSR > 0
- [ ] All 11 personas signed off via `tests/acceptance/<persona>.py` exit 0
- [ ] Monte Carlo stress passed (2020-03, 2021-01, 2024-08) — MDD < 10 % simulated equity
- [ ] Kill drill passed 8 consecutive Sundays
- [ ] External watchdog delivered Telegram alert in last 7 days (live, not a drill)
- [ ] Broker reconcile zero discrepancies 14 consecutive days
- [ ] Cost governor halted LLM spend in a live `$15/day` drill
- [ ] Golden signal test green for 14 consecutive days
- [ ] Every row of `FIELD_CONSUMPTION_LEDGER.md` has a proof-metric `last_seen < 24 h`
- [ ] Every LLM agent's `is_alpha_positive()` is True
- [ ] `bounds.toml` validated; `learned.toml` has updated from trade data at least 30 times
- [ ] Hetzner CX32 deployed; Mac removed from production path
- [ ] ISA/GIA/IG account splits configured; stamp duty + IG financing verified

Initial live allocation: **£500 at 25 % Kelly.** Not £20K. Scale up only after 500 live trades meet criteria.

**Why this matters:** V4's CIO rejection was 8.5 % plan compliance and "23 % win rate, synthetic models." V5 cannot go live until this checklist is literally green. No "mostly ready." No "we'll watch it closely." Green or not green.

### M-6 — The Session Discipline Contract

V4 lost progress because of specific repeated mistakes: uncommitted code lost on Docker rebuild, `docker exec` env vars that don't persist, SIGHUP that clears bar history, `docker cp` edits lost. V5 adopts four session rules enforced by tooling, not memory:

1. **All changes via git commit.** `docker cp` is forbidden. `scripts/deploy.sh` refuses to start if the working tree is dirty.
2. **All env vars in `docker-compose.yml`.** Not in `docker exec`. CI lints any `.sh` script that tries to set env via exec.
3. **All config reloads via NATS `config.reload`.** Not SIGHUP. SIGHUP is wired to a no-op with a log warning.
4. **Session handover is a single file.** `docs/SESSION_HANDOVER.md` is updated at the end of every session with: what was done, what the CI state is, what the next session starts with. New sessions read it first, no exceptions.

**Why this matters:** AEGIS history is littered with "lost in rebuild" notes. V5 removes the ways those can happen.

### Probability delta (updated for M-1..M-6 + Part -0.5 sim-reframe + Part -0.3 refined rules)

Rough honest estimates:

| Probability | V5 plain | V5 + Part -1 | V5 + Part -1 + Part -0.5 + Part -0.3 |
|---|---|---|---|
| V5 reaches Phase 12 paper graduation with 11/11 sign-off | ~45 % | ~70 % | **~78 %** |
| V5 produces a clean labelled dataset ≥ 5 k trades suitable for Ouroboros learning | ~60 % | ~85 % | **~94 %** |
| V5 makes positive net return Year 1 on live capital, conditional on Phase 13 | ~55 % | ~65 % | **~68 %** |
| V5 avoids V4's "built but never consumed" failure mode | ~85 % | ~97 % | **~98 %** |
| V5 avoids a V4-style zero-trade-day loss | ~70 % | ~95 % | **~96 %** |

**Why Part -0.5 helps the middle rows most:** reframing V5 as a dataset-generation lab aligns the acceptance gates with the actual bottleneck (evaluation quality / labelled observations), not simulated PnL. The biggest remaining risk on Year-1 return is market risk that no architecture can eliminate — the strategies must earn it.

The residual ~22 % of graduation risk decomposes roughly: ~10 % strategy edge not replicating, ~7 % regime shock during paper window, ~5 % operator discipline slip despite the enforcement layer.

### Bottom line

With Part -1, V5 is **as likely to work as the operator's discipline to enforce the gates.** The mechanisms turn discipline from "remembered each session" into "automated in CI and cron." That's the strongest version of the plan I can write without knowing things only live data will tell us.

---

## PART -0.5 — SIMULATION-FIRST REFRAME (the real goal)

Everything above was written as if V5's goal is "trade well." **The real goal is:** build a simulation lab that produces clean, ML-ready evidence so Ouroboros can learn what actually survives costs. Alpha comes out of that evidence, not out of guessing well upfront.

### Reframed objectives (paper-era, until live readiness checklist is green)

1. **Maximise varied, high-quality trades under realistic frictions**, not simulated PnL.
2. **Every trade is a labelled observation** written to the WAL under the dataset contract in § PART -0.4.
3. **Finish Phase 0–2 just enough to boot continuous paper**, then fill the rest of V5 while data flows. Do not wait for the full stack before paper-trading.
4. **Downweight live-only concerns** (IG 2FA runbooks, elaborate watchdog drills, live cost-governor live-cap drills) until Phase 10+. Keep them in the plan, just not on the critical path to first paper data.
5. **Judge every design decision by:** *how much clean, diverse, labelled data does it produce per unit of complexity?* Not by what it looks like on a PnL chart.

### What changes in the plan under the reframe

| Area | Original plan framing | Reframed |
|---|---|---|
| MVP strategy count | 15–20 live + 10 shadow | **4–6 live, wildly diverse; everything else `shadow/` unwired until Gate 0–2 proves edge** |
| Phase 5 gate | ≥20 signals/day per strategy | Signals must be *data-rich*: non-degenerate features, no NaN/constants, valid fills, valid exit reasons, ≤5 % rejected for broken data-health. See § PART -0.3 |
| Phase 12 graduation | Sharpe + PF + MDD + DSR | All of above **plus** one stress-window replay (2020-03 or 2024-08) through the replay harness with same config. See § PART -0.3 |
| Observability at MVP | 3 dashboards + Loki + watchdog on Day 1 | **Phases 0–4**: file-based structured logs + Prometheus for the 3 critical alerts (crash / drawdown / IBKR disconnect) + zero-trade-day autodiag. Loki deferred to P10. External watchdog deferred to **P10.1**, not a blocker for early paper. Three dashboards render but on Prometheus data, not Loki logs. |
| LLMs + ML | "No ONNX at MVP" | **Stronger:** no learned model of any kind (even plain sklearn) on the hot path until ≥10 k real observations exist per candidate feature set. Post-Phase 9 only. |
| First paper restart | After Phase 5 | **After Phase 2A** — as soon as engine hot-path + cost modelling + WAL-under-dataset-contract are stable, switch to continuous paper. Fill remaining subsystems while paper runs. |

### Consequence for complexity budget

The single-operator bottleneck is evaluation quality, not code volume. The reframe says: build the minimum that produces honest data, run it continuously, and let Ouroboros + the research harness do the strategy-discovery work later.

---

## PART -0.4 — THE DATASET CONTRACT (the WAL schema every phase serves)

The WAL is V5's only durable artefact. If nothing else works, we must leave behind a dataset that Ouroboros, a researcher, or the next version can learn from. The dataset contract is therefore the single non-negotiable schema in V5.

### Every `SignalReceived` event carries

```
schema_version: u16
signal_id: uuid                               # correlates to TradeClosed
strategy_name: str
strategy_version: str                          # git short-sha of strategy file at signal time
ticker: str
exchange: str
account: enum {ISA, GIA, IG}
timestamp_ns: u64
feature_vector:
    # every input the strategy consulted, typed, no NaN allowed
    ibs: f64
    atr: f64
    rsi: f64
    adx: f64
    hurst: f64
    ema_fast: f64
    ema_slow: f64
    bb_width: f64
    keltner_width: f64
    macd_hist: f64
    vwap_distance_bps: f64
    spread_bps: f64
    bid_size: u64
    ask_size: u64
    book_imbalance: f64
    book_pressure: f64
    rvol: f64
    session_high: f64
    session_low: f64
    # quant_core
    garch_vol_annualized: f64
    evt_cvar_95: f64
    kalman_residual: f64
    regime_probs: [f64; 4]                     # steady/trending/crisis/rotation
    hy_correlation_to_spy: f64
    # portfolio context
    equity_total: f64
    equity_hwm: f64
    drawdown_pct: f64
    position_count: i32
    sector_exposure_pct: f64
    ticker_exposure_pct: f64
    # llm / intel (nullable if unused)
    llm_conviction: Option<f64>
    llm_provider: Option<str>
    llm_model_version: Option<str>
    intel_sources_consulted: [str]
conviction_score: f64                          # after conviction engine
portfolio_rank: i32                            # rank among simultaneous signals
account_route_chosen: enum {ISA, GIA, IG}
account_routes_available: [enum]               # alternatives the portfolio constructor saw
expected_fill_price: f64                       # arrival price at signal time
risk_deltas: map<str, f64>                     # per-check confidence_delta from risk_arbiter
risk_final_confidence: f64
```

### Every `TradeClosed` event carries

```
schema_version: u16
signal_id: uuid                                # links to SignalReceived
entry_timestamp_ns: u64
exit_timestamp_ns: u64
entry_price: f64
exit_price: f64
size_shares: i64
# costs, itemised
spread_cost_bps: f64
commission_abs: f64                            # native currency
stamp_duty_abs: f64
financing_cost_abs: f64                        # IG only, non-zero otherwise
slippage_bps_vs_arrival: f64                   # TCA
# outcomes
realized_pnl_abs: f64
realized_pnl_bps: f64
mae_bps: f64                                   # max adverse excursion during hold
mfe_bps: f64                                   # max favourable excursion during hold
# classification
regime_at_entry: [f64; 4]
regime_at_exit: [f64; 4]
exit_reason: enum {
    ChandelierStop,
    FixedDayExpiry,
    EventWindowExit,
    NextOpen,
    ProfitTargetHit,
    StopLossGuaranteed,
    KillFlatten,
    BrokerRejected,
    CorpActionFlatten,
    ManualClose,
}
```

**Invariants:**
- A strategy is not `READY` to fire until every field above is populated in its `SignalReceived` and `TradeClosed` WAL entries. Missing or NaN field → strategy blocked.
- The dataset-contract test runs in CI (`tests/dataset_contract_test.py`) and on every deploy.
- Schema upgrades are additive only. `schema_version` increments. Old events remain readable (replay harness must handle all historical versions).

**Consumers of this schema:**
- Ouroboros (Kelly/Chandelier/floor calibration)
- Research harness (new-strategy backtests on historical trades)
- Replay harness (counterfactuals)
- P&L attribution dashboard
- Zero-trade-day autodiagnostic

If the dataset contract is right, every other subsystem has a durable output to learn from. If the dataset contract is wrong, nothing else recovers.

---

## PART -0.3 — REFINED GATES AND RULES (from review)

### 4-R1. Field Consumption Ledger — "consumed" is narrowly defined

A field counts as **consumed** only if it is read by:
- sizing logic (Kelly scaling, position size),
- entry or exit decision logic,
- a risk check that emits a non-zero `confidence_delta`, or
- a regime / portfolio classification that demonstrably alters Kelly or confidence-floor.

Reading a field into a log line, a metric, or a dashboard does **not** count. Those are "observed-only" and are tagged `status: observed_only` in the ledger. Observed-only fields must either be **promoted** (wired into one of the four categories above) within one phase of their capture, or **unsubscribed**. Paying IBKR for a field that is only observed is the failure V5 is built to prevent.

### 4-R2. Phase 5 gate — data quality, not activity

Phase 5 used to close on "each strategy emits ≥ 20 signals/day." Replaced with:

- **Quantity floor**: ≥ X signals/day per strategy (X tuned per strategy's natural frequency, not uniform).
- **Quality floor**:
  - No signal may contain NaN, null, or constant-value features.
  - ≥ 95 % of signals must have valid fills and a recognised `exit_reason`.
  - ≤ 5 % of signals may be rejected due to data-health failures (missing intel dependencies, stale benzinga, etc.).
  - Feature-vector entropy per strategy must exceed a per-strategy threshold (prevents a strategy firing identical signals repeatedly).

### 4-R3. Phase 12 graduation — add stress-window replay

Existing criteria (500+ trades, Sharpe > 0.5, PF > 1.05, MDD < 2× backtest, DSR > 0) are preserved. Add:

- Each graduating strategy must pass a **stress-window replay** through `replay.rs`: inject the 2020-03 or 2024-08 tick sequence, run the exact current config, confirm the strategy would not have blown through `dd_black` (−10 %). Dalio signs off on which windows to replay each quarter.

### 4-R4. LLM Impact-Proof Rule — concrete minimal detectable effect

For each LLM agent, pre-register a target metric and a minimum effect size:

| Agent | Pre-registered metric | Minimum effect |
|---|---|---|
| news_reactor | conviction calibration (Brier score) | 5 % absolute improvement vs strategy default |
| earnings_whisper | earnings-strategy PF | +0.10 PF absolute |
| sec_scanner | filing_change strategy PF | +0.10 PF absolute |
| regime_council | Kelly-adjusted return at equal risk | +3 % annualised |
| thesis_monitor | adverse-exit rate reduction | −10 % relative |

A/B harness runs bootstrap 95 % CI over N ≥ 200 rolling signals, stratified by regime. If the CI crosses zero, the agent is **returned to V5.1 shelf**, even if users feel it helps. This is enforced in `cost_governor.py` — the agent is auto-disabled.

### 4-R5. LLM forbidden zones

LLMs may **not** originate raw numeric entry/exit rules. They may only:
- Modify conviction within a bounded range (−30 to +15 percentage points of strategy default).
- Classify interpretable, logged context (news sentiment, filing category, regime label).
- Gate a signal (binary include/exclude) only if their decision is logged with a one-sentence justification that is replayable.

LLMs may not set stop levels, position sizes, or rebalance thresholds directly. Those come from Rust quant-core + bounded Ouroboros parameters. This keeps the "no pseudocode strategies" mandate intact.

### 4-R6. Zero-trade-day autodiag — machine-readable output

The autodiag already writes `docs/incidents/YYYY-MM-DD.md`. It must additionally emit `docs/incidents/YYYY-MM-DD.json`:

```
{
  "date": "2026-04-16",
  "root_cause": "intel_starvation",
  "primary_subsystem": "earnings_whisper",
  "suspected_change_sha": "abc1234",
  "evidence": {
    "intel_files_empty": ["earnings_whisper.json"],
    "strategies_starved": ["earnings_pattern", "pead_continuation"],
    "confidence_floor_rejections": 438,
    "ibkr_session_gaps_min": 0
  }
}
```

This JSON feeds a future "incident mining" Ouroboros step: what classes of failures cluster together, which changes preceded which failures. That's the meta-learning we're actually building.

### 4-R7. Phase 2 split

| Original Phase 2 | Split |
|---|---|
| Engine + all of quant core | **Phase 2A**: engine, bars, indicators, WAL under dataset contract, risk arbiter, exit engine, basic order routing, broker sync. |
| (same) | **Phase 2B**: quant core (GARCH, EVT, Kalman, regime prob vector, Hayashi-Yoshida) — each output **must** have a consumer in risk or sizing before that sub-phase closes. |

Phase 2A closing = **continuous paper restarts immediately** with 4–6 strategies. Data starts flowing to the WAL under the dataset contract while 2B runs. This is the single biggest change from the original plan — paper doesn't wait for the full stack.

### 4-R8. Phase 0 timebox

Phase 0 is **one focused block**. Target: 2–4 days of scaffolding. During Phase 0:
- Only stubs, smoke tests, compile checks. No polish.
- No subsystem is expanded beyond a skeleton + TODO + passing import test.
- Exit Phase 0 the moment `cargo build && pytest tests/smoke && docker compose up` all go green.

If Phase 0 stretches beyond 4 days, that is a signal to cut scope, not to keep polishing.

### 4-R9. MVP strategy set (final)

4 V3 Gate-2 winners + 1–2 very-different edges, as data generators:

| # | Strategy | Edge type | Dataset value |
|---|---|---|---|
| 1 | sentiment_long_short | Cross-sectional sentiment | News/NLP labelled trades |
| 2 | filing_change_detect | Event-driven (SEC filings) | Filing-window PEAD labels |
| 3 | index_recon | Calendar-driven (Russell/S&P) | Passive-flow microstructure |
| 4 | earnings_pattern | Post-earnings drift | Earnings-surprise labels |
| 5 | overnight_return | Structural (close-to-open) | Overnight premium labels |
| 6 | ibs_mean_reversion | Intraday mean reversion | Fade/reversal microstructure |

Everything else — NAVArbitrage, FOMC drift, ETP lag, the shadow list — sits in `strategies/shadow/` unwired until Gate 0–2 on real V5 data proves edge. This is the "cap MVP harder" refinement + dataset-first reframe combined.

### Summary of refined rules

| Rule | Where enforced |
|---|---|
| Ledger "consumed" ≠ logged | `field_ledger_check.py` AST-greps for decision-site reads, not metric/log sites |
| Data-quality gate in P5 | `tests/acceptance/wood.py` plus a new `tests/data_quality_gate.py` |
| Stress replay in P12 | `tests/acceptance/dalio.py` — runs `replay.rs` against stress tapes |
| LLM minimum effect | `core/ab_harness.py` — enforces pre-registered target + N ≥ 200 + regime strata |
| LLM forbidden zones | `conviction_engine.py` clips LLM output to [−30, +15] pp; no direct stop/size setters |
| Autodiag JSON | `scripts/zero_trade_day_autodiag.py` emits both `.md` and `.json` |
| Phase 2 split | Master plan build order, Phase 2A acceptance gate = paper restart trigger |
| Phase 0 timebox | Master plan rule; drift here is an explicit red flag |
| MVP strategy cap | `strategies/registry.toml` — 6 entries at MVP, everything else under `shadow/` |

---

## PART 0 — WHY V4 FAILED (ROOT-CAUSE SUMMARY)

Five root causes, each mapped to a V5 structural response:

| # | Root Cause | Evidence | V5 Response |
|---|---|---|---|
| **RC-1** | **Data starvation** — 42/50 strategies never fired; intel JSONs were 47–60 byte stubs | V4 Session 14 | **Data Health Contract** — every producer declares a schema; every consumer declares a dependency; startup-blocking health gate refuses to launch if any consumed dependency is starved. **Zero-trade-day auto-diagnostic.** |
| **RC-2** | **Gauntlet starvation** — 50+ cascading `return None` gates silently killed every signal on the production path | V4 Session 38, V2 Session 39 | **Weighted scoring only.** Every check emits a continuous `confidence_delta`. One confidence floor is the sole arbiter. Only 8-consecutive-losses halts. |
| **RC-3** | **Dead-code LLM/ML layer** — `ConvictionEngine.rank_signals()` never called; synthetic-data ONNX deployed; preference logger never invoked; 13/16 agents wrote empty JSON | V4 Gap Audit | **Impact-Proof Rule** — every LLM call and every ML model ships with a logged A/B delta vs its default. No measurable delta = deleted. CI dead-code gate. |
| **RC-4** | **Multiple engines / multiple ports** — scanner writes `watchlist.json`, engine reads `contracts.toml`; TCP bridge brittle; Python/Rust coupled through shared filesystem | V4 Gap Audit | **One message bus (NATS).** No shared mutable filesystem state. One engine binary, one brain binary, one scanner binary, schemas on the wire. |
| **RC-5** | **Paid-for IBKR data not consumed** — 42 MarketTick fields captured, most never read; L2 depth pipeline dormant until Session 33; Benzinga headlines 12 days stale, no ticker extraction | V4 Sessions 32-34 | **Field Consumption Ledger** — every IBKR tick field listed in a registry; unit test proves each is read by ≥1 strategy or risk check; any unread field flagged and either wired or unsubscribed. |

**One-line V5 philosophy:** *Nothing is built unless it is consumed. Nothing is consumed unless it is measured. Nothing that doesn't change a trade stays in the system.*

---

## PART 1 — THE 11 PERSONAS DESIGN V5

| # | Persona | Role | V5 Subsystem | Non-Negotiable Contract |
|---|---|---|---|---|
| 1 | **Fink** | Risk | `risk-arbiter` (Rust) | Weighted scoring, no hard gates except 8-consec-loss halt. Server-side IBKR STP for every open position. Min £2,000. |
| 2 | **Wood** | Portfolio Manager | `portfolio-constructor` (Python) | Signals ranked top-N, not FIFO. ISA/GIA/IG split enforced. Bayesian Kelly on rolling 60 trades. |
| 3 | **Simons** | Quant | `quant-core` (Rust) + `research-harness` (Python) | GARCH→EVT→Kalman→HMM feeds position sizing. CUSUM alpha-decay per strategy. DSR at graduation. Replay harness mandatory. |
| 4 | **Cifu** | Execution | `order-router` (Rust) | 4-tier IBKR algo routing. Per-fill TCA. Sim slippage calibrated from first 100 live fills. |
| 5 | **Dario** | LLM Architect | `conviction-engine` (Python) | Every LLM call A/B-logged: `strategy_default` vs `llm_output` vs `realized_pnl`. Structured outputs, T=0, pinned versions. |
| 6 | **Pichai** | Data Engineer | `data-plane` (Rust+Python) | One bus (NATS). Every producer has a schema. **Field Consumption Ledger** enforced. WAL daily rotation. Feature values logged with every signal. |
| 7 | **Vogels** | SRE / Infra | `observability` + external watchdog | 3 dashboards + 3 critical alerts ship with MVP. Independent cloud watchdog with its own KILL authority. |
| 8 | **Cantrill** | Model Validation | `governance` | Every model has a model card. Every learned parameter has a bound. Dead-code CI gate. No claim without citation. |
| 9 | **Buckley** | CFO | `cost-governor` (Python) | LLM daily spend cap enforced by code. Phase-gated spend. Cost-per-signal on dashboard. |
| 10 | **Dalio** | Macro / Regime | `regime-council` (Python, cold) | Weekly MC stress test (2020-03, 2021-01, 2024-08). 2× backtest-to-live drawdown multiplier. Asymmetric de-risking. |
| 11 | **Anthropic Engineers** | Broker/Ops + Safety | `broker-sync` (Rust) + `audit-trail` | Broker reconcile every 60 s paper AND live. Corp actions detection. Hash-chained WAL, replayable. Prompt-injection defence. |

**Ownership rule:** any V5 file that cannot name its owning persona does not ship.

---

## PART 2 — V5 ARCHITECTURE (SINGLE HOST, SINGLE BUS)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ EXTERNAL WATCHDOG (Hetzner CX22)                                        │   ← Vogels
│  polls main /health, reads equity from NATS mirror, writes KILL         │
└──────────────┬───────────────────────────────────────────────────────────┘
               │
┌──────────────▼───────────────────────────────────────────────────────────┐
│ HETZNER CX32 — ONE HOST, ONE docker-compose                              │
│                                                                          │
│  IB Gateway ──┐                                                          │
│               ▼                                                          │
│   ┌────────────────┐   NATS JetStream (the ONLY inter-process bus)       │
│   │ engine (Rust)  │◄────────────────────────────────────────┐           │
│   │ hot path       │──────────────────┐                      │           │
│   └────┬───────────┘                  ▼                      │           │
│        │                      ┌────────────────┐             │           │
│        │                      │ brain (Python) │             │           │
│        │                      │ strategies +   │             │           │
│        │                      │ conviction +   │             │           │
│        │                      │ portfolio      │             │           │
│        │                      └────┬───────────┘             │           │
│        │                           │                         │           │
│        │                           ▼                         │           │
│        │                   ┌──────────────┐                  │           │
│        │                   │scanner(Python│──────────────────┤           │
│        │                   │client_id=103)│                  │           │
│        │                   └──────────────┘                  │           │
│        │                                                     │           │
│  ticks │  bars signals fills risk llm.* intel.* watchlist.*  │           │
│        ▼                                                     │           │
│  ┌─────────────────────────────────────────────────────────┐ │           │
│  │ NATS JetStream                                          │ │           │
│  └─────────┬────────────┬──────────────┬──────────────────┘ │           │
│            ▼            ▼              ▼                                 │
│       Prometheus   DuckDB+WAL    Grafana+Loki                            │
│                                        │                                 │
│                                  Telegram ◄──────────────────────────────┤
└──────────────────────────────────────────────────────────────────────────┘
```

**Why this kills V4's multi-engine/multi-port bug:** one host, one compose file, one message bus, schemas on the wire. No `watchlist.json`-vs-`contracts.toml` disconnect. No TCP-bridge reconnect fragility.

---

## PART 3 — SUBSYSTEMS & THE FIELD CONSUMPTION LEDGER

### 3.0 Field Consumption Ledger (NEW — solves the "paid-for IBKR data not used" problem)

Every IBKR tick field, every intel JSON key, every LLM output field, and every ML model output is registered in `docs/FIELD_CONSUMPTION_LEDGER.md`. Each row:

| Field | Producer | Schema | Consumed by | Metric that proves consumption | Status |
|---|---|---|---|---|---|

Unit tests (`tests/ledger_test.py`) parse the ledger and assert:
1. Every listed field appears in ≥ 1 strategy or risk-check file (AST grep).
2. Every listed field has an incrementing Prometheus metric.
3. Any field without a consumer triggers a CI FAIL.

This is the ledger that gets filled as each phase closes — when an LLM agent is wired, its output fields get ticked; when a strategy reads `opt_put_oi`, the tick appears against that row.

### 3.1 DATA PLANE (Pichai)
**Purpose** — one message bus; no filesystem source-of-truth.
**Files** — `infra/nats/jetstream.conf`, `rust_core/src/nats_client.rs`, `python_brain/core/nats_client.py`, `schemas/*.proto`.
**Invariants** — every message has `schema_version`; consumers fail CLOSED on unknown version.
**Consumers** — engine, brain, scanner, dashboard, watchdog.
**Health** — `/health/data` green iff every declared subject received a message within its SLA, DuckDB writable, no storage-soft-limit.
**Metrics** — `nats_messages_total{subject}`, `nats_consumer_lag{subject,consumer}`.
**Acceptance** — kill NATS; all apps reconnect without data loss; schema mismatch logs and skips, never crashes.

### 3.2 ENGINE CORE (Simons+Fink+Cifu — Rust)
**Purpose** — hot path, zero LLM dependency.
**Files** — `types.rs` (42-field MarketTick), `config.rs` (defaults→config→learned, bounds-gated), `bar_builder.rs` (5 TFs: 100 ms/1 m/5 m/15 m/1 h — ALL shipped), `indicators.rs` (RSI, ATR, VWAP, IBS, ADX, Hurst, EMA, **BB, Keltner, MACD** — ALL shipped), `ibkr_broker.rs`, `clock.rs`, `exchange_profile.rs`, `quant_core/{garch,garch_evt,kalman,regime,hayashi_yoshida}.rs`, `risk_arbiter.rs`, `exit_engine.rs` (5 methods: Chandelier, FixedDay, EventWindow, NextOpen, ProfitTarget — ALL shipped), `order_router.rs` (4 tiers), `broker_sync.rs`, `corporate_actions.rs`, `event_store.rs` (hash-chained, daily-rotated), `replay.rs`, `health.rs`, `metrics_export.rs`, `nats_client.rs`.
**Sim fills** — quadratic slippage, `k` calibrated from first 100 live fills (not hardcoded 100). Participation capped 10 % ADV.
**Health** — `/health/engine` tick age < 30 s, IBKR connected, WAL writable, KILL accessible.
**Metrics** — `ticks_received_total`, `bars_completed_total{tf}`, `risk_deltas_total{reason}`, `exits_total{method}`, `stp_updated_total`, `fills_total{algo}`, `slippage_bps{algo}`.
**Acceptance** — 24 h IBKR paper session: zero panics, STP on 100 % positions, broker_sync zero discrepancies.

### 3.3 BRAIN + STRATEGIES (Wood+Simons — Python)
**Strategy set** — 15–20 max at MVP. Launch with V3-validated 4 (sentiment PF=3.68, filing_change PF=2.49, index_recon PF=2.30, earnings_pattern PF=1.40) + V2-validated 5 (NAVArbitrage, IBS mean-reversion, FOMC drift, ETP lag, overnight return). Up to 10 more in `shadow` mode. One earnings file, not five.
**Files** — `strategies/base.py`, `strategies/registry.toml`, `strategies/<name>.py`, `conviction_engine.py` (**and `rank_signals()` is WIRED into `server.py`**), `portfolio_constructor.py`, `cost_governor.py`, `preference_logger.py` (**called on every signal AND every close**), `server.py`.
**Conviction engine** — dual-LLM ensemble (Haiku + Gemini Flash in parallel), JSON-schema structured outputs (`strict:true`), T=0, pinned model versions, circuit breaker 3-fails → 60-s fallback.
**Portfolio constructor** — ranks by `conviction × edge_estimate / risk`. Caps: per-strategy, per-ticker, per-sector, cross-correlation penalty, per-account (ISA/GIA/IG), Bayesian Kelly (EWMA last 60 trades).
**Health** — `/health/brain` ≥10 strategies enabled, LLM circuit closed, ranked signals flowing, preference-logger calls > 0 last hour.
**Metrics** — `signals_generated_total{strategy}`, `signals_ranked_top_n{strategy}`, `signals_rejected_by_floor{strategy}`, `llm_calls_total{provider,purpose}`, `llm_cost_usd_total`, `conviction_delta_vs_default{strategy}`.
**Acceptance** — every `SignalReceived` WAL event carries all 16 feature values + LLM structured-output JSON + conviction + rank + replayable. LLM down → strategy-default fallback with `llm_status=fallback` metric.

### 3.4 SCANNER + UNIVERSE (Pichai+Wood)
**Purpose** — delayed-data scanner (client_id=103) on 37K IBKR symbols → live watchlist (top 100) on NATS → engine hot-swaps subscriptions.
**Files** — `scanner/scanner.py`, `scanner/thompson.py` (40 dark-horse slots), `scanner/watchlist_publisher.py`.
**Invariants** — engine reads `watchlist.current` from NATS, not `contracts.toml`. Held positions ALWAYS in watchlist. Thompson Beta(α,β) posteriors persisted to DuckDB.
**Health** — `/health/scanner` delayed client connected, cycle completed within interval, watchlist published last 10 min.
**Acceptance** — start engine with empty watchlist → scanner publishes → engine subscribes within 30 s; held position preserved 5 min later.

### 3.5 LLM ARMY (Dario+Buckley — minimal, cold path)
**Rule** — every agent ships with an A/B harness proving delta-alpha or it gets deleted.
**Agents at MVP (5)** — `news_reactor` (Haiku, <1 s Benzinga classify → `intel.news`), `earnings_whisper` (nightly, Finnhub + IBKR WSH → `intel.earnings`), `sec_scanner` (Gemini 2.5 Pro 1 M on 10-K/10-Q → `intel.filings`), `regime_council` (3× Sonnet weekly → `intel.regime`), `thesis_monitor` (nightly invalidation scan → `intel.thesis`).
**Deferred to V5.1** — agent_swarm, catalyst_hunter, red_team, adversarial_review, social_scanner, path_signature, game_theory_crowding, ppo_agent, congressional_tracker. Re-enter only after alpha-gated A/B.
**Infra** — Langfuse trace: prompt, response, tokens, latency, cost, model version, `strategy_default` vs `llm_output` vs `realized_pnl`.
**Guardrails** — structured outputs, 500-char external-text limit + regex strip (prompt-injection), T=0, pinned versions, circuit breaker.
**Acceptance** — disable LLM entirely → every strategy fires at default conviction, no crash, Telegram warning.

### 3.6 AI-NATIVE MODELS (Simons+Dario)
**Rule** — **no ONNX model ships in V5 MVP.** Rule-based replacements: RSI/MACD momentum (in place of LNN), weighted L2 mid-price (in place of micro-price ONNX), softmax(rolling-Sharpe) (in place of DRL meta-allocator).
**Training gate for V5.1** — ≥ 10,000 real feature observations, hold-out test, DSR > 0 at N = total_strategy_count, model card approved.
**Runtime** — if/when added, Rust `ort` crate (not Python round-trip).

### 3.7 OUROBOROS + NIGHTLY PIPELINE (Simons+Cantrill)
**Files** — `ouroboros/core.py`, `kelly_bayesian.py`, `chandelier_calibrate.py`, `alpha_decay.py` (CUSUM + rolling-60 Sharpe), `drift.py` (ADWIN via River), `demote_resurrect.py` (14-day thresholds), `learned_writer.py` (with `bounds.toml` validation), `config/bounds.toml` (**exists Day 1**).
**12 steps** — (1) load WAL, (2) per-strategy MAE/MFE, (3) Kelly Bayesian update, (4) Chandelier ATR grid, (5) confidence-floor per regime, (6) alpha-decay CUSUM, (7) ADWIN drift, (8) auto-demote, (9) auto-resurrect, (10) bounds validate (fail-closed), (11) write `learned.toml`, (12) SIGHUP engine.
**Acceptance** — inject out-of-bounds value → refusal to write + alert.

### 3.8 OBSERVABILITY (Vogels)
**MVP dashboards (3)** — Engine Health, P&L Attribution (Brinson: beta/sector/momentum/reversal), LLM Cost.
**MVP alerts (3)** — engine crashed / no tick > 60 s, drawdown > 5 %, IBKR disconnected > 60 s.
**External watchdog** — Hetzner CX22 independent. Reads equity + health from NATS mirror. Can write KILL. Telegram alerts independently.
**Acceptance** — kill-main-host drill: watchdog alerts + KILL within 120 s.

### 3.9 GOVERNANCE (Cantrill)
**Files** — `docs/MODEL_INVENTORY.md`, `config/bounds.toml`, `docs/VALIDATION_REPORTS/<strategy>.md` (≥ 2 pages: hypothesis, data, methodology, results, limitations), `docs/CHANGELOG.md`, `scripts/dead_code_check.py` (CI), `docs/FIELD_CONSUMPTION_LEDGER.md`.
**Dead-code CI** — every module imported into hot or warm path must have a caller; every `@step` must run; every NATS subject must have a subscriber. Zero callers = CI FAIL.
**bounds.toml** — `kelly∈[0.02,0.30]`, `chandelier_atr_mult∈[1.5,3.0]`, `heat_limit∈[0.05,0.10]`, `confidence_floor∈[0.55,0.80]`, `regime_scale∈[0.25,1.0]`. Exists before any Ouroboros code.
**Acceptance** — `scripts/dead_code_check.py` exit 0 on CI. New module without caller fails build.

### 3.10 COST GOVERNANCE (Buckley)
**Rules** — hard-cap `$15/day` LLM in code. Phase 1 Haiku-only (`$5/day` max). Phase 2 add Gemini Flash if alpha confirmed. Phase 3 Sonnet/Opus cold path only.
**Return targets** — base 20-40 % net ISA, 50-100 % net blended Year 1. **Delete 2 %/day from every doc.**
**Acceptance** — simulate $20/day → governor halts at $15, alerts at $12, system continues on strategy defaults.

### 3.11 BROKER + OPS (Anthropic Engineers)
**Files** — `broker_sync.rs`, `corporate_actions.rs`, `order_book.rs` (order lifecycle), `ops/ig_financing.py`, `ops/isa_tax_year.py`.
**Rules** — reqPositions every 60 s paper AND live; 1-share mismatch = CRITICAL; stock split via >40 % gap + contractDetails change → flatten + manual review; IG overnight financing (LIBOR + 2.5 %); ISA reset 6 April; IBKR weekend 2FA recovery + Monday health check.
**Acceptance** — simulate NVDA 10:1 → detect, flatten, Telegram approval before reopen.

---

## PART 4 — CONTINUOUS PHASES (AGENT-PACED)

Phases are **continuous**. Each ends with a hard acceptance gate; the moment it passes, the next begins. **Phase 0 is different** — it is a big-bang scaffolding of **every single placeholder file in the repo**, so the shape of V5 exists from the start.

### PHASE 0 — SCAFFOLD EVERYTHING (placeholder for every file in the final system)

At the end of Phase 0, the entire V5 tree exists. Every file is either:
- **SCAFFOLD** — contains a module docstring, a TODO referencing the phase that fills it, a passing smoke test that imports it, and a Prometheus no-op counter so its metric exists.
- **READY** — filled and passes its acceptance test.

The tree:
```
aegis-v5/
├── docs/
│   ├── AEGIS_V5_MASTER_PLAN.md                  (this file)
│   ├── FIELD_CONSUMPTION_LEDGER.md              SCAFFOLD
│   ├── MODEL_INVENTORY.md                       SCAFFOLD
│   ├── CHANGELOG.md                             SCAFFOLD
│   └── VALIDATION_REPORTS/
│       ├── sentiment.md                         SCAFFOLD
│       ├── filing_change.md                     SCAFFOLD
│       ├── index_recon.md                       SCAFFOLD
│       ├── earnings_pattern.md                  SCAFFOLD
│       ├── nav_arbitrage.md                     SCAFFOLD
│       ├── ibs_mean_reversion.md                SCAFFOLD
│       ├── fomc_drift.md                        SCAFFOLD
│       ├── etp_lag.md                           SCAFFOLD
│       └── overnight_return.md                  SCAFFOLD
├── config/
│   ├── defaults.toml                            SCAFFOLD
│   ├── bounds.toml                              SCAFFOLD
│   └── learned.toml                             (generated — absent at start)
├── schemas/
│   ├── tick.proto                               SCAFFOLD
│   ├── bar.proto                                SCAFFOLD
│   ├── signal.proto                             SCAFFOLD
│   ├── risk.proto                               SCAFFOLD
│   ├── fill.proto                               SCAFFOLD
│   ├── llm_call.proto                           SCAFFOLD
│   ├── intel.proto                              SCAFFOLD
│   ├── watchlist.proto                          SCAFFOLD
│   ├── health.proto                             SCAFFOLD
│   ├── metric.proto                             SCAFFOLD
│   └── kill.proto                               SCAFFOLD
├── infra/
│   ├── docker-compose.yml                       SCAFFOLD (7 services)
│   ├── nats/jetstream.conf                      SCAFFOLD
│   ├── prometheus/prometheus.yml                SCAFFOLD
│   ├── grafana/dashboards/engine_health.json    SCAFFOLD
│   ├── grafana/dashboards/pnl_attribution.json  SCAFFOLD
│   ├── grafana/dashboards/llm_cost.json         SCAFFOLD
│   ├── loki/loki.yml                            SCAFFOLD
│   └── alerts/telegram.yml                      SCAFFOLD
├── rust_core/
│   └── src/
│       ├── main.rs                              SCAFFOLD
│       ├── nats_client.rs                       SCAFFOLD
│       ├── types.rs                             SCAFFOLD  (42-field MarketTick struct stub)
│       ├── config.rs                            SCAFFOLD
│       ├── clock.rs                             SCAFFOLD
│       ├── exchange_profile.rs                  SCAFFOLD
│       ├── ibkr_broker.rs                       SCAFFOLD
│       ├── bar_builder.rs                       SCAFFOLD  (5 TFs)
│       ├── indicators.rs                        SCAFFOLD  (10 indicators)
│       ├── quant_core/
│       │   ├── garch.rs                         SCAFFOLD
│       │   ├── garch_evt.rs                     SCAFFOLD
│       │   ├── kalman.rs                        SCAFFOLD
│       │   ├── regime.rs                        SCAFFOLD  (prob-vector output)
│       │   └── hayashi_yoshida.rs               SCAFFOLD
│       ├── risk_arbiter.rs                      SCAFFOLD  (16 weighted checks, no hard gates)
│       ├── exit_engine.rs                       SCAFFOLD  (5 methods)
│       ├── order_router.rs                      SCAFFOLD  (4 tiers)
│       ├── broker_sync.rs                       SCAFFOLD
│       ├── corporate_actions.rs                 SCAFFOLD
│       ├── order_book.rs                        SCAFFOLD
│       ├── event_store.rs                       SCAFFOLD  (hash-chained, daily rotation)
│       ├── replay.rs                            SCAFFOLD
│       ├── health.rs                            SCAFFOLD  (HTTP /health/*)
│       └── metrics_export.rs                    SCAFFOLD  (Prometheus HTTP)
├── python_brain/
│   ├── server.py                                SCAFFOLD
│   ├── core/
│   │   ├── nats_client.py                       SCAFFOLD
│   │   ├── data_health.py                       SCAFFOLD  (startup-blocking)
│   │   ├── cost_governor.py                     SCAFFOLD
│   │   └── preference_logger.py                 SCAFFOLD  (called on every signal + close)
│   ├── strategies/
│   │   ├── base.py                              SCAFFOLD
│   │   ├── registry.toml                        SCAFFOLD
│   │   ├── sentiment.py                         SCAFFOLD
│   │   ├── filing_change.py                     SCAFFOLD
│   │   ├── index_recon.py                       SCAFFOLD
│   │   ├── earnings_pattern.py                  SCAFFOLD
│   │   ├── nav_arbitrage.py                     SCAFFOLD
│   │   ├── ibs_mean_reversion.py                SCAFFOLD
│   │   ├── fomc_drift.py                        SCAFFOLD
│   │   ├── etp_lag.py                           SCAFFOLD
│   │   ├── overnight_return.py                  SCAFFOLD
│   │   └── shadow/                              (shadow strategies added later)
│   ├── conviction_engine.py                     SCAFFOLD  (with rank_signals WIRED)
│   ├── portfolio_constructor.py                 SCAFFOLD
│   ├── scanner/
│   │   ├── scanner.py                           SCAFFOLD  (client_id=103)
│   │   ├── thompson.py                          SCAFFOLD
│   │   └── watchlist_publisher.py               SCAFFOLD
│   ├── intelligence/
│   │   ├── news_reactor.py                      SCAFFOLD
│   │   ├── earnings_whisper.py                  SCAFFOLD
│   │   ├── sec_scanner.py                       SCAFFOLD
│   │   ├── regime_council.py                    SCAFFOLD
│   │   └── thesis_monitor.py                    SCAFFOLD
│   ├── ouroboros/
│   │   ├── core.py                              SCAFFOLD
│   │   ├── kelly_bayesian.py                    SCAFFOLD
│   │   ├── chandelier_calibrate.py              SCAFFOLD
│   │   ├── alpha_decay.py                       SCAFFOLD  (CUSUM)
│   │   ├── drift.py                             SCAFFOLD  (ADWIN)
│   │   ├── demote_resurrect.py                  SCAFFOLD
│   │   └── learned_writer.py                    SCAFFOLD
│   └── ops/
│       ├── ig_financing.py                      SCAFFOLD
│       └── isa_tax_year.py                      SCAFFOLD
├── scripts/
│   ├── dead_code_check.py                       SCAFFOLD  (CI gate)
│   ├── field_ledger_check.py                    SCAFFOLD  (CI gate)
│   ├── zero_trade_day_autodiag.py               SCAFFOLD
│   └── watchdog/                                SCAFFOLD  (Hetzner CX22 compose)
└── tests/
    ├── ledger_test.py                           SCAFFOLD
    ├── dead_code_test.py                        SCAFFOLD
    ├── acceptance/                              (per-persona acceptance tests)
    │   ├── fink.py                              SCAFFOLD
    │   ├── wood.py                              SCAFFOLD
    │   ├── simons.py                            SCAFFOLD
    │   ├── cifu.py                              SCAFFOLD
    │   ├── dario.py                             SCAFFOLD
    │   ├── pichai.py                            SCAFFOLD
    │   ├── vogels.py                            SCAFFOLD
    │   ├── cantrill.py                          SCAFFOLD
    │   ├── buckley.py                           SCAFFOLD
    │   ├── dalio.py                             SCAFFOLD
    │   └── anthropic_engineers.py               SCAFFOLD
    └── smoke/
        └── imports_test.py                      READY (imports every module)
```

**Phase 0 exit gate:**
- `cargo build` succeeds (all Rust scaffolds compile).
- `pytest tests/smoke/imports_test.py` passes.
- `scripts/dead_code_check.py` runs — reports every module as SCAFFOLD with its phase target (non-failing during Phase 0 only).
- `scripts/field_ledger_check.py` runs — prints 0 filled / N total.
- `docker compose up` brings all 7 services to healthy (NATS, Prometheus, Grafana, Loki all green; engine + brain + scanner running as SCAFFOLD emitting heartbeat only).
- 3 dashboards render on dummy data.

### PHASE 1 — DATA PLANE LIVE
Fill `nats_client.rs`, `nats_client.py`, every `schemas/*.proto`, `data_health.py`.
**Gate:** every subject has a producer + consumer; `/health/data` green; kill-NATS drill passes; field-ledger check shows every produced message's fields registered.

### PHASE 2A — ENGINE HOT PATH + DATASET CONTRACT LIVE (Rust) — **PAPER RESTART TRIGGER**
Fill `types.rs`, `config.rs`, `bar_builder.rs` (all 5 TFs), `indicators.rs` (all 10), `ibkr_broker.rs`, `event_store.rs` (WAL writes under the dataset contract in § PART -0.4), `health.rs`, `metrics_export.rs`, `risk_arbiter.rs` (weighted-only, no hard gates), `exit_engine.rs` (5 methods), `order_router.rs` (basic routing, 4-tier can come in P4), `broker_sync.rs`. 24-h IBKR paper smoke.
**Gate:** 24 h paper, zero panics, WAL rotates daily, every `SignalReceived` and `TradeClosed` event schema-validates against the dataset contract, every captured IBKR tick field has a reader per the ledger "consumed" definition. **On gate pass, continuous paper starts immediately — remaining phases fill in while paper runs.**

### PHASE 2B — QUANT CORE LIVE (Rust)
Fill `garch.rs` (wired to sizing), `garch_evt.rs`, `kalman.rs` (consumed in risk), `regime.rs` (probability vector), `hayashi_yoshida.rs`. Each quant output must have a consumer in risk or sizing before the sub-phase closes.
**Gate:** every QuantState output field is read by at least one risk check or sizing rule — proven by field-ledger test.

### PHASE 4 — RISK + EXIT + ORDER ROUTER LIVE (Rust)
Fill `risk_arbiter.rs` (16 weighted, no hard gates), `exit_engine.rs` (5 methods), `order_router.rs` (4 tiers), `broker_sync.rs`, `corporate_actions.rs`, `order_book.rs`.
**Gate:** server-side STPs on every open position; broker_sync zero discrepancies 24 h; 4-tier router used by each strategy per its config; TCA slippage metric populated.

### PHASE 5 — STRATEGIES LIVE (Python) — 6, not 9
Fill the **6 MVP strategies from § PART -0.3 4-R9**: sentiment_long_short, filing_change_detect, index_recon, earnings_pattern, overnight_return, ibs_mean_reversion. Wire `preference_logger` into every signal + close. 6 `VALIDATION_REPORTS/*.md` filled from V2/V3 evidence. All other strategy files live in `strategies/shadow/` and are NOT imported by the strategy registry until Gate 0–2 on V5 data proves edge.
**Gate (data-quality, not activity, per 4-R2):**
- Each strategy emits ≥ X signals/day (X per strategy's natural frequency).
- No signal has NaN, null, or constant features.
- ≥ 95 % of signals have valid fills and a recognised `exit_reason`.
- ≤ 5 % of signals rejected by data-health failures.
- Feature-vector entropy per strategy exceeds its threshold.
- Preference-logger call-count > 0 every hour.
- Zero zero-trade-days (any occurrence triggers `scripts/zero_trade_day_autodiag.py` → `.md` + `.json` incident + Telegram).

### PHASE 6 — CONVICTION ENGINE + PORTFOLIO CONSTRUCTOR LIVE (Python)
Fill `conviction_engine.py` with **`rank_signals()` actually called from `server.py`** (V4's dead-code bug), dual-LLM ensemble with structured outputs, Langfuse wired. Fill `portfolio_constructor.py` with Bayesian Kelly + ISA/GIA/IG split + ranked top-N allocation. Fill `cost_governor.py`.
**Gate:** every signal has a logged `strategy_default` vs `llm_output` vs `realized_pnl` triple; cost governor halts at `$15/day` in a drill; portfolio constructor rejects FIFO behaviour — top-N is enforced.

### PHASE 7 — LLM ARMY LIVE (5 agents only)
Fill `news_reactor.py`, `earnings_whisper.py`, `sec_scanner.py`, `regime_council.py`, `thesis_monitor.py`. Each produces a NATS `intel.*` subject. Each has a consumer strategy listed in the field ledger.
**Gate:** for each of the 5 agents, an A/B harness run over ≥ 200 signals shows **non-zero delta** in conviction or sizing. Zero delta → agent disabled until A/B evidence earned. *(This is the direct response to "what's the point of spending money on API calls if they don't impact trades.")*

### PHASE 8 — SCANNER + UNIVERSE LIVE
Fill `scanner.py` (client_id=103 delayed), `thompson.py` (40 dark-horse), `watchlist_publisher.py`. Engine hot-swaps from NATS `watchlist.current`.
**Gate:** watchlist churns daily; held positions preserved; engine receives new subscriptions within 30 s of publication; `contracts.toml` deleted.

### PHASE 9 — OUROBOROS LIVE
Fill 12 nightly steps. `learned.toml` validated against `bounds.toml`, SIGHUP delivered.
**Gate:** out-of-bounds injection → refusal + alert; alpha-decay CUSUM populates for every strategy; per-strategy Kelly/Chandelier/floor actually update from trade data.

### PHASE 10 — OBSERVABILITY + WATCHDOG LIVE
Fill 3 dashboards with live data, 3 alerts firing, external Hetzner CX22 watchdog deployed with NATS mirror.
**Gate:** kill-main-host drill: watchdog detects + alerts + KILL published within 120 s.

### PHASE 11 — ANTI-DEAD-CODE SWEEP
`scripts/dead_code_check.py` runs in **strict** mode (Phase 0 tolerance is removed). Any SCAFFOLD still unfilled is either completed, moved to `shadow/`, or deleted. Every row of `FIELD_CONSUMPTION_LEDGER.md` is green. Every deployed LLM agent has a measured A/B delta. Every deployed ML model has `production_accuracy ≥ training_accuracy * 0.7` or it is replaced with its rule-based fallback.
**Gate:** zero SCAFFOLD files remain; zero unfilled ledger rows; CI green; dead-code check exit 0.

### PHASE 12 — PAPER GRADUATION (60 days continuous paper)
Paper trade continuously. Daily `zero_trade_day_autodiag` runs at session close. Per-strategy graduation gates: 500+ trades, rolling Sharpe > 0.5, PF > 1.05, MDD < 2× backtest, DSR > 0 at N = total_strategy_count. **Additionally, per 4-R3: each graduating strategy must pass a stress-window replay (2020-03 or 2024-08 tick tape through `replay.rs` under the current config) without breaching `dd_black`.** Weekly persona check-in.
**Gate:** graduation criteria (incl. stress replay) met on at least 3 strategies AND 11/11 persona sign-off. Anything less → stay in paper.

### PHASE 13 — LIVE CAPITAL (50 % Kelly until first 500 live trades)
Small live allocation. All kill switches armed. Ouroboros still runs nightly. Weekly review.
**Gate (to full Kelly):** 500 live trades, live MDD < 2× backtest, persona sign-off re-confirmed.

---

## PART 5 — ZERO-TRADE-DAY AUTO-DIAGNOSTIC (Pichai + Vogels)

Because today a trading day was lost to zero signals, V5 bakes in an automatic root-cause pipeline that runs whenever a session closes with zero trades AND no market-closed reason:

1. **Detect** — at session close, the engine publishes `metric.trades_today = 0` if the count is zero and the exchange was open.
2. **Trigger** — `scripts/zero_trade_day_autodiag.py` runs automatically. No human action.
3. **Pipeline:**
   a. `data_health.py` — intel files fed? Schema-valid? Ages inside SLA?
   b. Field-ledger check — every consumed field produced tick data today?
   c. Risk-arbiter replay — run the WAL through the arbiter, tally the `confidence_delta` distribution per strategy; identify which check(s) pushed the most signals below the floor.
   d. Conviction-engine replay — per strategy, how many signals cleared `min_composite_score`?
   e. Portfolio constructor — any signals reached it? If yes, why weren't they ranked top-N?
   f. Broker sync — was IBKR connected all day? Any symbol unsubscribed?
4. **Output** — `docs/incidents/YYYY-MM-DD_zero_trade_day.md` populated from the above, Telegram alert with a summary, and a GitHub Issue opened with label `zero-trade-day`.
5. **Block** — next session cannot start until the incident doc has a root-cause and a commit referencing the issue closes it.

**This alone would have caught today.** V4 had no such mechanism — it silently skipped a day and the loss was not attributable until hand-investigation.

---

## PART 6 — MIGRATION FROM V4 (REUSE-WHERE-SAFE)

V5 reuses V4's working pieces, cuts the dead-code, replaces the brittle coupling:

| V4 component | V5 treatment |
|---|---|
| `rust_core/src/ibkr_broker.rs` | **Reuse** — 63-ticker streaming proven. |
| `rust_core/src/event_store.rs` | **Reuse + extend** — add daily rotation, schema version. |
| `rust_core/src/risk_arbiter.rs` | **Refactor** — strip all `Reject`, convert to `confidence_delta`. Keep 8-consec-loss halt. |
| `rust_core/src/exit_engine.rs` | **Finish** — implement the 3 stubbed methods. |
| `python_brain/strategies/` (50 files) | **Cut to 15–20.** Consolidate earnings. Shadow the rest. |
| `python_brain/conviction_engine.py` | **Reuse + WIRE** — call `rank_signals()` from `server.py`. |
| `python_brain/scanner/` | **Rewrite coupling** — output to NATS `watchlist.current`. |
| `python_brain/ouroboros/nightly_pipeline.py` (51 steps) | **Cut to 12.** Delete stubs. |
| `python_brain/intelligence/` (16 agents, 13 empty) | **Keep 5, move 11 to V5.1 alpha-gated.** |
| ONNX models | **Delete.** Replace with rule-based until real-data training. |
| `python_brain/core/data_health.py` | **Reuse + expand** into startup-blocking gate. |
| TCP bridge | **Replace with NATS.** |
| `contracts.toml` | **Delete.** Replace with NATS subject. |
| V4 dashboard HTML | **Replace with 3 Grafana dashboards.** |

---

## PART 7 — THE ANTI-PATTERNS V5 REFUSES

| V4 anti-pattern | V5 rule |
|---|---|
| "50 strategies, 7 firing" | 15-20 max; cut `return None` gauntlet; shadow until edge proven. |
| Synthetic ONNX models deployed | No model without 10K real observations + DSR > 0. |
| `ConvictionEngine.rank_signals()` never called | Dead-code CI fails the build. |
| Scanner writes JSON; engine reads TOML | One bus, schemas on the wire. |
| Market order for everything | 4-tier router in MVP. |
| No TCA | Slippage calibration blocks Phase-8 sign-off. |
| No server-side stops | Every open position gets an IBKR STP. |
| KILL path `/app/data/KILL` hardcoded | Env-configurable + external-watchdog KILL path. |
| Mac deployment | Hetzner CX32 + CX22. Mac dev-only. |
| No P&L attribution | Brinson in MVP dashboard. |
| `bounds.toml` absent | Exists Day 1, before Ouroboros code. |
| `preference_logger` never called | Every signal + every close calls it. Hourly alert if count=0. |
| LLMs unquantified cost | A/B delta logged every call. |
| Engine god object | Decomposed: TickProcessor, BarManager, QuantEngine, ExitMonitor, SignalProcessor, BridgeManager. |
| Pending-ticks unbounded | Bounded 1000, discard-oldest, metric. |
| WAL grows forever | Daily rotation, scan-last reads today only. |
| No schema versioning | Every message + every WAL event carries `schema_version`. |
| Zero-trade days go undiagnosed | `zero_trade_day_autodiag` runs automatically at session close. |
| Paid IBKR fields not read | Field Consumption Ledger + CI test. |

---

## PART 8 — PERSONA SIGN-OFF MATRIX (Phase 12 gate)

All 11 sign off, or V5 stays paper. Each sign-off gated by a measurable acceptance test from Part 3:

| # | Persona | Sign-off trigger |
|---|---|---|
| 1 | Fink | STP on 100 % open positions; 8-consec-loss halt tested; min £2K enforced |
| 2 | Wood | Portfolio ranks signals; ISA/GIA/IG split enforced; top-N operational |
| 3 | Simons | CUSUM alpha-decay live; DSR > 0 on ≥3 strategies; replay reproducible |
| 4 | Cifu | 4-tier router live; TCA slippage < 10 bps avg |
| 5 | Dario | Langfuse every call; structured-output parse > 99.9 %; A/B delta > 0 |
| 6 | Pichai | NATS sole bus; data-health green; WAL rotates daily; field-ledger 100 % |
| 7 | Vogels | 3 dashboards live; 3 alerts fire in test; kill-drill passes |
| 8 | Cantrill | Dead-code CI green; bounds.toml complete; every deployed model carded |
| 9 | Buckley | Cost-governor halt tested; phase-gated LLM rollout executed |
| 10 | Dalio | MC stress tests (2020-03, 2021-01, 2024-08) survived; 2× multiplier enforced |
| 11 | Anthropic Engineers | Broker reconcile zero discrepancies 7 days; corp-action test passes; 2FA Monday procedure in place |

**10/11 is not good enough.**

---

## APPENDIX A — TECHNOLOGY CHOICES (FINAL)

| Layer | Choice | Why |
|---|---|---|
| Bus | **NATS JetStream** | single binary, persistence, replay; V4 planned Kafka but never delivered |
| Hot path | **Rust (tokio)** | reuse V4 |
| Warm path | **Python 3.12 + asyncio** | reuse V4 |
| Broker | **IBKR (ibapi)** | already integrated; real data; £52/mo subs paid |
| Observability | **Prometheus + Grafana + Loki** | industry standard; alerts → Telegram |
| Storage | **DuckDB + Parquet** | embedded zero-ops; replaces V4's ArcticDB/Arrow-IPC that never shipped |
| LLM | **Anthropic Haiku/Sonnet + Gemini Flash/Pro** | dual-provider ensemble |
| Tracing | **Langfuse** | LLM observability; V4 planned, never delivered |
| ML runtime | **`ort` crate** | only when earned; not MVP |
| Deployment | **Docker Compose on Hetzner CX32 + CX22 failover** | V4 persona rule |
| CI | **GitHub Actions** | dead-code check + ledger check + cargo + pytest |

---

## APPENDIX B — DELETED FROM V4

- All three ONNX models
- 11 empty-output agents (re-enter V5.1 alpha-gated)
- 31+ never-fired strategies (moved to `shadow/`)
- `contracts.toml` as source of truth
- TCP bridge
- JSON-filesystem intel handshake
- "28/28 wired" / "50 strategies" marketing
- `2%/day` target from every doc

---

## APPENDIX C — REVIEW TRIAGE (Perplexity refinements, 2026-04-16)

External review scored the plan as "one of the best-structured rebuild specs realistically runnable at this scale" and suggested 13 refinements. Triage:

| # | Refinement | Status | Where it lives |
|---|---|---|---|
| 1 | Cap MVP strategies harder (5–6 serious) | **INTEGRATED** | § PART -0.3 4-R9 + Phase 5 rewrite |
| 2 | "No learned model at all" — stronger than "no ONNX" | **INTEGRATED** | § PART -0.5 table row "LLMs + ML" |
| 3 | Right-size observability for single-operator (defer Loki + external watchdog) | **INTEGRATED** | § PART -0.5 table row "Observability at MVP" — Loki/watchdog to P10/P10.1 |
| 4 | Tighten Field Consumption Ledger: "consumed" = decision-altering, not logged | **INTEGRATED** | § PART -0.3 4-R1 |
| 5 | Define dataset contract explicitly | **INTEGRATED** | § PART -0.4 (full schema) |
| 6 | Zero-trade-day autodiag outputs machine-readable JSON | **INTEGRATED** | § PART -0.3 4-R6 |
| 7 | Split Phase 2 → 2A (engine+WAL) / 2B (quant core) | **INTEGRATED** | Phase build-order + 4-R7 + Phase 2A now triggers paper restart |
| 8 | Phase 5 gate about data quality, not activity | **INTEGRATED** | Phase 5 rewrite + § PART -0.3 4-R2 |
| 9 | Phase 12 graduation requires stress-window replay | **INTEGRATED** | Phase 12 rewrite + 4-R3 |
| 10 | LLM Impact-Proof: concrete MDE per agent, bootstrap CI, regime strata | **INTEGRATED** | § PART -0.3 4-R4 (pre-registered metric + effect size table) |
| 11 | LLM forbidden zones (no raw numeric entry/exit) | **INTEGRATED** | § PART -0.3 4-R5 (bounded to [−30, +15] pp; no stop/size setters) |
| 12 | Timebox Phase 0 scaffolding (2–4 days, no polish) | **INTEGRATED** | § PART -0.3 4-R8 |
| 13 | Start paper as soon as Phase 2A passes, fill rest live | **INTEGRATED** | § PART -0.5 reframe + Phase 2A "PAPER RESTART TRIGGER" label |

Zero refinements rejected, zero deferred. The reframe (sim-lab producing Ouroboros-ready evidence, not "optimise live trading") is now the spine of the plan.

---

**END OF V5 MASTER PLAN**

*Authored by the 11-persona committee (Fink, Wood, Simons, Cifu, Dario, Pichai, Vogels, Cantrill, Buckley, Dalio, Anthropic Engineers) from the V4 adversarial review + V3 audit baseline. Refined 2026-04-16 with 13 external review points, simulation-first reframe, dataset contract, Part -1 enforcement layer.*
*Verdict: V5 is a simulation-first research lab that produces clean, labelled, Ouroboros-ready trades. Every scaffolded file is filled through gated phases. No piece ships unless it is consumed (per the strict ledger definition), measured, and changes a trade. Continuous paper starts at Phase 2A — not Phase 12.*
