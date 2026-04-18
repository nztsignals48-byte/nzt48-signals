# V5 Institutional-Plus Implementation Plan
## Keep All Strategies — Earn Every Tick

**Created:** 2026-04-18
**Directive:** Keep ALL 10 strategies running. Do not kill or demote any. Build the infrastructure that lets each one earn the ticks it's currently failing.
**Scope:** Flavour A (Statistical Rigour) + Flavour C (Research Accelerator).
**NOT in scope:** Flavour B (HFT/microstructure) — requires $2k+/mo infra you don't have.

---

## Philosophy change

Previous plan: "kill the ones that fail the gate."
New plan: **"build the gate, then let every strategy fight for each tick it's missing."**

This preserves:
- Signal diversity (10 strategies × 10 exchanges × multiple regimes)
- Optionality (a "retail-looking" strategy may surprise with real edge once gated properly)
- Data for meta-labeler training (losers + winners both useful)

**Every ❌ below becomes a concrete engineering task to flip it to ✅.**

---

## Part 1 — Current tournament state (keep all)

| Strategy | V3 G-2 | Cost-aware | DSR>0 | Meta-labelable | Queue-free | Survivor-safe |
|---|---|---|---|---|---|---|
| sentiment_long_short | ✅ PF=3.68 | ✅ | ❌ | ✅ | ✅ | ✅ |
| filing_change_detect | ✅ PF=2.49 | ✅ | ❌ | ✅ | ✅ | ✅ |
| index_recon | ✅ PF=2.30 | ✅ | ❌ | ✅ | ✅ | ✅ |
| earnings_pattern | ✅ PF=1.40 | ✅ | ❌ | ✅ | ❌ | ✅ |
| overnight_return | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ |
| ibs_mean_reversion | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| momentum_burst | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| scanner_momentum_fast | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| scanner_momentum_fast_inverse | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| news_alpha_trader | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ |

**Total missing ticks: 38.** Every one gets a concrete path below.

---

## Part 2 — How each ❌ becomes a ✅

### Tick group A: V3 Gate-2 validation (6 strategies missing)

**What V3 Gate-2 was:** backtest with real IBKR spreads + real slippage on known-edge test set. 4 strategies passed; 6 haven't been tested.

**Path to ✅ for all 6:**

Build `scripts/v3_gate2_replay.py` — a standardized backtest harness that:
- Replays the live `data/archive/scanner_hits_*.jsonl` and `ticks.live.*` archives
- Applies real IBKR commission ($1/trade US, £3 UK) + real spread (bid-ask at entry/exit)
- Outputs a Gate-2-style report: PF, Sharpe, hit-rate, max-DD, win-loss-ratio

Every strategy runs through this harness weekly. **Tick earned when PF > 1.05 with trade count ≥ 100 on real-cost replay.**

| Strategy | Concrete task | Expected timeline to tick |
|---|---|---|
| overnight_return | Run replay on last 30 days of equity data | Days |
| ibs_mean_reversion | Replay with per-ticker bid-ask spread from ticks.live | Days |
| momentum_burst | Replay on past 90 days; if PF>1.05 it earns tick | Days |
| scanner_momentum_fast | Replay with slippage adjustment for small-caps (3-10bps extra) | Week |
| scanner_momentum_fast_inverse | Replay with overnight decay haircut | Week |
| news_alpha_trader | Replay LLM-derived signals against actual forward returns | 2 weeks (need data) |

**Files to build:**
- `scripts/v3_gate2_replay.py`
- `python_brain/backtest/cost_model.py` (real IBKR cost structure per exchange)
- `python_brain/backtest/slippage_model.py` (bid-ask spread + size impact)

### Tick group B: Cost-aware (5 strategies missing)

These strategies fire signals without subtracting real trading costs from expected edge.

**Path to ✅:** Make cost-awareness mandatory in every `StrategyView` output:
- Every strategy must populate `cost_bps_estimate` and `net_edge_bps = edge_bps - cost_bps_estimate`
- If `net_edge_bps <= 0`, strategy is not allowed to publish the signal
- Base class `Strategy` gets a `_compute_cost_bps(ctx)` helper using exchange commission table + spread

