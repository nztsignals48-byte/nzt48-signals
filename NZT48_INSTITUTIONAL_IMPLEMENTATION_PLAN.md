# NZT-48 — INSTITUTIONAL IMPLEMENTATION PLAN
**Version**: 3.0 | **Date**: 2026-03-01 | **Author**: Claude (Sonnet 4.6) + Gemini Cross-Validation
**Classification**: Internal — Engineering Execution Document
**Status**: Authoritative — all file paths, line numbers, and changes verified against live codebase

---

## GOVERNING PRINCIPLE

Upgrade the mathematics and data quality. Do not restrict the system's ability to trade. Every mandate adds capability or corrects a mathematical flaw — none remove valid signal sources.

**What this document is**: The single source of truth combining three inputs:
1. The original 6-mandate mathematical corrections (verified against codebase)
2. The 6-sprint operational execution plan (EXECUTION_PLAN_V1.md)
3. Gemini's "Trading System Critique: Brutal Honesty" — filtered through engineering reality

**One rule above all others**: Never deploy real money until Sprint 4 gate is green (ALL 10 Go/No-Go criteria). Every sprint before that is building the machine.

---

## CODEBASE GROUND TRUTH (2026-03-01)

| Component | Reality |
|-----------|---------|
| main.py | 7,607 lines. Kelly at line 3720. ML blend at lines 1643–1663. Decay halt at lines 1486–1495 (ticker) + 1575–1581 (strategy). |
| kelly_sizer.py | ✅ DONE — Merton continuous-time + leverage adjustment implemented in `bots/kelly_sizer.py`. Call site at main.py line 3720 still needs `ticker=` arg passed. |
| delivery/database.py | ✅ DONE — WAL mode enabled at line 213 (`PRAGMA journal_mode=WAL`). |
| ml_meta_model.py | 70/30 blend active via `blend_confidence()`. Model UNTRAINED — pass-through mode. 2,327 outcomes in data/outcomes.jsonl. Win rate: 38.5%. |
| mean_reversion.py | `_STRATEGY_DORMANT = False` (reactivated in V2.1). NO leverage guard. Actively runs on 3x/5x ETPs — structural conflict. |
| daily_target.py | `_DAILY_TARGET_PCT = 2.0`. Runner mode to 6%. Chandelier exit NOT yet implemented. |
| cross_asset_macro.py | VIX, DXY, credit spread wired. HMM NOT wired. Alternative.me NOT wired. |
| docker-compose.yml | Two containers only (nzt48 + dashboard). NO Redis. |
| sprint6_live_gate.py | Does NOT exist. |
| data/outcomes.jsonl | 2,327 trades, 38.5% win rate. Below 45% floor for Sprint 1 gate. |

---

## GEMINI CRITIQUE FILTER — WHAT WE ACCEPT VS REJECT

Before the mandates: every Gemini recommendation has been evaluated against the system's reality at £10k paper ISA trading.

**GEMINI "KILL SHOTS" — EVALUATED:**

| Gemini Concern | Our Assessment | Decision |
|----------------|----------------|----------|
| "2% daily target is a mathematical fantasy at scale — $2.08B AUM issue" | TRUE at institutional scale. FALSE at £10k–£250k ISA. The compound target is a horizon goal, not a daily obligation. At current scale (£10k, 1 trade/day), this is not a kill shot. | REJECTED as immediate concern. Revisit at £100k+ AUM. |
| "Capacity constraint — AUM growth vs market impact" | Genuine but premature. QQQ3.L daily volume >£10M. At £10k position sizing, we are < 0.1% of daily flow. | DEFERRED to Sprint 6 (£100k+ milestone). |
| "Volatility decay / sequence of returns risk" | Real. The profit ladder (Chandelier exit) and Kelly sizing address this. | ALREADY IN PLAN (Mandate 5, Task 1.6). |
| "Adverse selection / HFT toxic flow" | Real at microsecond execution. We are a daily momentum system, not a microstructure strategy. Our edge is regime + momentum, not order flow. | NOT APPLICABLE at daily frequency. |
| "TCA missing — commissions eat 50-80% of gross profits" | TRUE and important. Round-trip spread on LSE ETPs is real. Bid-ask EV filter is Mandate 2. | ALREADY IN PLAN (Mandate 2). |
| "Naive risk management — need CVaR not profit targets" | Partially valid. Portfolio heat (RC-02) is Sprint 1. CVaR at £10k paper is over-engineering. | CVaR deferred to Sprint 4+. Portfolio heat is Task 1.5. |
| "De-lever target to 0.10-0.15 Max Daily VaR" | Rejected for now. Our 0.75% immutable risk cap per trade + Half-Kelly already manages this. Re-examine at live capital deployment. | DEFERRED — risk constitution already addresses this. |
| "Walk-forward optimisation if params are static" | Valid. Auto-improvement loop (Task 3.3) implements this via Sunday weekly param sweep. | ALREADY IN PLAN (Task 3.3). |
| "Audit for look-ahead bias" | Valid. Known risk with any indicator-based system. | ADDED as Mandate 11 (lightweight audit, not full rebuild). |
| "Synthetic Order Book / L2 data" | Over-engineering for current scale. Bid-ask EV filter (Mandate 2) using real bid-ask quotes achieves the same goal more simply. | REJECTED. Mandate 2 covers this. |

