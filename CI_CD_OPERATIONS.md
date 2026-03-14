# NZT-48 CI/CD Operations Guide

## 🚀 Quick Start (After GitHub Secrets Setup)

1. **Push code to main branch** - Deployment starts automatically
2. **Check Actions tab** - Monitor deployment progress
3. **System goes live** - Nzt48 starts trading automatically

---

## 📋 Daily Operations

### Check System Status

```bash
# Via GitHub Actions (no SSH needed)
1. Go to repository → Actions tab
2. Click latest "Deploy NZT-48 AEGIS to EC2" workflow
3. View logs

# Via SSH to EC2 (if needed)
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
cd /home/ubuntu/nzt48-signals
docker compose ps
docker logs nzt48 --tail 50
```

### Monitor P&L

The system sends Telegram alerts automatically for:
- ✅ Trade entries
- ✅ Trade exits
- ✅ Daily P&L
- ✅ Errors/warnings

**Telegram bot token** is stored in GitHub secret `ENV_PRODUCTION`

### Check Nightly Deployment

Every night at 23:00 UTC:
1. System auto-deploys latest code
2. IB Gateway reauthed on Sundays (22:00 UTC)
3. Health check every 10 minutes

**All automatic** - no action needed.

---

## 🔧 Common Operations

### Deploy Immediately (Don't Wait for Nightly)

```bash
# Option 1: Trigger via GitHub UI
1. Go to Actions tab
2. Click "Deploy NZT-48 AEGIS to EC2"
3. Click "Run workflow" → "Run workflow"

# Option 2: Push to main
git add .
git commit -m "Deploy now"
git push origin main
# Deployment starts automatically
```

### Stop Trading (Emergency)

```bash
# Via SSH to EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
cd /home/ubuntu/nzt48-signals

# Stop all trading
docker compose stop nzt48

# Restart
docker compose start nzt48
```

### View Logs in Real-Time

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
cd /home/ubuntu/nzt48-signals
docker logs nzt48 -f --tail 100
```

### Check Today's P&L

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
docker exec nzt48 python -c "
from delivery.database import get_connection
conn = get_connection()
trades = conn.execute('SELECT COUNT(*), SUM(COALESCE(pnl,0)) FROM trades WHERE date=CURRENT_DATE').fetchone()
print(f'Trades: {trades[0]}, P&L: \${trades[1]:.2f}')
"
```

### View All Trades (This Month)

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
docker exec nzt48 python -c "
from delivery.database import get_connection
import datetime
conn = get_connection()
start = datetime.date.today().replace(day=1)
trades = conn.execute(
    'SELECT DATE(created), COUNT(*), SUM(pnl) FROM trades WHERE created >= ? GROUP BY DATE(created)',
    (start,)
).fetchall()
for row in trades:
    print(f'{row[0]}: {row[1]} trades, \${row[2]:.2f}')
"
```

---

## 📊 GitHub Actions Status

### View Workflow Runs

```
Repository → Actions → [Select workflow]
```

**Status indicators:**
- ✅ Green = Success (deployment completed)
- ❌ Red = Failed (check logs for error)
- ⏳ Yellow = Running (deployment in progress)

### Workflow Execution Times

| Workflow | Duration | Frequency |
|----------|----------|-----------|
| Deploy | 20-30 min | Push to main or 23:00 UTC nightly |
| IB Gateway Reauth | 5-10 min | Sundays 22:00 UTC |
| Monitoring | 2-3 min | Every 10 minutes (24/7) |

### View Specific Workflow Logs

1. Go to Actions tab
2. Click workflow name
3. Click job name
4. Expand "Run" steps to see output

Example: `Deploy → Build → Verify image`

---

## 🚨 Alerts & Monitoring

### Slack Notifications

System sends notifications for:
- ✅ Deployment success/failure
- ✅ IB Gateway reauth completion
- ✅ Health check failures
- ❌ Container crashes
- ❌ Disk space critical

**Configure in:** GitHub secret `SLACK_WEBHOOK`

### What to Do on Failures

| Failure | Cause | Action |
|---------|-------|--------|
| Deploy fails | Code error | Check Actions logs, fix code, push again |
| IB Gateway unhealthy | 2FA dialog | Manual intervention needed (see below) |
| nzt48 not running | Dependency failed | Wait for IB Gateway reauth, or restart manually |
| Disk full | Old logs/data | SSH to EC2, run `docker system prune -f` |

### Manual IB Gateway Authentication

If IB Gateway fails to authenticate automatically:

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
cd /home/ubuntu/nzt48-signals

# Check IB Gateway logs
docker logs nzt48-ib-gateway --tail 50 | grep -i "session\|auth\|login"

# If stuck, restart it
docker compose restart ib-gateway

# Monitor startup
docker logs nzt48-ib-gateway -f

# Once it's healthy, restart nzt48
docker compose up -d nzt48
docker logs nzt48 --tail 20
```

---

## 📈 Performance Metrics

