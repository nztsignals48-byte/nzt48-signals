# HISTORICAL BACKFILL PLAN

**Document ID:** NZT48-ANNEX-HBF-001
**Version:** 1.0
**Date:** 2026-02-27
**Status:** DRAFT — Requires sign-off before execution
**Scope:** Construction of a 5-year historical research database for backtesting, analog matching, regime analysis, and strategy development
**Dependency:** DATA_VENDOR_MIGRATION_PLAN.md (providers must be available before backfill)

---

## 1. OBJECTIVE

Build a comprehensive, quality-assured historical market database (`/data/research.db`) that provides 5 years of daily OHLCV data and 1 year of intraday data for all tickers in the NZT-48 universe. This database is strictly for research and backtesting. It is physically separate from the live trading database (`data/nzt48.db`) to prevent any contamination of live operations.

---

## 2. CURRENT STATE

### 2.1 Existing Data Inventory

| Data Type | Source | Depth | Quality | Gaps |
|-----------|--------|-------|---------|------|
| Daily OHLCV (US tickers) | yfinance | ~2 years | Moderate | Occasional missing bars; no split adjustment verification |
| Daily OHLCV (LSE tickers) | yfinance | ~1-2 years | Low-Moderate | Leveraged ETPs often have missing days; delisted products return empty |
| Intraday 1-min (US) | None stored | 0 | N/A | No intraday history exists |
| Intraday 1-min (LSE) | None stored | 0 | N/A | No intraday history exists |
| VIX daily | CBOE/yfinance | ~2 years | High | Minimal gaps |
| Corporate actions | None | 0 | N/A | No split/dividend history tracked |
| Sector rotation data | None | 0 | N/A | No historical sector performance data |

### 2.2 Problems with Current Data

1. **No research database exists.** All historical queries hit yfinance live, introducing latency and rate-limit risk into backtests.
2. **Split adjustments are not verified.** yfinance auto-adjusts, but leveraged ETPs frequently reverse-split. No audit trail exists.
3. **Delisted tickers return empty DataFrames** from yfinance, silently corrupting any analysis that includes them.
4. **No intraday data** is stored anywhere. Strategy backtesting that requires sub-daily resolution is impossible.

---

## 3. REQUIREMENTS

### 3.1 Data Scope

| # | Requirement | Tickers | Granularity | Depth | Priority |
|---|-------------|---------|-------------|-------|----------|
| R-1 | Daily OHLCV for CORE ISA tickers | 12 tickers | 1D | 5 years (2021-02-27 to 2026-02-27) | P0 (Critical) |
| R-2 | Daily OHLCV for EXTENDED ISA tickers | 10 tickers | 1D | 5 years | P0 |
| R-3 | Daily OHLCV for EXPANSION v2 tickers | 20 tickers | 1D | 5 years (or since listing) | P1 |
| R-4 | Daily OHLCV for EXPANSION v3 tickers | 20 tickers | 1D | 5 years (or since listing) | P1 |
| R-5 | Daily OHLCV for US Bot B tickers | 18 tickers | 1D | 5 years | P0 |
| R-6 | Daily OHLCV for context tickers | QQQ, SMH, SPY, SOXX, VIX, TLT, GLD | 1D | 5 years | P0 |
| R-7 | 1-minute intraday for CORE ISA tickers | 12 tickers | 1m | 1 year (2025-02-27 to 2026-02-27) | P1 |
| R-8 | 1-minute intraday for US Bot B tickers | 18 tickers | 1m | 1 year | P1 |
| R-9 | VIX daily close history | ^VIX | 1D | 5 years | P0 |
| R-10 | VIX3M daily close history | ^VIX3M | 1D | 5 years | P2 |
| R-11 | Corporate actions (splits, dividends) | All 80+ tickers | Event-level | 5 years | P1 |
| R-12 | Sector performance (XLK, XLF, XLE, XLU, XLV, XLI, XLB, XLP, XLY, XLRE, XLC) | 11 sector ETFs | 1D | 5 years | P2 |
| R-13 | Delisted ticker historical data | 9 known delisted tickers | 1D | Full available history | P2 |

### 3.2 Ticker Inventory (Total: ~87 active + 9 delisted)

**CORE ISA (12):** QQQ3.L, 3LUS.L, 3SEM.L, GPT3.L, NVD3.L, TSL3.L, TSM3.L, MU2.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L

