# AEGIS V2 — Investment & Scaling Proposal
## What Happens When You Throw Money At It

**Date**: 11 March 2026 (v4.0 — post Phase 1 fixes, 22-hour universe framing)
**Current Monthly Cost**: ~$65 (EC2 instance + Elastic IP)
**Current Annual Cost**: ~$780
**System State**: Paper trading, 39+ ticker universe (12 core ISA contracts + 27 extended LSE ETPs, nightly expansion toward ~1,000), realtime data requested (subscription required), buy+sell capability, self-contained V2 Docker stack. Designed for near-22-hour global trading coverage — multi-session modules for Asian (TSE), European (XETRA/Euronext), and UK/US sessions are coded and ready to wire.

---

## EXECUTIVE SUMMARY

AEGIS V2 is currently running on a shoestring budget — a $60/month AWS server with free-tier everything. As of 11 March 2026, the three most severe structural defects have been fixed (broker can now sell, data is requested in realtime, fills are attributed to the correct ticker), and the engine is self-contained with its own IB Gateway. The system is architected for near-22-hour global market coverage — multi-session modules for Asian (TSE), European (XETRA/Euronext), and UK/US sessions are already coded and ready to wire, scaling from the current 39-ticker universe toward 1,000+ instruments across global exchanges. This document maps out exactly what each upgrade buys you, in order of impact-per-dollar.

---

## TIER 1: ESSENTIALS ($150-250/month) — "Turn On the Lights"

### 1A: IBKR Market Data Subscription ($4.50-39/month)
**What it does**: Activates the real-time data feed that the engine now requests.

**Current state (post Phase 1D fix)**: The engine now requests `MarketDataType::Realtime` (Type 1) instead of the old `DelayedFrozen` (Type 4). If no IBKR data subscription is active, IB Gateway gracefully falls back to delayed data. **The code fix is done — you just need to subscribe.**

**What you get**:
- Real-time bid/ask quotes for all subscribed LSE ETPs
- Accurate spread data (replacing synthetic estimate from `realtime_bars()` OHLCV)
- Valid paper trading track record (currently on fallback delayed until subscription active)
- Proper Chandelier stop-loss execution timing
- The 2-minute stale data check in the Risk Arbiter becomes meaningful

**Impact**: **CRITICAL** — The cheapest and highest-ROI investment. The engine is ready for it — just activate the subscription.

**Options**:
| Bundle | Monthly | Coverage |
|--------|---------|----------|
| LSE Level 1 | $4.50 | Real-time quotes for LSE-listed instruments |
| US Consolidated (NASDAQ + NYSE) | $4.50 | For tracking underlying indices |
| IBKR Snapshot Bundle | $1/snapshot | Pay-per-use alternative |
| Quote Booster (100→300 lines) | $30/month | For scaling to more tickers |
| Full Bundle (LSE + US + Quote Booster) | ~$39/month | Recommended |

### 1B: EC2 Upgrade — c7i-flex.xlarge ($100-130/month)
**What it does**: Doubles RAM from 4GB to 8GB, keeps 2 vCPU.

**Current state**: 4GB RAM with 3 Docker containers (engine 1GB + IB Gateway 1GB + Redis 512MB = 2.5GB used). Only 1.5GB headroom. The V1 engine was getting OOM-killed hourly at similar memory pressure.

**What you get**:
- 8GB RAM (3.2x headroom instead of 1.5x)
- Room for the War Room dashboard container
- Room for Ouroboros to process larger datasets
- Safety margin against Docker memory spikes

**Why not bigger?** The engine is I/O-bound (waiting for IBKR ticks), not CPU-bound. Doubling RAM is more impactful than adding CPU cores. The c7i-flex.xlarge is the sweet spot.

### 1C: EBS Volume Upgrade ($5-10/month)
**What it does**: Increases disk from 19GB to 50GB.

**Current state**: 57% used (11GB of 19GB). WAL files grow ~1MB/day. After 6 months of trading + Docker images + log rotation, the disk will hit 90%+.

**What you get**: 2+ years of WAL history without rotation, room for backtest data, room for GARCH model archives.

### Tier 1 Total: ~$175/month ($2,100/year)

**ROI Assessment**: The engine is now engineered correctly (buy+sell, realtime data request, correct fill routing). This tier activates the realtime data subscription and adds infrastructure headroom, turning AEGIS from "ready-to-go engine on delayed data" to "functional trading system with live prices."

---

## TIER 2: ACCELERATION ($300-500/month) — "Open the Throttle"

### 2A: Polygon.io Starter Plan ($29/month → $79/month)
**What it does**: Unlimited API calls for corporate actions, historical data, and reference data.

**Current state**: Free tier with 5 calls/minute. The bootstrap scripts need 37.5 minutes each for dividends and splits because of rate limiting.

**What you get**:
- Full dividend + splits calendar in <2 minutes (vs 75 minutes)
- Reference data for all US + UK instruments
- Historical bars for backtesting
- ASER (Alpha-Sized Efficiency Ratio) calculated from real data

### 2B: TwelveData Growth Plan ($79/month)
**What it does**: Real-time streaming data for 300+ symbols with 1-minute bars.

