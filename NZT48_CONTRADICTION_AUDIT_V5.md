# NZT-48 TRADING ENGINE — COMPLETE CONTRADICTION AUDIT
## Principal Systems Auditor Report | 2026-03-03
### Directive: "One Brain, One Truth, Zero Emotion" Pre-Architecture Extraction

---

# PART 1: THE DEEP-SCAN CONTRADICTION AUDIT

---

## 1. PARAMETER FRAGMENTATION

### 1.1 TICKER UNIVERSE DEFINITIONS — 4 COMPETING SOURCES

#### Source A: `uk_isa/isa_universe.py:25-38` (CANONICAL)
```python
CORE_UNIVERSE: list[str] = [
    "QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L", "TSL3.L",
    "TSM3.L", "MU2.L", "QQQS.L", "3USS.L", "QQQ5.L", "SP5L.L",
]  # 12 tickers
```

#### Source B: `config/settings.yaml:1089-1100` (EXPANDED — CLASHES)
```yaml
isa_tickers_v2:
  core_long: ["QQQ3.L", "3LUS.L", "3SEM.L", "GPT3.L", "NVD3.L",
              "TSL3.L", "TSM3.L", "MU2.L", "QQQ5.L", "SP5L.L"]
  core_inverse: ["QQQS.L", "3USS.L", "SC3S.L", "GPTS.L", "3SNV.L",
                 "3STS.L", "TSMS.L", "SQQQ.L", "SPYS.L"]
```
**CLASH:** settings.yaml `core_long + core_inverse = 19 tickers`. isa_universe.py = 12 tickers. **Delta = 7 extra inverse tickers** (SC3S.L, GPTS.L, 3SNV.L, 3STS.L, TSMS.L, SQQQ.L, SPYS.L) that exist in YAML but NOT in Python CORE_UNIVERSE.

#### Source C: `config/universe.yaml:7-19` (ALIGNED)
```yaml
universe:
  core_list:
    - QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L,
      QQQS.L, 3USS.L, QQQ5.L, SP5L.L   # 12 — matches isa_universe.py
```

#### Source D: `strategies/daily_target.py` (IMPLICIT — imports from isa_universe)
Imports `CORE_UNIVERSE` from `uk_isa.isa_universe`. Aligned.

#### Source E: `strategies/universal_scanner.py` (EXPANDS with US A-Team/B-Team)
Adds US individual stocks:
- A-Team: NVDA, AMD, TSM, MSFT, AAPL, GOOGL, META, AMZN, MU, SMCI
- B-Team: TSLA, PLTR, COIN, MSTR, GME, AMC

**VERDICT:** `isa_universe.py` is canonical for ISA. `settings.yaml` has 7 orphan inverse tickers.

---

### 1.2 EXTENDED UNIVERSE — ALIGNED

| Source | Count | Tickers |
|--------|-------|---------|
| `uk_isa/isa_universe.py:44-55` | 22 | CORE_UNIVERSE + AMD3.L, ARM3.L, NVDS.L, TSLS.L, 3LDE.L, 3LEU.L, 3GOL.L, 3SIL.L, 3OIL.L, LLY3.L |
| `config/universe.yaml:28-39` | 10 peers | Same 10 tickers as extension |

Aligned. No clash.

---

### 1.3 FULL SCAN / EXPANSION UNIVERSE — CLASH

| Source | Count | Definition |
|--------|-------|------------|
| `uk_isa/isa_universe.py:85-87` | 35 | `EXTENDED_UNIVERSE + SECTOR_RADAR_UNIVERSE` (deduped) |
| `config/settings.yaml` core_expansion_v2 + v3 | 40 | 20+20 verified ETPs |

**CLASH:** Python says 35, YAML says 40. **5 tickers in YAML not in Python**: 3LNG.L, SILV.L, NGAS.L, SLV3.L, WSLV.L.

---

### 1.4 SPREAD & SLIPPAGE — CRITICAL CONFLICT

#### Static Spread Table: `uk_isa/isa_universe.py:300-340`
```python
SLIPPAGE_MODEL: dict = {
    "default_bps": 5,
    "spread_bps": {
        "QQQ3.L":  8,    "QQQ5.L":  10,   "SP5L.L":  8,
        "3LUS.L":  8,    "QQQS.L":  10,   "3USS.L":  10,
        "3SEM.L":  12,   "GPT3.L":  12,   "NVD3.L":  12,
        "TSL3.L":  15,   "TSM3.L":  10,   "MU2.L":   10,
        # ... sector radar tickers at 20 bps
    },
}
```

#### Cost Model Defaults: `execution/cost_model.py:37-56`
```python
# Line 37-44:
try:
    from uk_isa.isa_universe import SLIPPAGE_MODEL
    SPREAD_BPS: dict[str, float] = {
        k: float(v) for k, v in SLIPPAGE_MODEL.get("spread_bps", {}).items()
    }
    _DEFAULT_SPREAD_BPS = float(SLIPPAGE_MODEL.get("default_bps", 20))  # Fallback = 20!
except ImportError:
    SPREAD_BPS = {}
    _DEFAULT_SPREAD_BPS = 20.0  # Fallback if import fails

# Line 45-50:
_SLIPPAGE_BPS_PER_SIDE = 5.0
_PLATFORM_FEE_BPS = 2.0
SPREAD_WATCH_THRESHOLD_BPS  = 22.0
SPREAD_VETO_THRESHOLD_BPS   = 32.0
```

**CRITICAL CLASH:**
- `isa_universe.py` default = **5 bps**
- `cost_model.py` fallback (line 41) loads `SLIPPAGE_MODEL.get("default_bps", 20)` → if key missing, **20 bps**
- If `uk_isa` import fails entirely → **20 bps** hardcoded fallback
- Same QQQ3.L trade: **17 bps total** (with import) vs **29 bps total** (without import) — crosses WATCH threshold (22 bps)

