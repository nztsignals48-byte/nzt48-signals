# NZT-48 Multi-Region Infrastructure (Phase Q4)

This directory contains Terraform configuration for deploying NZT-48's multi-region active-active infrastructure.

## Architecture

- **Primary Region**: us-east-1 (Virginia) - existing deployment
- **Secondary Region**: eu-west-1 (Ireland) - failover target, 1-2ms to LSE
- **Data Sync**: RDS PostgreSQL cross-region replication
- **State Sync**: Redis ElastiCache with Global Datastore (optional)
- **Failover**: Route53 health checks + automatic DNS failover
- **Target**: <5 min RTO, <30 sec RPO, zero trade loss

## Prerequisites

1. AWS CLI configured with appropriate credentials
2. Terraform >= 1.5 installed
3. Existing Route53 hosted zone
4. S3 bucket for Terraform state (create manually first)

## Initial Setup

### 1. Create Terraform State Backend

```bash
# Create S3 bucket for state
aws s3 mb s3://nzt48-terraform-state --region us-east-1

# Enable versioning
aws s3api put-bucket-versioning \
  --bucket nzt48-terraform-state \
  --versioning-configuration Status=Enabled

# Create DynamoDB table for state locking
aws dynamodb create-table \
  --table-name nzt48-terraform-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

### 2. Configure Variables

```bash
# Copy example file
cp terraform.tfvars.example terraform.tfvars

# Edit with your values
nano terraform.tfvars
```

**Required variables:**
- `db_password` - Strong password for RDS
- `redis_auth_token` - Secure token for Redis
- `route53_zone_name` - Your domain name
- `allowed_ssh_cidr` - Your IP address for SSH

### 3. Initialize Terraform

```bash
terraform init
```

## Deployment

### Plan

Review changes before applying:

```bash
terraform plan
```

### Apply

Deploy infrastructure:

```bash
terraform apply
```

**Note:** Initial deployment takes 10-15 minutes (RDS creation is slow).

### Verify

```bash
# Check outputs
terraform output

# Test health checks
terraform output primary_endpoint_dns
curl http://$(terraform output -raw primary_eip):8080/health
```

## Phased Deployment Strategy

### Phase 1: RDS Replication (Week 1)

Deploy only RDS cross-region replication:

```bash
# Comment out Redis and Route53 resources in main.tf
# Keep only: aws_db_instance resources

terraform apply -target=aws_db_instance.primary
terraform apply -target=aws_db_instance.secondary
```

**Validate:**
- Check replication lag: <5 seconds
- Verify data sync: Query both endpoints

### Phase 2: Redis Cluster (Week 2)

Add Redis cross-region replication:

```bash
terraform apply -target=aws_elasticache_replication_group.primary
terraform apply -target=aws_elasticache_replication_group.secondary
```

**Validate:**
- Test Redis connectivity from both regions
- Verify key replication

### Phase 3: Route53 Failover (Week 3)

Enable automatic failover:

```bash
terraform apply
```

**Validate:**
- Test health checks
- Simulate failover (see failover_runbook.md)
- Verify automatic recovery

## Cost Estimate

**Free Tier Eligible:**
- EC2: c7i-flex.large (covered by existing deployment)
- RDS: db.t4g.micro × 2 = $0 (first 750 hours/month free)
- ElastiCache: cache.t4g.micro × 2 = $0 (first 750 hours/month free)
- S3: Negligible (<1GB)
- Route53: $0.50/month per health check × 2 = $1/month

**Total Monthly Cost:** ~$1-5/month (within free tier limits)

**Production Scale (After Free Tier):**
- RDS: db.t4g.small × 2 = ~$30/month
- ElastiCache: cache.t4g.small × 2 = ~$40/month
- Data transfer: ~$5-10/month
- **Total:** ~$75-80/month

## Monitoring

### CloudWatch Dashboards

```bash
# View primary region metrics
aws cloudwatch get-dashboard \
  --dashboard-name nzt48-primary \
  --region us-east-1

# View secondary region metrics
aws cloudwatch get-dashboard \
  --dashboard-name nzt48-secondary \
  --region eu-west-1
```

### Health Check Status

```bash
# Check primary health
aws route53 get-health-check-status \
  --health-check-id $(terraform output -raw primary_health_check_id)

# Check secondary health
aws route53 get-health-check-status \
  --health-check-id $(terraform output -raw secondary_health_check_id)