**Current state**: Free tier, limited symbols, 8 calls/minute.

**What you get**:
- Secondary data feed (redundancy if IBKR drops)
- 1-minute bars for intraday analysis
- Forex data for USD/GBP hedging intelligence
- Can feed the RotationScanner with broader market data

### 2C: EC2 Upgrade — c7i.large or m7i-flex.large ($130-180/month)
**What it does**: Dedicated vCPUs (not flex/burstable) + 8-16GB RAM.

**Why**: Burstable instances throttle CPU after sustained use. During market hours (8 hours/day), the engine runs continuously. A dedicated-CPU instance ensures consistent tick processing.

### 2D: S3 Backup + CloudWatch ($15/month)
**What it does**: Automated daily WAL backup to S3 + basic monitoring alerts.

**What you get**:
- WAL files backed up nightly (disaster recovery)
- CloudWatch alarms on CPU/memory/disk thresholds
- Email/SMS alerts without custom Telegram bot

### Tier 2 Total: ~$400/month ($4,800/year)

**ROI Assessment**: This tier enables backtesting (Polygon historical data), data redundancy (TwelveData), and eliminates infrastructure anxiety (dedicated CPU, S3 backups).

---

## TIER 3: EXPANSION ($500-1,000/month) — "Scale the Universe"

### 3A: IBKR Quote Booster 2 ($60/month → 1,000 market data lines)
**What it does**: Scales from 100 to 1,000 simultaneous market data subscriptions.

**Current state**: 39 tickers use 39 of 100 free lines (12 core ISA + 27 extended). The SubscriptionManager (dead code in `subscription_manager.rs`) is designed for dynamic rotation across 1,000 tickers.

**What you get**:
- Monitor 200+ LSE instruments simultaneously
- Pre-screen the entire LSE leveraged ETP universe
- Feed HotScanner with 100x more opportunities
- Wire the RotationScanner for real sector rotation

### 3B: IBKR Pro Account (commission structure change)
**What it does**: Switches from fixed to tiered commission structure.

**Impact**: At scale (100+ trades/month), tiered pricing saves 20-40% on commissions. For the current 39-ticker universe, the difference is minimal.

### 3C: Dedicated EC2 — c7i.xlarge ($200-250/month)
**What it does**: 4 vCPU, 8GB RAM, dedicated.

**Why**: 4 cores allows true parallel processing: 1 for engine, 1 for Python bridge, 1 for War Room dashboard, 1 for system overhead. At 1,000 tickers with aggressive scanning, this becomes necessary.

### 3D: European Market Data ($30-50/month)
**What it does**: Real-time data for XETRA, Euronext, BME (European exchanges).

**Why**: The codebase already has `european_session.rs` (187 lines, dead code) designed for multi-exchange trading. With European market data, AEGIS can trade European leveraged ETPs alongside LSE ones. Different market hours = more trading windows.

### Tier 3 Total: ~$750/month ($9,000/year)

**ROI Assessment**: This tier transforms AEGIS from a 39-ticker LSE-focused system into a 200+ ticker multi-exchange scanner covering near-22-hour global trading. Multi-session modules (`asian_session.rs` 299 LOC, `european_session.rs` 187 LOC, `cross_timezone.rs` 216 LOC) are already coded — you're paying to unlock capabilities that exist as dead code. The 22-hour coverage means the system can find opportunities across Asian, European, and UK/US sessions rather than sitting idle 16 hours a day.

---

## TIER 4: INSTITUTIONAL ($1,500-3,000/month) — "The Hedge Fund Stack"

### 4A: Colocation or Low-Latency Server ($500-1,500/month)
**What it does**: Places the server physically near the exchange or uses a low-latency cloud provider (Equinix LD4 for LSE, or AWS eu-west-2 London region).

**Current state**: The engine runs in us-east-1 (Virginia, USA). Round-trip latency to LSE is ~80ms. For 5-second bars, this doesn't matter. For tick-by-tick or sub-second strategies, it's a disadvantage.

**What you get**:
- <5ms round-trip to LSE
- Sub-second order execution
- Ability to use TWAP/VWAP execution algorithms
- Competitive with prop trading firms

### 4B: Bloomberg Terminal ($2,000/month) or Refinitiv Eikon ($400/month)
**What it does**: Institutional-grade data, analytics, and news.

**Why**: For strategy research, not execution. Bloomberg provides the data that institutional traders use for decision-making. At this investment level, AEGIS would be competitive with small hedge fund infrastructure.

### 4C: GPU Instance for ML Research ($200-500/month)
**What it does**: Enables neural network training for signal enhancement.

**Why**: The AEGIS Master Plan mentions DQN (Deep Q-Networks) and Neural Hawkes processes as Phase Q3-Q4 targets. These require GPU compute for training (inference could run on CPU). A p3.large or g4dn.xlarge instance for periodic training runs would enable this.

### 4D: Multi-Region Deployment ($300-500/month)
**What it does**: Runs AEGIS replicas in London (eu-west-2) and Tokyo (ap-northeast-1).

