# SOLUTIONS: 24-Hour Global Multi-Market Trading
**Date**: March 13, 2026 | **Status**: Implementation roadmap | **Timeline**: 15 weeks (March 14 → Late June 2026)

---

## EXECUTIVE SUMMARY

You want to scale from LSE-only (8 hours/day, £30-50/day target) to 24-hour global (Asia + Europe + US, £50-100/day target).

**The problems are real but solvable. Here are concrete solutions.**

**Top-level recommendations**:
1. ✅ **Week 1-2**: Stick with LSE-only (08:00-16:30 UTC). Prove 0.3-0.5% daily.
2. ✅ **Week 3-4**: Add US (14:30-21:00 UTC) via IBKR expansion.
3. ✅ **Week 5-6**: Add Europe Euronext (parallel LSE trading).
4. ⚠️ **Week 7-10**: Asia (Japan/HK) only if US/Europe validated (high complexity, lower returns).
5. 🎯 **Week 11-15**: Full 24-hour only if each phase hits targets.

---

## PROBLEM 1: BROKER INFRASTRUCTURE

### The Challenge
IBKR can trade all 4 markets, but:
- Japan (JPX) + HK (HKEX) have lower liquidity
- Multiple time zones = need 4 different client IDs simultaneously
- US pattern day trading rule (need £25k per account)
- UK ISA restrictions (only LSE/Euronext, no Asia, no US)

### SOLUTION 1A: ACCOUNT STRUCTURE

**Recommended Setup** (Lowest complexity, highest compliance):

```
Account 1: IBKR Main (US-based, primary)
├─ Client ID: 101
├─ Base currency: USD
├─ Account type: Margin (NOT ISA)
├─ Purpose: US + Europe (Euronext/Deutsche) + some HK
├─ Capital: £6,000 (60% of total)
├─ Minimum balance: £1,500 (PDT rule needs £25k equiv, but we stay under 4 trades/week)
└─ Supported exchanges: NYSE, NASDAQ, Euronext, HKEX, JPX

Account 2: IBKR ISA (UK-based, ISA-compliant)
├─ Client ID: 102
├─ Base currency: GBP
├─ Account type: ISA (tax-free)
├─ Purpose: LSE only (MPL-eligible only)
├─ Capital: £4,000 (40% of total)
├─ Annual limit: £20,000 (you're at £10k, so £10k room left)
└─ Supported: GPT3.L, 3LUS.L, 3SEM.L, TSL3.L, MU2.L, NVD3.L, QQQS.L, 3USS.L, QQQ5.L, SP5L.L, GLD.L, QQQ3.L

WHY THIS STRUCTURE:
✓ ISA account for LSE (tax-efficient, compliant)
✓ Main account for US (avoid ISA restriction on US equities)
✓ Single IBKR parent account managing 2 sub-accounts
✓ Capital can flow between accounts daily (no lock-up)
✓ Avoids creating 4 separate accounts (regulatory nightmare)
✓ Stays under £25k PDT threshold (only 2-4 day trades/week per account)
```

### SOLUTION 1B: CAPITAL DISTRIBUTION

```
Starting Capital: £10,000

Week 1-2 (LSE only):
└─ £10,000 in ISA account (Account 2)

Week 3-4 (Add US):
├─ ISA account: £4,000 (LSE only)
└─ Main account: £6,000 (US + Euronext start)

Week 5-6 (Add Euronext):
├─ ISA: £4,000 (LSE)
├─ Main: £6,000 (US + Euronext)
└─ Rebalance daily based on daily P&L

Week 7+ (If adding Asia):
├─ ISA: £4,000 (LSE)
├─ Main: £6,000 (US + Euronext + maybe HK/Japan)
└─ Japan/HK only if profitability proven

REBALANCING RULE:
├─ After each session: If one account has >70% equity, transfer 10% to other
├─ Keeps both accounts above £1,500 minimum
├─ Ensures no account hits PDT rule (< 4 day trades/week)
└─ Maximizes capital utilization globally
```

### SOLUTION 1C: PDT RULE WORKAROUND

**Problem**: US has Pattern Day Trading rule (need £25k if > 3 day trades/5 days)

**Solution**:
```
Strategy 1: Stay UNDER 4 trades/5 days per account
├─ Account 1 (Main, USD): Max 2 day trades/5 days
├─ Account 2 (ISA, GBP): Max 1 day trade/5 days (not same stocks anyway)
└─ Hold positions > 1 day = not a "day trade" per SEC

Strategy 2: Hold positions overnight
├─ Enter at US afternoon (16:00 ET)
├─ Exit at US morning next day (09:30 ET + 30 min)
├─ NOT a day trade (held > 24 hours)
├─ But increases overnight risk (carry)

Strategy 3: Use swing trading (hold 2-5 days)
├─ Reduce frequency of day trades
├─ Accept longer holding periods
├─ Lower daily P&L but still positive compounding

RECOMMENDED: Strategy 1 + Strategy 2 HYBRID
├─ Day trades: Max 2/week (stay under PDT limit)
├─ Swing trades: 3-5 day holds (no PDT restriction)
├─ Carry: Use cross_timezone.rs to manage overnight risk
└─ Result: Can operate without £25k minimum
```

### SOLUTION 1D: BROKER ROUTING LOGIC

**Modified `ibkr_broker.rs`** to support 4 client IDs:

```rust
// rust_core/src/ibkr_broker.rs (modify existing)

pub struct IBKRBrokerMultiClient {
    isa_client: IBKRClient,        // Client 102: LSE only
    main_client: IBKRClient,       // Client 101: US + Europe + HK/JP
    current_session: TradingSession, // Which market is active?
}

impl IBKRBrokerMultiClient {
    pub fn route_order(&mut self, order: &Order) -> Result<OrderID> {
        match order.exchange {
            // LSE = ISA account only
            Exchange::LSE => {
                self.isa_client.place_order(order)?
            }

            // US/Europe/Asia = Main account
            Exchange::NASDAQ | Exchange::NYSE | Exchange::EURONEXT
            | Exchange::HKEX | Exchange::JPX => {
                self.main_client.place_order(order)?
            }

            // Unknown = Error
            _ => Err("Exchange not supported")
        }
    }

    pub fn set_session(&mut self, session: TradingSession) {
        self.current_session = session;
        // Adjust max position sizes based on session liquidity
        match session {
            TradingSession::LSE => self.max_position_pct = 0.25, // 25% of equity
            TradingSession::US => self.max_position_pct = 0.20,  // 20% (larger market)
            TradingSession::Europe => self.max_position_pct = 0.15, // 15% (lower liquidity)
            TradingSession::Asia => self.max_position_pct = 0.10, // 10% (lowest liquidity)
        }
    }
}
```

### SOLUTION 1E: IBKR CONNECTION CHECKLIST

```
Week 1 Setup Tasks:
- [ ] Create IBKR ISA account (Account 2, Client ID 102)
- [ ] Verify LSE trading enabled (MPL leverage products only)
- [ ] Verify US trading enabled (Client ID 101)
- [ ] Verify Europe trading enabled (Euronext access)
- [ ] Verify HK/Japan enabled (might need to request)
- [ ] Test TWO simultaneous connections (Client 101 + 102)
- [ ] Load account 1 with £6,000
- [ ] Load account 2 with £4,000
- [ ] Run live connection test: Place small test order on each
- [ ] Verify fills + commissions match expectations
- [ ] Set up withdrawal limits (prevent accidental over-trading)

Documentation needed:
- ISA rules doc (what can/can't trade in ISA)
- IBKR fee schedule (commissions per market)
- PDT rule summary (max 3 trades/5 days under £25k)
- Account limits (max positions, max order size per fund)
```

