# Integration Guide: Adding Adaptive Learning to main.py

## Quick Start (5 minutes)

### 1. Add Import
In `main.py`, add:
```python
from scripts.adaptive_learning_scheduler import get_scheduler
```

### 2. Initialize Scheduler at Startup
In your async `main()` function:
```python
async def main():
    # ... existing code ...

    # Initialize adaptive learning scheduler
    scheduler = get_scheduler()
    scheduler.start()
    logger.info("✓ Adaptive Learning Scheduler started")

    # ... trading loop ...

    # At shutdown
    scheduler.shutdown()
```

### 3. Test the Integration
```bash
python3 -c "
from scripts.adaptive_learning_scheduler import get_scheduler
scheduler = get_scheduler()
print('Jobs registered:')
for job in scheduler.get_jobs():
    print(f'  {job.id}: {job.name} → {job.next_run_time}')
"
```

---

## Detailed Integration Steps

### Step 1: Ensure APScheduler is Installed
```bash
cd /Users/rr/nzt48-signals
source venv/bin/activate
pip install apscheduler>=3.10.0
```

### Step 2: Verify Database Tables
The scheduler will auto-create all required tables on first run. To pre-create:
```python
from learning.daily_optimization import DailyOptimizer
from learning.signal_decay_detector import SignalDecayDetector
from learning.weekly_backtest import WeeklyBacktester
from monitoring.performance_report import GeneratePerformanceReport

# Force table creation
DailyOptimizer().db_path
SignalDecayDetector().db_path
WeeklyBacktester().db_path
GeneratePerformanceReport().db_path
```

### Step 3: Configure Environment Variables
Add to `.env`:
```bash
# Telegram alerts for adaptive learning
TELEGRAM_BOT_TOKEN="your_bot_token"
TELEGRAM_CHAT_ID="your_chat_id"
```

### Step 4: Add to docker-compose.yml (if running in Docker)
The scheduler runs in the main `nzt48` container, no changes needed.

---

## Monitoring & Management

### Check Jobs in Real-Time
```python
from scripts.adaptive_learning_scheduler import get_scheduler

scheduler = get_scheduler()

# List all jobs
for job in scheduler.get_jobs():
    print(f"""
    Job: {job.id}
    Name: {job.name}
    Next Run: {job.next_run_time}
    Trigger: {job.trigger}
    """)

# Output example:
# Job: daily_optimization
# Name: Daily Optimization Analysis (17:00 UTC)
# Next Run: 2025-03-13 17:00:00+00:00
# Trigger: cron[hour='17', minute='0', timezone='UTC']
```

### Pause/Resume Jobs
```python
scheduler = get_scheduler()

# Pause daily optimization (useful for testing)
scheduler.pause_job("daily_optimization")

# Resume
scheduler.resume_job("daily_optimization")
```

### Manually Trigger a Job
```python
from learning.daily_optimization import DailyOptimizer
from monitoring.performance_report import GeneratePerformanceReport

# Run daily optimization now
optimizer = DailyOptimizer()
result = optimizer.run_nightly_analysis()
print(f"Analysis complete: {result['trades_analyzed']} trades")

# Run daily report now
reporter = GeneratePerformanceReport()
summary = reporter.daily_summary()
print(f"Daily report: {summary.trades} trades, WR={summary.win_rate:.1%}")
```

---

## Handling Recommendations

### 1. Check Pending Recommendations Daily
```python
from learning.daily_optimization import DailyOptimizer

optimizer = DailyOptimizer()
pending = optimizer.get_pending_recommendations()

if pending:
    print(f"\n⚠️ {len(pending)} pending recommendations:\n")
    for rec in pending:
        print(f"""
    ID: {rec['recommendation_id']}
    Category: {rec['category']}
    Current: {rec['current_value']}
    Suggested: {rec['suggested_value']}
    Reason: {rec['reason']}
    Confidence: {rec['confidence_score']:.1%}
        """)
else:
    print("✓ No pending recommendations")
```

### 2. Approve a Recommendation
```python
optimizer.approve_recommendation("rec-abc123", approve=True)
# Logs to learning_audit_log with status = APPROVED
```

### 3. Deploy Approved Changes
Once approved, manually update configuration:
```bash
# Edit config/settings.yaml
nano config/settings.yaml

# Update parameters, e.g.:
# confidence_threshold: 70 → 75

# Restart strategy
docker compose restart nzt48

# Monitor for 5+ trades to verify improvement
```

### 4. Rollback if Degradation Detected
```python
optimizer.rollback_change(
    "rec-abc123",
    reason="Win rate dropped from 45% to 38% after deployment"
)
# Logs to learning_audit_log with status = REVERTED
```

---

## Viewing Reports

### Daily Reports
```bash
ls /Users/rr/nzt48-signals/reports/2025-03-13/
# Output:
# daily_summary.txt
```

### Weekly Reports
```bash
ls /Users/rr/nzt48-signals/reports/week_2025-03-10/
# Output:
# weekly_summary.txt
```

