# DATA VENDOR MIGRATION PLAN

**Document ID:** NZT48-ANNEX-DVM-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** DRAFT — Requires sign-off before implementation
**Scope:** Migration of NZT-48 data infrastructure from scraping-based providers to licensed, SLA-backed data feeds

---

## 1. OBJECTIVE

Migrate the NZT-48 trading system from its current scraping-dependent data stack (yfinance primary) to a robust, legally licensed, multi-provider architecture that eliminates single-point-of-failure risk and provides contractual uptime guarantees. The migration must be non-disruptive: at no point during the transition should the system have fewer data capabilities than it has today.

---

## 2. CURRENT STATE ASSESSMENT

### 2.1 Active Data Providers (from `config/settings.yaml` PART VIII)

| # | Provider | Role | Auth | Rate Limit | Legal Basis | Reliability |
|---|----------|------|------|------------|-------------|-------------|
| 1 | **yfinance** | Primary OHLCV, all tickers | None (scraping) | Unofficial (~2000/hr) | **ToS VIOLATION RISK** | Breaks ~2x/year |
| 2 | **Alpha Vantage** | Backup intraday | API key (`ALPHA_VANTAGE_KEY`) | 25 calls/day (free) | Licensed API | Stable but slow |
| 3 | **SqueezMetrics** | GEX/DIX readings | Web scraper | ~10/hr manual | **Scraping** | Fragile |
| 4 | **CBOE** | VIX/VIX3M | Public data | N/A | Public | Stable |
| 5 | **Finviz** | Hot stock screener | Web scraper | ~5/min | **Scraping** | Moderate |
| 6 | **Finnhub** | Earnings, financials, analyst | API key (`FINNHUB_API_KEY`) | 60 calls/min | Licensed API | Stable |
| 7 | **Forex Factory** | Macro calendar (FOMC, CPI, NFP) | Web scraper | ~2/min | **Scraping** | Fragile |
| 8 | **NewsAPI** | Headline sentiment | API key (`NEWSAPI_API_KEY`) | Free tier | Licensed API | Stable |
| 9 | **Twelve Data** | Real-time quotes, intraday | API key (`TWELVEDATA_API_KEY`) | 8 calls/min (free) | Licensed API | Stable |
| 10 | **FMP** | Profiles, bulk quotes, financials | API key (`FMP_KEY`) | 250 calls/day (free) | Licensed API | Stable |

### 2.2 Critical Vulnerabilities

1. **yfinance is the single point of failure.** Every OHLCV bar for all 65+ tickers routes through yfinance first. It is an unofficial scraper of Yahoo Finance's internal APIs. Yahoo has broken it twice in 2025. No SLA. No support. No recourse.
2. **Three providers are pure scrapers** (SqueezMetrics, Finviz, Forex Factory). Any HTML layout change breaks them silently.
3. **Free tiers are exhausted at scale.** Alpha Vantage at 25/day cannot cover 65 tickers with intraday data. Twelve Data at 8/min constrains real-time refresh to ~2 tickers/min.
4. **LSE coverage is yfinance-only.** If yfinance `.L` ticker support breaks, the entire ISA universe goes dark.
5. **No contractual data quality guarantee** from any current provider.

### 2.3 Monthly Cost: Current

| Provider | Monthly Cost |
|----------|-------------|
| yfinance | $0 |
| Alpha Vantage | $0 (free tier) |
| SqueezMetrics | $0 (scraper) |
| CBOE | $0 (public) |
| Finviz | $0 (scraper) |
| Finnhub | $0 (free tier) |
| Forex Factory | $0 (scraper) |
| NewsAPI | $0 (free tier) |
| Twelve Data | $0 (free tier) |
| FMP | $0 (free tier) |
| **TOTAL** | **$0/mo** |

---

## 3. LEGALITY ASSESSMENT

### 3.1 yfinance

- **Mechanism:** Reverse-engineers Yahoo Finance's undocumented internal JSON endpoints.
- **Yahoo Finance ToS (Section 1):** "You shall not... use any robot, spider, scraper, or other automated means to access the Services for any purpose."
- **Risk Level:** MEDIUM-HIGH. Yahoo has not pursued legal action against yfinance users to date, but has repeatedly broken the scraper's endpoints. Using it for systematic trading at scale increases exposure.
- **Recommendation:** Retain as emergency fallback only. Do not rely on it as primary feed. Flag all yfinance-sourced data with `FALLBACK_DATA` provenance tag.

