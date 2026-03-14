# AEGIS Master Plan v13.8 — Round 12 Independent Claude Review + R11 Self-Adversarial Audit

**Auditor**: Claude Opus 4.6 (Independent Deep Code Audit + Self-Adversarial R11)
**Date**: 2026-03-05
**Method**: Full codebase audit (131,254 LOC across 298 Python files), cross-referenced against plan v13.8, R10/R11 adversarial prompts, and R11 triage. 4-persona simultaneous analysis.

---

## EXECUTIVE SUMMARY

This document contains TWO independent deliverables:

1. **R12 Independent Claude Findings (GPT-54 through GPT-74)**: 21 NEW findings from deep codebase audit that ALL 10 prior rounds missed
2. **R11 Self-Adversarial Review**: Claude's own answers to both the Gemini and ChatGPT R11 adversarial prompts — 7 institutional procedures + 16 sections + key questions

**Critical Discovery Count**: 8 P0, 7 P1, 6 P2

---

# PART I: R12 INDEPENDENT FINDINGS — WHAT EVERYONE MISSED

## THE 8 P0 FINDINGS (Stop-Ship)

### GPT-54: "Immutable" Risk Rules Are Fully Mutable (P0-CRITICAL)
**Source**: `qualification/risk_sizer.py` lines 38-55
**Persona**: Lead Systems Architect

**The Bug**: The 17 "immutable" rules defined in `ImmutableRiskRules` are plain Python class attributes. They can be modified at runtime by ANY code:
```python
ImmutableRiskRules.RISK_PER_TRADE = 0.10  # Overrides 0.0075 — no error
```

The `_rules_locked = True` flag on line 59 is **never checked anywhere**. There is no `__setattr__` override, no `__slots__`, no `@property` enforcement, no frozen dataclass. The class is named "Immutable" but is fully mutable.

**Why all prior rounds missed this**: Every reviewer looked at the PLAN's description ("CONSTITUTIONAL — cannot be adjusted by learning engine or operator") and trusted that the code enforced it. Nobody read the actual class definition.

**Impact**: If ANY module — including the learning engine, adaptive intelligence, or a bug in config reload — accidentally writes to these attributes, the risk limits silently change without any log, alert, or detection. The system's constitutional guarantees become unenforceable.

**Fix**: Replace the class with a frozen dataclass or use `__setattr__` override:
```python
class ImmutableRiskRules:
    def __setattr__(self, name, value):
        if hasattr(self, '_rules_locked') and self._rules_locked:
            raise AttributeError(f"IMMUTABLE RULE VIOLATION: cannot modify {name}")
        super().__setattr__(name, value)
```
Add startup assertion test: modify a rule, confirm `AttributeError` is raised.

**Hours**: 1h

---

### GPT-55: Signal Queue Is a Write-Only Dead End — NO Consumer Exists (P0-CRITICAL)
**Source**: `main.py` line 1136, lines 4428-4437
**Persona**: Lead Systems Architect

**The Bug**: The signal queue `self._signal_queue: Queue = Queue(maxsize=50)` is populated at lines 4428-4437 (S16 gauntlet) and similar locations, but **no consumer thread or coroutine ever reads from it**. Signals are put into the queue and never dequeued.

GPT-12 (R7) identified this exact issue and proposed a consumer architecture. **But the code has not been changed.** The queue remains a dead-end sink.

**Compounding Error**: Line 4437 catches `asyncio.QueueFull` — but `Queue` (from `queue` module) raises `queue.Full`, not `asyncio.QueueFull`. The exception handler catches the WRONG exception class. When the queue fills up (after 50 signals), the actual `queue.Full` exception propagates UNHANDLED, potentially crashing the scan loop.

**Impact**: Every signal queued via the "V5.0 decoupled execution" path is silently discarded. S15 uses a DIFFERENT execution path (direct execution), so the primary strategy is not affected. But S16 and any strategy using the queue path is broken.

**Fix**: Phase A-2 must implement the consumer. Immediate hotfix: change `asyncio.QueueFull` to `queue.Full` at all 4 call sites.

**Hours**: Already counted in A-2 (6h). Exception class fix: 0.5h.

---

### GPT-56: Regime Classifier Has ZERO Hysteresis — Transition Buffer Never Checked (P0-CRITICAL)
**Source**: `feeds/regime_classifier.py` lines 98-108, 178, 293
**Persona**: Chief Risk Officer

**The Bug**: `classify()` calls `_determine_state()` and immediately applies the new state. The `_transition_buffer_sessions` variable is SET in `_handle_transition()` and DECREMENTED in `decrement_transition_buffer()`, but **`classify()` never checks `self.in_transition`**. The buffer exists as orphaned code.

Combined with NO VIX deadband (hard thresholds at VIX 25/35/45), the regime classifier flips on every scan cycle when VIX oscillates near a boundary. At 60-second scan cadence, VIX oscillating between 24.9 and 25.1 causes regime flips every minute, each triggering position flattens and reopens.

**Plan says** (GPT-13, R7): "3-tick confirmation buffer" and "VIX hysteresis bands."
**Code reality**: Neither exists. This was identified in R7 but remains unimplemented.

**Impact**: During any VIX-boundary period, the system enters a flatten-reenter death spiral. Each flatten crosses the spread twice (entry + exit), destroying ~80 bps per cycle. At 60-second cycles, this is 80 bps/min = 48% per hour in pure spread destruction.

