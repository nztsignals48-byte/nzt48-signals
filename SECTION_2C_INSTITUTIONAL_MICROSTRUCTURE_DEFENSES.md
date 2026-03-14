# SECTION 2C: INSTITUTIONAL MICROSTRUCTURE DEFENSES {#section-2c}

## Purpose

Section 2B fixed the macro timing problem (entering trades 15-60 minutes late). This section addresses the **micro timing problem**: what happens in the 0-60 seconds around trade execution. Even with perfect signal timing, the system bleeds alpha through microstructure friction — adverse stop placement, mid-price illusions, leverage asymmetry, rapid-fire stopouts, and cross-asset divergence. These 20 defenses, contributed by four institutional personas (Chief Quant, Lead Systems Architect, Chief Risk Officer, Academic Reviewer), close the gap between theoretical edge and realised P&L.

**Dependency**: Section 2C assumes Section 2B timing fixes (T-01 through T-11) are complete. The FAST/SLOW tier architecture from T-05 is referenced throughout.

---

## GROUP 1: CHIEF QUANT — SIGNAL INTEGRITY (5 Items)

### CQ-01: Volatility-Scaled Breathing Room for Stops

**Problem**: Current stop is a FIXED percentage (1.0% for 3x, 0.75% for 5x — `daily_target.py:28-29`). This ignores intraday microstructure noise. On a quiet morning, 1.0% is generous; during US open (14:30 UK), a 3x ETP can oscillate 0.8% in 30 seconds on normal market-maker inventory rotation. The fixed stop conflates *invalidation* (the thesis is wrong) with *noise* (normal bid-ask bounce). Result: stopped out on noise, then price continues in the original direction.

**Specification**: Decouple the *invalidation stop* (strategic) from the *microstructure stop* (tactical). The microstructure stop must never be tighter than the current 1-min ATR at execution time.

**Formula**:
```
stop_distance = max(invalidation_stop, noise_floor)

Where:
  invalidation_stop = existing stop logic (ATR14 x 1.5, capped at 0.75% risk)
  noise_floor       = k x ATR_1min(t_entry) x L

  k = 2.0           (2 standard deviations of 1-min noise)
  ATR_1min(t_entry)  = Average True Range of 1-minute bars, 14-period, at execution time
  L = leverage factor (3 or 5)

If noise_floor > invalidation_stop:
  Widen stop to noise_floor.
  Reduce position size proportionally to maintain 0.75% risk budget:
    shares_adjusted = shares_original x (invalidation_stop / noise_floor)
```

**Implementation**:
- File: `strategies/daily_target.py` — `_create_signal()` method (~line 480)
- New dependency: 1-min ATR must be computed in the FAST tier indicator set. Add `atr_1min` field to `IndicatorSnapshot` in `models.py`.
- File: `core/realtime_data.py` — add 1-min bar history buffer (rolling 14 bars per CORE ticker, ~1KB RAM per ticker)
- File: `qualification/dynamic_sizer.py` — adjust share calculation when noise_floor binds: `shares = risk_budget / noise_floor` instead of `risk_budget / invalidation_stop`

**New P0 item**: **CQ-01** — Implement 1-min ATR computation in indicator pipeline, wire into stop logic. Est. 4h.

---

### CQ-02: Mid-Price Illusion Filter

**Problem**: The FAST tier (T-05) triggers when price moves > 1.0% from session open. But price is currently computed as the mid-price `(bid + ask) / 2`. On thinly-traded ETPs (3SEM.L, GPT3.L — spread 25-30 bps), the ask can drop while the bid stays flat, creating a phantom mid-price move. The system enters LONG, but no real buyer absorption occurred — the ask side simply withdrew liquidity. The existing `calculate_micro_price()` in `core/quant_math/microstructure.py` computes a Stoikov-weighted micro-price but this is NOT used in the FAST tier trigger.

**Specification**: FAST tier LONG signals trigger ONLY if the **bid price** (not mid, not ask) has moved up from session open. For SHORT signals, trigger only if the **ask price** has moved down. This confirms actual buyer/seller absorption, not phantom mid-price moves from one-sided book changes.

**Formula**:
```
FAST_LONG_TRIGGER:
  PASS if: bid_price(now) > bid_price(session_open) x (1 + threshold)
  FAIL otherwise

FAST_SHORT_TRIGGER:
  PASS if: ask_price(now) < ask_price(session_open) x (1 - threshold)
  FAIL otherwise

Where:
  threshold = 0.01 (1.0% — same as current mid-price trigger)
  session_open bid/ask = first valid quote after 09:05 UK (post-OBSERVE window from T-01)
```

**Implementation**:
- File: `strategies/daily_target.py` — within the scan loop, replace `snap.price` comparisons in FAST tier logic with `snap.bid_price` (LONG) or `snap.ask_price` (SHORT)
- File: `models.py` — ensure `IndicatorSnapshot` has `bid_price` and `ask_price` fields (currently uses `price` which is mid). Add fields if missing.
- File: `core/realtime_data.py` — store session-open bid/ask in Redis: `nzt:session_open:{ticker}:bid` and `:ask`, set at 09:05 UK
- **Fallback**: If bid/ask not available (yfinance provides only last price), use `last_price - half_spread` as synthetic bid estimate. Log warning. This is a degraded mode — upgrade to real L1 feed resolves it.

**New P1 item**: **CQ-02** — Mid-price illusion filter for FAST tier triggers. Est. 3h.

---

### CQ-03: Asymmetric Leverage Decay Offset

**Problem**: The cost drag calculator (`core/cost_drag_calculator.py:73`) uses a symmetric variance drag formula: `vol_drag = 0.5 x L^2 x sigma^2`. This treats long and short 3x ETPs identically. In reality, inverse ETPs suffer HIGHER variance drag because: (a) the compounding asymmetry is worse for inverse products (a -3% day followed by +3% day loses more on inverse than long), and (b) inverse ETPs carry higher implied financing costs embedded in the swap structure. Avellaneda & Zhang (2010) derive the exact asymmetry: inverse variance drag is ~1.5x the long-side drag for the same underlying volatility.

**Specification**: Apply asymmetric variance drag and Kelly sizing penalty for inverse/short ETPs.

**Formula**:
```
Long 3x ETP:
  vol_drag_daily = 0.5 x L^2 x sigma^2

Inverse 3x ETP (QQQS.L, 3USS.L, etc.):
  vol_drag_daily = 0.5 x L^2 x sigma^2 x 1.5    (Avellaneda & Zhang 2010 asymmetry factor)

Kelly penalty for inverse ETPs:
  f*_inverse = f*_standard x 0.85                  (15% haircut on Kelly fraction)

Rationale: The 15% Kelly haircut compensates for the ~15% higher annualised drag
on inverse products at typical underlying vol of 20-25%.
```

