# NZT-48 Sanity Gate Specification

**Version:** 1.0
**Status:** Active
**Applies to:** All signal paths (S15 Daily Target, V2 PDF generation, predictive scoring)
**Enforcement:** Mandatory pre-publish check. No signal reaches delivery without passing ALL gates.

---

## Objective

Define mathematical and logical sanity checks that run BEFORE any signal is published. These gates form the last line of defence between computed values and user-facing output. A signal that violates any gate is blocked, tagged with the specific failure code, and logged for forensic review.

The sanity gate layer exists because:

1. Leveraged ETPs (3x, 5x) amplify both real moves and computational errors.
2. Division-by-zero and stale-data bugs produce astronomically wrong numbers that look plausible in isolation.
3. A single published garbage signal destroys trust in the entire system.

Every gate is binary: PASS or BLOCK. There is no "warn and publish". If a gate fails, the signal does not ship.

---

## Gate Definitions

### GATE 1: RETURN MAGNITUDE

**Code:** `SANITY_FAIL_MAGNITUDE`

**Rule:**

| Condition | Threshold | Action |
|---|---|---|
| Single-session return (intraday) | > +30% or < -30% | BLOCK |
| Overnight / futures implied return | > +8% or < -8% | BLOCK |

**Rationale:** Even 3x leveraged LSE ETPs rarely exceed 25% in a single session. A computed return of 45% or 120% is almost certainly a double-leverage bug or a stale-price artefact. The 8% overnight cap catches futures-implied moves that have been erroneously amplified.

**Implementation:**

```python
def gate_return_magnitude(return_pct: float, session_type: str) -> tuple[bool, str]:
    """
    Returns (passed: bool, tag: str).
    session_type: 'intraday' | 'overnight'
    """
    if session_type == 'intraday':
        threshold = 30.0
    elif session_type == 'overnight':
        threshold = 8.0
    else:
        return False, "SANITY_FAIL_MAGNITUDE_UNKNOWN_SESSION"

    if abs(return_pct) > threshold:
        return False, f"SANITY_FAIL_MAGNITUDE_{session_type.upper()}_{return_pct:+.2f}%"

    return True, ""
```

**Edge case:** A return of exactly +30.00% PASSES. A return of +30.01% BLOCKS. The threshold is exclusive (`>`), not inclusive (`>=`).

---

### GATE 2: LEVERAGE-ONCE ASSERTION

**Code:** `SANITY_FAIL_DOUBLE_LEVERAGE`

**Rule:** For every return displayed to the user, verify it has NOT been multiplied by the leverage factor more than once.

**Rationale:** The most dangerous class of bug in the system. A 3x ETP that moves 5% intraday has a 15% leveraged return. If the code multiplies by 3 again (because two different functions both apply leverage), the displayed return becomes 45% -- plausible enough to look real on a volatile day, catastrophically wrong for position sizing.

**Implementation:**

Every computation path that touches returns must carry a `leverage_applied` boolean flag:

```python
@dataclass
class ReturnComputation:
    raw_return_pct: float
    leverage_factor: float
    leverage_applied: bool = False
    computation_path: list[str] = field(default_factory=list)

    def apply_leverage(self, caller: str) -> float:
        if self.leverage_applied:
            raise SanityGateError(
                "SANITY_FAIL_DOUBLE_LEVERAGE",
                f"Leverage already applied. First: {self.computation_path[-1]}, "
                f"Second attempt: {caller}"
            )
        self.leverage_applied = True
        self.computation_path.append(caller)
        return self.raw_return_pct * self.leverage_factor
```

**Tracking points:** The `leverage_applied` flag must be checked at:

- `predictive_scoring.py` when computing forward return estimates
- `pdf_v2_momentum.py` when formatting return columns for PDF output
- `daily_target.py` (S15) when computing the 2% target reachability score
- `lse_registry.py` when computing daily change percentages

If `leverage_applied` is already `True` when a second multiplication is attempted, the gate fires immediately. The signal is blocked and both the first and second application sites are logged.

---

### GATE 3: DIVISION-BY-ZERO GUARDS

**Code:** `SANITY_FAIL_DIV_ZERO`

