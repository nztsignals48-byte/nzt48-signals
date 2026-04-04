# AEGIS V2 — Validation Infrastructure

**Audit Date:** 2026-04-04

---

## Anti-Overfitting: 7 Sequential Gates

**File:** `python_brain/validation/strategy_gates.py` (360 lines)

Before any strategy is promoted from PAPER to LIVE, it must pass 7 sequential gates. Failing any gate halts promotion.

| Gate | Name | Threshold | Reference |
|------|------|-----------|-----------|
| 1 | Minimum Sample Size | >= 30 trades | Statistical power |
| 2 | Sharpe Ratio | > 0.5 (annualized) | Risk-adjusted return |
| 3 | Max Drawdown | < 15% | Tail risk |
| 4 | Deflated Sharpe Ratio | > 0 | Bailey & Lopez de Prado 2014 |
| 5 | Probability of Backtest Overfitting (PBO) | < 0.40 | CSCV method |
| 6 | Walk-Forward OOS Sharpe | > 0 | Out-of-sample validation |
| 7 | Minimum Backtest Length | Calculated per MinBTL formula | Sufficient data |

**Gate 4 (DSR)** is particularly important — it corrects for multiple testing bias. If you test 100 strategies and pick the best, the DSR adjusts for the inflation of the Sharpe ratio from selection effects.

**Gate 5 (PBO)** uses the Combinatorially Symmetric Cross-Validation method. A PBO > 0.40 means there's a >40% probability the strategy's backtest performance was overfit and will not persist out-of-sample.

## Simulation Fidelity Scoring

**File:** `python_brain/validation/simulation_fidelity.py` (350 lines)

Scores simulation realism across 5 dimensions:

| Dimension | Weight | What It Checks |
|-----------|--------|----------------|
| Fill Realism | 30% | Partial fills, slippage, queue position |
| Cost Modeling | 25% | Spread, commission, market impact, FX |
| Timing | 15% | Latency, execution delay, bar alignment |
| Data Quality | 15% | Missing bars, volume accuracy, price accuracy |
| Risk Parity | 15% | Backtest risk model matches live risk model |

**Composite score < 60 = unreliable simulation.**

## Institutional Backtest Framework (Quarantined)

**File:** `python_brain/quarantine/institutional_backtest.py` (857 lines)

9-phase institutional-grade validation:

| Phase | Name | Description |
|-------|------|-------------|
| 1 | Baseline | All types, all tickers, with costs |
| 2 | Standalone Decomposition | Per-entry-type isolated testing |
| 3 | Parameter Sweeps | Sensitivity analysis (optional) |
| 4 | Walk-Forward Validation | 3 overlapping windows, OOS testing |
| 5 | Monte Carlo Simulation | 5,000 bootstrap simulations, confidence intervals |
| 6 | Ablation Testing | Remove each component, measure impact |
| 7 | Regime & Session Analysis | Performance by regime, time-of-day, day-of-week |
| 8 | Portfolio Combination | 13 strategy combinations tested |
| 9 | Final Verdicts | KEEP_LIVE / TIGHTEN / SHADOW / DISABLE / ABANDON |

Three runs completed (March 22-23, 2026) with results in `data/institutional_backtest_*/`.

## Production-Parity Backtester (Quarantined)

**File:** `python_brain/quarantine/production_backtest.py` (577 lines)

Spawns actual bridge.py subprocess and sends tick-format JSON via stdin — the only backtester that exercises the full 33-strategy pipeline with all overlays and gates. Currently in quarantine, not the primary backtest tool.

## SPRT Strategy Quarantine

**File:** `python_brain/risk/strategy_quarantine.py`

Uses Sequential Probability Ratio Test (Wald, 1947) for real-time edge-death detection:
- Null hypothesis: Strategy edge has died (WR <= 50%)
- Alternative hypothesis: Strategy edge persists (WR >= threshold)
- Auto-quarantine when SPRT reaches lower boundary
- Auto-revive when sufficient evidence accumulates

The Compounding Machine in bridge.py integrates with SPRT for the Track/Score/Size/Kill loop.

## Go-Live Validation Gate

**File:** `config/config.live.toml` comments

Pre-live transition checklist:
- [ ] Set IS_LIVE=true in main.rs (compile-time constant)
- [ ] Verify config.live.toml values for current equity
- [ ] Run 100-trade validation gate: WR >= 40%, PF >= 1.3, DD < 10%
- [ ] Human sign-off on go-live decision

## Paper Trading Readiness

The system entered paper trading readiness as of Session 21 (2026-04-03):
- Real backtest completed: 16.2M trades, 4,377 tickers, 730 days
- Bugs fixed: S6_Catalyst (0.016x PF), TypeC (0.876x PF), S1_Microstructure (disabled)
- Risk arbiter: paper_uses_live_gates=True
- Paper trading target: April 7, 2026+
