# FORENSICS MAP — NZT-48 Bug & Risk Inventory

**Date**: 2026-02-27
**Status**: READ-ONLY evidence collection — no code modified
**Scope**: All code paths that produce desk outputs (Telegram, PDF, War Room)

---

## 1. PREMARKET / FUTURES RETURN MATH

### 1.1 Impossible Premarket Moves
**Root Cause Hypothesis**: Futures return computation in `PreMarketIntelligenceEngine` (imported at `main.py:528`) trusts upstream data without staleness or magnitude validation.

**Evidence**:
- `main.py:1933` — calls `await self._premarket_engine.run_scan(scan_window)` and passes result directly to Telegram (`main.py:1946-1949`) with NO sanity check on returned values
- `main.py:608-611` — data quality gate only checks for `SYSTEM_DOWN` status; `DEGRADED` or stale data passes through (FAIL-OPEN)
- No maximum magnitude filter exists anywhere in the pipeline — a 500% overnight move would pass unchallenged
- `delivery/pdf_v2_momentum.py:314,320` — division by zero risk if `close[-1] == 0` (unlikely but unguarded)

**Files Involved**:
| File | Lines | Function | Issue |
|------|-------|----------|-------|
| `main.py` | 1914-1951 | `run_premarket_intelligence()` | No data validation on brief results |
| `main.py` | 608-611 | `run_scan()` data quality gate | Only binary SYSTEM_DOWN check, not granular |
| `main.py` | 630-638 | Bar validation | Validates OHLC integrity but NOT freshness/staleness |
| `strategies/daily_target.py` | 44-69 | `DailyTargetStrategy.score_candidates()` | Returns score=0 for tickers with insufficient data but score=0 signals can still reach Telegram if gating is incomplete |

### 1.2 Leverage-Once Policy Violation Risk
**Root Cause Hypothesis**: ETP returns are already leveraged (3x, 5x). If any code path applies leverage math AGAIN, returns are double-leveraged.

**Evidence**:
- `delivery/pdf_v2_risk.py:394` — `vol_decay_score = min(100, atr_pct * leverage * 3)` — correctly uses leverage factor but this is for SCORING not return display
- `delivery/pdf_v2_momentum.py:346-431` — bias scoring uses raw price returns, does NOT multiply by leverage (correct)
- `uk_isa/predictive_scoring.py:532-901` — component scoring uses raw indicators (correct)
- **Risk**: No systemic "leverage-once" assertion exists. A future code change could accidentally re-leverage.

**Recommendation**: Add `assert_leverage_once()` guard at every return computation entry point.

---

## 2. REGIME / CONFIDENCE CONTRADICTIONS

### 2.1 Regime Computation
**Location**: Delegated to `RegimeClassifier` (external module), read at `main.py:254,1464,1971,2831`

**Contradiction Risk**:
- `uk_isa/volatility_regime.py:260-313` — classifies into 5 regimes (COMPRESSION, EXPANSION, BLOW_OFF, EXHAUSTION, BREAKDOWN) with probability scores
- `config/settings.yaml` Section 7 — defines 8 regime states (TRENDING_UP_STRONG/MOD, TRENDING_DOWN_STRONG/MOD, RANGE_BOUND, HIGH_VOLATILITY, RISK_OFF, SHOCK)
- **These are TWO DIFFERENT regime taxonomies** — volatility_regime.py uses one, settings.yaml defines another
- **No mapping function** between them has been identified — potential for Telegram to show "EXPANSION" while PDF shows "RANGE_BOUND"

### 2.2 Confidence Scoring Layers
**Location**: 5-layer confidence in `config/settings.yaml` (Section 8), adjustments in `main.py:1011-1012,1505-1508`

**Issues**:
- `main.py:1014-1016` — Confluence scorer is a **SOFT GATE**: if it crashes, signal continues with UNADJUSTED confidence (fail-open)
- `main.py:1505-1508` — AI enhancement adjusts confidence ±3-5 points; if Gemini fails, original confidence preserved (fail-open)
- `main.py:1131-1133` — Dynamic sizer is also SOFT GATE: failure → fallback to qualifier sizing

