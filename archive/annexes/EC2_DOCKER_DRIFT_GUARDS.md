# EC2-Docker Drift Guards

| Field          | Value                    |
|----------------|--------------------------|
| Document ID    | NZT48-ANNEX-DDG-001      |
| Version        | 1.0                      |
| Date           | 2026-02-27               |
| Status         | **BINDING**              |
| Classification | Internal / Operations    |

---

## 1. OBJECTIVE

Prevent "deployed but not in container" drift. Code on the EC2 host filesystem **MUST** match code inside the Docker container at all times. Any divergence between host and container constitutes a **deployment integrity violation** and must be detected, alerted, and resolved before the engine processes live signals.

---

## 2. CANONICAL DEPLOY PIPELINE

Every deployment follows this exact sequence. **No steps may be skipped or reordered.**

| Step | Command / Action                                                                                                     | Purpose                        | Verification                  |
|------|----------------------------------------------------------------------------------------------------------------------|--------------------------------|-------------------------------|
| 1    | Edit code locally on Mac (`/Users/rr/nzt48-signals/`)                                                               | Development                    | Local tests pass              |
| 2    | `rsync -avz --rsh='ssh -i /Users/rr/.ssh/nzt48-key.pem' --exclude='.git/' --exclude='venv/' --exclude='node_modules/' --exclude='__pycache__/' --exclude='data/' --exclude='.env' /Users/rr/nzt48-signals/ ubuntu@100.55.69.28:/home/ubuntu/nzt48-signals/` | Sync to EC2 host               | rsync exit code 0             |
| 3    | `ssh -i /Users/rr/.ssh/nzt48-key.pem ubuntu@100.55.69.28 'cd /home/ubuntu/nzt48-signals && docker compose build nzt48'` | Bake code into Docker image    | Build completes without error |
| 4    | `ssh -i /Users/rr/.ssh/nzt48-key.pem ubuntu@100.55.69.28 'cd /home/ubuntu/nzt48-signals && docker compose up -d nzt48'` | Start container from new image | Container status: running     |
| 5    | Run container parity check (see Section 3)                                                                           | Verify host = container        | All checksums match           |
| 6    | `ssh -i /Users/rr/.ssh/nzt48-key.pem ubuntu@100.55.69.28 'curl -s http://localhost:8000/api/health'`                | Health check                   | HTTP 200 + JSON healthy       |

### Deploy Pipeline Flowchart

```
[Mac: Edit Code]
       |
       v
[Mac: rsync to EC2 Host]
       |
       v
[EC2: docker compose build nzt48]
       |
       v
[EC2: docker compose up -d nzt48]
       |
       v
[EC2: Parity Check]---FAIL---> [STOP. Do NOT proceed. Fix drift first.]
       |
      PASS
       |
       v
[EC2: curl health check]---FAIL---> [STOP. Check logs. Rollback if needed.]
       |
      PASS
       |
       v
[DEPLOY COMPLETE. Tag image as LKG.]
```

---

## 3. CONTAINER PARITY CHECK

The parity check compares MD5 checksums of critical files between the EC2 host filesystem and the running Docker container. **Every deployment must pass this check before the engine is considered live.**

### 3.1 Critical Files List

| Category       | Files                                                                                      |
|----------------|--------------------------------------------------------------------------------------------|
| Core           | `main.py`, `requirements.txt`                                                             |
| Config         | `config/settings.yaml` (bind-mounted; verify mount is active)                              |
| Engine         | `signal_engine/engine.py`, all files in `signal_engine/`                                   |
| Strategies     | All files in `strategies/`                                                                 |
| UK ISA         | All files in `uk_isa/`                                                                     |
| Delivery       | `delivery/telegram_bot.py`, all files in `delivery/`                                       |
| Learning       | All files in `learning/`                                                                   |

### 3.2 Parity Check Commands

**Single file check:**

```bash
# Host side
md5sum /home/ubuntu/nzt48-signals/main.py

# Container side
docker exec nzt48 md5sum /app/main.py

# Compare: hashes MUST be identical
```

**Batch check script (`scripts/parity_check.sh`):**

