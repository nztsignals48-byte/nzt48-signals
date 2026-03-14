# REGIME & DROUGHT STATE MANAGEMENT -- BINDING SPECIFICATION

**Document ID**: NZT48-ANNEX-001
**Version**: 1.0
**Date**: 2026-02-27
**Status**: BINDING -- All implementation MUST conform to this specification
**Scope**: Regime classification taxonomy, drought state machine, contradiction detection, transition actions

---

## 1. OBJECTIVE

Establish a single, canonical regime classification taxonomy for the entire NZT-48 system, define the drought state machine with escalation tiers, specify contradiction detection rules, and map every regime transition pair to a required portfolio action. This specification resolves the dual-taxonomy conflict identified in FORENSICS_MAP P0-4.

---

## 2. THE DUAL TAXONOMY PROBLEM (AS-IS)

Two incompatible regime taxonomies currently coexist:

### Taxonomy A -- `uk_isa/volatility_regime.py` (5 states)
| State | Description | Scope |
|-------|-------------|-------|
| `COMPRESSION` | Low vol, coiling, breakout imminent | Per-ticker |
| `EXPANSION` | Vol expanding, trend accelerating | Per-ticker |
| `BLOW_OFF` | Extreme vol spike, climactic move | Per-ticker |
| `EXHAUSTION` | Vol decelerating after expansion | Per-ticker |
| `BREAKDOWN` | Structural vol collapse / liquidity event | Per-ticker |

### Taxonomy B -- `config/settings.yaml` Section 7 (8 states)
| State | Description | Scope |
|-------|-------------|-------|
| `TRENDING_UP_STRONG` | Both QQQ+SPY > VWAP, strong slope | Market-wide |
| `TRENDING_UP_MOD` | 1 of 2 > VWAP, moderate slope | Market-wide |
| `TRENDING_DOWN_STRONG` | Both < VWAP, strong negative slope | Market-wide |
| `TRENDING_DOWN_MOD` | 1 of 2 < VWAP, moderate negative slope | Market-wide |
| `RANGE_BOUND` | Flat around VWAP, EMAs flat, VIX 15-22 | Market-wide |
| `HIGH_VOLATILITY` | VIX > 25 or range > 2x normal | Market-wide |
| `RISK_OFF` | VIX > 35 or SPY falls > -2% intraday | Market-wide |
| `SHOCK` | VIX > 45 or circuit breaker | Market-wide |

### Root Conflict
- Taxonomy A is a **per-ticker volatility regime** (instrument-level).
- Taxonomy B is a **market-wide directional regime** (system-level).
- No mapping function exists between them.
- Telegram can show "EXPANSION" while PDF shows "RANGE_BOUND" for the same moment.

---

## 3. DECISION: CANONICAL DUAL-LAYER REGIME MODEL

**RULING**: Both taxonomies are retained, but with strict layer separation and a required cross-layer consistency check.

### Layer 1: MARKET REGIME (system-wide, Taxonomy B -- 8 states)
- **Source of truth**: `config/settings.yaml` Section 7.
- **Computed by**: `RegimeClassifier` in main.py (reads QQQ, SPY, VIX, internals).
- **Stored in**: `regime_history` table (`state` column).
- **Used by**: Bot activation (BullBot/RangeBot/BearBot), signal gating, Telegram labels, PDF headers, War Room badge.
- **Update frequency**: Every scan cycle (60 seconds).
- **Canonical field name**: `market_regime`

### Layer 2: VOLATILITY REGIME (per-ticker, Taxonomy A -- 5 states)
- **Source of truth**: `uk_isa/volatility_regime.py`.
- **Computed by**: `VolatilityRegimeClassifier.classify_all()`.
- **Stored in**: `vol_regimes` table (`regime` column).
- **Used by**: Ticker-level scoring, PDF instrument sections, opportunity lane.
- **Update frequency**: Daily at 08:00 UTC (configurable via `settings.yaml:vol_regime.compute_hour`).
- **Canonical field name**: `vol_regime`

