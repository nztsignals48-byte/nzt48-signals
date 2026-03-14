# NZT-48 Research Data Store

**Purpose**: Centralised, vendor-agnostic historical data store for backtesting, analytics, and War Room research.
**Format**: Apache Parquet (columnar, compressed, schema-enforced)
**Analytics**: DuckDB for SQL queries over parquet files (zero-copy reads)

---

## Directory Structure

```
research_data/
|
+-- {TICKER}/                          # One directory per instrument
|   +-- daily/
|   |   +-- {TICKER}_daily_raw.parquet          # Raw OHLCV as received from source
|   |   +-- {TICKER}_daily_adjusted.parquet     # Corporate-action-adjusted series
|   +-- weekly/
|   |   +-- {TICKER}_weekly_adjusted.parquet
|   +-- hourly/
|   |   +-- {TICKER}_1h_adjusted.parquet
|   +-- intraday_5m/
|   |   +-- {TICKER}_5m_{YYYY}.parquet          # One file per calendar year
|   +-- intraday_1m/
|   |   +-- {TICKER}_1m_{YYYY}_{MM}.parquet     # One file per month
|   +-- meta/
|       +-- {TICKER}_corporate_actions.json      # Split/consolidation/delisting events
|       +-- {TICKER}_source_log.json             # Provenance: which source supplied each bar
|
+-- _index/
|   +-- instrument_registry.parquet              # Master instrument metadata table
|   +-- backfill_progress.json                   # Tracks completion status per ticker/timeframe
|   +-- data_quality_report.json                 # Validation results per ticker
|
+-- _analytics/
|   +-- nzt48_research.duckdb                    # DuckDB analytics database (read-only over parquet)
|
+-- README.md                                    # This file
```

---

## Parquet Schema: Daily Bars

All daily parquet files follow this schema:

| Column          | Type              | Description                                    |
|-----------------|-------------------|------------------------------------------------|
| timestamp       | datetime64[ns, UTC] | Bar timestamp (UTC, timezone-aware)           |
| open            | float64           | Opening price (GBP for .L tickers)             |
| high            | float64           | Session high                                   |
| low             | float64           | Session low                                    |
| close           | float64           | Closing price                                  |
| volume          | float64           | Bar volume (shares traded)                     |
| adjusted_close  | float64           | Close adjusted for splits/corporate actions    |
| source          | string            | Data provider (yfinance, ibkr, polygon, stooq) |
| is_adjusted     | bool              | Whether corporate action adjustment was applied|
| quality_score   | float32           | Per-bar quality score (0.0 to 1.0)             |

**Raw vs Adjusted files:**
- `_raw.parquet`: Prices as received from the data source. No adjustments applied.
- `_adjusted.parquet`: Prices adjusted for splits, consolidations, and other corporate actions. This is the default for backtesting.

Both files are retained so adjustments can be re-applied if errors are discovered.

---

## Parquet Schema: Intraday Bars (1m, 5m, 1h)

Same schema as daily, plus:

| Column          | Type              | Description                                    |
|-----------------|-------------------|------------------------------------------------|
| bar_interval    | string            | "1m", "5m", "1h"                               |
| session         | string            | "LSE" / "US_OVERLAP" / "PRE" / "POST"          |

---

## Instrument Registry Schema

`_index/instrument_registry.parquet`:

| Column          | Type    | Description                                      |
|-----------------|---------|--------------------------------------------------|
| ticker          | string  | Yahoo Finance ticker symbol (e.g., QQQ3.L)       |
| isin            | string  | International Securities Identification Number    |
| figi            | string  | Financial Instrument Global Identifier (if known) |
| name            | string  | Full product name                                |
| issuer          | string  | Product issuer (WisdomTree, GraniteShares, etc.) |
| exchange        | string  | Primary exchange (LSE, NYSE, NASDAQ)             |
| currency        | string  | Trading currency (GBP, USD)                      |
| leverage        | float64 | Leverage multiplier (positive=long, negative=short) |
| underlying      | string  | Underlying index or stock ticker                 |
| asset_class     | string  | ETP, ETF, ETC, Equity                           |
| inception_date  | date    | Product launch date                              |
| delisting_date  | date    | Delisting date (null if still active)            |
| is_active       | bool    | Currently tradeable                              |
| data_start_date | date    | Earliest date with available data                |
| data_end_date   | date    | Latest date with available data                  |

---

## Instrument ID Mapping Table

The system uses Yahoo Finance ticker symbols as the primary identifier. This table maps
to alternative identifiers used by different data providers.

