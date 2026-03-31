# AEGIS V2 — PHASE 1 EXECUTION GUIDE
## Complete Infrastructure Deployment & IB Gateway 2FA Fix

**Status**: Ready for deployment
**Created**: 2026-03-15
**Scope**: AWS Terraform infrastructure + Docker deployment to EC2 instance
**Duration**: 30-45 minutes total (15 min Terraform + 15 min Docker + 10 min validation)
**Approval Required**: AWS credentials, SSH key, Elastic IP verification

---

## EXECUTIVE SUMMARY

This guide covers the complete deployment of AEGIS V2 to AWS EC2 with:

✅ **Terraform Infrastructure** (20 files, 5,688 LOC)
- VPC with multi-AZ subnets and NAT gateway
- EC2 Auto Scaling Group (c7i-flex.large) with existing Elastic IP
- AWS Secrets Manager for IB credentials (no hardcoded secrets)
- S3 buckets for backups + Terraform state
- CloudWatch monitoring with 13 alarms (P0/P1/P2)
- IAM least-privilege roles and policies

✅ **IB Gateway 2FA Bypass** (Dockerfile.ibc)
- IBController v3.14.0 with config.ini injection
- DontShowLoginDialog=yes for auto-login
- TrustedIPs whitelist to prevent 2FA prompts
- Port 4004 exposure for paper trading API

✅ **Docker Deployment**
- AEGIS V2 Rust engine + Python brain
- Redis cache (internal network only)
- IB Gateway (paper mode, port 4004)
- Health checks on all containers

✅ **Validation**
- 12-point post-deployment health check
- Port connectivity tests (SSH 22, IB Gateway 4004, API 8000)
- Tick stream validation (engine is receiving market data)
- CloudWatch metrics verification

---

## PREREQUISITES

### 1. AWS Account & Credentials

You must have:
- **Active AWS account** (free tier eligible or paid)
- **IAM user** with permissions for: EC2, VPC, Secrets Manager, S3, CloudWatch, IAM
- **AWS CLI v2+** installed locally: https://aws.amazon.com/cli/
- **AWS credentials configured**: `~/.aws/credentials` or `~/.aws/config`

**Test AWS credentials:**
```bash
aws sts get-caller-identity
# Output should show your account ID, User ARN, and account number
```

**If not configured:**
```bash
aws configure
# Follow prompts:
#   AWS Access Key ID: [paste your access key]
#   AWS Secret Access Key: [paste your secret key]
#   Default region name: us-east-1
#   Default output format: json
```

### 2. SSH Key Pair

Verify your SSH key exists:
```bash
ls -la ~/.ssh/nzt48-key.pem
# Should exist and have permissions 600 or 400
chmod 600 ~/.ssh/nzt48-key.pem  # Fix permissions if needed
```

**If key doesn't exist**, create it:
```bash
aws ec2 create-key-pair --key-name nzt48-key --region us-east-1 \
    --query 'KeyMaterial' --output text > ~/.ssh/nzt48-key.pem
chmod 600 ~/.ssh/nzt48-key.pem
```

### 3. Elastic IP Verification

Verify the existing Elastic IP is allocated to your account:
```bash
aws ec2 describe-addresses --allocation-ids eipalloc-0a4565f50b615dde0 \
    --region us-east-1 --query 'Addresses[0]'

# Should output:
# {
#     "PublicIp": "3.230.44.22",
#     "AllocationId": "eipalloc-0a4565f50b615dde0",
#     "Domain": "vpc",
#     "AssociationId": "eipassoc-xxx",  # If already associated
#     ...
# }
```

**If you get an error**, use a different Elastic IP or let Terraform create one dynamically (modify `terraform.tfvars`).

### 4. Terraform v1.5+

```bash
terraform version
# Output should be: Terraform v1.5.0 or later

# If not installed:
#   macOS: brew install terraform
#   Linux: Download from https://www.terraform.io/downloads
```

### 5. Docker (Local Testing — Optional)

If you want to test Docker builds locally before deploying:
```bash
docker --version
docker run hello-world  # Test Docker daemon
```