---

## PROBLEM 2: DATA INFRASTRUCTURE

### The Challenge
Need real-time bars for 150+ stocks across 4 markets simultaneously:
- IBKR 5-second bars: Limited to ~100 concurrent subscriptions
- Polygon API: 4 calls/min = 240/hour = can't refresh 150 stocks frequently
- yfinance: Rate-limited, no real-time (2-5s latency)
- Cost: Multiple data vendors = £500-1,000/month

### SOLUTION 2A: TIERED DATA ARCHITECTURE

**MVP (Weeks 1-6): LSE + US Only**

```
Tier 1: IBKR Real-Time (Primary, highest quality)
├─ 12 LSE funds → Subscribe via IBKR API (5-sec bars, free)
├─ 50 US mega-caps → Subscribe via IBKR API (5-sec bars, free)
├─ Total: 62 concurrent subscriptions (within IBKR limit of ~100)
├─ Cost: £0 (free with trading account)
└─ Latency: <100ms (real-time)

Tier 2: yfinance (Fallback + historical)
├─ All 150 stocks → 1 daily pull (end of day)
├─ Purpose: Fill in data gaps, historical depth
├─ Throttle: 0.5-1.5s between calls (avoid IP ban)
├─ Cost: £0 (free)
├─ Latency: 2-5 seconds (not real-time, OK for historical)
└─ Usage: Signal modules that don't need intraday precision

Tier 3: Polygon API (Bootstrap only, not continuous)
├─ Dividends: 150 calls per night (37.5 min, rate-limited)
├─ Splits: 150 calls per night (37.5 min, rate-limited)
├─ Purpose: Historical data adjustment, not real-time
├─ Cost: £50-300/month (depends on tier)
│  └─ Free tier: 5 calls/min (we use 4 = OK)
│  └─ Basic: £50/mo = 20 calls/min
│  └─ Pro: £300/mo = unlimited
├─ Latency: Not applicable (batch processing)
└─ Usage: Only at 23:00 UTC nightly Ouroboros
```

**Full 24-Hour (Weeks 7+): Add Asia**

```
If adding Japan + Hong Kong, upgrade:

Tier 1 Extended:
├─ 12 LSE funds → IBKR (5-sec)
├─ 50 US stocks → IBKR (5-sec)
├─ 10 Japan mega-caps → IBKR (5-sec) — uses different client ID
├─ 10 HK mega-caps → IBKR (5-sec) — uses different client ID
├─ Total: 82 subscriptions (still under 100-limit)
└─ Cost: £0

BUT: If exceeding IBKR limit, need external data:
├─ Option A: Upgrade Polygon to Basic (£50/mo) → 20 calls/min
├─ Option B: Use market data vendor (e.g., Refinitiv, Bloomberg)
└─ Cost: +£50-300/month if needed

Tier 2-3: Same as MVP
```

### SOLUTION 2B: FALLBACK STRATEGY

```
Real-time data chain (for critical signals):

┌─ IBKR (Primary, <100ms latency)
│  └─ If disconnected or no subscription:
│
├─ yfinance polling (10-second loop)
│  └─ If yfinance fails or slow:
│
└─ Cached prices from Redis
   └─ If all sources fail: STOP TRADING until restored
```

**Code implementation** (`python_brain/data_loaders.py`):

```python
class MultiTierDataLoader:
    async def get_bar(self, ticker: str, interval: int = 5) -> Bar:
        # Tier 1: IBKR real-time
        try:
            bar = await self.ibkr.get_latest_bar(ticker)
            return bar  # <100ms
        except IBKRConnectionError:
            pass

        # Tier 2: yfinance with cache
        try:
            bar = await self.yfinance.get_bar(ticker)
            self.redis.set(f"cache:{ticker}", bar, ttl=10)  # Cache 10 sec
            return bar  # 2-5 sec latency
        except yfinanceError:
            pass

        # Tier 3: Cached price (stale, but better than nothing)
        try:
            cached = self.redis.get(f"cache:{ticker}")
            if cached and cached.age_sec < 30:  # Max 30 sec old
                log.warning(f"{ticker}: Using 30-sec cached price")
                return cached
        except:
            pass

        # Tier 4: All failed
        log.error(f"{ticker}: All data sources failed, STOP TRADING")
        self.risk_manager.trigger_circuit_breaker()
        raise DataSourceFailure(ticker)
```

### SOLUTION 2C: COST BREAKDOWN

**MVP (LSE + US, Weeks 1-6)**:
```
Data costs:
├─ IBKR: £0 (free with trading)
├─ yfinance: £0 (free)
├─ Polygon free tier: £0
└─ Total: £0/month

Total system cost (first 6 weeks):
├─ IBKR commissions: ~£50-100/month (200 trades × £0.50 avg)
├─ AWS EC2: £30/month (t3.medium, 24/7)
├─ VPS/backup: £10/month
└─ Total: £90-140/month (BREAK-EVEN if earn £100/day profit)
```

**Full 24-Hour (Weeks 7+)**:
```
If adding Asia:
├─ Polygon upgrade: +£50-300/month
├─ Market data vendor (if needed): +£100-500/month
├─ Additional IBKR commissions: +£50/month
└─ Total: +£200-850/month extra cost

Must earn +£200/day to justify Asia expansion (vs +£100/day MVP)
```

---

## PROBLEM 3: FX & CURRENCY RISK

### The Challenge
Trading in 4 currencies (JPY, HKD, EUR, GBP, USD) without proper hedging = massive drawdowns from FX moves alone (±3% daily swings common).

### SOLUTION 3A: CURRENCY EXPOSURE ANALYSIS

```
For every £10,000 capital:

LSE (GBP):
├─ Currency: GBP (home currency, no FX risk)
├─ % of capital: 40% = £4,000
└─ FX impact: 0% (already GBP)

US (USD):
├─ Currency: USD
├─ % of capital: 30% = £3,000
├─ GBP/USD rate: ~1.27 (1 GBP = $1.27)
├─ 1% USD weakness = -1.27% loss in GBP terms
└─ FX impact: HIGH if unhedged

Euronext (EUR):
├─ Currency: EUR
├─ % of capital: 15% = £1,500
├─ EUR/GBP rate: ~0.86 (1 EUR = £0.86)
├─ 1% EUR weakness = -1% loss in GBP terms
└─ FX impact: MEDIUM

Japan (JPY):
├─ Currency: JPY
├─ % of capital: 10% = £1,000 (if trading)
├─ GBP/JPY rate: ~180 (1 GBP = ¥180)
├─ 1% JPY weakness = -1% loss in GBP terms
└─ FX impact: VERY HIGH (JPY volatile)

HK (HKD):
├─ Currency: HKD
├─ % of capital: 5% = £500 (if trading)
├─ HKD pegged to USD (fixed rate)
├─ Risk: USD weakness translates to HKD weakness
└─ FX impact: SAME AS USD
```

### SOLUTION 3B: FX HEDGING STRATEGY (RECOMMENDED)

**Option A: Static 50% Hedge** (Simplest, lowest cost)

```
For every £3,000 in US stocks:
├─ £1,500 in US stocks (exposed)
├─ Buy £1,500 of GBP/USD forward contracts (hedge)
│  └─ Lock in USD/GBP rate for 30 days
│  └─ Cost: ~0.1-0.2% per month
└─ Net: 50% FX risk eliminated

For every £1,500 in Euronext:
├─ £750 in EUR stocks (exposed)
├─ Buy £750 of GBP/EUR forward contracts
└─ Net: 50% FX risk eliminated

For JPY/HKD:
├─ Only trade if FX-neutral setup
├─ Or keep tiny position (< 5% of capital)
└─ Not worth the FX risk for low-liquidity markets
```

