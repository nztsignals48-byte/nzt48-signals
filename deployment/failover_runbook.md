# NZT-48 Multi-Region Failover Runbook

**Phase Q4 Deliverable #1**
**Target:** <5 min failover, zero trade loss

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Route53 DNS                             │
│  Failover: PRIMARY (us-east-1) → SECONDARY (eu-west-1)      │
│  Health checks every 30s on /health endpoint                 │
└─────────────────────────────────────────────────────────────┘
                          ↓
         ┌────────────────┴────────────────┐
         ↓                                  ↓
┌────────────────────┐          ┌────────────────────┐
│  PRIMARY (us-east-1)│          │ SECONDARY (eu-west-1)│
│  EC2: c7i-flex.large│          │ EC2: c7i-flex.large │
│  EIP: 3.230.44.22   │          │ EIP: TBD            │
│  RDS: Primary       │          │ RDS: Read Replica   │
│  Redis: Master      │          │ Redis: Replica      │
│  IB Gateway: Active │          │ IB Gateway: Standby │
└────────────────────┘          └────────────────────┘
         ↓                                  ↓
   Cross-region replication (async)
         - RDS: Continuous replication
         - Redis: Global Datastore (optional)
         - S3: Backup sync every 24h
```

---

## Failure Scenarios

### Scenario 1: EC2 Instance Failure (us-east-1)

**Detection:** Route53 health check fails 3 consecutive times (90 seconds)

**Automatic Actions:**
1. Route53 stops routing traffic to primary A record
2. All traffic automatically routes to secondary endpoint
3. CloudWatch alarm triggers (nzt48-primary-unhealthy)

**Manual Actions Required:**
```bash
# 1. Check primary instance status
aws ec2 describe-instance-status \
  --instance-ids i-027add7c7366d4c86 \
  --region us-east-1

# 2. If instance is terminated, launch replacement
aws ec2 run-instances \
  --image-id ami-0c55b159cbfafe1f0 \
  --instance-type c7i-flex.large \
  --key-name nzt48-key \
  --security-group-ids sg-xxxxx \
  --user-data file://scripts/user_data.sh \
  --region us-east-1

# 3. Associate Elastic IP to new instance
aws ec2 associate-address \
  --instance-id <new-instance-id> \
  --allocation-id eipalloc-0a4565f50b615dde0 \
  --region us-east-1

# 4. Wait for health check to recover
# Route53 will automatically route traffic back to primary
```

**Expected Downtime:** 2-3 minutes (health check detection + DNS propagation)

---

### Scenario 2: RDS Primary Failure

**Detection:** RDS monitoring shows unavailable status

**Automatic Actions:**
1. RDS automatically promotes read replica to standalone instance
2. Application connection strings remain valid (endpoint DNS updates)

**Manual Actions Required:**
```bash
# 1. Promote read replica to primary (if automatic failover didn't trigger)
aws rds promote-read-replica \
  --db-instance-identifier nzt48-secondary \
  --region eu-west-1

# 2. Update application config to point to new primary
# Edit docker-compose.yml or .env.production:
# DATABASE_URL=postgresql://user:pass@<new-endpoint>:5432/nzt48

# 3. Create new read replica in us-east-1
aws rds create-db-instance-read-replica \
  --db-instance-identifier nzt48-primary-new \
  --source-db-instance-identifier arn:aws:rds:eu-west-1:xxx:db:nzt48-secondary \
  --region us-east-1
```

**Expected Downtime:** <5 minutes (RDS automatic promotion + DNS propagation)

---

### Scenario 3: Region-Wide Outage (us-east-1)

**Detection:**
- Route53 health check fails
- AWS status dashboard shows region issues
- Multiple services unreachable

**Automatic Actions:**
1. Route53 fails over to eu-west-1
2. Traffic routes to secondary instance
3. Secondary RDS promoted to primary

**Manual Actions Required:**
```bash
# 1. Promote secondary RDS to primary (if not automatic)
aws rds promote-read-replica \
  --db-instance-identifier nzt48-secondary \
  --region eu-west-1

# 2. Promote secondary Redis to primary (if using Global Datastore)
aws elasticache modify-replication-group-shard-configuration \
  --replication-group-id nzt48-redis-secondary \
  --node-group-count 2 \
  --apply-immediately \
  --region eu-west-1

# 3. SSH to secondary instance and restart services
ssh -i ~/.ssh/nzt48-key.pem ubuntu@<secondary-eip>
cd /home/ubuntu/nzt48-signals
docker compose down
docker compose up -d

