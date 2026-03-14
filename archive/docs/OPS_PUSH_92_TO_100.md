# NZT-48 Ops Runbook: 92 → 100 (PAPER → LIMITED LIVE)

## Current State: 92/100 (Paper Mode)

| Parameter         | Value                                      |
|-------------------|--------------------------------------------|
| Mode              | PAPER (simulated fills, no real capital)    |
| Starting Equity   | £10,000                                    |
| Universe          | 12 CORE ETPs (.L suffix, LSE-listed)       |
| Strategy          | S15 "2% Daily Target" (primary)            |
| Infrastructure    | EC2 + Docker Compose (nzt48 + dashboard)   |
| Scan Cycle        | 60s continuous via APScheduler             |
| Delivery          | Dual PDF (Momentum + Risk) + Telegram      |

### CORE Universe (12 Active ETPs)

```
QQQ3.L   3LUS.L   3SEM.L   GPT3.L
NVD3.L   TSL3.L   TSM3.L   MU2.L
QQQS.L   3USS.L   QQQ5.L   SP5L.L
```

### Why 92/100?

The system is architecturally complete. Data flows, strategies fire, PDFs generate,
Telegram delivers. What remains is **operational proof** — sustained reliability
under real market conditions with zero human intervention. The last 8 points are
earned through time, discipline, and passing every gate below.

---

## 10 Go/No-Go Gates

Every gate must be GREEN before progressing to LIMITED LIVE. No exceptions.
No "close enough". If a gate fails, the clock resets for that gate.

### G1: Data Reliability

| Metric                | Threshold                              |
|-----------------------|----------------------------------------|
| Data completeness     | >= 0.85 for ALL 12 CORE tickers        |
| Measurement window    | 30 consecutive calendar days           |
| What counts           | Bars fetched / bars expected per day   |
| Failure mode          | Any ticker below 0.85 on any day       |

**How to verify:**
```bash
docker exec nzt48 python -c "
from data_hub import DataHub
dh = DataHub()
dh.print_reliability_report(days=30)
"
```

**Pass criteria:** Every ticker shows >= 0.85 for every day in the trailing 30-day
window. A single day below threshold for any ticker resets the 30-day clock.

---

### G2: Win Rate

| Metric                | Threshold                              |
|-----------------------|----------------------------------------|
| Win rate              | >= 45% over 100+ resolved outcomes     |
| Resolved outcome      | Position that hit target OR stop        |
| Measurement           | Cumulative since paper launch          |

**How to verify:**
```bash
docker exec nzt48 python -c "
from core.edge_ledger import EdgeLedger
el = EdgeLedger()
el.print_summary()
"
```

**Pass criteria:** At least 100 resolved outcomes with win rate >= 45%.
Unresolved (still open or timed out) positions do not count.

---

### G3: System Uptime

| Metric                | Threshold                              |
|-----------------------|----------------------------------------|
| Uptime                | >= 99% over 30 calendar days           |
| Measurement           | Minutes online / total market minutes   |
| Market hours          | 08:00-16:30 London time, Mon-Fri       |
| Excludes              | Planned maintenance (max 2 per month)  |

**How to verify:**
```bash
docker exec nzt48 python -c "
from system_watchdog import SystemWatchdog
sw = SystemWatchdog()
sw.print_uptime_report(days=30)
"
```

**Pass criteria:** 99%+ uptime during market hours. Each planned maintenance
window must be logged in advance and last no more than 15 minutes.

---

### G4: Zero HALTED Events

| Metric                | Threshold                              |
|-----------------------|----------------------------------------|
| HALTED events         | 0 in last 14 calendar days             |
| What counts           | Any system HALT (crash, OOM, deadlock) |
| Excludes              | Graceful shutdowns, planned restarts   |

**How to verify:**
```bash
docker logs nzt48 --since 336h 2>&1 | grep -i "HALT\|FATAL\|OOM\|deadlock"
```

**Pass criteria:** Zero matches. Any HALTED event resets the 14-day clock.

---

### G5: Cost Model Calibrated

| Metric                | Threshold                              |
|-----------------------|----------------------------------------|
| Paper vs expected     | Within 20% of expected fill prices     |
| Spread model          | Calibrated against live L2 data        |
| Commission model      | Reflects actual broker fee schedule     |

**How to verify:**
```bash
docker exec nzt48 python -c "
from execution.cost_model import CostModel
cm = CostModel()
cm.print_calibration_report()
"
```