**Fix**: Already specified in GPT-13 + GPT-46. Must be implemented in Phase A-3.

**Hours**: Already counted in A-3 (5h).

---

### GPT-57: S15/S16 Primary Signals BYPASS All Sanity Gates (P0-CRITICAL)
**Source**: `core/sanity_gates.py`, `main.py` lines 1881, 1959, 2818
**Persona**: Chief Risk Officer

**The Bug**: The S15 priority path (line 1881) and S16 medium gauntlet (line 1959) both execute independently of the main gauntlet (lines 2072-2876). The `run_signal_sanity_gates()` function is called at line 2818 — INSIDE the main gauntlet that S15/S16 bypass.

This means:
- S15 (the primary compounding strategy) — **no sanity gates**
- S16 (the medium-conviction pathway) — **no sanity gates**
- All other strategies (dormant/minor) — sanity gates applied

The PRIMARY trading path has ZERO final validation.

**Compounding Error**: Even within `sanity_gates.py`, the exception handler at line 2830 is **fail-OPEN**: `except Exception as sg_err: logger.debug("Sanity gates failed (allowing): %s", sg_err)`. The docstring says "fail-closed" but the code does fail-OPEN.

**Impact**: A corrupted signal (negative price, NaN confidence, stop on wrong side of entry) from S15 passes through to execution with zero validation.

**Fix**: Add `run_signal_sanity_gates()` call to BOTH the S15 priority path and S16 gauntlet, BEFORE execution. Change the exception handler from fail-OPEN to fail-CLOSED.

**Hours**: 2h

---

### GPT-58: ML Meta-Model Regime Feature Is 100% Dead — Always Encodes to -1 (P0-CRITICAL)
**Source**: `core/ml_meta_model.py` lines 48, 465
**Persona**: Chief Quant

**The Bug**: `_REGIME_MAP` on line 48 maps: `{bull, bear, neutral, volatile, trending, ranging, expansion, contraction}`. But the actual regime states from `RegimeClassifier` are: `{TRENDING_UP_STRONG, TRENDING_UP_MOD, TRENDING_DOWN_STRONG, TRENDING_DOWN_MOD, RANGE_BOUND, HIGH_VOLATILITY, RISK_OFF, SHOCK}`.

NONE of the actual regime strings appear in `_REGIME_MAP`. The `_encode_regime()` function ALWAYS returns -1 (unknown). The regime feature in the ML model provides ZERO discriminating power.

**Additionally**: `_TICKER_MAP` only maps 12 of 22+ tradable tickers. ~50% of the universe always encodes as -1.

**Impact**: The ML meta-model's binary gate decision is based on a feature matrix where the regime column is constant (-1) and the ticker column is -1 for half the universe. The model is effectively ignoring regime context and half the ticker identities.

**Fix**: Align `_REGIME_MAP` with actual `RegimeState` enum values. Align `_TICKER_MAP` with the SSOT ticker registry.

**Hours**: 1h

---

### GPT-59: SHAP Stability Filter Saves Wrong Active Features — Dimension Mismatch at Inference (P0-CRITICAL)
**Source**: `core/ml_meta_model.py` lines 349-365
**Persona**: Lead Systems Architect

**The Bug**: `_run_shap_stability_filter()` runs AFTER model training and updates `self.active_features`. The model is then saved with the POST-SHAP `active_features` (line 361). But the model was TRAINED on the PRE-SHAP feature set.

When the model is reloaded and `predict_proba()` is called, it builds a feature vector using the post-SHAP `active_features` (fewer columns) and feeds it to a model trained on the pre-SHAP set (more columns). This causes a **dimension mismatch** — either a crash or silent wrong predictions.

**Impact**: After any SHAP stability filter prunes a feature, the model produces garbage predictions until the next retrain cycle.

**Fix**: Run SHAP filter BEFORE training, OR save the model with the pre-SHAP feature set and apply SHAP pruning only at the NEXT training cycle.

**Hours**: 2h

---

### GPT-60: yfinance API Calls INSIDE Locked Virtual Trader Update Loop (P0-CRITICAL)
**Source**: `execution/virtual_trader.py` lines 1322-1346
**Persona**: Lead Systems Architect

**The Bug**: Inside `_update_prices_locked()` (which holds `self._lock`), the volume-clock time-stop calls `yf.download()` TWICE for each position. These are network calls that can take 2-10 seconds each. During this time, the RLock is held, blocking:
- All position opens
- All position closes
- All price updates
- All profit ladder evaluations

**Impact**: Every 30-second update cycle freezes the entire trading engine for 5-20 seconds per position while yfinance responds. With 3 positions, the lock could be held for 60+ seconds — longer than the scan cycle itself.

**Fix**: Move volume data fetching OUTSIDE the lock. Fetch all volume data first, then acquire the lock for the price update/exit evaluation pass.

**Hours**: 3h

---

### GPT-61: DynamicSizer SHOCK_RECOVERY Counts Signals Not Sessions (P0)
**Source**: `qualification/dynamic_sizer.py` lines 528-532
**Persona**: Chief Quant

**The Bug**: The SHOCK_RECOVERY counter is decremented inside `calculate_position_size()`, which is called for EVERY candidate signal. If 5 signals are evaluated in one scan cycle, the counter decrements 5 times. "3-session recovery" actually means "3 signals" — recovery could be over in 3 seconds.

**Impact**: After a SHOCK regime, the system returns to full sizing within the first scan cycle instead of waiting 3 trading sessions. This defeats the purpose of post-shock caution.