**EXTENDED ISA (10):** AMD3.L, ARM3.L, NVDS.L, TSLS.L, 3LDE.L, 3LEU.L, 3GOL.L, 3OIL.L, 3SIL.L, LLY3.L

**EXPANSION v2 (20):** SPXL.L, SEMI.L, 3SNV.L, 3OIL.L, PHAG.L, 3HCL.L, SOXL.L, 3LTS.L, 3STS.L, 3SIL.L, 3LNV.L, MAGS.L, AAPS.L, PHAU.L, 3GOL.L, XLKS.L, XLFS.L, XLES.L, XLUS.L, NVDS.L

**EXPANSION v3 (20):** 3LNG.L, SILV.L, NGAS.L, SLV3.L, WSLV.L, GDX3.L, WGLD.L, GLD3.L, 3GOS.L, AVGS.L, CRM3.L, SOXS.L, DIS3.L, TSMS.L, 3SMS.L, UKDV.L, BATT.L, JPGL.L, ETHE.L, BTCE.L

**US Bot B (18):** NVDA, TSLA, MU, SNDK, AMD, AVGO, MRVL, ARM, TSM, ASML, SMCI, VRT, CRDO, ANET, QCOM, LRCX, KLAC, ON

**Context (7+):** QQQ, SMH, SPY, SOXX, VIX, TLT, GLD

**Sector ETFs (11):** XLK, XLF, XLE, XLU, XLV, XLI, XLB, XLP, XLY, XLRE, XLC

---

## 4. STORAGE ARCHITECTURE

### 4.1 Database Location and Isolation

```
/Users/rr/nzt48-signals/
  data/
    nzt48.db          <-- LIVE trading DB (DO NOT TOUCH)
    research.db       <-- NEW: Historical research DB
    research_intraday/ <-- NEW: Intraday data in Parquet files (too large for SQLite)
      NVDA_1m_2025.parquet
      NVDA_1m_2026.parquet
      QQQ3.L_1m_2025.parquet
      ...
```

**Isolation rules:**
- `research.db` MUST NOT share a connection with `nzt48.db`.
- No live trading code may read from `research.db`.
- No backfill process may write to `nzt48.db`.
- The backfill scripts live in `research_data/backfill/` (separate from live modules).

### 4.2 SQLite Schema: `research.db`

```sql
-- Daily OHLCV bars
CREATE TABLE daily_bars (
    ticker       TEXT NOT NULL,
    date         TEXT NOT NULL,        -- YYYY-MM-DD
    open         REAL NOT NULL,
    high         REAL NOT NULL,
    low          REAL NOT NULL,
    close        REAL NOT NULL,
    adj_close    REAL NOT NULL,
    volume       INTEGER NOT NULL,
    source       TEXT NOT NULL,        -- 'polygon', 'ibkr', 'yfinance', 'manual'
    currency     TEXT DEFAULT 'USD',   -- 'USD', 'GBP', 'GBp'
    split_adjusted BOOLEAN DEFAULT 1,
    backfill_ts  TEXT NOT NULL,        -- ISO timestamp of when this row was backfilled
    PRIMARY KEY (ticker, date)
);

-- Corporate actions
CREATE TABLE corporate_actions (
    ticker       TEXT NOT NULL,
    date         TEXT NOT NULL,
    action_type  TEXT NOT NULL,        -- 'SPLIT', 'REVERSE_SPLIT', 'DIVIDEND', 'DELIST'
    ratio        REAL,                 -- e.g., 0.1 for 10:1 reverse split
    amount       REAL,                 -- dividend amount (if applicable)
    source       TEXT NOT NULL,
    backfill_ts  TEXT NOT NULL,
    PRIMARY KEY (ticker, date, action_type)
);

-- Ticker metadata
CREATE TABLE ticker_metadata (
    ticker       TEXT PRIMARY KEY,
    name         TEXT,
    geography    TEXT,                 -- 'US', 'UK_LSE'
    instrument   TEXT,                 -- 'EQUITY', 'LEVERAGED_ETP', 'ETF', 'INDEX'
    leverage     TEXT,                 -- '1x', '2x', '3x'
    underlying   TEXT,
    listing_date TEXT,                 -- YYYY-MM-DD (earliest known data)
    delist_date  TEXT,                 -- NULL if active
    issuer       TEXT,                 -- 'WisdomTree', 'GraniteShares', etc.
    isa_eligible BOOLEAN,
    source       TEXT NOT NULL,
    backfill_ts  TEXT NOT NULL
);

-- Backfill audit log
CREATE TABLE backfill_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT NOT NULL,
    granularity  TEXT NOT NULL,        -- 'daily', '1min'
    date_from    TEXT NOT NULL,
    date_to      TEXT NOT NULL,
    source       TEXT NOT NULL,
    rows_inserted INTEGER NOT NULL,
    gaps_detected INTEGER DEFAULT 0,
    quality_score REAL,               -- 0.0 to 1.0
    started_ts   TEXT NOT NULL,
    completed_ts TEXT NOT NULL,
    notes        TEXT
);

CREATE INDEX idx_daily_bars_date ON daily_bars(date);
CREATE INDEX idx_daily_bars_ticker ON daily_bars(ticker);
CREATE INDEX idx_corporate_actions_ticker ON corporate_actions(ticker);
```