**Files to modify:**
- `python_brain/strategies/base.py` — add `cost_bps_estimate` + `net_edge_bps` to `StrategyView`
- `python_brain/strategies/momentum_burst.py` — add `_compute_cost_bps` call
- `python_brain/strategies/overnight_return.py` — same
- `python_brain/strategies/ibs_mean_reversion.py` — same
- `python_brain/strategies/scanner_momentum_fast.py` — hardest: include estimated small-cap slippage
- `python_brain/strategies/news_alpha_trader.py` — demote to advisor first, then cost-aware re-entry

**Tick earned when** strategy can produce signals with verified `net_edge_bps > 0` across ≥ 50 forward-tested signals.

### Tick group C: Deflated Sharpe Ratio > 0 (10 strategies missing — ALL)

Currently NO strategy has a DSR computation at all.

**Path to ✅ for all 10:**

Build the DSR pipeline:
- New file: `python_brain/ouroboros/deflated_sharpe.py`
- Function: `deflated_sharpe(returns, trials, skew, kurt) -> float`
- Nightly `ouroboros_v2_nightly.py` computes DSR per strategy using trade returns from `data/fills/realised_*.jsonl`
- Output: `data/strategy_dsr.json` with `{strategy: {dsr, trials, n_trades, last_updated}}`

Track trial count properly:
- Every parameter tweak in `config/learned.toml` increments trial count in `data/strategy_trials.json`
- DSR formula: deflate observed Sharpe by # of trials attempted

**Tick earned** when a strategy has:
- ≥ 100 real trades
- DSR > 0

For strategies without 100 trades yet, keep firing but status = "pre-DSR". Dashboard shows DSR as "pending: X/100 trades".

### Tick group D: Meta-labelable (4 strategies missing)

Strategies that produce signals but without structured features that a meta-labeler can train on.

**Path to ✅:** Mandate feature schema in every `StrategyView.features` dict. Required keys:
```
{
  "regime": str,       # from BOCPD
  "vix": float,
  "time_of_day_bucket": str,
  "spread_bps": float,
  "ticker_realized_vol": float,
  "primary_conviction": float,
  "recent_win_rate": float,  # strategy-level rolling
}
```

**Files to modify:**
- `python_brain/strategies/base.py` — `StrategyView` validates feature schema
- All 10 strategy files — ensure they populate these keys
- The 4 currently-failing strategies (momentum_burst, scanner_momentum_fast, scanner_momentum_fast_inverse — each needs features added; news_alpha_trader needs advisor-mode feature set)

**Tick earned** when strategy's signals all carry the required feature set + meta_labeler has ≥500 samples.

### Tick group E: Queue-free (3 strategies missing)

Strategies whose profitability depends on getting queue position (sub-20bps edge that disappears at back of queue).

**Path to ✅:** Two options per strategy:
1. **Switch to liquid large-caps only** → eliminates queue dependency
2. **Add queue-aware validation** → replay through hftbacktest to verify edge survives

For earnings_pattern + ibs_mean_reversion + scanner_momentum_fast — all can use option 1 (restrict to liquid tickers where queue isn't decisive). Scanner_momentum_fast_inverse is hardest (small-cap leveraged ETPs have wide spreads).

**Files to modify:**
- Each strategy gets a `min_adv_usd` filter (average daily volume) and `max_spread_bps` filter
- Config: `config/strategy_liquidity_floors.toml`

**Tick earned** when strategy only fires on tickers passing liquidity floors OR hftbacktest replay shows profit after queue loss.

### Tick group F: Survivorship-safe (5 strategies missing)

Strategies whose backtest universe comes from today's scanner (biased toward winners that didn't get delisted).

**Path to ✅:** Point-in-time universe construction:
- Nightly snapshot of `contracts.toml` into `data/pit_universe/YYYY-MM-DD.toml`
- Backtest reads historical PIT universes instead of today's
- Strategies that depend on scanner must weight by historical survival rate of their picks

**Files to build:**
- `scripts/snapshot_pit_universe.py` (nightly)
- `python_brain/backtest/pit_loader.py`

**Tick earned** when strategy backtest uses PIT universe with ≥ 90-day history AND shows comparable Sharpe to today's-universe backtest.

---

## Part 3 — Institutional infrastructure (all 10 strategies benefit)

These are built once and apply to every strategy regardless of its current tick count.

### Infra 1 — DSR gate (`deflated_sharpe.py`)
All 10 strategies get a DSR computation nightly. Below threshold strategies stay alive but don't get capital boost.

### Infra 2 — CPCV harness (`cpcv_harness.py`)
Every strategy gets validated via combinatorial-purged k-fold weekly.

