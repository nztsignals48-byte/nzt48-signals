# PHASE 2b: Integration & Deployment Guide

## Quick Start (For Deployment Team)

### Pre-Deployment Checklist

- [ ] Code reviewed and merged to `main` branch
- [ ] Unit tests passing locally: `pytest tests/test_ib_gateway_health_monitor.py -v`
- [ ] Docker-compose syntax valid: `docker-compose config > /dev/null`
- [ ] .env.production has `TWS_USERID`, `TWS_PASSWORD` set
- [ ] Telegram token configured (for alerts)

### EC2 Deployment (5 minutes)

```bash
# 1. SSH to EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# 2. Pull latest code
cd /home/ubuntu/nzt48-signals
git pull origin main
git checkout main  # Ensure on main branch

# 3. Rebuild Docker images (skip if only config changes)
docker-compose down
docker-compose build --no-cache

# 4. Start system
docker-compose up -d

# 5. Wait for startup (60s for IB Gateway health check)
sleep 60

# 6. Verify status
docker-compose ps  # All containers should show "healthy" or "Up"

# 7. Check logs
docker logs nzt48-ib-gateway --tail 20
docker logs nzt48 --tail 20 | grep "health\|monitor\|gateway"
```

### Verification Steps

**Check 1: IB Gateway Port Responsive**
```bash
bash -c 'echo > /dev/tcp/localhost/4002' && echo "✅ Port 4002 responsive" || echo "❌ Port 4002 not responding"
```

**Check 2: Health Monitor Started**
```bash
# Look for this line in nzt48 logs:
docker logs nzt48 --tail 100 | grep "IB Gateway health monitor"
# Should show: "IB Gateway health monitor initialized"
# Should show: "IB Gateway health monitor loop started"
```

**Check 3: Manual Health Check Test**
```bash
# From EC2, simulate what the monitor does
python3 -c "
import socket
try:
    s = socket.create_connection(('localhost', 4002), timeout=5)
    s.close()
    print('✅ Health check would pass')
except Exception as e:
    print(f'❌ Health check would fail: {e}')
"
```

### Post-Deployment Validation

**Day 1:** Observe logs for 24 hours
```bash
# Watch logs in real-time
docker logs nzt48 --follow | grep -E "health|gateway|monitor|restart"

# Look for:
# ✅ "IB Gateway health monitor initialized" — success
# ✅ "Health monitor loop started" — background task running
# ❌ Any restart loops? (healthy recovery would show once, then quiet)
```

**Day 2:** Check for 2FA timeout handling
```bash
# Monday morning 07:50 UK, before LSE opens
# Look for either:
# ✅ Silent (gateway was healthy) — GOOD
# ✅ "IB Gateway health monitor" logs with restart — GOOD (recovery worked)
# ❌ Repeated restart loops — BAD (escalate to support)
```

---

## What the Monitor Does (Background)

### Startup Sequence (First 5 minutes)

```
1. Engine starts → Creates health monitor
2. wait_for_ready(timeout=300s)
   - Socket test every 10s
   - Wait for port 4002 to respond
   - Max 5 minutes
3. If ready: Continue startup normally
4. If timeout: Log warning, continue anyway (non-blocking)
5. Start background monitor_loop()
   - Runs indefinitely in asyncio task
   - Checks every 30s
   - Silent if healthy
   - Logs + restarts if unhealthy
```

### Ongoing (Every 30 seconds)

```
Health Check:
  Port 4002 responds → failure_count = 0 → continue
  Port 4002 doesn't respond → failure_count += 1
                           → If count == 3: restart Docker
                           → If count >= 3: send Telegram alert
```

### Weekly (Monday 07:50 UK)

```
Market Aware Check:
  Is gateway healthy? Yes  → No alert, continue
  Is gateway healthy? No   → Send Telegram alert + restart
  Time to LSE open: 10 min → Perfect timing for alert
```

---

## Troubleshooting

### Symptom: Port 4002 not responding

**Check 1: Is container running?**
```bash
docker-compose ps | grep ib-gateway
# Should show: "nzt48-ib-gateway ... healthy" or "Up X minutes"
```

**Check 2: Container logs**
```bash
docker logs nzt48-ib-gateway --tail 50
# Look for errors about TWOFA or port binding
```

**Check 3: Manual restart**
```bash
docker-compose restart nzt48-ib-gateway
sleep 30
bash -c 'echo > /dev/tcp/localhost/4002' && echo "✅ Recovered"
```

### Symptom: Repeated restart loops

**This is BAD — investigate:**
```bash
# Check container restart count
docker inspect nzt48-ib-gateway | grep -A2 "RestartCount"

# If RestartCount > 10, something is seriously wrong
# Check credentials in .env.production:
cat .env.production | grep TWS_
# Make sure TWS_USERID and TWS_PASSWORD are correct

# Check IB Gateway logs for login errors:
docker logs nzt48-ib-gateway --tail 100 | grep -i "error\|fail\|invalid"
```

