# NZT-48 Live Trading Deployment Checklist
## Build Week 7-8 — EC2 Deployment Infrastructure

**Status**: Ready for deployment
**Last Updated**: 2026-03-13
**Target Environment**: EC2 c7i-flex.large (Elastic IP 3.230.44.22)

---

## PHASE 1: PRE-DEPLOYMENT VALIDATION

### Paper Trading Gates (Must Pass Before Live)

- [ ] **Gate 1: Entry Quality Threshold**
  - Requirement: ≥70% entries in first rung over 100+ paper trades
  - Validation: Check `daily_metrics.avg_entry_quality_pct` in paper trading database
  - Evidence location: `/data/trades.db` (paper mode)
  - Status check: `SELECT AVG(entry_quality_pct) FROM trades WHERE trade_date >= DATE('now', '-30 days');`

- [ ] **Gate 2: Win Rate Stability**
  - Requirement: ≥50% win rate over 100+ paper trades, stable over 7-day windows
  - Validation: Check rolling 50-trade win rate
  - Evidence location: Paper trading Grafana dashboard (rolling metrics)
  - Status check: Recent 50 trades with ≥50% win rate

- [ ] **Gate 3: Drawdown Recovery**
  - Requirement: Recover from -2.5% heat within 5 trading days (not overnight)
  - Validation: Check circuit breaker event timeline
  - Evidence location: `circuit_breaker_events` table (paper database)
  - Status check: All -2.5% triggers followed by recovery within 5 days

- [ ] **Gate 4: ISA Compliance Dry-Run**
  - Requirement: 100% of trades pass ISA audit (no violations in 50+ trades)
  - Validation: All position limits respected + no cross-ticker conflicts
  - Evidence location: Paper database trade audit trail
  - Status check: `SELECT COUNT(*) FROM trades WHERE isa_compliant = 0;` returns 0

### Account & Credentials Validation

- [ ] **IBKR Live Account Setup**
  - [ ] Account type: **Live (not paper)**
  - [ ] Account value: ≥£10,000 equity (ISA/GIA)
  - [ ] API access: Enabled for client ID 101
  - [ ] Port 4004 verified working (live API port, vs 4002=paper)
  - [ ] 2FA authentication configured (phone + backup)

- [ ] **IB Gateway Readiness**
  - [ ] IB Gateway running on EC2 via Docker (gnzsnz/ib-gateway:stable)
  - [ ] Listening on port 4004 (live account)
  - [ ] Daily restart strategy verified (IBC auto-restart at 04:45 GMT)
  - [ ] Weekly 2FA timeout: Monday 09:00 GMT (requires manual auth on phone)

- [ ] **Credentials Storage**
  - [ ] `.env.production` updated with live account credentials
  - [ ] `TWS_USERID` = live account username
  - [ ] `TWS_PASSWORD` = live account password (in secrets manager)
  - [ ] `TRADING_MODE` = `live` (not `paper`)
  - [ ] `TELEGRAM_BOT_TOKEN` = valid token for live alerts
  - [ ] `TELEGRAM_CHAT_ID` = target channel/group

### Database & State Initialization

- [ ] **PostgreSQL Live Database**
  - [ ] `trades_live` database created
  - [ ] Schema initialized (trades, positions, metrics tables)
  - [ ] Backup strategy configured (daily to S3)
  - [ ] Connection string: `postgresql://nzt48:${POSTGRES_PASSWORD}@postgres:5432/trades_live`

- [ ] **Redis State Persistence**
  - [ ] Redis configured with `appendonly yes` (durability)
  - [ ] `requirepass nzt48redis` set (password protection)
  - [ ] Database 0: critical state (noeviction policy)
  - [ ] Database 1: telemetry (allkeys-lru policy)
  - [ ] Connection verified: `redis-cli -a nzt48redis ping`

- [ ] **Initial Position State**
  - [ ] `/data/positions/open_positions.json` initialized (empty {})
  - [ ] Directory permissions: `755` for data directories
  - [ ] Volume mounts verified in docker-compose-live.yml