### 3.2 SqueezMetrics / Finviz / Forex Factory (Scrapers)

- **Risk Level:** MEDIUM. Web scraping without explicit permission violates most sites' ToS. IP blocks are common.
- **Recommendation:** Replace with licensed alternatives where possible. Keep as best-effort secondary sources with clear provenance tagging.

### 3.3 Licensed Providers (Alpha Vantage, Finnhub, Twelve Data, FMP, NewsAPI)

- **Risk Level:** LOW. All have published API terms permitting programmatic access within rate limits.
- **Recommendation:** Upgrade tiers where needed. These are safe to depend on.

### 3.4 Polygon.io

- **Risk Level:** NONE. Fully licensed, SEC-registered market data provider. Official API with documented terms.
- **Coverage:** US equities real-time, options, forex, crypto. Limited international (some LSE via delayed feeds).

### 3.5 Interactive Brokers (IBKR)

- **Risk Level:** NONE. Regulated broker providing market data as part of brokerage services.
- **Coverage:** Global equities including LSE real-time. Requires funded brokerage account.
- **Key advantage:** Only viable source for real-time LSE leveraged ETP data.

---

## 4. MIGRATION TARGET EVALUATION

### 4.1 Polygon.io

| Attribute | Assessment |
|-----------|------------|
| **Coverage** | US equities (real-time), options, forex, crypto. International coverage via delayed feeds. |
| **LSE Support** | LIMITED. Some UK tickers available but not comprehensive. Does NOT reliably cover leveraged ETPs like QQQ3.L. |
| **Pricing** | Starter: $29/mo (5 API calls/min, delayed). Developer: $79/mo (unlimited, real-time). Business: $199/mo (WebSocket, all data). |
| **API Quality** | Excellent. RESTful + WebSocket. Official client libraries (Python). Well-documented. |
| **Historical Data** | 5+ years daily, 2 years 1-min intraday for US equities. |
| **Latency** | <100ms for real-time feeds on Business tier. |
| **SLA** | 99.9% uptime on Business tier. Published incident page. |
| **Verdict** | **PRIMARY for US equities.** Cannot serve as primary for LSE. |

**Recommended Tier:** Developer ($79/mo) to start. Upgrade to Business ($199/mo) when going live.

### 4.2 Interactive Brokers (IBKR)

| Attribute | Assessment |
|-----------|------------|
| **Coverage** | Global. US, UK (LSE), EU, Asia. All instrument types. |
| **LSE Support** | FULL. Real-time LSE data including all leveraged ETPs. Requires LSE market data subscription ($4.50/mo). |
| **Pricing** | Free with funded account (min $0 balance for IBKR Lite, $10k for Pro). Market data packs: US ($1.50/mo), LSE ($4.50/mo). |
| **API Quality** | TWS API (Python `ib_insync`). Mature but complex. Requires TWS Gateway running. |
| **Historical Data** | Extensive. 5+ years daily, 1+ year intraday depending on instrument. |
| **Latency** | Real-time with TWS connection. <50ms for subscribed instruments. |
| **SLA** | Broker-grade uptime. Regulated by FCA (UK), SEC (US). |
| **Verdict** | **PRIMARY for LSE.** Only realistic source for real-time leveraged ETP data. |

**Recommended Setup:** IBKR Pro account, LSE + US market data subscriptions (~$6/mo).

### 4.3 TradingView

| Attribute | Assessment |
|-----------|------------|
| **Coverage** | Broad (visual platform). No official programmatic API. |
| **LSE Support** | Visual charts only. No programmatic access to LSE data. |
| **Pricing** | Pro: $12.95/mo. Premium: $24.95/mo. Expert: $49.95/mo. |
| **API Quality** | **NO OFFICIAL API.** Unofficial pine-connector and webhook-based approaches exist but violate ToS. |
| **Verdict** | **REJECTED.** No official API. Using unofficial access is the same problem we are trying to solve with yfinance. |

### 4.4 Alpha Vantage (Upgraded Tier)

| Attribute | Assessment |
|-----------|------------|
| **Coverage** | US equities, forex, crypto, some fundamentals. Limited international. |
| **LSE Support** | PARTIAL. Some `.L` tickers work but coverage is unreliable for leveraged ETPs. |
| **Pricing** | Free: 25/day. Premium: $49.99/mo (75 calls/min). |
| **API Quality** | RESTful. Simple. Well-documented. Slow for bulk operations. |
| **Verdict** | **RETAIN as secondary.** Upgrade to Premium ($49.99/mo) for better intraday coverage of US tickers. |