#### Signal Engine Hardcoded Spreads: `signal_engine/engine.py`
```python
# Cost model: spreads 15-22 bps (.L tickers), slippage 5 bps/side
```
Comment says 15-22 bps for .L tickers, but actual isa_universe ranges from 8-20 bps. **Stale comment.**

---

### 1.5 LEVERAGE FACTORS — TRIPLE DEFINITION (ALIGNED)

| Source | Location |
|--------|----------|
| `uk_isa/isa_universe.py:188-226` | `LEVERAGE_MAP` dict — canonical |
| `config/settings.yaml:152-177` | `leveraged_4x_5x` list — informational |
| `config/holdings.yaml` | Holdings decomposition — metadata only |

Values aligned across all three. No clash detected.

---

### 1.6 POSITION SIZING (RISK PER TRADE) — FALLBACK DIVERGENCE

| Location | Value | Context |
|----------|-------|---------|
| `config/settings.yaml:678` | `0.0075` (0.75%) | Immutable rules — canonical |
| `qualification/dynamic_sizer.py:55` | `0.0075` (0.75%) | `_IMMUTABLE_MAX_RISK_PCT` — aligned |
| `qualification/risk_sizer.py:38` | `0.0075` (0.75%) | `RISK_PER_TRADE` — aligned |
| **`bots/kelly_sizer.py:126`** | **Default `0.005` (0.5%)** | `kelly_cfg.get("default_risk", 0.005)` — **CONFLICT** |
| `config/settings.yaml:997` | `recalc_interval: 20` | Kelly config |
| **`bots/kelly_sizer.py:126`** | **Default `recalc_interval: 10`** | `kelly_cfg.get("recalc_interval", 10)` — **CONFLICT** |

**CLASH 1:** Kelly sizer uses `0.005` (0.5%) as fallback when no trade data. The immutable rule says 0.75%. For the first 30 trades, the system sizes at **33% less** than intended.

**CLASH 2:** Kelly recalc interval defaults to 10 trades in code, but settings.yaml says 20. If YAML key is ever missing, recalc doubles in frequency.

---

### 1.7 STOP-LOSS RULES — 9 COMPETING DEFINITIONS

| Strategy File | Constant | Value | Line |
|---------------|----------|-------|------|
| `strategies/regime_trend.py` | `_STOP_ATR_MULT` | **1.5** | ~61 |
| `strategies/momentum_breakout.py` | `_STOP_ATR_MULT` | **1.5** | ~66 |
| `strategies/hot_scanner.py` | `_STOP_ATR_MULT` | **1.5** | ~59 |
| `strategies/ai_thematic.py` | `_STOP_ATR_MULT` | **1.5** | ~67 |
| `strategies/trend_compound.py` | `_STOP_ATR_MULT` | **1.5** | ~56 |
| `strategies/rebalance_flow.py` | `_STOP_ATR_MULT` | **1.5** | ~77 |
| `strategies/daily_target.py` | `_STOP_ATR_MULT` | **1.5** | ~241 |
| `strategies/mean_reversion.py` | `_STOP_ATR_MULT` | **1.0** | ~68 |
| `strategies/gamma_squeeze.py` | `_STOP_ATR_MULT` | **1.0** | ~58 |
| `strategies/pead_earnings.py` | `STOP_ATR_MULT` | **2.0** | custom |

**FRAGMENTATION:** Same ticker can receive 3 different stop distances:
- Via momentum_breakout: 1.5x ATR
- Via mean_reversion: 1.0x ATR
- Via pead_earnings: 2.0x ATR

Per-ticker overrides exist in `config/settings.yaml` (bot_b_universe.overrides) but only for US equities, NOT for ISA ETPs.

#### Signal Engine Stop Fractions: `signal_engine/engine.py`
```python
# Stop/target fractions by setup type:
# Continuation: 0.40x ATR stop
# Breakout:     0.35x ATR stop
# Mean revert:  0.60x ATR stop
# Default:      0.50x ATR stop
```
**These are DIFFERENT from strategy-level constants.** Signal engine uses fractional ATR (0.35-0.60x), strategies use integer multiples (1.0-2.0x). **Potential double-counting if both are applied.**

---

### 1.8 INDICATOR THRESHOLD GATES — 4 DIFFERENT ATR% MINIMUMS

| File | Value | Purpose |
|------|-------|---------|
| `strategies/daily_target.py:64` | `_MIN_ATR_PCT = 0.8%` | S15 feasibility gate |
| `strategies/opportunity_scanner.py:23` | Min ATR = `3.0%` | 2% net-after-fees feasibility |
| `signal_engine/gates.py` | ATR% = `1.0%` strict, `0.6%` step-4 fallback | Engine tradability gate |
| `delivery/pdf_v2_momentum.py` | `1.0%` | PDF "too quiet" warning |

**CLASH:** A ticker with 0.9% ATR passes S15's gate (0.8%) but fails the signal engine strict gate (1.0%). Ticker with 0.7% ATR fails S15 but passes engine step-4 fallback (0.6%).

---

### 1.9 SPREAD VETO MAGIC NUMBER

| Location | Value |
|----------|-------|
| `execution/cost_model.py:50` | `SPREAD_VETO_THRESHOLD_BPS = 32.0` |
| `main.py` (execution bridge) | `if exec_plan.spread_proxy_bps > 32:` — hardcoded |

**CLASH:** Magic number `32` in main.py is not imported from cost_model.py. If cost_model updates to 35, main.py still uses 32.

---

## 2. CHRONOLOGICAL FRAGMENTATION

### 2.1 FOUR DATETIME MECHANISMS IN USE

| Mechanism | Count | Files |
|-----------|-------|-------|
| `datetime.now(ZoneInfo(...))` | 45+ | main.py, tick_loop.py, daily_target.py, universal_scanner.py, session_manager.py, virtual_trader.py |
| `datetime.utcnow()` | 7 | main.py:449, short_interest_feed.py, short_squeeze_monitor.py, indicator_ranking_report.py, param_sweep.py, walkforward_stress.py |
| `datetime.now()` (naive) | 30+ | scheduled_jobs.py, dashboard/api.py, scripts/ |
| `time.time()` (epoch) | 60+ | news_feed.py, tick_loop.py, chandelier_exit.py, trading_discipline.py, telegram_bot.py |

