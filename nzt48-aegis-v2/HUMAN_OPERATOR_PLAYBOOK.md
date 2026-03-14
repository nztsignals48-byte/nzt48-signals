# AEGIS V2 -- Human Operator Playbook

**Deliverable 4 of the AEGIS V2 Apex Terminal Directive**
**Last Updated**: 2026-03-11
**System**: AEGIS V2 (UK ISA Momentum-Volatility Trading Engine)
**Mode**: Paper trading, GBP 10,000 starting equity

---

## 1. DAILY CHECKLIST (Before Market Open -- 07:30 London)

LSE opens at 08:00 London. Complete all checks by 07:45.

### 1.1 SSH into EC2

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
```

Expected: clean login, no disk warning banners.

### 1.2 Check Docker containers are healthy

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Expected output:

```
NAMES               STATUS                   PORTS
aegis-v2            Up 14 hours (healthy)
aegis-ib-gateway    Up 14 hours (healthy)
aegis-redis         Up 14 hours (healthy)
```

All three containers must show `(healthy)`. If any shows `(unhealthy)` or is missing, go to Section 4 (Incident Response).

### 1.3 Check IB Gateway is connected

IB Gateway runs in V2's own Docker network (`aegis-net`). V1 is dead.

```bash
docker ps --filter name=aegis-ib-gateway --format "table {{.Names}}\t{{.Status}}"
```

Expected:

```
NAMES               STATUS
aegis-ib-gateway    Up 3 days (healthy)
```

Verify AEGIS can reach the gateway on port 4004:

```bash
docker exec aegis-v2 bash -c "echo quit | nc -w 2 aegis-ib-gateway 4004 && echo 'OK: port 4004 reachable' || echo 'FAIL: cannot reach IB Gateway'"
```

Expected: `OK: port 4004 reachable`

### 1.4 Check last Ouroboros run completed

Ouroboros runs nightly at 23:50 ET (Mon-Fri). Check its log:

```bash
docker exec aegis-v2 tail -20 /app/events/ouroboros.log
```

Look for the previous night's timestamp and a completion line (no tracebacks). If the last entry is older than 24 hours on a weekday, Ouroboros may have failed -- check further:

```bash
docker exec aegis-v2 ps aux | grep ouroboros
docker exec aegis-v2 ps aux | grep supercronic
```

Supercronic must be running (it schedules Ouroboros). If supercronic is missing, the container needs a restart.

### 1.5 Verify no HALT regime is active

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis GET aegis:regime
```

Expected: `GREEN` or `YELLOW`.

If result is `RED` or `HALT`, do NOT clear it blindly. Go to Section 4.3 (HALT Regime Triggered).

### 1.6 Check disk space

```bash
df -h / | tail -1
```

Expected: `Use%` below 80%. If above 85%, go to Section 4.6 (Full Disk).

### 1.7 Check Redis memory

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis INFO memory | grep used_memory_human
```

Expected: well below 256MB (the configured maxmemory). If above 200MB, investigate key growth:

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis DBSIZE
```

### 1.8 Quick log health check

```bash
docker logs aegis-v2 --tail 30 --since 1h
```

Scan for `ERROR`, `PANIC`, or `FATAL`. A few `WARN` entries about transient network timeouts are normal. Repeated errors are not.

### Daily Checklist Summary

| # | Check | Command | Good | Bad |
|---|-------|---------|------|-----|
| 1 | SSH access | `ssh -i ...` | Clean login | Connection refused |
| 2 | Containers up | `docker ps` | Both `(healthy)` | Missing or unhealthy |
| 3 | IB Gateway | `nc` test | Port 4004 reachable | Connection refused |
| 4 | Ouroboros | `tail ouroboros.log` | Last night's timestamp | Stale or traceback |
| 5 | Regime | `redis GET aegis:regime` | GREEN or YELLOW | RED or HALT |
| 6 | Disk | `df -h /` | Below 80% | Above 85% |
| 7 | Redis memory | `INFO memory` | Below 200MB | Above 200MB |
| 8 | Log scan | `docker logs --tail 30` | No ERROR/PANIC | Repeated errors |

---

## 2. WEEKLY CHECKLIST (Monday Morning)

### 2.1 IB Gateway 2FA Re-authentication

