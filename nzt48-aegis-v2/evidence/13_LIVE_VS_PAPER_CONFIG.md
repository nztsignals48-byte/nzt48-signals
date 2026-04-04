# AEGIS V2 — Live vs Paper Configuration

**Audit Date:** 2026-04-04

---

## Configuration Loading Order

1. `config/config.toml` — master config (paper-safe defaults)
2. If `IS_LIVE=true`: overlay `config/config.live.toml` on top (tighter limits)
3. `config/dynamic_weights.toml` — Ouroboros nightly overrides (kelly, regime scales, confidence)
4. `config/strategies.toml` — strategy-specific parameters
5. `config/contracts.toml` — universe definition

## Key Parameter Differences

| Parameter | Paper (config.toml) | Live (config.live.toml) | Notes |
|-----------|-------------------|----------------------|-------|
| max_simultaneous_positions | 3 | 3 | Same — conservative for 10K equity |
| portfolio_heat_limit_pct | 10.0 | 10.0 | Same |
| cash_buffer_pct | 25.0 | 25.0 | Same |
| daily_trade_limit | 999999 (simulation) | 3 | Paper allows unlimited for data collection |
| confidence_floor | 50 | 65 | Paper intentionally lower to capture marginal signals |
| kelly clamp_max | 0.05 | 0.20 | Paper uses tighter Kelly (BT-008 optimal) |
| kelly clamp_min | -- | 0.15 | Live enforces minimum Kelly |
| consecutive_loss_halt | 999999 (simulation) | 5 | Paper never halts for learning |
| daily_drawdown_pct | 4.0 | 3.0 | Live is tighter |
| weekly_drawdown_pct | 7.0 | 5.0 | Live is tighter |
| peak_drawdown_halt_pct | 15.0 | 12.0 | Live triggers halt sooner |
| min_gross_edge_pct | 0.10 | 0.15 | Live requires more edge vs spread |
| min_trade_gbp_live | -- | 1500.0 | Minimum trade size in live |
| TypeB confidence | 82.0 | 82.0 | Same |
| TypeD confidence | 80.0 | 80.0 | Same |
| stale_data_threshold_secs | per-exchange | 120 | Live uses single global value |

## Simulation Mode Overrides (config.toml [simulation] section)

When in simulation mode, these limits are intentionally relaxed:

```toml
[simulation]
# All limits set to 999999 for maximum data collection
max_daily_trades_sim = 999999
max_positions_sim = 999999
velocity_limit_sim = 999999
consecutive_loss_halt_sim = 999999
```

This is **by design** — paper trading collects all possible signals and trades to feed the Ouroboros learning loop. The 100-trade validation gate at go-live ensures the system has demonstrated consistent edge before live capital is deployed.

## Transition Checklist (from config.live.toml)

```
PAPER -> LIVE transition checklist:
[ ] Set IS_LIVE=true in main.rs (requires code change + review)
[ ] Verify config.live.toml values are appropriate for current equity
[ ] Run 100-trade validation gate (WR>=40%, PF>=1.3, DD<10%)
[ ] Human sign-off on go-live decision
```

## IS_LIVE Flag

```rust
// main.rs line 35
const IS_LIVE: bool = false;  // Compile-time constant
```

Changing to `true` requires:
1. Code modification
2. Cargo build
3. Docker image rebuild
4. Deployment review

This is intentionally a compile-time constant, not a runtime flag, to prevent accidental live mode activation.
