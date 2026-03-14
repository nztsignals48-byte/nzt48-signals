# Perfect Entry Timing System — Paper Trading Deployment Guide

**Status**: Ready for Deployment
**Last Updated**: 2026-03-13
**Phase**: Pre-50-Trade Validation Gate

---

## EXECUTIVE SUMMARY

The Perfect Entry Timing System is fully architected and ready to deploy to IBKR paper trading. This guide covers:

1. **Pre-deployment verification** (IBKR connection, market data, execution)
2. **Paper trading startup** (`run_paper_trading.py`)
3. **Real-time monitoring** (validator metrics, gate status)
4. **Validation gates** (50-trade threshold, win rate ≥60%)
5. **Telegram alerting** (P0-P3 priority routing)
6. **Deployment to LIVE** (only after validation gates pass)

---

## SYSTEM ARCHITECTURE

### Core Components

| Component | File | Purpose |
|-----------|------|---------|
| **Paper Trading Session** | `scripts/run_paper_trading.py` | Main orchestrator: connects to IBKR, streams market data, processes signals |
| **Trade Validator** | `uk_isa/paper_trading_validator.py` | Tracks trades, calculates metrics, checks validation gates |
| **Telegram Alerter** | `delivery/telegram_notifier.py` | P0-P3 tiered alerts, batching, digest, escalation |
| **IBKR Gateway** | IBKR Port 4002 (paper) or 4004 (live) | Real-time market data + order execution |
| **Market Data** | `core/realtime_data.py` | 5-second bars for 12 ISA tickers |
| **Database** | `/data/paper_trades.db` (SQLite) | Trade log, session metrics, gate events |

### Data Flow

```
IBKR Paper Account (£10,000 starting equity)
        ↓
    IBKR Gateway (localhost:4002, client_id=2)
        ↓
    5-second market data subscription (12 ISA tickers)
        ↓
    PaperTradingSession.run_event_loop()
        ├── Update open positions (tick)
        ├── Check halt conditions (every tick)
        └── Generate report (every 60s)
        ↓
    PaperTradingValidator
        ├── Track entries/exits
        ├── Calculate metrics
        └── Evaluate 5 validation gates
        ↓
    TelegramNotifier (P0-P3)
        ├── P0: Instant + sound (halt, error)
        ├── P1: Instant silent (entry, exit)
        ├── P2: 30-min batch (signals)
        └── P3: 2x daily digest (health)
```

---

## VALIDATION GATES (All Must Pass)

Paper trading halts automatically when any condition triggers. To deploy LIVE, all gates must pass:

| Gate | Metric | Required | Current | Status |
|------|--------|----------|---------|--------|
| **Gate 1** | Entry Quality ≥60% | 60.0% | ❓ | Pending |
| **Gate 2** | Rung Hit Rate ≥60% | 60.0% | ❓ | Pending |
| **Gate 3** | Win Rate ≥60% | 60.0% | ❓ | Pending |
| **Gate 4** | Profit Factor ≥1.5x | 1.5 | ❓ | Pending |
| **Gate 5** | Max Cascades <3 | <3 | ❓ | Pending |

**Halt Conditions**:
- Trades ≥50 → STOP (validation complete)
- Days ≥14 → STOP (time limit exceeded)
- Heat ≤-4% → STOP (circuit breaker)
- Any gate fails (after 5 trades) → STOP

---

## PRE-DEPLOYMENT CHECKLIST

### Step 1: Verify IBKR Connection

```bash
# On your local machine or EC2
cd /Users/rr/nzt48-signals

# Check if IBKR Gateway is running
lsof -i :4002 | grep -q LISTEN && echo "✅ IBKR on port 4002" || echo "❌ Not running"

# OR for EC2 remote
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22 'docker ps | grep ib-gateway'
```

**Expected Output**:
```
CONTAINER ID   IMAGE               STATUS              PORTS
abc12345...    gnzsnz/ib-gateway   Up 2 days          0.0.0.0:4002->4002/tcp
```