### Naming Convention (BINDING)
Every code path, log entry, Telegram message, PDF section, and API response MUST use the canonical field names:
- `market_regime` for system-wide regime (Layer 1)
- `vol_regime` for per-ticker volatility regime (Layer 2)
- The bare word "regime" without a prefix is FORBIDDEN in new code.

---

## 4. CROSS-LAYER CONSISTENCY MATRIX

The following combinations trigger a `CONSISTENCY_WARNING`:

| Market Regime | Vol Regime (majority of tickers) | Verdict | Action |
|---------------|----------------------------------|---------|--------|
| `TRENDING_UP_STRONG` | `COMPRESSION` | WARNING | Expected: EXPANSION. If COMPRESSION persists > 3 cycles, fire alert. |
| `TRENDING_UP_STRONG` | `BREAKDOWN` | CRITICAL | Contradiction. Fire `CONTRADICTION_ALERT` immediately. |
| `TRENDING_DOWN_STRONG` | `COMPRESSION` | WARNING | Expected: EXPANSION (inverse). Monitor. |
| `RISK_OFF` | `EXPANSION` | WARNING | Expected: BLOW_OFF or BREAKDOWN. Monitor. |
| `SHOCK` | `COMPRESSION` | CRITICAL | Impossible under SHOCK. Fire `CONTRADICTION_ALERT`. |
| `RANGE_BOUND` | `BLOW_OFF` | CRITICAL | BLOW_OFF in range is contradictory. Fire `CONTRADICTION_ALERT`. |

### Consistency Check Implementation
```
Function: check_regime_consistency(market_regime, vol_regime_distribution)
Trigger: Every scan cycle, after both layers are computed.
Output: {consistent: bool, level: "OK"|"WARNING"|"CRITICAL", message: str}
Action on CRITICAL: Send [SYSTEM] CONTRADICTION_ALERT to Telegram + War Room.
Action on WARNING: Log to system_state.json, display on War Room, no Telegram unless persists > 3 cycles.
```

---

## 5. DROUGHT STATE MACHINE

### 5.1 Definition
A **drought** is a period during which no qualifying signal has been generated and delivered to Telegram. Qualifying means: the signal passed ALL qualification gates (7-stage pipeline) AND was successfully sent to Telegram (not deduped, not rate-limited, not firewall-blocked).

### 5.2 Drought State Enum
```
DROUGHT_NONE     -- Normal operation. Signals flowing.
DROUGHT_WATCH    -- 10 consecutive scan cycles with no qualifying signal (~10 minutes).
DROUGHT_ACTIVE   -- 20 consecutive scan cycles with no qualifying signal (~20 minutes).
DROUGHT_CRITICAL -- 60 consecutive scan cycles with no qualifying signal (~1 hour).
```

### 5.3 State Transitions

```
DROUGHT_NONE  --[10 dry cycles]--> DROUGHT_WATCH
DROUGHT_WATCH --[10 more dry cycles (total 20)]--> DROUGHT_ACTIVE
DROUGHT_ACTIVE --[40 more dry cycles (total 60)]--> DROUGHT_CRITICAL
Any state --[qualifying signal sent to Telegram]--> DROUGHT_NONE
```

### 5.4 Cycle Counter Rules
| Rule | Description |
|------|-------------|
| **Increment** | Counter increments by 1 at the END of every scan cycle that produces zero qualifying signals sent to Telegram. |
| **Reset** | Counter resets to 0 ONLY when a signal passes ALL qualification gates AND is successfully sent to Telegram AND is not deduped/blocked. |
| **No partial reset** | A signal that is generated but blocked by firewall, deduped, or rate-limited does NOT reset the counter. |
| **Restart behaviour** | On system restart, counter resets to 0 (fresh start). The 5-minute quiet period (Section 8.2) does NOT count as drought cycles. |
| **Persistence** | Counter is persisted to `artifacts/system_state.json` field `drought_cycle_count` every cycle. On restart, read persisted value as initial counter (do NOT reset to 0 if stale < 2 hours). |