---

### 2.2 CRITICAL BUG: ULYSSES LOCK — UTC vs UK

**File:** `main.py:442-455`
```python
async def enforce_read_only_market_hours(request, call_next):
    from datetime import time as dtime
    now = datetime.utcnow().time()            # ← UTC time
    if request.method in ("POST", "PUT", "DELETE"):
        if dtime(8, 0) <= now <= dtime(16, 30):  # ← Compared as if UK time
            if "emergency_halt" not in request.url.path:
                raise HTTPException(status_code=403, detail="MARKET_HOURS_FREEZE")
```

**BUG:** Compares UTC time to hardcoded 8:00-16:30 as if it were London time.
- During BST (March-October): UTC 07:00 = London 08:00. Middleware freezes 1 hour early.
- During BST: UTC 15:30 = London 16:30. Middleware unfreezes 1 hour early.

**Same bug in FrozenConfig** at `main.py:476-481`:
```python
def get(self, key, default=None):
    from datetime import time as dtime
    now = datetime.now(timezone.utc).time()   # ← UTC
    if dtime(8, 0) <= now <= dtime(16, 30) and self._frozen_config:
        return self._frozen_config.get(key, default)
```

---

### 2.3 CRITICAL BUG: LSE OPEN HOUR WRONG

**File:** `strategies/universal_scanner.py:54`
```python
_LSE_OPEN_HOUR = 9     # ← WRONG. LSE opens at 08:00 UK, not 09:00
```

**Correct value** per `delivery/dst_anchor.py:41`:
```python
_LSE_OPEN_UK  = time(8,  0)   # ← CORRECT
```

**Impact:** S16 Universal Scanner misses the entire 08:00-09:00 UK window for LSE signals.

---

### 2.4 CRITICAL BUG: 5x OVERNIGHT KILL USES UTC

**File:** `execution/virtual_trader.py:885-888`
```python
# 5x overnight kill: if ticker is 5x and past 16:15 UTC, force close
if pos.ticker in FIVE_X_TICKERS and pos.bot_instance != "SWING":
    now_utc = datetime.now(timezone.utc)
    past_1615 = (now_utc.hour > 16) or (now_utc.hour == 16 and now_utc.minute >= 15)
```

**BUG:** Uses UTC for a 16:15 check. LSE closes at 16:30 UK. During BST, UTC 16:15 = UK 17:15 — this fires **45 minutes too late** in summer.

---

### 2.5 TIMEZONE CONSTANT EXPLOSION — 7 INDEPENDENT DEFINITIONS

| File | Variable Name | Value |
|------|---------------|-------|
| `feeds/data_validator.py:44` | `_ET` | `ZoneInfo("America/New_York")` |
| `command_center/tick_loop.py:54` | `_UK` | `ZoneInfo("Europe/London")` |
| `strategies/universal_scanner.py:52-53` | `_UK_TZ`, `_US_TZ` | Europe/London, America/New_York |
| `strategies/daily_target.py:272` | `_UK_TZ` | `ZoneInfo("Europe/London")` |
| `execution/session_manager.py:55-56` | `ET`, `UK` | America/New_York, Europe/London |
| `core/intraday_momentum.py:52-53` | `ET_ZONE`, `UK_ZONE` | America/New_York, Europe/London |
| `delivery/dst_anchor.py:36-37` | `_ET`, `_UK` | America/New_York, Europe/London |
| `qualification/dynamic_sizer.py:47` | `self._et_tz` | `ZoneInfo("America/New_York")` |

**No centralised timezone module.** Every file defines its own constants.

---

### 2.6 MARKET HOURS CONSTANTS — FRAGMENTED

| File | Open | Close | Timezone |
|------|------|-------|----------|
| `feeds/data_validator.py:46-47` | 9:30 | 16:00 | ET (US) |
| `execution/smart_routing.py:94,99` | 9:30 | 16:00 | ET (US) |
| `feeds/indicators.py:29-30` | 9:30 | — | ET (US) |
| `delivery/dst_anchor.py:41-42` | 8:00 | 16:30 | UK (LSE) |
| `strategies/universal_scanner.py:54-61` | **9:00** | 15:15 | UK (LSE) — **WRONG OPEN** |
| `signal_engine/strategy_router.py` | 8:00-8:30 | 16:00-16:30 | UK (LSE sessions) |

---

### 2.7 `datetime.utcnow()` DEPRECATION

Python 3.12+ deprecated `datetime.utcnow()`. Used in 7 locations. Will raise warnings or fail on Python 3.13+.

---

## 3. STATE SCHIZOPHRENIA

### 3.1 POSITION TRACKING — 3 SOURCES OF TRUTH

| Source | Type | Location | Canonical? |
|--------|------|----------|------------|
| `VirtualTrader.open_positions` | `dict[str, VirtualPosition]` | `execution/virtual_trader.py:280` | YES (runtime) |
| `virtual_positions` table | SQLite | `delivery/database.py:536-750` | YES (persistence) |
| Redis `chandelier:{ticker}:{trade_id}` | Redis hash | `core/chandelier_exit.py:86-136` | Profit ladder only |

**SYNC MECHANISM:**
- On position open: INSERT into SQLite (line 1567-1600)
- Periodic updates: UPDATE SQLite (line 1602-1619)
- On position close: DELETE from dict (line 1500), THEN INSERT into `virtual_trades` (line 1504)

**VULNERABILITY:** Between lines 1500 and 1504, the position exists in **neither** dict nor DB. If DB insert fails, position data is lost.

---

### 3.2 P&L TRACKING — DUAL CALCULATION WITH NO RECONCILIATION

