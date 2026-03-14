# BUILD WEEK 9-10: Adaptive Learning & Continuous Optimization System

## Overview

Complete system for automated, transparent learning from daily trading results with full audit trail and rollback capability.

**Core Principle**: READ-ONLY analysis by default. ALL optimization decisions require explicit manual approval before deployment.

## Architecture

### 4 Core Modules

#### 1. Daily Optimization (`learning/daily_optimization.py`)
**Purpose**: End-of-day analysis of trading performance to identify improvement opportunities.

**What it does**:
- Analyzes all trades executed that day
- Identifies top 5 winners: what made them work?
  - Which tier (BLUE_CHIP, SPECIALIST, EXPANSION)?
  - Which early detection signals fired?
  - Confidence level and regime?
- Identifies top 5 losers: what went wrong?
  - False signal (high confidence, no move)?
  - Entry too late (after 2%+ already moved)?
  - Regime mismatch?
  - Whipsaw (stopped out, then reversed)?
- Computes daily metrics:
  - Win rate trends (7-day moving average)
  - Confidence calibration (do high-conf trades actually win more?)
  - Tier performance (which tier has best win rate?)
- **Generates recommendations** (requires approval):
  - Tighten confidence threshold if win rate dropping
  - Lower early detection requirements if entering too late
  - Add cooldown rules if cascades detected
  - Reduce allocation to underperforming tier

**Database Tables**:
- `daily_metrics_history`: Daily summary metrics
- `trade_factor_analysis`: Per-trade win/loss factor breakdown
- `optimization_recommendations`: Pending approval
- `learning_audit_log`: All decisions + reversions

**Runs**: Daily at 17:00 UTC (12:00 PM ET)

---

#### 2. Signal Decay Detector (`learning/signal_decay_detector.py`)
**Purpose**: Continuous monitoring of signal quality using Deflated Sharpe Ratio.

**What it does**:
- Calculates DSR for each signal (S1-S14)
- **Decay detected** when DSR < 0.5
  - Automatically disables signal for 7 days
  - Reduces confidence weight
  - Logs reasoning
- **Regime shift** detected when 3+ signals decay simultaneously
  - Triggers urgent alert
  - Recommends recalibrating adaptive ladder
  - Suggests reducing signal weights

**DSR Formula**:
```
DSR = SR * sqrt(1 - (k-1)/(N-1))
where:
  SR = Sharpe Ratio
  k = number of tested parameters (assume 5)
  N = number of trades
```

**Database Tables**:
- `signal_decay_history`: DSR tracking per signal
- `signal_disabled_log`: When signals disabled/re-enabled + reason

**Runs**: Every 60 minutes (continuous monitoring)

---

#### 3. Weekly Backtest (`learning/weekly_backtest.py`)
**Purpose**: Validate strategy assumptions by comparing model backtest vs actual execution.

**What it does**:
- Every Sunday 23:00 UTC: backtests past week's data
- Compares model predictions vs actual paper trades
- **Investigates if gap > 5%**:
  - Poor fill quality?
  - Regime flapping (too many regime changes)?
  - Data gaps or signal rejections?
  - Model assumption violations?
- Generates weekly performance report with:
  - "What worked well" (keep it)
  - "What degraded" (reduce weight)
  - Volatility forecast
  - Regime prediction for coming week

**Database Tables**:
- `weekly_backtest_results`: Model vs actual comparison
- `weekly_performance_reports`: Strategic summary

**Runs**: Sunday 23:00 UTC

---

#### 4. Performance Reporter (`monitoring/performance_report.py`)
**Purpose**: Comprehensive performance reporting at 3 frequencies.

**Daily Summary (17:15 UTC)**:
- Trade count, win rate, avg profit per winner
- Entry quality metrics (% in first rung, avg confidence vs actual)
- Risk summary (max drawdown, heat level, largest loss)
- Best signal of the day + win rate

**Weekly Summary (Sunday 23:15 UTC)**:
- Rolling 7-day win rate, avg R, Sharpe ratio
- Tier performance breakdown
- Signal effectiveness (which signals fire most? win most?)
- Best/worst signal
- Regime performance

**Monthly Summary (1st of month, 09:00 UTC)**:
- 30-day return %, Sharpe, max drawdown
- Signal evolution (what changed?)
- Regime patterns (when did we do best?)
- Best/worst day analysis

**Database Tables**:
- `daily_summary_reports`: End-of-day snapshots
- `weekly_summary_reports`: 7-day rolling analysis
- `monthly_summary_reports`: 30-day institutional reporting

---

## Scheduler Integration

### `scripts/adaptive_learning_scheduler.py`

APScheduler-based job orchestration with 7 registered jobs:

```
DAILY (UTC):
  17:00 → DailyOptimizer.run_nightly_analysis()
  17:15 → GeneratePerformanceReport.daily_summary()

WEEKLY (UTC):
  Sun 23:00 → WeeklyBacktester.run_weekly_backtest()
  Sun 23:15 → GeneratePerformanceReport.weekly_summary()

MONTHLY (UTC):
  1st 09:00 → GeneratePerformanceReport.monthly_summary()

CONTINUOUS (UTC):
  Every 60min → SignalDecayDetector.detect_decay()
```

### Telegram Integration

All jobs send rich alerts to Telegram:
- Daily optimization summaries
- Daily performance (trades, WR, return)
- Weekly backtest results (model vs actual gap)
- Weekly performance (7-day Sharpe, best signal)
- Monthly performance (30-day return, Sharpe)
- **Critical alerts**: Regime shift detected, job errors

**Setup**:
```bash
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"
```

---

## Starting the Scheduler

### Option 1: Standalone
```python
from scripts.adaptive_learning_scheduler import get_scheduler

scheduler = get_scheduler()
scheduler.start()  # Runs jobs on schedule
scheduler.shutdown()  # Graceful shutdown
```

### Option 2: Integrated with main.py
```python
# In main.py async loop
from scripts.adaptive_learning_scheduler import get_scheduler

scheduler = get_scheduler()
scheduler.start()

# ... main trading loop ...

# On shutdown
scheduler.shutdown()
```

### Option 3: Systemd Service
```ini
[Unit]
Description=NZT-48 Adaptive Learning Scheduler
After=network.target

[Service]
Type=simple
User=nzt48
WorkingDirectory=/Users/rr/nzt48-signals
ExecStart=/usr/bin/python3 -m scripts.adaptive_learning_scheduler
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Database Schema

### learning_audit_log
```sql
CREATE TABLE learning_audit_log (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    optimization_id TEXT NOT NULL,
    decision_type TEXT NOT NULL,           -- "recommendation_approved", "recommendation_rejected", "rollback"
    old_value REAL,
    new_value REAL,
    reason TEXT,
    confidence_score REAL,
    status TEXT,                           -- "PENDING", "APPROVED", "APPLIED", "REVERTED"
    reverted_at TEXT,
    revert_reason TEXT,
    UNIQUE(optimization_id)
);
```

### daily_metrics_history
```sql
CREATE TABLE daily_metrics_history (
    date TEXT PRIMARY KEY,
    trades_taken INTEGER,
    win_rate REAL,
    avg_r_winner REAL,
    avg_r_loser REAL,
    confidence_calibration REAL,
    avg_entry_quality REAL,
    tier_performance TEXT,                 -- JSON: {tier -> win_rate}
    regime_performance TEXT,               -- JSON: {regime -> win_rate}
    signal_effectiveness TEXT,             -- JSON: {pattern -> win_rate}
    computed_at TEXT
);
```

### trade_factor_analysis
```sql
CREATE TABLE trade_factor_analysis (
    trade_id TEXT PRIMARY KEY,
    date TEXT,
    ticker TEXT,
    direction TEXT,
    pnl_r REAL,
    confidence REAL,
    regime TEXT,
    entry_quality REAL,
    tier TEXT,
    patterns TEXT,                         -- JSON: [pattern1, pattern2, ...]
    why_won_lost TEXT,
    analyzed_at TEXT
);
```

### signal_decay_history
```sql
CREATE TABLE signal_decay_history (
    signal_name TEXT,
    date TEXT,
    trades_count INTEGER,
    win_rate REAL,
    sharpe_ratio REAL,
    deflated_sharpe_ratio REAL,
    decay_detected INTEGER,
    status TEXT,                           -- "ACTIVE", "DEGRADED", "DISABLED"
    recorded_at TEXT,
    PRIMARY KEY (signal_name, date)
);
```

---

## Workflow: Approving & Deploying Changes

### 1. Review Pending Recommendations
```python
from learning.daily_optimization import DailyOptimizer

optimizer = DailyOptimizer()
pending = optimizer.get_pending_recommendations()

for rec in pending:
    print(f"{rec['category']}: {rec['current_value']} → {rec['suggested_value']}")
    print(f"Confidence: {rec['confidence_score']:.1%}")
    print(f"Reason: {rec['reason']}\n")
```

### 2. Approve or Reject
```python
# Approve
optimizer.approve_recommendation("rec-abc123", approve=True)

