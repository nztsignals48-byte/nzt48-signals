# AEGIS V2 — PHASE 1 QUICK START (5 MINUTES)

**TL;DR**: Deploy AEGIS V2 to AWS in 45 minutes with one command.

---

## Prerequisites (One-time Setup)

```bash
# Install AWS CLI
# macOS: brew install awscli
# Linux: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
# Windows: https://aws.amazon.com/cli/

# Configure AWS credentials
aws configure
# Enter: Access Key, Secret Key, Region (us-east-1), Output Format (json)

# Verify credentials work
aws sts get-caller-identity

# Verify SSH key exists
ls ~/.ssh/nzt48-key.pem

# Verify Terraform installed
terraform version  # Should be v1.5 or later
```

---

## Deploy in 3 Steps

### Step 1: Configure Terraform (2 min)

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2/terraform

# Copy template
cp terraform.tfvars.example terraform.tfvars

# Edit with your values (keys, Elastic IP, etc)
nano terraform.tfvars
```

### Step 2: Deploy Infrastructure (15 min)

```bash
terraform init
terraform plan -out=terraform.tfplan
terraform apply terraform.tfplan

# Save outputs
terraform output -json > outputs.json
```

### Step 3: Deploy to EC2 (15 min)

```bash
cd /Users/rr/nzt48-signals/nzt48-aegis-v2

bash deploy/deploy_to_ec2.sh \
    --ec2-ip 3.230.44.22 \
    --ssh-key ~/.ssh/nzt48-key.pem \
    --docker-build
```

**Done!** ✅

---

## Verify Deployment (5 min)

```bash
bash deploy/validate_deployment.sh --ec2-ip 3.230.44.22
```

Expected: All 12 checks pass ✓

---

## What Was Deployed?

✅ VPC with 4 subnets across 2 AZs
✅ EC2 (c7i-flex.large) with Elastic IP 3.230.44.22
✅ IB Gateway with IBController v3.14.0 (no 2FA prompts)
✅ AEGIS V2 Rust engine + Python brain
✅ Redis cache
✅ AWS Secrets Manager (IB credentials)
✅ S3 backups + Terraform state
✅ CloudWatch monitoring (13 alarms)
✅ 12 LSE leveraged ETPs streaming live data

---

## Monthly Cost

- **On-Demand**: $113
- **With Spot Instances**: $69
- **Free-Tier Eligible**: May be covered

---

## Troubleshooting

**IB Gateway won't start?**
```bash
docker logs nzt48-ib-gateway --tail 50
# Check for config.ini injection, TrustedIPs whitelist, 2FA bypass status
```

**Engine not receiving ticks?**
```bash
docker logs nzt48 | grep -i "tick\|error\|ibkr"
# Verify IB Gateway is healthy on port 4004
docker exec nzt48 nc -zv localhost 4004
```

**SSH connection refused?**
```bash
aws ec2 describe-instances --instance-ids i-xxx
# Verify security group allows port 22
```

---

## Next: Paper Trading (Phase 2-3)

After validation passes, engine is ready for:
1. **Paper trading** (100 trades over ~7-15 days)
2. **4 validation gates**: WR≥40%, Rung≥60%, PF≥1.5, Losses<3%
3. **Go-live** (if all gates pass)

---

## Documentation

- **Full Setup Guide**: `PHASE_1_EXECUTION_GUIDE.md`
- **Terraform Details**: `terraform/README.md`
- **Deployment Checklist**: `terraform/DEPLOYMENT_CHECKLIST.md`
- **Architecture**: `terraform/SUMMARY.md`

---

**Ready?** `cd terraform && bash ../deploy.sh deploy`