**Pass criteria:** Simulated fill prices are within 20% of what real execution
would produce, validated against at least 50 paper trades.

---

### G6: Edge Stability

| Metric                | Threshold                              |
|-----------------------|----------------------------------------|
| Strategy stability    | No strategy with stability < 0.5       |
| Sample size           | n >= 20 trades per strategy            |
| Stability formula     | Rolling Sharpe consistency metric      |

**How to verify:**
```bash
docker exec nzt48 python -c "
from core.edge_ledger import EdgeLedger
el = EdgeLedger()
el.print_stability_report(min_n=20)
"
```

**Pass criteria:** Every active strategy with 20+ trades shows stability >= 0.5.
Strategies with fewer than 20 trades are excluded (insufficient data).

---

### G7: Drawdown Recovery

| Metric                | Threshold                              |
|-----------------------|----------------------------------------|
| Recovery events       | At least 1 YELLOW drawdown recovered   |
| YELLOW threshold      | -3% to -5% portfolio drawdown          |
| Recovery              | Return to previous high-water mark     |

**How to verify:**
```bash
docker exec nzt48 python -c "
from risk_officer.drawdown_tracker import DrawdownTracker
dt = DrawdownTracker()
dt.print_recovery_history()
"
```

**Pass criteria:** The system has experienced at least one YELLOW drawdown event
and successfully recovered to the previous high-water mark without manual
intervention. This proves the system can handle adverse conditions.

---

### G8: Kill Switch Tested

| Metric                | Threshold                              |
|-----------------------|----------------------------------------|
| Kill methods          | All 3 methods verified                 |
| Method 1              | Telegram /kill command                 |
| Method 2              | Dashboard emergency stop button        |
| Method 3              | Docker stop (infrastructure level)     |

**How to verify:**

Test each method during a non-market window:

```bash
# Method 1: Telegram kill
# Send /kill to the bot — verify all positions flatten, no new signals

# Method 2: Dashboard kill
# Click EMERGENCY STOP on dashboard — verify same behavior

# Method 3: Docker kill
docker-compose stop nzt48
# Verify container stops, no orphaned processes
docker-compose start nzt48
# Verify clean restart, state recovery
```

**Pass criteria:** All 3 methods tested within the last 30 days. Each test
must be logged with timestamp and outcome in `artifacts/kill_switch_tests.json`.

---

### G9: PDF Consistency

| Metric                | Threshold                              |
|-----------------------|----------------------------------------|
| Contradictions        | 0 in last 7 calendar days              |
| What counts           | PDF1 says BUY, PDF2 says regime RED    |
| Verification          | Manual review of last 7 PDF pairs      |

**How to verify:**
```bash
docker exec nzt48 python -c "
from delivery.pdf_consistency import PDFConsistencyChecker
pc = PDFConsistencyChecker()
pc.check_last_n_days(7)
"
```

**Pass criteria:** Zero contradictions between PDF1 (Momentum & Opportunity) and
PDF2 (Risk & Structural) for the trailing 7 days. A contradiction is any case
where PDF1 recommends action that PDF2's risk assessment would prohibit.

---

### G10: Telegram Reliability

| Metric                | Threshold                              |
|-----------------------|----------------------------------------|
| Delivery rate         | 99%+ successful delivery               |
| False TRADE signals   | 0 false TRADE-level signals            |
| Measurement window    | Last 30 days                           |

**How to verify:**
```bash
docker exec nzt48 python -c "
from bots.telegram_bot import TelegramBot
tb = TelegramBot()
tb.print_delivery_report(days=30)
"
```

**Pass criteria:** 99%+ of attempted Telegram messages were successfully delivered
(confirmed by Telegram API response). Zero TRADE-level signals that were later
determined to be false (no valid setup existed).

---

## Gate Status Dashboard

```
G1  Data Reliability     [ ] GREEN  [ ] RED   Last checked: ____-__-__
G2  Win Rate             [ ] GREEN  [ ] RED   Last checked: ____-__-__
G3  System Uptime        [ ] GREEN  [ ] RED   Last checked: ____-__-__
G4  Zero HALTED          [ ] GREEN  [ ] RED   Last checked: ____-__-__
G5  Cost Model           [ ] GREEN  [ ] RED   Last checked: ____-__-__
G6  Edge Stability       [ ] GREEN  [ ] RED   Last checked: ____-__-__
G7  Drawdown Recovery    [ ] GREEN  [ ] RED   Last checked: ____-__-__
G8  Kill Switch          [ ] GREEN  [ ] RED   Last checked: ____-__-__
G9  PDF Consistency      [ ] GREEN  [ ] RED   Last checked: ____-__-__
G10 Telegram Reliability [ ] GREEN  [ ] RED   Last checked: ____-__-__
```

