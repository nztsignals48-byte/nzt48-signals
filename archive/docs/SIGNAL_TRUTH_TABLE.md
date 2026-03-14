# NZT-48 Signal Truth Table -- Canonical Schema & Logging Specification

**Document:** Phase 2 Institutional Audit -- Signal Record Schema
**Version:** 1.0.0
**Date:** 2026-02-27
**Scope:** Every signal emitted by the NZT-48 trading system MUST conform to this schema.

---

## 1. Purpose

This document defines the **canonical Signal Record schema** -- the single source of truth for every signal the system generates. It serves three purposes:

1. **Auditability** -- Every decision the system makes can be reconstructed from its signal record.
2. **Reproducibility** -- Given the same `run_id`, `git_hash`, `config_hash`, and `universe_hash`, the exact same signals MUST be reproducible.
3. **Accountability** -- The `objective_tag` field makes explicit whether a signal targets MAX_INTRADAY_GAINS (CORE) or operates under the legacy 2% cap (non-core).

---

## 2. Canonical Signal Record Schema

### 2.1 Provenance Block (Immutable Run Context)

Every signal record MUST include these fields to identify the exact system state that produced it.

| Field | Type | Required | Description |
|---|---|---|---|
| `run_id` | `string` | YES | UUID v4 (first 8 chars uppercase). Unique per pipeline invocation. |
| `git_hash` | `string` | YES | First 12 chars of HEAD commit SHA at engine start. |
| `config_hash` | `string` | YES | SHA-256 of `config/settings.yaml` content at engine start. |
| `universe_hash` | `string` | YES | SHA-256 of sorted ticker list used for this run. |
| `engine_version` | `string` | YES | Semantic version of the signal engine (e.g., `"4.0.0"`). |

**Current state:** `run_id` is generated in `pipeline_runner.py` (line 88) but NOT attached to individual signal records. `git_hash`, `config_hash`, and `universe_hash` are NOT logged anywhere.

### 2.2 Signal Identity Block

| Field | Type | Required | Description |
|---|---|---|---|
| `signal_id` | `string` | YES | Deterministic hash: `NZT-{SHA256(date|ts|ticker|strategy|track|entry)[:6]}`. Stable across restarts. Currently generated in `learning/schemas.py:make_signal_id()`. |
| `time_logged_utc` | `string (ISO 8601)` | YES | UTC timestamp when signal was persisted to `signal_log.jsonl`. |
| `time_logged_uk` | `string (ISO 8601)` | YES | Europe/London timestamp (handles BST/GMT automatically). |
| `date_str` | `string (YYYY-MM-DD)` | YES | Trading date (may differ from UTC date for pre-market signals). |

**Current state:** `signal_id` and `generated_at` (UTC) are logged. `time_logged_uk` is NOT logged.

### 2.3 Session & Scheduling Block

| Field | Type | Required | Allowed Values | Description |
|---|---|---|---|---|
| `session` | `string` | YES | `PRE_LSE`, `LSE`, `PRE_NYSE`, `NYSE`, `EOD`, `preview_pre_lse`, `preview_eod_institutional` | Which scheduled session produced this signal. |
| `time_of_day_window` | `string` | YES | `CHAOS_OPEN`, `MORNING_MOMENTUM`, `TREND_EXTENSION`, `LUNCH_CHOP`, `AFTERNOON_PUSH`, `POWER_HOUR`, `CLOSE_MECHANICS`, `AFTER_HOURS`, `UNKNOWN` | Master Spec Section 10 time-of-day window. |

**Current state:** `session` is logged. `time_window` is logged but often defaults to `"UNKNOWN"`.

### 2.4 Instrument Block

| Field | Type | Required | Description |
|---|---|---|---|
| `instrument_traded` | `string` | YES | The actual ISA ETP ticker that would be bought/sold (e.g., `QQQ3.L` for a bullish Nasdaq play, `QQQS.L` for a bearish Nasdaq play). This MUST be the correct leveraged long vs leveraged short ticker. |
| `direction` | `string` | YES | `LONG` or `SHORT`. For ISA (buy-only), always `LONG` on the selected ETP. A bearish market view = `LONG` on an inverse ETP. |
| `underlying_proxy` | `string` | NO | The underlying index/stock the ETP tracks (e.g., `"Nasdaq 100"`, `"NVIDIA"`, `"S&P 500"`). |
| `leverage_multiple` | `string` | NO | e.g., `"3x"`, `"-3x"`, `"5x"`, `"2x"`. |
| `factor_group` | `string` | NO | Factor cluster from `ISA_FACTOR_GROUPS` (e.g., `"nasdaq_beta_long"`, `"semiconductors"`, `"ai_gpt"`). |
| `is_inverse_etp` | `boolean` | NO | `true` if ticker is in `INVERSE_ETPS` set (`QQQS.L`, `3USS.L`, `NVDS.L`, `TSLS.L`). |

**Current state:** `ticker` is logged (maps to `instrument_traded`). `direction` is logged. `underlying_proxy`, `leverage_multiple`, `is_inverse_etp` are NOT logged. `factor_group` is available on `SignalCard` but NOT persisted to `signal_log.jsonl`.

### 2.5 Universe Tier Block

