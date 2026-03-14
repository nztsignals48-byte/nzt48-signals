# NZT-48 COMPREHENSIVE TEST PLAN

**Version**: 1.0
**Date**: 2026-02-27
**Status**: PLAN ONLY -- no tests executed yet
**Scope**: All code paths identified in FORENSICS_MAP.md + INSTITUTIONAL_PLAN_110.md
**Traceability**: Every test has a unique ID (T-UNIT-xxx, T-INT-xxx, T-REG-xxx, T-PW-xxx, T-PDF-xxx, T-PERF-xxx)

---

## TABLE OF CONTENTS

1. [Unit Tests](#1-unit-tests)
2. [Integration Tests](#2-integration-tests)
3. [Regression Tests](#3-regression-tests)
4. [Playwright War Room QA Plan](#4-playwright-war-room-qa-plan)
5. [PDF QA Plan](#5-pdf-qa-plan)
6. [Performance / SLA Checks](#6-performance--sla-checks)

---

## 1. UNIT TESTS

### 1.1 Percent Math -- Leverage-Once Policy

Tests that all return calculations apply leverage ONCE and only once. Leveraged ETPs (3x, 5x) embed leverage in their price moves. Any code that multiplies by a leverage factor on top of the ETP's own move double-leverages the return.

| Test ID | Description | Input | Expected Output | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-UNIT-001 | Raw price change with 3x leverage factor produces correct single-leveraged return | `raw_change=+2.0%`, `leverage=3` | Return = `+2.0%` (NOT `+6.0%`; the ETP already embeds 3x) | `delivery/pdf_v2_momentum.py:346-431` |
| T-UNIT-002 | Raw price change with 5x leverage factor produces correct return | `raw_change=+1.5%`, `leverage=5` | Return = `+1.5%` (price change IS the leveraged return) | `delivery/pdf_v2_momentum.py` |
| T-UNIT-003 | Zero percent change produces zero return regardless of leverage | `raw_change=0.0%`, `leverage=3` | Return = `0.0%` | `delivery/pdf_v2_momentum.py`, `delivery/pdf_v2_risk.py` |
| T-UNIT-004 | Boundary: exactly +30% change accepted (large but plausible for 5x ETP) | `raw_change=+30.0%`, `leverage=5` | Return = `+30.0%`, no rejection | `delivery/pdf_v2_momentum.py` |
| T-UNIT-005 | Boundary: exactly -30% change accepted | `raw_change=-30.0%`, `leverage=5` | Return = `-30.0%`, no rejection | `delivery/pdf_v2_momentum.py` |
| T-UNIT-006 | Negative price input rejected | `close=-5.00`, `prev_close=100.00` | Raise `ValueError` or return `None` with logged warning | All return computation paths |
| T-UNIT-007 | Both prices negative rejected | `close=-5.00`, `prev_close=-10.00` | Raise `ValueError` or return `None` | All return computation paths |
| T-UNIT-008 | Vol-decay scoring uses leverage factor correctly for SCORING (not returns) | `atr_pct=2.5`, `leverage=3` | `vol_decay_score = min(100, 2.5 * 3 * 3) = 22.5` | `delivery/pdf_v2_risk.py:394` |
| T-UNIT-009 | Predictive scoring uses raw indicators without re-leveraging | `rsi=65`, `macd_hist=0.5`, `leverage=3` | Scoring based on raw RSI/MACD values, NOT multiplied by 3 | `uk_isa/predictive_scoring.py:532-901` |
| T-UNIT-010 | S15 2% reachability uses ATR% from price (already leveraged) | `atr_pct=3.5%`, `target=2.0%` | Reachability computed from raw ATR%, no leverage multiplication | `strategies/daily_target.py:75-89` |

### 1.2 Division-by-Zero Guards

Every identified div/0 location from FORENSICS_MAP must produce a safe fallback value, never crash.

| Test ID | Description | Input | Expected Output | Target File:Line |
|---------|-------------|-------|-----------------|------------------|
| T-UNIT-011 | RVOL division: avg_vol_20 = 0 | `last_vol=50000`, `avg_vol_20=0` | Return fallback `rvol=0.0` or `rvol=1.0`, no ZeroDivisionError | `delivery/pdf_v2_risk.py:399` |
| T-UNIT-012 | RVOL division: both volumes zero | `last_vol=0`, `avg_vol_20=0` | Return fallback, no crash | `delivery/pdf_v2_risk.py:399` |
| T-UNIT-013 | Long points / max points: max_pts = 0 | `long_pts=5`, `max_pts=0` | Return `0.0` or `50.0` (neutral), no crash | `delivery/pdf_v2_momentum.py:429` |
| T-UNIT-014 | Long points / max points: both zero | `long_pts=0`, `max_pts=0` | Return `0.0`, no crash | `delivery/pdf_v2_momentum.py:429` |
| T-UNIT-015 | VIX ratio: vix_ann_vol = 0 | `ann_vol=15.5`, `vix_ann_vol=0.0` | Return fallback `vix_ratio=1.0`, no crash | `uk_isa/volatility_regime.py:254` |
| T-UNIT-016 | Predictive scoring: denominator = 0 in component scoring | `component_max=0`, `component_raw=5.0` | Return `0.0`, no crash | `uk_isa/predictive_scoring.py:867` |
| T-UNIT-017 | Predictive scoring: second denominator = 0 | `weight_sum=0.0` | Return `0.0`, no crash | `uk_isa/predictive_scoring.py:868` |
| T-UNIT-018 | LSE registry: single-bar price change | DataFrame with exactly 1 row | Return `0.0` change, no IndexError | `uk_isa/lse_registry.py:331-333` |
| T-UNIT-019 | LSE registry: empty DataFrame | `df = pd.DataFrame()` | Return `None` or `0.0`, log warning | `uk_isa/lse_registry.py:331-333` |
| T-UNIT-020 | Bollinger band width: SMA = 0 | `upper=2.0`, `lower=1.0`, `sma=0.0` | Return fallback `bb_width=0.0`, no crash | `delivery/pdf_v2_momentum.py:382-383` |
| T-UNIT-021 | Close[-1] = 0 in momentum bias | `close_series=[0.0, 1.0, 2.0]` | Return `0.0` or skip ticker, no crash | `delivery/pdf_v2_momentum.py:314,320` |

### 1.3 Confidence Bounds

Confidence values must be clamped to [0, 100]. Values outside this range indicate a bug in a scoring layer.

| Test ID | Description | Input | Expected Output | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-UNIT-022 | Confidence = -1 rejected | `confidence=-1` | Clamped to `0` or rejected with error | `qualification/confidence_scorer.py` |
| T-UNIT-023 | Confidence = 0 accepted (edge) | `confidence=0` | Accepted; signal will be killed by confidence gate (< 60) | `qualification/qualifier.py` |
| T-UNIT-024 | Confidence = 50 accepted, below threshold | `confidence=50` | Accepted; blocked by confidence gate (< 60 threshold) | `qualification/qualifier.py` |
| T-UNIT-025 | Confidence = 60 accepted, passes threshold | `confidence=60` | Accepted; passes confidence gate | `qualification/qualifier.py` |
| T-UNIT-026 | Confidence = 100 accepted (edge) | `confidence=100` | Accepted; passes confidence gate | `qualification/qualifier.py` |
| T-UNIT-027 | Confidence = 101 rejected | `confidence=101` | Clamped to `100` or rejected with error | `qualification/confidence_scorer.py` |
| T-UNIT-028 | Confidence = NaN rejected | `confidence=float('nan')` | Rejected; signal blocked | `qualification/qualifier.py` |
| T-UNIT-029 | AI enhancement cannot push confidence above 100 | `base_confidence=98`, `ai_adjustment=+5` | Final confidence = `100` (clamped) | `main.py:1505-1508` |
| T-UNIT-030 | AI enhancement cannot push confidence below 0 | `base_confidence=2`, `ai_adjustment=-5` | Final confidence = `0` (clamped) | `main.py:1505-1508` |

### 1.4 Score Validation

Score = 0 must be BLOCKED at the signal gate. A score-0 signal reaching Telegram is a critical bug.

| Test ID | Description | Input | Expected Output | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-UNIT-031 | Score = 0 blocked before Telegram | `score=0`, `confidence=65` | Signal BLOCKED; never reaches `telegram_bot.py:_gated_send()` | `strategies/daily_target.py:44-69`, `main.py:1014-1051` |
| T-UNIT-032 | Score = 0 with high confidence still blocked | `score=0`, `confidence=95` | Signal BLOCKED (score gate takes priority) | `main.py`, `telegram_bot.py:731-759` |
| T-UNIT-033 | Score = None blocked | `score=None`, `confidence=70` | Signal BLOCKED with MISSING_SCORE reason | `telegram_bot.py:731-759` |
| T-UNIT-034 | Score = negative blocked | `score=-5`, `confidence=70` | Signal BLOCKED with INVALID_SCORE reason | All scoring paths |
| T-UNIT-035 | Score = 1 (minimum valid) passes score gate | `score=1`, `confidence=65` | Passes score gate (may be blocked by other gates) | `main.py` |
| T-UNIT-036 | Telegram validate_telegram_signal rejects score=0 | `play.score=0` | `validate_telegram_signal()` returns False | `telegram_bot.py:731-759` |

### 1.5 OHLC Integrity

Bad OHLC data must be rejected at the bar validation layer before entering any strategy or scoring pipeline.

| Test ID | Description | Input | Expected Output | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-UNIT-037 | High < Low rejected | `open=100, high=95, low=100, close=98, volume=1000` | Bar rejected with `OHLC_INTEGRITY_FAIL` | `main.py:630-638` |
| T-UNIT-038 | Negative price rejected | `open=-1.0, high=5.0, low=-2.0, close=3.0` | Bar rejected | `main.py:630-638` |
| T-UNIT-039 | Zero close price rejected | `open=1.0, high=2.0, low=0.5, close=0.0` | Bar rejected (prevents div/0 downstream) | `main.py:630-638` |
| T-UNIT-040 | Negative volume rejected | `open=1.0, high=2.0, low=0.5, close=1.5, volume=-100` | Bar rejected with `NEGATIVE_VOLUME` | `main.py:630-638` |
| T-UNIT-041 | Zero volume accepted (pre/post market) | `open=1.0, high=2.0, low=0.5, close=1.5, volume=0` | Bar accepted (zero volume is valid outside market hours) | `main.py:630-638` |
| T-UNIT-042 | Open outside High/Low range rejected | `open=110, high=105, low=95, close=100` | Bar rejected (open > high is invalid) | `main.py:630-638` |
| T-UNIT-043 | Close outside High/Low range rejected | `open=100, high=105, low=95, close=110` | Bar rejected (close > high is invalid) | `main.py:630-638` |
| T-UNIT-044 | NaN in any OHLCV field rejected | `open=NaN` | Bar rejected with `NAN_FIELD` | `main.py:630-638` |

### 1.6 Regime Classification

Test each of the 5 volatility regimes from `uk_isa/volatility_regime.py` with boundary inputs. The 5 regimes: COMPRESSION, EXPANSION, BLOW_OFF, EXHAUSTION, BREAKDOWN.

| Test ID | Description | Input | Expected Output | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-UNIT-045 | COMPRESSION regime: low volatility, narrow BBs | `bb_width=0.02`, `atr_pct=0.8`, `vix_ratio=0.5` | Regime = `COMPRESSION` with high probability | `uk_isa/volatility_regime.py:260-313` |
| T-UNIT-046 | EXPANSION regime: rising volatility, widening BBs | `bb_width=0.08`, `atr_pct=3.5`, `vix_ratio=1.2` | Regime = `EXPANSION` | `uk_isa/volatility_regime.py:260-313` |
| T-UNIT-047 | BLOW_OFF regime: extreme volatility spike | `bb_width=0.15`, `atr_pct=8.0`, `vix_ratio=2.5` | Regime = `BLOW_OFF` | `uk_isa/volatility_regime.py:260-313` |
| T-UNIT-048 | EXHAUSTION regime: high volatility decelerating | `bb_width=0.10`, `atr_pct=5.0`, `vix_ratio=1.8`, `vol_trend=declining` | Regime = `EXHAUSTION` | `uk_isa/volatility_regime.py:260-313` |
| T-UNIT-049 | BREAKDOWN regime: volatility collapse after blow-off | `bb_width=0.12`, `atr_pct=6.0`, `vix_ratio=2.0`, `price_trend=sharply_down` | Regime = `BREAKDOWN` | `uk_isa/volatility_regime.py:260-313` |
| T-UNIT-050 | Boundary: COMPRESSION/EXPANSION threshold exact value | Input at exact threshold between COMPRESSION and EXPANSION | Must resolve to one (no UNKNOWN), probability sum = 1.0 | `uk_isa/volatility_regime.py:260-313` |
| T-UNIT-051 | Boundary: EXPANSION/BLOW_OFF threshold exact value | Input at exact threshold | Must resolve to one, probability sum = 1.0 | `uk_isa/volatility_regime.py:260-313` |
| T-UNIT-052 | All zero inputs produce a valid regime (not crash) | `bb_width=0`, `atr_pct=0`, `vix_ratio=0` | Returns a regime (likely COMPRESSION), no crash | `uk_isa/volatility_regime.py:260-313` |
| T-UNIT-053 | Regime probabilities always sum to 1.0 (within floating point) | Any valid input set | `abs(sum(probabilities) - 1.0) < 0.001` | `uk_isa/volatility_regime.py` |
| T-UNIT-054 | Empty BB history returns UNKNOWN or fallback | `bb_hist=[]` | No crash; returns UNKNOWN with warning logged | `uk_isa/volatility_regime.py:240-241` |

### 1.7 Drought State Machine

Test all state transitions in the drought escalation ladder: NORMAL -> WATCH -> DROUGHT -> CRITICAL -> CLEARED.

| Test ID | Description | Input | Expected Output | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-UNIT-055 | NORMAL -> WATCH: no signals for N ticks | `signals_last_N_ticks=0`, `current_state=NORMAL` | Transition to `WATCH` | `main.py:2755-2758` (proposed drought manager) |
| T-UNIT-056 | WATCH -> DROUGHT: continued no signals past threshold | `signals_since_watch=0`, `ticks_in_watch > threshold` | Transition to `DROUGHT` | Drought state machine |
| T-UNIT-057 | DROUGHT -> CRITICAL: prolonged drought past escalation threshold | `hours_in_drought > critical_threshold` | Transition to `CRITICAL` | Drought state machine |
| T-UNIT-058 | Any state -> CLEARED: qualifying signal arrives | `new_signal=True`, `signal_passes_all_gates=True` | Transition to `CLEARED`, then `NORMAL` on next tick | Drought state machine |
| T-UNIT-059 | NORMAL stays NORMAL when signals flow | `signals_last_N_ticks=3`, `current_state=NORMAL` | State remains `NORMAL` | Drought state machine |
| T-UNIT-060 | CRITICAL -> NORMAL not allowed (must go through CLEARED) | `current_state=CRITICAL`, `signal_arrives=True` | Transition to `CLEARED` first, then `NORMAL` | Drought state machine |
| T-UNIT-061 | Drought + EXPANSION regime contradiction detected | `drought_state=DROUGHT`, `regime=EXPANSION` | Warning emitted: "Drought in EXPANSION regime is contradictory" | Drought-regime cross-check |
| T-UNIT-062 | Drought state survives restart (persisted to disk) | Write `drought_state=DROUGHT` to file, simulate restart | State loaded as `DROUGHT` on restart | State persistence layer |

---

## 2. INTEGRATION TESTS

### 2.1 Artifact Consistency

After a full scan cycle, all output surfaces (system_state.json, plays.json, Telegram message) must agree on regime, drought state, and signal count.

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-INT-001 | System state and plays agree on regime | 1. Run full scan cycle with mock market data. 2. Read `artifacts/system_state.json`. 3. Read `artifacts/plays.json`. 4. Compare regime fields. | `system_state.regime == plays[*].regime_at_emission` for all plays | `main.py`, `command_center/state.py` |
| T-INT-002 | System state and Telegram agree on regime | 1. Run full scan. 2. Capture Telegram message via mock. 3. Parse regime tag from message. 4. Compare with system_state.json. | Telegram regime label matches system_state.json | `main.py`, `delivery/telegram_bot.py` |
| T-INT-003 | Signal count consistency | 1. Run full scan. 2. Count plays in plays.json. 3. Count signals sent to Telegram mock. 4. Count signals in system_state.json `signals_emitted`. | All three counts match | `main.py`, `command_center/state.py`, `delivery/telegram_bot.py` |
| T-INT-004 | Drought state consistency | 1. Run scan with zero qualifying signals. 2. Check system_state.json drought field. 3. Check Telegram drought notification. | Both surfaces report consistent drought state | `main.py`, `command_center/state.py` |
| T-INT-005 | Kill switch state propagates to all surfaces | 1. Activate kill switch. 2. Check system_state.json. 3. Check Telegram status. 4. Check War Room API `/api/state`. | All three surfaces show kill_switch=true | `command_center/state.py`, `delivery/telegram_bot.py`, War Room API |

### 2.2 Cross-PDF Consistency

PDF1 (Momentum) and PDF2 (Risk) generated for the same scan session must not contradict each other.

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-INT-006 | No regime contradictions between PDF1 and PDF2 | 1. Generate PDF1 and PDF2 for same session. 2. Extract regime labels from both PDFs. 3. Compare. | Regime labels identical for same ticker in both PDFs | `delivery/pdf_v2_momentum.py`, `delivery/pdf_v2_risk.py` |
| T-INT-007 | No direction contradictions for same ticker | 1. Generate both PDFs. 2. For each ticker appearing in both, extract bias direction. 3. Compare. | If PDF1 says "BULLISH" for QQQ3.L, PDF2 must not say "BEARISH" for same ticker in same session | `delivery/pdf_v2_momentum.py`, `delivery/pdf_v2_risk.py` |
| T-INT-008 | Data timestamps match between PDFs | 1. Generate both PDFs. 2. Extract per-ticker data as-of timestamps. 3. Compare. | Timestamps within 60s of each other for same ticker | Both PDF modules |
| T-INT-009 | Ticker counts match between PDFs and system state | 1. Generate PDFs. 2. Count tickers per tier in each PDF. 3. Compare with system_state universe count. | PDF ticker counts match system_state active universe | Both PDF modules, `command_center/state.py` |
| T-INT-010 | Both PDFs use same data source (DataHub) | 1. Mock DataHub. 2. Generate both PDFs. 3. Verify all DataHub calls come from same cache. | No direct yfinance calls from PDF generation code | `delivery/pdf_v2_momentum.py`, `delivery/pdf_v2_risk.py` |

### 2.3 Signal Pipeline End-to-End

Create a mock signal and push it through the entire qualification pipeline. Verify each gate's verdict.

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-INT-011 | Full pipeline: qualifying signal passes all 7 gates | 1. Create mock signal with score=75, confidence=70, valid OHLC, fresh data. 2. Push through qualification pipeline. 3. Record each gate verdict. | All 7 gates return PASS; signal reaches Telegram mock | `main.py:1014-1051`, `qualification/qualifier.py` |
| T-INT-012 | Full pipeline: confidence < 60 blocked at gate | 1. Create signal with score=75, confidence=55. 2. Push through pipeline. | BLOCKED at confidence gate; no Telegram send | `qualification/qualifier.py` |
| T-INT-013 | Full pipeline: score = 0 blocked at gate | 1. Create signal with score=0, confidence=70. 2. Push through pipeline. | BLOCKED at score/validation gate | `strategies/daily_target.py`, `telegram_bot.py:731-759` |
| T-INT-014 | Full pipeline: stale data blocked (proposed freshness gate) | 1. Create signal with data timestamp > 10min old. 2. Push through pipeline. | BLOCKED at data freshness gate (new gate) | Proposed freshness gate |
| T-INT-015 | Full pipeline: rate limiter blocks excessive signals | 1. Send 6 qualifying signals within 1 minute. 2. Observe Telegram mock. | First 5 sent; 6th blocked by rate limiter (MAX_PER_MINUTE=5) | `delivery/telegram_bot.py:90-92` |
| T-INT-016 | Full pipeline: dedupe blocks duplicate signal within 5min | 1. Send signal. 2. Send identical signal at T+2min. | Second signal silently dropped by dedupe | `delivery/telegram_bot.py:63-84` |
| T-INT-017 | Full pipeline: kill switch blocks all signals | 1. Activate kill switch. 2. Create qualifying signal. 3. Push through pipeline. | Signal BLOCKED; kill_switch reason logged | `signal_engine/strategy_router.py:636-638` |
| T-INT-018 | Telegram format matches expected structure | 1. Send qualifying signal through pipeline. 2. Capture formatted Telegram message. | Message contains: ticker, direction, score, confidence, regime, entry, stop, target, star rating | `delivery/telegram_bot.py:180-212` |

### 2.4 Restart Resilience

Simulate system restarts and verify no state corruption, no duplicate sends, and safety mechanisms restored.

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-INT-019 | Kill switch state restored after restart | 1. Activate kill switch. 2. Write state to disk. 3. Simulate restart. 4. Check kill switch state. | Kill switch remains active after restart | `command_center/state.py:123`, `telegram_bot.py:1277,1300` |
| T-INT-020 | No duplicate signals on restart within dedupe window | 1. Send signal at T=0. 2. Simulate crash at T+2min. 3. Restart at T+2.5min. 4. Run initial scan. | Duplicate signal caught by dedupe (within 5min window) | `main.py:3678`, `delivery/telegram_bot.py:66` |
| T-INT-021 | Startup quiet mode: 5min grace period | 1. Restart system. 2. Generate signals during first 5min. | Signals held in queue, not sent until quiet period ends | `main.py:3678` (proposed startup guard) |
| T-INT-022 | Paused strategies restored after restart | 1. Pause S15 via Telegram command. 2. Persist state. 3. Restart. | S15 remains paused | `telegram_bot.py:1277` (currently in-memory only) |
| T-INT-023 | System_state.json integrity after crash | 1. Simulate mid-write crash (corrupt JSON). 2. Restart. | System detects corrupt JSON, falls back to last known good state file | `command_center/state.py` |
| T-INT-024 | APScheduler jobs re-register after restart | 1. Restart. 2. Check scheduler job list. | All scheduled jobs (60s scan, PDF generation, outcome resolution) present | `main.py:2439` |

---

## 3. REGRESSION TESTS

Replay known-bad scenarios to verify fixes hold. Each test injects a previously-observed failure into the pipeline.

### 3.1 "Impossible Premarket" Replay

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-REG-001 | 500% overnight move blocked by magnitude gate | 1. Create mock premarket data with `futures_return=+500%`. 2. Inject into `PreMarketIntelligenceEngine`. 3. Push result to Telegram. | BLOCKED by `SANITY_FAIL_MAGNITUDE` gate; Telegram receives NO message for this ticker. Health alert emitted instead. | `main.py:1933-1949` (proposed sanity gate) |
| T-REG-002 | 100% overnight move blocked (3x ETP max ~45% daily) | `futures_return=+100%` | BLOCKED; exceeds max plausible daily move for leverage tier | Proposed magnitude gate |
| T-REG-003 | 15% overnight move for 5x ETP accepted (plausible) | `futures_return=+15%`, `leverage=5` | ACCEPTED; within plausible range for 5x product | Proposed magnitude gate |
| T-REG-004 | Stale premarket data detected and labelled | Premarket data with timestamp > 30min old | Message sent with `[STALE DATA]` warning prefix, or BLOCKED | `main.py:1933-1949` (proposed staleness gate) |
| T-REG-005 | Negative futures return for inverse ETP is valid | `ticker=QQQS.L` (inverse), `futures_return=-8%` | ACCEPTED; negative return on inverse ETP is a gain scenario | Proposed magnitude gate |

### 3.2 "Score 0 Leak" Replay

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-REG-006 | Score=0, confidence=65 blocked before Telegram | 1. Create SignalCard with `score=0`, `confidence=65`. 2. Push through full qualification pipeline. 3. Check Telegram mock. | BLOCKED; qualification_log contains `SCORE_ZERO_BLOCK` reason. Zero messages sent. | `strategies/daily_target.py:44-69`, `telegram_bot.py:731-759` |
| T-REG-007 | Score=0, confidence=95 blocked (high confidence does not override zero score) | `score=0`, `confidence=95` | BLOCKED; same as T-REG-006 | All qualification paths |
| T-REG-008 | Score=0 from insufficient data path specifically | 1. Feed `daily_target.score_candidates()` with ticker having only 2 bars. 2. Capture returned score. 3. If score=0, push through pipeline. | Score=0 returned AND blocked downstream | `strategies/daily_target.py:44-69` |

### 3.3 "Duplicate Send" Replay

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-REG-009 | Restart at T+3min: duplicate caught by dedupe | 1. Send signal at T=0. 2. Simulate restart at T+3min. 3. Same signal regenerated by initial scan. | DUPLICATE caught (within 5min window); not sent | `delivery/telegram_bot.py:63-84` |
| T-REG-010 | Restart at T+6min: duplicate sent (dedupe expired) | 1. Send signal at T=0. 2. Simulate restart at T+6min. 3. Same signal regenerated. | DUPLICATE sent (dedupe window expired at 5min). This confirms the known gap. | `delivery/telegram_bot.py:66` |
| T-REG-011 | Restart at T+6min with replay protection: duplicate caught | 1. Enable proposed replay protection (persistent dedupe). 2. Send signal at T=0. 3. Restart at T+6min. | DUPLICATE caught by persistent replay log even though in-memory dedupe expired | Proposed persistent dedupe |
| T-REG-012 | Spam kill fires after 10 signals in 1 minute | 1. Send 10 signals in rapid succession (< 60s). 2. Check spam kill state. | Spam kill activated; 15min auto-pause engaged; health alert sent | `delivery/telegram_bot.py:92,111` |
| T-REG-013 | Restart clears spam kill (in-memory) -- verifying known gap | 1. Trigger spam kill. 2. Restart. 3. Check spam kill state. | Spam kill state LOST (in-memory only); system resumes sending. This confirms the known gap requiring persistent state. | `delivery/telegram_bot.py` |

---

## 4. PLAYWRIGHT WAR ROOM QA PLAN

Browser-based end-to-end testing of the War Room dashboard using Playwright.

### 4.1 Panel Rendering

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-PW-001 | All 30+ panels render without blank content | 1. Navigate to War Room. 2. Wait for all data to load (10s timeout). 3. For each panel, check `innerText.length > 0`. | Zero blank panels |
| T-PW-002 | No JavaScript console errors on initial load | 1. Start console error listener. 2. Navigate to War Room. 3. Wait for full load. 4. Check captured errors. | Zero `console.error` entries |
| T-PW-003 | No JavaScript errors after clicking every panel | 1. For each clickable panel element: click, wait 2s, check console. | Zero `console.error` entries across all panel interactions |
| T-PW-004 | No network 4xx/5xx errors on load | 1. Start network listener. 2. Navigate. 3. Capture all responses. | Zero responses with status 400-599 (except the 6 known missing endpoints) |
| T-PW-005 | 6 missing endpoints return proper error format (not crash) | 1. Call each: `/api/scan_health`, `/api/opportunity`, `/api/exits`, `/api/telegram/events`, `/api/consistency`, `/api/copilot/query`. 2. Check response. | Each returns JSON `{"error": "not_implemented", "message": "..."}` with status 501, NOT a 500 crash or HTML error page |
| T-PW-006 | Regime badge displays current regime | 1. Navigate. 2. Find regime badge element. 3. Check text content. | Badge shows one of the valid regime states; never "undefined" or "null" |
| T-PW-007 | Regime badge updates in real-time via WebSocket | 1. Navigate. 2. Note initial regime. 3. Push regime change via mock WebSocket. 4. Check badge. | Badge updates within 5s without page refresh |

### 4.2 Light/Dark Mode

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-PW-008 | Screenshot all panels in LIGHT mode | 1. Set theme to light. 2. Navigate to War Room. 3. Scroll through all sections. 4. Capture full-page screenshot. | Screenshot captured; visual review confirms all text readable, no missing elements |
| T-PW-009 | Screenshot all panels in DARK mode | 1. Set theme to dark. 2. Same as T-PW-008. | Screenshot captured; all text readable, proper contrast, no white-on-white or black-on-black |
| T-PW-010 | Theme toggle does not cause panel re-render errors | 1. Navigate. 2. Toggle theme 5 times in rapid succession. 3. Check console. | Zero console errors during rapid theme toggling |

### 4.3 WebSocket Resilience

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-PW-011 | WebSocket auto-reconnect after disconnect | 1. Navigate. 2. Verify WebSocket connected. 3. Kill WebSocket server. 4. Wait 5s. 5. Restart server. 6. Check connection state. | WebSocket auto-reconnects within 10s; data resumes flowing |
| T-PW-012 | UI shows "Disconnected" indicator during outage | 1. Kill WebSocket. 2. Check UI. | Visible "disconnected" or "reconnecting" indicator appears |
| T-PW-013 | No data loss after reconnection | 1. Disconnect. 2. Send 3 updates to server. 3. Reconnect. 4. Check if missed updates are backfilled. | Either updates are backfilled OR UI refreshes full state on reconnect |
| T-PW-014 | WebSocket reconnect does not cause memory leak | 1. Disconnect/reconnect 20 times. 2. Check browser memory via `performance.memory`. | Memory does not grow unbounded (< 50MB increase) |

### 4.4 Interactive Elements

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-PW-015 | Analysis page renders ticker charts | 1. Navigate to `/analysis`. 2. Select a ticker. 3. Wait for chart render. | Chart displays with price data; no blank canvas |
| T-PW-016 | Kill switch button triggers confirmation | 1. Click kill switch button (if present). 2. Check for confirmation dialog. | Confirmation dialog appears; no immediate action without confirmation |
| T-PW-017 | Sort columns work on signal table | 1. Find signal table. 2. Click column header. 3. Verify sort order. | Rows reorder correctly; no NaN sort crashes (ref: `page.tsx:1029-1035`) |
| T-PW-018 | NaN values display gracefully | 1. Feed mock data with null win rate. 2. Check `rolling_60d_wr * 100` display. | Shows "N/A" or "--", not "NaN" or crash (ref: `page.tsx:982`) |

---

## 5. PDF QA PLAN

Automated and manual checks on generated PDF1 (Momentum) and PDF2 (Risk).

### 5.1 Lane Separation

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PDF-001 | PDF1 contains ONLY momentum content | 1. Generate PDF1. 2. Extract text. 3. Search for risk-only terms: "RVOL", "vol decay score", "correlation matrix", "drawdown analysis". | Zero hits for risk-only terms in PDF1 | `delivery/pdf_v2_momentum.py` |
| T-PDF-002 | PDF2 contains ONLY risk content | 1. Generate PDF2. 2. Extract text. 3. Search for momentum-only terms: "momentum bias", "trend score", "breakout probability", "long/short bias points". | Zero hits for momentum-only terms in PDF2 | `delivery/pdf_v2_risk.py` |
| T-PDF-003 | Neither PDF contains content from the other lane | 1. Define lane-specific keyword sets. 2. Cross-check both PDFs. | Complete lane isolation verified | Both PDF modules |

### 5.2 Timestamps and Provenance

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PDF-004 | Every ticker section has as-of timestamp | 1. Generate PDF. 2. For each ticker section, extract timestamp. | 100% of ticker sections have a visible timestamp in format `YYYY-MM-DD HH:MM UTC` | Both PDF modules |
| T-PDF-005 | Timestamps are within 60s of generation time | 1. Record generation start time. 2. Generate PDF. 3. Compare ticker timestamps with generation time. | All timestamps within 60s of generation time (no stale cached data) | Both PDF modules |
| T-PDF-006 | PDF header contains session identifier | 1. Check PDF header/footer. | Contains: date, session type (PRE_LSE/PRE_NYSE/EOD), generation timestamp | Both PDF modules |

### 5.3 Count Consistency

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PDF-007 | "N TIER_1" label matches actual count | 1. Extract tier count labels from PDF (e.g., "3 TIER_1"). 2. Count actual tickers listed under TIER_1. | Label count == actual listed count | Both PDF modules |
| T-PDF-008 | "N TIER_2" label matches actual count | Same as T-PDF-007 for TIER_2. | Label count == actual listed count | Both PDF modules |
| T-PDF-009 | Total tickers across all tiers matches universe size | 1. Sum all tier counts. 2. Compare with active universe. | Total == active universe size (currently 12 CORE) | Both PDF modules |

### 5.4 Closest Misses Section

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PDF-010 | Closest misses section present and populated | 1. Generate PDF with at least 1 signal and at least 1 near-miss. 2. Check for "Closest Misses" or equivalent section. | Section exists with at least 1 entry showing: ticker, reason blocked, how close it was | Both PDF modules |
| T-PDF-011 | Closest misses show gate that blocked them | 1. Extract closest miss entries. 2. Check each has a blocking gate name. | Every miss entry has `blocked_by` field (e.g., "CONFIDENCE < 60") | Both PDF modules |
| T-PDF-012 | Closest misses sorted by proximity to qualifying | 1. Check order of closest misses. | Sorted by smallest deficit first (closest to passing) | Both PDF modules |

### 5.5 Data Health Section

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PDF-013 | Data health section shows per-ticker completeness | 1. Generate PDF. 2. Find data health section. 3. Verify each ticker has completeness percentage. | Every ticker has a completeness indicator: PASS (>=80%), WARN (>=50%), FAIL (<50%) | `delivery/pdf_v2_momentum.py:779-784` |
| T-PDF-014 | Data health section matches system_state health | 1. Compare PDF data health with `system_state.json` health fields. | No contradictions between PDF and system state | Both PDF modules, `command_center/state.py` |
| T-PDF-015 | Tickers with FAIL health have visible warning in their section | 1. Generate PDF with a ticker at <50% completeness. 2. Check ticker section. | Ticker section shows `[DATA HEALTH: FAIL]` or similar visible warning | Both PDF modules |

### 5.6 Regime and Direction Consistency (Within PDF)

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PDF-016 | No regime contradictions within same PDF | 1. Extract regime labels from all sections of one PDF. 2. Compare. | All regime references within one PDF agree; no section says "EXPANSION" while another says "COMPRESSION" for same session | Both PDF modules |
| T-PDF-017 | No direction contradictions for same ticker within PDF | 1. Extract all direction/bias references per ticker. 2. Compare within same PDF. | A ticker cannot be both "BULLISH" and "BEARISH" within the same PDF | Both PDF modules |
| T-PDF-018 | Regime label matches one of the valid 5-regime or 8-regime taxonomy | 1. Extract all regime labels. 2. Check against valid set. | Every regime label is a member of the valid taxonomy; no "UNKNOWN" when system is OK | Both PDF modules, `uk_isa/volatility_regime.py`, `config/settings.yaml` |

---

## 6. PERFORMANCE / SLA CHECKS

### 6.1 Scan Heartbeat

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PERF-001 | 60s continuous scan completes within 45s | 1. Time a full scan cycle from start to artifact write. 2. Repeat 10 times. 3. Check P95. | P95 scan duration < 45s (75% of 60s cycle budget) | `main.py` (APScheduler 60s loop) |
| T-PERF-002 | Scan does not skip ticks under normal load | 1. Run for 10 minutes (10 cycles). 2. Count actual scans completed. | At least 9 out of 10 cycles completed (90% SLA) | `main.py` |
| T-PERF-003 | Scan warns if approaching deadline | 1. Add timing instrumentation. 2. If scan takes > 50s, emit warning. | Warning logged when scan exceeds 50s | `main.py` |

### 6.2 Stale Feed Detection

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PERF-004 | Alert if yfinance data not updated in 2x TTL | 1. Set TTL for yfinance to 5min. 2. Mock yfinance returning same data for 11min. 3. Check alerts. | STALE_FEED alert emitted after 10min (2x 5min TTL) | Data quality monitoring (proposed) |
| T-PERF-005 | Alert specifies which ticker(s) are stale | 1. Make only 1 ticker stale. 2. Check alert content. | Alert names the specific stale ticker(s), not just "data stale" | Data quality monitoring |
| T-PERF-006 | Stale feed alert does not spam (one alert per staleness event) | 1. Data stays stale for 30min. 2. Count alerts. | Maximum 1 alert per ticker per staleness event (not repeated every tick) | Data quality monitoring |

### 6.3 Telegram Rate Limits

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PERF-007 | No more than 5 signals per minute sent | 1. Generate 10 qualifying signals in under 60s. 2. Count Telegram sends. | Exactly 5 sent in first minute; remaining queued or dropped | `delivery/telegram_bot.py:90-92` |
| T-PERF-008 | No more than 30 signals per hour sent | 1. Generate 35 qualifying signals over 60min (evenly spaced). 2. Count Telegram sends. | Exactly 30 sent in the hour; remaining 5 dropped or queued | `delivery/telegram_bot.py:90-92` |
| T-PERF-009 | Rate limiter resets correctly at window boundaries | 1. Send 5 signals at T=0. 2. Wait 61s. 3. Send 5 more. | All 10 sent (rate limit window expired) | `delivery/telegram_bot.py` |
| T-PERF-010 | Spam kill fires at 10/min and pauses for 15min | 1. Somehow push 10 signals past rate limiter in 60s. 2. Check spam kill state. | Spam kill activated; system paused for 15min; health alert sent | `delivery/telegram_bot.py:92,111` |

### 6.4 API Response Time

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PERF-011 | `/api/health` responds in < 200ms | 1. Send 100 requests. 2. Measure P95 latency. | P95 < 200ms | War Room API |
| T-PERF-012 | `/api/state` responds in < 200ms | 1. Same as T-PERF-011 for `/api/state`. | P95 < 200ms | War Room API |
| T-PERF-013 | `/api/plays` responds in < 200ms | 1. Same as T-PERF-011 for `/api/plays`. | P95 < 200ms | War Room API |
| T-PERF-014 | All existing War Room endpoints respond in < 200ms | 1. Test all implemented endpoints. 2. Measure P95. | All P95 < 200ms | War Room API |
| T-PERF-015 | WebSocket initial state push < 500ms | 1. Connect WebSocket. 2. Measure time to first message. | First message received within 500ms of connection | War Room WebSocket |

### 6.5 Memory and Resource Usage

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-PERF-016 | System stays under 1GB RSS after 1 hour | 1. Start system. 2. Run for 60 minutes of continuous scanning. 3. Check RSS. | RSS < 1GB | System-wide |
| T-PERF-017 | No memory leak over 24 hours | 1. Record RSS at T=0. 2. Run for 24h (simulated or real). 3. Record RSS at T=24h. | RSS increase < 200MB over 24h (allows for data accumulation) | System-wide |
| T-PERF-018 | Docker container stays within memory limit | 1. Set Docker memory limit to 2GB. 2. Run for 1h. | Container not OOM-killed; RSS < 1.5GB | Docker deployment |
| T-PERF-019 | Disk usage for artifacts bounded | 1. Run for 24h. 2. Check `artifacts/` directory size. | < 500MB per day of artifacts (auto-cleanup configured) | Artifact management |

---

## 7. GO-LIVE GATE PAGE TESTS

Verify the Go-Live Gate Page functions as the authoritative fund-manager readiness screen.

### 7.1 API Endpoint Tests

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-GATE-001 | All 8 checks PASS → go_live=true | 1. Ensure all systems healthy. 2. Call `GET /api/go-live-gate`. | Response: `go_live=true`, all 8 checks `status=PASS`, `pass_count=8`, `fail_count=0` | `api.py` |
| T-GATE-002 | One check fails → go_live=false | 1. Disable `sanity_gate_v2` feature flag. 2. Call `GET /api/go-live-gate`. | Response: `go_live=false`, sanity_gate check `status=FAIL`, `fail_count≥1` | `api.py` |
| T-GATE-003 | Missing data source → check reports FAIL (not silent pass) | 1. Rename `artifacts/system_state.json` to `.bak`. 2. Call gate. | system_state check: `status=FAIL`, `detail` contains "source not found" | `api.py` |
| T-GATE-004 | Check function exception → FAIL with error message | 1. Corrupt `data/pdf_qa_log.jsonl` with invalid JSON. 2. Call gate. | pdf_audit check: `status=FAIL`, `detail` contains exception description | `api.py` |
| T-GATE-005 | All 8 check functions are registered | 1. Call gate. 2. Count checks in response. | Exactly 8 checks present with names: system_state, sanity_gate, data_health, telegram_tape, dedupe_active, war_room_qa, pdf_audit, scan_sla | `api.py` |
| T-GATE-006 | Gate evaluation logged to jsonl | 1. Call gate. 2. Read last line of `data/go_live_gate_log.jsonl`. | Log entry matches response with timestamp | `api.py` |

### 7.2 Frontend Tests

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-GATE-007 | Gate page renders at /gate route | 1. Navigate to `/gate`. 2. Wait for data load. | GO/NO-GO indicator visible; 8 check cards rendered |
| T-GATE-008 | GO indicator shown when all checks pass | 1. Mock all checks PASS. 2. Navigate to `/gate`. | Large green "GO" indicator at top |
| T-GATE-009 | NO-GO indicator shown when any check fails | 1. Mock one check FAIL. 2. Navigate to `/gate`. | Large red "NO-GO" indicator at top; failed check card shown in red |
| T-GATE-010 | CONDITIONAL indicator on WARN (no FAIL) | 1. Mock one check WARN, rest PASS. 2. Navigate. | Orange "CONDITIONAL" indicator |
| T-GATE-011 | Auto-refresh every 30 seconds | 1. Navigate. 2. Wait 35s. 3. Verify network request made. | New `/api/go-live-gate` call visible in network tab after ~30s |
| T-GATE-012 | Click check card expands detail | 1. Click any check card. 2. Verify expansion. | Detail panel shows: raw source excerpt, last 5 transitions, link to artifact |

### 7.3 Self-Validation Tests

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-GATE-013 | Gate endpoint crash returns HTTP 500 with go_live=false | 1. Force gate endpoint exception. 2. Check response. | HTTP 500, body: `{"go_live": false, "error": "gate endpoint failure"}` |
| T-GATE-014 | Frontend shows RED "GATE ERROR" on 500 | 1. Force gate 500. 2. Check UI. | Red "GATE ERROR" screen; never defaults to green |
| T-GATE-015 | Gate survives code changes (check registration verification) | 1. Remove one check function. 2. Call gate. | Gate detects missing check and reports FAIL for that check with "check not registered" message |

---

## 8. TELEGRAM MODE FILTERING TESTS

Verify DEGRADED/HALTED mode message suppression.

| Test ID | Description | Steps | Expected Result | Target File(s) |
|---------|-------------|-------|-----------------|-----------------|
| T-MODE-001 | DEGRADED mode suppresses SIGNAL messages | 1. Set system mode=DEGRADED. 2. Generate qualifying signal. 3. Check Telegram mock. | Signal NOT sent; logged as `SUPPRESSED_DEGRADED` in `telegram_debug.jsonl` | `telegram_bot.py` |
| T-MODE-002 | DEGRADED mode allows SYSTEM messages | 1. Set mode=DEGRADED. 2. Send [SYSTEM] health update. | Message delivered to Telegram | `telegram_bot.py` |
| T-MODE-003 | DEGRADED mode allows CRITICAL ERROR messages | 1. Set mode=DEGRADED. 2. Trigger critical error. | [CRITICAL ERROR] message delivered | `telegram_bot.py` |
| T-MODE-004 | HALTED mode suppresses all non-SYSTEM messages | 1. Set mode=HALTED. 2. Attempt SIGNAL, BRIEF, REGIME sends. | All suppressed; only SYSTEM and CRITICAL ERROR would pass | `telegram_bot.py` |
| T-MODE-005 | Mode transition NORMAL→DEGRADED sends notification | 1. Transition from NORMAL to DEGRADED. 2. Check Telegram. | `[SYSTEM] ⚠ DEGRADED MODE` message sent immediately | `telegram_bot.py` |
| T-MODE-006 | Mode transition DEGRADED→NORMAL sends notification | 1. Recover from DEGRADED to NORMAL. | `[SYSTEM] ✅ NORMAL MODE RESTORED` message sent | `telegram_bot.py` |
| T-MODE-007 | Suppressed messages logged but not retransmitted | 1. Suppress 5 signals in DEGRADED. 2. Return to NORMAL. 3. Check if suppressed signals sent. | Suppressed signals NOT retransmitted; remain in debug log only | `telegram_bot.py` |

---

## 9. WIRING PROOF PACK TESTS

Verify the mandatory wiring proof pack is complete and valid.

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-PROOF-001 | Proof pack generator creates all 8 artifacts | 1. Run proof pack generator. 2. Check `artifacts/wiring_proof/` directory. | All 8 artifacts present: all_endpoints_200.json, schema_validation.json, screenshots/, console_errors.txt, playwright_results.json, performance_bench.json, websocket_test.json, gate_pass.png |
| T-PROOF-002 | all_endpoints_200.json has 36 entries all status 200 | 1. Parse JSON. 2. Count entries. 3. Verify all status=200. | Exactly 36 entries, all HTTP 200 |
| T-PROOF-003 | console_errors.txt is empty | 1. Read file. 2. Check contents. | File exists but is empty (zero bytes) |
| T-PROOF-004 | Screenshot directory has 36+ files | 1. Count PNG files in screenshots/. | ≥36 PNG files |
| T-PROOF-005 | Proof pack expires after 7 days | 1. Check pack timestamp. 2. Verify validation rejects >7 day old pack. | Pack marked EXPIRED if older than 7 days |

---

## SUMMARY

| Category | Test Count | ID Range |
|----------|-----------|----------|
| Unit Tests | 62 | T-UNIT-001 to T-UNIT-062 |
| Integration Tests | 24 | T-INT-001 to T-INT-024 |
| Regression Tests | 13 | T-REG-001 to T-REG-013 |
| Playwright War Room QA | 18 | T-PW-001 to T-PW-018 |
| PDF QA | 18 | T-PDF-001 to T-PDF-018 |
| Performance / SLA | 19 | T-PERF-001 to T-PERF-019 |
| Go-Live Gate Page | 15 | T-GATE-001 to T-GATE-015 |
| Telegram Mode Filtering | 7 | T-MODE-001 to T-MODE-007 |
| Wiring Proof Pack | 5 | T-PROOF-001 to T-PROOF-005 |
| **TOTAL** | **181** | |

---

## EXECUTION PRIORITY

1. **P0 (Run First)**: T-UNIT-031 to T-UNIT-036 (score=0 leak), T-UNIT-011 to T-UNIT-021 (div/0 guards), T-REG-001 to T-REG-005 (impossible premarket)
2. **P1 (Run Second)**: T-INT-001 to T-INT-005 (artifact consistency), T-INT-011 to T-INT-018 (pipeline E2E), T-UNIT-037 to T-UNIT-044 (OHLC integrity), T-GATE-001 to T-GATE-006 (Go-Live Gate API)
3. **P2 (Run Third)**: T-PDF-001 to T-PDF-018 (PDF QA), T-PW-001 to T-PW-018 (War Room QA), T-MODE-001 to T-MODE-007 (Telegram mode filtering)
4. **P3 (Run Last)**: T-PERF-001 to T-PERF-019 (SLA checks), T-INT-019 to T-INT-024 (restart resilience), T-GATE-007 to T-GATE-015 (Go-Live Gate UI + self-validation), T-PROOF-001 to T-PROOF-005 (wiring proof pack)
5. **P1.5 (Run with Phase 2)**: T-STARTUP-001 to T-STARTUP-008, T-WIRE-001 to T-WIRE-010, T-INTEG-001 to T-INTEG-010, T-HEAL-001 to T-HEAL-008, T-SSP-001 to T-SSP-006, T-DRIFT-001 to T-DRIFT-006, T-PW-021 to T-PW-024, T-LUX-001 to T-LUX-007

---

## ADDENDUM: W13 ALWAYS-WIRED TEST SECTIONS

**Added by**: `docs/ADDENDUM_ALWAYS_WIRED_110.md` v1.0

### Section 10: Startup Readiness Gate Tests

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-STARTUP-001 | All 8 checks pass → READY | Boot system with all services healthy | Gate returns READY; normal operation begins |
| T-STARTUP-002 | Telegram token missing → HALTED | Remove TELEGRAM_BOT_TOKEN env var; boot | Gate returns HALTED; only [SYSTEM] messages; operator actions printed |
| T-STARTUP-003 | Artifacts dir not writable → HALTED | chmod 444 artifacts/; boot | Gate returns HALTED; write permission failure logged |
| T-STARTUP-004 | yfinance unreachable → DEGRADED | Block yfinance DNS; boot | Gate returns DEGRADED; system health messages only |
| T-STARTUP-005 | War Room API down → HALTED | Stop FastAPI; boot engine | Gate returns HALTED; critical check failed |
| T-STARTUP-006 | Corrupt system_state.json → DEGRADED | Write invalid JSON to system_state.json; boot | Gate returns DEGRADED; schema validation failure logged |
| T-STARTUP-007 | Override flag set → DEGRADED proceeds | Set startup_gate_override: true; boot with 1 non-critical failure | Gate logs override; system proceeds in DEGRADED |
| T-STARTUP-008 | Gate re-runs every 5 min when not READY | Boot with failure → fix after 3 min → wait | Gate re-checks at 5 min mark; transitions to READY |

### Section 11: Wiring Path Tests

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-WIRE-001 | Tick→engine→artifacts path connected | Run scan cycle | All 4 artifacts (system_state, plays, scan_health, drought) updated within 120s |
| T-WIRE-002 | Engine→Telegram path connected | Generate qualifying signal | Telegram message received with matching run_id within 30s |
| T-WIRE-003 | Engine→PDF path (no recomputation) | Generate PDF | All plays in PDF match plays.json exactly (same scores, same run_id) |
| T-WIRE-004 | Artifacts→War Room path connected | Update artifacts | API /api/system_state returns data matching artifact file |
| T-WIRE-005 | Learning loop end-to-end | Log signal → resolve outcome | Edge ledger updated; War Room shows updated stats |
| T-WIRE-006 | EC2/Docker code parity | rsync + build + parity check | All critical file checksums match between host and container |
| T-WIRE-007 | Scheduler jobs registered | List APScheduler jobs | All expected jobs present with valid next_run_time |
| T-WIRE-008 | Regime single-source | Force regime change | All 3 output channels show same regime within 120s |
| T-WIRE-009 | Data health→gating | Degrade data health below 80% | Trade outputs blocked; DEGRADED mode active |
| T-WIRE-010 | Drought→all outputs | Trigger drought (20 empty cycles) | Drought flag consistent across Telegram, PDF, War Room |

### Section 12: Continuous Integrity Monitor Tests

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-INTEG-001 | Monitor detects engine→artifact drift | Run engine but delete artifacts before write completes | DRIFT_ENGINE_ARTIFACT detected; DEGRADED mode |
| T-INTEG-002 | Monitor detects artifact→War Room drift | Update artifacts but block API from reading | DRIFT_ARTIFACT_WARROOM detected |
| T-INTEG-003 | Monitor detects regime mismatch | Set different regime in volatility_regime vs system_state | DRIFT_REGIME_CROSS detected |
| T-INTEG-004 | Monitor handles all-green state | Normal operation, all paths healthy | integrity_status.json shows all PASS |
| T-INTEG-005 | Monitor sends Telegram alert on drift | Trigger any drift | [SYSTEM] INTEGRITY ALERT message received |
| T-INTEG-006 | Monitor auto-recovers after fix | Trigger drift → fix root cause → wait 5 min | Status returns to PASS; DEGRADED cleared |
| T-INTEG-007 | 2-consecutive-fail requirement | Single transient failure | No alert (requires 2 consecutive) |
| T-INTEG-008 | Monitor check interval correct | Observe timing | Checks run every 5 min during market hours, 15 min outside |
| T-INTEG-009 | Scan SLA check | Delay tick loop beyond 120s | Scan SLA check FAIL; alert sent |
| T-INTEG-010 | Artifact freshness check | Let artifacts age beyond TTL | Freshness check FAIL; alert sent |

### Section 13: Self-Healing Tests

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-HEAL-001 | Config cache reload (auto) | Modify settings.yaml | Cache reloaded automatically; log entry with action_id |
| T-HEAL-002 | War Room restart (gated) | War Room health check fails 3x | Auto-restart triggered; logged; 5s downtime max |
| T-HEAL-003 | Log rotation (auto) | Log exceeds 10MB | Rotated automatically per RotatingFileHandler |
| T-HEAL-004 | Readiness re-check (auto) | Boot in DEGRADED → fix issue | Gate re-checks at 5 min; transitions to READY |
| T-HEAL-005 | Threshold change blocked | Attempt auto-threshold change | REJECTED; requires human approval |
| T-HEAL-006 | Universe change blocked | Attempt auto-universe change | REJECTED; requires human approval |
| T-HEAL-007 | Escalation on failed auto-action | Auto-action fails → retry → fails again | Telegram [SYSTEM] alert; affected output blocked |
| T-HEAL-008 | Action logging complete | Any auto-action | action_id, timestamp, trigger_reason, evidence_snapshot all present |

### Section 14: Single Source Policy Tests

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-SSP-001 | PDF renders from artifacts only | Generate PDF; check network calls | No yfinance calls during PDF render |
| T-SSP-002 | Telegram reads from artifacts | Send signal message; verify source | run_id in message matches artifacts/plays.json run_id |
| T-SSP-003 | Run manifest generated per cycle | Complete scan cycle | artifacts/manifests/manifest_{run_id}.json created with all required fields |
| T-SSP-004 | Missing artifacts block output | Delete plays.json before Telegram send | Message blocked; INTEGRITY ALERT sent |
| T-SSP-005 | Schema validation on write | Write invalid schema to plays.json | Write rejected; original file preserved |
| T-SSP-006 | Cross-check run_id consistency | API response vs artifact file | run_id matches exactly |

### Section 15: Docker Drift Tests

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-DRIFT-001 | Parity check passes after clean deploy | rsync + build + parity check | All checksums match; PASS |
| T-DRIFT-002 | Parity check fails on host-only edit | Edit main.py on host without rebuild | Checksum mismatch; FAIL alert |
| T-DRIFT-003 | No host-only code detected | List all .py files; compare host vs container | All production .py files present in container |
| T-DRIFT-004 | Code hash logged on startup | Restart container | Log entry showing code_hash |
| T-DRIFT-005 | Daily scheduled parity check | Wait for scheduled check | Parity results written to checksum_comparison.json |
| T-DRIFT-006 | LKG tag created on successful deploy | Complete deploy + health check | Docker image tagged nzt48:lkg-YYYYMMDD-HHMM |

### Section 16: W13 War Room + Luxury Tests

| Test ID | Description | Steps | Expected Result |
|---------|-------------|-------|-----------------|
| T-PW-021 | System Wiring panel renders | Navigate to War Room | 7 indicators visible (DataHub, Engine, Artifacts, Telegram, PDF, Learning, Scheduler) |
| T-PW-022 | Wiring indicators correct | All systems healthy | All 7 indicators green |
| T-PW-023 | Wiring indicator turns red on failure | Stop engine | Engine indicator turns red within 60s |
| T-PW-024 | Readiness checklist visible at session times | Check War Room at 06:55 UK | Startup readiness checklist prominently displayed |
| T-LUX-001 | Evidence pack export | Click export button | ZIP file downloaded with all expected contents |
| T-LUX-002 | Deterministic replay | Replay yesterday | Replay results generated; no live sends |
| T-LUX-003 | Incident library | Trigger integrity alert | Incident record created in data/incidents.jsonl |
| T-LUX-004 | Manager one-pager | Wait for 07:00 UK schedule | PDF generated; Telegram [DAILY BRIEF] sent |
| T-LUX-005 | SLA dashboard | Navigate to SLA panel | Metrics displayed with colour coding |
| T-LUX-006 | Change impact simulator | Apply test change | Impact report generated showing affected paths |
| T-LUX-007 | Incident library War Room panel | Navigate to incidents | Recent incidents displayed with status |

### UPDATED SUMMARY (v3.1)

| Category | Test Count | ID Range |
|----------|-----------|----------|
| Unit Tests | 62 | T-UNIT-001 to T-UNIT-062 |
| Integration Tests | 24 | T-INT-001 to T-INT-024 |
| Regression Tests | 13 | T-REG-001 to T-REG-013 |
| Playwright War Room QA | 24 | T-PW-001 to T-PW-024 |
| PDF QA | 18 | T-PDF-001 to T-PDF-018 |
| Performance / SLA | 19 | T-PERF-001 to T-PERF-019 |
| Go-Live Gate Page | 15 | T-GATE-001 to T-GATE-015 |
| Telegram Mode Filtering | 7 | T-MODE-001 to T-MODE-007 |
| Wiring Proof Pack | 5 | T-PROOF-001 to T-PROOF-005 |
| Startup Readiness Gate | 8 | T-STARTUP-001 to T-STARTUP-008 |
| Wiring Paths | 10 | T-WIRE-001 to T-WIRE-010 |
| Integrity Monitor | 10 | T-INTEG-001 to T-INTEG-010 |
| Self-Healing | 8 | T-HEAL-001 to T-HEAL-008 |
| Single Source Policy | 6 | T-SSP-001 to T-SSP-006 |
| Docker Drift | 6 | T-DRIFT-001 to T-DRIFT-006 |
| Luxury Features | 7 | T-LUX-001 to T-LUX-007 |
| **TOTAL** | **242** | |