### 4.3 Intraday Storage: Parquet Files

Intraday data is too large for SQLite. Each file covers one ticker for one calendar year.

**File naming:** `{TICKER}_1m_{YEAR}.parquet`
**Columns:** `timestamp (datetime64), open, high, low, close, volume, source (string)`
**Compression:** Snappy (default Parquet compression)
**Partitioning:** By ticker and year for efficient access patterns.

### 4.4 Estimated Storage Requirements

| Data Type | Tickers | Rows (est.) | Size (est.) |
|-----------|---------|-------------|-------------|
| Daily OHLCV (5yr, ~1260 trading days) | 87 active | ~110,000 | ~50 MB |
| Daily OHLCV (delisted, partial) | 9 delisted | ~5,000 | ~2 MB |
| VIX + VIX3M (5yr) | 2 | ~2,500 | ~1 MB |
| Sector ETFs (5yr) | 11 | ~14,000 | ~6 MB |
| Corporate actions | All | ~500 | <1 MB |
| **Daily subtotal** | | ~132,000 | **~60 MB** |
| Intraday 1-min (1yr, ~252 days x 390 min) | 30 (CORE + US) | ~2,950,000 | **~4.5 GB** |
| **GRAND TOTAL** | | ~3,082,000 | **~4.6 GB** |

---

## 5. DATA SOURCES FOR BACKFILL

### 5.1 Source Priority Per Data Type

| Data Type | Primary Source | Secondary Source | Notes |
|-----------|---------------|-----------------|-------|
| US daily OHLCV (5yr) | Polygon.io (historical endpoint) | yfinance | Polygon has clean, split-adjusted data |
| LSE daily OHLCV (5yr) | IBKR (historical data) | yfinance | Many LSE ETPs only 2-3 years old |
| US intraday 1-min (1yr) | Polygon.io (aggregates endpoint) | Twelve Data | Polygon has full 1-min history |
| LSE intraday 1-min (1yr) | IBKR (reqHistoricalData) | None | yfinance does not provide LSE intraday |
| VIX history | CBOE website (CSV download) | yfinance | CBOE provides official VIX history |
| Corporate actions | Polygon.io (reference/splits) | Manual research | IBKR also provides splits data |
| Sector ETF data | Polygon.io | yfinance | Standard US ETFs, well-covered |

### 5.2 Licensing Compliance

- **Polygon.io:** Historical data access included in Developer tier ($79/mo). API terms permit local storage for personal use.
- **IBKR:** Historical data available to account holders. Terms permit local caching for personal analysis. No redistribution.
- **CBOE VIX:** Publicly available CSV downloads. No licensing restrictions for personal use.
- **yfinance:** Used only as gap-filler when licensed sources are unavailable. All yfinance-sourced rows flagged with `source='yfinance'`.

---

## 6. BACKFILL PROCESS

### 6.1 Phase 1: US Daily Data (Days 1-3)

1. **Polygon historical endpoint:** `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}`
2. Fetch 5 years for each of the 18 US Bot B tickers + 7 context tickers + 11 sector ETFs.
3. Rate limit: Polygon Developer allows unlimited historical requests (no per-minute cap for historical).
4. Insert into `daily_bars` with `source='polygon'`.
5. Cross-validate: For each ticker, fetch the same data from yfinance. Compare close prices. Log any discrepancy > 0.5%.

### 6.2 Phase 2: LSE Daily Data (Days 4-7)

