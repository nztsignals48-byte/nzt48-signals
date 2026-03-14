# NZT-48 Phase 8: Data Vendor Migration Plan

**Status**: PLAN ONLY -- requires approval before any implementation
**Author**: Institutional Audit Phase 8
**Date**: 2026-02-27
**Goal**: Move yfinance from primary to fallback; establish reliable, validated data pipeline

---

## 1. Current State: yfinance Usage Audit

### 1.1 Direct yfinance Import Locations

The codebase has **25+ files** that directly `import yfinance as yf`. This is the core problem:
yfinance is scattered throughout the codebase rather than centralised behind an abstraction.

**Critical Path (data pipeline):**

| File                                  | Usage                                           | Calls        |
|---------------------------------------|--------------------------------------------------|--------------|
| `feeds/data_feeds.py`                 | DataFeedManager -- primary data gateway           | yf.download, yf.Ticker |
| `data_hub/sources/yfinance_source.py` | YFinanceSource -- DataHub fallback                | yf.download, yf.Ticker |
| `uk_isa/data_health.py`              | DataHealthGate -- batch validation                | yf.download   |
| `uk_isa/lse_registry.py`            | LSERegistry -- batch download for registry refresh | yf.download   |
| `uk_isa/correlation_engine.py`       | Correlation matrix computation                    | yf.download   |
| `uk_isa/volatility_regime.py`        | Volatility regime classification                  | yf.download   |
| `uk_isa/multiframe_analytics.py`     | Multi-timeframe technical analysis                | yf.download   |
| `uk_isa/predictive_scoring.py`       | Predictive scoring OHLCV fetch                    | yf.download   |
| `uk_isa/sector_rotation.py`          | Sector rotation analysis                          | yf.download   |
| `uk_isa/peer_finder.py`             | Peer correlation finder                           | yf.download   |
| `uk_isa/gate_diagnostics.py`        | Gate diagnostic data fetch                        | yf.download   |

**PDF Generation (delivery):**

| File                                  | Usage                                           |
|---------------------------------------|--------------------------------------------------|
| `delivery/pdf_v2_risk.py`            | Risk PDF data fetch + VIX/SPX/NDX                |
| `delivery/pdf_v2_momentum.py`        | Momentum PDF historical data                     |
| `delivery/pdf_v2_daily_review.py`    | Daily review PDF bars                            |
| `delivery/pdf_intelligence.py`       | Intelligence report live data                    |
| `delivery/mega_report.py`           | Mega report data fetch                           |

**Signal Engine:**

| File                                  | Usage                                           |
|---------------------------------------|--------------------------------------------------|
| `signal_engine/engine.py`            | Signal generation OHLCV fetch                    |
| `signal_engine/intel_card.py`        | Intel card data fetch                            |

**Other:**

| File                                  | Usage                                           |
|---------------------------------------|--------------------------------------------------|
| `feeds/market_structure.py`          | VIX/VIX3M term structure                         |
| `feeds/calendar_feed.py`            | Earnings dates, IV rank                          |
| `command_center/tick_loop.py`        | Quick regime from SPX/VIX                        |
| `dashboard/api.py`                   | Dashboard API endpoints                          |
| `learning/outcomes_engine.py`        | Trade outcome verification                       |
| `diagnostics_setup.py`              | Diagnostics                                      |
| `diagnostics_live.py`               | Live diagnostics                                 |

### 1.2 What yfinance Currently Provides

1. **Daily OHLCV bars**: `yf.download(ticker, period="5d"/"1mo"/"1y"/"max", interval="1d")`
2. **Intraday bars**: `yf.download(ticker, interval="1m"/"5m"/"15m"/"1h", period="1d"/"5d")`
3. **Real-time quotes**: `yf.Ticker(ticker).fast_info.last_price`
4. **Ticker info**: `yf.Ticker(ticker).info` -- pre/post market prices, previous close
5. **Batch downloads**: `yf.download(list_of_tickers, group_by="ticker")`
6. **Earnings calendar**: `yf.Ticker(ticker).calendar`

### 1.3 Known Reliability Issues

