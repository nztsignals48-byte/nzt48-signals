# NZT-48 EC2/Docker Release Engineering

| Field           | Value                          |
|-----------------|--------------------------------|
| Document ID     | NZT48-ANNEX-RE-001             |
| Version         | 1.0                            |
| Date            | 2026-02-27                     |
| Status          | **BINDING**                    |
| Classification  | Internal -- Operations         |
| Related         | EC2_DOCKER_DRIFT_GUARDS.md (NZT48-ANNEX-DDG-001) -- drift detection specification |

---

## 1. PURPOSE

EC2_DOCKER_DRIFT_GUARDS.md defines parity checks and drift detection between host and container. This document covers the **full release engineering pipeline**: from local edit to production-verified deployment, with image tagging, reproducible builds, and LKG management.

This is the authoritative reference for how code gets from a developer's machine into production.

---

## 2. BUILD PIPELINE

Every release follows a 6-step pipeline. **No steps may be skipped or reordered.**

### Step 1: Edit (Local Mac)

| Detail | Value |
|--------|-------|
| Location | `/Users/rr/nzt48-signals/` |
| Verification | Local tests pass; `flutter analyze` (if applicable) clean |
| Artefact | Modified source files |

### Step 2: Rsync to EC2

| Detail | Value |
|--------|-------|
| Command | `rsync -avz --rsh='ssh -i /Users/rr/.ssh/nzt48-key.pem' --exclude='.git/' --exclude='venv/' --exclude='node_modules/' --exclude='__pycache__/' --exclude='data/' --exclude='.env' --exclude='*.pyc' --exclude='.DS_Store' /Users/rr/nzt48-signals/ ubuntu@54.242.32.11:/home/ubuntu/nzt48-signals/` |
| Verification | rsync exit code 0; file list reviewed |
| Exclusions | `.git/`, `venv/`, `node_modules/`, `__pycache__/`, `data/`, `.env`, `*.pyc`, `.DS_Store` |
| Rule | MUST use `--rsh` flag (not `-e`) for Mac openrsync compatibility |
| Rule | MUST include trailing slash on source path |
| Rule | NEVER rsync while `docker compose build` is in progress |

### Step 3: Build Docker Image

| Detail | Value |
|--------|-------|
| Command | `ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 'cd /home/ubuntu/nzt48-signals && docker compose build nzt48'` |
| Verification | Build completes without error; image created |
| Duration | Typically 60-120 seconds |
| Caching | Docker layer caching active; only changed layers rebuilt |

### Step 4: Start Container

| Detail | Value |
|--------|-------|
| Command | `ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 'cd /home/ubuntu/nzt48-signals && docker compose up -d nzt48'` |
| Verification | `docker ps` shows `nzt48` container with status `Up` |
| Startup | Engine enters 5-minute quiet mode (see OUTPUT_POLICY_SPEC.md Rule 7) |

### Step 5: Parity Check

| Detail | Value |
|--------|-------|
| Command | `ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 'cd /home/ubuntu/nzt48-signals && bash scripts/parity_check.sh'` |
| Verification | All critical file checksums match between host and container |
| On Failure | **STOP. Do NOT proceed.** Re-run `docker compose build`. If persistent, investigate. |

### Step 6: Health Check

| Detail | Value |
|--------|-------|
| Command | `ssh -i ~/.ssh/nzt48-key.pem ubuntu@54.242.32.11 'curl -sf http://localhost:8000/api/health'` |
| Verification | HTTP 200 with `{"status": "ok"}` |
| On Failure | Check logs: `docker logs nzt48 --tail 50`. Rollback to LKG if unresolvable within 5 minutes. |

### Pipeline Flowchart

```
[1. Edit locally] --> [2. rsync to EC2] --> [3. docker compose build]
                                                    |
                                                    v
[6. Health check] <-- [5. Parity check] <-- [4. docker compose up -d]
       |                    |
      PASS                FAIL --> STOP, rebuild
       |
       v
[Tag as LKG. Deploy complete.]
```

---

## 3. IMAGE TAGGING STRATEGY

### 3.1 Tag Format

```
nzt48:YYYYMMDD-HHMM-<git-sha-7>
```

Examples:
- `nzt48:20260227-0900-abc1234`
- `nzt48:20260228-1430-def5678`

### 3.2 Tag Components

| Component | Source | Purpose |
|-----------|--------|---------|
| `YYYYMMDD` | Deploy date (UTC) | Date-based ordering |
| `HHMM` | Deploy time (UTC) | Multiple deploys per day |
| `git-sha-7` | First 7 characters of HEAD commit | Code traceability |

