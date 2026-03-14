# GitHub CI/CD Setup Guide

## Overview

This repository uses GitHub Actions for:
1. **Automated testing** - Python syntax, imports, unit tests
2. **Automated deployment** - Build Docker image, push to EC2
3. **Automated monitoring** - 24/7 health checks
4. **IB Gateway auth** - Weekly 2FA reauth (Sunday 22:00 UTC)

## Prerequisites

1. GitHub repository (public or private)
2. EC2 instance with Docker installed
3. SSH key pair for EC2 access
4. Telegram bot token (optional, for alerts)

## Step-by-Step Setup

### 1. Create GitHub Secrets

Go to: `Settings → Secrets and Variables → Actions`

Create the following secrets:

#### `EC2_SSH_KEY`
- Value: Contents of your EC2 SSH private key
- How to get:
  ```bash
  cat ~/.ssh/nzt48-key.pem
  # Copy the entire output (including -----BEGIN RSA PRIVATE KEY-----)
  ```
- Paste in GitHub secret

#### `ENV_PRODUCTION`
- Value: Contents of `.env.production` file
- How to get:
  ```bash
  cat .env.production
  ```
- Paste in GitHub secret
- **Important:** This file contains sensitive data (Telegram tokens, API keys)
- It will NOT be committed to git (added to .gitignore)

#### `SLACK_WEBHOOK` (Optional, for notifications)
- Value: Your Slack incoming webhook URL
- How to get:
  1. Create Slack app: https://api.slack.com/apps
  2. Enable incoming webhooks
  3. Create webhook for your channel
  4. Copy the URL
- If you don't have Slack, the workflow will skip this step (continue-on-error)

### 2. Update EC2 Inbound Rules

If using AWS security groups:

```bash
# Allow GitHub Actions runner (runner.github.com IP range)
# SSH port 22
# Source: GitHub Actions IP range (changes, use 0.0.0.0/0 for testing)
```

### 3. Verify SSH Key Format

GitHub secret should look like:
```
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA1234567890...
...
-----END RSA PRIVATE KEY-----
```

### 4. Test the Setup

Once secrets are created:

1. Push any change to `main` branch
2. Go to `Actions` tab
3. Monitor the workflow run
4. Check logs if anything fails

## Workflows Explained

### `deploy.yml` - Main Deployment Pipeline

**Triggers:**
- Push to main branch
- Manual dispatch (workflow_dispatch)
- Nightly (23:00 UTC)

**Stages:**
1. **Test** - Python syntax, imports, unit tests
2. **Build** - Docker image build locally
3. **Deploy** - Upload tarball, extract, build on EC2, start containers
4. **Monitor** - Verify deployment success

**Duration:** ~20-30 minutes

### `ibgateway-auth.yml` - IB Gateway Weekly Reset

**Triggers:**
- Every Sunday at 22:00 UTC (before Monday trading)
- Manual dispatch for on-demand reauth

**What it does:**
1. Checks current IB Gateway status
2. Force restarts with fresh authentication
3. Waits for health check to pass
4. Verifies nzt48 is running
5. Sends Slack notification

**Duration:** ~5-10 minutes

### `monitor.yml` - 24/7 Health Monitoring

**Triggers:**
- Every 10 minutes (24/7)
- Manual dispatch

**What it monitors:**
1. Container status (nzt48, redis, ib-gateway)
2. System resources (disk, memory, CPU)
3. Recent error logs
4. Signal generation activity

**Alerts on:**
- Any container is down
- Disk space low (<1GB)
- Errors in logs
- No signals for 30+ minutes

**Duration:** ~2-3 minutes

## Accessing Logs

### GitHub Actions Logs
1. Go to repository → Actions tab
2. Click the workflow name
3. Click the job to see logs

### EC2 Logs
Via GitHub Actions:
```bash
# Manually SSH to EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# Check status
docker compose ps
docker logs nzt48 --tail 50
docker logs nzt48 -f  # Follow logs in real-time
```

## Troubleshooting

### SSH Key Not Working
- Verify key is in `.pem` format (not `.ppk`)
- Check key permissions: `chmod 600 ~/.ssh/nzt48-key.pem`
- Confirm key matches EC2 instance
- Test manually: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22`

### Deployment Hangs on Docker Build
- EC2 disk space likely full
- SSH to EC2 and run: `docker system prune -f`
- Check: `df -h`

### IB Gateway Won't Authenticate
- Requires 2FA during first auth
- Manual intervention needed:
  1. SSH to EC2
  2. SSH into IB Gateway container for GUI (requires X11 forwarding or manual auth)
  3. Or wait for automated Sunday reauth
  4. Then restart nzt48: `docker compose up -d nzt48`

### Slack Notifications Not Working
- Verify webhook URL is correct
- Check webhook is enabled in Slack app
- Workflows have `continue-on-error: true` so deployment won't fail

## Monitoring the System

### Real-time Monitoring
```bash
# SSH to EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# Watch container status
watch -n 5 'docker compose ps'

# Follow logs
docker logs nzt48 -f --tail 30
```

### Check Daily Statistics
```bash
# Inside nzt48 container
docker exec nzt48 python -c "
from delivery.database import get_connection
conn = get_connection()
trades = conn.execute('SELECT COUNT(*), SUM(pnl) FROM trades WHERE date=CURRENT_DATE').fetchone()
print(f'Today: {trades[0]} trades, P&L: {trades[1]}')
"
```

## Cost Considerations

GitHub Actions is **free for public repositories** and includes:
- 2,000 free minutes/month for private repos (more than enough for this system)
- Unlimited workflows
- Unlimited runs

At 3 deployments/day + monitoring every 10 minutes:
- Monitoring: ~144 runs/day × 3 min = 432 min/day
- Deployments: ~1 run/day × 25 min = 25 min/day
- **Total: ~457 min/day = ~13,500 min/month**

This exceeds free tier, so upgrade to GitHub Pro ($4/month) if using private repo.

## Next Steps

1. ✅ Create GitHub secrets (EC2_SSH_KEY, ENV_PRODUCTION, SLACK_WEBHOOK)
2. ✅ Push code to main branch
3. ✅ Go to Actions tab, monitor first deployment
4. ✅ Verify nzt48 is running: `docker compose ps`
5. ✅ Check logs for trading activity

System will then:
- ✅ Auto-deploy every night at 23:00 UTC
- ✅ Auto-reauth IB Gateway every Sunday at 22:00 UTC
- ✅ Monitor health every 10 minutes
- ✅ Send Slack alerts on failures
- ✅ Rollback to previous version on deploy failure

## Support

If workflows fail:
1. Check GitHub Actions logs for specific error
2. SSH to EC2 to check system state
3. Verify secrets are set correctly
4. Check EC2 security group allows SSH from your IP

---

**System is now automated. It will run 24/7 without manual intervention.** 🚀