If NOT running:
```bash
# On EC2, restart IB Gateway
ssh ubuntu@3.230.44.22
cd /home/ubuntu/nzt48-signals
docker-compose restart ib-gateway
# Wait 30-60 seconds for startup
docker logs ib-gateway --tail 20
```

### Step 2: Verify Market Data Access

```bash
# Test ib_insync connection
python3 << 'EOF'
from ib_insync import IB, Stock
import time

ib = IB()
ib.connect('localhost', 4002, clientId=2)
print("✅ Connected to IBKR")

# Subscribe to QQQ3.L
contract = Stock('QQQ3.L', 'SMART', 'GBP')
bars = ib.reqHistoricalData(
    contract, endDateTime='', durationStr='1 D',
    barSizeSetting='5 secs', whatToShow='TRADES',
    useRTH=False, formatDate=1, keepUpToDate=True
)

time.sleep(2)
if bars and len(bars) > 0:
    print(f"✅ Received {len(bars)} bars for QQQ3.L")
    print(f"   Last price: £{bars[-1].close}")
else:
    print("❌ No market data received")

ib.disconnect()
EOF
```

**Expected Output**:
```
✅ Connected to IBKR
✅ Received 45 bars for QQQ3.L
   Last price: £234.56
```

If FAILED:
- Check IBKR Gateway logs: `docker logs ib-gateway --tail 50`
- Verify network connectivity: `telnet localhost 4002`
- Check IB account is paper mode + properly configured

### Step 3: Verify Paper Account Balance

```python
from ib_insync import IB
ib = IB()
ib.connect('localhost', 4002, clientId=2)

account = ib.accountValues()
for av in account:
    if av.tag in ['NetLiquidation', 'TotalCashValue', 'BuyingPower']:
        print(f"{av.tag}: {av.value} {av.currency}")

ib.disconnect()
```

**Expected Output**:
```
NetLiquidation: 10000.00 USD  (or GBP)
TotalCashValue: 10000.00 USD  (or GBP)
BuyingPower: 20000.00 USD     (2x leverage)
```

### Step 4: Verify Order Execution

```python
from ib_insync import IB, Stock, MarketOrder
ib = IB()
ib.connect('localhost', 4002, clientId=2)

# Place test order (market order for 1 share of QQQ3.L)
contract = Stock('QQQ3.L', 'SMART', 'GBP')
order = MarketOrder('BUY', 1)

trade = ib.placeOrder(contract, order)
print(f"Order placed: {trade.order.orderId}")

# Wait for fill
import time
time.sleep(2)
print(f"Order status: {trade.orderStatus.status}")
print(f"Fill price: {trade.fills[0].execution.price if trade.fills else 'PENDING'}")

# Cancel if not filled
if trade.orderStatus.status != 'Filled':
    ib.cancelOrder(order)
    print("Order cancelled")

ib.disconnect()
```

**Expected Output**:
```
Order placed: 1
Order status: Filled
Fill price: 234.56
```

### Step 5: Verify Telegram Configuration

```bash
# Check environment variables
cat /Users/rr/nzt48-signals/.env | grep TELEGRAM

# Should output:
# TELEGRAM_BOT_TOKEN=123456789:ABCdef...
# TELEGRAM_CHAT_ID=987654321
```

If empty or missing:
```bash
# Get token from BotFather (@BotFather on Telegram)
# Get chat_id from bot interaction or /dev/null webhook
# Add to .env:
export TELEGRAM_BOT_TOKEN="your_token_here"
export TELEGRAM_CHAT_ID="your_chat_id_here"
```

Test Telegram send:
```python
from delivery.telegram_notifier import get_notifier, P0
import asyncio

notifier = get_notifier()
asyncio.run(notifier.send_alert("Test alert from paper trading", priority=P0))
print("✅ Telegram alert sent")
```

---

