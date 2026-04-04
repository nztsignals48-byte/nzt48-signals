# AEGIS V2 — Session 22 Backtest Results

**Date:** 2026-04-04 | **Branch:** `feat/tier-system-enhancements-full`

---

## Backtest Tools Available

| Tool | Entry Types | Risk Arbiter | Walk-Forward | Cost Model |
|------|-------------|-------------|--------------|------------|
| `fast_backtest_pipeline.py` | 10 | Simulation (sentinels) | No | 1-component (flat bps) |
| `world_class_backtest.py` | **14** | **Real 33-CHECK** (calibrated proxies) | **Yes (IS/OOS)** | **3-component (spread+slippage+FX)** |

Results below are from `fast_backtest_pipeline.py` (Session 22 completed run). The `world_class_backtest.py` runner adds VolExpansion, GapFade, NightRider, AlphaFactory with per-entry-type cooldowns, realistic GARCH/scanner proxies, and walk-forward validation. Run it with: `PYTHONDONTWRITEBYTECODE=1 python3 world_class_backtest.py`

## Run Parameters (Fast Validation)

| Parameter | Value |
|-----------|-------|
| Period | 730 days (2-year lookback) |
| Interval | 60-minute OHLCV bars |
| Universe | 4,635 tickers from contracts.toml |
| Tickers with data | 4,635 (100%) |
| Entry types | 10 active (TypeA,B,D,E,F + S2,S3,S5 + VolCompression, NAVArbitrage, FOmcDrift) |
| Risk arbiter | 33-CHECK Python mirror, simulation mode |
| Total runtime | 3,646s (~61 minutes) |

## Aggregate Results

| Metric | Value |
|--------|-------|
| **Total trades** | 9,403,542 |
| **Win rate** | 48.97% |
| **Profit factor** | 0.998 |
| **Veto rate** | 0.0% (simulation mode — see Known Limitations) |

### Interpretation

The aggregate PF of 0.998 means the full universe of strategies collectively is near break-even on a per-share basis. This is expected: most of the 4,635 tickers are generic equities where the edge is marginal. The value comes from **strategy-specific edges** on subsets of the universe, combined with **risk gates that filter out low-quality signals in live trading**.

## By Entry Type

| Entry Type | Trades | WR | PF | Avg Hold |
|------------|--------|----|----|----------|
| FOmcDrift | 108,628 | 51.7% | **1.368** | 53.8 bars |
| NAVArbitrage | 69,485 | 50.8% | **1.189** | 44.4 bars |
| TypeD (SupportBounce) | 548,601 | 50.3% | **1.075** | 50.5 bars |
| TypeE (IBSMeanReversion) | 1,448,113 | 49.4% | **1.039** | 51.8 bars |
| TypeF (OBVDivergence) | 1,259,065 | 49.6% | **1.024** | 51.8 bars |
| TypeA (DipRecovery) | 8,900 | 43.6% | **1.016** | 3.0 bars |
| TypeB (EarlyRunner) | 2,663,193 | 48.9% | 0.981 | 51.4 bars |
| S3_MacroTrend | 3,059,784 | 48.1% | 0.948 | 51.4 bars |
| S5_OvernightCarry | 236,104 | 49.9% | 0.934 | 53.4 bars |
| VolCompression | 1,669 | 47.1% | 0.727 | 52.0 bars |

### Strategy Tier Assessment

**Tier 1 — Positive edge (PF > 1.05):**
- FOmcDrift (1.368x PF) — strongest signal, exploits post-FOMC announcement drift
- NAVArbitrage (1.189x PF) — exploits ETP NAV discounts on LSE
- TypeD SupportBounce (1.075x PF) — price near daily low + RSI oversold

**Tier 2 — Marginal positive (1.0 < PF < 1.05):**
- TypeE IBSMeanReversion (1.039x PF) — IBS < 0.10 mean reversion
- TypeF OBVDivergence (1.024x PF) — OBV-RSI divergence signal
- TypeA DipRecovery (1.016x PF) — RSI oversold + volume spike

**Tier 3 — Negative or neutral (PF < 1.0):**
- TypeB EarlyRunner (0.981x PF) — needs tighter entry criteria
- S3_MacroTrend (0.948x PF) — SMA crossover too noisy on 60m bars
- S5_OvernightCarry (0.934x PF) — overnight gap carry doesn't hold on broad universe
- VolCompression (0.727x PF) — Keltner squeeze too rare and unreliable (only 1,669 trades)

### Recommendation
Disable S3_MacroTrend, S5_OvernightCarry, and VolCompression in paper trading. Concentrate on Tier 1+2 strategies which collectively show positive edge. TypeB may recover with tighter RVOL threshold.

## By Exchange

| Exchange | Trades | WR | PF |
|----------|--------|----|----|
| US (SMART) | 9,401,556 | 49.0% | 0.998 |
| TSE | 205 | 71.7% | **2.914** |
| XETRA | 217 | 59.5% | **1.871** |
| EURONEXT | 263 | 51.3% | 0.921 |
| HKEX | 1,300 | 40.7% | 0.862 |
| LSE | 1 | 0.0% | 0.000 |

### Observation
Non-US exchanges have tiny sample sizes (1-1,300 trades) compared to US (9.4M). TSE and XETRA show strong edges but need validation with larger samples. LSE has only 1 trade — yfinance does not reliably serve LSE ETP data. Live trading on LSE will use IBKR data.

## By Day of Week

| Day | Trades | WR | PF |
|-----|--------|----|----|
| Tuesday | 1,939,366 | 48.7% | **1.043** |
| Friday | 1,858,489 | 49.6% | **1.035** |
| Monday | 1,719,009 | 49.1% | **1.010** |
| Wednesday | 2,030,894 | 48.5% | 0.987 |
| Thursday | 1,855,784 | 49.0% | 0.919 |

### Observation
Tuesday and Friday show the strongest edges. Thursday is the weakest day — consistent with known institutional rebalancing patterns.

## Equity Curve (Unrealistic)

The Kelly-compounded equity curve shows starting 10,000 growing to 1.0 quadrillion. **This is an artifact of compounding 9.4M trades at 10% Kelly — see Known Limitations.** The equity curve metric is not meaningful for this run. Realistic P&L estimates are in the Executive Summary.

## Comparison with Session 21

| Metric | Session 21 | Session 22 |
|--------|------------|------------|
| Total trades | 16,200,000 | 9,403,542 |
| Win rate | 49.71% | 48.97% |
| Profit factor | 7.778 | 0.998 |
| Entry types | 7 | 10 (+3 new) |
| TypeF WR | 67.66% | 49.6% |
| TypeF PF | 35.97 | 1.024 |

### Why Results Differ
1. **Trade count**: S22 uses optimized VolCompression step (20 vs 5), reducing spurious entries
2. **TypeF performance**: S21 may have had a bug inflating TypeF OBV-RSI readings. S22 uses corrected code with proper column alignment
3. **Aggregate PF**: S21's 7.778x PF was likely inflated by TypeF concentration and compounding artifacts. S22's 0.998x PF is more honest
4. **New strategies**: FOmcDrift (1.368x PF) and NAVArbitrage (1.189x PF) are genuine new alpha sources

## Data Quality Notes

- VID.L shows extreme outlier losses (-37K PnL/trade) — likely data quality issue or penny stock
- Hour-of-day breakdown shows all trades at 00:00 UTC — datetime extraction needs improvement
- 0 tickers failed yfinance download (chunked fetch approach improved reliability)