**Rule:** All division operations must have explicit guards. A denominator of zero (or effectively zero, defined as `abs(x) < 1e-10`) must never reach a division operator.

**Required guard locations:**

| File | Line(s) | Denominator Variable | Context |
|---|---|---|---|
| `pdf_v2_risk.py` | 399 | `avg_vol_20` | 20-day average volume used as normaliser |
| `pdf_v2_momentum.py` | 429 | `max_pts` | Maximum points used for percentage-of-range calc |
| `volatility_regime.py` | 254 | `vix_ann_vol` | Annualised VIX volatility for regime classification |
| `predictive_scoring.py` | 867-868 | `fwd_returns` | Forward return array used in denominator for normalisation |
| `lse_registry.py` | 331-333 | Previous close price | Price change percentage calculation |

**Implementation pattern:**

```python
def safe_divide(numerator: float, denominator: float, context: str) -> float:
    """
    Returns numerator / denominator, or raises SanityGateError if
    denominator is effectively zero.
    """
    if abs(denominator) < 1e-10:
        raise SanityGateError(
            "SANITY_FAIL_DIV_ZERO",
            f"Division by zero avoided. Context: {context}, "
            f"numerator={numerator}, denominator={denominator}"
        )
    return numerator / denominator
```

Every guarded location must use `safe_divide()` or an equivalent explicit check. Raw `/` operators at these locations are a spec violation.

**Note:** This gate fires at computation time, not at the pre-publish checkpoint. It prevents the bad value from ever being computed, rather than catching it downstream.

---

### GATE 4: DATA COMPLETENESS

**Code:** `SANITY_FAIL_DATA_INCOMPLETE` | `SANITY_FAIL_SYSTEM_DOWN`

**Rule:**

| Completeness | Action |
|---|---|
| >= 80% of required fields non-null and non-stale | PASS |
| 50% -- 79% | BLOCK signal, tag `SANITY_FAIL_DATA_INCOMPLETE` |
| < 50% | Enter `SYSTEM_DOWN` state, suppress ALL signals system-wide |

**Required fields per signal:**

```
ticker, current_price, open, high, low, close, volume,
avg_vol_20, atr_14, rsi_14, regime, confidence, score,
leverage_factor, timestamp, sector
```

Total required fields: 16.

- 80% threshold = at least 13 of 16 fields present and non-stale.
- 50% threshold = at least 8 of 16 fields present and non-stale.

**Staleness definition:** A field is stale if its source timestamp is older than 2x the expected refresh interval. For daily bars, stale means older than 48 hours. For intraday, stale means older than 2x the bar interval.

**Implementation:**

```python
def gate_data_completeness(signal: dict, required_fields: list[str]) -> tuple[bool, str]:
    total = len(required_fields)
    present = sum(
        1 for f in required_fields
        if signal.get(f) is not None and not is_stale(signal, f)
    )
    ratio = present / total

    if ratio >= 0.80:
        return True, ""
    elif ratio >= 0.50:
        missing = [f for f in required_fields if signal.get(f) is None or is_stale(signal, f)]
        return False, f"SANITY_FAIL_DATA_INCOMPLETE_{present}/{total}_missing={missing}"
    else:
        return False, f"SANITY_FAIL_SYSTEM_DOWN_{present}/{total}"
```

**SYSTEM_DOWN behaviour:** When triggered, no signals are published for any ticker until completeness recovers above 50%. A SYSTEM_DOWN event is logged as CRITICAL severity and triggers an alert.

---

### GATE 5: REGIME COHERENCE

**Code:** `SANITY_FAIL_REGIME_DROUGHT_CONTRADICTION`

**Rule:** If `regime == EXPANSION` and `drought == True` simultaneously, this is a logical contradiction. Expansion means volatility is expanding and opportunity is high; drought means no viable signals exist. These two states are mutually exclusive.

**Action on contradiction:** Tag as `REGIME_DROUGHT_CONTRADICTION`, suppress ALL signals until the contradiction resolves. This is not a per-signal block -- it is a system-level hold.

**Implementation:**