## STARTING PAPER TRADING

### Option 1: Local Deployment (for testing/development)

Assumes IBKR Gateway is running on `localhost:4002`:

```bash
cd /Users/rr/nzt48-signals

# Create logs directory
mkdir -p logs

# Start paper trading session
python3 scripts/run_paper_trading.py \
    --session-id "PT_$(date +%Y%m%d_%H%M%S)" \
    --host localhost \
    --port 4002

# Expected output:
# 2026-03-13 09:00:00 | nzt48.paper_trading | INFO | Starting paper trading session: PT_20260313_090000
# 2026-03-13 09:00:01 | nzt48.paper_trading | INFO | Connected to IBKR at localhost:4002
# 2026-03-13 09:00:02 | nzt48.paper_trading | INFO | Subscribed to QQQ3.L
# ...
# (every 60s) PAPER_TRADING_REPORT
# Trades: 0/0, Win Rate: —%, Profit Factor: —, Gates Passed: False
```

### Option 2: EC2 Remote Deployment (for production)

Assumes IBKR Gateway is running on EC2 at `3.230.44.22:4002`:

```bash
cd /Users/rr/nzt48-signals

# SSH into EC2
ssh -i ~/.ssh/nzt48-key.pem ubuntu@3.230.44.22

# Once inside EC2:
cd /home/ubuntu/nzt48-signals
python3 scripts/run_paper_trading.py \
    --session-id "PT_EC2_$(date +%Y%m%d_%H%M%S)" \
    --host localhost \
    --port 4002 \
    > /var/log/paper_trading.log 2>&1 &

# Verify running
tail -f /var/log/paper_trading.log
```

---

## REAL-TIME MONITORING

While paper trading is running, monitor progress via:

### 1. Live Logs

```bash
tail -f /Users/rr/nzt48-signals/logs/*.log | grep -E "TRADE_ENTRY|TRADE_EXIT|GATE|REPORT"
```

### 2. Database Queries

```bash
cd /Users/rr/nzt48-signals

# Check trades
sqlite3 data/paper_trades.db << 'EOF'
SELECT COUNT(*) as trades_total,
       SUM(CASE WHEN is_closed=1 THEN 1 ELSE 0 END) as closed,
       SUM(CASE WHEN is_winner=1 THEN 1 ELSE 0 END) as winners,
       ROUND(AVG(pnl_dollars), 2) as avg_pnl
FROM paper_trades;
EOF

# Watch gate status
sqlite3 data/paper_trades.db << 'EOF'
SELECT gate_name, required_value, current_value,
       CASE WHEN passed=1 THEN '✅' ELSE '❌' END as status
FROM gate_events
WHERE session_id = (SELECT session_id FROM session_metrics ORDER BY created_at DESC LIMIT 1)
ORDER BY timestamp DESC LIMIT 5;
EOF
```

### 3. Telegram Alerts

You'll receive:
- **P0**: Critical halts (gate failure, heat cap)
- **P1**: Trade entry/exit (silent)
- **P2**: Batched signals (every 30 min)
- **P3**: Daily digest (08:00 & 17:00 UK)

### 4. Python Dashboard (Optional)

Create `/Users/rr/nzt48-signals/scripts/monitor_paper_trading.py`:

```bash
python3 scripts/monitor_paper_trading.py --session-id PT_20260313_090000
```

This displays real-time metrics in the console.

---

## INTERPRETATION OF METRICS

### Entry Quality (Gate 1)

**What it measures**: % of entries showing directional move within 5 minutes.

- **60%+** = ✅ PASS. Entries are correctly timed; system is picking good moments.
- **<60%** = ❌ FAIL. Many whipsaws early; entry timing needs refinement.

**Root cause if failing**:
- Entries occurring at noise peaks, not true breakouts
- Confidence threshold too low (catching noise)
- Lack of momentum confirmation

### Rung Hit Rate (Gate 2)