**All 10 GREEN required to proceed to LIMITED LIVE.**

---

## Weekly Progression Plan

### Phase 1: Baseline (Weeks 1-2)

**Objective:** Establish reliable data collection and baseline metrics.

- [ ] Confirm all 12 CORE tickers returning data consistently
- [ ] Verify APScheduler 60s cycle stability
- [ ] Establish data reliability baseline per ticker
- [ ] Document any yfinance .L ticker issues (delisted, empty bars)
- [ ] Verify dual PDF generation runs without errors daily
- [ ] Confirm Telegram delivery for all message types
- [ ] Run `diagnostics_live.py` daily — log all anomalies
- [ ] Begin G1 30-day clock

**Daily log template:**
```
Date: YYYY-MM-DD
Tickers OK: __/12
Scan cycles: ___
Errors: ___
PDF generated: Y/N
Telegram sent: Y/N
Notes: ___
```

### Phase 2: Tuning (Weeks 3-4)

**Objective:** Calibrate cost model and fine-tune signal parameters.

- [ ] Collect L2 spread data for all 12 CORE ETPs
- [ ] Calibrate cost model against observed spreads
- [ ] Tune S15 scoring thresholds based on paper results
- [ ] Review and adjust volatility regime boundaries
- [ ] Validate sector rotation signals against actual sector moves
- [ ] Calibrate predictive scoring model
- [ ] Begin G2 outcome tracking (need 100+ resolved)
- [ ] Test kill switch Method 1 (Telegram /kill)

### Phase 3: Calibration (Weeks 5-8)

**Objective:** Accumulate sufficient trade history for statistical validity.

- [ ] Reach 50+ resolved paper trades
- [ ] Assess win rate trajectory — is it trending toward 45%?
- [ ] Evaluate edge stability per strategy
- [ ] Monitor for YELLOW drawdown events (need at least 1 for G7)
- [ ] Refine PDF consistency checks
- [ ] Test kill switch Method 2 (Dashboard emergency stop)
- [ ] G1 30-day clock should complete during this phase
- [ ] Begin G3 uptime measurement

### Phase 4: Drills (Weeks 9-10)

**Objective:** Stress-test operational procedures and emergency response.

- [ ] Simulate network failure during market hours — verify recovery
- [ ] Simulate data feed failure — verify graceful degradation
- [ ] Simulate Docker OOM — verify watchdog restart
- [ ] Test kill switch Method 3 (Docker stop/start)
- [ ] Run full rollback drill (restore from last known good state)
- [ ] Verify all emergency contacts and escalation paths
- [ ] Practice the LIMITED LIVE cutover procedure (dry run)
- [ ] Reach 100+ resolved paper trades

### Phase 5: Review (Weeks 11-12)

**Objective:** Final gate review and go/no-go decision.

- [ ] Review all 10 gates — document status of each
- [ ] Generate final paper trading report (equity curve, metrics)
- [ ] Review all HALTED events and root causes
- [ ] Verify cost model accuracy against latest spread data
- [ ] Conduct "pre-mortem" — what could go wrong in limited live?
- [ ] Document all known issues and mitigations
- [ ] Prepare LIMITED LIVE configuration (see below)
- [ ] Sign off: ALL 10 GATES GREEN

### Phase 6: Limited Live (Week 13+)

**Objective:** Begin real capital deployment with maximum constraints.

- [ ] Deploy LIMITED LIVE configuration
- [ ] Monitor first live trade end-to-end
- [ ] Compare live fill vs paper fill — validate cost model
- [ ] Daily review for first 5 trading days
- [ ] Weekly review thereafter
- [ ] Scale-up decisions only after 20+ live trades

---

## LIMITED LIVE Parameters

When all 10 gates are GREEN, transition to LIMITED LIVE with these constraints:

```yaml
mode: LIMITED_LIVE

capital:
  max_deployed: 1000          # £1,000 maximum capital at risk
  max_positions: 1            # 1 position at a time, no exceptions
  max_position_pct: 100       # Can use full £1,000 allocation
  reserve: 9000               # £9,000 remains in cash

universe:
  allowed: CORE_ONLY          # 12 CORE ETPs only
  no_new_tickers: true        # No universe expansion in limited live
  no_inverse: false           # Inverse ETPs allowed (QQQS, 3USS)

risk:
  max_daily_loss: 50          # £50 max daily loss → auto-halt
  max_weekly_loss: 150        # £150 max weekly loss → auto-halt
  max_drawdown_pct: 5         # 5% drawdown from HWM → auto-halt
  stop_loss: 1_ATR            # Per-position stop unchanged

strategy:
  allowed: [S15]              # S15 only in limited live
  min_score: 75               # Higher minimum score threshold
  require_regime_green: true  # Must be GREEN regime to trade

execution:
  order_type: LIMIT           # Limit orders only, no market
  max_slippage_bps: 50        # 50 bps max slippage tolerance
  confirm_before_send: true   # Require confirmation before order

monitoring:
  alert_on_fill: true         # Telegram alert on every fill
  alert_on_cancel: true       # Telegram alert on every cancel
  daily_pnl_report: true      # End-of-day P&L report
  heartbeat_interval: 300     # 5-minute heartbeat checks
```

### Transition Procedure

1. **Pre-market (before 07:30 London time):**
   ```bash
   # Stop paper mode
   docker-compose stop nzt48

   # Update config
   # In settings.yaml: mode: LIMITED_LIVE
   # Apply all parameters from above

   # Restart with new config
   docker-compose up -d nzt48

   # Verify mode
   docker exec nzt48 python -c "
   from config import settings
   print(f'Mode: {settings.MODE}')
   print(f'Max capital: {settings.MAX_DEPLOYED}')
   print(f'Max positions: {settings.MAX_POSITIONS}')
   "
   ```

2. **Verification (07:30-07:55):**
   - Confirm dashboard shows LIMITED_LIVE mode
   - Confirm Telegram bot responds to /status with correct mode
   - Confirm risk limits are loaded correctly
   - Confirm broker connection is live (API health check)

3. **Market open (08:00):**
   - Monitor first scan cycle
   - Verify no immediate signals fire (system should wait for setup)
   - Confirm heartbeat messages arriving every 5 minutes

---

## Daily Operational Checklist

### Morning (07:30 - 08:00 London)

- [ ] SSH into EC2: `ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28`
- [ ] Check container health: `docker ps` — both containers running
- [ ] Check logs for overnight errors: `docker logs nzt48 --since 12h 2>&1 | grep -i error`
- [ ] Verify data feed: `docker exec nzt48 python -c "from data_hub import DataHub; DataHub().quick_check()"`
- [ ] Check disk space: `df -h` — must be > 20% free
- [ ] Check memory: `free -m` — must be > 500MB available
- [ ] Review overnight PDF (if generated): check `artifacts/` for latest
- [ ] Verify Telegram bot is responsive: send `/status`
- [ ] Check dashboard: `http://100.55.69.28:3001` — all panels green

### Midday (12:00 - 12:15 London)

- [ ] Check for any open positions: `docker exec nzt48 python -c "from execution.virtual_trader import VirtualTrader; VirtualTrader().print_positions()"`
- [ ] Review scan health: `docker exec nzt48 python -c "from core.scan_health import ScanHealthTracker; ScanHealthTracker().print_status()"`
- [ ] Check edge ledger for new outcomes: `docker exec nzt48 python -c "from core.edge_ledger import EdgeLedger; EdgeLedger().print_today()"`
- [ ] Verify no HALTED events: `docker logs nzt48 --since 4h 2>&1 | grep -i halt`
- [ ] Quick dashboard check — any amber/red indicators?

### Evening (17:00 - 17:15 London)

- [ ] Market closed — review day's activity
- [ ] Check final P&L: `docker exec nzt48 python -c "from core.edge_ledger import EdgeLedger; EdgeLedger().print_daily_pnl()"`
- [ ] Verify evening PDF generated
- [ ] Review Telegram message log — any failed deliveries?
- [ ] Check container resource usage: `docker stats --no-stream`
- [ ] Log daily metrics in `artifacts/daily_ops_log.json`
- [ ] Update gate status dashboard if any gate changed

---

## Emergency Procedures

### Kill Switch Activation

**When to activate:**
- Any unexpected live order execution
- System behaving erratically (rapid-fire signals, contradictory actions)
- Broker API returning unexpected responses
- Drawdown exceeding limits
- Any uncertainty — when in doubt, kill it

**Method 1: Telegram (fastest)**
```
Send /kill to the NZT-48 bot
Expected response: "KILLED. All positions flattened. No new signals."
```