1. **IBKR reqHistoricalData:** Request daily bars for each LSE ticker, going back as far as available.
2. Many leveraged ETPs were listed after 2021. Record the actual listing date in `ticker_metadata`.
3. For tickers where IBKR history is < 5 years, supplement with yfinance (flag `source='yfinance'`).
4. **GBp normalization:** IBKR returns LSE prices in GBp (pence). Divide by 100 to store in GBP.
5. Insert into `daily_bars` with appropriate source tag.

### 6.3 Phase 3: VIX and Macro Data (Day 8)

1. Download VIX daily history from CBOE: `https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv`
2. Download VIX3M history similarly.
3. Parse CSV, insert into `daily_bars` with `source='cboe'`.
4. Validate: Check that VIX values are in plausible range (8-90 historically).

### 6.4 Phase 4: Corporate Actions (Days 9-10)

1. **Polygon splits endpoint:** `GET /v3/reference/splits?ticker={ticker}`
2. For LSE tickers, use IBKR corporate actions data.
3. Manual verification: Cross-reference known major events (e.g., NVDA 10:1 split July 2024).
4. Insert into `corporate_actions` table.
5. Re-verify `daily_bars.adj_close` is correctly adjusted for all splits.

### 6.5 Phase 5: Intraday Data (Days 11-20)

1. **Polygon 1-min endpoint:** `GET /v2/aggs/ticker/{ticker}/range/1/minute/{from}/{to}`
2. Fetch 1 day at a time (Polygon returns max 50,000 bars per request).
3. For 18 US tickers x 252 trading days = 4,536 API calls. At 5 requests/second = ~15 minutes total.
4. Store as Parquet files: one file per ticker per year.
5. For LSE CORE tickers, use IBKR `reqHistoricalData` with `barSizeSetting='1 min'`.
6. IBKR limits historical requests to 60 per 10 minutes. Budget 4-5 hours for 12 LSE tickers x 252 days.

### 6.6 Phase 6: Incremental Updates (Ongoing)

After initial backfill, run a nightly job at 23:00 UTC:

1. For each ticker, query the latest date in `daily_bars`.
2. Fetch new bars from the primary provider (Polygon for US, IBKR for LSE).
3. Insert new rows. Log to `backfill_log`.
4. For intraday, append new days to the current year's Parquet file.
5. Run quality checks on the new data (see Section 7).

---

## 7. QUALITY CHECKS

### 7.1 Gap Detection

For each ticker, after backfill:

1. Generate a list of expected trading days (US: NYSE calendar; UK: LSE calendar).
2. Compare against actual dates in `daily_bars`.
3. Flag any missing trading day as a GAP.
4. **Acceptable gap rate:** < 1% of trading days for US tickers, < 3% for LSE leveraged ETPs (some have genuinely thin trading days).
5. Any gap > 3 consecutive trading days MUST be manually investigated and documented.

### 7.2 Split Adjustment Verification

For each ticker with known splits (from `corporate_actions`):

1. Check the close price on the day before and after the split.
2. The adjusted close should be continuous (within 2% of expected value).
3. If discontinuity > 5%, flag as SPLIT_ERROR and investigate.

### 7.3 Price Sanity Checks

| Check | Rule | Action on Failure |
|-------|------|-------------------|
| Zero price | `close <= 0` | Reject row, log error |
| Extreme daily return | `abs(daily_return) > 50%` | Flag for manual review (leveraged ETPs can legitimately move 30%+) |
| Volume = 0 | `volume == 0` on a trading day | Accept but flag as LOW_QUALITY |
| High > Low violation | `high < low` | Reject row, log error |
| Open outside H/L range | `open > high OR open < low` | Reject row, log error |
| Currency anomaly | LSE price in GBp not GBP | Divide by 100, log correction |

### 7.4 Cross-Source Validation

For a random sample of 10 tickers, compare daily close prices between two independent sources:

| Metric | Threshold |
|--------|-----------|
| Mean absolute difference | < 0.5% |
| Maximum single-day difference | < 2.0% |
| Correlation coefficient | > 0.999 |

### 7.5 Benchmark Validation

Verify known historical data points against published benchmarks:

| Ticker | Date | Known Close | Source |
|--------|------|-------------|--------|
| SPY | 2021-12-31 | ~$474.96 | Public record |
| NVDA | 2024-07-10 (pre-split close) | ~$134.91 (post-split adjusted) | Public record |
| QQQ | 2022-01-03 | ~$396.00 | Public record |
| VIX | 2020-03-16 | ~82.69 | CBOE official |