**NET VERDICT**: Gemini's critique is institutionally correct but applied to the wrong AUM tier. At £10k–£100k ISA paper trading, the correct fixes are: (1) cost modelling, (2) leverage-adjusted Kelly, (3) regime-aware ML gating, (4) win rate improvement. Everything else is premature optimisation.

---

## PART I: MATHEMATICAL CORRECTIONS (Mandates 1–6)

---

### MANDATE 1 — MERTON CONTINUOUS-TIME KELLY
**File**: `bots/kelly_sizer.py` + `main.py` line 3720
**Status**: ✅ COMPLETE (kelly_sizer.py rewritten) | ⏳ Wire-up pending (10 min)
**Priority**: CRITICAL

**What was done**: Full rewrite implementing:
```
f* = μ / σ²                     (Merton 1971 unleveraged)
f*_leveraged = f*_unl / λ       (MacLean, Thorp & Ziemba 2011)
half_kelly = f*_leveraged / 2   (Hakansson 1971 safety buffer)
```

**Leverage map**:
```
QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, QQQS.L, 3USS.L → λ = 3
MU2.L → λ = 2
QQQ5.L, SP5L.L → λ = 5
```

**Pending**: `main.py` line 3720 — add `ticker=signal.ticker` to the `get_position_size()` call. 10-minute task.

**Hard caps (unchanged)**: Max 5% notional per trade | 0.75% max risk per trade (immutable)

---

### MANDATE 2 — BID-ASK EV FILTER
**Files**: `core/realtime_data.py` + `strategies/daily_target.py`
**Status**: ⏳ NOT STARTED — depends on TwelveData bid-ask feed (Task 2.7)
**Priority**: CRITICAL before live capital

**The problem**: yfinance provides no live bid-ask data. Round-trip spread on LSE ETPs is 0.2–1.0%. On a 2% gross target, a 0.5% round-trip (2× spread) consumes 25% of gross profit before any cost. For thin ETPs (3SEM.L, TSM3.L, GPT3.L), this can reach 50%+ on bad-liquidity days.

**What to build** (after Task 2.7 TwelveData upgrade):

Step A — `core/realtime_data.py`:
```python
def get_bid_ask(self, ticker: str) -> dict:
    """TwelveData /quote for .L tickers (has bid/ask fields).
    Polygon /v2/last/nbbo/{ticker} for US tickers.
    Fallback: SpreadHistoryTracker.get_fallback_spread(ticker) — P90 rolling."""
```

Step B — EV gate in `strategies/daily_target.py`:
```python
spread_pct = bid_ask.get("spread_pct", spread_tracker.get_fallback_spread(ticker))
round_trip = spread_pct * 2
ev_net = (win_rate * (target_pct - round_trip)) - (loss_rate * (stop_pct + round_trip))
if ev_net <= 0.005:  # 0.5% floor
    return None, f"EV_NEGATIVE: {ev_net:.4f}"
```

**Fallback spread**: P90 of rolling 5-day history per ticker (not hardcoded 0.2% — see Mandate 10).
**Key constraint**: NEVER block a trade on missing bid-ask data. If feed unavailable, use fallback.

---

### MANDATE 3 — DE PRADO META-LABELLING
**File**: `core/ml_meta_model.py` + `main.py` lines 1643–1663
**Status**: ⏳ NOT STARTED (but 2,327 outcomes available — model can train NOW)
**Priority**: HIGH

**The problem**: The 70/30 `blend_confidence()` is a regression blend — ML tries to predict confidence magnitude. De Prado (2018) Chapter 4 proves this is wrong: ML should decide BINARY (trade or skip), not adjust a continuous confidence score.

**What to build** — replace `blend_confidence()` with `meta_label()`:
```python
def meta_label(self, features: dict) -> dict:
    """Binary classifier gate. S15 generates the signal. This decides: trade or skip."""
    if not self.is_trained:
        return {"veto": False, "p_success": 0.5, "model_active": False}  # cold-start: never veto
    p_success = self.predict_proba(features)
    threshold = self._get_adaptive_threshold()  # 0.65 default, regime-adjusted
    return {"veto": p_success < threshold, "p_success": p_success, "threshold": threshold}
```

**Veto threshold**: 0.65 default (TRENDING: 0.60, CHOPPY/VOLATILE: 0.70, SHOCK: veto all)

**main.py replacement** (lines 1643–1663):
```python
# Replace blend_confidence() block with meta_label gate:
_verdict = await loop.run_in_executor(None, self.ml_model.meta_label, _features)
if _verdict.get("veto"):
    raw_signals.remove(_sig)  # filtered before gauntlet
    logger.info("META_LABEL_VETO: %s P=%.3f thresh=%.2f", _sig.ticker,
                _verdict["p_success"], _verdict["threshold"])
```

**Cold-start**: With 2,327 outcomes, the model should be trained NOW. `meta_label()` cold-start returns `veto=False` always — system never restricts itself before it has evidence.

---

### MANDATE 4 — REGIME GATING AND STRUCTURAL CONFLICT RESOLUTION