### Infra 3 — Meta-labeler (`meta_labeler.py`)
Subscribes to `signals.core`, filters through LightGBM, publishes `signals.validated`. All strategies feed into it; meta-labeler decides which make it to execution.

### Infra 4 — BOCPD regime detector (`bocpd_regime.py`)
Replaces ad-hoc `regime_amplifier`. Publishes `regime.bocpd`. Every strategy's feature dict pulls regime from this.

### Infra 5 — Weekly PnL clustering (`weekly_pnl_cluster.py`)
Runs Sunday. If two strategies have > 0.7 correlation, they're BOTH kept but flagged for merger analysis. Keeps option to consolidate later.

### Infra 6 — Conformal stops (`conformal_stops.py`)
Wraps Chandelier v4. All position exits use conformal intervals regardless of which strategy opened the position.

### Infra 7 — Thompson-sampling capital allocation (`capital_bandit.py`)
Per-strategy Beta(wins, losses) posterior. All 10 strategies compete for capital. Bad ones naturally get smaller sizes; good ones grow. **This is the key mechanism that lets us keep all 10 without manually killing any** — the bandit auto-starves losers while we keep the code alive.

### Infra 8 — LLM research accelerator (`llm_research_agent.py`)
Reads fills + regime + news. Proposes parameter tweaks + new strategy variants. Every proposal runs through DSR + CPCV before live promotion. Keeps LLM in Man Group / Two Sigma lane (research, not decisions).

---

## Part 4 — The big shift: Thompson bandit replaces "kill/keep"

Instead of binary kill/keep decisions, **capital_bandit** makes every strategy continuously earn its allocation.

**How it works:**
```
every N trades per strategy:
    alpha = wins, beta = losses
    kelly_mult[strategy] = sample from Beta(alpha+1, beta+1)
    size = base_kelly × kelly_mult × meta_labeler_prob
```

A strategy that loses 20 in a row gets near-zero kelly_mult automatically. A strategy that wins after being starved recovers. Nothing is manually killed.

This is **the mechanism that makes "keep all strategies" institutional-grade**: the system self-regulates.

---

## Part 5 — File manifest

### New files (11)
```
python_brain/ouroboros/deflated_sharpe.py          # DSR gate
python_brain/backtest/cpcv_harness.py              # CPCV validation
python_brain/backtest/cost_model.py                # IBKR commission + spread model
python_brain/backtest/slippage_model.py            # Bid-ask + size impact
python_brain/backtest/pit_loader.py                # Point-in-time universe reader
python_brain/engine/meta_labeler.py                # LightGBM signal filter
python_brain/engine/conformal_stops.py             # MAPIE exit bands
python_brain/engine/capital_bandit.py              # Thompson-sampled Kelly per strategy
python_brain/quant/bocpd_regime.py                 # Bayesian regime detector
python_brain/research/llm_research_agent.py        # LLM hypothesis funnel
scripts/v3_gate2_replay.py                         # Standardized backtest harness
scripts/weekly_pnl_cluster.py                      # Correlation monitoring
scripts/snapshot_pit_universe.py                   # PIT universe snapshotter
scripts/train_meta_labeler.py                      # Nightly retrain
scripts/promote_strategy.py                        # DSR + CPCV gate enforcer
```

### Modified files (8)
```
python_brain/strategies/base.py                    # Feature schema + cost_bps requirement
python_brain/strategies/momentum_burst.py          # cost_bps_estimate, feature dict
python_brain/strategies/overnight_return.py        # same
python_brain/strategies/ibs_mean_reversion.py      # same
python_brain/strategies/scanner_momentum_fast.py   # slippage aware
python_brain/strategies/news_alpha_trader.py       # advisor mode + feature dict
python_brain/engine/signal_to_order_bridge.py      # consume signals.validated, use bandit
scripts/ouroboros_v2_nightly.py                    # DSR + CPCV integration
scripts/v5_supervisor_v2.py                        # register new services
```

### Deleted: **NONE.** Everything stays.

---

## Part 6 — Implementation phases (no strategies killed)

### Phase 0 — Foundation (Day 1-2)
- Build DSR + cost model + slippage model
- Snapshot PIT universe
- Run v3_gate2_replay once for all 10 strategies
- **Outcome:** Every strategy has a DSR estimate and Gate-2 replay report. No strategy is killed even if it fails — it just carries a visible DSR score.

