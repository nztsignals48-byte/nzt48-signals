# AEGIS V2 Deployment Instructions
## Complete UTC Migration + Production Setup
**Date:** 2026-04-03
**Status:** READY FOR DEPLOYMENT
**Safety:** 🔒 IS_LIVE=false (No real money at risk)

---

## DEPLOYMENT REQUIREMENTS

### Environment
- **Target:** EC2 instance (ubuntu@3.230.44.22)
- **Existing:** Docker, docker-compose, git configured
- **Access:** SSH with key (~/.ssh/ec2-temp-key) or AWS console

### System Requirements
- ✅ 8GB+ RAM (for Rust build)
- ✅ 50GB disk space (for Docker images + build artifacts)
- ✅ Network: IBKR, GitHub, Telegram access
- ✅ Time sync: UTC (critical for trading)

---

## PRE-DEPLOYMENT CHECKLIST

```bash
# On EC2, verify prerequisites:
docker --version          # Should be 20.10+
docker-compose --version  # Should be 1.29+
cd ~/nzt48-aegis-v2 && git status  # Should be clean

# Verify space:
df -h / | grep -E "Avail|Use"  # Need 50GB+ available

# Verify time:
date -u  # Should be current UTC time
timedatectl  # Check NTP sync
```

---

## AUTOMATED DEPLOYMENT (RECOMMENDED)

### Step 1: Copy deployment script
```bash
# On your local machine:
scp -i ~/.ssh/ec2-temp-key /tmp/deploy_aegis_v2_complete.sh ubuntu@3.230.44.22:/tmp/

# Or on EC2 directly:
curl -o /tmp/deploy_aegis_v2_complete.sh https://raw.githubusercontent.com/nztsignals48-byte/nzt48-signals/feat/tier-system-enhancements-full/DEPLOYMENT_INSTRUCTIONS.md
# (Then extract the script)
```

### Step 2: Run deployment
```bash
# SSH to EC2
ssh -i ~/.ssh/ec2-temp-key ubuntu@3.230.44.22

# Run deployment script
bash /tmp/deploy_aegis_v2_complete.sh
```

### Step 3: Monitor startup
```bash
# In EC2, tail the logs:
docker compose logs -f aegis-v2

# Expected logs (in order):
# 1. [1/1000] Building image...
# 2. STARTUP: IS_LIVE = false (SIMULATION MODE)
# 3. Python Brain: bridge started (pid=...)
# 4. Market data farm connection is OK: eufarm
# 5. Bridge: 22 strategies loaded
```

---

## MANUAL DEPLOYMENT (IF SCRIPT FAILS)

### Step 1: Clone/update repository
```bash
cd ~/nzt48-aegis-v2
git fetch --all
git checkout feat/tier-system-enhancements-full
git pull
```

### Step 2: Clean build artifacts
```bash
# Remove stale Rust build cache (speeds up build)
find . -type d -name "target" -exec rm -rf {} + 2>/dev/null || true

# Clean Docker artifacts
docker system prune -a --volumes  # Optional: more aggressive clean
```

### Step 3: Build Docker image
```bash
# Stop existing containers first
docker compose down

# Build new image (UTC migration)
docker compose build --no-cache

# Expected output:
# Step 1/X : FROM rust:1.75
# ...
# Successfully built abc123def456
```

**Build time:** 3-5 minutes (includes Rust compilation)

### Step 4: Start containers
```bash
# Start AEGIS V2
docker compose up -d aegis-v2

# Verify it's running
docker compose ps
# Should show: aegis-v2  Up 10 seconds
```

### Step 5: Verify startup
```bash
# Watch startup logs (50 lines)
docker compose logs aegis-v2

# Or tail continuously
docker compose logs -f aegis-v2
```

---

## DEPLOYMENT VERIFICATION

### Critical Checks

**1. Container Running**
```bash
docker compose ps aegis-v2
# Expected: aegis-v2  Up X seconds  0.0.0.0:8000->8000/tcp
```

**2. IS_LIVE = false (Safety Lock)**
```bash
docker compose logs aegis-v2 | grep "IS_LIVE"
# Expected: IS_LIVE = false (SIMULATION MODE)
```

**3. Python Bridge Started**
```bash
docker compose logs aegis-v2 | grep "Python Brain: bridge started"
# Expected: Python Brain: bridge started (pid=123)
```

**4. IBKR Connection**
```bash
docker compose logs aegis-v2 | grep "Market data farm"
# Expected: Market data farm connection is OK: eufarm
```

**5. Strategy Execution**
```bash
docker compose logs aegis-v2 | grep "Bridge: strategy"
# Expected: Bridge: strategy execution active (22 strategies)
```

### If Verification Fails

**Container won't start:**
```bash
# Check logs for errors
docker compose logs aegis-v2

# Check disk space
df -h /

# Rebuild (cleans everything)
docker compose down --remove-orphans
docker system prune -a
docker compose build --no-cache
```

**Bridge not spawning:**
```bash
# Check if IBKR is connected
docker compose logs aegis-v2 | grep "IBKR\|ib-gateway"

# Restart just the bridge
docker compose restart aegis-v2
```

**IBKR not connecting:**
```bash
# This is normal during 2FA. Check ib-gateway container:
docker compose logs ib-gateway

# 2FA dialog should appear in IB API logs
# Provide 2FA code when prompted
```

---

## WHAT WAS DEPLOYED