If any benchmark deviates by > 1%, the entire backfill for that source must be re-examined.

---

## 8. FAILURE MODES

| # | Failure Mode | Impact | Mitigation |
|---|-------------|--------|------------|
| FM-1 | Polygon rate limit exceeded during bulk download | Backfill stalls | Implement exponential backoff; retry after 60s; split into smaller date ranges |
| FM-2 | IBKR disconnects mid-backfill | Partial LSE data | Checkpoint progress in `backfill_log`; resume from last successful date |
| FM-3 | LSE ticker has no history (too new) | Less than 5 years of data | Record actual listing date; accept partial history; flag in metadata |
| FM-4 | Delisted ticker returns empty | No data for backtesting | Try multiple sources; accept gracefully; log in `ticker_metadata.delist_date` |
| FM-5 | Split adjustment mismatch | Incorrect backtesting results | Cross-validate with corporate actions table; manual review for flagged tickers |
| FM-6 | SQLite file corruption | Total data loss | Daily backup of `research.db` to compressed archive; verify with `PRAGMA integrity_check` |
| FM-7 | Parquet file write failure (disk full) | Intraday data lost | Monitor disk space before starting; EC2 volume must have 20GB free |
| FM-8 | Timezone confusion (UTC vs ET vs London) | Misaligned bars | All timestamps stored in UTC; conversion done only at display/query time |
| FM-9 | Incremental update overlaps existing data | Duplicate rows | `INSERT OR IGNORE` for daily bars; Parquet files partitioned by date range |
| FM-10 | GBp vs GBP confusion | 100x price error | Sanity check: any LSE price > 10000 is likely GBp; auto-divide and log |

---

## 9. OPERATOR ACTIONS

| Scenario | Operator Action |
|----------|----------------|
| Backfill job fails mid-way (provider error, network timeout, process crash) | Check `backfill_log` table in `research.db` for the last successfully completed ticker and date range. Do NOT restart the entire backfill from scratch -- resume from the last checkpoint. Run `SELECT ticker, MAX(date) FROM daily_bars GROUP BY ticker` to identify where each ticker stopped. Restart the backfill script with `--resume` flag (or manually set the start date to last checkpoint + 1 day). Investigate the failure cause in process logs before resuming. |
| Quality checks find >5% gap rate for a ticker | Identify the specific missing dates by comparing against the expected trading calendar. Try fetching the missing dates from an alternative provider (e.g., if Polygon failed, try IBKR or yfinance). If the gaps are due to the ticker being genuinely illiquid (no trades on those days), document this in `ticker_metadata.notes` and accept the gaps. If gaps are provider errors, re-run backfill for that ticker from the alternative source. Update `backfill_log` with the gap investigation results. |
| Corporate actions detected retroactively (split or reverse-split discovered after backfill) | Re-run the split adjustment calculation for the affected ticker across ALL historical dates. Verify price continuity around the corporate action date (adjusted close should be smooth). Update `corporate_actions` table with the newly discovered event. Cross-validate adjusted prices against a second source. If downstream backtests have already been run with unadjusted data, flag those results as INVALID and schedule re-runs. |
| Research DB grows too large (disk space on EC2 approaching capacity) | Check current disk usage with `df -h` and `du -sh data/research_intraday/`. Archive intraday Parquet files older than 1 year to compressed storage (`tar -czf research_intraday_archive_YYYY.tar.gz data/research_intraday/*_YYYY.parquet`). Keep ALL daily data intact -- daily data is small (~60 MB) and must never be archived. Consider moving old intraday archives to S3 or local backup. Monitor disk space weekly to prevent this from recurring. |

---

## 10. ACCEPTANCE TESTS

### AT-1: Daily Data Completeness

- [ ] For each of the 18 US Bot B tickers: verify row count >= 1200 (5yr x 252 days, minus holidays).
- [ ] For each of the 12 CORE ISA tickers: verify row count >= 500 (most listed 2-4 years ago).
- [ ] For VIX: verify row count >= 1250 (5 years).
- [ ] For all sector ETFs: verify row count >= 1200 each.

### AT-2: Gap Rate