**Option B: Dynamic Hedging** (More complex, lower cost)

```
Hedge only when FX volatility is high:
├─ Monitor GBP/USD 20-day realized volatility
├─ If vol > 15% annualized: Buy 75% hedge
├─ If vol < 10%: Buy 25% hedge (light)
└─ Adjust weekly based on market conditions

Cost:
├─ When hedged 75%: 0.15% per month
├─ When hedged 25%: 0.05% per month
├─ Average: 0.08% per month (vs 0.15% static)
```

**Option C: No Hedge** (Simplest, highest variance)

```
Accept FX risk, don't hedge:
├─ GBP strengthens (Good): US/EUR positions gain extra return
├─ GBP weakens (Bad): US/EUR positions lose extra from FX
├─ Daily FX swings ±3% can overwhelm trading P&L
├─ Year 1: Might get lucky (+50% return), might get unlucky (-10%)
└─ Not recommended for 0.3% daily target
```

### SOLUTION 3C: RECOMMENDED APPROACH

**Weeks 1-4 (MVP: LSE + US only)**:
- ✅ Use **Option A: Static 50% Hedge** on USD exposure
- Cost: 0.15% per month = £15/month on £10k
- Benefit: Sleep at night, no USD surprise wipeout
- Implementation: Contact IBKR about GBP/USD forwards

**Weeks 5-6 (Add Euronext)**:
- ✅ Add 50% hedge on EUR exposure
- Cost: +£8/month (1.5k × 0.15%)
- Total hedging cost: £23/month (0.23% monthly drag)

**Weeks 7+ (If adding Asia)**:
- ❌ DO NOT trade JPY/HKD without full hedges
- Cost of hedging Asia: £50-100/month (too expensive)
- Better: Keep Asia allocation <5% or skip entirely

**Net FX Impact on Returns**:

```
Without hedge:
├─ Best case (GBP weakness): +50% annual return
├─ Worst case (GBP strength): -10% annual return
├─ Variance: Huge, unpredictable
└─ Expected: 0-30% annual (too variable)

With 50% hedge:
├─ Best case: +35% annual (50% + 25% from trading + FX boost)
├─ Worst case: +5% annual (50% - 20% from drawdown - FX drag)
├─ Variance: Much lower
├─ Hedging cost: -2-3% annually
└─ Expected: +15-30% annual (consistent, predictable)
```

---

## PROBLEM 4: OPERATIONAL RISK

### The Challenge
System runs 24 hours, but human monitoring = impossible. Need automated failover, circuit breakers, emergency stops.

### SOLUTION 4A: AUTOMATED FAILOVER

**Connection Monitoring** (modified `main.py`):

```python
import asyncio
from datetime import datetime

class ConnectionMonitor:
    def __init__(self):
        self.ibkr_status = "CONNECTED"
        self.data_status = "CONNECTED"
        self.last_heartbeat = datetime.now()

    async def monitor_ibkr_connection(self):
        """Check IBKR connection every 30 seconds"""
        while True:
            try:
                # Ping IBKR gateway
                status = await self.ibkr.get_account_summary()
                self.ibkr_status = "CONNECTED"
                self.last_heartbeat = datetime.now()
            except IBKRConnectionError:
                self.ibkr_status = "DISCONNECTED"

                # Try to reconnect
                log.warning("IBKR disconnected, attempting reconnect...")
                for attempt in range(5):
                    try:
                        await self.ibkr.reconnect()
                        log.info(f"Reconnected on attempt {attempt+1}")
                        self.ibkr_status = "CONNECTED"
                        break
                    except:
                        await asyncio.sleep(10 * (attempt + 1))  # Exponential backoff

                # If still disconnected after 5 attempts
                if self.ibkr_status == "DISCONNECTED":
                    log.error("IBKR reconnection failed, triggering circuit breaker")
                    self.trigger_circuit_breaker()

            await asyncio.sleep(30)  # Check every 30 seconds

    async def monitor_data_feed(self):
        """Check data feed every 10 seconds"""
        while True:
            try:
                # Get latest bar for anchor stock (GLD.L)
                bar = await self.data_loader.get_bar("GLD.L")
                age_sec = (datetime.now() - bar.timestamp).total_seconds()

                if age_sec > 15:  # Data more than 15 sec old = stale
                    self.data_status = "STALE"
                    log.warning(f"Data stale: GLD.L is {age_sec} seconds old")
                else:
                    self.data_status = "CONNECTED"
            except DataSourceError:
                self.data_status = "DISCONNECTED"
                log.error("All data sources failed!")
                self.trigger_circuit_breaker()

            await asyncio.sleep(10)

    def trigger_circuit_breaker(self):
        """Emergency stop: liquidate all positions"""
        log.critical("CIRCUIT BREAKER TRIGGERED - liquidating all positions")

        # Liquidate all open positions immediately
        for position in self.position_manager.all_open_positions():
            self.ibkr.market_order(
                ticker=position.ticker,
                side="SELL" if position.is_long else "BUY",
                qty=position.qty
            )

        # Stop all trading
        self.trading_engine.pause()

        # Alert human
        self.send_alert(f"CIRCUIT BREAKER: All positions liquidated at {datetime.now()}")
        self.send_sms("AEGIS circuit breaker triggered. Check logs immediately.")
```

### SOLUTION 4B: SESSION MANAGEMENT (Multi-Timezone)

**Modified `session_manager.rs`** to handle 4 sessions:

```rust
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TradingSession {
    Asia,           // Japan 23:50-06:30 UTC, HK 01:30-08:00 UTC
    Europe,         // 08:00-16:30 UTC
    Transatlantic,  // 14:30-16:30 UTC (LSE + US overlap)
    US,             // 14:30-21:00 UTC (pure US)
    Idle,           // 21:00-23:00 UTC (between US close and Asia open)
}

pub struct SessionManager {
    current_session: TradingSession,
    entry_cutoff: HashMap<TradingSession, Duration>,
    exit_cutoff: HashMap<TradingSession, Duration>,
}

impl SessionManager {
    pub fn get_current_session() -> TradingSession {
        let hour_utc = chrono::Utc::now().hour();

        match hour_utc {
            23..=23 | 0..=8  => TradingSession::Asia,        // 23:50-08:00
            8..=14           => TradingSession::Europe,      // 08:00-14:30
            14..=16          => TradingSession::Transatlantic, // 14:30-16:30
            16..=21          => TradingSession::US,          // 16:30-21:00
            _                => TradingSession::Idle,        // 21:00-23:50
        }
    }

    pub fn should_allow_entry(&self) -> bool {
        let now = chrono::Local::now();
        let hour_minute = (now.hour() * 60) + now.minute();

        match self.current_session {
            TradingSession::Asia => hour_minute >= 1430 && hour_minute < 480,  // 23:50-08:00
            TradingSession::Europe => hour_minute >= 800 && hour_minute < 1430,  // 08:00-14:30
            TradingSession::Transatlantic => hour_minute >= 1430 && hour_minute < 1630, // 14:30-16:30
            TradingSession::US => hour_minute >= 1430 && hour_minute < 2100,  // 14:30-21:00
            TradingSession::Idle => false,  // No new entries during idle
        }
    }

    pub fn should_flatten_positions(&self) -> bool {
        let now = chrono::Local::now();
        let hour_minute = (now.hour() * 60) + now.minute();

        // Force flatten at session boundaries to avoid overnight risk
        match self.current_session {
            TradingSession::Asia => hour_minute >= 800 - 15,  // 07:45 (15 min before EU open)
            TradingSession::Europe => hour_minute >= 1630,    // 16:30 (LSE close)
            TradingSession::US => hour_minute >= 2100,        // 21:00 (US close)
            _ => false,
        }
    }
}
```