| Field | Type | Required | Allowed Values | Description |
|---|---|---|---|---|
| `tier` | `string` | YES | `CORE`, `PEER`, `FULL_SCAN` | Universe tier from `TieredPipelineResult`. |
| `objective_tag` | `string` | YES | `MAX_INTRADAY_GAINS`, `2PCT_CAP`, `INTEL_ONLY` | **CORE** tickers: `MAX_INTRADAY_GAINS` -- uncapped profit target, exploit full intraday range. **Non-core** tickers: `2PCT_CAP` -- legacy 2% daily target framing. **FULL_SCAN**: `INTEL_ONLY` -- monitoring, no trade eligibility. |

**Current state:** `tier` is written to tiered artifact JSON but NOT to `signal_log.jsonl`. `objective_tag` does NOT exist anywhere in the codebase.

### 2.6 Decision Block

| Field | Type | Required | Allowed Values | Description |
|---|---|---|---|---|
| `decision` | `string` | YES | `TRADE`, `WATCH`, `INTEL`, `ABSTAIN` | Final disposition of the signal. `TRADE` = actionable with full execution plan. `WATCH` = near-miss, monitoring only. `INTEL` = informational context signal. `ABSTAIN` = conditions evaluated, explicitly decided not to trade. |
| `decision_reasons` | `array[string]` | YES | -- | Human-readable bullets explaining why this decision was reached. Currently stored in `SignalCard.reasons`. |
| `category` | `string` | NO | `TRADE`, `WATCH`, `EXCLUDED` | PDF sectioning category from `SignalCard.category`. |
| `label` | `string` | NO | `STRICT`, `WATCH-SIGNAL (xxx)`, `PEER`, `FULL_SCAN` | Granular label from scoring. |

**Current state:** No explicit `decision` field exists. The closest are `SignalCard.category` (`TRADE`/`WATCH`/`EXCLUDED`) and `SignalLogRecord.outcome` (`PENDING`/`RESOLVED`/`EXPIRED`). The semantic distinction between TRADE/WATCH/INTEL/ABSTAIN is not captured.

### 2.7 Strategy Block

| Field | Type | Required | Description |
|---|---|---|---|
| `strategy_tag` | `string` | YES | Active strategy tag from `StrategyRouter` (e.g., `TREND_MOMENTUM_CTA`, `OPENING_RANGE_BREAKOUT`, `STAT_ARB_MEAN_REVERT`). |
| `strategy_weighted_score` | `float` | NO | Composite score after strategy boost: `composite * (1 + score_boost)`. |
| `allocation_weight` | `float` | NO | Capital allocation weight for this strategy from `RouterResult.allocation_weights`. |
| `why_strategy_now` | `array[string]` | NO | Up to 3 bullets explaining why this strategy is active now. |
| `overlay_tags` | `array[string]` | NO | Active overlay tags (e.g., `VOL_TARGET_OVERLAY_REDUCED`, `LEVERAGE_DECAY_WARNING`). |
| `overlay_warnings` | `array[string]` | NO | Warning messages from overlays. |

**Current state:** `strategy_tag` is logged in `signal_log.jsonl` but is often empty string `""`. `strategy_weighted_score`, `allocation_weight`, `why_strategy_now`, `overlay_tags`, `overlay_warnings` exist on `SignalCard` but are NOT persisted to the signal log.

### 2.8 Track & Setup Block

| Field | Type | Required | Allowed Values | Description |
|---|---|---|---|---|
| `track` | `string` | YES | `SCALP`, `INTRADAY_SWING`, `OVERNIGHT_SWING` | Trade timeframe. |
| `setup_type` | `string` | NO | `continuation`, `breakout`, `mean_revert`, `default` | Inferred setup classification from `_infer_setup_type()` in `engine.py`. Determines stop/target ATR fractions. |
| `mode` | `string` | NO | `WIN_RATE`, `R_MULTIPLE` | Operating mode of the engine for this run. |

**Current state:** `track` is logged. `setup_type` exists on `TickerFeatures` and `PlayScore` but is NOT in the signal log.

### 2.9 Regime Block

| Field | Type | Required | Description |
|---|---|---|---|
| `regime_tag` | `string` | YES | 8-state regime from Master Spec Section 7: `TRENDING_UP_STRONG`, `TRENDING_UP_MOD`, `TRENDING_DOWN_STRONG`, `TRENDING_DOWN_MOD`, `RANGE_BOUND`, `HIGH_VOLATILITY`, `RISK_OFF`, `SHOCK`. |
| `regime_confidence` | `float` | YES | 0.0-1.0 confidence in regime classification. |

**Current state:** Both fields are logged. However, `regime_tag` sometimes uses legacy labels (e.g., `RISK_ON`, `NEUTRAL`) instead of the canonical 8-state labels.

### 2.10 Trade Levels Block

| Field | Type | Required | Description |
|---|---|---|---|
| `entry` | `float` | YES | Entry price (GBP for .L tickers). |
| `entry_zone_lo` | `float` | NO | Entry zone lower bound (entry * 0.999). |
| `entry_zone_hi` | `float` | NO | Entry zone upper bound (entry * 1.001). |
| `stop` | `float` | YES | Stop-loss price. |
| `target1` | `float` | YES | Primary target (T1). |
| `target2` | `float` | YES | Runner/secondary target (T2). |
| `time_stop_min` | `integer` | NO | Minutes: exit if not +0.3R by this time (half of full time stop). |
| `time_stop_full` | `integer` | NO | Full time stop in minutes (180 for swing, 12 for scalp). |
| `be_level` | `float` | NO | Break-even level (+0.6R from entry). |
| `partial_at` | `float` | NO | Price to take partial profit (+1R). |

