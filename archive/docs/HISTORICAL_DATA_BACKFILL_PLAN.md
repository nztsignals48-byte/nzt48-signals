# NZT-48 Phase 8: 5-Year Historical Data Backfill Plan

**Status**: PLAN ONLY -- requires approval and API keys before execution
**Author**: Institutional Audit Phase 8
**Date**: 2026-02-27
**Scope**: 5 years of OHLCV data for the ISA leveraged ETP universe + intel tickers

---

## 1. Data Requirements

### 1.1 Target Universe

**Core ISA Universe (12 tickers -- MUST HAVE):**

| Ticker   | Name                                       | Leverage | Underlying   |
|----------|--------------------------------------------|----------|--------------|
| QQQ3.L   | WisdomTree NASDAQ 100 3x Daily ETP         | 3x       | NDX/QQQ      |
| 3LUS.L   | WisdomTree S&P 500 3x Daily ETP            | 3x       | SPX/SPY      |
| 3SEM.L   | WisdomTree Semiconductors 3x Daily ETP     | 3x       | SOX/SMH      |
| GPT3.L   | WisdomTree US AI 3x Daily ETP              | 3x       | AI/Tech      |
| NVD3.L   | GraniteShares NVIDIA 3x Long Daily ETP     | 3x       | NVDA         |
| TSL3.L   | GraniteShares Tesla 3x Long Daily ETP      | 3x       | TSLA         |
| TSM3.L   | GraniteShares TSMC 3x Long Daily ETP       | 3x       | TSM          |
| MU2.L    | GraniteShares Micron 2x Long Daily ETP     | 2x       | MU           |
| QQQS.L   | WisdomTree NASDAQ 100 3x Short ETP         | -3x      | NDX/QQQ      |
| 3USS.L   | WisdomTree S&P 500 3x Short ETP            | -3x      | SPX/SPY      |
| QQQ5.L   | WisdomTree NASDAQ 100 5x Daily ETP         | 5x       | NDX/QQQ      |
| SP5L.L   | WisdomTree S&P 500 5x Daily ETP            | 5x       | SPX/SPY      |

**Extended Universe (9 additional tickers -- NICE TO HAVE):**
AMD3.L, ARM3.L, NVDS.L, TSLS.L, 3LDE.L, 3LEU.L, 3GOL.L, 3SIL.L, 3OIL.L

**Intel Context Tickers (14 tickers -- for regime/correlation analysis):**
QQQ, SPY, SMH, SOXX, ^VIX, TLT, GLD, USO, DX-Y.NYB, NVDA, TSLA, TSM, MU, AMD

### 1.2 Timeframes Required

| Timeframe | Period       | Purpose                                        | Priority |
|-----------|--------------|-------------------------------------------------|----------|
| Daily     | 5 years      | Backtesting, regime classification, momentum    | P0       |
| Weekly    | 5 years      | Long-term trend analysis, sector rotation       | P0       |
| 1-hour    | 2 years      | Intraday pattern research, entry optimisation   | P1       |
| 5-minute  | 1 year       | ORB analysis, session profiling, MFE/MAE        | P2       |
| 1-minute  | 6 months     | Microstructure research, slippage modelling     | P3       |

### 1.3 Data Fields Required

**Per bar (all timeframes):**
- timestamp (UTC, timezone-aware)
- open, high, low, close (adjusted for splits/corporate actions)
- volume
- source (provider that supplied this bar)
- adjusted (boolean: corporate-action-adjusted?)

**Per instrument (metadata):**
- ticker, ISIN, FIGI (where available)
- exchange, currency, leverage factor, bias (long/short)
- inception date, delisting date (if applicable)
- issuer (WisdomTree, GraniteShares, Leverage Shares)
- underlying index/stock ticker

---

## 2. Per-Ticker Data Availability Assessment

### 2.1 Availability by Provider

LSE-listed leveraged ETPs (.L tickers) have notoriously poor coverage across data vendors.
Many of these products were launched in 2020-2023, so 5 full years of history may not exist.