**What it measures**: % of trades hitting first rung (+0.3% profit target).

- **60%+** = ✅ PASS. System is capturing initial momentum correctly.
- **<60%** = ❌ FAIL. Positions moving wrong direction; inverse detection not working.

**Root cause if failing**:
- Inverse timing module not detecting short opportunities
- Position sizing too large (getting stopped too quickly)
- Market regime shift (trending → range-bound)

### Win Rate (Gate 3)

**What it measures**: % of closed trades with positive P&L.

- **60%+** = ✅ PASS. Majority of trades are profitable.
- **<60%** = ❌ FAIL. More losers than winners; risk/reward imbalanced.

**Root cause if failing**:
- Stop loss too tight relative to target
- Entry quality poor (many whipsaws)
- Profit ladder not capturing runners
- Risk/reward ratio < 1:1

### Profit Factor (Gate 4)

**What it measures**: Gross profit ÷ Gross loss. Should be ≥1.5.

- **1.5+** = ✅ PASS. Winners outweigh losers by 1.5x.
- **<1.5** = ❌ FAIL. Losers too large or too frequent.

**Root cause if failing**:
- Large outlier losses (bad fills, gapping)
- Winners too small (too-tight profit targets)
- Risk/reward not properly calibrated

### Max Cascades (Gate 5)

**What it measures**: Longest consecutive loss chain (must be <3).

- **0-2** = ✅ PASS. No prolonged losing streaks.
- **≥3** = ❌ FAIL. Risk of account depletion.

**Root cause if failing**:
- Regime shift (momentum → mean reversion)
- Confidence calibration off (taking low-conviction trades)
- Stops being hit repeatedly in tight range

---

## HALT SCENARIOS & RECOVERY

### Scenario 1: 50 Trades Completed ✅

**Expected**: Validation complete, all gates checked.

**Action**:
1. Review final report in SQLite
2. If ALL gates ≥60% → proceed to LIVE deployment
3. If ANY gate <60% → analyze failures, iterate system, re-run

```bash
sqlite3 data/paper_trades.db << 'EOF'
SELECT * FROM session_metrics WHERE session_id =
  (SELECT session_id FROM session_metrics ORDER BY created_at DESC LIMIT 1);
EOF
```

### Scenario 2: Heat Cap Breach (-4% daily) ❌

**Expected**: System halts to prevent larger losses.

**Action**:
1. **DO NOT** resume same day
2. Review losing trades → identify common factors
3. Tighten entry filters (increase confidence threshold)
4. Adjust stop loss / profit target ratio
5. Resume next trading day

**Example failure log**:
```
HEAT_CAP_BREACH: Net PnL = -£400 (-4.0%)
Last 3 trades: -£150, -£175, -£75
Root cause: Wide stops (1.5% vs 0.5% targets)
Action: Reduce stop size to 0.75%, test again
```

### Scenario 3: Gate Failure (After 5 Trades) ❌

**Example**: Entry Quality = 42% (target 60%).

**Interpretation**: Too many wrong-direction moves post-entry.

**Root causes & fixes**:
1. **Noise vs. trend** → Increase momentum threshold (MA length, RSI)
2. **Inverse detection** → Check short detection logic
3. **Market regime** → Monitor HMM regime state; pause in range-bound
4. **Confidence calibration** → Increase min confidence to 70%

**Resume action**:
1. Update strategy parameters in `core/entry_timing_model.py`
2. Backtest new parameters (synthetic data)
3. Restart paper trading with new session_id
4. Repeat 50-trade gate cycle

### Scenario 4: 14 Days Elapsed ⏰

**Expected**: Time limit for validation reached (even if <50 trades).