**Current state:** `entry`, `stop`, `target1`, `target2` are logged. `entry_zone_lo/hi`, `time_stop_min/full`, `be_level`, `partial_at` exist on `SignalCard` but are NOT in the signal log.

### 2.11 Risk-Reward Block

| Field | Type | Required | Description |
|---|---|---|---|
| `rr_gross` | `float` | YES | Gross reward:risk ratio (before cost deduction). |
| `rr_net` | `float` | YES | Net reward:risk ratio (after spread + slippage deduction). |
| `stop_distance_pct` | `float` | NO | Stop distance as % of entry price. |
| `target1_distance_pct` | `float` | NO | T1 distance as % of entry price. |

**Current state:** `net_rr` is logged (single field). `rr_gross` is NOT logged. `stop_distance_pct` and `target1_distance_pct` exist on `SignalCard` but NOT in signal log.

### 2.12 Cost Model Block

| Field | Type | Required | Description |
|---|---|---|---|
| `spread_bps` | `float` | YES | Estimated bid-ask spread in basis points for this ticker. Source: `_SPREAD_BPS` lookup table in `engine.py`. |
| `slippage_bps` | `float` | YES | Estimated slippage per side in bps. Currently hardcoded at 5.0 bps. |
| `round_trip_cost_bps` | `float` | NO | Total round-trip cost: `(spread_bps + slippage_bps * 2)`. |
| `fx_fee_bps` | `float` | NO | T212 FX conversion fee (15 bps for USD-denominated ETPs). |

**Current state:** `spread_bps` is in the signal log but always `0.0` (never populated from the `_SPREAD_BPS` table). `slippage_bps`, `round_trip_cost_bps`, `fx_fee_bps` are NOT logged.

### 2.13 Data Quality Block

| Field | Type | Required | Description |
|---|---|---|---|
| `data_health_status` | `string` | YES | `PASS`, `WARN`, or `FAIL` from `DataHealthGate`. |
| `data_reliability_score` | `float` | YES | 0.0-1.0 score. `1.0 - reliability_penalty`. Penalty = `0.05 * max(0, 14 - n_bars)`. |
| `bars_available` | `integer` | YES | Number of OHLCV bars used for indicator computation. |
| `indicator_window_used` | `integer` | NO | Adaptive window size: `min(n_bars, 14)`. |
| `short_window` | `boolean` | NO | `true` if 7 <= bars < 14 (SHORT_WINDOW mode). |
| `quality_passed` | `boolean` | YES | Whether all hard gates (DATA_HEALTH, PRICE_SCALE, MIN_BARS) passed. |
| `price_scale_check` | `string` | NO | `PASS` or `FAIL` -- pence-vs-pounds detection result. |

**Current state:** NONE of these fields are in `signal_log.jsonl`. `data_reliability` exists on `SignalCard`. `bars_available`, `indicator_window_used`, `short_window` exist on `SignalCard`. `data_health_status` is evaluated but not persisted per-signal.

### 2.14 Feature Snapshot Block

| Field | Type | Required | Description |
|---|---|---|---|
| `rvol` | `float` | YES | Relative Volume (last bar vs trailing average). |
| `atr_pct` | `float` | YES | ATR as percentage of price. |
| `rsi` | `float` | NO | RSI-14 (or adaptive window RSI). |
| `adx` | `float` | NO | ADX-14. |
| `macd_hist` | `float` | NO | MACD histogram value. |
| `ema_aligned` | `boolean` | NO | Whether EMAs (9/20/50) are aligned with direction. |
| `bb_width_rank` | `float` | NO | Bollinger Band width percentile rank (0-1). |
| `momentum_score` | `float` | NO | 0-1 composite momentum from RSI + MACD + EMA alignment. |
| `volatility_score` | `float` | NO | 0-1 composite volatility opportunity from ATR + BB expansion. |
| `regime_fit_score` | `float` | NO | 0-1 regime compatibility score. |
| `liquidity_score` | `float` | NO | 0-1 liquidity score from RVOL. |
| `rr_score` | `float` | NO | 0-1 risk-reward score. |
| `quality_score` | `float` | NO | 0-1 quality/ADX score. |
| `composite` | `float` | YES | 0-100 PlayScore composite. |
| `stars` | `integer` | NO | 1-5 star rating. |

**Current state:** `rvol`, `atr_pct`, `composite` are logged. `rsi`, `adx`, `bb_width` are logged but always `0.0`. Component scores (`momentum_score` through `quality_score`) exist on `PlayScore`/`SignalCard` but are NOT in the signal log.

### 2.15 Data Freshness Block

| Field | Type | Required | Description |
|---|---|---|---|
| `ohlcv_as_of` | `string (ISO 8601)` | YES | Timestamp of the most recent OHLCV bar used. |
| `field_used` | `string` | NO | Which price field was primary: `"Close"` (default). |
| `yfinance_period` | `string` | NO | Period parameter passed to yfinance (e.g., `"5d"`). |
| `yfinance_interval` | `string` | NO | Interval parameter (e.g., `"1h"`). |

**Current state:** NONE of these fields exist. Data freshness is completely untracked.