```

### Replication Lag

```bash
# RDS replication lag
aws cloudwatch get-metric-statistics \
  --namespace AWS/RDS \
  --metric-name ReplicaLag \
  --dimensions Name=DBInstanceIdentifier,Value=nzt48-secondary \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Average \
  --region eu-west-1
```

## Failover Testing

See `../failover_runbook.md` for detailed procedures.

### Quick Test

```bash
# 1. Stop primary health endpoint
ssh ubuntu@$(terraform output -raw primary_eip)
docker stop nzt48

# 2. Wait 90 seconds for health check to fail

# 3. Verify Route53 failed over
dig @8.8.8.8 nzt48.example.com
# Should return secondary IP

# 4. Restart primary
docker start nzt48

# 5. Verify failback within 60 seconds
```

## Disaster Recovery

### Backup Strategy

1. **RDS Automated Backups**: 7-day retention
2. **RDS Manual Snapshots**: Monthly, kept indefinitely
3. **Redis AOF Persistence**: Enabled on both clusters
4. **S3 Sync**: Daily backup of SQLite + outcomes

### Recovery Procedures

#### RDS Corruption

```bash
# Restore from snapshot
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier nzt48-primary-restored \
  --db-snapshot-identifier nzt48-primary-2026-03-15 \
  --region us-east-1
```

#### Redis Data Loss

```bash
# Restore from backup
aws elasticache create-cache-cluster \
  --cache-cluster-id nzt48-redis-restored \
  --snapshot-name nzt48-redis-backup-2026-03-15 \
  --region us-east-1
```

## Cleanup

**WARNING:** This destroys all infrastructure. Backup data first!

```bash
# Create final snapshots
terraform apply -var="skip_final_snapshot=false"

# Destroy infrastructure
terraform destroy
```

## Troubleshooting

### RDS Replication Lag High

```bash
# Check network connectivity
aws rds describe-db-instances \
  --db-instance-identifier nzt48-secondary \
  --region eu-west-1 \
  --query 'DBInstances[0].StatusInfos'

# Check for blocking queries
psql -h <rds-endpoint> -U nzt48admin -d nzt48 \
  -c "SELECT * FROM pg_stat_replication;"
```

### Redis Connection Refused

```bash
# Verify security group allows traffic
aws ec2 describe-security-groups \
  --group-ids $(terraform output -raw redis_primary_sg_id) \
  --region us-east-1

# Test connectivity from EC2
ssh ubuntu@$(terraform output -raw primary_eip)
redis-cli -h <redis-endpoint> -p 6379 -a <auth-token> PING
```

### Route53 Not Failing Over

```bash
# Check health check status
aws route53 get-health-check \
  --health-check-id $(terraform output -raw primary_health_check_id)

# Verify health endpoint responds
curl -v http://$(terraform output -raw primary_eip):8080/health
```

## Security

### Network Security

- EC2: Only SSH (port 22) and API (port 8000) exposed
- RDS: Only accessible from EC2 security group
- Redis: Only accessible from EC2 security group
- All traffic encrypted in transit (TLS)
- All data encrypted at rest (KMS)

### Access Control

- IAM roles for EC2 instances (no hardcoded credentials)
- RDS passwords stored in AWS Secrets Manager
- Redis AUTH tokens rotated monthly
- SSH keys rotated quarterly

### Compliance

- GDPR: Data residency in eu-west-1 for EU users
- SOC 2: Audit logs enabled on all services
- Encryption: FIPS 140-2 compliant KMS keys

## Updates

### Terraform Version

```bash
# Check current version
terraform version

# Upgrade providers
terraform init -upgrade
```

### Infrastructure Updates

```bash
# Plan changes
terraform plan

# Apply updates with approval
terraform apply

# Auto-approve (use with caution)
terraform apply -auto-approve
```

## Support

**Terraform Issues:**
- Docs: https://registry.terraform.io/providers/hashicorp/aws/latest/docs
- GitHub: https://github.com/hashicorp/terraform/issues

**AWS Support:**
- Console: https://console.aws.amazon.com/support/
- Phone: 1-866-999-0305 (24/7)

**NZT-48 Internal:**
- Runbook: ../failover_runbook.md
- Architecture: ../../docs/architecture.md
- Monitoring: ../../monitoring/README.md

---

**Last Updated:** 2026-03-15
**Maintained By:** NZT-48 DevOps
**Version:** 1.0