---

## PHASE 2: CONTAINER BUILD & DEPLOYMENT

### Docker Image Build

- [ ] **Dockerfile.aegis-v2-live**
  - Location: `/docker/Dockerfile.aegis-v2-live`
  - Status: Built and tested locally
  - Build command:
    ```bash
    docker build \
      --build-arg GIT_SHA=$(git rev-parse HEAD) \
      -f docker/Dockerfile.aegis-v2-live \
      -t nzt48/aegis-v2-live:latest .
    ```
  - Image size: <800 MB (verify with `docker images`)
  - Base image: `python:3.11-slim`
  - Healthcheck endpoint: `/metrics`

- [ ] **Multi-stage Build Verification**
  - [ ] Stage 1: Dependencies compiled (no size bloat)
  - [ ] Stage 2: Runtime only (libgomp, curl, jq)
  - [ ] Copy Python packages from builder (verified)
  - [ ] No git/build tools in final image

### Docker Compose Deployment

- [ ] **docker-compose-live.yml**
  - Location: `/docker/docker-compose-live.yml`
  - Services: 6 (aegis-v2-live, postgres, redis, grafana, prometheus, network)
  - Network configuration: bridges nzt48-live + shares nzt48-default (ib-gateway)

- [ ] **Service Health Checks**
  - [ ] aegis-v2-live: curl `/metrics` (30s interval, 3 retries)
  - [ ] postgres: pg_isready (10s interval, 5 retries)
  - [ ] redis: redis-cli ping (10s interval, 5 retries)
  - [ ] grafana: curl `/api/health` (30s interval, 3 retries)
  - [ ] prometheus: curl `/-/healthy` (30s interval, 3 retries)

- [ ] **Memory & CPU Limits**
  - [ ] aegis-v2-live: 1536 MB limit (instance has 4 GB)
  - [ ] postgres: 512 MB
  - [ ] redis: 512 MB
  - [ ] grafana: 256 MB
  - [ ] prometheus: 512 MB
  - [ ] Total: 3.3 GB (leaves 700 MB headroom)

- [ ] **Volume Configuration**
  - [ ] `/data/trades` → SQLite trades.db
  - [ ] `/data/positions` → open_positions.json
  - [ ] `/logs` → live_trading.log + rotation
  - [ ] postgres_data → PostgreSQL WAL + data files
  - [ ] redis_data → RDB snapshots + AOF

### EC2 Deployment Steps

- [ ] **SSH into EC2**
  ```bash
  ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
  ```

- [ ] **Clone/Pull Latest Code**
  ```bash
  cd /home/ubuntu/nzt48-signals
  git pull origin main
  git status  # verify clean working tree
  ```

- [ ] **Update Environment Files**
  ```bash
  # Copy .env.production with live credentials
  cp .env.production.example .env.production
  # Edit with LIVE account details:
  # - TWS_USERID (live)
  # - TWS_PASSWORD (secure)
  # - TRADING_MODE=live
  # - IBKR_PORT=4004
  ```

- [ ] **Rebuild & Deploy**
  ```bash
  # Stop old containers
  docker compose down

  # Build live image
  docker build \
    --build-arg GIT_SHA=$(git rev-parse HEAD) \
    -f docker/Dockerfile.aegis-v2-live \
    -t nzt48/aegis-v2-live:latest .

  # Start live deployment
  docker compose -f docker/docker-compose-live.yml up -d

  # Verify all services healthy
  docker compose -f docker/docker-compose-live.yml ps
  docker compose -f docker/docker-compose-live.yml logs aegis-v2-live
  ```

- [ ] **Verify Container Health**
  ```bash
  # Check all services
  docker compose -f docker/docker-compose-live.yml ps

  # Expected output:
  # aegis-v2-live     healthy
  # postgres          healthy
  # redis             healthy
  # grafana           healthy
  # prometheus        healthy

  # View logs
  docker compose -f docker/docker-compose-live.yml logs -f aegis-v2-live
  ```