---

## 5. TARGET ARCHITECTURE

```
                    +-----------------+
                    |   NZT-48 Engine |
                    +--------+--------+
                             |
                    +--------v--------+
                    | DataFeedRouter  |  <-- New unified abstraction layer
                    +--------+--------+
                             |
            +----------------+----------------+
            |                |                |
    +-------v------+  +-----v------+  +------v------+
    |  Polygon.io  |  |    IBKR    |  |  Fallbacks  |
    |  (US Primary)|  |(LSE Primary)|  | (yfinance,  |
    |  $79/mo      |  |  ~$6/mo    |  |  AV, etc.)  |
    +--------------+  +------------+  +-------------+
```

### 5.1 Provider Hierarchy Per Data Type

| Data Type | Primary | Secondary | Emergency Fallback |
|-----------|---------|-----------|-------------------|
| US OHLCV (daily) | Polygon.io | Alpha Vantage (Premium) | yfinance + FALLBACK_DATA tag |
| US OHLCV (intraday) | Polygon.io | Twelve Data | yfinance + FALLBACK_DATA tag |
| LSE OHLCV (daily) | IBKR | yfinance | None (alert operator) |
| LSE OHLCV (intraday) | IBKR | None | yfinance (15-min delayed) + FALLBACK_DATA tag |
| VIX / VIX3M | CBOE (direct) | Polygon.io | yfinance |
| GEX / DIX | SqueezMetrics (scraper) | None | Hardcoded neutral if unavailable |
| Earnings calendar | Finnhub | FMP | None |
| Macro calendar | Forex Factory (scraper) | Finnhub economic events | Hardcoded known dates |
| News sentiment | NewsAPI | Finnhub news | None |
| Company profiles | FMP | Finnhub | None |

### 5.2 DataFeedRouter Specification

A new module `data/feed_router.py` must implement:

```python
class DataFeedRouter:
    """
    Unified interface for all market data.
    Routes requests to the correct provider based on:
    - Ticker geography (US vs LSE)
    - Data type (daily, intraday, fundamentals, calendar)
    - Provider health status
    - Fallback chain
    """

    def get_ohlcv(self, ticker: str, interval: str, period: str) -> DataFrame:
        """Returns OHLCV with provenance metadata column."""
        ...

    def get_quote(self, ticker: str) -> dict:
        """Real-time or last-available quote."""
        ...

    def health_check(self) -> dict:
        """Returns status of each provider."""
        ...
```

Every DataFrame returned must include a `_provenance` column with values:
- `POLYGON_LIVE` -- Polygon real-time
- `IBKR_LIVE` -- IBKR real-time
- `AV_PREMIUM` -- Alpha Vantage premium tier
- `TWELVEDATA` -- Twelve Data
- `YFINANCE_FALLBACK` -- yfinance fallback (degraded)
- `MANUAL_OVERRIDE` -- Operator-supplied data

---

## 6. YFINANCE FALLBACK RULES

yfinance must be retained as the emergency fallback for the foreseeable future, but its usage must be explicitly controlled and tracked.

### 6.1 When yfinance Activates

1. Primary provider returns HTTP 5xx three consecutive times within 60 seconds.
2. Primary provider returns stale data (last bar timestamp > 15 minutes old during market hours).
3. Primary provider connection fails (timeout after 10 seconds).
4. Operator manually sets `FORCE_YFINANCE=true` in environment.

### 6.2 Fallback Behavior

- All data sourced from yfinance during fallback MUST carry `_provenance = "YFINANCE_FALLBACK"`.
- The Telegram alert channel MUST receive a message: `DATA DEGRADED: {ticker} using yfinance fallback since {timestamp}`.
- If yfinance fallback is active for > 30 minutes during market hours, escalate to `DATA_EMERGENCY` alert.
- Signals generated using fallback data MUST have their confidence score reduced by 15 points.
- The daily PDF report MUST include a "Data Quality" section listing any fallback events.

### 6.3 Fallback Exit

- Once primary provider returns 3 consecutive successful responses, switch back automatically.
- Log the fallback duration and reason to `data/fallback_log.jsonl`.

---

## 7. COST ANALYSIS

### 7.1 Proposed Monthly Budget

