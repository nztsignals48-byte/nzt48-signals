# AEGIS V2 — Canonical System Plan
**Last verified**: 2026-03-23 from actual code
**Branch**: feat/tier-system-enhancements-full

---

## 1. Architecture

```
[IBKR Gateway] → [Rust Engine] → [Python Brain] → [Risk Arbiter] → [Broker] → [WAL]
     ↑                                                                              ↓
[IB Gateway]                                                              [Ouroboros Nightly]
     ↑                                                                              ↓
[2FA Weekly]                                                          [dynamic_weights.toml]
```

### Components
| Component | Language | LOC | Purpose |
|-----------|----------|-----|---------|
| Engine | Rust | 3,443 | Tick processing, state machine, event loop |
| Risk Arbiter | Rust | 667 | 32 sequential risk checks, regime hierarchy |
| Exit Engine | Rust | 928 | Chandelier 5-rung trailing stop |
| Entry Engine | Rust | 786 | TypeA-F classification (reference/backtest only) |
| Position Sizer | Rust | 346 | Kelly criterion with fractional scaling |
| Portfolio | Rust | 530 | Position tracking, equity, drawdown |
| IBKR Broker | Rust | 1,585 | IB Gateway adapter, subscriptions |
| Bridge (Python) | Python | ~1,800 | 5-stage signal pipeline, 5 strategies |
| Ouroboros | Python | ~2,000 | Nightly learning, config generation |

### Docker Containers
| Container | Image | Port | Purpose |
|-----------|-------|------|---------|
| aegis-v2 | Custom (Rust+Python) | - | Engine + brain |
| aegis-ib-gateway | gnzsnz/ib-gateway | 4003 | IB Gateway + IBC |
| aegis-redis | redis:7-alpine | 6379 (internal) | State journal |

### Volumes
| Volume | Mount | Purpose |
|--------|-------|---------|
| aegis-events | /app/events | WAL .ndjson files (persistent) |
| aegis-data | /app/data | Reports, gate vetoes, scanner results |
| aegis-logs | /var/log | Cron logs, Ouroboros reports |
| aegis-redis-data | /data | Redis AOF persistence |
| claude-auth | /root/.claude | Claude CLI auth token |

---

## 2. Signal Pipeline (5 Stages)