1. **Volume data for .L tickers**: Often returns 0 or stale values (documented in PAPER_LAUNCH_AUDIT.md)
2. **Pence vs pounds confusion**: LSE ETPs sometimes return prices in pence (GBX) instead of pounds (GBP), 100x scale error
3. **Rate limiting**: Yahoo periodically throttles requests, returning empty DataFrames
4. **MultiIndex columns**: yfinance >= 0.2.40 returns MultiIndex columns requiring special handling (already handled in `data_feeds.py`)
5. **Stale data during off-hours**: Returns previous session data with no indication it's stale
6. **No bid/ask spread**: Only close prices -- bid/ask is proxied at 10bps in `yfinance_source.py`
7. **No SLA**: Free service with no uptime guarantee, can break at any time
8. **Corporate action inconsistency**: auto_adjust sometimes misses or incorrectly applies leveraged ETP corporate actions

### 1.4 Existing Mitigation Already Built

The system already has significant infrastructure anticipating this migration:

- `data_hub/hub.py`: DataHub with IBKR (truth) -> yfinance (fallback) -> Validator (comparison) architecture
- `data_hub/sources/ibkr_source.py`: IBKRSource stub (IS_AVAILABLE = False)
- `data_hub/sources/validator_source.py`: ValidatorSource stub pointing to Polygon/Tiingo
- `data_hub/models.py`: `DataReliabilityScore` model with source tracking
- `feeds/data_feeds.py`: DataFeedManager with yfinance -> TwelveData -> FMP -> AlphaVantage fallback chain
- `feeds/data_validator.py`: DataFeedValidator with quality scoring (0-100)

The problem: **most modules bypass DataHub/DataFeedManager entirely** and call `yf.download()` directly.

---

## 2. Vendor Evaluation Matrix

### 2.1 Polygon.io

| Criterion              | Assessment                                              | Score |
|------------------------|---------------------------------------------------------|-------|
| LSE .L ticker coverage | Limited. Major LSE stocks YES, leveraged ETPs UNLIKELY  | 2/10  |
| US ticker coverage     | Excellent. Full OHLCV + trades + quotes                 | 10/10 |
| Historical depth       | Up to 20+ years for US equities                         | 9/10  |
| Intraday data          | 1-minute bars, tick data available                      | 10/10 |
| Data quality           | Institutional grade, corporate action adjusted          | 9/10  |
| API design             | Clean REST API, WebSocket for real-time                 | 9/10  |
| Rate limits            | Unlimited on paid plans                                 | 9/10  |
| Pricing                | $29/mo Starter, $199/mo Developer                       | 7/10  |
| Python SDK             | Official `polygon-api-client` package                   | 9/10  |
| Latency                | <100ms REST, real-time WebSocket                        | 9/10  |
| **Verdict**            | **Excellent for US intel tickers. Cannot be primary for LSE ETPs.** | |

### 2.2 TradingView

| Criterion              | Assessment                                              | Score |
|------------------------|---------------------------------------------------------|-------|
| LSE .L ticker coverage | Excellent visual coverage. But...                       | N/A   |
| Automated API access   | **PROHIBITED by Terms of Service**                      | 0/10  |
| Legal for our use      | NO. ToS Section 7 prohibits automated/systematic use    | 0/10  |
| **Verdict**            | **DO NOT USE. ToS violation. Manual research only.**    | |

### 2.3 IBKR Historical Data API

| Criterion              | Assessment                                              | Score |
|------------------------|---------------------------------------------------------|-------|
| LSE .L ticker coverage | Best available. All tradeable LSE ETPs covered          | 9/10  |
| US ticker coverage     | Complete                                                | 10/10 |
| Historical depth       | 1-2 years standard, longer with market data subscription| 7/10  |
| Intraday data          | 1-min, 5-min, 1-hour bars                              | 8/10  |
| Data quality           | Exchange-sourced, high quality                          | 9/10  |
| API design             | ib_insync wrapper over TWS API. Complex but powerful    | 6/10  |
| Rate limits            | ~60 requests per 10 minutes                             | 5/10  |
| Pricing                | Requires IBKR account. LSE data: ~$1-4/mo              | 8/10  |
| Python SDK             | `ib_insync` (well-maintained, async-capable)            | 7/10  |
| Latency                | Real-time streaming available                           | 8/10  |
| **Verdict**            | **Best primary source for LSE ETPs. Requires IBKR account.** | |

### 2.4 Stooq

