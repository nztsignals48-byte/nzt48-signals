# AEGIS V2 — Risk Gate Audit

**Audit Date:** 2026-04-04

---

## Rust Risk Arbiter: 39 Synchronous Checks

Every signal must pass through `RiskArbiter::evaluate()` synchronously in <1ms. Fail-closed: any check failure = immediate rejection.

### 4-State Regime Hierarchy

```
HALT > FLATTEN > REDUCE > NORMAL
```

- **HALT:** All trading stopped. Manual human approval required to de-escalate.
- **FLATTEN:** Close all positions. New entries blocked.
- **REDUCE:** Halved position limits, tighter loss limits.
- **NORMAL:** Standard operation.

Escalation is one-way. De-escalation: REDUCE requires 5min nominal; FLATTEN requires all positions closed + clean reconciliation; HALT requires human approval only.

### Complete Check Registry

| CHECK | Name | Trigger | Action |
|-------|------|---------|--------|
| 1 | ISA Short-Sell Block | direction = Short | REJECT |
| 2 | Inverse Mutual Exclusion (H32) | Hold QQQ3.L + QQQS.L simultaneously | REJECT |
| 5 | Risk Regime Gate | Regime = HALT or FLATTEN | REJECT |
| 6 | Max Positions (H34) | filled + pending >= max (3 live) | REJECT |
| 7 | Data Staleness | >120s no tick | HALT |
| 8 | Broker Connected | Disconnected | HALT |
| 9 | WAL Available | WAL writer down | HALT |
| 10 | Confidence Floor | Leverage-adjusted: floor/sqrt(leverage) | REJECT |
| 11 | Time-of-Day Cutoff | After 20:55 London | REJECT |
| 13 | Spread Veto (H36) | >0.3% spread | REJECT |
| 14 | Cash Buffer (H31) | Cash < 25% equity | REJECT |
| 15 | Portfolio Heat | Heat >= 10% | REJECT |
| 16 | Sector Heat (H30) | Any sector >= 33% | REJECT |
| 17 | ISA Annual Limit | >20,000 GBP invested this tax year | REJECT |
| 18 | Daily Drawdown (H29) | >4% from daily HWM | FLATTEN |
| 19 | Per-Ticker Velocity (H37) | >5 intents for same ticker in 5 min | REJECT |
| 19b | System Velocity | >10 entries system-wide in 5 min | REJECT |
| 20 | Macro Regime Escalation | VIX >35 | FLATTEN |
| 20b | Macro Stress | VIX >25 (with hysteresis deadband) | REDUCE |
| 20c | Macro Crisis | Cross-asset crisis signal | FLATTEN |
| 20d | Stale Macro + Non-Normal | Macro data stale + regime != Normal | REDUCE |
| 21 | Consecutive Loss Breaker (H38) | 5 consecutive stop-losses | HALT |
| 22 | Duplicate Position | IC-gated: 1/2/3 positions per ticker | REJECT |
| 23 | Ticker Halted | Synthetic halt or reverse split | REJECT |
| 24 | CVaR Heat | Portfolio CVaR > 1.5x heat limit | REJECT |
| 25 | GARCH Vol Ceiling | sigma > 0.80 * sqrt(leverage) | REJECT |
| 26 | Scanner Score Minimum | 0 < score < 30 | REJECT |
| 27 | Kelly Fraction Floor | 0 < kelly < 0.5% | REJECT |
| 28 | Daily Trade Limit (N0a) | 3 trades/day max | REJECT |
| 29 | Minimum Gross Edge (N0d) | Edge < 2x spread | REJECT |
| 30 | Weekly Drawdown | >7% from Monday HWM | FLATTEN |
| 31 | Peak Drawdown | >15% from all-time HWM | HALT |
| 32 | Equity Floor | Equity < 70% of initial | HALT |
| 34 | Correlation Concentration | >3 positions in same sector | REJECT |
| 35 | Structural Tradability | Score < 15 | REJECT |
| 36 | Session Exposure Limits | Asia 30%, Europe 50%, US 60%, Overlap 80% | REJECT |
| 37 | Regime-Scaled Daily Loss | STEADY -3%, Reduce -2.5% | FLATTEN |
| 38 | Regime-Scaled Weekly Loss | STEADY -7%, Reduce -5.5% | FLATTEN |
| 39 | Regime Risk Per Trade | HALT=0x, FLATTEN=0x, REDUCE=0.53x, NORMAL=1.0x | SCALE |
| SC05 | Min Entry Size | < 1500 GBP (live) / 100 (paper) | REJECT |

