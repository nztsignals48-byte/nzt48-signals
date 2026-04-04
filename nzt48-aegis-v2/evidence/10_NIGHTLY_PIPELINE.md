# AEGIS V2 — Ouroboros Nightly Pipeline

**Audit Date:** 2026-04-04

---

## Overview

Ouroboros is the autonomous nightly learning loop. It runs at 04:51 UTC and generates three TOML config files that the Rust engine hot-reloads via SIGHUP.

**Core principle:** Track/Score/Size/Kill. Every strategy is evaluated, winners get more capital, losers get killed.

## Pipeline Steps (from nightly_v6.py)

| Step | Name | What It Does |
|------|------|--------------|
| 1 | WAL Harvest | Read WAL events (fills, exits, P&L) from past 24 hours |
| 2 | Performance Scoring | Compute per-strategy WR, PF, Sharpe, drawdown |
| 3 | Bayesian Update | Update prior on win_rate, trade_count using Bayesian inference |
| 4 | Exit Optimization | Tune chandelier_atr_mult from actual exit data |
| 5 | Regime Analysis | Identify best/worst regime from trade distribution |
| 6 | Kelly Recalibration | Recompute per-tier Kelly fractions from updated stats |
| 7 | Ticker Evaluation | Wilson-score blacklisting for tickers with WR < 30% (N>10) |
| 8 | Indicator Intelligence | Discover high-confidence indicator threshold rules |
| 9 | Config Generation | Write dynamic_weights.toml, spread_cache.toml, universe_classification.toml |
| 10 | SIGHUP Delivery | Signal Rust engine for hot-reload |
| 11 | Notification | Telegram morning brief with overnight changes |

## Config Writer (config_writer.py, 1,923 lines)

The config writer is the most critical Ouroboros component. It generates:

### dynamic_weights.toml
- Bayesian win_rate and trade_count
- Chandelier exit parameters (chandelier_atr_mult)
- Regime scaling factors (Normal, bear_quiet, bear_volatile, etc.)
- Kelly fractions per tier (t1, t2, t3)
- Adaptive confidence floor
- Ticker blacklist (Wilson score)
- Indicator gates (auto-discovered rules)
- Per-entry-type confidences

### spread_cache.toml
- Median intraday spreads per ticker from WAL fill analysis
- Used by risk arbiter CHECK 13 (spread veto)

### universe_classification.toml
- Tier 1/2/3/locked ticker classification
- Drives Vanguard vs Apex routing in engine

## Safety Controls

| Control | Description |
|---------|-------------|
| Observe-only mode | Config mutations frozen until N=300 trades threshold |
| Atomic writes | tmp + rename prevents corrupt config |
| TOML validation | Parsed before write; corrupt TOML never reaches engine |
| Rollback ledger | 30-day diff history for debugging |
| Hard bounds | confidence_floor [0.55, 0.80], chandelier_atr_mult [1.5, 3.0], heat_limit [5%, 10%] |

## Current State

```toml
# P2-B0.8 RESET: 2026-03-28
# Previous values stale (WR=79.2% on 20 trades vs actual 35.4% on 64)
# Observe-only mode active until N=300 trades

[bayesian]
win_rate = 0.354000    # Actual from Mega Audit
trade_count = 64

[kelly_fractions]
t1 = 0.050000          # Capped at config.toml clamp_max = 0.05
t2 = 0.040000
t3 = 0.030000

[signal]
confidence_floor = 45   # HOTFIX lowered from 65 to unblock signal flow
```

## Integration Points

| Component | How It Connects |
|-----------|----------------|
| Claude (claude_curator.py) | Soft gate for live signals, forensic review |
| Gemini (gemini_scanner.py) | Morning brief: avoid/focus tickers, strategy weights |
| Persistent Memory | Cross-session cumulative stats per strategy |
| IBKR Data Provider | Nightly data refresh for backfill and analysis |
| Strategy Quarantine (SPRT) | Sequential Probability Ratio Test for edge-death detection |
| Compounding Machine | Strategy kill/revive based on live Sharpe |
