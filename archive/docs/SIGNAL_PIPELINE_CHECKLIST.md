# NZT-48 Signal Pipeline Checklist

**Phase 3 Institutional Audit -- Signal Generation Pipeline**
**Date**: 2026-02-27
**Scope**: Complete signal lifecycle from universe selection through outcome resolution
**Status**: AUDIT ONLY -- no code changes

---

## Table of Contents

1. [Pipeline Overview](#1-pipeline-overview)
2. [Step 1: Universe Selection](#2-step-1-universe-selection)
3. [Step 2: Data Ingestion](#3-step-2-data-ingestion)
4. [Step 3: Normalization and DataHealth](#4-step-3-normalization-and-datahealth)
5. [Step 4: Feature Computation](#5-step-4-feature-computation)
6. [Step 5: Regime Computation](#6-step-5-regime-computation)
7. [Step 6: Strategy Router](#7-step-6-strategy-router)
8. [Step 7: Gate Funnel (Hard vs Soft)](#8-step-7-gate-funnel)
9. [Step 8: Scoring (Composite + Weighted)](#9-step-8-scoring)
10. [Step 9: RiskOfficer Decision](#10-step-9-riskofficer-decision)
11. [Step 10: ExecutionPlan Creation](#11-step-10-executionplan-creation)
12. [Step 11: QualityGate (State Machine + Tape)](#12-step-11-qualitygate)
13. [Step 12: Emission (Telegram + War Room + Artifacts)](#13-step-12-emission)
14. [Step 13: Outcome Resolution](#14-step-13-outcome-resolution)
15. [Step 14: Learning Loop / Edge Ledger](#15-step-14-learning-loop)
16. [Cross-Cutting Concerns](#16-cross-cutting-concerns)

---

## 1. Pipeline Overview

The NZT-48 signal pipeline has TWO parallel pathways:

### Pathway A: Legacy Orchestrator (main.py `run_scan()`)
```
INGEST -> PERCEIVE -> CLASSIFY -> DECIDE -> QUALIFY -> SIZE -> EXECUTE -> DELIVER -> LEARN
```
- 15 strategy modules produce `Signal` objects
- Multi-stage qualification: BotRouter -> Learning -> Tournament -> Confluence -> PortfolioRisk -> ImmutableRules -> Firewall
- Virtual execution via `VirtualTrader`
- Telegram delivery + Sheets logging

### Pathway B: Signal Engine (signal_engine/engine.py `SignalEngine.run()`)
```
HEALTH_CHECK -> FETCH -> BUILD_FEATURES -> GATE_FUNNEL -> SCORE -> STRATEGY_ROUTER -> RISK_OFFICER -> ARTIFACTS -> PDF
```
- Operates on the ISA leveraged ETP universe
- Produces `PlayScore` / `SignalCard` objects
- Writes plays.json, risk_officer.json, drought.json artifacts
- Drives all 3 PDF reports (Momentum, Risk, Daily Review)

### Pathway C: Tiered Pipeline (pipeline_runner.py `run_tiered_pipeline()`)
```
CORE_SCAN -> PEER_SCAN -> FULL_SCAN -> UNIVERSE_ARTIFACTS
```
- Wraps Signal Engine for three-tier universe: CORE (trade), PEER (watch), FULL_SCAN (intel)

**KEY FINDING**: Pathways A and B are architecturally independent. Pathway A uses the `strategies/` modules and `qualification/` pipeline. Pathway B uses `signal_engine/` with its own gates, scoring, and RiskOfficer. Both coexist in the same process. This is a structural risk -- see improvement plan.

---

## 2. Step 1: Universe Selection

### What It Does
- **CORE list**: 12 ISA leveraged ETPs defined in `uk_isa/isa_universe.py::CORE_UNIVERSE`
  - QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L
- **EXTENDED list**: CORE + 9 additional tickers (AMD3.L, ARM3.L, NVDS.L, TSLS.L, 3LDE.L, 3LEU.L, 3GOL.L, 3SIL.L, 3OIL.L, LLY3.L)
- **INTEL list**: 14 US benchmark instruments (QQQ, SPY, SMH, ^VIX, etc.) -- context only, never traded
- **Tiered pipeline** uses `UniverseManager` singleton with CORE/PEER/FULL_SCAN tiers
- **Peer selection**: `uk_isa/peer_finder.py` auto-selects top-k similar instruments

### Source Files
| File | Role |
|------|------|
| `uk_isa/isa_universe.py` | Canonical CORE/EXTENDED/INTEL lists, factor groups, leverage map |
| `uk_isa/universe_manager.py` | Thread-safe singleton, three-tier management, peer selection |
| `uk_isa/peer_finder.py` | Correlation-based peer candidate ranking |
| `config/settings.yaml` | Bot A/B universes, overrides, ISA mapping |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| Universe list empty | `SignalEngine.__init__` defaults to CORE_UNIVERSE | Hardcoded fallback in isa_universe.py |
| UniverseManager import fails | Try/except in pipeline_runner.py L255-265 | Falls back to `isa_universe.CORE_UNIVERSE` |
| PeerFinder fails | Try/except in pipeline_runner.py L248-252 | Uses fallback peer_candidates[:peer_size_target] |
| universe.yaml missing | yaml=None guard in universe_manager.py L37 | Uses _DEFAULT_CORE hardcoded list |
| Ticker delisted / returns empty | Handled downstream at data health gate | Ticker excluded from scan |

### Artifacts
- `artifacts/YYYY-MM-DD/universe/` -- universe snapshot JSON
- `artifacts/YYYY-MM-DD/{session}/universe.json` -- sizes + compute times

### Recommended Improvements
1. **P1**: Add universe staleness check -- if CORE_UNIVERSE has not been reviewed in 30 days, log WARNING
   - Acceptance test: `test_universe_review_age_warning_fires_at_31_days`
2. **P2**: Add automated delisting detector -- yfinance returns empty for 3+ consecutive days triggers alert
   - Acceptance test: `test_delisted_ticker_detected_after_3_empty_days`

---

## 3. Step 2: Data Ingestion

### What It Does
- **Signal Engine path**: `yfinance.download(ticker, period, interval="1h")` called per ticker inside `SignalEngine._build_features()`
- **DataHub path** (data_hub/hub.py): Multi-source with fallback chain:
  1. IBKR (truth source -- stub, always unavailable)
  2. yfinance (always available)
  3. ValidatorSource (polygon/tiingo -- stub)
- **Legacy path** (main.py): `DataFeedManager.get_intraday_bars()` and `.get_daily_bars()` per ticker

### Source Files
| File | Role |
|------|------|
| `data_hub/hub.py` | DataHub with truth/fallback/validator chain |
| `data_hub/sources/yfinance_source.py` | YFinance wrapper |
| `data_hub/sources/ibkr_source.py` | IBKR stub (IS_AVAILABLE = False) |
| `data_hub/sources/validator_source.py` | Polygon/Tiingo stub |
| `feeds/data_feeds.py` | Legacy DataFeedManager |
| `feeds/data_validator.py` | Bar-level integrity checks |
| `signal_engine/engine.py` L549-561 | Direct yfinance.download inside _build_features |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| yfinance rate-limited (429) | Exception caught in _build_features L680 | Returns None, ticker skipped |
| yfinance returns empty DataFrame | `if raw.empty: return None` at L555 | Ticker excluded |
| MultiIndex columns (yfinance quirk) | Explicit check at L558-559 | Flattened via `.get_level_values(0)` |
| All tickers fail fetch | `if not features_map:` at L225 | EngineResult.drought() returned |
| IBKR unavailable | `if self._ibkr.IS_AVAILABLE:` in hub.py L76 | Falls back to yfinance |
| Stale data (market closed) | DataHealthGate checks (next step) | WARN status, data still usable |
| Pence/pounds miscoding | `scale_bars()` in normalization | Auto-divided by 100 |
| Network timeout | Generic exception handler | Ticker skipped, logged |

### Artifacts
- None at this step (data is transient in-memory)

### Recommended Improvements
1. **P0**: Signal Engine calls `yf.download()` directly in `_build_features()` -- should use DataHub for normalization and validation consistency
   - Acceptance test: `test_engine_uses_datahub_not_direct_yfinance`
2. **P1**: No retry logic on yfinance failures -- a single timeout kills the ticker for the entire session
   - Acceptance test: `test_yfinance_retry_on_timeout_succeeds_on_second_attempt`
3. **P1**: DataHub validator sources are stubs -- no actual cross-validation occurs
   - Acceptance test: `test_validator_source_returns_comparison_when_available`
4. **P2**: No as-of timestamp recorded on ingested data -- impossible to audit data staleness post-hoc
   - Acceptance test: `test_bar_result_includes_fetch_timestamp_and_staleness_seconds`

---

## 4. Step 3: Normalization and DataHealth

### What It Does
- **DataHealthGate** (`uk_isa/data_health.py`): 8 checks per ticker:
  1. OHLC_PRESENT -- columns exist, no NaN
  2. VOLUME_NONZERO -- volume > 0
  3. RANGE_VS_MOVE -- detect stale bars (range=0 but move>0)
  4. NAN_INF -- no np.nan or np.inf
  5. OHLC_SANITY -- high >= max(open,close), low <= min(open,close)
  6. PRICE_SCALE -- pence vs pounds detection for .L tickers
  7. MIN_ROWS -- at least 2 rows
  8. VOLUME_PLAUS -- reject suspiciously round volumes (1000, 10000)
- **Output**: `DataHealthResult` per ticker with status PASS/WARN/FAIL, `corrected_df` (price divided if pence)
- **Batch**: `validate_universe()` returns `DataHealthSummary`

### Source Files
| File | Role |
|------|------|
| `uk_isa/data_health.py` | DataHealthGate, 8 checks, DataHealthResult/Summary |
| `data_hub/normalization/price_units.py` | scale_bars(), normalize_to_pounds() |
| `data_hub/models.py` | DataReliabilityScore dataclass |
| `feeds/data_validator.py` | Legacy bar-level validator (used in main.py path) |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| All tickers FAIL health | `if not healthy:` at engine.py L210 | EngineResult.drought() with blocker list |
| Price scale misdetected | EXPECTED_PRICE_RANGE lookup in data_health.py L57-86 | Manual range update needed |
| corrected_df is None | `if df is None or df.empty:` in _build_features L551 | Fresh yf.download attempted |
| Volume always zero for .L ETPs after hours | VOLUME_NONZERO marks WARN | Still usable (WARN, not FAIL) |
| Stale bar (range=0, move>0) | RANGE_VS_MOVE check | WARN status |

### Artifacts
- Health summary embedded in EngineResult.health_summary
- No standalone health artifact file

### Recommended Improvements
1. **P1**: No standalone data_health artifact written -- health check results are only embedded in EngineResult, not independently auditable
   - Acceptance test: `test_data_health_artifact_written_to_session_directory`
2. **P1**: Expected price ranges are hardcoded -- leveraged ETP prices can drift significantly over months (volatility decay, splits)
   - Acceptance test: `test_expected_price_range_auto_updated_from_rolling_90d_median`
3. **P2**: Duplicate price range definitions -- `_EXPECTED_PRICE_RANGE` in data_health.py and `EXPECTED_PRICE_RANGES` in isa_universe.py overlap with slightly different values
   - Acceptance test: `test_single_source_of_truth_for_expected_price_ranges`

---

## 5. Step 4: Feature Computation

### What It Does
`SignalEngine._build_features()` (engine.py L541-682) computes per-ticker:
- **ATR(14)** -- True Range with adaptive window (Wilder's method)
- **ATR%** -- ATR / close * 100
- **RSI(14)** -- Relative Strength Index with adaptive window
- **MACD histogram** -- MACD(12,26,9) histogram
- **EMA alignment** -- 9/20/50 EMA stack check
- **Direction** -- Consensus from RSI>52, MACD>0, EMA aligned, close>EMA20 (4-vote system)
- **ADX** -- Average Directional Index (14-period)
- **BB width rank** -- Bollinger Band width percentile rank
- **RVOL** -- Relative volume vs trailing average
- **Setup type** -- Classified: continuation, breakout, mean_revert, default
- **Entry/Stop/Target levels** -- ATR-fraction based (setup-type-specific)
- **Net R:R** -- Reward:risk after round-trip cost model
- **SHORT_WINDOW mode** -- Adaptive windowing when 7 <= bars < 14

### Direction Constraint (ISA)
- All tickers forced to LONG (ISA = buy-only)
- If direction consensus = SHORT, `_build_features` returns None (ticker excluded)
- Inverse ETPs (QQQS.L, 3USS.L) have LONG direction = bearish market bet (valid ISA buy)

### Source Files
| File | Role |
|------|------|
| `signal_engine/engine.py` L541-682 | _build_features() -- all feature computation |
| `signal_engine/engine.py` L75-86 | Stop/target ATR fractions, spread BPS table |
| `signal_engine/engine.py` L122-148 | TickerFeatures.compute_levels() |
| `signal_engine/engine.py` L768-777 | _infer_setup_type() |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| < 7 bars available | `if n_total < 7: return None` at L563 | Ticker excluded |
| Division by zero (close=0) | `if close > 0` guard | ATR% set to 0.0 |
| RSI NaN (all gains or all losses) | loss.replace(0, 1e-9) | Avoids divide-by-zero |
| EMA span > available bars | ewm() handles gracefully | Produces less reliable values |
| All tickers return SHORT direction | All return None | EngineResult.drought() |
| OverflowError in sigmoid | try/except in _sigmoid_score | Returns 0.0 or 1.0 |

### Artifacts
- Features embedded in TickerFeatures objects (transient)
- Feature values surfaced in SignalCard and plays.json

### Recommended Improvements
1. **P0**: Feature computation is monolithic inside `_build_features()` -- no unit-testable indicator functions
   - Acceptance test: `test_atr_computation_matches_reference_for_known_series`
2. **P1**: RSI computed with simple rolling mean, not Wilder's smoothing (exponential) -- industry standard uses Wilder's
   - Acceptance test: `test_rsi_wilder_smoothing_matches_reference_implementation`
3. **P1**: RVOL calculation uses `vol_s.iloc[:-1].mean()` which includes the current bar's volume in the average for short series
   - Acceptance test: `test_rvol_excludes_current_bar_from_baseline`
4. **P2**: No caching of computed features across scan cycles -- each call re-downloads and recomputes everything
   - Acceptance test: `test_feature_cache_returns_cached_result_within_ttl`

---

## 6. Step 5: Regime Computation

### What It Does
- **Signal Engine path**: Regime is passed as a string parameter (`regime="NEUTRAL"`) from the caller. The engine does NOT compute regime internally.
- **Legacy path** (main.py): `RegimeClassifier` produces regime from market context:
  - `_build_market_context()` aggregates SPY/QQQ indicators
  - `RegimeClassifier` maps to 8 states: TRENDING_UP_STRONG/MOD, TRENDING_DOWN_STRONG/MOD, RANGE_BOUND, HIGH_VOLATILITY, RISK_OFF, SHOCK
  - `TimeOfDayEngine` determines time-of-day window

### Single Source Issue
The Signal Engine receives regime as a string parameter. When called from `run_pipeline()`, the default is `"NEUTRAL"`. When called from main.py's scheduler, it would need to pass the regime from the RegimeClassifier. **Currently, the pipeline runner defaults regime to "NEUTRAL" at L78** -- meaning the Signal Engine may not be receiving the actual computed regime.

### Source Files
| File | Role |
|------|------|
| `feeds/regime_classifier.py` | RegimeClassifier (8-state), TimeOfDayEngine |
| `feeds/hmm_regime_overlay.py` | Hidden Markov Model regime overlay |
| `signal_engine/strategy_router.py` L47-58 | Legacy->canonical regime mapping |
| `signal_engine/engine.py` L188-191 | Receives regime as parameter |
| `signal_engine/pipeline_runner.py` L78 | Default regime="NEUTRAL" |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| Regime defaults to NEUTRAL | No detection -- silent | All plays scored with NEUTRAL regime fit (0.60) |
| RegimeClassifier import fails | try/except in main.py L44 | Process crashes (hard dependency) |
| VIX data unavailable | market_ctx.vix defaults to None | VolShockRule uses 0.0 |
| Regime oscillates rapidly | `in_transition` flag on RegimeClassifier | Transition actions executed |

### Artifacts
- Regime tag embedded in plays.json, SignalCard
- strategies.json artifact from StrategyRouter

### Recommended Improvements
1. **P0**: Signal Engine receives regime as a string parameter with default "NEUTRAL" -- if caller does not pass actual regime, all signals are scored against NEUTRAL
   - Acceptance test: `test_pipeline_runner_passes_live_regime_not_default`
2. **P0**: No regime computation inside the Signal Engine -- it relies entirely on external caller
   - Acceptance test: `test_signal_engine_computes_regime_from_market_data_if_not_provided`
3. **P1**: Strategy Router has its own regime mapping (`_REGIME_MAP`) that could drift from RegimeClassifier
   - Acceptance test: `test_regime_map_covers_all_regime_classifier_output_states`

---

## 7. Step 6: Strategy Router

### What It Does
`StrategyRouter.run()` (strategy_router.py) evaluates which strategies are active for current conditions:
1. Maps legacy regime labels to canonical 8-state regime
2. Determines time-of-day window (7 windows adapted to UK/LSE/NYSE hours)
3. Activates/deactivates 14+ strategy specs based on regime + time + data availability
4. Applies overlay risk rules (VOL_TARGET, DISPERSION, CORRELATION)
5. Sets sizing mode (NORMAL / REDUCED / DEFENSIVE)
6. Sets kill switch if SHOCK or VIX > 45
7. Computes score_boost factor for plays
8. Computes allocation_weights per strategy tag
9. Detects event strategy availability (earnings, lockup, M&A adapters -- all stubs)

### Source Files
| File | Role |
|------|------|
| `signal_engine/strategy_router.py` | StrategyRouter, RouterResult, StrategySpec |
| `signal_engine/adapters/earnings_adapter.py` | Earnings calendar stub |
| `signal_engine/adapters/lockup_adapter.py` | Lockup expiry stub |
| `signal_engine/adapters/ma_adapter.py` | M&A event stub |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| Router import fails | try/except at engine.py L304-325 | Router skipped ("non-fatal"), no strategy enrichment |
| All adapters unavailable | `.is_available()` returns False | INACTIVE status logged; strategies deactivated |
| Kill switch triggered | `router_result.kill_switch = True` | Score boost set to 0, all plays suppressed |
| Score boost amplifies weak signals | Boost capped: `min(100.0, boosted)` | Composite never exceeds 100 |
| Allocation weights sum != 1.0 | No explicit check | Weights used as-is (could distort final_rank_score) |

### Artifacts
- `artifacts/YYYY-MM-DD/{session}/strategies.json`

### Recommended Improvements
1. **P1**: All three event adapters are stubs that always return `is_available() = False` -- EventWindowRule in RiskOfficer is dead code
   - Acceptance test: `test_earnings_adapter_returns_real_data_from_yfinance_calendar`
2. **P1**: Allocation weights are not validated to sum to 1.0
   - Acceptance test: `test_allocation_weights_sum_to_one_within_epsilon`
3. **P2**: Time-of-day window mapping has gaps (e.g., 17:00-19:30 UK falls to AFTER_HOURS) -- legitimate US afternoon trading missed
   - Acceptance test: `test_time_windows_cover_full_us_session`

---

## 8. Step 7: Gate Funnel (Hard vs Soft)

### What It Does
`run_full_gate_funnel()` (gates.py) runs 9 gates in sequence. **Hard gates short-circuit on first failure.** Soft gates collect all failures without short-circuiting.

| # | Gate | Type | Threshold (Strict) | Fallback |
|---|------|------|-------------------|----------|
| 1 | DATA_HEALTH | Hard | PASS/WARN | Never relaxed |
| 2 | PRICE_SCALE | Hard | close > 0, < 5000 for .L | Never relaxed |
| 3 | MIN_BARS | Hard | >= 14 (PASS), >= 7 (RELAXED) | Never relaxed |
| 4 | TRADABILITY | Hard (step-4 relaxable) | ATR% >= 1.0% | Step 4: >= 0.60% |
| 5 | VOLUME_LIQUIDITY | Soft | RVOL >= 0.40 | Step 1: >= 0.20 |
| 6 | RR_RATIO | Soft | R:R >= 1.5 | Step 2: >= 1.2 |
| 7 | MOMENTUM_ALIGNMENT | Soft | score >= 0.55 | Step 3: >= 0.40 |
| 8 | REGIME_FIT | Soft | Direction matches regime | Always RELAXED (ISA buy-only) |
| 9 | FACTOR_CAP | Soft | <= 3 per factor group | Never relaxed |

### Fallback Cascade
- If strict mode produces < `MIN_SIGNALS_FALLBACK` (5), fallback steps 1-4 are tried sequentially
- Each step relaxes ONE soft gate threshold
- Fallback signals are labelled "WATCH-SIGNAL (xxx-relaxed)"

### Source Files
| File | Role |
|------|------|
| `signal_engine/gates.py` | All gate functions, run_full_gate_funnel() |
| `signal_engine/engine.py` L232-258 | Strict + fallback orchestration |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| All tickers fail hard gates | drought check at engine.py L268 | SignalDroughtReport generated |
| RVOL always None/0 for .L ETPs | `if rvol is None or rvol <= 0:` -> RELAXED | Gate passes as RELAXED with "RVOL N/A" |
| Fallback step 4 still produces 0 signals | Drought report with top blockers | Telegram alert sent |
| Factor cap blocks all plays in a correlated session | gate_factor_cap returns FAIL for 4th+ signal | Expected behavior -- limits concentration |
| PRICE_SCALE false positive (legitimate high-priced ETP) | close > 5000 for .L ticker | Needs manual range update |

### Artifacts
- Gate funnel summary in EngineResult.gate_funnel (dict)
- Per-ticker gate reports in EngineResult.gate_reports
- Blocker summary in EngineResult.blocker_summary

### Recommended Improvements
1. **P1**: REGIME_FIT gate always returns RELAXED for LONG direction in ISA mode -- gate is effectively decorative for the primary use case
   - Acceptance test: `test_regime_fit_applies_meaningful_penalty_for_bearish_regime_long_signal`
2. **P1**: VOLUME_LIQUIDITY gate treats RVOL=None as RELAXED pass -- this means tickers with no volume data bypass the liquidity gate entirely
   - Acceptance test: `test_rvol_none_produces_warning_not_silent_pass`
3. **P2**: Fallback cascade relaxes gates one at a time in fixed order -- no adaptive selection of which gate is the most restrictive for current conditions
   - Acceptance test: `test_fallback_relaxes_most_restrictive_gate_first`

---

## 9. Step 8: Scoring (Composite + Weighted)

### What It Does
`compute_play_score()` (scoring.py) produces a 0-100 composite score from 6 weighted components:

| Component | Weight | Formula |
|-----------|--------|---------|
| Momentum | 0.30 | Mean of: RSI sigmoid, MACD agreement, EMA alignment |
| Volatility Opportunity | 0.20 | 0.6 * (ATR%/3.0) + 0.4 * BB_width_rank |
| Regime Fit | 0.15 | 0.9 if aligned, 0.5 if headwind, 0.6 if neutral |
| Liquidity | 0.15 | min(RVOL/3.0, 1.0) or 0.55 if N/A |
| Risk:Reward | 0.10 | (R:R - 1.0) / 2.0, capped at 1.0 |
| Quality (ADX) | 0.10 | min(ADX/50.0, 1.0) |

Star rating: 90+ = 5 stars, 80-89 = 4, 70-79 = 3, 60-69 = 2, <60 = 1
Star modifiers: -1 for factor overload, -1 for decay risk in choppy, -1 for spread risk, +1 for multi-source + regime

### Data Reliability Penalty
- `reliability_penalty = max(0.0, 1.0 - data_reliability) * 0.15`
- Applied as subtraction from raw composite

### Source Files
| File | Role |
|------|------|
| `signal_engine/scoring.py` | PlayScore, compute_play_score(), SignalDroughtReport |
| `signal_engine/engine.py` L513-534 | Gate pass -> score computation |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| All components score low | composite < 60 | 1-star rating, labelled WATCH |
| RVOL N/A produces 0.55 liquidity score | Neutral score, not penalized | Expected but not ideal |
| ADX=0 defaults quality to 0.40 | Not penalized heavily | Should be investigated |
| Data reliability penalty too harsh | SHORT_WINDOW mode caps at 0.35 penalty | 7-bar tickers lose max 5.25 composite points |

### Artifacts
- PlayScore objects with all component breakdowns
- Surfaced in plays.json via SignalCard.to_dict()

### Recommended Improvements
1. **P1**: Volatility scoring uses fixed `ATR%/3.0` ceiling -- for 3x/5x leveraged ETPs, 3% ATR is routine, so scoring ceiling may be too low
   - Acceptance test: `test_volatility_score_ceiling_calibrated_for_leveraged_etps`
2. **P1**: Liquidity default 0.55 for RVOL N/A means tickers with unknown liquidity score higher than tickers with low but known RVOL (e.g., 0.30)
   - Acceptance test: `test_rvol_na_scores_lower_than_rvol_0_55`
3. **P2**: No rolling recalibration of scoring weights -- weights are fixed constants, not adapted from outcome data
   - Acceptance test: `test_scoring_weights_recalibrated_from_last_100_outcomes`

---

## 10. Step 9: RiskOfficer Decision

### What It Does
`RiskOfficer.evaluate()` (risk_officer/officer.py) runs 6 rules per SignalCard. Decisions: APPROVE, DOWNSIZE, VETO. Worst-wins aggregation.

| Rule | Triggers | Decision |
|------|----------|----------|
| VOL_SHOCK | VIX>35 + ATR%>3.5% | VETO |
| VOL_SHOCK | VIX 25-35 + ATR%>2.5% | DOWNSIZE |
| VOL_SHOCK | Kill switch active | VETO |
| LIQUIDITY | RVOL < 0.40 | VETO |
| LIQUIDITY | RVOL 0.40-0.60 | DOWNSIZE |
| LIQUIDITY | Spread > 30bps | VETO |
| CORRELATION | > max_factor_cap signals in group | VETO |
| CORRELATION | = max_factor_cap | DOWNSIZE |
| DRAWDOWN | consecutive_losses >= 5 | VETO |
| DRAWDOWN | consecutive_losses >= 3 | DOWNSIZE |
| DRAWDOWN | daily_loss > 3% | VETO |
| EVENT_WINDOW | Earnings in 1d + fallback signal | VETO (stub) |
| DATA_RELIABILITY | data_reliability < 0.50 | VETO |
| DATA_RELIABILITY | data_reliability < 0.70 | DOWNSIZE |
| DATA_RELIABILITY | SHORT_WINDOW + fallback > 2 | DOWNSIZE |

### Source Files
| File | Role |
|------|------|
| `risk_officer/officer.py` | RiskOfficer, RiskDecision, RiskOfficerReport |
| `risk_officer/rules/vol_shock.py` | VIX + ATR compound rule |
| `risk_officer/rules/liquidity.py` | RVOL + spread rule |
| `risk_officer/rules/correlation.py` | Factor overload (stateful) |
| `risk_officer/rules/drawdown.py` | Consecutive loss + daily loss |
| `risk_officer/rules/event_window.py` | Earnings event guard (stub) |
| `risk_officer/rules/data_reliability.py` | Data quality guard |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| RiskOfficer import fails | try/except at engine.py L397-430 | "non-fatal" -- cards get no risk_officer_decision |
| Context missing VIX | `context.get("vix", 0.0)` defaults to 0 | VOL_SHOCK never fires |
| Context missing consecutive_losses | defaults to 0 | DRAWDOWN never fires |
| CorrelationRule not reset between runs | `reset()` called at officer.py L100 | Expected behavior |
| VIX hardcoded to 18.0 in engine.py L403 | Always passed as 18.0 | **VOL_SHOCK rule never fires in Signal Engine path** |

### Critical Finding
At engine.py L403, the VIX context is hardcoded:
```python
context={"vix": 18.0, "consecutive_losses": 0},
```
This means the RiskOfficer in the Signal Engine path **never sees real VIX data** and **never sees real consecutive losses**. The VOL_SHOCK and DRAWDOWN rules are effectively dead code in Pathway B.

### Artifacts
- `artifacts/YYYY-MM-DD/{session}/risk_officer.json`

### Recommended Improvements
1. **P0**: VIX hardcoded to 18.0 and consecutive_losses to 0 in engine.py L403 -- RiskOfficer receives dummy context, rendering VOL_SHOCK and DRAWDOWN rules non-functional
   - Acceptance test: `test_risk_officer_receives_live_vix_from_market_context`
   - Acceptance test: `test_risk_officer_receives_real_consecutive_losses_from_db`
2. **P1**: Event window rule is dead code (all adapters are stubs)
   - Acceptance test: `test_event_window_rule_fires_when_yfinance_earnings_within_2d`
3. **P1**: RiskOfficer failure is silently swallowed as "non-fatal" -- signals emit without risk governance
   - Acceptance test: `test_risk_officer_failure_produces_warning_artifact_and_telegram_alert`

---

## 11. Step 10: ExecutionPlan Creation

### What It Does
Two parallel execution plan systems:

**Signal Engine path** (engine.py L374-391): Inline execution plan dict per SignalCard:
```python
execution_plan = {
    "order_type": "MARKETABLE_LIMIT" or "LIMIT",
    "max_slippage_bps": 10.0,
    "spread_proxy_bps": from _SPREAD_BPS table,
    "spread_gate_result": "PASS" / "WATCH" / "VETO",
    "cancel_conditions": [price move, session close],
    "time_in_trade_window": from router,
}
```

**Execution module** (execution/planner.py): Full `ExecutionPlanner.build()`:
- Uses `cost_model.py` for spread/cost calculations
- Uses `order_rules.py` for cancel conditions
- Produces `ExecutionPlan` dataclass with limit price, do-not-trade gate, PM summary

### Source Files
| File | Role |
|------|------|
| `signal_engine/engine.py` L374-391 | Inline execution plan dict |
| `execution/planner.py` | ExecutionPlanner, ExecutionPlan dataclass |
| `execution/cost_model.py` | get_spread_bps(), round_trip_cost_bps() |
| `execution/order_rules.py` | CancelConditions, DoNotTradeConditions |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| Spread BPS lookup misses ticker | `_SPREAD_BPS.get(ticker, 20.0)` default | Default 20bps used |
| Spread gate VETO | spread_gate_result = "VETO" | Card still emitted (VETO is informational only in inline path) |
| Live quote unavailable | `live_quote=None` | Planner uses proxy spreads |
| Order type wrong for thin market | RVOL > 1.0 triggers MARKETABLE_LIMIT | Could cause slippage |

### Recommended Improvements
1. **P1**: Two parallel execution plan systems with different logic -- inline dict in engine.py vs ExecutionPlanner class
   - Acceptance test: `test_signal_engine_uses_execution_planner_not_inline_dict`
2. **P2**: Spread gate VETO in inline path is informational only -- card still emitted as TRADE
   - Acceptance test: `test_spread_veto_downgrades_card_category_to_watch`

---

## 12. Step 11: QualityGate (State Machine + Tape)

### What It Does
**Signal Engine path**: `SignalTape` (state_machine.py) provides lifecycle tracking:
- States: CANDIDATE -> QUALIFIED -> SIGNAL -> ORDER_INTENT -> EXPIRED -> INVALIDATED
- Top N plays emitted to tape as SIGNAL records
- Stale signals auto-expired after configurable max_age_seconds (default 3600)

**Legacy path** (main.py L715-999): 10+ qualification stages per signal:
1. Session boundary check
2. BotRouter personality routing
3. Learning engine adjustments
4. Strategy tournament bench check
5. Pre-market bias adjustment
6. Market internals adjustment
7. Edge decay time adjustment
8. Confluence multi-TF scoring (minimum confluence gate)
9. Portfolio risk manager gate (concentration, directional, budget)
10. Adaptive intelligence per-ticker check
11. Overseer portfolio constraint check
12. Session protection min confidence filter
13. QualificationPipeline (7-stage legacy qualifier)
14. Dynamic sizer (8-factor position sizing)
15. Smart router liquidity cap + slippage prediction
16. Tournament size multiplier
17. Immutable risk rules
18. Emotional firewall (14 emotional patterns)

### Source Files
| File | Role |
|------|------|
| `signal_engine/state_machine.py` | SignalRecord, SignalTape, SignalState |
| `qualification/qualifier.py` | QualificationPipeline |
| `qualification/circuit_breakers.py` | CircuitBreakerSystem (5 breakers) |
| `qualification/confluence_scorer.py` | Multi-TF agreement scoring |
| `qualification/portfolio_risk.py` | 8-dimension risk decomposition |
| `qualification/dynamic_sizer.py` | 8-factor Kelly + vol sizing |
| `qualification/risk_sizer.py` | ImmutableRiskRules, EmotionalFirewall, SessionProtection, DrawdownRecovery |
| `qualification/confidence_scorer.py` | Confidence scoring adjustments |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| All signals rejected by qualification | qualified_signals empty list | No delivery |
| Confluence scorer exception | try/except at main.py L886 | "non-critical", signal proceeds without confluence check |
| Portfolio risk gate exception | try/except at main.py L917 | Signal proceeds without risk gate |
| Dynamic sizer fails | try/except at main.py L998 | Uses qualifier sizing |
| Smart router fails | try/except at main.py L1034 | Signal proceeds with original sizing |
| All 10+ stages pass but risk rules fail | Signal marked SKIPPED | Missed trade journal entry |

### Artifacts
- Signal tape lines (in-memory, surfaced via to_lines())
- Signals table in SQLite database
- Firewall events in database

### Recommended Improvements
1. **P0**: Legacy path has 10+ try/except blocks that silently skip quality gates on error -- a broken confluence scorer, risk manager, or smart router passes signals without those checks
   - Acceptance test: `test_qualification_gate_failure_produces_warning_not_silent_pass`
2. **P1**: Signal Engine path has minimal qualification compared to legacy -- no confluence, no portfolio risk, no emotional firewall
   - Acceptance test: `test_signal_engine_path_applies_equivalent_qualification_gates`
3. **P2**: SignalTape max_records=500 with FIFO eviction -- oldest signals silently dropped
   - Acceptance test: `test_signal_tape_overflow_triggers_archive_not_silent_drop`

---

## 13. Step 12: Emission (Telegram + War Room + Artifacts)

### What It Does

**Signal Engine artifacts** (engine.py + pipeline_runner.py):
- `plays.json` -- all SignalCards with scores, levels, risk officer decisions
- `risk_officer.json` -- per-signal risk decisions
- `drought.json` -- drought package when no signals
- `strategies.json` -- active strategies from router
- `peers_intel.json` -- peer scan results (tiered pipeline)
- `full_scan.json` -- full scan intel cards (tiered pipeline)
- `intel.json` -- intel cards for INTEL_UNIVERSE
- All artifact writes use atomic temp-file + rename pattern

**PDF delivery** (3 daily reports):
- PDF1: Momentum & Opportunity (pdf_v2_momentum.py)
- PDF2: Risk & Structural (pdf_v2_risk.py)
- PDF3: Daily Review (pdf_v2_daily_review.py)

**Legacy delivery** (main.py):
- Telegram: `TelegramDelivery.send_signal()` per qualified signal
- Google Sheets: `SheetsLogger.log_signal()` per signal
- Database: `insert_signal()` per signal (qualified + rejected)

**Signal logging** (learning/signal_logger.py):
- Appends to `data/signal_log.jsonl` via `SignalLogger.log_plays()`
- Deterministic signal IDs via SHA-256 hash
- Duplicate detection via file scan

### Source Files
| File | Role |
|------|------|
| `signal_engine/signal_card.py` | SignalCard model, write_plays_artifact() |
| `signal_engine/pipeline_runner.py` | Pipeline orchestration, PDF generation |
| `delivery/telegram_bot.py` | TelegramDelivery (55k lines) |
| `delivery/sheets_logger.py` | Google Sheets logger |
| `delivery/database.py` | SQLite persistence |
| `delivery/pdf_v2_momentum.py` | PDF1 Momentum report |
| `delivery/pdf_v2_risk.py` | PDF2 Risk report |
| `delivery/pdf_v2_daily_review.py` | PDF3 Daily Review |
| `learning/signal_logger.py` | JSONL signal log for learning |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| Artifact write fails (disk full, permissions) | try/except at engine.py L468 | Warning logged, artifact not written |
| PDF generation fails | try/except in pipeline_runner.py L511 | Returns None, no PDF |
| Telegram delivery fails | done_callback + asyncio.gather | Exception logged, other deliveries proceed |
| Sheets logger fails | try/except at main.py L1292 | Warning logged |
| Signal log duplicate detection is O(n) file scan | `_already_logged()` reads entire JSONL | Performance degrades with log size |
| Drought alert fails | try/except at pipeline_runner.py L159 | Warning logged |
| Session status update fails | bare except at pipeline_runner.py L181 | Silently swallowed |

### Artifacts
- `artifacts/YYYY-MM-DD/{session}/plays.json`
- `artifacts/YYYY-MM-DD/{session}/risk_officer.json`
- `artifacts/YYYY-MM-DD/{session}/drought.json`
- `artifacts/YYYY-MM-DD/{session}/strategies.json`
- `artifacts/YYYY-MM-DD/{session}/intel.json`
- `reports/YYYY-MM-DD/NZT48_{TYPE}_{SESSION}.pdf`
- `data/signal_log.jsonl`
- `data/nzt48.db` (SQLite)

### Recommended Improvements
1. **P1**: Signal log duplicate detection reads entire JSONL file on every log call -- O(n) scan degrades with history
   - Acceptance test: `test_signal_logger_duplicate_detection_uses_index_not_file_scan`
2. **P1**: Session status update failure is silently swallowed (bare except at pipeline_runner.py L181)
   - Acceptance test: `test_session_status_update_failure_logged_with_details`
3. **P2**: No artifact integrity verification -- no checksums or schema validation on reads
   - Acceptance test: `test_artifact_includes_checksum_and_schema_version`

---

## 14. Step 13: Outcome Resolution

### What It Does
`OutcomesEngine` (learning/outcomes_engine.py) resolves signal outcomes using path-based analysis:

1. Reads pending signals from `data/signal_log.jsonl`
2. Fetches 1-minute intraday bars via yfinance
3. Walks bars chronologically checking if stop or target was hit first
4. Ambiguous bars (both hit in same bar): worst-case = stop hit
5. TIME_STOP: neither hit by expiry, exits at last bar close
6. Computes MFE (max favorable excursion), MAE (max adverse excursion)
7. Computes realized R-multiple gross and net (after cost model)
8. Writes OutcomeRecord to `data/outcomes.jsonl`

### Cost Model
Per-ticker basis points: default 8bps, 3x ETPs 12bps, QQQ5 14bps, SP5L 10bps

### Resolution Schedule
- SCALP: every 15 minutes
- SWING: T+4h + EOD check

### Source Files
| File | Role |
|------|------|
| `learning/outcomes_engine.py` | OutcomesEngine, path-based resolution |
| `learning/schemas.py` | SignalLogRecord, OutcomeRecord, CounterfactualVariant |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| yfinance 1-min data unavailable (>7 days old) | `_fetch_intraday_bars` returns None | Outcome not resolved, stays PENDING |
| Bar data gap (market halt) | Bars filtered to window | May miss stop/target hit during gap |
| Ambiguous bar (both stop and target hit) | Conservative: worst-case = stop hit | Overstates losses |
| Signal log corrupt (malformed JSON) | try/except on json.loads | Line skipped |
| Outcomes file grows unbounded | No rotation | Disk consumption |

### Artifacts
- `data/outcomes.jsonl`
- `data/outcomes_index.json`

### Recommended Improvements
1. **P0**: No outcome resolution scheduler visible in the codebase -- outcomes_engine exists but no scheduler calls it
   - Acceptance test: `test_outcomes_engine_scheduled_at_15min_for_scalps_and_4h_for_swings`
2. **P1**: Ambiguous bars always resolve to stop hit -- this biases outcome data pessimistically, affecting edge ledger accuracy
   - Acceptance test: `test_ambiguous_bar_uses_ohlc_ordering_heuristic`
3. **P2**: No outcome data rotation or archiving -- outcomes.jsonl grows unbounded
   - Acceptance test: `test_outcomes_archive_rotates_monthly`

---

## 15. Step 14: Learning Loop / Edge Ledger

### What It Does

**Edge Ledger** (learning/edge_ledger.py):
- Computes per-bucket statistics: win rate, expectancy, confidence
- Bucket key: `strategy_tag x regime_tag x track x time_window x liquidity_bucket`
- Wilson interval for win-rate confidence bounds (90% CI)
- Status levels: NEEDS_DATA (<10 trades), CALIBRATION_READY (10-19), ACTIONABLE (20+, expectancy>0)
- Weekly delta: compares last 7 days vs prior 7 days for trend detection

**Learning Engine** (learning/learning_engine.py):
- 10 sub-modules: strategy tracking, indicator tracking, regime analysis, parameter optimization, etc.
- `get_signal_adjustments()` returns confidence_adj and should_disable flags
- `_on_trade_closed()` callback feeds all learning systems

**Additional Learning Modules**:
- Trade autopsy (5-grade post-trade analysis)
- Missed trade journal (tracks rejected signals to evaluate filter effectiveness)
- Strategy tournament (Darwinian capital allocation)
- Adaptive intelligence (Gemini AI nightly learning cycle)
- Performance attribution (6-factor trade decomposition)
- Edge decay engine (intraday alpha curve per strategy x regime x 30min bucket)
- ML meta-model (machine learning layer)
- Drift detection (feature/residual/hit-rate/regime drift)
- Calibration (probability calibration)

### Source Files
| File | Role |
|------|------|
| `learning/edge_ledger.py` | EdgeLedger, per-bucket statistics |
| `learning/schemas.py` | All learning data contracts |
| `learning/learning_engine.py` | LearningEngine (10 modules) |
| `learning/trade_autopsy.py` | 5-grade post-trade analysis |
| `learning/missed_trade_journal.py` | Rejected signal tracking |
| `learning/strategy_tournament.py` | Darwinian capital allocation |
| `learning/adaptive_intelligence.py` | Gemini AI learning cycle |
| `learning/performance_attribution.py` | 6-factor decomposition |
| `learning/edge_decay_engine.py` | Intraday alpha curve |
| `learning/outcomes_engine.py` | Path-based outcome resolution |
| `learning/signal_logger.py` | JSONL signal logging |
| `learning/drift.py` | Drift detection |
| `learning/calibration.py` | Probability calibration |
| `learning/meta_learner.py` | Meta-learner weight computation |

### What Can Fail
| Failure Mode | Detection | Recovery |
|---|---|---|
| No outcomes data (fresh system) | All buckets NEEDS_DATA | No adjustments applied |
| Edge ledger file corrupt | try/except in load() | Returns empty dict |
| Learning engine disabled | `should_disable` returned | Strategy signals skipped |
| Weekly delta shows DECAYING | Trend field in delta JSON | Manual investigation needed |
| Gemini API unavailable | api_key empty or timeout | AI learning skipped |

### Artifacts
- `data/edge_ledger.json`
- `data/edge_weekly_delta.json`
- `data/outcomes.jsonl`
- `data/signal_log.jsonl`
- Edge decay state in SQLite

### Recommended Improvements
1. **P0**: Edge ledger is not wired into the Signal Engine path -- it only feeds into the legacy path via `learning.get_signal_adjustments()`
   - Acceptance test: `test_signal_engine_consults_edge_ledger_before_scoring`
2. **P1**: Edge ledger rebuild is not scheduled -- no evidence of periodic recalculation
   - Acceptance test: `test_edge_ledger_rebuilds_nightly_at_22_30_uk`
3. **P1**: Wilson CI uses z=1.645 (90% CI) which may be too narrow for small samples -- consider z=1.96 (95%)
   - Acceptance test: `test_wilson_ci_width_appropriate_for_sample_size`
4. **P2**: Counterfactual variants defined in schema but not populated in outcomes engine
   - Acceptance test: `test_outcome_resolution_populates_3_counterfactual_variants`

---

## 16. Cross-Cutting Concerns

### 16.1 Two Independent Pipelines

The system has two architecturally independent signal generation pathways:
- **Pathway A** (main.py `run_scan()`): Strategy-based, 15 strategies, full qualification chain, virtual execution
- **Pathway B** (signal_engine `SignalEngine.run()`): Feature-based, gate funnel, scoring, RiskOfficer, artifacts/PDFs

These share no qualification logic. A signal that would be VETOED in Pathway A (by emotional firewall, portfolio risk, etc.) could appear as a top-rated TRADE in Pathway B's PDF. This is a structural integrity risk.

### 16.2 Error Handling Philosophy

The codebase uses a "fail-open" pattern pervasively:
- 15+ `try/except` blocks catch exceptions and continue processing
- Critical governance components (RiskOfficer, confluence scorer, portfolio risk) are wrapped in "non-fatal" exception handlers
- When these components fail, signals proceed WITHOUT their checks

This is appropriate for uptime but dangerous for risk management -- a broken risk gate should halt signals, not silently pass them.

### 16.3 Stale Data Handling

No system-wide as-of timestamp tracking:
- Data fetched at scan start may be minutes old by scan end
- No staleness detection on ingested OHLCV data
- Price levels computed from stale data could be dangerously wrong for leveraged 3x/5x ETPs

### 16.4 Configuration Drift

Multiple sources of truth for overlapping configuration:
- `_SPREAD_BPS` in engine.py vs `SLIPPAGE_MODEL` in isa_universe.py -- different values
- `_EXPECTED_PRICE_RANGE` in data_health.py vs `EXPECTED_PRICE_RANGES` in isa_universe.py
- `INVERSE_ETPS` in engine.py vs `_INVERSE_ETPS` in daily_target.py

### 16.5 ISA Constraint Enforcement

ISA buy-only constraint enforced at multiple inconsistent levels:
- `_build_features()`: Returns None for SHORT direction (engine.py L620-621)
- `DailyTargetStrategy._score_ticker()`: Returns None for SHORT (daily_target.py L200)
- `MeanReversionStrategy`: DORMANT flag (mean_reversion.py L52)
- Gate: REGIME_FIT always RELAXED for LONG

No centralized ISA constraint enforcer -- each component implements its own version.

---

## Summary Table: All Pipeline Steps

| Step | Component | Files | Failure Mode Count | P0 Issues |
|------|-----------|-------|--------------------:|-----------|
| 1. Universe | isa_universe, universe_manager | 4 | 5 | 0 |
| 2. Data Ingestion | DataHub, yfinance, data_feeds | 6 | 8 | 1 |
| 3. Normalization | DataHealthGate | 4 | 5 | 0 |
| 4. Features | engine._build_features | 4 | 6 | 1 |
| 5. Regime | RegimeClassifier, strategy_router | 5 | 4 | 2 |
| 6. Strategy Router | strategy_router, adapters | 4 | 5 | 0 |
| 7. Gate Funnel | gates.py, engine._run_mode | 2 | 5 | 0 |
| 8. Scoring | scoring.py | 2 | 4 | 0 |
| 9. RiskOfficer | officer.py, 6 rules | 7 | 5 | 1 |
| 10. Execution Plan | planner.py, engine inline | 4 | 4 | 0 |
| 11. QualityGate | state_machine, qualifier, 10+ checks | 8 | 6 | 1 |
| 12. Emission | signal_card, telegram, PDF, signal_logger | 9 | 7 | 0 |
| 13. Outcome Resolution | outcomes_engine | 2 | 5 | 1 |
| 14. Learning Loop | edge_ledger, learning_engine | 14 | 5 | 1 |
| **Totals** | | **75 files** | **74 failure modes** | **8 P0** |