```bash
#!/bin/bash
set -euo pipefail

CONTAINER="nzt48"
HOST_BASE="/home/ubuntu/nzt48-signals"
CONTAINER_BASE="/app"
FAIL=0

# Core files
CRITICAL_FILES=(
    "main.py"
    "requirements.txt"
    "signal_engine/engine.py"
    "delivery/telegram_bot.py"
)

# Directories to check recursively
CRITICAL_DIRS=(
    "strategies"
    "uk_isa"
    "signal_engine"
    "delivery"
    "learning"
)

echo "=== NZT-48 Container Parity Check ==="
echo "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""

# Check individual critical files
for FILE in "${CRITICAL_FILES[@]}"; do
    HOST_HASH=$(md5sum "${HOST_BASE}/${FILE}" 2>/dev/null | awk '{print $1}')
    CONTAINER_HASH=$(docker exec ${CONTAINER} md5sum "${CONTAINER_BASE}/${FILE}" 2>/dev/null | awk '{print $1}')

    if [ "${HOST_HASH}" = "${CONTAINER_HASH}" ]; then
        echo "PASS  ${FILE}  (${HOST_HASH})"
    else
        echo "FAIL  ${FILE}  host=${HOST_HASH}  container=${CONTAINER_HASH}"
        FAIL=1
    fi
done

# Check directories recursively
for DIR in "${CRITICAL_DIRS[@]}"; do
    while IFS= read -r -d '' FILE; do
        REL_PATH="${FILE#${HOST_BASE}/}"
        HOST_HASH=$(md5sum "${FILE}" 2>/dev/null | awk '{print $1}')
        CONTAINER_HASH=$(docker exec ${CONTAINER} md5sum "${CONTAINER_BASE}/${REL_PATH}" 2>/dev/null | awk '{print $1}')

        if [ "${HOST_HASH}" = "${CONTAINER_HASH}" ]; then
            echo "PASS  ${REL_PATH}"
        else
            echo "FAIL  ${REL_PATH}  host=${HOST_HASH}  container=${CONTAINER_HASH}"
            FAIL=1
        fi
    done < <(find "${HOST_BASE}/${DIR}" -name "*.py" -print0 2>/dev/null)
done

echo ""
if [ ${FAIL} -eq 0 ]; then
    echo "RESULT: ALL CHECKS PASSED"
    exit 0
else
    echo "RESULT: PARITY FAILURE DETECTED"
    echo "ACTION: Do NOT route traffic to this container. Rebuild required."
    exit 1
fi
```

### 3.3 Pass / Fail Criteria

| Outcome | Condition                           | Action Required                                      |
|---------|-------------------------------------|------------------------------------------------------|
| PASS    | All checksums match                 | Deploy is valid. Tag image as LKG.                   |
| FAIL    | One or more checksums differ        | **BLOCK** deploy. Re-run `docker compose build`.     |
| ERROR   | File missing on host or container   | **BLOCK** deploy. Investigate missing file.          |

---

## 4. "NO HOST-ONLY PRODUCTION CODE" POLICY

| Rule | Description                                                                                       |
|------|---------------------------------------------------------------------------------------------------|
| R-1  | **ALL** production Python code must exist inside the Docker container at `/app/`                  |
| R-2  | Config files (`settings.yaml`, `.env`) may be bind-mounted into the container (allowed exception) |
| R-3  | Any `.py` file edited on the EC2 host **MUST** be followed by `docker compose build`             |
| R-4  | Editing files **inside** a running container via `docker exec` is **FORBIDDEN**                   |
| R-5  | Ad-hoc scripts in `/home/ubuntu/` that interact with the engine are **FORBIDDEN**                 |
| R-6  | Cron jobs on the host that call Python directly (not via `docker exec`) are **FORBIDDEN**         |

### Why R-4 Is Forbidden

Changes made inside a running container via `docker exec bash` are:
- Lost on container restart
- Lost on `docker compose up -d` (recreates container from image)
- Invisible to parity checks (host file unchanged, container file changed)
- Impossible to reproduce or audit

### Allowed Exception: Bind Mounts

The following bind mounts are permitted and expected:

```yaml
# docker-compose.yml
volumes:
  - ./config/settings.yaml:/app/config/settings.yaml:ro
  - ./data:/app/data
  - ./.env:/app/.env:ro
```

These files are **not baked into the image** and changes take effect on container restart without rebuild.

---

## 5. DRIFT DETECTION

### 5.1 Startup Code Hash

On every engine startup, the engine logs its own code identity:

```python
import hashlib
from pathlib import Path

def compute_code_hash() -> str:
    """SHA-256 of main.py first 1000 bytes as identity fingerprint."""
    content = Path("/app/main.py").read_bytes()[:1000]
    return hashlib.sha256(content).hexdigest()[:16]

# On startup
code_hash = compute_code_hash()
logger.info(f"ENGINE_START code_hash={code_hash}")
```

| Condition                                     | Action                                              |
|-----------------------------------------------|-----------------------------------------------------|
| `code_hash` same as previous run              | Normal startup. No action.                          |
| `code_hash` differs from previous run         | Log `WARNING: CODE_HASH_CHANGED`. Write to `system_state.json`. |
| `code_hash` cannot be computed (file missing) | Log `CRITICAL: MAIN_PY_MISSING`. Engine refuses to start. |