| Mechanism | Location | When Updated |
|-----------|----------|--------------|
| `VirtualTrader.daily_pnl` (in-memory) | `virtual_trader.py:282` | Every trade close: `+= net_pnl` |
| `VirtualTrader.equity` (in-memory) | `virtual_trader.py:284` | Every trade close: `+= net_pnl` |
| `SELECT SUM(net_pnl) FROM virtual_trades` | `database.py:956-1005` | On-demand query |
| `equity_intraday` table | `main.py:7465` | Hourly snapshot from in-memory |

**CRITICAL:** Main loop reads DB P&L at `main.py:1058` but then overwrites with `self.virtual_trader.equity` at `main.py:1101`. **In-memory always wins.** No cross-validation.

---

### 3.3 PROFIT LADDER — TRIPLE DIVERGENCE RISK

| Source | Location | Scope |
|--------|----------|-------|
| `ChandelierState._states` dict | `core/chandelier_exit.py` | In-memory |
| Redis `chandelier:*` keys | `core/chandelier_exit.py:118-126` | 24h TTL |
| `VirtualPosition.ladder_rung` | `execution/virtual_trader.py` | Per-position |

If Redis restarts, ladder state falls back to in-memory (which may be stale from a previous session if VirtualTrader was also restarted). DB `virtual_positions.ladder_rung` is the persistence backup but is not used for rehydration.

---

### 3.4 P&L KILL SWITCH — IN-MEMORY ONLY

**Location:** `execution/virtual_trader.py:304-323`

```python
# Kill switch reads from self._all_trades (in-memory only, never truncated)
# self.closed_trades is capped at 1000 entries
# After 1000 trades: kill switch sees full history, reporting sees truncated
```

**No database cross-check.** If engine restarts mid-session, kill switch loses all trade history.

---

### 3.5 JSON ARTIFACT STATE

Artifacts (`plays.json`, `drought.json`, `system_state.json`, `risk_officer.json`) are written atomically (tempfile + rename). These are **read-only snapshots** — not sources of truth. No state inconsistency risk.

---

### 3.6 CSV LOGGING

**None found.** No CSV state files in the codebase.

---

## 4. DATA FEED OVERLAPS

### 4.1 OHLCV DATA — 4 REDUNDANT SOURCES

| Priority | Source | Key | LSE? | US? | Limit |
|----------|--------|-----|------|-----|-------|
| 1 (LSE) | TwelveData | `TWELVEDATA_API_KEY` | YES | Fallback | 8 calls/min |
| 1 (US) | yfinance | None | Fallback | YES | ~2000 req/hr |
| 2 | FMP | `FMP_KEY` | YES | YES | 250/day |
| 3 | Alpha Vantage | `ALPHA_VANTAGE_KEY` | YES | YES | **25/day** |

**Priority chains:**
- `.L` tickers: TwelveData → yfinance → FMP → Alpha Vantage
- US tickers: yfinance → TwelveData → FMP → Alpha Vantage

**File:** `feeds/data_feeds.py:275-370` (chain implementation)

**ISSUE:** No shared cache across sources. Same OHLCV bar may be fetched 3+ times during fallback cascade. Alpha Vantage's 25/day limit can exhaust in minutes during heavy fallback.

---

### 4.2 REAL-TIME PRICES — 5 REDUNDANT SOURCES

| Priority | Source | File | Latency |
|----------|--------|------|---------|
| 1 (US) | Polygon | `core/realtime_data.py:162` | Real-time |
| 1 (LSE) | TwelveData | `core/realtime_data.py:156` | ~15s |
| 2 | yfinance `fast_info` | `core/realtime_data.py:284-296` | 15-20min |
| 3 | Alpha Vantage | `core/realtime_data.py:255-279` | 15-20min |
| 4 | FMP | `feeds/data_feeds.py:403-446` | Delayed |

**File:** `core/realtime_data.py` — parallel to `feeds/data_feeds.py`. Two separate modules fetch prices independently.

---

### 4.3 EARNINGS CALENDAR — 2 REDUNDANT SOURCES

| Source | File | Endpoint |
|--------|------|----------|
| Finnhub | `feeds/calendar_feed.py:117-150` | `/calendar/earnings` |
| yfinance | `feeds/calendar_feed.py` (fallback) | `Ticker.calendar` |
| ForexFactory | `feeds/calendar_feed.py:153-201` | Web scraper (macro only) |

No deduplication between Finnhub and yfinance for same ticker.

---

### 4.4 NEWS/SENTIMENT — 3 SOURCES

| Source | File | Limit |
|--------|------|-------|
| NewsAPI | `feeds/news_feed.py:278-354` | 100/day (free) |
| Yahoo Finance RSS | `feeds/news_feed.py:281-309` | No limit |
| Seeking Alpha RSS | `feeds/news_feed.py:281-309` | No limit |

RSS sources are fallback only.

---

### 4.5 MARKET STRUCTURE — SINGLE POINTS OF FAILURE

| Data | Source | Fallback |
|------|--------|----------|
| VIX | yfinance `^VIX` | **NONE** |
| GEX/DIX | SqueezeMetrics scraper | Returns neutral defaults (GEX=0, DIX=0.45) |
| Hot stocks | Finviz screener | **NONE** |
| Short interest | FINRA CDN | **NONE** |
| Fear & Greed | alternative.me API | **NONE** |

---

### 4.6 DORMANT/UNUSED API KEYS

| Key | Status | Evidence |
|-----|--------|----------|
| `FRED_API_KEY` | Set in `.env` | **No active code calls found** |
| CBOE | Listed "enabled" in settings.yaml | **VIX fetched via yfinance only** |
| `GEMINI_API_KEY` | Set in environment | Used for earnings sentiment + AI research |

---

## 5. CONCURRENCY CLASHES

### 5.1 APScheduler — 68+ REGISTERED JOBS

**Scheduler type:** `AsyncIOScheduler` (timezone="Europe/London")
**Event loop:** Single `asyncio.run()` at `main.py:7822`

#### CRITICAL: Continuous Scan Has NO Instance Protection

