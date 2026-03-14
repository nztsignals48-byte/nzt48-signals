# NZT-48 V8.0 -- Apex Predator Engine
## System Status: PAPER TRADING
## Last Updated: 2026-03-04

---

### Architecture

- **Orchestrator**: APScheduler + AsyncIO event loop (`main.py` ~7800 lines), 60s continuous scan
- **State**: Redis SSOT via `StateManager` (Lua atomic scripts, fail-closed after 3 consecutive Redis failures)
- **Ulysses Lock**: Config frozen at 07:55 UK, SHA256 hash verified every tick -- mismatch = HALT
- **Ghost Ledger**: Shadow execution engine for DSR comparison (tracks what *would* have happened)
- **Clock**: Single time source (`core/clock.py`) -- all modules import from here, never standalone `datetime.now()`
- **Tick Loop**: `command_center/tick_loop.py` -- ring buffer telemetry (C-05), Numba JIT warm-up pre-market (C-17)
- **15 strategies** (S1-S15), ISA-primary with global module set to dormant

### Key V8.0 Changes (Contradiction Audit C-01 to C-26)

| Code | Fix |
|------|-----|
| C-01 | Ulysses Lock now uses UK time, not UTC |
| C-02 | LSE open = 08:00 (was incorrectly 09:00) |
| C-03 | 5x overnight kill uses UK timezone (BST-safe) |
| C-04 | Staleness detection: warn if data fetch > 2.5s (monotonic clock) |
| C-05 | Ring buffer telemetry for tick/sniper latency tracking |
| C-06 | Phantom tickers in settings.yaml flagged as DEAD |
| C-07 | Slippage delegated to `execution/cost_model.py` (no more hardcoded values) |
| C-08 | Per-ticker leverage factor in `TickerEntry` dataclass |
| C-09 | `overnight_kill` flag on 5x ETPs -- mandatory session-end close |
| C-10 | `FROZEN_TICKERS` frozenset -- immutable runtime universe |
| C-13 | Singleton timezone objects (replaced 7 independent ZoneInfo definitions) |
| C-14 | Redis-SQLite reconciliation on startup and nightly |
| C-15 | Redis key scheme standardised (`nzt:pos:*`, `nzt:equity`, etc.) |
| C-16 | Lua atomic position close (P&L update + delete in single roundtrip) |
| C-17 | Numba JIT warm-up before market open; asyncio task GC prevention |
| C-18 | Async-safe news/data feed calls (no event loop blocking) |
| C-21 | `datetime.utcnow()` eliminated (deprecated Python 3.12+) |
| C-23 | Cached last-known-good VIX fallback in cross-asset macro |
| C-24 | Stateful EVT `TailRiskMonitor` with per-ticker GPD caching (1h TTL), Numba hot loops |
| C-25 | Conservative API rate limits to avoid silent exhaustion |
| C-26 | Kill switch persisted in Redis (survives container restarts) |

### Active ISA Universe (12 Core Tickers)

| Ticker | Name | Lev | Dir |
|--------|------|-----|-----|
| QQQ3.L | Nasdaq 100 3x Long | 3x | LONG |
| 3LUS.L | S&P 500 3x Long | 3x | LONG |
| 3SEM.L | Semiconductors 3x Long | 3x | LONG |
| GPT3.L | AI / GPT 3x Long | 3x | LONG |
| NVD3.L | NVIDIA 3x Long | 3x | LONG |
| TSL3.L | Tesla 3x Long | 3x | LONG |
| TSM3.L | TSMC 3x Long | 3x | LONG |
| MU2.L | Micron 2x Long | 2x | LONG |
| QQQS.L | Nasdaq 100 3x Short | 3x | SHORT |
| 3USS.L | S&P 500 3x Short | 3x | SHORT |
| QQQ5.L | Nasdaq 100 5x Long | 5x | LONG |
| SP5L.L | S&P 500 5x Long | 5x | LONG |

Plus 10 EXTENDED (tradable) + 13 SECTOR_RADAR (monitoring only). Source of truth: `uk_isa/isa_universe.py`.

### File Structure