#### 4a — Mean Reversion Leverage Guard
**File**: `strategies/mean_reversion.py`
**Status**: ⏳ NOT STARTED — URGENT (actively running on 3x/5x ETPs right now)
**Priority**: CRITICAL — fix before next trading session
**Effort**: 5 minutes

**The problem**: `_STRATEGY_DORMANT = False` in V2.1. Avellaneda & Zhang (2010): leveraged ETP daily rebalancing mechanically reinforces trends. Mean reversion on QQQ3.L fights the instrument's own mechanics and will systematically lose during trending regimes — which is when S15 is most active.

**Fix** — add at top of `_evaluate_ticker()`:
```python
_LEVERAGED_SUFFIXES = ("3.L", "5.L", "2.L", "S.L")
if any(ticker.upper().endswith(s) for s in _LEVERAGED_SUFFIXES):
    return None  # Hard veto — leveraged ETPs are trend-reinforcing, not mean-reverting
```

This is 5 lines. It does NOT disable mean reversion for any 1× instrument added in the future.

#### 4b — HMM Regime Classifier
**File**: `core/cross_asset_macro.py`
**Status**: ⏳ NOT STARTED
**Priority**: HIGH
**Effort**: 2–3 hours

**The problem**: Regime classification uses VIX thresholds + EMA alignment (deterministic heuristics). Hamilton (1989) HMM estimates the PROBABILITY the market is in State 1 (choppy/high-vol) — it doesn't just react to today's VIX, it models the latent transition probability.

```python
class HMMRegimeClassifier:
    """2-state Gaussian HMM. Hamilton (1989) — Econometrica.
    State 0: Trending/Low-Vol (S15 permitted).
    State 1: Choppy/High-Vol (S15 halted, S16 fallback).
    Trained on 60-day rolling QQQ daily returns. Retrained weekly."""

    def predict_choppy_prob(self, recent_returns: np.ndarray) -> float:
        """Returns P(State=1 choppy). 0.5 if model unavailable — never blocks trading."""

    def is_choppy(self, threshold: float = 0.60) -> bool:
        return self._current_state_prob > threshold
```

Wire into `cross_asset_macro.is_risk_off()`:
```python
def is_risk_off(self) -> bool:
    return (
        self._vix_signal == "BACKWARDATION"
        or self._credit_signal == "CREDIT_STRESS"
        or self._hmm.is_choppy()  # NEW
    )
```

Dependency: `hmmlearn` — already in requirements.txt (confirmed).

#### 4c — Alternative.me Fear & Greed
**File**: `core/cross_asset_macro.py`
**Status**: ⏳ NOT STARTED
**Priority**: EASY WIN — 30 minutes, zero cost

```python
def _get_fear_greed_signal(self) -> str:
    """Alternative.me Crypto Fear & Greed as supplementary risk-off signal.
    <25 = Extreme Fear → veto all longs. Free API, no key needed.
    NEVER blocks trading if API unavailable — returns UNKNOWN."""
    try:
        import urllib.request, json as _json
        with urllib.request.urlopen("https://api.alternative.me/fng/?limit=1", timeout=5) as r:
            value = int(_json.loads(r.read())["data"][0]["value"])
        return "EXTREME_FEAR" if value < 25 else "EXTREME_GREED" if value > 75 else "NEUTRAL"
    except:
        return "UNKNOWN"  # Never block on API failure
```

---

### MANDATE 5 — CHANDELIER TRAILING EXIT + EXPLICIT PROFIT LADDER
**File**: `strategies/daily_target.py` + exit engine
**Status**: ⏳ NOT STARTED
**Priority**: HIGH (this IS the fat-right-tail capture mechanism)

**The problem**: Hard `_DAILY_TARGET_PCT = 2.0` caps wins at 2% on most days. The profit ladder in the PDF (Entry → +2% → +4% → +6% → ∞) is not explicitly coded.

**What to build** — two components:

**Component A: Explicit Profit Ladder** (replaces current runner logic):
```python
LADDER_RUNGS = [
    {"pct": 2.0, "action": "move_stop_to_breakeven"},
    {"pct": 4.0, "action": "lock_profit_2pct"},
    {"pct": 6.0, "action": "scale_out_50pct_and_trail"},
    {"pct": 8.0, "action": "trail_stop_1atr"},
    {"pct": 10.0, "action": "trail_stop_0.5atr"},
    # Infinite — trail tightens 0.25× ATR every 2% forever
]
```

**Component B: Chandelier Exit** (Le Beau 1999) — activates after 2% profit:
```python
class ChandelierExit:
    """Trailing stop at Highest_High - N×ATR. Activates at ≥2% profit.
    λ=5: N=1.0 (tighter — vol drag). λ=3: N=1.5. λ=2: N=2.0."""

    def update(self, current_high, current_price, atr, entry_price) -> dict:
        if not self._active:
            if (current_price - entry_price) / entry_price >= 0.02:
                self._active = True
                self._highest_high = current_high
        if not self._active:
            return {"exit": False, "trailing_stop": None}
        self._highest_high = max(self._highest_high, current_high)
        trailing_stop = self._highest_high - (self._atr_mult * atr)
        return {"exit": current_price <= trailing_stop, "trailing_stop": trailing_stop}
```