### 5.5 Escalation Actions

| State | Telegram Action | War Room Action | Log Action |
|-------|----------------|-----------------|------------|
| `DROUGHT_WATCH` | None (silent). | Yellow badge: "DROUGHT WATCH (10 cycles)". | `logger.info("DROUGHT_WATCH entered at cycle %d")` |
| `DROUGHT_ACTIVE` | Send `[DROUGHT] No qualifying signals for 20 cycles (~20 min). Market regime: {market_regime}. Vol regime majority: {vol_regime_majority}.` | Orange badge: "DROUGHT ACTIVE (20 cycles)". | `logger.warning("DROUGHT_ACTIVE entered")` |
| `DROUGHT_CRITICAL` | Send `[DROUGHT] CRITICAL -- No qualifying signals for 60 cycles (~1 hour). Operator review required. Market regime: {market_regime}.` | Red badge: "DROUGHT CRITICAL (60 cycles)". | `logger.error("DROUGHT_CRITICAL entered")` |

### 5.6 Drought Clearing Rules (BINDING)

A drought clears (transitions back to `DROUGHT_NONE`) ONLY when ALL of the following are true:
1. A signal is generated by any strategy (S1-S15).
2. The signal passes ALL 7 qualification stages.
3. The signal passes the emotional firewall (not blocked).
4. The signal passes Telegram validation (`validate_telegram_signal()` returns True).
5. The signal passes deduplication (`_dedupe.should_send()` returns True).
6. The signal passes rate limiting (`_rate_limiter.can_send()` returns True).
7. The signal is successfully sent to Telegram (`_send_message()` returns True).

If ANY of steps 1-7 fail, the drought counter continues to increment.

---

## 6. DROUGHT-REGIME CONTRADICTION DETECTION

### 6.1 Contradiction Rules

| # | Condition | Alert Type | Severity |
|---|-----------|------------|----------|
| C1 | `market_regime` in (`TRENDING_UP_STRONG`, `TRENDING_UP_MOD`, `TRENDING_DOWN_STRONG`, `TRENDING_DOWN_MOD`) AND `drought_state` in (`DROUGHT_ACTIVE`, `DROUGHT_CRITICAL`) | `CONTRADICTION_ALERT` | HIGH |
| C2 | `vol_regime` majority = `EXPANSION` AND `drought_state` in (`DROUGHT_ACTIVE`, `DROUGHT_CRITICAL`) | `CONTRADICTION_ALERT` | HIGH |
| C3 | `market_regime` = `COMPRESSION` AND `drought_state` = `DROUGHT_NONE` | NORMAL | N/A (compression = low signals expected) |
| C4 | `market_regime` = `RANGE_BOUND` AND `drought_state` = `DROUGHT_WATCH` | NORMAL | N/A (range-bound = fewer signals expected) |
| C5 | `market_regime` = `SHOCK` AND `drought_state` = `DROUGHT_NONE` | `CONTRADICTION_ALERT` | HIGH (SHOCK should halt signals, not produce them) |

### 6.2 Contradiction Alert Format
```
[SYSTEM] CONTRADICTION DETECTED
Market Regime: {market_regime} (since {regime_since})
Drought State: {drought_state} (cycle {drought_cycle_count})
Vol Regime Majority: {vol_regime_majority} ({n_expansion}/{n_total} tickers in EXPANSION)
Contradiction Rule: {rule_id} -- {description}
Operator Action Required: Review signal pipeline. Check data freshness. Check qualification gates.
```

### 6.3 Contradiction Check Frequency
- Run at the END of every scan cycle, after both regime layers and drought state are updated.
- If a contradiction is detected AND was already reported in the last 30 minutes, do NOT re-send to Telegram (dedupe by rule_id).
- Always update War Room regardless of dedupe.

---