**File:** `main.py:5018-5024`
```python
scheduler.add_job(
    self.run_scan,
    "interval",
    seconds=60,
    id="continuous_24_7",
    # NO max_instances
    # NO coalesce
)
```

**Impact:** At 06:00 UK, both `pre_market` (cron) and `continuous_24_7` (interval) call `run_scan()` simultaneously. No mutex.

#### Cron Scan Overlap Windows

```
06:00 UK: pre_market + continuous_24_7
08:00 UK: lse_open + continuous_24_7
09:00 UK: premarket_movers + continuous_24_7
14:30 UK: us_open + continuous_24_7
15:00 UK: midsession_setups + continuous_24_7
16:00 UK: mid_session + continuous_24_7
17:00 UK: afternoon_push + continuous_24_7
19:00 UK: rebalance + continuous_24_7
20:30 UK: late_session + continuous_24_7
```

**Result:** Minimum 2 concurrent `run_scan()` calls at every cron boundary.

---

### 5.2 SIGNAL QUEUE — UNHANDLED OVERFLOW

**File:** `main.py:1028`
```python
self._signal_queue: Queue = Queue(maxsize=50)
```

**Put sites (NO exception handling):**
- `main.py:2929`: `self._signal_queue.put_nowait(signal_dict)`
- `main.py:4045`: `self._signal_queue.put_nowait(signal_dict)`
- `main.py:4268`: `self._signal_queue.put_nowait(signal_dict)`

**BUG:** `put_nowait()` raises `queue.Full` if queue has 50 items. No try/except. Scan aborts.

---

### 5.3 FIRE-AND-FORGET ASYNC TASKS

**File:** `main.py:7240`
```python
asyncio.create_task(self.telegram.send_alert(_wiring_msg))  # Not awaited, not tracked
```

**File:** `main.py:7726`
```python
asyncio.create_task(_heartbeat_loop())  # Not awaited, not tracked
```

If either task crashes, no restart mechanism. No error logging for untracked tasks.

---

### 5.4 BLOCKING `time.sleep()` IN ASYNC CONTEXT

| File | Line | Duration | Impact |
|------|------|----------|--------|
| `feeds/news_feed.py:308` | Variable | Rate limit | **Blocks event loop** |
| `feeds/news_feed.py:421` | Variable | Retry backoff | **Blocks event loop** |
| `feeds/data_feeds.py:1188` | Variable | Retry wait | **Blocks event loop** |
| `learning/outcomes_engine.py:71` | 2^attempt (1-4s) | Retry backoff | **Blocks event loop** |

These should be `await asyncio.sleep()`.

---

### 5.5 NESTED `asyncio.run()` INSIDE EVENT LOOP

**File:** `main.py:6363`
```python
asyncio.run(update)  # CANNOT create new event loop inside existing loop
```

**BUG:** RuntimeError on Python 3.10+. Should use `await`.

---

### 5.6 NIGHTLY JOBS WITHOUT INSTANCE PROTECTION

| Line | Job | Has `max_instances=1`? |
|------|-----|-----------------------|
| 4763 | `_run_nightly_intelligence` | **NO** |
| 4771 | `_run_go_nogo_check` | **NO** |
| 4851 | `_run_pre_close_audit` | **NO** |
| 4859 | `_run_daily_reset` | **NO** |
| 4868 | `_force_close_eod` | **NO** |
| 4878 | `_force_close_lse` | **NO** |
| 5000 | `_refresh_lse_registry` | **NO** |

If any of these exceeds its scheduled interval, duplicates fire.

---

### 5.7 THREADING LOCKS — 16 INSTANCES

All follow `with self._lock:` pattern. No nested lock acquisition detected. **Deadlock risk is LOW.**

| File | Lock | Guarded Resource |
|------|------|-----------------|
| `feeds/data_feeds.py:73` | `_av_lock` | Alpha Vantage rate counter |
| `feeds/correlation_matrix.py:209` | `self._lock` | Correlation cache |
| `feeds/data_validator.py:159` | `self._lock` | Validation state |
| `feeds/correlation_matrix.py:671` | `_INSTANCE_LOCK` | Singleton |
| `uk_isa/universe_manager.py:568` | `_INSTANCE_LOCK` | Singleton |
| `qualification/dynamic_sizer.py:161` | `self._lock` | Size mutations |
| `delivery/telegram_bot.py:70,119,185` | `self._lock` (x3) | Dedupe, state, rate limit |
| `bots/bot_base.py:59` | `self._lock` | Bot state |
| `command_center/copilot/router.py:69` | `self._lock` | Copilot state |
| `command_center/copilot/throttling.py:37` | `self._lock` | Query throttle |
| `core/data_retention.py:55` | `_ring_lock` | Ring buffer |
| `core/provenance.py:218,431` | `self._lock` (x2) | Provenance tracker |
| `core/data_health_provider.py:73` | `self._lock` | Health state |
| `core/scan_health.py:45` | `self._lock` | Scan SLA |
| `learning/adaptive_engine.py:144` | `self._lock` | Playbook |

---

### 5.8 TICK LOOP vs SCHEDULER RACE

```
Tick loop: 30s interval (async) — runs in main event loop
APScheduler: runs in SAME event loop (AsyncIOScheduler)
Both call run_scan() — no mutual exclusion
```

**Worst case at 14:30 UK (US open overlap):**
1. Tick loop fires scan at T+0
2. `us_open` cron fires at T+0
3. `continuous_24_7` fires at T+0
4. `midsession_setups` fires at T+0 (if scheduled close)
5. **4 concurrent `run_scan()` invocations**

---

# PART 2: THE PHYSICAL ARCHITECTURE

---

## Project Directory Tree

