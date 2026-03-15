# PHASE 2a Deployment Checklist

**Status**: Ready for deployment
**Date**: 2026-03-15
**Estimated time to deploy**: 2-3 hours

---

## Pre-Deployment (Before Deployment)

- [ ] **Review Code**
  - [ ] Read `core/market_session_scheduler.py` (337 lines)
  - [ ] Verify no hardcoded times (all use broker queries or ZoneInfo)
  - [ ] Check error handling (fallback mode)
  - [ ] Verify thread-safety (cache locks)

- [ ] **Run Tests Locally**
  ```bash
  cd /Users/rr/nzt48-signals
  python3 -m pytest tests/test_market_session_scheduler.py -v
  ```
  - [ ] All 30 tests pass
  - [ ] No warnings
  - [ ] Runtime < 1 second

- [ ] **Run Examples**
  ```bash
  PYTHONPATH=/Users/rr/nzt48-signals python3 examples/market_scheduler_example.py
  ```
  - [ ] 8 examples run without errors
  - [ ] Output shows timezone conversions working
  - [ ] Diagnostic info shows no errors

- [ ] **Verify Imports**
  ```bash
  python3 -c "from core.market_session_scheduler import MarketSessionScheduler, get_market_scheduler"
  ```
  - [ ] No ImportError
  - [ ] No dependency issues

---

## Integration (During Deployment)

### Step 1: Code Integration into main.py

- [ ] **Add imports** (top of main.py)
  ```python
  try:
      from core.market_session_scheduler import get_market_scheduler
      _MARKET_SCHEDULER_AVAILABLE = True
  except ImportError as _e:
      _MARKET_SCHEDULER_AVAILABLE = False
      logging.getLogger("nzt48.main").warning("Market scheduler not available: %s", _e)
  ```

- [ ] **Initialize scheduler** (in main() function, after IBKR init)
  ```python
  if _MARKET_SCHEDULER_AVAILABLE and ib_gateway.ib:
      market_scheduler = get_market_scheduler(ib_client=ib_gateway.ib)
      logger.info("Market session scheduler initialized (DST-aware)")
  else:
      market_scheduler = None
  ```

- [ ] **Update hardcoded time checks**
  - [ ] Find all `datetime.utcnow().hour` comparisons
  - [ ] Replace with `market_scheduler.get_current_session()`
  - [ ] Update logging to show current session

- [ ] **Wire universe refresh scheduling**
  - [ ] Get phase timings from scheduler
  - [ ] Schedule refreshes 15min before each phase
  - [ ] Log refresh triggers

### Step 2: Tier 3 Exit Enforcement Integration

- [ ] **Hook into SessionExitEnforcer**
  - [ ] Pass market_scheduler to exit enforcer
  - [ ] Use `get_time_until_market_close()` for warnings
  - [ ] Force exit 5min before close

- [ ] **Update logging**
  - [ ] Log time until close every 15 minutes
  - [ ] Alert when < 15 minutes to close
  - [ ] Alert when < 5 minutes (force exit)

### Step 3: Docker & EC2 Preparation

- [ ] **Verify no new dependencies**
  - [ ] ib_insync: already required
  - [ ] ZoneInfo: built-in (Python 3.9+)
  - [ ] threading: built-in
  - [ ] No new pip packages needed

- [ ] **Update docker-compose.yml** (if needed)
  - [ ] No changes required (no new services)

- [ ] **Create deployment branch**
  ```bash
  git checkout -b feat/market-scheduler-phase-2a
  ```

- [ ] **Git commit**
  ```bash
  git add core/market_session_scheduler.py
  git add tests/test_market_session_scheduler.py
  git add examples/market_scheduler_example.py
  git add docs/MARKET_SESSION_SCHEDULER_INTEGRATION.md
  git add MARKET_SCHEDULER_QUICK_REFERENCE.md
  git add PHASE_2A_COMPLETION_SUMMARY.md
  git commit -m "Phase 2a: Market-driven session scheduling (timezone-adaptive, DST-aware)

  - Add MarketSessionScheduler class (337 lines)
    * Queries IB Gateway for market hours
    * Automatic DST handling via ZoneInfo
    * 24-hour cache with thread-safe locking
    * Graceful fallback to typical hours
    * No hardcoded UTC times

  - 30 comprehensive tests (all passing)
    * Timezone awareness (GMT/BST/EST/EDT/HKT)
    * DST transitions (spring/fall)
    * Cache behavior
    * Fallback mode
    * Integration workflows

  - Complete documentation
    * Integration guide (400 lines)
    * Quick reference card (250 lines)
    * 8 working examples
    * Troubleshooting guide

  Ready for deployment to EC2."
  ```

