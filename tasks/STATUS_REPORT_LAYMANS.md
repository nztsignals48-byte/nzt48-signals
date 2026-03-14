# NZT-48 Trading System — Where We Are & Where We're Going
## Plain English Status Report — 8 March 2026

---

## WHAT IS NZT-48?

NZT-48 is an automated stock trading system that runs on a cloud server 24/7. It watches 12 special products on the London Stock Exchange (called "leveraged ETPs") — these are instruments that amplify market moves by 2x, 3x, or even 5x. When the NASDAQ goes up 1%, a 3x product goes up roughly 3%.

The system's goal: make 0.3-0.5% profit per day. That sounds small, but compounded daily over a year, it's **145-348% annual return**. Starting with £10,000, that becomes £24,500-£44,800 after one year.

The system runs in "paper mode" (fake money) right now. No real pounds are at risk yet.

---

## WHERE WE ARE RIGHT NOW

### The Good News
The core architecture is built and running:
- **12 LSE products** being monitored in real-time
- **Signal generation** — the system can spot momentum opportunities
- **Circuit breakers** — safety systems that halt trading if losses mount
- **Position sizing** — a sophisticated 12-factor formula that determines how much to buy
- **Profit trailing** — a "chandelier exit" that locks in gains as price rises through 5 profit rungs
- **Docker deployment** — fully containerised on AWS, restarts automatically
- **Redis state** — fast in-memory database that remembers positions across restarts
- **Telegram alerts** — notifications sent to your phone when the system trades

### The Bad News (Discovered Today)

We ran a brutal, adversarial audit with 8 AI agents simultaneously tearing through every line of code. Think of it like hiring 8 hostile pen-testers to attack your house at once.

**They found 121 issues. Here's the plain English version:**

#### The System Will Crash Before It Can Trade (9 issues)
These are bugs where the code calls functions that literally don't exist. Imagine pressing a button labelled "LAUNCH" but there's no wire connected to anything. The system would crash the moment it tried to:
- Size a position (two called functions don't exist)
- Evaluate any signal using the slower, more careful analysis path
- Start up outside market hours (evenings/weekends)
- Do its daily housekeeping (missing database connection)

**Think of it like**: A car that won't start because the ignition is wired to nothing.

#### Safety Systems Are Broken (10 issues)
Even if the crashes were fixed, the safety nets have holes:
- **The 5x emergency exit doesn't work** — the code that's supposed to force-sell dangerous 5x products at 3:30 PM literally looks for products by the wrong identifier. It will never find them. Ever.
- **The "stop cascade" detector is wired up but nobody calls it** — like installing a smoke alarm but never connecting it to power
- **Risk limits are ignored** — the position sizer doesn't talk to the circuit breaker. The CB could say "HALT ALL TRADING" and the sizer would happily approve a new trade
- **The profit ladder never actually sells anything** — it calculates when to take partial profits but nobody reads the answer
- **Duplicate alerts** — every trade notification arrives 2-3 times on Telegram

**Think of it like**: A car with no brakes, a disconnected seatbelt, and a speedometer that shows a random number.

#### Features That Look Real But Do Nothing (12 issues)
The system has dozens of sophisticated-sounding features — first half-hour predictability, EWMA volatility widening, escalation protocols, macro regime sizing — but many are "theatre". The constants are defined, the variables are initialised, the methods are called... but the output is never used.

**Think of it like**: A dashboard with lots of impressive-looking gauges that aren't connected to sensors.

#### Wrong Calculations & Logic Errors (16 issues)
- The "Fear & Greed Index" the system uses is for **crypto**, not stocks
- The VIX data source is deprecated and often returns stale data
- The gap scanner can emit "SHORT" signals — which are **illegal in a UK ISA**
- Several time calculations mix up UTC, UK time, and US Eastern time
- The position sizer penalises well-known tickers forever (a 50% win rate ticker permanently gets half-sized)

**Think of it like**: A GPS using outdated maps that sometimes routes you into one-way streets.

#### Infrastructure Risks (12 issues)
- Redis (the memory database) will eventually fill up and stop accepting writes — killing the whole system
- The IB Gateway (broker connection) restarts every Monday for security checks but nothing pauses trading during the restart
- The backup script backs up the wrong file
- Config files have 60+ phantom tickers that don't exist anymore

---

## WHERE WE'RE GOING

### Immediate Priority: Make It Not Crash (Phase 0)
**Time: 4-6 hours**

Fix the 9 items that prevent the system from even starting. This is pure bug fixing — implementing the missing functions, fixing wrong argument types, returning empty lists instead of None.

### Next: Make Safety Actually Work (Phase 1)
**Time: 6-8 hours**

Wire up the disconnected safety systems. The 5x hard kill, the cascade detector, the circuit breaker → sizer link. After this phase, the system's safety net is real, not theatre.

### Then: Fix the Infrastructure (Phase 4)
**Time: 4-6 hours**

Split Redis into two databases properly, add health checks, fix the config inconsistencies, verify the broker connection on startup.

### Then: Fix the Logic & Fill in Dead Features (Phases 2+3)
**Time: 14-20 hours**

Correct the wrong calculations, implement the features that are currently just constants, fix the timezone confusion, replace the crypto Fear & Greed with an equity version.

### Total Estimated Work: 30-43 hours

---

## THE PATH TO REAL MONEY

```
NOW ──────────── Phase 0-4 fixes ──────── Paper Trading Gate ──────── Real Money
(broken)         (30-43 hrs code)         (200 trades at:)          (go live)
                                          WR >= 40%
                                          Profit Factor > 1.2
                                          Sharpe > 1.0
                                          + 63-day gauntlet
```

### After all fixes are done:
1. **200-Trade Validation Gate** — the system must prove itself over 200 paper trades with a win rate of at least 40%, a profit factor above 1.2, and a Sharpe ratio above 1.0
2. **63-Day Gauntlet** — run live paper trading for ~3 months with full monitoring
3. **Go/No-Go Decision** — if the gate passes, start with real money (small position, then scale up)

### Realistic Timeline
- **Weeks 1-2**: Fix Phases 0+1+4 (system stable and safe)
- **Weeks 3-4**: Fix Phases 2+3 (logic correct, features working)
- **Weeks 5-17**: 200-trade validation + 63-day gauntlet
- **Month 5+**: Real money decision

---

## BOTTOM LINE

The system's **architecture is solid** — the design, the strategy logic, the indicator pipeline, the multi-tier safety system, the profit ladder, the position sizing model — these are all well-thought-out and correctly designed.

The problem is **wiring**. Many components exist independently but aren't connected to each other. It's like building a Formula 1 car where each part is well-engineered, but the fuel line isn't connected to the engine, the brakes aren't connected to the pedal, and the steering wheel turns a different axle.

The 30-43 hours of work needed isn't about designing new features. It's about connecting what's already there, fixing the handful of crashes, and verifying that what should work actually does work.

Once the wiring is done and the system passes its 200-trade validation gate, it becomes a genuinely viable automated trading system targeting £10,000 → £24,500+ in year one.
