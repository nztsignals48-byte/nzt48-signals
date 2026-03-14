# NZT-48 Data Provenance Specification

**Version:** 1.0
**Status:** Draft
**Author:** NZT-48 Engineering
**Date:** 2026-02-27

---

## 1. Objective

Every data field displayed in Telegram messages, PDF reports, or War Room panels -- and every data field used in decision-making (signal scoring, regime classification, sector rotation, correlation analysis) -- must carry provenance metadata. No data point may be consumed, displayed, or acted upon without an attached provenance record specifying:

- **provider** -- the upstream source that produced the value
- **field_used** -- the exact field name as returned by the provider
- **as_of** -- the timestamp at which the value was observed or published
- **staleness TTL** -- the maximum acceptable age of the value before it is considered stale
- **quality score** -- a normalised confidence score reflecting data completeness, consistency, and provider reliability

This specification exists to eliminate "ghost data" -- values displayed or used in scoring whose origin, freshness, and reliability are unknown. The system must never silently serve stale or degraded data. Every consumer of data (strategy engine, PDF renderer, Telegram formatter, War Room dashboard) must be provenance-aware.

---

## 2. Provenance Record Schema

Every data field in the system carries the following provenance record:

```json
{
  "provider": "string",
  "field": "string",
  "as_of": "ISO8601",
  "ttl_seconds": 90,
  "quality": 0.95,
  "source_url": "string|null"
}
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `provider` | `string` | Canonical provider identifier (e.g., `yfinance`, `alpha_vantage`, `cboe`). Must match a key in the Data Providers Table. |
| `field` | `string` | Exact field name as returned or mapped from the provider (e.g., `close`, `regularMarketVolume`, `vix_spot`). |
| `as_of` | `ISO8601` | UTC timestamp of when this value was observed, fetched, or published. Not the time it was written to cache. |
| `ttl_seconds` | `int` | Maximum acceptable staleness in seconds. Derived from the Field TTL Matrix (Section 4). After `as_of + ttl_seconds`, the value is STALE. |
| `quality` | `float(0-1)` | Normalised quality score. 1.0 = complete, verified, primary source. Degrades for: missing fields (0.8), fallback provider (0.7), interpolated/estimated values (0.5), provider returning partial data (0.3). |
| `source_url` | `string\|null` | The API endpoint or URL used to fetch this data. Null for derived/calculated fields. Used in audit trail for reproducibility. |

### Provenance Record Constraints

- `as_of` must never be in the future (clock skew tolerance: 5 seconds).
- `quality` below 0.3 triggers automatic rejection -- the field is treated as missing.
- `ttl_seconds` must be a positive integer; a value of 0 means "no caching, always refetch."
- `provider` must be a registered provider in the system configuration; unknown providers are rejected.

---

## 3. Data Providers Table

### 3.1 Provider Registry

| # | Provider | Canonical ID | Fields Provided | Refresh Cadence | Rate Limits | Fallback Provider | Auth |
|---|----------|-------------|-----------------|-----------------|-------------|-------------------|------|
| 1 | **yfinance** | `yfinance` | OHLCV, market cap, PE, EPS, dividend yield, 52w high/low, sector, industry, short interest, regular market price, pre/post market price | 60s polling during market hours | ~2000 req/hr (unofficial, no key) | `twelve_data`, `fmp` | None |
| 2 | **Alpha Vantage** | `alpha_vantage` | OHLCV (daily/intraday), SMA, EMA, RSI, MACD, Bollinger Bands, ADX, sector performance, earnings calendar, income statement | 5 calls/min (free), 75/min (premium) | Strict; 500/day free | `twelve_data` | API key |
| 3 | **Squeezemetrics** | `squeezemetrics` | Dark pool index (DIX), Gamma Exposure (GEX), dark pool short volume | Daily (published ~18:00 ET) | Public endpoint, no formal limit | None (unique data) | None |
| 4 | **CBOE** | `cboe` | VIX spot, VIX futures term structure, VIX9D, VIX3M, VIX6M, VVIX, put/call ratios, SKEW index | 15s (VIX spot), daily (term structure) | Public feeds; no formal API limit | `alpha_vantage` (VIX only) | None |
| 5 | **Finviz** | `finviz` | Screener results, sector/industry heatmaps, insider trading, analyst ratings, performance tables, technical indicators | Intraday (screener), daily (maps) | Aggressive scrape detection; 1 req/s recommended | `fmp` (fundamentals) | None (Elite for real-time) |
| 6 | **Finnhub** | `finnhub` | Real-time quotes (US), earnings calendar, earnings surprises, company news, SEC filings, insider transactions, recommendation trends, economic calendar | Real-time (WebSocket), REST polling | 60 calls/min (free), 300/min (premium) | `alpha_vantage`, `fmp` | API key |
| 7 | **Forex Factory** | `forex_factory` | Economic calendar events (date, time, currency, impact, forecast, previous, actual) | Updated as events release | Scraping; respectful rate (1 req/5s) | `finnhub` (economic calendar) | None |
| 8 | **NewsAPI** | `newsapi` | News headlines, article metadata, sentiment (via NLP post-processing), source credibility | Near real-time (5-15 min lag) | 100 req/day (free), 1000/day (business) | `finnhub` (company news) | API key |
| 9 | **Twelve Data** | `twelve_data` | OHLCV, real-time prices, technical indicators (55+ built-in), forex rates, crypto, ETF data | Real-time (WebSocket), 1-min bars | 800 req/day (free), 8/min | `alpha_vantage`, `yfinance` | API key |
| 10 | **Financial Modeling Prep (FMP)** | `fmp` | OHLCV, financial statements, ratios, DCF, earnings transcripts, economic data, ETF holdings, institutional holders, stock screener | 15-min delayed (free), real-time (premium) | 250 req/day (free), 300/min (premium) | `alpha_vantage`, `yfinance` | API key |

### 3.2 Per-Provider Staleness TTL Defaults

| Provider | Price TTL | Indicator TTL | Calendar/Event TTL | Fundamentals TTL |
|----------|-----------|---------------|-------------------|-----------------|
| yfinance | 90s (market hours), 86400s (after close) | 300s | 86400s | 86400s |
| alpha_vantage | 300s | 300s | 86400s | 86400s |
| squeezemetrics | 86400s (daily publication) | N/A | N/A | N/A |
| cboe | 300s (VIX), 86400s (term structure) | 300s | N/A | N/A |
| finviz | 300s (screener), 86400s (maps) | 300s | 86400s | 86400s |
| finnhub | 60s (WebSocket), 300s (REST) | 300s | 86400s | 86400s |
| forex_factory | N/A | N/A | 3600s | N/A |
| newsapi | 1800s | N/A | N/A | N/A |
| twelve_data | 90s | 300s | N/A | 86400s |
| fmp | 300s (delayed), 90s (premium) | 300s | 86400s | 86400s |

---

## 4. Field TTL Matrix

The authoritative staleness thresholds for each critical field type. These override any provider-level defaults when more restrictive.

| Field Type | Examples | TTL (Market Hours) | TTL (After Close) | Notes |
|------------|----------|-------------------|-------------------|-------|
| **Price (OHLCV)** | open, high, low, close, volume, regular_market_price | **90 seconds** | **24 hours** (86400s) | Market hours = 08:00-16:30 LSE, 09:30-16:00 NYSE/NASDAQ. Pre/post market uses 300s TTL. |
| **VIX** | vix_spot, vix9d, vix3m, vix6m | **5 minutes** (300s) | **24 hours** | US market hours only (09:30-16:15 ET). VVIX same TTL. |
| **Regime Classification** | volatility_regime, trend_regime, market_regime | **5 minutes** (300s) | **24 hours** | Derived field. TTL resets when any upstream input is refreshed. |
| **Volume / RVOL** | volume, relative_volume, avg_volume_10d, dark_pool_volume | **90 seconds** | **24 hours** | RVOL is derived; inherits the shorter TTL of its two inputs (current volume, average volume). |
| **Earnings Calendar** | earnings_date, eps_estimate, revenue_estimate | **24 hours** (86400s) | **24 hours** | Refreshed once daily before market open. Surprise values update within 1 hour post-release. |
| **News / Sentiment** | headline, sentiment_score, article_count, news_momentum | **30 minutes** (1800s) | **30 minutes** | Sentiment is time-sensitive even after close (overnight news impacts next open). |
| **Macro Indicators** | DXY, GLD, TLT, US10Y, crude_oil | **5 minutes** (300s) | **24 hours** | DXY and bond yields trade nearly 24h; use 300s TTL during any active session. |
| **LSE Registry** | leveraged_etps, product_type, leverage_factor, underlying_index | **24 hours** (86400s) | **24 hours** | Refreshed daily at 06:30 UK. New products/delistings detected here. |
| **Dark Pool (DIX/GEX)** | dix, gex, dark_pool_short_volume | **24 hours** (86400s) | **24 hours** | Published once daily after US close (~18:00 ET). No intraday updates available. |
| **Technical Indicators** | rsi_14, macd, bollinger_upper, adx, atr | **5 minutes** (300s) | **24 hours** | Derived from price; TTL should not be shorter than price TTL but may be longer for slow indicators. |
| **Sector Rotation** | sector_rank, sector_momentum, sector_relative_strength | **5 minutes** (300s) | **24 hours** | Derived from multi-ticker price data. Recalculated on each scan cycle. |
| **Correlation Matrix** | pair_correlation, beta, rolling_correlation_20d | **5 minutes** (300s) | **24 hours** | Computationally expensive; cached at 5-min granularity. |

### Market Hours Definition

| Market | Open (Local) | Close (Local) | Timezone | Pre-Market | Post-Market |
|--------|-------------|---------------|----------|------------|-------------|
| LSE | 08:00 | 16:30 | Europe/London | 07:00-08:00 | 16:30-17:00 |
| NYSE/NASDAQ | 09:30 | 16:00 | America/New_York | 04:00-09:30 | 16:00-20:00 |
| CBOE (VIX) | 09:30 | 16:15 | America/New_York | N/A | N/A |

---

## 5. Staleness Detection

### 5.1 Detection Algorithm

Every data consumer must execute the following check before using any field:

```python
from datetime import datetime, timezone