---

## EC2 Deployment

### Step 1: SSH to EC2

```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22
```

### Step 2: Pull Latest Code

```bash
cd /home/ubuntu/nzt48-signals
git fetch origin
git checkout feat/market-scheduler-phase-2a
```

### Step 3: Rebuild Docker Images

```bash
docker-compose down
docker-compose build --no-cache nzt48
docker-compose up -d nzt48
```

- [ ] Verify startup (wait 30 seconds)
  ```bash
  docker-compose ps
  # Should show nzt48 "Up"
  ```

### Step 4: Verify Logs

```bash
docker logs nzt48 --tail 50
```

- [ ] No ImportError for market_session_scheduler
- [ ] "Market session scheduler initialized" message appears
- [ ] No TypeError or AttributeError

### Step 5: Test IB Gateway Connection

```bash
docker exec -it nzt48 python3 -c "
from execution.ibkr_gateway import IBKRGateway
from core.market_session_scheduler import get_market_scheduler

ib = IBKRGateway()
scheduler = get_market_scheduler(ib_client=ib.ib)
print('Market session:', scheduler.get_current_session())
print('Phases:', list(scheduler.get_phase_timings().keys()))
"
```

- [ ] Output shows current session (LSE/US/ASIA/CLOSED)
- [ ] All 5 phases listed
- [ ] No errors

---

## Post-Deployment (After Deployment)

### Immediate Verification (Day 1)

- [ ] **Verify logs for 24 hours**
  ```bash
  # SSH to EC2
  docker logs nzt48 --follow &
  # Watch for errors related to market_scheduler
  ```
  - [ ] No ImportError
  - [ ] No AttributeError
  - [ ] No TypeError

- [ ] **Test during market hours**
  - [ ] LSE trading window (09:00-15:15 UK): verify "LSE" session
  - [ ] US trading window (13:30-20:00 UTC): verify "US" session
  - [ ] Check phase boundaries match expected times
  - [ ] Verify universe refresh happens 15min before phase

- [ ] **Test fallback mode**
  - [ ] Disconnect IB Gateway: `docker-compose pause ib-gateway`
  - [ ] Verify scheduler uses fallback (check logs)
  - [ ] Reconnect: `docker-compose unpause ib-gateway`
  - [ ] Verify scheduler recovers

- [ ] **Monitor performance**
  - [ ] No CPU spikes during market hours
  - [ ] Memory stable (no leaks)
  - [ ] Response times normal

### Short-term Testing (Week 1)

- [ ] **DST Transition Testing** (if near transition date)
  - [ ] Verify times correct during EDT/BST
  - [ ] Check cache refresh (automatic at midnight UTC)
  - [ ] No off-by-1-hour issues

- [ ] **Phase Timing Verification**
  - [ ] Phase 1: 08:00-14:30 UTC (6.5h)
  - [ ] Phase 2: 14:30-16:30 UTC (2h)
  - [ ] Phase 3: 13:30-20:00 UTC (6.5h)
  - [ ] Phase 4: 19:00-20:00 UTC (1h)
  - [ ] Phase 5: 01:30-08:00 UTC (6.5h)

- [ ] **Tier 3 Exit Testing**
  - [ ] Monitor logs for "approaching close" warnings
  - [ ] Verify forced exit < 5min before close
  - [ ] Check no positions held past market close

### Long-term Monitoring (Month 1)

- [ ] **Cache Behavior**
  - [ ] One broker query per market per day
  - [ ] Cache hits on subsequent calls
  - [ ] 24-hour expiry working

