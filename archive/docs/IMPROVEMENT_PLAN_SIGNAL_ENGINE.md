# NZT-48 Signal Engine Improvement Plan

**Phase 3 Institutional Audit -- Improvement Plan**
**Date**: 2026-02-27
**Scope**: CORE "max intraday gains" objective + non-core 2% framing + ranked improvements
**Status**: PLAN ONLY -- no code changes

---

## Table of Contents

1. [CORE Objective: MAX_INTRADAY_GAINS](#1-core-objective-max_intraday_gains)
2. [Non-Core: 2% Framing Preservation](#2-non-core-2-framing-preservation)
3. [P0 -- Critical (Fix Immediately)](#3-p0--critical)
4. [P1 -- High Priority (Fix This Sprint)](#4-p1--high-priority)
5. [P2 -- Medium Priority (Backlog)](#5-p2--medium-priority)
6. [Implementation Sequence](#6-implementation-sequence)

---

## 1. CORE Objective: MAX_INTRADAY_GAINS

The 12 CORE tickers (QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L) must be optimized for **maximum intraday gains**, not capped at 2%.

### 1.1 Target Logic Changes

**Current state**: Fixed 2% target in S15 (`_DAILY_TARGET_PCT = 2.0`). Signal Engine uses fixed ATR-fraction targets: `t1_dist = max(stop_dist * 2.0, ATR * 1.0)` and `t2_dist = stop_dist * 2.5`.

**Proposed changes**:

| Component | Current | Proposed | Rationale |
|-----------|---------|----------|-----------|
| Target1 (primary) | 2% or ATR-based fixed | `max(ATR * 1.5, close * 0.02)` with regime scaling: x1.5 in TRENDING_UP_STRONG, x1.0 in RANGE_BOUND, x0.8 in HIGH_VOLATILITY | Leveraged ETPs in strong trends routinely move 5-8% intraday; a 2% cap leaves money on the table |
| Target2 (runner) | `stop_dist * 2.5` | `ATR * 3.0` with trailing stop activation at T1 | Runners on 3x/5x ETPs can produce 10%+ moves |
| Time stop | Not implemented in engine | Add time-based exit: SCALP=15min, SWING=3h from entry | Prevents holding dead positions that tie up capital |
| Session close exit | Mentioned in cancel_conditions but not enforced | Enforce flat-by-close for all intraday positions | ISA leveraged ETPs have overnight decay risk |

**Acceptance tests**:
- `test_core_target1_scales_with_regime_strength`
- `test_core_target2_uses_atr_multiplier_not_fixed_stop_multiple`
- `test_time_stop_fires_after_configured_minutes`
- `test_flat_by_close_enforced_for_core_intraday`

### 1.2 Trailing Stop Logic

**Current state**: Fixed stop at entry. No trailing stop mechanism in Signal Engine.

**Proposed**: Three-phase trailing stop for CORE tickers:

| Phase | Trigger | Stop Behavior |
|-------|---------|---------------|
| Phase 1: Initial | Entry to +0.5R | Fixed stop (entry - ATR_frac) |
| Phase 2: Protect | +0.5R to +1.0R | Trail to breakeven |
| Phase 3: Lock profit | +1.0R onwards | Trail at entry + 50% of unrealized gain |

**Acceptance tests**:
- `test_trailing_stop_moves_to_breakeven_at_half_r`
- `test_trailing_stop_locks_50pct_unrealized_above_1r`
- `test_trailing_stop_never_moves_against_position`

### 1.3 Partial Exits

**Current state**: SignalCard has `partial_at` field (set to +1R) but no partial execution logic in Signal Engine.

**Proposed**:

| Exit | Trigger | Size |
|------|---------|------|
| Partial 1 | +1R (T1 hit) | Exit 33% of position |
| Partial 2 | +2R | Exit 33% of position |
| Runner | Trailing stop hit | Exit remaining 34% |

**Acceptance tests**:
- `test_partial_exit_at_t1_closes_33pct`
- `test_partial_exit_at_t2_closes_33pct`
- `test_runner_exits_on_trailing_stop`
- `test_partial_exits_sum_to_100pct`

### 1.4 Reporting Changes for CORE

**Current state**: All tickers reported uniformly with composite score, star rating, and fixed R:R.

**Proposed CORE-specific reporting**:
- **Intraday P&L attribution**: Show how much of the day's move was captured vs left on table
- **MFE/MAE tracking**: Real-time max favorable/adverse excursion per signal
- **Trailing stop visualization**: Show trailing stop position relative to price in PDF
- **Daily compounding tracker**: Running total of daily captures vs 2% target, showing cumulative compound effect
- **Best-case scenario**: If trailing stop was perfect, what would the capture have been?

**Acceptance tests**:
- `test_core_report_shows_mfe_mae_for_each_signal`
- `test_daily_compounding_tracker_updates_after_each_outcome`
- `test_core_report_shows_trailing_stop_path`

---

## 2. Non-Core: 2% Framing Preservation

Non-CORE tickers (PEER, FULL_SCAN, INTEL) must remain capped at 2% framing. These serve as intel-only context instruments.

### 2.1 Non-Core Tickers

- PEER tier: AMD3.L, ARM3.L, NVDS.L, TSLS.L, 3LDE.L, 3LEU.L, 3GOL.L, 3SIL.L, 3OIL.L, LLY3.L
- FULL_SCAN tier: QQQ, SPY, SMH, SOXX, ^VIX, TLT, GLD, USO, DX-Y.NYB, NVDA, TSLA, TSM, MU, AMD
- INTEL cards only -- never produce TRADE signals

### 2.2 How Non-Core Stays Capped

| Component | Non-Core Behavior |
|-----------|-------------------|
| Target | Fixed at 2% from entry (current S15 logic) |
| Stop | 1x ATR (current) |
| Trailing stop | Not applied |
| Partial exits | Not applied |
| Label | "PEER" or "FULL_SCAN" or "INTEL-ONLY" |
| Reporting | 2% reachability score, daily range coverage |
| Signal category | WATCH or INTEL (never TRADE) |

### 2.3 Tier Separation Enforcement

**Current state**: Tiered pipeline tags plays with `tier` attribute, but no hard enforcement prevents PEER/FULL_SCAN from appearing as TRADE.

**Proposed**: Add tier-gated enforcement:
- `if play.tier != "CORE": play.category = "WATCH"` -- enforced in signal_card.py
- PDF: Separate sections for CORE (actionable) vs PEER/INTEL (context)
- Telegram: CORE signals marked TRADE, non-core marked INTEL

**Acceptance tests**:
- `test_peer_play_always_categorized_as_watch`
- `test_full_scan_never_produces_trade_category`
- `test_intel_card_category_is_never_trade`
- `test_telegram_tags_core_as_trade_and_noncore_as_intel`

---

## 3. P0 -- Critical

### P0-1: RiskOfficer Receives Hardcoded Context
**Location**: `signal_engine/engine.py` L403
**Issue**: VIX hardcoded to 18.0, consecutive_losses hardcoded to 0. VOL_SHOCK and DRAWDOWN rules are non-functional in Signal Engine path.
**Impact**: Risk governance is decorative. In a VIX spike (35+), the Signal Engine would still emit full-size TRADE signals.
**Fix plan**:
1. Pass live VIX from market data (RegimeClassifier or direct yfinance fetch of ^VIX)
2. Pass consecutive_losses from database query (same as main.py L316-335)
3. Pass daily_loss_pct from virtual_trader equity tracking
**Acceptance tests**:
- `test_risk_officer_receives_live_vix_not_hardcoded_18`
- `test_risk_officer_receives_real_consecutive_losses`
- `test_vol_shock_veto_fires_when_vix_above_35_and_atr_above_3_5`
- `test_drawdown_veto_fires_when_5_consecutive_losses`

### P0-2: Signal Engine Defaults Regime to NEUTRAL
**Location**: `signal_engine/pipeline_runner.py` L78
**Issue**: `run_pipeline()` defaults `regime="NEUTRAL"`. If caller does not pass actual regime, all signals scored against NEUTRAL (regime_fit = 0.60). In a SHOCK regime, signals should be suppressed, not scored at 0.60.
**Impact**: Regime-based risk management is bypassed. SHOCK regime produces same scores as NEUTRAL.
**Fix plan**:
1. Add regime computation inside `run_pipeline()` using RegimeClassifier
2. Or: require regime as mandatory parameter (remove default)
3. Log WARNING if regime is "NEUTRAL" and market is open (likely indicates default was used)
**Acceptance tests**:
- `test_pipeline_runner_computes_regime_if_not_provided`
- `test_warning_logged_when_neutral_regime_during_market_hours`
- `test_shock_regime_suppresses_signals_via_kill_switch`

### P0-3: Signal Engine Uses Direct yfinance, Bypasses DataHub
**Location**: `signal_engine/engine.py` L553-560
**Issue**: `_build_features()` calls `yf.download()` directly instead of using DataHub. This bypasses pence normalization, validator comparison, and DataReliabilityScore computation from DataHub.
**Impact**: Signal Engine may receive un-normalized pence prices for .L tickers (though DataHealthGate catches most cases). No cross-vendor validation.
**Fix plan**:
1. Replace `yf.download()` call with `DataHub().get_bars()`
2. Use `BarResult.reliability` for data_reliability scoring
3. Use `BarResult.pence_adjusted` flag for audit trail
**Acceptance tests**:
- `test_engine_uses_datahub_for_bar_fetch`
- `test_datahub_pence_normalization_applied_before_feature_computation`
- `test_datahub_reliability_score_flows_to_ticker_features`

### P0-4: Silent Quality Gate Failures Pass Signals
**Location**: Multiple try/except blocks in `main.py` L806-999
**Issue**: Confluence scorer, portfolio risk manager, dynamic sizer, and smart router are all wrapped in try/except that catch any exception and continue. A broken risk gate silently passes signals without that check.
**Impact**: A bug in portfolio_risk.py (for example) would silently disable concentration limits, potentially allowing 100% of equity in one factor group.
**Fix plan**:
1. Classify governance gates as HARD or SOFT
2. HARD gate failure (portfolio risk, immutable rules, firewall) -> signal rejected with GATE_ERROR reason
3. SOFT gate failure (confluence, smart router, edge decay) -> signal proceeds with warning
4. All gate failures produce structured warning artifact + telegram alert
**Acceptance tests**:
- `test_portfolio_risk_exception_rejects_signal_not_passes_it`
- `test_immutable_rules_exception_rejects_signal`
- `test_soft_gate_failure_produces_warning_artifact`
- `test_telegram_alert_sent_on_hard_gate_failure`

### P0-5: No Outcome Resolution Scheduler
**Location**: `learning/outcomes_engine.py` exists but no scheduler invocation found
**Issue**: The OutcomesEngine is defined but appears to have no scheduled invocation. Signals stay PENDING indefinitely. Edge ledger receives no outcome data. Learning loop cannot learn.
**Impact**: The entire learning loop (edge ledger, strategy tournament, drift detection) operates on no data. All learning adjustments are based on empty history.
**Fix plan**:
1. Add APScheduler job in main.py to call `OutcomesEngine.resolve_pending()` every 15 minutes during market hours
2. Add EOD batch resolution job at 22:00 UK
3. Log outcome resolution counts per run
**Acceptance tests**:
- `test_outcomes_engine_invoked_every_15_minutes_during_market_hours`
- `test_outcomes_engine_batch_resolution_at_eod`
- `test_pending_signals_resolved_within_24_hours`

### P0-6: Feature Computation Not Unit-Testable
**Location**: `signal_engine/engine.py` L541-682
**Issue**: All indicator computation (ATR, RSI, MACD, ADX, EMA, BB) is inside one 140-line method. No individual functions are testable in isolation.
**Impact**: Cannot verify indicator correctness against reference implementations. A bug in RSI computation would silently produce wrong scores for every ticker.
**Fix plan**:
1. Extract each indicator into a pure function: `compute_atr(highs, lows, closes, window)`, etc.
2. Move to `signal_engine/indicators.py` or use existing `feeds/indicators.py`
3. Add reference test cases from known data series
**Acceptance tests**:
- `test_compute_atr_matches_reference_for_spy_daily`
- `test_compute_rsi_matches_reference_for_known_series`
- `test_compute_adx_returns_zero_for_flat_series`
- `test_compute_macd_hist_sign_matches_trend_direction`

### P0-7: Two Independent Pipelines With No Risk Parity
**Location**: `main.py` (Pathway A) vs `signal_engine/engine.py` (Pathway B)
**Issue**: The Signal Engine path has 6 RiskOfficer rules. The legacy path has 18+ qualification gates (including emotional firewall, portfolio risk, confluence, etc.). A signal VETOed in Pathway A could appear as a top-rated TRADE in Pathway B's PDF.
**Impact**: PDF consumers receive signals that would be rejected by the system's own risk governance.
**Fix plan**:
1. Create `qualification/unified_gate.py` that applies the union of both pathways' critical gates
2. Signal Engine must apply: portfolio risk check, drawdown check (from DB), session protection, and immutable rules before emitting TRADE signals
3. Legacy path gates that are not in Signal Engine: confluence, emotional firewall, tournament -- these should be available as optional enrichment
**Acceptance tests**:
- `test_signal_engine_applies_portfolio_risk_gate`
- `test_signal_engine_applies_drawdown_gate_from_db`
- `test_signal_engine_applies_session_protection`
- `test_no_signal_vetoed_in_pathway_a_appears_as_trade_in_pathway_b`

### P0-8: Edge Ledger Not Wired to Signal Engine
**Location**: `learning/edge_ledger.py` (exists), `signal_engine/engine.py` (no reference)
**Issue**: Edge ledger computes per-bucket win rate and expectancy, but Signal Engine does not consult it. Signals are scored without historical edge data.
**Impact**: The system cannot learn from its own history. A strategy with 20% win rate in RANGE_BOUND regime continues to produce signals at the same score as a strategy with 80% win rate.
**Fix plan**:
1. Load edge ledger at engine initialization
2. Before scoring, check bucket for this signal's (strategy_tag, regime, track, time_window)
3. If bucket status = ACTIONABLE: apply expectancy-weighted adjustment to composite score
4. If bucket status = NEEDS_DATA: flag signal as UNVALIDATED
5. If expectancy < 0 and confidence > 80%: apply -10% score penalty
**Acceptance tests**:
- `test_engine_loads_edge_ledger_on_init`
- `test_negative_expectancy_bucket_applies_score_penalty`
- `test_actionable_bucket_boosts_score_by_expectancy`
- `test_needs_data_bucket_flags_signal_as_unvalidated`

---

## 4. P1 -- High Priority

### P1-1: No Data Retry on yfinance Failure
**Issue**: A single yfinance timeout kills the ticker for the entire session. No retry logic.
**Fix plan**: Add exponential backoff retry (max 3 attempts, 2s/4s/8s delay).
**Acceptance test**: `test_yfinance_retry_succeeds_on_second_attempt`

### P1-2: RSI Uses Simple Rolling, Not Wilder's Smoothing
**Issue**: Industry standard RSI uses Wilder's exponential smoothing, not simple rolling mean.
**Fix plan**: Switch to `ewm(alpha=1/period)` for gain/loss smoothing.
**Acceptance test**: `test_rsi_wilder_matches_tradingview_reference`

### P1-3: RVOL N/A Scores Higher Than Low RVOL
**Issue**: RVOL=None gets liquidity score 0.55. RVOL=0.30 (real, low) gets lower score. Unknown is rewarded over known-bad.
**Fix plan**: Set RVOL N/A score to 0.35 (below the RVOL gate threshold).
**Acceptance test**: `test_rvol_na_scores_lower_than_rvol_at_strict_threshold`

### P1-4: Expected Price Ranges Hardcoded
**Issue**: Leveraged ETP prices drift via volatility decay, splits. Hardcoded ranges become stale.
**Fix plan**: Auto-update from rolling 90-day median + 3x range.
**Acceptance test**: `test_price_range_auto_updates_from_rolling_median`

### P1-5: Duplicate Configuration Sources
**Issue**: `_SPREAD_BPS` in engine.py vs `SLIPPAGE_MODEL` in isa_universe.py have different values for the same tickers.
**Fix plan**: Single source in isa_universe.py, imported everywhere.
**Acceptance test**: `test_no_duplicate_spread_bps_definitions_across_codebase`

### P1-6: No Standalone Data Health Artifact
**Issue**: Health check results embedded in EngineResult only, not independently auditable.
**Fix plan**: Write `data_health.json` artifact per session.
**Acceptance test**: `test_data_health_artifact_written_with_all_ticker_statuses`

### P1-7: Signal Logger O(n) Duplicate Detection
**Issue**: `_already_logged()` reads entire JSONL file per call. Degrades with history.
**Fix plan**: Maintain in-memory set of logged IDs, load once at startup.
**Acceptance test**: `test_duplicate_detection_is_o1_not_on`

### P1-8: Event Adapters All Stubs
**Issue**: Earnings, lockup, M&A adapters all return `is_available() = False`. EventWindowRule is dead code.
**Fix plan**: Wire earnings adapter to yfinance calendar data. Lockup/M&A can remain stubs with clear status.
**Acceptance test**: `test_earnings_adapter_returns_real_data`

### P1-9: REGIME_FIT Gate Always RELAXED for ISA
**Issue**: ISA is buy-only, all directions are LONG. In bearish regime, REGIME_FIT returns RELAXED (not FAIL), so it never blocks.
**Fix plan**: Apply a scoring penalty (not gate block) proportional to regime headwind severity. Bear regime + LONG = -15% composite penalty.
**Acceptance test**: `test_bearish_regime_long_signal_receives_scoring_penalty`

### P1-10: RiskOfficer Failure Silently Passes Signals
**Issue**: RiskOfficer exception at engine.py L429 logged as "non-fatal". Cards emit without risk governance.
**Fix plan**: On RiskOfficer failure, set all cards to risk_officer_decision="UNKNOWN" and sizing_hint="S".
**Acceptance test**: `test_risk_officer_failure_sets_decision_unknown_and_sizing_s`

### P1-11: Allocation Weights Not Validated
**Issue**: Router's allocation_weights may not sum to 1.0.
**Fix plan**: Normalize weights to sum=1.0 after computation. Log WARNING if raw sum deviates by >5%.
**Acceptance test**: `test_allocation_weights_normalized_to_sum_one`

### P1-12: Strategy Router Regime Map Could Drift
**Issue**: `_REGIME_MAP` in strategy_router.py maps legacy labels to canonical. Could miss new RegimeClassifier outputs.
**Fix plan**: Add exhaustive mapping validation test covering all `RegimeState` enum values.
**Acceptance test**: `test_regime_map_covers_all_regime_state_enum_values`

### P1-13: Ambiguous Bar Resolution Always Pessimistic
**Issue**: When both stop and target are hit in the same 1-min bar, outcome is always "HIT_STOP". This biases learning data pessimistically.
**Fix plan**: Use OHLC ordering heuristic: if Open is closer to entry direction, assume target hit first; otherwise stop.
**Acceptance test**: `test_ambiguous_bar_uses_open_proximity_heuristic`

### P1-14: Volatility Scoring Ceiling Too Low for Leveraged ETPs
**Issue**: `atr_s = min(atr_pct / 3.0, 1.0)` -- 3% ATR is max score. 3x ETPs routinely have 4-6% ATR, so all leveraged ETPs score 1.0 (no differentiation).
**Fix plan**: Use leverage-adjusted ceiling: `ATR% / (3.0 * leverage_factor)`.
**Acceptance test**: `test_volatility_score_differentiates_3x_and_5x_etps`

### P1-15: No As-Of Timestamp on Data
**Issue**: No staleness detection. Data could be minutes old by scan end.
**Fix plan**: Record fetch_timestamp on every BarResult. Reject bars older than 5 minutes for SCALP signals.
**Acceptance test**: `test_bar_result_includes_fetch_timestamp`

### P1-16: Edge Ledger Not Scheduled
**Issue**: No evidence of periodic edge ledger rebuild.
**Fix plan**: Schedule nightly rebuild at 22:30 UK via APScheduler.
**Acceptance test**: `test_edge_ledger_rebuilds_nightly`

---

## 5. P2 -- Medium Priority

### P2-1: No Artifact Checksums or Schema Versioning
**Fix plan**: Add sha256 checksum and schema_version to every JSON artifact.
**Acceptance test**: `test_artifact_includes_checksum_and_schema_version`

### P2-2: Feature Caching Across Scan Cycles
**Fix plan**: Cache TickerFeatures with 60-second TTL.
**Acceptance test**: `test_feature_cache_hit_within_ttl`

### P2-3: Signal Tape FIFO Eviction Without Archive
**Fix plan**: Archive evicted records to daily file before dropping.
**Acceptance test**: `test_tape_eviction_writes_archive`

### P2-4: Outcomes File Grows Unbounded
**Fix plan**: Monthly rotation to outcomes_YYYY_MM.jsonl + archive.
**Acceptance test**: `test_outcomes_rotated_monthly`

### P2-5: Time-of-Day Window Gaps
**Fix plan**: Add EARLY_AFTERNOON (16:30-17:00) and US_AFTERNOON (17:00-19:30) windows.
**Acceptance test**: `test_all_minutes_in_trading_day_mapped_to_window`

### P2-6: Fallback Cascade Fixed Order
**Fix plan**: Analyze gate failure distribution to determine optimal relaxation order per session.
**Acceptance test**: `test_fallback_order_adapts_to_most_restrictive_gate`

### P2-7: Counterfactual Variants Not Populated
**Fix plan**: Compute 3 counterfactual variants per outcome (tighter stop, wider stop, partial at 0.8R).
**Acceptance test**: `test_outcome_includes_3_counterfactual_variants`

### P2-8: ISA Constraint Not Centralized
**Fix plan**: Create `uk_isa/isa_constraints.py` with `enforce_isa_rules(signal)` called once.
**Acceptance test**: `test_isa_constraint_enforced_in_single_location`

### P2-9: Spread Gate VETO Is Informational Only in Engine Path
**Fix plan**: Spread VETO should downgrade SignalCard category to WATCH.
**Acceptance test**: `test_spread_veto_downgrades_to_watch`

### P2-10: Automated Delisting Detection
**Fix plan**: If yfinance returns empty for 3+ consecutive days, trigger alert and auto-remove from CORE.
**Acceptance test**: `test_delisted_ticker_removed_after_3_empty_days`

### P2-11: Scoring Weight Recalibration
**Fix plan**: Monthly recalibration of scoring component weights from outcome data.
**Acceptance test**: `test_scoring_weights_updated_from_last_100_outcomes`

### P2-12: Session Status Update Bare Except
**Fix plan**: Replace bare except at pipeline_runner.py L181 with explicit exception logging.
**Acceptance test**: `test_session_status_failure_logged`

---

## 6. Implementation Sequence

### Phase 1: Risk Integrity (Week 1-2)
**Goal**: Ensure risk governance is actually functional.

| # | Item | Severity | Effort | Dependencies |
|---|------|----------|--------|--------------|
| 1 | P0-1: Live VIX + losses to RiskOfficer | P0 | 2h | None |
| 2 | P0-2: Live regime to Signal Engine | P0 | 2h | None |
| 3 | P0-4: Hard gate failure = signal rejection | P0 | 4h | None |
| 4 | P0-7: Unified risk parity across pipelines | P0 | 8h | P0-1, P0-2 |
| 5 | P1-10: RiskOfficer failure = UNKNOWN + S | P1 | 1h | None |

### Phase 2: Data Quality (Week 2-3)
**Goal**: Ensure data flowing into the pipeline is correct and auditable.

| # | Item | Severity | Effort | Dependencies |
|---|------|----------|--------|--------------|
| 6 | P0-3: Use DataHub instead of direct yfinance | P0 | 3h | None |
| 7 | P0-6: Extract indicator functions | P0 | 6h | None |
| 8 | P1-1: yfinance retry logic | P1 | 2h | P0-3 |
| 9 | P1-2: RSI Wilder's smoothing | P1 | 1h | P0-6 |
| 10 | P1-5: Deduplicate spread config | P1 | 1h | None |
| 11 | P1-6: Data health artifact | P1 | 2h | None |
| 12 | P1-15: As-of timestamps | P1 | 2h | P0-3 |

### Phase 3: Learning Loop (Week 3-4)
**Goal**: Close the learning loop so the system improves from its own history.

| # | Item | Severity | Effort | Dependencies |
|---|------|----------|--------|--------------|
| 13 | P0-5: Schedule outcome resolution | P0 | 3h | None |
| 14 | P0-8: Wire edge ledger to Signal Engine | P0 | 4h | P0-5 |
| 15 | P1-7: Fix signal logger O(n) dedup | P1 | 2h | None |
| 16 | P1-13: Ambiguous bar heuristic | P1 | 2h | P0-5 |
| 17 | P1-16: Schedule edge ledger rebuild | P1 | 1h | P0-5 |

### Phase 4: CORE Objective Upgrade (Week 4-6)
**Goal**: Upgrade CORE tickers from 2% cap to MAX_INTRADAY_GAINS.

| # | Item | Severity | Effort | Dependencies |
|---|------|----------|--------|--------------|
| 18 | Target logic: regime-scaled targets | P1 | 4h | P0-2 |
| 19 | Trailing stop: 3-phase trailing | P1 | 6h | Phase 3 |
| 20 | Partial exits: 33/33/34 split | P1 | 4h | Phase 3 |
| 21 | Tier enforcement: CORE vs non-core | P1 | 3h | None |
| 22 | CORE reporting: MFE/MAE + compounding tracker | P1 | 4h | Phase 3 |
| 23 | P1-14: Volatility ceiling for leveraged ETPs | P1 | 1h | None |

### Phase 5: Polish (Week 6+)
**Goal**: Address remaining P1 and P2 items.

| # | Item | Severity | Effort | Dependencies |
|---|------|----------|--------|--------------|
| 24 | P1-3: RVOL N/A scoring fix | P1 | 30min | None |
| 25 | P1-4: Auto-update price ranges | P1 | 2h | None |
| 26 | P1-8: Wire earnings adapter | P1 | 3h | None |
| 27 | P1-9: Regime headwind scoring penalty | P1 | 1h | None |
| 28 | P1-11: Normalize allocation weights | P1 | 30min | None |
| 29 | P1-12: Regime map validation | P1 | 30min | None |
| 30 | P2-* items | P2 | Variable | Various |

---

## Effort Summary

| Severity | Count | Total Effort |
|----------|------:|-------------:|
| P0 | 8 | ~34h |
| P1 | 16 | ~30h |
| P2 | 12 | ~20h |
| **Total** | **36** | **~84h** |

---

## Risk Summary

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| RiskOfficer governance is decorative (P0-1) | HIGH | CRITICAL | Immediate fix |
| Regime defaults mask SHOCK events (P0-2) | HIGH | HIGH | Immediate fix |
| Learning loop produces no data (P0-5) | CONFIRMED | HIGH | Schedule outcome resolution |
| Signals pass with broken quality gates (P0-4) | MEDIUM | CRITICAL | Classify gates as HARD/SOFT |
| Two pipelines produce contradictory signals (P0-7) | HIGH | HIGH | Unified gate module |
| Data ingestion bypasses normalization (P0-3) | MEDIUM | MEDIUM | Route through DataHub |

---

## Acceptance Criteria for Plan Completion

The improvement plan is considered complete when:

1. All P0 items have passing acceptance tests
2. RiskOfficer receives live market context (VIX, losses, daily P&L)
3. Signal Engine computes or receives actual regime state
4. Outcome resolution runs on schedule and populates edge ledger
5. Edge ledger is consulted during scoring
6. No signal rejected in Pathway A appears as TRADE in Pathway B
7. CORE tickers use regime-scaled targets with trailing stops
8. Non-CORE tickers remain at 2% framing with WATCH/INTEL labels
9. All hard quality gates reject signals on failure instead of passing silently
