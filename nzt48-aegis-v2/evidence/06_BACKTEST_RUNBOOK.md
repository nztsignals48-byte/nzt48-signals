# AEGIS V2 — Backtest Runbook

**Audit Date:** 2026-04-04

---

## Rerunnable Commands

From the repository root (`/Users/rr/nzt48-signals/nzt48-aegis-v2/`):

### Option A: Full-Fidelity World-Class Backtest (RECOMMENDED)

14 entry types, real 33-CHECK risk arbiter, per-exchange spreads (US:2bps, LSE:4bps, HKEX:6bps), GARCH/scanner proxies by entry type, walk-forward IS/OOS split, Sharpe/Sortino/Calmar, strategy attribution, robustness (top-10 removal).

```bash
# Clear bytecode cache (prevents stale .pyc issues)
find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
find . -name '*.pyc' -delete 2>/dev/null

# Run the world-class backtest
PYTHONDONTWRITEBYTECODE=1 python3 world_class_backtest.py
```

Output: `data/backtest_reports/world_class_<timestamp>.json` + `trade_ledger_<timestamp>.csv`

### Option B: Fast Validation Backtest

10 entry types, quicker (~25 min), no walk-forward split.

```bash
# Clear bytecode cache
find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
find . -name '*.pyc' -delete 2>/dev/null

# Run the fast validation backtest
PYTHONDONTWRITEBYTECODE=1 python3 -c "
import sys, os
os.environ['AEGIS_ROOT'] = os.getcwd()
sys.path.insert(0, '.')
sys.path.insert(0, 'python_brain')

from python_brain.ouroboros.fast_backtest_pipeline import run_pipeline, print_summary

result = run_pipeline(
    days=730,
    interval='60m',
    output_dir='data/ouroboros_reports',
    config_path='config/config.toml',
)

print_summary(result)
"
```

## What the World-Class Pipeline Does

### Stage 1: Data Fetch (~2-3 minutes)
- Reads `config/contracts.toml` (4,635 tickers across 14 exchanges)
- Parallel download via yfinance ThreadPoolExecutor (10 workers, 100-ticker chunks)
- Returns ~4,370 tickers with data (266 delisted/failed)
- Output: Dict[str, DataFrame] of 60-minute OHLCV bars

### Stage 2: Signal Simulation (~15-20 minutes)
- For each ticker, computes indicators:
  - RSI (14-period Wilder), ATR (14-period), RVOL (20-bar rolling relative volume)
  - Hurst exponent (R/S analysis), regime classification
  - OBV + OBV-RSI(5) for TypeF
- `classify_entries_extended()` scans for 14 entry types:
  - TypeA-F + S2_Reversion + S3_MacroTrend (original 8)
  - S5_OvernightCarry, VolCompression, NAVArbitrage, FOmcDrift (Session 22)
  - VolExpansion, GapFade, NightRider, AlphaFactory (world-class additions)
- Per-entry-type cooldowns (5-40 bars) + per-ticker daily cap (5 entries/day)
- Chandelier exit with 5-rung trailing stop:
  - Rungs: [1.5x, 1.35x, 1.125x, 1.0x, 0.75x] ATR multiplier
  - Rung thresholds: [0%, 0.8%, 1.5%, 2.5%, 4.0%] gain from entry
  - Max hold: 60 bars
- Per-exchange cost model (round-trip bps + slippage + FX conversion)

### Stage 3: Risk Filtering (~5-8 minutes)
- Loads `RiskArbiterPy` with `simulation_mode=True`, `paper_uses_live_gates=True`
- Constructs `EvalContext` per trade with **realistic proxies** (not sentinels):
  - Confidence from entry_type_confidence map
  - Spread from exchange-specific map (US:2bps, LSE:4bps, TSE:3bps, HKEX:6bps)
  - GARCH sigma calibrated by entry type (e.g., 0.02 for momentum, 0.03 for mean-reversion)
  - Scanner score calibrated by entry type (e.g., 65 for momentum, 50 for event-driven)
  - Time from bar timestamp, regime from ticker-level Hurst
- Filters trades through 33-CHECK arbiter, day-boundary HALT/FLATTEN reset
- Records veto reasons and pass/fail

### Stage 4: Walk-Forward Validation
- IS/OOS split at day 365 (50/50 split)
- Computes per-split: Sharpe, Sortino, Calmar, max drawdown, PF, WR
- Strategy attribution: per-entry-type IS vs OOS comparison
- Robustness: remove top-10 tickers by trade count, recompute aggregate

### Stage 5: Report Generation
- JSON report with all breakdowns: exchange, entry type, ticker, day of week, walk-forward
- CSV trade ledger with per-trade details
- Saved to `data/backtest_reports/`

## Output Files

| File | Description |
|------|-------------|
| `world_class_<timestamp>.json` | Full JSON report with walk-forward validation |
| `trade_ledger_<timestamp>.csv` | Per-trade CSV ledger |
| `fast_backtest_730d_60m_<timestamp>.json` | Quick validation JSON report |
| `TRADE_LEDGER_<timestamp>.csv` | Quick validation trade ledger |

## Dependencies

```
python3 (3.9+)
yfinance
numpy
toml (or tomllib in 3.11+)
```

No GPU required. No PyTorch/TensorFlow. Runs on 4GB RAM (EC2 t3.medium).

## Expected Runtime

| Pipeline | Stage | Duration |
|----------|-------|----------|
| World-class | Data fetch | ~120-180 seconds |
| World-class | Simulation | ~900-1200 seconds |
| World-class | Risk filtering | ~300-500 seconds |
| World-class | Walk-forward + report | ~30 seconds |
| **World-class total** | | **~25-35 minutes** |
| Fast validation | Total | **~20-25 minutes** |

## How to Verify Results

1. Check JSON output for `total_trades`, `win_rate`, `profit_factor`
2. Open trade_ledger CSV — each row is one trade with full context
3. Compare entry type breakdown to Session 22 baseline
4. Check `walk_forward` section for IS vs OOS consistency (OOS should not degrade >30%)
5. Check `robustness` section — removing top-10 tickers shouldn't collapse PF
6. Cross-reference exchange breakdown against contracts.toml coverage