IB Gateway requires weekly 2FA re-auth every Monday morning. IBC (IB Controller) handles daily restarts, but the TWS/Gateway session expires weekly.

**Steps:**

1. Open the IB Gateway VNC or web portal (if configured) on the EC2 host
2. Complete the 2FA challenge from your phone (IBKR mobile app or SMS)
3. Verify reconnection:

```bash
docker logs aegis-ib-gateway --tail 20
```

Look for: connection established / authenticated messages.

4. Verify AEGIS can reach gateway after re-auth:

```bash
docker exec aegis-v2 bash -c "echo quit | nc -w 2 aegis-ib-gateway 4004 && echo 'OK' || echo 'FAIL'"
```

**If 2FA fails or you miss the Monday window:** IB Gateway will disconnect. AEGIS will detect this and enter backoff mode (see Section 4.2). Re-auth as soon as possible.

### 2.2 Check WAL file sizes

```bash
docker exec aegis-v2 ls -lh /app/events/
```

Expected: `current.ndjson` should be under 100MB per week of operation. If growing beyond 500MB, WAL rotation is overdue (see Section 4.6).

### 2.3 Verify S3 backup ran

Check the V1 backup script (runs from the host, not from AEGIS container):

```bash
ls -la /home/ubuntu/nzt48-signals/data/backups/ | tail -5
```

Or check S3 directly:

```bash
aws s3 ls s3://nzt48-backups/ --recursive | tail -5
```

Look for a file dated within the last 7 days.

### 2.4 Review weekly PnL summary

Check the Ouroboros output for the week:

```bash
docker exec aegis-v2 grep -i "weekly\|pnl\|return" /app/events/ouroboros.log | tail -10
```

Or query Redis for cached metrics:

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis GET aegis:metrics:weekly_pnl
```

### 2.5 Check for error patterns in logs

```bash
docker logs aegis-v2 --since 168h 2>&1 | grep -c "ERROR"
```

Expected: low single digits or zero. If double digits, investigate:

```bash
docker logs aegis-v2 --since 168h 2>&1 | grep "ERROR" | sort | uniq -c | sort -rn | head -10
```

This groups and counts unique error messages. Focus on the top entries.

---

## 3. MONTHLY CHECKLIST

### 3.1 Review Crucible validation status

Crucible is the 100-trade validation gate. After the initial validation, check that ongoing paper performance tracks within expected bounds:

- Win rate >= 40%
- Sharpe ratio >= 0.8
- Max drawdown <= 2.5%

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis HGETALL aegis:crucible:stats
```

### 3.2 Check ISA annual limit tracking

The UK ISA has a GBP 20,000 annual contribution limit. Verify tracking:

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis GET aegis:isa:contributions_ytd
```

This should stay at or below the initial deposit (GBP 10,000). Profits do not count toward the ISA limit; only new deposits do.

### 3.3 Review sector concentration

The ISA funds should not cluster into a single sector bet. Check position distribution:

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis HGETALL aegis:positions:current
```

Visually verify no single fund exceeds 40% of total exposure.

### 3.4 EC2 billing check

Log into the AWS Console or use the CLI:

```bash
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '30 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity MONTHLY \
  --metrics BlendedCost
```

Expected: approximately USD 65/month (c7i-flex.large + EBS). If significantly higher, check for unexpected resources.

### 3.5 Redis memory trend

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis INFO memory | grep -E "used_memory_human|used_memory_peak_human"
```

Compare current vs peak. If peak is approaching 256MB (the maxmemory limit), investigate which keys are growing:

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis --bigkeys
```

---

## 4. INCIDENT RESPONSE

### Broker & Engine Defects — ALL CRITICAL FIXES DEPLOYED (11 March 2026)

Eight P0 defects fixed and deployed. All verified via Ralph Wiggum Loop (405 tests, 0 warnings):