| Ticker   | Inception (approx) | yfinance | Polygon.io | Alpha Vantage | Stooq  | IBKR     |
|----------|---------------------|----------|------------|---------------|--------|----------|
| QQQ3.L   | ~2017               | YES (daily/intraday, patchy volume) | UNLIKELY (LSE .L limited) | UNLIKELY | YES (daily) | YES (if account) |
| 3LUS.L   | ~2017               | YES      | UNLIKELY   | UNLIKELY       | YES    | YES      |
| 3SEM.L   | ~2020               | YES      | UNLIKELY   | UNLIKELY       | MAYBE  | YES      |
| GPT3.L   | ~2023               | YES (short history) | NO  | NO            | MAYBE  | YES      |
| NVD3.L   | ~2022               | YES      | NO         | NO            | MAYBE  | YES      |
| TSL3.L   | ~2021               | YES      | NO         | NO            | MAYBE  | YES      |
| TSM3.L   | ~2022               | YES      | NO         | NO            | MAYBE  | YES      |
| MU2.L    | ~2022               | YES      | NO         | NO            | MAYBE  | YES      |
| QQQS.L   | ~2017               | YES      | UNLIKELY   | UNLIKELY       | YES    | YES      |
| 3USS.L   | ~2017               | YES      | UNLIKELY   | UNLIKELY       | YES    | YES      |
| QQQ5.L   | ~2022               | YES      | NO         | NO            | MAYBE  | YES      |
| SP5L.L   | ~2022               | YES      | NO         | NO            | MAYBE  | YES      |

**Intel tickers (US-listed):**
Full 5-year daily + intraday coverage from ALL providers (QQQ, SPY, NVDA, etc. are tier-1 instruments).

### 2.2 Critical Finding

**No single paid provider reliably covers ALL LSE leveraged ETPs.**

- **Polygon.io**: Excellent US coverage. LSE coverage is limited to major stocks and popular ETFs. Leveraged ETPs like QQQ3.L, NVD3.L are very unlikely to be in Polygon's LSE feed.
- **Alpha Vantage**: Claims LSE support but in practice returns empty or stale data for niche ETPs.
- **Stooq**: Free daily-only historical data. Covers some WisdomTree products (QQQ3.L, 3LUS.L, QQQS.L, 3USS.L). Does NOT cover GraniteShares single-stock ETPs.
- **IBKR**: Best coverage of LSE leveraged ETPs (they are tradeable on IBKR). Requires active account. Historical data API limits apply.
- **yfinance**: Most complete free source for .L tickers. Data quality issues (volume often zero or stale, pence/pounds confusion, occasional gaps).

### 2.3 Realistic Assessment

For the **Core 12** ISA tickers:
- **Daily bars**: yfinance likely has data from inception for all 12. Quality varies.
- **Intraday bars**: yfinance caps at 7 calendar days for 1m, 60 days for 1h. Historical intraday requires IBKR or paid vendor.
- **5-year history**: Only possible for tickers that existed 5 years ago (QQQ3.L, 3LUS.L, QQQS.L, 3USS.L). Newer tickers (GPT3.L, NVD3.L, etc.) will have inception-to-present only.

---

## 3. Corporate Actions Handling

### 3.1 Why This Matters for Leveraged ETPs

Leveraged ETPs undergo corporate actions that profoundly affect historical price series:

1. **Reverse splits (consolidations)**: Common when ETP price approaches zero due to leverage decay. A 10:1 consolidation makes historical prices appear 10x higher. Must be adjusted.
2. **Share splits**: Less common but possible when price grows too large.
3. **Ticker changes**: Product rebranding (e.g., issuer change from Boost to WisdomTree).
4. **Delistings**: Some leveraged ETPs get delisted entirely (loss of AUM, regulatory changes).
5. **NAV resets / cash distributions**: Some issuers reset the NAV periodically.

### 3.2 Adjustment Strategy

The system already has `data_hub/normalization/corporate_actions.py` with a JSON-based cache.
For backfill purposes:

1. **Source corporate actions from issuer websites**: WisdomTree and GraniteShares publish corporate action calendars.
2. **Cross-reference with LSE RNS announcements**: Regulatory News Service filings for each ISIN.
3. **yfinance auto_adjust=True**: Handles splits automatically for most tickers. Verify by comparing raw vs adjusted.
4. **Manual verification**: For each ticker, compare the first available close with the issuer's NAV history to detect any missed adjustments.
5. **Store raw AND adjusted**: Keep both series so adjustments can be re-applied if errors are found.

### 3.3 Existing Infrastructure

The system already has these modules (no changes needed to existing code):
- `data_hub/normalization/corporate_actions.py` -- load/save corporate action JSON, adjust for splits
- `data_hub/normalization/instrument_map.py` -- ISIN/FIGI mapping, hardcoded known instruments
- `data_hub/normalization/price_units.py` -- pence/pounds detection and conversion
- `data_hub/models.py` -- `CorporateAction` dataclass with type, ratio, date

---

## 4. Storage Design

### 4.1 File-Based Storage (Parquet)

```
research_data/
  {ticker}/
    daily/
      {ticker}_daily_raw.parquet          # raw OHLCV as received
      {ticker}_daily_adjusted.parquet     # split/action adjusted
    weekly/
      {ticker}_weekly_adjusted.parquet
    hourly/
      {ticker}_1h_adjusted.parquet
    intraday_5m/
      {ticker}_5m_{YYYY}.parquet          # one file per year (size management)
    intraday_1m/
      {ticker}_1m_{YYYY}_{MM}.parquet     # one file per month
    meta/
      {ticker}_corporate_actions.json
      {ticker}_source_log.json            # which provider supplied each bar
  _index/
    instrument_registry.parquet           # all tickers with metadata
    backfill_progress.json                # tracks which tickers/timeframes are done
    data_quality_report.json              # validation results per ticker
```

### 4.2 Parquet Schema (Daily)

```
timestamp:        datetime64[ns, UTC]   (partition key)
open:             float64
high:             float64
low:              float64
close:            float64
volume:           float64
adjusted_close:   float64               (after corporate actions)
source:           string                (yfinance | ibkr | polygon | stooq)
is_adjusted:      bool
quality_score:    float32               (0.0 - 1.0)
```

### 4.3 DuckDB Analytics Layer

A DuckDB database (`research_data/_analytics/nzt48_research.duckdb`) provides:
- SQL-queryable access to all parquet files via external table references
- Pre-computed views: returns, drawdowns, rolling statistics
- Cross-ticker correlation matrices
- Session-level aggregations (LSE open/close, US overlap, etc.)

DuckDB reads parquet directly with zero-copy -- no ETL step required.
Parquet files remain the source of truth; DuckDB is a read-only analytics layer.

---

## 5. Backfill Execution Plan

### Phase 1: Metadata Collection (Day 1)

1. For each ticker in CORE_UNIVERSE + EXTENDED_UNIVERSE:
   - Confirm ISIN, inception date, issuer
   - Check for known corporate actions (WisdomTree/GraniteShares websites)
   - Record expected price range (GBP) from `isa_universe.py`
2. Create `research_data/_index/instrument_registry.parquet`
3. Create `research_data/_index/backfill_progress.json` with all tickers marked "pending"

### Phase 2: Daily Data Backfill via yfinance (Day 1-2)

1. For each ticker, download `max` period daily bars via yfinance
2. Run pence/pounds normalisation (`price_units.py` logic)
3. Run corporate action adjustment (`corporate_actions.py` logic)
4. Store raw and adjusted parquet files
5. Log source and quality metadata
6. Validation: check for gaps (missing trading days), outliers (>20% daily move without corresponding underlying move), zero-volume days

### Phase 3: Cross-Validation with Stooq (Day 2-3)

1. Download daily data from Stooq for tickers that Stooq covers
2. Compare close prices: flag any day where abs(yfinance_close - stooq_close) / stooq_close > 1%
3. For flagged days, investigate and choose the more reliable value
4. Document all discrepancies in `data_quality_report.json`

### Phase 4: IBKR Historical Backfill (Day 3-5, if IBKR account available)

1. Connect via ib_insync to TWS/Gateway
2. Request historical bars for each ticker:
   - Daily: max available (IBKR typically provides 1-2 years free, more with subscription)
   - 1-hour: up to 1 year
   - 5-minute: up to 6 months (IBKR limit)