**Method 2: Dashboard**
```
Navigate to http://100.55.69.28:3001
Click the red EMERGENCY STOP button (top right)
Verify confirmation message
```

**Method 3: Infrastructure (nuclear option)**
```bash
ssh -i ~/.ssh/nzt48-key.pem ubuntu@100.55.69.28
docker-compose stop nzt48
# Verify: docker ps — nzt48 should not be listed
# To restart: docker-compose start nzt48
```

### Rollback Procedure

If the system needs to return to a known good state:

```bash
# 1. Kill the system
docker-compose stop nzt48

# 2. Backup current state
cp -r artifacts/ artifacts_backup_$(date +%Y%m%d_%H%M%S)/
cp config/settings.yaml config/settings_backup_$(date +%Y%m%d_%H%M%S).yaml

# 3. Restore last known good config
# (Identify the last good config from git log)
git log --oneline config/settings.yaml
git checkout <commit_hash> -- config/settings.yaml

# 4. Restart
docker-compose up -d nzt48

# 5. Verify
docker logs nzt48 --tail 20
```

### Communication Protocol

| Event               | Action                                     | Timeline    |
|---------------------|--------------------------------------------|-------------|
| System HALT         | Check logs, restart if safe                | Within 5min |
| Data feed failure   | Switch to fallback, monitor                | Within 15min|
| Unexpected trade    | Kill switch immediately                    | Immediate   |
| Drawdown YELLOW     | Review, no action unless RED               | Within 1hr  |
| Drawdown RED        | Kill switch, full review                   | Immediate   |
| Cost model drift    | Recalibrate, pause new entries             | Within 1day |
| Gate regression     | Document, reset gate clock                 | Within 1day |

---

## Monitoring Dashboard Usage Guide

### Dashboard URL
```
http://100.55.69.28:3001
```

### Key Panels

1. **System Status (top bar)**
   - Mode indicator: PAPER / LIMITED_LIVE / HALTED
   - Uptime counter
   - Last scan timestamp (should be < 90s old)
   - Memory / CPU usage

2. **Universe Panel (left)**
   - All 12 CORE tickers with latest price, volume, data health
   - Color coding: GREEN = healthy, AMBER = degraded, RED = no data
   - Click ticker for detailed chart and signal history

3. **Signal Panel (center)**
   - Current S15 candidates ranked by score
   - Active positions (if any) with P&L
   - Recent signal history (last 7 days)

4. **Risk Panel (right)**
   - Regime indicator (GREEN / AMBER / RED)
   - Drawdown from HWM
   - Sector correlation matrix
   - Volatility regime state

5. **Edge Ledger (bottom)**
   - Win rate (running)
   - Resolved outcomes table
   - Equity curve chart
   - Strategy stability metrics

### Dashboard Alerts

The dashboard will show prominent alerts for:
- Any gate regression (was GREEN, now RED)
- HALTED events
- Data feed failures lasting > 5 minutes
- Drawdown exceeding YELLOW threshold
- Telegram delivery failures

---

## Escalation Matrix

| Severity | Description                          | Response                       | Escalation        |
|----------|--------------------------------------|--------------------------------|--------------------|
| LOW      | Minor data gap, single ticker        | Log, monitor                   | None               |
| MEDIUM   | Multiple ticker failures, PDF error  | Investigate within 1hr         | Self-review        |
| HIGH     | System HALT, unexpected behavior     | Kill switch, investigate       | Full post-mortem   |
| CRITICAL | Unexpected live execution            | Kill switch immediately        | Full audit, pause  |

### Post-Incident Review Template

```markdown
## Incident Report

**Date:** YYYY-MM-DD
**Severity:** LOW / MEDIUM / HIGH / CRITICAL
**Duration:** HH:MM

### What happened?
[Description]

### Root cause
[Analysis]

### Impact
[Trades affected, P&L impact, gate regressions]

### Resolution
[What was done to fix it]

### Prevention
[What will prevent recurrence]

### Gate impact
[Which gates were affected, new reset dates]
```

---

## Appendix: The 2% Compounding Law

Never lose sight of the mission:

```
£10,000 x (1.02)^252 = £1,485,757.36
```

That is 14,757.57% annualised. The system does not need to be right every day.
It needs to be right *enough* days, with controlled losses on wrong days. S15
finds ONE stock per day capable of a 2% move. The math does the rest.

**LIMITED LIVE is not about making money. It is about proving the system works
with real capital, real fills, real slippage, and real emotions. Keep the size
small. Keep the discipline absolute. The scale comes later.**