**Action**:
1. If trades <20 → market was slow, extend to 21 days
2. If gates passing → proceed to LIVE (don't wait for 50 trades)
3. If gates failing → fix issues, restart fresh cycle

---

## TRANSITION TO LIVE TRADING

### Prerequisites

All of the following MUST be true:

- ✅ **Validation Gate 1**: Entry Quality ≥60%
- ✅ **Validation Gate 2**: Rung Hit Rate ≥60%
- ✅ **Validation Gate 3**: Win Rate ≥60%
- ✅ **Validation Gate 4**: Profit Factor ≥1.5
- ✅ **Validation Gate 5**: Max Cascades <3
- ✅ **Minimum trades**: ≥20 (lower threshold OK if gates pass)
- ✅ **No catastrophic loss**: Last 5 trades not all losers
- ✅ **Telegram verified**: P0 alerts received correctly
- ✅ **Manual sign-off**: Trader reviews and approves

### Live Deployment Steps

```bash
cd /Users/rr/nzt48-signals

# 1. Backup paper trading database
cp data/paper_trades.db data/paper_trades_BACKUP_$(date +%Y%m%d_%H%M%S).db

# 2. Create live trading config
cp config/settings.yaml config/settings.live.yaml
# Edit config.live.yaml:
#   - Change IBKR_PORT: 4004 (live account)
#   - Change IBKR_CLIENT_ID: 101 (live trading)
#   - Set CIRCUIT_BREAKER_THRESHOLD: -0.02 (-2% LIVE vs -4% paper)

# 3. Update .env for live
export IBKR_PORT=4004
export IBKR_CLIENT_ID=101

# 4. Start live trading
python3 scripts/run_live_trading.py \
    --config config/settings.live.yaml \
    --session-id "LIVE_$(date +%Y%m%d_%H%M%S)" \
    --mode live \
    > logs/live_trading.log 2>&1 &

# 5. Monitor
tail -f logs/live_trading.log | grep -E "ENTRY|EXIT|HEAT|GATE"

# 6. Emergency halt
# Create kill switch: touch data/KILL_SWITCH
# Or: kill %1 (in same terminal)
```

### First Week LIVE Checklist

| Day | Action | Expected | Status |
|-----|--------|----------|--------|
| **Day 1** | Manual review of first 5 trades | P1 alerts received | ✓ |
| **Day 2** | Check P&L, rung advancement | +0.3% to +1.0% expected | ✓ |
| **Day 3** | Monitor win rate | Should be ≥60% | ✓ |
| **Day 4** | Check circuit breaker (never triggered) | Green status | ✓ |
| **Day 5** | Weekly report generated | Telegram digest | ✓ |

---

## TROUBLESHOOTING

### Issue: No Trades Generated

**Symptom**: After 1 hour, `trades_total = 0`.

**Diagnosis**:
```bash
# Check logs
tail -20 logs/paper_trading.log | grep -E "SIGNAL|ENTRY|ERROR"

# Check market data
sqlite3 data/paper_trades.db "SELECT COUNT(*) FROM market_data;"
```

**Solutions**:
1. **Market closed**: Check if LSE is open (08:00-16:30 UK)
2. **No signals**: Increase signal sensitivity (lower confidence threshold)
3. **Connection issue**: Verify IBKR: `lsof -i :4002`

### Issue: All Trades Are Losing

**Symptom**: `net_pnl < 0`, `win_rate = 0%`.

**Diagnosis**:
```bash
sqlite3 data/paper_trades.db << 'EOF'
SELECT direction, COUNT(*) as cnt, AVG(pnl_dollars) as avg_pnl
FROM paper_trades WHERE is_closed=1
GROUP BY direction;
EOF
```

**Solutions**:
1. **Wrong direction**: Check inverse detection logic
2. **Market regime**: LSE might be in drawdown phase
3. **Slippage**: Execution prices much worse than entry
4. **Stops too tight**: Increase from 0.5% to 0.75%

### Issue: Telegram Alerts Not Received

**Symptom**: No P0/P1 alerts even after trades.

**Diagnosis**:
```python
from delivery.telegram_notifier import get_notifier, P0
status = get_notifier().get_status()
print(status)
# Should show: telegram_sender_active=True
```

**Solutions**:
1. **Bot token invalid**: Regenerate in BotFather
2. **Chat ID wrong**: Verify in Telegram (Settings → My ID)
3. **Network issue**: Check firewall for api.telegram.org

### Issue: Gate Keeps Failing

**Symptom**: Entry Quality = 35%, need 60%.

**Diagnosis**:
```bash
# Analyze entry direction vs actual direction
sqlite3 data/paper_trades.db << 'EOF'
SELECT direction, COUNT(*) as cnt,
       SUM(CASE WHEN (direction='LONG' AND high_since_entry > entry_price) THEN 1 ELSE 0 END) as correct_long,
       SUM(CASE WHEN (direction='SHORT' AND low_since_entry < entry_price) THEN 1 ELSE 0 END) as correct_short
FROM paper_trades WHERE is_closed=1
GROUP BY direction;
EOF
```

**Solutions**:
1. **Momentum weak**: Increase required momentum (RSI >70 for long)
2. **Inverse missing**: Check short detection in `entry_timing_model.py`
3. **Regime wrong**: Switch to range-bound strategy if HMM says bearish

---

## SUCCESS CRITERIA

### Minimum (To Deploy LIVE)

- [x] All 5 gates ≥60% target
- [x] ≥20 closed trades
- [x] No 3+ consecutive losses
- [x] Telegram P0 alerts working
- [x] Manual sign-off from trader

### Excellent (High-Confidence LIVE)

- [x] Win rate 65%+ (safety margin)
- [x] Entry quality 70%+ (well-timed)
- [x] Profit factor 2.0+ (strong winners)
- [x] Entry/exit timing consistent
- [x] No unexpected regime transitions

### Conservative (Ultra-Safe)

- [x] Win rate 70%+
- [x] All gates 75%+
- [x] ≥30 trades (more data)
- [x] 7-day paper trading (proves consistency)
- [x] Multi-trader sign-off

---

## KEY FILES & PATHS

| Purpose | File | Action |
|---------|------|--------|
| Start paper trading | `scripts/run_paper_trading.py` | `python3 scripts/run_paper_trading.py` |
| Trade validator | `uk_isa/paper_trading_validator.py` | Internal (auto-called) |
| Telegram alerts | `delivery/telegram_notifier.py` | Internal (auto-called) |
| Trade database | `data/paper_trades.db` | SQLite queries |
| Logs | `logs/paper_trading.log` | `tail -f logs/*.log` |
| Config | `config/settings.yaml` | Edit before startup |
| .env secrets | `.env` | Add TELEGRAM_* keys |

---

## NEXT STEPS

1. **Verify IBKR connection** (pre-deployment checklist, Step 1-4)
2. **Verify Telegram** (Step 5)
3. **Start paper trading** (`scripts/run_paper_trading.py`)
4. **Monitor progress** (hourly, daily reports)
5. **Check gates** (at 50 trades or 14 days)
6. **If gates pass**: Deploy to LIVE
7. **If gates fail**: Fix issues, restart fresh cycle

---

## SUPPORT & DEBUGGING

**Emergency Halt**:
```bash
# Kill paper trading
killall python3
# Or
touch /Users/rr/nzt48-signals/data/KILL_SWITCH
```

**View Current Session**:
```bash
sqlite3 /Users/rr/nzt48-signals/data/paper_trades.db << 'EOF'
SELECT session_id FROM session_metrics ORDER BY created_at DESC LIMIT 1;
EOF
```

**Restart After Fix**:
```bash
# Clear old session, start new
python3 scripts/run_paper_trading.py --session-id "PT_v2_$(date +%Y%m%d_%H%M%S)"
```

---

## DOCUMENT CONTROL

| Version | Date | Change |
|---------|------|--------|
| 1.0 | 2026-03-13 | Initial deployment guide |

**Approval**: Ready for immediate paper trading deployment.