def check_freshness(provenance: dict) -> str:
    """
    Returns: 'FRESH', 'STALE', or 'REJECTED'
    """
    now = datetime.now(timezone.utc)
    as_of = datetime.fromisoformat(provenance["as_of"])
    age_seconds = (now - as_of).total_seconds()
    ttl = provenance["ttl_seconds"]
    quality = provenance["quality"]

    # Quality gate
    if quality < 0.3:
        return "REJECTED"

    # Freshness gate
    if age_seconds <= ttl:
        return "FRESH"
    elif age_seconds <= ttl * 2:
        return "STALE"      # Usable with warning
    else:
        return "REJECTED"   # Too old, must refetch or fallback
```

### 5.2 Staleness Response Cascade

When a field is detected as STALE or REJECTED, the system executes the following cascade:

```
1. STALE (age <= 2x TTL):
   a. Mark field with staleness warning in all displays.
   b. Attempt async refresh from primary provider.
   c. If refresh succeeds within 10s -> promote to FRESH.
   d. If refresh fails -> attempt fallback provider.
   e. If fallback fails -> continue using stale value with degraded quality (quality *= 0.5).
   f. Log staleness event to audit trail.

2. REJECTED (age > 2x TTL or quality < 0.3):
   a. Do NOT use this value in any decision-making.
   b. Attempt immediate fetch from primary provider.
   c. If primary fails -> attempt fallback provider (from Providers Table).
   d. If fallback fails -> mark field as MISSING.
   e. If field is required for signal scoring -> suppress the signal entirely.
   f. If field is display-only -> show "DATA UNAVAILABLE" with last-known timestamp.
   g. Log rejection event to audit trail.