```
main.py                    Orchestrator (APScheduler, 60s scan, ~7800 lines)
config/settings.yaml       All parameters (993 lines)
core/
  clock.py                 Single time source (UK/UTC/ET + monotonic)
  state_manager.py         Redis SSOT, Ulysses Lock, Ghost Ledger, Lua scripts
  evt.py                   EVT tail risk monitor (GPD, Numba, per-ticker cache)
  cross_asset_macro.py     VIX + DXY + Credit + Fear&Greed + HMM regime
  ml_meta_model.py         De Prado meta-labelling binary gate
  chandelier_exit.py       Le Beau 1999, 5-rung profit ladder, Redis-persisted
  portfolio_heat.py        RC-02 daily P&L circuit breaker
  earnings_fade_gate.py    RC-07b Buy-the-Rumour / Sell-the-News gate
  tail_loss_monitor.py     CVaR-based tail monitoring
  (50+ modules total)
strategies/
  daily_target.py          S15 -- 2% daily target compounding machine
  mean_reversion.py        S3 -- DORMANT (preserved, disabled)
  (15 strategies: S1-S15)
uk_isa/
  isa_universe.py          SSOT for all ticker metadata, leverage, costs
  multiframe_analytics.py  Multi-timeframe momentum scoring
  volatility_regime.py     Volatility regime classification
  predictive_scoring.py    Composite predictive score
  sector_rotation.py       Sector rotation signals
  correlation_engine.py    Ledoit-Wolf shrinkage correlation matrix
  lse_registry.py          Auto-scrape LSE leveraged ETPs
execution/
  virtual_trader.py        Paper trade execution with EVT veto
  cost_model.py            Perold shortfall + Almgren-Chriss impact
  adaptive_twap.py         Time-weighted execution
  exit_engine.py           Exit logic orchestrator
command_center/
  tick_loop.py             Real-time tick processing, ring buffer telemetry
  server.py                FastAPI command/control endpoints
feeds/
  data_feeds.py            Multi-source data with dedup cache
  news_feed.py             News sentiment feed
  indicators.py            Technical indicator calculations
  (22 feed modules)
qualification/
  dynamic_sizer.py         Kelly + multi-factor position sizing
scripts/
  sprint6_live_gate.py     Romano & Wolf 10-criteria Go/No-Go
  backup_to_s3.sh          Daily S3 backup
  deploy_to_ec2.sh         EC2 deployment script
```

### Infrastructure

- **EC2**: t3.small (us-east-1c), instance `i-027add7c7366d4c86`
- **Docker Compose**: `nzt48` (engine+API :8000) + `nzt48-dashboard` (Next.js :3001) + `nzt48-redis` (internal only)
- **Redis**: Password `nzt48redis`, internal Docker network, not host-exposed
- **Mode**: PAPER, £10,000 starting equity UK ISA
- **No Elastic IP** -- IP changes on stop/start (TODO: allocate in AWS Console)

### Risk Controls

| Control | Value |
|---------|-------|
| Max risk per trade | 0.75% of equity |
| Max position size | 5% of nominal bankroll |
| Daily P&L halt (RC-02) | -10% |
| Max drawdown halt | -8% |
| Consecutive loss halt | 5 losses |
| Spread gate veto | > 32 bps |
| 5x overnight kill (C-09) | Mandatory session-end close |
| EVT tail veto (C-24) | GPD-based VaR exceedance |
| Earnings fade gate (RC-07b) | Pre-earnings run-up >= 8% blocks longs |
| MTRL minimum for live | 63 trading days |
| Go/No-Go gate | 10 criteria (Romano & Wolf) |

### Version History

| Version | Date | Summary |
|---------|------|---------|
| V8.0 | 2026-03-04 | Contradiction audit (C-01 to C-26), Redis SSOT, Ulysses Lock, EVT TailRiskMonitor, clock.py single time source, Ghost Ledger |
| V7.0 | 2026-03-01 | Master Plan expansion |
| V2.0 | 2026-02-25 | UK ISA Momentum-Volatility pivot, `uk_isa/` module suite |
| V1.0 | 2026-02-24 | Initial system |

### Quick Commands

```bash
# SSH to EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# Engine logs
docker logs nzt48 --tail 50

# Restart engine
docker compose restart nzt48

# Rebuild + deploy
bash scripts/deploy_to_ec2.sh

# Kill port 8080 (if stuck)
lsof -i :8080 -t | xargs kill

# Redis CLI (from inside container)
docker exec -it nzt48-redis redis-cli -a nzt48redis

# S3 backup
bash scripts/backup_to_s3.sh
```