### 2.3 Score=0 Signal Leak
**Evidence**: `strategies/daily_target.py:44-69` can return `score=0` for tickers with insufficient data. The qualification pipeline (7-stage, settings Section 9) kills confidence < 60, but score and confidence are DIFFERENT fields. A score=0 signal with confidence=65 (from a different layer) could theoretically pass.

**File**: `main.py:1014-1051` — Qualification gates check confidence, not composite score.

---

## 3. DROUGHT STATE MANAGEMENT

### 3.1 Drought Detection & Clearing
**Location**: `main.py:2755-2758` (read), never explicitly managed in orchestrator

**Issues**:
- Drought is READ from `engine_result.drought` but **never explicitly set or cleared** by main.py
- No "drought cleared" event is emitted — if drought was true, it stays true until engine says otherwise
- No maximum drought duration / auto-escalation exists
- `artifacts/system_state.json` drought field: `false` as of 2026-02-27 06:59 UTC
- No binding rule exists: "if drought AND regime=EXPANSION → contradiction alert"

### 3.2 Drought-Regime Contradiction
**Scenario**: System reports "DROUGHT" (no qualifying signals) while regime is "EXPANSION" (strong trend). This is logically contradictory — strong trends should produce abundant signals.
**No guard exists** to detect or report this contradiction.

---

## 4. TELEGRAM TAPE ISSUES

### 4.1 Duplicate Sends on Restart
**Location**: `main.py:3678` runs initial scan on startup; `delivery/telegram_bot.py:66` dedupe window = 300s

**Scenario**:
1. Signal sent at T=0
2. System crashes at T+2min
3. Restart at T+2.5min → initial scan fires → same signal generated
4. Dedupe catches it (within 5min window) → silently dropped
5. BUT: if restart is > 5min after original send → **DUPLICATE sent**

### 4.2 Formatting & Labelling
**Location**: `delivery/telegram_bot.py:180-212` (`format_signal_message()`)

**Issues**:
- Rate limiter (`MAX_PER_MINUTE=5`, `MAX_PER_HOUR=30`) is hard-coded at `telegram_bot.py:90-92`
- Spam kill threshold (10/min → 15min auto-pause) at `telegram_bot.py:92,111`
- `telegram_bot.py:805-806` — `format_firewall_block_message()` uses `parse_mode=None` (disables HTML) — inconsistent with other formatters that use HTML
- Kill switch state (`_paused_strategies`, `_killed_strategies`) held in-memory only — **lost on restart** (`telegram_bot.py:1277,1300`)

### 4.3 Signal Validation Before Send
**Location**: `telegram_bot.py:731-759` (`_gated_send()`)

**4-gate pipeline**:
1. `validate_telegram_signal(play)` — rejects score=0/None or missing fields
2. `_dedupe.should_send(hash)` — 5min window
3. `_rate_limiter.can_send()` — rate limits
4. Try/except on send

**Gap**: No **data freshness gate** — signal could be based on 10-minute-stale data and still pass all 4 gates.

---

## 5. PDF / DESK NOTES ISSUES

### 5.1 PDF V2 Momentum (`delivery/pdf_v2_momentum.py`)
**Size**: 2,286 lines

**Issues**:
| Line(s) | Issue | Severity |
|---------|-------|----------|
| 314, 320 | Division by zero if `close[-1] == 0` | MEDIUM |
| 429 | `long_pts / max_pts * 100` — div/0 if `max_pts == 0` | MEDIUM |
| 333 | `vol_20d = float(np.std(...))` — no check for sufficient data after dropna | MEDIUM |
| 382-383 | `bb_width = (upper - lower) / sma` — div/0 if `sma == 0` | MEDIUM |
| 441-449 | Regime classification uses hardcoded thresholds with no validation | LOW |
| 769-776 | yfinance retry logic lacks backoff strategy | LOW |
| 779-784 | Data completeness: PASS (≥80%), WARN (≥50%), FAIL (<50%) — thresholds not configurable | LOW |