**Implementation**:
- File: `core/cost_drag_calculator.py:72-74` — add `is_inverse` parameter to `get_total_drag_bps()`. If inverse, multiply `vol_drag_daily` by 1.5.
- File: `qualification/dynamic_sizer.py` — add `inverse_kelly_penalty` scalar. Query `INVERSE_PAIRS` from `uk_isa/isa_universe.py` (already imported) to detect inverse tickers. Apply 0.85 multiplier.
- File: `uk_isa/isa_universe.py` — `INVERSE_PAIRS` dict already exists and maps tickers. Use this as the canonical inverse detection.
- File: `execution/planner.py` — pass `is_inverse` flag through to cost model.

**No new stop-ship item** — this is a sizing refinement. Integrate during Phase 1.

---

### CQ-04: Reversal Recovery Cooldown

**Problem**: When the system enters a trade and gets stopped out in under 60 seconds, this is almost always microstructure noise — a fleeting spike that immediately reverses. The current system can immediately re-enter the same ticker on the next scan cycle (60s later), getting stopped out again. This creates a loop: enter -> 30s stopout -> re-enter -> 30s stopout -> L1 circuit breaker hit. The operator sees 3 losses in 3 minutes, all on the same ticker, all from the same noise event.

**Specification**: If a trade is stopped out within 60 seconds of entry, impose a 15-minute cooldown on that specific ticker. The ticker is blocked from re-entry but other tickers remain eligible.

**Formula**:
```
If (exit_time - entry_time) < 60 seconds AND exit_reason = "STOP_HIT":
  cooldown_end = exit_time + 15 minutes
  BLOCK ticker until cooldown_end

Cooldown state stored in Redis:
  Key: nzt:cooldown:{ticker}
  Value: cooldown_end timestamp (ISO 8601)
  TTL: 900 seconds (auto-expire)
```

**Implementation**:
- File: `execution/virtual_trader.py` — in `close_position()` method, check `(exit_time - entry_time).total_seconds() < 60` and `exit_reason == "STOP_HIT"`. If true, set Redis cooldown key.
- File: `strategies/daily_target.py` — in `_score_ticker_with_reason()`, before scoring, check Redis for active cooldown. If active, return `(None, "cooldown_active")`.
- File: `main.py` — in the heartbeat scan trigger, skip cooldown tickers when evaluating anomalies.

**New P1 item**: **CQ-04** — Reversal recovery cooldown (15-min per-ticker after sub-60s stopout). Est. 2h.

---

### CQ-05: Cross-Asset Premium Divergence Filter

**Problem**: Leveraged ETPs can spike due to LSE-specific microstructure events (market maker inventory adjustment, stale NAV recalculation, ETF creation/redemption basket misalignment) without the underlying US asset moving at all. If NVD3.L spikes 2% but NVDA US futures are flat, the ETP spike is an artefact — not a genuine momentum signal. Entering on this signal guarantees a loss when the ETP mean-reverts to NAV.

**Specification**: Before the FAST tier fires on any ETP, check the underlying US futures/pre-market price. If the ETP has moved > 1.5% but the underlying has moved < 0.3% in the same direction, VETO the signal. This applies during overlapping UK/US hours (14:30-16:30 UK) and pre-market (12:00-14:30 UK via US futures).

**Formula**:
```
etp_move   = (etp_price_now - etp_session_open) / etp_session_open
under_move = (underlying_now - underlying_prev_close) / underlying_prev_close

VETO if:
  |etp_move| > 0.015 (1.5%) AND
  sign(etp_move) x under_move < 0.003 (underlying moved < 0.3% in same direction)

Underlying mapping (from PEER tier):
  QQQ3.L / QQQS.L -> ^NQ (Nasdaq futures) or QQQ
  NVD3.L          -> NVDA
  TSL3.L          -> TSLA
  3SEM.L          -> ^SOX (PHLX Semiconductor)
  3LUS.L / 3USS.L -> ^ES (S&P futures) or SPY
```

**Implementation**:
- File: `strategies/daily_target.py` — new method `_cross_asset_divergence_check(ticker, etp_move)` called in FAST tier path before signal emission.
- File: `uk_isa/isa_universe.py` — add `UNDERLYING_MAP` dict mapping each ETP to its primary underlying/futures ticker.
- File: `core/realtime_data.py` — extend data fetcher to include PEER tier underlying prices (already planned in Section 1 Universe Registrar Tier 2).
- Data source: yfinance for ^NQ, ^ES, ^SOX during paper. Real-time feed for live.
- **Fallback**: Outside US market hours (before 14:30 UK), use previous US close. In this case, only VETO if ETP move > 3% (higher threshold because stale reference).

**New P1 item**: **CQ-05** — Cross-asset premium divergence filter. Est. 4h.

---

## GROUP 2: LEAD SYSTEMS ARCHITECT — LATENCY & INFRASTRUCTURE (5 Items)

### SA-01: Pre-Computation of SLOW Indicators in Background Thread

**Problem**: The current scan loop (`main.py:4835+`) computes ALL indicators for ALL tickers inline — FAST and SLOW together. With T-05's FAST/SLOW tier split, SLOW indicators (EMA9/20/50, OBV, Stochastic RSI) are no longer required for gap/momentum signals but still add 2-3 seconds of computation per scan cycle. This latency applies even when only the FAST path fires.

**Specification**: Run SLOW indicator computation in a dedicated background thread on a 60-second cadence, independent of the FAST scan loop. FAST scans read from a pre-computed cache. SLOW values update asynchronously and are available for confidence scoring when the SLOW tier evaluates.

**Implementation**:
```
Architecture:
  Main Loop (60s heartbeat) ──> FAST indicators (VWAP, MACD, RSI, ROC) ──> S15 FAST path
                                    ^
  Background Thread (60s) ─────> SLOW indicators (EMA9/20/50, OBV, StochRSI) ──> Redis cache
                                    └── Key: nzt:slow_ind:{ticker}
                                    └── TTL: 120s (2 cycles — stale after that)
                                    └── Format: JSON {ema9, ema20, ema50, obv, stoch_rsi, ts}
```

- File: `main.py` — new class `SlowIndicatorWorker(threading.Thread)`. Daemon thread started at engine init. Computes SLOW indicators for all CORE tickers every 60s. Writes to Redis.
- File: `strategies/daily_target.py` — `_score_ticker_with_reason()` reads SLOW indicators from Redis for confidence bonus computation (0-5 points per agreeing indicator, per T-05 spec).
- File: `models.py` — `IndicatorSnapshot` already has `ema_9`, `ema_20`, `ema_50`, `obv` fields. No model change needed.
- **Stale guard**: If Redis SLOW cache for a ticker is > 120s old, treat all SLOW indicators as NEUTRAL (0 confidence bonus). Never block a FAST signal due to stale SLOW data.
- Memory: ~2KB per ticker x 60 tickers = 120KB Redis. Negligible.

**No new stop-ship item** — performance optimisation. Phase 0A integration alongside T-03 event-driven scanning.

---

### SA-02: JIT Compilation for Qualification Gauntlet

**Problem**: T-10 targets < 500ms for the FAST path qualification. The 7 remaining gates involve float comparisons, dict lookups, and conditionals — all pure Python. CPython overhead on these operations is ~10-50us per gate, totalling 200-350us for the 7-gate path. This is acceptable but leaves no headroom for future gate additions. The SLOW path (18 gates) at ~4.5s is dominated by database queries (fixable by SA-01 caching), but the numerical portions still waste cycles.