- [ ] US tickers: gap rate < 1% of expected trading days.
- [ ] LSE tickers: gap rate < 3% of expected trading days.
- [ ] No ticker has > 3 consecutive missing trading days without documented explanation.

### AT-3: Split Adjustment

- [ ] NVDA 10:1 split (June 2024): verify price continuity in adjusted close.
- [ ] At least 3 other known splits verified in `corporate_actions` table.
- [ ] No ticker has > 5% price discontinuity on a non-event day.

### AT-4: Intraday Data

- [ ] For NVDA: verify > 95,000 1-min bars for the year (252 days x 390 min = 98,280 theoretical).
- [ ] For QQQ3.L: verify > 90,000 1-min bars for the year (252 days x 510 min LSE hours = 128,520 theoretical; but leveraged ETPs may have gaps).
- [ ] Each Parquet file readable and parsable without errors.
- [ ] Timestamp column is in UTC.

### AT-5: Cross-Source Validation

- [ ] 10-ticker random sample: mean absolute daily close difference < 0.5% between primary and secondary source.
- [ ] Benchmark validation: all 4 known data points within 1% of published values.

### AT-6: Database Integrity

- [ ] `PRAGMA integrity_check` on `research.db` returns "ok".
- [ ] `backfill_log` has one entry per ticker per granularity with `quality_score >= 0.8`.
- [ ] `ticker_metadata` has entries for all 87+ active tickers and 9 delisted tickers.

### AT-7: Isolation

- [ ] `research.db` and `nzt48.db` are separate files (not symlinked, not shared).
- [ ] No import of `research.db` exists in any file under `strategies/`, `signal_engine/`, or `main.py`.
- [ ] Backfill scripts exist only in `research_data/backfill/`.

---

## 11. PROOF ARTIFACTS

| # | Artifact | Location | Purpose |
|---|----------|----------|---------|
| PA-1 | Research database | `data/research.db` | 5-year daily historical data |
| PA-2 | Intraday Parquet files | `data/research_intraday/*.parquet` | 1-year 1-min bars |
| PA-3 | Backfill scripts | `research_data/backfill/backfill_daily.py`, `backfill_intraday.py` | Reproducible data loading |
| PA-4 | Incremental updater | `research_data/backfill/incremental_update.py` | Nightly data maintenance |
| PA-5 | Quality check report | `research_data/BACKFILL_QA_REPORT.md` | Gap analysis, validation results |
| PA-6 | Cross-source validation log | `research_data/cross_validation_log.csv` | Per-ticker comparison results |
| PA-7 | Backfill audit log | Table `backfill_log` in `research.db` | Complete audit trail |
| PA-8 | Ticker metadata | Table `ticker_metadata` in `research.db` | Listing dates, delisting, geography |
| PA-9 | Database backup script | `research_data/backfill/backup_research_db.sh` | Daily compressed backup |
| PA-10 | Acceptance test suite | `tests/test_backfill_quality.py` | Automated quality verification |

---

## 12. SCHEDULE

| Phase | Duration | Prerequisites |
|-------|----------|---------------|
| Phase 1: US Daily | Days 1-3 | Polygon.io account active |
| Phase 2: LSE Daily | Days 4-7 | IBKR account + LSE data subscription |
| Phase 3: VIX / Macro | Day 8 | None (CBOE public data) |
| Phase 4: Corporate Actions | Days 9-10 | Phases 1-2 complete |
| Phase 5: Intraday | Days 11-20 | Polygon + IBKR active |
| Phase 6: QA + Validation | Days 21-25 | All phases complete |
| Phase 7: Incremental Setup | Day 26 | QA passed |
| **TOTAL** | **~26 working days** | |

---

## 13. BACKUP AND RETENTION

- **Daily backup:** `research.db` compressed to `research_db_YYYYMMDD.tar.gz` at 00:00 UTC.
- **Retention:** Keep last 7 daily backups. Keep one monthly backup indefinitely.
- **Backup location:** `data/backups/` on EC2 instance.
- **Parquet files:** Not backed up incrementally (can be regenerated from providers). Full backup monthly.
- **Integrity check:** `PRAGMA integrity_check` runs before every backup. If it fails, the backup is skipped and an alert is sent.

---

## 14. SIGN-OFF

| Role | Name | Date | Signature |
|------|------|------|-----------|
| System Operator | | | |
| Data Quality Reviewer | | | |

**This plan must be signed off before any backfill script is executed against production data directories.**
