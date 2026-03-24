# AEGIS V2 — Canonical System Plan
**Last verified**: 2026-03-24 00:00 UTC from deployed code + WAL data
**Branch**: `feat/tier-system-enhancements-full` @ `99f733e`
**Authoritative P&L source**: `system_memory.json` (nightly_v6) + WAL `final_pnl` field

---

## 1. Architecture

```
[IBKR Gateway :4003] → [Rust Engine] → [Python Brain (subprocess)] → [Risk Arbiter (32 checks)]
         ↑                    ↓                    ↓                          ↓
    [IB Gateway]         [WAL .ndjson]      [Signal JSON]              [Broker submit]
         ↑                    ↓                                              ↓
    [Weekly 2FA]      [Ouroboros nightly]                            [Exit Engine (Chandelier)]
                             ↓
                   [dynamic_weights.toml → SIGHUP hot-reload]
```

### Classification Ownership (who does what)
| Concern | Owner | Notes |
|---------|-------|-------|
| Entry type classification (TypeA-F) | **Python bridge.py** (Stage 4) | Runtime authority. Assigns `entry_type` from live indicators. |
| Entry type thresholds | **config.toml** `[entry_types]` | Single source of truth for RSI/RVOL/IBS thresholds. |
| Entry type reference implementation | Rust `entry_engine.rs` | **NOT used at runtime.** Reference for backtest/Crucible only. |
| Signal generation | **Python bridge.py** (Stage 3) | 6 strategy sources. Best 2 passed to Stage 4. |
| Risk gating | **Rust risk_arbiter.rs** | 32 checks. Deterministic. No model authority. |
| Exit decisions | **Rust exit_engine.rs** | Chandelier 5-rung. Single canonical exit authority. |
| Position sizing | **Python kelly_12factor.py** → Rust validation | 12-factor Kelly computed in Python, validated in Rust. |

### Components
| Component | Language | LOC | Purpose |
|-----------|----------|-----|---------|
| Engine | Rust | 3,443 | Tick processing, state machine, event loop |
| Risk Arbiter | Rust | 667 | 32 sequential risk checks, regime hierarchy |
| Exit Engine | Rust | 928 | Chandelier 5-rung trailing stop |
| Entry Engine | Rust | 786 | TypeA-F classification — **reference/backtest only, NOT runtime** |
| Position Sizer | Rust | 346 | Kelly criterion with fractional scaling |
| Portfolio | Rust | 530 | Position tracking, equity, drawdown |
| IBKR Broker | Rust | 1,585 | IB Gateway adapter, subscriptions |
| Bridge (Python) | Python | ~1,850 | 5-stage signal pipeline, 6 strategy sources |
| Ouroboros | Python | ~2,000 | Nightly learning, config generation |

### Docker Containers
| Container | Image | Port | Purpose |
|-----------|-------|------|---------|
| aegis-v2 | Custom (Rust+Python baked) | — | Engine + brain |
| aegis-ib-gateway | gnzsnz/ib-gateway | 4003 | IB Gateway + IBC |
| aegis-redis | redis:7-alpine | 6379 (internal) | State journal |

### Persistent Volumes
| Volume | Mount | Survives restart? | Content |
|--------|-------|-------------------|---------|
| aegis-events | /app/events | Yes (named volume) | WAL .ndjson + archive/ |
| aegis-data | /app/data | Yes (named volume) | Reports, gate vetoes, system_memory.json |
| aegis-logs | /var/log | Yes (named volume) | Cron logs, Ouroboros reports |
| aegis-redis-data | /data | Yes (named volume) | Redis AOF persistence |
| ./config | /app/config | Yes (bind mount) | config.toml, contracts.toml, universe.json |

---

## 2. Signal Pipeline (5 Stages in Python bridge.py)

### Stage 1: Indicator Computation (`_compute_indicators`)
5-minute OHLCV bar aggregation → RVOL, Hurst, ADX, volume divergence, volume slope, VPIN (shadow), IBS, VWAP, Structural Tradability Score (0-100).