### Phase 1 — Gate-aware sizing (Week 1)
- Build capital_bandit with Thompson sampling
- Integrate into sig2order
- Every strategy's Kelly is now proportional to its DSR + recent win rate
- **Outcome:** Bad strategies naturally starve; no manual demotion needed. Retail strategies become "tiny-size strategies" until they prove themselves.

### Phase 2 — Meta-labeling (Week 2)
- Build meta_labeler with LightGBM
- Feature-schema enforcement on all strategies
- sig2order consumes `signals.validated` from meta_labeler
- **Outcome:** Every signal validated by a learned filter before execution. Retail-looking strategies can still fire but most signals get meta-filtered out.

### Phase 3 — Regime + clustering (Week 2-3)
- BOCPD replacing regime_amplifier
- Weekly correlation cluster report
- **Outcome:** Fake diversification visible. Capital bandit penalizes correlated winners.

### Phase 4 — Conformal stops (Week 3)
- Wrap Chandelier v4 in conformal intervals
- **Outcome:** Stops asymmetric to uncertainty, capture-ratio improves.

### Phase 5 — LLM research agent (Week 4)
- Build llm_research_agent
- Version-control prompts
- **Outcome:** Nightly proposal reports in `docs/research_proposals/`. Human review gate before any live promotion.

### Phase 6 — Monitoring (Week 4)
- Dashboards: DSR per strategy, capture-ratio, correlation matrix, regime state
- Daily backtest-vs-live slippage report
- **Outcome:** Every strategy's health visible at a glance.

---

## Part 7 — Tick earn-back path (concrete 4-week roadmap)

### Week 1 — foundations land
- Every strategy gets DSR score (tick G-C)
- Every strategy has cost-aware signal output (tick G-B)
- v3_gate2_replay runs (tick G-A for all 10)

**End of week 1:** ~20 ticks earned across the 38 missing.

### Week 2 — signal discipline
- Meta-labeler filters all signals (tick G-D for all 10)
- BOCPD publishes regime
- Strategies feed regime into features

**End of week 2:** ~30 ticks earned.

### Week 3 — execution discipline
- Conformal stops live
- Capital bandit rebalances every hour
- PIT universe snapshots (tick G-F)

**End of week 3:** ~35 ticks earned.

### Week 4 — research & monitoring
- LLM research agent proposes improvements
- Dashboards complete
- Queue-aware replay for remaining strategies (tick G-E)

**End of week 4:** all 38 ticks earned OR strategy naturally starved by bandit.

**Final state:** All 10 strategies alive. All 38 ticks accounted for. Bandit + meta-labeler + DSR gate ensure only strategies with genuine edge get meaningful capital. Retail-looking strategies survive on tiny allocations until they either prove themselves or fade to near-zero kelly_mult.

---

## Part 8 — Acceptance criteria

Plan complete when:

1. ✅ All 10 strategies still in `STRATEGY_CLASSES`
2. ✅ Every strategy has current DSR score in `data/strategy_dsr.json`
3. ✅ Every strategy has current Gate-2 replay report
4. ✅ sig2order consumes `signals.validated` (meta-labeled) not raw `signals.core`
5. ✅ capital_bandit allocates Kelly per strategy (no fixed 0.20)
6. ✅ Conformal intervals wrap every Chandelier exit
7. ✅ BOCPD publishes `regime.bocpd` every 30s
8. ✅ Weekly correlation cluster report in `docs/diversification_reports/`
9. ✅ LLM research agent running nightly, proposals in `docs/research_proposals/`
10. ✅ PIT universe snapshots daily
11. ✅ Grafana shows DSR, capture-ratio, correlation matrix, regime per strategy
12. ✅ Daily backtest-vs-live slippage report

---

## Part 9 — What we're NOT promising

- Won't beat HRT on latency (no infra for that)
- Won't generate novel alpha via LLM (LLM is research assistant only)
- Won't make any strategy profitable that genuinely has no edge — but bandit + meta-labeler make losers cost near-zero
- Won't eliminate all overfitting — just bound it via CPCV + DSR

**What we WILL deliver:** every strategy earns its capital allocation trade-by-trade. Retail-looking strategies survive on crumbs until they earn bigger slices. Institutional-grade patterns applied to ALL of them, not just to the V3-validated subset.

---

**Plan written. No code executed. Review and say which phase to start.**