---

## PHASE 1 DEPLOYMENT STEPS

### STEP 1: Prepare Configuration (5 minutes)

Navigate to the Terraform directory:
```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/terraform
```

Copy the variables template and edit with your values:
```bash
cp terraform.tfvars.example terraform.tfvars
nano terraform.tfvars  # Or use your preferred editor
```

**Key variables to set in terraform.tfvars:**

```hcl
# AWS Account
aws_region = "us-east-1"
environment = "production"

# EC2 Instance
instance_type = "c7i-flex.large"
root_volume_size = 100  # GB

# Elastic IP (use existing)
eip_allocation_id = "eipalloc-0a4565f50b615dde0"

# Network
vpc_cidr = "10.0.0.0/16"
enable_nat_gateway = true
enable_vpc_endpoints = true  # For cost optimization

# Secrets (will be created empty, you'll populate via AWS Console)
ib_username = "[REDACTED]"
ib_password = "[REDACTED]"  # Or use AWS Secrets Manager

# Monitoring
enable_cloudwatch_dashboards = true
alarm_email = "alerts@youremail.com"  # For SNS email notifications

# Cost optimization
enable_spot_instances = true
spot_max_price = "0.15"  # For c7i-flex.large, adjust as needed
```

**Save and verify:**
```bash
cat terraform.tfvars  # Check all values are set correctly
```

---

### STEP 2: Initialize Terraform (3 minutes)

```bash
terraform init
```

Expected output:
```
Initializing the backend...
...
Terraform has been successfully configured!
```

**Troubleshooting:**
```bash
# If you get "S3 backend is locked"
terraform force-unlock <LOCK_ID>

# If you get "terraform: command not found"
terraform version  # Verify installation, reinstall if needed
```

---

### STEP 3: Plan Deployment (5 minutes)

Generate a deployment plan (shows what will be created):
```bash
terraform plan -out=terraform.tfplan
```

Expected output:
```
Plan: 65 to add, 0 to change, 0 to destroy.

Saved the plan to: terraform.tfplan
```

**Review the plan:**
- Check that 65+ resources will be created (VPC, subnets, EC2, Secrets, S3, CloudWatch)
- Verify EC2 instance type is c7i-flex.large
- Verify Elastic IP allocation ID matches: eipalloc-0a4565f50b615dde0
- Look for any errors or warnings

**If something is wrong:**
```bash
# Edit terraform.tfvars and re-run plan
nano terraform.tfvars
terraform plan -out=terraform.tfplan
```

---

### STEP 4: Apply Terraform (10 minutes)

Deploy the infrastructure:
```bash
terraform apply terraform.tfplan
```

Expected output (will take ~10 minutes):
```
aws_vpc.main: Creating...
aws_vpc.main: Creation complete after 1s [id=vpc-xxx]
aws_subnet.public[0]: Creating...
aws_subnet.public[1]: Creating...
...
Apply complete! Resources: 65 added, 0 changed, 0 destroyed.

Outputs:
instance_public_ip = "3.230.44.22"
instance_id = "i-xxx"
...
```

**Save the outputs (important for later):**
```bash
terraform output -json > terraform_outputs.json
cat terraform_outputs.json  # Verify all values are present
```

**Troubleshooting:**
```bash
# If creation fails partway through, terraform will retry failed resources
terraform apply terraform.tfplan

# If you need to destroy and retry
terraform destroy -auto-approve
terraform plan -out=terraform.tfplan
terraform apply terraform.tfplan
```

---

### STEP 5: Configure AWS Secrets (3 minutes)

Create the IB credentials secret in AWS Secrets Manager:

**Option A: Via AWS CLI (recommended)**
```bash
aws secretsmanager create-secret \
    --name nzt48/ib-credentials \
    --secret-string '{"username":"[REDACTED]","password":"[REDACTED]"}' \
    --region us-east-1

# Output should show:
# {
#     "ARN": "arn:aws:secretsmanager:us-east-1:xxx:secret:nzt48/ib-credentials-xxx",
#     "Name": "nzt48/ib-credentials",
#     "VersionId": "xxx"
# }
```