**Why**: The codebase has `asian_session.rs` (299 lines, dead code) and `cross_timezone.rs` (216 lines, dead code) for multi-timezone trading. With servers in each region, AEGIS can trade LSE (08:00-16:30 London), XETRA (09:00-17:30 CET), and TSE (09:00-15:00 Tokyo) with local-latency execution.

### Tier 4 Total: ~$2,500/month ($30,000/year)

**ROI Assessment**: This tier is only justified if the system is generating consistent profits at Tier 2-3 levels. It enables institutional-grade execution and multi-asset, multi-exchange trading.

---

## TIER 5: THE APEX ($5,000+/month) — "Quantum Apex Protocol"

This tier corresponds to AEGIS Master Plan Phases Q2-Q4, estimated at 1,354 engineering hours.

### 5A: Rust FFI + DPDK Network Stack ($500/month infra + 200h engineering)
- Kernel-bypass networking for sub-microsecond tick ingestion
- Only relevant at tick-by-tick frequency (not 5-second bars)

### 5B: DQN (Deep Q-Network) Meta-Model ($300/month GPU + 400h engineering)
- Neural network learns which strategy to allocate capital to in which regime
- Replaces static Ouroboros with online learning

### 5C: Neural Hawkes Process ($200/month GPU + 400h engineering)
- Self-exciting point process for order flow prediction
- Academic frontier — few production implementations exist

**ROI Assessment**: Speculative. Only pursue after the system proves edge at Tier 1-3.

---

## THE SCALING CURVE

| Investment | Monthly | Annual | Capabilities |
|-----------|---------|--------|--------------|
| **Current** | $65 | $780 | 39+ tickers (12 core ISA), realtime-ready, buy+sell, paper mode |
| **Tier 1** | $175 | $2,100 | 39+ tickers, REAL-TIME data active, valid paper trading |
| **Tier 2** | $400 | $4,800 | 39+ tickers + backtesting + data redundancy |
| **Tier 3** | $750 | $9,000 | 200+ tickers, multi-exchange, near-22-hour global coverage, sector rotation |
| **Tier 4** | $2,500 | $30,000 | Institutional-grade, multi-region deployment, GPU ML, full 22-hour execution |
| **Tier 5** | $5,000+ | $60,000+ | Quantum Apex (DQN, Hawkes, kernel-bypass) |

### Break-Even Analysis

| Tier | Annual Cost | Break-Even Requirement |
|------|------------|----------------------|
| Tier 1 | $2,100 | 21% annual return on £10K starting capital |
| Tier 2 | $4,800 | 48% annual return on £10K |
| Tier 1 + £20K ISA | $2,100 | 10.5% annual return on £20K |
| Tier 2 + £20K ISA | $4,800 | 24% annual return on £20K |
| Tier 3 + £20K ISA | $9,000 | 45% annual return on £20K |

**The math is clear**: Tier 1 is a no-brainer if the strategy has any edge at all. Tier 2 pays for itself at returns that professional momentum strategies routinely achieve. Tier 3+ requires scaling capital beyond the ISA limit or generating exceptional returns.

### The Tax-Free Advantage

Every pound of profit inside the ISA is tax-free. At current UK capital gains tax rates (20% for higher-rate taxpayers), this means:
- A £4,000 profit outside an ISA = £3,200 after tax
- A £4,000 profit inside an ISA = £4,000 after tax
- The ISA wrapper saves £800 per £4,000 profit

Over time, this compounds. After 5 years of £20K contributions with 15% returns:
- ISA value: ~£170,000 (all gains tax-free)
- Taxable account: ~£150,000 (after CGT on £70K gains = £14K tax)
- **ISA advantage: £20,000+**

---

## RECOMMENDED IMMEDIATE ACTION

### Step 0: Complete Phase 2 engineering (FREE — just code)
The engine can now buy and sell and requests realtime data. But the exit path code (Phase 2A) needs validation, and Ouroboros weights need wiring to the engine (Phase 2D). This is pure code work with zero cost.

### Step 1: Subscribe to IBKR LSE Level 1 ($4.50/month)
The engine already requests realtime data (Phase 1D fix). Activating the subscription turns it on. This is the single most impactful investment. Do this today.

### Step 2: Upgrade EBS to 50GB ($5/month)
Prevents disk emergencies. Takes 5 minutes via AWS console.

### Step 3: Wait for Crucible validation
Do NOT invest in Tier 2+ until the system proves edge through 100 paper trades with real-time data, functional exits, and calibrated Ouroboros weights.

### Step 4: If Crucible passes, move to Tier 2
Add Polygon Starter + S3 backups + dedicated CPU.

### Step 5: If 3 months profitable, consider Tier 3
Scale the universe, add European markets.

---

*This is not financial advice. Past performance does not guarantee future results. Leveraged ETFs carry significant risk including total loss of capital. The ISA tax-free status is subject to UK government policy changes.*

---

*Generated: 11 March 2026 (v4.0 — updated post Phase 1 + Phase 2A, 22-hour universe framing)*
*Reference: AEGIS_WAR_FILE_AND_ACTION_PLAN.md v4.0*
*Phase 1 fixes verified: broker sell capability, realtime data, correct fill attribution*