## 7. REGIME TRANSITION ACTIONS

### 7.1 Market Regime Transition Matrix (BINDING)

Every transition from old market_regime to new market_regime triggers a mandatory portfolio action. The engine MUST execute the action before processing any new signals.

| Old Regime | New Regime | Required Action | Telegram Label |
|-----------|-----------|----------------|----------------|
| `TRENDING_UP_STRONG` | `TRENDING_UP_MOD` | Tighten long stops to +0.5R. Reduce new position size to 0.8x. | `[REGIME] TREND WEAKENING` |
| `TRENDING_UP_STRONG` | `RANGE_BOUND` | Tighten ALL long stops to breakeven. No new longs until range confirms. | `[REGIME] TREND -> RANGE` |
| `TRENDING_UP_STRONG` | `TRENDING_DOWN_STRONG` | FLATTEN all longs. Switch to short-hunting. Emergency action. | `[REGIME] TREND REVERSAL (EMERGENCY)` |
| `TRENDING_UP_STRONG` | `TRENDING_DOWN_MOD` | FLATTEN all longs. Evaluate short opportunities. | `[REGIME] TREND REVERSAL` |
| `TRENDING_UP_STRONG` | `HIGH_VOLATILITY` | Tighten all stops to breakeven. Reduce size to 0.5x. No new entries. | `[REGIME] HIGH VOL WARNING` |
| `TRENDING_UP_STRONG` | `RISK_OFF` | FLATTEN all positions. Cash. No entries. | `[REGIME] RISK OFF` |
| `TRENDING_UP_STRONG` | `SHOCK` | EMERGENCY FLATTEN all positions. Kill switch activated. | `[REGIME] SHOCK (KILL)` |
| `TRENDING_UP_MOD` | `TRENDING_UP_STRONG` | Widen stops to standard. Increase size to 1.0x. | `[REGIME] TREND STRENGTHENING` |
| `TRENDING_UP_MOD` | `RANGE_BOUND` | Tighten long stops to breakeven. Reduce size to 0.6x. | `[REGIME] TREND -> RANGE` |
| `TRENDING_UP_MOD` | `TRENDING_DOWN_STRONG` | FLATTEN all longs. Switch to short-hunting. | `[REGIME] TREND REVERSAL (EMERGENCY)` |
| `TRENDING_UP_MOD` | `TRENDING_DOWN_MOD` | FLATTEN all longs. Evaluate shorts. | `[REGIME] TREND REVERSAL` |
| `TRENDING_UP_MOD` | `HIGH_VOLATILITY` | Tighten stops. Reduce size 0.5x. | `[REGIME] HIGH VOL WARNING` |
| `TRENDING_UP_MOD` | `RISK_OFF` | FLATTEN all. Cash. | `[REGIME] RISK OFF` |
| `TRENDING_UP_MOD` | `SHOCK` | EMERGENCY FLATTEN. Kill switch. | `[REGIME] SHOCK (KILL)` |
| `TRENDING_DOWN_STRONG` | `TRENDING_DOWN_MOD` | Tighten short stops to -0.5R. Reduce size 0.8x. | `[REGIME] TREND WEAKENING` |
| `TRENDING_DOWN_STRONG` | `RANGE_BOUND` | Tighten short stops to breakeven. No new shorts. | `[REGIME] TREND -> RANGE` |
| `TRENDING_DOWN_STRONG` | `TRENDING_UP_STRONG` | FLATTEN all shorts. Switch to long-hunting. | `[REGIME] TREND REVERSAL (EMERGENCY)` |
| `TRENDING_DOWN_STRONG` | `TRENDING_UP_MOD` | FLATTEN all shorts. Evaluate longs. | `[REGIME] TREND REVERSAL` |
| `TRENDING_DOWN_STRONG` | `HIGH_VOLATILITY` | Tighten stops. Reduce size 0.5x. | `[REGIME] HIGH VOL WARNING` |
| `TRENDING_DOWN_STRONG` | `RISK_OFF` | FLATTEN all. Cash. | `[REGIME] RISK OFF` |
| `TRENDING_DOWN_STRONG` | `SHOCK` | EMERGENCY FLATTEN. Kill switch. | `[REGIME] SHOCK (KILL)` |
| `TRENDING_DOWN_MOD` | `TRENDING_DOWN_STRONG` | Widen stops. Size 1.0x. | `[REGIME] TREND STRENGTHENING` |
| `TRENDING_DOWN_MOD` | `RANGE_BOUND` | Tighten short stops to breakeven. | `[REGIME] TREND -> RANGE` |
| `TRENDING_DOWN_MOD` | `TRENDING_UP_STRONG` | FLATTEN all shorts. | `[REGIME] TREND REVERSAL (EMERGENCY)` |
| `TRENDING_DOWN_MOD` | `TRENDING_UP_MOD` | FLATTEN all shorts. Evaluate longs. | `[REGIME] TREND REVERSAL` |
| `TRENDING_DOWN_MOD` | `HIGH_VOLATILITY` | Tighten stops. 0.5x size. | `[REGIME] HIGH VOL WARNING` |
| `TRENDING_DOWN_MOD` | `RISK_OFF` | FLATTEN all. Cash. | `[REGIME] RISK OFF` |
| `TRENDING_DOWN_MOD` | `SHOCK` | EMERGENCY FLATTEN. Kill switch. | `[REGIME] SHOCK (KILL)` |
| `RANGE_BOUND` | `TRENDING_UP_STRONG` | Opportunity. Enter long direction. +10 confidence bonus. | `[REGIME] BREAKOUT UP` |
| `RANGE_BOUND` | `TRENDING_UP_MOD` | Opportunity. Cautious longs. +5 confidence bonus. | `[REGIME] TRENDING UP` |
| `RANGE_BOUND` | `TRENDING_DOWN_STRONG` | Opportunity. Enter short direction. +10 confidence bonus. | `[REGIME] BREAKDOWN` |
| `RANGE_BOUND` | `TRENDING_DOWN_MOD` | Cautious shorts. +5 confidence bonus. | `[REGIME] TRENDING DOWN` |
| `RANGE_BOUND` | `HIGH_VOLATILITY` | Reduce size 0.5x. Tighten stops. | `[REGIME] HIGH VOL WARNING` |
| `RANGE_BOUND` | `RISK_OFF` | FLATTEN all. Cash. | `[REGIME] RISK OFF` |
| `RANGE_BOUND` | `SHOCK` | EMERGENCY FLATTEN. Kill switch. | `[REGIME] SHOCK (KILL)` |
| `HIGH_VOLATILITY` | `RANGE_BOUND` | Resume normal sizing. Remove vol warnings. | `[REGIME] VOL NORMALISING` |
| `HIGH_VOLATILITY` | `TRENDING_UP_STRONG` | Resume trend following. Size 0.8x (cautious). | `[REGIME] TREND EMERGING` |
| `HIGH_VOLATILITY` | `TRENDING_DOWN_STRONG` | Resume trend following (short bias). Size 0.8x. | `[REGIME] TREND EMERGING (SHORT)` |
| `HIGH_VOLATILITY` | `RISK_OFF` | FLATTEN all. Cash. | `[REGIME] RISK OFF` |
| `HIGH_VOLATILITY` | `SHOCK` | EMERGENCY FLATTEN. Kill switch. | `[REGIME] SHOCK (KILL)` |
| `RISK_OFF` | Any non-SHOCK | Resume with 0.25x size for 30 minutes, then normal. | `[REGIME] RISK OFF CLEARING` |
| `SHOCK` | Any | Deactivate kill switch. Resume with 0.25x size. 60-minute ramp. | `[REGIME] SHOCK CLEARING` |