- [ ] **Error Handling**
  - [ ] Graceful degradation if IB fails
  - [ ] Correct fallback to typical hours
  - [ ] Diagnostic info accurate

- [ ] **Integration Quality**
  - [ ] Universe refresh happening correctly
  - [ ] Phase-aware logic working
  - [ ] No data anomalies

---

## Rollback Plan (If Issues)

If critical issues found:

1. **Immediate Rollback**
   ```bash
   git checkout main
   docker-compose down
   docker-compose up -d
   docker logs nzt48 --tail 20
   ```

2. **Identify Issue**
   - Check if error in market_scheduler.py
   - Check if integration in main.py incorrect
   - Check if cache issue

3. **Fix**
   - Fix code locally
   - Re-test locally
   - Redeploy

4. **Prevention**
   - Add more tests for specific scenario
   - Update documentation
   - Prevent in future deployments

---

## Monitoring Commands

### Check Current Session

```bash
docker exec nzt48 python3 -c "
from core.market_session_scheduler import get_market_scheduler
s = get_market_scheduler()
print(f'Session: {s.get_current_session()}')
print(f'Minutes to close: {s.get_time_until_market_close()}')
"
```

### Check Diagnostic Info

```bash
docker exec nzt48 python3 -c "
from core.market_session_scheduler import get_market_scheduler
s = get_market_scheduler()
import json
print(json.dumps(s.get_diagnostic_info(), indent=2, default=str))
"
```

### Check Phase Timings

```bash
docker exec nzt48 python3 -c "
from core.market_session_scheduler import get_market_scheduler
s = get_market_scheduler()
for phase, (start, end) in s.get_phase_timings().items():
    print(f'{phase:20s}: {start} → {end}')
"
```

### Monitor Logs in Real-time

```bash
docker logs nzt48 --follow | grep -i "market_session\|phase\|close"
```

---

## Success Criteria

### Deployment is successful if:

- [ ] Code builds without errors
- [ ] All 30 tests pass
- [ ] No ImportError on EC2
- [ ] Market session correctly identified during trading hours
- [ ] Phase timings match expected values
- [ ] Universe refresh scheduled 15min before phases
- [ ] Tier 3 exit enforcement active
- [ ] No CPU/memory spikes
- [ ] Logs show normal operation
- [ ] Fallback works when IB disconnected

### Deployment should rollback if:

- [ ] Repeated ImportError despite fixes
- [ ] Phase timings incorrect (>5min deviation)
- [ ] CPU spikes during market hours
- [ ] Memory leak detected
- [ ] Frequent timeouts connecting to broker
- [ ] Off-by-1-hour issues during DST

---

## Estimated Timeline

| Task | Time | Status |
|------|------|--------|
| Code review | 15 min | Ready |
| Local testing | 10 min | ✅ Done |
| Example run | 5 min | ✅ Done |
| Git commit | 5 min | Ready |
| EC2 pull & build | 10 min | Ready |
| Verification | 10 min | Ready |
| **Total** | **55 min** | **Ready** |

---

## Documentation References

- **Integration**: `/Users/rr/nzt48-signals/docs/MARKET_SESSION_SCHEDULER_INTEGRATION.md`
- **Quick Ref**: `/Users/rr/nzt48-signals/MARKET_SCHEDULER_QUICK_REFERENCE.md`
- **Examples**: `/Users/rr/nzt48-signals/examples/market_scheduler_example.py`
- **Summary**: `/Users/rr/nzt48-signals/PHASE_2A_COMPLETION_SUMMARY.md`

---

## Contact & Support

- **Issue**: Check diagnostic_info() output
- **Questions**: See quick reference card
- **Rollback**: See rollback plan above
- **Escalation**: Check test_market_session_scheduler.py for working patterns

---

## Sign-Off

- [ ] Code reviewed
- [ ] Tests passing
- [ ] Examples verified
- [ ] Deployment approved
- [ ] Ready to deploy

**Status**: ✅ Ready for EC2 deployment

**Date**: 2026-03-15
**Deployment window**: Recommended between 21:00-23:00 UTC (after US market close)