3. MISSING (no provider returned data):
   a. Signal scoring: exclude this dimension from composite score; recalculate weights.
   b. Display: show "N/A" with explanation.
   c. Alert: send Telegram notification if >3 fields missing for same ticker.
   d. Log missing data event to audit trail.
```

### 5.3 Continuous Freshness Monitoring

The main scan loop (60s cycle in `main.py`) must run a freshness audit on every cycle:

1. Iterate all cached data points.
2. Identify any that have crossed their TTL boundary since last check.
3. Proactively refresh fields approaching staleness (within 80% of TTL).
4. Emit a summary metric: `{fresh_count, stale_count, rejected_count, missing_count}` per scan cycle.
5. If `stale_count + rejected_count > 20%` of total fields, trigger a DEGRADED MODE alert via Telegram.

---

## 6. Display Requirements

### 6.1 Data Vintage in All Outputs

Every output channel must display the data vintage -- the timestamp of the oldest data point used in that output.

#### Telegram Messages

All Telegram signal and report messages must include a footer line:

```
---
Prices as of 14:32:15 UTC | VIX as of 14:30:00 UTC | Regime: EXPANSION (fresh)
```

If any field is stale, the footer must flag it:

```
---
Prices as of 14:32:15 UTC | VIX as of 14:25:00 UTC [STALE] | Regime: EXPANSION (fresh)
```

#### PDF Reports

Both PDF1 (Momentum & Opportunity) and PDF2 (Risk & Structural) must include:

- **Header:** Report generation timestamp (UTC).
- **Per-section vintage line:** Each section (e.g., "Top Movers", "Regime Analysis", "Sector Rotation") must display the data vintage for the fields used in that section.
- **Footer:** Global data quality summary: `Data Quality: 94% fresh | 4% stale | 2% unavailable`.

#### War Room Dashboard

The War Room must implement per-panel freshness indicators:

| Indicator | Condition | Visual |
|-----------|-----------|--------|
| **GREEN** | All fields FRESH (age < TTL) | Green dot / green border |
| **YELLOW** | Any field STALE (TTL < age <= 2x TTL) | Yellow dot / yellow border + tooltip showing stale field names |
| **RED** | Any field REJECTED or MISSING (age > 2x TTL or quality < 0.3) | Red dot / red border + tooltip showing affected fields |

Each panel must also display a hover tooltip with:
- Oldest data point timestamp
- Provider name
- Time until staleness (`TTL - age` countdown)

### 6.2 Degraded Mode Banner

When the system enters DEGRADED MODE (>20% fields stale/rejected), all outputs must display a prominent banner:

- **Telegram:** Prepend message with `DEGRADED DATA MODE -- signals may be unreliable`
- **PDF:** Red banner across top of first page
- **War Room:** Full-width red banner at top of dashboard

---

## 7. Audit Trail

### 7.1 Signal Provenance Chain

Every signal generated by the system must log its complete provenance chain. This chain records every data field that contributed to the signal, including the provider, timestamp, quality score, and whether fallbacks were used.

#### Storage

The provenance chain is stored in the signals database table as a JSON column named `provenance_chain`:

```sql
ALTER TABLE signals ADD COLUMN provenance_chain JSONB;
```

#### Schema

```json
{
  "signal_id": "sig_20260227_QQQ3L_S15_001",
  "generated_at": "2026-02-27T14:32:18Z",
  "strategy": "S15_daily_target",
  "ticker": "QQQ3.L",
  "fields_used": [
    {
      "provider": "yfinance",
      "field": "close",
      "as_of": "2026-02-27T14:31:45Z",
      "ttl_seconds": 90,
      "quality": 0.98,
      "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/QQQ3.L",
      "freshness": "FRESH",
      "fallback_used": false
    },
    {
      "provider": "yfinance",
      "field": "volume",
      "as_of": "2026-02-27T14:31:45Z",
      "ttl_seconds": 90,
      "quality": 0.98,
      "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/QQQ3.L",
      "freshness": "FRESH",
      "fallback_used": false
    },
    {
      "provider": "cboe",
      "field": "vix_spot",
      "as_of": "2026-02-27T14:30:00Z",
      "ttl_seconds": 300,
      "quality": 1.0,
      "source_url": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
      "freshness": "FRESH",
      "fallback_used": false
    },
    {
      "provider": "squeezemetrics",
      "field": "gex",
      "as_of": "2026-02-26T22:00:00Z",
      "ttl_seconds": 86400,
      "quality": 0.95,
      "source_url": "https://squeezemetrics.com/monitor/dix",
      "freshness": "FRESH",
      "fallback_used": false
    }
  ],
  "aggregate_quality": 0.9775,
  "stale_fields": [],
  "missing_fields": [],
  "degraded_mode": false
}
```

### 7.2 Audit Log Retention

- **Hot storage (PostgreSQL/SQLite):** 90 days of full provenance chains.
- **Cold storage (JSON files, compressed):** Indefinite. Archived weekly to `data/audit/provenance_YYYYWW.json.gz`.
- **Queryable fields:** `signal_id`, `ticker`, `strategy`, `generated_at`, `aggregate_quality`, `degraded_mode`.

### 7.3 Audit Queries

The system must support the following audit queries:

1. **"What data produced this signal?"** -- Given a `signal_id`, return the full `provenance_chain`.
2. **"How often is provider X stale?"** -- Aggregate `freshness == "STALE"` counts by provider over a time range.
3. **"Which signals used fallback data?"** -- Filter for `fallback_used == true` in any field.
4. **"What was the system data quality on date Y?"** -- Average `aggregate_quality` across all signals on that date.
5. **"Show all signals generated during DEGRADED MODE."** -- Filter for `degraded_mode == true`.

---

## 8. Failure Modes

### 8.1 Provider Down

| Scenario | Detection | Response |
|----------|-----------|----------|
| Provider returns HTTP 5xx | 3 consecutive failures within 60s | Mark provider as DOWN. Switch all fields to fallback provider. Send Telegram alert: `[PROVIDER DOWN] {provider} unreachable since {timestamp}`. Retry primary every 5 minutes. |
| Provider returns HTTP 429 (rate limited) | Single 429 response | Exponential backoff (1s, 2s, 4s, 8s, max 60s). Switch to fallback if backoff exceeds 30s. Log rate limit event. |
| Provider returns HTTP 200 but empty/malformed data | Response validation fails (missing expected fields, NaN values, negative prices) | Treat as provider failure. Increment bad-data counter. If 3 consecutive bad responses, mark provider as UNRELIABLE (quality *= 0.3) for 15 minutes. Switch to fallback. |
| DNS resolution failure | Socket timeout or DNS error | Immediate fallback. Likely network issue -- check connectivity to other providers. If all providers fail, enter OFFLINE MODE (freeze all data, suppress all signals). |

### 8.2 Rate-Limited

When a provider is rate-limited:

1. Queue pending requests with priority ordering (price > indicators > calendar > news).
2. Drain queue at the provider's sustainable rate.
3. If queue depth exceeds 50 requests, shed low-priority requests and serve cached (potentially stale) data.
4. Log all shed requests for audit.

### 8.3 Bad Data Detection

The system must detect and reject bad data using the following heuristics:

| Check | Condition | Action |
|-------|-----------|--------|
| **Price sanity** | Price change > 50% from previous close (non-leveraged) or > 150% (3x leveraged) | Reject. Flag for manual review. Use previous close with quality 0.5. |
| **Volume sanity** | Volume = 0 during market hours | Mark quality 0.3. Likely stale or API error. |
| **Timestamp sanity** | `as_of` timestamp is in the future or > 7 days in the past | Reject. Provider clock skew or serving cached data. |
| **NaN / null fields** | Any numeric field returns NaN, null, or non-numeric | Exclude field. Degrade quality of remaining fields from same response by 0.1. |
| **Cross-provider disagreement** | Price differs > 2% between two providers for same ticker at same time | Flag for investigation. Use the provider with higher historical quality score. Log disagreement. |

### 8.4 Cascading Failure

If more than 3 providers are simultaneously DOWN or UNRELIABLE:

1. Enter DEGRADED MODE.
2. Suppress all new signal generation.
3. Send high-priority Telegram alert: `[SYSTEM DEGRADED] Multiple providers down. Signal generation suspended.`
4. Continue displaying last-known data with STALE markers.
5. Attempt provider recovery checks every 60 seconds.
6. Resume normal operation only when at least 2 primary providers (yfinance + one other) are healthy.

---

## 9. Operator Actions

| Scenario | Operator Action |
|----------|----------------|
| Provider goes down (HTTP 5xx, DNS failure) | Verify the provider status page. Confirm fallback provider has activated (check `_provenance` tags in recent data). If fallback is also degraded, set `FORCE_YFINANCE=true` as emergency measure. Monitor Telegram for `[PROVIDER DOWN]` alerts. Do NOT restart the engine unless all providers are failing. |
| Staleness alerts firing repeatedly for the same field | SSH to server and check `artifacts/system_state.json` for `stale_count`. Verify the data feed is connected (`docker logs nzt48 --tail 100 | grep STALE`). If a specific provider is returning stale timestamps, restart its connection adapter. If staleness persists after restart, switch that provider's fields to fallback and investigate API endpoint changes. |
| Quality score drops below 0.3 threshold for a provider | Check if the provider has changed their API response schema (compare current response to documented schema in Provenance Spec Section 2). Verify API key is still valid and rate limits are not exhausted. If quality drop is across ALL fields from one provider, mark provider as UNRELIABLE and switch to fallback. Log the incident to `data/fallback_log.jsonl` with root cause. |
| Provenance chain is broken (signal has missing or null provenance fields) | Audit recent code changes to the provider adapters (`git log --oneline -10`). Check that every `fetch()` call in the provider modules is wrapping responses with provenance metadata. Run `PROV-T14` acceptance test to verify end-to-end provenance chain. If a code regression is found, revert and redeploy. |
| Market open with ALL feeds stale (no fresh data from any provider) | EMERGENCY: Check internet connectivity from EC2 (`curl -s https://api.polygon.io/v2/aggs/ticker/SPY/prev`). If connectivity is fine, check if all API keys have expired or been revoked. As immediate mitigation, the system should auto-enter DEGRADED MODE with last-known-good values. Suppress all signal generation until at least one primary provider returns FRESH data. Send manual Telegram update: `ALL FEEDS STALE -- investigating. No signals until resolved.` |