### Stage 1: Indicator Computation (`_compute_indicators`)
- 5-minute OHLCV bar aggregation from tick stream
- RVOL (relative volume, 20-bar window)
- Hurst exponent (regime detection)
- ADX (trend strength, Wilder's method)
- Volume divergence, volume slope
- VPIN (shadow, never gates)
- IBS (Internal Bar Strength)
- VWAP (session-aware, resets on date change)
- Structural Tradability Score (0-100: spread + regime + volume + ADX + data quality)

### Stage 2: Quality Gates (`_check_quality_gates`)
- G1: Spread gate (leverage-scaled)
- G2: VWAP absolute extension (15% max)
- G3: VWAP directional extension (10% max long)
- G4: Structural tradability minimum (15/100)
- G5: Hurst extreme mean-reversion (< 0.10 blocked)
- G6: Ouroboros indicator gates (dynamic from nightly)

### Stage 3: Signal Generation (`_generate_signals`) — 5 SOURCES
1. **VanguardSniper** (Momentum): ADX-based trend + EMA + volume breakout. Non-mean-reverting regimes only.
2. **Orchestrator** (Multi-strategy): Configurable from strategies.toml, all regimes.
3. **IBS Mean Reversion** (NEW): Connors RSI-2/IBS combo. Mean-reverting/random regimes. IBS < 0.2 + RSI(2) < 15.
4. **Volume Expansion** (NEW): RVOL > 2.0 + ADX > 20 + 3+ up bars + price > EMA20.
5. **Opening Range Breakout** (NEW): US session 14:45-15:30 UTC. Breakout above first-30min high with volume.

Best 2 signals passed to Stage 4.

### Stage 4: Adjustments (`_apply_adjustments`)
- LSE boost during London hours (+20 confidence)
- Drawdown penalty (inverse boost during drawdown)
- Hour-of-day confidence weights
- Best signal selection
- Per-ticker cooldown (5 min in live, 0 in sim)
- STS confidence adjustment
- **TypeA-F classification** (assigns entry_type from indicators)
- **TypeA/D BLOCKED** — proven net losers, return None
- Adaptive entry type weights (from dynamic_weights.toml)
- Adaptive exchange weights
- Adaptive Kelly cap
- VPIN shadow fields
- Claude curator (shadow mode, non-blocking)

### Stage 5: Output
Signal dict → JSON → Rust engine via stdin/stdout pipe.

---

## 3. Risk Arbiter (32 Checks)

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

| Strategy | Status | Entry Type | WR (Backtest) | Notes |
|----------|--------|------------|---------------|-------|
| VanguardSniper | ACTIVE | Momentum | ~52% (TypeB) | Core strategy |
| Orchestrator | ACTIVE | Various | Varies | Config-driven |
| IBS_MeanReversion | ACTIVE | TypeE | ~57% (academic) | New 2026-03-23 |
| VolExpansion | ACTIVE | TypeB (refined) | ~55% (expected) | New 2026-03-23 |
| ORB_Breakout | ACTIVE | Unclassified | ~52% (academic) | US session only |
| TypeA (DipRecovery) | ACTIVE (monitored) | TypeA | 29.5% (backtest) | Collecting live data |
| TypeD (SupportBounce) | ACTIVE (monitored) | TypeD | 24.1% (backtest) | Collecting live data |

---

## 6. Deployment

### Deploy Sequence (MANDATORY)
```bash
git add . && git commit -m "..." && git push
rsync -avz --exclude '.git' ... ubuntu@3.230.44.22:/home/ubuntu/nzt48-aegis-v2/
ssh EC2 'cd /home/ubuntu/nzt48-aegis-v2 && docker compose build aegis-v2 && docker compose up -d'
```

### EC2 Details
- Instance: c7i-flex.large (4GB RAM, 2 vCPUs)
- IP: 3.230.44.22 (Elastic)
- Disk: 19GB (target <80% usage)
- SSH: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22`

---

## 7. Daily Operations

### Pre-Market (07:00 UTC)
1. SSH to EC2
2. `docker ps` — verify all 3 containers healthy
3. `docker logs aegis-v2 --tail 20` — check for errors
4. IB Gateway 2FA if needed (check IBKR mobile app)

### During Market
1. Check signals: `docker logs aegis-v2 2>&1 | grep SIGNAL | tail -10`
2. Check trades: `docker logs aegis-v2 2>&1 | grep SIM_TRADE | tail -10`
3. Check P&L: `docker logs aegis-v2 2>&1 | grep HEARTBEAT | tail -1`

### Post-Market (21:00 UTC)
1. Check daily P&L summary
2. Verify Chandelier exits fired for completed trades
3. Ouroboros nightly runs at 04:50 UTC — check next morning

### Weekly (Monday)
1. IB Gateway 2FA re-auth (IBKR mobile app)
2. `docker system prune -f` if disk > 80%
3. Review dynamic_weights.toml changes from nightly

---

## 8. Universe

| Exchange | Contracts | Currency | Notes |
|----------|-----------|----------|-------|
| SMART (US) | 548 | USD | NYSE + NASDAQ equities |
| LSE | 428 | GBP | FTSE 100+250 equities |
| TSE | 81 | JPY | Tokyo equities |
| HKEX | 54 | HKD | Hong Kong equities |
| LSEETF | 52 | USD/GBP | Leveraged ETPs |
| XETRA | 30 | EUR | German equities |
| EURONEXT | 18 | EUR | Paris equities |
| SGX | 11 | SGD | Singapore equities |
| Others | 29 | Various | KRX, ASX, AEB, etc. |
| **Total** | **1,251** | | |

Universe database: 867 tickers in `config/universe.json` (FTSE100+250, S&P500, NDX100).

---

## 9. Ouroboros Learning Loop

```
Engine (WAL) → nightly_v6.py (04:50 UTC) → JSON recommendations
→ config_writer.py (04:51 UTC) → dynamic_weights.toml → SIGHUP → Engine hot-reload
```

What it tunes:
- Chandelier ATR multiplier per regime
- Kelly fractions per tier
- Confidence floor
- Ticker blacklist (Wilson score interval)
- Indicator gates
- Regime scales
- Entry type weights

---

## 10. Validation Gate (100 trades)

Before going live, the system must complete 100+ paper trades passing:
- Win Rate >= 40%
- Profit Factor >= 1.3
- Trades on 4+ exchanges
- Max consecutive losses < 8
- Spread drag < 30% of gross P&L

**Current Status**: 4 trades completed. ~96 remaining.