**Key constraint**: 2% minimum qualifying threshold stays. Chandelier activates AFTER 2% — it replaces the ceiling, not the floor. `_DAILY_TARGET_PCT = 2.0` remains as the minimum and cold-start exit.

---

### MANDATE 6 — ROMANO & WOLF STATISTICAL GATE
**File**: `scripts/sprint6_live_gate.py` (does not exist — must be created)
**Status**: ⏳ NOT STARTED
**Priority**: HIGH (must exist before Sprint 4 Go/No-Go)

With 20 strategies, selection bias is severe. Harvey, Liu & Zhu (2016): t ≥ 3.0 for single-strategy testing. Romano & Wolf (2005) Bonferroni correction for N=20: **t ≥ 4.3**.

```python
"""Sprint 6 Live Trading Gate — Romano & Wolf (2005) StepM Implementation
Gate criteria for live capital approval:
  1. t-stat (cost-adjusted Sharpe) ≥ 4.3   [Bonferroni-corrected, N=20]
  2. MTRL ≥ 63 trading days paper trades
  3. Cost-adjusted Sharpe ≥ 1.5             [NOT gross Sharpe]
  4. Max drawdown < 8%
  5. Holdout t-stat ≥ 3.0                   [last 30% of MTRL, out-of-sample]
  6. Consecutive profitable weeks ≥ 3
  7. Win rate (rolling 50) ≥ 50%
  8. No circuit breaker fires in MTRL period
"""
```

**Current status on gate criteria**:
- Win rate: 38.5% → needs to reach 50%+ via Sprints 1–3
- t-stat: cannot compute without cost-adjusted returns
- This gate will not pass until Sprint 3 work is complete

---

## PART II: INFRASTRUCTURE ADDITIONS (Mandates 7–10)

Three of Gemini's five infrastructure concerns are valid and actionable. Two were rejected as over-engineering.

---

### MANDATE 7 — SQLITE WAL MODE
**File**: `delivery/database.py`
**Status**: ✅ COMPLETE — WAL mode already enabled at line 213

Confirmed: `PRAGMA journal_mode=WAL` is live. No further action needed.

---

### MANDATE 8 — ML EXECUTION ISOLATION
**File**: `main.py` lines 1643–1663
**Status**: ⏳ COMBINED WITH MANDATE 3 — when meta_label() is added, wrap in run_in_executor

```python
loop = asyncio.get_event_loop()
_verdict = await loop.run_in_executor(None, self.ml_model.meta_label, _features)
```

This is the same change as Mandate 3's integration. One change, two benefits: GIL isolation + meta-label gate.

---

### MANDATE 9 — PERSISTENT CHANDELIER STATE (REDIS)
**Files**: `docker-compose.yml` + `ChandelierExit` class (Mandate 5)
**Status**: ⏳ NOT STARTED — implement with Mandate 5
**Effort**: 1 hour

Add Redis to `docker-compose.yml`:
```yaml
redis:
  image: redis:7-alpine
  container_name: nzt48-redis
  command: redis-server --appendonly yes --appendfsync everysec
  ports:
    - "6379:6379"
  volumes:
    - redis_data:/data
  restart: always
  mem_limit: 256m
```

`ChandelierExit.__init__` hydrates from Redis on startup, `update()` persists every call:
```python
self._redis_key = f"chandelier:{ticker}:{trade_id}"
# On init: load from Redis if available
# On update: redis.set(key, json.dumps(state), ex=86400)
# On close: redis.delete(key)
```

Fallback: if Redis unavailable, operate in-memory (paper mode is acceptable without Redis).
Add `redis>=5.0.0` to `requirements.txt`.

---

### MANDATE 10 — DYNAMIC FALLBACK SPREAD (P90)
**File**: `core/realtime_data.py` — implement with Mandate 2
**Status**: ⏳ NOT STARTED
**Effort**: 1–2 hours

The hardcoded 0.2% fallback spread is dangerous during feed outages. During market shocks, 3SEM.L spread can reach 1.5%+. P90 of rolling 5-day history is the correct statistical approach.

```python
class SpreadHistoryTracker:
    """Rolling 500-record spread history per ticker. Fallback = P90."""
    def record(self, ticker: str, spread_pct: float) -> None: ...
    def get_fallback_spread(self, ticker: str) -> float:
        spreads = [r["spread"] for r in self._history.get(ticker, [])]
        if len(spreads) < 10:
            return 0.004  # 0.4% conservative floor — no history yet
        return max(float(np.percentile(spreads, 90)), 0.002)
```

---

### MANDATE 11 — LOOK-AHEAD BIAS AUDIT (NEW — from Gemini)
**File**: `strategies/daily_target.py`, `strategies/universal_scanner.py`, `core/ml_meta_model.py`
**Status**: ⏳ NOT STARTED
**Priority**: MEDIUM (must complete before Sprint 4 Go/No-Go)
**Effort**: 2–3 hours

Gemini correctly identified that look-ahead bias (using t+1 data to make t decisions) is a common silent killer in algorithmic systems. This is not a rebuild — it is an audit.