---

## 10. Acceptance Tests

### 9.1 Stale Data Injection

| Test ID | Scenario | Setup | Expected Result |
|---------|----------|-------|-----------------|
| `PROV-T01` | Price data exceeds 90s TTL during market hours | Inject a price record with `as_of` = now - 120s, `ttl_seconds` = 90 | Field marked STALE. Fallback provider queried. Display shows yellow indicator. |
| `PROV-T02` | Price data exceeds 2x TTL (180s) | Inject a price record with `as_of` = now - 200s, `ttl_seconds` = 90 | Field marked REJECTED. Primary + fallback queried. If both fail, field shown as "DATA UNAVAILABLE". Signal suppressed if price is required. |
| `PROV-T03` | VIX stale by 6 minutes during US hours | Inject VIX record with `as_of` = now - 360s, `ttl_seconds` = 300 | VIX marked STALE. Regime classification flagged as potentially unreliable. Fallback attempted. |
| `PROV-T04` | News data stale by 45 minutes | Inject news record with `as_of` = now - 2700s, `ttl_seconds` = 1800 | News sentiment marked STALE. Sentiment score excluded from signal scoring. |
| `PROV-T05` | All cached data fresh | All records within TTL | All indicators GREEN. No warnings. Full signal generation. |

### 9.2 Provider Failover