### 7.2 Transition Execution Rules
1. **Atomicity**: Transition action MUST complete before any new signal is processed.
2. **Logging**: Every transition fires a log entry: `logger.warning("REGIME_TRANSITION: %s -> %s, action=%s, positions_affected=%d")`.
3. **Telegram**: Every transition sends a `[REGIME]` labelled message (see Section 7.1).
4. **War Room**: Every transition broadcasts via WebSocket `{"type": "REGIME_CHANGE", "data": {...}}`.
5. **Cooldown**: After a transition, ignore the NEXT scan cycle's regime output (prevent flapping). Minimum regime hold time = 2 scan cycles (2 minutes).
6. **Persistence**: Current market_regime is persisted to `regime_history` table AND `artifacts/system_state.json`.

### 7.3 Regime Flapping Protection
- If market_regime changes more than 3 times in 10 minutes, enter `REGIME_FLAPPING` state.
- In `REGIME_FLAPPING`: Hold current positions, no new entries, size = 0.25x.
- Exit `REGIME_FLAPPING` when regime is stable for 5 consecutive cycles.
- Fire `[SYSTEM] REGIME FLAPPING DETECTED -- holding positions, no new entries` to Telegram.

---

## 8. FAILURE MODES

| # | Failure Mode | Detection | Impact | Mitigation |
|---|-------------|-----------|--------|------------|
| F1 | Regime classifier crashes | try/except in main.py scan loop | market_regime goes stale | Use last known regime. Fire `[ERROR] Regime classifier failed`. Mark regime as STALE in War Room. |
| F2 | Vol regime classifier crashes | try/except in classify_all() | vol_regime goes stale | Use last known vol_regime. Log warning. |
| F3 | Both classifiers return same regime for 24+ hours | Monitor via system_state.json | Classifier may be stuck | Fire `[SYSTEM] Regime classifier may be stuck -- same state for 24h`. |
| F4 | Drought counter overflows | Counter is uint64 | Theoretical only | Cap at 99999. Fire DROUGHT_CRITICAL if not already fired. |
| F5 | Contradiction check crashes | try/except wrapper | Contradiction goes undetected | Log error. Contradiction check is a soft gate -- system continues. |
| F6 | Transition action fails (e.g., flatten fails) | try/except on each position close | Positions remain open in wrong regime | Retry once. If still fails, fire `[ERROR] FLATTEN FAILED -- manual intervention required`. |
| F7 | Regime flapping during high-impact news | Flapping detector (Section 7.3) | Excessive transitions | Enter REGIME_FLAPPING state. Hold all positions. |