| Provider | Tier | Monthly Cost | Annual Cost |
|----------|------|-------------|-------------|
| Polygon.io | Developer | $79.00 | $948.00 |
| IBKR | Pro (LSE + US data) | $6.00 | $72.00 |
| Alpha Vantage | Premium | $49.99 | $599.88 |
| Finnhub | Free | $0.00 | $0.00 |
| Twelve Data | Free | $0.00 | $0.00 |
| FMP | Free | $0.00 | $0.00 |
| NewsAPI | Free | $0.00 | $0.00 |
| **TOTAL** | | **$134.99/mo** | **$1,619.88/yr** |

### 7.2 Cost Justification

- The 2% daily compounding target on a $10,000 ISA implies annual returns of ~$1.48M.
- Even at 10% of target performance, annual returns exceed $148K.
- $1,620/year in data costs is 0.11% of the 10%-target scenario.
- A single missed trade due to yfinance downtime during a major move could cost more than a full year of data subscriptions.

### 7.3 Upgrade Path (When Going Live)

| Provider | Live Tier | Monthly Cost |
|----------|-----------|-------------|
| Polygon.io | Business (WebSocket) | $199.00 |
| IBKR | Pro (Level 2 data) | $20.00 |
| Alpha Vantage | Premium (retain) | $49.99 |
| **Live TOTAL** | | **$268.99/mo** |

---

## 8. LSE-SPECIFIC REQUIREMENTS

### 8.1 Mandatory Capabilities

1. **`.L` suffix ticker resolution:** Must resolve `QQQ3.L`, `3LUS.L`, `3SEM.L` and all 62+ ISA-universe tickers.
2. **Leveraged ETP coverage:** Many leveraged ETPs (WisdomTree, GraniteShares, Leverage Shares) have low volume. Provider must return data even for tickers with <1000 shares/day.
3. **GBp vs GBP handling:** LSE prices are often quoted in GBp (pence). The feed router must normalize to GBP (pounds) consistently.
4. **Corporate actions:** Split/consolidation handling for leveraged ETPs (these are common; reverse splits happen ~annually for decaying products).
5. **Trading hours:** LSE opens 08:00 London, closes 16:30 London. Pre-market/auction data desirable but not mandatory.

### 8.2 Provider Capability Matrix for LSE

| Requirement | Polygon.io | IBKR | yfinance | Alpha Vantage |
|-------------|-----------|------|----------|---------------|
| `.L` ticker resolution | Partial | Full | Full | Partial |
| Leveraged ETP data | No | Yes | Yes (fragile) | No |
| GBp normalization | N/A | Manual | Auto | N/A |
| Corporate actions | N/A | Yes | No | No |
| Real-time | N/A | Yes | No (15-min) | No (15-min) |

**Conclusion:** IBKR is the only viable primary for LSE. yfinance remains as fallback.

---

## 9. MIGRATION STRATEGY

### Phase 1: Foundation (Week 1-2)

- [ ] Implement `DataFeedRouter` abstraction layer in `data/feed_router.py`
- [ ] Add provenance column to all data flows
- [ ] Write unit tests for fallback chain logic
- [ ] Add fallback logging to `data/fallback_log.jsonl`

### Phase 2: Polygon Integration (Week 3-4)

- [ ] Sign up for Polygon.io Developer tier
- [ ] Implement `data/providers/polygon_provider.py`
- [ ] Run parallel: Polygon vs yfinance for all US tickers (1 week)
- [ ] Validate data match rate > 99.5% for daily OHLCV
- [ ] Switch US primary to Polygon
- [ ] Monitor fallback rate for 1 week (target: <1% of requests)

### Phase 3: IBKR Integration (Week 5-8)

- [ ] Open IBKR Pro account (or verify existing)
- [ ] Subscribe to LSE + US market data packs
- [ ] Deploy TWS Gateway on EC2 instance (or IB Gateway headless)
- [ ] Implement `data/providers/ibkr_provider.py` using `ib_insync`
- [ ] Run parallel: IBKR vs yfinance for all `.L` tickers (2 weeks)
- [ ] Validate data match rate > 99% for daily OHLCV
- [ ] Switch LSE primary to IBKR

### Phase 4: Alpha Vantage Upgrade (Week 9)

- [ ] Upgrade Alpha Vantage to Premium tier ($49.99/mo)
- [ ] Route secondary US intraday through AV Premium
- [ ] Verify 75 calls/min capacity is sufficient for universe size