### 2.16 Gate Audit Block

| Field | Type | Required | Description |
|---|---|---|---|
| `failed_gates` | `array[string]` | YES | List of gate names that failed for this signal (empty if all passed). Format: `["VOLUME_LIQUIDITY: RVOL=0.35 < 0.40", ...]`. |
| `fallback_step` | `integer` | YES | 0 = strict mode. 1-4 = which fallback relaxation was applied. |
| `gate_mode` | `string` | YES | `STRICT` or `FALLBACK_STEP{N}`. |
| `gates_summary` | `object` | NO | Full gate funnel result: `{gate_name: "PASS"/"FAIL"/"RELAXED", ...}`. |

**Current state:** `fallback_step` is available on `PlayScore` but NOT in the signal log. `failed_gates`, `gate_mode`, `gates_summary` are NOT logged per-signal. `TickerGateReport` contains all this data but is discarded after the run.

### 2.17 Risk Officer Block

| Field | Type | Required | Description |
|---|---|---|---|
| `risk_officer_decision` | `string` | YES | `APPROVE`, `DOWNSIZE`, or `VETO`. |
| `risk_officer_reasons` | `array[string]` | NO | List of reasons from all rules that fired. |
| `risk_adjustment_factor` | `float` | NO | 0.0-1.0 aggregate risk severity (0 = safe, 1 = full veto risk). |
| `rules_checked` | `array[string]` | NO | Names and outcomes of each rule: `["VOL_SHOCK:pass", "LIQUIDITY:DOWNSIZE", ...]`. |

**Current state:** `risk_officer_decision` is logged in signal log (always `"APPROVE"`). `risk_officer_reasons`, `risk_adjustment_factor`, `rules_checked` exist on `SignalCard` but are NOT in the signal log.

### 2.18 Sizing & Execution Plan Block

| Field | Type | Required | Description |
|---|---|---|---|
| `sizing_hint` | `string` | YES | `XS`, `S`, `M`, `L`. |
| `sizing_reason` | `string` | NO | Why this sizing was chosen. |
| `execution_plan` | `object` | NO | Full execution plan object (see sub-schema below). |

**Execution plan sub-schema:**

| Sub-field | Type | Description |
|---|---|---|
| `order_type` | `string` | `LIMIT` or `MARKETABLE_LIMIT`. |
| `max_slippage_bps` | `float` | Maximum acceptable slippage in bps. |
| `spread_proxy_bps` | `float` | Estimated spread for this ticker. |
| `spread_gate_result` | `string` | `PASS`, `WATCH`, or `VETO`. |
| `cancel_conditions` | `array[string]` | Conditions that cancel the order. |
| `time_in_trade_window` | `string` | Time-of-day window this trade targets. |

**Current state:** `sizing_hint` is logged. `execution_plan` exists on `SignalCard` but is NOT in the signal log.

### 2.19 Upgrade Condition Block (WATCH signals only)

| Field | Type | Required | Description |
|---|---|---|---|
| `upgrade_condition` | `string` | NO (required if decision = WATCH) | Human-readable condition that would promote this from WATCH to TRADE. e.g., "Upgrade to TRADE if RVOL rises above 0.80 within 30 min". |
| `closest_miss_gate` | `string` | NO | Which gate blocked this signal from being STRICT. |
| `closest_miss_delta` | `float` | NO | How far the observed value was from the threshold. |

**Current state:** `why_fallback` on `SignalCard` partially covers this. `ClosestMiss` in `DroughtPackage` captures closest-miss data but is NOT linked to individual signal records.

### 2.20 Outcome Block (Post-Resolution)

| Field | Type | Required | Description |
|---|---|---|---|
| `outcome` | `string` | YES | `PENDING`, `HIT_TARGET`, `HIT_STOP`, `TIME_STOP`, `AMBIGUOUS`, `SCRATCH`, `EXPIRED`, `RESOLVED`. |
| `exit_price` | `float` | NO | Price at resolution. |
| `pnl_r_gross` | `float` | NO | Gross P&L in R-multiples. |
| `pnl_r_net` | `float` | NO | Net P&L after costs. |
| `mfe_pct` | `float` | NO | Maximum Favourable Excursion as %. |
| `mae_pct` | `float` | NO | Maximum Adverse Excursion as %. |
| `duration_minutes` | `integer` | NO | How long the trade lasted. |
| `closed_at` | `string (ISO 8601)` | NO | When the outcome was resolved. |

**Current state:** `outcome` is logged (as `"PENDING"` or `"RESOLVED"`). All other outcome fields are tracked in `OutcomeRecord` in `learning/schemas.py` but are in a separate file (`data/outcome_log.jsonl`), not joined to the signal record.

### 2.21 Liquidity Metadata Block

| Field | Type | Required | Description |
|---|---|---|---|
| `liquidity_bucket` | `string` | YES | `HIGH` (RVOL >= 2.0), `NORMAL` (>= 1.2), `LOW` (>= 0.7), `THIN` (< 0.7), `UNKNOWN` (RVOL unavailable). |
| `spread_risk` | `string` | NO | `LOW`, `HIGH` from `PlayScore.spread_risk`. |
| `decay_risk` | `string` | NO | `LOW`, `HIGH` -- leveraged ETP daily volatility decay risk. |

**Current state:** `liquidity_bucket` is logged. `spread_risk` and `decay_risk` exist on `PlayScore`/`SignalCard` but are NOT in the signal log.