| Test ID | Scenario | Setup | Expected Result |
|---------|----------|-------|-----------------|
| `PROV-T06` | yfinance returns 500 | Mock yfinance to return HTTP 500 for 3 consecutive calls | yfinance marked DOWN. All price/volume fields switch to `twelve_data`. Telegram alert sent. Quality score reduced to 0.7 for fallback data. |
| `PROV-T07` | Alpha Vantage rate-limited | Mock Alpha Vantage to return HTTP 429 | Exponential backoff initiated. After 30s backoff, switch to `twelve_data`. Queue drains at sustainable rate. |
| `PROV-T08` | Fallback provider also fails | Mock both yfinance and twelve_data to fail | Second fallback attempted (`fmp`). If all fail, field marked MISSING. Signal suppressed. |
| `PROV-T09` | Provider recovers after downtime | Mock yfinance down for 5 minutes, then healthy | After recovery detected (successful health check), switch back to yfinance within 60s. Quality score restored to 1.0 after 3 consecutive healthy responses. |
| `PROV-T10` | Squeezemetrics unavailable (no fallback) | Mock Squeezemetrics to return 500 | DIX/GEX fields marked MISSING. Signal scoring proceeds without dark pool data (weights redistributed). Telegram alert sent noting unique data source is down. |

### 9.3 Multi-Source Disagreement