---

## 9. OPERATOR ACTIONS

| Scenario | Operator Action |
|----------|----------------|
| `CONTRADICTION_ALERT` received | Check data freshness (yfinance staleness). Check qualification gates (are they too tight?). Review last 10 rejected signals. |
| `DROUGHT_CRITICAL` received | Check if market is actually open. Check data feed health. Check if all strategies are dormant. Review gate rejection reasons. |
| `REGIME FLAPPING` received | Wait for stability. Do NOT manually force regime. Review VIX and SPY for cause. |
| `FLATTEN FAILED` received | SSH to server. Check position status manually. Force close via `/close` Telegram command or direct DB update. |
| Regime stuck for 24h | Check `regime_history` table. Verify classifier is running. Check yfinance data freshness. |

---

## 10. ACCEPTANCE TESTS

### 10.1 Regime Taxonomy Tests
| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T1 | Set market_regime to each of 8 states via DB injection | War Room badge updates, Telegram label correct, bot activation matches settings.yaml | All 8 states display correctly |
| T2 | Set vol_regime for QQQ3.L to each of 5 states via DB injection | PDF instrument section shows correct vol_regime | All 5 states render correctly |
| T3 | Search codebase for bare "regime" (without `market_` or `vol_` prefix) in new code | Zero matches in new code paths | grep returns 0 matches in diff |
| T4 | Trigger TRENDING_UP_STRONG -> SHOCK transition | All longs flattened, kill switch activated, Telegram `[REGIME] SHOCK (KILL)` sent | Positions = 0, kill file exists |
| T5 | Trigger 4 regime changes in 5 minutes | REGIME_FLAPPING detected, no new entries, 0.25x size | Flapping badge on War Room |