---

## 3. Gap Analysis: Current vs. Canonical

### 3.1 Fields Currently Logged (signal_log.jsonl)

The `SignalLogRecord` in `learning/schemas.py` and the actual JSONL output contain these 26 fields:

```
signal_id, ticker, direction, strategy_tag, regime_tag, regime_confidence,
time_window, track, session, composite, entry, stop, target1, target2,
net_rr, generated_at, date_str, rvol, atr_pct, bb_width, rsi, adx,
spread_bps, liquidity_bucket, risk_officer_decision, sizing_hint, outcome
```

### 3.2 Critical Missing Fields (MUST ADD -- Audit Failures)

These fields are required for institutional audit compliance and are completely absent from the signal log.

| # | Missing Field | Why Critical | Source in Codebase |
|---|---|---|---|
| 1 | `run_id` | Cannot correlate signals to pipeline run | `pipeline_runner.py:88` -- generated but not passed to logger |
| 2 | `git_hash` | Cannot reproduce the exact code that generated the signal | Not computed anywhere |
| 3 | `config_hash` | Cannot verify configuration at signal time | Not computed anywhere |
| 4 | `universe_hash` | Cannot verify which tickers were scanned | Not computed anywhere |
| 5 | `time_logged_uk` | UK-local timestamp needed for LSE session alignment | Not computed in logger |
| 6 | `tier` | No way to distinguish CORE vs PEER vs FULL_SCAN signals | `pipeline_runner.py` sets it but logger ignores it |
| 7 | `objective_tag` | No way to know if signal targeted MAX_GAINS or 2% cap | Does not exist |
| 8 | `decision` | No explicit TRADE/WATCH/INTEL/ABSTAIN decision | Implicit in `category` but not canonical |
| 9 | `data_health_status` | Cannot verify data quality at signal time | `DataHealthGate` evaluates but discards |
| 10 | `data_reliability_score` | Cannot assess indicator quality | Exists on `SignalCard`, not logged |
| 11 | `bars_available` | Cannot assess data depth | Exists on `SignalCard`, not logged |
| 12 | `quality_passed` | Cannot verify hard gate passage | `TickerGateReport.all_passed` -- discarded |
| 13 | `failed_gates` | Cannot reconstruct why signals were excluded | `TickerGateReport.gates` -- discarded |
| 14 | `fallback_step` | Cannot distinguish strict from relaxed signals | Exists on `PlayScore`, not logged |
| 15 | `ohlcv_as_of` | Cannot verify data freshness | Not tracked anywhere |
| 16 | `instrument_traded` | Semantic alias for `ticker` -- explicit naming for audit | Rename of existing field |

### 3.3 Important Missing Fields (SHOULD ADD -- Quality Gaps)

| # | Missing Field | Source in Codebase | Impact of Missing |
|---|---|---|---|
| 17 | `underlying_proxy` | `isa_universe.py:LEVERAGE_MAP` | Cannot link ETP signals to underlying assets |
| 18 | `leverage_multiple` | `isa_universe.py:LEVERAGE_MAP` | Cannot assess leverage risk per signal |
| 19 | `is_inverse_etp` | `engine.py:INVERSE_ETPS` | Cannot filter/audit inverse-only plays |
| 20 | `factor_group` | `TickerFeatures.factor_group` | Cannot audit factor concentration |
| 21 | `setup_type` | `TickerFeatures.setup_type` | Cannot reconstruct stop/target ATR fractions |
| 22 | `entry_zone_lo/hi` | `SignalCard.entry_zone_lo/hi` | Execution precision lost |
| 23 | `time_stop_full` | `SignalCard.time_stop_full` | Time management rules invisible |
| 24 | `rr_gross` | `TickerFeatures.rr_net` computed from gross | Cannot separate cost impact from signal quality |
| 25 | `cost model inputs` | `_SPREAD_BPS`, `_SLIPPAGE_BPS` in engine.py | Cannot audit cost assumptions |
| 26 | `execution_plan` | `SignalCard.execution_plan` | Cannot audit order management rules |
| 27 | `risk_officer_reasons` | `RiskDecision.reasons` | Cannot audit risk governance |
| 28 | `risk_adjustment_factor` | `RiskDecision.risk_score` | Cannot reconstruct final rank score |
| 29 | `upgrade_condition` | Partially in `why_fallback` | WATCH signals lack actionable upgrade criteria |
| 30 | Component scores | `PlayScore.momentum/volatility/...` | Cannot decompose composite score |

### 3.4 Fields Logged but Broken