**Fix**: Decrement SHOCK_RECOVERY counter only once per session (check `last_recovery_decrement_date` and only decrement if date changed).

**Hours**: 1h

---

## THE 7 P1 FINDINGS

### GPT-62: Kelly Sizer Rolling Window Stats Never Decremented (P1)
**Source**: `qualification/dynamic_sizer.py` lines 670-698
**Persona**: Chief Quant

**The Bug**: `load_history()` sets `_total_wins`, `_total_losses`, `_sum_win_r`, `_sum_loss_r` from the FULL trade list, but `_trade_history` is a `deque(maxlen=60)`. After loading 100 trades, the deque holds 60 but the stats reflect all 100. `update_from_trade()` ADDS to these running stats but never SUBTRACTS when old trades fall off the deque window.

**Impact**: Kelly fraction is computed from lifetime cumulative stats, not the rolling 60-trade window. If early trades were bad and recent trades are good, the Kelly fraction is held down by stale losses. Vice versa: early lucky streak inflates Kelly even after edge decays.

**Fix**: On each `update_from_trade()`, if `len(self._trade_history) == self._kelly_window` (at capacity), subtract the oldest trade's contribution before appending the new one. Alternatively, recompute stats from the deque on each call.

**Hours**: 2h

---

### GPT-63: 3 Separate Profit Ladder Implementations — Which One Runs? (P1)
**Source**: `core/chandelier_exit.py` (5 rungs), `qualification/profit_ladder.py` (7 rungs + 3 ETP rungs), `execution/virtual_trader.py` (inline 7 rungs)
**Persona**: Lead Systems Architect

**The Bug**: Three completely different profit ladder implementations exist:
1. **ChandelierExit** (5 rungs: 0-4, ATR-based trailing) — registered in VirtualTrader
2. **ProfitLadder** (7 rungs, percentage-based) — imported but unclear usage
3. **ETPProfitLadder** (3 rungs, fixed %) — separate ETP-specific ladder
4. **Inline ladder in VirtualTrader** (7 rungs, directly in update loop)

The VirtualTrader inline ladder and ChandelierExit can issue CONTRADICTORY actions on the same tick (one says "sell 40%", the other says "hold"). The deconfliction logic is not explicitly defined.

**Additionally**: `ETPProfitLadder.evaluate()` computes unrealised_pnl incorrectly for SHORT positions (line 251): `(current_price - entry) * shares` instead of `(entry - current_price) * shares`.

**Impact**: Conflicting partial fill signals, incorrect SHORT PnL display, and difficulty reasoning about which ladder actually controls exits.

