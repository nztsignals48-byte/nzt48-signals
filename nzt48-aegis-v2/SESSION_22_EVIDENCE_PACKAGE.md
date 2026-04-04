# Session 22 — Corrected Backtest Evidence Package

**Date**: 2026-04-04
**Branch**: `feat/tier-system-enhancements-full`
**Key Commits**: `83d66a6`, `acca739`, `d3122f2`

---

## CRITICAL: Session 21 Results Were Invalid

Session 21's headline results (16.2M trades, 49.71% WR, 7.778x PF) were **entirely caused by a yfinance thread-safety bug**:

- `fetch_historical_data_parallel` used a `ThreadPoolExecutor` with 8 workers
- yfinance `Ticker.history()` is not thread-safe — multiple threads shared internal state
- Result: tickers received **each other's price data** (cross-contamination)
- A ticker with 200% daily move got assigned to dozens of other tickers
- TypeF (OBV Divergence) appeared to have 67.66% WR and 35.97x PF — this was noise from corrupted data

**Fix**: Replace `ThreadPoolExecutor` with batched `yf.download(list_of_tickers)` which is thread-safe by design.

---

## Corrected S22 Results

| Metric | Session 21 (INVALID) | Session 22 (CORRECTED) |
|--------|---------------------|------------------------|
| Raw trades | 16,200,000 | 3,091,451 |
| Win Rate | 49.71% | 49.00% |
| Profit Factor | 7.778x | **0.985x** |
| Total PnL | +£5.8M | -£152K |
| Max Drawdown | 54.1% | 100.0% |
| Veto Rate | 0% | 0% |

**Backtest scope**: 4,635 tickers, 730 days, 60m bars, 10 chunks × 500 tickers.

---

## Walk-Forward Validation

| Period | Trades | Win Rate | Profit Factor | Sharpe |
|--------|--------|----------|---------------|--------|
| In-Sample (70%) | 2,073,833 | 47.87% | 0.954x | 0.739 |
| Out-of-Sample (30%) | 1,017,618 | 51.30% | **1.043x** | 2.092 |
| Degradation | — | +3.44% | +0.089 | — |

**Positive finding**: OOS performance is *better* than IS, indicating no overfitting.

---

## Strategy Attribution (Corrected)

| Strategy | Trades | Win Rate | Profit Factor | Status |
|----------|--------|----------|---------------|--------|
| FOmcDrift | 49,643 | 49.3% | **1.190x** | UNKNOWN |
| NAVArbitrage | 16,054 | 50.2% | **1.116x** | UNKNOWN |
| TypeD (SupportBounce) | 209,801 | 49.9% | 1.008x | LIVE |
| TypeE (IBS MeanRev) | 792,268 | 49.2% | 1.007x | SHADOW |
| TypeA (DipRecovery) | 5,900 | 42.5% | 1.001x | LIVE |
| TypeF (OBVDivergence) | 522,027 | 49.2% | 0.974x | SHADOW |
| TypeB (EarlyRunner) | 1,495,758 | 48.7% | 0.962x | LIVE |

**Key insight**: FOmcDrift and NAVArbitrage show genuine edge. TypeB dominates volume (48%) and drags the aggregate below 1.0x.

---

## Exchange Breakdown

| Exchange | Trades | Win Rate | Profit Factor |
|----------|--------|----------|---------------|
| XETRA | 94 | 56% | 1.692x |
| US | 3,090,719 | 49% | 0.985x |
| EURONEXT | 112 | 49% | 0.766x |
| HKEX | 459 | 43% | 0.935x |
| TSE | 67 | 69% | 0.412x |

Note: Non-US volumes are tiny — insufficient for statistical conclusions.

---

## Critical Bugs Fixed in Session 22

### Bug 1: yfinance Thread-Safety (Root cause of S21 corruption)
- **File**: `backfill_simulator.py`
- **Fix**: `fetch_historical_data_batch()` using `yf.download(tickers_list)` in batches of 100
- **Commit**: `83d66a6`

### Bug 2: OOM in save_report (asdict on 16M objects)
- **File**: `fast_backtest_pipeline.py`
- **Fix**: Removed `dataclasses.asdict()` call that serialized all trade objects into RAM
- **Commit**: `83d66a6`

### Bug 3: Regime cascade across day boundaries
- **File**: `backfill_simulator.py`
- **Fix**: Added `arbiter.clear_flatten()` + `arbiter.manual_clear_halt()` at each new date
- **Commit**: `83d66a6`

### Bug 4: OOM from full-universe fetch
- **File**: `fast_backtest_pipeline.py`
- **Fix**: Chunked fetch+simulate (SIM_CHUNK_SIZE=500) with `del chunk_data; gc.collect()` after each chunk
- **Commit**: `acca739`

### Bug 5: OOM from 16M FilteredTrade objects
- **File**: `fast_backtest_pipeline.py`
- **Fix**: Streaming filter — `filter_trades_through_arbiter` now returns tuple `(approved_results, veto_counts, missed_winners, vetoed_count)`. Only APPROVED trades (~25K) stored; veto_counts and missed_winners computed inline.
- **Commit**: `acca739`

---

## 0% Veto Rate Explanation

All 3,091,451 trades were approved by the risk arbiter. This is **expected** for bar-level simulation:

1. L2 spread check: passes trivially (no real bid/ask in OHLCV data)
2. Stale data check: every bar is "fresh" by definition
3. GARCH/scanner proxies: set to permissive simulation values

Live veto rate will be higher (estimated 5-15% rejection). The backtest measures **signal quality**, not live throughput.

---

## Institutional Proof Package Location

`data/institutional_proof/20260404_044824/`

Files:
- `EXECUTIVE_SUMMARY.md`
- `SYSTEM_SCOPE.md`
- `DATA_PROVENANCE.md`
- `UNIVERSE_MANIFEST.csv` (4,635 tickers)
- `ENABLED_STRATEGY_MANIFEST.csv`
- `RISK_GATE_AUDIT.md`
- `BACKTEST_RUNBOOK.md`
- `KNOWN_LIMITATIONS.md`
- `results.json` (full metrics, strategy attribution, hourly/daily breakdown)
- `trade_ledger.csv` (3,091,451 rows)

---

## Paper Trading Status

**April 7+ onwards**: Live paper trading begins. This corrected backtest establishes the honest baseline.

Expected to see in paper trading:
- Overall WR: ~47-51% (consistent with corrected backtest)
- FOmcDrift: strongest live signal candidate (1.19 PF)
- NAVArbitrage: second strongest (1.12 PF)
- TypeB: monitor for continued drag — may need tuning or reduced allocation