### 3.3 Tagging Commands

```bash
# After successful deploy (Step 6 PASS)
GIT_SHA=$(git rev-parse --short=7 HEAD)
TAG="nzt48:$(date -u +%Y%m%d-%H%M)-${GIT_SHA}"
docker tag nzt48:latest "${TAG}"
echo "Tagged: ${TAG}"
```

### 3.4 Tag Retention

| Tag Type | Retention | Purpose |
|----------|----------|---------|
| `nzt48:latest` | Always (overwritten each build) | Current running image |
| `nzt48:YYYYMMDD-HHMM-*` | 30 days | Rollback targets |
| `nzt48:lkg-*` | Until superseded by next LKG + 7 days grace | Last Known Good |

Cleanup command (run monthly):
```bash
# Remove deploy tags older than 30 days (keep LKG tags longer)
docker images nzt48 --format '{{.Tag}} {{.CreatedAt}}' | \
  grep -v lkg | \
  awk -v cutoff="$(date -d '30 days ago' +%Y-%m-%d)" '$2 < cutoff {print "nzt48:" $1}' | \
  xargs -r docker rmi
```

---

## 4. PARITY CHECKS

Parity checks compare MD5 checksums of critical files between the EC2 host filesystem and the running Docker container. Full specification in EC2_DOCKER_DRIFT_GUARDS.md Section 3.

### 4.1 Critical File Categories

| Category | Files | Priority |
|----------|-------|---------|
| Core | `main.py`, `requirements.txt` | P0 -- any mismatch blocks deploy |
| Config | `config/settings.yaml` (bind-mounted) | Verify mount is active |
| Engine | All `.py` files in `signal_engine/` | P0 |
| Strategies | All `.py` files in `strategies/` | P0 |
| UK ISA | All `.py` files in `uk_isa/` | P0 |
| Delivery | All `.py` files in `delivery/` | P0 |
| Learning | All `.py` files in `learning/` | P1 |

### 4.2 Scheduled Parity

In addition to deploy-time checks, a daily parity check runs at 05:00 UTC (before UK market open) via APScheduler. Results written to `artifacts/checksum_comparison.json`. Failures trigger Telegram alert.

---

## 5. NO HOST-ONLY CODE POLICY

| Rule | Statement |
|------|-----------|
| R-1 | ALL production Python code MUST exist inside the Docker container at `/app/` |
| R-2 | Config files (`settings.yaml`, `.env`) may be bind-mounted (allowed exception) |
| R-3 | Any `.py` file edited on EC2 host MUST be followed by `docker compose build` |
| R-4 | Editing files inside a running container via `docker exec` is **FORBIDDEN** |
| R-5 | Ad-hoc scripts on the host that interact with the engine are **FORBIDDEN** |
| R-6 | Cron jobs on the host that call Python directly (not via `docker exec`) are **FORBIDDEN** |

**Why R-4 is forbidden:** Changes made inside a running container via `docker exec bash` are lost on container restart, lost on `docker compose up -d`, invisible to parity checks, and impossible to reproduce or audit.

---

## 6. REPRODUCIBLE BUILDS

### 6.1 Dockerfile Determinism

The Dockerfile MUST produce identical images given identical inputs:

| Requirement | Implementation |
|-------------|---------------|
| Pinned base image | `FROM python:3.11.7-slim` (exact tag, not `python:3.11` or `python:latest`) |
| Pinned dependencies | `requirements.txt` with exact versions (`pandas==2.1.4`, not `pandas>=2.0`) |
| No network calls during build | All dependencies downloaded via `pip install --no-cache-dir -r requirements.txt` |
| Deterministic layer ordering | COPY requirements.txt first (for layer caching), then COPY source code |
| No timestamps in image | Avoid `RUN date` or similar commands that change per build |
| Multi-stage build | Stage 1: install dependencies. Stage 2: copy only runtime files. Reduces image size and attack surface |

### 6.2 Multi-Stage Build Structure

```dockerfile
# Stage 1: Builder
FROM python:3.11.7-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.11.7-slim AS runtime
WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .
CMD ["python", "main.py"]
```

### 6.3 Dependency Lock

- `requirements.txt` is the single source of truth for Python dependencies.
- Every dependency specifies an exact version (`==`).
- Transitive dependencies are included (use `pip freeze > requirements.txt` after any change).
- The `requirements.txt` file is included in parity checks.