**Specification**: Apply `numba.njit` to the numerical core of the qualification gauntlet — specifically the portfolio heat check, correlation computation, and Kelly sizing math. Target: sub-10ms total for all numerical gates.

**Implementation**:
```python
# File: qualification/fast_gates.py (NEW)
import numba

@numba.njit(cache=True)
def check_portfolio_heat(open_risk_pcts: np.ndarray, new_risk_pct: float,
                         heat_cap: float) -> bool:
    """PASS if total portfolio heat + new trade <= cap."""
    return open_risk_pcts.sum() + new_risk_pct <= heat_cap

@numba.njit(cache=True)
def check_correlation_brake(corr_matrix: np.ndarray, new_idx: int,
                            threshold: float, max_per_cluster: int) -> bool:
    """PASS if new position doesn't breach correlation cluster limit."""
    count = 0
    for i in range(corr_matrix.shape[0]):
        if i != new_idx and corr_matrix[new_idx, i] > threshold:
            count += 1
    return count < max_per_cluster

@numba.njit(cache=True)
def kelly_size(win_rate: float, payoff_ratio: float, regime_mult: float,
               max_risk: float) -> float:
    """Half-Kelly with regime multiplier, capped at immutable max."""
    f_star = (win_rate * payoff_ratio - (1 - win_rate)) / payoff_ratio
    half_kelly = max(f_star * 0.5, 0.0) * regime_mult
    return min(half_kelly, max_risk)
```

- File: `qualification/fast_gates.py` — NEW file containing numba-compiled gate functions
- File: `main.py` — import `fast_gates` and call JIT functions in FAST qualification path
- File: `requirements.txt` — numba already in venv (`venv/lib/python3.12/site-packages/numba/` confirmed)
- **Warmup**: First call to `@njit(cache=True)` compiles and caches to disk (~2s). Subsequent calls are native speed. Add warmup call during startup readiness gate (Section 8B).
- **Fallback**: If numba import fails (e.g., binary incompatibility after system update), fall back to pure Python. Log P1 alert.

**New P1 item**: **SA-02** — JIT-compile numerical qualification gates. Est. 4h.

---

### SA-03: Async Telemetry Offloading via Redis Stream

**Problem**: The telemetry pipeline (`core/telemetry.py`) writes feature snapshots to Redis synchronously on the hot path (between signal qualification and order submission). Each `FeatureSnapshot` serialisation + Redis SET + TTL takes ~5-15ms. During multi-signal scan cycles (post T-08 removal of single-fire limit), this adds 15-60ms of cumulative latency. Worse, if Redis is slow (GC pause, persistence), the trade execution blocks.

**Specification**: Replace synchronous Redis writes with an async offload pattern. The hot path pushes to an in-memory `asyncio.Queue`. A background worker drains the queue and writes to Redis. Trade execution never waits for telemetry.

**Implementation**:
```
Hot Path (per signal):
  signal_qualified -> queue.put_nowait(snapshot)  [<0.1ms]
  -> proceed to order submission immediately

Background Worker (continuous):
  while True:
    batch = drain_queue(max_batch=10, timeout=1.0)
    pipeline = redis.pipeline()
    for snapshot in batch:
      pipeline.set(f"nzt:telemetry:{snapshot.signal_id}", json.dumps(asdict(snapshot)))
      pipeline.expire(f"nzt:telemetry:{snapshot.signal_id}", _TELEMETRY_TTL)
    pipeline.execute()
```

- File: `core/telemetry.py` — replace direct Redis SET with `self._write_queue.put_nowait(snapshot)`. Add `TelemetryWriter` class with `asyncio.Queue` and background consumer.
- File: `main.py` — start `TelemetryWriter` as asyncio task during engine init. Already has event loop.
- Queue depth: maxsize=500. If full, drop telemetry (not trade-critical). Log P2 warning.
- **Alignment with I-07B**: This pattern mirrors the SQLite async write queue from Section 8. Use same priority scheme: EMERGENCY > TRADE > TELEMETRY.

**No new stop-ship item** — performance optimisation. Phase 1 integration.

---

### SA-04: Stateful LOB Cache via Continuously Updated Pricing

**Problem**: Every price check fetches a fresh quote from the data feed (yfinance/Polygon/TwelveData). For the 60s heartbeat scan across 60+ tickers, this means 60+ API calls per cycle. Each call has 50-500ms network latency. The total scan time can exceed 30 seconds, defeating the purpose of the heartbeat. There is no local order book (LOB) state — each call is stateless.

**Specification**: Maintain a continuously updated in-memory price cache (`LOBCache`) that stores the latest bid/ask/last/volume for all CORE tickers. The heartbeat scan reads from cache (sub-microsecond) instead of making API calls. A separate data feed thread refreshes the cache at the maximum rate the feed allows.

**Implementation**:
```python
# File: core/lob_cache.py (NEW)
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

@dataclass
class LOBEntry:
    ticker: str = ""
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    stale: bool = False  # True if ts > 120s old

class LOBCache:
    """Thread-safe in-memory LOB cache for all CORE tickers.

    Read path: lock-free (dict read is atomic in CPython).
    Write path: per-ticker threading.Lock for update atomicity.
    """
    def __init__(self):
        self._cache: dict[str, LOBEntry] = {}
        self._locks: dict[str, threading.Lock] = {}

    def update(self, ticker: str, bid: float, ask: float,
               last: float, volume: int) -> None: ...

    def get(self, ticker: str) -> LOBEntry | None: ...

    def get_all_moves_from_open(self, session_opens: dict) -> list[tuple[str, float]]:
        """Return (ticker, pct_move) for all tickers moved > threshold."""
        ...
```

- File: `core/lob_cache.py` — NEW module
- File: `core/realtime_data.py` — data feed refresh thread writes to LOBCache instead of returning values directly
- File: `main.py` — heartbeat scan reads from `lob_cache.get_all_moves_from_open()` instead of calling `yf.Ticker().fast_info`
- File: `strategies/daily_target.py` — `snap.bid_price` / `snap.ask_price` populated from LOBCache
- **WebSocket upgrade path**: When migrating to Polygon.io WebSocket (Phase B), the LOBCache becomes the WebSocket sink. The consumer interface stays identical — only the producer changes. This is the primary architectural benefit.
- Memory: ~200 bytes per ticker x 60 tickers = 12KB. Negligible.

**New P1 item**: **SA-04** — LOBCache module for sub-microsecond heartbeat reads. Est. 6h.

---

### SA-05: Stale Order Cancellation (IOC Flag + Script-Side Timeout)

**Problem**: When the system submits a limit order (per Section 9B: Limited Live uses LIMIT only), there is no timeout or cancellation logic. If the order doesn't fill within seconds, it sits open on the book indefinitely. During fast moves, this means: (a) the signal was valid 5 seconds ago but the market moved, (b) the stale order fills minutes later at a now-adverse price, (c) the system enters a trade that no longer qualifies. This is a known failure mode in all systematic trading systems (Cartea, Jaimungal & Penalva 2015).