| Test ID | Scenario | Setup | Expected Result |
|---------|----------|-------|-----------------|
| `PROV-T11` | Price disagreement > 2% between yfinance and twelve_data | yfinance returns close=100.00, twelve_data returns close=103.50 for same ticker at same time | Disagreement flagged. Higher-quality-score provider used. Both values logged in provenance chain with `disagreement: true`. Telegram alert sent. |
| `PROV-T12` | Volume disagreement > 50% | yfinance returns volume=1M, fmp returns volume=2.1M | Flag disagreement. Use provider with more granular (intraday) data. Log both values. |
| `PROV-T13` | VIX spot disagrees between CBOE and Alpha Vantage | CBOE returns VIX=18.5, Alpha Vantage returns VIX=17.2 | CBOE is authoritative source for VIX. Use CBOE value. Log disagreement. Investigate Alpha Vantage data lag. |

### 9.4 End-to-End Provenance

| Test ID | Scenario | Setup | Expected Result |
|---------|----------|-------|-----------------|
| `PROV-T14` | Full signal generation with audit trail | Generate a signal for QQQ3.L using S15 strategy | Signal stored with complete `provenance_chain` JSON. All fields have valid provenance records. `aggregate_quality` computed. Chain is queryable by `signal_id`. |
| `PROV-T15` | PDF report data vintage display | Generate PDF1 with mix of fresh and stale data | Per-section vintage lines present. Footer shows quality summary. Stale sections have yellow indicators. |
| `PROV-T16` | Telegram message with degraded data | Generate Telegram alert while 25% of fields are stale | Message includes DEGRADED MODE banner. Footer shows stale field names. |