3. IBKR data becomes the "truth" baseline where available
4. Reconcile with yfinance data: replace yfinance bars where IBKR disagrees

### Phase 5: Intel Ticker Backfill (Day 5-6)

1. For US-listed intel tickers (QQQ, SPY, NVDA, etc.):
   - Download 5-year daily from yfinance (reliable for US tickers)
   - Download 1-year hourly from yfinance
   - Optionally backfill from Polygon.io or Alpha Vantage (excellent US coverage)
2. These are reference data -- quality bar is lower than for trading tickers

### Phase 6: Validation Pipeline (Day 6-7)

Run the complete validation suite (see Section 6 below) on all backfilled data.
Fix any issues. Re-backfill individual tickers if needed.

### Phase 7: DuckDB Analytics Layer (Day 7-8)

1. Create DuckDB database pointing at parquet files
2. Build views: daily_returns, drawdowns, correlation_matrix, session_profiles
3. Run sanity queries: verify row counts, date ranges, price ranges
4. Generate backfill report

---

## 6. Data Quality Validation Pipeline

### 6.1 Checks Applied Per Ticker/Timeframe

| Check                  | Description                                                   | Action on Failure     |
|------------------------|---------------------------------------------------------------|-----------------------|
| GAP_DETECTION          | Missing trading days (compare vs LSE calendar)                | Log + flag for manual review |
| OUTLIER_DETECTION      | Daily return > 30% (unusual even for 3x/5x ETPs)            | Flag + cross-check with underlying |
| ZERO_VOLUME            | Volume = 0 on known trading days                              | Flag as potentially stale |
| OHLC_SANITY            | H >= max(O,C), L <= min(O,C), H >= L                        | Reject bar              |
| NAN_INF                | Any NaN or Inf values in OHLCV                               | Reject bar              |
| PENCE_POUNDS           | Close outside expected GBP range (isa_universe.py)           | Apply scale correction  |
| CROSS_SOURCE_RECONCILE | Compare yfinance vs IBKR/Stooq close: > 2% divergence       | Flag + investigate      |
| SPLIT_DETECTION        | Sudden 50%+ price change without matching underlying move    | Check for unreported split |
| CONTINUITY             | Price vs median of last 10 bars: > 10% = suspicious          | Flag + investigate      |
| INCEPTION_CHECK        | No data before product inception date                        | Trim pre-inception rows |
| LEVERAGE_CONSISTENCY   | 3x ETP daily return vs 3 x underlying daily return: > 5% divergence | Flag (may indicate NAV drift or data error) |

### 6.2 Quality Score Computation

Each ticker/timeframe pair gets a composite quality score (0-100):

```
quality = (
    gap_score * 0.30 +       # % of expected trading days present
    outlier_score * 0.20 +   # % of bars passing outlier check
    volume_score * 0.15 +    # % of bars with non-zero volume
    ohlc_score * 0.15 +      # % of bars passing OHLC sanity
    reconcile_score * 0.20   # % of bars matching cross-source validation
)
```

Thresholds:
- quality >= 80: PASS -- data is research-grade
- quality 60-79: WARN -- usable with caveats, document gaps
- quality < 60: FAIL -- do not use for backtesting without manual review

---

## 7. Licensing and Legal Constraints

### 7.1 Per Provider

| Provider        | License Type              | Redistribution | Storage   | Cost (approx)       |
|-----------------|---------------------------|----------------|-----------|---------------------|
| yfinance        | Yahoo ToS (scraping)      | NO             | Personal use OK | Free (no guarantee of continued access) |
| Polygon.io      | Commercial API license    | NO (client-only) | Local OK  | $29-199/mo (Starter-Developer) |
| Alpha Vantage   | Free tier: personal use   | NO             | Local OK  | Free (25/day), $49/mo (premium) |
| Stooq           | Free historical data      | NO             | Personal OK | Free |
| IBKR            | Account holder access     | NO             | Local OK  | Account required, data subscriptions vary |
| TradingView     | ToS PROHIBITS automated scraping | PROHIBITED | PROHIBITED | N/A -- do not use for automated access |
| Twelve Data     | API license               | NO             | Local OK  | Free (800/day), paid tiers available |
| FMP             | API license               | NO             | Local OK  | Free (250/day), paid tiers available |

