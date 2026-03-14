# AEGIS — Infrastructure + Operations
> Liquidity scaling, deployment, startup gate, daily procedures.
> Extracted from AEGIS Master Plan v16.2.
> See [README](README.md) for full index.
---

# SECTION 7: LIQUIDITY SCALING {#section-7}

## Market Impact Model (Kyle's Lambda)

```
Delta_P ~ lambda x sqrt(Q / V_daily)
lambda = 0.02 (conservative for leveraged ETPs; units = fractional price impact)
```

### Impact Table — QQQ3.L (57K shares/day, ~GBP 1.425M ADV, 3% heat)

| Equity | Heat (3%) | Impact | Verdict |
|--------|-----------|--------|---------|
| 10K | 300 | <0.1 bps | SAFE |
| 50K | 1,500 | ~0.7 bps | SAFE |
| 100K | 3,000 | ~0.9 bps | SAFE |
| 500K | 15,000 | ~2.1 bps | CAUTION |
| 1M | 30,000 | ~2.9 bps | CAUTION |
| 3M+ | 90,000 | ~5.0 bps | DANGER — TWAP required |

### Scaling Tiers

- **10K-100K**: No constraints. Market orders acceptable.
- **100K-500K**: Limit orders preferred. Dynamic heat cap binding.
- **500K-1M**: TWAP/VWAP mandatory for large orders. Expand universe.
- **1M-3M**: Multi-instrument diversification. Iceberg orders.
- **3M+**: Leveraged ETP universe fundamentally too small. Migrate to futures.

---

# SECTION 8: INFRASTRUCTURE {#section-8}

### Deployment Architecture (Current)

```
EC2 t3.small (2GB) — upgrading to t3.medium (4GB) in ~1 week
├── nzt48 container (engine + FastAPI API, supervisord, 1536MB limit)
│   ├── main.py (APScheduler, signal engine, 60s scan loop)
│   ├── uvicorn (dashboard.api:app on :8000)
│   ├── Connects to: ib-gateway:4002 (IBKR data), redis:6379 (state)
│   └── Env: IBKR_HOST=ib-gateway, IBKR_PORT=4002
├── ib-gateway container (IB Gateway + IBC, gnzsnz/ib-gateway:stable, 1024MB limit)
│   ├── IB Gateway 10.37 (headless via Xvfb)
│   ├── IBC (auto-login, daily restart, dialog handling)
│   ├── Port 4002 (paper mode) — internal Docker network only
│   └── Env: TWS_USERID, TWS_PASSWORD, TRADING_MODE=paper, READ_ONLY_API=true
├── redis container (Redis 7 Alpine, 256MB limit)
│   └── AOF persistence, Chandelier exit state, session cache
└── Dashboard container: REMOVED (freed ~200-400MB for IB Gateway)
```

### Immediate (This Week)

| Task | Priority | Effort |
|------|----------|--------|
| I-01: Allocate Elastic IP | P0 | 30 min |
| I-02: Automate S3 backup cron | P0 | 30 min |
| I-03: Fix VIX default (0 -> fail-closed 99) | P0 | 1h |
| I-04: Redis WAIT for state persistence | P1 | 2h |
| **I-10: Deploy IB Gateway + IBC on EC2** | **P0** | **1h** |
| **I-11: Set IBKR credentials in .env.production** | **P0** | **10 min** |
| **I-12: First-time 2FA approval for IB Gateway** | **P0** | **5 min** |

### Short-Term (Weeks 1-2)

| Task | Priority | Effort |
|------|----------|--------|
| I-05: Upgrade t3.small -> t3.medium | P1 | 1h |
| I-06: CloudWatch monitoring | P1 | 8h |
| I-07: Redis 256MB -> 512MB | P2 | 30 min |
| **I-07B: SQLite Async Write Queue** | **P1** | **4h** |

### I-07B: SQLite Concurrency Protection

**Problem**: With multiple simultaneous trades, concurrent SQLite writes will cause `database is locked` errors. SQLite uses a single-writer lock — any concurrent INSERT/UPDATE from different coroutines will block or fail.