### 5.2 Scheduled Daily Parity Check

A daily parity check runs automatically to detect silent drift:

```python
# In APScheduler job definitions (main.py)
scheduler.add_job(
    run_parity_check,
    trigger="cron",
    hour=5,         # 05:00 UTC daily (before UK market open)
    minute=0,
    id="daily_parity_check",
    misfire_grace_time=3600
)
```

| Result | Action                                                              |
|--------|---------------------------------------------------------------------|
| PASS   | Log `INFO: DAILY_PARITY_PASS`. No further action.                  |
| FAIL   | Log `CRITICAL: DAILY_PARITY_FAIL`. Send Telegram alert. Write to `system_state.json` with `drift_detected: true`. |

### 5.3 Drift State in system_state.json

```json
{
  "drift_detection": {
    "code_hash": "a3f7c291e4b2f1a0",
    "last_parity_check": "2026-02-27T05:00:00Z",
    "parity_status": "PASS",
    "drift_detected": false,
    "mismatched_files": []
  }
}
```

---

## 6. RSYNC SAFETY

### 6.1 Canonical rsync Command

```bash
rsync -avz \
  --rsh='ssh -i /Users/rr/.ssh/nzt48-key.pem' \
  --exclude='.git/' \
  --exclude='venv/' \
  --exclude='node_modules/' \
  --exclude='__pycache__/' \
  --exclude='data/' \
  --exclude='.env' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  /Users/rr/nzt48-signals/ \
  ubuntu@100.55.69.28:/home/ubuntu/nzt48-signals/
```

### 6.2 rsync Rules

| Rule    | Description                                                                                      |
|---------|--------------------------------------------------------------------------------------------------|
| RS-1    | **MUST** use `--rsh` flag (not `-e`) for Mac openrsync compatibility                             |
| RS-2    | **MUST** exclude `.git/`, `venv/`, `node_modules/`, `__pycache__/`, `data/`, `.env`              |
| RS-3    | **MUST** include trailing slash on source path (`nzt48-signals/`) to sync contents, not directory |
| RS-4    | Pre-deploy: verify EC2 `config/` directory has no Mac junk (`.DS_Store`, `._*` files)            |
| RS-5    | Post-deploy: **always** run parity check (Section 3)                                             |
| RS-6    | Never rsync while container is mid-build (`docker compose build` in progress)                    |

### 6.3 Pre-Deploy Cleanup

```bash
# Run on EC2 before rsync to remove Mac artifacts
ssh -i /Users/rr/.ssh/nzt48-key.pem ubuntu@100.55.69.28 \
  'find /home/ubuntu/nzt48-signals -name ".DS_Store" -delete && \
   find /home/ubuntu/nzt48-signals -name "._*" -delete'
```

---

## 7. ROLLBACK

### 7.1 Tag Every Successful Deployment

After parity check PASSES and health check returns 200:

```bash
# On EC2
docker tag nzt48 nzt48:lkg-$(date +%Y%m%d-%H%M)
```

This creates a **Last Known Good (LKG)** image tag that can be restored instantly.

### 7.2 List Available LKG Images

```bash
docker images nzt48 --format "table {{.Tag}}\t{{.CreatedAt}}\t{{.Size}}" | grep lkg
```

### 7.3 Rollback Procedure

```bash
# Step 1: Stop current container
docker compose down

# Step 2: Retag the LKG image as latest
docker tag nzt48:lkg-YYYYMMDD-HHMM nzt48:latest

# Step 3: Bring container back up from LKG image
docker compose up -d

# Step 4: Verify health
curl -s http://localhost:8000/api/health
```

### 7.4 Rollback Decision Matrix

| Condition                                      | Action                              | Urgency  |
|------------------------------------------------|-------------------------------------|----------|
| Health check fails after deploy                | Rollback to previous LKG           | **P0**   |
| Parity check fails after deploy                | Rebuild (do not rollback)           | **P0**   |
| Engine crashes within 5 minutes of deploy      | Rollback to previous LKG           | **P0**   |
| Subtle data issue detected hours after deploy  | Investigate first; rollback if P&L risk | **P1** |
| Feature not working but engine stable          | Fix forward (no rollback needed)    | **P2**   |

---

## 8. ACCEPTANCE TESTS

### T-DRIFT-001: Clean Deploy Parity

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify a clean deploy produces identical host and container code      |
| **Procedure**  | 1. rsync code from Mac to EC2                                        |
|                | 2. `docker compose build nzt48`                                      |
|                | 3. `docker compose up -d nzt48`                                      |
|                | 4. Run `parity_check.sh`                                            |
| **Pass**       | All files report PASS; exit code 0                                   |
| **Fail**       | Any file reports FAIL or ERROR                                       |