# Or reject
optimizer.approve_recommendation("rec-abc123", approve=False)
```

### 3. Deploy to Strategy
Approved recommendations require **manual implementation**:
- Update `config/settings.yaml` parameters
- Restart strategy with `docker compose restart nzt48`
- Monitor for 5+ trades before considering successful

### 4. Rollback if Necessary
If metrics degrade after deployment:
```python
optimizer.rollback_change("rec-abc123", reason="Confidence threshold too high, win rate dropped to 35%")
```

All rollbacks logged in `learning_audit_log`.

---

## Critical Safeguards

### 1. Read-Only Analysis
- NO automated parameter changes
- ALL decisions logged with confidence scores
- Manual approval required for any deployment

### 2. Approval Workflow
```
Detection → Recommendation → Manual Review → Approval → Deployment → Monitoring → Rollback (if needed)
```

### 3. Audit Trail
Every decision tracked in `learning_audit_log`:
- WHO (user ID)
- WHAT (parameter, old → new)
- WHY (reason + confidence)
- WHEN (timestamp)
- STATUS (pending/approved/applied/reverted)

### 4. Rollback Capability
```python
optimizer.rollback_change(optimization_id, reason="Win rate degraded 45% → 38%")
```
- Reverts to previous parameter
- Logs reason
- Updates audit trail

### 5. Regime Shift Detection
If 3+ signals decay simultaneously:
```
🚨 REGIME SHIFT DETECTED
Decayed signals: S1, S3, S7, S11
Actions: Recalibrate adaptive ladder
Status: MANUAL REVIEW REQUIRED
```

---

## Daily Workflow (Operations)

### 12:00 PM ET (17:00 UTC)
1. Daily optimization analysis completes
2. Reviews top winners/losers
3. Generates recommendations
4. Email summary sent

### 12:15 PM ET (17:15 UTC)
1. Daily performance report generated
2. Telegram alert with WR, return, heat level
3. Entry quality metrics reviewed

### 6:00 PM Sunday ET (23:00 UTC)
1. Weekly backtest runs
2. Model vs actual gap calculated
3. If gap > 5%: investigation report generated

### 6:15 PM Sunday ET (23:15 UTC)
1. Weekly performance summary generated
2. Telegram alert with Sharpe, rolling WR, best signal

### Monthly (1st @ 9:00 AM UTC)
1. Monthly report generated
2. 30-day metrics compiled
3. Signal evolution tracked

---

## Monitoring the System

### Check Job Status
```python
scheduler = get_scheduler()
for job in scheduler.get_jobs():
    print(f"{job.id}: {job.next_run_time}")
```

### Pause a Job
```python
scheduler.pause_job("daily_optimization")
```

### Resume a Job
```python
scheduler.resume_job("daily_optimization")
```

### Manually Trigger a Job
```python
from learning.daily_optimization import DailyOptimizer
optimizer = DailyOptimizer()
result = optimizer.run_nightly_analysis()
print(result)
```

---

## File Structure

```
/Users/rr/nzt48-signals/
├── learning/
│   ├── daily_optimization.py          (350 lines)
│   ├── signal_decay_detector.py       (200 lines)
│   ├── weekly_backtest.py             (150 lines)
│   └── __init__.py
├── monitoring/
│   ├── performance_report.py          (400 lines)
│   └── __init__.py
├── scripts/
│   └── adaptive_learning_scheduler.py (500 lines)
└── data/
    └── nzt48.db (SQLite)
        ├── daily_metrics_history
        ├── trade_factor_analysis
        ├── optimization_recommendations
        ├── learning_audit_log
        ├── signal_decay_history
        ├── signal_disabled_log
        ├── weekly_backtest_results
        ├── weekly_performance_reports
        ├── daily_summary_reports
        ├── weekly_summary_reports
        └── monthly_summary_reports
```

---

## Performance Targets

### Daily
- Analysis completes in < 30 seconds
- Recommendations generated within 1 minute
- Telegram alert sent within 2 minutes

### Weekly
- Backtest completes in < 5 minutes
- Model-reality gap < 5% (OK status)
- Report generated within 10 minutes

### Monthly
- Comprehensive analysis in < 15 minutes
- All metrics computed
- Report written to disk

---

## Troubleshooting

### Job Not Running
```bash
# Check logs
docker logs nzt48 | grep "adaptive_learning_scheduler"

# Verify APScheduler
python3 -c "from apscheduler.schedulers.background import BackgroundScheduler; print('OK')"
```

### Database Locked
- Ensure only one scheduler instance running
- Check for zombie Python processes
- Restart container: `docker compose restart nzt48`

### Telegram Not Sending
- Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` environment variables
- Test with curl: `curl -X POST https://api.telegram.org/botXXX/sendMessage -d "chat_id=YYY&text=Test"`

### Missing Trade Data
- Verify trades are being persisted to database
- Check `trades` table has recent entries
- Review database schema creation in `db_writer.py`

---

## Next Steps (Phase 2)

1. **Automated Backtesting**: Integrate production backtest runner
2. **Machine Learning**: Learn optimal parameter ranges from history
3. **Dynamic Tier Allocation**: Auto-adjust position sizing per tier
4. **Predictive Monitoring**: Forecast regime shifts 24h ahead
5. **Multi-Strategy Optimization**: Cross-strategy learning

---

## References

- De Prado, M. L. (2018). *Advances in Financial Machine Learning*. Chapter 6: DSR
- APScheduler Documentation: https://apscheduler.readthedocs.io/
- SQLite WAL Mode: https://sqlite.org/wal.html