---

## PHASE 3: LIVE TRADING INITIALIZATION

### First Trade Manual Approval Process

- [ ] **Risk Officer Sign-Off**
  - [ ] Risk officer reviews:
    - [ ] Entry quality metrics (≥70%)
    - [ ] Position sizing rules (max 5% per trade)
    - [ ] Circuit breaker thresholds (-4% daily)
    - [ ] ISA compliance framework
  - [ ] Sign-off date/time: ________________
  - [ ] Officer name/email: ________________

- [ ] **Live Account Funding**
  - [ ] Account equity: £10,000 (confirmed)
  - [ ] Transfer settled (T+2 for bank transfers)
  - [ ] No pending deposits/withdrawals

- [ ] **System Readiness Check**
  - [ ] IB Gateway connected and authenticated
  - [ ] IBKR API responding to test requests
  - [ ] Telegram bot connected (test message sent)
  - [ ] Database health check:
    ```bash
    docker compose -f docker/docker-compose-live.yml exec postgres \
      psql -U nzt48 -d trades_live -c "SELECT COUNT(*) FROM trades;"
    ```

### Entry Signal Processing (First Trade)

- [ ] **Universe Scan Operational**
  - [ ] TieredUniverseScanner running (60s interval)
  - [ ] ISA universe (12 tickers) loaded and validated
  - [ ] No stale data flags in logs

- [ ] **Entry Signal Evaluation**
  - [ ] Confidence threshold: ≥65%
  - [ ] Entry quality: ≥70% (first rung priority)
  - [ ] Position sizing: ≤5% account, ≤£990 Kelly
  - [ ] Circuit breaker: Not active

- [ ] **Order Execution (Live)**
  - [ ] Order routed to IBKR (port 4004)
  - [ ] Order status: FILLED (not PENDING)
  - [ ] Position recorded in `/data/positions/open_positions.json`
  - [ ] Trade logged to PostgreSQL trades table
  - [ ] Telegram alert sent:
    ```
    📈 ENTRY
    Ticker: [symbol]
    Price: £X.XX
    Size: N shares
    Confidence: XX%
    Quality: XX%
    ```

- [ ] **Post-Entry Monitoring (First 10 Minutes)**
  - [ ] Market update loop running (5s interval)
  - [ ] Position P&L updating every 5s
  - [ ] Chandelier exit monitoring active
  - [ ] No unexpected errors in logs

---

## PHASE 4: ONGOING MONITORING & MAINTENANCE

### Daily Checks (During Market Hours)

- [ ] **Morning Standup (08:00 GMT)**
  - [ ] Verify all containers healthy: `docker compose ps`
  - [ ] Check Grafana dashboard at http://3.230.44.22:3000
  - [ ] Review yesterday's P&L and trades
  - [ ] Verify no circuit breaker halts

- [ ] **Intraday Monitoring**
  - [ ] Daily P&L tracking (target: +0.3% to +0.5%)
  - [ ] Win rate monitoring (target: ≥50%)
  - [ ] Entry quality tracking (target: ≥70%)
  - [ ] Position count (max 3 concurrent)
  - [ ] Heat level (max -4% daily loss)

- [ ] **End of Day (17:00 GMT)**
  - [ ] Reconcile trade count vs Telegram alerts
  - [ ] Verify all positions closed or transferred to next day
  - [ ] Calculate daily P&L (realized + unrealized)
  - [ ] Log metrics to daily_metrics table
  - [ ] Back up SQLite + PostgreSQL data to S3

### Weekly Checks

- [ ] **Monday 09:00 GMT**
  - [ ] IB Gateway 2FA re-authentication required
  - [ ] Verify IB Gateway reconnected after weekend
  - [ ] Check Redis durability (RDB + AOF files)

- [ ] **Weekly Risk Review**
  - [ ] Win rate trend (should be ≥50%)
  - [ ] Entry quality trend (should be ≥70%)
  - [ ] Largest single loss (should be <2% account)
  - [ ] Consecutive losses (max 3 before cooldown)
  - [ ] Any circuit breaker events (should be rare)