### SOLUTION 4C: RECONCILIATION ACROSS BROKERS

**Daily 3-way reconciliation** (after all sessions close):

```python
class MultiAccountReconciliation:
    async def reconcile_daily(self):
        """
        Verify all trades match across:
        1. IBKR ISA (Account 2) - LSE only
        2. IBKR Main (Account 1) - US/Europe/Asia
        3. Local WAL (Write-Ahead Log)
        """

        # Get statements from both IBKR accounts
        isa_trades = await self.ibkr_isa.get_trades_today()  # Client 102
        main_trades = await self.ibkr_main.get_trades_today()  # Client 101
        wal_trades = self.wal.get_trades_today()  # Local file

        # Count trades
        total_isa = len(isa_trades)
        total_main = len(main_trades)
        total_wal = len(wal_trades)
        total_expected = total_isa + total_main

        if total_wal != total_expected:
            log.error(f"Trade count mismatch: WAL={total_wal}, Expected={total_expected}")
            # Each trade in WAL must exist in IBKR
            for trade in wal_trades:
                if trade not in isa_trades and trade not in main_trades:
                    log.critical(f"Missing trade: {trade}")
            raise ReconciliationError("Trade count mismatch")

        # Verify P&L matches
        isa_pnl = sum(t.realized_pnl for t in isa_trades)
        main_pnl = sum(t.realized_pnl for t in main_trades)
        total_pnl = isa_pnl + main_pnl

        if abs(total_pnl - self.expected_daily_pnl) > 0.01:  # Tolerance: 1 penny
            log.warning(f"P&L variance: Expected {self.expected_daily_pnl}, Got {total_pnl}")

        # Verify cash balances
        isa_cash = await self.ibkr_isa.get_cash_balance()
        main_cash = await self.ibkr_main.get_cash_balance()
        total_cash = isa_cash + main_cash
        expected_cash = self.starting_capital + total_pnl

        if abs(total_cash - expected_cash) > 0.01:
            log.critical(f"Cash mismatch: Expected {expected_cash}, Got {total_cash}")
            raise ReconciliationError("Cash balance mismatch")

        log.info(f"Reconciliation OK: {total_isa + total_main} trades, P&L={total_pnl}")
```

### SOLUTION 4D: MONITORING & ALERTS

**Real-time monitoring dashboard** (if manual oversight):

```python
class MonitoringDashboard:
    async def update_status(self):
        """Display real-time status every 10 seconds"""
        while True:
            status = {
                "timestamp": datetime.now(),
                "session": self.session_manager.current_session,
                "ibkr_status": self.connection_monitor.ibkr_status,
                "data_status": self.connection_monitor.data_status,
                "positions_open": len(self.position_manager.open_positions),
                "daily_pnl": self.pnl_manager.daily_pnl,
                "daily_loss_count": self.risk_manager.daily_loss_limit_used,
                "trades_today": self.trade_counter.count,
                "avg_win_rate": self.stats.win_rate_percent,
            }

            # Log to screen (if monitoring)
            print(f"""
            ═══════════════════════════════════════
            AEGIS STATUS - {status['timestamp'].strftime('%H:%M:%S %Z')}
            ═══════════════════════════════════════
            Session: {status['session'].name:15} | IBKR: {status['ibkr_status']:12} | Data: {status['data_status']:12}
            Positions: {status['positions_open']:3} | Daily P&L: £{status['daily_pnl']:7.2f} | Trades: {status['trades_today']:3}
            Win Rate: {status['avg_win_rate']:.1f}% | Daily Loss Used: £{status['daily_loss_count']:.2f}/£100
            ═══════════════════════════════════════
            """)

            # Alert if something wrong
            if status['ibkr_status'] == "DISCONNECTED":
                self.send_alert("⚠️ IBKR DISCONNECTED - attempting reconnect")

            if status['data_status'] == "DISCONNECTED":
                self.send_alert("🚨 DATA FEED FAILED - circuit breaker triggered")

            if status['daily_loss_count'] > 80:  # 80% of daily limit used
                self.send_alert("⚠️ Daily loss limit 80% used - reducing position sizes")

            await asyncio.sleep(10)
```

---

## PROBLEM 5: REGULATORY & COMPLIANCE

### The Challenge
Trading in 4 countries = 4 different sets of rules. ISA restrictions are tight.

### SOLUTION 5A: REGULATORY MATRIX

```
┌─────────────────────────────────────────────────────────────────────────┐
│ JURISDICTION COMPARISON                                                 │
├─────────────┬──────────────┬──────────────┬──────────────┬──────────────┤
│ Rule        │ UK/ISA       │ US (IBKR)    │ Europe       │ Asia (JP/HK) │
├─────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ Account Type│ ISA (tax-free)│ Margin       │ Margin       │ Trading      │
│ Min Balance │ £0           │ £25k (PDT)   │ Variable     │ Variable     │
│ Day Trades  │ Unlimited    │ 3/5 days if <£25k │ None    │ None         │
│ Leverage    │ NO (100%)    │ YES (2:1)    │ YES (5:1)    │ YES (3:1)    │
│ Pattern Day │ NO PDT rule  │ YES (sec)    │ NO PDT       │ NO PDT       │
│ Tax         │ 0% (ISA)     │ 20% CG tax   │ Variable     │ Variable     │
│ Record      │ 7 years      │ 7 years (IRS)│ 10 years     │ 10 years     │
│ Reporting   │ ISA only     │ Annual 1099  │ Annual       │ Annual       │
└─────────────┴──────────────┴──────────────┴──────────────┴──────────────┘

RECOMMENDATION FOR WEEK 1-6 (LSE + US ONLY):

✅ Account 1: ISA (UK-domiciled)
   └─ Can trade: LSE only (MPL-eligible products)
   └─ Cannot trade: US, Europe, Asia
   └─ Benefit: 0% capital gains tax
   └─ Limit: £20k/year max contributions

✅ Account 2: IBKR Main (US-domiciled, margin account)
   └─ Can trade: US (NASDAQ, NYSE), Europe (Euronext), Asia (if approved)
   └─ Pattern Day Trading: Max 3/5 days IF balance <£25k (we'll stay under)
   └─ Tax: 20% CG tax on profits
   └─ No annual limit
```

### SOLUTION 5B: ISA COMPLIANCE (CRITICAL)

**ISA Eligibility Check** (built into order routing):

