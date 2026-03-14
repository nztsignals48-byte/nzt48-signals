# AEGIS — Risk Architecture + Discipline + Constitution
> 15-control defence matrix, 10 commandments, regime integrity.
> Extracted from AEGIS Master Plan v16.2.
> See [README](README.md) for full index.
---

# SECTION 6: RISK ARCHITECTURE — 15-CONTROL DEFENCE MATRIX {#section-6}

### R-01: Circuit Breaker Cascade (Constitutional)

| Level | Trigger | Action |
|-------|---------|--------|
| L1 | Daily P&L <= -1.5% | Reduce all new sizing by 50%. Telegram alert |
| L2 | Daily P&L <= -2.5% | EXIT-ONLY mode. No new entries |
| L3 | Daily P&L <= -4.0% | FLATTEN ALL. HALT. Manual restart required |
| Weekly | Weekly P&L <= -8.0% | HALT for remainder of week |
| Monthly | Monthly P&L <= -15.0% | HALT. IC review required |

### R-02: Immutable Risk Rules
- 0.75% per trade — SACRED, NEVER modified
- Must have `__setattr__` guard (currently broken — P0-7)

### R-03: Portfolio Correlation Brake
- Max 2 positions per correlation cluster
- If rolling 20-day pairwise correlation > 0.70 for 3+ pairs: cap total exposure to 1 position

### R-04: Total Deployment Cap
- Max 40% of equity deployed across all open positions
- At 4 concurrent positions: average 10% notional each

### R-05: Overnight Kill
- ALL leveraged ETPs closed by 16:25 UK during paper/limited live
- 5x ETPs: always killed overnight, no exceptions
- **Session-end exit protocol**: 16:25 exit MUST be MARKET order (not limit) to guarantee fill. If any position still open at 16:28, submit MOC (market-on-close). If position open at 16:30, log as P0 incident.
- **Stop detection**: Dedicated stop-watch coroutine checks prices every 10s for tickers with open positions (separate from 60s full-universe scan). This is P1-17.

### R-06: Drawdown Recovery Cascade (Portfolio-Level)

| AUM Tier | Yellow | Orange | Red | Critical | Halt |
|----------|--------|--------|-----|----------|------|
| 10K-100K | -2% | -4% | -8% | -10% | -12% |
| 100K-500K | -1.5% | -3% | -6% | -8% | -10% |
| 500K-1M | -1% | -2.5% | -5% | -7% | -9% |
| 1M+ | -1% | -2% | -4% | -6% | -8% |

### R-07: Portfolio CVaR Gate
- Block new entries if portfolio CVaR_95 > 3% of equity
- iCVaR: reject if new position adds > 0.5% tail risk

### R-08: CDaR Circuit Breaker
- If CDaR_95 > 5%: HALT all entries, tighten stops

### R-09: Risk State Machine (GPT-30)
```
Precedence: SYSTEM_HALTED > EMERGENCY_FLATTEN > REDUCE > NORMAL
Single executor: only one risk action at a time. No contradictory actions.
```

### R-10: Anti-Correlation-Cascade Stop
- 3+ stops in 15 minutes: HALT all entries, tighten remaining stops to 0.5x ATR
- 30-minute cool-down

### R-11: Spread Veto Gate
- VETO if current_spread > 2.5x median_3d_spread (time-of-day normalised)

### R-12: OBI Toxicity Wait Gate (SHADOW MODE until L2 data available)

### R-13: Event-Based Stop Widening
- US Open: 14:30-15:30 UK: ATR multiplier 1.5x -> 2.0x
- BoE Rate Decision days: 11:30-12:30 UK: ATR multiplier 2.0x
- UK Data Release mornings (GDP, CPI): 06:30-08:00 UK: ATR multiplier 2.0x
- FTSE Quarterly Rebalance days: halt new entries 15:30-16:35 UK
- Source: Bank of England calendar, updated weekly

### R-14: ETP Financing Cost Offset
- Long leveraged: -2 bps/day drag
- Inverse leveraged: -4 bps/day drag

### R-15: Gamma/Strike Proximity Risk
- Underlying within 0.5% of major options strike: -10 confidence penalty

---

# SECTION 6B: TRADING DISCIPLINE — 10 COMMANDMENTS {#section-6b}

1. **NO TRADE IS BETTER THAN A BAD TRADE** — Min quality = 65. Below = flat. No exceptions.
2. **THE SYSTEM MUST NEVER BE FORCED INTO TRADING** — Max 4 trades/day. No FOMO.
3. **CASH IS A POSITION** — SHOCK or VIX > 35 = sit on hands.
4. **CAPITAL PRESERVATION IS RULE #1** — L1/L2/L3 cascade. Constitutional.
5. **WHEN IN DOUBT, KILL IT** — Cost of false positive (missed day) << cost of uncontained incident on 3x leverage.
6. **EACH TRADE STANDS ON ITS OWN MERIT** — No fallacy of large numbers.
7. **TODAY'S EXCELLENCE IS TOMORROW'S AVERAGE** — Win rate bar ratchets up.
8. **IF THERE ARE MULTIPLE QUALIFYING TRADES, TAKE THEM ALL** — All strategies fire independently. Portfolio limits are the only governors.
9. **IF THERE ARE NO QUALIFIED TRADES, STAY SILENT** — No-trade days = discipline, not failure. Quality floor NEVER below 50.
10. **NO EMOTION, NO OVERRIDE, ZERO EXCEPTIONS** — Go-Live Gate tracks overrides as FAILURES.

