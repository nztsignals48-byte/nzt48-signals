# AEGIS V2 — Data Provenance

**Audit Date:** 2026-04-04

---

## Live Data Source

| Property | Value |
|----------|-------|
| **Provider** | Interactive Brokers (IBKR) via `ibapi` crate (Rust) + `ib_insync` (Python) |
| **Client IDs** | 101 (engine execution), 102 (Python analytics) |
| **Connection** | Localhost IB Gateway (paper: port 4003, live: port 4001) |
| **Bar type** | 5-second realtime bars + L1 tick-by-tick BidAsk |
| **Latency** | 5-40ms typical |
| **Subscriptions** | NYSE, NASDAQ, LSE, TSE, SGX, HKEX + 8 European exchanges |
| **Cost** | $0 additional (covered by IBKR account subscriptions) |
| **Fallback** | yfinance (graceful degradation when IBKR unavailable) |

## Backtest Data Source

| Property | Value |
|----------|-------|
| **Provider** | Yahoo Finance via `yfinance` Python library |
| **Bar size** | 60-minute OHLCV |
| **Period** | 730 days (2-year lookback, max for hourly data) |
| **Download method** | Parallel ThreadPoolExecutor, 10 workers, 100-ticker chunks |
| **Tickers attempted** | 4,635 (from contracts.toml) |
| **Tickers with data** | ~4,370 (266 delisted/failed) |
| **Known limitations** | Yahoo 60m data can have gaps, volume discrepancies vs IBKR |

## Contract Universe

| Property | Value |
|----------|-------|
| **Source file** | `config/contracts.toml` (673.9 KB) |
| **Total contracts** | 4,635 |
| **Exchanges** | 14 (LSEETF, LSE, SMART/US, HKEX, TSE, XETRA, EURONEXT, SGX, AEB, HEX, XMAD, KRX, plus additional) |
| **Core ISA ETPs** | 12 leveraged/inverse LSE products (QQQ3.L, QQQS.L, 3LUS.L, 3USS.L, QQQ5.L, 3SEM.L, NVD3.L, TSL3.L, GPT3.L, TSM3.L, MU2.L, 5SPY.L) |
| **Fields per contract** | symbol, con_id, exchange, sec_type, currency, leverage, sector, inverse_of |

## Data Integrity Controls

1. **Universe filter (Rust):** Amihud illiquidity, ASER spread >0.5%, erroneous tick (>15% from 1s EMA), reverse split (>500% overnight), synthetic halt (>30s no ticks), NaN/Inf validation
2. **Stale data threshold:** 120 seconds (configurable per exchange) triggers HALT regime
3. **FX rates:** Live rates from Ouroboros nightly pipeline, 6-hour refresh. Fallback: hardcoded FX_TO_GBP map
4. **Currency mapping:** Per-contract currency from contracts.toml, with FX conversion cost applied for non-GBP LSE instruments

## Backtest Data vs Live Data Gaps

| Dimension | Live | Backtest | Impact |
|-----------|------|----------|--------|
| Bar granularity | 5-second | 60-minute | Reduces signal precision for microstructure strategies |
| Bid/ask spreads | Real L1 ticks | Not available | Spread-based strategies (S1_Microstructure) disabled |
| Volume profile | Tick-level | Hourly aggregated | VPIN, volume sweep detection approximated |
| Overnight gaps | Exact open prices | First bar of session | Gap detection approximate |
| Corporate actions | IBKR-adjusted | Yahoo-adjusted | Consistent for both |
| Latency | 5-40ms | Zero (instant fill) | Slippage assumption: 0.5% in backtest cost model |