```rust
// rust_core/src/isa_gate.rs (already exists, just expand)

pub struct ISAComplianceGate {
    // ISA-eligible stocks (UK stocks only)
    allowed_exchanges: HashSet<&'static str> = {
        let mut set = HashSet::new();
        set.insert("LSE");  // London Stock Exchange
        set.insert("AIM");  // Alternative Investment Market (UK)
        set
    },

    // ISA-ineligible stocks (block these in ISA account)
    blocked_exchanges: HashSet<&'static str> = {
        let mut set = HashSet::new();
        set.insert("NASDAQ");   // US only
        set.insert("NYSE");     // US only
        set.insert("EURONEXT"); // EU only
        set.insert("JPX");      // Japan only
        set.insert("HKEX");     // Hong Kong only
        set
    },
}

impl ISAComplianceGate {
    pub fn can_trade_in_isa(&self, ticker: &str, exchange: &str) -> bool {
        // ISA: Only UK stocks
        if self.blocked_exchanges.contains(exchange) {
            return false;  // REJECT
        }

        true  // OK for ISA
    }

    pub fn route_order(&self, order: &Order, account: AccountType) -> AccountType {
        match account {
            AccountType::ISA => {
                if !self.can_trade_in_isa(&order.ticker, &order.exchange) {
                    // Force to main account
                    log::warn!("Order routed to Main (not ISA): {} is not ISA-eligible", order.ticker);
                    return AccountType::MAIN;
                }
                return AccountType::ISA;
            }
            AccountType::MAIN => {
                return AccountType::MAIN;  // Main account can trade anything
            }
        }
    }
}

// Example usage:
let mut order = Order::new("GPT3.L", 100, Side::BUY);
order.exchange = "LSE";
let target_account = isa_gate.route_order(&order, AccountType::ISA);
// Returns: ISA (allowed)

let mut order2 = Order::new("MSFT", 50, Side::BUY);
order2.exchange = "NASDAQ";
let target_account = isa_gate.route_order(&order2, AccountType::ISA);
// Returns: MAIN (ISA blocked, route to main account)
```

### SOLUTION 5C: PDT RULE WORKAROUND (US ONLY)

**Pattern Day Trading monitoring**:

```python
class PDTComplianceMonitor:
    async def monitor_pdt_rule(self):
        """
        US PDT Rule: If account balance < £25k, can only do 3 day trades in 5 days.
        A "day trade" = open and close same position on same trading day.

        Strategy to stay under PDT:
        1. Keep day trades < 3 per 5-day rolling window
        2. Hold positions overnight (becomes swing trade, not day trade)
        3. Distribute trades across days
        """

        # Get account balance
        balance = await self.ibkr.get_account_balance()

        if balance < 25000:  # Under PDT threshold
            # Count day trades in last 5 days
            day_trades_5day = self._count_day_trades(days=5)

            if day_trades_5day >= 3:
                log.warning(f"PDT limit reached: {day_trades_5day}/3 day trades in 5 days")
                # Option 1: Hold positions overnight (no longer a day trade)
                # Option 2: Wait until tomorrow (reset counter)
                self.risk_manager.max_intraday_holds = 0  # No new day trades
                self.risk_manager.min_hold_time = 24 * 60 * 60  # Hold >= 1 day
        else:
            # Over £25k = unlimited day trades
            self.risk_manager.min_hold_time = 0  # Can day trade

    def _count_day_trades(self, days: int = 5) -> int:
        """Count trades opened and closed on same day in last N days"""
        day_trades = 0

        for trade in self.trade_history.last_n_days(days):
            if trade.entry_date == trade.exit_date:  # Opened and closed same day
                day_trades += 1

        return day_trades
```

### SOLUTION 5D: AUTOMATED REPORTING

**Monthly compliance report** (auto-generated):

```python
class ComplianceReporter:
    def generate_monthly_report(self, month: int, year: int):
        """Generate tax/compliance reports for ISA + Main accounts"""

        report = {
            "period": f"{month:02d}/{year}",
            "isa_account": {
                "trades": len(self.isa_trades),
                "realized_pnl": self.isa_pnl,
                "unrealized_pnl": self.isa_unrealized,
                "tax": "£0 (ISA-eligible)",
                "total_contributions": self.isa_contributions_ytd,
                "remaining_allowance": 20000 - self.isa_contributions_ytd,
            },
            "main_account": {
                "trades": len(self.main_trades),
                "realized_pnl": self.main_pnl,
                "unrealized_pnl": self.main_unrealized,
                "capital_gains_tax": self.main_pnl * 0.20,  # 20% CG tax
                "total_contributions": self.main_contributions_ytd,
            },
            "combined": {
                "total_trades": len(self.all_trades),
                "total_realized_pnl": self.isa_pnl + self.main_pnl,
                "win_rate": f"{self.win_rate:.1f}%",
                "max_drawdown": f"{self.max_dd:.1f}%",
                "sharpe_ratio": f"{self.sharpe:.2f}",
            },
            "compliance_checklist": {
                "isa_only_lse_stocks": True if self.isa_violations == 0 else False,
                "pdt_rule_compliant": True if self.pdt_violations == 0 else False,
                "daily_loss_limit_honored": True,
                "position_limits_honored": True,
                "leverage_limits_honored": True,
                "audit_trail_complete": True,
                "reconciliation_complete": True,
            }
        }

        # Save as PDF + CSV
        self.save_report_pdf(report, f"aegis_compliance_{month}_{year}.pdf")
        self.save_report_csv(report, f"aegis_compliance_{month}_{year}.csv")

        return report
```

---

## PROBLEM 6: CAPITAL EFFICIENCY

### The Challenge
With £10,000, need to maximize returns while managing risk across multiple time zones and account types.

### SOLUTION 6A: CAPITAL ALLOCATION STRATEGY

**Dynamic rebalancing** (after each session):

```
Starting: £10,000 total
├─ ISA Account: £4,000 (40%, LSE only)
└─ Main Account: £6,000 (60%, US/Europe/Asia)

AFTER LSE SESSION (16:30 UTC close):
├─ ISA P&L: £180 (added) or -£50 (lost)
├─ ISA balance now: £4,180 or £3,950
├─ If imbalanced (one >70% equity):
│  └─ Transfer 10% from overweight to underweight
│  └─ Keeps both accounts >£1,500 minimum
└─ New balances: Rebalanced

AFTER US SESSION (21:00 UTC close):
├─ Main P&L: +£750 (added)
├─ Main balance now: £6,750
├─ Combined: £4,180 + £6,750 = £10,930 (+9.3% day 1)
└─ If winning: Let it ride; if losing: Rebalance

DAILY MAXIMUM LEVERAGE (Protection):
├─ Total equity <= £10,000 × 1.5 = £15,000 (never borrow)
├─ Per account leverage: <= 1.3x (if using margin)
└─ Most likely: 1.0x (no leverage, 100% cash)
```

### SOLUTION 6B: CAPITAL ALLOCATION ACROSS MARKETS

**Optimal allocation by market liquidity**:

```
IF trading all 4 markets (Weeks 7+):

Market      | Capital | Rationale
─────────────────────────────────────────────────
LSE         | 35%     | High liquidity, tax-free (ISA)
US          | 30%     | Highest liquidity, largest moves
Euronext    | 20%     | Medium liquidity
Japan/HK    | 15%     | Low liquidity, high FX risk

Capital distribution:
├─ ISA account: £3,500 (LSE only)
├─ Main account: £6,500 (US + Europe + Asia)
│  ├─ US reserves: £1,950 (30%)
│  ├─ Euronext reserves: £1,300 (20%)
│  └─ Asia reserves: £975 (15%)
└─ Cash buffer: £500 (to cover margin/slippage)

Rebalancing rule:
├─ Daily after each market close
├─ If one market > 70% equity: Move 10% to others
├─ If one market < 10% equity: Move 10% from others
└─ Target: Equal-weight across active markets
```

### SOLUTION 6C: CASH DRAG CALCULATION

**How much cash to keep ready**:

```
Per-trade cash needs:
├─ Order execution: £500-1,000 (to cover margin requirements)
├─ Slippage: 0.1-0.2% of position size
├─ Commission: £0.50-2.00 per trade
└─ Total per trade: £2-5 avg

Daily 200-300 trades:
├─ Total cash needed: £200-500 (for partial fills)
├─ But: Reusing capital every 5-30 minutes
├─ Effective: Need only £500-1,000 cash buffer
└─ As % of capital: 5-10%

Recommended cash buffer: £1,000 (10% of £10k)
├─ Prevents forced liquidation if market gaps
├─ Covers commissions for multi-day losses
└─ Leaves £9,000 for active trading
```