```python
def gate_regime_coherence(regime: str, drought: bool) -> tuple[bool, str]:
    if regime == "EXPANSION" and drought is True:
        return False, "SANITY_FAIL_REGIME_DROUGHT_CONTRADICTION"
    return True, ""
```

**Resolution:** The contradiction resolves when either:

1. The regime classifier updates to a non-EXPANSION state, OR
2. The drought flag clears because viable signals are found.

The system re-evaluates on every scan cycle (60s). No manual intervention required for resolution, but the contradiction window is logged with start and end timestamps.

---

### GATE 6: CONFIDENCE BOUNDS

**Code:** `SANITY_FAIL_CONFIDENCE_OOB` | `SANITY_FAIL_SCORE_OOB` | `SANITY_FAIL_SCORE_ZERO`

**Rule:**

| Field | Valid Range | Boundary Behaviour | Failure Action |
|---|---|---|---|
| `confidence` | [0, 100] inclusive | 0 and 100 are valid | BLOCK if outside range |
| `score` | [0, 100] inclusive | 0 is always blocked | BLOCK if outside range OR if exactly 0 |

**Rationale:** Confidence and score are user-facing metrics. A confidence of 150 or -20 is a computation error. A score of 0 means the scoring pipeline produced no meaningful result and the signal must not ship.

**Implementation:**

```python
def gate_confidence_bounds(confidence: float, score: float) -> tuple[bool, str]:
    if not (0 <= confidence <= 100):
        return False, f"SANITY_FAIL_CONFIDENCE_OOB_{confidence}"

    if not (0 <= score <= 100):
        return False, f"SANITY_FAIL_SCORE_OOB_{score}"

    if score == 0:
        return False, "SANITY_FAIL_SCORE_ZERO"

    return True, ""
```

**Edge cases:**

- `confidence = 0` PASSES (low confidence is valid information).
- `score = 0` BLOCKS (zero score means no signal quality).
- `confidence = 100` PASSES.
- `score = 100` PASSES.
- `confidence = 100.0001` BLOCKS.
- `score = -0.001` BLOCKS.

---

### GATE 7: OHLC INTEGRITY

**Code:** `SANITY_FAIL_OHLC`

**Rule:** Every price bar must satisfy ALL of the following:

| Check | Condition | Tag Suffix |
|---|---|---|
| High >= Low | `high >= low` | `HIGH_LT_LOW` |
| All prices non-negative | `open >= 0 AND high >= 0 AND low >= 0 AND close >= 0` | `NEGATIVE_PRICE` |
| Volume non-negative | `volume >= 0` | `NEGATIVE_VOLUME` |
| Open within range | `low <= open <= high` | `OPEN_OOB` |
| Close within range | `low <= close <= high` | `CLOSE_OOB` |

**Action:** Any violation causes the ENTIRE bar to be rejected. A rejected bar is excluded from all calculations. If rejection causes the data window to fall below the minimum required length, GATE 4 (Data Completeness) will catch the downstream effect.

**Implementation:**

```python
def gate_ohlc_integrity(o: float, h: float, l: float, c: float, v: float) -> tuple[bool, str]:
    failures = []

    if h < l:
        failures.append("HIGH_LT_LOW")
    if any(p < 0 for p in [o, h, l, c]):
        failures.append("NEGATIVE_PRICE")
    if v < 0:
        failures.append("NEGATIVE_VOLUME")
    if not (l <= o <= h):
        failures.append("OPEN_OOB")
    if not (l <= c <= h):
        failures.append("CLOSE_OOB")

    if failures:
        return False, f"SANITY_FAIL_OHLC_{'|'.join(failures)}"

    return True, ""
```

**Note on yfinance data:** LSE `.L` tickers occasionally return bars where `open` is marginally outside `[low, high]` due to auction pricing. A tolerance of 0.1% is applied to the open/close range checks for `.L` tickers only:

```python
tolerance = 0.001 * (h - l) if ticker.endswith('.L') else 0
```

---

### GATE 8: TEMPORAL CONSISTENCY

**Code:** `SANITY_FAIL_TEMPORAL`

**Rule:**