| Field | Issue | Root Cause |
|---|---|---|
| `spread_bps` | Always `0.0` | `SignalLogger.log_signal()` reads `g("spread_bps", 0.0)` from the play, but `PlayScore` does not have a `spread_bps` attribute. The cost model's `_SPREAD_BPS` table is in `engine.py` and is only used to compute `rr_net`, never attached to the play object. |
| `rsi` | Always `0.0` | Same issue: `PlayScore` does not carry RSI. `TickerFeatures` has RSI but is not passed to the logger. |
| `adx` | Always `0.0` | Same: lives on `TickerFeatures`, not on `PlayScore`. |
| `bb_width` | Always `0.0` | Same: `bb_width_rank` is on `TickerFeatures`, not surfaced as `bb_width`. |
| `strategy_tag` | Often empty `""` | Strategy router enrichment happens AFTER `PlayScore` creation. The tag is set on `SignalCard` but the logger reads from `PlayScore`. |
| `time_window` | Often `"UNKNOWN"` | `time_window` is not an attribute of `PlayScore`. It exists on `RouterResult.time_of_day_window` and `SignalCard.time_of_day_window`. |
| `risk_officer_decision` | Always `"APPROVE"` | Logger sets default `"APPROVE"`. Risk Officer runs AFTER the signal card is created and enriched, but the logger reads from the raw `PlayScore` which predates risk evaluation. |
| `regime_tag` | Uses legacy labels | Logger receives the regime string from `engine.run()` which may be `"NEUTRAL"` or `"RISK_ON"` rather than the canonical 8-state label. The router normalises it but this happens after logging. |

### 3.5 Architectural Root Cause

The logging pipeline has a fundamental ordering problem:

```
1. engine.run()
   2. _build_features()    --> TickerFeatures (has RSI, ADX, etc.)
   3. run_full_gate_funnel() --> TickerGateReport (has gate outcomes)
   4. compute_play_score() --> PlayScore (has composite, but NOT features)
   5. SignalRecord.from_play_score() --> emitted to tape

6. pipeline_runner.run_pipeline()
   7. sig_logger.log_plays(plays)  <-- LOGGING HAPPENS HERE (from PlayScore)

8. Back in engine.run()
   9. StrategyRouter.run()     --> enriches with strategy_tag
  10. SignalCard.from_play_score() --> builds full card
  11. RiskOfficer.evaluate()   --> enriches with risk decision
  12. write_plays_artifact()   --> full-fidelity artifact written
```

**The logger (step 7) captures data BEFORE strategy enrichment (step 9) and risk evaluation (step 11).** The `plays.json` artifact (step 12) has full fidelity, but the `signal_log.jsonl` does not.

---

## 4. Implementation Requirements

### 4.1 Where Changes MUST Go

| Change | File | Specifics |
|---|---|---|
| Add provenance (git_hash, config_hash, universe_hash) | `signal_engine/pipeline_runner.py` | Compute at pipeline start, pass through to logger |
| Move logging AFTER enrichment | `signal_engine/engine.py` | Log from `SignalCard` objects (after step 12), not `PlayScore` objects (step 7) |
| Expand `SignalLogRecord` | `learning/schemas.py` | Add all missing fields from Section 2 |
| Expand `make_signal_id()` | `learning/schemas.py` | No change needed -- current hash is adequate |
| Populate `spread_bps` from cost model | `signal_engine/engine.py` or `learning/signal_logger.py` | Attach `_SPREAD_BPS[ticker]` to play/card before logging |
| Carry features to log | `signal_engine/engine.py` | Attach `rsi`, `adx`, `bb_width_rank`, `macd_hist` from `TickerFeatures` to `PlayScore` or `SignalCard` |
| Add `objective_tag` | `signal_engine/engine.py` or `pipeline_runner.py` | Derive from tier: CORE -> MAX_INTRADAY_GAINS, non-core -> 2PCT_CAP |
| Add `decision` field | `signal_engine/signal_card.py` | Map: category=TRADE + fallback_step=0 -> TRADE, category=WATCH -> WATCH, etc. |
| Persist gate reports | `learning/signal_logger.py` | Accept gate report alongside play, extract `failed_gates` and `gate_mode` |
| Add `time_logged_uk` | `learning/signal_logger.py` | `datetime.now(ZoneInfo("Europe/London")).isoformat()` |
| Add `ohlcv_as_of` | `signal_engine/engine.py` | Extract `df.index[-1]` timestamp from OHLCV data, attach to `TickerFeatures` |
| Add `upgrade_condition` | `signal_engine/signal_card.py` | For WATCH signals: generate condition string from closest missed gate |

### 4.2 Migration Path

1. **Phase 1 (non-breaking):** Add new fields to `SignalLogRecord` with defaults. Existing log files remain valid.
2. **Phase 2 (enrichment fix):** Move logging call from `pipeline_runner.py` step 7 to after `engine.py` step 12. Log from `SignalCard` objects.
3. **Phase 3 (provenance):** Add `git_hash`, `config_hash`, `universe_hash` computation at pipeline start.
4. **Phase 4 (data freshness):** Add `ohlcv_as_of` tracking to `TickerFeatures`.
5. **Phase 5 (validation):** Add JSON Schema validation (see `schemas/signal_record.schema.json`) to logger.

---

## 5. Inverse ETP Direction Semantics

This section clarifies a critical semantic that MUST be correct in every signal record.

### 5.1 ISA Buy-Only Constraint

In a Trading 212 ISA, you can only BUY (never short-sell). Therefore:
- **Bullish Nasdaq view** = BUY `QQQ3.L` (3x Long Nasdaq)
- **Bearish Nasdaq view** = BUY `QQQS.L` (3x Short/Inverse Nasdaq)

Both are `direction: "LONG"` in the signal record (because both are BUY orders).

### 5.2 Correct `instrument_traded` Assignment