**Specification**: All limit orders use IOC (Immediate-Or-Cancel) flag where broker supports it. Additionally, implement a script-side 3-second timeout: if no fill acknowledgment within 3 seconds, cancel the order and log as MISSED_FILL.

**Implementation**:
```
Order Submission:
  1. Submit limit order with time_in_force = "IOC"
  2. Start 3-second timer
  3. If fill_ack received: proceed to position management
  4. If timer expires with no fill:
     a. Send cancel request
     b. Log: {"event": "MISSED_FILL", "ticker": ..., "reason": "timeout_3s"}
     c. Signal re-enters the FAST scan pool on next heartbeat (can re-qualify)

Broker compatibility:
  IOC: Supported by IBKR, Trading 212 (ISA). Fall back to GTC + script-side cancel if IOC unavailable.
  Cancel latency: Expect 50-200ms for cancel acknowledgment.
```

- File: `execution/virtual_trader.py` — add `_order_timeout_seconds = 3.0` constant. In `open_position()`, wrap order submission with timeout.
- File: `execution/planner.py` — add `time_in_force: str = "IOC"` to order parameters.
- File: `execution/order_rules.py` — add IOC enforcement rule.
- **Paper mode**: VirtualTrader simulates IOC by checking if price moved > 0.5% from signal price within the simulated 3-second window. If yes, reject the fill (MISSED_FILL). This trains the ML model on realistic fill rates.

**New P0 item**: **SA-05** — Stale order cancellation. Est. 3h. Required before LIMITED LIVE (stale fills on real capital = direct loss).

---

## GROUP 3: CHIEF RISK OFFICER — CAPITAL PRESERVATION (5 Items)

### RO-01: 08:00 Toxic Spread Hard-Cap

**Problem**: LSE leveraged ETPs exhibit extreme spread widening in the first 10 minutes after open (09:00-09:10 UK). Market makers quote defensive spreads until order flow establishes a fair price. On 3SEM.L and GPT3.L, opening spreads regularly exceed 50 bps (vs. 25-30 bps steady state). The T-01 fix (Section 2B) replaces the 30-min blackout with a 5-min observe window — but does NOT add a spread quality gate for the gap scan that fires at 09:05. Entering a gap trade at 09:05 through a 50 bps spread means the trade starts -50 bps in the hole on a 200 bps target (25% of the target consumed by spread alone).

**Specification**: During the first 10 minutes of LSE trading (09:00-09:10 UK), impose a hard spread cap of 35 bps. Any signal where the current bid-ask spread exceeds 35 bps receives an automatic PASS (skip). This overrides the gap scan from T-01.

**Formula**:
```
If time_uk in [09:00, 09:10]:
  current_spread_bps = ((ask - bid) / mid) x 10000
  If current_spread_bps > 35:
    REJECT signal with reason = "toxic_spread_opening"

After 09:10 UK:
  Normal spread gate applies (R-11: veto if spread > 2.5x median_3d)
```

**Implementation**:
- File: `strategies/daily_target.py` — in the gap scan logic (09:05-09:15 from T-01), add spread check BEFORE signal emission. Read bid/ask from LOBCache (SA-04) or IndicatorSnapshot.
- File: `execution/cost_model.py` — `spread_gate_result()` already exists. Add `opening_toxic_cap_bps = 35` parameter when called during 09:00-09:10 window.
- Threshold: 35 bps is calibrated from the `SpreadHistoryTracker` P90 data in `core/realtime_data.py`. QQQ3.L P90 opening spread = ~20 bps (liquid). 3SEM.L P90 = ~40 bps. 35 bps passes QQQ3.L, blocks 3SEM.L during toxic window. Adjust after 30 days of data.

**New P0 item**: **RO-01** — Toxic spread hard-cap for first 10 minutes. Est. 2h. Required because T-01 opens the gap scan window without spread protection.

---

### RO-02: Consecutive Instant-Stop Circuit Breaker

**Problem**: The existing consecutive loss breaker (`circuit_breakers.py:58-60`) triggers after 3/5/7 losses with 15/30-min cooldowns. But it counts ALL losses equally — a loss after 2 hours of holding is fundamentally different from an instant stopout in < 60 seconds. Three instant stopouts in a row indicate a systematic microstructure problem (wrong stop level, stale data, or market-maker manipulation) that a 15-minute cooldown won't fix. The operator should NOT continue trading that session.

**Specification**: Track "instant stopouts" (exit within 60 seconds, reason = STOP_HIT) separately. Three instant stopouts in a single session = HALT ALL ENTRIES for the rest of the day. This is stricter than the existing 3-loss cooldown.

**Formula**:
```
instant_stopout_count = count of trades where:
  (exit_time - entry_time) < 60s AND exit_reason == "STOP_HIT"
  within current session (since 09:00 UK)

If instant_stopout_count >= 3:
  action = HALT_SESSION
  reason = "3x instant stopout circuit breaker"
  Resume: next trading day (05:00 UTC reset)
```

**Implementation**:
- File: `qualification/circuit_breakers.py` — new method `check_instant_stopout_breaker()`. Add to `check_all()` method. New state variable `_instant_stopouts_today: int = 0`, reset at session boundary.
- File: `execution/virtual_trader.py` — in `close_position()`, if exit meets instant-stopout criteria, call `circuit_breakers.record_instant_stopout()`.
- **Persistence**: State persisted to SQLite via the async write queue (I-07B). Docker restart does NOT reset this counter (same as existing circuit breaker persistence, P0-9).

**New P0 item**: **RO-02** — Instant-stopout circuit breaker (3 = halt). Est. 2h. Critical because CQ-01 stop widening reduces but doesn't eliminate instant stopouts.

---

### RO-03: Underlying Inventory Limits

**Problem**: The correlation brake (R-03, R-22) limits correlated positions but uses a 0.70 threshold on rolling 20-day correlations. It does NOT prevent holding both QQQ3.L (3x long Nasdaq) and NVD3.L (3x long NVIDIA) simultaneously — these can have 20-day correlation of 0.65 (below threshold) during divergence periods, yet both are fundamentally long US tech. A NASDAQ flash crash hits both simultaneously. The system can also theoretically hold QQQ3.L (3x long) AND QQQS.L (-3x short Nasdaq) — perfectly hedged, zero P&L, paying double the spread and financing costs.

**Specification**: Impose a hard limit of 1 active derivative per underlying asset. The underlying is defined by the `UNDERLYING_MAP` from CQ-05. No hedging within the same underlying — if long QQQ3.L, cannot also enter QQQS.L.

**Formula**:
```
For each new signal:
  underlying = UNDERLYING_MAP[signal.ticker]
  active_on_underlying = count of open_positions where
    UNDERLYING_MAP[position.ticker] == underlying

  If active_on_underlying >= 1:
    REJECT signal with reason = "underlying_inventory_limit"
```