---

## 7. LKG TAGGING

### 7.1 When to Tag LKG

An image is tagged LKG when:

1. Parity check PASSES (Step 5).
2. Health check returns HTTP 200 (Step 6).
3. Engine runs for 30 minutes with zero ERROR-level log entries.
4. At least 1 successful scan cycle completes.

### 7.2 LKG Tag Format

```
nzt48:lkg-YYYYMMDD-HHMM
```

### 7.3 LKG Tagging Command

```bash
LKG_TAG="lkg-$(date -u +%Y%m%d-%H%M)"
SETTINGS_HASH=$(sha256sum config/settings.yaml | cut -d' ' -f1)

# Tag the Docker image
docker tag nzt48:latest "nzt48:${LKG_TAG}"

# Tag in git
git tag -a "${LKG_TAG}" -m "LKG: settings=${SETTINGS_HASH}"

# Archive current good state
mkdir -p data/lkg
cp artifacts/system_state.json "data/lkg/system_state_${LKG_TAG}.json"
cp config/settings.yaml "data/lkg/settings_${LKG_TAG}.yaml"

echo "LKG tagged: ${LKG_TAG} (settings hash: ${SETTINGS_HASH})"
```

### 7.4 LKG Catalog

Maintained at `data/lkg/LKG_CATALOG.txt`:

```
# LKG Catalog -- NZT-48
# Format: tag  commit  status  description
lkg-20260227-0700  abc123f  OK  "Pre-Master-Plan baseline"
lkg-20260227-1400  def456a  OK  "Post-W0 deployment verified"
```

---

## 8. CI/CD FUTURE STATE

The current deployment pipeline is manual (rsync + build on EC2). The target architecture uses GitHub Actions for automated CI/CD:

### 8.1 Target Pipeline

```
[Push to main] --> [GitHub Actions]
                         |
                    [Run tests]
                         |
                    [Build Docker image]
                         |
                    [Push to ECR]
                         |
                    [Deploy to EC2]
                         |
                    [Parity check]
                         |
                    [Health check]
                         |
                    [Tag LKG if successful]
```

### 8.2 Prerequisites for CI/CD

| Prerequisite | Status | Notes |
|-------------|--------|-------|
| Test suite passing | NOT READY | Tests defined in TEST_PLAN.md but not all implemented |
| GitHub Actions workflow | NOT STARTED | `.github/workflows/deploy.yml` |
| AWS ECR repository | NOT STARTED | Container registry for versioned images |
| EC2 deploy agent | NOT STARTED | Script on EC2 to pull from ECR and restart |
| Secrets management | NOT STARTED | GitHub Secrets for SSH key, API keys |

### 8.3 Implementation Priority

CI/CD is a post-Gate-2 enhancement. The manual pipeline is sufficient for paper mode and limited live. CI/CD becomes mandatory before full live (Gate 4).

---

## 9. ACCEPTANCE TESTS

| Test ID | Scenario | Expected Result | Pass Criteria |
|---------|----------|-----------------|---------------|
| RE-T01 | Execute full 6-step build pipeline | All steps complete; parity PASS; health check 200 | Exit code 0 at each step; no errors in Docker build log |
| RE-T02 | Verify image tag contains correct date and git SHA | Tag format matches `YYYYMMDD-HHMM-<sha7>` | `docker images nzt48 --format '{{.Tag}}'` shows correctly formatted tag |
| RE-T03 | Verify parity check detects host-only edit | Edit `main.py` on host without rebuild; run parity check | Parity check reports FAIL for `main.py` |
| RE-T04 | Verify `docker exec` edit is lost on restart | Edit file inside container; restart; check file | File reverts to image state on restart |
| RE-T05 | Verify LKG tag created after successful deploy | Complete pipeline; run LKG tagging | `docker images nzt48 --format '{{.Tag}}'` includes `lkg-*` tag; git tag exists |
| RE-T06 | Verify LKG rollback restores previous version | Deploy v2; rollback to v1 LKG; verify code hash | `code_hash` in startup log matches v1; health check passes |
| RE-T07 | Verify reproducible build: two builds from same code produce same behaviour | Build twice from identical source; compare functionality | Both images pass health check and produce identical scan results |

---

## REVISION HISTORY

| Version | Date       | Author           | Changes                    |
|---------|------------|------------------|----------------------------|
| 1.0     | 2026-02-27 | NZT-48 Governance | Initial release engineering specification |

---

*End of Document NZT48-ANNEX-RE-001*