### Expected Performance (Post-Q1 Validation)

| Metric | Conservative | Expected | Optimistic |
|--------|--------------|----------|-----------|
| Daily P&L | 0.30% | 0.35-0.50% | 0.50-0.75% |
| Win Rate | 35% | 40%+ | 50%+ |
| Sharpe Ratio | 2.0 | 3-8 | 8-15 |
| Max Drawdown | 10% | 5-8% | 3-5% |

### Monitor Key Metrics

```bash
# Check daily metrics
docker exec nzt48 python -c "
from delivery.database import get_connection
import datetime
conn = get_connection()
today = datetime.date.today()

# Win rate
wins = conn.execute('SELECT COUNT(*) FROM trades WHERE date=? AND pnl > 0', (today,)).fetchone()[0]
losses = conn.execute('SELECT COUNT(*) FROM trades WHERE date=? AND pnl < 0', (today,)).fetchone()[0]
total = wins + losses
wr = wins / total * 100 if total > 0 else 0

# P&L
pnl = conn.execute('SELECT SUM(pnl) FROM trades WHERE date=?', (today,)).fetchone()[0] or 0

# Heat used
heat = conn.execute('SELECT SUM(ABS(position_size)) FROM trades WHERE date=?', (today,)).fetchone()[0] or 0

print(f'Today ({today}):')
print(f'  Trades: {total} ({wins}W / {losses}L)')
print(f'  Win Rate: {wr:.1f}%')
print(f'  P&L: \${pnl:.2f}')
print(f'  Heat Used: {heat}% of £10k')
"
```

---

## 🔄 Rollback Procedure

If deployment breaks the system:

### Automatic Rollback
- Deployment has built-in rollback on failure
- Previous version is restored automatically
- System will revert to last known-good state

### Manual Rollback
```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
cd /home/ubuntu

# If old backup exists
if [ -d nzt48-signals-old ]; then
  rm -rf nzt48-signals
  mv nzt48-signals-old nzt48-signals
  cd nzt48-signals
  docker compose up -d
  echo "✅ Rolled back to previous version"
fi
```

---

## 🛠️ Troubleshooting

### Deployment Hangs

```bash
# Check what's running
docker ps

# Kill stuck process
docker kill nzt48-ib-gateway

# Restart
docker compose up -d
```

### High Memory Usage

```bash
# Check memory usage
docker stats --no-stream

# Clean up unused images/volumes
docker system prune -f --volumes

# Restart services
docker compose restart nzt48
```

### Telegram Alerts Not Arriving

```bash
# Check if nzt48 is running
docker compose ps nzt48

# Check Telegram configuration in logs
docker logs nzt48 | grep -i telegram

# Verify token (in GitHub secret)
docker exec nzt48 env | grep TELEGRAM
```

### IB Gateway Stuck in Health Check

```bash
# Kill and remove
docker compose kill ib-gateway
docker compose rm -f ib-gateway

# Restart (will retrigger authentication flow)
docker compose up -d ib-gateway

# Monitor
docker logs nzt48-ib-gateway -f
```

---

## 📞 When Things Break

### 1. Check Logs
```bash
# GitHub Actions logs first
Actions tab → Latest run → See output

# If that doesn't help, SSH to EC2
docker logs nzt48 --tail 100
docker logs nzt48-ib-gateway --tail 100
docker logs nzt48-redis --tail 50
```

### 2. Check System State
```bash
docker compose ps
df -h
free -h
docker stats --no-stream
```

### 3. Restart Services
```bash
# Stop all
docker compose down

# Start all
docker compose up -d

# Or restart individually
docker compose restart nzt48
```

### 4. If Still Broken
- Check GitHub secret configurations (SSH key, ENV_PRODUCTION)
- Verify EC2 security group allows SSH port 22
- Confirm EC2 instance is running
- Check AWS CloudWatch for instance issues

---

## 📚 Reference

### Key Files
- `.github/workflows/deploy.yml` - Main deployment
- `.github/workflows/ibgateway-auth.yml` - Weekly 2FA reset
- `.github/workflows/monitor.yml` - 24/7 health checks
- `.github/GITHUB_SETUP.md` - Setup instructions

### System Ports
- 8000: NZT-48 API server
- 4002: IB Gateway (paper trading)
- 6379: Redis (internal only)

### Important Files (EC2)
- `/home/ubuntu/nzt48-signals/` - Main system
- `/home/ubuntu/nzt48-signals/data/` - SQLite database
- `/home/ubuntu/nzt48-signals/.env.production` - Secrets (credentials)

### Documentation
- `DEPLOYMENT_READY_2026_03_14.txt` - Deployment checklist
- `Q1_Q10_COMPLETE_UNIFIED_SYSTEM.md` - System architecture
- `MERGED_MASTER_PLAN_v1.0.md` - Strategic plan

---

**System is fully automated. No daily manual intervention required.** ✅

Last Updated: 2026-03-14