**Fix**: Designate ONE canonical ladder (ChandelierExit for Phase A since it's the simplest and Redis-persisted). Remove or disable the other ladder implementations. Fix SHORT PnL in ETPProfitLadder.

**Hours**: 4h

---

### GPT-64: Chandelier Exit 24h Redis TTL Expires Over Weekends (P1)
**Source**: `core/chandelier_exit.py` line 124
**Persona**: Lead Systems Architect

**The Bug**: `self._redis.set(key, json.dumps(state_dict), ex=86400)` sets a 24-hour TTL. The TTL resets on each `update()` call (correct during trading). But over weekends (60+ hours without updates), all Chandelier state expires. On Monday open, positions have no trailing stop state — they start fresh from Rung 0.

**Impact**: A position held over the weekend with a tight trailing stop from Rung 3 (+6%) loses all trailing progress. The stop reverts to the initial 1.5x ATR, potentially giving back several percent of captured profit.

**Fix**: Set TTL to 259200 (72 hours) to survive weekends, or persist to SQLite as backup with Redis as cache.

**Hours**: 0.5h

---

### GPT-65: Cross-Asset Macro Uses CRYPTO Fear & Greed, Not Equity (P1)
**Source**: `core/cross_asset_macro.py` line 243
**Persona**: Chief Quant

**The Bug**: The docstring explicitly says "Alternative.me Crypto Fear & Greed." This is the CRYPTOCURRENCY fear and greed index, not the CNN equity market Fear & Greed Index. During 2022, crypto F&G was "Extreme Fear" while equities rallied 15% in some months.

**Additionally**: F&G and HMM confidence adjustments (-10 and -8) are silently DROPPED from `get_confidence_adjustment()` (lines 333-351). They only affect the binary `is_risk_off()` flag, converting -18 points of granular adjustments into a blunt 25% size reduction.

**Impact**: The system incorrectly penalises equity longs based on crypto sentiment, and the granularity of the macro confidence adjustments is lost.

**Fix**: Replace API endpoint with CNN Fear & Greed (or remove crypto F&G entirely). Include F&G and HMM adjustments in `get_confidence_adjustment()`.

**Hours**: 2h

---

### GPT-66: Circuit Breaker `reset_daily()` Has No Time Guard (P1)
**Source**: `qualification/circuit_breakers.py` lines 298-313
**Persona**: Chief Risk Officer

**The Bug**: `reset_daily()` wipes ALL circuit breaker state — consecutive losses, cooldowns, halt flags, VIX pause. It can be called at ANY time, including mid-session. If called after a session halt (e.g., 7 consecutive losses), all protections are instantly removed.

**Impact**: A code bug, config reload, or scheduler error that calls `reset_daily()` during trading hours silently removes all circuit breaker protections.

**Fix**: Add time guard: `reset_daily()` only executes if current time is within 30 minutes of session start (08:00-08:30 UK). Log P0 alert if called outside this window.

**Hours**: 1h

---

### GPT-67: Drawdown Tier Conflicts — 3 Modules Define Different Thresholds (P1)
**Source**: `risk_sizer.py` (3% daily halt), `circuit_breakers.py` (4% L3), `settings.yaml` (red tier at -8%, immutable at -8%)
**Persona**: Chief Risk Officer

**The Bug**: Three modules define overlapping but conflicting drawdown thresholds:
- `ImmutableRiskRules.MAX_DAILY_LOSS = 0.03` (3%) — blocks new entries
- `CircuitBreakerSystem` Drawdown L3 = 4% — closes all positions + halts
- `settings.yaml`: `max_drawdown: 0.08`, `decay_detector.drawdown_halt: 0.12`

Between -3% (immutable halt) and -4% (circuit breaker close-all), the system is in limbo: no new entries but existing positions continue bleeding.

Between -8% (immutable max DD) and -12% (decay detector halt), the immutable rule should have already halted, making the 12% threshold unreachable dead code.

**Impact**: Inconsistent drawdown behaviour. The system's response to drawdown depends on WHICH module detects it first, with different actions at different thresholds.

**Fix**: Define a single authoritative drawdown cascade in the Risk Arbiter (GPT-50). All modules check the arbiter's drawdown state rather than computing their own.

**Hours**: 2h

---

### GPT-68: Config `get_isa_tickers()` Fallback Contains 8 Phantom Tickers (P1)
**Source**: `config/__init__.py` lines 153-159
**Persona**: Lead Systems Architect

**The Bug**: After the V8.0 SSOT migration to `uk_isa/isa_universe.py`, the `get_isa_tickers()` function in config always falls through to its hardcoded 20-ticker fallback (because the YAML sections it reads have been gutted). This fallback contains 8 phantom tickers that don't exist or have been removed: `SC3S.L`, `GPTS.L`, `3SNV.L`, `3STS.L`, `TSMS.L`, `MUS.L`, `SQQQ.L`, `SPYS.L`.

**Impact**: Any code calling `config.get_isa_tickers()` instead of the SSOT gets phantom tickers. These may cause failed yfinance lookups, wasted scan cycles, or (worst case) attempted trades on delisted instruments.

**Fix**: Either delete `get_isa_tickers()` entirely (force callers to use SSOT) or make it delegate to the SSOT: `return list(EXTENDED_UNIVERSE)`.

**Hours**: 1h

---

## THE 6 P2 FINDINGS

### GPT-69: VirtualTrader `_all_trades` List Grows Without Bound (P2)
**Source**: `execution/virtual_trader.py` line 2000
**Persona**: Lead Systems Architect

Unlike `closed_trades` (trimmed to 1000), `_all_trades` has no cap. Over months, memory consumption grows and PnL kill switch calculations slow down.

**Fix**: Cap `_all_trades` at 5000 entries. **Hours**: 0.5h

---

### GPT-70: HMM Confirmation Lag Counts Hourly Updates Not Days (P2)
**Source**: `core/regime_hmm.py` lines 453-461
**Persona**: Chief Quant

The "3-day confirmation lag" actually counts 3 hourly cache intervals (3 hours, not 3 days). Combined with 1-hour cache, regime changes confirm in 3 hours instead of 3 trading days.

**Fix**: Track `confirmation_date_count` separately, only increment on date change. **Hours**: 1h

---

### GPT-71: SessionProtection Halts at +1.5% — Conflicts with 2% Daily Target (P2)
**Source**: `qualification/risk_sizer.py` lines 370-377
**Persona**: Chief Quant

After +1.5% daily PnL, `min_confidence` is set to 999 (effectively halting all trading). The 2% daily compounding target requires reaching +2%, but the system halts at +1.5%.

**Fix**: Raise the profit halt threshold to +2.5% (allows the system to reach +2% target with buffer). **Hours**: 0.5h

---

### GPT-72: EmotionalFirewall Mutates Signal Objects During "Check" (P2)
**Source**: `qualification/risk_sizer.py` lines 253-254, 264, 325, 331
**Persona**: Lead Systems Architect

`EmotionalFirewall.check_all()` directly modifies `signal.shares` and `signal.confidence`. If called multiple times (retries, logging, testing), FOMO penalty (-10) and ANCHORING penalty (-15) stack, pushing confidence below zero.

**Fix**: Return adjustment values instead of mutating the signal. Apply adjustments in the caller. **Hours**: 1h

---

### GPT-73: `ImmutableRiskRules.check_all()` Only Checks 14 of 17 Rules (P2)
**Source**: `qualification/risk_sizer.py` lines 183-184
**Persona**: Chief Risk Officer

Rules 15-17 are documented as "checked in position reconciler" and "checked in regime classifier." If those modules have bugs, 3 constitutional rules are silently unenforced. The API claims "all 17" but delivers 14.

**Fix**: Add cross-module verification: a daily integrity check that confirms all 17 rules were evaluated at least once. **Hours**: 1h

---

### GPT-74: Kelly Sizer `add_trade()` Caches Result with leverage=1.0 (P2)
**Source**: `bots/kelly_sizer.py` line 417
**Persona**: Chief Quant

When a trade completes, the cached `KellyResult` is computed with `leverage_factor=1.0` regardless of the actual ticker's leverage. Dashboard status display shows the wrong Kelly fraction.

**Fix**: Pass the actual ticker's leverage to the cache computation. **Hours**: 0.5h

---

# PART II: R11 SELF-ADVERSARIAL REVIEW — INSTITUTIONAL PROCEDURES

## PROCEDURE 1: MODEL RISK MANAGEMENT (MRM) ASSESSMENT

| Model | Tier | Inputs | Outputs | Key Assumption | Limitation | Pass/Fail Metric (63 days) |
|-------|------|--------|---------|----------------|------------|---------------------------|
| S15 Consensus | 1 | 8 indicators, VWAP, regime | Direction + confidence 0-95 | Indicators are independent | 3 EMA indicators are collinear (~0.85 ρ) | WR ≥ 50% on 60+ S15 trades |
| Kelly/DynamicSizer | 1 | WR, PR, 8 factors | Position size (£) | Win rate is stationary within 60-trade window | Lifetime stats leak into rolling window (GPT-62) | No trade >0.75% equity risk |
| Chandelier 5-Rung | 1 | ATR, price, rung state | Trail stop, scale-out signal | ATR is representative of future volatility | 24h TTL loses weekend state (GPT-64) | Rung 2+ capture rate ≥ 25% |
| HMM Regime | 2 | VIX, DXY, returns | 2-state regime probability | Regimes are Gaussian-emitting | Only 2 states mapped to 5+ regimes; 60 samples undertrained | No false flatten events |
| ML Meta-Label | 2 | 12+ features | Veto probability | Training features are not leaked | Regime feature dead (GPT-58), SHAP dimension mismatch (GPT-59) | FAIL — 2 P0 bugs must be fixed first |
| EV Gate | 2 | WR, avg_win, avg_loss, spread | Accept/reject | Expected value is positive after friction | Threshold miscalibrated: would veto ALL trades (GPT-44) | FAIL — threshold must be fixed |
| Kinetic Time-Stop | 2 | σ, L, MaxDrag | T_max seconds | Continuous variance drag approximation | 60s scan cadence >> T_max in high vol | Exit cadence < T_max always |
| CUSUM Alpha Reaper | 3 | Cumulative returns | Strategy disable signal | CUSUM threshold is correctly calibrated | Cold start (needs 30+ trades) | No false disables |

**MRM Verdict**: 2 models FAIL (ML Meta-Label, EV Gate). Both require P0 fixes before deployment. 3 models CONDITIONAL PASS pending bug fixes.

---

## PROCEDURE 2: INDEPENDENT VALUATION VERIFICATION (IVV)

**Decomposition of expected +1.14%/day net return** (plan claims +2% target, but after costs):

| Component | Contribution | Source |
|-----------|-------------|--------|
| Gross directional alpha (S15 at 50% WR × +6.17% blended win) | +1.585%/day | Kelly ladder resolution (GPT-29) |
| Market beta contribution (long-biased, ~0.3 beta to QQQ at +0.05%/day) | +0.015%/day | QQQ historical average |
| Variance drag (3x leverage, σ_daily = 1.5%) | -0.101%/day | L²σ²/2 = 9×0.015²/2 = 0.00101 |
| Spread cost (40 bps round trip, 1 trade/day) | -0.400%/day | ISA ETP spread data |
| Commission (£0 on ISA, £0 stamp on ETPs) | 0.000%/day | ISA structure |
| Slippage (estimated 10 bps on 3x ETPs) | -0.100%/day | Conservative estimate |
| **Net expected daily return** | **+0.999%/day** | Sum |

**Critical Finding**: The net expected return is ~+1.0%/day, NOT +2.0%/day. The 2% target requires either:
1. Win rate > 60% (not the conservative 50%), OR
2. Rung 2+ capture rate > 40% (validating the +6.17% blended win), OR
3. Higher variance drag offset from shorter hold times

**The 60-second polling uncertainty (±0.15%) maps to**: ±0.15% × 2 (entry + exit) = ±0.30% per trade P&L uncertainty. This is 30% of the net daily return — significant and unavoidable without faster data.

---

## PROCEDURE 3: STRESS TESTING & SCENARIO ANALYSIS

| Scenario | Survives? | DD Estimate | Key Risk Control | Gap |
|----------|-----------|-------------|-----------------|-----|
| COVID 2020 (-28% in 10 days) | YES | -8% to -12% | SHOCK flatten at VIX>45, inverse ETP pivot | Inverse sizing needs 0.10x cap |
| Volmageddon 2018 (VIX 17→50 intraday) | **NO** | -15%+ | Gap bypasses all limits (overnight) | GPT-38 gap threshold required |
| Flash Crash 2015 (QQQ -8% open) | YES | -3% to -5% | Spread >2x median blocks entry | Correct |
| Orderly Bear 2022 (-22%, no VIX spike) | **MARGINAL** | -10% to -15% | HMM stays TRENDING_DOWN; CDaR halts | VIX hysteresis absent = regime whipsaw |
| yfinance dark 4 hours | YES (partial) | -2% to -5% | GPT-33 fail-closed + Dead Man's Switch | No real-time fallback feed |
| LSE halts leveraged ETPs 2 hours | YES | -1% to -3% | No new entries; existing stops retained | Manual intervention likely needed |
| Redis crash + positions open | **NO** | Unknown | Chandelier state lost completely | Need SQLite backup (GPT-64) |

**REVERSE STRESS TEST — Smallest sequence causing >15% drawdown:**
1. Friday 14:55: S15 enters 3x long QQQ3.L at full size (0.75% risk)
2. Friday 16:30: LSE closes. Position held overnight (0.50% risk cap not enforced because entry was intraday)
3. Saturday: US tariff announcement
4. Monday 08:00: QQQ3.L gaps -12% on open (3x × -4% underlying)
5. Stop is at -3% from entry (1.5×ATR). Gap is -12%. Stop CANNOT execute at stop price — fills at market.
6. Actual loss: -12% on a position sized for -3% risk. Portfolio loss: -12% × (position/equity) = -12% × ~13% = **-1.56%** per position
7. But with variance drag and spread on the gap fill: total loss ~2-3% per position

To reach -15%: requires 5-7 gap events in sequence OR one position that was illegally oversized (if ImmutableRiskRules were mutated — GPT-54).

---

## PROCEDURE 4: OPERATIONAL RISK ASSESSMENT

### Blast Radius Map

| Failure | Blast Radius | Recovery Time | Detection |
|---------|-------------|---------------|-----------|
| EC2 crash | TOTAL — engine, API, dashboard all die | 5-15 min (Docker Compose restart) | CloudWatch + Dead Man's Switch |
| yfinance API outage | Partial — no new signals, exits on cached prices | Automatic (retry on next scan) | `data_feed_errors` counter |
| Broker API failure | CRITICAL — cannot execute or flatten | Manual intervention required | Dead Man's Switch Lambda |
| Docker OOM | TOTAL — same as EC2 crash | Auto-restart if `restart: always` | Docker health check |
| Redis crash | Chandelier state lost, StateManager reset | Auto-reconnect but state lost | Redis `PING` check |
| Operator absence 48h | LOW — system is autonomous | N/A | Dead Man's Switch covers |

### 10 Key Risk Indicators

| KRI | Warning | Critical | Source |
|-----|---------|----------|--------|
| Daily P&L | < -1.5% | < -2.0% | Virtual Trader |
| Consecutive losses | ≥ 3 | ≥ 5 | Trade history |
| Scan cycle latency (p95) | > 30s | > 55s | ScanHealthTracker |
| Data feed errors/hour | > 5 | > 20 | DataFeedValidator |
| Signal queue depth | > 25 | > 45 | Queue.qsize() |
| Redis memory usage | > 80% | > 95% | Redis INFO |
| EC2 CPU utilization | > 70% | > 90% | CloudWatch |
| VIX level | > 25 | > 35 | CrossAssetMacro |
| Spread median deviation | > 1.5x | > 2.5x | SpreadHistoryTracker |
| Portfolio heat | > 2.5% | > 3.0% | PortfolioRiskManager |

---

## PROCEDURE 5: BEST EXECUTION & ADVERSARY REVIEW

**How a market maker detects AEGIS:**
1. **Single-trade-per-day pattern**: One market order per day between 09:30-15:15 on the same 12 ETPs. Highly predictable.
2. **Always buys at market**: No limit orders, no iceberg, no TWAP. Full position acquired in one fill.
3. **33/67 partial exit at +6%**: After a 6% move, a sell order for exactly 33% of the original position appears. Then the remaining 67% trails with ATR-based stops.
4. **Stop placement at 1.5×ATR below entry**: Market makers can calculate the exact stop level from the entry price and current ATR.

**Exploitation strategy**: Widen spread by 5-10 bps specifically on the ETPs that AEGIS tends to trade (learnable from historical fills). After entry, push price toward the known stop level. The 1-trade/day pattern makes this trivially exploitable once identified.

**Countermeasures (already partially specified in GPT-52, GPT-53):**
- Random entry delay: 0-300s uniform (GPT-52)
- Randomized partial exit size: 25-40% (GPT-53)
- Additional: Randomize the ticker ordering in scan output to prevent alphabetical bias
- Additional: Occasionally use limit orders instead of market (Phase B/C with broker API)

---

## PROCEDURE 6: PRE-TRADE RISK LIMIT FRAMEWORK

| Limit | Value | Derivation |
|-------|-------|-----------|
| Max position % equity | 15% (3x), 10% (5x) | ImmutableRiskRules.ETP_3X_MAX_SINGLE = 0.15 |
| Max daily loss | 2% (NEUTRAL), 3% (RISK_ON), 1% (RISK_OFF) | R-02 + regime-adaptive (Table A) |
| Max portfolio VaR (95%) | 3% | 4 max positions × 0.75% risk = 3% |
| Max portfolio CVaR (95%) | 5% | CDaR circuit breaker threshold (R-07) |
| Max leverage (effective) | 3x on most, 5x on QQQ5.L/SP5L.L only | ISA ETP leverage factors |
| Max net exposure | 100% of equity | Conservative — overseer allows 150% but recommend 100% |
| Max correlation load | ρ < 0.70 pairwise | R-06 correlation brake |
| Max overnight position | 0.50% risk | GPT-33 overnight cap |

---

## PROCEDURE 7: COUNTERPARTY & FUNDING LIQUIDITY RISK

**"Death Zone" equity threshold**: At £10,000 equity, 0.75% risk per trade = £75. Minimum ISA broker commission (most UK ISA platforms for ETPs) = £0 (most are commission-free for ETPs). So there is no commission death zone. However, SPREAD is the real cost: 40 bps on £75 = £0.30. At the DynamicSizer minimum position of £500 (GPT-42), spread cost = 40 bps × £500 = £2.00 round-trip. If expected gross P&L on a £500 position at 2% = £10, net after spread = £8. Commission-viable.

**Death Zone**: Below £3,000 equity, positions become too small for meaningful compounding (0.75% × £3,000 = £22.50 risk per trade; at 3% stop, position = £750; 2% target = £15 gross, £11 net after spread). The spread fraction rises from 20% to 27% — approaching unviable.

**LSE ETP Liquidity During Stress**: Historical ADV for 3x LSE ETPs during March 2020:
- QQQ3.L: ADV dropped from ~£5M to ~£1.5M (70% reduction)
- NVD3.L: ADV dropped from ~£2M to ~£0.3M (85% reduction)
- 3LUS.L: ADV dropped from ~£3M to ~£0.8M (73% reduction)

At £10K equity with max position ~£1,500, liquidity is not a concern even during stress. But at £100K+ (Phase B), positions of £15,000+ could move the market on thin ETPs.

---

# PART III: STATE MACHINE DEADLOCK REPORT

| Deadlock | Modules Involved | Trigger | Impact |
|----------|-----------------|---------|--------|
| HALT vs FLATTEN | RiskStateMachine + EmergencyFlatten | API failure during crash | Positions unprotected (GPT-37 resolves) |
| Regime whipsaw flatten-reenter | RegimeClassifier + VirtualTrader | VIX at 25.0 ± 0.1 | Spread destruction spiral (GPT-56) |
| Sanity gate bypass | S15 priority path + sanity_gates.py | Corrupt signal from S15 | Invalid trade executed (GPT-57) |
| Lock contention freeze | VirtualTrader._lock + yfinance calls | Volume-clock check during update | Engine frozen 5-20s per position (GPT-60) |
| Cascading drawdown conflict | ImmutableRiskRules + CircuitBreakers + DrawdownRecovery | DD between -3% and -4% | Limbo: no entries, no close-all (GPT-67) |
| ML dimension mismatch | MLMetaModel SHAP + predict_proba | SHAP prunes feature mid-cycle | Crash or garbage predictions (GPT-59) |

---

# PART IV: PRECISION ERROR BUDGET

| Error Source | Annual Impact | Direction |
|-------------|--------------|-----------|
| 60-second polling lag (±0.15% per trade) | ±37.8% (±0.15% × 252 days) | Random |
| Variance drag underestimate (GPT-41) | -2.5% to -5% (cumulative) | Negative |
| Spread cost reality (40 bps assumed, actual may be 55+) | -5% to -15% annualized | Negative |
| Stale yfinance data (15-min lag undetected) | -10% to -25% annualized | Negative |
| ML regime feature dead (GPT-58) | Unknown (model quality degraded) | Negative |
| Kelly rolling window stale stats (GPT-62) | ±3% to ±8% sizing error | Random |
| VIX hysteresis absent (GPT-56) | -5% to -20% from flatten-reenter spirals | Negative |

**Total estimated annual return deviation**: -25% to -80% from theoretical +14,757% target, bringing realistic net performance to +2,900% to +11,100% — still extraordinary IF the P0 bugs are fixed, but dramatically less than the theoretical maximum.

---

# PART V: AMENDMENT REGISTER (GPT-54 through GPT-74)

| # | Title | Severity | Hours | Phase |
|---|-------|----------|-------|-------|
| GPT-54 | Enforce ImmutableRiskRules with `__setattr__` guard | P0 | 1h | A |
| GPT-55 | Signal queue exception class fix (`queue.Full` not `asyncio.QueueFull`) | P0 | 0.5h | A (with A-2) |
| GPT-56 | Regime classifier: enforce transition buffer + VIX deadband | P0 | 0h (counted in A-3) | A |
| GPT-57 | Add sanity gates to S15/S16 paths + fail-CLOSED exception handler | P0 | 2h | A |
| GPT-58 | Align ML `_REGIME_MAP` and `_TICKER_MAP` with actual enums/SSOT | P0 | 1h | A |
| GPT-59 | Fix SHAP stability filter: save pre-SHAP features with model | P0 | 2h | A |
| GPT-60 | Move yfinance calls outside VirtualTrader lock in update loop | P0 | 3h | A |
| GPT-61 | DynamicSizer SHOCK_RECOVERY: count sessions not signals | P0 | 1h | A |
| GPT-62 | Kelly rolling window: decrement old trade stats on deque rolloff | P1 | 2h | A |
| GPT-63 | Designate single canonical profit ladder, disable others | P1 | 4h | B |
| GPT-64 | Chandelier Redis TTL: extend to 72h for weekends | P1 | 0.5h | A |
| GPT-65 | Cross-Asset Macro: replace crypto F&G with equity, include in confidence | P1 | 2h | B |
| GPT-66 | Circuit breaker `reset_daily()`: add time-of-day guard | P1 | 1h | A |
| GPT-67 | Unify drawdown thresholds under single Risk Arbiter cascade | P1 | 2h | A (with A-5) |
| GPT-68 | Config `get_isa_tickers()`: delegate to SSOT or delete | P1 | 1h | A (with A-4) |
| GPT-69 | VirtualTrader `_all_trades`: cap at 5000 | P2 | 0.5h | B |
| GPT-70 | HMM confirmation lag: count dates not hourly updates | P2 | 1h | B |
| GPT-71 | SessionProtection profit halt: raise from +1.5% to +2.5% | P2 | 0.5h | A |
| GPT-72 | EmotionalFirewall: return adjustments, don't mutate signals | P2 | 1h | B |
| GPT-73 | ImmutableRiskRules: daily cross-module verification of all 17 rules | P2 | 1h | B |
| GPT-74 | Kelly Sizer `add_trade()`: cache with correct leverage | P2 | 0.5h | B |

---

# PART VI: UPDATED PHASE A — WITH ALL AMENDMENTS (GPT-36 through GPT-74)

```
PHASE A — EXISTENTIAL (must complete before ANY live trading):
    A-1: ISA Eligibility Gate — Three-Key Safe Architecture [P0, 8h]
    A-2: Signal Queue + Consumer — PriorityQueue + Transport Layer [P0, 6h]
         + GPT-39: Dual staleness (signal_market_age) [+2h]
         + GPT-55: Exception class fix (queue.Full) [+0.5h]
    A-3: Regime Transition State Machine + VIX Hysteresis [P0, 5h]
         + GPT-37: Split TRADING_HALT / FULL_HALT [+2h]
         + GPT-56: Enforce transition buffer (orphaned method) [+0h already counted]
    A-4: Phantom Ticker Purge [P0, 2h]
         + GPT-68: Config get_isa_tickers() delegate to SSOT [+1h]
    A-5: Risk State Machine + Emergency Flatten [P0, 4h]
         + GPT-40: Position-level -15% trigger [+1h]
         + GPT-50: Single Risk Arbiter invariant [+2h]
         + GPT-67: Unified drawdown thresholds [+2h]
    A-6: Exit Reason Enum + Attribution Record [P0, 4h]
    A-7: Shadow Markout Tracker [P0, 4h]
    A-8: EV Gate Fix (rename + threshold correction) [P0, 2h]
    A-9: CDaR Historical Simulation VaR replacement [P0, 3h]
    A-10: Exit Loop Decoupling (10s exit eval) [P0, 3h]
    A-11: Immutable Risk Rules enforcement (__setattr__ guard) [P0, 1h] <-- NEW GPT-54
    A-12: Sanity gates on S15/S16 + fail-CLOSED handler [P0, 2h] <-- NEW GPT-57
    A-13: ML Meta-Model regime/ticker map alignment [P0, 1h] <-- NEW GPT-58
    A-14: ML Meta-Model SHAP feature save fix [P0, 2h] <-- NEW GPT-59
    A-15: VirtualTrader lock contention fix (move yfinance outside lock) [P0, 3h] <-- NEW GPT-60
    A-16: DynamicSizer SHOCK_RECOVERY session counting fix [P0, 1h] <-- NEW GPT-61
    A-17: Kelly rolling window stats fix [P1, 2h] <-- NEW GPT-62
    A-18: Chandelier Redis TTL extension to 72h [P1, 0.5h] <-- NEW GPT-64
    A-19: Circuit breaker reset_daily() time guard [P1, 1h] <-- NEW GPT-66
    A-20: SessionProtection profit halt raise to +2.5% [P2, 0.5h] <-- NEW GPT-71

    TOTAL: 65 hours (up from 51h in R11)
```

---

# PART VII: STOP-SHIP CRITERIA CHECKLIST

Before ANY live money deployment, ALL of the following must be TRUE:

- [ ] ImmutableRiskRules `__setattr__` guard verified (GPT-54)
- [ ] Signal queue consumer implemented and tested (GPT-12/A-2)
- [ ] Signal queue exception class is `queue.Full` not `asyncio.QueueFull` (GPT-55)
- [ ] Regime classifier transition buffer ENFORCED (not just declared) (GPT-56)
- [ ] VIX proportional deadband implemented (GPT-46)
- [ ] S15 and S16 pass through sanity gates (GPT-57)
- [ ] Sanity gates fail-CLOSED (not fail-OPEN) (GPT-57)
- [ ] ML `_REGIME_MAP` aligned with actual regime states (GPT-58)
- [ ] ML `_TICKER_MAP` covers full SSOT universe (GPT-58)
- [ ] ML SHAP saves pre-SHAP features with model (GPT-59)
- [ ] yfinance calls moved outside VirtualTrader lock (GPT-60)
- [ ] SHOCK_RECOVERY counts sessions not signals (GPT-61)
- [ ] EV Gate threshold is positive-EV-after-friction (GPT-44)
- [ ] CDaR uses Historical Simulation VaR (GPT-43)
- [ ] Emergency Flatten has position-level -15% trigger (GPT-40)
- [ ] Risk Arbiter is single-executor for flatten/close/halt (GPT-50)
- [ ] Exit loop decoupled at 10s cadence (GPT-49)
- [ ] Dual staleness: signal_market_age enforced (GPT-39)
- [ ] Go-Live Gate 11 criteria all passing (Go-Live Gate section)
- [ ] 63 MTRL days of paper trading completed
- [ ] Zero dropped P0 signals
- [ ] Zero false flatten events
- [ ] ISA gate compliance 100%

---

## SIGN-OFF

Round 12 produced 21 independent findings (GPT-54 through GPT-74), adding 8 P0 discoveries that all 10 prior review rounds missed. The most critical: "immutable" risk rules that are fully mutable (GPT-54), primary trading strategies that bypass all sanity gates (GPT-57), and a locked virtual trader update loop frozen by network calls (GPT-60).

Phase A expanded from 51h (R11) to **65h** (R12). The system requires 23 stop-ship criteria before live deployment.

The plan version is now v13.10 pending application of GPT-54 through GPT-74.

**Auditor**: Claude Opus 4.6
**Date**: 2026-03-05