### Phase 5: Stabilization (Week 10-12)

- [ ] Monitor all providers for 2 weeks with full logging
- [ ] Verify daily PDF reports include Data Quality section
- [ ] Verify fallback events are < 0.5% of total data requests
- [ ] Run a simulated "Polygon down" test (block outbound to Polygon, verify yfinance activates)
- [ ] Run a simulated "IBKR down" test (disconnect TWS, verify yfinance activates for LSE)
- [ ] Document final architecture in system README

---

## 10. FAILURE MODES

| # | Failure Mode | Impact | Mitigation |
|---|-------------|--------|------------|
| FM-1 | Polygon API goes down during US market hours | No real-time US OHLCV | Auto-fallback to yfinance within 30s; confidence penalty applied |
| FM-2 | IBKR TWS Gateway crashes on EC2 | No LSE data | Auto-restart via systemd; fallback to yfinance; Telegram alert |
| FM-3 | yfinance breaks permanently (Yahoo changes endpoints) | Emergency fallback unavailable | Polygon + IBKR cover all tickers; AV covers US backup |
| FM-4 | LSE leveraged ETP delists without notice | Stale/missing data for that ticker | LSE Registry daily refresh detects missing data; auto-quarantine |
| FM-5 | IBKR rate-limits API during high volatility | Delayed LSE data | Batch requests; use yfinance as supplementary during spikes |
| FM-6 | Polygon subscription lapses (payment failure) | Downgrade to yfinance primary | Billing alert 7 days before renewal; fallback chain activates |
| FM-7 | Data mismatch between providers | Inconsistent signals | Provenance logging allows post-hoc investigation; alert if mismatch > 1% |
| FM-8 | GBp/GBP conversion error in IBKR feed | 100x price error in ISA tickers | Sanity check: reject any LSE price < 0.01 or > 100000; log anomaly |

---

## 11. OPERATOR ACTIONS

| Scenario | Operator Action |
|----------|----------------|
| Polygon API returns errors (HTTP 5xx or 4xx) during market hours | Check Polygon.io status page for reported incidents. Verify the API key is valid and the subscription is active (`curl -s "https://api.polygon.io/v2/aggs/ticker/SPY/prev?apiKey=$POLYGON_KEY"`). Check if rate limits have been exceeded (Developer tier = unlimited for REST, but verify). If Polygon is confirmed down, the system should have auto-fallen back to yfinance -- verify via `_provenance` tags in recent data. Monitor `data/fallback_log.jsonl` for fallback activation. |
| yfinance fallback is also failing (primary AND fallback both down) | Check EC2 internet connectivity (`curl -s https://google.com`). Verify Yahoo Finance is accessible (`curl -s "https://query1.finance.yahoo.com/v8/finance/chart/SPY"`). If internet is up but Yahoo is down, check if Yahoo has changed their endpoints (common ~2x/year). Try Alpha Vantage or Twelve Data as manual override. If ALL providers are down, the system enters DEGRADED MODE automatically -- verify no signals are being generated. Send manual Telegram alert: `MULTI-PROVIDER OUTAGE -- all signals suspended.` |
| Data from primary and fallback disagree by >1% for same ticker | Check `data/fallback_log.jsonl` for the discrepancy entry. Verify both providers are returning data for the same timestamp (clock skew can cause apparent disagreement). If timestamps match and disagreement persists, use the primary provider (Polygon for US, IBKR for LSE) as source of truth. Log the discrepancy with both values for post-market investigation. If disagreement exceeds 5%, suppress signals for that ticker until resolved. |
| Migration is causing increased latency (scan cycle exceeding 60s budget) | Measure the delta: check `artifacts/system_state.json` for cycle timing. Identify which provider call is slow (`docker logs nzt48 --tail 200 | grep latency`). If latency is >2x the pre-migration baseline, consider rolling back that specific provider to yfinance using `FORCE_YFINANCE=true`. If latency is 1.5-2x, investigate connection pooling or batching optimisations before rollback. Document findings in incident log. |
| A provider changes their API schema (fields renamed, removed, or restructured) | Immediate: activate fallback chain for all fields from that provider. Check provider changelog or API documentation for announced changes. Update the provider adapter module (`data/providers/{provider}_provider.py`) to map new schema to internal field names. Run the acceptance tests (AT-1 through AT-4) against the updated adapter. Do NOT switch back to the updated provider until all tests pass. |