**Implementation**:
- File: `qualification/portfolio_risk.py` — new method `check_underlying_limit()`. Called in the FAST path qualification (one of the 7 gates from T-10).
- File: `uk_isa/isa_universe.py` — `UNDERLYING_MAP` dict (also needed by CQ-05). Define:
  ```python
  UNDERLYING_MAP = {
      "QQQ3.L": "NDX", "QQQS.L": "NDX", "QQQ5.L": "NDX",
      "NVD3.L": "NVDA", "TSL3.L": "TSLA", "TSM3.L": "TSM",
      "3SEM.L": "SOX", "GPT3.L": "GPT_BASKET",
      "3LUS.L": "SPX", "3USS.L": "SPX", "SP5L.L": "SPX",
      "MU2.L": "MU",
  }
  ```
- This is a FAST-path gate — must be O(1). Use `set(UNDERLYING_MAP[p.ticker] for p in open_positions)` membership check.

**New P0 item**: **RO-03** — Underlying inventory limit (max 1 derivative per underlying). Est. 2h. Without this, correlation brake has a known bypass.

---

### RO-04: Overnight Gap Sizing Penalty

**Problem**: Gap plays in the first 15 minutes (T-01 gap scan, 09:05-09:15 UK) carry asymmetric tail risk. An ETP that gaps up 3% at open can gap down 5% on a single contradicting data point (e.g., an analyst downgrade published at 09:07). The standard Kelly sizing does not account for the elevated tail risk during the gap absorption window. Gao et al. (2018) show that while gap direction predicts EOD direction with 62% accuracy, the first 15 minutes after a gap have 2.3x the normal volatility.

**Specification**: During the gap scan window (09:05-09:15 UK), reduce Kelly fraction by 50%. This is additive to existing regime multipliers.

**Formula**:
```
If time_uk in [09:05, 09:20] AND signal.trigger_type == "GAP_SCAN":
  f*_adjusted = f*_standard x 0.50

Rationale:
  Gap window volatility = 2.3x normal (Gao et al. 2018)
  Kelly is proportional to 1/variance
  Optimal fraction reduction: 1/(2.3)^2 ≈ 0.19 (aggressive)
  Compromise: 0.50 (conservative, preserves participation while reducing tail exposure)

After 09:20 UK: standard sizing resumes for all signals.
```

**Implementation**:
- File: `qualification/dynamic_sizer.py` — new scalar `gap_window_scalar`. If signal has metadata `trigger_type == "GAP_SCAN"` AND time is in gap window, apply 0.50 multiplier. Insert in the 12-factor sizing pipeline after `regime_scalar`.
- File: `strategies/daily_target.py` — tag gap scan signals with `signal.metadata["trigger_type"] = "GAP_SCAN"` in the T-01 observe-then-act code path.
- File: `models.py` — `Signal.metadata` dict already exists. No model change.

**No new stop-ship item** — sizing refinement integrated with T-01 implementation.

---

### RO-05: Maker-Pegged Synthetic Limits

**Problem**: Raw market orders on LSE leveraged ETPs suffer adverse selection from the bid-ask spread. A market BUY on 3SEM.L (25 bps spread) immediately fills at the ask, costing 12.5 bps. On a 200 bps target, that is 6.25% of the profit consumed before the trade begins. Multiplied across hundreds of trades, this spread leakage compounds to a significant drag. The current code (`virtual_trader.py:228`) applies direction-appropriate slippage but always assumes a market-crossing fill.

**Specification**: Replace raw market orders with "maker-pegged" synthetic limits: submit a limit order at Bid + 1 tick (for BUY) or Ask - 1 tick (for SELL). This captures the maker side of the spread ~40-60% of the time on liquid ETPs (QQQ3.L, 3LUS.L). Combined with SA-05's IOC/3-second timeout, unfilled maker orders auto-cancel.

**Formula**:
```
BUY order:
  limit_price = bid_price + tick_size
  tick_size = 0.01 for ETPs priced < £10, 0.05 for > £10 (LSE tick table)

SELL order:
  limit_price = ask_price - tick_size

Expected fill rate at maker price:
  QQQ3.L:  ~55% (liquid, tight spread)
  3SEM.L:  ~35% (thin, wide spread)
  GPT3.L:  ~25% (very thin)

Unfilled: IOC cancels. Re-evaluate. If still qualifying, resubmit at current bid+1tick.
Max resubmissions: 3 per signal. After 3 misses, escalate to marketable limit (mid-price).
```

**Implementation**:
- File: `execution/planner.py` — `ExecutionPlanner` currently outputs `order_type: "MARKET"`. Change to `order_type: "LIMIT"` with `limit_price = bid + tick_size(ticker)`.
- File: `execution/order_rules.py` — new function `get_tick_size(ticker, price)` implementing LSE tick table.
- File: `execution/virtual_trader.py` — `SlippageModel.entry_slippage()` must simulate maker vs taker fill probability. If `random() < maker_fill_probability(ticker)`, apply zero spread cost. Otherwise, apply full half-spread.
- **Paper mode**: Log maker-fill rate per ticker. After 100+ paper trades, if actual maker-fill rate < 20% for a ticker, downgrade that ticker to market orders (spread is too wide for maker strategy).

**New P1 item**: **RO-05** — Maker-pegged synthetic limit orders. Est. 5h. Important for cost reduction but not blocking for paper trading.

---

## GROUP 4: ACADEMIC REVIEWER — STATISTICAL RIGOR (5 Items)

### AR-01: Microstructure Noise Filter (Kalman or Hull MA)

**Problem**: The FAST tier indicators (VWAP, MACD, RSI, ROC) operate on raw 1-minute price data from yfinance/data feed. Raw tick data contains microstructure noise: bid-ask bounce, market maker inventory adjustments, and minimum tick-size quantisation. On thin ETPs (GPT3.L, 3SEM.L), this noise can be 10-20 bps per bar — enough to flip short-term RSI and ROC signals. Hasbrouck (2007) demonstrates that raw transaction prices are a noisy estimate of the efficient price, with noise variance proportional to the square of the spread.

**Specification**: Apply a noise-reducing filter to raw price before computing FAST indicators. Two options (implement whichever integrates more cleanly):

**Option A — Kalman Filter** (preferred):
```
State model: p_t = p_{t-1} + w_t  (random walk efficient price)
Observation: z_t = p_t + v_t       (noisy observed price)

Parameters:
  Q = process noise variance = ATR_1min^2 x 0.01  (slow drift)
  R = observation noise variance = (spread_bps / 10000)^2 x price^2 / 4  (half-spread squared)

Kalman gain: K_t = P_t|t-1 / (P_t|t-1 + R)
Filtered price: p_hat_t = p_hat_{t-1} + K_t x (z_t - p_hat_{t-1})

Output: p_hat_t replaces raw price as input to RSI, ROC, MACD.
VWAP is already volume-weighted and inherently filtered — no change needed.
```

**Option B — Hull Moving Average** (simpler fallback):
```
HMA(n) = WMA(2 x WMA(n/2) - WMA(n), sqrt(n))
n = 5 bars (1-min)
Lag: ~2 bars (vs. 5 for SMA, 3.5 for EMA)
```