---

## PROBLEM 7: TECHNICAL ARCHITECTURE

### The Challenge
Single Python engine vs multiple engines for 4 time zones? How to manage state across brokers?

### SOLUTION 7A: RECOMMENDED: SINGLE UNIFIED ENGINE

**Why single engine is better**:

```
✅ Pros:
  ├─ One risk manager (sees total equity)
  ├─ One DQN agent (learns globally, not per-market)
  ├─ One WAL (single audit trail)
  ├─ Easier to debug
  ├─ Lower CPU/memory usage
  └─ Simpler deployment

❌ Cons:
  ├─ Must handle 4 broker APIs simultaneously
  ├─ More complex state management
  ├─ Single point of failure (but we have circuit breaker)
  └─ Sleep pattern required (monitoring at 00:30-23:00 UTC)
```

**Architecture** (modified `main.py`):

```python
# main.py (unified 24-hour engine)

import asyncio
from config import settings
from core import SessionManager, RiskManager, TradingEngine
from brokers import IBKRMultiClient
from data import MultiTierDataLoader

class AEGISUnifiedEngine:
    def __init__(self):
        self.ibkr = IBKRMultiClient()  # Manages 2 IBKR accounts
        self.session_mgr = SessionManager()
        self.risk_mgr = RiskManager()
        self.trading_engine = TradingEngine()
        self.data_loader = MultiTierDataLoader()
        self.position_mgr = PositionManager()
        self.reconciler = MultiAccountReconciliation()

    async def run_24hour_loop(self):
        """Main loop: runs forever until stopped"""

        # Background tasks (run in parallel)
        tasks = [
            self.continuous_trading_loop(),   # 5-sec loop
            self.monitor_connections(),       # 30-sec checks
            self.monitor_data_feed(),         # 10-sec checks
            self.nightly_ouroboros(),         # 23:00 UTC job
            self.reconcile_accounts(),        # 21:30 UTC job
        ]

        await asyncio.gather(*tasks)

    async def continuous_trading_loop(self):
        """Main trading loop: executes every 5 seconds"""

        while True:
            try:
                # 1. Check session (which market is active?)
                session = self.session_mgr.get_current_session()

                if session == TradingSession.Idle:
                    # 21:00-23:00 UTC: No trading, just wait
                    await asyncio.sleep(60)
                    continue

                # 2. Get data (IBKR → yfinance → cache)
                bars = await self.data_loader.get_bars(session)

                # 3. Run 33 signal modules
                signals = await self.trading_engine.run_modules(bars, session)

                # 4. Weight votes (DQN)
                weighted_signal = self.trading_engine.dqn_weight(signals)

                # 5. Risk checks (hard stops)
                if not self.risk_mgr.pass_all_checks(weighted_signal, session):
                    await asyncio.sleep(5)
                    continue  # Skip this 5-sec cycle

                # 6. Generate orders
                orders = self.trading_engine.generate_orders(weighted_signal, session)

                # 7. Route orders to correct broker
                for order in orders:
                    account = self.ibkr.route_order(order)
                    await self.ibkr.place_order(order, account)

                # 8. Update position tracking
                self.position_mgr.update(orders)

            except Exception as e:
                log.error(f"Error in trading loop: {e}")
                self.trigger_circuit_breaker()

            # Sleep until next 5-sec cycle
            await asyncio.sleep(5)

    async def nightly_ouroboros(self):
        """Runs every day at 23:00 UTC"""

        while True:
            now = datetime.utcnow()

            # Wait until 23:00 UTC
            if now.hour != 23 or now.minute != 0:
                await asyncio.sleep(60)
                continue

            log.info("=== OUROBOROS STARTING ===")

            # 1. Disconnect from market data
            await self.data_loader.disconnect()

            # 2. Bootstrap dividends/splits (37.5 min each)
            await self.ouroboros_bootstrap_dividends()
            await self.ouroboros_bootstrap_splits()

            # 3. Fetch fresh data
            await self.ouroboros_yfinance_fetch()

            # 4. Adjust prices (retroactively)
            await self.ouroboros_price_adjustment()

            # 5. Fit GARCH volatility
            await self.ouroboros_garch_fit()

            # 6. Update DQN weights
            await self.trading_engine.update_dqn_weights()

            # 7. Reset daily counters
            self.risk_mgr.reset_daily_counters()

            log.info("=== OUROBOROS COMPLETE ===")

            # 8. Reconnect to market data
            await self.data_loader.connect()

            # Sleep until next day's 23:00
            await asyncio.sleep(86400)  # 24 hours
```

### SOLUTION 7B: STATE MANAGEMENT (PER-MARKET)

**How to track positions across 2 accounts**:

```python
class PositionManager:
    def __init__(self):
        self.positions_isa = {}  # {ticker: Position}
        self.positions_main = {}  # {ticker: Position}

    def get_position(self, ticker: str, account: AccountType) -> Position:
        """Get position for specific ticker in specific account"""

        if account == AccountType.ISA:
            return self.positions_isa.get(ticker)
        else:
            return self.positions_main.get(ticker)

    def get_all_positions(self) -> List[Position]:
        """Get all open positions across both accounts"""
        return list(self.positions_isa.values()) + list(self.positions_main.values())

    def get_total_equity(self) -> float:
        """Sum of both accounts' equity"""
        isa_equity = sum(p.market_value for p in self.positions_isa.values())
        main_equity = sum(p.market_value for p in self.positions_main.values())
        return isa_equity + main_equity

    def get_daily_pnl_by_account(self) -> Dict[AccountType, float]:
        """P&L per account"""
        return {
            AccountType.ISA: sum(p.realized_pnl for p in self.positions_isa.values()),
            AccountType.MAIN: sum(p.realized_pnl for p in self.positions_main.values()),
        }
```

---

## PROBLEM 8: MODEL/STRATEGY DIFFERENCES

### The Challenge
Same 33 modules for all 4 markets? Or market-specific tuning?

### SOLUTION 8A: MARKET-SPECIFIC PARAMETERS

**One code base, market-specific configs**:

```toml
# config/aegis_global.toml

[modules]
# Global settings (apply to all markets)
momentum_threshold = 0.70
mean_reversion_threshold = 0.50
volatility_threshold = 0.75

[market_asia]
# Japan + Hong Kong specific
momentum_threshold = 0.75  # Higher threshold (noisier market)
hold_time_sec = 300  # Hold longer (lower liquidity)
max_position_pct = 0.10  # Smaller positions (lower liquidity)
garch_lookback_days = 200  # Shorter window (higher volatility)

[market_europe]
# LSE + Euronext + Deutsche Boerse
momentum_threshold = 0.70
hold_time_sec = 600  # Hold longer (lower volume than US)
max_position_pct = 0.15
garch_lookback_days = 252  # Standard

[market_us]
# NASDAQ + NYSE
momentum_threshold = 0.65  # Lower (highest liquidity)
hold_time_sec = 300  # Hold shorter (high liquidity allows quick exits)
max_position_pct = 0.20  # Larger positions (highest liquidity)
garch_lookback_days = 252

[dqn]
# Single global DQN, but per-market performance tracking
global_weights = true  # One DQN learns all markets
per_market_accuracy = true  # Track accuracy by market
adjust_weights_per_session = true  # Reweight after each session
```

**Implementation** (in `trading_engine.rs`):