| Check | Condition | Tag Suffix |
|---|---|---|
| Monotonic timestamps | Each bar's timestamp > previous bar's timestamp within a session | `NON_MONOTONIC` |
| No future timestamps | `bar_timestamp <= now + max_clock_skew` (max_clock_skew = 60 seconds) | `FUTURE_TIMESTAMP` |
| TTL enforcement | `now - bar_timestamp <= TTL` | `STALE_DATA` |

**TTL values:**

| Data Type | TTL |
|---|---|
| Daily bars | 48 hours |
| Intraday bars (1m-15m) | 2x bar interval |
| Regime classification | 6 hours |
| Predictive scores | 12 hours |

**Implementation:**

```python
def gate_temporal_consistency(
    timestamps: list[datetime],
    now: datetime,
    ttl: timedelta,
    max_clock_skew: timedelta = timedelta(seconds=60)
) -> tuple[bool, str]:
    failures = []

    # Monotonic check
    for i in range(1, len(timestamps)):
        if timestamps[i] <= timestamps[i - 1]:
            failures.append(f"NON_MONOTONIC_at_index_{i}")

    # Future check
    for i, ts in enumerate(timestamps):
        if ts > now + max_clock_skew:
            failures.append(f"FUTURE_TIMESTAMP_at_index_{i}_{ts.isoformat()}")

    # TTL check (applied to most recent bar only)
    if timestamps and (now - timestamps[-1]) > ttl:
        age = now - timestamps[-1]
        failures.append(f"STALE_DATA_age={age}_ttl={ttl}")

    if failures:
        return False, f"SANITY_FAIL_TEMPORAL_{'|'.join(failures)}"

    return True, ""
```

---

## Verdict Logic

```
VERDICT = GATE_1 AND GATE_2 AND GATE_3 AND GATE_4 AND GATE_5 AND GATE_6 AND GATE_7 AND GATE_8
```

**ALL gates must pass.** This is strict AND logic. There is no weighting, no majority-vote, no "pass if 7 of 8 gates clear". A single failure blocks the signal.

**Execution order:** Gates are evaluated in numerical order (1 through 8). Evaluation does NOT short-circuit -- all gates are always evaluated so the log contains the complete set of failures, not just the first one encountered.

**On failure:**

1. The signal is tagged with ALL failing gate codes (comma-separated).
2. The signal is written to `logs/blocked_signals/` with full context.
3. The signal is NOT published to PDF, dashboard, or any delivery channel.
4. A summary line is written to `logs/sanity_gate.log`.

**On pass:**

1. The signal proceeds to the delivery pipeline.
2. A PASS entry is written to `logs/sanity_gate.log` with the signal's fingerprint.

```python
def run_all_gates(signal: dict) -> tuple[bool, list[str]]:
    """
    Returns (passed: bool, failure_tags: list[str]).
    All gates run regardless of earlier failures.
    """
    failures = []

    # Gate 1: Return Magnitude
    ok, tag = gate_return_magnitude(signal['return_pct'], signal['session_type'])
    if not ok:
        failures.append(tag)

    # Gate 2: Leverage-Once
    ok, tag = gate_leverage_once(signal['return_computation'])
    if not ok:
        failures.append(tag)

    # Gate 3: Div-Zero (fires at computation time, but verify no NaN/Inf leaked through)
    ok, tag = gate_no_nan_inf(signal)
    if not ok:
        failures.append(tag)

    # Gate 4: Data Completeness
    ok, tag = gate_data_completeness(signal, REQUIRED_FIELDS)
    if not ok:
        failures.append(tag)

    # Gate 5: Regime Coherence
    ok, tag = gate_regime_coherence(signal['regime'], signal['drought'])
    if not ok:
        failures.append(tag)

    # Gate 6: Confidence Bounds
    ok, tag = gate_confidence_bounds(signal['confidence'], signal['score'])
    if not ok:
        failures.append(tag)

    # Gate 7: OHLC Integrity
    ok, tag = gate_ohlc_integrity(
        signal['open'], signal['high'], signal['low'], signal['close'], signal['volume']
    )
    if not ok:
        failures.append(tag)

    # Gate 8: Temporal Consistency
    ok, tag = gate_temporal_consistency(
        signal['timestamps'], datetime.utcnow(), signal['ttl']
    )
    if not ok:
        failures.append(tag)

    passed = len(failures) == 0
    return passed, failures
```