1. ~~**Delayed Data**~~ → ✅ FIXED: Engine requests `MarketDataType::Realtime` with graceful fallback
2. ~~**Buy-Only Broker**~~ → ✅ FIXED: `OrderSide { Buy, Sell }` enum in all broker adapters
3. ~~**Ticker Misattribution**~~ → ✅ FIXED: `process_broker_event()` extracts `ticker_id` from `BrokerEvent::Fill`
4. ~~**No Exit Management**~~ → ✅ FIXED: Chandelier exit triggers submit `OrderSide::Sell` to broker (Phase 2A)
5. ~~**Unbounded WAL**~~ → ✅ FIXED: `crossbeam::bounded(50_000)` with backpressure handling (Phase 2E)
6. ~~**Ouroboros Disconnected**~~ → ✅ FIXED: DynamicWeights applied to ExitEngine + RiskArbiter (Phase 2D)
7. ~~**Phantom Signals**~~ → ✅ FIXED: No Python = no trade
8. ~~**Regime Lost on Restart**~~ → ✅ FIXED: Regime persisted to WAL, restored on replay

### 4.1 Engine Crash

**Symptoms:**
- `docker ps` shows aegis-v2 as `Exited` or restarting
- No new log entries from the engine

**Diagnose:**

```bash
docker ps -a --filter name=aegis-v2
docker logs aegis-v2 --tail 100
```

Look for `PANIC`, `SIGSEGV`, `OOM`, or Python tracebacks near the end of the log.

**Check if positions are safe:**

AEGIS has a 60-second graceful shutdown window (stop_grace_period). On clean shutdown, it flattens positions. On a hard crash (OOM kill, segfault), positions may be orphaned.

```bash
# Check for open positions in Redis
docker exec aegis-redis redis-cli -a nzt48redis HGETALL aegis:positions:current
```

If positions show up and the engine is down, they are orphaned. The IB Gateway will maintain them at the broker level (stop-losses at the broker still apply), but no new management will occur until the engine restarts.

**Restart:**

```bash
cd /home/ubuntu/nzt48-aegis-v2
docker compose restart aegis-v2
```

Wait 10 seconds, then verify:

```bash
docker ps --filter name=aegis-v2
docker logs aegis-v2 --tail 20
```

**If restart loops (crashes immediately on start):**

```bash
# Check for corrupted WAL
docker exec aegis-v2 wc -l /app/events/current.ndjson

# Check for Redis connectivity
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis PING
```

If WAL is corrupted, rename and let the engine create a fresh one:

```bash
docker exec aegis-v2 mv /app/events/current.ndjson /app/events/current.ndjson.corrupt.$(date +%s)
docker compose restart aegis-v2
```

### 4.2 IB Gateway Disconnect

**Symptoms:**
- Engine logs show repeated `IBKR connection failed` or `timeout connecting to aegis-ib-gateway:4004`
- Regime may shift to YELLOW automatically

**Auto-recovery behavior:**

AEGIS implements exponential backoff with client ID rotation:
1. First retry after 1 second (client_id=101)
2. Then 2s, 4s, 8s, up to 60s cap
3. Rotates through client IDs 101-105 to avoid stale session locks
4. After 10 minutes of failure, falls back to yfinance data feed
5. Regime downgrades to YELLOW (50% position sizing)

**Check IB Gateway status:**

```bash
docker logs aegis-ib-gateway --tail 30
docker ps --filter name=aegis-ib-gateway
```

**Manual intervention (if auto-recovery fails after 10+ minutes):**

```bash
# Restart IB Gateway (V2's own container)
cd /home/ubuntu/nzt48-aegis-v2
docker compose restart aegis-ib-gateway
```

Wait 30 seconds for IBC to re-authenticate, then check:

```bash
docker logs aegis-ib-gateway --tail 10
docker exec aegis-v2 bash -c "echo quit | nc -w 2 aegis-ib-gateway 4004 && echo 'OK' || echo 'FAIL'"
```

**When to escalate:** If IB Gateway won't reconnect after restart and it is Monday morning, you likely need to complete 2FA re-authentication (Section 2.1).

### 4.3 HALT Regime Triggered

**Check current regime:**

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis GET aegis:regime
```

**Check what caused HALT:**

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis GET aegis:regime:reason
docker logs aegis-v2 --tail 200 | grep -i "halt\|regime\|red"
```

**Common HALT causes:**

| Cause | Description | Action |
|-------|-------------|--------|
| Consecutive losses | 3+ consecutive losing trades | Review trade log, check for data issues |
| Max drawdown breach | Cumulative DD exceeded 2.5% | Do NOT clear until you understand why |
| Reconciliation mismatch | IBKR reported positions differ from internal state | Compare Redis state vs IBKR account |
| Python bridge crash | 3 crashes within 60 seconds | Check Python logs, restart engine |
| Latency breach | IBKR latency exceeded 500ms for 30+ seconds | Check network, IB Gateway health |

