# QUICK DEPLOY - AEGIS V2 UTC MIGRATION
**For: Saturday deployment** | **Status:** Production Ready | **Safety:** 🔒 IS_LIVE=false

---

## FASTEST DEPLOYMENT (2 commands)

```bash
# SSH to EC2
ssh -i ~/.ssh/ec2-temp-key ubuntu@3.230.44.22

# Then run (takes 5-10 minutes):
cd ~/nzt48-aegis-v2 && git fetch && git checkout feat/tier-system-enhancements-full && git pull && \
docker compose down && docker compose build --no-cache && docker compose up -d aegis-v2 && \
docker compose logs -f aegis-v2
```

---

## STEP-BY-STEP MANUAL DEPLOYMENT

```bash
# 1. SSH to EC2
ssh -i ~/.ssh/ec2-temp-key ubuntu@3.230.44.22

# 2. Update code
cd ~/nzt48-aegis-v2
git fetch --all
git checkout feat/tier-system-enhancements-full
git pull

# 3. Clean old builds (optional but recommended)
find . -type d -name "target" -exec rm -rf {} + 2>/dev/null || true

# 4. Deploy
docker compose down
docker compose build --no-cache
docker compose up -d aegis-v2

# 5. Monitor startup
docker compose logs -f aegis-v2
```

---

## VERIFY DEPLOYMENT (Copy-Paste Commands)

```bash
# Is it running?
docker compose ps aegis-v2

# Is simulation mode on? (CRITICAL)
docker compose logs aegis-v2 | grep "IS_LIVE"
# Expected: IS_LIVE = false

# Bridge spawned?
docker compose logs aegis-v2 | grep "Python Brain: bridge started"

# IBKR connected?
docker compose logs aegis-v2 | grep "Market data farm"

# Ready for trading?
docker compose logs aegis-v2 | grep "Bridge: strategy execution active"
```

---

## EXPECTED LOGS (In Order)

1. `╔════════════════════════════════════════════════════════╗` - Startup banner
2. `IS_LIVE = false` - Safety lock ✅
3. `STARTUP: Initial trading mode = Mode...` - UTC migration ✅
4. `Python Brain: bridge started (pid=...)` - Bridge alive ✅
5. `Market data farm connection is OK: eufarm` - IBKR connected ✅
6. `Bridge: strategy execution active (22 strategies)` - Ready ✅

---

## WHAT TO DO IF BUILD FAILS

```bash
# Clean everything and retry
docker compose down --remove-orphans
docker system prune -a
cd ~/nzt48-aegis-v2
git pull
docker compose build --no-cache
docker compose up -d aegis-v2
```

---

## MONITORING TOMORROW (Market Open)

```bash
# Tail logs continuously
docker compose logs -f aegis-v2

# Watch for trades
docker exec aegis-v2 tail -f /app/events/current.ndjson

# Check system status
curl -s http://3.230.44.22:8000/api/status | jq .
```

---

## CRITICAL INFO

| Item | Value |
|------|-------|
| **Branch** | `feat/tier-system-enhancements-full` |
| **Safety** | `IS_LIVE=false` (compile-time constant) |
| **Mode** | SIMULATION ONLY (paper broker) |
| **Equity** | £10,000 (ISA protected) |
| **Market Open** | Saturday 06:00 UTC |
| **Telegram** | chat 8649112811 |

---

## IF DEPLOYMENT WORKS ✅

```
You should see:
- IS_LIVE = false
- Python Bridge: bridge started
- Market data farm connection is OK
- 22 strategies loaded

Then wait for market open tomorrow.
Trades will start flowing in automatically.
```

---

## IF DEPLOYMENT FAILS ❌

1. Check: `docker compose logs aegis-v2` (last 100 lines)
2. Space: `df -h /` (need 50GB+)
3. Memory: `free -h` (need 8GB+)
4. Network: `ping github.com` (need GitHub access)
5. Read: `DEPLOYMENT_INSTRUCTIONS.md` (full guide)

---

**Generated:** 2026-04-03
**Time to deploy:** 5-10 minutes
**Complexity:** Simple (automated script provided)
**Risk level:** 🔒 ZERO (IS_LIVE=false)