| Criterion              | Assessment                                              | Score |
|------------------------|---------------------------------------------------------|-------|
| LSE .L ticker coverage | Partial. WisdomTree ETPs: YES. GraniteShares: LIMITED   | 5/10  |
| US ticker coverage     | Good daily coverage                                     | 7/10  |
| Historical depth       | 5+ years where available                                | 8/10  |
| Intraday data          | Daily only                                              | 2/10  |
| Data quality           | Good for daily, no corporate action adjustment          | 6/10  |
| API design             | CSV download (URL-based, no official API)               | 4/10  |
| Rate limits            | Informal, be polite (~10 req/min)                       | 5/10  |
| Pricing                | Free                                                    | 10/10 |
| Python SDK             | `pandas_datareader` with Stooq backend                  | 5/10  |
| Latency                | Not real-time, daily snapshots                          | 3/10  |
| **Verdict**            | **Free daily validator. Use for cross-source reconciliation.** | |

### 2.5 Alpha Vantage

| Criterion              | Assessment                                              | Score |
|------------------------|---------------------------------------------------------|-------|
| LSE .L ticker coverage | Claims support, but in practice unreliable for ETPs     | 3/10  |
| US ticker coverage     | Good                                                    | 7/10  |
| Historical depth       | 20+ years daily (US), limited for LSE                   | 6/10  |
| Intraday data          | 1-min, 5-min intraday for supported tickers             | 6/10  |
| Data quality           | Acceptable, known occasional gaps                       | 6/10  |
| API design             | Simple REST API                                         | 7/10  |
| Rate limits            | 25/day free, 75/min premium                             | 4/10  |
| Pricing                | Free (25/day), $49/mo premium                           | 7/10  |
| Python SDK             | `alpha_vantage` package (unofficial but popular)        | 6/10  |
| **Verdict**            | **Already integrated as fallback. Keep as-is. Limited value for LSE ETPs.** | |

### 2.6 Twelve Data

| Criterion              | Assessment                                              | Score |
|------------------------|---------------------------------------------------------|-------|
| LSE .L ticker coverage | Some LSE support, ETPs coverage uncertain               | 4/10  |
| US ticker coverage     | Good                                                    | 8/10  |
| Historical depth       | Varies by tier, generally good                          | 7/10  |
| Intraday data          | 1-min through daily                                     | 8/10  |
| Data quality           | Good for US, variable for international                 | 7/10  |
| API design             | Clean REST + WebSocket                                  | 8/10  |
| Rate limits            | 800/day free, higher on paid                            | 6/10  |
| Pricing                | Free (800/day), $29/mo Basic                            | 7/10  |
| Python SDK             | `twelvedata` official package                           | 7/10  |
| **Verdict**            | **Already integrated. Good US supplementary source. Limited LSE ETP value.** | |

### 2.7 Other Providers Worth Noting

| Provider       | Notes                                                          |
|----------------|----------------------------------------------------------------|
| **Databento**  | Institutional-grade. LSE coverage unknown for niche ETPs. $100+/mo |
| **Norgate Data** | Australian provider with good international coverage. Desktop-focused. |
| **EOD Historical Data** | Claims LSE coverage. $20/mo. Worth testing for .L ETPs. |
| **Tiingo**     | Good US data, IEX integration. LSE coverage minimal. |
| **MarketStack** | Claims LSE end-of-day. Worth testing. Free tier available. |

---

## 3. Recommended Architecture

### 3.1 Provider Interface Pattern

```
                          +------------------+
                          |   Application    |
                          |   (25+ modules)  |
                          +--------+---------+
                                   |
                          +--------v---------+
                          |    DataHub       |  <-- Single entry point
                          |  (data_hub/hub)  |     (already exists)
                          +--------+---------+
                                   |
                  +----------------+----------------+
                  |                |                 |
          +-------v------+  +-----v-------+  +-----v--------+
          | PrimarySource|  | FallbackSrc |  | ValidatorSrc |
          | (IBKR/EOD)   |  | (yfinance)  |  | (Stooq/Poly) |
          +--------------+  +-------------+  +--------------+
```

### 3.2 Recommended Primary Source: IBKR (if account available)

**Rationale:**
- Only provider with confirmed coverage of ALL 12 core LSE leveraged ETPs
- Exchange-sourced data (highest quality for LSE)
- Real-time streaming capability
- Already has a stub in the codebase (`ibkr_source.py`)
- Cost: minimal (existing account + LSE data subscription)