**How to investigate before clearing:**

```bash
# View recent trades
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis LRANGE aegis:trades:recent 0 9

# View drawdown
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis GET aegis:metrics:current_drawdown

# View reconciliation state
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis HGETALL aegis:reconciliation
```

**How to manually clear HALT (only after investigation):**

```bash
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis SET aegis:regime GREEN
```

**When NOT to clear HALT:**
- You do not understand why it triggered
- Max drawdown breach and losses are real (not a data glitch)
- Reconciliation mismatch is unresolved
- Market conditions are extreme (flash crash, circuit breaker events)

In these cases, leave HALT active and investigate further. The system is protecting your capital.

### 4.4 Python Bridge Crash

The Rust engine spawns a Python subprocess for Ouroboros analytics and ML signal generation.

**Symptoms:**
- No new signals being generated (stale signal timestamps)
- Engine logs show: `python subprocess exited` or `bridge: restart attempt`

**Check:**

```bash
docker exec aegis-v2 ps aux | grep python
docker logs aegis-v2 --tail 50 | grep -i "python\|bridge\|subprocess"
```

**Recovery:**

The engine automatically restarts the Python bridge with exponential backoff (1s, 2s, 4s, 8s, 60s cap). If it fails 3 times in 60 seconds, regime shifts to RED (full halt).

To force a full recovery:

```bash
cd /home/ubuntu/nzt48-aegis-v2
docker compose restart aegis-v2
```

This restarts the entire engine, which respawns the Python bridge as a child process.

**Log locations for Python errors:**

```bash
docker exec aegis-v2 cat /app/events/ouroboros.log | tail -50
docker logs aegis-v2 --tail 100 | grep -i "traceback\|error\|exception"
```

### 4.5 Redis Down

**Symptoms:**
- Engine logs show `Redis connection refused` or `NOAUTH`
- State operations fail

**Impact:**
- Real-time state is lost (regime, position tracking, metrics)
- WAL still persists to disk (ndjson file), so the event log is safe
- Redis has AOF (append-only file) enabled with `appendfsync everysec`, so at most 1 second of state is lost on crash

**Check:**

```bash
docker ps --filter name=aegis-redis
docker logs aegis-redis --tail 30
```

**Recovery:**

```bash
cd /home/ubuntu/nzt48-aegis-v2
docker compose restart aegis-redis
```

Wait 10 seconds, then verify:

```bash
docker exec aegis-redis redis-cli -a nzt48redis PING
```

Expected: `PONG`