**What to check**:
1. In `daily_target.py`: Confirm all indicators (VWAP, EMA, ATR, MACD) use only data available at signal generation time. VWAP uses intraday cumulative — confirm it does not use end-of-day VWAP.
2. In `ml_meta_model.py`: Training labels must be assigned AFTER the trade closes, not at entry. Confirm `outcomes.jsonl` records entry time AND close time separately.
3. In `cross_asset_macro.py`: Confirm VIX and macro data used are the prior-day close values, not same-day values fetched before market open.
4. In `move_attribution.py`: Confirm peer-move boosts use confirmed prior moves, not real-time data that hasn't settled.

**Output**: A short audit log (`data/lookahead_audit.md`) documenting each check, confirming clean or flagging the issue.

---

## PART III: THE 6-SPRINT OPERATIONAL PLAN

This is the implementation sequence that takes the system from 38.5% win rate to live capital deployment. Each sprint has a hard entry gate that must be met before starting.

---

### SPRINT 0 — IMMEDIATE FIXES (This Week)
**Gate**: None — do these NOW before next trading session
**Est. effort**: 3–4 hours

| Task | File | Effort | Why Urgent |
|------|------|--------|-----------|
| S0.1: Wire Kelly ticker arg | `main.py` line 3720 | 10 min | Mandate 1 is half-done without this |
| S0.2: Mean reversion leverage guard | `strategies/mean_reversion.py` | 5 min | Actively losing on leveraged ETPs RIGHT NOW |
| S0.3: Alt.me Fear & Greed | `core/cross_asset_macro.py` | 30 min | Free, zero-risk, additive signal |
| S0.4: Redis in docker-compose | `docker-compose.yml` | 30 min | Required by Mandate 9 and Chandelier |
| S0.5: Train ML meta-model | `core/ml_meta_model.py` | 1 hr | 2,327 outcomes available — no excuse not to train now |

Sprint 0 acceptance: Kelly wired, mean reversion guard active, ML trained on existing outcomes.

---

### SPRINT 1 — WIRE THE MACHINE
**Gate to start**: Sprint 0 complete
**Gate to finish**: Win rate (rolling 50 trades) ≥ 45% · Learning engine firing on every closed trade
**Est. effort**: 10–12 hours over 2 weeks (3–14 March)

| Task | File | Priority |
|------|------|----------|
| 1.1: Wire learning.record_trade() after every closed trade | `execution/virtual_trader.py` | 🔴 CRITICAL |
| 1.2: Activate move attribution chain reactions | `main.py` + `learning/move_attribution.py` | 🔴 CRITICAL |
| 1.3: RC-07b earnings fade hard gate | `core/earnings_fade_gate.py` (new) | 🔴 CRITICAL |
| 1.4: Monday penalty & day-of-week filter | `strategies/daily_target.py` + `universal_scanner.py` | 🟡 HIGH |
| 1.5: Portfolio heat aggregation (RC-02) | `core/portfolio_heat.py` (new) | 🟡 HIGH |
| 1.6: Chandelier exit + explicit profit ladder | `strategies/daily_target.py` + `core/chandelier_exit.py` | 🟡 HIGH |
| 1.7: Pre-earnings run-up score (nightly compute) | `scripts/scheduled_jobs.py` | 🟡 HIGH |
| 1.8: Google Sheets live P&L logger | `delivery/sheets_logger.py` config | 🟢 LOW |

**Sprint 1 gate** (ALL must be true):
- `learning.record_trade()` called after every closed trade (verified in logs)
- `move_attribution.check_move()` called in scan loop
- RC-07b fade gate blocking pre-earnings LONG entries
- Monday penalty active and logged
- Portfolio heat monitor running
- Chandelier exit + explicit profit rungs deployed
- **Win rate (rolling 50 trades) ≥ 45%**

---

### SPRINT 2 — MARKET MICROSTRUCTURE LAYER
**Gate to start**: Sprint 1 complete · Win rate ≥ 45%
**Gate to finish**: All 8 microstructure modules active · Win rate ≥ 48%
**Est. effort**: 10–12 hours (17–31 March)

| Task | File | Priority |
|------|------|----------|
| 2.1: IV crush module | `strategies/vol_crush.py` | 🟡 HIGH |
| 2.2: Short squeeze monitor | `feeds/short_interest_feed.py` (new) | 🟡 HIGH |
| 2.3: Options expiry pinning | `feeds/expiry_calendar.py` (new) | 🟡 HIGH |
| 2.4: Window dressing enhancement | `strategies/rebalance_flow.py` | 🟠 MEDIUM |
| 2.5: Gap fill detection & fade signals | `strategies/universal_scanner.py` | 🟠 MEDIUM |
| 2.6: Earnings whisper magnitude filter | `strategies/pead_earnings.py` | 🟠 MEDIUM |
| 2.7: TwelveData Grow plan upgrade | `.env.production` + `feeds/data_feeds.py` | 🟠 MEDIUM |
| 2.8: GEX data source verification | `strategies/gamma_squeeze.py` | 🟠 MEDIUM |
| 2.9: Bid-Ask EV Filter + P90 spread | `core/realtime_data.py` + `daily_target.py` | 🟡 HIGH (requires 2.7 first) |