---

## 11. Proof Artifacts

### 10.1 Example Provenance Record

A single provenance record attached to a price field:

```json
{
  "provider": "yfinance",
  "field": "close",
  "as_of": "2026-02-27T14:31:45Z",
  "ttl_seconds": 90,
  "quality": 0.98,
  "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/QQQ3.L?interval=1m&range=1d"
}
```

### 10.2 Sample Audit Log Entry

A complete provenance chain for a generated signal:

```json
{
  "signal_id": "sig_20260227_QQQ3L_S15_001",
  "generated_at": "2026-02-27T14:32:18Z",
  "strategy": "S15_daily_target",
  "ticker": "QQQ3.L",
  "direction": "LONG",
  "entry_price": 87.45,
  "stop_price": 85.12,
  "target_price": 89.20,
  "fields_used": [
    {
      "provider": "yfinance",
      "field": "close",
      "as_of": "2026-02-27T14:31:45Z",
      "ttl_seconds": 90,
      "quality": 0.98,
      "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/QQQ3.L",
      "freshness": "FRESH",
      "fallback_used": false
    },
    {
      "provider": "yfinance",
      "field": "volume",
      "as_of": "2026-02-27T14:31:45Z",
      "ttl_seconds": 90,
      "quality": 0.98,
      "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/QQQ3.L",
      "freshness": "FRESH",
      "fallback_used": false
    },
    {
      "provider": "yfinance",
      "field": "atr_14",
      "as_of": "2026-02-27T14:31:45Z",
      "ttl_seconds": 300,
      "quality": 0.95,
      "source_url": null,
      "freshness": "FRESH",
      "fallback_used": false,
      "note": "Derived from OHLC via ta-lib"
    },
    {
      "provider": "cboe",
      "field": "vix_spot",
      "as_of": "2026-02-27T14:30:00Z",
      "ttl_seconds": 300,
      "quality": 1.0,
      "source_url": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
      "freshness": "FRESH",
      "fallback_used": false
    },
    {
      "provider": "squeezemetrics",
      "field": "gex",
      "as_of": "2026-02-26T22:00:00Z",
      "ttl_seconds": 86400,
      "quality": 0.95,
      "source_url": "https://squeezemetrics.com/monitor/dix",
      "freshness": "FRESH",
      "fallback_used": false
    },
    {
      "provider": "yfinance",
      "field": "relative_volume",
      "as_of": "2026-02-27T14:31:45Z",
      "ttl_seconds": 90,
      "quality": 0.95,
      "source_url": null,
      "freshness": "FRESH",
      "fallback_used": false,
      "note": "Derived: current_volume / avg_volume_10d"
    },
    {
      "provider": "finviz",
      "field": "analyst_rating",
      "as_of": "2026-02-27T08:15:00Z",
      "ttl_seconds": 86400,
      "quality": 0.85,
      "source_url": "https://finviz.com/quote.ashx?t=QQQ",
      "freshness": "FRESH",
      "fallback_used": false
    }
  ],
  "aggregate_quality": 0.9514,
  "stale_fields": [],
  "missing_fields": [],
  "fallback_fields": [],
  "degraded_mode": false,
  "regime_at_signal": {
    "volatility_regime": "LOW",
    "trend_regime": "BULLISH",
    "provider": "derived",
    "as_of": "2026-02-27T14:30:00Z"
  }
}
```

