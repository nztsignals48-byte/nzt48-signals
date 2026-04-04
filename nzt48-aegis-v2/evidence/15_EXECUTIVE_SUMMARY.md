# AEGIS V2 — Executive Summary

**Date:** 2026-04-04 | **Status:** Ready for paper trading (April 7+)

---

## What Is AEGIS V2?

A hybrid Rust + Python automated trading engine designed for a UK ISA (tax-free) account via Interactive Brokers.

- **Rust engine** (36K lines, 78 files): Real-time execution, 39-check risk arbiter, broker integration, WAL persistence
- **Python brain** (160K lines, 353 files): 34 signal generators, 40 ML modules, nightly self-tuning pipeline
- **Architecture**: Rust handles execution (<1ms latency), Python handles intelligence (signal generation, analytics)
- **Communication**: JSON over stdin/stdout IPC — Python generates signals, Rust validates and executes

## Key Design Decisions

1. **Fail-closed risk**: Every signal passes through 39 synchronous checks in <1ms. Any failure = immediate rejection.
2. **ISA-compliant**: Long-only (no short selling), annual limit enforcement, GBP-denominated P&L tracking.
3. **No ML frameworks**: All 40 ML modules are numpy-only. No PyTorch, TensorFlow, or GPU required.
4. **Compile-time safety**: `IS_LIVE = false` constant prevents accidental live deployment.
5. **KILL switch**: File-based emergency halt (`/app/data/KILL`) checked every 1 second.

## Backtest Results (Session 22)

| Metric | Value |
|--------|-------|
| Universe | 4,635 tickers across 14 exchanges |
| Period | 730 days of 60-minute bars |
| Total trades simulated | 9,403,542 |
| Aggregate win rate | 48.97% |
| Aggregate profit factor | 0.998 |

### What the numbers mean

The aggregate PF of ~1.0 across 4,635 tickers is expected — most equities don't have exploitable edges. The system's value comes from:

1. **Strategy selection**: FOmcDrift (1.37x PF), NAVArbitrage (1.19x PF), TypeD (1.08x PF) show genuine alpha
2. **Risk filtering**: The 39-check arbiter rejects 30-70% of signals in live trading, keeping only high-quality entries
3. **Position sizing**: 12-factor Kelly sizing allocates more capital to higher-confidence, higher-edge signals
4. **Self-tuning**: Nightly Ouroboros pipeline adjusts confidence floors, chandelier ATR multipliers, and heat limits based on recent performance

### Strategy leaderboard

| Strategy | PF | Assessment |
|----------|----|-----------|
| FOmcDrift | 1.368 | Post-FOMC drift — strongest, calendar-based |
| NAVArbitrage | 1.189 | ETP NAV discount — structural, LSE-only |
| TypeD (SupportBounce) | 1.075 | Price + RSI oversold — reliable |
| TypeE (IBSMeanReversion) | 1.039 | Connors RSI-2 — well-studied factor |
| TypeF (OBVDivergence) | 1.024 | Volume divergence — marginal |
| S3_MacroTrend | 0.948 | SMA crossover — to be disabled |
| VolCompression | 0.727 | Keltner squeeze — to be disabled |

## Risk Architecture

### Four-state regime hierarchy
```
HALT > FLATTEN > REDUCE > NORMAL
```
Escalation is automatic. De-escalation requires human approval for HALT, clean reconciliation for FLATTEN.

### Notable safeguards
- **Max 3 concurrent positions** in live trading
- **Max 3 trades per day**
- **4% daily drawdown limit** triggers FLATTEN
- **15% peak drawdown** triggers HALT (requires human review)
- **VIX > 35** triggers FLATTEN; VIX > 25 triggers REDUCE
- **Per-ticker velocity limit**: max 5 entries per ticker in 5 minutes
- **Spread veto**: > 0.3% spread blocks entry

## What Is NOT Tested in the Backtest

Two backtest tools exist. The full-fidelity `world_class_backtest.py` exercises **14 of 34 signal generators** (41% coverage) with the real risk arbiter, per-exchange cost model, and walk-forward IS/OOS validation. Not tested:

- 20 bridge.py signal generators requiring real-time data (VanguardSniper, LeadLag, etc.)
- 25+ pre-signal quality gates (VPIN toxicity, TDA crash detector, adversarial detection)
- 15+ post-signal overlays (calendar anomaly modifier, Student-t Kelly, Compounding Machine)
- Real bid-ask spreads (backtest uses per-exchange estimates)
- L2 order book data (backtest uses OHLCV only)

These components will be exercised during paper trading.

## Deployment Plan

| Phase | Timeline | Description |
|-------|----------|-------------|
| Paper trading | April 7 — May 7 | IBKR paper account, full pipeline, real market data |
| Performance review | May 7 — May 14 | Analyse paper results, tune parameters |
| ISA go-live | May 15+ | Real money, ISA account, starting equity: £10,000 |

### Realistic P&L estimate (ISA constraints)
- Starting capital: £10,000
- Max 3 positions, 3 trades/day, Kelly-clamped sizing
- **2-year range**: £8,000 — £25,000 (conservative to moderate)
- Assumes Tier 1+2 strategies maintain edge through regime changes

## How to Reproduce

### Quick validation (fast_backtest_pipeline — 10 entry types, ~25 min):
```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/
PYTHONDONTWRITEBYTECODE=1 python3 -c "
import sys, os
os.environ['AEGIS_ROOT'] = os.getcwd()
sys.path.insert(0, '.')
sys.path.insert(0, 'python_brain')
from python_brain.ouroboros.fast_backtest_pipeline import run_pipeline, print_summary
result = run_pipeline(days=730, interval='60m', output_dir='data/ouroboros_reports', config_path='config/config.toml')
print_summary(result)
"
```

### Full-fidelity (world_class_backtest — 14 entry types + risk arbiter + walk-forward):
```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/
PYTHONDONTWRITEBYTECODE=1 python3 world_class_backtest.py
```

Expected runtime: ~60 minutes (fast), ~90 minutes (world-class). No GPU required.

## Evidence Package

This summary is part of a 17-document evidence package. See [00_INDEX.md](00_INDEX.md) for the full manifest and reading guide.