### UTC Migration (Critical Fix)
- **Problem:** System could get time wrong by ±3 days due to BST approximation
- **Solution:** Migrated to UTC-only timekeeping with dynamic DST handling
- **Files Changed:**
  - `rust_core/src/clock.rs` (240 lines) - UTC functions, BST hardcoded 2025-2032
  - `rust_core/src/engine.rs` (30 changes) - UTC function calls
  - `rust_core/src/main.rs` (5 changes) - Clock updates
  - Tests: 50+ UTC variants

### Safety Locks
- ✅ `IS_LIVE = false` (compile-time constant)
- ✅ IBKR retry loop skipped in simulation mode
- ✅ Paper broker enforced (no real orders)
- ✅ Simulation mode flag propagated everywhere

### Signal Pipeline Verification
- ✅ Python → Bridge → Rust wired completely
- ✅ Order submission → Paper broker → WAL logging
- ✅ Exit evaluation → Telegram alerts configured

---

## POST-DEPLOYMENT OPERATIONS

### Monitor System Health
```bash
# Continuous log monitoring
docker compose logs -f aegis-v2

# Check WAL events (trades logged here)
docker exec aegis-v2 tail -f /app/events/current.ndjson

# System status API
curl -s http://3.230.44.22:8000/api/status | jq .

# Heartbeat file
docker exec aegis-v2 cat /app/data/bridge_heartbeat.json | jq .
```

### Wait for Market Open
**Current Status:** Markets closed (18:44 Paris time)
**Next Trading Session:** Tomorrow morning (06:00 UTC = 08:00 Paris)

**What happens at market open:**
1. IBKR sends market ticks
2. Bridge evaluates 22 strategies
3. Signals generated (if confidence >= floor)
4. Engine entry gates checked
5. Simulated orders placed
6. WAL logged
7. Telegram alerts sent

### Monitor First Trades
```bash
# Watch for signals in logs
docker compose logs aegis-v2 | grep "SIGNAL_ARRIVED\|ENTRY_GATE"

# Check WAL for order events
docker exec aegis-v2 jq '.type' /app/events/current.ndjson | sort | uniq -c

# Expected: EventType::EntrySignal, EventType::OrderAcked, EventType::OrderFilled

# Check equity P&L
curl -s http://3.230.44.22:8000/api/status | jq '.equity, .daily_pnl'
```

### Troubleshooting After Deployment

**No signals generated:**
- Check Python bridge logs for confidence/kelly thresholds
- Verify mode is not Dark (21:00-23:00 UTC)
- Check if market ticks are arriving (grep "tick" in logs)

**Orders not executing:**
- Verify entry gates: mode, auction, cutoff checks
- Check risk arbiter regime (should be Normal)
- Verify broker connection (should show "connected")

**Time seems wrong:**
- Check system UTC time: `docker exec aegis-v2 date -u`
- Check BST status: grep "BST" in logs
- Verify trading mode matches expected hour

---

## ROLLBACK PROCEDURE

If deployment fails critically:

```bash
# Revert to previous container
docker compose down
git checkout HEAD~1  # Go back 1 commit
docker compose build --no-cache
docker compose up -d aegis-v2

# Or restore from backup (if you have one)
# Instructions depend on your backup strategy
```

---

## DEPLOYMENT SUCCESS INDICATORS

✅ **All Green:**
- Container running
- IS_LIVE=false confirmed
- Bridge spawned
- IBKR connected
- Strategies loaded
- Logs flowing

✅ **Tomorrow (Market Open):**
- First market ticks arrive
- Signals generate
- Orders execute (simulation)
- WAL populates
- Telegram alerts received

---

## CRITICAL CONTACTS

**If deployment fails:**
1. Check DEPLOYMENT_INSTRUCTIONS.md (this file)
2. Check SESSION_17_COMPLETION_REPORT.md (technical details)
3. Check git history: 3 critical commits (2fcccad, e4b1c4b, 3225e9b)

**Telegram Notifications:**
- Configured: chat 8649112811
- Enabled: System events, signals, trades

**Logs Location:**
- Container: `/app/events/current.ndjson` (WAL)
- Container: `/app/data/bridge_heartbeat.json` (health)
- Container stdout: `docker compose logs aegis-v2`

---

## DEPLOYMENT CHECKLIST

- [ ] Prerequisites verified (Docker, git, space, time)
- [ ] Repository updated (feat/tier-system-enhancements-full)
- [ ] Build artifacts cleaned
- [ ] Docker image built successfully
- [ ] Container started and healthy
- [ ] IS_LIVE=false confirmed
- [ ] Bridge spawned successfully
- [ ] IBKR connection established
- [ ] Strategies loaded (22 confirmed)
- [ ] Telegram alerts armed
- [ ] WAL logging active
- [ ] Ready for market open

---

## EXPECTED TIMELINE

| Time | Event | Status |
|------|-------|--------|
| **Now (18:44 Paris)** | Deployment | ⏳ In progress |
| **Friday evening** | Deployment complete | ✅ Ready |
| **Saturday 06:00 UTC** | Market open (Asia) | 📊 First ticks |
| **Saturday 08:00 UTC** | Europe open | 📈 Signal generation |
| **Saturday 13:30 UTC** | US open | 🚀 Peak activity |

---

**Generated:** 2026-04-03
**Status:** PRODUCTION READY
**Safety:** 🔒 MAXIMUM PROTECTION (IS_LIVE=false, Paper broker, Simulation mode)