**Solution**: Dedicated async writer coroutine with `asyncio.Queue`:
```
Trade Exec 1 --+
Trade Exec 2 --+--> Write Queue (asyncio.Queue) --> DB Writer (single coroutine)
Circuit Brkr --+
```
- ALL database writes go through the queue — no direct writes from trade coroutines
- Single writer coroutine processes queue sequentially (no lock contention)
- Queue has priority levels: EMERGENCY (circuit breakers, flatten) > TRADE (fills, stops) > TELEMETRY (metrics, logs)
- WAL mode enabled (`PRAGMA journal_mode=WAL`) for concurrent reads during writes
- Queue depth monitoring: if >50 pending writes, P1 alert (writer falling behind)
- This is a Phase 0/1 requirement — must be in place before multi-trade is enabled

### Medium-Term (Month 2)

| Task | Priority | Effort |
|------|----------|--------|
| I-08: PostgreSQL migration (RDS) | P2 | 24h |
| I-09: CI/CD pipeline (GitHub Actions) | P2 | 12h |

### Notification Architecture

| Priority | Use Case | Delivery |
|----------|----------|----------|
| P0 | Drawdown > L2, crash, cascade halt | Instant + SOUND |
| P1 | Trade fill, stop hit, regime change | Instant, silent |
| P2 | Signal generated, graduation | 30-min batch |
| P3 | ML health, macro summary | 2x daily digest |

Correlation escalation: 3+ P1 events in 15 min -> auto-escalate to P0.

**Notification Fallback (Defence-in-Depth)**:
- P0 alerts: Telegram AND email (via AWS SES). If Telegram delivery fails, auto-escalate to SMS (via AWS SNS) within 30 seconds.
- P1 burst protection: If >5 P1 events queue within 60 seconds, consolidate into single summary. P0 escalation bypasses P1 queue.
- Log all notification delivery failures as P1 incidents.

**Broker Failure Protocol**:
- If no order acknowledgment within 30s: retry once with exponential backoff.
- If no ack within 60s: enter DEGRADED (no new entries, monitor only).
- Open positions during broker outage: rely on pre-placed bracket orders (P1-12).
- Log all broker connectivity failures with timestamp and portfolio state.
- ISA broker: **Interactive Brokers (IBKR)** via IB Gateway + IBC on EC2. Backup: manual trading via IBKR web/mobile if IB Gateway disconnects.

---

# SECTION 8B: STARTUP READINESS GATE {#section-8b}

8 pre-flight checks before ANY trading logic executes.

| # | Check | READY | HALTED |
|---|-------|-------|--------|
| 1 | Database connectivity | All tables accessible | No connection |
| 2 | Redis + Chandelier state | Connected, state loaded | No connection |
| 3 | Data feed health (universe tickers) | Fresh data (<5 min) | >2 tickers stale or 0 data |
| 4 | Kill switch status | OFF | ON |
| 5 | Circuit breaker state | GREEN/YELLOW | RED/CRITICAL/HALTED |
| 6 | Disk space | >20% free | <10% free |
| 7 | Memory | >500MB free | <200MB free |
| 8 | Time sync (NTP) | Drift <5s | >30s drift |

Three-tier output: READY (full trading) / DEGRADED (monitoring only) / HALTED (nothing).

---

# SECTION 8C: DAILY OPERATIONAL PROCEDURES {#section-8c}

## Morning Checklist (07:30-08:00 UK)

1. Container health: `docker compose ps`
2. Overnight error count
3. Data feed status: verify tickers returning data
4. Disk space and memory
5. Startup Readiness Gate result

## Midday Checklist (12:00 UK)

1. Open positions P&L review
2. scan_health.json review
3. Circuit breaker status
4. Drought state

## Evening Checklist (17:00 UK)

1. Daily P&L log
2. Telegram alerts acknowledged
3. Backup verification
4. Tomorrow's calendar events

---