### Symptom: No alerts sent (Telegram not configured)

**This is OK — trading continues normally:**
```bash
# Health monitor is optional for Telegram
# Check if notifier is configured:
docker logs nzt48 --tail 50 | grep "TelegramNotifier"

# If missing: Trading still works, just no alerts (add later)
```

### Symptom: Health monitor not starting

**Check logs:**
```bash
docker logs nzt48 --tail 100 | grep -i "health\|monitor"
# Should see: "IB Gateway health monitor initialized"
# If missing: Check for import errors in main.py
```

---

## Monitoring Commands (Ops Runbook)

### Quick Status Check
```bash
# All-in-one health check
docker-compose ps && \
docker logs nzt48-ib-gateway --tail 5 && \
bash -c 'echo > /dev/tcp/localhost/4002' && echo "✅ All systems healthy"
```

### Watch Real-Time Monitoring
```bash
# Terminal 1: Watch container status
watch -n 5 'docker-compose ps'

# Terminal 2: Watch engine logs (health monitor events)
docker logs nzt48 --follow | grep -E "health|gateway|monitor|restart"

# Terminal 3: Watch IB Gateway logs
docker logs nzt48-ib-gateway --follow
```

### Extract Health Status (JSON for APIs)
```bash
# Get live status (requires engine to have API endpoint)
curl -s http://localhost:8000/api/health | jq '.ib_gateway'
```

---

## Rollback Procedure (If Needed)

If Phase 2b causes issues:

```bash
# 1. Revert to previous version
git revert HEAD

# 2. Rebuild without Phase 2b code
docker-compose down
docker-compose build --no-cache

# 3. Restart
docker-compose up -d

# 4. Verify
docker logs nzt48 --tail 20
```

Note: Phase 2b is designed to be non-intrusive. If disabled:
- Docker healthcheck still works (Layer 1 still active)
- Python monitor doesn't run (Layer 2/3 disabled)
- System falls back to manual recovery only

---

## Key Configuration Parameters

### Docker-Compose (docker-compose.yml)

```yaml
# How long to wait before marking unhealthy
healthcheck.retries: 3          # Mark unhealthy after 3 failed checks

# How often health check runs
healthcheck.interval: 30s        # Check every 30 seconds

# Container restart behavior
restart_policy.max_retries: 5    # Auto-restart up to 5 times

# IBC 2FA handling
TWOFA_TIMEOUT: 120               # Wait 2 min for 2FA approval
TWOFA_TIMEOUT_ACTION: restart    # Restart on timeout
```

### Python Code (core/ib_gateway_health_monitor.py)

```python
# In monitor.py, editable parameters:
monitor.max_failures_before_restart = 3   # Trigger restart after this many
check_interval_seconds = 30               # How often to check (in monitor_loop)
wait_for_ready(timeout_seconds=300)       # Startup wait timeout
```

All parameters have sensible defaults. Change only if needed.

---

## Expected Behavior (Checklist)

### Normal Day (Gateway Healthy)

- [ ] Startup completes without errors
- [ ] No Telegram alerts sent
- [ ] Logs are clean (no error messages)
- [ ] Trading proceeds normally
- [ ] Docker containers show "healthy"

### Exceptional Day (2FA Timeout)

- [ ] IBC detects 2FA timeout
- [ ] IBC restarts internally (TWOFA_TIMEOUT_ACTION=restart)
- [ ] Port 4002 goes down briefly (<1 minute)
- [ ] Health monitor detects failure after ~90 seconds (3 checks × 30s)
- [ ] docker-compose restart executed
- [ ] Container recovers within 60 seconds
- [ ] Telegram alert sent: "✅ IB Gateway restarted automatically"
- [ ] Trading resumes without user intervention

---

## Support Contacts

- **Health Monitor Author:** Claude Code
- **Docker Configuration:** Use `docker-compose` standard commands
- **IB Gateway Issues:** gnzsnz/ib-gateway on GitHub
- **Telegram Alerts:** Configure in .env.production (`TELEGRAM_TOKEN`)

---

## Additional Resources

- [Full Completion Report](./PHASE_2b_COMPLETION_REPORT.md)
- [Health Monitor Source](./core/ib_gateway_health_monitor.py)
- [Unit Tests](./tests/test_ib_gateway_health_monitor.py)
- [IB Gateway Docker Image](https://github.com/gnzsnz/ib-gateway)

---

**Last Updated:** 2026-03-15
**Phase:** 2b (Infrastructure Fixes)
**Status:** Ready for Production