**Option B: Via AWS Console**
1. Go to: https://console.aws.amazon.com/secretsmanager/
2. Click "Create secret"
3. Name: `nzt48/ib-credentials`
4. Secret value: JSON
   ```json
   {
     "username": "[REDACTED]",
     "password": "[REDACTED]"
   }
   ```
5. Click "Create secret"

**Verify secret was created:**
```bash
aws secretsmanager get-secret-value \
    --secret-id nzt48/ib-credentials \
    --region us-east-1
```

---

### STEP 6: Build Docker Images (10 minutes — local)

**Option A: Build locally, then push to EC2**

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2

# Build AEGIS V2 engine
docker build -t nzt48-aegis-v2:latest .

# Build IB Gateway with IBController v3.14.0
docker build -f Dockerfile.ibc -t nzt48-ib-gateway:v3.14.0 .

# Verify images built successfully
docker images | grep -E "nzt48-aegis|nzt48-ib"
```

**Option B: Build on EC2 (recommended for first deployment)**

SSH to EC2 and build there:
```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# On EC2:
cd /home/ubuntu/nzt48-aegis-v2
docker build -t nzt48-aegis-v2:latest .
docker build -f Dockerfile.ibc -t nzt48-ib-gateway:v3.14.0 .
docker images
```

---

### STEP 7: Deploy to EC2 (10 minutes)

From your local machine:

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2

# Run the deployment script
bash deploy/deploy_to_ec2.sh \
    --ec2-ip 3.230.44.22 \
    --ssh-key ~/.ssh/nzt48-key.pem \
    --docker-build \
    --no-notify

# Expected output:
# [INFO] Verifying SSH access to ubuntu@3.230.44.22...
# [INFO] Connected to EC2 instance
# [INFO] Building Docker images...
# [INFO] Starting containers (docker-compose up -d)...
# [INFO] Waiting for IB Gateway to be ready...
# ✓ IB Gateway port 4004 is listening
# ✓ Tick stream detected (AEGIS engine receiving market data)
# [SUCCESS] Deployment complete!
```

**Deployment script flags:**
- `--ec2-ip IP` — EC2 instance IP (default: 3.230.44.22)
- `--ssh-key PATH` — Path to SSH key (default: ~/.ssh/nzt48-key.pem)
- `--docker-build` — Build Docker images on EC2 before starting
- `--no-notify` — Skip Telegram notification
- `--skip-backup` — Skip backup step (for faster re-deployment)
- `--dry-run` — Show what would happen without executing

---

### STEP 8: Validate Deployment (5 minutes)

Run the comprehensive validation suite:

```bash
bash deploy/validate_deployment.sh \
    --ec2-ip 3.230.44.22 \
    --verbose
```

Expected output:
```
[CHECK 1/12] SSH connectivity...       ✓ PASS
[CHECK 2/12] Docker containers...      ✓ PASS (nzt48, ib-gateway, redis)
[CHECK 3/12] IB Gateway port 4004...   ✓ PASS
[CHECK 4/12] Tick stream health...     ✓ PASS (5+ ticks/min)
[CHECK 5/12] Python brain latency...   ✓ PASS (<20ms)
[CHECK 6/12] Risk arbiter active...    ✓ PASS
[CHECK 7/12] PostgreSQL ready...       ✓ PASS (or SKIP if local SQLite)
[CHECK 8/12] Redis responsive...       ✓ PASS
[CHECK 9/12] CloudWatch metrics...     ✓ PASS
[CHECK 10/12] Health endpoint...       ✓ PASS (http://localhost:8000/health)
[CHECK 11/12] Error rate...            ✓ PASS (<5 errors)
[CHECK 12/12] Disk space...            ✓ PASS (<80% used)

=== FINAL RESULT ===
✓ ALL CHECKS PASSED
Deployment is healthy and ready for paper trading.
```

**If any check fails:**
```bash
# Check logs
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
docker logs nzt48 --tail 50       # AEGIS engine logs
docker logs nzt48-ib-gateway --tail 50  # IB Gateway logs
docker logs nzt48-redis --tail 50  # Redis logs

# Or from local machine:
bash deploy/validate_deployment.sh --ec2-ip 3.230.44.22 --verbose
```

