# Quick Start: Deploy Q1-Q10 Master Orchestrator

**Status:** Production Ready ✅  
**Last Updated:** 2026-03-14 16:58 UTC  
**Expected Deployment Time:** < 5 minutes

---

## 1-Minute Pre-Check

```bash
cd /Users/rr/nzt48-signals

# Verify all files exist
ls -la main.py core/master_orchestrator.py core/orchestrator_adapter.py
ls -la tests/test_integration_q1_q10.py scripts/dry_run_1hour.py

# Verify integration test passes
python3 tests/test_integration_q1_q10.py 2>&1 | grep "INTEGRATION TEST PASSED"
```

Expected output: `✅ INTEGRATION TEST PASSED`

---

## Deploy to EC2 (Choose One)

### Option A: Automated Deployment (Recommended)

```bash
bash scripts/deploy_to_ec2.sh
```

This script:
- Compiles all Python files
- Copies to EC2 (3.230.44.22)
- Rebuilds Docker images
- Restarts containers
- Verifies connectivity

### Option B: Manual Deployment

```bash
# SSH into EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# Navigate to working directory
cd /home/ubuntu/nzt48-signals

# Pull latest code
git pull

# Rebuild and restart
docker compose down
docker compose build
docker compose up -d nzt48 nzt48-redis ib-gateway

# Monitor startup
docker logs nzt48 --tail 50
```

---

## Post-Deployment Verification (5 min)

### 1. Check Service Health

```bash
# SSH to EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# Monitor logs
docker logs nzt48 -f --tail 20

# Watch for these log lines:
# ✅ Master Orchestrator initialized (Q1-Q10 complete)
# ✅ 08:00 UK pre-market check: IBKR connected
```

### 2. Verify First Signal (Wait 5-10 min)

Check Telegram for first signal:
- **P0 Alert:** Critical events (halt, error)
- **P1 Alert:** New signal generated
- **P2 Alert:** Position update (batch, max 5/day)
- **P3 Digest:** Daily summary (nightly)

### 3. Check Dashboard

```bash
# Access web dashboard
curl -s http://3.230.44.22:8000/api/status | jq '.'

# Expected output:
# {
#   "equity": 10000,
#   "pnl": 0,
#   "open_positions": 0,
#   "status": "operational"
# }
```

### 4. Verify Database Persistence

```bash
# Check database size
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
du -h /home/ubuntu/nzt48-signals/data/nzt48.db

# Should be > 800 KB (with trade history)
```

---

## Key Files Modified

| File | Change | Impact |
|------|--------|--------|
| `main.py` | Added Master Orchestrator imports (line 135-142) | ✅ Minimal |
| `main.py` | Added initialization (line 1165-1182) | ✅ Non-blocking |
| `core/orchestrator_adapter.py` | Fixed enum imports | ✅ No impact |
| `tests/test_integration_q1_q10.py` | New file (test harness) | ✅ No runtime impact |
| `scripts/dry_run_1hour.py` | New file (validation script) | ✅ No runtime impact |

All changes are backward-compatible with graceful fallback if Master Orchestrator unavailable.

---

## Rollback Plan (If Needed)

```bash
# On EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# Stop containers
docker compose down

# Restore database backup
cp data/nzt48.backup.2026-03-14.db data/nzt48.db

# Checkout previous main.py
git checkout HEAD~1 main.py

# Restart
docker compose up -d nzt48 nzt48-redis

# Verify
docker logs nzt48 --tail 20
```

---

## Q1 Phase Goals (63 Trading Days)

| Metric | Target | Pass |
|--------|--------|------|
| **Win Rate** | ≥ 40% | ☐ |
| **Profit Factor** | ≥ 1.5x | ☐ |
| **Max Drawdown** | ≤ -3% | ☐ |
| **Sharpe Ratio** | ≥ 2.0 | ☐ |

All 4 criteria must pass to advance to Q2 (KRONOS selective upgrades).

---

## Monitoring Checklist

Daily (First Week):
- [ ] Check Telegram alerts arriving
- [ ] Verify equity matches £10,000
- [ ] Confirm signals generating (≥ 1-2 per day in normal markets)
- [ ] Monitor PnL tracking

Weekly (Ongoing):
- [ ] Review win rate percentage
- [ ] Check for any halt conditions
- [ ] Verify database size growing (trades persisting)
- [ ] Assess signal quality vs market conditions

---

## Common Issues & Fixes

| Issue | Fix |
|-------|-----|
| Port 8080 in use | `lsof -i :8080 -t \| xargs kill` |
| IB Gateway auth expired | Manual 2FA Monday AM + IBC handles restart |
| Telegram not alerting | Check token/chat ID in .env on EC2 |
| No signals generating | Check market hours (LSE 09:00-15:15 UK) |
| Database size not growing | Verify VirtualTrader connected to DB |

---

## Success Criteria

You'll know deployment succeeded when:

1. ✅ Docker logs show "Master Orchestrator initialized (Q1-Q10 complete)"
2. ✅ First Telegram alert arrives within 10 minutes of market open
3. ✅ Equity in dashboard = £10,000
4. ✅ PnL tracking active (updates each trade)
5. ✅ Signals generate 1-4 per day (market-dependent)

---

## Support & Next Steps

**Need Help?**
- Check logs: `docker logs nzt48 --tail 100`
- Review: `/Users/rr/nzt48-signals/DEPLOYMENT_READY_SUMMARY.txt`
- Test: `python3 tests/test_integration_q1_q10.py`

**Next Phase (Q2):**
After 63 days of paper validation (if all 4 criteria pass):
- Integrate KRONOS selective upgrades
- Add confidence decay engine
- Add regime-aware gates

**Timeline to Live:**
- Q1: 63 trading days (paper validation)
- Q2: 7 trading days (transition gates)
- Go-Live: ~4 weeks (if Sharpe ≥ 2.0)

---

**Deployed by:** Claude Code  
**Deployment Date:** 2026-03-14  
**Status:** PRODUCTION READY ✅
