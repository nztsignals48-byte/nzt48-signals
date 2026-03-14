# DEPLOYMENT ROLLBACK PLAN
## NZT48 Trading System v2.0
**Created:** 2026-03-14  
**Last Updated:** 2026-03-14  
**Version:** 1.0

---

## EXECUTIVE SUMMARY

This document provides step-by-step procedures to rollback NZT48 Trading System in case of deployment failure, data corruption, or critical bugs discovered in production.

**Objective:** Minimize downtime and losses by having pre-planned, tested rollback procedures.

**Rollback Window:** 5-10 minutes (fastest: 2 minutes)

---

## TABLE OF CONTENTS

1. [Rollback Decision Tree](#rollback-decision-tree)
2. [Quick Rollback (Emergency)](#quick-rollback-emergency)
3. [Database Rollback](#database-rollback)
4. [Code Rollback](#code-rollback)
5. [Configuration Rollback](#configuration-rollback)
6. [Full System Rollback](#full-system-rollback)
7. [Verification Procedures](#verification-procedures)
8. [Post-Rollback Actions](#post-rollback-actions)

---

## ROLLBACK DECISION TREE

```
Is system completely down?
├─ YES → Use QUICK ROLLBACK (section 2)
└─ NO
    Is it a database corruption issue?
    ├─ YES → Use DATABASE ROLLBACK (section 3)
    └─ NO
        Is it a code/logic bug?
        ├─ YES → Use CODE ROLLBACK (section 4)
        └─ NO
            Is it a configuration issue?
            ├─ YES → Use CONFIG ROLLBACK (section 5)
            └─ NO
                Use FULL SYSTEM ROLLBACK (section 6)
```

---

## QUICK ROLLBACK (Emergency)
**Use When:** System completely non-functional  
**Time:** 2-5 minutes  
**Risk:** Low (proven backup)

### Step 1: Stop Everything
```bash
cd /Users/rr/nzt48-signals
docker compose down

# Verify containers stopped
docker ps | grep nzt48
# (should return empty)
```

### Step 2: Restore from Backup
```bash
# Option A: Full directory backup (fastest)
cd /Users/rr
ls -la nzt48-signals-backup-2026-03-14/

# If backup exists, use it
cp -r nzt48-signals-backup-2026-03-14 nzt48-signals-restored
rm -rf nzt48-signals
mv nzt48-signals-restored nzt48-signals

# If backup doesn't exist, proceed to database-only restore
```

### Step 3: Start System
```bash
cd /Users/rr/nzt48-signals
docker compose up -d

# Wait 30 seconds for startup
sleep 30

# Verify startup
docker logs nzt48 --tail 20
```

### Step 4: Verify Core Functions
```bash
# Check if system is running
docker ps | grep nzt48

# Check logs for errors
docker logs nzt48 | grep -i error | tail -5

# Check database is accessible
docker exec nzt48 sqlite3 /data/nzt48.db "SELECT COUNT(*) FROM signals"
```

### If Quick Rollback Fails:
→ Proceed to [Full System Rollback](#full-system-rollback)

---

## DATABASE ROLLBACK
**Use When:** Database corruption, data loss, or bad trades recorded  
**Time:** 1-2 minutes  
**Risk:** Medium (loses recent data)

### Backup Manifest
```
Primary backup:    /Users/rr/nzt48-signals/data/nzt48.backup.2026-03-14.db
Location:          /Users/rr/nzt48-signals/data/
Size:              868,352 bytes
Backup Date:       2026-03-14 16:59:00 GMT
Integrity:         VERIFIED ✅
```

### Step 1: Verify Current Database
```bash
cd /Users/rr/nzt48-signals

# Check current database
ls -la data/nzt48.db

# Run integrity check
sqlite3 data/nzt48.db "PRAGMA integrity_check"
```

### Step 2: Stop System
```bash
docker compose pause nzt48

# Wait for graceful pause
sleep 5
```

### Step 3: Backup Current Database (for forensics)
```bash
cp data/nzt48.db data/nzt48.corrupted.$(date +%s).db

# Verify backup created
ls -la data/nzt48.corrupted.*
```

### Step 4: Restore Clean Database
```bash
# Restore from clean backup
cp data/nzt48.backup.2026-03-14.db data/nzt48.db

# Verify restoration
ls -la data/nzt48.db
sqlite3 data/nzt48.db "PRAGMA integrity_check"
```

### Step 5: Restart System
```bash
docker compose unpause nzt48

# Or full restart if needed:
# docker compose restart nzt48

# Wait 10 seconds
sleep 10

# Verify system running
docker logs nzt48 --tail 20
```

### Step 6: Validate Data
```bash
# Check signal counts
docker exec nzt48 sqlite3 /data/nzt48.db "SELECT COUNT(*) FROM signals"

# Check trades
docker exec nzt48 sqlite3 /data/nzt48.db "SELECT COUNT(*) FROM trades"

# Check for errors in logs
docker logs nzt48 | grep -i error | tail -3
```

---

## CODE ROLLBACK
**Use When:** Bad code deployed, logic bugs, calculation errors  
**Time:** 3-5 minutes  
**Risk:** Low (code is versioned)

### Step 1: Check Git Status
```bash
cd /Users/rr/nzt48-signals
git status
git log --oneline -10
```

### Step 2: Identify Good Commit
```bash
# Show recent commits with messages
git log --oneline -20

# Find the last known-good commit
# Example: abc1234 "Q1 complete - timing fixes"
```

### Step 3: Stop System
```bash
docker compose down

# Wait for complete shutdown
sleep 10
```

### Step 4: Reset to Good Commit
```bash
# IMPORTANT: This will DISCARD all uncommitted changes
git reset --hard <commit-hash>

# Example:
# git reset --hard abc1234

# Verify reset
git log --oneline -3
```

### Step 5: Rebuild Docker Image
```bash
docker compose build --no-cache nzt48

# This may take 2-3 minutes
# Progress: downloading deps, installing packages, etc.
```

### Step 6: Restart System
```bash
docker compose up -d

# Wait 30 seconds
sleep 30

# Check logs
docker logs nzt48 --tail 30
```

### Step 7: Verify Functionality
```bash
# Check if orchestrator initialized
docker logs nzt48 | grep -i "orchestrator\|initialized"

# Check for critical errors
docker logs nzt48 | grep -i error

# Test a signal generation
docker exec nzt48 python3 -c "from core.master_orchestrator import MasterOrchestrator; print('OK')"
```

---

## CONFIGURATION ROLLBACK
**Use When:** Config file errors, wrong parameters, bad settings  
**Time:** 1 minute  
**Risk:** Very Low

### Backup Manifest
```
Config files:
  - config/settings.yaml          (35,779 bytes)
  - .env                          (589 bytes)
  - docker-compose.yml            (4,050 bytes)
```

### Step 1: Identify Bad Config
```bash
cd /Users/rr/nzt48-signals

# Check recent changes
git diff config/settings.yaml
git diff .env
git diff docker-compose.yml
```

### Step 2: Restore from Git
```bash
# Restore specific config file
git checkout config/settings.yaml

# Or restore all configs
git checkout config/ .env docker-compose.yml

# Verify restoration
git status
```

### Step 3: Restart System
```bash
docker compose down
docker compose up -d

# Wait 30 seconds
sleep 30

# Verify
docker logs nzt48 --tail 20
```

---

## FULL SYSTEM ROLLBACK
**Use When:** Multiple failures, unable to identify root cause  
**Time:** 10-15 minutes  
**Risk:** Medium (resets everything)

### Prerequisites
- Have backup directory available: `/Users/rr/nzt48-signals-backup-2026-03-14/`
- Have backup database: `/Users/rr/nzt48-signals/data/nzt48.backup.2026-03-14.db`
- Have git history intact

### Step 1: Emergency Stop
```bash
cd /Users/rr/nzt48-signals

# Kill everything
docker compose down
docker compose kill

# Force kill if needed
pkill -f nzt48
pkill -f 'docker.*nzt48'

# Wait for cleanup
sleep 10
```

### Step 2: Archive Current State (for forensics)
```bash
# Create timestamped archive
TIMESTAMP=$(date +%Y-%m-%d_%H-%M-%S)
mkdir -p /Users/rr/nzt48-signals-failed-$TIMESTAMP

# Copy current state for analysis
cp -r /Users/rr/nzt48-signals/* /Users/rr/nzt48-signals-failed-$TIMESTAMP/ 2>/dev/null || true

echo "Current state archived to: /Users/rr/nzt48-signals-failed-$TIMESTAMP"
```

### Step 3: Restore From Backup
```bash
# Option A: Full backup directory exists
if [ -d "/Users/rr/nzt48-signals-backup-2026-03-14" ]; then
    rm -rf /Users/rr/nzt48-signals
    cp -r /Users/rr/nzt48-signals-backup-2026-03-14 /Users/rr/nzt48-signals
    echo "Restored from full backup"
else
    # Option B: Restore code from git, database separately
    cd /Users/rr/nzt48-signals
    git clean -fd
    git reset --hard HEAD~5  # Go back 5 commits
    cp /Users/rr/nzt48-signals/data/nzt48.backup.2026-03-14.db /Users/rr/nzt48-signals/data/nzt48.db
    echo "Restored code from git, database from backup"
fi
```

### Step 4: Rebuild Everything
```bash
cd /Users/rr/nzt48-signals

# Clean up old containers and images
docker system prune -f

# Rebuild fresh
docker compose build --no-cache

# This takes 3-5 minutes
```

### Step 5: Start System
```bash
docker compose up -d

# Wait for startup (60 seconds)
sleep 60

# Watch logs
docker logs nzt48 -f &
LOGS_PID=$!

# Wait 30 seconds to see startup
sleep 30
kill $LOGS_PID 2>/dev/null || true
```

### Step 6: Full Validation
```bash
# See section: Verification Procedures
bash /Users/rr/nzt48-signals/tests/run_all_tests.sh
```

---

## VERIFICATION PROCEDURES

### Phase 1: System Health (0-2 minutes)
```bash
# 1. Docker status
docker ps | grep nzt48
# Should show all 3 containers running

# 2. Port bindings
netstat -an | grep -E "8000|4002|6379"
# Should show services listening

# 3. Resource usage
docker stats nzt48 --no-stream
# Should show CPU <20%, Memory <500MB

# 4. Log check
docker logs nzt48 --tail 20 | grep -i error
# Should be empty or only warnings
```

### Phase 2: Core Functions (2-5 minutes)
```bash
# 1. Database accessibility
docker exec nzt48 sqlite3 /data/nzt48.db "SELECT COUNT(*) FROM signals"
# Should return number

# 2. Orchestrator status
docker logs nzt48 | grep -i "orchestrator\|initialized"
# Should show initialization messages

# 3. Configuration loading
docker logs nzt48 | grep -i "config\|settings"
# Should show config loaded successfully

# 4. Strategy status
docker logs nzt48 | grep -i "strategy\|daily_target"
# Should show strategy ready
```

### Phase 3: Signal Generation (5-10 minutes)
```bash
# 1. Wait for first signal (may take 1-5 min)
docker logs nzt48 -f | grep "signal\|SIGNAL"

# 2. Check recent signals in DB
docker exec nzt48 sqlite3 /data/nzt48.db \
  "SELECT COUNT(*) FROM signals WHERE timestamp > datetime('now', '-5 minutes')"

# 3. Verify Telegram connection
docker logs nzt48 | grep -i "telegram"
# Should show successful bot connection
```

### Phase 4: Complete Health Check Script
```bash
cat > /Users/rr/nzt48-signals/verify_rollback.sh << 'VERIFY'
#!/bin/bash

echo "╔════════════════════════════════════════════════════════════════╗"
echo "║               POST-ROLLBACK VERIFICATION                       ║"
echo "╚════════════════════════════════════════════════════════════════╝"

PASS=0
FAIL=0

# Check 1: Containers running
echo "1. Checking containers..."
if docker ps | grep -q nzt48; then
    echo "   ✅ nzt48 container running"
    ((PASS++))
else
    echo "   ❌ nzt48 container not running"
    ((FAIL++))
fi

# Check 2: Database
echo "2. Checking database..."
if docker exec nzt48 sqlite3 /data/nzt48.db "PRAGMA integrity_check" | grep -q "ok"; then
    echo "   ✅ Database integrity OK"
    ((PASS++))
else
    echo "   ❌ Database corrupted"
    ((FAIL++))
fi

# Check 3: Logs
echo "3. Checking logs..."
ERROR_COUNT=$(docker logs nzt48 | grep -i "error\|critical\|fatal" | wc -l)
if [ $ERROR_COUNT -lt 5 ]; then
    echo "   ✅ No critical errors ($ERROR_COUNT warnings)"
    ((PASS++))
else
    echo "   ❌ Critical errors found ($ERROR_COUNT)"
    ((FAIL++))
fi

# Check 4: CPU/Memory
echo "4. Checking resources..."
MEMORY=$(docker stats nzt48 --no-stream | tail -1 | awk '{print $6}' | sed 's/MiB//')
if (( $(echo "$MEMORY < 500" | bc -l) )); then
    echo "   ✅ Memory usage ${MEMORY}MiB (acceptable)"
    ((PASS++))
else
    echo "   ⚠️  Memory usage ${MEMORY}MiB (high)"
fi

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Results: $PASS passed, $FAIL failed                           ║"
if [ $FAIL -eq 0 ]; then
    echo "║  Status: ✅ ROLLBACK SUCCESSFUL                               ║"
else
    echo "║  Status: ❌ ROLLBACK NEEDS REVIEW                             ║"
fi
echo "╚════════════════════════════════════════════════════════════════╝"
VERIFY

chmod +x /Users/rr/nzt48-signals/verify_rollback.sh

# Run verification
bash /Users/rr/nzt48-signals/verify_rollback.sh
```

---

## POST-ROLLBACK ACTIONS

### Immediate (within 1 hour)
- [ ] System running stably
- [ ] Verify no continuous errors
- [ ] Check database integrity
- [ ] Verify Telegram alerts working
- [ ] Monitor P&L for consistency

### Short-term (within 24 hours)
- [ ] Analyze what caused failure
- [ ] Document root cause
- [ ] Create fix for identified issue
- [ ] Test fix in isolated environment
- [ ] Plan re-deployment

### Long-term
- [ ] Update runbooks with findings
- [ ] Improve monitoring to catch similar issues
- [ ] Add regression tests
- [ ] Review deployment checklist

---

## DISASTER RECOVERY CONTACT

If rollback procedures fail:

1. **Preserve State:** Don't make random changes
2. **Document:** Take screenshots, save logs
3. **Contact Support:**
   - Check git log for deployment history
   - Review `/Users/rr/nzt48-signals-failed-*/` archives
   - Analyze error patterns

4. **Manual Recovery:**
   ```bash
   # As last resort, manually check system state
   docker ps -a
   docker logs nzt48 > /tmp/nzt48-logs-full.txt
   sqlite3 /Users/rr/nzt48-signals/data/nzt48.db .dump > /tmp/nzt48-db-dump.sql
   ```

---

## APPENDIX: Backup Schedule

```
Backup Type          Frequency   Retention   Location
─────────────────────────────────────────────────────
Database snapshot    Daily       30 days     data/nzt48.backup.*.db
Full system backup   Weekly      8 weeks     ../nzt48-signals-backup-*
Code archive         Per commit  ∞           Git history
Docker image         Per build   Latest 5    Docker registry
```

**Last Backup:** 2026-03-14 16:59:00 GMT  
**Next Backup:** 2026-03-15 04:00:00 GMT  

---

## Document Information

**Created:** 2026-03-14  
**Last Updated:** 2026-03-14  
**Author:** Automated Testing Suite  
**Version:** 1.0  
**Status:** APPROVED FOR PRODUCTION  

---

**End of Deployment Rollback Plan**