### 5.2 PDF V2 Risk (`delivery/pdf_v2_risk.py`)
**Size**: 2,116 lines

**Issues**:
| Line(s) | Issue | Severity |
|---------|-------|----------|
| 399 | `rvol = (last_vol / avg_vol_20)` — div/0 if `avg_vol_20 == 0` | HIGH |
| 428 | Day change formula assumes 2+ bars exist | MEDIUM |
| 431-432 | 1M/3M returns assume 21/63 days history; returns 0.0 if insufficient | LOW |
| 220-233 | Regime classification hardcoded thresholds (different from volatility_regime.py) | MEDIUM |

### 5.3 Cross-PDF Consistency
**No automated check exists** that PDF1 (Momentum) and PDF2 (Risk) agree on:
- Regime classification for same ticker
- Direction bias for same ticker
- Data freshness timestamps

---

## 6. WAR ROOM / DASHBOARD ISSUES

### 6.1 Missing API Endpoints (Frontend calls → 404)
| Endpoint | Frontend Location | Status |
|----------|------------------|--------|
| `/api/scan_health` | `page.tsx:87` (5s poll) | **NOT IMPLEMENTED** in api.py |
| `/api/opportunity` | `page.tsx:126` (15s poll) | **NOT IMPLEMENTED** |
| `/api/exits` | `page.tsx:127` (15s poll) | **NOT IMPLEMENTED** |
| `/api/telegram/events` | `page.tsx:128` (15s poll) | **NOT IMPLEMENTED** |
| `/api/consistency` | `page.tsx:129` (15s poll) | **NOT IMPLEMENTED** |
| `/api/copilot/query` (POST) | `page.tsx:218` (on-demand) | **NOT IMPLEMENTED** |

### 6.2 Hardcoded Assumptions
| File | Line(s) | Issue |
|------|---------|-------|
| `lib/api.ts:7-9` | API base URL defaults to `http://{hostname}:8000` | Hardcoded port |
| `lib/api.ts:13-14` | WebSocket URL derived from HTTP URL + `/ws/live` | Assumes same host |
| `api.py:100` | CORS origins: `localhost:3001,8000,8765` only | No production origins |
| `analysis/page.tsx:67-80` | ISA_FUNDS list hardcoded (12 tickers) | Stale if universe changes |
| `analysis/page.tsx:60-64` | US equity tickers hardcoded | Stale if universe changes |

### 6.3 Null Check Gaps
| File | Line(s) | Issue |
|------|---------|-------|
| `page.tsx:92` | `setPerformance(perf.aggregate)` — assumes `aggregate` key | Blank panel if missing |
| `page.tsx:982` | `tp.rolling_60d_wr * 100` — assumes number not null | NaN display |
| `page.tsx:1029-1035` | Sort by count — NaN if count null | Crash risk |

---

## 7. DATA QUALITY & STALENESS

### 7.1 yfinance Feed Risks
| File | Line(s) | Issue |
|------|---------|-------|
| `uk_isa/lse_registry.py:318` | Multi-ticker yfinance returns different DataFrame structures | Parse failure risk |
| `uk_isa/lse_registry.py:331-333` | Price change calc assumes `len(df) >= 2` | Crash on single-bar |
| `uk_isa/lse_registry.py:347` | Liquidity tier stays "UNKNOWN" if fetch fails | Stale classification |
| `uk_isa/volatility_regime.py:254` | `vix_ratio = ann_vol / self._vix_ann_vol` — div/0 if VIX=0 | HIGH |
| `uk_isa/volatility_regime.py:240-241` | BB percentile crashes if `bb_hist` empty | MEDIUM |

### 7.2 No Staleness Enforcement
- **No field in any artifact records "as-of" timestamp per data point**
- `system_state.json` has a session timestamp but individual field staleness is unknown
- A stale VIX reading (e.g., from market close) used during premarket would produce wrong regime
- **No TTL/expiry mechanism** on any cached data field