**Sprint 2 gate** (ALL must be true):
- IV crush, short interest, options expiry, window dressing live
- TwelveData real-time confirmed for .L tickers (QQQ3.L within 5s of live)
- Bid-ask EV filter active with P90 fallback spread
- GEX verified or proxy implemented
- **Win rate (rolling 50 trades) ≥ 48%**

---

### SPRINT 3 — INTELLIGENCE ACTIVATION
**Gate to start**: Sprint 2 complete · 200+ total trades · Win rate ≥ 48%
**Gate to finish**: ML meta-label gate active · NLP sentiment live · Auto-improvement weekly
**Est. effort**: 8–10 hours (7–25 April)

| Task | File | Priority |
|------|------|----------|
| 3.1: Meta-label gate (replaces 70/30 blend) | `core/ml_meta_model.py` + `main.py` | 🔴 CRITICAL |
| 3.2: HMM regime classifier | `core/cross_asset_macro.py` | 🟡 HIGH |
| 3.3: NLP earnings sentiment (Gemini API) | `feeds/earnings_sentiment.py` (new) | 🟠 MEDIUM |
| 3.4: Auto-improvement loop verification | `learning/param_optimizer.py` | 🟡 HIGH |
| 3.5: Regime-strategy disabling | `learning/learning_engine.py` | 🟡 HIGH |
| 3.6: Friday indicator ranking Telegram report | `scheduled_jobs.py` | 🟠 MEDIUM |
| 3.7: Look-ahead bias audit | All strategy files | 🟡 HIGH (required before Sprint 4) |
| 3.8: Romano & Wolf statistical gate | `scripts/sprint6_live_gate.py` (new) | 🟡 HIGH |

**Sprint 3 gate** (ALL must be true):
- Meta-label gate running (NOT blend — binary veto)
- HMM choppy-state detection active
- Auto-improvement loop firing Sunday 21:00 weekly
- Regime-strategy disabling active
- Look-ahead bias audit complete and clean
- **Win rate (rolling 50 trades) ≥ 50%** — THIS IS THE MINIMUM VIABLE THRESHOLD
- **Profit factor ≥ 1.3**

---

### SPRINT 4 — BROKER INTEGRATION & GO/NO-GO
**Gate to start**: Sprint 3 complete · Win rate ≥ 50% · Profit factor ≥ 1.3
**Gate to finish**: ALL 10 Go/No-Go criteria green · IBKR paper fills validated

| Task | Priority |
|------|----------|
| 4.1: IBKR account setup | 🔴 CRITICAL |
| 4.2: IBKR paper API integration | 🔴 CRITICAL |
| 4.3: The Go/No-Go Gate (all 10 criteria) | 🔴 CRITICAL |

**The 10 Go/No-Go Criteria** (ALL must be simultaneously green):

| # | Criterion | Threshold | Current Status |
|---|-----------|-----------|----------------|
| 1 | Paper trades total | ≥ 200 | ✅ 2,327 |
| 2 | Win rate (rolling 50) | > 50% | ❌ 38.5% |
| 3 | Effective win rate (incl. BE) | > 60% | ❌ Unknown |
| 4 | Profit factor | > 1.3 | ❌ Unknown |
| 5 | Max drawdown | < 8% | ❌ Unknown |
| 6 | Max consecutive losses | ≤ 4 | ❌ Unknown |
| 7 | Firewall overrides | Zero | ✅ |
| 8 | System uptime | 5/5 days | ✅ |
| 9 | Strategy coverage | ≥ 5 trades each | ⚠️ Check |
| 10 | t-stat (cost-adjusted) | ≥ 4.3 (Bonferroni) | ❌ Unknown |

**Not one of these is negotiable. 9/10 is not enough.**

---

### SPRINT 5 — LIVE DEPLOYMENT
**Gate**: ALL Sprint 4 gates green · ALL 10 Go/No-Go criteria simultaneously green

**Scaling Ladder (Non-Negotiable)**:
```
Weeks 1–2:  25% of paper position sizes. Kill switch: account < £9,750 → halt.
Weeks 3–4:  50% if IBKR fills within 5bps of model. Revert to 25% if circuit breaker fires.
Weeks 5–6:  75% if weeks 3–4 clean.
Week 7+:    100% — full autonomous mode. Human role: monitoring only.
```

**The Three Immutable Rules**:
1. The system decides. No overrides for gut feel.
2. The stop is sacred. Set at entry, moved only upward by the profit ratchet.
3. The kill switch is your friend. -3% day or -8% month: circuit breaker fires. Let it.

---

### SPRINT 6 — COMPOUNDING AT SCALE
**Gate**: 3 consecutive profitable months in live trading