| Market View | Instrument | Direction | is_inverse_etp | Regime Gate Treatment |
|---|---|---|---|---|
| Bullish Nasdaq | `QQQ3.L` | LONG | false | LONG vs regime |
| Bearish Nasdaq | `QQQS.L` | LONG | true | SHORT vs regime (flipped) |
| Bullish S&P | `3LUS.L` | LONG | false | LONG vs regime |
| Bearish S&P | `3USS.L` | LONG | true | SHORT vs regime (flipped) |
| Bullish NVIDIA | `NVD3.L` | LONG | false | LONG vs regime |
| Bullish Tesla | `TSL3.L` | LONG | false | LONG vs regime |
| Bullish TSMC | `TSM3.L` | LONG | false | LONG vs regime |
| Bullish Micron | `MU2.L` | LONG | false | LONG vs regime |
| Bullish Nasdaq 5x | `QQQ5.L` | LONG | false | LONG vs regime |
| Bullish S&P 5x | `SP5L.L` | LONG | false | LONG vs regime |
| Bullish AI/GPT | `GPT3.L` | LONG | false | LONG vs regime |
| Bullish Semis | `3SEM.L` | LONG | false | LONG vs regime |

### 5.3 Current Code Reference

The inverse ETP set is defined in `signal_engine/engine.py` line 65:
```python
INVERSE_ETPS = {"QQQS.L", "3USS.L", "NVDS.L", "TSLS.L"}
```

The regime gate flips direction for inverse ETPs in `signal_engine/gates.py` line 211-234:
```python
effective_dir = direction
if is_inverse:
    effective_dir = "SHORT" if direction == "LONG" else "LONG"
```

---

## 6. CORE Tickers -- Objective Tag Reference

| Ticker | Underlying | Leverage | Tier | Objective Tag |
|---|---|---|---|---|
| `QQQ3.L` | Nasdaq 100 | 3x Long | CORE | `MAX_INTRADAY_GAINS` |
| `3LUS.L` | S&P 500 | 3x Long | CORE | `MAX_INTRADAY_GAINS` |
| `3SEM.L` | PHLX Semiconductor | 3x Long | CORE | `MAX_INTRADAY_GAINS` |
| `GPT3.L` | Solactive US AI | 3x Long | CORE | `MAX_INTRADAY_GAINS` |
| `NVD3.L` | NVIDIA | 3x Long | CORE | `MAX_INTRADAY_GAINS` |
| `TSL3.L` | Tesla | 3x Long | CORE | `MAX_INTRADAY_GAINS` |
| `TSM3.L` | TSMC | 3x Long | CORE | `MAX_INTRADAY_GAINS` |
| `MU2.L` | Micron | 2x Long | CORE | `MAX_INTRADAY_GAINS` |
| `QQQS.L` | Nasdaq 100 | 3x Short | CORE | `MAX_INTRADAY_GAINS` |
| `3USS.L` | S&P 500 | 3x Short | CORE | `MAX_INTRADAY_GAINS` |
| `QQQ5.L` | Nasdaq 100 | 5x Long | CORE | `MAX_INTRADAY_GAINS` |
| `SP5L.L` | S&P 500 | 5x Long | CORE | `MAX_INTRADAY_GAINS` |

Non-core tickers (EXTENDED_UNIVERSE minus CORE): `objective_tag = "2PCT_CAP"`
INTEL_UNIVERSE tickers: `objective_tag = "INTEL_ONLY"`

---

## 7. Session Definitions

| Session Key | Schedule (UK) | Description |
|---|---|---|
| `PRE_LSE` | 07:00 | Pre-market scan before LSE opens at 08:00 |
| `LSE` | 08:00-16:30 | London Stock Exchange active trading hours |
| `PRE_NYSE` | 13:30 | Pre-NYSE overlap scan |
| `NYSE` | 14:30-21:00 | NYSE active hours (affects ETP prices) |
| `EOD` | 22:00 | End-of-day review and overnight positioning |
| `preview_*` | On-demand | Preview/test runs, not scheduled |

---

## 8. Example Canonical Signal Record