# 4. Update environment variables to use eu-west-1 resources
# Edit .env.production:
# AWS_REGION=eu-west-1
# DATABASE_URL=postgresql://user:pass@<eu-rds-endpoint>:5432/nzt48
# REDIS_URL=redis://<eu-redis-endpoint>:6379

# 5. Restart application
docker compose restart nzt48
```

**Expected Downtime:** 3-5 minutes (manual intervention + service restart)

---

### Scenario 4: IB Gateway Connection Failure

**Detection:**
- `ib_gateway_health_monitor.py` detects disconnection
- Telegram alert: "IB Gateway disconnected"

**Automatic Actions:**
1. System halts all new trades (safety enforcer)
2. Existing positions remain open
3. Health check endpoint returns 503

**Manual Actions Required:**
```bash
# 1. SSH to affected instance
ssh -i ~/.ssh/nzt48-key.pem ubuntu@<eip>

# 2. Check IB Gateway logs
docker logs ib-gateway --tail 100

# 3. Restart IB Gateway container
docker compose restart ib-gateway

# 4. Re-authenticate if needed (Monday 2FA)
# VNC to IB Gateway: vnc://localhost:5900 (password: nzt48vnc)
# Enter 2FA code from IBKR mobile app

# 5. Verify reconnection
docker exec nzt48 python -c "from core.ib_gateway_health_monitor import check_connection; print(check_connection())"

# 6. Resume trading manually if needed
docker exec nzt48 python scripts/resume_trading.py
```

**Expected Downtime:** 2-10 minutes (depends on 2FA requirements)

---

## Manual Failover Procedure

Use this procedure to manually fail over to secondary region for planned maintenance:

### Step 1: Prepare Secondary Region

```bash
# 1. SSH to secondary instance
ssh -i ~/.ssh/nzt48-key.pem ubuntu@<secondary-eip>

# 2. Pull latest code
cd /home/ubuntu/nzt48-signals
git pull origin main

# 3. Build Docker images
docker compose build

# 4. Verify health check endpoint
curl http://localhost:8080/health
# Should return: {"status": "healthy", "region": "eu-west-1"}
```

### Step 2: Promote Secondary RDS

```bash
# Promote read replica to standalone
aws rds promote-read-replica \
  --db-instance-identifier nzt48-secondary \
  --region eu-west-1

# Wait for promotion to complete (2-5 minutes)
aws rds wait db-instance-available \
  --db-instance-identifier nzt48-secondary \
  --region eu-west-1
```

### Step 3: Update Route53 (Manual Override)

```bash
# Update primary record to point to secondary
aws route53 change-resource-record-sets \
  --hosted-zone-id <zone-id> \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "nzt48.example.com",
        "Type": "A",
        "TTL": 60,
        "ResourceRecords": [{"Value": "<secondary-eip>"}]
      }
    }]
  }'
```

### Step 4: Start Trading on Secondary

```bash
# 1. Update environment variables
cd /home/ubuntu/nzt48-signals
cp .env.production .env.production.backup
nano .env.production
# Set: AWS_REGION=eu-west-1
# Set: DATABASE_URL=<secondary-rds-endpoint>
# Set: REDIS_URL=<secondary-redis-endpoint>

# 2. Restart services
docker compose down
docker compose up -d

# 3. Verify all services running
docker compose ps
# All services should show "Up" status

# 4. Check logs
docker logs nzt48 --tail 50 -f
```

### Step 5: Stop Primary (For Maintenance)

```bash
# 1. SSH to primary
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# 2. Gracefully stop trading
cd /home/ubuntu/nzt48-signals
docker exec nzt48 python scripts/halt_trading.py

# 3. Wait for all positions to close (or manually close)

# 4. Stop services
docker compose down

# 5. Perform maintenance (OS updates, etc.)
```

### Step 6: Failback to Primary

```bash
# 1. Verify primary is healthy
curl http://3.230.44.22:8080/health

# 2. Re-establish RDS replication
aws rds create-db-instance-read-replica \
  --db-instance-identifier nzt48-primary-replica \
  --source-db-instance-identifier arn:aws:rds:eu-west-1:xxx:db:nzt48-secondary \
  --region us-east-1

# 3. Wait for replication lag to catch up (<1 min)

# 4. Promote primary replica back to primary
aws rds promote-read-replica \
  --db-instance-identifier nzt48-primary-replica \
  --region us-east-1