### T-DRIFT-002: Host-Only Edit Detection

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify parity check detects host-side edits not baked into container  |
| **Procedure**  | 1. Complete a clean deploy (T-DRIFT-001 passes)                      |
|                | 2. Edit `main.py` on EC2 host (add a comment)                       |
|                | 3. Do NOT rebuild container                                         |
|                | 4. Run `parity_check.sh`                                            |
| **Pass**       | `main.py` reports FAIL; script exits with code 1                     |
| **Fail**       | Script reports PASS despite host/container mismatch                  |

### T-DRIFT-003: Container-Side Edit Detection

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify that container-side edits are lost on restart (proving R-4)    |
| **Procedure**  | 1. `docker exec nzt48 bash -c "echo '# hack' >> /app/main.py"`      |
|                | 2. Verify parity check now FAILS (container differs from host)       |
|                | 3. `docker compose restart nzt48`                                    |
|                | 4. Run parity check again                                           |
| **Pass**       | Step 2: FAIL. Step 4: PASS (container reset to image state)          |
| **Fail**       | Step 4: FAIL (container-side edit persisted across restart)           |

### T-DRIFT-004: Startup Code Hash Logging

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify engine logs `code_hash` on every startup                      |
| **Procedure**  | 1. Start engine                                                      |
|                | 2. Check logs: `docker logs nzt48 --tail 50`                        |
|                | 3. Search for `ENGINE_START code_hash=`                              |
| **Pass**       | Log line present with 16-character hex hash                          |
| **Fail**       | Log line missing or hash is empty/malformed                          |

### T-DRIFT-005: Rollback Restores Previous Version

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify rollback to LKG image restores previous code version          |
| **Procedure**  | 1. Deploy version A; tag as LKG                                     |
|                | 2. Deploy version B (modify `main.py`)                              |
|                | 3. Verify parity check shows version B code                         |
|                | 4. Execute rollback to version A LKG tag                            |
|                | 5. Verify `code_hash` matches version A                             |
| **Pass**       | After rollback, engine runs version A code; health check passes      |
| **Fail**       | After rollback, engine still runs version B code                     |

### T-DRIFT-006: rsync Exclusion Verification

| Field          | Value                                                                 |
|----------------|-----------------------------------------------------------------------|
| **Objective**  | Verify rsync excludes sensitive and unnecessary files                 |
| **Procedure**  | 1. Create files on Mac: `.git/test`, `venv/lib/test.py`, `data/test.csv`, `.env.local` |
|                | 2. Run rsync to EC2                                                  |
|                | 3. Check EC2 for excluded files                                     |
| **Pass**       | None of the excluded files exist on EC2 host                         |
| **Fail**       | Any excluded file was transferred to EC2                             |

---

## APPENDIX A: Quick Reference Commands

| Action                        | Command                                                                                    |
|-------------------------------|--------------------------------------------------------------------------------------------|
| Deploy (full pipeline)        | Steps 1-6 in Section 2                                                                     |
| Parity check                  | `bash /home/ubuntu/nzt48-signals/scripts/parity_check.sh`                                  |
| Health check                  | `curl -s http://localhost:8000/api/health \| python3 -m json.tool`                         |
| View logs                     | `docker logs nzt48 --tail 100 -f`                                                          |
| Tag as LKG                    | `docker tag nzt48 nzt48:lkg-$(date +%Y%m%d-%H%M)`                                         |
| List LKG images               | `docker images nzt48 --format "table {{.Tag}}\t{{.CreatedAt}}" \| grep lkg`                |
| Rollback                      | `docker compose down && docker tag nzt48:lkg-PREV nzt48:latest && docker compose up -d`    |
| Restart (no rebuild)          | `docker compose restart nzt48`                                                             |
| Rebuild + restart             | `docker compose build nzt48 && docker compose up -d nzt48`                                 |
| SSH to EC2                    | `ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28`                                          |
| Exec into container           | `docker exec -it nzt48 bash`                                                               |
| Clean Mac artifacts on EC2    | `find /home/ubuntu/nzt48-signals -name ".DS_Store" -delete`                                |

---

## APPENDIX B: Implementation Priority

| Priority | Component                        | Effort   | Impact   |
|----------|----------------------------------|----------|----------|
| P0       | Parity check script              | Low      | Critical |
| P0       | Startup code hash logging        | Low      | Critical |
| P1       | Daily automated parity check     | Low      | High     |
| P1       | LKG tagging in deploy script     | Low      | High     |
| P2       | Drift state in system_state.json | Medium   | Medium   |
| P2       | Telegram drift alerts            | Medium   | Medium   |

---

*End of Document NZT48-ANNEX-DDG-001*