| Ticker   | ISIN           | Provider Symbol (Polygon) | Provider Symbol (IBKR) | Provider Symbol (Stooq) |
|----------|----------------|---------------------------|------------------------|-------------------------|
| QQQ3.L   | IE00BLRPRL42   | (verify availability)     | QQQ3-LSE               | QQQ3.UK                 |
| 3LUS.L   | IE00BLRPRL42   | (verify)                  | 3LUS-LSE               | 3LUS.UK                 |
| 3SEM.L   | (lookup)       | (verify)                  | 3SEM-LSE               | 3SEM.UK                 |
| GPT3.L   | (lookup)       | (unlikely)                | GPT3-LSE               | (verify)                |
| NVD3.L   | IE000WWARXM5   | (unlikely)                | NVD3-LSE               | (verify)                |
| TSL3.L   | IE00BMF7G516   | (unlikely)                | TSL3-LSE               | (verify)                |
| TSM3.L   | (lookup)       | (unlikely)                | TSM3-LSE               | (verify)                |
| MU2.L    | IE00BG0J4271   | (unlikely)                | MU2-LSE                | (verify)                |
| QQQS.L   | (lookup)       | (verify)                  | QQQS-LSE               | QQQS.UK                 |
| 3USS.L   | (lookup)       | (verify)                  | 3USS-LSE               | 3USS.UK                 |
| QQQ5.L   | IE00BLRPRM59   | (unlikely)                | QQQ5-LSE               | (verify)                |
| SP5L.L   | IE00BLRPRL42   | (unlikely)                | SP5L-LSE               | (verify)                |

**Note**: Many entries marked "(verify)" or "(unlikely)" require manual testing against each provider's API before the backfill can begin. This is a critical pre-backfill task.

---

## Corporate Action Adjustment Pipeline

### Adjustment Types

1. **SPLIT / CONSOLIDATION**: Multiply historical prices by inverse of split ratio.
   Example: 10:1 consolidation -> all historical prices multiplied by 10.

2. **TICKER_CHANGE**: Map old ticker to new ticker. Merge histories.

3. **DELISTING**: Mark end-of-life date. No data beyond that point.

4. **NAV_RESET**: Some leveraged ETPs reset their NAV. Treat as a new instrument
   from the reset date for backtesting purposes.

### Adjustment Process

1. Load corporate actions from `{TICKER}/meta/{TICKER}_corporate_actions.json`
2. For each action (sorted chronologically):
   a. If SPLIT: adjust all bars BEFORE the action date
   b. If TICKER_CHANGE: rename bars from old ticker
   c. If DELISTING: mark `is_active=False`, set `data_end_date`
3. Write adjusted series to `_adjusted.parquet`
4. Log all adjustments to `{TICKER}/meta/{TICKER}_source_log.json`

### Corporate Action JSON Format

```json
{
  "ticker": "QQQ3.L",
  "actions": [
    {
      "date": "2023-06-15",
      "type": "SPLIT",
      "ratio": 0.1,
      "description": "10:1 consolidation"
    }
  ]
}
```

---

## DuckDB Analytics Layer

The DuckDB database at `_analytics/nzt48_research.duckdb` provides SQL access to all parquet
files. It is a **read-only analytics layer** -- parquet files are the source of truth.

### Example Queries

```sql
-- Load a ticker's daily data
SELECT * FROM read_parquet('research_data/QQQ3.L/daily/QQQ3.L_daily_adjusted.parquet')
WHERE timestamp >= '2023-01-01'
ORDER BY timestamp;

-- Cross-ticker correlation (daily returns)
WITH returns AS (
  SELECT
    ticker,
    timestamp,
    (close - LAG(close) OVER (PARTITION BY ticker ORDER BY timestamp)) / LAG(close) OVER (PARTITION BY ticker ORDER BY timestamp) AS daily_return
  FROM read_parquet('research_data/*/daily/*_daily_adjusted.parquet', filename=true)
)
SELECT
  a.ticker AS ticker_a,
  b.ticker AS ticker_b,
  CORR(a.daily_return, b.daily_return) AS correlation
FROM returns a
JOIN returns b ON a.timestamp = b.timestamp AND a.ticker < b.ticker
GROUP BY a.ticker, b.ticker;
```

---

## Usage Notes

- All timestamps are in UTC. Convert to Europe/London or US/Eastern as needed for session analysis.
- Prices for .L tickers are in GBP (pounds). The backfill pipeline applies pence-to-pounds
  conversion during ingestion (using logic from `data_hub/normalization/price_units.py`).
- Volume data for .L tickers may be unreliable (often zero or stale from yfinance).
  Always check the `quality_score` column before using volume in analysis.
- Do NOT modify parquet files manually. Use the backfill script (`scripts/backfill_5y.py`)
  or the DataHub pipeline to update data.