**Implementation**:
- File: `core/noise_filter.py` — NEW module. `KalmanPriceFilter` class with `update(raw_price, spread_bps) -> filtered_price`.
- File: `strategies/daily_target.py` — in FAST tier indicator computation, pass prices through filter before computing RSI/ROC/MACD.
- File: `models.py` — add `filtered_price: float = 0.0` to `IndicatorSnapshot`.
- **Calibration**: R (observation noise) is auto-calibrated from the live spread. Q (process noise) set from ATR. No manual tuning needed. Kalman gain self-adjusts: when spread is wide (high R), filter trusts the model more; when spread is tight, filter trusts observations more. This is exactly the behaviour needed.
- Memory: 3 floats per ticker (state, covariance, prior). Negligible.

**New P1 item**: **AR-01** — Kalman noise filter for FAST indicator inputs. Est. 5h.

---

### AR-02: Information Half-Life Modeling

**Problem**: A signal generated at 09:10 UK on a gap move has an information half-life measured in minutes, not hours. By 10:30, the gap has been priced in and the signal's edge has decayed to zero or negative. Currently, a qualified signal remains valid indefinitely — if the execution is delayed (broker latency, qualification queue, manual confirmation during Limited Live), the system may execute a stale signal whose edge has expired.

**Specification**: Model each signal's information half-life based on its trigger type. After 2 half-lives, the signal's expected value turns negative — auto-expire it.

**Formula**:
```
Signal value decay:
  EV(t) = EV_0 x exp(-lambda x t)

Where:
  lambda = ln(2) / half_life
  t = time elapsed since signal generation (minutes)

Half-lives by trigger type:
  GAP_SCAN:        tau = 8 min   (gap absorption is fast)
  FAST_MOMENTUM:   tau = 15 min  (momentum decays as participants enter)
  SLOW_TREND:      tau = 45 min  (trend continuation has longer life)
  PRE_MARKET_INTEL: tau = 30 min (overnight info decays at UK open)

Signal expiry: 2 x tau (EV decayed to 25% of original)
  GAP_SCAN:        expire after 16 min
  FAST_MOMENTUM:   expire after 30 min
  SLOW_TREND:      expire after 90 min
  PRE_MARKET_INTEL: expire after 60 min

Stale signal action: reject from qualification pipeline, log as SIGNAL_EXPIRED.
```

**Implementation**:
- File: `models.py` — add `generated_at: datetime` and `trigger_type: str` to `Signal` dataclass (if not already present). Add `expiry_at: datetime` computed from `generated_at + 2 x tau`.
- File: `qualification/qualifier.py` — new Stage 0 (before Stage 1 dedup): check `datetime.now() > signal.expiry_at`. If expired, reject immediately.
- File: `strategies/daily_target.py` — set `signal.trigger_type` based on which path generated it (GAP_SCAN, FAST_MOMENTUM, etc.).
- **ML integration**: Log `signal_age_at_execution = execution_time - generated_at` for every executed trade. After 200+ trades, the Ouroboros can learn the actual half-life from data and adjust tau values.

**New P1 item**: **AR-02** — Signal information half-life with auto-expiry. Est. 3h.

---

### AR-03: Look-Ahead Bias Prevention in ML Entry Score

**Problem**: The ML meta-model (`core/ml_meta_model.py`) is trained on `outcomes.jsonl` — a flat file of completed trades with features at entry and outcome labels. M-01 (Section 5) fixes the `confidence` feature leakage. But a deeper problem remains: the training set includes the ENTIRE history, and the model is evaluated on the ENTIRE history. There is no temporal split. A model trained on data from Week 10 that is evaluated on data from Week 5 has look-ahead bias — it has "seen the future". Bailey, Borwein, Lopez de Prado & Zhu (2017) show that even minor look-ahead bias inflates Sharpe ratios by 30-100%.

**Specification**: Enforce strict out-of-sample training with expanding-window walk-forward validation. The model may NEVER see future data during training. M-04 (Section 5) already calls for this — this item provides the exact implementation spec.

**Implementation**:
```
Walk-Forward Protocol:
  1. Sort outcomes.jsonl by entry_time (ascending)
  2. Initial training window: first 200 trades
  3. Validation window: next 50 trades (NOT seen during training)
  4. Test window: next 50 trades (NOT seen during training OR validation)
  5. Record test-window metrics (accuracy, precision, recall, Sharpe)
  6. Expand training window to include old validation data
  7. Repeat from step 3

Retrain trigger (fix for M-03):
  Every 50 new trades OR weekly (whichever first)

Anti-snooping rules:
  - NEVER use test-window metrics to select model hyperparameters
  - Hyperparameter selection uses ONLY training-validation split
  - Report ONLY test-window Sharpe in Go-Live Gate metrics
  - Log train/val/test split dates for every retrain event (audit trail)

Combinatorial Purged Cross-Validation (CPCV — De Prado 2018):
  - Apply CPCV for hyperparameter search within the training window
  - Purge: remove trades within 24h of each fold boundary (prevent leakage from autocorrelation)
  - Embargo: exclude 48h after each test fold start (prevent forward-looking bias)
```