---

## Failure Modes Table

| Gate | Failure Code | What Happens | How to Investigate | Override Procedure |
|---|---|---|---|---|
| 1 - Return Magnitude | `SANITY_FAIL_MAGNITUDE` | Signal blocked. Tagged with session type and actual return value. | Check `logs/blocked_signals/` for the raw return. Inspect whether the underlying price data is correct (yfinance stale cache?). Verify leverage was applied only once (cross-ref with Gate 2). | Set `OVERRIDE_MAGNITUDE_THRESHOLD` in `config/settings.yaml` to a higher value. Requires restart. Log override reason. |
| 2 - Leverage-Once | `SANITY_FAIL_DOUBLE_LEVERAGE` | Signal blocked. Both application sites logged. | Read the `computation_path` in the blocked signal log. It shows the first function that applied leverage and the second function that tried. Fix the duplicate application at source. | No runtime override. This is a code bug. Fix the code. |
| 3 - Division-by-Zero | `SANITY_FAIL_DIV_ZERO` | Computation aborted at the division site. Downstream values are not computed. | Check the logged context string for which variable was zero and where. Inspect upstream data feed for the ticker. | No runtime override. The denominator data must be fixed at source. If a specific field is legitimately zero (e.g., a halted stock with zero volume), add an explicit code path to handle that case. |
| 4 - Data Completeness | `SANITY_FAIL_DATA_INCOMPLETE` | Signal blocked. Missing/stale fields listed in tag. | Check yfinance connectivity. Check if the ticker is delisted or halted. Inspect `logs/data_feed/` for fetch errors. | Set `OVERRIDE_COMPLETENESS_THRESHOLD` in `config/settings.yaml` to a lower value (minimum 0.50). Below 0.50 always triggers SYSTEM_DOWN regardless of override. |
| 4 - Data Completeness | `SANITY_FAIL_SYSTEM_DOWN` | ALL signals suppressed system-wide. CRITICAL alert. | Immediate investigation required. Likely cause: API key expired, network partition, or exchange holiday not in calendar. | Manually set `FORCE_SYSTEM_UP=true` in `config/settings.yaml`. This bypasses SYSTEM_DOWN but individual signals still go through all other gates. Log the override with justification. |
| 5 - Regime Coherence | `SANITY_FAIL_REGIME_DROUGHT_CONTRADICTION` | ALL signals suppressed until contradiction resolves. | Check `volatility_regime.py` output and drought detection logic. Likely cause: regime classifier and drought detector use different lookback windows or data sources. | Wait for automatic resolution (next scan cycle). If stuck, manually set `regime` via `config/settings.yaml` override. Log the manual regime with justification. |
| 6 - Confidence Bounds | `SANITY_FAIL_CONFIDENCE_OOB` / `SANITY_FAIL_SCORE_OOB` / `SANITY_FAIL_SCORE_ZERO` | Signal blocked. Actual value logged. | Inspect the scoring pipeline in `predictive_scoring.py`. A score of 0 usually means the scoring function received insufficient data. An out-of-bounds value means a normalisation step is broken. | No runtime override for out-of-bounds. This is a code bug. For score=0, investigate why the scorer produced zero and fix the data or logic. |
| 7 - OHLC Integrity | `SANITY_FAIL_OHLC` | Entire bar rejected. Not used in any calculation. | Check the raw yfinance response for the ticker and date. LSE `.L` tickers occasionally have auction-related anomalies. If the bar is genuinely malformed upstream, there is nothing to fix locally. | No runtime override. Bad bars are always rejected. If a ticker consistently produces bad bars, add it to the exclusion list in `config/settings.yaml`. |
| 8 - Temporal Consistency | `SANITY_FAIL_TEMPORAL` | Signal blocked (non-monotonic, future) or data rejected (stale). | Check system clock (`timedatectl`). Check if yfinance is returning cached/stale data. Check if the scan scheduler is running on time. | Adjust TTL values in `config/settings.yaml` if the defaults are too aggressive for a specific data type. `max_clock_skew` can be increased if the EC2 instance clock drifts. |