```rust
pub struct TradingEngine {
    global_dqn: DQNAgent,  // One agent, learns all markets
    market_params: HashMap<TradingSession, MarketParams>,
}

impl TradingEngine {
    pub fn run_modules(&self, bars: &BarData, session: TradingSession) -> ModuleVotes {
        let params = &self.market_params[&session];

        // Run all 33 modules with market-specific thresholds
        let momentum_signals = self.momentum_modules(bars, params.momentum_threshold);
        let mean_reversion_signals = self.mean_reversion_modules(bars, params.mean_reversion_threshold);
        let volatility_signals = self.volatility_modules(bars, params.volatility_threshold, params.garch_model);

        // Combine all votes
        ModuleVotes {
            momentum: momentum_signals,
            mean_reversion: mean_reversion_signals,
            volatility: volatility_signals,
            // ... (30 more modules)
        }
    }

    pub fn dqn_weight(&self, votes: &ModuleVotes, session: TradingSession) -> Signal {
        // Use global DQN weights, but adjust for session
        let weights = &self.global_dqn.weights;

        let weighted_signal = votes.momentum.votes * weights.momentum * session.momentum_boost
                            + votes.mean_reversion.votes * weights.mean_reversion * session.mr_boost
                            + // ... (31 more)

        weighted_signal
    }
}
```

---

## PROBLEM 9: COSTS & PROFITABILITY

### The Challenge
Costs eat into profits. Need to break even quickly.

### SOLUTION 9A: DETAILED COST BREAKDOWN

**MVP Phase (Weeks 1-6: LSE + US only)**:

```
MONTHLY COSTS:
├─ IBKR commissions:
│  ├─ 200 trades/day × £0.50 avg = £100/day
│  ├─ × 20 trading days/month = £2,000/month
│  └─ Reduce via volume discounts: Negotiate to £0.30/trade → £1,200/month
│
├─ Data costs:
│  ├─ IBKR real-time: £0 (free with trading)
│  ├─ yfinance: £0 (free)
│  ├─ Polygon free tier: £0
│  └─ Total: £0/month
│
├─ AWS hosting:
│  ├─ EC2 t3.medium (24/7): £30/month
│  └─ Total: £30/month
│
├─ ISA/IBKR account fees:
│  ├─ ISA: £0/month (no fee)
│  ├─ IBKR: £0/month (no minimum balance fee)
│  └─ Total: £0/month
│
└─ TOTAL MONTHLY: £1,230/month (if £0.30/trade negotiated)

BREAK-EVEN CALCULATION:
├─ Need £1,230/month profit to cover costs
├─ ÷ 20 trading days = £61.50/day break-even
├─ On £10,000 capital = 0.615% daily needed to break even
├─ Target: 0.3-0.5% daily
└─ Result: PROFITABLE (target >> break-even)
```

**Full 24-Hour Phase (Weeks 7+: Add Asia)**:

```
ADDITIONAL MONTHLY COSTS:

├─ Polygon upgrade (if needed):
│  ├─ Basic tier: £50/month → 20 calls/minute
│  └─ Pro tier: £300/month → unlimited
│
├─ Data vendor (alternative):
│  ├─ Refinitiv (Bloomberg-quality): £500/month
│  └─ (Only if IBKR insufficient)
│
├─ Japan broker (if separate from IBKR):
│  ├─ Account fee: £10-30/month
│  ├─ Commissions: JPX is expensive (0.15% typical)
│  └─ Probably: Use IBKR only, skip Japan
│
├─ HK broker (if separate):
│  ├─ Account fee: £5-20/month
│  ├─ Commissions: HKEX ~£1-2 per trade
│  └─ Probably: Use IBKR only, skip separate accounts
│
└─ TOTAL ADDITIONAL: £50-550/month (depending on choices)

FULL SYSTEM COST: £1,230 + £300 = £1,530/month
BREAK-EVEN: £1,530 ÷ 20 days = £76.50/day = 0.765% daily
TARGET: 0.3-0.5% daily

DECISION: Only add Asia if daily P&L reaches £150+/day
├─ Then incremental cost (£300) is justified
└─ If daily is £50-100, skip Asia expansion
```

### SOLUTION 9B: PROFITABILITY ANALYSIS

**Year 1 Projections**:

```
SCENARIO 1: MVP SUCCESS (LSE + US, 0.4% daily)
├─ Starting capital: £10,000
├─ Daily profit target: £40 (0.4%)
├─ Monthly profit: £40 × 20 = £800
├─ Monthly costs: £1,230
├─ Net monthly: -£430 (NEGATIVE! Problem.)
│
│ BUT: Months 1-3 building up, then:
│ Month 4-12: If 0.5% daily achieved
├─ Daily: £50
├─ Monthly profit: £50 × 20 = £1,000
├─ Monthly costs: £1,230
├─ Net monthly: -£230 (still negative, but improving)
│
│ Year 1 total:
├─ Cumulative trading P&L: +£12,000-15,000 (1.2-1.5x capital)
├─ Less costs: -£14,760 (12 months × £1,230)
├─ Net P&L: -£2,760 to -£760
├─ Result: Year 1 breakeven or slightly negative
└─ Year 2: Profit if compound and reduce costs

DECISION: Negotiate IBKR commissions DOWN to £0.10/trade
├─ £0.10 × 200 trades/day × 20 days = £400/month
├─ Total costs: £430/month
├─ Break-even: £21.50/day = 0.215% (achievable!)
├─ At 0.4% daily: Profit £8/day = £160/month (Year 1 positive)
└─ Year 1: Break-even to +£2,000 profit
```

**Recommendation**:

✅ **Negotiate IBKR commissions to £0.10-0.20/trade** before starting
- At £0.10: Break-even at 0.215% daily
- At £0.20: Break-even at 0.43% daily
- Ask IBKR about "active trader discounts" (they typically offer 20-50% off)

---

## PROBLEM 10: PHASED IMPLEMENTATION

### The Challenge
How to scale gradually without rewriting the system each time?

### SOLUTION 10A: PHASED ROADMAP (15 WEEKS)

```
WEEK 1-2: BOOTSTRAP + ISA SETUP (LSE only)
├─ Task: Complete Week 1 bootstrap + RM refactoring (all 5 mandates)
├─ Trading: Minimal (just verify system works)
├─ Success criteria: 588 tests passing, £0-100/day profit
├─ Go/No-Go: Proceed only if 45%+ win rate on 100+ trades
└─ Capital: £4,000 in ISA (LSE only)

WEEK 3-4: ADD US TRADING (LSE + US)
├─ Setup: Create IBKR main account (Client ID 101)
├─ Load: £6,000 into main account
├─ Adapt: Add US market params to config
├─ Trade: LSE 08:00-16:30 UTC + US 14:30-21:00 UTC (overlap) + 16:30-21:00 (pure US)
├─ Success criteria: £100-200/day profit, 50%+ win rate
├─ Go/No-Go: Proceed only if combined 0.2%+ daily achieved
└─ Capital: £4,000 ISA + £6,000 Main

WEEK 5-6: ADD EURONEXT (LSE + Europe + US)
├─ Setup: Euronext trading enabled in IBKR main account
├─ Load: Reallocate capital: £4k ISA, £6k Main (no new capital)
├─ Adapt: Add Europe market params
├─ Trade: LSE 08:00-16:30 + Euronext 09:00-17:00 + US 14:30-21:00 (overlaps)
├─ Success criteria: £150-250/day profit, 50%+ win rate
├─ Go/No-Go: Only add Asia if daily > £150/day consistently
└─ Capital: Same (no new capital injected)

WEEK 7-8: OPTIONAL - ADD JAPAN (If daily > £150)
├─ Setup: JPX trading enabled in IBKR main account
├─ Load: Reallocate capital: £3,500 ISA, £6,500 Main
├─ Adapt: Add Japan market params
├─ Trade: Japan 23:50-06:30 UTC (nightly)
├─ Risk: HIGH (JPY volatile, low liquidity)
├─ Go/No-Go: Abort if any session profitability drops
└─ Capital: Reallocated (no new capital)

WEEK 9-10: OPTIONAL - ADD HK (If daily > £200)
├─ Setup: HKEX trading enabled in IBKR main account
├─ Load: Reallocate capital: £3,500 ISA, £6,500 Main
├─ Adapt: Add HK market params
├─ Trade: HK 01:30-08:00 UTC (nightly, parallel with Japan)
├─ Risk: VERY HIGH (HKD pegged, China policy risk)
├─ Go/No-Go: Abort if Japan session not profitable
└─ Capital: Reallocated (no new capital)

WEEK 11-15: LIVE CAPITAL DEPLOYMENT (All markets)
├─ Week 11: £1,000 paper → live test
├─ Week 12: £2,000 live (if WR ≥ 45%)
├─ Week 13: £5,000 live (if WR ≥ 50% + Sharpe ≥ 1.5)
├─ Week 14: £10,000 live (if WR ≥ 52% + Sharpe ≥ 1.8)
├─ Week 15: Full optimization at scale
└─ Target: 0.3-0.5% daily on live capital
```