### 7.2 Key Legal Notes

1. **TradingView**: Their Terms of Service explicitly prohibit automated data access, scraping, or API usage for systematic/automated trading. DO NOT attempt to scrape TradingView charts or use their data programmatically. Manual research only.

2. **yfinance**: Relies on Yahoo Finance's undocumented APIs. Yahoo has historically been tolerant but could block access at any time. This is exactly why we need to migrate away from sole dependency on yfinance.

3. **Polygon.io**: Legitimate commercial API. However, their LSE coverage for niche leveraged ETPs is limited. Must verify specific ticker availability before purchasing a subscription.

4. **IBKR**: Best option for LSE ETP data if you have an IBKR account. Historical data is available via the TWS API (ib_insync). Rate limits apply: ~60 requests per 10 minutes for historical bars.

5. **Stooq**: Polish financial data provider. Free daily historical data. Good coverage of WisdomTree ETPs (which are registered in Ireland and cross-listed in Warsaw). Coverage of GraniteShares products is less certain.

---

## 8. Estimated Costs and Rate Limits

### 8.1 Monthly Costs

| Scenario                          | Monthly Cost | Notes                              |
|-----------------------------------|--------------|------------------------------------|
| yfinance only (current state)     | $0           | Free but unreliable, no SLA        |
| yfinance + Stooq validation       | $0           | Best free option for daily data    |
| IBKR data (existing account)      | $0-10        | LSE data subscription may apply    |
| Polygon.io Starter                | $29          | US data excellent, LSE limited     |
| Polygon.io Developer              | $199         | Better limits, still limited LSE   |
| Alpha Vantage Premium             | $49          | 75 calls/min, still limited LSE    |
| Twelve Data Basic                 | $29          | 800/day, some LSE coverage         |
| FMP Starter                       | $29          | Better fundamentals than OHLCV     |

### 8.2 Rate Limits for Backfill

| Provider        | Rate Limit              | Time to backfill 12 tickers x 5yr daily |
|-----------------|-------------------------|-----------------------------------------|
| yfinance        | ~2000 calls/hour (soft) | ~5 minutes                              |
| IBKR            | ~60 req/10min           | ~30 minutes                             |
| Stooq           | ~10 req/min (polite)    | ~15 minutes                             |
| Alpha Vantage   | 25/day (free)           | 1 day per timeframe                     |
| Polygon.io      | Unlimited (paid)        | ~5 minutes                              |
| Twelve Data     | 800/day (free)          | ~30 minutes                             |

---

## 9. Recommended Execution Order

1. **Immediate (no cost)**: yfinance daily backfill for all tickers -> parquet files
2. **Immediate (no cost)**: Stooq daily cross-validation for covered tickers
3. **If IBKR account exists**: IBKR historical backfill (highest quality source for LSE ETPs)
4. **If budget permits**: Polygon.io Starter ($29/mo) for US intel tickers + validation
5. **Future**: Evaluate Databento or Norgate Data if institutional-grade LSE ETP data is needed

---

## 10. Risks and Mitigations

| Risk                                    | Impact  | Mitigation                                      |
|-----------------------------------------|---------|-------------------------------------------------|
| yfinance access blocked by Yahoo        | HIGH    | IBKR + Stooq as alternatives, parquet cache     |
| Leveraged ETP delisted mid-backfill     | MEDIUM  | Store data up to delisting date, flag in meta   |
| Pence/pounds confusion in historical    | HIGH    | Run price_units.py detection on every bar       |
| Corporate action missed in adjustment   | HIGH    | Cross-check with issuer NAV history             |
| Product inception < 5 years ago         | LOW     | Accept shorter history, document in metadata    |
| IBKR rate limiting during backfill      | LOW     | Exponential backoff, spread over multiple days  |
| Data vendor ToS change                  | MEDIUM  | Store data locally in parquet (vendor-agnostic) |