---

## 12. ACCEPTANCE TESTS

Each test must PASS before the corresponding migration phase is considered complete.

### AT-1: DataFeedRouter Abstraction

- [ ] `test_fallback_chain_us`: Block Polygon; verify yfinance serves US data within 30s.
- [ ] `test_fallback_chain_lse`: Block IBKR; verify yfinance serves LSE data within 30s.
- [ ] `test_provenance_tag`: Every returned DataFrame has `_provenance` column with valid value.
- [ ] `test_confidence_penalty`: Signals using FALLBACK_DATA have confidence reduced by 15.
- [ ] `test_health_check`: `health_check()` returns correct status for each provider (mock failures).

### AT-2: Polygon Integration

- [ ] `test_polygon_daily_ohlcv`: Fetch 30 days of NVDA daily data; compare to yfinance; match rate > 99.5%.
- [ ] `test_polygon_intraday`: Fetch 1 day of 1-min NVDA bars; verify count > 350 (6.5h market).
- [ ] `test_polygon_rate_limit`: Issue 100 requests in 60s; verify no 429 errors on Developer tier.
- [ ] `test_polygon_historical`: Fetch 5 years of SPY daily data; verify no gaps on trading days.

### AT-3: IBKR Integration

- [ ] `test_ibkr_lse_daily`: Fetch 30 days of QQQ3.L daily data; compare to yfinance; match rate > 99%.
- [ ] `test_ibkr_lse_intraday`: Fetch 1 day of 5-min QQQ3.L bars during LSE hours.
- [ ] `test_ibkr_gbp_normalization`: Verify all LSE prices are in GBP (not GBp); sanity check range.
- [ ] `test_ibkr_leveraged_etp`: Fetch data for all 12 CORE ISA tickers; verify no empty responses.
- [ ] `test_ibkr_reconnect`: Kill TWS connection; verify auto-reconnect within 60s.

### AT-4: End-to-End

- [ ] `test_full_scan_cycle`: Run one complete 60s scan cycle with new provider stack; verify all tickers have data.
- [ ] `test_pdf_data_quality_section`: Generate daily PDF; verify "Data Quality" section is present and accurate.
- [ ] `test_fallback_log`: After simulated outage, verify `data/fallback_log.jsonl` has correct entries.
- [ ] `test_telegram_degraded_alert`: Simulate fallback; verify Telegram receives `DATA DEGRADED` message.

---

## 13. PROOF ARTIFACTS

Upon completion of the migration, the following artifacts must exist:

| # | Artifact | Location | Purpose |
|---|----------|----------|---------|
| PA-1 | DataFeedRouter module | `data/feed_router.py` | Unified data abstraction |
| PA-2 | Polygon provider | `data/providers/polygon_provider.py` | US data primary |
| PA-3 | IBKR provider | `data/providers/ibkr_provider.py` | LSE data primary |
| PA-4 | Fallback log | `data/fallback_log.jsonl` | Historical fallback events |
| PA-5 | Provider health dashboard | Dashboard tab or Telegram `/health` command | Live provider status |
| PA-6 | Data quality section in PDF | `data/reports/*.pdf` | Daily provenance audit |
| PA-7 | Parallel validation report | `annexes/PARALLEL_VALIDATION_REPORT.md` | Side-by-side data comparison results |
| PA-8 | Cost tracking spreadsheet | `annexes/DATA_COST_TRACKER.csv` | Monthly spend per provider |
| PA-9 | Updated `settings.yaml` | `config/settings.yaml` PART VIII | New provider configuration |
| PA-10 | Acceptance test results | `tests/test_data_vendors.py` | All AT-* tests passing |

---

## 14. ROLLBACK PLAN

If the migration causes data quality issues that cannot be resolved within 48 hours:

1. Set `FORCE_YFINANCE=true` in environment to revert all data to yfinance.
2. The `DataFeedRouter` must support this override flag to bypass the provider hierarchy.
3. Notify via Telegram: `DATA ROLLBACK: Reverted to yfinance primary. Reason: {reason}`.
4. Open incident ticket. Do not re-attempt migration until root cause is identified and fixed.

---

## 15. SIGN-OFF

| Role | Name | Date | Signature |
|------|------|------|-----------|
| System Operator | | | |
| Data Quality Reviewer | | | |

**This plan must be signed off before any provider account is created or any code is deployed.**