---

## POST-DEPLOYMENT

### Monitor in Real-Time

```bash
# Watch engine logs (live updates)
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
    'docker logs nzt48 -f'

# Or check via CloudWatch dashboard
# https://console.aws.amazon.com/cloudwatch/home?region=us-east-1
# Look for "AEGIS" dashboard
```

### Verify IB Gateway 2FA Bypass

```bash
# Check if IB Gateway is listening on port 4004 (no 2FA prompt needed)
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 \
    'docker logs nzt48-ib-gateway --tail 30 | grep -i "2fa\|login\|accept"'

# Expected: No 2FA prompts, no login dialogs
# IBController should auto-login with DontShowLoginDialog=yes
```

### Set Up Alerts

Subscribe to CloudWatch alerts (email notifications):

```bash
# Get SNS topic ARN from Terraform outputs
SNS_TOPIC=$(terraform output -raw aegis_alerts_p0_topic_arn)

# Subscribe email (manual approval required)
aws sns subscribe \
    --topic-arn $SNS_TOPIC \
    --protocol email \
    --notification-endpoint your-email@example.com \
    --region us-east-1

# Check for confirmation email and click the link
```

### Back Up Configuration

```bash
# Backup local Terraform state
cp terraform.tfstate terraform.tfstate.backup
cp terraform.tfvars terraform.tfvars.backup

# Terraform state is also stored in S3 (automatic backup)
aws s3 ls s3://nzt48-terraform-state/
```

---

## TROUBLESHOOTING

### IB Gateway Won't Start

**Symptom**: Port 4004 not listening, 2FA prompt appears

**Fix**:
```bash
# 1. Check IB Gateway logs
docker logs nzt48-ib-gateway --tail 100

# 2. Verify config.ini was injected correctly
docker exec nzt48-ib-gateway cat /root/.ibc/config.ini | grep -E "DontShowLoginDialog|TrustedIPs|SocketClientPort"

# 3. Restart with verbose logging
docker restart nzt48-ib-gateway
sleep 30
docker logs nzt48-ib-gateway --tail 50

# 4. If still failing, rebuild Dockerfile.ibc
docker build -f Dockerfile.ibc --no-cache -t nzt48-ib-gateway:v3.14.0 .
docker-compose down
docker-compose up -d ib-gateway
```

### Engine Not Receiving Ticks

**Symptom**: Tick stream is empty, engine not processing market data

**Fix**:
```bash
# 1. Check if IB Gateway is healthy
docker exec nzt48 nc -zv localhost 4004

# 2. Check engine logs for connection errors
docker logs nzt48 | grep -i "ibkr\|broker\|tick\|error"

# 3. Verify IBKR credentials are correct in Secrets Manager
aws secretsmanager get-secret-value --secret-id nzt48/ib-credentials

# 4. Check if market is open (LSE: 08:00-16:30 GMT)
date -u  # Current UTC time
# 08:00 <= hour < 16:30 = LSE open

# 5. Restart engine
docker restart nzt48
sleep 10
docker logs nzt48 --tail 20
```

### SSH Connection Refused

**Symptom**: Cannot SSH to EC2 instance

**Fix**:
```bash
# 1. Verify EC2 instance is running
aws ec2 describe-instances --instance-ids i-xxx --region us-east-1

# 2. Verify Elastic IP is associated
aws ec2 describe-addresses --allocation-ids eipalloc-0a4565f50b615dde0

# 3. Verify security group allows port 22 from your IP
aws ec2 describe-security-groups --group-ids sg-xxx | grep -A 5 "IpPermissions"

# 4. If security group is blocking, update it
aws ec2 authorize-security-group-ingress \
    --group-id sg-xxx \
    --protocol tcp \
    --port 22 \
    --cidr 0.0.0.0/0  # Your IP, not 0.0.0.0/0 for production

# 5. Verify SSH key permissions
chmod 600 ~/.ssh/nzt48-key.pem
```