### Stage 2: Quality Gates (`_check_quality_gates`)
G1: Spread (leverage-scaled) → G2: VWAP absolute extension (15%) → G3: VWAP directional (10%) → G4: Structural min (15/100) → G5: Hurst extreme (<0.10) → G6: Ouroboros indicator gates.

### Stage 3: Signal Generation (`_generate_signals`) — 6 SOURCES
1. **VanguardSniper** — ADX + EMA + volume breakout. Non-mean-reverting regimes.
2. **Orchestrator** — Configurable from strategies.toml. All regimes.
3. **IBS Mean Reversion** — IBS < 0.2 + RSI(2) < 15 + RVOL > 0.7. Mean-reverting/random regimes.
4. **Volume Expansion** — RVOL > 2.0 + ADX > 20 + 3+ up bars + price > EMA20.
5. **Opening Range Breakout** — US session 14:45-15:30 UTC. Breakout above ORB high with volume.
6. **Gap Fade** — Liquidity gap down >1% (RVOL < 2.0). ISA long-only fade.

Best 2 signals by confidence passed to Stage 4.

### Stage 4: Adjustments (`_apply_adjustments`)
LSE boost → drawdown penalty → hour-of-day weights → best selection → per-ticker cooldown → STS adjustment → **TypeA-F classification from live indicators** → adaptive entry/exchange/Kelly weights → VPIN shadow → Claude curator (shadow mode).

**TypeA and TypeD are ENABLED and pass through.** They are known backtest losers (29.5%/24.1% WR) but are kept active during paper validation to collect live performance data. Ouroboros nightly tracks per-type WR and can auto-downweight via `adaptive_entry_weights` in `dynamic_weights.toml`.

### Stage 5: Output
Signal dict → JSON stdout → Rust engine stdin pipe.

---

## 3. Risk Arbiter (32 Checks in Rust)

| # | Check | Action |
|---|-------|--------|
| 1 | ISA Safety (no short) | Reject |
| 2 | Inverse Mutual Exclusion | Reject |
| 5 | Risk Regime (HALT/FLATTEN) | Reject |
| 6 | Max Positions (regime-scaled) | Reject |
| 7 | Data Staleness (>120s → HALT) | Escalate |
| 8 | Broker Connected | Reject |
| 9 | WAL Available | Reject |
| 10 | Confidence Floor (leverage-aware) | Reject |
| 11 | Time-of-Day Cutoff | Reject |
| 13 | Spread Veto | Reject |
| 14 | Cash Buffer | Reject |
| 15 | Portfolio Heat | Reject |
| 16 | Sector Heat | Reject |
| 17 | ISA Annual Limit | Reject |
| 18 | Daily Drawdown (>4% → FLATTEN) | Escalate |
| 19 | Velocity Check (system + ticker) | Reject |
| 20 | Macro Escalation (VIX/DXY) | Escalate |
| 21 | Consecutive Loss Breaker | Escalate |
| 22 | Duplicate Position | Reject |
| 23 | Ticker Halted | Reject |
| 24 | CVaR Heat | Reject |
| 25 | GARCH Forecast | Reject |
| 26 | Scanner Score | Reject |
| 27 | Kelly Floor (<0.5%) | Reject |
| 28 | Daily Trade Limit | Reject |
| 29 | Minimum Gross Edge | Reject |
| 30 | Weekly Drawdown | Reject |
| 31 | Peak Drawdown (HWM) | Reject |
| 32 | Equity Floor | Reject |

Regime Hierarchy: **HALT > FLATTEN > REDUCE > NORMAL**

---

## 4. Exit Engine (Chandelier 5-Rung)

| Rung | Trigger | Stop Level |
|------|---------|------------|
| 1 | Entry | Entry - 1.5x ATR |
| 2 | +0.8% | Breakeven + fees |
| 3 | +1.5% | Peak - 1.0x ATR |
| 4 | +2.5% | Peak - 0.75x ATR |
| 5 | +4.0% | Peak - 0.5x ATR |