### Additional Safety Controls (Outside Arbiter)

| Control | Description |
|---------|-------------|
| KILL Switch (N10a) | File `/app/data/KILL` = immediate graceful shutdown. Checked every 1s. |
| PAUSE Switch (N10a) | File `/app/data/PAUSE` = freeze signal generation, data continues. |
| Drawdown Velocity | >2% equity drop in 1 hour = HALT |
| Backpressure Escalation | Tick channel depth exceeded = regime escalation |
| Fork Bomb Protection | >N Python crashes in 60s = SystemHalt |
| Broker Circuit Breaker | Error rate limiting with auto-reset |
| Tick Watchdog | No ticks for configurable seconds = HALT |

## Python Risk Arbiter Mirror (33 CHECKs)

File: `python_brain/ouroboros/risk_arbiter_py.py` (1,101 lines)

Python mirror of the Rust arbiter used in backtesting. Key difference:

```python
enforce_live_gates = not self.simulation_mode or self.paper_uses_live_gates
```

When `simulation_mode=True` and `paper_uses_live_gates=True`:
- CHECKs 14, 15, 16, 17, 18, 30, 31, 32 ARE enforced (portfolio-level gates)
- CHECKs 6, 19/19b, 21, 28 are relaxed (limits set to 999999 in simulation config)
- CHECKs 25 (GARCH) and 26 (Scanner): `fast_backtest_pipeline.py` uses sentinel values (-1.0) to bypass; `world_class_backtest.py` uses **realistic calibrated proxies** per entry type (e.g., GARCH sigma 0.02 for momentum, scanner score 65 for structural)

## Python Pre-Signal Quality Gates (25+ gates in bridge.py)

These are NOT in the risk arbiter — they run BEFORE signal generation in the live pipeline:

| Gate | Book | Description |
|------|------|-------------|
| Spread gate (G1) | -- | Spread > threshold blocks entry |
| VWAP extension (G2, G3) | -- | Price too far from VWAP |
| Structural tradability (G4) | -- | Score < 15 blocks all |
| Hurst extreme (G5) | -- | Hurst < 0.10 blocks |
| Ouroboros indicator gates (G6) | -- | Dynamic rules from config_writer |
| VPIN toxicity | 162 | > 0.80 blocks all entries |
| Liquidity pulse | 117 | Deterioration detection |
| Micro-regime | 83 | TOXIC microstructure blocks |
| Break-even vol filter | 46 | 3x ETP vol check |
| Turnover budget | 81 | Max daily turnover |
| TDA crash detector | 127 | Topology anomaly > 70% blocks |
| Adversarial detection | 103 | Spoofing/wash trading |
| Data quality | 176 | Pre-gate for data integrity |
| Structural break | 48 | Regime break detection |
| Safety boundary | 190 | Hard limits |
| Capital phase filter | 179 | New strategy graduated deployment |
| Concentration risk | 7 | Correlation + time-of-day |
| Time-of-day block | 12, 177 | First 15min, last 30min, ETP rebalance window |
| Macro event block | 24 | FOMC/CPI/NFP within 5 min |
| Earnings proximity | 40 | 3x ETPs on underlying earnings day |
| Regime daily/weekly loss | 85 | Scaled loss limits |
| Exchange blackout | -- | Per-exchange cutoff enforcement |
| Ticker/exchange blacklist | -- | Static + adaptive blacklists |
| IBKR resilience | 44 | Broker connectivity check |
| Flash crash detection | -- | Rapid price deviation blocks |

## Backtest Risk Coverage

| Layer | Live | Backtest |
|-------|------|----------|
| Rust 39-CHECK arbiter | All 39 checks | N/A (Python mirror used) |
| Python 33-CHECK arbiter | All 33 checks | 33 nominal, ~5 effective |
| Pre-signal quality gates (25+) | All active | **0 exercised** |
| Post-signal overlays (15+) | All active | **0 exercised** |
| **Backtest veto rate** | -- | **0%** (documented limitation) |

The 0% veto rate in `fast_backtest_pipeline.py` is explained by: simulation mode relaxes position/velocity/trade limits to 999999, GARCH/scanner use sentinel values to bypass, and portfolio state (cash, heat, drawdown) is not tracked across trades. The `world_class_backtest.py` runner uses calibrated proxies and day-boundary HALT/FLATTEN resets for more realistic filtering. See `KNOWN_LIMITATIONS.md`.