# 5. Update Route53 back to primary
aws route53 change-resource-record-sets \
  --hosted-zone-id <zone-id> \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "nzt48.example.com",
        "Type": "A",
        "TTL": 60,
        "ResourceRecords": [{"Value": "3.230.44.22"}]
      }
    }]
  }'

# 6. Restart primary services
ssh ubuntu@3.230.44.22
cd /home/ubuntu/nzt48-signals
docker compose up -d
```

**Total Failback Time:** 10-15 minutes

---

## Monitoring & Alerts

### CloudWatch Alarms

1. **Primary Health Check Failure**
   - Metric: `HealthCheckStatus < 1`
   - Action: SMS/Email alert
   - Threshold: 2 consecutive failures (60 sec)

2. **Secondary Health Check Failure**
   - Metric: `HealthCheckStatus < 1`
   - Action: SMS/Email alert
   - Threshold: 2 consecutive failures

3. **RDS Replication Lag**
   - Metric: `ReplicaLag > 30 seconds`
   - Action: Telegram alert
   - Threshold: 30 seconds

4. **Redis Memory Utilization**
   - Metric: `DatabaseMemoryUsagePercentage > 80%`
   - Action: Email alert

### Dashboard Links

- Primary CloudWatch: https://console.aws.amazon.com/cloudwatch/home?region=us-east-1
- Secondary CloudWatch: https://console.aws.amazon.com/cloudwatch/home?region=eu-west-1
- Route53 Health Checks: https://console.aws.amazon.com/route53/healthchecks/home

---

## Testing Failover

### Simulate Primary Failure

```bash
# 1. Stop primary health check endpoint
ssh ubuntu@3.230.44.22
docker stop nzt48

# 2. Wait 90 seconds for health check to fail

# 3. Verify Route53 failed over
dig nzt48.example.com
# Should return secondary IP

# 4. Verify traffic routing to secondary
curl http://nzt48.example.com:8000/health
# Should return: {"status": "healthy", "region": "eu-west-1"}

# 5. Restart primary
docker start nzt48

# 6. Verify failback after health check recovers
```

**Expected Results:**
- Failover: 90-120 seconds
- Failback: 60-90 seconds
- Zero trade loss (positions preserved in RDS)

---

## Disaster Recovery Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Recovery Time Objective (RTO) | <5 min | Time to restore service |
| Recovery Point Objective (RPO) | <30 sec | Max data loss |
| Health Check Detection Time | 90 sec | 3 × 30s checks |
| DNS Propagation Time | 60 sec | TTL=60s |
| RDS Promotion Time | 2-5 min | Automated |
| Manual Intervention Time | 5-10 min | Worst case |

---

## Rollback Plan

If failover causes issues:

```bash
# 1. Immediate rollback: Update Route53 to point back to primary
aws route53 change-resource-record-sets --hosted-zone-id <zone-id> \
  --change-batch file://rollback-to-primary.json

# 2. Stop secondary services
ssh ubuntu@<secondary-eip>
docker compose down

# 3. Verify primary is serving traffic
curl http://3.230.44.22:8000/health
```

---

## Post-Incident Review

After any failover event:

1. Document timeline in `logs/failover_YYYY-MM-DD.md`
2. Review CloudWatch logs for root cause
3. Update runbook with lessons learned
4. Test failback procedure
5. Verify data consistency between regions

---

## Contact Information

**On-Call:**
- Primary: [Your contact]
- Secondary: [Backup contact]

**AWS Support:**
- Phone: 1-866-999-0305
- Case submission: https://console.aws.amazon.com/support/

**IBKR Support:**
- Phone: +1-312-542-6901
- Hours: 24/7

---

## Appendix: Configuration Files

### Route53 Health Check Config

```json
{
  "Type": "HTTP",
  "ResourcePath": "/health",
  "FullyQualifiedDomainName": "3.230.44.22",
  "Port": 8080,
  "RequestInterval": 30,
  "FailureThreshold": 3
}
```

### Health Endpoint Response

```json
{
  "status": "healthy",
  "region": "us-east-1",
  "services": {
    "ib_gateway": "connected",
    "redis": "connected",
    "rds": "connected"
  },
  "trading_active": true,
  "timestamp": "2026-03-15T10:30:00Z"
}
```

---

**Document Version:** 1.0
**Last Updated:** 2026-03-15
**Next Review:** 2026-04-15