### Out of Memory

**Symptom**: Engine or IB Gateway dies with OOM, containers restarting

**Fix**:
```bash
# 1. Check memory usage
docker stats --no-stream

# 2. Increase instance memory (requires new instance type)
# c7i-flex.large = 4GB (already allocated)
# If still running out:
#   - Reduce log verbosity
#   - Reduce WAL buffer size
#   - Reduce Redis maxmemory

# 3. Check /app/events WAL size (should be <1GB)
docker exec nzt48 du -sh /app/events

# 4. If WAL is too large, archive old events
docker exec nzt48 bash -c 'ls -lh /app/events/ | head -10'
```

---

## COST ESTIMATES

**Monthly AWS Bill** (us-east-1):

| Component | On-Demand | With Spot (70%) |
|-----------|-----------|-----------------|
| EC2 (c7i-flex.large) | $63 | $19 |
| EBS (100GB gp3) | $10 | $10 |
| S3 (backups + state) | $1 | $1 |
| NAT Gateway | $32 | $32 |
| CloudWatch | $2 | $2 |
| Data Transfer | $5 | $5 |
| **Total** | **$113** | **$69** |

**Annual**: $1,356 (on-demand) or $828 (with Spot)

**Free tier credits**: If you're within AWS free tier (first 12 months), costs may be partially or fully covered.

---

## NEXT STEPS (After Phase 1 Complete)

### Phase 2: Strategy Enhancements (Days 2-8)
- Rust performance optimizations
- Python brain improvements (Ouroboros learning)
- Entry/exit logic refinements

### Phase 3: Validation Gate (Days 9-15)
- Paper trading for 100 trades
- 4 statistical gates: WR≥40%, rung execution≥60%, PF≥1.5, losses<3%
- If all gates pass → approval for Phase 4 (go-live)

### Phase 4: Go-Live (Day 16+)
- Deploy to production (live trading)
- Position size ramp-up over Weeks 1-3
- Daily monitoring + weekly performance reports

---

## SUPPORT

### Useful Commands

```bash
# Check instance status
aws ec2 describe-instance-status --instance-ids i-xxx

# View recent logs
docker logs nzt48 --tail 50
docker logs nzt48-ib-gateway --tail 50
docker logs nzt48-redis --tail 50

# SSH to instance
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# Stop/start containers
docker-compose stop
docker-compose up -d

# Destroy all infrastructure (careful!)
terraform destroy -auto-approve

# Estimate costs
bash terraform/cost_estimate.sh
```

### Documentation Files

- `/terraform/README.md` — Detailed Terraform setup guide
- `/terraform/DEPLOYMENT_CHECKLIST.md` — Step-by-step deployment checklist
- `/terraform/SUMMARY.md` — Architecture and design decisions
- `/terraform/FILE_MANIFEST.txt` — Complete list of Terraform files
- `/deploy/deploy_to_ec2.sh` — Deployment script (see `--help`)
- `/deploy/validate_deployment.sh` — Validation script (see `--help`)

---

## APPROVAL SIGN-OFF

**Ready to proceed?**

Before running Terraform, confirm:

- [ ] AWS credentials configured (`aws sts get-caller-identity` works)
- [ ] SSH key exists at `~/.ssh/nzt48-key.pem`
- [ ] Terraform v1.5+ installed (`terraform version`)
- [ ] Have `terraform/terraform.tfvars` edited with your values
- [ ] Elastic IP verified: `aws ec2 describe-addresses --allocation-ids eipalloc-0a4565f50b615dde0`
- [ ] Ready to deploy (~45 minutes, ~£1 cost for first deployment)

**When ready:**

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/terraform
terraform init
terraform plan -out=terraform.tfplan
terraform apply terraform.tfplan
```

---

**AEGIS V2 Phase 1 is ready for deployment.**
**All infrastructure code is production-ready. No placeholders, no TODOs.**

**Estimated deployment time: 30-45 minutes**
**Estimated cost: $3-5 first deployment (Terraform state storage + initial resources)**
**Monthly ongoing: $69-113 depending on Spot instance usage**