```
nzt48-signals/
├── main.py                          # 7826 lines — master orchestrator
├── models.py                        # Enums, dataclasses, Signal, Position, Trade
├── exceptions.py                    # NZTGateError hierarchy
├── scheduled_jobs.py                # 440 lines — PDF generation + Telegram delivery
├── system_watchdog.py               # 464 lines — health state machine
├── docker-compose.yml               # 3 services: engine, dashboard, redis
├── deploy.sh                        # Deployment script
│
├── config/
│   ├── settings.yaml                # 993 lines — master config
│   ├── universe.yaml                # 115 lines — 3-tier universe
│   ├── holdings.yaml                # 536 lines — ETP constituent decomposition
│   ├── secrets.py                   # 131 lines — env var management
│   └── change_log.py               # 101 lines — config audit trail
│
├── uk_isa/                          # UK ISA Intelligence Engine (12 files)
│   ├── isa_universe.py              # 522 lines — CANONICAL ticker/leverage/spread source
│   ├── universe_manager.py          # 601 lines — thread-safe singleton
│   ├── lse_registry.py              # 452 lines — 102-product LSE catalog
│   ├── sector_rotation.py           # 673 lines — 10 sector groups
│   ├── peer_finder.py               # 692 lines — 3-axis similarity scoring
│   ├── predictive_scoring.py        # 1134 lines — 0-100 institutional scoring
│   ├── gate_diagnostics.py          # 655 lines — S15 retroactive replay
│   ├── multiframe_analytics.py      # 364 lines — 1M/3M/6M/1Y analytics
│   ├── volatility_regime.py         # 416 lines — 5-state regime classifier
│   ├── data_health.py               # 668 lines — OHLCV validation
│   ├── correlation_engine.py        # 1019 lines — cross-asset correlation + patterns
│   └── __init__.py
│
├── command_center/                  # Realtime Dashboard (11 files)
│   ├── tick_loop.py                 # 1418 lines — 30/120s tick, sniper, microstructure
│   ├── server.py                    # 2544 lines — FastAPI War Room, 30+ endpoints
│   ├── state.py                     # 671 lines — singleton state container
│   ├── diff.py                      # 196 lines — tick delta engine
│   ├── copilot/
│   │   ├── router.py               # 291 lines — NL query dispatch
│   │   ├── handlers.py             # 983 lines — intent handlers
│   │   ├── intents.py              # 213 lines — regex intent classification
│   │   ├── evidence.py             # 248 lines — artifact resolver
│   │   └── throttling.py           # 89 lines — query rate limiter
│   └── __init__.py
│
├── execution/                       # Execution Layer (10 files)
│   ├── virtual_trader.py            # 1754 lines — paper execution engine
│   ├── session_manager.py           # 1459 lines — market session boundaries
│   ├── smart_routing.py             # 967 lines — liquidity + impact assessment
│   ├── exit_engine.py               # 570 lines — exit scoring
│   ├── cost_model.py                # 322 lines — Almgren-Chriss cost model
│   ├── ibkr_gateway.py              # 223 lines — IBKR TWS wrapper
│   ├── planner.py                   # 143 lines — execution plan builder
│   ├── order_rules.py               # 82 lines — cancel/replace rules
│   ├── adaptive_twap.py             # 90 lines — TWAP execution
│   └── __init__.py
│
├── strategies/                      # 16 Strategies + 3 Utilities (20 files)
│   ├── daily_target.py              # S15 — 2% daily compounding machine
│   ├── universal_scanner.py         # S16 — multi-setup scanner (40+ tickers)
│   ├── regime_trend.py              # S1 — trend following
│   ├── momentum_breakout.py         # S2 — BB squeeze breakout
│   ├── mean_reversion.py            # S3 — DORMANT in V2
│   ├── catalyst_narrative.py        # S4 — news-driven
│   ├── pead_earnings.py             # S5 — post-earnings drift
│   ├── macro_regime.py              # S6 — macro regime shift
│   ├── sector_rotation.py           # S7 — weekly sector ranking
│   ├── vol_crush.py                 # S8 — VIX spike recovery
│   ├── pairs_trade.py               # S9 — market-neutral pairs
│   ├── ai_thematic.py               # S10 — AI sector theme
│   ├── hot_scanner.py               # S11 — pre-market gappers
│   ├── rebalance_flow.py            # S12 — ETP sponsor rebalancing
│   ├── trend_compound.py            # S13 — swing trend compounding
│   ├── gamma_squeeze.py             # S14 — gamma/options flow
│   ├── opportunity_scanner.py       # +2% feasibility scanner (utility)
│   ├── b_team_manager.py            # A/B/C team promotion/relegation
│   ├── base.py                      # StrategyBase abstract class
│   └── __init__.py
│
├── signal_engine/                   # Signal Pipeline (12 files)
│   ├── engine.py                    # 874 lines — strict + fallback two-layer
│   ├── gates.py                     # 318 lines — hard + soft gate funnel
│   ├── scoring.py                   # 336 lines — 6-component PlayScore 0-100
│   ├── signal_card.py               # 400+ lines — canonical signal schema
│   ├── strategy_router.py           # 300+ lines — 8-state regime routing
│   ├── pipeline_runner.py           # 350+ lines — tiered pipeline runner
│   ├── intel_card.py                # Intel-only cards for FULL_SCAN tier
│   ├── unified_risk_gate.py         # Consolidated risk checks
│   ├── state_machine.py             # Signal state transitions
│   ├── adapters/
│   │   ├── earnings_adapter.py
│   │   ├── lockup_adapter.py
│   │   └── ma_adapter.py
│   └── __init__.py
│
├── bots/                            # Multi-Bot Architecture (8 files)
│   ├── kelly_sizer.py               # 369 lines — Kelly Criterion sizing
│   ├── portfolio_overseer.py        # 586 lines — cross-bot coordination
│   ├── sector_meta_bot.py           # 247 lines — sector-level meta-bot
│   ├── specialist_bots.py           # 216 lines — Bull/Range/Bear/Earnings bots
│   ├── timeframe_stacking.py        # 400 lines — multi-timeframe alignment
│   ├── earnings_specialist.py       # 296 lines — earnings event specialist
│   ├── bot_base.py                  # 184 lines — abstract base
│   └── __init__.py
│
├── qualification/                   # Risk Qualification (11 files)
│   ├── dynamic_sizer.py             # 1197 lines — regime-aware sizing
│   ├── portfolio_risk.py            # 1557 lines — portfolio-level risk
│   ├── circuit_breakers.py          # 857 lines — escalating circuit breakers
│   ├── confluence_scorer.py         # 690 lines — multi-signal confluence
│   ├── qualifier.py                 # 657 lines — 6-stage qualification pipeline
│   ├── risk_sizer.py                # 535 lines — risk-based position sizing
│   ├── confidence_scorer.py         # 424 lines — 5-layer confidence engine
│   ├── go_nogo.py                   # 314 lines — launch readiness gate
│   ├── profit_ladder.py             # 301 lines — profit-taking rules
│   ├── pdt_tracker.py               # 153 lines — pattern day trade tracking
│   └── __init__.py
│
├── risk_officer/                    # Post-Router Governance (8 files)
│   ├── officer.py                   # 194 lines — worst-wins risk gate
│   ├── rules/
│   │   ├── vol_shock.py             # VIX-based sizing/veto
│   │   ├── liquidity.py             # Spread/RVOL rules
│   │   ├── correlation.py           # Factor group concentration
│   │   ├── drawdown.py              # Daily/consecutive loss
│   │   ├── event_window.py          # Earnings/macro event risk
│   │   └── data_reliability.py      # Short window penalty
│   └── __init__.py
│
├── core/                            # Core Modules (46 files)
│   ├── schemas.py                   # Canonical data structures
│   ├── chandelier_exit.py           # Le Beau 1999 — 5-rung profit ladder + Redis
│   ├── cross_asset_macro.py         # VIX, DXY, credit, F&G, HMM
│   ├── ml_meta_model.py             # LightGBM + XGBoost meta-labelling
│   ├── regime_provider.py           # Single source of truth for regime
│   ├── hmm_regime.py                # Hamilton 2-state HMM
│   ├── portfolio_heat.py            # Escalating circuit breakers
│   ├── liquidity_monitor.py         # Liquidity black hole detection
│   ├── drought_manager.py           # Signal drought state machine
│   ├── realtime_data.py             # Multi-source price fetcher
│   ├── provenance.py                # Data freshness tracking
│   ├── safe_math.py                 # Division-by-zero guards
│   ├── quant_math/                  # Advanced Math (11 files)
│   │   ├── almgren_chriss.py        # Execution impact model
│   │   ├── cornish_fisher.py        # Higher-moment VaR
│   │   ├── dsr.py                   # Deflated Sharpe Ratio
│   │   ├── eigen_risk.py            # PCA portfolio risk
│   │   ├── frac_diff.py             # Fractional differentiation
│   │   ├── hawkes.py                # Self-exciting cascade detection
│   │   ├── lead_lag.py              # NQ→QQQ3 lead-lag
│   │   ├── microstructure.py        # Order book dynamics
│   │   ├── nav_basis.py             # ETP NAV tracking
│   │   ├── ofi.py                   # Order flow imbalance
│   │   └── vpin.py                  # Toxic flow detection
│   ├── [30+ additional core modules — analytics, sentiment, gaps, etc.]
│   └── __init__.py
│
├── feeds/                           # Data Feed Layer (21 files)
│   ├── data_feeds.py                # Multi-source OHLCV with priority chains
│   ├── data_validator.py            # Quality scoring 0-100
│   ├── indicators.py                # 22 core indicators
│   ├── market_structure.py          # DIX/GEX/internals
│   ├── calendar_feed.py             # Earnings + macro calendar
│   ├── news_feed.py                 # NewsAPI + RSS fallback
│   ├── screener.py                  # Finviz hot stock scanner
│   ├── pattern_detector.py          # Technical pattern recognition
│   ├── correlation_matrix.py        # Cross-sectional correlations
│   ├── premarket_intelligence.py    # Overnight gap analysis
│   ├── [11 additional feeds — cointegration, herding, sentiment, etc.]
│   └── __init__.py
│
├── delivery/                        # Output Layer (16 files)
│   ├── database.py                  # 1194 lines — SQLite with 23 tables
│   ├── telegram_bot.py              # Alert distribution
│   ├── sheets_logger.py             # Google Sheets trade log
│   ├── pdf_v2_momentum.py           # Momentum & Opportunity PDF
│   ├── pdf_v2_risk.py               # Risk & Structural PDF
│   ├── pdf_v2_daily_review.py       # 3165 lines — Daily Review PDF
│   ├── pdf_intelligence.py          # 2474 lines — Legacy intelligence PDF
│   ├── pdf_master_spec.py           # Master Spec PDF
│   ├── pdf_mid_session.py           # Mid-session risk check
│   ├── pdf_overnight_risk.py        # Overnight risk PDF
│   ├── pdf_shared.py                # Shared PDF infrastructure
│   ├── mega_report.py               # EOD mega report
│   ├── dst_anchor.py                # DST-aware scheduling
│   ├── play_renderer.py             # Signal card formatting
│   ├── report_generator.py          # Report orchestrator
│   └── __init__.py
│
├── learning/                        # Adaptive Learning (35 files)
│   ├── learning_engine.py           # Regime matrix + ticker profiles + MAE/MFE
│   ├── outcomes_engine.py           # Trade outcome classification
│   ├── adaptive_intelligence.py     # AI parameter tuning (Gemini)
│   ├── meta_learner.py              # Strategy weight optimization
│   ├── edge_ledger.py               # Historical edge tracking
│   ├── drift_detector.py            # Model decay detection
│   ├── performance_attribution.py   # Per-strategy attribution
│   ├── signal_logger.py             # Signal persistence
│   ├── [27 additional learning modules]
│   └── __init__.py
│
├── data_hub/                        # Data Normalization (9 files)
│   ├── hub.py                       # Central data gateway
│   ├── models.py                    # Data models
│   ├── normalization/               # Price units, corporate actions, instrument map
│   ├── sources/                     # yfinance, IBKR, validator sources
│   └── __init__.py
│
├── api/                             # API Endpoints
│   ├── war_room_endpoints.py        # 6 additional War Room endpoints
│   └── __init__.py
│
├── dashboard/                       # Next.js Frontend
│   ├── api.py                       # Dashboard API
│   └── frontend/                    # Next.js build
│
├── scripts/                         # Operational Scripts (24 files)
│   ├── sprint6_live_gate.py         # Romano & Wolf Go/No-Go gate
│   ├── deploy_to_ec2.sh             # EC2 deployment
│   ├── backup_to_s3.sh              # Daily S3 backup
│   ├── smoke_test.py                # Pre-flight smoke test
│   ├── health_check.py              # Runtime health check
│   └── [19 additional scripts]
│
├── schemas/
│   └── signal_record.schema.json    # 720 lines — JSON Schema 2020-12
│
├── tests/                           # Test Suite (15 files)
│   ├── test_daily_target.py
│   ├── test_virtual_trader.py
│   ├── test_signal_pipeline.py
│   ├── test_risk_officer.py
│   └── [11 additional test files]
│
├── data/                            # Runtime Data
│   ├── edge_ledger.json
│   ├── meta_weights.json
│   ├── playbook.json
│   ├── ticker_profiles.json
│   └── session_status.json
│
└── artifacts/                       # Session Artifacts
    ├── drought.json
    └── [date-stamped session snapshots]
```