If Redis data is corrupted (won't start, AOF errors):

```bash
# Check AOF integrity
docker exec aegis-redis redis-check-aof --fix /data/appendonly.aof

# If unrecoverable, delete and restart (state loss)
docker compose down aegis-redis
docker volume rm nzt48-aegis-v2_aegis-redis-data
docker compose up -d aegis-redis
```

After data loss, restart the engine so it re-initializes state:

```bash
docker compose restart aegis-v2
```

### 4.6 Full Disk

**Check:**

```bash
df -h /
du -sh /var/lib/docker/volumes/* 2>/dev/null | sort -rh | head -10
```

**WAL rotation (manual):**

WAL files grow continuously. Archive old entries:

```bash
# Archive current WAL to timestamped file
docker exec aegis-v2 bash -c "cp /app/events/current.ndjson /app/events/archive_$(date +%Y%m%d_%H%M%S).ndjson"

# Truncate current WAL (engine will continue appending)
docker exec aegis-v2 bash -c "echo '' > /app/events/current.ndjson"
```

**Clean Docker build cache:**

```bash
docker system prune -f
docker builder prune -f
```

**Move archives to S3:**

```bash
# Copy WAL archives to S3
aws s3 cp /home/ubuntu/nzt48-aegis-v2/events/ s3://nzt48-backups/aegis-v2/wal/ --recursive --exclude "current.ndjson"

# Remove local archives after confirming S3 upload
rm /home/ubuntu/nzt48-aegis-v2/events/archive_*.ndjson
```

**Docker log rotation** is already configured (10MB max, 3 files) in docker-compose.yml, so Docker logs should not be the cause.

---

## 5. KEY COMMANDS REFERENCE

### SSH Access

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
```

### Docker Commands

```bash
# View running containers
docker ps

# View all containers (including stopped)
docker ps -a

# Engine logs (last 50 lines)
docker logs aegis-v2 --tail 50

# Engine logs (live follow)
docker logs aegis-v2 -f

# Engine logs (last hour)
docker logs aegis-v2 --since 1h

# Restart engine
cd /home/ubuntu/nzt48-aegis-v2 && docker compose restart aegis-v2

# Restart Redis
cd /home/ubuntu/nzt48-aegis-v2 && docker compose restart aegis-redis

# Full rebuild and restart
cd /home/ubuntu/nzt48-aegis-v2 && docker compose build && docker compose up -d

# Stop everything
cd /home/ubuntu/nzt48-aegis-v2 && docker compose down

# Exec into engine container
docker exec -it aegis-v2 bash

# Exec into Redis container
docker exec -it aegis-redis sh
```

### Engine Status Check

```bash
# Container health
docker inspect aegis-v2 --format='{{.State.Health.Status}}'

# Process check inside container
docker exec aegis-v2 pgrep -a aegis

# Supercronic (Ouroboros scheduler) running
docker exec aegis-v2 pgrep -a supercronic
```

### WAL Inspection

```bash
# WAL file size
docker exec aegis-v2 ls -lh /app/events/current.ndjson

# Last 5 WAL entries
docker exec aegis-v2 tail -5 /app/events/current.ndjson

# Count total WAL entries
docker exec aegis-v2 wc -l /app/events/current.ndjson

# Search WAL for specific ticker
docker exec aegis-v2 grep "QQQ3.L" /app/events/current.ndjson | tail -5
```

### Redis Health

```bash
# Ping
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis PING

# Memory usage
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis INFO memory

# All keys count
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis DBSIZE

# Current regime
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis GET aegis:regime

# All positions
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis HGETALL aegis:positions:current

# Recent trades
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis LRANGE aegis:trades:recent 0 9

# Find large keys
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis --bigkeys
```

### Ouroboros Manual Trigger

```bash
docker exec aegis-v2 python3 -m ouroboros.cli \
  --config-dir /app/config \
  --wal-path /app/events/current.ndjson \
  --day-count 1
```

### Emergency Position Flatten

**Use only in genuine emergencies.** This instructs IBKR to close all positions.

```bash
# Option 1: Graceful engine stop (60-second window to flatten)
cd /home/ubuntu/nzt48-aegis-v2 && docker compose stop aegis-v2

# Option 2: Set regime to HALT (engine stays up but stops trading)
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis SET aegis:regime HALT

# Option 3: Nuclear -- kill engine immediately (positions orphaned at broker)
docker kill aegis-v2
```

After any emergency stop, verify positions at the broker level via the IBKR web portal or TWS.

---

## 6. ESCALATION MATRIX

### Level 0: Monitor (no action needed)

- Single `WARN` in logs about transient network timeout
- yfinance fallback activated briefly (IBKR blip)
- Regime at YELLOW with auto-recovery in progress
- Redis memory usage rises temporarily then stabilizes

### Level 1: Restart (fix in under 5 minutes)

- Engine container exited and did not auto-restart
- Redis container unhealthy
- Supercronic process missing (Ouroboros won't run)
- Python bridge crashed but engine is still running

**Action:** `docker compose restart aegis-v2` (or the specific service)

### Level 2: Manual Intervention (fix in under 30 minutes)

- IB Gateway disconnect lasting more than 10 minutes
- 2FA re-authentication needed (Monday)
- WAL file exceeding 500MB (needs rotation)
- Disk usage above 85%
- Reconciliation mismatch between Redis state and IBKR

**Action:** Follow the relevant Section 4 procedure

### Level 3: Pull the Plug (immediate capital protection)

Trigger this level if ANY of the following are true:

- Drawdown exceeds 2.5% and is accelerating
- Reconciliation shows phantom positions (positions the engine does not know about)
- Engine is in a crash loop and has open positions
- You suspect unauthorized access to the EC2 instance
- Market is in extreme distress (flash crash, exchange halt) and the engine is behaving erratically

**Action:**

```bash
# 1. Halt all trading immediately
docker exec aegis-v2 redis-cli -a nzt48redis -h aegis-redis SET aegis:regime HALT

# 2. Stop the engine (triggers 60-second graceful flatten)
cd /home/ubuntu/nzt48-aegis-v2 && docker compose stop aegis-v2

# 3. Verify at broker level -- log into IBKR web portal
#    https://www.interactivebrokers.com/sso/Login
#    Check positions, cancel any open orders manually

# 4. Do NOT restart until you have fully investigated
```

### Where to Get Help

| Resource | Access |
|----------|--------|
| IBKR support | Client portal or +1-877-442-2757 |
| AWS EC2 issues | AWS Console or `aws support` CLI |
| AEGIS codebase | `/home/ubuntu/nzt48-aegis-v2/` on EC2 |
| Architecture docs | `docs/` directory in the AEGIS V2 repo |
| Terminal Directive | `AEGIS_V2_TERMINAL_DIRECTIVE.md` in repo root |
| Deployment checklist | `PHASE_5_LIVE_DEPLOYMENT_CHECKLIST.md` in repo root |

---

## 7. GLOSSARY

**ATR (Average True Range):** A measure of how much a security's price moves per day, accounting for gaps. Used to set stop-loss distances. Higher ATR means wider stops.

**Chandelier Exit:** A trailing stop-loss method invented by Chuck Le Beau. It hangs from the highest price reached (like a chandelier from a ceiling) at a distance of N times ATR. As price rises, the stop rises. It never moves down. AEGIS uses a 5-rung profit ladder variant.

**Crucible:** The 100-trade paper validation gate. The system must achieve win rate >= 40%, Sharpe >= 0.8, and max drawdown <= 2.5% before being approved for live capital. Named for the container in which metals are tested under extreme heat.

**CVaR (Conditional Value-at-Risk):** The average loss in the worst X% of scenarios. More conservative than VaR because it looks at the tail of the distribution, not just the boundary. If 5% CVaR is -3%, it means in the worst 5% of days, the average loss is 3%.

**Drawdown:** The peak-to-trough decline in portfolio value before a new high is reached. A 2.5% max drawdown means the portfolio never falls more than 2.5% from its highest point. AEGIS halts trading if this threshold is breached.

**GARCH (Generalized Autoregressive Conditional Heteroskedasticity):** A model that forecasts tomorrow's volatility based on today's volatility and today's return. Helps the system size positions correctly: larger when volatility is low, smaller when it spikes.

**Heat:** The total risk exposure of the portfolio expressed as a percentage of equity. If heat is 60%, it means 60% of the portfolio is at risk. The engine manages heat to stay within prescribed limits.

**Kelly (Kelly Criterion):** A formula for optimal bet sizing. Given a known win rate and payoff ratio, Kelly tells you what fraction of capital to risk to maximize long-term growth. AEGIS uses a fractional Kelly (typically half-Kelly) for safety.

**Ouroboros:** The nightly analytics job that runs at 23:50 ET (after US market close). It reviews the day's trades, recalibrates GARCH parameters, updates sector rotation scores, and generates the next day's signal configuration. Named for the serpent eating its own tail (self-renewal).

**Regime:** The system's current operating mode based on market conditions and internal health:
- **GREEN:** Normal operation, full position sizing
- **YELLOW:** Elevated caution, 50% position sizing (triggered by data feed issues, moderate drawdown, or Python bridge crash)
- **RED / HALT:** Trading suspended, no new positions (triggered by consecutive losses, max drawdown breach, reconciliation mismatch, or repeated crashes)

**Vanguard Tier / Apex Tier:** Internal classification of the trading system's maturity:
- **Vanguard:** The initial paper-validated system (Phases 0-5, current state)
- **Apex:** The fully evolved system after Phases Q1-Q4 (Rust FFI, DPDK, DRL, Neural Hawkes)

**WAL (Write-Ahead Log):** An append-only file (ndjson format) that records every event before it is acted upon. If the engine crashes, it can replay the WAL to reconstruct state. Located at `/app/events/current.ndjson` inside the container.

---

*HUMAN_OPERATOR_PLAYBOOK.md -- AEGIS V2 Deliverable 4*
*Updated: 12 March 2026 (v5.0 — all Phase 1+2A/2D/2E deployed, engine paper trading, 8/12 P0s fixed)*