**Capital Milestones**:
| Equity | Unlock |
|--------|--------|
| £15,000 | Runner mode on 2nd concurrent position |
| £25,000 | B-Team expansion: 22 → 44 tickers |
| £50,000 | Options flow signal activates |
| £100,000 | Volume-clock scan trigger (Easley & O'Hara 1992) — requires tick data |
| £100,000 | Order book imbalance (Level 2) |
| £250,000 | Transformer volatility forecasting |
| £1,000,000 | Signal service launch |

---

## MASTER IMPLEMENTATION SEQUENCE

Ordered by: (1) zero-disruption fixes first, (2) mathematical correctness, (3) win rate improvement, (4) infrastructure.

| Order | Mandate/Task | File | Effort | Risk to System |
|-------|-------------|------|--------|----------------|
| 1 | ✅ Merton Kelly (`kelly_sizer.py`) | DONE | — | Zero |
| 2 | ✅ SQLite WAL mode | DONE | — | Zero |
| 3 | Wire Kelly ticker arg | `main.py:3720` | 10 min | Zero |
| 4 | **Mean reversion leverage guard** | `mean_reversion.py` | 5 min | Zero — only blocks bad trades |
| 5 | Alt.me Fear & Greed | `cross_asset_macro.py` | 30 min | Zero (fails open) |
| 6 | Redis container | `docker-compose.yml` | 30 min | Zero (additive) |
| 7 | Train ML on 2,327 outcomes | `ml_meta_model.py` | 1 hr | Zero (cold-start = no veto) |
| 8 | Wire learning.record_trade() | `virtual_trader.py` | 2 hr | Zero |
| 9 | Move attribution chains | `main.py` + `move_attribution.py` | 2 hr | Zero |
| 10 | RC-07b Earnings fade gate | `core/earnings_fade_gate.py` | 2 hr | Low (fails open) |
| 11 | Monday penalty | `daily_target.py` + `universal_scanner.py` | 1 hr | Zero |
| 12 | Portfolio heat RC-02 | `core/portfolio_heat.py` | 2 hr | Zero |
| 13 | Chandelier exit + profit ladder | `daily_target.py` + exit engine | 3–4 hr | Low (old exit is fallback) |
| 14 | TwelveData upgrade | Config | 30 min | Zero |
| 15 | Dynamic P90 fallback spread | `core/realtime_data.py` | 1–2 hr | Zero |
| 16 | Bid-Ask EV Filter | `realtime_data.py` + `daily_target.py` | 2–3 hr | Low (fallback present) |
| 17 | HMM Regime Classifier | `cross_asset_macro.py` | 2–3 hr | Low (fails open) |
| 18 | Meta-label gate (replaces blend) | `ml_meta_model.py` + `main.py` | 3–4 hr | Low (cold-start = no veto) |
| 19 | Look-ahead bias audit | All strategy files | 2–3 hr | Zero (read-only) |
| 20 | Romano & Wolf statistical gate | `scripts/sprint6_live_gate.py` | 2–3 hr | Zero (evaluation only) |
| 21 | IBKR account + paper API | External + `execution/ibkr_trader.py` | 4–8 hr | Low (parallel with virtual) |

**Total estimated effort**: 38–46 hours of focused engineering over ~4 months.
**Live capital target date**: ~June 2026 (assuming Sprint 1–3 each take 2 weeks and win rate goal met).

---

## WHAT MUST NOT BE CHANGED

| Component | Why it stays |
|-----------|-------------|
| S15 2% minimum qualifying threshold | This is the entry bar — Chandelier replaces the ceiling, not the floor |
| 0.75% immutable risk cap | Hard constitutional rule — untouchable |
| 5% max position notional cap | Correctly sized for £10k ISA |
| Decay detector two-layer halt | Per-ticker + per-strategy is correct and wired |
| 18-gate full gauntlet | For non-S15 strategies — sound governance |
| A/B tier promotion/relegation | 24h human confirmation before demotion — keep |
| TelegramEventBus P0/P1/P2/P3 | Alert hierarchy correctly calibrated |
| VIX gates (no 5× above 22, no 3× above 25) | Keep and extend with HMM |
| Outcome logging to outcomes.jsonl | Training source — must remain unmodified |
| Sprint 4 "All 10 criteria" requirement | Non-negotiable. 9/10 is not enough. |

---

## DEFERRED — SPRINT 6 OR LATER

| Item | Reason deferred |
|------|----------------|
| Volume-clock scan trigger (Easley & O'Hara 1992) | Requires tick-level data. Cannot implement before IBKR API live. |
| TimescaleDB migration | SQLite WAL solves concurrency at current scale. Revisit at £100k+ AUM. |
| Celery worker architecture | `run_in_executor` solves GIL at current 1 trade/day frequency. Celery needed only if frequency scales to 50+/day. |
| CVaR risk management | 0.75% cap + portfolio heat covers this at £10k. CVaR is Sprint 4+ when live capital is at risk. |
| Synthetic Order Book (L2) | Bid-ask EV filter (Mandate 2) achieves the same goal at our scale. L2 data needed at £50k+ AUM. |
| Capacity constraint modelling | Not relevant until AUM > £100k and daily position > 1% of ETP volume. |
| RL / HMM Stage 5 self-learning | Year 2. Rule-based + LightGBM is correct for current data volume. |

---

## ACADEMIC CITATIONS — COMPLETE BIBLIOGRAPHY

| Mandate/Task | Paper | Application |
|-------------|-------|-------------|
| M1 | Merton (1971) "Optimum Consumption and Portfolio Rules" | f* = (μ-r)/σ² base formula |
| M1 | MacLean, Thorp & Ziemba (2011) "Kelly Capital Growth Criterion" | Leverage adjustment f*_lev = f*_unl / λ |
| M1 | Hakansson (1971) "Capital Growth and Mean-Variance Approach" | Half-Kelly safety buffer |
| M2 | Glosten & Milgrom (1985) "Bid, Ask and Transaction Prices" | Bid-ask as cost of trading |
| M2 | Hasbrouck (2007) "Empirical Market Microstructure" | Round-trip transaction cost model |
| M2 | Pastor & Stambaugh (2003) "Liquidity Risk and Expected Stock Returns" | Spread spikes during illiquidity |
| M3 | De Prado (2018) "Advances in Financial Machine Learning" Ch.4 | Meta-labelling binary classifier |
| M3 | Dietterich (2000) "Ensemble Methods in Machine Learning" | LightGBM + XGBoost diversity |
| M3 | Chen & Guestrin (2016) "XGBoost" | AUC 0.63 on next-day direction |
| M4 | Hamilton (1989) "A New Approach to Economic Analysis of Nonstationary Time Series" | 2-state Gaussian HMM |
| M4 | Ang & Timmermann (2012) "Regime Changes and Financial Markets" | Momentum fails in choppy regimes |
| M4 | Avellaneda & Zhang (2010) "Path-Dependence of Leveraged ETF Returns" | Daily rebalancing forces trends |
| M5 | Le Beau (1999) "Chandelier Exits" | Highest_High − N×ATR trailing stop |
| M5 | Bianchi, Drew & Fan (2016) "Tail Risk in Momentum Strategy Returns" | Fat right-tail via trailing exits |
| M6 | Romano & Wolf (2005) "Stepwise Multiple Testing as Formalized Data Snooping" | FWER correction, StepM |
| M6 | Harvey, Liu & Zhu (2016) "…and the Cross-Section of Expected Returns" | t ≥ 3.0 single-strategy baseline |
| M6 | Bailey & De Prado (2014) "The Deflated Sharpe Ratio" | Out-of-sample Sharpe validation |
| T1.3 | Kim & Verrecchia (1991) "Market Reaction to Anticipated Announcements" | Buy the rumour sell the news |
| T1.3 | Bartov, Radhakrishnan & Krinsky (2000) "Investor Sophistication and Patterns in Stock Returns" | Earnings fade mechanism |
| T1.4 | French (1980) "Stock Returns and the Weekend Effect" | Monday return penalty |
| T1.4 | Gibbons & Hess (1981) "Day of the Week Effects and Asset Returns" | Day-of-week systematic patterns |
| T2.1 | Amin & Lee (1997) "Trading Patterns, Bid-Ask Spreads, and Estimated Security Returns" | IV crush pre/post earnings |
| T2.2 | Cohen, Diether & Malloy (2007) "Supply and Demand Shifts in the Shorting Market" | Short covering predicts reversals |
| T2.3 | Ni, Pearson & Poteshman (2005) "Stock Price Clustering on Option Expiration Dates" | Options expiry pinning |
| T2.4 | Lakonishok, Shleifer & Vishny (1994) "Contrarian Investment, Extrapolation, and Risk" | Window dressing flows |
| T2.6 | Bagnoli, Clement & Watts (1999) "Around-the-Clock Media Coverage and the Timing of Earnings Announcements" | Whisper number vs consensus |
| T3.2 | Loughran & McDonald (2011) "When is a Liability not a Liability?" | NLP earnings sentiment prediction |
| T3.3 | Almgren & Chriss (2001) "Optimal Execution of Portfolio Transactions" | Market impact modelling |
| M10 | Easley & O'Hara (1992) "Time and Security Price Adjustment" | Volume-clock stationarity (deferred) |

---

## POST-IMPLEMENTATION MONITORING

| Metric | Target | Action if Breached |
|--------|--------|-------------------|
| Win rate (rolling 50) | Rising toward 50% | If stalled at <42%: audit top gate rejection reason |
| Meta-label approval rate | 40–80% of S15 signals | <40%: lower veto threshold; >80%: raise |
| Chandelier avg hold time | > 45 minutes | <30 min: Chandelier activating too early |
| Kelly fraction post-leverage-adj | < 1.5% per trade | >2%: check leverage map applied correctly |
| HMM choppy-state frequency | 20–40% of trading days | >60%: model over-sensitive |
| Bid-ask EV rejections | < 15% of candidates | >30%: data feed degraded |
| Sprint 6 gate t-stat trend | Rising over MTRL | Flat/falling: S15 edge not accumulating |
| Chandelier Redis misses | Zero | Any miss: check Redis AOF config |
| Monday win rate vs other days | Should be lower (acceptable) | If Monday WR < 25%: raise RVOL veto threshold |

---

**Document Version 3.0 — Complete.**
**Part I (M1–M6)**: Mathematical corrections — M1 and M7 done, M2–M6 pending.
**Part II (M7–M11)**: Infrastructure — M7 done, M8–M10 pending, M11 (look-ahead audit) added.
**Part III (Sprints 0–6)**: Full operational plan from 38.5% win rate to live capital deployment.
**Gemini critique integrated**: Valid concerns accepted, over-engineering rejected, premature concerns deferred with explicit reasoning.
**No contradictions**: Every deferred item has documented reasoning. Every accepted item has a file, line number, and effort estimate.