Volume exhaustion: When RVOL > 10x, tighten to 0.5x ATR.

---

## 5. Strategy Status

### Maturity Definitions
- **Implemented**: Code exists in bridge.py.
- **Enabled**: Code is reachable at runtime (not gated off).
- **Observed**: Has produced at least 1 signal in live paper WAL data.

| Strategy | Implemented | Enabled | Observed | Entry Type | WR Basis | Live Trades | Notes |
|----------|:-----------:|:-------:|:--------:|------------|----------|:-----------:|-------|
| VanguardSniper | Yes | Yes | **Yes** | Momentum/Unclassified | ~52% (10.8M backtest) | 33 | Core strategy. All sessions. |
| Orchestrator | Yes | Yes | No | Various | Varies | 0 | Requires strategies.toml on EC2. |
| IBS_MeanReversion | Yes | Yes | No | TypeE | ~57% (academic) | 0 | Deployed 2026-03-23. Needs market open. |
| VolExpansion | Yes | Yes | No | TypeB (refined) | ~55% (expected) | 0 | Deployed 2026-03-23. Needs market open. |
| ORB_Breakout | Yes | Yes | No | Unclassified | ~52% (academic) | 0 | US session only (14:45-15:30 UTC). |
| GapFade | Yes | Yes | No | Unclassified | ~65% (academic, gap fill rate) | 0 | Deployed 2026-03-23. Needs gap-down >1%. |
| TypeE (IBS classifier) | Yes | Yes | **Yes** | TypeE | N/A | 3 | Classified by bridge.py Stage 4. |
| TypeA (DipRecovery) | Yes | Yes | No | TypeA | 29.5% (backtest) | 0 | Monitored. Ouroboros auto-downweights if losing. |
| TypeD (SupportBounce) | Yes | Yes | No | TypeD | 24.1% (backtest) | 0 | Monitored. Ouroboros auto-downweights if losing. |
| TypeB (EarlyRunner) | Yes | Yes | No | TypeB | 52.4% (backtest) | 0 | Best backtest performer. |
| TypeC (OverboughtFade) | Yes | Yes | No | TypeC | N/A | 0 | Requires RSI > 80 (rare). |
| TypeF (OBVDivergence) | Yes | Yes | No | TypeF | 72% (backtest, small sample) | 0 | Requires vol_div < -0.5. |

---

## 6. P&L and Trade Data

### WAL Data Integrity
- `PositionClosed.final_pnl`: **Correctly populated** in WAL .ndjson. Values are in GBP.
- `PositionClosed.gross_pnl`: Correctly populated (before commission).
- `PositionClosed.mae` / `PositionClosed.mfe`: Correctly populated per-unit.
- `PositionClosed.total_commission`: Currently 0.0 (paper mode, no real commissions).

### Authoritative All-Time P&L (system_memory.json, 2026-03-23 04:50 UTC)
| Metric | Value |
|--------|-------|
| Total trades | 48 entries, 48 exits |
| Wins / Losses | 17 / 31 |
| Win Rate | 35.4% |
| Cumulative P&L | **-£6.79** |
| Profit Factor | 0.0 (nightly calculation issue — gross winners exist) |

### Current WAL (post-restart, 16 additional closures)
| Metric | Value |
|--------|-------|
| Entries in current WAL | 36 |
| Closures in current WAL | 16 |
| WAL P&L (sum of final_pnl) | ~-£2.27 |

### Combined estimated total: ~64 trades, ~-£9.06

---

## 7. Universe

### Two Counts Explained
| File | Count | What It Is |
|------|-------|------------|
| `config/contracts.toml` | **1,251** | All IBKR contract definitions (symbol, exchange, con_id, currency). The full subscription universe. |
| `config/universe.json` | **867** | Curated watchlist (FTSE100+250, S&P500, NDX100). Subset fed to ticker_selector for rotation priority. |