```json
{
  "run_id": "A3F7B2C1",
  "git_hash": "a1b2c3d4e5f6",
  "config_hash": "sha256:9f86d081884c...",
  "universe_hash": "sha256:7c211433f02...",
  "engine_version": "4.0.0",

  "signal_id": "NZT-CB51AC",
  "time_logged_utc": "2026-02-27T08:30:15.123456+00:00",
  "time_logged_uk": "2026-02-27T08:30:15.123456+00:00",
  "date_str": "2026-02-27",

  "session": "PRE_LSE",
  "time_of_day_window": "MORNING_MOMENTUM",

  "instrument_traded": "QQQ3.L",
  "direction": "LONG",
  "underlying_proxy": "Nasdaq 100",
  "leverage_multiple": "3x",
  "factor_group": "nasdaq_beta_long",
  "is_inverse_etp": false,

  "tier": "CORE",
  "objective_tag": "MAX_INTRADAY_GAINS",

  "decision": "TRADE",
  "decision_reasons": [
    "Momentum aligned (RSI=62, MACD positive)",
    "ATR=2.1% -- routinely moves 2%+",
    "Regime (TRENDING_UP_MOD) favours LONG",
    "R:R=2.1 -- favourable risk/reward"
  ],
  "category": "TRADE",
  "label": "STRICT",

  "strategy_tag": "TREND_MOMENTUM_CTA",
  "strategy_weighted_score": 82.5,
  "allocation_weight": 0.3200,
  "why_strategy_now": [
    "Regime TRENDING_UP_MOD: trend confirmed (ADX 28 >= 20)",
    "EMA alignment + ADX confirm directional momentum",
    "CTA/managed-futures intraday adaptation active"
  ],
  "overlay_tags": ["TSM_TREND_OVERLAY_NA"],
  "overlay_warnings": [],

  "track": "INTRADAY_SWING",
  "setup_type": "continuation",
  "mode": "WIN_RATE",

  "regime_tag": "TRENDING_UP_MOD",
  "regime_confidence": 0.75,

  "entry": 24.50,
  "entry_zone_lo": 24.4755,
  "entry_zone_hi": 24.5245,
  "stop": 23.98,
  "target1": 25.54,
  "target2": 25.80,
  "time_stop_min": 90,
  "time_stop_full": 180,
  "be_level": 24.812,
  "partial_at": 25.02,

  "rr_gross": 2.20,
  "rr_net": 2.00,
  "stop_distance_pct": 2.122,
  "target1_distance_pct": 4.245,

  "spread_bps": 15.0,
  "slippage_bps": 5.0,
  "round_trip_cost_bps": 25.0,
  "fx_fee_bps": 0.0,

  "data_health_status": "PASS",
  "data_reliability_score": 1.0,
  "bars_available": 35,
  "indicator_window_used": 14,
  "short_window": false,
  "quality_passed": true,
  "price_scale_check": "PASS",

  "rvol": 1.42,
  "atr_pct": 2.10,
  "rsi": 62.3,
  "adx": 28.1,
  "macd_hist": 0.045,
  "ema_aligned": true,
  "bb_width_rank": 0.65,
  "momentum_score": 0.73,
  "volatility_score": 0.68,
  "regime_fit_score": 0.90,
  "liquidity_score": 0.47,
  "rr_score": 0.50,
  "quality_score": 0.56,
  "composite": 75.0,
  "stars": 3,

  "ohlcv_as_of": "2026-02-27T07:00:00+00:00",
  "field_used": "Close",
  "yfinance_period": "5d",
  "yfinance_interval": "1h",

  "failed_gates": [],
  "fallback_step": 0,
  "gate_mode": "STRICT",
  "gates_summary": {
    "DATA_HEALTH": "PASS",
    "PRICE_SCALE": "PASS",
    "MIN_BARS": "PASS",
    "TRADABILITY": "PASS",
    "VOLUME_LIQUIDITY": "PASS",
    "REGIME_FIT": "PASS",
    "RR_RATIO": "PASS",
    "MOMENTUM_ALIGNMENT": "PASS",
    "FACTOR_CAP": "PASS"
  },

  "risk_officer_decision": "APPROVE",
  "risk_officer_reasons": [],
  "risk_adjustment_factor": 0.0,
  "rules_checked": [
    "VOL_SHOCK:pass",
    "LIQUIDITY:pass",
    "DRAWDOWN:pass",
    "EVENT_WINDOW:pass",
    "DATA_RELIABILITY:pass",
    "CORRELATION:pass"
  ],

  "sizing_hint": "M",
  "sizing_reason": "Standard sizing for TRENDING_UP_MOD regime",
  "execution_plan": {
    "order_type": "MARKETABLE_LIMIT",
    "max_slippage_bps": 10.0,
    "spread_proxy_bps": 15.0,
    "spread_gate_result": "PASS",
    "cancel_conditions": [
      "price moves >2.1% against entry before fill",
      "session closes before fill"
    ],
    "time_in_trade_window": "MORNING_MOMENTUM"
  },

  "upgrade_condition": null,
  "closest_miss_gate": null,
  "closest_miss_delta": null,

  "liquidity_bucket": "NORMAL",
  "spread_risk": "LOW",
  "decay_risk": "LOW",

  "outcome": "PENDING",
  "exit_price": null,
  "pnl_r_gross": null,
  "pnl_r_net": null,
  "mfe_pct": null,
  "mae_pct": null,
  "duration_minutes": null,
  "closed_at": null
}
```

---

## 9. Validation Rules

1. `signal_id` MUST match pattern `^NZT-[A-F0-9]{6}$`.
2. `instrument_traded` MUST be a member of `CORE_UNIVERSE + EXTENDED_UNIVERSE`.
3. `direction` MUST be `"LONG"` for all ISA signals (buy-only constraint).
4. If `tier == "CORE"`, then `objective_tag` MUST be `"MAX_INTRADAY_GAINS"`.
5. If `decision == "TRADE"`, then `entry > 0`, `stop > 0`, `target1 > 0`.
6. `stop < entry` for LONG direction (stop must be below entry).
7. `target1 > entry` for LONG direction (target must be above entry).
8. `rr_net >= 1.2` for any signal with `decision == "TRADE"` (absolute floor from fallback step 2).
9. `data_reliability_score >= 0.50` for any signal with `decision == "TRADE"` (below 0.50 = VETO).
10. `composite` must be in range [0, 100].
11. `regime_confidence` must be in range [0.0, 1.0].
12. `fallback_step` must be in range [0, 4].
13. `risk_officer_decision` MUST be one of: `"APPROVE"`, `"DOWNSIZE"`, `"VETO"`.
14. If `risk_officer_decision == "VETO"`, then `decision` MUST NOT be `"TRADE"`.
15. `time_logged_utc` MUST be a valid ISO 8601 datetime with timezone.