---

## 8. RESTART HYGIENE

### 8.1 State Lost on Restart
| State | Location | Persisted? |
|-------|----------|-----------|
| Dedupe hashes | `telegram_bot.py:63-84` (in-memory dict) | NO — lost on restart |
| Rate limiter counters | `telegram_bot.py:87-122` (in-memory) | NO |
| Paused strategies | `telegram_bot.py:1277` (in-memory set) | NO |
| Killed strategies | `telegram_bot.py:1300` (in-memory set) | NO |
| Learning state | `main.py:3610-3613` (DB load) | YES |
| System state | `artifacts/system_state.json` | YES (file) |
| Scheduler state | `main.py:2439` (APScheduler) | NO — fresh on restart |

### 8.2 Initial Scan Duplicate Risk
- `main.py:3678` — runs `await self.run_scan()` immediately on startup
- If previous session's signals are < 5min old, dedupe catches them
- If > 5min old, duplicate signals will be sent
- **No "startup grace period"** or "replay protection" exists

---

## 9. STRATEGY-SPECIFIC ISSUES

### 9.1 S15 Daily Target (`strategies/daily_target.py`)
**Size**: 287 lines

**Issues**:
| Line(s) | Function | Issue | Severity |
|---------|----------|-------|----------|
| 44-69 | `score_candidates()` | Returns score=0 for insufficient data — but score=0 not explicitly blocked downstream | MEDIUM |
| 75-89 | `_compute_2pct_reachability()` | Uses `atr_pct` to estimate 2% reachability — if ATR is stale, estimate is wrong | MEDIUM |
| 110-130 | `generate_signal()` | Stop = 1×ATR, Target = +2% exactly — hardcoded, not configurable | LOW |
| 156-170 | `_validate_candidate()` | Checks ADX > 20 and RVOL > 1.0 — if both are stale, validation passes incorrectly | MEDIUM |

### 9.2 S3 Mean Reversion (`strategies/mean_reversion.py`)
**Status**: DORMANT in V2 (line 1, `DORMANT = True`)
**Risk**: Code still loaded, could be accidentally reactivated. No feature flag mechanism.

---

## 10. CRITICAL PATH SUMMARY (Priority Order)

| # | Issue | Impact | Root Location |
|---|-------|--------|---------------|
| P0-1 | No premarket data sanity gate (impossible moves pass through) | Bad Telegram alerts | `main.py:1933-1949` |
| P0-2 | No staleness enforcement on any data field | Stale data → wrong signals | System-wide |
| P0-3 | 6 missing War Room API endpoints (404 errors) | Broken dashboard panels | `api.py` (missing routes) |
| P0-4 | Dual regime taxonomy without mapping | Contradictory regime labels | `volatility_regime.py` vs `settings.yaml` |
| P0-5 | Kill switch / pause state lost on restart | Safety gap | `telegram_bot.py:1277,1300` |
| P1-1 | Division by zero risks (5 locations) | Crash risk | See sections 5.1, 5.2, 7.1 |
| P1-2 | Drought-regime contradiction undetected | Confusing desk output | `main.py:2755-2758` |
| P1-3 | SOFT gates allow degraded signals through | Lower signal quality | `main.py:1014-1016,1131-1133` |
| P1-4 | No cross-PDF consistency check | Contradictory reports | PDF generation pipeline |
| P1-5 | Restart duplicate send window (>5min gap) | Spam/confusion | `main.py:3678`, `telegram_bot.py:66` |
| P2-1 | Hardcoded dashboard tickers (stale if universe changes) | UI drift | `analysis/page.tsx:60-80` |
| P2-2 | Score=0 signal leak potential | Score 0/100 alerts | `daily_target.py:44-69` → `main.py` |
| P2-3 | yfinance multi-ticker parse failure | Registry crash | `lse_registry.py:318` |