- [ ] **System Backup**
  ```bash
  # Backup databases to S3
  aws s3 cp /data/trades.db s3://nzt48-backups/live-trades-$(date +%Y%m%d).db
  aws s3 cp /data/positions/open_positions.json s3://nzt48-backups/live-positions-$(date +%Y%m%d).json
  ```

### Monthly Reviews

- [ ] **Performance Attribution**
  - [ ] Total trades: _____ (target: 20-30 per day)
  - [ ] Win rate: ____% (target: ≥50%)
  - [ ] Monthly P&L: £_____ (target: 0.3-0.5% daily = 6.5-10% monthly)
  - [ ] Largest drawdown: ____% (should recover in <5 days)
  - [ ] Sharpe ratio: _____ (target: ≥0.8)

- [ ] **Risk Framework Review**
  - [ ] Position limits enforced (max 5% per trade)
  - [ ] ISA compliance: 100% (0 violations)
  - [ ] Circuit breaker triggered: _____ times (should be ≤2)
  - [ ] False exit signals: _____ (review and tune)
  - [ ] Entry quality by instrument (identify weak performers)

---

## PHASE 5: INCIDENT RESPONSE

### Circuit Breaker Activation (Daily -4% Loss)

- [ ] **Immediate Actions (Automatic)**
  - [ ] Trading halted automatically
  - [ ] All new entries blocked
  - [ ] Telegram critical alert sent
  - [ ] Circuit breaker event logged to database
  - [ ] Existing positions managed via ChandelierExit

- [ ] **Manual Response**
  - [ ] Risk officer notified via Telegram
  - [ ] Review cause of daily loss (market event? bad entries?)
  - [ ] No manual trading allowed until next market open (08:00 GMT)
  - [ ] System clears automatically at 08:00 GMT next day

### IB Gateway Connection Loss

- [ ] **Automatic Recovery** (IBC handles this)
  - [ ] IBC reconnects within 60 seconds
  - [ ] If reconnect fails: container restart via `on-failure:5`
  - [ ] Max 5 restart attempts (then halt)

- [ ] **Manual Recovery Steps**
  ```bash
  # Check IB Gateway logs
  docker compose logs ib-gateway | tail -50

  # Restart IB Gateway
  docker compose restart ib-gateway

  # Verify connection
  docker compose logs ib-gateway | grep "Connected"

  # Re-authenticate if needed (2FA)
  # Access IB Gateway UI via VNC (port 5900)
  ```

### Database Corruption / Data Loss

- [ ] **Restore from Backup**
  ```bash
  # Stop containers
  docker compose down

  # Restore from S3
  aws s3 cp s3://nzt48-backups/live-trades-$(date +%Y%m%d).db /data/trades.db

  # Restart
  docker compose up -d
  ```

- [ ] **Verify Data Integrity**
  ```bash
  # Check trade count matches Telegram archive
  sqlite3 /data/trades.db "SELECT COUNT(*) FROM trades;"

  # Compare with daily_metrics
  sqlite3 /data/trades.db "SELECT * FROM daily_metrics ORDER BY date DESC LIMIT 7;"
  ```

---

## PHASE 6: SHUTDOWN & MAINTENANCE

### Scheduled Maintenance (Monthly)

- [ ] **Database Optimization**
  ```bash
  # Vacuum SQLite
  sqlite3 /data/trades.db "VACUUM;"

  # Reindex PostgreSQL
  docker compose exec postgres psql -U nzt48 -d trades_live -c "REINDEX DATABASE trades_live;"
  ```

- [ ] **Log Rotation**
  - [ ] Check `/logs/live_trading.log` size
  - [ ] Rotate if > 100 MB
  - [ ] Archive old logs to S3

### Emergency Shutdown