### SOLUTION 10B: GO/NO-GO GATES

**Decision criteria for each phase**:

```python
class PhaseGates:
    def week_2_gate(self) -> bool:
        """Can we proceed to US trading?"""

        # Minimum requirements
        checks = {
            "tests_passing": self.test_count == 588,
            "isa_account_created": self.isa_client.is_connected,
            "bootstrap_complete": self.ouroboros_succeeded,
            "daily_pnl_positive": self.pnl_manager.daily_pnl > 0,
            "win_rate": self.stats.win_rate > 0.45,
            "max_drawdown": self.stats.max_drawdown < 0.08,
        }

        if not all(checks.values()):
            failed = [k for k, v in checks.items() if not v]
            log.error(f"Week 2 gate FAILED: {failed}")
            return False

        log.info("Week 2 gate PASSED - Proceeding to US trading")
        return True

    def week_4_gate(self) -> bool:
        """Can we add Euronext?"""

        combined_metrics = {
            "daily_pnl": self.pnl_manager.daily_pnl,  # Must be £100+
            "win_rate": self.stats.win_rate,  # Must be 50%+
            "sharpe": self.stats.sharpe_ratio,  # Should be >1.0
            "drawdown": self.stats.max_drawdown,  # Must be <8%
        }

        if combined_metrics["daily_pnl"] < 100:
            log.error("Week 4 gate FAILED: Daily P&L < £100")
            return False

        if combined_metrics["win_rate"] < 0.50:
            log.error("Week 4 gate FAILED: Win rate < 50%")
            return False

        log.info("Week 4 gate PASSED - Adding Euronext")
        return True

    def week_6_gate(self) -> bool:
        """Can we add Asia?"""

        if self.pnl_manager.daily_pnl < 150:
            log.warning("Week 6 gate WARNING: Daily P&L < £150, skipping Asia")
            return False

        log.info("Week 6 gate PASSED - Ready for Asia trading")
        return True
```

---

## FINAL CHECKLIST: SOLUTIONS SUMMARY

| Problem | Solution | Status | Timeline |
|---------|----------|--------|----------|
| **1. Broker Infrastructure** | 2-account IBKR setup (ISA + Main) + routing logic | ✅ Ready | Week 1 setup |
| **2. Data Infrastructure** | Tiered (IBKR → yfinance → cache) with fallback | ✅ Ready | Week 1 implementation |
| **3. FX Risk** | 50% static hedge on USD/EUR exposure | ✅ Recommended | Week 3 (before US trading) |
| **4. Operational Risk** | Circuit breakers, auto-reconnect, reconciliation | ✅ Ready | Week 1 implementation |
| **5. Regulatory** | ISA compliance gate + PDT monitoring | ✅ Ready | Week 1 implementation |
| **6. Capital Efficiency** | Dynamic rebalancing, 10% cash buffer | ✅ Ready | Week 1 implementation |
| **7. Technical Architecture** | Single unified engine, per-market state | ✅ Ready | Week 1 implementation |
| **8. Model Differences** | Market-specific params, global DQN | ✅ Ready | Week 1 implementation |
| **9. Costs & Profitability** | Negotiate £0.10-0.20/trade commissions | ⚠️ Critical | Before trading starts |
| **10. Phased Implementation** | 15-week plan with go/no-go gates | ✅ Ready | Weeks 1-15 execution |

---

## EXECUTION CHECKLIST (Before Week 1 Starts)

**By Friday March 13 (TODAY)**:
- [ ] Read this document thoroughly
- [ ] Identify which problems affect Week 1 (mainly #1, #2, #4, #5)
- [ ] Decide on FX hedging (Problem #3)

**By Monday March 17 (Before bootstrap starts)**:
- [ ] Setup IBKR ISA account (Account 2, Client ID 102)
- [ ] Verify LSE trading enabled
- [ ] Negotiate commissions: Ask IBKR about "active trader discounts"
- [ ] Load £4,000 into ISA account
- [ ] Test connection to IBKR ISA (place small test order)
- [ ] Implement ISA compliance gate in code
- [ ] Review circuit breaker + fallback logic

**During Week 1 (March 17-21)**:
- [ ] Complete bootstrap (Tasks 1-3)
- [ ] Implement all RM mandates (RM-1 through RM-5)
- [ ] Test connection failover (disconnect IBKR, verify fallback to yfinance)
- [ ] Run reconciliation test (verify P&L calculations match)
- [ ] Pass Week 1 gate: 588 tests passing, 45%+ win rate, £30-50/day target

**Before Week 3 (US trading starts)**:
- [ ] Create IBKR main account (Client ID 101)
- [ ] Load £6,000 into main account
- [ ] Setup USD/GBP hedge (50% of US equity exposure)
- [ ] Implement multi-broker routing logic
- [ ] Test placing orders on both ISA + Main accounts
- [ ] Verify reconciliation works with 2 accounts

---

## BOTTOM LINE

**You can build this 24-hour global system. Here are the solutions:**

1. ✅ **Broker**: Use IBKR ISA + IBKR Main (2 accounts, same parent)
2. ✅ **Data**: Tiered (IBKR → yfinance → cache)
3. ✅ **FX**: 50% hedge on USD/EUR (costs 0.15%/month, prevents disaster)
4. ✅ **Operations**: Circuit breakers, auto-reconnect, multi-broker reconciliation
5. ✅ **Regulatory**: ISA gate + PDT monitoring (both built-in)
6. ✅ **Capital**: Dynamic rebalancing, 10% cash buffer
7. ✅ **Architecture**: Single engine, per-market state management
8. ✅ **Models**: Market-specific params, global DQN
9. ⚠️ **Costs**: Negotiate £0.10-0.20/trade (critical!)
10. ✅ **Phasing**: 15-week go/no-go gates (abort if metrics fail)

**Timeline**: Weeks 1-2 (LSE only) → Weeks 3-4 (add US) → Weeks 5-6 (add Europe) → Weeks 7-10 (optional Asia) → Weeks 11-15 (live capital)

**Expected returns**: 0.3-0.5% daily MVP (LSE+US) = 75-125% annual

Let me know which problem needs more depth. Ready to start Week 1? 🚀