### 7 Discipline Gates (Before Technical Analysis)

| # | Gate | Trigger | Action |
|---|------|---------|--------|
| D-1 | Daily Loss | Realized P&L < -3% | HALT entries for session |
| D-2 | Cooldown | 4+ consecutive losses | 2h forced pause, then reduced size |
| D-3 | Max Trades | 4 trades executed today | No more entries today |
| D-4 | Setup Quality | Quality < 65 | Reject immediately |
| D-5 | Edge Expectancy | Expected R < 0.10 | Reject |
| D-6 | SHOCK Regime | Regime = SHOCK | Absolute block |
| D-7 | VIX Extreme | VIX > 35 | Absolute block |

### Multi-Trade Execution Rules

1. ALL strategies fire independently in every scan cycle. No mutual exclusion.
2. If multiple strategies produce qualifying signals, all enter the pipeline.
3. Portfolio-level governors are the ONLY trade-count limiter: max 4 concurrent positions, 3.5% heat cap, correlation brake, iCVaR veto.
4. The system can hold positions from multiple strategies simultaneously.
5. Correlation brake prevents concentration: pairwise rho > 0.70 blocks new position.
6. A position can be closed and reopened on the same ticker in the same session if a new qualifying signal fires.

---

# SECTION 6C: RISK CONSTITUTION {#section-6c}

## Constitutional Hierarchy (Supremacy Order)

```
TIER 0: RISK CONSTITUTION
    overrides everything below
TIER 1: IMMUTABLE RISK RULES (R-02)
    overrides everything below
TIER 2: CIRCUIT BREAKERS + EMERGENCY FLATTEN (R-01)
    overrides everything below
TIER 3: GAUNTLET GATES
    overrides everything below
TIER 4: STRATEGY SIGNALS (S1-S16)
    overrides everything below
TIER 5: LEARNING ENGINE ADJUSTMENTS
    overrides everything below
TIER 6: OPERATOR INSTRUCTIONS
```

**Rule 0 — Kill-First Asymmetry**: UNCERTAIN always resolves to KILL.

**Amendment Procedure**: Written IC submission -> 5-day review -> unanimous consent -> append-only audit trail. No emergency bypasses.

**Circuit Breaker Persistence**: State persists to SQLite. Docker restart does NOT reset breakers. Only session boundary reset (05:00 UTC daily — always before LSE pre-market in both GMT and BST; Monday for weekly). NOTE: Do NOT use 06:00 UTC — during BST, 06:00 UTC = 07:00 BST which overlaps LSE pre-market.

---

# SECTION 6D: REGIME INTEGRITY CONTROLS {#section-6d}

## Regime Transition Rules

| Transition | Action |
|---|---|
| Any -> SHOCK | EMERGENCY FLATTEN. Kill switch. |
| Any -> RISK_OFF | FLATTEN all. Cash. No entries. |
| TRENDING_UP -> TRENDING_DOWN | FLATTEN all longs. |
| RISK_OFF -> NORMAL | Resume at 0.25x size for 30 min |
| SHOCK -> NORMAL | Resume at 0.25x size for 60 min |

All transitions require 3-tick (3-min) confirmation. SHOCK exception: instant flatten when VIX > 45 AND (delta_24h > 10 OR delta_4h > 5). Also: if VIX > 45 for > 6 continuous hours regardless of delta, enter SHOCK (absolute level sufficient).

## Regime Flapping Protection
- 3+ regime changes in 10 minutes -> REGIME_FLAPPING state
- Hold positions, no new entries, 0.25x size
- Auto-clear after 30 min of stable regime

## Post-Recovery Ramp-Up
- RISK_OFF -> NORMAL: 0.25x for 30 min
- SHOCK -> NORMAL: 0.25x for 60 min
- REGIME_FLAPPING -> NORMAL: 0.50x for 15 min

## Regime Stuck Detection
- Same regime for >24h of market time -> P1 alert
- Manual review required

## Drought State Machine

```
DROUGHT_NONE --(10 dry cycles)--> DROUGHT_WATCH
DROUGHT_WATCH --(20 dry cycles)--> DROUGHT_ACTIVE
DROUGHT_ACTIVE --(60 dry cycles)--> DROUGHT_CRITICAL
Any state --(qualifying signal fires)--> DROUGHT_NONE
```

- Counter resets ONLY on signals that pass ALL gates
- At DROUGHT_CRITICAL: quality threshold decays 2 pts/day, floor 50
- Message at every escalation: "The market owes us nothing"

## Drought-Regime Contradiction Detection

| Condition | Expected | Meaning |
|-----------|----------|---------|
| TRENDING + drought | Should have signals | Data feed or gate issue |
| EXPANSION + drought | Should have signals | Gates too tight |
| RANGE + drought | Normal | Expected |
| SHOCK + no drought | Should have drought | Counter bug |

---