**If no IBKR account**: Use yfinance as primary with Stooq as validator (current effective state, formalised). Evaluate EOD Historical Data ($20/mo) as a potential primary -- requires testing .L ticker coverage.

### 3.3 Recommended Validator: Stooq + Polygon.io

- **Stooq**: Free daily validation for WisdomTree ETPs
- **Polygon.io** ($29/mo): Validation for US intel tickers (QQQ, SPY, NVDA, etc.)

### 3.4 yfinance Role After Migration

yfinance becomes the **last-resort fallback** only:
- Called only when primary AND validator both fail
- Never called directly from application modules (all access through DataHub)
- Data from yfinance always tagged with `source="yfinance"` and `reliability_penalty=0.05`

---

## 4. Migration Phases

### Phase 1: Provider Abstraction Layer (SAFE -- no behaviour change)

**Goal**: Create a clean `DataProvider` interface that all sources implement.
**Risk**: Zero -- additive only, no existing code changes.

**Deliverables:**
- `data_hub/sources/base_provider.py` -- abstract base class
- Refactor `YFinanceSource`, `IBKRSource`, `ValidatorSource` to implement interface
- Add `StooqSource`, `PolygonSource` stubs implementing same interface
- Unit tests for interface compliance

**Rollback**: Delete new files. No existing code affected.

### Phase 2: Centralise All yfinance Calls Through DataHub (MEDIUM RISK)

**Goal**: Replace all direct `yf.download()` / `yf.Ticker()` calls with `DataHub.get_bars()`.
**Risk**: Medium -- changes 25+ files. Thorough testing required.

**Approach:**
1. Create a compatibility shim: `DataHub.download()` that mimics `yf.download()` return format
2. File-by-file replacement with the shim
3. Each replacement is a separate commit (easy to revert individual files)
4. Run full system test after each batch of replacements

**Rollback**: Git revert individual commits.

**Files to change (ordered by risk, lowest first):**
1. `diagnostics_setup.py`, `diagnostics_live.py` (low traffic, easy to test)
2. `uk_isa/gate_diagnostics.py` (diagnostic only)
3. `uk_isa/data_health.py` (already has yf.download, swap to DataHub)
4. `uk_isa/lse_registry.py` (batch download, test carefully)
5. `uk_isa/correlation_engine.py`, `uk_isa/volatility_regime.py`
6. `uk_isa/multiframe_analytics.py`, `uk_isa/predictive_scoring.py`
7. `uk_isa/sector_rotation.py`, `uk_isa/peer_finder.py`
8. `delivery/pdf_v2_*.py` (PDF generation, test output quality)
9. `signal_engine/engine.py`, `signal_engine/intel_card.py`
10. `feeds/market_structure.py`, `feeds/calendar_feed.py`
11. `dashboard/api.py` (highest traffic, change last)
12. `command_center/tick_loop.py`

### Phase 3: Implement Primary Provider (IBKR or best available)

**Goal**: Flesh out `IBKRSource` (or chosen primary) with real implementation.
**Risk**: Low -- additive, DataHub already prefers IBKR when available.

