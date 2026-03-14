# IMMEDIATE ACTIONS (AMENDED)
### Corrections Based on Eleventh-Order Audit
**Date**: 2026-03-10 | **Classification**: BLOCKING AMENDMENTS

---

## TODAY (2026-03-10) — CRITICAL AMENDMENTS REQUIRED

### 1. EBS Expansion: 100GB (NOT 50GB) — **CRITICAL**

**The Problem**:
- Original plan: 50GB EBS
- Reality: 48-hour Crucible generates 86GB WAL data + 15GB Docker overhead = 101GB minimum
- **Result**: 50GB will hit 100% disk at ~36 hours → data loss, watchdog timeout, crash loop

**The Fix**:
```bash
# AWS Console: Modify volume to 100GB gp3
aws ec2 modify-volume \
  --volume-id vol-0da987aac2c09d7c5 \
  --size 100 \
  --region us-east-1

# Monitor progress (5-10 minutes)
aws ec2 describe-volumes-modifications \
  --filters Name=original-volume-id,Values=vol-0da987aac2c09d7c5 \
  --region us-east-1

# On EC2: Expand filesystem (after volume modification complete)
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
sudo growpart /dev/xvda 1
sudo resize2fs /dev/xvda1

# Verify
df -h /
# Expected: ~100G available
```

**Acceptance Test** (AT-EBS-100GB):
```bash
df -h / | awk 'NR==2 {print $2}'
# Expected: 100G or ~100G
```

---

### 2. Polygon Grouped Endpoint Verification — **DATA VENDOR PHYSICS FIX**

**The Problem**:
- If Ouroboros iterates over 5,200 tickers at 4 req/min, it takes 21.8 hours
- Pipeline must complete in 2 hours (21:00-23:00 UTC DARK window)
- **Result**: Pipeline timeout, Asian session opens blind, system crashes

**The Fix**:
Verify Ouroboros uses **Polygon Grouped Daily endpoint ONLY**:

```bash
# Test the grouped endpoint live
curl -s "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/2026-03-10" \
  -H "Authorization: Bearer e8vYJGn7..." | jq '.results | length'
# Expected output: ~10,000 (all US stocks in one call)

# Measure response time
time curl -s "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/2026-03-10" \
  -H "Authorization: Bearer e8vYJGn7..." > /dev/null
# Expected: <1 second
```

**Code Verification** (AT-Polygon-Grouped):
```bash
# Verify Ouroboros uses grouped endpoint
grep -c "get_grouped_daily_aggs" python_brain/ouroboros/step_0_polygon_loader.py
# Must return ≥1

# Verify NO iterative loops
grep -c "for.*in.*tickers.*get_aggs" python_brain/ouroboros/step_0_polygon_loader.py
# Must return 0

# Measure step_0 completion time
time python python_brain/ouroboros/step_0_polygon_loader.py --date 2026-03-10
# Must complete in <2 minutes
```

---

### 3. European Data Pathway Validation — **DATA VENDOR PHYSICS FIX**

**The Problem**:
- Polygon provides zero European coverage (LSE .L tickers return 0 results)
- TwelveData has 800 call/day limit (cannot cover 2,000-ticker EU universe)
- **Result**: GARCH fitting for European assets fails; system trades on stale parameters

**The Fix**:
Test YFinance parallel fetch for 12 LSE tickers:

```bash
# Test YFinance parallel fetch (5 concurrent threads)
python -c "
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor
import time

lse_tickers = ['QQQ3.L', '3LUS.L', '3SEM.L', 'GPT3.L', 'NVD3.L', 'TSL3.L',
               'TSM3.L', 'MU2.L', 'QQQS.L', '3USS.L', 'QQQ5.L', 'SP5L.L']

start = time.time()
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {t: executor.submit(yf.download, t, period='60d', progress=False)
              for t in lse_tickers}
    results = {t: f.result() for t, f in futures.items()}
elapsed = time.time() - start

print(f'Fetched {len(results)} tickers in {elapsed:.1f} seconds')
print(f'Data quality: {sum(1 for v in results.values() if len(v) >= 50)}/{len(results)} tickers have ≥50 days data')
"
# Expected: <10 seconds, all 12 tickers have data
```

**Code Verification** (AT-European-Data):
```bash
# Verify Ouroboros has European data pathway
grep -c "yfinance\|ThreadPoolExecutor" python_brain/ouroboros/data_loader.py
# Must return ≥2

# Verify all 12 LSE tickers return data
python -c "
import yfinance as yf
tickers = ['QQQ3.L', '3LUS.L', '3SEM.L', 'GPT3.L', 'NVD3.L', 'TSL3.L',
           'TSM3.L', 'MU2.L', 'QQQS.L', '3USS.L', 'QQQ5.L', 'SP5L.L']
for t in tickers:
    data = yf.download(t, period='60d', progress=False)
    assert len(data) > 50, f'{t} has insufficient data: {len(data)} rows'
print('All 12 LSE tickers validated')
"
# Expected: "All 12 LSE tickers validated"
```

---

## BEFORE MONDAY (THIS WEEK) — REFACTORING SPRINT FIXES

### 4. GARCH State Persistence Design