### 10.3 Sample Degraded Audit Log Entry

An audit entry showing fallback usage and stale data:

```json
{
  "signal_id": "sig_20260227_3LUS_S15_002",
  "generated_at": "2026-02-27T15:05:33Z",
  "strategy": "S15_daily_target",
  "ticker": "3LUS.L",
  "direction": "LONG",
  "entry_price": 42.10,
  "stop_price": 40.88,
  "target_price": 42.94,
  "fields_used": [
    {
      "provider": "twelve_data",
      "field": "close",
      "as_of": "2026-02-27T15:04:50Z",
      "ttl_seconds": 90,
      "quality": 0.70,
      "source_url": "https://api.twelvedata.com/time_series?symbol=3LUS.L",
      "freshness": "FRESH",
      "fallback_used": true,
      "fallback_reason": "yfinance returned HTTP 500 (3 consecutive failures)",
      "primary_provider": "yfinance"
    },
    {
      "provider": "cboe",
      "field": "vix_spot",
      "as_of": "2026-02-27T14:58:00Z",
      "ttl_seconds": 300,
      "quality": 0.80,
      "source_url": "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
      "freshness": "STALE",
      "fallback_used": false,
      "note": "CBOE delayed; value 7 minutes old"
    }
  ],
  "aggregate_quality": 0.75,
  "stale_fields": ["vix_spot"],
  "missing_fields": [],
  "fallback_fields": ["close"],
  "degraded_mode": false
}
```

---

## Appendix A: Implementation Checklist

- [ ] Define `ProvenanceRecord` dataclass in `models/provenance.py`
- [ ] Add `provenance_chain` JSONB column to `signals` table
- [ ] Wrap every provider fetch call to attach provenance metadata on return
- [ ] Implement `check_freshness()` in `utils/provenance.py`
- [ ] Integrate staleness detection into main scan loop (`main.py`)
- [ ] Add fallback cascade logic to each provider adapter
- [ ] Update Telegram formatter to include data vintage footer
- [ ] Update PDF renderers (`pdf_v2_momentum.py`, `pdf_v2_risk.py`) with per-section vintage
- [ ] Implement War Room freshness indicators (green/yellow/red)
- [ ] Add degraded mode detection and banner display
- [ ] Implement audit log rotation and cold storage archival
- [ ] Write all acceptance tests (PROV-T01 through PROV-T16)
- [ ] Add provider health monitoring dashboard panel
- [ ] Document provider failover in operations runbook

## Appendix B: Glossary

| Term | Definition |
|------|------------|
| **Provenance** | The origin and history of a data value, including its source, timestamp, and quality. |
| **Staleness** | The condition where a data value's age exceeds its defined TTL. |
| **TTL** | Time To Live. The maximum acceptable age of a data value before it requires refresh. |
| **Fallback Provider** | A secondary data source used when the primary provider is unavailable or returning bad data. |
| **Quality Score** | A normalised (0-1) measure of data reliability, completeness, and trustworthiness. |
| **Degraded Mode** | System state where >20% of data fields are stale or rejected, triggering alerts and signal suppression. |
| **Provenance Chain** | The complete set of provenance records for all fields contributing to a single signal. |
| **Data Vintage** | The human-readable display of a data point's timestamp (e.g., "as of 14:32 UTC"). |
| **Ghost Data** | A data value with unknown or missing provenance -- the condition this specification eliminates. |
