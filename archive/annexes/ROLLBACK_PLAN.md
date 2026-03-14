# NZT-48 ROLLBACK PLAN

**Version**: 2.0
**Date**: 2026-02-27
**Status**: PLAN ONLY
**Scope**: Per-workstream rollback procedures (W0-W12), Last Known Good restore, feature flags, emergency procedures
**Principle**: Every change must be independently reversible within 5 minutes without data loss.

---

## TABLE OF CONTENTS

1. [Per-Workstream Rollback (W0-W12)](#1-per-workstream-rollback)
2. [Last Known Good (LKG) Restore](#2-last-known-good-lkg-restore)
3. [Feature Flags](#3-feature-flags)
4. [Emergency Procedures](#4-emergency-procedures)
5. [Rollback Drill Schedule](#5-rollback-drill-schedule)

---

## 1. PER-WORKSTREAM ROLLBACK

### W0: Deployment & Verification

**What changes**: Deploy code to EC2 via rsync, rebuild nzt48 Docker image (with lxml 5.1.0 fix), start nzt48 container and verify `/api/health`, build and start dashboard container, verify Telegram bot token end-to-end.

**How to detect problems**:
- Symptom: `/api/health` returns non-200 or times out after container start
- Symptom: Docker build fails (lxml compilation, missing dependencies)
- Symptom: Telegram bot token invalid or webhook unreachable
- Symptom: Dashboard container fails to start or returns blank page
- Monitor: `docker logs nzt48 --tail 50` for startup errors
- Monitor: `docker logs nzt48-dashboard --tail 50` for frontend build errors

**Feature flag**: N/A (deployment is binary -- either the new code is running or it is not)

**Rollback steps**:
```bash
# Full rollback: stop containers, restore from LKG git tag, rebuild
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals

# Step 1: Stop everything
docker compose down

# Step 2: Restore code from LKG tag
LKG_TAG=$(git tag -l 'lkg-*' --sort=-creatordate | head -1)
git stash
git checkout "$LKG_TAG"

# Step 3: Rebuild and restart
docker compose build nzt48
docker compose up -d nzt48
docker compose build nzt48-dashboard
docker compose up -d nzt48-dashboard

# Step 4: Verify
sleep 10
curl -sf http://localhost:8000/api/health
EOF
```

**Verification after rollback**: `curl http://localhost:8000/api/health` returns 200 with `"status":"ok"`. Dashboard loads at port 3001. Telegram responds to `/status`.

---

### W1: Premarket Sanity + Fail-Closed State Machine

**What changes**: Magnitude filter (+-8% overnight, +-30% intraday), data quality gate ternary (PASS/DEGRADED/DOWN), Score>=10 check in `gated_send`, SOFT gates that log + flag. Affects `main.py:1933-1949`, `main.py:608-611`, `telegram_bot.py:731-759`, `main.py:1014-1016`.

**How to detect problems**:
- Symptom: Legitimate signals being rejected by overly strict magnitude filter (false SANITY_FAIL)
- Symptom: Data quality gate stuck on DEGRADED/DOWN when feeds are healthy
- Symptom: Score>=10 check blocking signals that previously passed
- Symptom: Telegram signals dropping to zero during market hours
- Monitor: Log entries containing `SANITY_FAIL` or `GATE_BLOCKED`
- Monitor: `system_state.json` -> `gates_fired` breakdown

**Feature flag**: `settings.yaml` -> `feature_flags.sanity_gate_v2: true`
- When `false`: No magnitude validation on premarket data, no ternary quality gate, no Score>=10 check (original fail-open behavior)

**Rollback steps**:
```bash
# Option A: Feature flag (instant, no restart)
# Edit config/settings.yaml:
#   feature_flags:
#     sanity_gate_v2: false
# Engine picks up config change on next tick (60s)

# Option B: Full git revert
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W1-commit-hash>
docker compose restart nzt48
curl http://localhost:8000/api/health  # verify
EOF
```

**Verification after rollback**: Check that signals are flowing again. Premarket data passes through without magnitude filtering. `system_state.json` -> `signals_emitted_last_hour > 0` during market hours.

---

### W2: Canonical Return Math + Leverage-Once Policy

**What changes**: `leverage_applied` boolean flag on all return calculations, div/0 guards at 5 locations, confidence bounds assertion [0,100], `safe_divide()` utility function. Affects `pdf_v2_risk.py:399`, `pdf_v2_momentum.py:429`, `volatility_regime.py:254`, `predictive_scoring.py:867-868`, `lse_registry.py:331-333`.

**How to detect problems**:
- Symptom: Return calculations showing different values than before (leverage applied differently)
- Symptom: `safe_divide()` masking real errors by returning 0 instead of raising
- Symptom: Confidence assertions rejecting valid scores outside [0,100]
- Symptom: PDF generation crashing on assertion failures
- Monitor: Log entries containing `LEVERAGE_DOUBLE_COUNT` or `ASSERTION_FAIL`
- Monitor: PDF output values -- compare against pre-W2 baseline

**Feature flag**: `settings.yaml` -> `feature_flags.leverage_once_assertion: true`
- When `false`: Original return math (double-leverage risk returns), no div/0 guards, no confidence bounds assertion

**Rollback steps**:
```bash
# Option A: Feature flag
# settings.yaml -> feature_flags.leverage_once_assertion: false
# Original return math resumes on next tick. Accept that div/0 risks return.

# Option B: Git revert
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W2-commit-hash>
docker compose restart nzt48
EOF
```

**Verification after rollback**: Generate a test PDF. Verify it renders without crash. Accept that div/0 risks and double-leverage counting may return.

---

### W3: Provenance & Freshness Reporting

**What changes**: New `core/provenance.py` with `ProvenanceRecord` dataclass, TTL matrix (Price: 90s, VIX: 5min, etc.), TTL validation before every field use. Affects all data paths throughout the system.

**How to detect problems**:
- Symptom: Provenance TTL checks rejecting data that is actually fresh (clock skew, timestamp format mismatch)
- Symptom: ProvenanceRecord overhead slowing down scan cycles
- Symptom: Stale feed alerts firing when data is actually fresh
- Monitor: Log entries containing `TTL_EXPIRED` or `PROVENANCE_FAIL`
- Monitor: `system_state.json` -> `provenance_violations` count
- Monitor: Scan cycle duration increasing beyond 45s budget

**Feature flag**: `settings.yaml` -> `feature_flags.provenance_tracking: true`
- When `false`: No TTL validation, no provenance records attached to data (original behavior -- data used regardless of age)

**Rollback steps**:
```bash
# Option A: Feature flag
# settings.yaml -> feature_flags.provenance_tracking: false
# All data passes through without freshness checks on next tick

# Option B: Git revert
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W3-commit-hash>
docker compose restart nzt48
EOF
```

**Verification after rollback**: Data flows through without TTL rejection. Scan cycle duration returns to pre-W3 baseline.

---

### W4: Telegram Tape Rebuild

**What changes**: Category labels ([SIGNAL], [BRIEF], etc.), HTML `parse_mode` consistency, persistent dedupe hashes to DB, persistent kill/pause state to DB, 5-minute quiet mode on restart. Affects `telegram_bot.py`, `main.py:3678`.

**How to detect problems**:
- Symptom: Telegram messages not sending (persistent dedupe too aggressive, blocking legitimate signals)
- Symptom: Telegram messages malformatted (HTML parse errors from `parse_mode` change)
- Symptom: Kill/pause state not persisting across restarts (DB write failure)
- Symptom: 5-minute quiet mode preventing time-sensitive signals after restart
- Monitor: Telegram bot error logs
- Monitor: `data/dedupe_log.json` file size and content
- Monitor: Telegram channel -- messages still arriving with expected format

**Feature flags**: `settings.yaml` -> `feature_flags.telegram_tape_v2: true` and `feature_flags.persistent_dedupe: true`
- `telegram_tape_v2: false` -> Original Telegram formatting, labels, and in-memory kill/pause state
- `persistent_dedupe: false` -> In-memory-only dedupe (original volatile window)

**Rollback steps**:
```bash
# Option A: Feature flags (independent toggles)
# settings.yaml:
#   feature_flags:
#     telegram_tape_v2: false      # revert to original formatting
#     persistent_dedupe: false     # revert to in-memory dedupe

# Option B: Git revert
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W4-commit-hash>
docker compose restart nzt48
# NOTE: Persistent dedupe file (data/dedupe_log.json) will be orphaned but harmless
EOF
```

**Verification after rollback**: Send a test signal via Telegram `/test` command. Verify message format is correct. Verify kill/pause state resets on restart (original behavior).

---

### W5: Regime/Confidence/Drought Binding

**What changes**: Canonical regime mapping (5-state to 8-state, hybrid approach), drought state machine (NORMAL->WATCH->DROUGHT->CRITICAL->CLEARED, 20 cycles threshold), contradiction detector (EXPANSION + DROUGHT -> alert). New files: `core/regime_mapping.py`, `core/drought_manager.py`.

**How to detect problems**:
- Symptom: Regime shows "UNKNOWN" persistently when market is open
- Symptom: Regime contradictions between Telegram and War Room (the exact bug this fixes)
- Symptom: Drought state machine stuck in DROUGHT when conditions have cleared
- Symptom: Contradiction detector firing false alerts (EXPANSION + DROUGHT both valid in transition)
- Monitor: `system_state.json` field `regime` == "UNKNOWN" during market hours
- Monitor: `system_state.json` field `drought_state` for stuck states
- Monitor: Telegram alerts for regime contradictions

**Feature flags**: `settings.yaml` -> `feature_flags.regime_unification: true` and `feature_flags.drought_escalation: true`
- `regime_unification: false` -> Each module computes regime independently (original behavior, contradictions may occur)
- `drought_escalation: false` -> No drought state machine, no escalation (original behavior)

**Rollback steps**:
```bash
# Option A: Feature flags (independent toggles)
# settings.yaml:
#   feature_flags:
#     regime_unification: false     # revert to independent regime computation
#     drought_escalation: false     # revert to no drought tracking

# Option B: Git revert
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W5-commit-hash>
docker compose restart nzt48
EOF
```

**Verification after rollback**: Check `system_state.json` regime field is one of the valid states. Accept that cross-surface contradictions may resume and drought is not tracked.

---

### W6: PDF/Brief Desk Notes QA + Pre-Flight Audit Gate

**What changes**: Pre-flight QA checker, lane separation (PDF1=momentum, PDF2=risk), as-of timestamps on every section, count verification, closest misses section, cross-artifact consistency checker. New files: `delivery/pdf_qa_checker.py`, `core/consistency_checker.py`.

**How to detect problems**:
- Symptom: Pre-flight QA rejecting all PDFs (overly strict validation rules)
- Symptom: PDF generation crashing in new QA checker code path
- Symptom: Lane separation breaking existing PDF content (sections missing)
- Symptom: Consistency checker blocking PDF delivery due to minor discrepancies
- Monitor: `data/pdf_generation.log` for QA failures
- Monitor: `artifacts/` directory -- check for expected PDF files at scheduled times
- Monitor: Log entries containing `PDF_QA_FAIL` or `CONSISTENCY_FAIL`

**Feature flag**: `settings.yaml` -> `feature_flags.pdf_qa_gate: true`
- When `false`: PDFs generated without pre-flight QA, no consistency checking, no lane enforcement (original behavior)

**Rollback steps**:
```bash
# Option A: Feature flag
# settings.yaml -> feature_flags.pdf_qa_gate: false
# PDFs generate without QA gate on next scheduled generation

# Option B: Git revert
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W6-commit-hash>
docker compose restart nzt48
EOF
```

**Verification after rollback**: Manually trigger a PDF generation. Verify it renders without crash and delivers on schedule.

---

### W7: War Room Full Wiring + Manager UX

**What changes**: 6 missing API endpoints wired up, per-panel freshness indicators, dynamic ticker lists from API, production CORS origins. Affects `api.py`, `page.tsx`.

**How to detect problems**:
- Symptom: New endpoints returning 500 errors
- Symptom: Dashboard panels blank or showing stale data
- Symptom: CORS errors blocking dashboard requests from production origins
- Symptom: WebSocket connection instability after server changes
- Monitor: War Room console errors (browser dev tools)
- Monitor: `docker logs nzt48-dashboard --tail 50`
- Monitor: `curl http://localhost:8000/api/health` and new endpoint responses

**Feature flag**: `settings.yaml` -> `feature_flags.war_room_v2: true`
- When `false`: 6 new endpoints return `501 Not Implemented`. Dashboard falls back to existing panels only. New tabs hidden.

**Rollback steps**:
```bash
# Option A: Feature flag (backend only -- frontend still has new code but endpoints return 501)
# settings.yaml -> feature_flags.war_room_v2: false

# Option B: Full revert (backend + frontend)
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W7-commit-hash>
docker compose build nzt48-dashboard && docker compose up -d nzt48-dashboard
docker compose restart nzt48
EOF
```

**Verification after rollback**: `curl http://localhost:8000/api/health` returns 200. All previously-working panels still render. No CORS errors in dashboard console.

---

### W8: Data Vendor Migration

**What changes**: Polygon ($29/mo) integration as primary data vendor (approved), IBKR evaluation for LSE data, provider abstraction layer (`data/provider_interface.py`), yfinance fallback with `FALLBACK_DATA` flag.

**How to detect problems**:
- Symptom: No market data returned (Polygon API key invalid, rate limited, or service down)
- Symptom: yfinance fallback activating too frequently (Polygon unreliable)
- Symptom: Provider abstraction layer introducing latency in data fetches
- Symptom: LSE ticker data missing or stale through new provider
- Monitor: `data/scan_health.json` field `data_source` (should show provider name)
- Monitor: Log entries containing `FALLBACK_DATA` (indicates primary provider failed)
- Monitor: Telegram HEALTH alert with `DATA_SOURCE_FAILURE`

**Feature flag**: `settings.yaml` -> `feature_flags.datahub_routing: true`
- When `false`: All modules revert to direct `yf.download()` calls (original behavior). Provider abstraction layer exists but is not invoked.

**Rollback steps**:
```bash
# Option A: Feature flag (instant, no restart)
# settings.yaml -> feature_flags.datahub_routing: false
# All data calls revert to yfinance on next tick (60s)

# Option B: Git revert
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W8-commit-hash>
docker compose restart nzt48
curl http://localhost:8000/api/health  # verify
EOF
```

**Verification after rollback**: Run `grep -r "yf.download\|DataHub\|provider" --include="*.py" | grep -v "test\|venv"` to confirm yfinance is being called directly. Data flowing in scan cycles.

---

### W9: 5-Year Historical Backfill

**What changes**: SQLite research DB (`data/research_db.py`), 5yr daily + 1yr intraday historical data, corporate actions adjustment. New file: `data/research_db.py`. This is a separate database -- no impact on live trading engine.

**How to detect problems**:
- Symptom: SQLite DB file growing unexpectedly large (disk space)
- Symptom: Backfill process consuming too much CPU/bandwidth during market hours
- Symptom: Corporate actions adjustment producing incorrect historical prices
- Monitor: `data/research.db` file size
- Monitor: System resource usage during backfill windows

**Feature flag**: N/A (separate DB, no flag needed -- research DB is fully independent of live engine)

**Rollback steps**:
```bash
# Simply stop the backfill process and remove the research DB if needed
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
# Kill any running backfill process
pkill -f research_db || true
# Optionally remove the DB (no impact on live engine)
rm -f data/research.db
EOF

# Or git revert if code changes affected other modules
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W9-commit-hash>
docker compose restart nzt48
EOF
```

**Verification after rollback**: Live engine unaffected. `curl http://localhost:8000/api/health` returns 200. Research DB removed or backfill stopped.

---

### W10: Universe Governance

**What changes**: CORE-first compute budget (70/20/10%), approval gate for universe changes, low-liquidity handling (25% position size), removal of 9 delisted tickers. New file: `core/universe_governance.py`.

**How to detect problems**:
- Symptom: Scans taking > 45s (compute budget miscalculated)
- Symptom: Unknown/delisted tickers still appearing in scans (removal incomplete)
- Symptom: Low-liquidity handling reducing position sizes too aggressively
- Symptom: Approval gate blocking legitimate universe changes
- Monitor: `scan_health.json` field `scan_duration_ms` exceeding budget
- Monitor: Per-ticker data health in artifacts
- Monitor: `system_state.json` -> `universe.active_tickers` count

**Feature flag**: `settings.yaml` -> `feature_flags.universe_governance: true`
- When `false`: Original flat universe with all tickers receiving equal compute. No approval gate. No liquidity-based sizing. Delisted tickers remain (may cause yfinance errors).

**Rollback steps**:
```bash
# Option A: Feature flag (instant)
# settings.yaml -> feature_flags.universe_governance: false
# Original flat universe on next tick

# Option B: Git revert
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W10-commit-hash>
docker compose restart nzt48
EOF
```

**Verification after rollback**: Check `system_state.json` -> `universe.active_tickers` contains the original ticker set. Scan duration returns to pre-W10 baseline.

---

### W11: Learning Loop Hardening

**What changes**: 24h outcome resolution timeout, strategy drift detection (35%/25% thresholds), DEFENSIVE mode on drift, readiness gates (100+ outcomes required). New file: `core/drift_detector.py`.

**How to detect problems**:
- Symptom: Drift detector triggering DEFENSIVE mode too early (threshold too sensitive)
- Symptom: 24h timeout expiring legitimate signals that resolve slightly late
- Symptom: Readiness gate blocking all learning (< 100 outcomes accumulated)
- Symptom: DEFENSIVE mode reducing signal quality by being overly conservative
- Monitor: `system_state.json` -> `learning_loop.mode` (NORMAL vs DEFENSIVE)
- Monitor: `system_state.json` -> `learning_loop.drift_score`
- Monitor: Log entries containing `DRIFT_DETECTED` or `DEFENSIVE_MODE`

**Feature flag**: `settings.yaml` -> `feature_flags.learning_loop_hardened: true`
- When `false`: No drift detection, no DEFENSIVE mode, no 24h timeout, no readiness gates (original behavior -- learning loop runs without guardrails)

**Rollback steps**:
```bash
# Option A: Feature flag
# settings.yaml -> feature_flags.learning_loop_hardened: false
# Learning loop reverts to ungoverned operation on next tick

# Option B: Git revert
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W11-commit-hash>
docker compose restart nzt48
EOF
```

**Verification after rollback**: Learning loop mode shows NORMAL (no DEFENSIVE override). Drift detection disabled. Signals flow without readiness gate checks.

---

### W12: Ops Governance

**What changes**: Run manifest per session (`core/run_manifest.py`), feature flags infrastructure, config hash verification, paper->live migration gates, LKG tagging automation. New files: `core/run_manifest.py`, updates to `settings.yaml`.

**How to detect problems**:
- Symptom: Run manifest failing to write (disk permission, path error)
- Symptom: Config hash verification rejecting valid configs (hash computation mismatch)
- Symptom: Paper->live migration gate blocking when it should not
- Symptom: LKG tagging automation creating too many tags
- Monitor: `data/manifests/` directory for current session manifest
- Monitor: Log entries containing `MANIFEST_FAIL` or `CONFIG_HASH_MISMATCH`

**Feature flag**: N/A (meta-infrastructure -- this workstream creates the feature flag system itself and other governance tooling)

**Rollback steps**:
```bash
# Git revert (feature flags infrastructure is foundational -- if broken, revert fully)
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'EOF'
cd /home/ubuntu/nzt48-signals
git revert --no-commit <W12-commit-hash>
docker compose restart nzt48
EOF

# If run manifests are causing disk issues:
# rm -rf data/manifests/
# mkdir -p data/manifests/
```

**Verification after rollback**: Engine starts without manifest or config hash errors. Feature flags section in `settings.yaml` still readable by other workstreams (W12 rollback must not break the flag-reading pattern).

---

## 2. LAST KNOWN GOOD (LKG) RESTORE

### 2.1 Definition

**LKG** = a snapshot of the system that is known to produce correct output. It consists of:

| Component | Identifier | Where Stored |
|-----------|-----------|--------------|
| Code | Git commit hash | Git repository |
| Configuration | SHA-256 of `config/settings.yaml` | Git tag annotation |
| Docker image | Docker image tag | Local Docker registry |
| Artifacts | Latest valid `system_state.json` | `data/lkg/` directory |

### 2.2 Tagging Process

After every successful deployment (defined as: system runs for 30 minutes with zero ERROR-level log entries and at least 1 successful signal cycle):

```bash
# Automated by deployment script (or manual)
LKG_TAG="lkg-$(date +%Y%m%d-%H%M)"
SETTINGS_HASH=$(sha256sum config/settings.yaml | cut -d' ' -f1)

git tag -a "$LKG_TAG" -m "LKG: settings=$SETTINGS_HASH"
docker tag nzt48:latest "nzt48:$LKG_TAG"

# Archive current good state
mkdir -p data/lkg
cp artifacts/system_state.json "data/lkg/system_state_${LKG_TAG}.json"
cp config/settings.yaml "data/lkg/settings_${LKG_TAG}.yaml"

echo "LKG tagged: $LKG_TAG"
```

### 2.3 LKG Catalog

Maintain a running log of all LKG snapshots:

```
data/lkg/LKG_CATALOG.txt
---
lkg-20260227-0700  abc123f  OK  "Pre-Master-Plan baseline"
lkg-20260227-1400  def456a  OK  "Post-W0 deployment verified"
lkg-20260227-1800  ghi789b  OK  "Post-W1 sanity gate + W2 return math"
lkg-20260228-0900  jkl012c  OK  "Post-W3+W4 provenance + telegram"
```

### 2.4 Full LKG Restore Procedure

**Target**: Complete restore to last known good state within 5 minutes.

```bash
#!/bin/bash
# RESTORE TO LKG
# Usage: ./scripts/restore_lkg.sh [lkg-tag]
# If no tag provided, uses most recent LKG

set -euo pipefail

LKG_TAG="${1:-$(git tag -l 'lkg-*' --sort=-creatordate | head -1)}"

if [ -z "$LKG_TAG" ]; then
    echo "FATAL: No LKG tags found. Manual intervention required."
    exit 1
fi

echo "=== RESTORING TO LKG: $LKG_TAG ==="
echo "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Step 1: Stop the engine (graceful)
echo "[1/5] Stopping engine..."
docker compose stop nzt48
sleep 2

# Step 2: Checkout LKG code
echo "[2/5] Checking out LKG code..."
git stash  # preserve any uncommitted work
git checkout "$LKG_TAG"

# Step 3: Restore LKG config (if archived)
if [ -f "data/lkg/settings_${LKG_TAG}.yaml" ]; then
    echo "[3/5] Restoring LKG config..."
    cp "data/lkg/settings_${LKG_TAG}.yaml" config/settings.yaml
else
    echo "[3/5] No archived config for $LKG_TAG; using repo version"
fi

# Step 4: Rebuild and restart
echo "[4/5] Rebuilding and restarting..."
docker compose build nzt48
docker compose up -d nzt48

# Step 5: Verify
echo "[5/5] Verifying health..."
sleep 10
HEALTH=$(curl -sf http://localhost:8000/api/health || echo "FAIL")
if echo "$HEALTH" | grep -q '"status":"ok"'; then
    echo "=== LKG RESTORE COMPLETE: $LKG_TAG ==="
    echo "Health: OK"
else
    echo "=== LKG RESTORE FAILED: Health check returned: $HEALTH ==="
    echo "Manual intervention required."
    exit 1
fi
```

### 2.5 EC2 Remote LKG Restore

For restoring directly on the production EC2 instance:

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28 << 'REMOTE_EOF'
cd /home/ubuntu/nzt48-signals
./scripts/restore_lkg.sh
REMOTE_EOF
```

### 2.6 Data Migration Rollback

If any workstream changes the artifact schema (adds/removes fields from JSON):

| Scenario | Rollback Action |
|----------|----------------|
| New field added to `system_state.json` | Old code ignores unknown fields (safe) |
| Field removed from `system_state.json` | Old code will KeyError on missing field; restore archived `system_state.json` from `data/lkg/` |
| Field type changed (e.g., string -> int) | Old code will TypeError; restore archived state |
| New artifact file added | Old code ignores files it does not read (safe) |
| Artifact file renamed | Old code will FileNotFoundError; restore with `cp data/lkg/system_state_<tag>.json artifacts/system_state.json` |

**Rule**: Every schema change must be backward-compatible (additive only) OR the workstream must archive the current artifact before modification.

---

## 3. FEATURE FLAGS

### 3.1 Configuration Format

All feature flags live in `config/settings.yaml` under the `feature_flags` section:

```yaml
# Feature Flags -- Master Switch Section
# Set any flag to false to revert that feature without code changes.
# Flags are read on every scan tick (60s). No restart required for flag changes.
# DEFAULT: All flags FALSE until explicitly enabled after testing.

feature_flags:
  # W1: Premarket sanity
  sanity_gate_v2: false

  # W2: Return math
  leverage_once_assertion: false

  # W3: Provenance
  provenance_tracking: false

  # W4: Telegram tape
  telegram_tape_v2: false
  persistent_dedupe: false

  # W5: Regime/drought
  regime_unification: false
  drought_escalation: false

  # W6: PDF QA
  pdf_qa_gate: false

  # W7: War Room
  war_room_v2: false

  # W8: Data vendor
  datahub_routing: false

  # W10: Universe governance
  universe_governance: false

  # W11: Learning loop
  learning_loop_hardened: false

  # W0, W9, W12: No feature flags (W0=deployment, W9=separate DB, W12=meta-infrastructure)
```

### 3.2 Flag Reading Pattern

Every module that checks a feature flag must use this pattern:

```python
def _is_flag_enabled(self, flag_name: str) -> bool:
    """Read feature flag from settings.yaml. Default: False (safe)."""
    flags = self.config.get("feature_flags", {})
    return flags.get(flag_name, False)
```

**Rules**:
1. Default is always `False` (feature disabled)
2. Missing flag = disabled (fail-safe)
3. Flags are re-read from disk on every tick (no caching)
4. Flag changes do NOT require restart
5. Log every flag state at startup: `logger.info("Feature flags: %s", flags)`

### 3.3 Flag Dependency Matrix

Some flags depend on others. Enabling a downstream flag without its upstream dependency produces undefined behavior.

| Flag (Downstream) | Depends On (Upstream) | Effect If Upstream Disabled |
|---|---|---|
| `provenance_tracking` (W3) | `leverage_once_assertion` (W2) | Provenance may track uncorrected return values |
| `telegram_tape_v2` (W4) | `provenance_tracking` (W3) | Telegram tape cannot attach freshness metadata to messages |
| `pdf_qa_gate` (W6) | `provenance_tracking` (W3) + `regime_unification` (W5) | QA checker cannot verify data freshness or regime consistency |
| `war_room_v2` (W7) | `telegram_tape_v2` (W4) + `drought_escalation` (W5) | War Room panels missing Telegram events and drought state |
| `datahub_routing` (W8) | `provenance_tracking` (W3) | Data vendor layer cannot attach provenance records |
| `learning_loop_hardened` (W11) | `regime_unification` (W5) | Drift detector cannot reference canonical regime states |

**Rule**: When rolling back an upstream flag, also disable all dependent downstream flags.

### 3.4 Flag Rollout Sequence

Enable flags in this order (each step validated before proceeding):

```
Phase 1: sanity_gate_v2 (W1) + leverage_once_assertion (W2)
Phase 2: provenance_tracking (W3)
Phase 3: telegram_tape_v2, persistent_dedupe (W4)
Phase 4: regime_unification, drought_escalation (W5)
Phase 5: pdf_qa_gate (W6)
Phase 6: war_room_v2 (W7)
Phase 7: datahub_routing (W8)
Phase 8: universe_governance (W10)
Phase 9: learning_loop_hardened (W11)
```

**Note**: W0 (deployment), W9 (historical backfill), and W12 (ops governance) have no feature flags and are deployed/rolled back independently.

---

## 4. EMERGENCY PROCEDURES

### 4.1 KILL SWITCH

**Purpose**: Immediately halt all signal emission and trading activity.

**Three activation methods** (any one is sufficient):

| Method | How | Latency | Persisted? |
|--------|-----|---------|-----------|
| Telegram | Send `/kill ALL` to bot | < 5s | YES (with persistent state from W4) |
| File | `touch /home/ubuntu/nzt48-signals/data/KILL_SWITCH` | < 60s (next tick) | YES (file on disk) |
| Signal | Send SIGTERM to process | Immediate | NO (process dies, restart needed) |

**Kill switch behavior**:
1. All pending signals are discarded (not queued)
2. No new signals generated
3. Existing position management continues (stops remain active)
4. Telegram sends "KILL SWITCH ACTIVE" status message
5. War Room shows red "HALTED" banner
6. PDF generation continues but with "SYSTEM HALTED" watermark

**Deactivation**:
```bash
# Via Telegram
/resume ALL

# Via file
rm /home/ubuntu/nzt48-signals/data/KILL_SWITCH
# System resumes on next tick (60s)
```

### 4.2 EMERGENCY FLATTEN

**Purpose**: Kill switch PLUS force-close all open positions.

**Activation**:
```bash
# Telegram
/kill ALL
/flatten ALL

# Or combined
/emergency_flatten
```

**Procedure**:
1. Kill switch activates (step 4.1)
2. All open positions receive MARKET SELL order
3. Position status set to FORCE_CLOSED with reason "EMERGENCY_FLATTEN"
4. Telegram sends confirmation of each closed position
5. War Room updates position panel to show all flat

**CRITICAL**: This is irreversible. Positions are closed at market price. Only use in genuine emergency (system malfunction, data corruption, or account risk).

### 4.3 ROLLBACK TO LKG

**Purpose**: Full system restore to last known good state.

**When to use**: When feature flag disabling is insufficient (core code is broken, not just a feature).

**Procedure** (target: < 5 minutes):

```
T+0:00  DETECT: Anomalous behavior identified
T+0:30  DECIDE: Operator determines LKG restore needed
T+1:00  KILL: Send /kill ALL via Telegram
T+1:30  STOP: docker compose stop nzt48
T+2:00  RESTORE: ./scripts/restore_lkg.sh [tag]
T+4:00  VERIFY: curl health check + Telegram /status
T+5:00  RESUME: /resume ALL (if health OK)
```

**Decision criteria for LKG vs feature flag rollback**:

| Symptom | Action |
|---------|--------|
| One feature producing bad output | Disable that feature's flag |
| Multiple features broken | Disable all flags (revert to pre-Master-Plan behavior) |
| Core engine crash loop | LKG restore |
| Artifact corruption | LKG restore + clear `artifacts/` directory |
| Docker image broken | `docker pull nzt48:<lkg-tag>` + restart |

### 4.4 INCIDENT LOG

Every emergency action must be recorded in `data/INCIDENT_LOG.jsonl`:

```json
{
  "timestamp": "2026-02-27T14:32:00Z",
  "action": "KILL_SWITCH",
  "method": "telegram",
  "operator": "rr",
  "reason": "Impossible premarket values detected for QQQ3.L (+340%)",
  "outcome": "All signals halted. 0 open positions affected.",
  "resolution": "Disabled sanity_gate_v2 flag. Investigated: yfinance returned stale pre-split price. Enabled flag after fix.",
  "duration_minutes": 12,
  "lkg_used": false,
  "feature_flags_changed": ["sanity_gate_v2: true -> false -> true"]
}
```

**Fields** (all required):

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | ISO-8601 UTC | When the action was taken |
| `action` | enum | `KILL_SWITCH`, `EMERGENCY_FLATTEN`, `LKG_RESTORE`, `FLAG_CHANGE`, `RESTART` |
| `method` | string | How activated: `telegram`, `file`, `sigterm`, `script`, `manual` |
| `operator` | string | Who took the action |
| `reason` | string | Why the action was taken (detailed) |
| `outcome` | string | What happened as a result |
| `resolution` | string | How the issue was ultimately resolved |
| `duration_minutes` | int | Time from detection to resolution |
| `lkg_used` | bool | Whether LKG restore was needed |
| `feature_flags_changed` | list[str] | Which flags were toggled and in what direction |

### 4.5 Escalation Ladder

| Severity | Condition | Action | Auto? |
|----------|-----------|--------|-------|
| SEV-4 | Single gate failure, signals still flowing | Log warning, continue | YES |
| SEV-3 | Multiple gate failures OR 1 hard gate failure | Disable affected feature flag, alert operator | YES (flag) + ALERT |
| SEV-2 | Engine crash loop (3 crashes in 5min) | Kill switch + alert operator | YES |
| SEV-1 | Data corruption OR impossible signals sent to Telegram | Emergency flatten + LKG restore + alert operator | MANUAL (operator decides) |
| SEV-0 | Account at risk (real money mode only) | Emergency flatten + kill switch + full stop | YES (auto-flatten) |

---

## 5. ROLLBACK DRILL SCHEDULE

Regular drills ensure the rollback procedures actually work.

| Drill | Frequency | Procedure | Pass Criteria |
|-------|-----------|-----------|---------------|
| Feature flag toggle | Weekly | Disable and re-enable each flag | System behavior changes correctly in both directions; no stale state |
| LKG restore | Monthly | Full `restore_lkg.sh` execution on EC2 | Restore completes in < 5 minutes; health check passes; signals resume |
| Kill switch | Weekly | Activate and deactivate kill switch via all 3 methods | Each method works; deactivation resumes normal operation |
| Emergency flatten | Monthly (paper mode only) | Execute full emergency flatten | All mock positions closed; incident log entry created |
| Incident log review | Weekly | Review all entries since last review | All entries complete; no missing fields; resolution documented |

### Drill Log Format

```
data/DRILL_LOG.jsonl
---
{"date": "2026-02-28", "drill": "kill_switch_telegram", "result": "PASS", "duration_s": 4, "notes": ""}
{"date": "2026-02-28", "drill": "kill_switch_file", "result": "PASS", "duration_s": 55, "notes": "Picked up on next tick"}
{"date": "2026-02-28", "drill": "lkg_restore", "result": "PASS", "duration_s": 210, "notes": "Restored to lkg-20260227-0700"}
{"date": "2026-02-28", "drill": "flag_toggle_sanity_gate_v2", "result": "PASS", "duration_s": 62, "notes": ""}
{"date": "2026-02-28", "drill": "flag_toggle_provenance_tracking", "result": "PASS", "duration_s": 58, "notes": ""}
{"date": "2026-02-28", "drill": "flag_toggle_regime_unification", "result": "PASS", "duration_s": 60, "notes": ""}
```

---

## QUICK REFERENCE: ROLLBACK DECISION TREE

```
Is the system sending bad signals?
├── YES
│   ├── Is it one specific feature?
│   │   ├── YES → Disable that feature's flag in settings.yaml
│   │   │   ├── Check dependency matrix: also disable downstream flags
│   │   │   └── Verify on next tick (60s)
│   │   └── NO → Set ALL feature_flags to false
│   │       ├── Did that fix it?
│   │       │   ├── YES → Binary search: re-enable flags one at a time to find culprit
│   │       │   └── NO → LKG RESTORE (./scripts/restore_lkg.sh)
│   └── Is it sending to Telegram / affecting real output?
│       ├── YES → /kill ALL first, THEN diagnose
│       └── NO → Diagnose without kill switch
├── NO, but engine is crashing
│   ├── Crash loop (3+ in 5min)?
│   │   ├── YES → LKG RESTORE
│   │   └── NO → Check logs, fix, restart
├── NO, but deployment failed (W0)
│   ├── Docker build failed?
│   │   ├── YES → Fix Dockerfile/deps, rebuild
│   │   └── NO → docker compose down, restore LKG tag, rebuild
│   └── Health check failed after deploy?
│       └── docker compose down, restore LKG tag, rebuild
└── NO, system is fine
    └── No action needed
```

---

## QUICK REFERENCE: EC2 COMMANDS

```bash
# SSH into EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28

# Deploy code
rsync -avz --rsh='ssh -i /Users/rr/.ssh/nzt48-key.pem' /Users/rr/nzt48-signals/ ubuntu@100.55.69.28:/home/ubuntu/nzt48-signals/

# Rebuild and restart engine
cd /home/ubuntu/nzt48-signals && docker compose build nzt48 && docker compose up -d

# Rebuild and restart dashboard
docker compose build nzt48-dashboard && docker compose up -d nzt48-dashboard

# Health check
curl http://localhost:8000/api/health

# View engine logs
docker logs nzt48 --tail 50

# View dashboard logs
docker logs nzt48-dashboard --tail 50

# Stop everything
docker compose down

# Feature flag change (no restart needed)
nano config/settings.yaml  # edit flags, save -- picked up on next 60s tick
```

---

## ADDENDUM: W13 ALWAYS-WIRED ROLLBACK STRATEGY

**Added by**: `docs/ADDENDUM_ALWAYS_WIRED_110.md` v1.0

### Master Flag

```yaml
feature_flags:
  always_wired_v1: false  # Disables ALL W13 controls instantly
```

Setting `always_wired_v1: false` disables: startup readiness gate, continuous integrity monitor, self-healing ops, artifact single source enforcement, and Docker parity checks.

**Sub-flags** (only active when master is `true`):

| Flag | Controls | Rollback Impact |
|------|----------|----------------|
| `startup_readiness_gate` | Boot-time 8-check gate | System boots without pre-flight |
| `continuous_integrity` | 5-min wiring drift monitor | Wiring drift goes undetected |
| `self_healing_ops` | Auto-remediation actions | Manual ops only |
| `artifact_single_source` | Render-from-artifacts enforcement | Consumers may fetch live data independently |
| `docker_parity_check` | Container vs host checksums | Deploy without parity verification |

### Rollback Procedure

1. Edit `config/settings.yaml`: set `always_wired_v1: false`
2. Changes picked up on next 60s tick (no restart needed)
3. Verify: War Room System Wiring panel shows "DISABLED"
4. Verify: No `[SYSTEM] INTEGRITY ALERT` messages sent

### Blast Radius: LOW

W13 controls are purely additive monitoring and gating. Disabling returns to W0-W12 behavior. No data loss, no signal disruption.

### Time to Rollback: < 1 minute

Single config change, no restart required.