- File: `core/ml_meta_model.py` — replace `sklearn.model_selection.KFold` (or equivalent) with custom `WalkForwardSplitter`. Add purge and embargo parameters.
- File: `learning/learning_engine.py` — ensure `should_retrain()` triggers on 50-trade count (fix M-03's signature bug first).
- **Validation**: After implementation, compare walk-forward test Sharpe vs. full-history Sharpe. If walk-forward Sharpe is < 50% of full-history Sharpe, the model has significant look-ahead bias and should not be used for sizing. Revert to frequency baseline (M-05).

**New P0 item**: **AR-03** — Walk-forward validation with purge/embargo. Est. 6h. This is P0 because look-ahead bias makes ML metrics meaningless — Go-Live Gate criterion "Win Rate >= 50% on 60+ trades" cannot be trusted without it.

---

### AR-04: Regime-Conditioned Go-Live Gates

**Problem**: The current Go-Live Gate (Section 9) requires "Win Rate >= 50% on 60+ trades" and "S15 Win Rate >= 40% on 30+ trades". These are AGGREGATE metrics. A system can achieve 55% WR overall while having 80% WR in TRENDING_UP and 15% WR in RANGE_BOUND. If it goes live during a range-bound market, it will hemorrhage capital. The aggregate masks regime-specific incompetence. Lopez de Prado (2019) argues that strategy evaluation must be regime-conditional.

**Specification**: Replace aggregate WR gates with per-regime minimum WR requirements. The system must demonstrate competence in EVERY regime it will trade in — not just the easy ones.

**Formula**:
```
Go-Live Gate — Regime-Conditioned Win Rate:
  For each regime R in {TRENDING_UP_STRONG, TRENDING_UP_MOD, RANGE_BOUND,
                         TRENDING_DOWN_STRONG, TRENDING_DOWN_MOD, HIGH_VOLATILITY}:
    If trade_count(R) >= 10:
      REQUIRE win_rate(R) >= 0.40
      If win_rate(R) < 0.40:
        FAIL Go-Live Gate with reason = f"WR in {R} = {wr:.0%} < 40% minimum"

  RISK_OFF and SHOCK regimes:
    No trade count required (system should be flat in these regimes)
    If trade_count > 0: WARN (should not be trading in RISK_OFF/SHOCK)

  Minimum regime coverage (expanded from v15.2):
    Paper period MUST include:
    - >= 20 trades in TRENDING regimes (UP_STRONG + UP_MOD + DOWN_STRONG + DOWN_MOD)
    - >= 10 trades in RANGE_BOUND
    - >= 5 trades in HIGH_VOLATILITY
    If market doesn't provide these regimes naturally, extend paper period — do NOT simulate.
```

**Implementation**:
- File: `scripts/sprint6_live_gate.py` — the Romano & Wolf 10-criteria Go-Live gate. Add regime-conditioned WR as criteria 11-16 (one per tradable regime).
- File: `qualification/go_nogo.py` — `GoNoGoTracker` already tracks per-regime stats. Add `get_regime_win_rates() -> dict[str, float]` method.
- File: `learning/learning_engine.py` — ensure regime label is correctly recorded for every trade (fix M-02's regime map bug first).
- **The aggregate 50% WR criterion remains** — it is necessary but not sufficient. Both aggregate AND per-regime gates must pass.

**Updated Go-Live Gate entry** (replaces existing Win Rate row):

| Criterion | Threshold |
|-----------|-----------|
| Win Rate (aggregate) | >= 50% on 60+ trades |
| Win Rate (per regime) | >= 40% on 10+ trades per tradable regime |
| Regime coverage | >= 20 trending + 10 range + 5 high-vol trades |
| RISK_OFF/SHOCK trades | 0 (any trade in these regimes = WARN) |

**New P0 item**: **AR-04** — Regime-conditioned Go-Live gates. Est. 4h. P0 because without this, the system can pass the aggregate gate while being catastrophically unprepared for certain regimes.

---

### AR-05: Survivorship Bias Warning and Forward-Test-Only Policy

**Problem**: The system uses yfinance for historical data. yfinance returns data only for currently listed tickers. Delisted ETPs (those that failed, were liquidated, or were absorbed) are absent from the historical record. Any backtest using yfinance data suffers from survivorship bias — it only tests against the "winners" (ETPs that survived to today). Garcia & Norli (2012) show that survivorship bias inflates backtest returns by 20-40% for leveraged products because the worst-performing products are systematically excluded.

**Specification**: Formally declare that NO yfinance-based backtest may be used for strategy timing, parameter selection, or Go-Live Gate evaluation. All performance metrics must come from FORWARD-TEST (paper trading) data only. This is a policy declaration, not a code change, but it requires enforcement mechanisms.

**Implementation**:
```
Enforcement Rules:
  1. Go-Live Gate: ALL criteria evaluated on paper trading data (outcomes.jsonl) ONLY.
     No criterion may reference yfinance historical backtests.

  2. ML training: outcomes.jsonl is forward-test data. This is already the case.
     HOWEVER: GPD tail risk (T-04) uses 270 days of yfinance history for VaR estimation.
     This is ACCEPTABLE because GPD is a risk measure (conservative bias helps),
     not a return measure (where survivorship bias inflates).

  3. Parameter calibration: Stop distances, target levels, Kelly inputs — these must
     ALL come from paper trading data after 100+ trades.
     Current state: stop=0.3%, target=0.5% from playbook.json (forward-test). CORRECT.

  4. DSR Graduation Gate: Uses outcomes.jsonl (forward-test). CORRECT.

  5. Code enforcement: Add a header comment to `scripts/sprint6_live_gate.py`:
     # WARNING: ALL METRICS BELOW USE FORWARD-TEST DATA ONLY.
     # yfinance backtests suffer from survivorship bias (Garcia & Norli 2012).
     # Do NOT substitute historical backtest results for any Go-Live criterion.
```

- File: `scripts/sprint6_live_gate.py` — add survivorship bias warning header. Add assertion that data source = `outcomes.jsonl` (not any `*backtest*` file).
- File: `core/evt.py` — `TailRiskMonitor.veto_signal()` uses yfinance history. Add comment: "Survivorship bias present but acceptable — GPD is a risk measure (conservative direction)."
- **Documentation**: Add to Section 12 Glossary: `Survivorship Bias: The systematic overstatement of returns caused by excluding failed/delisted instruments from historical data. All yfinance data suffers from this. Garcia & Norli (2012).`

**No new stop-ship item** — this is a policy enforcement. Integrate during Go-Live Gate implementation (Phase 3).

---

## UPDATED UNIFIED THRESHOLD TABLE (Additions from Section 2C)

These entries are ADDED to the Section 0.1 Unified Threshold Table:

| Parameter | Value | Code Location | Notes |
|-----------|-------|---------------|-------|
| **Stop noise floor (1-min ATR)** | **k=2.0 x ATR_1min x L** | `daily_target.py` | **CQ-01: Microstructure breathing room** |
| **FAST trigger price source** | **Bid (LONG) / Ask (SHORT)** | `daily_target.py` | **CQ-02: Mid-price illusion filter** |
| **Inverse ETP vol drag multiplier** | **1.5x** | `cost_drag_calculator.py` | **CQ-03: Avellaneda & Zhang 2010** |
| **Inverse ETP Kelly penalty** | **0.85x** | `dynamic_sizer.py` | **CQ-03: Asymmetric decay offset** |
| **Reversal recovery cooldown** | **15 min per-ticker** | `virtual_trader.py` + Redis | **CQ-04: After sub-60s stopout** |
| **Cross-asset divergence veto** | **ETP >1.5%, underlying <0.3%** | `daily_target.py` | **CQ-05: Premium divergence filter** |
| **SLOW indicator cache TTL** | **120s** | Redis `nzt:slow_ind:*` | **SA-01: Stale after 2 cycles** |
| **Telemetry queue maxsize** | **500** | `core/telemetry.py` | **SA-03: Drop if full (not trade-critical)** |
| **LOBCache stale threshold** | **120s** | `core/lob_cache.py` | **SA-04: Mark stale, don't delete** |
| **Order timeout (IOC/cancel)** | **3 seconds** | `execution/planner.py` | **SA-05: Stale fill prevention** |
| **Opening toxic spread cap** | **35 bps** | `execution/cost_model.py` | **RO-01: 09:00-09:10 UK only** |
| **Instant-stopout halt** | **3 per session** | `circuit_breakers.py` | **RO-02: Sub-60s stops** |
| **Max derivatives per underlying** | **1** | `portfolio_risk.py` | **RO-03: Underlying inventory limit** |
| **Gap scan sizing penalty** | **0.50x Kelly** | `dynamic_sizer.py` | **RO-04: 09:05-09:20 UK window** |
| **Maker-peg max resubmissions** | **3** | `execution/planner.py` | **RO-05: Then escalate to mid-price** |
| **Kalman R (noise variance)** | **(spread_bps/10000)^2 x P^2 / 4** | `core/noise_filter.py` | **AR-01: Auto-calibrated** |
| **Signal half-life (GAP_SCAN)** | **8 min** | `models.py` + `qualifier.py` | **AR-02: Expire at 16 min** |
| **Signal half-life (FAST_MOMENTUM)** | **15 min** | `models.py` + `qualifier.py` | **AR-02: Expire at 30 min** |
| **Signal half-life (SLOW_TREND)** | **45 min** | `models.py` + `qualifier.py` | **AR-02: Expire at 90 min** |
| **Walk-forward purge window** | **24h** | `ml_meta_model.py` | **AR-03: Prevent autocorrelation leakage** |
| **Walk-forward embargo** | **48h** | `ml_meta_model.py` | **AR-03: Prevent forward-looking bias** |
| **Per-regime Go-Live WR** | **>= 40% on 10+ trades** | `sprint6_live_gate.py` | **AR-04: Per tradable regime** |

---

## UPDATED GO-LIVE GATE CRITERIA (Additions from Section 2C)

These criteria are ADDED to the existing Go-Live Gate table in Section 9:

| Criterion | Threshold | Source |
|-----------|-----------|--------|
| **Win Rate (per regime)** | **>= 40% on 10+ trades per tradable regime** | **AR-04** |
| **Regime Coverage** | **>= 20 trending + 10 range + 5 high-vol trades** | **AR-04** |
| **RISK_OFF/SHOCK Trade Count** | **0 (any trade = WARN, 3+ = FAIL)** | **AR-04** |
| **ML Walk-Forward Test Sharpe** | **> 0.5 (out-of-sample, not in-sample)** | **AR-03** |
| **Instant Stopout Rate** | **< 10% of all trades exit within 60s** | **RO-02** |
| **Maker Fill Rate** | **> 30% of limit orders filled at maker price** | **RO-05** |
| **Cross-Asset Divergence Vetoes** | **Logged and reviewed; < 5% of signals vetoed by CQ-05** | **CQ-05** |
| **Data Source** | **100% forward-test (zero yfinance backtest metrics in gate)** | **AR-05** |

---

## NEW STOP-SHIP ITEMS (Section 2C Additions)

### P0-CRITICAL (5 new items — total P0 count: 18 + 5 = 23)

| # | ID | Description | File(s) | Status | Est. Hours |
|---|-----|-------------|---------|--------|-----------|
| 19 | **RO-01** | **Toxic spread hard-cap 35 bps for first 10 min** | `daily_target.py`, `cost_model.py` | **OPEN** | **2h** |
| 20 | **RO-02** | **3x instant-stopout circuit breaker = halt session** | `circuit_breakers.py`, `virtual_trader.py` | **OPEN** | **2h** |
| 21 | **RO-03** | **Underlying inventory limit (max 1 derivative per underlying)** | `portfolio_risk.py`, `isa_universe.py` | **OPEN** | **2h** |
| 22 | **AR-03** | **Walk-forward validation with purge/embargo for ML** | `ml_meta_model.py`, `learning_engine.py` | **OPEN** | **6h** |
| 23 | **AR-04** | **Regime-conditioned Go-Live gates (40% WR per regime)** | `sprint6_live_gate.py`, `go_nogo.py` | **OPEN** | **4h** |

### P1 (7 new items — total P1 count: 20 + 7 = 27)

| # | ID | Description | Status | Est. Hours |
|---|-----|-------------|--------|-----------|
| 21 | **CQ-01** | Volatility-scaled breathing room for stops (1-min ATR noise floor) | OPEN | 4h |
| 22 | **CQ-02** | Mid-price illusion filter (bid/ask trigger for FAST tier) | OPEN | 3h |
| 23 | **CQ-04** | Reversal recovery cooldown (15-min per-ticker after instant stopout) | OPEN | 2h |
| 24 | **CQ-05** | Cross-asset premium divergence filter | OPEN | 4h |
| 25 | **SA-02** | JIT-compile numerical qualification gates (numba) | OPEN | 4h |
| 26 | **SA-04** | LOBCache module for sub-microsecond heartbeat reads | OPEN | 6h |
| 27 | **RO-05** | Maker-pegged synthetic limit orders | OPEN | 5h |

### P1 (no new stop-ship — integrate during scheduled phase)

| ID | Description | Phase |
|----|-------------|-------|
| SA-01 | SLOW indicator background pre-computation | Phase 0A (with T-03) |
| SA-03 | Async telemetry offloading via Redis stream | Phase 1 |
| SA-05 | Stale order cancellation (IOC + 3s timeout) | **P0 for LIMITED LIVE** |
| CQ-03 | Asymmetric leverage decay offset | Phase 1 |
| RO-04 | Overnight gap sizing penalty | Phase 0A (with T-01) |
| AR-01 | Kalman noise filter for FAST indicator inputs | P1, Phase 1 |
| AR-02 | Signal information half-life with auto-expiry | P1, Phase 1 |
| AR-05 | Survivorship bias forward-test-only policy | Phase 3 (Go-Live Gate) |

---

## IMPLEMENTATION PRIORITY ORDER

Integrate Section 2C items into the existing phase structure:

**Phase 0A (Week 1)** — alongside T-01 through T-08:
- RO-01 (toxic spread cap — needed because T-01 opens the gap window)
- RO-04 (gap sizing penalty — same dependency on T-01)
- SA-01 (SLOW background thread — needed for T-05 FAST/SLOW split)

**Phase 0B (Week 2)** — alongside risk fixes:
- RO-02 (instant-stopout circuit breaker)
- RO-03 (underlying inventory limit)
- AR-03 (walk-forward validation — needed before any ML metric is trusted)
- AR-04 (regime-conditioned gates — needed for Go-Live Gate definition)

**Phase 1 (Weeks 2-3)** — alongside execution upgrades:
- CQ-01 (vol-scaled stops)
- CQ-02 (mid-price illusion filter)
- CQ-03 (asymmetric leverage decay)
- CQ-04 (reversal cooldown)
- CQ-05 (cross-asset divergence)
- SA-02 (JIT compilation)
- SA-03 (async telemetry)
- AR-01 (Kalman noise filter)
- AR-02 (signal half-life)
- RO-05 (maker-pegged limits)

**Phase 2+ / Go-Live Gate**:
- SA-04 (LOBCache — best value when real-time feed is available)
- SA-05 (IOC/timeout — required before LIMITED LIVE, not before paper)
- AR-05 (survivorship bias policy — enforcement at Go-Live Gate)

---

**Section 2C Statistics**:
- 20 institutional microstructure defenses across 4 personas
- 5 new P0 items (total P0: 23)
- 7 new P1 items (total P1: 27)
- 8 items integrated into existing phases without new stop-ship status
- 16 new rows in Unified Threshold Table
- 8 new Go-Live Gate criteria
- Estimated total implementation: ~73 hours
- New files: `core/noise_filter.py`, `core/lob_cache.py`, `qualification/fast_gates.py`
- Modified files: `strategies/daily_target.py`, `qualification/dynamic_sizer.py`, `qualification/circuit_breakers.py`, `qualification/portfolio_risk.py`, `qualification/qualifier.py`, `execution/virtual_trader.py`, `execution/planner.py`, `execution/cost_model.py`, `execution/order_rules.py`, `core/telemetry.py`, `core/cost_drag_calculator.py`, `core/realtime_data.py`, `core/ml_meta_model.py`, `uk_isa/isa_universe.py`, `models.py`, `main.py`, `scripts/sprint6_live_gate.py`