---

## Acceptance Tests

### Gate 1: Return Magnitude

| # | Input | Expected Result |
|---|---|---|
| 1.1 | `return_pct=15.0, session_type='intraday'` | PASS |
| 1.2 | `return_pct=30.0, session_type='intraday'` | PASS (exactly at threshold, exclusive `>`) |
| 1.3 | `return_pct=30.01, session_type='intraday'` | BLOCK `SANITY_FAIL_MAGNITUDE` |
| 1.4 | `return_pct=-30.01, session_type='intraday'` | BLOCK `SANITY_FAIL_MAGNITUDE` |
| 1.5 | `return_pct=-30.0, session_type='intraday'` | PASS |
| 1.6 | `return_pct=5.0, session_type='overnight'` | PASS |
| 1.7 | `return_pct=8.0, session_type='overnight'` | PASS (exactly at threshold) |
| 1.8 | `return_pct=8.01, session_type='overnight'` | BLOCK `SANITY_FAIL_MAGNITUDE` |
| 1.9 | `return_pct=-8.01, session_type='overnight'` | BLOCK `SANITY_FAIL_MAGNITUDE` |
| 1.10 | `return_pct=0.0, session_type='intraday'` | PASS |
| 1.11 | `return_pct=120.0, session_type='intraday'` | BLOCK (likely double-leverage artifact) |

### Gate 2: Leverage-Once Assertion

| # | Input | Expected Result |
|---|---|---|
| 2.1 | Single `apply_leverage()` call | PASS, returns leveraged value |
| 2.2 | Two `apply_leverage()` calls on same `ReturnComputation` | BLOCK `SANITY_FAIL_DOUBLE_LEVERAGE` on second call |
| 2.3 | `leverage_factor=1` (unleveraged ETF), single call | PASS |
| 2.4 | `leverage_factor=3`, single call, result checked against Gate 1 | PASS if within magnitude bounds |
| 2.5 | Fresh `ReturnComputation` instance (flag reset) | PASS (new instance, clean flag) |

### Gate 3: Division-by-Zero Guards

| # | Input | Expected Result |
|---|---|---|
| 3.1 | `avg_vol_20 = 1500000` | PASS, division proceeds normally |
| 3.2 | `avg_vol_20 = 0` | BLOCK `SANITY_FAIL_DIV_ZERO` |
| 3.3 | `avg_vol_20 = 1e-11` (below 1e-10 threshold) | BLOCK `SANITY_FAIL_DIV_ZERO` |
| 3.4 | `avg_vol_20 = 1e-10` (exactly at threshold) | BLOCK (threshold is `< 1e-10`, so `abs(1e-10) < 1e-10` is false) -- PASS |
| 3.5 | `max_pts = 0` in momentum PDF | BLOCK `SANITY_FAIL_DIV_ZERO` |
| 3.6 | `vix_ann_vol = -0.0` (negative zero) | BLOCK `SANITY_FAIL_DIV_ZERO` (abs(-0.0) = 0.0 < 1e-10) |
| 3.7 | `fwd_returns = []` (empty array, implicit zero length) | BLOCK (array length zero triggers guard before division) |
| 3.8 | Previous close = 0 in `lse_registry.py` price change calc | BLOCK `SANITY_FAIL_DIV_ZERO` |

### Gate 4: Data Completeness

| # | Input (of 16 required fields) | Expected Result |
|---|---|---|
| 4.1 | 16/16 present and fresh | PASS |
| 4.2 | 13/16 present (80%) | PASS (exactly at threshold) |
| 4.3 | 12/16 present (75%) | BLOCK `SANITY_FAIL_DATA_INCOMPLETE` |
| 4.4 | 8/16 present (50%) | BLOCK `SANITY_FAIL_DATA_INCOMPLETE` |
| 4.5 | 7/16 present (43.75%) | BLOCK `SANITY_FAIL_SYSTEM_DOWN` |
| 4.6 | 0/16 present | BLOCK `SANITY_FAIL_SYSTEM_DOWN` |
| 4.7 | 16/16 present but 4 are stale | Effectively 12/16, BLOCK `SANITY_FAIL_DATA_INCOMPLETE` |
| 4.8 | All fields present, all stale | Effectively 0/16, BLOCK `SANITY_FAIL_SYSTEM_DOWN` |