**Deliverables:**
- Full `IBKRSource` implementation using `ib_insync`
- Connection management (reconnect on failure)
- Rate limiting (respect IBKR's 60-per-10-min limit)
- Integration tests with TWS Paper account

**Rollback**: Set `IBKRSource.IS_AVAILABLE = False`. System falls back to yfinance.

### Phase 4: Implement Validator Provider (Stooq + Polygon)

**Goal**: Replace `ValidatorSource` stub with real Stooq daily comparison + Polygon for US tickers.
**Risk**: Low -- validation is advisory, does not change trading decisions.

**Deliverables:**
- `StooqSource` implementation (CSV download, daily bars)
- `PolygonSource` implementation (REST API, daily + intraday for US tickers)
- Cross-source reconciliation logic in `ValidatorSource.compare()`
- Reliability penalty calculation when sources disagree

**Rollback**: Set `ValidatorSource.IS_AVAILABLE = False`. System runs unvalidated (current state).

### Phase 5: Switch Primary and Backfill Historical Data

**Goal**: IBKR becomes primary, yfinance becomes fallback. Backfill historical data.
**Risk**: Medium -- changing the source of truth for live trading decisions.

**Pre-requisites:**
- Phase 1-4 complete and tested
- At least 2 weeks of parallel running (both sources active, compare results)
- No disagreements > 1% between IBKR and yfinance for any core ticker during parallel period

**Execution:**
1. Enable IBKR as primary in DataHub
2. Run backfill script (see `scripts/backfill_5y.py`)
3. Monitor for 1 week in PAPER mode
4. If stable, document as production-ready

**Rollback**: Set `IBKRSource.IS_AVAILABLE = False` in config. Instant fallback to yfinance.

---

## 5. Cost Analysis

### 5.1 Current State (Free)

| Item                  | Monthly Cost | Annual Cost |
|-----------------------|--------------|-------------|
| yfinance              | $0           | $0          |
| Alpha Vantage (free)  | $0           | $0          |
| Twelve Data (free)    | $0           | $0          |
| FMP (free)            | $0           | $0          |
| **Total**             | **$0**       | **$0**      |

**Hidden cost**: Unreliable data leads to bad trading decisions. Hard to quantify but real.

### 5.2 Recommended Setup

| Item                        | Monthly Cost | Annual Cost | Purpose                       |
|-----------------------------|--------------|-------------|-------------------------------|
| IBKR account (existing)     | $0           | $0          | Primary source for LSE ETPs   |
| IBKR LSE data subscription  | ~$4          | ~$48        | LSE real-time + historical    |
| Polygon.io Starter          | $29          | $348        | US ticker validation + backup |
| Stooq                       | $0           | $0          | Daily cross-validation        |
| yfinance (fallback)         | $0           | $0          | Last-resort fallback          |
| **Total**                   | **~$33**     | **~$396**   |                               |

### 5.3 Premium Setup (if budget allows)

| Item                        | Monthly Cost | Annual Cost | Purpose                       |
|-----------------------------|--------------|-------------|-------------------------------|
| IBKR + LSE data             | ~$4          | ~$48        | Primary                       |
| Polygon.io Developer        | $199         | $2,388      | Full US data + WebSocket      |
| EOD Historical Data         | $20          | $240        | LSE ETP backup + validation   |
| Alpha Vantage Premium       | $49          | $588        | Intraday fallback             |
| **Total**                   | **~$272**    | **~$3,264** |                               |

---

## 6. Rollback Plan Summary

Every phase has an independent rollback path:

| Phase | Rollback Action                                          | Time to Rollback |
|-------|----------------------------------------------------------|------------------|
| 1     | Delete new files (no existing code changed)              | < 1 minute       |
| 2     | `git revert` individual file commits                     | < 5 minutes      |
| 3     | Set `IBKRSource.IS_AVAILABLE = False`                    | < 1 minute       |
| 4     | Set `ValidatorSource.IS_AVAILABLE = False`               | < 1 minute       |
| 5     | Revert to yfinance primary (Phase 3 rollback)            | < 1 minute       |

**Critical safety rule**: NEVER remove yfinance as a fallback option until a paid provider has been running as primary for at least 3 months with zero data-loss incidents.

---

## 7. Success Criteria

The migration is considered complete when:

1. **Zero direct yfinance calls** outside of `data_hub/sources/yfinance_source.py`
2. **Primary source** (IBKR or equivalent) provides data for all 12 core ISA tickers
3. **Validator source** confirms data within 2% tolerance for daily closes
4. **DataReliabilityScore** is computed for every data point entering the system
5. **Historical data** available in parquet for all tickers from inception (or 5 years)
6. **Fallback chain** tested: primary down -> fallback activates within 1 scan cycle (60s)
7. **No increase in data-related warnings** compared to yfinance-only baseline

---

## 8. Timeline Estimate

| Phase | Duration    | Dependencies                        |
|-------|-------------|-------------------------------------|
| 1     | 1-2 days    | None                                |
| 2     | 3-5 days    | Phase 1 complete                    |
| 3     | 2-3 days    | Phase 1 complete, IBKR account      |
| 4     | 2-3 days    | Phase 1 complete                    |
| 5     | 1-2 weeks   | Phase 2-4 complete, parallel testing |
| **Total** | **3-4 weeks** | Including testing and validation |

Phases 1, 3, and 4 can run in parallel. Phase 2 is the critical path.