### 10.2 Drought State Tests
| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T6 | Run 10 scan cycles with no signals | drought_state = DROUGHT_WATCH | system_state.json shows DROUGHT_WATCH |
| T7 | Run 20 scan cycles with no signals | drought_state = DROUGHT_ACTIVE, Telegram message sent | Telegram log shows [DROUGHT] message |
| T8 | Run 60 scan cycles with no signals | drought_state = DROUGHT_CRITICAL | Telegram log shows CRITICAL message |
| T9 | After DROUGHT_ACTIVE, generate a signal that passes all gates and is sent to Telegram | drought_state = DROUGHT_NONE, counter = 0 | system_state.json shows DROUGHT_NONE |
| T10 | After DROUGHT_ACTIVE, generate a signal that is BLOCKED by firewall | drought_state remains DROUGHT_ACTIVE, counter continues | Counter did not reset |
| T11 | After DROUGHT_ACTIVE, generate a signal that is DEDUPED | drought_state remains DROUGHT_ACTIVE | Counter did not reset |

### 10.3 Contradiction Tests
| # | Test | Expected Result | Pass Criteria |
|---|------|-----------------|---------------|
| T12 | Set market_regime=TRENDING_UP_STRONG, drought_state=DROUGHT_ACTIVE | CONTRADICTION_ALERT fired (rule C1) | Telegram log shows CONTRADICTION message |
| T13 | Set market_regime=RANGE_BOUND, drought_state=DROUGHT_WATCH | No alert (normal for range-bound) | No CONTRADICTION message |
| T14 | Set market_regime=SHOCK, drought_state=DROUGHT_NONE | CONTRADICTION_ALERT fired (rule C5) | Alert sent |
| T15 | Fire same contradiction twice within 30 minutes | Second alert deduped (not re-sent to Telegram) | Only 1 Telegram message |

---

## 11. PROOF ARTIFACTS

Upon implementation, the following artifacts MUST be produced and committed:

| # | Artifact | Location | Description |
|---|----------|----------|-------------|
| A1 | Regime naming audit | `artifacts/regime_naming_audit.txt` | grep output showing all `market_regime` and `vol_regime` usages in codebase |
| A2 | Drought state test log | `artifacts/drought_test_log.jsonl` | JSONL output from running tests T6-T11 |
| A3 | Contradiction test log | `artifacts/contradiction_test_log.jsonl` | JSONL output from running tests T12-T15 |
| A4 | Transition matrix test log | `artifacts/transition_test_log.jsonl` | JSONL output from testing each transition in Section 7.1 |
| A5 | Updated system_state.json schema | `artifacts/system_state_schema.json` | JSON Schema showing drought_state, drought_cycle_count, market_regime, vol_regime fields |
| A6 | Flapping detector test | `artifacts/flapping_test_log.jsonl` | JSONL from rapid regime toggling test |

---

## 12. CONFIGURATION PARAMETERS

All parameters MUST be configurable via `config/settings.yaml` under a new `regime_management` section:

```yaml
regime_management:
  # Drought thresholds (scan cycles)
  drought_watch_threshold: 10
  drought_active_threshold: 20
  drought_critical_threshold: 60

  # Regime stability
  min_regime_hold_cycles: 2
  flapping_threshold_changes: 3
  flapping_threshold_window_minutes: 10
  flapping_stability_cycles: 5

  # Contradiction dedupe
  contradiction_dedupe_minutes: 30

  # Transition ramp-up
  risk_off_ramp_minutes: 30
  risk_off_ramp_size: 0.25
  shock_ramp_minutes: 60
  shock_ramp_size: 0.25
```

---

## REVISION HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | NZT-48 Spec Engine | Initial binding specification |