### Gate 5: Regime Coherence

| # | Input | Expected Result |
|---|---|---|
| 5.1 | `regime='EXPANSION', drought=False` | PASS |
| 5.2 | `regime='EXPANSION', drought=True` | BLOCK `SANITY_FAIL_REGIME_DROUGHT_CONTRADICTION` |
| 5.3 | `regime='CONTRACTION', drought=True` | PASS (contraction + drought is coherent) |
| 5.4 | `regime='CONTRACTION', drought=False` | PASS |
| 5.5 | `regime='NEUTRAL', drought=True` | PASS |
| 5.6 | `regime='NEUTRAL', drought=False` | PASS |

### Gate 6: Confidence Bounds

| # | Input | Expected Result |
|---|---|---|
| 6.1 | `confidence=50, score=75` | PASS |
| 6.2 | `confidence=0, score=50` | PASS (zero confidence is valid) |
| 6.3 | `confidence=100, score=100` | PASS |
| 6.4 | `confidence=100.0001, score=50` | BLOCK `SANITY_FAIL_CONFIDENCE_OOB` |
| 6.5 | `confidence=-0.001, score=50` | BLOCK `SANITY_FAIL_CONFIDENCE_OOB` |
| 6.6 | `confidence=50, score=0` | BLOCK `SANITY_FAIL_SCORE_ZERO` |
| 6.7 | `confidence=50, score=-1` | BLOCK `SANITY_FAIL_SCORE_OOB` |
| 6.8 | `confidence=50, score=101` | BLOCK `SANITY_FAIL_SCORE_OOB` |
| 6.9 | `confidence=0, score=0` | BLOCK `SANITY_FAIL_SCORE_ZERO` (score=0 blocks even with valid confidence) |
| 6.10 | `confidence=NaN, score=50` | BLOCK (NaN is not in [0, 100]) |

### Gate 7: OHLC Integrity

| # | Input (O, H, L, C, V) | Expected Result |
|---|---|---|
| 7.1 | `100, 105, 98, 103, 1000000` | PASS |
| 7.2 | `100, 95, 98, 103, 1000000` | BLOCK `HIGH_LT_LOW` (high 95 < low 98) |
| 7.3 | `100, 105, 98, 103, -1` | BLOCK `NEGATIVE_VOLUME` |
| 7.4 | `-1, 105, 98, 103, 1000000` | BLOCK `NEGATIVE_PRICE` |
| 7.5 | `110, 105, 98, 103, 1000000` | BLOCK `OPEN_OOB` (open 110 > high 105) |
| 7.6 | `100, 105, 98, 106, 1000000` | BLOCK `CLOSE_OOB` (close 106 > high 105) |
| 7.7 | `98, 105, 98, 105, 1000000` | PASS (open=low, close=high are valid) |
| 7.8 | `100, 100, 100, 100, 0` | PASS (flat bar with zero volume is valid) |
| 7.9 | `97, 105, 98, 103, 1000000` for `.L` ticker | PASS if within 0.1% tolerance |
| 7.10 | Multiple violations in same bar | BLOCK with all violation tags joined (e.g., `HIGH_LT_LOW\|NEGATIVE_VOLUME`) |

### Gate 8: Temporal Consistency

| # | Input | Expected Result |
|---|---|---|
| 8.1 | Timestamps `[T1, T2, T3]` where T1 < T2 < T3, all within TTL | PASS |
| 8.2 | Timestamps `[T1, T3, T2]` (non-monotonic) | BLOCK `NON_MONOTONIC` |
| 8.3 | Timestamps `[T1, T1, T2]` (duplicate, non-strictly-increasing) | BLOCK `NON_MONOTONIC` (uses `<=`, not `<`) |
| 8.4 | Latest timestamp = now + 30s | PASS (within 60s clock skew allowance) |
| 8.5 | Latest timestamp = now + 61s | BLOCK `FUTURE_TIMESTAMP` |
| 8.6 | Latest timestamp = now - 49h (daily bar, TTL=48h) | BLOCK `STALE_DATA` |
| 8.7 | Latest timestamp = now - 47h (daily bar, TTL=48h) | PASS |
| 8.8 | Empty timestamp list | PASS (no bars to validate, Gate 4 catches missing data) |