- [ ] **Graceful Shutdown**
  ```bash
  # Stop new entries gracefully
  docker compose exec aegis-v2-live touch /tmp/graceful_shutdown

  # Wait for existing positions to exit (max 5 minutes)
  sleep 300

  # Force stop if still running
  docker compose down
  ```

- [ ] **Data Preservation**
  ```bash
  # Backup everything before shutdown
  aws s3 sync /data s3://nzt48-backups/final-backup-$(date +%Y%m%d_%H%M%S)/
  aws s3 sync /logs s3://nzt48-backups/logs-$(date +%Y%m%d_%H%M%S)/
  ```

---

## File Checklist

| Component | File Path | Status |
|-----------|-----------|--------|
| Live Dockerfile | `/docker/Dockerfile.aegis-v2-live` | ✅ Created |
| Live Orchestrator | `/scripts/run_live_trading.py` | ✅ Created |
| Docker Compose | `/docker/docker-compose-live.yml` | ✅ Created |
| Monitoring Daemon | `/monitoring/live_trading_monitor.py` | ✅ Created |
| Grafana Dashboard | `/monitoring/grafana_dashboard.json` | ✅ Created |
| Prometheus Config | `/monitoring/prometheus.yml` | ✅ Created |
| Grafana Datasources | `/monitoring/grafana_datasources.yml` | ✅ Created |
| Deployment Guide | `/DEPLOYMENT_CHECKLIST_LIVE.md` | ✅ Created |

---

## Critical Requirements Verification

- [ ] **ISA Compliance**
  - Every trade audited in PostgreSQL
  - Position limits enforced (max 5% per trade, max 3 concurrent)
  - No cross-ticker conflicts (1 position per ticker max)

- [ ] **Position Limits**
  - Max 5% account per trade (£500 on £10k)
  - Max £990 per Kelly-sized trade
  - Max 3 concurrent positions
  - Max 1 position per ticker

- [ ] **Daily Circuit Breaker**
  - Loss > 4% → AUTO-HALT all trading
  - Clears at 08:00 GMT next day (automatic)
  - Logged to database + Telegram alert

- [ ] **Logging & Transparency**
  - All trades → PostgreSQL (permanent audit log)
  - Daily metrics → daily_metrics table
  - Circuit breaker events → circuit_breaker_events table
  - Telegram alerts: every entry, every exit, daily summary

- [ ] **No Live Execution Until**
  - Paper trading passed all 4 gates ✅
  - Daily paper backtest shows ≥70% entry quality ✅
  - Manual approval given ✅

---

## Deployment Sign-Off

- [ ] Code reviewed and tested locally
- [ ] Paper trading gates confirmed (4/4 passed)
- [ ] Risk officer approval obtained
- [ ] Live account funded (£10,000+)
- [ ] IB Gateway connection verified (port 4004)
- [ ] Containers built and health checks passing
- [ ] Database initialized and backed up
- [ ] Monitoring dashboard configured
- [ ] Telegram alerts tested

**Deployment Date**: ________________
**Approved By**: ________________
**Risk Officer**: ________________

---

## Post-Deployment Monitoring (First 7 Days)

- [ ] Day 1: ≥2 trades, entry quality ≥70%, no circuit breaker
- [ ] Day 2-3: Win rate stabilizing (≥50%), entry timing consistent
- [ ] Day 4-5: Daily P&L in target range (+0.3% to +0.5%)
- [ ] Day 6-7: Full week of data validates all systems working

If any metric is outside target: **HALT LIVE TRADING** and revert to paper mode.

---

## Success Metrics (After 100 Trades)

- [ ] Win rate: ≥50%
- [ ] Entry quality: ≥70%
- [ ] Daily average P&L: +0.3% to +0.5%
- [ ] Max daily loss: <4% (circuit breaker never triggered)
- [ ] ISA compliance: 100% (0 violations)
- [ ] Sharpe ratio: ≥0.8
- [ ] Max consecutive losses: ≤3

---

*This checklist must be completed before first live trade.
System is production-ready only when ALL boxes are checked.*