The 1,251 contracts are the superset. The 867 tickers are the prioritized core. Ticker_selector rotates from both sources within the 100-stream IBKR limit.

---

## 8. Deployment

### Deploy Sequence (MANDATORY)
```bash
git add <files> && git commit -m "..." && git push
rsync -avz --exclude '.git' --exclude 'target' ... ubuntu@3.230.44.22:/home/ubuntu/nzt48-aegis-v2/
ssh EC2 'cd /home/ubuntu/nzt48-aegis-v2 && docker compose build aegis-v2 && docker compose up -d'
ssh EC2 'docker system prune -f'   # reclaim old images
```

**Disk constraint**: 19GB total, builds need ~5GB temp. Always prune before `--no-cache` builds. Cached builds work if only Python changed.

### EC2 Details
- Instance: c7i-flex.large (4GB RAM, 2 vCPUs, x86_64)
- IP: 3.230.44.22 (Elastic)
- Disk: 19GB (current usage: 75%, 4.7GB free)
- SSH: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22`

---

## 9. Daily Operations

### Pre-Market (07:00 UTC)
1. `ssh EC2 'docker ps'` — 3 containers healthy
2. `docker logs aegis-v2 --tail 20` — no CRITICAL/PANIC
3. IB Gateway 2FA if Monday or after restart

### During Market
1. Signals: `docker logs aegis-v2 2>&1 | grep SIGNAL_ARRIVED | tail -10`
2. Trades: `docker logs aegis-v2 2>&1 | grep SIM_TRADE | tail -10`
3. P&L: `docker logs aegis-v2 2>&1 | grep HEARTBEAT | tail -1`
4. Errors: `docker exec aegis-v2 cat /app/data/bridge_stderr.log | tail -10`

### Post-Market (21:00 UTC)
1. Daily P&L from heartbeat
2. Chandelier exits: `grep PositionClosed /app/events/current.ndjson`
3. Ouroboros nightly runs at 04:50 UTC

### Weekly (Monday)
1. IB Gateway 2FA re-auth
2. `docker system prune -f` if disk > 80% (also automated at 04:30 UTC daily)
3. Review `dynamic_weights.toml` changes

### Emergency Kill Switch
```bash
# Immediate halt — stops all trading, keeps containers running for forensics
ssh EC2 'docker exec aegis-v2 touch /app/KILL'

# Graceful flatten — engine sells all positions then halts
ssh EC2 'docker exec aegis-v2 touch /app/PAUSE'

# Full stop — kills all containers
ssh EC2 'cd /home/ubuntu/nzt48-aegis-v2 && docker compose down'

# Resume after kill
ssh EC2 'docker exec aegis-v2 rm -f /app/KILL /app/PAUSE'
ssh EC2 'cd /home/ubuntu/nzt48-aegis-v2 && docker compose up -d'
```

---

## 10. Ouroboros Learning Loop

```
Engine (WAL) → nightly_v6.py (04:50 UTC) → system_memory.json + recommendations
→ config_writer.py (04:51 UTC) → dynamic_weights.toml → SIGHUP → Engine hot-reload
```

Tunes: Chandelier ATR multiplier, Kelly fractions, confidence floor, ticker blacklist (Wilson score), indicator gates, regime scales, entry type weights.

---

## 11. Validation Gate

**Target**: 100+ paper trades before considering live.

| Gate | Target | Current (est.) | Status |
|------|--------|-----------------|--------|
| Total trades | 100 | ~64 | 64% |
| Win Rate | >= 40% | 35.4% | Below — dragged by LSEETF |
| Profit Factor | >= 1.3 | ~0.77 | Below |
| Exchanges traded | >= 4 | 5 (LSE, LSEETF, HKEX, US, EURONEXT) | Pass |
| Max consecutive losses | < 8 | ~14 (Mar 19 session) | Fail |
| Spread drag | < 30% gross | Unknown | Need more data |