---

## Proof Artifacts

### Log Entries

Every gate evaluation produces a structured log entry in `logs/sanity_gate.log`:

**Format (PASS):**

```
2025-06-15T09:30:01Z | SANITY_PASS | ticker=QQQ3.L | signal_id=a1b2c3 | gates_evaluated=8 | verdict=PASS
```

**Format (BLOCK):**

```
2025-06-15T09:30:01Z | SANITY_BLOCK | ticker=QQQ3.L | signal_id=a1b2c3 | gates_evaluated=8 | verdict=BLOCK | failures=SANITY_FAIL_MAGNITUDE_INTRADAY_+45.23%,SANITY_FAIL_DOUBLE_LEVERAGE
```

### Blocked Signal Archive

Every blocked signal is written as a JSON file to `logs/blocked_signals/`:

**Filename pattern:** `{timestamp}_{ticker}_{signal_id}.json`

**Contents:**

```json
{
  "signal_id": "a1b2c3",
  "ticker": "QQQ3.L",
  "timestamp": "2025-06-15T09:30:01Z",
  "verdict": "BLOCK",
  "failures": [
    {
      "gate": "GATE_1_RETURN_MAGNITUDE",
      "code": "SANITY_FAIL_MAGNITUDE_INTRADAY_+45.23%",
      "details": {
        "return_pct": 45.23,
        "session_type": "intraday",
        "threshold": 30.0
      }
    },
    {
      "gate": "GATE_2_LEVERAGE_ONCE",
      "code": "SANITY_FAIL_DOUBLE_LEVERAGE",
      "details": {
        "first_application": "predictive_scoring.py:compute_forward_return",
        "second_application": "pdf_v2_momentum.py:format_return_column"
      }
    }
  ],
  "signal_data": {
    "open": 150.20,
    "high": 155.80,
    "low": 149.10,
    "close": 154.30,
    "volume": 2340000,
    "return_pct": 45.23,
    "confidence": 72,
    "score": 65,
    "regime": "EXPANSION",
    "drought": false
  }
}
```

### Audit Trail

A daily summary is written to `logs/sanity_audit/{date}.json`:

```json
{
  "date": "2025-06-15",
  "total_signals_evaluated": 216,
  "passed": 198,
  "blocked": 18,
  "system_down_events": 0,
  "regime_contradiction_events": 0,
  "failures_by_gate": {
    "GATE_1": 4,
    "GATE_2": 2,
    "GATE_3": 0,
    "GATE_4": 6,
    "GATE_5": 0,
    "GATE_6": 3,
    "GATE_7": 2,
    "GATE_8": 1
  },
  "tickers_most_blocked": ["3LUS.L", "NVD3.L"],
  "overrides_applied": []
}
```

### Override Log

Any manual override is recorded in `logs/sanity_overrides.log`:

```
2025-06-15T10:00:00Z | OVERRIDE | gate=GATE_1 | param=OVERRIDE_MAGNITUDE_THRESHOLD | old=30.0 | new=40.0 | reason="NVD3.L post-earnings move, verified legitimate 35% session return" | operator=rr
```

Overrides without a logged reason are a spec violation.

---

## Integration Points

The sanity gate layer integrates at a single chokepoint:

```
[Computation Pipeline] --> [Sanity Gates] --> [Delivery Pipeline]
                                |
                                v
                        [Blocked Signal Archive]
```

- `main.py`: Call `run_all_gates(signal)` after signal computation, before any call to `pdf_v2_momentum.py` or `pdf_v2_risk.py` delivery functions.
- `strategies/daily_target.py`: Call `run_all_gates(signal)` before the S15 signal is emitted.
- Gate 3 (div-zero) additionally fires inline at the five specified code locations during computation.

No signal bypasses this chokepoint. If a new delivery channel is added, it must consume signals from the post-gate output, never from pre-gate computation.