**The Problem**:
- Container restart at 14:00 UTC wipes in-memory GARCH state (\sigma_t^2)
- Rust reboots with static params but zero state memory
- First tick: artificial "innovation shock" triggers false CVaRExceeded
- Result: All positions forcibly liquidated on phantom risk

**The Fix**:
- Serialize \sigma_t^2 to WAL on every tick
- On boot: replay WAL to reconstruct exact state
- See ELEVENTH_ORDER_EXECUTION_REALITY_AUDIT.md Part 2A for full implementation

**Acceptance Test** (AT-GARCH-Persistence):
- Boot container, verify logs show "GARCHState reconstructed from WAL"
- No artificial volatility spikes on boot

---

### 5. WAL Channel Bounding Strategy

**The Problem**:
- Unbounded channel OOMs under EBS latency spike
- Bounded channel blocks Tokio reactor (exact deadlock you're fixing)

**The Fix**:
- Use bounded(10000) + try_send()
- On channel full: gracefully drop telemetry (prioritize execution survival)
- See ELEVENTH_ORDER_EXECUTION_REALITY_AUDIT.md Part 2B for full implementation

**Acceptance Test** (AT-WAL-Bounded):
- Simulate 10k events/sec burst with EBS latency
- Verify: no OOM, events dropped gracefully, reactor stays alive

---

### 6. Python Subprocess Emergency Freeze

**The Problem**:
- 15-minute backoff leaves portfolio blind during crash
- ES moves 2% in 15 minutes; hedges become stale

**The Fix**:
- Immediately activate regime=Yellow (50% size reduction)
- Tighten Chandelier stops by 50%
- See ELEVENTH_ORDER_EXECUTION_REALITY_AUDIT.md Part 2C for full implementation

**Acceptance Test** (AT-Python-Backoff):
- Force Python crash (pkill -9 python)
- Verify: regime=Yellow within 1 second
- Verify: Chandelier factor ~0.5

---

## REVISED WEEK 1 EXECUTION PROTOCOL

### **Critical Change: Session Isolation**

**Original Plan**: Execute RM-1 through RM-5 in one 7.5-hour refactoring sprint
**Revised Plan**: Execute RM-1 through RM-5 as **5 strictly isolated sessions**

**Why**:
- Claude's context window is finite (~150k tokens)
- By RM-5, lifetime definitions from RM-1 are "forgotten"
- Rust compiler errors cascade (3-day debugging loop)
- **Solution**: Clear context between each RM

### **Revised Week 1 Timeline**

**MONDAY**:
- [ ] Session 1: RM-1 (GARCH daily fit + persistence) — 2.5h
  - Scope: ONLY garch_inference.rs + step_0_garch_calibration.py
  - Gate: AT-RM1 + AT-GARCH-Persistence pass
  - **Context window**: CLEAR after Session 1

**TUESDAY**:
- [ ] Session 2: RM-2 (WAL dedicated thread + bounded channel) — 3h
  - Scope: ONLY wal_actor.rs + main.rs
  - Gate: AT-RM2 + AT-WAL-Bounded pass
  - Provide exact signatures from Session 1 (copy-paste)
  - **Context window**: CLEAR after Session 2

**WEDNESDAY**:
- [ ] Session 3: RM-3 (PyO3 native FFI) — 1h
  - Scope: ONLY python_bridge.rs
  - Gate: AT-RM3 passes
  - Provide exact signatures from previous sessions
  - **Context window**: CLEAR after Session 3

- [ ] Session 4: RM-4 (Dynamic Huber delta) — 0.5h
  - Scope: ONLY student_t_kalman.rs
  - Gate: AT-RM4 passes
  - Provide exact signatures from previous sessions
  - **Context window**: CLEAR after Session 4

**THURSDAY**:
- [ ] Session 5: RM-5 (Exponential backoff + emergency freeze) — 0.5h
  - Scope: ONLY python_subprocess_manager.rs + cli.py
  - Gate: AT-RM5 + AT-Python-Backoff pass
  - Provide exact signatures from previous sessions
  - **Context window**: CLEAR after Session 5

**FRIDAY**:
- [ ] All 5 refactoring mandates merged
- [ ] 24-hour continuous paper run (validation)
- [ ] **Gate: GO FOR PHASE 8**

---

## FINAL CHECKLIST BEFORE MONDAY

- [ ] EBS expanded to 100GB (not 50GB)
- [ ] Polygon grouped endpoint verified (<1 sec, ~10k stocks)
- [ ] YFinance parallel fetch tested (<10 sec, 12 LSE tickers)
- [ ] GARCH state persistence design documented
- [ ] WAL channel bounding strategy documented
- [ ] Python emergency freeze logic documented
- [ ] Session isolation protocol understood
- [ ] All 6 acceptance tests identified (AT-EBS, AT-Polygon, AT-European, AT-GARCH, AT-WAL, AT-Python-Backoff)

---

## STATUS

✅ **All Eleventh-Order amendments are immediately implementable**
✅ **Week 1 execution is still on track (starting Monday)**
✅ **Phase 8 gate is still achievable Thursday EOD**

Execute these corrections. The institution is ready.

---

*IMMEDIATE_ACTIONS_AMENDED.md — Generated 2026-03-10*
*Classification: CRITICAL AMENDMENTS*
*Status: READY FOR EXECUTION*