---

## TOTAL CODEBASE METRICS

| Category | Files | Est. Lines |
|----------|-------|------------|
| Orchestrator (main.py) | 1 | 7,826 |
| Strategies | 20 | ~8,000 |
| Signal Engine | 12 | ~3,500 |
| Execution | 10 | ~5,600 |
| Core Modules | 46 | ~15,000 |
| UK ISA Engine | 12 | ~6,600 |
| Feeds | 21 | ~8,000 |
| Qualification | 11 | ~6,900 |
| Risk Officer | 8 | ~1,200 |
| Bots | 8 | ~2,300 |
| Delivery | 16 | ~14,000 |
| Learning | 35 | ~10,000 |
| Command Center | 11 | ~6,800 |
| Data Hub | 9 | ~2,000 |
| Config | 5 | ~1,900 |
| Scripts | 24 | ~5,000 |
| Tests | 15 | ~3,000 |
| Misc (api, schemas, top-level) | 8 | ~2,500 |
| **TOTAL** | **~272 files** | **~104,000 lines** |

---

# CONTRADICTION SEVERITY MATRIX

| # | Issue | Category | Severity | Location |
|---|-------|----------|----------|----------|
| **C-01** | Ulysses Lock uses UTC for UK market hours | Chronological | **CRITICAL** | `main.py:449-481` |
| **C-02** | LSE open hour = 9 (should be 8) | Chronological | **CRITICAL** | `universal_scanner.py:54` |
| **C-03** | 5x overnight kill uses UTC not UK | Chronological | **HIGH** | `virtual_trader.py:885-888` |
| **C-04** | Signal queue put_nowait() unhandled | Concurrency | **HIGH** | `main.py:2929,4045,4268` |
| **C-05** | Continuous scan no max_instances | Concurrency | **HIGH** | `main.py:5018-5024` |
| **C-06** | Spread default 5 vs 20 bps fallback | Parameters | **HIGH** | `cost_model.py:41` vs `isa_universe.py:301` |
| **C-07** | Kelly default risk 0.5% vs immutable 0.75% | Parameters | **HIGH** | `kelly_sizer.py:126` |
| **C-08** | Kelly recalc_interval 10 vs 20 | Parameters | **MEDIUM** | `kelly_sizer.py:126` vs `settings.yaml:997` |
| **C-09** | 7 orphan inverse tickers in settings.yaml | Parameters | **MEDIUM** | `settings.yaml:1089-1100` |
| **C-10** | 9 different stop-loss multipliers | Parameters | **MEDIUM** | 9 strategy files |
| **C-11** | 4 different ATR% minimums | Parameters | **MEDIUM** | daily_target/opp_scanner/engine/pdf |
| **C-12** | Spread veto magic number 32 hardcoded | Parameters | **MEDIUM** | `main.py` |
| **C-13** | 7 duplicate timezone definitions | Chronological | **MEDIUM** | 7 files |
| **C-14** | P&L dual-source no reconciliation | State | **MEDIUM** | virtual_trader vs database |
| **C-15** | Profit ladder triple divergence risk | State | **MEDIUM** | memory/Redis/DB |
| **C-16** | Position close gap (lines 1500-1504) | State | **MEDIUM** | `virtual_trader.py` |
| **C-17** | Fire-and-forget async tasks | Concurrency | **MEDIUM** | `main.py:7240,7726` |
| **C-18** | Blocking time.sleep() in async | Concurrency | **MEDIUM** | news_feed, data_feeds, outcomes_engine |
| **C-19** | Nested asyncio.run() | Concurrency | **HIGH** | `main.py:6363` |
| **C-20** | 7 nightly jobs no instance protection | Concurrency | **LOW** | `main.py:4763-5005` |
| **C-21** | datetime.utcnow() deprecated | Chronological | **LOW** | 7 occurrences |
| **C-22** | FRED_API_KEY configured but unused | Data Feeds | **LOW** | `.env` |
| **C-23** | VIX has no fallback source | Data Feeds | **LOW** | yfinance only |
| **C-24** | No OHLCV dedup cache across sources | Data Feeds | **LOW** | feeds/data_feeds.py |
| **C-25** | Alpha Vantage 25/day exhaustion risk | Data Feeds | **LOW** | feeds/data_feeds.py |
| **C-26** | P&L kill switch in-memory only | State | **LOW** | virtual_trader.py:304-323 |

---

# END OF AUDIT

**Report compiled by:** Principal Systems Auditor
**Files analyzed:** 272+ Python/YAML/JSON files
**Lines audited:** ~104,000
**Critical contradictions found:** 3
**High-severity contradictions:** 5
**Total contradictions:** 26
