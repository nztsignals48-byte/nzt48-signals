# NZT-48 Infrastructure Runbook
**Phase Q3: Production-Grade Observability & Reliability**

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Architecture Overview](#architecture-overview)
3. [Deployment Options](#deployment-options)
4. [Monitoring & Observability](#monitoring--observability)
5. [Backup & Disaster Recovery](#backup--disaster-recovery)
6. [CI/CD Pipeline](#cicd-pipeline)
7. [Load Testing & Capacity Planning](#load-testing--capacity-planning)
8. [Troubleshooting](#troubleshooting)
9. [Runbook Operations](#runbook-operations)

---

## Quick Start

### Start with Monitoring (Docker Compose)

```bash
# Start core services
docker compose up -d

# Start monitoring stack (Prometheus + Grafana)
docker compose -f docker-compose.yml -f deployment/docker-compose.monitoring.yml up -d

# Access dashboards
# Grafana: http://localhost:3000 (admin/nzt48admin)
# Prometheus: http://localhost:9090
```

### Deploy to Kubernetes

```bash
# Create namespace
kubectl apply -f deployment/k8s/deployment.yaml

# Create secrets (REQUIRED - see deployment/k8s/secrets-template.yaml)
kubectl create secret generic nzt48-secrets \
  --namespace=nzt48 \
  --from-literal=redis-password='YOUR_PASSWORD' \
  --from-literal=api-key='YOUR_API_KEY'

# Deploy all resources
kubectl apply -f deployment/k8s/

# Check status
kubectl get pods -n nzt48
kubectl logs -f deployment/nzt48-engine -n nzt48
```

---

## Architecture Overview

### Components

| Component | Purpose | Replicas | Port |
|-----------|---------|----------|------|
| **nzt48-engine** | Trading engine + API | 2-3 (K8s) / 1 (Docker) | 8000 |
| **nzt48-redis** | State persistence (Chandelier, circuit breaker) | 1 (StatefulSet) | 6379 |
| **nzt48-ib-gateway** | IB Gateway connection | 1 (single IBKR session) | 4002/4004 |
| **prometheus** | Metrics collection | 1 | 9090 |
| **grafana** | Metrics visualization | 1 | 3000 |
| **alertmanager** | Alert routing (Telegram/email) | 1 | 9093 |

### Data Flow

```
IB Gateway (4002) → Trading Engine → Redis (state)
                                  ↓
                              SQLite (trades/signals)
                                  ↓
                          Prometheus (metrics) → Grafana (dashboards)
                                  ↓
                          Alertmanager → Telegram/Email
```

---

## Deployment Options

### Option 1: Docker Compose (Current - EC2)

**Pros:**
- Simple deployment
- Low overhead
- Easy debugging

**Cons:**
- Single point of failure
- Manual scaling
- No automatic failover

**Best for:** Paper trading, development, single-instance production

```bash
# Deploy to EC2
ssh ubuntu@3.230.44.22
cd /home/ubuntu/nzt48-signals
docker compose up -d
```

### Option 2: Kubernetes (High Availability)

**Pros:**
- Automatic failover
- Horizontal scaling
- Zero-downtime deployments
- Self-healing

**Cons:**
- Complex setup
- Higher cost (EKS/GKE)
- Requires K8s expertise

**Best for:** Live trading, high-value accounts, production at scale

**Setup:**

1. **Create EKS cluster** (or use kind locally):
   ```bash
   eksctl create cluster --name nzt48-prod --region us-east-1 --nodes 3
   ```

2. **Install NGINX Ingress Controller:**
   ```bash
   kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/aws/deploy.yaml
   ```

3. **Install cert-manager (for TLS):**
   ```bash
   kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.0/cert-manager.yaml
   ```

4. **Deploy NZT-48:**
   ```bash
   kubectl apply -f deployment/k8s/
   ```

5. **Verify deployment:**
   ```bash
   kubectl get all -n nzt48
   kubectl logs -f deployment/nzt48-engine -n nzt48
   ```

---

## Monitoring & Observability

### Prometheus Metrics

NZT-48 exposes Prometheus metrics at `http://localhost:8000/metrics`:

**Trading Metrics:**
- `nzt48_daily_pnl_total_pounds` - Daily P&L (realized + unrealized)
- `nzt48_win_rate_percentage` - Win rate (%)
- `nzt48_position_count` - Open positions
- `nzt48_sharpe_ratio` - Rolling Sharpe ratio
- `nzt48_daily_heat_percentage` - Daily drawdown (%)

**System Metrics:**
- `process_cpu_seconds_total` - CPU usage
- `process_resident_memory_bytes` - Memory usage
- `http_request_duration_seconds` - API latency

**Infrastructure Metrics:**
- `nzt48_ib_gateway_connected` - IB Gateway status
- `nzt48_market_data_lag_seconds` - Data feed lag
- `redis_commands_processed_total` - Redis ops/sec

### Grafana Dashboards

4 pre-built dashboards in `deployment/grafana/dashboards/`:

1. **Trading Metrics** (`trading_metrics.json`)
   - Daily P&L chart
   - Win rate gauge
   - Heat level gauge
   - Position count
   - Chandelier exit distribution

2. **System Health** (`system_health.json`)
   - Service uptime
   - CPU/memory usage
   - API response time
   - Market data lag
   - Error rate

3. **Risk Metrics** (`risk_metrics.json`)
   - Circuit breaker status
   - Kill switch status
   - Total leverage
   - Margin usage
   - Drawdown tracking
   - VaR (99% 1-day)

4. **Infrastructure** (`infrastructure.json`)
   - Docker container status
   - IB Gateway health
   - Data feed status
   - Network traffic
   - Disk I/O
   - Last backup timestamp

**Import Dashboards:**

```bash
# Manual import via Grafana UI
1. Open Grafana: http://localhost:3000
2. Login: admin / nzt48admin
3. Click + → Import
4. Upload JSON file from deployment/grafana/dashboards/
```

### Alerting

Alerts configured in `deployment/prometheus/alerts/trading_alerts.yml`:

**Critical (P0):**
- Daily P&L < -£200
- Circuit breaker activated
- Heat level > 4% (RED)
- IB Gateway down
- Redis down
- Trading engine down

**Warning (P1):**
- Win rate < 40%
- Position count > 5
- Leverage > 3.0x
- High CPU usage (>80%)
- Market data lag > 30s

**Info (P2):**
- Entry quality < 60%
- Sharpe ratio < 2.0

**Alert Routing:**

Alerts route to Telegram + Email via Alertmanager:

```bash
# Test alert
curl -X POST http://localhost:9093/api/v1/alerts -d '[{
  "labels": {"alertname": "TestAlert", "severity": "critical"},
  "annotations": {"summary": "Test alert from runbook"}
}]'
```

---

## Backup & Disaster Recovery

### Automated Backups

**Script:** `scripts/backup_and_recovery.sh`

**Schedule:** Daily at 03:00 UTC (cron)

**Components backed up:**
1. SQLite database (nzt48.db) → S3
2. Redis AOF + RDB → S3
3. Configuration files (settings.yaml) → S3
4. Credentials (encrypted with KMS) → S3

**Retention:** 30 days

**Setup:**

```bash
# 1. Create S3 bucket
aws s3 mb s3://nzt48-backups --region us-east-1

# 2. Test backup manually
bash scripts/backup_and_recovery.sh

# 3. Schedule daily backup (crontab)
crontab -e
# Add: 0 3 * * * /home/ubuntu/nzt48-signals/scripts/backup_and_recovery.sh
```

### Disaster Recovery

**RTO (Recovery Time Objective):** 10 minutes
**RPO (Recovery Point Objective):** 24 hours (daily backups)

**Recovery Procedure:**

```bash
# 1. Download latest backup
aws s3 cp s3://nzt48-backups/production/LATEST - | xargs -I {} \
  aws s3 sync s3://nzt48-backups/production/backups/{}/ /tmp/restore/

# 2. Stop services
docker compose down

# 3. Restore database
gunzip -c /tmp/restore/nzt48_*.db.gz > data/nzt48.db

# 4. Restore Redis
gunzip -c /tmp/restore/redis_aof_*.aof.gz > /tmp/appendonly.aof
docker cp /tmp/appendonly.aof nzt48-redis:/data/appendonly.aof

# 5. Restore config
tar xzf /tmp/restore/config_*.tar.gz -C .

# 6. Restart services
docker compose up -d

# 7. Verify
curl http://localhost:8000/api/health
docker logs nzt48 --tail 50
```

**Monthly DR Drill:**

```bash
# Test recovery without affecting production
bash scripts/test_recovery.sh

# Generates report in S3: s3://nzt48-backups/production/recovery-tests/
```

---

## CI/CD Pipeline

### GitHub Actions Workflow

**File:** `.github/workflows/deploy.yml`

**Stages:**

1. **Test & Lint** (on PR)
   - Python syntax check
   - Unit tests (pytest)
   - Code formatting (black)
   - Type checking (mypy)
   - Security scan (bandit)

2. **Build Docker Image** (on merge to main)
   - Build with git SHA
   - Push to ECR (AWS)
   - Vulnerability scan (Trivy)

3. **Deploy to Staging** (automatic)
   - Deploy to staging EC2
   - Run smoke tests
   - Wait for health check

4. **Deploy to Production** (manual approval required)
   - Create pre-deployment backup
   - Rolling update (zero-downtime)
   - Post-deployment smoke tests
   - Monitor for 5 minutes
   - Rollback on failure

**Required GitHub Secrets:**

```bash
AWS_ACCESS_KEY_ID          # AWS credentials for ECR
AWS_SECRET_ACCESS_KEY
EC2_SSH_KEY                # SSH key for EC2 deployment
ENV_PRODUCTION             # .env.production contents
TELEGRAM_TOKEN             # Telegram bot token
TELEGRAM_CHAT_ID           # Telegram chat ID
```

**Manual Deployment:**

```bash
# Trigger deployment via GitHub UI
# Actions → Deploy NZT-48 AEGIS to EC2 → Run workflow → main

# Or push to main
git push origin main
```

**Rollback:**

```bash
# SSH to EC2
ssh ubuntu@3.230.44.22

# Rollback to previous version
cd /home/ubuntu
rm -rf nzt48-signals
mv nzt48-signals-old nzt48-signals
cd nzt48-signals
docker compose up -d
```

---

## Load Testing & Capacity Planning

### Load Test Suite

**Script:** `scripts/load_test.py`

**Tests:**

1. **Market Data Injection** - Simulate concurrent ticker processing
2. **API Throughput** - Measure API response time under load
3. **Database Writes** - SQLite write throughput
4. **Redis Operations** - Redis ops/sec capacity
5. **Max Ticker Capacity** - Find bottleneck (binary search)

**Run Load Tests:**

```bash
# Quick smoke test (15 seconds)
python scripts/load_test.py --scenario=quick

# Full suite (5 minutes)
python scripts/load_test.py --scenario=full --report

# Custom test
python scripts/load_test.py --duration=60 --max-tickers=200
```

**Capacity Report:**

Generated in `scripts/capacity_report.md`:

- Max tickers before 50% latency degradation
- API requests/second capacity
- Database write throughput
- Resource usage trends

**Recommendations:**

- **Current capacity**: ~100-150 tickers on c7i-flex.large (4GB RAM, 2 vCPUs)
- **Horizontal scaling**: Shard tickers across multiple engines for >150 tickers
- **Vertical scaling**: Upgrade to c7i-flex.xlarge for >200 tickers

---

## Troubleshooting

### Common Issues

#### 1. IB Gateway Disconnected

**Symptoms:**
- `nzt48_ib_gateway_connected = 0`
- Logs: "Connection refused" or "2FA timeout"

**Diagnosis:**

```bash
docker logs nzt48-ib-gateway --tail 50
docker exec nzt48-ib-gateway ps aux | grep TWS
```

**Fix:**

```bash
# Check 2FA status
# If Monday morning, approve 2FA on IBKR mobile app

# Restart IB Gateway
docker compose restart ib-gateway

# Wait 60s for healthcheck
docker inspect nzt48-ib-gateway --format='{{.State.Health.Status}}'
```

#### 2. Circuit Breaker Tripped

**Symptoms:**
- `nzt48_circuit_breaker_active = 1`
- Trading halted
- Logs: "Circuit breaker triggered"

**Diagnosis:**

```bash
# Check daily drawdown
curl http://localhost:8000/api/performance | jq '.daily_heat_pct'

# Check recent trades
curl http://localhost:8000/api/trades?limit=10 | jq '.[] | {ticker, pnl, timestamp}'
```

**Fix:**

```bash
# Review trades, identify root cause
# If adverse selection, pause strategy
curl -X POST http://localhost:8000/api/pause \
  -H "X-API-Key: $NZT48_API_KEY" \
  -d '{"strategy": "s15_daily_target"}'

# Manual reset (only if justified)
docker exec nzt48-redis redis-cli -a nzt48redis DEL circuit_breaker:active
```

#### 3. High Memory Usage

**Symptoms:**
- `process_resident_memory_bytes > 1.5GB`
- Container OOM kills

**Diagnosis:**

```bash
# Check memory usage
docker stats nzt48 --no-stream

# Check Python objects
docker exec nzt48 python -c "import gc; gc.collect(); print(len(gc.get_objects()))"
```

**Fix:**

```bash
# Restart engine (clears memory)
docker compose restart nzt48

# If persistent, check for memory leaks
docker logs nzt48 | grep -i "memory\|leak\|oom"

# Increase memory limit in docker-compose.yml
# mem_limit: 2048m
```

#### 4. SQLite Database Locked

**Symptoms:**
- `OperationalError: database is locked`
- API timeouts on `/api/trades`

**Diagnosis:**

```bash
# Check database size
ls -lh data/nzt48.db

# Check WAL mode
sqlite3 data/nzt48.db "PRAGMA journal_mode;"
```

**Fix:**

```bash
# Ensure WAL mode is enabled
sqlite3 data/nzt48.db "PRAGMA journal_mode=WAL;"

# Checkpoint WAL
sqlite3 data/nzt48.db "PRAGMA wal_checkpoint(TRUNCATE);"

# If corrupted, restore from backup
bash scripts/test_recovery.sh
```

---

## Runbook Operations

### Daily Operations

**Morning (08:00 UK)**
1. Check Grafana dashboards (http://localhost:3000)
2. Review overnight P&L
3. Check for alerts in Telegram
4. Verify IB Gateway connection
5. Check system health (no errors in logs)

**Evening (16:30 UK - market close)**
1. Review daily performance
2. Check Chandelier exit distribution
3. Verify backups completed (S3)
4. Check disk space on EC2

### Weekly Operations

**Monday (07:50 UK)**
1. Approve IBKR 2FA on mobile app
2. Verify IB Gateway reconnects

**Friday (17:00 UK)**
1. Review weekly performance metrics
2. Check for software updates
3. Run capacity tests (if needed)

### Monthly Operations

**First of Month**
1. Run disaster recovery drill (`scripts/test_recovery.sh`)
2. Review capacity report
3. Check backup retention (30 days)
4. Update dependencies (security patches)
5. Review alert thresholds

### Quarterly Operations

**Every 3 Months**
1. Full load testing suite
2. Review and update SLOs
3. Infrastructure cost optimization
4. Security audit (credentials rotation)

---

## Performance SLOs

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| **Uptime** | 99.5% | TBD | ⏳ |
| **API Latency (p95)** | <100ms | TBD | ⏳ |
| **Data Feed Lag** | <10s | TBD | ⏳ |
| **Daily Backup Success** | 100% | TBD | ⏳ |
| **Alert Response Time** | <5min | TBD | ⏳ |

---

## Security Checklist

- [ ] All secrets stored in environment variables (not in code)
- [ ] API key authentication enabled (`NZT48_API_KEY`)
- [ ] Redis password protected (`requirepass nzt48redis`)
- [ ] IB Gateway credentials encrypted in `.env.production`
- [ ] S3 backups encrypted with KMS
- [ ] HTTPS enabled on Ingress (cert-manager)
- [ ] Network policies restrict inter-pod traffic
- [ ] Docker images scanned for vulnerabilities (Trivy)
- [ ] No sensitive data in logs
- [ ] Regular credential rotation (quarterly)

---

## Support & Escalation

**Incident Severity:**

- **P0 (Critical)**: Trading halted, data loss, security breach → Fix immediately
- **P1 (High)**: Degraded performance, alerts firing → Fix within 4 hours
- **P2 (Medium)**: Non-critical bugs, warnings → Fix within 24 hours
- **P3 (Low)**: Enhancements, optimization → Schedule for next sprint

**On-Call Runbook:**

1. Receive alert (Telegram/email)
2. Acknowledge within 5 minutes
3. Diagnose using Grafana dashboards + logs
4. Follow troubleshooting steps above
5. Document incident in `reports/incidents/`
6. Post-mortem within 48 hours

---

## Useful Commands

```bash
# === Docker Compose ===
docker compose ps                           # Service status
docker compose logs -f nzt48                # Follow logs
docker compose restart nzt48                # Restart engine
docker compose down && docker compose up -d # Full restart

# === Kubernetes ===
kubectl get pods -n nzt48                   # Pod status
kubectl logs -f deployment/nzt48-engine -n nzt48  # Follow logs
kubectl exec -it nzt48-engine-xxx -n nzt48 -- bash  # Shell into pod
kubectl describe pod nzt48-engine-xxx -n nzt48  # Pod details

# === Metrics ===
curl http://localhost:8000/metrics | grep nzt48  # Prometheus metrics
curl http://localhost:8000/api/health             # Health check
curl http://localhost:8000/api/performance        # Performance stats

# === Database ===
sqlite3 data/nzt48.db "SELECT COUNT(*) FROM trades;"  # Trade count
sqlite3 data/nzt48.db "PRAGMA integrity_check;"       # Check integrity

# === Redis ===
docker exec nzt48-redis redis-cli -a nzt48redis KEYS '*'  # List keys
docker exec nzt48-redis redis-cli -a nzt48redis INFO stats  # Stats

# === Backups ===
bash scripts/backup_and_recovery.sh         # Create backup
bash scripts/test_recovery.sh               # Test recovery
aws s3 ls s3://nzt48-backups/production/    # List backups

# === Load Testing ===
python scripts/load_test.py --scenario=quick  # Quick test
python scripts/load_test.py --scenario=full --report  # Full suite
```

---

## References

- **AEGIS Master Plan**: `AEGIS_MASTER_PLAN_v15_MERGED.md`
- **Phase Q1 Plan**: `PHASE_Q1_IMPLEMENTATION_PLAN.md`
- **Deployment Guide**: `DEPLOYMENT_READY_2026_03_14.txt`
- **Prometheus Alerts**: `deployment/prometheus/alerts/trading_alerts.yml`
- **Grafana Dashboards**: `deployment/grafana/dashboards/`
- **Kubernetes Manifests**: `deployment/k8s/`

---

**Last Updated**: 2026-03-15
**Version**: 1.0.0
**Owner**: NZT-48 Infrastructure Team