### Monthly Reports
```bash
ls /Users/rr/nzt48-signals/reports/month_2025-03/
# Output:
# monthly_summary.txt
```

---

## Telegram Setup

### 1. Create Telegram Bot
```bash
# Message @BotFather on Telegram
/newbot
# Follow prompts, get token: 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
```

### 2. Get Chat ID
```bash
# Message your bot with: /start
# In Python:
import requests
token = "YOUR_TOKEN"
url = f"https://api.telegram.org/bot{token}/getUpdates"
r = requests.get(url).json()
chat_id = r['result'][0]['message']['chat']['id']
print(chat_id)  # Your chat ID
```

### 3. Set Environment Variables
```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
export TELEGRAM_CHAT_ID="987654321"
```

### 4. Test
```python
from scripts.adaptive_learning_scheduler import AdaptiveLearningScheduler
scheduler = AdaptiveLearningScheduler()
scheduler._post_telegram_message(
    token=os.environ.get("TELEGRAM_BOT_TOKEN"),
    chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
    text="✅ Adaptive Learning System Online"
)
```

---

## Performance: Expected Runtime

| Job | Typical Duration | Frequency |
|-----|------------------|-----------|
| Daily Optimization | 15-30s | 1x daily |
| Daily Summary | 5-10s | 1x daily |
| Signal Decay Detection | 2-5s | Every 60 min |
| Weekly Backtest | 3-5 min | 1x weekly |
| Weekly Summary | 10-15s | 1x weekly |
| Monthly Summary | 20-30s | 1x monthly |

**Total system overhead**: < 1% CPU, < 50MB RAM

---

## Logging

### Enable Debug Logging
```python
import logging
logging.getLogger("nzt48.adaptive_learning_scheduler").setLevel(logging.DEBUG)
logging.getLogger("nzt48.learning").setLevel(logging.DEBUG)
```

### View Logs in Docker
```bash
docker logs nzt48 | grep "adaptive_learning"
docker logs nzt48 | grep "daily_optimization"
docker logs nzt48 | grep "signal_decay"
```

### View Database Audit Trail
```bash
sqlite3 /Users/rr/nzt48-signals/data/nzt48.db

SELECT * FROM learning_audit_log ORDER BY timestamp DESC LIMIT 10;
SELECT * FROM optimization_recommendations WHERE status = 'PENDING';
SELECT * FROM signal_disabled_log WHERE re_enabled_at IS NULL;
```

---

## Troubleshooting

### Q: Jobs not running at scheduled time
**A**: Check timezone. All times are in UTC. Convert to your local:
- 17:00 UTC = 12:00 PM ET / 09:00 AM PT
- Sunday 23:00 UTC = Sunday 6:00 PM ET

### Q: Telegram alerts not sending
**A**: Verify credentials:
```python
import os
print(os.environ.get("TELEGRAM_BOT_TOKEN"))
print(os.environ.get("TELEGRAM_CHAT_ID"))
```

### Q: Database locked errors
**A**: Only one scheduler instance should run. Check:
```bash
ps aux | grep adaptive_learning_scheduler
# Kill any duplicates
```

### Q: Missing recommendations
**A**: Ensure trades are being logged:
```bash
sqlite3 /Users/rr/nzt48-signals/data/nzt48.db
SELECT COUNT(*) FROM trades;
SELECT DATE(time_entered), COUNT(*) FROM trades GROUP BY DATE(time_entered);
```

---

## Next: Manual Operations Commands

### Daily Checklist
```python
from learning.daily_optimization import DailyOptimizer
from learning.signal_decay_detector import SignalDecayDetector
from monitoring.performance_report import GeneratePerformanceReport

# 1. Review pending recommendations
optimizer = DailyOptimizer()
pending = optimizer.get_pending_recommendations()
print(f"Pending: {len(pending)}")

# 2. Check signal health
detector = SignalDecayDetector()
reports = detector.detect_decay(lookback_days=30)
decayed = [r for r in reports if r.decay_detected]
print(f"Decayed signals: {len(decayed)}")

# 3. View daily performance
reporter = GeneratePerformanceReport()
summary = reporter._compute_daily_summary("2025-03-13")
print(f"Today: {summary.trades} trades, WR={summary.win_rate:.1%}")
```

---

## Reference

- Main scheduler file: `/Users/rr/nzt48-signals/scripts/adaptive_learning_scheduler.py`
- Daily optimizer: `/Users/rr/nzt48-signals/learning/daily_optimization.py`
- Signal decay: `/Users/rr/nzt48-signals/learning/signal_decay_detector.py`
- Weekly backtest: `/Users/rr/nzt48-signals/learning/weekly_backtest.py`
- Performance reports: `/Users/rr/nzt48-signals/monitoring/performance_report.py`
- Full docs: `/Users/rr/nzt48-signals/BUILD_WEEK_9_10_ADAPTIVE_LEARNING_README.md`
